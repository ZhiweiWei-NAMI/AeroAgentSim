"""Compile reversible plan operations into real, portable scenario drafts."""

from __future__ import annotations

import copy
import difflib
import hashlib
import importlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.authoring.templates import TemplateRegistry, _knob_value, _pointer
from aeroagentsim.authoring.workspace import WorkspaceStore, issue_location
from aeroagentsim.packs.lowalt.overlay import overlay
from aeroagentsim.packs.lowalt.rules import WIND_LIMITS
from aeroagentsim.packs.lowalt.tasks import FOOD_FIELDS
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader

from .operations import OperationError, PLACE_TYPES, ROLES, validate_operation
from .places import place_catalogue

SECOND = 1_000_000_000


@dataclass
class CompileContext:
    store: WorkspaceStore
    templates: TemplateRegistry
    agent_profile: str | None
    locale: str = "en"


@dataclass
class BaseScene:
    document: dict[str, Any]
    directory: Path
    label: str
    revision: int | None
    template_id: str | None
    controls: dict[str, Any]


@dataclass
class CompileResult:
    ok: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    document: dict[str, Any]
    files_dir: Path
    digest: str
    summary: dict[str, Any] = field(default_factory=dict)
    diff_base: dict[str, Any] = field(default_factory=dict)
    interactions: dict[str, Any] = field(default_factory=dict)
    expectations: list[dict[str, Any]] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        return copy.deepcopy({**self.__dict__, "files_dir": str(self.files_dir)})


class PlanConflict(ValueError):
    """The confirmed plan no longer describes the current draft."""


def resolve_base(ctx: CompileContext, base: dict) -> BaseScene:
    if not isinstance(base, dict):
        raise ValueError("base: workspace or template required / base 需要工作区或模板")
    # The tool layer records the revision it compiled against; apply_plan checks it, not this.
    if "workspace" in base and not set(base) - {"workspace", "revision"}:
        draft = ctx.store.get(base["workspace"])
        document = copy.deepcopy(draft["scenario"])
        _editable_packages(document)
        source = draft.get("template", {})
        return BaseScene(document, ctx.store._base(document, draft["id"]), draft["name"],
                         draft["revision"], source.get("id"), copy.deepcopy(source.get("knobs", {})))
    if "template" in base and not set(base) - {"template", "controls"}:
        prepared = ctx.templates.apply(base["template"], base.get("controls", {}))
        document = copy.deepcopy(prepared.document)
        _editable_packages(document)
        return BaseScene(document, prepared.base, prepared.metadata["title"],
                         None, base["template"], copy.deepcopy(prepared.knobs))
    raise ValueError("base: supply exactly workspace or template with controls / 仅提供工作区，或模板及控件")


def _editable_packages(document: dict) -> None:
    if "behaviours" in document:
        document["behaviours"] = [copy.deepcopy(p.get("document", p)) for p in document["behaviours"]]


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")


def _excluded(path: Path) -> bool:
    return any(p == "versions" or p == "__pycache__" or p.startswith("validation-") or
               (p.startswith("draft") and p.endswith(".json")) for p in path.parts)


def _copy_files(source: Path, target: Path) -> None:
    source = source.resolve()
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if _excluded(relative) or not path.is_file():
            continue
        if not path.resolve().is_relative_to(source):
            raise ValueError(f"Source symlink escapes the scene: {relative} / 源文件链接超出场景范围")
        destination = target / relative
        if not destination.resolve().is_relative_to(target.resolve()):
            raise ValueError("Destination escapes plan files / 目标超出计划文件范围")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def _rebase(value: Any, source: Path, target: Path) -> Any:
    if isinstance(value, dict):
        return {k: _rebase(v, source, target) for k, v in value.items()}
    if isinstance(value, list):
        return [_rebase(v, source, target) for v in value]
    if isinstance(value, str) and Path(value).is_absolute():
        path = Path(value).resolve()
        if path.is_relative_to(source.resolve()):
            return str(target.resolve() / path.relative_to(source.resolve()))
    return value


def _digest(document: dict, directory: Path | None) -> str:
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
    paths = sorted(p for p in directory.rglob("*") if p.is_file()) if directory is not None else []
    for path in paths:
        relative = path.relative_to(directory).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
        digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    return "sha256:" + digest.hexdigest()


def _fail(field: str, en: str, zh: str) -> None:
    raise OperationError(field, en + " / " + zh)


def _engine(document: dict, plugin: str) -> tuple[str, dict]:
    engines = [(k, v["config"]) for k, v in document.get("engines", {}).items() if v.get("plugin") == plugin]
    if len(engines) != 1:
        _fail("base", f"Starting scene needs exactly one {plugin} engine", f"起点场景需要一个 {plugin} 引擎")
    return engines[0]


def _closest(identity: Any, candidates: list[str]) -> tuple[str, str]:
    # Models guess plausible ids ("vertiport-a", "meal-fleet"); naming the real ones lets them repair in one step.
    close = difflib.get_close_matches(str(identity), candidates, n=5, cutoff=0.45)
    return (f". Closest existing ids: {', '.join(close)}", "。相近的现有标识：" + "、".join(close)) if close else ("", "")


def _entity(document: dict, identity: str, field_name: str) -> dict:
    found = next((e for e in document["entities"] if e["id"] == identity), None)
    if found is None:
        en, zh = _closest(identity, [e["id"] for e in document["entities"]])
        _fail(field_name, f"Entity {identity!r} does not exist{en}", f"该实体不存在{zh}")
    return found


def _position(document: dict, value: Any, field_name: str) -> list[float]:
    if isinstance(value, list):
        return [float(v) for v in value]
    # Explicit scene ids take precedence over place catalogue ids.
    entity = next((e for e in document["entities"] if e["id"] == value), None)
    if entity is not None:
        for key in ("lowalt.site_position", "lowalt.position", "lowalt.incident_position"):
            if key in entity["facts"]:
                return list(entity["facts"][key])
        _fail(field_name, f"{value} has no lowalt position", "此对象没有低空空间坐标")
    for engine in document.get("engines", {}).values():
        cooperation = engine.get("config", {}).get("cooperation", {})
        point = next((p for p in cooperation.get("handoff_points", []) + cooperation.get("incidents", []) if p["id"] == value), None)
        if point is not None:
            return list(point["position"])
    place = next((p for p in place_catalogue() if p["key"] == value), None)
    if place is None:
        en, zh = _closest(value, [p["key"] for p in place_catalogue()] + [e["id"] for e in document["entities"]])
        _fail(field_name, f"Unknown place {value!r}; use find_places{en}", f"地点不存在，请使用 find_places 查找{zh}")
    return list(place["position"])


def _unique(document: dict, identity: str, field_name: str = "id") -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", identity):
        _fail(field_name, "Use a stable alphanumeric identifier", "请使用稳定的字母数字标识")
    if any(e["id"] == identity for e in document["entities"]):
        _fail(field_name, f"Identifier {identity!r} is already used", "此标识已被使用")
    if _fleet_entities(document, identity):
        _fail(field_name, "Identifier is already used by a fleet", "此标识已被机群使用")
    cooperation = next((e["config"].get("cooperation") for e in document.get("engines", {}).values() if e["plugin"] == "lowalt_tasks"), None)
    if cooperation and any(p["id"] == identity for p in cooperation.get("handoff_points", []) + cooperation.get("incidents", [])):
        _fail(field_name, "Identifier is already used by cooperation", "此标识已被协同配置使用")
    fleet = document.get("generators", {}).get("fleet")
    if fleet and identity.startswith(fleet["prefix"]):
        _fail(field_name, "Identifier overlaps the generated base fleet prefix", "此标识与基础机群生成前缀冲突")


