"""Read-only scene adapters and compiled plan tools; no execution or apply tools."""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aeroagentsim.services.storage import RunStorage

from .connection import now
from .sessions import SessionStore

STRING = {"type": "string", "minLength": 1}
STRINGS = {"type": "array", "items": STRING}
QUESTIONS = {"type": "array", "maxItems": 2, "items": {"anyOf": [STRING, {"type": "object", "properties": {"question": STRING, "options": STRINGS}, "required": ["question"], "additionalProperties": False}]}}
BASE = {"anyOf": [
    {"type": "object", "properties": {"workspace": STRING}, "required": ["workspace"], "additionalProperties": False},
    {"type": "object", "properties": {"template": STRING, "controls": {"type": "object"}}, "required": ["template"], "additionalProperties": False},
]}


def object_schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def validate_arguments(schema: dict[str, Any], value: Any, path: str = "arguments") -> None:
    if "anyOf" in schema or "oneOf" in schema:
        for branch in schema.get("anyOf", schema.get("oneOf", [])):
            try:
                validate_arguments(branch, value, path)
                return
            except ValueError:
                continue
        raise ValueError(f"{path}: no matching schema / 不符合参数结构")
    kind = schema.get("type")
    if kind is None:
        return
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "integer": type(value) is int,
               "number": type(value) in (int, float) and math.isfinite(value),
               "boolean": type(value) is bool, "null": value is None}
    if not matches.get(kind, False):
        raise ValueError(f"{path}: expected {kind} / 参数类型错误")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: choose from {schema['enum']} / 请使用列出的选项")
    if kind == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - value.keys()
        extra = value.keys() - properties.keys() if schema.get("additionalProperties") is False else set()
        if missing or extra:
            raise ValueError(f"{path}: missing {sorted(missing)}, unknown {sorted(extra)} / 参数缺失或未声明")
        for key, child in value.items():
            if key in properties:
                validate_arguments(properties[key], child, f"{path}.{key}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1000):
            raise ValueError(f"{path}: array length outside bounds / 列表长度超出范围")
        for index, child in enumerate(value):
            validate_arguments(schema.get("items", {}), child, f"{path}[{index}]")
    elif kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 16000):
            raise ValueError(f"{path}: string length outside bounds / 文本长度超出范围")
    elif kind in {"integer", "number"}:
        if ("minimum" in schema and value < schema["minimum"]) or ("maximum" in schema and value > schema["maximum"]):
            raise ValueError(f"{path}: number outside bounds / 数值超出范围")


def operation_catalog() -> tuple[dict[str, Any], str]:
    from .operations import OPERATIONS

    lines = []
    branches = []
    for name, spec in OPERATIONS.items():
        params = spec["schema"]
        fields = ", ".join(params.get("properties", {}))
        lines.append(f"{name}({fields}): {spec['description']}")
        branches.append(object_schema({"id": STRING, "op": {"type": "string", "enum": [name]}, "params": params}, ("op", "params")))
    return {"type": "array", "maxItems": 80, "items": {"anyOf": branches}}, "\n".join(lines)


