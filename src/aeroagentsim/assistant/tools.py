"""Read-only scene adapters and compiled plan tools; no execution or apply tools."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aeroagentsim.services.storage import RunStorage

from .connection import now
from .operations import PLACE_TYPES, OperationError, enum, validate_parameters
from .operations import obj as object_schema
from .sessions import SessionStore

STRING = {"type": "string", "minLength": 1}
STRINGS = {"type": "array", "items": STRING}
QUESTIONS = {"type": "array", "maxItems": 2, "items": {"anyOf": [STRING, {"type": "object", "properties": {"question": STRING, "options": STRINGS, "default": STRING}, "required": ["question"], "additionalProperties": False}]}}
BASE = {"anyOf": [
    {"type": "object", "properties": {"workspace": STRING}, "required": ["workspace"], "additionalProperties": False},
    {"type": "object", "properties": {"template": STRING, "controls": {"type": "object"}}, "required": ["template"], "additionalProperties": False},
]}


def validate_arguments(schema: dict[str, Any], value: Any, path: str = "arguments") -> None:
    try:
        validate_parameters(value, schema, path, tool_input=True)
    except OperationError as error:
        raise ValueError(f"{error.field}: {error}") from error


def operation_catalog() -> tuple[dict[str, Any], str]:
    from .operations import OPERATION_SPECS as OPERATIONS

    lines = []
    branches = []
    for name, spec in OPERATIONS.items():
        params = spec["parameters"]
        fields = ", ".join(params.get("properties", {}))
        lines.append(f"{name}({fields}): {spec['description']}")
        branches.append(object_schema({"id": STRING, "op": {"type": "string", "enum": [name]}, "params": params}, ("op", "params")))
    return {"type": "array", "maxItems": 80, "items": {"anyOf": branches}}, "\n".join(lines)


def tool_schemas() -> list[dict[str, Any]]:
    from .capabilities import CAPABILITY_TOPICS

    operations, catalogue = operation_catalog()
    revision = object_schema({
        "note": STRING, "add": operations, "update": operations, "remove": STRINGS,
        "assumptions": STRINGS, "resolve_questions": STRINGS, "add_questions": QUESTIONS,
        "title": STRING, "summary": STRING, "open_questions": QUESTIONS,
    })
    specs = [
        ("get_current_scene", "Read the saved workspace draft, independent of proposals. Supply id for complete participant facts. An overview is partial, never evidence that an omitted entity was deleted.", object_schema({"id": STRING})),
        ("list_starting_points", "List every scenario template with its control defaults. Use describe_starting_point for control ranges, options and the template scene.", object_schema({})),
        ("describe_starting_point", "Read a template's actual participants and controls.", object_schema({"template_id": STRING}, ("template_id",))),
        ("list_capabilities", "Read supported operations, parameter limits, configured region extracts and installed algorithms.", object_schema({"topic": enum(CAPABILITY_TOPICS)})),
        ("find_places", "Find named catalogue places near an existing participant/place id or an ENU position. Catalogue coordinates belong to the shipped Huangpu frame.", object_schema({"query": STRING, "near": {"anyOf": [STRING, {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}]}, "role": enum(PLACE_TYPES)})),
        ("lookup_contract", "Search real AeroGraph types, fields, relations and ancestry.", object_schema({"term": STRING}, ("term",))),
        ("draft_plan", "Compile a proposed plan now. Operations use {id?, op, params}; ids are assigned by the server. Never applies.\n" + catalogue, object_schema({"base": BASE, "title": STRING, "summary": STRING, "operations": operations, "assumptions": STRINGS, "open_questions": QUESTIONS, "note": STRING}, ("base", "title", "summary", "operations"))),
        ("revise_plan", "Compile a minimal revision. update contains full operations with existing op ids; remove accepts op ids or unambiguous target object/workload ids. Failed compilations retain operations: edit them instead of adding again. open_questions replaces all questions; add_questions/resolve_questions edit them.\n" + catalogue, revision),
        ("restore_operations", "Restore all or selected operation ids from an earlier version; keep unrelated operations.", object_schema({"from_version": {"type": "integer", "minimum": 1}, "op_ids": STRINGS, "note": STRING}, ("from_version", "note"))),
        ("propose_alternatives", "Compile two or three labelled variants of the current plan. Supply complete operations or minimal changes, never both.", object_schema({"variants": {"type": "array", "minItems": 2, "maxItems": 3, "items": object_schema({"label": STRING, "title": STRING, "summary": STRING, "note": STRING, "operations": operations, "changes": revision, "assumptions": STRINGS}, ("label", "title", "summary", "assumptions"))}}, ("variants",))),
        ("explain_participant", "Read full participant facts; auto uses an unapplied proposal, otherwise the saved draft. Choose current for Form edits, proposed for plan review.", object_schema({"id": STRING, "scope": enum(["auto", "current", "proposed"])}, ("id",))),
        ("explain_relation", "Explain configured links in the saved draft or unapplied proposal.", object_schema({"a": STRING, "b": STRING, "scope": enum(["auto", "current", "proposed"])}, ("a", "b"))),
        ("list_runs", "List recorded runs of this session's bound project.", object_schema({})),
        ("run_story", "Read the exact requested run's pinned expectations and journal, including completed Form-saved runs. Locale is optional; no simulation is performed.", object_schema({"run_id": STRING, "locale": {"type": "string", "minLength": 1, "maxLength": 32}}, ("run_id",))),
    ]
    return [{"type": "function", "function": {"name": name, "description": description, "parameters": schema}} for name, description, schema in specs]


LABELS = {
    "get_current_scene": ("Reading the scene", "读取当前场景"),
    "list_starting_points": ("Finding starting points", "查找起始模板"),
    "describe_starting_point": ("Reading the starting point", "读取起始模板"),
    "list_capabilities": ("Checking supported capabilities", "检查支持的能力"),
    "find_places": ("Finding places", "查找地点"),
    "lookup_contract": ("Searching AeroGraph", "检索 AeroGraph"),
    "draft_plan": ("Compiling a proposed plan", "编译待确认方案"),
    "revise_plan": ("Compiling the revision", "编译修改后的方案"),
    "restore_operations": ("Restoring the arrangement", "恢复此前安排"),
    "propose_alternatives": ("Compiling alternatives", "编译备选方案"),
    "explain_participant": ("Reading participant responsibilities", "读取参与者职责"),
    "explain_relation": ("Reading entity relations", "读取对象关系"),
    "list_runs": ("Reading recorded runs", "读取运行记录"),
    "run_story": ("Reading the recorded story", "读取已记录的过程"),
}


def tool_label(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    if name == "lookup_contract" and "term" in arguments:
        term = str(arguments["term"])[:80]
        return f"Searching AeroGraph for '{term}'", f"在 AeroGraph 中查找“{term}”"
    return LABELS.get(name, ("Checking tool request", "检查工具请求"))


def compact_json(value: Any, budget: int = 6500) -> str:
    if isinstance(value, dict) and "coverage" in value and "expectations" in value and "events" in value:
        # The model sees an explicit sample plus full journal coverage, never a raw
        # string slice that can hide a failure or look like an empty recorded run.
        events = value["events"]
        failures = value.get("failures", [])
        from .story import failure_summary

        groups = failure_summary(failures, limit=4)
        summaries = [{**row, "payload": {"reason": row["reason"]} if row["reason"] is not None else {}} for row in groups]
        value = {**{k: value[k] for k in ("run_id", "status", "terminal", "assessment", "coverage", "detail", "expectations_source", "workspace_version", "plan_version", "last_at_ns", "details_url") if k in value},
                 "expectations": [{k: row[k] for k in ("id", "status", "at_ns", "progress") if k in row} for row in value["expectations"]],
                 "events": [{k: e[k][:240] if isinstance(e[k], str) else e[k] for k in ("schema", "text", "at_ns") if k in e} for e in events[-12:]],
                 "events_sampled": len(events) > 12,
                 "failures": summaries, "failure_count": len(failures), "failures_sampled": len(groups) < len({row["schema"] for row in failures})}
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw) <= budget:
        return raw
    if isinstance(value, dict) and "coverage" in value:
        value["expectation_count"] = len(value["expectations"])
        value["coverage"] = copy.deepcopy(value["coverage"])
        schemas = value["coverage"].get("schemas", {})
        if len(schemas) > 12:
            value["coverage"].update(schemas=dict(list(schemas.items())[:12]), schema_count=len(schemas), schemas_sampled=True)
        for key, limit in (("events", 4), ("expectations", 12), ("failures", 2)):
            value[key] = value[key][:limit]
            value[key + "_sampled"] = True
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(raw) <= budget:
            return raw
        value = {**{k: value[k] for k in ("run_id", "status", "terminal", "assessment", "failure_count", "expectation_count", "details_url") if k in value},
                 "coverage": {k: value["coverage"][k] for k in ("recorded_events", "committed_records") if k in value["coverage"]},
                 "truncated": True, "detail": "Read Story details for the full evidence / 完整证据见 Story 详情"}
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return json.dumps({"truncated": True, "preview": raw[:budget - 400], "detail": "Result exceeds context budget; omitted data is not evidence of absence. Query a specific id or narrower topic / 结果超出预算，省略不代表不存在；请查询具体标识或缩小主题"}, ensure_ascii=False)


def operation_diff(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    old, new = ({op["id"]: op for op in ops} for ops in (before, after))
    return {"added": [op for op in after if op["id"] not in old],
            "removed": [op for op in before if op["id"] not in new],
            "changed": [{"id": op["id"], "before": old[op["id"]], "after": op} for op in after if op["id"] in old and old[op["id"]] != op]}


def short_scene(summary: Any) -> Any:
    if not isinstance(summary, dict):
        return summary
    result = {}
    for key, value in summary.items():
        if key == "entity_facts":
            continue
        if key == "entity_ids":
            result[key] = value
        elif isinstance(value, list):
            result[key] = {"count": len(value), "ids": [r["id"] for r in value if isinstance(r, dict) and "id" in r],
                           "items": [{k: row[k] for k in ("id", "label", "label_zh", "role", "count", "kind", "start", "position", "details", "release", "at_s", "time_s") if k in row} if isinstance(row, dict) else row for row in value[:6]], "truncated": len(value) > 6}
        elif key in {"environment", "cooperation", "rules", "algorithms", "counts"}:
            result[key] = value
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def operation_list(operations: list[dict]) -> list[dict]:
    return [{"id": op.get("id"), "op": op.get("op"), "target": next((op.get("params", {})[key] for key in ("id", "fleet", "vehicle", "task", "control", "slot", "rule", "extract") if key in op.get("params", {})), None)} for op in operations]


def operation_summary(operations: list[dict], locale: str = "en") -> str:
    """Parameter prose is regenerated from restored/revised operations, never old counts."""
    from .presentation import operation_summaries

    rows = operation_summaries(operations)
    key = "text_zh" if locale.startswith("zh") else "text"
    result = "; ".join(row[key] for row in rows[:12])
    if len(rows) > 12:
        result += f"；另有 {len(rows) - 12} 项修改，见变更详情" if locale.startswith("zh") else f"; {len(rows) - 12} more changes in Changes"
    return result or ("无修改" if locale.startswith("zh") else "No changes")


def workspace_operations(operations: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply only changed arrangements to an already-confirmed real workspace."""
    from .operations import OPERATION_SPECS as OPERATIONS

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
        return {"id": op["id"], "op": target, "params": params}

    for op in operations:
        previous = old.get(op["id"])
        if previous == op:
            continue
        if previous is not None and op["op"] == previous["op"] and op["op"] in replacements:
            if op["params"].get("id") != previous["params"].get("id"):
                raise ValueError(f"{op['id']}: keep an applied entity's id when revising it / 修改已确认对象时须保留对象标识")
            changed = {k: v for k, v in op["params"].items() if k != "id" and (k not in previous["params"] or previous["params"][k] != v)}
            if previous["params"].keys() - op["params"].keys():
                raise ValueError(f"{op['id']}: specify replacement values for removed parameters / 请明确提供被移除参数的替代值")
            target, subject = replacements[op["op"]]
            result.append(translate(op, target, subject, changed))
        elif previous is not None and previous["op"].startswith("add_"):
            raise ValueError(f"{op['id']}: revise an applied addition with the same operation kind / 请保留已确认新增对象的操作类型")
        else:
            result.append(copy.deepcopy(op))
    for op in baseline:
        if op["id"] in new:
            continue
        if op["op"] not in removals:
            raise ValueError(f"{op['id']}: removing this already-applied arrangement needs an explicit supported replacement / 移除此已确认安排需要明确的受支持替代操作")
        target, subject = removals[op["op"]]
        result.insert(0, translate(op, target, subject, {}))
    return result