def _overlay(document: dict) -> None:
    for category, rows in overlay().items():
        selected = document["registry"].setdefault(category, [])
        known = {row["id"] for row in selected}
        selected.extend(copy.deepcopy(row) for row in rows if row["id"] not in known)


def _rule_package(document: dict, directory: Path) -> dict:
    for spec in document.get("behaviours", []):
        package = spec
        if "path" in spec:
            path = (directory / spec["path"]).resolve()
            if not path.is_relative_to(directory.resolve()):
                _fail("rule", "Rule package escapes scene files", "规则包超出场景文件范围")
            package = yaml.load(path.read_bytes(), Loader=UniqueLoader)
        if package.get("id") == "lowalt.rules":
            if package is not spec:
                document["behaviours"][document["behaviours"].index(spec)] = package
            return package
    _fail("rule", "Starting scene has no lowalt operational rule package", "起点场景没有低空运行规则包")


def _fleet_entities(document: dict, identity: str) -> list[dict]:
    """A plan fleet is its members `<fleet>-<n>` (generated base-fleet members are not a plan fleet)."""
    pattern = re.compile(rf"^{re.escape(identity)}-\d+$")
    generator = document.get("generators", {}).get("fleet")
    return [e for e in document["entities"] if e["type"] == "lowalt:UAV" and pattern.match(e["id"])
            and not (generator and e["id"].startswith(generator["prefix"]))]


def _vehicles(document: dict, values: list[str], field_name: str) -> list[str]:
    result: list[str] = []
    for value in values:
        members = _fleet_entities(document, value)
        base = document.get("generators", {}).get("fleet")
        if base and value in {"fleet", base["prefix"].rstrip("-.")}:
            ids = [base["prefix"] + str(i + 1) for i in range(base["count"])]
        elif members:
            ids = [e["id"] for e in members]
        else:
            entity = _entity(document, value, field_name)
            if entity["type"] != "lowalt:UAV":
                _fail(field_name, f"{value} is not a lowalt UAV", "此实体不是低空无人机")
            ids = [value]
        for identity in ids:
            if identity not in result:
                result.append(identity)
    return result


def _cooperation_module() -> Any:
    try:
        return importlib.import_module("aeroagentsim.packs.lowalt.cooperation")
    except ImportError as exc:
        raise OperationError("cooperation", "Cooperation capability is not installed; integrate packs/lowalt/cooperation.py / 协同能力尚未安装，请集成 cooperation.py") from exc


def _cooperation(document: dict) -> dict:
    _cooperation_module()
    _, tasks = _engine(document, "lowalt_tasks")
    if "cooperation" not in tasks:
        tasks["cooperation"] = {
            "handoff_points": [], "incidents": [],
            "watchers": {"vehicles": [], "notice_radius_m": 150.0},
            "responder": {"mode": "prescribed", "order": ["respond_now", "request_handoff", "finish_then_respond", "report_only"],
                          "rules": {"protected_task_kinds": ["medical"], "require_replacement": True,
                                    "min_battery_pct": 30.0, "max_response_distance_m": 600.0}},
            "helpers": {"vehicles": [], "ask_radius_m": 500.0, "mode": "prescribed",
                        "rules": {"accept_when": "idle", "min_battery_pct": 25.0}, "selection": "nearest"},
            "photograph": {"dwell_s": 5.0, "altitude_m": 35.0},
        }
    return tasks["cooperation"]


def _drop_generated(document: dict, coop: dict) -> None:
    ids = {("lowalt:HandoffPoint", point["id"]) for point in coop.get("handoff_points", [])}
    for incident in coop.get("incidents", []):
        ids |= {("lowalt:Incident", incident["id"]), ("lowalt:Handoff", "handoff-" + incident["id"])}
    document["entities"] = [e for e in document["entities"] if (e["type"], e["id"]) not in ids]
    bindings = document.get("bindings", {})
    for key in ("rules", "lifecycle"):
        if key in bindings:
            bindings[key] = [row for row in bindings[key] if (row.get("type"), row.get("ids")) not in ids]


def _merge(target: dict, changes: dict) -> None:
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _group_id(group: dict) -> str:
    if "id" in group:
        return group["id"]
    tasks = group["tasks"]
    if len(tasks) > 1 and all(re.sub(r"[-.]\d+$", "", t) == re.sub(r"[-.]\d+$", "", tasks[0]) for t in tasks):
        return re.sub(r"[-.]\d+$", "", tasks[0])
    return tasks[0] if tasks else group["kind"]


def _group(tasks: dict, identity: str) -> dict:
    matches = [g for g in tasks["generators"] if _group_id(g) == identity]
    if len(matches) != 1:
        _fail("id", f"Workload {identity!r} is absent or ambiguous; use its summary id", "工作量不存在或有歧义，请使用摘要中的标识")
    return matches[0]


def _arrivals(release: dict, count: int, duration_ns: int) -> dict:
    if release["mode"] == "scheduled":
        times = release["times_s"]
        if len(times) != count or times != sorted(times):
            _fail("release.times_s", "Provide one nondecreasing release time per task", "每个任务需要一个非递减释放时间")
        return {"mode": "scheduled", "times_ns": [round(t * SECOND) for t in times]}
    start = round(release.get("start_s", 0) * SECOND)
    end = round(release.get("end_s", duration_ns / SECOND) * SECOND)
    if end <= start:
        _fail("release.end_s", "Release end must follow start", "释放结束必须晚于开始")
    return {"mode": "poisson", "rate_per_s": float(release["rate_per_s"]), "start_ns": start, "end_ns": end,
            **({"burst_size": release["burst_size"]} if "burst_size" in release else {})}


def _endpoints(document: dict, values: list[str], field_name: str, food: bool = False) -> None:
    for identity in values:
        entity = _entity(document, identity, field_name)
        if "lowalt.site_position" not in entity["facts"]:
            _fail(field_name, f"{identity} is not a facility; add_place first", "此实体不是设施，请先使用 add_place")
        if food and entity["type"] != "lowalt:Restaurant":
            _fail(field_name, f"Meal origin {identity} must be a kitchen", "餐食起点必须是厨房")


def _food_bindings(document: dict, owner: str) -> None:
    for type_id, fields in (("lowalt:Restaurant", ["lowalt.food_preparation_s"]),
                            ("lowalt:FoodOrder", [f for f in FOOD_FIELDS if f != "lowalt.food_preparation_s"])):
        rule = next((r for r in document["bindings"]["rules"] if r["writer"] == owner and r["type"] == type_id), None)
        if rule is None:
            document["bindings"]["rules"].append({"writer": owner, "type": type_id, "fields": fields})
        else:
            rule["fields"] = list(dict.fromkeys([*rule["fields"], *fields]))


