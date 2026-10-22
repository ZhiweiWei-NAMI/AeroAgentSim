"""Private builtin staging compatibility; I/O stays outside operation compilers.

All writes are to the host's isolated plan directory. This adapter neither
applies a workspace nor constructs a model provider. External v1 families need
only the pure contract, not this legacy authoring integration.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from aeroagentsim.assistant.operations import OperationError
from aeroagentsim.authoring.templates import _knob_value, _pointer
from aeroagentsim.packs.lowalt.template import has_lowalt_components
from .assistant import (
    _fail, _group_id, _cooperation_module, _allocation_provider, _engine,
    _fleet_entities,
)

SECOND = 1_000_000_000


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")


def _sync_base_fleet(document: dict) -> None:
    fleet = document.get("generators", {}).get("fleet")
    if fleet is None:
        return
    _, tasks = _engine(document, "lowalt_tasks")
    overrides = tasks.get("vehicle_task_kinds", {})
    ids = {fleet["prefix"] + str(i + 1) for i in range(fleet["count"])}
    removed = {identity for identity in overrides if identity.startswith(fleet["prefix"]) and identity not in ids}
    cooperation = tasks.get("cooperation", {})
    referenced = set(cooperation.get("watchers", {}).get("vehicles", []) + cooperation.get("helpers", {}).get("vehicles", []))
    if any(identity.startswith(fleet["prefix"]) and identity not in ids for identity in referenced):
        _fail("count", "Removed base-fleet members are referenced by cooperation; revise responsibilities first", "待删除的基础机群成员被协同配置引用，请先修改责任")
    for identity in removed:
        overrides.pop(identity)
    kinds = tasks.get("vehicle_kinds", {}).get(fleet["facts"]["lowalt.vehicle_class"])
    if "vehicle_task_kinds" in tasks and kinds is not None:
        for identity in ids:
            overrides.setdefault(identity, list(kinds))


def _refresh_controls(ctx, scene, document: dict, warnings: list[dict]) -> None:
    if scene.template_id is None:
        return
    for knob in ctx.templates.get(scene.template_id)["knobs"]:
        if "pointer" in knob:
            node: Any = document
            try:
                for part in knob["pointer"][1:].split("/"):
                    part = part.replace("~1", "/").replace("~0", "~")
                    node = node[int(part)] if isinstance(node, list) else node[part]
                scene.controls[knob["id"]] = copy.deepcopy(node)
            except (KeyError, IndexError, ValueError, TypeError):
                scene.controls.pop(knob["id"], None)
                warnings.append({"op_id": None, "field": "control", "message": f"Control {knob['id']} has no remaining target after these edits / 修改后控件 {knob['id']} 已无对应目标"})
        elif knob.get("apply") == "lowalt.operator_mode":
            streams = [s for s in document.get("ingress_streams", []) if s["id"] == "operator"]
            if streams:
                scene.controls[knob["id"]] = "interactive" if streams[0]["initial_watermark_ns"] == 0 else "scheduled"


def _idle_kitchens(document: dict, warnings: list[dict]) -> None:
    """Explain inert kitchens while retaining their authored preparation settings."""
    if not any(e["type"] == "lowalt:Restaurant" for e in document["entities"]):
        return
    _, tasks = _engine(document, "lowalt_tasks")
    if any(g["kind"] == "food" for g in tasks.get("generators", [])):
        return
    idle = [e for e in document["entities"] if e["type"] == "lowalt:Restaurant"]
    if not idle:
        return
    names = ", ".join(e["id"] for e in idle)
    warnings.append({"op_id": None, "field": "kitchens", "message": f"Kitchen {names} has no meal orders; preparation settings are retained; add orders with kind food to cook / 厨房 {names} 没有餐食订单，已保留准备参数；请添加 food 类型订单"})


def _time_bounds(document: dict, operations: list[dict], errors: list[dict], warnings: list[dict]) -> None:
    """Check final inputs, after duration edits; never extend the experiment implicitly."""
    duration_ns = document["run"]["until_ns"]
    duration_s = duration_ns / SECOND
    authored: set[tuple[str, int]] = set()

    def check(value: float, field_name: str, op_id: str | None, *, ns: int | None = None) -> None:
        at_ns = round(value * SECOND) if ns is None else ns
        authored.add((field_name, at_ns))
        if at_ns <= duration_ns:
            return
        message = (f"{field_name} is {value:g} s, beyond the {duration_s:g} s run. Move it inside the run or set the run duration to at least {value:g} s / "
                   f"{field_name} 为 {value:g} 秒，超出 {duration_s:g} 秒运行。请移至运行范围内，或将运行时长设为至少 {value:g} 秒")
        row = {"op_id": op_id, "field": field_name, "code": "RUN_HORIZON", "message": message}
        (errors if op_id is not None else warnings).append(row)

    task_engines = [spec["config"] for spec in document["engines"].values() if spec["plugin"] == "lowalt_tasks"]
    groups = {_group_id(group): group for tasks in task_engines for group in tasks.get("generators", [])}
    incidents = {incident["id"]: incident for tasks in task_engines for incident in tasks.get("cooperation", {}).get("incidents", [])}
    workload_ids = {o["id"]: o["params"]["id"] for o in operations if o["op"] == "add_orders" and "id" in o}
    release_owners = {}
    incident_owners = {}
    task_owners = {}
    for operation in operations:
        name, params = operation["op"], operation["params"]
        if name in {"add_orders", "set_orders"} and "release" in params:
            identity = params["id"] if params["id"] in groups else workload_ids.get(params["id"], params["id"])
            release_owners[identity] = operation
        if name in {"add_incident", "set_incident"} and "time_s" in params:
            incident_owners[params["id"]] = operation
        if name == "set_task" and "release_s" in params:
            task_owners[params["task"]] = operation
    gusts = {(g["start_ns"], g["end_ns"]) for spec in document["engines"].values() if spec["plugin"] == "environment"
             for profiles in spec["config"].get("profiles", {}).values() for profile in profiles.values() for g in profile.get("gusts", [])}
    task_times = {task: group["arrivals"]["times_ns"][index] for group in groups.values() if group["arrivals"]["mode"] == "scheduled"
                  for index, task in enumerate(group["tasks"])}
    for operation in operations:
        name, params, op_id = operation["op"], operation["params"], operation.get("id")
        if name == "add_gust" and (round(params["start_s"] * SECOND), round(params["end_s"] * SECOND)) in gusts:
            for key in ("start_s", "end_s"):
                check(params[key], "params." + key, op_id)
        if name in {"add_incident", "set_incident"} and incidents.get(params["id"]) and incident_owners.get(params["id"]) is operation:
            incident = incidents[params["id"]]
            check(incident["at_s"], "params.time_s", op_id)
            if "end_s" in incident:
                check(incident["end_s"], "params.end_s", op_id)
        # An end removed by a later set_incident end_s:null keeps every earlier end edit reachable.
        if name == "set_incident" and params.get("end_s") is None:
            for other in operations:
                if other["op"] in {"add_incident", "set_incident"} and other["params"].get("id") == params["id"] and "end_s" in other["params"] and other["params"]["end_s"] is not None:
                    check(other["params"]["end_s"], "params.end_s", other.get("id"))
        if name == "set_duration":
            for identity, incident in incidents.items():
                owner_id = next((o.get("id") for o in operations if o["op"] in {"add_incident", "set_incident"} and o["params"].get("id") == identity), None)
                if "end_s" in incident:
                    check(incident["end_s"], f"incidents.{identity}.end_s", owner_id)
        if name == "set_task" and task_owners.get(params["task"]) is operation and params["task"] in task_times:
            ns = task_times[params["task"]]
            check(ns / SECOND, "params.release_s", op_id, ns=ns)
        if name == "schedule_event":
            check(params["at_s"], "params.at_s", op_id)
        if name in {"add_orders", "set_orders"}:
            identity = params["id"] if params["id"] in groups else workload_ids.get(params["id"], params["id"])
            group = groups.get(identity)
            if group is not None and release_owners.get(identity) is operation:
                release = group["arrivals"]
                for index, value in enumerate(release.get("times_ns", [])):
                    check(value / SECOND, f"params.release.times_s[{index}]", op_id, ns=value)
                for key in ("start_ns", "end_ns"):
                    if key in release:
                        check(release[key] / SECOND, "params.release." + key.replace("_ns", "_s"), op_id, ns=release[key])

    # Existing template schedules can intentionally outlast a shortened run.
    # They remain authored, but the limitation must be visible in the proposal.
    existing: list[tuple[str, int]] = []
    for engine_id, spec in document["engines"].items():
        config = spec["config"]
        if spec["plugin"] == "environment":
            for identity, profiles in config.get("profiles", {}).items():
                for field_id, profile in profiles.items():
                    for index, gust in enumerate(profile.get("gusts", [])):
                        existing.append((f"engines.{engine_id}.profiles.{identity}.{field_id}.gusts[{index}].end_s", gust["end_ns"]))
        if spec["plugin"] == "lowalt_tasks":
            for group in config.get("generators", []):
                arrivals = group["arrivals"]
                times = arrivals.get("times_ns", []) if arrivals["mode"] == "scheduled" else [arrivals["end_ns"]] if "end_ns" in arrivals else []
                if times:
                    existing.append((f"workloads.{_group_id(group)}.release", max(times)))
            for incident in config.get("cooperation", {}).get("incidents", []):
                existing.append((f"incidents.{incident['id']}.time_s", round(incident["at_s"] * SECOND)))
                if "end_s" in incident:
                    existing.append((f"incidents.{incident['id']}.end_s", round(incident["end_s"] * SECOND)))
    for command in document.get("bindings", {}).get("commands", []):
        existing.append((f"events.{command['schema']}.at_s", command["at_ns"]))
    explicit_times = {time for _, time in authored}
    for field_name, at_ns in existing:
        if at_ns not in explicit_times:
            check(at_ns / SECOND, field_name, None, ns=at_ns)


def _operation_order(operations: list[dict], document: dict) -> dict[int, set[int]]:
    """Stable dependency order; authored operation ids/order remain unchanged."""
    creators: dict[str, int] = {}
    existing = {e["id"] for e in document.get("entities", [])}
    existing.update(_group_id(group) for spec in document.get("engines", {}).values() if spec.get("plugin") == "lowalt_tasks"
                    for group in spec["config"].get("generators", []))
    for i, operation in enumerate(operations):
        if not isinstance(operation, dict) or not isinstance(operation.get("params"), dict):
            continue
        p, name = operation["params"], operation.get("op")
        if name in {"add_place", "add_fleet", "add_orders", "add_incident"} and isinstance(p.get("id"), str):
            creators.setdefault(p["id"], i)
            if name in {"add_fleet", "add_orders"} and type(p.get("count")) is int and 0 < p["count"] <= 1000:
                for member in range(1, p["count"] + 1):
                    creators.setdefault(f"{p['id']}-{member}", i)
        if name == "set_cooperation":
            for point in p.get("handoff_points", []):
                if isinstance(point, dict) and isinstance(point.get("id"), str):
                    creators.setdefault(point["id"], i)

    selectors = {"at", "start", "from", "to", "fleet", "vehicle", "task", "destination", "vehicles", "handoff_points", "site", "origin", "restaurant"}
    def references(value, key=""):
        if isinstance(value, dict):
            return set().union(*(references(v, k) for k, v in value.items())) if value else set()
        if isinstance(value, list):
            return set().union(*(references(v, key) for v in value)) if value else set()
        return {value} if key in selectors and isinstance(value, str) else set()

    deps: dict[int, set[int]] = {i: set() for i in range(len(operations))}
    installers = [i for i, o in enumerate(operations) if isinstance(o, dict) and o.get("op") == "add_lowalt_components"]
    regions = [i for i, o in enumerate(operations) if isinstance(o, dict) and o.get("op") == "set_region"]
    durations = [i for i, o in enumerate(operations) if isinstance(o, dict) and o.get("op") == "set_duration"]
    workloads = {o.get("id"): o["params"]["id"] for o in operations if isinstance(o, dict) and o.get("op") == "add_orders" and isinstance(o.get("params"), dict) and "id" in o["params"]}
    for i, operation in enumerate(operations):
        if not isinstance(operation, dict):
            continue
        params = operation.get("params", {})
        refs = references(params)
        if operation.get("op") in {"update_place", "remove_place", "set_orders", "remove_orders", "set_incident", "remove_incident"} and isinstance(params, dict):
            target = params.get("id")
            refs.add(workloads.get(target, target) if operation.get("op") in {"set_orders", "remove_orders"} and isinstance(target, str) and target not in existing else target)
        deps[i].update(creators[r] for r in refs if r not in existing and r in creators and creators[r] != i)
        if installers and i not in installers:
            deps[i].add(installers[0])
        if regions and i not in regions and i not in installers:
            deps[i].add(regions[-1])
        # Release defaults use the final requested horizon, regardless of list order.
        if operation.get("op") in {"add_orders", "set_orders", "set_task", "add_gust", "add_incident", "set_incident", "schedule_event"}:
            deps[i].update(durations)
    return deps


def _places_needed(document: dict, name: str, params: dict) -> bool:
    values = []
    if name in {"add_place", "update_place", "add_incident", "set_incident"}:
        values.append(params.get("at"))
    if name in {"add_fleet", "set_fleet", "set_vehicle"}:
        values.append(params.get("start"))
    if name == "set_cooperation":
        values.extend(point if isinstance(point, str) else point.get("at") for point in params.get("handoff_points", []))
    known = {entity["id"] for entity in document["entities"]}
    for engine in document.get("engines", {}).values():
        cooperation = engine.get("config", {}).get("cooperation", {})
        known.update(row["id"] for row in cooperation.get("handoff_points", []) + cooperation.get("incidents", []))
    centres = {"center", "centre", "middle", "region_center", "enu_origin", "中间", "中心"}
    return any(isinstance(value, str) and value not in known and value.casefold() not in centres for value in values)


class LowaltStaging:
    def __init__(self, ctx, scene, document: dict, files: Path, baseline, catalogue) -> None:
        self.ctx, self.scene, self.files = ctx, scene, files
        self.baseline, self.catalogue = baseline, catalogue
        self.base_lowalt = has_lowalt_components(baseline.document)
        self.created_by: dict[str, str] = {}
        self.workload_creators: dict[str, str] = {}
        if "generators" in document:
            self.expand(document)

    def ensure_contracts(self, target: dict) -> None:
        """Cooperation needs the frozen AeroGraph incident/cargo-transfer contracts in the plan's snapshot."""
        if not any(e["plugin"] == "lowalt_tasks" and "cooperation" in e.get("config", {}) for e in target["engines"].values()):
            return
        snapshot = target["registry"].get("snapshot")
        if snapshot is None:
            snapshot = "registry.plan.snapshot.json"
            self.baseline.compiled.write_snapshot(self.files / snapshot)
            target["registry"].pop("compile", None)
            target["registry"]["snapshot"] = snapshot
        path = Path(snapshot) if Path(snapshot).is_absolute() else self.files / snapshot
        if not path.resolve().is_relative_to(self.files.resolve()):
            _fail("registry.snapshot", "Registry snapshot must be inside plan files", "注册表快照必须位于计划文件内部")
        _write(path, _cooperation_module().merge_contract_snapshot(json.loads(path.read_text())))

    def expand(self, target: dict) -> None:
        from aeroagentsim.packs.lowalt.template import expand as expand_template

        # Expansion reads the snapshot; like the loader, resolve it against the plan files.
        self.ensure_contracts(target)
        snapshot = target["registry"].get("snapshot")
        if snapshot is not None and not Path(snapshot).is_absolute():
            target["registry"]["snapshot"] = str(self.files / snapshot)
        try:
            expand_template(target)
        finally:
            if snapshot is not None:
                target["registry"]["snapshot"] = snapshot

    @staticmethod
    def operation_name(spec) -> str | None:
        if spec.id.partition(".")[0] not in {"lowalt", "traffic"}:
            return None
        return spec.display.get("legacy_operation")

    def legacy(self, operation: dict) -> dict:
        row = copy.deepcopy(operation)
        if not isinstance(row, dict):
            return row
        try:
            spec = self.catalogue.resolve(row.get("op"))
        except OperationError:
            return row
        row["op"] = self.operation_name(spec) or row.get("op")
        return row

    def dependencies(self, operations: list[dict], document: dict) -> dict[int, set[int]]:
        legacy = [self.legacy(row) for row in operations]
        self.workload_creators = {o["id"]: o["params"]["id"] for o in legacy if isinstance(o, dict) and o.get("op") == "add_orders" and
                                 isinstance(o.get("params"), dict) and isinstance(o["params"].get("id"), str) and isinstance(o.get("id"), str)}
        return _operation_order(legacy, document)

    def prepare(self, spec, operation: dict, document: dict, params: dict, warnings: list[dict]) -> dict:
        name = self.operation_name(spec)
        if name is None:
            return {}
        op_id = operation.get("id")
        data: dict = {"workload_creators": self.workload_creators}
        if name in {"add_incident", "set_incident", "remove_incident", "set_cooperation", "remove_cooperation"} or name == "add_place" and params.get("role") == "handoff_point":
            _cooperation_module()
        if name == "set_algorithm":
            from aeroagentsim.services.algorithm_catalog import discover_algorithm_catalog
            data["algorithms"] = discover_algorithm_catalog()
        if _places_needed(document, name, params):
            from .assistant_places import place_catalogue
            # Coordinates, local centres and scene ids need no catalogue I/O.
            data["places"] = place_catalogue()
        if name not in {"set_region", "add_lowalt_components", "set_control", "remove_fleet"}:
            return data
        from aeroagentsim.assistant.compiler import _resource_inputs

        self.resource_base, _ = _resource_inputs(self.files)
        candidate = copy.deepcopy(document)
        controls = copy.deepcopy(self.scene.controls)
        staged_warnings: list[dict] = []
        removals = None
        if name == "set_region":
            if not self.ctx.allow_region:
                raise OperationError("op", "set_region is an assistant proposal operation. Use the Region map's /region preview/apply endpoint for form edits / set_region 用于助手方案；表单请使用区域地图的 /region 预览和应用接口")
            from aeroagentsim.assistant.regions import preview

            record = preview(self.ctx, candidate, params, self.files)
            staged_warnings.append({"op_id": op_id, "field": "bounds", "message": f"Region origin changes from {record['previous_origin']} to {record['scene']['origin']}; ENU coordinates remain authored relative to the new origin; the road network is invalidated / 区域原点改变，已有 ENU 坐标将相对于新原点解释，道路网络失效"})
            return self.prepared(data, candidate, controls, staged_warnings, removals)
        if name == "add_lowalt_components":
            from aeroagentsim.packs.lowalt.template import install_components

            install_components(candidate, directory=self.files)
            return self.prepared(data, candidate, controls, staged_warnings, removals)
        if name == "set_control":
            if self.scene.template_id is None:
                _fail("control", "This workspace has no template controls", "此工作区没有模板控件")
            knobs = {k["id"]: k for k in self.ctx.templates.get(self.scene.template_id)["knobs"]}
            if params["control"] not in knobs:
                _fail("control", f"Choose a declared control: {list(knobs)}", "请选择此起点已声明的控件")
            knob = knobs[params["control"]]
            try:
                _knob_value(knob, params["value"])
                value = float(params["value"]) if knob["type"] == "number" else params["value"]
                if "pointer" in knob:
                    _pointer(candidate, knob["pointer"], value)
                else:
                    self.ctx.templates.hooks[knob["apply"]](candidate, self.files, value)
            except (KeyError, ValueError, TypeError) as exc:
                raise OperationError("value", f"{exc} / 模板控件校验失败") from exc
            controls[params["control"]] = value
            if knob.get("pointer") == "/algorithms/allocation":
                _allocation_provider(self.ctx, candidate)
            return self.prepared(data, candidate, controls, staged_warnings, removals)
        if name == "remove_fleet":
            from aeroagentsim.authoring.removal import FleetRemovalPlan

            fleet = candidate.get("generators", {}).get("fleet")
            generated = fleet is not None and params["fleet"] in {"fleet", fleet["prefix"].rstrip("-.")}
            members = ({fleet["prefix"] + str(i + 1) for i in range(fleet["count"])} if generated
                       else {entity["id"] for entity in _fleet_entities(candidate, params["fleet"])})
            if not members:
                _fail("fleet", "Fleet does not exist; use its summary id", "机群不存在，请使用摘要中的标识")
            cleaned, dependencies = FleetRemovalPlan(candidate, members, self.files, generated=generated).build()
            candidate.clear()
            candidate.update(cleaned)
            removals = [{**row, "id": row.get("id", row["path"]), "label": row["detail"], "op_id": op_id} for row in dependencies]
            return self.prepared(data, candidate, controls, staged_warnings, removals)

    def prepared(self, data: dict, document: dict, controls: dict, warnings: list, removals) -> dict:
        from aeroagentsim.assistant.compiler import _resource_inputs

        current, _ = _resource_inputs(self.files)
        resources = [{"path": name, "content": current.get(name)} for name in sorted(self.resource_base.keys() | current.keys())
                     if name != "region.plan.json" and self.resource_base.get(name) != current.get(name)]
        data["prepared_operation"] = {"document": document, "controls": controls, "warnings": warnings,
                                      "references": removals or [], "resources": resources}
        return data

    def after(self, document: dict) -> None:
        if "generators" in document:
            _sync_base_fleet(document)
            self.expand(document)

    def succeeded(self, spec, params: dict, op_id: str) -> None:
        if self.operation_name(spec) in {"add_place", "add_fleet", "add_orders", "add_incident"}:
            self.created_by[params["id"]] = op_id

    def error(self, operation: dict, error: Exception, message: str) -> str:
        operation = self.legacy(operation)
        target = operation.get("params", {}).get("id") if isinstance(operation, dict) and isinstance(operation.get("params"), dict) else None
        owner = self.created_by.get(target) if isinstance(target, str) else None
        if owner is not None and isinstance(operation, dict) and str(operation.get("op", "")).startswith("add_"):
            message += f". {target!r} is already added by {owner} in THIS draft; edit {owner} with revise_plan.update instead of adding it again / 该对象已由本草稿操作 {owner} 添加，请修改该操作"
        if isinstance(operation, dict) and operation.get("op") in {"set_orders", "remove_orders"} and getattr(error, "field", None) == "id":
            message += f". Workload creation operations (op id → workload id): {self.workload_creators}; retain these creations / 工作量创建操作（操作标识→工作量标识），请保留创建操作"
        return message

    def finish(self, document: dict, operations: list[dict], errors: list[dict], warnings: list[dict]) -> None:
        coop = next((e["config"].get("cooperation") for e in document["engines"].values() if e["plugin"] == "lowalt_tasks"), None)
        if coop is not None and not errors:
            module = _cooperation_module()
            self.ensure_contracts(document)
            vehicles = {e["id"] for e in document["entities"] if e["type"] == "lowalt:UAV"}
            module.validate_config(coop, vehicles=vehicles, until_ns=document["run"]["until_ns"])
            snapshot = document["registry"]["snapshot"]
            document["registry"]["snapshot"] = str(self.files / snapshot) if not Path(snapshot).is_absolute() else snapshot
            try:
                module.expand(document)
            finally:
                document["registry"]["snapshot"] = snapshot
            if not coop.get("incidents"):
                # Valid but inert: without an incident nothing triggers noticing, handoff or photography.
                warnings.append({"op_id": None, "field": "incidents", "message": "Cooperation is configured but no incident is scheduled, so nobody will notice, hand over or photograph anything; add one with add_incident / 已配置协作但没有安排事件，不会触发发现、交接或拍摄；请用 add_incident 添加事件"})
            if not any(e["plugin"] in {"lowalt_city_capture", "traffic_camera_capture"} for e in document["engines"].values()):
                warnings.append({"op_id": None, "field": "photograph", "message": "Photograph events are recorded, but this scene has no city camera for an image artifact / 此场景可记录拍摄事件，但没有城市相机生成照片文件"})
        if not errors:
            _time_bounds(document, [self.legacy(operation) for operation in operations], errors, warnings)
        if not errors:
            _idle_kitchens(document, warnings)

    def refresh(self, document: dict, warnings: list[dict]) -> None:
        _refresh_controls(self.ctx, self.scene, document, warnings)

    def decorate(self, result, baseline) -> None:
        if result.ok and not self.base_lowalt and has_lowalt_components(result.document):
            added_engines = sorted(result.document["engines"].keys() - baseline.document["engines"].keys())
            result.diff_base["added"].insert(0, {"kind": "components", "id": "lowalt_components",
                "label": f"{len(added_engines)} required drone scene engines, decision methods and safety rules",
                "label_zh": f"{len(added_engines)} 个必需的无人机场景引擎、决策方法与安全规则", "op": "add_lowalt_components",
                "fields": [{"name": "engines", "before": [], "after": added_engines}]})
        region_plan = self.files / "region.plan.json"
        if result.ok and region_plan.is_file():
            region_record = json.loads(region_plan.read_text())
            result.summary["region"] = region_record["region"]
            result.diff_base["changed"].append({"kind": "scene", "id": "region", "label": "Map region", "label_zh": "地图区域",
                                               "fields": [{"name": "bounds", "after": region_record["region"]["bounds"]},
                                                          {"name": "origin", "before": region_record["previous_origin"], "after": region_record["scene"]["origin"]}]})
            if region_record["network_invalidated"]:
                result.diff_base["removed"].append({"kind": "network", "id": "network.net.xml", "label": "Previous road network", "label_zh": "原道路网络"})