def tool_schemas() -> list[dict[str, Any]]:
    operations, catalogue = operation_catalog()
    revision = object_schema({
        "note": STRING, "add": operations, "update": operations, "remove": STRINGS,
        "assumptions": STRINGS, "resolve_questions": STRINGS, "add_questions": QUESTIONS,
        "title": STRING, "summary": STRING,
    }, ("note",))
    specs = [
        ("get_current_scene", "Read the bound project's real draft scene; returns no scene when unbound.", object_schema({})),
        ("list_starting_points", "List available scenario templates and their defaults.", object_schema({})),
        ("describe_starting_point", "Read a template's actual participants and controls.", object_schema({"template_id": STRING}, ("template_id",))),
        ("list_capabilities", "Read supported operations, parameter limits and installed algorithms.", object_schema({"topic": STRING})),
        ("find_places", "Find named catalogue places; use their keys in add_place.at or add_incident.at.", object_schema({"query": STRING, "near": {"anyOf": [STRING, {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 3}]}, "role": STRING})),
        ("lookup_contract", "Search real AeroGraph types, fields, relations and ancestry.", object_schema({"term": STRING}, ("term",))),
        ("draft_plan", "Compile a proposed plan now. Operations use {id?, op, params}; ids are assigned by the server. Never applies.\n" + catalogue, object_schema({"base": BASE, "title": STRING, "summary": STRING, "operations": operations, "assumptions": STRINGS, "open_questions": QUESTIONS, "note": STRING}, ("base", "title", "summary", "operations", "assumptions", "open_questions", "note"))),
        ("revise_plan", "Compile a minimal revision. update contains full operations with existing ids; remove lists ids. Leave all other operations intact.\n" + catalogue, revision),
        ("restore_operations", "Restore all or selected operation ids from an earlier version; keep unrelated operations.", object_schema({"from_version": {"type": "integer", "minimum": 1}, "op_ids": STRINGS, "note": STRING}, ("from_version", "note"))),
        ("propose_alternatives", "Compile two or three labelled variants of the current plan. Supply complete operations or minimal changes, never both.", object_schema({"variants": {"type": "array", "minItems": 2, "maxItems": 3, "items": object_schema({"label": STRING, "title": STRING, "summary": STRING, "operations": operations, "changes": revision, "assumptions": STRINGS}, ("label", "title", "summary", "assumptions"))}}, ("variants",))),
        ("explain_participant", "Explain one real participant's responsibilities, tasks and decision rules.", object_schema({"id": STRING}, ("id",))),
        ("explain_relation", "Explain recorded or configured links between two real entities.", object_schema({"a": STRING, "b": STRING}, ("a", "b"))),
        ("list_runs", "List recorded runs of this session's bound project.", object_schema({})),
        ("run_story", "Read expectations versus recorded events for a run; never simulate an outcome.", object_schema({"run_id": STRING}, ("run_id",))),
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
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw) <= budget:
        return raw
    return json.dumps({"truncated": True, "preview": raw[:budget - 400], "detail": "Result exceeds context budget; query a narrower topic / 结果超出上下文预算；请缩小查询范围"}, ensure_ascii=False)


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
        if isinstance(value, list):
            result[key] = {"count": len(value), "items": [{k: row[k] for k in ("id", "label", "label_zh", "role", "count", "kind", "start", "at_s", "time_s") if k in row} if isinstance(row, dict) else row for row in value[:6]], "truncated": len(value) > 6}
        elif key in {"environment", "cooperation", "rules", "algorithms", "counts"}:
            result[key] = value
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def workspace_operations(operations: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply only changed arrangements to an already-confirmed real workspace."""
    from .operations import OPERATIONS

    old = {op["id"]: op for op in baseline}
    new = {op["id"]: op for op in operations}
    result = []
    replacements = {"add_place": ("update_place", "place"), "add_fleet": ("set_fleet", "fleet"), "add_orders": ("set_orders", "orders"), "add_incident": ("set_incident", "incident")}
    removals = {"add_place": ("remove_place", "place"), "add_orders": ("remove_orders", "orders"), "add_incident": ("remove_incident", "incident"), "set_cooperation": ("remove_cooperation", None)}

    def translate(op: dict[str, Any], target: str, subject: str | None, changes: dict[str, Any]) -> dict[str, Any]:
        spec = OPERATIONS[target]
        properties = spec["schema"].get("properties", {})
        params = copy.deepcopy(changes)
        if subject is not None:
            key = subject if subject in properties else "id" if "id" in properties else None
            if key is None:
                raise ValueError(f"{op['id']}: {target} does not declare the entity selector / 操作未声明对象选择字段")
            params[key] = op["params"]["id"]
        validate_arguments(spec["schema"], params, op["id"])
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

        return CompileContext(self.store, self.templates, self.agent_profile, locale=self.sessions.get(identifier)["locale"])

    def execute(self, identifier: str, name: str, arguments: dict[str, Any]) -> Any:
        schemas = {row["function"]["name"]: row["function"]["parameters"] for row in tool_schemas()}
        if name not in schemas:
            raise ValueError("Unknown tool; apply and run controls are unavailable / 工具不存在；对话不能确认或运行")
        validate_arguments(schemas[name], arguments)
        method = getattr(self, name)
        return method(identifier, **arguments)

    def get_current_scene(self, identifier: str) -> dict[str, Any]:
        from .scene import summarize

        workspace = self.sessions.get(identifier)["workspace_id"]
        if workspace is None:
            return {"scene": None, "detail": "No bound scene yet / 尚未绑定场景"}
        draft = self.store.get(workspace)
        return {"workspace_id": workspace, "revision": draft["revision"], "scene": summarize(draft["scenario"], directory=self.store.directory(workspace), catalog=self.store.catalog_for(workspace), controls=(draft.get("template") or {}).get("knobs", {}))}

    def list_starting_points(self, identifier: str) -> list[dict[str, Any]]:
        keys = ("id", "title", "title_zh", "summary", "summary_zh", "category", "duration_s")
        return [{**{k: row[k] for k in keys if k in row}, "controls": [{k: knob[k] for k in ("id", "label", "label_zh", "default", "options", "min", "max") if k in knob} for knob in row["knobs"]]} for row in self.templates.list()]

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

        return capabilities(topic, templates=self.templates, algorithms=discover_algorithm_catalog())

    def find_places(self, identifier: str, query: str | None = None, near: Any = None, role: str | None = None) -> Any:
        from .places import find_places

        return find_places(query=query, near=near, role=role, limit=12)

    def lookup_contract(self, identifier: str, term: str) -> dict[str, Any]:
        selected = self.store.catalog_for(self.sessions.get(identifier)["workspace_id"])
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
        from aeroagentsim.services.app import configuration_differences

        from .compiler import compile_plan, resolve_base

        session = self.sessions.get(identifier)
        previous = session["plans"].get(str(session["plan_version"]))
        ctx = self.context(identifier)
        base = copy.deepcopy(base)
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
        result = compile_plan(ctx, base, compile_operations, directory)
        changes = configuration_differences({"previous": previous["_document"], "current": result.document}) if previous and previous["compile"]["ok"] and result.ok else []
        diff_previous = {"changes": changes[:80], "total_changes": len(changes), "operations": operation_diff(previous["operations"] if previous else [], operations)}
        plan = {"version": version, "created_at": now(), "note": note, "variant": variant, "parent": parent,
                "base": base, "base_label": resolved.label, "title": title, "summary": summary,
                "operations": operations, "assumptions": assumptions, "open_questions": open_questions,
                "compile": {"ok": result.ok, "errors": result.errors, "warnings": result.warnings},
                "validation": {"state": "pending" if result.ok else "invalid", "errors": [] if result.ok else result.errors},
                "scene": result.summary, "diff_base": result.diff_base, "diff_previous": diff_previous,
                "interactions": result.interactions, "expectations": result.expectations,
                "_document": result.document, "_compile_data": result.to_data(), "_digest": result.digest,
                "_baseline_operations": baseline, "_compile_operations": compile_operations}
        self.sessions.put_plan(identifier, plan, current=current)
        if result.ok and self.on_plan is not None:
            self.on_plan(identifier, version)
        if not result.ok:
            return {"version": version, "ok": False, "errors": result.errors, "hint": "Fix the named operation ids and fields with revise_plan; this draft cannot be confirmed / 请用 revise_plan 修复指定操作和字段；该草稿不能确认"}
        compact_diff = {"total_changes": len(changes), "changes": changes[:8], "operations": {key: [{"id": op["id"], "op": op.get("op", op.get("after", {}).get("op"))} for op in rows] for key, rows in diff_previous["operations"].items()}}
        return {"version": version, "ok": True, "validation": "pending", "scene": short_scene(result.summary),
                "operations": [{"id": op["id"], "op": op["op"]} for op in operations],
                "diff_previous": compact_diff, "warnings": result.warnings,
                "prechecks": result.expectations, "note": note, "variant": variant}

    def draft_plan(self, identifier: str, **arguments: Any) -> dict[str, Any]:
        return self._compile(identifier, **arguments)

    @staticmethod
    def _revision(plan: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
        result = {k: copy.deepcopy(plan[k]) for k in ("base", "title", "summary", "operations", "assumptions", "open_questions")}
        known = {op["id"] for op in result["operations"]}
        remove = changes.get("remove", [])
        updates = changes.get("update", [])
        if len(remove) != len(set(remove)) or len({op.get("id") for op in updates}) != len(updates):
            raise ValueError("Duplicate revision ids / 修改标识重复")
        for op in updates:
            if op.get("id") not in known or op["id"] in remove:
                raise ValueError("Update must name an existing, retained operation / 修改必须指定保留的已有操作")
        if set(remove) - known:
            raise ValueError("Cannot remove unknown operations / 无法移除不存在的操作")
        by_id = {op["id"]: op for op in updates}
        result["operations"] = [copy.deepcopy(by_id.get(op["id"], op)) for op in result["operations"] if op["id"] not in remove]
        for op in changes.get("add", []):
            added = copy.deepcopy(op)
            added.pop("id", None)
            result["operations"].append(added)
        for key in ("assumptions", "title", "summary"):
            if key in changes:
                result[key] = copy.deepcopy(changes[key])
        questions = result["open_questions"]
        resolved = set(changes.get("resolve_questions", []))
        texts = {q if isinstance(q, str) else q["question"] for q in questions}
        if resolved - texts:
            raise ValueError("resolve_questions must match existing question text / 解决的问题必须匹配原问题文本")
        result["open_questions"] = [q for q in questions if (q if isinstance(q, str) else q["question"]) not in resolved] + changes.get("add_questions", [])
        if len(result["open_questions"]) > 2:
            raise ValueError("Keep at most two material open questions / 最多保留两个实质性问题")
        result["note"] = changes["note"]
        return result

    def revise_plan(self, identifier: str, **changes: Any) -> dict[str, Any]:
        plan = self.sessions.plan(identifier)
        return self._compile(identifier, **self._revision(plan, changes), parent=plan["version"], retained={op["id"] for op in plan["operations"]})

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
                arguments = self._revision(plan, variant["changes"])
            else:
                arguments = {k: copy.deepcopy(plan[k]) for k in ("base", "title", "summary", "operations", "assumptions", "open_questions")}
                arguments["operations"] = variant["operations"]
            arguments.update(title=variant["title"], summary=variant["summary"], assumptions=variant["assumptions"], note=f"Alternative {variant['label']} / 备选方案 {variant['label']}")
            results.append(self._compile(identifier, **arguments, variant=variant["label"], parent=plan["version"], current=False, retained={op["id"] for op in plan["operations"]}))
        with self.sessions.edit(identifier) as session:
            session["alternatives"] = [r["version"] for r in results]
            self.sessions._event(session, "plan", version=plan["version"], versions=session["alternatives"], note="Compare alternatives / 比较备选方案")
        return {"variants": results, "current_version": plan["version"]}

    def _scene(self, identifier: str) -> tuple[dict[str, Any], Any]:
        session = self.sessions.get(identifier)
        if session["plan_version"] is not None:
            plan = self.sessions.plan(identifier)
            if not plan["compile"]["ok"]:
                raise ValueError("Fix compilation before inspecting the proposed scene / 请先修复编译再检查待确认场景")
            return plan["_document"], plan["scene"]
        if session["workspace_id"] is None:
            raise ValueError("Draft a plan first / 请先提出方案")
        return self.store.get(session["workspace_id"])["scenario"], self.get_current_scene(identifier)["scene"]

    def explain_participant(self, identifier: str, id: str) -> Any:
        from .scene import explain_participant

        document, summary = self._scene(identifier)
        return explain_participant(document, summary, id)

    def explain_relation(self, identifier: str, a: str, b: str) -> Any:
        from .scene import explain_relation

        document, summary = self._scene(identifier)
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

    def run_story(self, identifier: str, run_id: str) -> dict[str, Any]:
        from .story import run_story

        if not run_id.startswith("run-") or Path(run_id).name != run_id:
            raise FileNotFoundError("Run not found / 运行不存在")
        directory = self.run_root / run_id
        metadata = RunStorage(directory).metadata()
        session = self.sessions.get(identifier)
        if session["workspace_id"] is None or metadata.get("studio_workspace") != session["workspace_id"]:
            raise FileNotFoundError("Run does not belong to this project / 运行不属于此项目")
        applied = next((r for r in reversed(session["applied"]) if r["workspace_id"] == session["workspace_id"] and r["version"] == metadata.get("workspace_version")), None)
        expectations = self.sessions.expectations_for(session["workspace_id"], metadata.get("workspace_version"))
        if expectations is None:
            raise ValueError("This run has no recorded applied-plan expectations / 此运行没有已确认方案的预期记录")
        return {**run_story(directory, expectations, locale=session["locale"]), "run_id": run_id, "status": metadata["status"], "plan_version": applied["plan_version"] if applied else None}