def _idle_kitchens(document: dict, warnings: list[dict]) -> None:
    """Kitchens without meal orders are plain sites: the task engine only produces food facts when meals exist."""
    if not any(e["type"] == "lowalt:Restaurant" for e in document["entities"]):
        return
    owner, tasks = _engine(document, "lowalt_tasks")
    if any(g["kind"] == "food" for g in tasks.get("generators", [])):
        return
    idle = [e for e in document["entities"] if e["type"] == "lowalt:Restaurant" and e["facts"].pop("lowalt.food_preparation_s", None) is not None]
    if not idle:
        return
    rules = document["bindings"]["rules"]
    for rule in rules:
        if rule["writer"] == owner and rule["type"] == "lowalt:Restaurant":
            rule["fields"] = [f for f in rule["fields"] if f != "lowalt.food_preparation_s"]
    document["bindings"]["rules"] = [r for r in rules if r["fields"]]
    names = ", ".join(e["id"] for e in idle)
    warnings.append({"op_id": None, "field": "kitchens", "message": f"Kitchen {names} has no meal orders, so it is only a site; add orders with kind food to cook / 厨房 {names} 没有餐食订单，仅作为站点；请添加 food 类型订单"})


def _add_orders(document: dict, p: dict) -> None:
    owner, tasks = _engine(document, "lowalt_tasks")
    if any(_group_id(g) == p["id"] for g in tasks["generators"]):
        _fail("id", "Workload id is already used", "工作量标识已被使用")
    kind = p["kind"]
    if kind not in {"parcel", "food", "medical", "passenger"} and kind not in tasks.get("work_routes", {}):
        _fail("kind", f"{kind} needs a matching work route already authored in the base", "此任务类型需要起点场景中已有匹配作业路线")
    _endpoints(document, p["from"], "from", food=kind == "food")
    _endpoints(document, p["to"], "to")
    if any(not any(a != b for b in p["to"]) for a in p["from"]):
        _fail("to", "Every origin needs a distinct destination", "每个起点需要不同的目的地")
    group = {"id": p["id"], "kind": kind, "tasks": [f"{p['id']}-{i + 1}" for i in range(p["count"])],
             "origins": p["from"], "destinations": p["to"], "priority": p.get("priority", 1),
             "payload_kg": float(p.get("payload_kg", 1)), "arrivals": _arrivals(p["release"], p["count"], document["run"]["until_ns"])}
    if kind == "food":
        group["delivery_window_s"] = float(p.get("delivery_window_s", 240))
        _overlay(document)
        _food_bindings(document, owner)
    for i, identity in enumerate(group["tasks"]):
        _unique(document, identity)
        document["entities"].append({"id": identity, "type": "lowalt:FoodOrder" if kind == "food" else "lowalt:Task",
            "label": f"{kind.title()} order {i + 1}", "label_zh": f"{_task_label_zh(kind)} {i + 1}",
            "facts": {"lowalt.task_state": "unreleased", "lowalt.task_kind": kind, "lowalt.task_priority": group["priority"],
                      "lowalt.task_origin": p["from"][0], "lowalt.task_destination": p["to"][0], "lowalt.task_created_ns": 0,
                      "lowalt.task_vehicle": "", "lowalt.task_payload_kg": group["payload_kg"]}})
    tasks["generators"].append(group)


def _task_label_zh(kind: str) -> str:
    from aeroagentsim.services.narratives import TASK_NOUN_ZH
    return TASK_NOUN_ZH[kind]


def _allocation_provider(ctx: CompileContext, document: dict) -> None:
    if document.get("algorithms", {}).get("allocation") != "llm":
        return
    if ctx.agent_profile is None:
        _fail("value", "Agent decisions need a connected model", "智能体决策需要已连接模型")
    from aeroagentsim.algorithms.llm import engine_config
    _, tasks = _engine(document, "lowalt_tasks")
    configuration = {"provider": {"profile": ctx.agent_profile}}
    tasks.setdefault("algorithm_config", {})["allocation"] = configuration
    document["engines"]["lowalt_allocation"] = {"plugin": "langgraph", "config": engine_config(configuration)}


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


def _refresh_controls(ctx: CompileContext, scene: BaseScene, document: dict, warnings: list[dict]) -> None:
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


def _admit_task_kind(document: dict, kind: str) -> None:
    """Older starting points declare a narrower task-kind enum; admit a supported kind in canonical order."""
    from aeroagentsim.packs.lowalt.overlay import TASK_KINDS

    for row in document.get("registry", {}).get("fields", []):
        enum = row.get("schema", {}).get("enum") if row.get("id") == "lowalt.task_kind" else None
        if isinstance(enum, list) and kind not in enum and kind in TASK_KINDS:
            row["schema"]["enum"] = [value for value in TASK_KINDS if value in enum or value == kind]


