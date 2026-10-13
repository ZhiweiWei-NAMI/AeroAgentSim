"""Atomic on-disk drafts exporting the platform's versioned scenario YAML."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import Scenario, load_scenario
from aeroagentsim.scenario.loader import UniqueLoader
from aeroagentsim.scenario.paths import source_path

from .catalog import Catalog, ScenarioCatalog, SnapshotCatalog
from .templates import (
    ENERGY,
    POS,
    STATE,
    VEL,
    motion,
    motion_messages,
    starter,
    workflow,
)


def _simple_polygon(points: list[list[float]]) -> bool:
    def cross(a: list[float], b: list[float], c: list[float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    edges = list(zip(points, points[1:] + points[:1]))
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j <= i or j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            ac, ad, ca, cb = (
                cross(a, b, c),
                cross(a, b, d),
                cross(c, d, a),
                cross(c, d, b),
            )
            if (
                ac * ad <= 0
                and ca * cb <= 0
                and max(min(a[0], b[0]), min(c[0], d[0]))
                <= min(max(a[0], b[0]), max(c[0], d[0]))
                and max(min(a[1], b[1]), min(c[1], d[1]))
                <= min(max(a[1], b[1]), max(c[1], d[1]))
            ):
                return False
    return True


# Configuration sections of the product logic, keyed by scenario top-level key.
SECTIONS = {
    "city": "space", "presentation": "space", "region": "space", "airspace": "space",
    "origin": "space",
    "registry": "objects", "entities": "objects",
    "relations": "relations", "resources": "relations",
    "behaviours": "tasks", "generators": "tasks", "tasks": "tasks",
    "engines": "components", "bindings": "components",
    "algorithms": "algorithms", "agents": "algorithms", "decisions": "algorithms",
    "run": "run", "outputs": "run", "ingress_streams": "run", "format": "run", "id": "run",
}


def issue_location(scenario: dict[str, Any], path: str, message: str) -> dict[str, Any]:
    """Locate real loader diagnostics, retaining dotted fact IDs as single pointer tokens."""
    if path.startswith("/"):
        parts = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
    else:
        authored = path
        if authored in {"", "$"}:
            # ScenarioError labels identify the failing input even without a .path attribute.
            head = re.match(r"([A-Za-z_][A-Za-z0-9_.\-]*(?:\[\d+\][A-Za-z0-9_.\-]*)*):", message)
            if not head or head.group(1).split(".")[0].split("[")[0] not in SECTIONS:
                return {}
            authored = head.group(1)
        tokens = re.sub(r"\[(\d+)\]", r".\1", authored.lstrip("$.")).split(".")
        # A package compiler path is relative to its source file, not to the scenario.
        # Keep that diagnostic intact instead of inventing a scenario pointer for it.
        if not tokens or tokens[0] not in SECTIONS:
            return {}
        parts: list[str] = []
        node: Any = scenario
        while tokens:
            # Match keys in the actual draft: lowalt.site_capacity is one field, not two.
            length = next((size for size in range(len(tokens), 0, -1) if isinstance(node, dict) and ".".join(tokens[:size]) in node), 1)
            part = ".".join(tokens[:length])
            del tokens[:length]
            parts.append(part)
            if isinstance(node, dict):
                node = node.get(part)
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                node = None
    location: dict[str, Any] = {"path": "".join("/" + part.replace("~", "~0").replace("/", "~1") for part in parts)}
    if parts and parts[0] in SECTIONS:
        location["section"] = SECTIONS[parts[0]]
    if parts and parts[0] == "entities" and len(parts) > 1 and parts[1].isdigit():
        index = int(parts[1])
        entities = scenario.get("entities", [])
        if isinstance(entities, list) and index < len(entities) and isinstance(entities[index], dict):
            if "id" in entities[index]:
                location["entity_id"] = entities[index]["id"]
        if len(parts) > 2:
            location["field"] = parts[3] if parts[2] == "facts" and len(parts) > 3 else parts[2]
    elif parts[0] in {"engines", "algorithms"} and len(parts) > 1:
        location["binding"] = parts[1]
    return location


def validate_configuration(document: dict[str, Any], *, base: Path, directory: Path) -> Scenario:
    """Bind configuration contracts; bootstrap and native resets belong to execution."""
    loaded = load_scenario(document, base=base)
    with tempfile.TemporaryDirectory(prefix="validation-", dir=directory) as scratch:
        Simulation(loaded, run_directory=Path(scratch)).close()
    return loaded


def validation_failure(document: dict[str, Any], error: Exception) -> dict[str, Any]:
    authored: BaseException = error
    while authored.__cause__ is not None and not hasattr(authored, "path"):
        authored = authored.__cause__
    path = getattr(authored, "path", "$")
    return {
        "valid": False,
        "errors": [f"{type(error).__name__}: {error}"],
        "issues": [{
            "source": getattr(authored, "source", "scenario"),
            "path": path,
            "message": str(authored),
            **issue_location(document, path, str(authored)),
        }],
    }


class WorkspaceConflict(ValueError):
    """An edit targets an older saved revision."""


class WorkspaceOperationError(ValueError):
    """A deterministic form operation failed at an authored field."""

    def __init__(self, operation: str | None, field: str, message: str) -> None:
        self.operation, self.field = operation, field
        if " / " not in message:
            message += " / 操作配置无效，请按字段提示修正"
        super().__init__(message)


class WorkspaceStore:
    """Draft edits are reversible; validation compiles real registry and engine contracts."""

    def __init__(self, root: Path, ontology_root: Path | None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.ontology_root = (
            ontology_root.resolve() if ontology_root is not None else None
        )
        self.catalog: Catalog
        if self.ontology_root is None:
            from .templates import demo_source

            source = demo_source("traffic-accident")
            self.catalog = SnapshotCatalog(
                yaml.load((source / "scenario.yaml").read_bytes(), Loader=UniqueLoader),
                base=source,
            )
        else:
            self.catalog = Catalog(self.ontology_root)
        self.lock = threading.RLock()
        self._snapshot_catalogs: dict[
            str, tuple[tuple[str, int, int], SnapshotCatalog]
        ] = {}

    def catalog_for(self, identifier: str | None = None) -> Catalog:
        if identifier is None:
            if self.ontology_root is not None:
                return self.catalog
            workspaces = self.list()
            if len(workspaces) == 1:
                identifier = workspaces[0]["id"]
        if identifier is None:
            return self.catalog
        draft = self.get(identifier)
        document = draft["scenario"]
        # Entity and engine edits do not change the ontology. Cache the actual
        # compiled registry by its declaration, not by every workspace save.
        base = self._base(document, identifier)
        if "snapshot" not in document["registry"]:
            return ScenarioCatalog(document, base=base)
        snapshot_stat = source_path(document["registry"]["snapshot"], base).stat()
        registry_key = (
            json.dumps({"registry": document["registry"], "behaviour": "behaviours" in document or any(isinstance(e, dict) and e.get("plugin") == "behaviour" for e in document.get("engines", {}).values())}, sort_keys=True),
            snapshot_stat.st_size,
            snapshot_stat.st_mtime_ns,
        )
        with self.lock:
            cached = self._snapshot_catalogs.get(identifier)
            if cached is not None and cached[0] == registry_key:
                return cached[1]
            catalog = SnapshotCatalog(document, base=base)
            if len(self._snapshot_catalogs) >= 32:
                self._snapshot_catalogs.pop(next(iter(self._snapshot_catalogs)))
            self._snapshot_catalogs[identifier] = (registry_key, catalog)
            return catalog

    def directory(self, identifier: str) -> Path:
        if not re.fullmatch(r"studio-[a-f0-9]{32}", identifier):
            raise ValueError("workspace id: unknown or invalid identifier")
        directory = (self.root / identifier).resolve()
        if directory.parent != self.root:
            raise ValueError("workspace path escapes storage root")
        return directory

    def _write(self, draft: dict[str, Any]) -> dict[str, Any]:
        directory = self.directory(draft["id"])
        directory.mkdir(exist_ok=True)
        draft["network_state"] = self.network_state(draft, directory)
        path = directory / "draft.json"
        stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        metadata = {"revision", "updated_at", "validation", "behaviour_validation", "notes"}

        def contents(value: dict[str, Any]) -> str:
            return json.dumps({key: item for key, item in value.items() if key not in metadata}, sort_keys=True, ensure_ascii=False, allow_nan=False)

        changed = stored is None or contents(stored) != contents(draft)
        draft["revision"] = (stored.get("revision", 0) if stored else 0) + int(changed)
        if changed:
            draft.pop("validation", None)
            draft.pop("behaviour_validation", None)
        now = datetime.now(timezone.utc).isoformat()
        draft["updated_at"] = now
        payload = json.dumps(draft, ensure_ascii=False, allow_nan=False, indent=2)
        pending = directory / "draft.pending.json"
        pending.write_text(payload, encoding="utf-8")
        pending.replace(directory / "draft.json")
        return copy.deepcopy(draft)

    def create(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name: nonempty string required")
        identifier = "studio-" + uuid.uuid4().hex
        with self.lock:
            document = starter(identifier, self.ontology_root or Path("."))
            if self.ontology_root is None:
                from .templates import demo_source

                directory = self.directory(identifier)
                directory.mkdir(exist_ok=True)
                shutil.copyfile(
                    demo_source("traffic-accident") / "registry.snapshot.json",
                    directory / "registry.snapshot.json",
                )
                document["registry"].pop("compile")
                document["registry"]["snapshot"] = "registry.snapshot.json"
            return self._write(
                {
                    "id": identifier,
                    "name": name.strip(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "scenario": document,
                }
            )

    def list(self) -> list[dict[str, Any]]:
        drafts = [
            self.summary(p.name)
            for p in sorted(self.root.glob("studio-*"))
            if (p / "draft.json").is_file()
        ]
        return sorted(drafts, key=lambda draft: draft["updated_at"], reverse=True)

    def get(self, identifier: str) -> dict[str, Any]:
        path = self.directory(identifier) / "draft.json"
        if not path.is_file():
            raise FileNotFoundError("workspace: not found")
        draft: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        draft.setdefault("revision", 0)  # Legacy drafts precede revision tracking.
        # Older drafts have no edit metadata; the stored file's modification time
        # is the actual last write, without inventing a creation date.
        draft.setdefault(
            "updated_at",
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        )
        draft["network_state"] = self.network_state(draft, path.parent)
        return draft

    @staticmethod
    def check_revision(draft: dict[str, Any], expected: int | None) -> None:
        if expected is None:
            return
        if type(expected) is not int or expected < 0:
            raise ValueError("workspace.revision: nonnegative integer required")
        if expected != draft["revision"]:
            raise WorkspaceConflict(f"Workspace changed from revision {expected} to {draft['revision']}; reload the saved draft before applying your changes")

    def save(self, identifier: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict) or set(changes) - {"name", "scenario", "revision"}:
            raise ValueError("workspace: only name, scenario and expected revision may be supplied")
        with self.lock:
            draft = self.get(identifier)
            self.check_revision(draft, changes.get("revision"))
            if "name" in changes and (
                not isinstance(changes["name"], str) or not changes["name"].strip()
            ):
                raise ValueError("name: nonempty string required")
            if "scenario" in changes and not isinstance(changes["scenario"], dict):
                raise TypeError("scenario: mapping required")
            if "scenario" in changes and not changes["scenario"]:
                raise ValueError("scenario: an empty root would erase the whole draft; remove only the selected object or generator / 空根对象会清空整个草稿，请只删除选定对象或生成器")
            draft.update(copy.deepcopy({key: value for key, value in changes.items() if key != "revision"}))
            if "scenario" in changes:
                from .wire import normalize_wire

                draft["scenario"] = normalize_wire(
                    draft["scenario"],
                    self.catalog,
                    base=self._base(draft["scenario"], identifier),
                )
                self._resolve_city(draft)
            return self._write(draft)

    @staticmethod
    def _resolve_city(draft: dict[str, Any], *, require_assets: bool = True) -> None:
        from .scene import region_source

        if "scene" in draft:
            draft["scene_source"] = region_source(draft.get("region", {}), draft["scene"])
            # A conflict remains authored and is reported by validation; the
            # preview must still use the compiled region, including in old drafts.
            draft.pop("demo_console", None)
        elif draft["scenario"].get("city") == "huangpu":
            from .demo import city_file, city_presentation

            asset_error = None
            try:
                city_file("scene.json")
            except FileNotFoundError as exc:
                if require_assets:
                    raise
                asset_error = f"City descriptor unavailable: {exc} / 城市资源描述不可用：{exc}"
            scene = city_presentation()
            draft["demo_console"] = {
                **draft.get("demo_console", {}), "capture_mode": "city",
                "city_available": asset_error is None, "scene": scene,
            }
            draft["demo_console"].pop("city_error", None)
            if asset_error is not None:
                draft["demo_console"]["city_error"] = asset_error
            draft["scene_source"] = {"kind": "fixed-city", "id": "huangpu", "attribution": scene["attribution"]}
        else:
            draft.pop("demo_console", None)
            draft.pop("scene_source", None)

    def operations(self, identifier: str, body: dict[str, Any], *, templates: Any, agent_profile: str | None = None) -> dict[str, Any]:
        """Compile form operations with the assistant compiler; apply one draft edit.

        Compilation uses a disposable sibling directory. Dry runs and failures
        leave the workspace and its resources untouched, and never call an LLM.
        """
        from aeroagentsim.assistant.compiler import CompileContext, _copy_files, _rebase, compile_plan
        from aeroagentsim.assistant.operations import OperationError, STRING, obj, validate_parameters

        try:
            validate_parameters(body, obj({
                "operations": {"type": "array", "items": obj({"id": STRING, "op": STRING, "params": {"type": "object"}}, ("id", "op", "params"))},
                "revision": {"type": "integer", "minimum": 0}, "dry_run": {"type": "boolean"},
            }, ("operations",)), "body")
        except OperationError as exc:
            operation = None
            match = re.match(r"body.operations\[(\d+)\]", exc.field)
            if match and isinstance(body, dict) and isinstance(body.get("operations"), list):
                item = body["operations"][int(match[1])]
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    operation = item["id"]
            raise WorkspaceOperationError(operation, exc.field, str(exc)) from exc
        with self.lock:
            draft = self.get(identifier)
            self.check_revision(draft, body.get("revision"))
            with tempfile.TemporaryDirectory(prefix="operations-", dir=self.root) as scratch:
                result = compile_plan(CompileContext(self, templates, agent_profile),
                                      {"workspace": identifier, "revision": draft["revision"]},
                                      body["operations"], Path(scratch))
                if not result.ok:
                    error = result.errors[0]
                    raise WorkspaceOperationError(error["op_id"], error["field"], error["message"])
                try:
                    self._check_scene_source(draft, result.document)
                    validate_configuration(result.document, base=result.files_dir, directory=Path(scratch))
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    operation = body["operations"][-1]["id"] if body["operations"] else None
                    message = str(exc)
                    if " / " not in message:
                        message += " / 操作配置无效，请按字段提示修正"
                    raise WorkspaceOperationError(operation, getattr(exc, "path", "params"), message) from exc
                if body.get("dry_run") is True:
                    return {"diff": {key: result.diff_base[key] for key in ("added", "changed", "removed")}, "notes": result.notes + [warning["message"] for warning in result.warnings if warning["message"] not in result.notes]}
                directory = self.directory(identifier)
                document = _rebase(result.document, result.files_dir, directory)
                self._base(document, identifier)
                _copy_files(result.files_dir, directory)
                draft["scenario"] = document
                draft["notes"] = result.notes + [warning["message"] for warning in result.warnings if warning["message"] not in result.notes]
                if draft.get("template"):
                    record = json.loads((Path(scratch) / "compile.json").read_text(encoding="utf-8"))
                    draft["template"]["knobs"] = record["controls"]
                self._resolve_city(draft)
                self._write(draft)
            return self.summary(identifier)

    @staticmethod
    def _check_scene_source(draft: dict[str, Any], document: dict[str, Any] | None = None) -> None:
        from aeroagentsim.scenario.loader import ScenarioError

        authored = draft["scenario"] if document is None else document
        if "scene" in draft and authored.get("city") is not None:
            raise ScenarioError("city: fixed city conflicts with the compiled region; choose one source / 固定城市与已编译区域冲突，请选择一个来源")
        if "scene" in draft and "origin" in draft["scene"] and authored.get("origin") != draft["scene"]["origin"]:
            raise ScenarioError("origin: must match the compiled region origin; preview and apply a region change / 原点须与已编译区域一致，请预览并应用区域变更")

    def import_demo(
        self,
        identifier: str,
        name: str,
        *,
        console: bool = False,
        primitive: bool = False,
        operator_injection: bool = True,
    ) -> dict[str, Any]:
        """Import the actual shipped draft and pinned source files, without implying validity."""
        from .templates import demo_source

        source = demo_source(name)
        with self.lock:
            draft = self.get(identifier)
            document = yaml.load(
                (source / "scenario.yaml").read_bytes(), Loader=UniqueLoader
            )
            if not isinstance(document, dict):
                raise TypeError("demo scenario: mapping required")
            files = [path for path in source.rglob("*") if path.is_file()]
            for path in files:
                if not path.resolve().is_relative_to(source.resolve()):
                    raise ValueError("demo source: symlink escapes template scope")
            for path in files:
                target = self.directory(identifier) / path.relative_to(source)
                if not target.resolve().is_relative_to(self.directory(identifier)):
                    raise ValueError("demo target escapes workspace")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
            if console:
                from aeroagentsim.integrations.aerograph import read_snapshot

                draft["registry_catalog"] = read_snapshot(
                    self.directory(identifier) / document["registry"]["snapshot"]
                ).registry.to_data()
                from .demo import configure_console

                draft["demo_console"] = configure_console(
                    document, self.directory(identifier), primitive=primitive
                )
            draft["scenario"] = document
            draft.pop("validation", None)
            draft.pop("behaviour_validation", None)
            draft.pop("behaviour_layout", None)
            self._write(draft)
            if console:
                exported = self.export_behaviour(identifier, 0)
                if exported["inline_package"] is None:
                    raise ValueError(
                        "Demo package could not be made editable: "
                        + str(exported["inline_errors"])
                    )
                draft["scenario"]["behaviours"] = [exported["inline_package"]]
                if operator_injection:
                    # The console operator chooses occurrence time; the file-run timer stays in the source template.
                    chain = draft["scenario"]["behaviours"][0]["chains"][
                        "traffic.incident_report"
                    ]
                    chain["transitions"] = [
                        row
                        for row in chain["transitions"]
                        if row["id"] not in {"schedule", "timer"}
                    ]
                    draft["scenario"]["ingress_streams"][0]["initial_watermark_ns"] = 0
                    # Interactive operator input has no implicit deadline. Users can
                    # explicitly author timeout_s on the imported scenario if needed.
                    draft["scenario"]["ingress_streams"][0].pop("timeout_s", None)
            return self._write(draft)

    def import_template(
        self,
        template_id: str,
        knobs: dict[str, Any],
        *,
        registry: Any,
    ) -> dict[str, Any]:
        """Copy public template inputs into an editable, independently based draft."""
        prepared = registry.apply(template_id, knobs)
        source = prepared.base.resolve()
        files = [path for path in source.rglob("*") if path.is_file()]
        for path in files:
            if not path.resolve().is_relative_to(source):
                raise ValueError("template resource symlink escapes source scope")
        with self.lock:
            draft = self.create(prepared.metadata["title"])
            directory = self.directory(draft["id"])
            for path in files:
                if "__pycache__" in path.parts:
                    continue
                target = directory / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            document = copy.deepcopy(prepared.document)
            snapshot = document["registry"].get("snapshot")
            if snapshot is not None and Path(snapshot).is_absolute():
                document["registry"]["snapshot"] = str(
                    Path(snapshot).resolve().relative_to(source)
                )
            decisions = document.get("engines", {}).get("decisions")
            if decisions is not None and decisions["plugin"] == "traffic_decisions":
                decisions["config"]["fixture_path"] = str(
                    directory / "fixtures/decisions.json"
                )
            draft["scenario"] = document
            draft["template"] = {
                "id": template_id,
                "knobs": prepared.knobs,
            }
            self._resolve_city(draft)
            self._base(document, draft["id"])
            self._write(draft)
            if document.get("behaviours"):
                exported = self.export_behaviour(draft["id"], 0)
                if exported["inline_package"] is None:
                    raise ValueError(
                        "Template behaviour could not be made editable: "
                        + str(exported["inline_errors"])
                    )
                draft["scenario"]["behaviours"][0] = exported["inline_package"]
            return self._write(draft)

    def _behaviour_package(self, identifier: str, index: int) -> dict[str, Any]:
        draft = self.get(identifier)
        entries = draft["scenario"].get("behaviours", [])
        if (
            type(index) is not int
            or not isinstance(entries, list)
            or not 0 <= index < len(entries)
        ):
            raise ValueError("behaviours.index: existing package index required")
        package = entries[index]
        if not isinstance(package, dict):
            raise TypeError("behaviours: package mapping required")
        if "path" in package:
            if set(package) - {"path", "sha256"} or not isinstance(
                package["path"], str
            ):
                raise ValueError("behaviours: path reference required")
            path = (self.directory(identifier) / package["path"]).resolve()
            if not path.is_relative_to(self.directory(identifier)):
                raise ValueError("behaviours.path: must stay inside workspace")
            data = path.read_bytes()
            package = yaml.load(data, Loader=UniqueLoader)
            if not isinstance(package, dict):
                raise TypeError("behaviours: YAML mapping required")
        return copy.deepcopy(package)

    def edit_behaviour(self, identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        """Keep unsupported authored content; editing never implies compilation success."""
        if (
            set(body) - {"index", "package", "yaml", "layout"}
            or not set(body) & {"package", "yaml", "layout"}
            or {"package", "yaml"} <= set(body)
        ):
            raise ValueError(
                "behaviours: supply package or yaml, and optional index/layout"
            )
        with self.lock:
            draft = self.get(identifier)
            entries = draft["scenario"].get("behaviours", [])
            if not isinstance(entries, list):
                raise TypeError("behaviours: list required")
            index = body.get("index", len(entries))
            if type(index) is not int or not 0 <= index <= len(entries):
                raise ValueError(
                    "behaviours.index: replace existing or append at length"
                )
            if "package" in body or "yaml" in body:
                package = body.get("package")
                if "yaml" in body:
                    if not isinstance(body["yaml"], str):
                        raise TypeError("behaviours.yaml: string required")
                    try:
                        package = yaml.load(body["yaml"], Loader=UniqueLoader)
                    except yaml.YAMLError as exc:
                        raise ValueError(f"behaviours.yaml: {exc}") from exc
                if not isinstance(package, dict):
                    raise TypeError("behaviours.package: mapping required")
                entries = copy.deepcopy(entries)
                if index == len(entries):
                    entries.append(copy.deepcopy(package))
                else:
                    entries[index] = copy.deepcopy(package)
                draft["scenario"]["behaviours"] = entries
            elif index == len(entries):
                raise ValueError("behaviours.layout: select an existing package")
            if "layout" in body:
                if not isinstance(body["layout"], dict):
                    raise TypeError("behaviours.layout: mapping required")
                draft.setdefault("behaviour_layout", {})[str(index)] = copy.deepcopy(
                    body["layout"]
                )
            draft.pop("validation", None)
            draft.pop("behaviour_validation", None)
            return self._write(draft)

    def export_behaviour(self, identifier: str, index: int) -> dict[str, Any]:
        """Draft export is available even for unsupported content."""
        package = self._behaviour_package(identifier, index)
        text = yaml.safe_dump(package, allow_unicode=True, sort_keys=True)
        draft = self.get(identifier)
        spec = draft["scenario"]["behaviours"][index]
        root = self.directory(identifier)
        base = (root / spec["path"]).parent if "path" in spec else root
        inline_model = copy.deepcopy(package)
        inline: dict[str, Any] | None = inline_model
        inline_errors: list[str] = []

        def rebase(document: dict[str, Any]) -> None:
            for imported in document.get("imports", []):
                if not isinstance(imported, dict):
                    raise TypeError("behaviours.imports: mapping required")
                if "path" in imported:
                    imported_path = (base / imported["path"]).resolve()
                    if not imported_path.is_relative_to(root):
                        raise ValueError(
                            "behaviours.imports: must stay inside workspace"
                        )
                    imported["path"] = imported_path.relative_to(root).as_posix()
                else:
                    rebase(imported.get("document", imported))
            registry = document.get("registry")
            if isinstance(registry, dict) and "snapshot" in registry:
                snapshot = (base / registry["snapshot"]).resolve()
                if not snapshot.is_relative_to(root):
                    raise ValueError(
                        "behaviours.registry.snapshot: must stay inside workspace"
                    )
                registry["snapshot"] = snapshot.relative_to(root).as_posix()

        if base != root:
            try:
                rebase(inline_model)
            except (ValueError, TypeError, KeyError) as exc:
                inline = None
                inline_errors.append(str(exc))
        return {
            "package": package,
            "inline_package": inline,
            "inline_errors": inline_errors,
            "yaml": text,
            "layout": self.get(identifier)
            .get("behaviour_layout", {})
            .get(str(index), {}),
        }

    def validate_behaviour(self, identifier: str, index: int) -> dict[str, Any]:
        """Validate with the real compiler in the scenario's registry and writer context."""
        with self.lock:
            self._behaviour_package(identifier, index)
            result = self.validate(identifier)
            return {
                **result,
                "scope": "bound_scenario",
                "compiler_available": True,
                "errors": result.get("issues", []),
            }

    def region(
        self, identifier: str, region: dict[str, Any], source: Path
    ) -> dict[str, Any]:
        from .scene import compile_scene, crop_osm, origin_distance, preview_geometry, region_source

        required = {"extract", "bounds", "alt", "level_height_m"}
        if required - region.keys() or region.keys() - required - {"dry_run"}:
            raise ValueError("region: extract/bounds/alt/level_height_m required")
        if "dry_run" in region and type(region["dry_run"]) is not bool:
            raise ValueError("region.dry_run: Boolean required / dry_run 须为布尔值")
        with self.lock:
            draft = self.get(identifier)
            scene = compile_scene(
                source,
                region["bounds"],
                alt=region["alt"],
                level_height_m=region["level_height_m"],
            )
            if region.get("dry_run") is True:
                document = draft["scenario"]
                previous = document.get("origin", draft.get("scene", {}).get("origin"))
                position_fields = {POS}
                position_fields.update(row["positionField"] for row in document.get("presentation", []) if "positionField" in row)
                position_fields.update(
                    spec["config"]["position_field"] for spec in document.get("engines", {}).values()
                    if "position_field" in spec.get("config", {})
                )
                position_fields.update(
                    field["id"] for field in document["registry"].get("fields", [])
                    if "position" in field["id"] and field.get("metadata", {}).get("frame") in {"enu", "ned", "wgs84"}
                )
                return {"preview": {
                    "extract": region["extract"], "bounds": scene["bounds"], "origin": scene["origin"],
                    "previous_origin": copy.deepcopy(previous),
                    "origin_shift_m": origin_distance(previous, scene["origin"]) if previous is not None else None,
                    "buildings": sum(feature["geometry"]["type"] in {"Polygon", "MultiPolygon"} for feature in scene["geojson"]["features"]),
                    "roads": len(scene["roads"]), "attribution": scene["attribution"],
                    "height_source": region_source(region, scene)["height_source"],
                    "diagnostics": scene["diagnostics"],
                    "geometry": preview_geometry(scene),
                    "affected_entities": [
                        entity["id"] for entity in document["entities"]
                        if any(isinstance(entity["facts"].get(field), list) and len(entity["facts"][field]) == 3 for field in position_fields)
                    ],
                    "network_invalidated": "network" in draft or (self.directory(identifier) / "network.net.xml").is_file(),
                    "city_conflict": document.get("city") is not None,
                }}
            crop_osm(
                source, region["bounds"], self.directory(identifier) / "region.osm.xml"
            )
            # A new crop invalidates the previous traffic network.
            had_network = draft["network_state"] != "none" or (self.directory(identifier) / "network.net.xml").is_file()
            (self.directory(identifier) / "network.net.xml").unlink(missing_ok=True)
            draft.pop("network", None)
            draft["network_state"] = "stale" if had_network else "none"
            draft["region"] = {key: copy.deepcopy(region[key]) for key in required}
            draft["scene"] = scene
            draft["scenario"]["origin"] = scene["origin"]
            self._resolve_city(draft)
            draft.pop("validation", None)
            return self._write(draft)

    def _check_behaviour_refs(self, specs: Any, identifier: str, *, directory: Path | None = None) -> None:
        root = self.directory(identifier) if directory is None else directory.resolve()
        active: set[Path] = set()

        def visit(spec: Any, base: Path) -> None:
            if not isinstance(spec, dict):
                raise TypeError("behaviours: package mapping required")
            path: Path | None = None
            if "path" in spec:
                if not isinstance(spec["path"], str):
                    raise TypeError("behaviours.path: string required")
                path = (base / spec["path"]).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("behaviours.path: must stay inside workspace")
                if path in active:
                    raise ValueError("behaviours.imports: cyclic package references")
                data = path.read_bytes()
                active.add(path)
                document = yaml.load(data, Loader=UniqueLoader)
                base = path.parent
            else:
                document = spec.get("document", spec)
            if not isinstance(document, dict):
                raise TypeError("behaviours: package mapping required")
            imports = document.get("imports", [])
            if not isinstance(imports, list):
                raise TypeError("behaviours.imports: list required")
            for imported in imports:
                visit(imported, base)
            if path is not None:
                active.remove(path)

        if not isinstance(specs, list):
            raise TypeError("behaviours: list required")
        for spec in specs:
            visit(spec, root)

    def _base(self, document: dict[str, Any], identifier: str, *, directory: Path | None = None) -> Path:
        """Browser imports cannot widen the configured source-file scope."""
        root = self.directory(identifier) if directory is None else directory.resolve()
        registry = document.get("registry", {})
        if not isinstance(registry, dict):
            raise TypeError("registry: mapping required")
        if "compile" in registry:
            spec = registry["compile"]
            if (
                not isinstance(spec, dict)
                or Path(spec.get("root", "")).resolve() != self.ontology_root
            ):
                raise ValueError(
                    "registry.compile.root: must use configured AeroGraph root"
                )
        if "snapshot" in registry:
            snapshot = source_path(registry["snapshot"], root).resolve()
            if not snapshot.is_relative_to(root):
                raise ValueError("registry.snapshot: must be inside this workspace")
        if "behaviours" in document:
            self._check_behaviour_refs(document["behaviours"], identifier, directory=root)
        for engine in document.get("engines", {}).values():
            if engine.get("plugin") == "behaviour" and "packages" in engine.get(
                "config", {}
            ):
                self._check_behaviour_refs(engine["config"]["packages"], identifier, directory=root)
        return root

    def validate(
        self, identifier: str, scenario: dict[str, Any] | None = None, *, revision: int | None = None
    ) -> dict[str, Any]:
        with self.lock:
            self.check_revision(self.get(identifier), revision)
            draft = (
                self.save(identifier, {"scenario": scenario})
                if scenario is not None
                else self.get(identifier)
            )
            try:
                self._check_scene_source(draft)
                loaded = validate_configuration(
                    draft["scenario"], base=self._base(draft["scenario"], identifier),
                    directory=self.directory(identifier),
                )
                result: dict[str, Any] = {
                    "valid": True,
                    "errors": [],
                    "scenario_id": loaded.run_id,
                    "scope": "configuration",
                }
            except Exception as exc:  # noqa: BLE001 - validation returns the real diagnostic
                result = validation_failure(draft["scenario"], exc)
            result["revision"] = draft["revision"]
            draft["validation"] = result
            self._write(draft)
            return result

    def apply_knobs(self, identifier: str, body: dict[str, Any], *, registry: Any) -> dict[str, Any]:
        """Change template controls on the draft itself, keeping other edits."""
        from .templates import _knob_value, _pointer

        if not isinstance(body, dict) or set(body) - {"knobs", "revision"} or not isinstance(body.get("knobs"), dict):
            raise ValueError("knobs: a knobs mapping and the expected revision are required")
        with self.lock:
            draft = self.get(identifier)
            self.check_revision(draft, body.get("revision"))
            source = draft.get("template")
            if not source:
                raise ValueError("This draft was not created from a template and has no template controls")
            declared = {knob["id"]: knob for knob in registry.get(source["id"])["knobs"]}
            if extra := body["knobs"].keys() - declared.keys():
                raise ValueError(f"Unknown knobs: {', '.join(sorted(extra))}")
            document = copy.deepcopy(draft["scenario"])
            values = dict(source.get("knobs", {}))
            for name, value in body["knobs"].items():
                knob = declared[name]
                _knob_value(knob, value)
                if knob["type"] == "number":
                    value = float(value)
                if "pointer" in knob:
                    _pointer(document, knob["pointer"], value)
                else:
                    registry.hooks[knob["apply"]](document, self.directory(identifier), value)
                values[name] = value
            saved = self.save(identifier, {"scenario": document})
            saved["template"] = {**source, "knobs": values}
            return self._write(saved)

    # --- Immutable versions -------------------------------------------------
    # A version freezes a validated draft revision together with every file in
    # the workspace, so a run of that version never observes later draft edits.
    _VERSION_EXCLUDE = {"draft.json", "draft.pending.json", "versions"}

    def version_directory(self, identifier: str, version: int) -> Path:
        if type(version) is not int or version < 1:
            raise ValueError("version: positive integer required")
        return self.directory(identifier) / "versions" / str(version) / "files"

    def _version_record(self, identifier: str, version: int) -> Path:
        return self.directory(identifier) / "versions" / str(version) / "version.json"

    def versions(self, identifier: str) -> list[dict[str, Any]]:
        self.get(identifier)
        root = self.directory(identifier) / "versions"
        rows = []
        if root.is_dir():
            for path in root.iterdir():
                record = path / "version.json"
                if path.name.isdigit() and record.is_file():
                    row = json.loads(record.read_text(encoding="utf-8"))
                    row.pop("scenario", None)
                    rows.append(row)
        return sorted(rows, key=lambda row: row["version"], reverse=True)

    def version(self, identifier: str, version: int) -> dict[str, Any]:
        record = self._version_record(identifier, version)
        if not record.is_file():
            raise FileNotFoundError(f"workspace version {version}: not found")
        return json.loads(record.read_text(encoding="utf-8"))

    def save_version(self, identifier: str, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict) or set(body) - {"revision", "note"}:
            raise ValueError("version: only revision and an optional note may be supplied")
        revision, note = body.get("revision"), body.get("note")
        if type(revision) is not int:
            raise ValueError("version.revision: integer required")
        if note is not None and not isinstance(note, str):
            raise ValueError("version.note: string required")
        with self.lock:
            draft = self.get(identifier)
            self.check_revision(draft, revision)
            self._resolve_city(draft)
            validation = draft.get("validation")
            if not validation or validation.get("revision") != revision:
                raise ValueError("Validate this revision before saving it as a version")
            if not validation.get("valid"):
                raise ValueError("This revision failed validation; fix the reported issues first")
            self._check_scene_source(draft)
            directory = self.directory(identifier)
            existing = self.versions(identifier)
            latest = existing[0] if existing else None
            if latest is not None and latest["revision"] == revision:
                return self.version(identifier, latest["version"])
            number = (latest["version"] if latest else 0) + 1
            files = self.version_directory(identifier, number)
            pending = files.parent.with_name(f".{number}.pending")
            if pending.exists():
                shutil.rmtree(pending)
            (pending / "files").mkdir(parents=True)
            for path in sorted(directory.iterdir()):
                if path.name in self._VERSION_EXCLUDE or path.name.startswith("validation-"):
                    continue
                target = pending / "files" / path.name
                if path.is_dir():
                    shutil.copytree(path, target, ignore=shutil.ignore_patterns("__pycache__"))
                else:
                    shutil.copyfile(path, target)
            # Absolute paths into the live draft directory would read later edits.
            text = json.dumps(draft["scenario"], ensure_ascii=False, allow_nan=False)
            text = text.replace(json.dumps(str(directory))[1:-1], json.dumps(str(files))[1:-1])
            scenario = json.loads(text)
            geometry = {key: copy.deepcopy(draft[key]) for key in ("demo_console", "region", "scene", "scene_source", "network", "network_state") if key in draft}
            digest = hashlib.sha256()
            digest.update(json.dumps(scenario, sort_keys=True, ensure_ascii=False).encode())
            digest.update(json.dumps(geometry, sort_keys=True, ensure_ascii=False, allow_nan=False).encode())
            for path in sorted(p for p in (pending / "files").rglob("*") if p.is_file()):
                digest.update(str(path.relative_to(pending / "files")).encode())
                digest.update(path.read_bytes())
            record = {
                "version": number,
                "revision": revision,
                "digest": "sha256:" + digest.hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "validation": validation,
                "scenario": scenario,
                **({"note": note.strip()} if note and note.strip() else {}),
                **({"template": draft["template"]} if draft.get("template") else {}),
                **({"derived_from": draft["derived_from"]} if draft.get("derived_from") else {}),
                # Display geometry the run attaches; frozen with the version.
                **geometry,
            }
            (pending / "version.json").write_text(
                json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
            )
            pending.replace(files.parent)
            return record

    def summary(self, identifier: str) -> dict[str, Any]:
        """The draft plus its version state, as the console shows it."""
        draft = self.get(identifier)
        self._resolve_city(draft, require_assets=False)
        versions = self.versions(identifier)
        latest = versions[0] if versions else None
        draft["latest_version"] = latest
        draft["dirty"] = latest is None or latest["revision"] != draft["revision"]
        from aeroagentsim.packs.lowalt.template import has_lowalt_components

        draft["capabilities"] = {
            "lowalt": has_lowalt_components(draft["scenario"]),
            "region": "region" in draft and "scene" in draft,
            "network": draft["network_state"] == "generated",
            "city": draft.get("scene_source", {}).get("kind") == "fixed-city" and draft.get("demo_console", {}).get("city_available") is True,
        }
        return draft

    @staticmethod
    def network_source(draft: dict[str, Any], directory: Path) -> dict[str, Any]:
        return {"region": copy.deepcopy(draft["region"]),
                "osm_sha256": hashlib.sha256((directory / "region.osm.xml").read_bytes()).hexdigest()}

    @classmethod
    def network_state(cls, draft: dict[str, Any], directory: Path) -> str:
        if "network" not in draft:
            previous = draft.get("network_state")
            # Read BE-2 records through the same field; new records use strings.
            if isinstance(previous, dict):
                previous = previous["status"]
            return "stale" if previous == "stale" else "none"
        if not (directory / "network.net.xml").is_file():
            return "stale"
        if "region" not in draft or not (directory / "region.osm.xml").is_file():
            return "stale"
        if draft["network"].get("source") != cls.network_source(draft, directory):
            return "stale"
        return "generated"

    def derive(self, run: Path, name: str | None, *, registry: Any = None) -> dict[str, Any]:
        """A new draft from a run's own configuration; the run is not changed."""
        manifest = json.loads((run / "manifest.json").read_text())
        source: dict[str, Any] = {"run_id": run.name}
        workspace_id, number = manifest.get("studio_workspace"), manifest.get("workspace_version")
        if workspace_id and type(number) is int:
            frozen = self.version(workspace_id, number)
            source.update(workspace_id=workspace_id, version=number)
            with self.lock:
                draft = self.create(name or f"{self.get(workspace_id)['name']} · variant")
                directory = self.directory(draft["id"])
                files = self.version_directory(workspace_id, number)
                for path in files.iterdir():
                    target = directory / path.name
                    if path.is_dir():
                        shutil.copytree(path, target)
                    else:
                        shutil.copyfile(path, target)
                text = json.dumps(frozen["scenario"], ensure_ascii=False)
                text = text.replace(json.dumps(str(files))[1:-1], json.dumps(str(directory))[1:-1])
                draft["scenario"] = json.loads(text)
                for key in ("template", "demo_console", "region", "scene", "scene_source", "network", "network_state"):
                    original = frozen.get(key)
                    if original is not None:
                        draft[key] = original
                draft["derived_from"] = source
                return self._write(draft)
        template = manifest.get("template")
        if template and registry is not None:
            draft = self.import_template(template["id"], template.get("knobs", {}), registry=registry)
            with self.lock:
                draft = self.get(draft["id"])
                if name:
                    draft["name"] = name
                draft["derived_from"] = source
                return self._write(draft)
        raise NotImplementedError(
            "This run was not created from a saved workspace version or a template; "
            "its configuration cannot be reopened as an editable draft"
        )

    def export(self, identifier: str) -> str:
        from .portable import pack

        # File is also the repair editor: exporting an authored draft does not
        # validate, start execution or mutate its revision.
        with self.lock:
            draft = self.get(identifier)
            if "network" in draft:
                if draft["network_state"] != "generated":
                    draft.pop("network")
            return yaml.safe_dump(pack(draft, self.directory(identifier)),
                                  allow_unicode=True, sort_keys=False)

    def import_yaml(self, identifier: str, text: str, *, revision: int | None = None) -> dict[str, Any]:
        try:
            document = yaml.load(text, Loader=UniqueLoader)
        except yaml.YAMLError as exc:
            raise ValueError(f"import: invalid YAML: {exc}") from exc
        if not isinstance(document, dict):
            raise TypeError("import: scenario YAML mapping required")
        from .portable import FORMAT, GEOGRAPHY, WORKSPACE, bind_paths, unpack, write_resources

        with self.lock:
            draft = self.get(identifier)
            self.check_revision(draft, revision)
            if document.get("format") != FORMAT:
                # Legacy scenario-only files retain the target's geography;
                # reject origin mismatches instead of silently changing it.
                self._check_scene_source(draft, document)
                load_scenario(document, base=self._base(document, identifier))
                self.save(identifier, {"scenario": document, "revision": revision})
                return self.summary(identifier)
            scenario, resources, metadata = unpack(document)
            candidate = copy.deepcopy(draft)
            for key in GEOGRAPHY | {"template", "scene_source", "demo_console", "registry_catalog", "behaviour_layout"}:
                candidate.pop(key, None)
            candidate.update(copy.deepcopy(metadata))
            directory = self.directory(identifier)
            with tempfile.TemporaryDirectory(prefix="import-", dir=self.root) as scratch:
                staging = Path(scratch)
                write_resources(resources, staging)
                staged = bind_paths(scenario, WORKSPACE, str(staging) + "/")
                candidate["scenario"] = staged
                if "region" in candidate:
                    from .scene import _bounds, _number

                    region, scene = candidate["region"], candidate["scene"]
                    if not isinstance(region, dict) or set(region) != {"extract", "bounds", "alt", "level_height_m"}:
                        raise ValueError("import.region: extract/bounds/alt/level_height_m required")
                    if not isinstance(region["extract"], str) or not region["extract"].strip():
                        raise ValueError("import.region.extract: nonempty source id required")
                    bounds = _bounds(region["bounds"])
                    alt = _number(region["alt"], "import.region.alt")
                    if _number(region["level_height_m"], "import.region.level_height_m") <= 0:
                        raise ValueError("import.region.level_height_m: must be positive")
                    origin = {"lon": (bounds[0] + bounds[2]) / 2, "lat": (bounds[1] + bounds[3]) / 2, "alt": alt}
                    if not isinstance(scene, dict) or scene.get("origin") != origin or scene.get("bounds") != bounds:
                        raise ValueError("import.scene: geometry must match the applied region's bounds and origin")
                    if not {"geojson", "roads", "ground", "diagnostics", "attribution"} <= scene.keys():
                        raise ValueError("import.scene: compiled geometry and source metadata required")
                self._check_scene_source(candidate)
                base = self._base(staged, identifier, directory=staging)
                from .wire import normalize_wire

                normalized = normalize_wire(staged, self.catalog, base=base)
                validate_configuration(normalized, base=base, directory=staging)
                candidate["scenario"] = normalized
                self._resolve_city(candidate)
                candidate["network_state"] = self.network_state(candidate, staging)
                # All schema and scope checks finish before any live file is
                # replaced. Existing saved versions are never overwritten.
                committed = bind_paths(normalized, str(staging) + "/", str(directory) + "/")
                # Reject non-JSON metadata before installing any resources.
                json.dumps(candidate, allow_nan=False)
                write_resources(resources, directory)
                if "network" not in candidate:
                    (directory / "network.net.xml").unlink(missing_ok=True)
                if "region" not in candidate:
                    (directory / "region.osm.xml").unlink(missing_ok=True)
                candidate["scenario"] = committed
                self._write(candidate)
            return self.summary(identifier)

    def place(self, identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"id", "type", "kind", "position", "engine"}
        if required - set(payload) or set(payload) - required - {
            "facts",
            "polygon",
            "floor_m",
            "ceiling_m",
        }:
            raise ValueError(
                "placement: id/type/kind/position/engine required; unknown fields rejected"
            )
        entity_id, type_id, kind, engine = (
            payload[k] for k in ("id", "type", "kind", "engine")
        )
        if any(not isinstance(x, str) or not x.strip() for x in (entity_id, type_id)):
            raise ValueError("placement id/type: nonempty strings required")
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", entity_id):
            raise ValueError(
                "placement.id: identifier cannot contain whitespace or wildcard selectors"
            )
        if kind not in {"entity", "facility", "airspace"} or engine not in {
            "kinematic",
            "workflow",
        }:
            raise ValueError(
                "placement kind/engine: use entity/facility/airspace and kinematic/workflow; edit engine config for other plugins"
            )
        position = payload["position"]
        if (
            not isinstance(position, list)
            or len(position) != 3
            or any(
                type(v) not in (float, int) or not math.isfinite(v) for v in position
            )
        ):
            raise ValueError("placement.position: finite ENU three-vector required")
        position = [float(v) for v in position]
        facts = copy.deepcopy(payload.get("facts", {}))
        if not isinstance(facts, dict):
            raise TypeError("placement.facts: mapping required")
        with self.lock:
            draft = self.get(identifier)
            document = draft["scenario"]
            if any(e["id"] == entity_id for e in document["entities"]):
                raise ValueError(f"placement.id: duplicate {entity_id!r}")
            custom = {t["id"]: t for t in document["registry"].get("types", [])}
            if type_id in custom:
                if custom[type_id]["abstract"]:
                    raise ValueError(
                        "placement.type: abstract types cannot be instantiated"
                    )
            else:
                selection = document["registry"].get("compile")
                selected_catalog = self.catalog if selection is not None else self.catalog_for(identifier)
                detail = selected_catalog.type_detail(type_id)
                if detail["abstract"]:
                    raise ValueError(
                        "placement.type: abstract types cannot be instantiated; choose a concrete type or explicitly author a subtype"
                    )
                if selection is not None and type_id not in selection["types"]:
                    selection["types"].append(type_id)
                declared = {f["id"] for f in document["registry"].get("fields", [])}
                applicable = {field["id"] for field in detail["fields"]}
                for field in facts:
                    if field in declared:
                        continue
                    if field not in applicable:
                        raise ValueError(f"placement.facts.{field}: not declared for {type_id}")
                    if selection is not None and "fields" in selection and field not in selection["fields"]:
                        selection["fields"].append(field)
            if POS in facts and facts[POS] != position:
                raise ValueError(
                    "placement.position: conflicts with supplied position fact"
                )
            facts[POS] = position
            engine_id = "motion" if engine == "kinematic" else "operations"
            if engine == "kinematic":
                if engine_id not in document["engines"]:
                    document["engines"][engine_id] = motion(type_id)
                    document["registry"]["messages"].extend(motion_messages())
                elif document["engines"][engine_id]["config"]["type_id"] != type_id:
                    raise ValueError(
                        "placement.type: this motion partition uses a different type; author a separate partition"
                    )
                # These are authored initialization/model assumptions, shown in the draft editor.
                facts.setdefault(VEL, [0.0, 0.0, 0.0])
                facts.setdefault(ENERGY, 100_000.0)
                if set(facts) - {POS, VEL, ENERGY}:
                    raise ValueError(
                        "kinematic placement: bind additional facts to a separate explicit writer"
                    )
            else:
                if engine_id not in document["engines"]:
                    document["engines"][engine_id] = workflow()
                facts.setdefault(STATE, "placed")
                cfg = document["engines"][engine_id]["config"]
                for field in facts:
                    if field not in cfg["produces"]:
                        cfg["produces"].append(field)
                cfg["machines"].append(
                    {
                        "entity": entity_id,
                        "field": STATE,
                        "initial": facts[STATE],
                        "states": {facts[STATE]: {"transitions": []}},
                    }
                )
            if kind == "airspace":
                if type_id != "aas:StudioAirspace" or engine != "workflow":
                    raise ValueError(
                        "airspace placement: use declared aas:StudioAirspace and workflow"
                    )
                polygon = payload.get("polygon")
                if (
                    not isinstance(polygon, list)
                    or len(polygon) < 3
                    or any(
                        not isinstance(p, list)
                        or len(p) != 2
                        or any(
                            type(v) not in (int, float) or not math.isfinite(v)
                            for v in p
                        )
                        for p in polygon
                    )
                ):
                    raise ValueError(
                        "airspace.polygon: at least three finite ENU points required"
                    )
                if polygon[0] == polygon[-1]:
                    polygon = polygon[:-1]
                if len({tuple(p) for p in polygon}) < 3:
                    raise ValueError(
                        "airspace.polygon: three distinct vertices required"
                    )
                area = sum(
                    a[0] * b[1] - b[0] * a[1]
                    for a, b in zip(polygon, polygon[1:] + polygon[:1])
                )
                if abs(area) < 1e-9:
                    raise ValueError("airspace.polygon: nonzero area required")
                if not _simple_polygon(polygon):
                    raise ValueError(
                        "airspace.polygon: self-intersections are not allowed"
                    )
                floor: Any = payload.get("floor_m")
                ceiling: Any = payload.get("ceiling_m")
                if (
                    any(
                        type(v) not in (int, float) or not math.isfinite(v)
                        for v in (floor, ceiling)
                    )
                    or floor >= ceiling
                ):
                    raise ValueError("airspace: finite floor_m < ceiling_m required")
                facts["aas.studio.airspace"] = {
                    "polygon": [[float(v) for v in p] for p in polygon],
                    "floor_m": float(floor),
                    "ceiling_m": float(ceiling),
                }
                if (
                    "aas.studio.airspace"
                    not in document["engines"][engine_id]["config"]["produces"]
                ):
                    document["engines"][engine_id]["config"]["produces"].append(
                        "aas.studio.airspace"
                    )
            document["entities"].append(
                {"id": entity_id, "type": type_id, "facts": facts}
            )
            document["bindings"]["exact"].extend(
                {"entity": entity_id, "field": field, "writer": engine_id}
                for field in facts
            )
            document["bindings"]["lifecycle"].append(
                {"controller": engine_id, "type": type_id, "ids": entity_id}
            )
            if not any(p["typeId"] == type_id for p in document["presentation"]):
                document["presentation"].append(
                    {
                        "typeId": type_id,
                        "positionField": POS,
                        "frame": "enu",
                        "visual": {
                            "kind": "marker",
                            "color": "#1677ff" if engine == "kinematic" else "#52c41a",
                        },
                    }
                )
            load_scenario(document, base=self._base(document, identifier))
            draft.pop("validation", None)
            return self._write(draft)

    def remove(self, identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
        from .removal import RemovalPlan

        if not isinstance(payload, dict) or "id" not in payload or payload.keys() - {"id", "revision", "dry_run"}:
            raise ValueError("remove: id and optional revision/dry_run required / 删除须提供 id，可选 revision 和 dry_run")
        entity_id = payload["id"]
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError("remove.id: nonempty entity id required / 须提供非空实体标识")
        if "dry_run" in payload and type(payload["dry_run"]) is not bool:
            raise ValueError("remove.dry_run: Boolean required / dry_run 须为布尔值")
        with self.lock:
            draft = self.get(identifier)
            self.check_revision(draft, payload.get("revision"))
            if not any(entity["id"] == entity_id for entity in draft["scenario"]["entities"]):
                raise FileNotFoundError(f"Entity {entity_id!r} is not an explicit draft instance / 实体不是草稿中的显式实例")
            base = self._base(draft["scenario"], identifier)
            document, dependencies = RemovalPlan(draft["scenario"], entity_id, base).build()
            if payload.get("dry_run") is True:
                return {"dependencies": dependencies}
            self._check_scene_source(draft, document)
            # Fact schemas, field writers, engine and behaviour contracts all
            # bind before the sole draft replacement. Failed plans change no files.
            validate_configuration(document, base=self._base(document, identifier), directory=self.directory(identifier))
            draft["scenario"] = document
            draft.pop("validation", None)
            draft.pop("behaviour_validation", None)
            draft.pop("behaviour_layout", None)
            self._resolve_city(draft)
            self._write(draft)
            return self.summary(identifier)