def _operation_name(name: str) -> str:
    prefix, separator, suffix = name.partition(".")
    return suffix if separator and prefix in {"lowalt", "traffic"} else name


def _like_operation(original: str, name: str) -> str:
    prefix, separator, _ = original.partition(".")
    return prefix + "." + name if separator and prefix in {"lowalt", "traffic"} else name


def workspace_operations(operations: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply only changed arrangements to an already-confirmed real workspace."""
    from .assistant import OPERATION_SPECS as OPERATIONS
    from aeroagentsim.assistant.tools import validate_arguments

    old = {op["id"]: op for op in baseline}
    new = {op["id"]: op for op in operations}
    result = []
    replacements = {"add_place": ("update_place", "place"), "add_fleet": ("set_fleet", "fleet"), "add_orders": ("set_orders", "orders"), "add_incident": ("set_incident", "incident")}
    removals = {"add_place": ("remove_place", "place"), "add_fleet": ("remove_fleet", "fleet"), "add_orders": ("remove_orders", "orders"), "add_incident": ("remove_incident", "incident"), "set_cooperation": ("remove_cooperation", None)}

    def translate(op: dict[str, Any], target: str, subject: str | None, changes: dict[str, Any]) -> dict[str, Any]:
        spec = OPERATIONS[target]
        properties = spec["parameters"].get("properties", {})
        params = copy.deepcopy(changes)
        if subject is not None:
            key = subject if subject in properties else "id" if "id" in properties else None
            if key is None:
                raise ValueError(f"{op['id']}: {target} does not declare the entity selector / 操作未声明对象选择字段")
            params[key] = op["params"]["id"]
        validate_arguments(spec["parameters"], params, op["id"])
        return {"id": op["id"], "op": _like_operation(op["op"], target), "params": params}

    for op in operations:
        previous = old.get(op["id"])
        if previous == op:
            continue
        if previous is not None and _operation_name(op["op"]) == _operation_name(previous["op"]) and _operation_name(op["op"]) in replacements:
            if op["params"].get("id") != previous["params"].get("id"):
                raise ValueError(f"{op['id']}: keep an applied entity's id when revising it / 修改已确认对象时须保留对象标识")
            changed = {k: v for k, v in op["params"].items() if k != "id" and (k not in previous["params"] or previous["params"][k] != v)}
            if previous["params"].keys() - op["params"].keys():
                raise ValueError(f"{op['id']}: specify replacement values for removed parameters / 请明确提供被移除参数的替代值")
            target, subject = replacements[_operation_name(op["op"])]
            result.append(translate(op, target, subject, changed))
        elif previous is not None and _operation_name(previous["op"]).startswith("add_"):
            raise ValueError(f"{op['id']}: revise an applied addition with the same operation kind / 请保留已确认新增对象的操作类型")
        else:
            result.append(copy.deepcopy(op))
    for op in baseline:
        if op["id"] in new:
            continue
        if _operation_name(op["op"]) not in removals:
            raise ValueError(f"{op['id']}: removing this already-applied arrangement needs an explicit supported replacement / 移除此已确认安排需要明确的受支持替代操作")
        target, subject = removals[_operation_name(op["op"])]
        result.insert(0, translate(op, target, subject, {}))
    return result



def recover_operations(operations: list[dict], plans: dict, parent) -> tuple[list[dict], list[str]]:
    session = {"plans": plans}
    operations = copy.deepcopy(operations)
    # Older failed revisions may already have replaced their creation with
    # set_orders. Recover only that same retained op's real ancestor recipe.
    recipes = {}
    ancestor = session["plans"].get(str(parent))
    visited = set()
    while ancestor is not None and ancestor["version"] not in visited:
        visited.add(ancestor["version"])
        for operation in ancestor["operations"]:
            if _operation_name(operation["op"]) == "add_orders":
                recipes.setdefault(operation["id"], (operation, ancestor["version"]))
        ancestor = session["plans"].get(str(ancestor.get("parent")))
    recovery_notes = []
    for index, operation in enumerate(operations):
        recipe = recipes.get(operation.get("id"))
        if _operation_name(operation["op"]) != "set_orders" or recipe is None:
            continue
        creation, source_version = recipe
        target = creation["params"]["id"]
        if operation["params"]["id"] not in {target, creation["id"]}:
            continue
        if any(_operation_name(op["op"]) == "add_orders" and op["params"]["id"] == target for op in operations):
            continue
        operations[index] = {"id": operation["id"], "op": _like_operation(operation["op"], "add_orders"), "params": {**copy.deepcopy(creation["params"]), **operation["params"], "id": target}}
        recovery_notes.append(f"Retained the creation of workload {target!r} from plan v{source_version} while editing its parameters / 修改工作量 {target!r} 时保留方案 v{source_version} 的真实创建配置")
    return operations, recovery_notes


def revise_operations(operations: list[dict], updates: dict) -> dict:
    result, by_id = {"operations": operations}, copy.deepcopy(updates)
    # Revising a retained workload creation edits its recipe. Replacing the
    # creation by set_orders would leave nothing to edit on recompilation.
    for original in result["operations"]:
        update = by_id.get(original["id"])
        if _operation_name(original["op"]) == "add_orders" and update is not None and _operation_name(update["op"]) == "set_orders":
            if update["params"]["id"] not in {original["params"]["id"], original["id"]}:
                raise ValueError(f"{original['id']}: workload is {original['params']['id']!r}; retain that target / 请保留该工作量标识")
            params = {**copy.deepcopy(original["params"]), **update["params"], "id": original["params"]["id"]}
            by_id[original["id"]] = {"id": original["id"], "op": _like_operation(original["op"], "add_orders"), "params": params}
    return by_id