def _apply_operation(ctx: CompileContext, scene: BaseScene, document: dict, name: str, p: dict,
                     directory: Path, warnings: list[dict], op_id: str) -> None:
    if name == "set_control":
        if scene.template_id is None:
            _fail("control", "This workspace has no template controls", "此工作区没有模板控件")
        knobs = {k["id"]: k for k in ctx.templates.get(scene.template_id)["knobs"]}
        if p["control"] not in knobs:
            _fail("control", "Choose a declared control from this starting point", "请选择此起点已声明的控件")
        knob = knobs[p["control"]]
        try:
            _knob_value(knob, p["value"])
            value = float(p["value"]) if knob["type"] == "number" else p["value"]
            if "pointer" in knob:
                _pointer(document, knob["pointer"], value)
            else:
                ctx.templates.hooks[knob["apply"]](document, directory, value)
        except (KeyError, ValueError, TypeError) as exc:
            raise OperationError("value", f"{exc} / 模板控件校验失败") from exc
        scene.controls[p["control"]] = value
        if knob.get("pointer") == "/algorithms/allocation":
            _allocation_provider(ctx, document)
        return
    if name == "set_duration":
        document["run"]["until_ns"] = round(p["seconds"] * SECOND)
        if document["run"]["until_ns"] < 1:
            _fail("seconds", "Duration must be at least one nanosecond", "时长必须至少为一纳秒")
        for stream in document.get("ingress_streams", []):
            if stream["initial_watermark_ns"] != 0:
                stream["initial_watermark_ns"] = document["run"]["until_ns"]
        return
    if name == "set_run_mode":
        streams = document.get("ingress_streams", [])
        if not any(stream["id"] == "operator" for stream in streams):
            _fail("mode", "Starting scene has no interactive ingress stream", "起点场景没有交互输入流")
        for stream in streams:
            if stream["id"] == "operator":
                stream["initial_watermark_ns"] = 0 if p["mode"] == "interactive" else document["run"]["until_ns"]
                stream.pop("timeout_s", None)
        return
    if name in {"set_wind", "add_gust"}:
        profiles = [profile["lowalt.wind"] for e in document["engines"].values() if e["plugin"] == "environment"
                    for profile in e["config"].get("profiles", {}).values() if "lowalt.wind" in profile]
        if not profiles:
            _fail("speed_mps", "Starting scene has no lowalt wind profile", "起点场景没有低空风场配置")
        if name == "add_gust" and p["end_s"] <= p["start_s"]:
            _fail("end_s", "Gust end must follow start", "阵风结束必须晚于开始")
        if name == "add_gust" and round(p["end_s"] * SECOND) <= round(p["start_s"] * SECOND):
            _fail("end_s", "Gust interval must span at least one nanosecond", "阵风间隔必须至少一纳秒")
        for profile in profiles:
            if name == "set_wind":
                profile["value"] = [float(p["speed_mps"]), 0.0, 0.0]
            else:
                if any(p["start_s"] * SECOND < g["end_ns"] and p["end_s"] * SECOND > g["start_ns"] for g in profile.get("gusts", [])):
                    _fail("start_s", "Gust overlaps an existing interval; choose a separate interval", "阵风与已有时段重叠，请选择独立时段")
                profile["mode"] = "gust"
                profile.setdefault("gusts", []).append({"start_ns": round(p["start_s"] * SECOND), "end_ns": round(p["end_s"] * SECOND), "value": [float(p["speed_mps"]), 0.0, 0.0]})
        return
    if name == "set_algorithm":
        from aeroagentsim.services.algorithm_catalog import discover_algorithm_catalog
        installed = discover_algorithm_catalog()
        if p["value"] not in {a["id"] for a in installed[p["slot"]]}:
            _fail("value", "Select an installed algorithm from the catalogue", "请选择目录中已安装的算法")
        if "algorithms" not in document:
            _fail("slot", "Starting scene has no lowalt algorithm slots", "起点场景没有低空算法槽")
        if p["value"] == "llm" and ctx.agent_profile is None:
            _fail("value", "Agent decisions need a connected model", "智能体决策需要已连接模型")
        document["algorithms"][p["slot"]] = p["value"]
        if p["value"] == "llm":
            _allocation_provider(ctx, document)
        return
    if name == "set_rule":
        value, rule = p["value"], p["rule"]
        if rule == "medical_preemption":
            if type(value) is not bool:
                _fail("value", "medical_preemption requires true or false", "医疗抢占规则需要布尔值")
            _engine(document, "lowalt_tasks")[1][rule] = value
            return
        if type(value) not in (int, float) or (rule == "battery_reserve_pct" and not 0 <= value <= 100) or (rule == "safety_distance_m" and value <= 0):
            _fail("value", "Use a valid numeric rule value (reserve 0–100; distance > 0)", "请使用有效规则数值（预留电量 0–100；间隔大于 0）")
        predicates = _rule_package(document, directory)["predicates"]
        if rule == "battery_reserve_pct":
            predicates["battery_below_reserve"]["parameters"]["reserve_percent"] = float(value)
        elif rule == "safety_distance_m":
            predicates["closer_than_safety_distance"]["parameters"]["horizontal_m"] = float(value)
        else:
            parameters = predicates["wind_above_limit"]["parameters"]
            for key in parameters:
                parameters[key] = float(value)
        return
    if name in {"add_place", "update_place", "remove_place"}:
        if name == "add_place":
            _engine(document, "lowalt_tasks")
            _unique(document, p["id"])
            if "preparation_s" in p and p["role"] != "kitchen":
                _fail("preparation_s", "Preparation time applies only to kitchens", "准备时间只适用于厨房")
            if "power_w" in p and p["role"] != "charging_pad":
                _fail("power_w", "Charging power applies only to charging pads", "充电功率只适用于充电位")
            if p["role"] == "handoff_point":
                if "capacity" in p:
                    _fail("capacity", "Handoff storage capacity is not modeled", "交接储物容量尚未建模")
                coop = _cooperation(document)
                coop["handoff_points"].append({"id": p["id"], "position": _position(document, p["at"], "at"),
                    "label": p.get("label", p["id"]), "label_zh": p.get("label_zh", "交接点 " + p["id"])})
                return
            _overlay(document)
            # The role is the contract type (lowalt:Restaurant, lowalt:Customer…); a separate role fact would
            # need a writer binding the simulator does not have.
            facts = {"lowalt.site_position": _position(document, p["at"], "at"), "lowalt.site_open": True, "lowalt.site_capacity": p.get("capacity", 1)}
            if p["role"] == "kitchen":
                facts["lowalt.food_preparation_s"] = float(p.get("preparation_s", 30))
                # A base without meals has no writer for kitchen facts until one is declared.
                _food_bindings(document, _engine(document, "lowalt_tasks")[0])
            if p["role"] == "charging_pad":
                facts["lowalt.pad_power_w"] = float(p.get("power_w", 5000))
            document["entities"].append({"id": p["id"], "type": "lowalt:" + PLACE_TYPES[p["role"]],
                "label": p.get("label", p["id"]), "label_zh": p.get("label_zh", p["id"]), "facts": facts})
            return
        _, tasks = _engine(document, "lowalt_tasks")
        handoffs = tasks.get("cooperation", {}).get("handoff_points", [])
        point = next((r for r in handoffs if r["id"] == p["id"]), None)
        if point is not None:
            for key in ("capacity", "preparation_s", "power_w"):
                if key in p:
                    _fail(key, "This setting is not modeled on handoff points", "交接点不模拟此设置")
            if name == "remove_place":
                if tasks["cooperation"].get("incidents"):
                    _fail("id", "Handoff point is used by cooperation; revise cooperation first", "交接点正被协同配置使用，请先修改协同配置")
                handoffs.remove(point)
            else:
                if "at" in p:
                    point["position"] = _position(document, p["at"], "at")
                for key in ("label", "label_zh"):
                    if key in p:
                        point[key] = p[key]
            return
        entity = _entity(document, p["id"], "id")
        if "lowalt.site_position" not in entity["facts"]:
            _fail("id", "Select a facility", "请选择设施")
        if name == "remove_place":
            # Search authored references outside the entity being removed.
            rest = copy.deepcopy(document)
            rest["entities"].remove(next(e for e in rest["entities"] if e["id"] == p["id"]))
            def referenced(value: Any) -> bool:
                if isinstance(value, dict):
                    return any(referenced(v) for v in value.values())
                if isinstance(value, list):
                    return any(referenced(v) for v in value)
                return value == p["id"]
            if referenced(rest):
                _fail("id", "Place is referenced by tasks, rules or events; revise those first", "地点被任务、规则或事件引用，请先修改引用")
            document["entities"].remove(entity)
        else:
            if "at" in p:
                entity["facts"]["lowalt.site_position"] = _position(document, p["at"], "at")
            for key in ("label", "label_zh"):
                if key in p:
                    entity[key] = p[key]
            for key, fact in (("capacity", "lowalt.site_capacity"), ("preparation_s", "lowalt.food_preparation_s"), ("power_w", "lowalt.pad_power_w")):
                if key in p:
                    if key == "preparation_s" and entity["type"] != "lowalt:Restaurant" or key == "power_w" and entity["type"] != "lowalt:ChargingPad":
                        _fail(key, "Parameter does not belong to this facility type", "此参数不属于该设施类型")
                    entity["facts"][fact] = p[key] if key == "capacity" else float(p[key])
        return
    if name == "add_fleet":
        _, tasks = _engine(document, "lowalt_tasks")
        if _fleet_entities(document, p["id"]):
            _fail("id", "Fleet id already used", "机群标识已被使用")
        _unique(document, p["id"])
        _overlay(document)
        vehicle_class, kinds, label, label_zh = ROLES[p["role"]]
        template = document.get("generators", {}).get("fleet")
        if template is None:
            _fail("base", "Add UAV fleets to a lowalt starting point", "请在低空起点中添加无人机群")
        base_kinds = tasks.setdefault("vehicle_kinds", {}).get(template["facts"]["lowalt.vehicle_class"], list(kinds))
        overrides = tasks.setdefault("vehicle_task_kinds", {})
        for i in range(template["count"]):
            overrides.setdefault(template["prefix"] + str(i + 1), list(base_kinds))
        tasks["vehicle_kinds"].setdefault(vehicle_class, list(kinds))
        tasks.setdefault("cruise_altitudes", {}).setdefault(vehicle_class, 125.0)
        for i in range(p["count"]):
            identity = f"{p['id']}-{i + 1}"
            _unique(document, identity)
            facts = copy.deepcopy(template["facts"])
            facts.update({"lowalt.position": _position(document, p["start"], "start"), "lowalt.vehicle_class": vehicle_class,
                          "lowalt.wind_limit_mps": WIND_LIMITS[vehicle_class]})
            if "battery_pct" in p:
                facts["lowalt.soc"] = p["battery_pct"] / 100
                facts["lowalt.motion_energy_j"] = facts["lowalt.capacity_j"] * facts["lowalt.soc"]
            facts["lowalt.payload_capacity_kg"] = float(p.get("payload_capacity_kg", 150 if vehicle_class == "passenger" else template["facts"]["lowalt.payload_capacity_kg"]))
            document["entities"].append({"id": identity, "type": "lowalt:UAV", "label": f"{label} {i + 1}", "label_zh": f"{label_zh} {i + 1}", "vehicle_class": vehicle_class, "facts": facts})
            overrides[identity] = list(p.get("task_kinds", kinds))
        return
    if name in {"set_fleet", "set_vehicle"}:
        _, tasks = _engine(document, "lowalt_tasks")
        fleet = document.get("generators", {}).get("fleet")
        if name == "set_fleet" and fleet and p["fleet"] in {"fleet", fleet["prefix"].rstrip("-.")}:
            if "count" in p:
                fleet["count"] = p["count"]
                old_overrides = tasks.get("vehicle_task_kinds", {})
                base_kinds = tasks.get("vehicle_kinds", {}).get(fleet["facts"]["lowalt.vehicle_class"])
                if base_kinds is not None:
                    for i in range(fleet["count"]):
                        old_overrides.setdefault(fleet["prefix"] + str(i + 1), list(base_kinds))
            if "battery_pct" in p:
                fleet["facts"]["lowalt.soc"] = p["battery_pct"] / 100
                fleet["facts"]["lowalt.motion_energy_j"] = fleet["facts"]["lowalt.capacity_j"] * p["battery_pct"] / 100
            if "start" in p:
                fleet["starts"] = [_position(document, p["start"], "start")]
            return
        if name == "set_vehicle":
            if fleet and p["vehicle"].startswith(fleet["prefix"]):
                _fail("vehicle", "Base fleet members are regenerated from one prototype; adjust set_fleet or add an explicit fleet for individual states", "基础机群成员由单一原型重新生成；请调整 set_fleet 或添加显式机群以设置单机状态")
            members = [_entity(document, p["vehicle"], "vehicle")]
            if members[0]["type"] != "lowalt:UAV":
                _fail("vehicle", "Select a lowalt UAV", "请选择低空无人机")
        else:
            members = _fleet_entities(document, p["fleet"])
            if not members:
                prefixes = sorted({re.sub(r"-\d+$", "", e["id"]) for e in document["entities"] if e.get("type") == "lowalt:UAV"})
                en, zh = _closest(p["fleet"], prefixes)
                _fail("fleet", f"Fleet does not exist; use its summary id{en}", f"机群不存在，请使用摘要中的标识{zh}")
            count = p.get("count", len(members))
            if count > len(members):
                prototype = members[0]
                for i in range(len(members), count):
                    identity = f"{p['fleet']}-{i + 1}"
                    _unique(document, identity)
                    entity = copy.deepcopy(prototype); entity["id"] = identity
                    # Members are named "<role label> <n>"; new members continue the numbering.
                    entity.update(label=re.sub(r"\s*\d+$", "", prototype.get("label", p["fleet"])) + f" {i + 1}",
                                  label_zh=re.sub(r"\s*\d+$", "", prototype.get("label_zh", p["fleet"])) + f" {i + 1}")
                    document["entities"].append(entity); members.append(entity)
                    tasks.setdefault("vehicle_task_kinds", {})[identity] = list(tasks["vehicle_task_kinds"][prototype["id"]])
            elif count < len(members):
                removed = {e["id"] for e in members[count:]}
                coop = tasks.get("cooperation", {})
                if removed & set(coop.get("watchers", {}).get("vehicles", []) + coop.get("helpers", {}).get("vehicles", [])):
                    _fail("count", "Removed vehicles are referenced by cooperation; revise responsibilities first", "待删除无人机被协同配置引用，请先调整责任")
                document["entities"] = [e for e in document["entities"] if e["id"] not in removed]
                for identity in removed:
                    tasks.get("vehicle_task_kinds", {}).pop(identity, None)
                members = members[:count]
        for entity in members:
            if "battery_pct" in p:
                entity["facts"]["lowalt.soc"] = p["battery_pct"] / 100
                entity["facts"]["lowalt.motion_energy_j"] = entity["facts"]["lowalt.capacity_j"] * p["battery_pct"] / 100
            if "start" in p:
                entity["facts"]["lowalt.position"] = _position(document, p["start"], "start")
        return
    if name == "add_orders":
        _admit_task_kind(document, p["kind"])
        _add_orders(document, p)
        return
    if name in {"set_orders", "remove_orders"}:
        _, tasks = _engine(document, "lowalt_tasks")
        group = _group(tasks, p["id"])
        if name == "remove_orders":
            from aeroagentsim.services.narratives import _entity_id
            task_ids = set(group["tasks"])
            if any(_entity_id(c.get("payload", {}).get("payload", {}).get("task")) in task_ids for c in document["bindings"].get("commands", [])):
                _fail("id", "Workload is referenced by a scheduled operator event", "工作量被预设操作事件引用")
            document["entities"] = [e for e in document["entities"] if e["id"] not in task_ids]
            tasks["generators"].remove(group)
            return
        if "delivery_window_s" in p and group["kind"] != "food":
            _fail("delivery_window_s", "Delivery windows apply only to meal workloads", "配送时间窗只适用于餐食工作量")
        release = p.get("release")
        if release is None:
            arrivals = group["arrivals"]
            release = {"mode": arrivals["mode"]}
            if arrivals["mode"] == "scheduled":
                release["times_s"] = [t / SECOND for t in arrivals["times_ns"]]
            else:
                release.update(rate_per_s=arrivals["rate_per_s"], start_s=arrivals["start_ns"] / SECOND)
                if "end_ns" in arrivals:
                    release["end_s"] = arrivals["end_ns"] / SECOND
                if "burst_size" in arrivals:
                    release["burst_size"] = arrivals["burst_size"]
        new = {"id": p["id"], "kind": group["kind"], "count": p.get("count", len(group["tasks"])),
               "from": p.get("from", group["origins"]), "to": p.get("to", group["destinations"]), "release": release,
               "priority": p.get("priority", group["priority"]), "payload_kg": p.get("payload_kg", group["payload_kg"])}
        if group["kind"] == "food":
            new["delivery_window_s"] = p.get("delivery_window_s", group["delivery_window_s"])
        old_ids = set(group["tasks"])
        old_entities = {e["id"]: copy.deepcopy(e) for e in document["entities"] if e["id"] in old_ids}
        document["entities"] = [e for e in document["entities"] if e["id"] not in old_ids]
        tasks["generators"].remove(group)
        _add_orders(document, new)
        for i, entity in enumerate(document["entities"]):
            if entity["id"] in old_entities:
                original = old_entities[entity["id"]]
                for parameter, fact in (("priority", "lowalt.task_priority"), ("payload_kg", "lowalt.task_payload_kg"),
                                        ("from", "lowalt.task_origin"), ("to", "lowalt.task_destination")):
                    if parameter in p:
                        original["facts"][fact] = entity["facts"][fact]
                document["entities"][i] = original
        return
    if name == "set_task":
        _, tasks = _engine(document, "lowalt_tasks")
        entity = _entity(document, p["task"], "task")
        group = next((g for g in tasks["generators"] if p["task"] in g["tasks"]), None)
        if group is None:
            _fail("task", "Select a lowalt task from a workload", "请选择工作量中的低空任务")
        group.setdefault("id", _group_id(group))
        single = copy.deepcopy(group)
        index = group["tasks"].index(p["task"])
        single["tasks"] = [p["task"]]
        single["id"] = p["task"]
        if group["arrivals"]["mode"] == "scheduled":
            time_ns = group["arrivals"]["times_ns"].pop(index)
            single["arrivals"] = {"mode": "scheduled", "times_ns": [time_ns]}
        elif "release_s" not in p:
            _fail("release_s", "A single task in a rate workload needs an explicit release time when edited", "修改速率工作量中的单个任务需要明确释放时间")
        if "release_s" in p:
            single["arrivals"] = {"mode": "scheduled", "times_ns": [round(p["release_s"] * SECOND)]}
        if "destination" in p:
            _endpoints(document, [p["destination"]], "destination")
            if p["destination"] in single["origins"]:
                _fail("destination", "Destination must differ from every possible origin", "目的地必须不同于所有可能起点")
            single["destinations"] = [p["destination"]]
            entity["facts"]["lowalt.task_destination"] = p["destination"]
        if "priority" in p:
            single["priority"] = p["priority"]
            entity["facts"]["lowalt.task_priority"] = p["priority"]
        group["tasks"].remove(p["task"])
        if not group["tasks"]:
            tasks["generators"].remove(group)
        tasks["generators"].append(single)
        return
    if name in {"add_incident", "set_incident", "remove_incident"}:
        coop = _cooperation(document)
        incident = next((r for r in coop["incidents"] if r["id"] == p["id"]), None)
        if name == "add_incident":
            if incident is not None:
                _fail("id", "Incident id already used", "事件标识已被使用")
            _unique(document, p["id"])
            incident = {"id": p["id"], "kind": p["kind"], "label": p.get("label", "Person fell on the street"),
                        "label_zh": p.get("label_zh", "行人在街道跌倒")}
            coop["incidents"].append(incident)
        elif incident is None:
            _fail("id", "Incident does not exist", "事件不存在")
        if name == "remove_incident":
            coop["incidents"].remove(incident)
            return
        if "at" in p:
            incident["position"] = _position(document, p["at"], "at")
        if "time_s" in p:
            incident["at_s"] = float(p["time_s"])
        for key in ("kind", "label", "label_zh"):
            if key in p:
                incident[key] = p[key]
        return
    if name == "set_cooperation":
        coop = _cooperation(document)
        for key in ("watchers", "helpers"):
            if "vehicles" in p.get(key, {}):
                p[key]["vehicles"] = _vehicles(document, p[key]["vehicles"], f"{key}.vehicles")
        if "handoff_points" in p:
            points = []
            for point in p["handoff_points"]:
                if isinstance(point, str):
                    existing = next((r for r in coop["handoff_points"] if r["id"] == point), None)
                    if existing:
                        points.append(copy.deepcopy(existing)); continue
                    existing_site = next((e for e in document["entities"] if e["id"] == point), None)
                    point_id = "handoff-" + point if existing_site is not None and existing_site["type"] != "lowalt:HandoffPoint" else point
                    if point_id != point:
                        warnings.append({"op_id": op_id, "field": "handoff_points", "message": f"Handoff at {point} uses a separate point entity {point_id} at the same position / 在 {point} 交接使用同位置的独立交接点实体 {point_id}"})
                    point = {"id": point_id, "at": point}
                point = copy.deepcopy(point)
                if "at" in point and "position" in point:
                    _fail("handoff_points", "Supply at or position, not both", "仅提供 at 或 position 其中一个")
                if "at" in point:
                    point["position"] = _position(document, point.pop("at"), "handoff_points.at")
                elif "position" not in point:
                    _fail("handoff_points", "Provide at or position for each handoff point", "每个交接点需要 at 或 position")
                else:
                    point["position"] = _position(document, point["position"], "handoff_points.position")
                point.setdefault("label", point["id"]); point.setdefault("label_zh", "交接点 " + point["id"])
                points.append(point)
            p["handoff_points"] = points
        # Generated points and incidents follow the configuration: drop the old ones so expansion rebuilds them.
        _drop_generated(document, coop)
        _merge(coop, p)
        if any(coop.get(k, {}).get("mode") == "agent" for k in ("responder", "helpers")):
            if ctx.agent_profile is None:
                _fail("mode", "Agent decisions need a connected model", "智能体决策需要已连接模型")
            coop["provider"] = {"profile": ctx.agent_profile}
        return
    if name == "remove_cooperation":
        _cooperation_module()
        _engine(document, "lowalt_tasks")[1].pop("cooperation", None)
        removed_types = {"lowalt:Incident", "lowalt:Handoff", "lowalt:HandoffPoint"}
        document["entities"] = [e for e in document["entities"] if e["type"] not in removed_types]
        bindings = document["bindings"]
        bindings["rules"] = [r for r in bindings.get("rules", []) if r["type"] not in removed_types]
        bindings["lifecycle"] = [r for r in bindings.get("lifecycle", []) if r["type"] not in removed_types]
        for rule in bindings["rules"]:
            rule["fields"] = [f for f in rule["fields"] if not f.startswith(("lowalt.incident_", "lowalt.handoff_"))]
        removed_engines = {identity for identity, engine in document["engines"].items()
                           if engine["plugin"] == "langgraph" and engine["config"].get("factory", "").startswith("aeroagentsim.packs.lowalt.cooperation:")}
        for identity in removed_engines:
            del document["engines"][identity]
        return
    if name == "schedule_event":
        _schedule_event(document, p, directory)
        return
    _fail("op", f"Unsupported operation {name}", "此操作尚未实现")


