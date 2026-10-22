"""Compile reversible plan operations into real, portable scenario drafts."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import (
    WorkspaceStore,
    validate_configuration,
    validation_failure,
)
from aeroagentsim.scenario import load_scenario

from aeroagentsim.assistant_api import CompileContext as OperationContext
from aeroagentsim.assistant_api import CompileResult as OperationResult
from aeroagentsim.assistant_api import ResourceProposal, thaw

from .catalogue import OperationCatalogue
from .operations import OperationError, validate_operation

SECOND = 1_000_000_000


@dataclass
class CompileContext:
    """Host staging services; never passed to an OperationSpec compiler."""

    store: WorkspaceStore
    templates: TemplateRegistry
    agent_profile: str | None
    locale: str = "en"
    allow_region: bool = False


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
    notes: list[str] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    contributions: dict[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return copy.deepcopy({**{k: v for k, v in self.__dict__.items()
                                 if k != "document" and (k != "contributions" or v)}, "files_dir": str(self.files_dir)})


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
        # The loader also writes compiled IR into config.packages, including
        # absolute diagnostic source paths. The top-level packages are the
        # portable source of truth; rebuild IR from them on the next load.
        for engine in document.get("engines", {}).values():
            if engine.get("plugin") == "behaviour":
                engine["config"].pop("packages", None)


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


def _resource_removals(contributions: dict) -> tuple[str, ...]:
    """Retain the final validated action for each proposed resource path."""
    actions = {}
    for row in contributions.get("resources", []):
        ResourceProposal(row["path"], None)  # Recheck portable, non-host-owned paths.
        if row["action"] not in {"write", "remove"}:
            _fail("resources", "Unknown resource action", "资源操作未知")
        actions[row["path"]] = row["action"]
    return tuple(sorted(path for path, action in actions.items() if action == "remove"))


def _digest(document: dict, directory: Path | None, removals: tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
    paths = sorted(p for p in directory.rglob("*") if p.is_file()) if directory is not None else []
    for path in paths:
        relative = path.relative_to(directory).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    if removals:
        # Tombstones are approved plan inputs too; changing only compile.json
        # must not be able to widen or suppress workspace deletions.
        digest.update(b"\x00assistant-resource-removals\x00")
        digest.update(json.dumps(removals, ensure_ascii=False, separators=(",", ":")).encode())
    return "sha256:" + digest.hexdigest()


def _fail(field: str, en: str, zh: str) -> None:
    raise OperationError(field, en + " / " + zh)


def apply_patches(document: dict, patches) -> dict:
    """Apply bounded JSON Pointer proposals to an isolated in-memory candidate."""
    result = copy.deepcopy(document)
    for frozen in patches:
        patch = thaw(frozen)
        path, action = patch["path"], patch["op"]
        if path == "":
            if action == "remove" or not isinstance(patch.get("value"), dict):
                _fail("patch", "A scenario proposal must remain an object", "场景提案须保持对象结构")
            result = copy.deepcopy(patch["value"])
            continue
        parts = path[1:].split("/")
        if any("~" in part.replace("~1", "").replace("~0", "") for part in parts):
            _fail("patch", "Invalid JSON Pointer escape", "JSON Pointer 转义无效")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in parts]
        node = result
        for token in parts[:-1]:
            node = node[_list_index(token, len(node))] if isinstance(node, list) else node[token]
        token = parts[-1]
        if isinstance(node, list):
            index = len(node) if token == "-" and action == "add" else _list_index(token, len(node), append=action == "add")
            if action == "add":
                node.insert(index, copy.deepcopy(patch["value"]))
            elif action == "replace":
                node[index] = copy.deepcopy(patch["value"])
            else:
                del node[index]
        elif isinstance(node, dict):
            if action != "add" and token not in node:
                _fail("patch", f"Missing patch target {path}", "补丁目标不存在")
            if action == "remove":
                del node[token]
            else:
                node[token] = copy.deepcopy(patch["value"])
        else:
            _fail("patch", "Patch parent must be an object or array", "补丁父节点须为对象或列表")
    return result


def _list_index(token: str, length: int, *, append: bool = False) -> int:
    if not token.isdigit() or str(int(token)) != token or int(token) >= length + int(append):
        _fail("patch", "Invalid array index", "列表索引无效")
    return int(token)


def compile_operations(context: OperationContext, operations: list[dict], *, catalogue: OperationCatalogue) -> OperationResult:
    """Pure, no-discovery entry point for data-only families and their prerequisites.

    Resource loading/staging and whole-scenario validation are deliberately not
    performed here. ``compile_plan`` provides those host responsibilities.
    """
    operations, _, warnings = catalogue.prerequisites(context, operations)
    document, resources, controls = thaw(context.document), dict(context.resources), thaw(context.controls)
    patches, proposals, issues, effective, effects, references, expected = [], {}, [], [], [], [], []
    issues.extend({"severity": "warning", **warning} for warning in warnings)
    for index in _operation_order(operations, context, catalogue):
        operation = operations[index]
        current = OperationContext(document, resources=resources, data={**thaw(context.data), "operations": operations}, controls=controls,
                                   operation_id=operation.get("id", f"op-{index + 1}"),
                                   agent_profile=context.agent_profile, locale=context.locale)
        result = catalogue.compile(current, operation)
        issues.extend(thaw(result.issues))
        if any(issue["severity"] == "error" for issue in result.issues):
            break
        document = apply_patches(document, result.patches)
        patches.extend(result.patches)
        for resource in result.resources:
            proposals[resource.path] = resource
            if resource.content is None:
                resources.pop(resource.path, None)
            else:
                resources[resource.path] = resource.content
        if result.controls is not None:
            controls = thaw(result.controls)
        effective.extend(thaw(result.effective_operations) or [copy.deepcopy(operation)])
        effects.extend(result.effects)
        references.extend(result.references)
        expected.extend(result.expectations)
    return OperationResult(patches=tuple(patches), resources=tuple(proposals.values()), issues=tuple(issues),
                           effective_operations=tuple(effective), effects=tuple(effects), references=tuple(references),
                           expectations=tuple(expected), controls=controls)


def _resource_inputs(directory: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    resources, names = {}, {}
    root = directory.resolve()
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            _fail("resources", "Resource escapes plan files", "资源超出计划文件范围")
        name = path.relative_to(directory).as_posix()
        resources[name] = path.read_bytes()
        names[str(path)] = name
        names[str(path.resolve())] = name
    return resources, names


def _stage_resources(files: Path, proposals) -> list[dict]:
    changes = []
    for resource in proposals:
        path = files / resource.path
        if not path.resolve().is_relative_to(files.resolve()):
            _fail("resources", "Resource escapes plan files", "资源超出计划文件范围")
        previous = path.read_bytes() if path.is_file() else None
        if previous == resource.content:
            continue
        group = "removed" if resource.content is None else "added" if previous is None else "changed"
        if resource.content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resource.content)
        changes.append({"group": group, "kind": "resource", "id": resource.path,
                        "label": resource.path, "label_zh": resource.path})
    return changes


def _operation_order(operations: list[dict], context: OperationContext, catalogue: OperationCatalogue) -> list[int]:
    dependencies = catalogue.dependencies(context, operations)
    canonical = []
    for operation in operations:
        try:
            canonical.append(catalogue.resolve(operation.get("op")).id if isinstance(operation, dict) else None)
        except OperationError:
            canonical.append(None)
    for index, identity in enumerate(canonical):
        if identity is not None:
            for prerequisite in catalogue.operations[identity].prerequisites:
                dependencies[index].update(i for i, other in enumerate(canonical) if other == prerequisite.operation and i != index)
    ordered = []
    while dependencies:
        ready = next((i for i in sorted(dependencies) if not dependencies[i]), None)
        if ready is None:
            ids = [operations[i].get("id", f"op-{i + 1}") for i in dependencies]
            _fail("operations", f"Circular creation references between {ids}; give one creation an explicit position", "创建操作存在循环引用，请为一个创建操作提供明确坐标")
        ordered.append(ready)
        dependencies.pop(ready)
        for waiting in dependencies.values():
            waiting.discard(ready)
    return ordered


def compile_plan(ctx: CompileContext, base: dict, operations: list[dict], out_dir: Path, *, resolved: BaseScene | None = None) -> CompileResult:
    from .scene import _registry_types, diff, expectations, interactions, summarize, validate_scene_expectations
    from .diff_display import enrich_diff

    out_dir = Path(out_dir)
    files = out_dir / "files"
    errors: list[dict] = []
    warnings: list[dict] = []
    document: dict = {}
    scene: BaseScene | None = None
    base_summary: dict = {}
    notes: list[str] = []
    effects: list[dict] = []
    contributed_expectations: list[dict] = []
    contributions: dict[str, Any] = {}
    adapters: list = []
    catalogue = None
    try:
        scene = resolve_base(ctx, base) if resolved is None else resolved
        if out_dir.resolve().is_relative_to(scene.directory.resolve()) or scene.directory.resolve().is_relative_to(files.resolve()):
            _fail("out_dir", "Plan directory must be outside the base scene", "计划目录必须位于起点场景之外")
    except Exception as exc:  # noqa: BLE001 - no output may replace the source scene
        return CompileResult(False, [{"op_id": None, "field": getattr(exc, "field", "base"), "message": str(exc)}], [], {}, files, _digest({}, None))
    out_dir.mkdir(parents=True, exist_ok=True)
    if files.exists():
        shutil.rmtree(files)
    files.mkdir()
    try:
        catalogue = OperationCatalogue()
        _copy_files(scene.directory, files)
        document = _rebase(scene.document, scene.directory, files)
        baseline = load_scenario(document, base=files)
        base_summary = summarize(baseline.document, directory=files, controls=scene.controls, catalogue=catalogue)
        factories = set()
        for plugin in catalogue.plugins:
            if plugin._host is not None and plugin._host not in factories:
                adapters.append(plugin._host(ctx, scene, document, files, baseline, catalogue))
                factories.add(plugin._host)
        if not isinstance(operations, list):
            _fail("operations", "Operations must be a list", "操作必须为列表")
        inputs, input_names = _resource_inputs(files)
        prerequisite_context = OperationContext(document, resources=inputs, controls=scene.controls,
                                                data={"resource_names": input_names, "types": _registry_types(document, files),
                                                      "template_id": scene.template_id},
                                                agent_profile=ctx.agent_profile, locale=ctx.locale)
        operations, notes, prerequisite_warnings = catalogue.prerequisites(prerequisite_context, operations)
        warnings.extend(prerequisite_warnings)
        seen: set[str] = set()
        for adapter in adapters:
            adapter.prepare_operations(operations)
        ordered_indices = _operation_order(operations, prerequisite_context, catalogue)
        effective = {}
        for index in ordered_indices:
            operation = operations[index]
            op_id = operation.get("id", f"op-{index + 1}") if isinstance(operation, dict) else f"op-{index + 1}"
            candidate_controls = copy.deepcopy(scene.controls)
            warning_count = len(warnings)
            try:
                if op_id in seen:
                    _fail("id", "Operation ids must be unique", "操作标识必须唯一")
                seen.add(op_id)
                name, params = validate_operation(operation, catalogue=catalogue)
                spec = catalogue.resolve(name)
                data = {"template_id": scene.template_id, "operations": operations}
                for adapter in adapters:
                    data.update(adapter.prepare(spec, operation, document, params, warnings))
                resources, resource_names = _resource_inputs(files)
                data.update(resource_names=resource_names, types=_registry_types(document, files))
                context = OperationContext(document, resources=resources, data=data, controls=scene.controls,
                                           operation_id=op_id, agent_profile=ctx.agent_profile, locale=ctx.locale)
                proposal = catalogue.compile(context, operation)
                issue_errors = [row for row in thaw(proposal.issues) if row["severity"] == "error"]
                if issue_errors:
                    errors.extend({"op_id": op_id, **{key: value for key, value in row.items() if key != "severity"}} for row in issue_errors)
                    continue
                candidate = apply_patches(document, proposal.patches)
                staged = _stage_resources(files, proposal.resources)
                for adapter in adapters:
                    adapter.after(candidate)
                document = candidate
                effects.extend(staged)
                effects.extend(thaw(proposal.effects))
                contributed_expectations.extend(thaw(proposal.expectations))
                if proposal.effects:
                    contributions.setdefault("effects", []).extend(thaw(proposal.effects))
                links = thaw(proposal.references)
                if links:
                    contributions.setdefault("references", []).extend(links)
                for change in staged:
                    resource = next(row for row in proposal.resources if row.path == change["id"])
                    contributions.setdefault("resources", []).append({
                        "path": resource.path, "action": "remove" if resource.content is None else "write",
                        "digest": None if resource.content is None else "sha256:" + hashlib.sha256(resource.content).hexdigest(),
                    })
                warnings.extend({"op_id": op_id, **{key: value for key, value in row.items() if key != "severity"}} for row in thaw(proposal.issues))
                if proposal.controls is not None:
                    scene.controls = thaw(proposal.controls)
                if proposal.effective_operations:
                    effective[index] = thaw(proposal.effective_operations)
                for adapter in adapters:
                    adapter.succeeded(spec, params, op_id)
            except (OperationError, ValueError, TypeError, KeyError, IndexError) as exc:
                scene.controls = candidate_controls
                del warnings[warning_count:]
                message = str(exc)
                for adapter in adapters:
                    message = adapter.error(operation, exc, message)
                errors.append({"op_id": op_id, "field": getattr(exc, "field", "params"), "message": message})
        for adapter in adapters:
            adapter.finish(document, [operations[index] for index in ordered_indices], errors, warnings)
        if not errors:
            loaded = load_scenario(document, base=files)
            document = loaded.document
            _editable_packages(document)
            for adapter in adapters:
                adapter.refresh(document, warnings)
        if effective:
            operations = [row for index, operation in enumerate(operations) for row in effective.get(index, [operation])]
    except Exception as exc:  # noqa: BLE001 - return real compiler/loader diagnostics
        errors.append({"op_id": operations[-1].get("id", f"op-{len(operations)}") if isinstance(operations, list) and operations and isinstance(operations[-1], dict) else None,
                       "field": getattr(exc, "field", getattr(exc, "path", "base")), "message": f"{type(exc).__name__}: {exc}"})
    summary, expected, difference, diagram = {}, [], {}, {}
    if not errors:
        projection = "summary"
        try:
            summary = summarize(document, directory=files, controls=scene.controls, catalogue=catalogue)
            for work in summary.get("workloads", []):
                if "note" in work["release"]:
                    warnings.append({"op_id": None, "field": f"workloads.{work['id']}.release", "message": f"{work['release']['note']} / {work['release']['note_zh']}"})
            projection = "expectations"
            expected = expectations(document, summary, catalogue=catalogue)
            for row in contributed_expectations:
                previous = next((entry for entry in expected if entry["id"] == row["id"]), None)
                if previous is None:
                    expected.append(row)
                elif previous != row:
                    _fail("expectations", "assistant expectations: unique nonempty ids required", "预期事件标识必须唯一")
            validate_scene_expectations(expected, summary, catalogue=catalogue)
            projection = "interactions"
            diagram = interactions(summary, catalogue=catalogue)
            projection = "summary"
            difference = diff(base_summary, summary)
        except Exception as exc:  # noqa: BLE001 - contributed projections are compiler inputs
            errors.append({"op_id": operations[-1].get("id", f"op-{len(operations)}") if operations else None,
                           "field": getattr(exc, "field", projection), "message": f"{type(exc).__name__}: {exc}"})
            summary, expected, difference, diagram = {}, [], {}, {}
    result = CompileResult(not errors, errors, warnings, document, files, _digest(document, files, _resource_removals(contributions)), summary,
                           difference, diagram, expected)
    result.notes = notes
    result.contributions = contributions
    result.operations = copy.deepcopy(operations) if isinstance(operations, list) else []
    if result.ok:
        for effect in effects:
            row = dict(effect)
            group = row.pop("group", "changed")
            if group not in {"added", "changed", "removed"} or not {"kind", "id"} <= row.keys():
                raise ValueError("Malformed assistant effect proposal")
            result.diff_base[group].append(row)
        for adapter in adapters:
            adapter.decorate(result, baseline)
        enrich_diff(result.diff_base)
    _write(out_dir / "scenario.json", document)
    _write(out_dir / "compile.json", {**result.to_data(), "base": base, "base_revision": scene.revision if scene else None,
                                      "template_id": scene.template_id if scene else None, "controls": scene.controls if scene else {}})
    return result


def validate_compiled(ctx: CompileContext, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    document = json.loads((out_dir / "scenario.json").read_text())
    record = json.loads((out_dir / "compile.json").read_text())
    try:
        if not record["ok"]:
            raise ValueError("Plan has compile errors / 计划存在编译错误")
        if _digest(document, out_dir / "files", _resource_removals(record.get("contributions", {}))) != record["digest"]:
            raise PlanConflict("Compiled plan files changed / 编译后的计划文件已改变")
        validate_configuration(document, base=out_dir / "files", directory=out_dir)
        result = {"valid": True, "errors": [], "issues": []}
    except Exception as exc:  # noqa: BLE001 - match WorkspaceStore diagnostics
        result = validation_failure(document, exc)
    _write(out_dir / "validation.json", {**result, "digest": record["digest"]})
    return result


def apply_plan(ctx: CompileContext, out_dir: Path, *, base: dict, workspace_id: str | None, name: str, note: str) -> dict:
    out_dir = Path(out_dir)
    record = json.loads((out_dir / "compile.json").read_text())
    document = json.loads((out_dir / "scenario.json").read_text())
    removals = _resource_removals(record.get("contributions", {}))
    if base != record["base"] or not record["ok"] or _digest(document, out_dir / "files", removals) != record["digest"]:
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
        # Resolve and check every exact target before any workspace mutation.
        # Never follow a workspace symlink outside the revision-checked draft.
        targets = []
        root = directory.resolve()
        for relative in removals:
            target = directory / relative
            if not target.resolve().is_relative_to(root) or target.is_dir():
                raise PlanConflict(f"Resource removal escapes workspace files: {relative} / 资源删除超出工作区文件范围")
            if (out_dir / "files" / relative).exists():
                raise PlanConflict(f"Removed resource is still staged: {relative} / 待删除资源仍存在于计划文件中")
            targets.append(target)
        from .regions import apply as apply_region

        region_draft = apply_region(ctx.store, workspace_id, out_dir / "files")
        if region_draft is not None:
            draft = region_draft
        _copy_files(out_dir / "files", directory)
        for target in targets:
            target.unlink(missing_ok=True)
        document = _rebase(document, out_dir / "files", directory)
        changes: dict = {"scenario": document, "name": name, "revision": draft["revision"]}
        saved = ctx.store.save(workspace_id, changes)
        # Preview markers are plan-local; later ordinary plans must not reapply an old region.
        (directory / "region.plan.json").unlink(missing_ok=True)
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