class ToolRunner:
    def __init__(self, store: Any, templates: Any, sessions: SessionStore, run_root: Path, *, agent_profile: str | None = None, on_plan: Callable[[str, int], None] | None = None) -> None:
        self.store, self.templates, self.sessions, self.run_root = store, templates, sessions, run_root
        self.agent_profile, self.on_plan = agent_profile, on_plan

    def context(self, identifier: str) -> Any:
        from .compiler import CompileContext

        return CompileContext(self.store, self.templates, self.agent_profile, locale=self.sessions.get(identifier)["locale"], allow_region=True)

    @staticmethod
    def _workspace_state(draft: dict[str, Any]) -> dict[str, Any]:
        latest = draft["latest_version"]
        return {"workspace_id": draft["id"], "revision": draft["revision"], "dirty": draft["dirty"],
                "validation": copy.deepcopy(draft.get("validation")),
                "latest_version": {k: copy.deepcopy(latest[k]) for k in ("version", "revision", "validation")} if latest is not None else None}

    def workspace_state(self, identifier: str) -> dict[str, Any] | None:
        """Read the same current draft/version status as the workbench, without loading a plan scene."""
        workspace = self.sessions.get(identifier)["workspace_id"]
        return self._workspace_state(self.store.summary(workspace)) if workspace is not None else None

    def execute(self, identifier: str, name: str, arguments: dict[str, Any]) -> Any:
        schemas = {row["function"]["name"]: row["function"]["parameters"] for row in tool_schemas()}
        if name not in schemas:
            raise ValueError("Unknown tool; apply and run controls are unavailable / 工具不存在；对话不能确认或运行")
        try:
            validate_arguments(schemas[name], arguments)
        except ValueError as error:
            import re

            match = re.search(r"arguments\.(operations|add|update)\[(\d+)\]", str(error))
            if match:
                operation = arguments[match[1]][int(match[2])]
                if isinstance(operation, dict):
                    raise ValueError(f"Operation {operation.get('id', match[0])}: {error}") from error
            raise
        method = getattr(self, name)
        return method(identifier, **arguments)

    def get_current_scene(self, identifier: str, id: str | None = None) -> dict[str, Any]:
        from .scene import explain_participant, summarize

        workspace = self.sessions.get(identifier)["workspace_id"]
        if workspace is None:
            return {"scene": None, "detail": "No bound scene yet / 尚未绑定场景"}
        draft = self.store.summary(workspace)
        summary = summarize(draft["scenario"], directory=self.store.directory(workspace), catalog=self.store.catalog_for(workspace), controls=(draft.get("template") or {}).get("knobs", {}))
        result = {"workspace_id": workspace, "revision": draft["revision"], "source": "saved_draft",
                  "workspace_state": self._workspace_state(draft)}
        if id is not None:
            return {**result, "participant": explain_participant(draft["scenario"], summary, id)}
        return {**result, "scene": short_scene(summary), "detail": "Overview lists participant ids but samples details; query get_current_scene(id=...) for complete saved facts / 摘要仅采样详情；用 id 查询完整已保存事实"}

    def list_starting_points(self, identifier: str) -> list[dict[str, Any]]:
        # Every starting point must fit one tool result; describe_starting_point has the control details.
        keys = ("id", "title", "title_zh", "summary", "category", "duration_s")
        return [{**{k: row[k] for k in keys if k in row}, "controls": {knob["id"]: knob.get("default") for knob in row["knobs"]}} for row in self.templates.list()]

    def describe_starting_point(self, identifier: str, template_id: str) -> dict[str, Any]:
        from aeroagentsim.authoring.catalog import ScenarioCatalog

        from .compiler import resolve_base
        from .scene import summarize

        base = resolve_base(self.context(identifier), {"template": template_id})
        metadata = self.templates.get(template_id)
        return {"template": {k: metadata[k] for k in ("id", "title", "title_zh", "summary", "summary_zh", "knobs", "observe", "observe_zh") if k in metadata}, "scene": summarize(base.document, directory=base.directory, catalog=ScenarioCatalog(base.document, base=base.directory), controls=base.controls)}

    def list_capabilities(self, identifier: str, topic: str | None = None) -> Any:
        from aeroagentsim.services.algorithm_catalog import discover_algorithm_catalog

        from .capabilities import capabilities

        result = capabilities(topic, templates=self.templates, algorithms=discover_algorithm_catalog())
        if result.get("topic") == "region":
            from .regions import catalogue

            result["extracts"] = catalogue()
            result["form_path"] = "Form → Scene & space → Region / 表单 → 场景与空间 → 区域"
        return result

    def find_places(self, identifier: str, query: str | None = None, near: Any = None, role: str | None = None) -> Any:
        from .places import find_places
        if isinstance(near, str):
            from .scene import explain_participant

            session = self.sessions.get(identifier)
            if session["plan_version"] is not None or session["workspace_id"] is not None:
                invalid = session["plan_version"] is not None and not session["plans"][str(session["plan_version"])]["compile"]["ok"]
                if invalid and session["workspace_id"] is None:
                    from .compiler import resolve_base
                    from .scene import summarize

                    base = resolve_base(self.context(identifier), self.sessions.plan(identifier)["base"])
                    document, summary = base.document, summarize(base.document, directory=base.directory)
                else:
                    document, summary = self._scene(identifier, "current" if invalid else "auto")
                if near in summary.get("entity_ids", []) or any(near == f["id"] for f in summary.get("fleets", [])):
                    participant = explain_participant(document, summary, near)
                    entity = participant["entity"]
                    from .scene import _position

                    position = _position(entity) if entity else None
                    if position is None and participant["fleet"]:
                        positions = [state["position"] for state in participant["fleet"]["member_states"]]
                        if positions and all(p == positions[0] for p in positions):
                            position = positions[0]
                    if position is None:
                        raise ValueError(f"near: {near!r} has no single ENU position; select a fleet member / 请选择有空间坐标的具体成员")
                    near = position
        return find_places(query=query, near=near, role=role, limit=12)

    def lookup_contract(self, identifier: str, term: str) -> dict[str, Any]:
        from aeroagentsim.authoring.catalog import ScenarioCatalog

        session = self.sessions.get(identifier)
        applied = any(r["plan_version"] == session["plan_version"] for r in session["applied"])
        if applied and session["workspace_id"] is not None:
            selected = self.store.catalog_for(session["workspace_id"])
        elif session["plan_version"] is not None and session["plans"][str(session["plan_version"])]["compile"]["ok"]:
            document, _ = self._scene(identifier)
            selected = ScenarioCatalog(document, base=self.sessions.plan_directory(identifier, session["plan_version"]) / "files")
        else:
            if session["workspace_id"] is not None or session["plan_version"] is None:
                selected = self.store.catalog_for(session["workspace_id"])
            else:
                from .compiler import resolve_base

                base = resolve_base(self.context(identifier), self.sessions.plan(identifier)["base"])
                selected = ScenarioCatalog(base.document, base=base.directory)
        result = selected.search(term)
        if "relations" in result:
            relations = result["relations"]
        elif hasattr(selected, "relations"):
            relations = selected.relations(term)
        else:
            relations = [r.data for rows in selected.sources().relations.values() for r in rows if term.casefold() in str(r.data).casefold()]
        types = []
        for row in result["types"][:8]:
            detail = selected.type_detail(row["id"])
            ancestors: list[str] = []
            pending = list(detail["parents"])
            while pending and len(ancestors) < 24:
                parent = pending.pop(0)
                if parent not in ancestors:
                    ancestors.append(parent)
                    pending.extend(selected.type_detail(parent)["parents"])
            types.append({"id": row["id"], "name": row.get("name"), "parents": detail["parents"], "ancestry": ancestors, "explanation": f"Declared contract type; effective fields: {', '.join(f['id'] for f in detail['fields'][:8])}", "explanation_zh": f"已声明的契约类型；有效字段：{', '.join(f['id'] for f in detail['fields'][:8])}"})
        fields = [{**{k: row[k] for k in ("id", "name", "declaringClass", "declaring_type", "schema", "valueSchema", "description", "description_zh") if k in row}, "explanation": row.get("description") or f"Declared field {row['id']}; schema shown alongside", "explanation_zh": row.get("description_zh") or f"已声明字段 {row['id']}；参数结构见所列定义"} for row in result["fields"][:10]]
        links = [{**{k: row[k] for k in ("id", "name", "source_type", "target_type", "source", "target", "description", "description_zh") if k in row}, "explanation": row.get("description") or f"Declared relation {row['id']}; endpoints shown alongside", "explanation_zh": row.get("description_zh") or f"已声明关系 {row['id']}；端点见所列定义"} for row in relations[:8]]
        return {"types": types, "fields": fields, "relations": links, "counts": {"types": len(result["types"]), "fields": len(result["fields"]), "relations": len(relations)}, "scope": result.get("catalog_scope", "aerograph-source")}

    def _compile(self, identifier: str, *, base: dict[str, Any], title: str, summary: str, operations: list[dict[str, Any]], assumptions: list[str], open_questions: list[Any], note: str, variant: str | None = None, parent: int | None = None, current: bool = True, retained: set[str] | None = None) -> dict[str, Any]:
        from aeroagentsim.services.comparison import configuration_differences

        from .compiler import compile_plan, resolve_base
        from .scene import diff as scene_diff

        session = self.sessions.get(identifier)
        previous = session["plans"].get(str(session["plan_version"]))
        # Older failed revisions may already have replaced their creation with
        # set_orders. Recover only that same retained op's real ancestor recipe.
        recipes = {}
        ancestor = session["plans"].get(str(parent))
        visited = set()
        while ancestor is not None and ancestor["version"] not in visited:
            visited.add(ancestor["version"])
            for operation in ancestor["operations"]:
                if operation["op"] == "add_orders":
                    recipes.setdefault(operation["id"], (operation, ancestor["version"]))
            ancestor = session["plans"].get(str(ancestor.get("parent")))
        recovery_notes = []
        operations = copy.deepcopy(operations)
        for index, operation in enumerate(operations):
            recipe = recipes.get(operation.get("id"))
            if operation["op"] != "set_orders" or recipe is None:
                continue
            creation, source_version = recipe
            target = creation["params"]["id"]
            if operation["params"]["id"] not in {target, creation["id"]}:
                continue
            if any(op["op"] == "add_orders" and op["params"]["id"] == target for op in operations):
                continue
            operations[index] = {"id": operation["id"], "op": "add_orders", "params": {**copy.deepcopy(creation["params"]), **operation["params"], "id": target}}
            recovery_notes.append(f"Retained the creation of workload {target!r} from plan v{source_version} while editing its parameters / 修改工作量 {target!r} 时保留方案 v{source_version} 的真实创建配置")
        if recovery_notes:
            summary = operation_summary(operations, session["locale"])
        ctx = self.context(identifier)
        base = copy.deepcopy(base)
        requested_base = copy.deepcopy(base)
        if session["workspace_id"] is not None:
            base = {"workspace": session["workspace_id"]}
        resolved = resolve_base(ctx, base)
        if resolved.revision is not None:
            base["revision"] = resolved.revision
        version, operations = self.sessions.allocate(identifier, operations, retained=retained)
        baseline: list[dict[str, Any]] = []
        if session["applied"] and parent is not None:
            applied_plan = session["plans"][str(session["applied"][-1]["plan_version"])]
            parent_plan = session["plans"][str(parent)]
            if parent == applied_plan["version"]:
                baseline = applied_plan["operations"]
            else:
                baseline = parent_plan.get("_baseline_operations", [])
        compile_operations = workspace_operations(operations, baseline) if baseline else operations
        directory = self.sessions.plan_directory(identifier, version)
        directory.parent.mkdir(parents=True, exist_ok=True)
        result = compile_plan(ctx, base, compile_operations, directory, resolved=resolved)
        result.notes.extend(recovery_notes)
        from .presentation import change_summaries

        what_changes = change_summaries(result.diff_base) if result.ok else []
        model_prose = {"summary": summary, "assumptions": copy.deepcopy(assumptions), "note": note}
        expanded = result.ok and result.operations != compile_operations
        if expanded:
            # The compiler introduced real prerequisites. Model prose written
            # before that expansion cannot establish their effects or absence.
            summary = operation_summary(result.operations, session["locale"])
            key = "text_zh" if session["locale"].startswith("zh") else "text"
            component_changes = [row[key] for row in what_changes if row["kind"] == "components"]
            summary += "; " + "; ".join(component_changes) if component_changes else ""
            assumptions = []
            note = summary
        if "template" in requested_base and "workspace" in base:
            result.notes.append(f"Bound workspace preserved; requested template {requested_base['template']!r} was not substituted. Lowalt prerequisites are installed by explicit compiler operations / 保留绑定工作区，未替换为请求模板，低空前置组件通过编译操作安装")
        if recovery_notes or ("template" in requested_base and "workspace" in base):
            record_path = directory / "compile.json"
            if record_path.is_file():
                record = json.loads(record_path.read_text())
                record["notes"] = result.notes
                record_path.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False))
        comparable = previous is not None and previous["compile"]["ok"] and result.ok
        changes = configuration_differences({"previous": previous["_document"], "current": result.document}) if comparable else []
        # The board renders scene-level differences; operation and raw config deltas stay alongside.
        operations_changed = operation_diff(previous["operations"] if previous else [], operations)
        diff_previous = {**scene_diff(previous["scene"], result.summary), "operations": operations_changed, "total_changes": len(changes)} if comparable else None
        plan = {"version": version, "created_at": now(), "note": note, "variant": variant, "parent": parent,
                "base": base, "base_label": resolved.label, "title": title, "summary": summary,
                "operations": operations, "assumptions": assumptions, "open_questions": open_questions,
                "compile": {"ok": result.ok, "errors": result.errors, "warnings": result.warnings, "notes": result.notes, "operations": result.operations},
                "validation": {"state": "pending" if result.ok else "invalid", "errors": [] if result.ok else result.errors},
                "scene": result.summary, "diff_base": result.diff_base, "diff_previous": diff_previous,
                "what_changes": what_changes,
                "interactions": result.interactions, "expectations": result.expectations,
                "_document": result.document,
                "_baseline_operations": baseline, "_compile_operations": result.operations}
        if expanded:
            plan["_model_prose"] = model_prose
        from .presentation import operation_summaries

        plan["operation_summaries"] = operation_summaries(operations)
        region_plan = directory / "files/region.plan.json"
        if result.ok and region_plan.is_file():
            geography = json.loads(region_plan.read_text())
            plan["_geography"] = {k: geography[k] for k in ("region", "scene", "scene_source", "preview")}
        elif result.ok and "workspace" in base:
            draft = self.store.get(base["workspace"])
            if draft.get("revision") == resolved.revision and "scene" in draft:
                from aeroagentsim.authoring.scene import preview_geometry

                plan["_geography"] = {k: copy.deepcopy(draft[k]) for k in ("region", "scene", "scene_source") if k in draft}
                plan["_geography"]["preview"] = {"origin": draft["scene"]["origin"], "geometry": preview_geometry(draft["scene"])}
        self.sessions.put_plan(identifier, plan, current=current)
        if result.ok and self.on_plan is not None:
            self.on_plan(identifier, version)
        if not result.ok:
            return {"version": version, "ok": False, "errors": result.errors, "operations": operation_list(operations),
                    "hint": "All operations below remain in THIS draft, including failed operations. Nothing was applied. Use revise_plan.update with the op id, or remove by op id/target id; do not add the same object again / 所列操作仍在本草稿中，均未应用；请按操作标识修改或按操作/对象标识移除，不要重复新增"}
        compact_diff = {"total_changes": len(changes), "changes": changes[:8], "operations": {key: [{"id": op["id"], "op": op.get("op", op.get("after", {}).get("op"))} for op in rows] for key, rows in operations_changed.items()}}
        latest = self.sessions.plan(identifier, version)
        return {"version": version, "ok": True, "validation": latest["validation"], "base": base, "scene": short_scene(result.summary),
                "operations": operation_list(result.operations), "what_changes": what_changes,
                "diff_previous": compact_diff, "warnings": result.warnings, "notes": result.notes,
                "prechecks": result.expectations, "note": note, "variant": variant}

    def draft_plan(self, identifier: str, **arguments: Any) -> dict[str, Any]:
        arguments.setdefault("note", "First proposal / 初版方案")
        arguments.setdefault("assumptions", [])
        arguments.setdefault("open_questions", [])
        return self._compile(identifier, **arguments)

    @staticmethod
    def _revision(plan: dict[str, Any], changes: dict[str, Any], locale: str = "en") -> dict[str, Any]:
        result = {k: copy.deepcopy(plan[k]) for k in ("base", "title", "summary", "operations", "assumptions", "open_questions")}
        known = {op["id"] for op in result["operations"]}
        remove = []
        for selected in changes.get("remove", []):
            if selected in known:
                remove.append(selected)
                continue
            targets = [op["id"] for op in result["operations"] if selected in [op["params"].get(k) for k in ("id", "fleet", "vehicle", "task")]]
            if len(targets) != 1:
                raise ValueError(f"remove: target {selected!r} is absent or ambiguous ({targets}); use an op id. Current operations: {operation_list(result['operations'])} / 请按当前操作标识移除")
            remove.append(targets[0])
        updates = changes.get("update", [])
        if len(remove) != len(set(remove)) or len({op.get("id") for op in updates}) != len(updates):
            raise ValueError("Duplicate revision ids / 修改标识重复")
        current = str(operation_list(result["operations"]))
        for op in updates:
            if op.get("id") not in known or op["id"] in remove:
                raise ValueError(f"Update must name an existing, retained operation; {op.get('id')!r} is not one. Current ids: {current} / 修改必须指定保留的已有操作；当前标识：{current}")
        if set(remove) - known:
            unknown = ", ".join(sorted(set(remove) - known))
            raise ValueError(f"Cannot remove unknown operations {unknown}. Current ids: {current} / 无法移除不存在的操作 {unknown}；当前标识：{current}")
        by_id = {op["id"]: copy.deepcopy(op) for op in updates}
        # Revising a retained workload creation edits its recipe. Replacing the
        # creation by set_orders would leave nothing to edit on recompilation.
        for original in result["operations"]:
            update = by_id.get(original["id"])
            if original["op"] == "add_orders" and update is not None and update["op"] == "set_orders":
                if update["params"]["id"] not in {original["params"]["id"], original["id"]}:
                    raise ValueError(f"{original['id']}: workload is {original['params']['id']!r}; retain that target / 请保留该工作量标识")
                params = {**copy.deepcopy(original["params"]), **update["params"], "id": original["params"]["id"]}
                by_id[original["id"]] = {"id": original["id"], "op": "add_orders", "params": params}
        result["operations"] = [copy.deepcopy(by_id.get(op["id"], op)) for op in result["operations"] if op["id"] not in remove]
        for op in changes.get("add", []):
            added = copy.deepcopy(op)
            added.pop("id", None)
            result["operations"].append(added)
        for key in ("assumptions", "title", "summary"):
            if key in changes:
                result[key] = copy.deepcopy(changes[key])
        if remove or updates or changes.get("add"):
            if "summary" not in changes:
                result["summary"] = operation_summary(result["operations"], locale)
            if "title" not in changes:
                result["title"] = "修改后的安排" if locale.startswith("zh") else "Revised arrangement"
            if "assumptions" not in changes:
                result["assumptions"] = []  # Old parameter prose cannot be assumed true after edits.
        if "open_questions" in changes and ("add_questions" in changes or "resolve_questions" in changes):
            raise ValueError("open_questions replaces all questions; use it alone or use add_questions/resolve_questions / 请单独使用问题替换，或使用增删问题")
        if "open_questions" in changes:
            result["open_questions"] = copy.deepcopy(changes["open_questions"])
        questions = result["open_questions"]
        resolved = set(changes.get("resolve_questions", []))
        texts = {q if isinstance(q, str) else q["question"] for q in questions}
        if resolved - texts:
            raise ValueError("resolve_questions must match existing question text / 解决的问题必须匹配原问题文本")
        result["open_questions"] = [q for q in questions if (q if isinstance(q, str) else q["question"]) not in resolved] + changes.get("add_questions", [])
        if len(result["open_questions"]) > 2:
            raise ValueError("Keep at most two material open questions / 最多保留两个实质性问题")
        result["note"] = changes.get("note") or changes.get("title") or "Revision / 修改"
        return result

    def revise_plan(self, identifier: str, **changes: Any) -> dict[str, Any]:
        if self.sessions.get(identifier)["plan_version"] is None:
            raise ValueError("No plan in this conversation yet; start one with draft_plan (an open project is its base) / 本对话还没有方案；请先用 draft_plan 起草（已打开的项目即为起点）")
        plan = self.sessions.plan(identifier)
        return self._compile(identifier, **self._revision(plan, changes, self.sessions.get(identifier)["locale"]), parent=plan["version"], retained={op["id"] for op in plan["operations"]})

    def restore_operations(self, identifier: str, from_version: int, note: str, op_ids: list[str] | None = None) -> dict[str, Any]:
        previous, current = self.sessions.plan(identifier, from_version), self.sessions.plan(identifier)
        arguments = {k: copy.deepcopy(current[k]) for k in ("base", "title", "summary", "operations", "assumptions", "open_questions")}
        if op_ids is None:
            arguments.update({k: copy.deepcopy(previous[k]) for k in ("operations", "assumptions", "open_questions", "title", "summary")})
        else:
            old = {op["id"]: op for op in previous["operations"]}
            if len(op_ids) != len(set(op_ids)) or set(op_ids) - old.keys():
                raise ValueError("Restore ids must belong to the source version / 恢复标识必须来自指定版本")
            arguments["operations"] = [copy.deepcopy(old[op["id"]]) if op["id"] in op_ids else op for op in current["operations"]]
            existing = {op["id"] for op in current["operations"]}
            for index, op in enumerate(previous["operations"]):
                if op["id"] not in op_ids or op["id"] in existing:
                    continue
                following = {later["id"] for later in previous["operations"][index + 1:]}
                position = next((i for i, current_op in enumerate(arguments["operations"]) if current_op["id"] in following), len(arguments["operations"]))
                arguments["operations"].insert(position, copy.deepcopy(op))
            locale = self.sessions.get(identifier)["locale"]
            arguments["title"] = f"恢复 v{from_version} 的安排" if locale.startswith("zh") else f"Restored arrangement from v{from_version}"
            arguments["summary"] = operation_summary(arguments["operations"], locale)
            arguments["assumptions"] = []
        arguments["note"] = note
        retained = {op["id"] for p in (previous, current) for op in p["operations"]}
        return self._compile(identifier, **arguments, parent=current["version"], retained=retained)

    def propose_alternatives(self, identifier: str, variants: list[dict[str, Any]]) -> dict[str, Any]:
        plan = self.sessions.plan(identifier)
        for variant in variants:
            if ("operations" in variant) == ("changes" in variant):
                raise ValueError("Each variant needs operations or changes / 每个备选方案需提供操作或修改之一")
        results = []
        for variant in variants:
            if "changes" in variant:
                arguments = self._revision(plan, variant["changes"], self.sessions.get(identifier)["locale"])
            else:
                arguments = {k: copy.deepcopy(plan[k]) for k in ("base", "title", "summary", "operations", "assumptions", "open_questions")}
                arguments["operations"] = variant["operations"]
            arguments.update(title=variant["title"], summary=variant["summary"], assumptions=variant["assumptions"], note=variant.get("note") or f"Alternative {variant['label']}: {variant['title']} / 备选方案 {variant['label']}：{variant['title']}")
            results.append(self._compile(identifier, **arguments, variant=variant["label"], parent=plan["version"], current=False, retained={op["id"] for op in plan["operations"]}))
        with self.sessions.edit(identifier) as session:
            session["alternatives"] = [r["version"] for r in results]
            self.sessions._event(session, "plan", version=plan["version"], versions=session["alternatives"], note="Compare alternatives / 比较备选方案")
        return {"variants": results, "current_version": plan["version"]}

    def _scene(self, identifier: str, scope: str = "auto") -> tuple[dict[str, Any], Any]:
        session = self.sessions.get(identifier)
        applied = any(record["plan_version"] == session["plan_version"] for record in session["applied"])
        if session["plan_version"] is not None and (scope == "proposed" or scope == "auto" and not applied):
            plan = self.sessions.plan(identifier)
            if not plan["compile"]["ok"]:
                raise ValueError("Fix compilation before inspecting the proposed scene / 请先修复编译再检查待确认场景")
            return plan["_document"], plan["scene"]
        if session["workspace_id"] is None:
            raise ValueError("Draft a plan first / 请先提出方案")
        from .scene import summarize

        workspace = session["workspace_id"]
        draft = self.store.get(workspace)
        document = draft["scenario"]
        return document, summarize(document, directory=self.store.directory(workspace), catalog=self.store.catalog_for(workspace), controls=(draft.get("template") or {}).get("knobs", {}))

    def explain_participant(self, identifier: str, id: str, scope: str = "auto") -> Any:
        from .scene import explain_participant

        document, summary = self._scene(identifier, scope)
        return explain_participant(document, summary, id)

    def explain_relation(self, identifier: str, a: str, b: str, scope: str = "auto") -> Any:
        from .scene import explain_relation

        document, summary = self._scene(identifier, scope)
        return explain_relation(document, summary, a, b)

    def list_runs(self, identifier: str) -> list[dict[str, Any]]:
        workspace = self.sessions.get(identifier)["workspace_id"]
        if workspace is None:
            return []
        result = []
        for path in sorted(self.run_root.glob("run-*/manifest.json")):
            metadata = RunStorage(path.parent).metadata()
            if metadata.get("studio_workspace") == workspace:
                result.append({k: metadata[k] for k in ("id", "status", "workspace_version", "scenario") if k in metadata})
        return result

    def run_story(self, identifier: str, run_id: str, locale: str | None = None) -> dict[str, Any]:
        from .story import run_story

        if not run_id.startswith("run-") or Path(run_id).name != run_id:
            raise FileNotFoundError("Run not found / 运行不存在")
        directory = self.run_root / run_id
        metadata = RunStorage(directory).metadata()
        session = self.sessions.get(identifier)
        if locale is not None and (not isinstance(locale, str) or not locale.strip() or len(locale) > 32):
            raise ValueError("locale: a nonempty locale of at most 32 characters is required / 语言标识须非空且不超过 32 个字符")
        if session["workspace_id"] is not None and metadata.get("studio_workspace") != session["workspace_id"]:
            raise FileNotFoundError("Run does not belong to this project / 运行不属于此项目")
        applied = next((r for r in reversed(session["applied"]) if r["workspace_id"] == session["workspace_id"] and r["version"] == metadata.get("workspace_version")), None)
        # Every run has a pinned scenario, including versions saved in the Form editor.
        # Derive from that exact input instead of reusing another plan/version's expectations.
        from .story import pinned_expectations

        expectations = pinned_expectations(directory)
        selected_locale = session["locale"] if locale is None else locale
        return {**run_story(directory, expectations, locale=selected_locale), "run_id": run_id, "status": metadata["status"],
                "expectations_source": "pinned_run_scenario", "workspace_version": metadata.get("workspace_version"),
                "plan_version": applied["plan_version"] if applied else None,
                "details_url": f"/v1/assistant/sessions/{identifier}/story/{run_id}?locale={quote(selected_locale, safe='')}"}