def _schedule_event(document: dict, p: dict, directory: Path) -> None:
    from aerokernel.ids import EntityRef
    from aeroagentsim.packs.lowalt.everyday import _operators
    from .operations import validate_parameters
    from .scene import _registry_types
    points: dict[str, str] = {}
    specs = document.get("behaviours", [])
    for spec in specs:
        package = yaml.load((directory / spec["path"]).read_bytes(), Loader=UniqueLoader) if "path" in spec else spec
        points.update({r["id"]: r["emits"] for r in package.get("injection_points", [])})
    point_id = p["event"]
    standard = {n: "lowalt.operator." + n for n in ("close_vertiport", "add_urgent_order", "restaurant_delay", "change_dropoff", "prioritize_medical", "investigate")}
    schema_id = points.get(point_id, standard.get(point_id))
    if schema_id is None:
        _fail("event", "Choose a base injection point or standard lowalt operator event", "请选择起点注入点或标准低空操作事件")
    if point_id not in points:
        _engine(document, "lowalt_tasks")
        _overlay(document)
        package = next((s for s in specs if s.get("id") == "lowalt.operator"), None)
        if package is not None:
            package["injection_points"].append({"id": point_id, "stream_id": "operator", "command": "aas.runtime.inject_event", "target": "behaviour", "emits": schema_id, "topic": "lowalt.events"})
        else:
            _operators(document, {point_id: schema_id}, [])
    descriptor = next((m for m in document["registry"].get("messages", []) if m["id"] == schema_id), None)
    if descriptor is None:
        _fail("event", "Injection schema is not declared in the scene registry", "场景注册表没有声明此注入模式")
    def json_schema(schema: dict) -> dict:
        kind = schema["type"]
        if kind == "record":
            return {"type": "object", "properties": {k: json_schema(v) for k, v in schema["members"].items()}, "required": schema["required"], "additionalProperties": schema.get("extra", False)}
        if kind == "ref":
            return {"type": "string", "minLength": 1}
        if kind == "vector":
            return {"type": "array", "minItems": schema["length"], "maxItems": schema["length"], "items": json_schema(schema["items"])}
        return copy.deepcopy(schema)
    validate_parameters(p["params"], json_schema(descriptor["schema"]), "params")
    for key in ("site", "task", "origin", "destination", "restaurant"):
        if key in p["params"]:
            _entity(document, p["params"][key], f"params.{key}")
    types = _registry_types(document, directory)
    def typed_payload(value: Any, schema: dict, field_name: str) -> Any:
        kind = schema["type"]
        if kind == "ref":
            entity = _entity(document, value, field_name)
            remaining = [entity["type"]]
            seen = set()
            while remaining:
                current = remaining.pop()
                if current == schema["target_type"]:
                    return {"$ref": EntityRef(document["id"], "0", entity["id"], 0, entity["type"]).to_data()}
                if current not in seen:
                    seen.add(current); remaining.extend(types.get(current, {}).get("parents", []))
            _fail(field_name, f"Select an entity of type {schema['target_type']}", "请选择符合目标契约类型的实体")
        if kind == "number":
            return float(value)
        if kind == "record":
            return {k: typed_payload(v, schema["members"][k], field_name + "." + k) for k, v in value.items()}
        if kind in {"array", "vector"}:
            return [typed_payload(v, schema["items"], field_name + "[]") for v in value]
        return value
    payload = typed_payload(p["params"], descriptor["schema"], "params")
    document["bindings"].setdefault("commands", []).append({"schema": "aas.runtime.inject_event", "target": "behaviour", "at_ns": round(p["at_s"] * SECOND), "payload": {"injection_point": point_id, "payload": payload}})


def _places_first(operations: list[dict]) -> list[int]:
    """Application order: new places before the operations that use them.

    Models often list the orders first and the customers they deliver to after. A new place
    depends on nothing else in the plan, so it is applied first unless an earlier operation
    moves or removes a place with the same id."""
    def early(i: int) -> bool:
        operation = operations[i]
        if not isinstance(operation, dict) or operation.get("op") != "add_place" or not isinstance(operation.get("params"), dict):
            return False
        identity = operation["params"].get("id")
        return not any(isinstance(o, dict) and o.get("op") in {"update_place", "remove_place"}
                       and isinstance(o.get("params"), dict) and o["params"].get("id") == identity for o in operations[:i])
    return sorted(range(len(operations)), key=lambda i: not early(i))


def compile_plan(ctx: CompileContext, base: dict, operations: list[dict], out_dir: Path) -> CompileResult:
    from .scene import diff, expectations, interactions, summarize
    out_dir = Path(out_dir)
    files = out_dir / "files"
    errors: list[dict] = []
    warnings: list[dict] = []
    document: dict = {}
    scene: BaseScene | None = None
    base_summary: dict = {}
    try:
        scene = resolve_base(ctx, base)
        if out_dir.resolve().is_relative_to(scene.directory.resolve()) or scene.directory.resolve().is_relative_to(files.resolve()):
            _fail("out_dir", "Plan directory must be outside the base scene", "计划目录必须位于起点场景之外")
    except Exception as exc:  # noqa: BLE001 - no output may replace the source scene
        return CompileResult(False, [{"op_id": None, "field": getattr(exc, "field", "base"), "message": str(exc)}], [], {}, files, _digest({}, None))
    out_dir.mkdir(parents=True, exist_ok=True)
    if files.exists():
        shutil.rmtree(files)
    files.mkdir()
    try:
        _copy_files(scene.directory, files)
        document = _rebase(scene.document, scene.directory, files)
        baseline = load_scenario(document, base=files)
        base_summary = summarize(baseline.document, directory=files, controls=scene.controls)
        # Keep the authored document: loader expansion must remain repeatable.
        from aeroagentsim.packs.lowalt.template import expand as expand_template

        def ensure_contracts(target: dict) -> None:
            """Cooperation needs the frozen AeroGraph incident/cargo-transfer contracts in the plan's snapshot."""
            if not any(e["plugin"] == "lowalt_tasks" and "cooperation" in e.get("config", {}) for e in target["engines"].values()):
                return
            snapshot = target["registry"].get("snapshot")
            if snapshot is None:
                snapshot = "registry.plan.snapshot.json"
                baseline.compiled.write_snapshot(files / snapshot)
                target["registry"].pop("compile", None)
                target["registry"]["snapshot"] = snapshot
            path = Path(snapshot) if Path(snapshot).is_absolute() else files / snapshot
            if not path.resolve().is_relative_to(files.resolve()):
                _fail("registry.snapshot", "Registry snapshot must be inside plan files", "注册表快照必须位于计划文件内部")
            _write(path, _cooperation_module().merge_contract_snapshot(json.loads(path.read_text())))

        def expand(target: dict) -> None:
            # Expansion reads the snapshot; like the loader, resolve it against the plan files.
            ensure_contracts(target)
            snapshot = target["registry"].get("snapshot")
            if snapshot is not None and not Path(snapshot).is_absolute():
                target["registry"]["snapshot"] = str(files / snapshot)
            try:
                expand_template(target)
            finally:
                if snapshot is not None:
                    target["registry"]["snapshot"] = snapshot

        if "generators" in document:
            expand(document)
        seen: set[str] = set()
        if not isinstance(operations, list):
            _fail("operations", "Operations must be a list", "操作必须为列表")
        for i in _places_first(operations):
            operation = operations[i]
            op_id = operation.get("id", f"op-{i + 1}") if isinstance(operation, dict) else f"op-{i + 1}"
            candidate, candidate_controls = copy.deepcopy(document), copy.deepcopy(scene.controls)
            warning_count = len(warnings)
            try:
                if op_id in seen:
                    _fail("id", "Operation ids must be unique", "操作标识必须唯一")
                seen.add(op_id)
                name, params = validate_operation(operation)
                _apply_operation(ctx, scene, candidate, name, params, files, warnings, op_id)
                if "generators" in candidate:
                    _sync_base_fleet(candidate)
                    expand(candidate)
                document = candidate
            except (OperationError, ValueError, TypeError, KeyError) as exc:
                scene.controls = candidate_controls
                del warnings[warning_count:]
                errors.append({"op_id": op_id, "field": getattr(exc, "field", "params"), "message": str(exc)})
        coop = next((e["config"].get("cooperation") for e in document["engines"].values() if e["plugin"] == "lowalt_tasks"), None)
        if coop is not None and not errors:
            module = _cooperation_module()
            ensure_contracts(document)
            vehicles = {e["id"] for e in document["entities"] if e["type"] == "lowalt:UAV"}
            module.validate_config(coop, vehicles=vehicles)
            snapshot = document["registry"]["snapshot"]
            document["registry"]["snapshot"] = str(files / snapshot) if not Path(snapshot).is_absolute() else snapshot
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
            _idle_kitchens(document, warnings)
            loaded = load_scenario(document, base=files)
            document = loaded.document
            _editable_packages(document)
            _refresh_controls(ctx, scene, document, warnings)
    except Exception as exc:  # noqa: BLE001 - return real compiler/loader diagnostics
        errors.append({"op_id": operations[-1].get("id", f"op-{len(operations)}") if isinstance(operations, list) and operations and isinstance(operations[-1], dict) else None,
                       "field": getattr(exc, "field", getattr(exc, "path", "base")), "message": f"{type(exc).__name__}: {exc}"})
    summary = summarize(document, directory=files, controls=scene.controls) if not errors else {}
    for work in summary.get("workloads", []):
        if "note" in work["release"]:
            warnings.append({"op_id": None, "field": f"workloads.{work['id']}.release", "message": f"{work['release']['note']} / {work['release']['note_zh']}"})
    result = CompileResult(not errors, errors, warnings, document, files, _digest(document, files), summary,
                           diff(base_summary, summary) if not errors else {}, interactions(summary) if not errors else {},
                           expectations(document, summary) if not errors else [])
    _write(out_dir / "scenario.json", document)
    _write(out_dir / "compile.json", {**result.to_data(), "base": base, "base_revision": scene.revision if scene else None,
                                      "template_id": scene.template_id if scene else None, "controls": scene.controls if scene else {}})
    return result


def validate_compiled(ctx: CompileContext, out_dir: Path) -> dict:
    from aeroagentsim.platform.simulation import Simulation
    out_dir = Path(out_dir)
    document = json.loads((out_dir / "scenario.json").read_text())
    record = json.loads((out_dir / "compile.json").read_text())
    try:
        if not record["ok"]:
            raise ValueError("Plan has compile errors / 计划存在编译错误")
        if _digest(document, out_dir / "files") != record["digest"]:
            raise PlanConflict("Compiled plan files changed / 编译后的计划文件已改变")
        loaded = load_scenario(document, base=out_dir / "files")
        with tempfile.TemporaryDirectory(prefix="validation-", dir=out_dir) as scratch:
            simulation = Simulation(loaded, run_directory=Path(scratch))
            simulation.close()
        result = {"valid": True, "errors": [], "issues": []}
    except Exception as exc:  # noqa: BLE001 - match WorkspaceStore diagnostics
        authored = exc
        while authored.__cause__ is not None and not hasattr(authored, "path"):
            authored = authored.__cause__
        path = getattr(authored, "path", "$")
        result = {"valid": False, "errors": [f"{type(exc).__name__}: {exc}"], "issues": [{"source": getattr(authored, "source", "scenario"), "path": path, "message": str(authored), **issue_location(document, path, str(authored))}]}
    _write(out_dir / "validation.json", {**result, "digest": record["digest"]})
    return result


def apply_plan(ctx: CompileContext, out_dir: Path, *, base: dict, workspace_id: str | None, name: str, note: str) -> dict:
    out_dir = Path(out_dir)
    record = json.loads((out_dir / "compile.json").read_text())
    document = json.loads((out_dir / "scenario.json").read_text())
    if base != record["base"] or not record["ok"] or _digest(document, out_dir / "files") != record["digest"]:
        raise PlanConflict("Plan or files changed; compile it again / 计划或文件已改变，请重新编译")
    validation_path = out_dir / "validation.json"
    if not validation_path.exists():
        validate_compiled(ctx, out_dir)
    validation = json.loads(validation_path.read_text())
    if not validation["valid"] or validation["digest"] != record["digest"]:
        raise ValueError("Plan failed validation / 计划校验失败: " + "; ".join(validation["errors"]))
    with ctx.store.lock:
        if "workspace" in base:
            if workspace_id is not None and workspace_id != base["workspace"]:
                raise PlanConflict("Plan belongs to another workspace / 计划属于另一个工作区")
            workspace_id = base["workspace"]
            draft = ctx.store.get(workspace_id)
            if draft["revision"] != record["base_revision"]:
                raise PlanConflict("The draft changed since this plan was made; revise the plan from the current draft / 草稿在计划创建后发生变化，请基于当前草稿修改计划")
        else:
            if workspace_id is not None:
                raise PlanConflict("A template plan creates its own workspace / 模板计划需要创建独立工作区")
            draft = ctx.store.import_template(base["template"], base.get("controls", {}), registry=ctx.templates)
            workspace_id = draft["id"]
        directory = ctx.store.directory(workspace_id)
        _copy_files(out_dir / "files", directory)
        document = _rebase(document, out_dir / "files", directory)
        changes: dict = {"scenario": document, "name": name, "revision": draft["revision"]}
        saved = ctx.store.save(workspace_id, changes)
        if record["template_id"]:
            saved["template"] = {"id": record["template_id"], "knobs": record["controls"]}
            saved = ctx.store._write(saved)
        validation = ctx.store.validate(workspace_id, revision=saved["revision"])
        if not validation["valid"]:
            raise ValueError("Applied draft failed validation; no version was saved / 写入的草稿校验失败，未保存版本: " + "; ".join(validation["errors"]))
        version = ctx.store.save_version(workspace_id, {"revision": saved["revision"], "note": note})
        difference = record["diff_base"]
        report = {"created": difference["added"], "changed": difference["changed"], "removed": difference["removed"],
                  "entities": difference["entities"], "summary_text": f"Configured {name} · version {version['version']} · not running / 已配置 {name} · 版本 {version['version']} · 未运行"}
        return {"workspace_id": workspace_id, "version": version["version"], "revision": saved["revision"], "validation": validation, "report": report}
