"""Atomic on-disk drafts exporting the platform's versioned scenario YAML."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader

from .catalog import Catalog
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


class WorkspaceStore:
    """Draft edits are reversible; validation compiles real registry and engine contracts."""

    def __init__(self, root: Path, ontology_root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.ontology_root = ontology_root.resolve()
        self.catalog = Catalog(self.ontology_root)
        self.lock = threading.RLock()

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
            return self._write(
                {
                    "id": identifier,
                    "name": name.strip(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "scenario": starter(identifier, self.ontology_root),
                }
            )

    def list(self) -> list[dict[str, Any]]:
        drafts = [
            self.get(p.name)
            for p in sorted(self.root.glob("studio-*"))
            if (p / "draft.json").is_file()
        ]
        return sorted(drafts, key=lambda draft: draft["updated_at"], reverse=True)

    def get(self, identifier: str) -> dict[str, Any]:
        path = self.directory(identifier) / "draft.json"
        if not path.is_file():
            raise FileNotFoundError("workspace: not found")
        draft: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        # Older drafts have no edit metadata; the stored file's modification time
        # is the actual last write, without inventing a creation date.
        draft.setdefault(
            "updated_at",
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        )
        return draft

    def save(self, identifier: str, changes: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(changes, dict) or set(changes) - {"name", "scenario"}:
            raise ValueError("workspace: only name and scenario may be edited")
        with self.lock:
            draft = self.get(identifier)
            if "name" in changes and (
                not isinstance(changes["name"], str) or not changes["name"].strip()
            ):
                raise ValueError("name: nonempty string required")
            if "scenario" in changes and not isinstance(changes["scenario"], dict):
                raise TypeError("scenario: mapping required")
            draft.update(copy.deepcopy(changes))
            if "scenario" in changes:
                from .wire import normalize_wire

                draft["scenario"] = normalize_wire(
                    draft["scenario"],
                    self.catalog,
                    base=self._base(draft["scenario"], identifier),
                )
            draft.pop("validation", None)
            draft.pop("behaviour_validation", None)
            return self._write(draft)

    def import_demo(
        self,
        identifier: str,
        name: str,
        *,
        console: bool = False,
        primitive: bool = False,
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
                draft["scenario"]["ingress_streams"][0]["timeout_s"] = 120.0
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
            if set(package) != {"path", "sha256"} or not isinstance(
                package["path"], str
            ):
                raise ValueError("behaviours: pinned path/sha256 reference required")
            path = (self.directory(identifier) / package["path"]).resolve()
            if not path.is_relative_to(self.directory(identifier)):
                raise ValueError("behaviours.path: must stay inside workspace")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != package["sha256"]:
                raise ValueError("behaviours.sha256: package hash mismatch")
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
        """Draft export is available even for unsupported content; hashes identify real bytes."""
        from aerokernel.values import canonical_json

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
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "semantic_digest": hashlib.sha256(canonical_json(package)).hexdigest(),
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
        from .scene import compile_scene, crop_osm

        if set(region) != {"extract", "bounds", "alt", "level_height_m"}:
            raise ValueError("region: extract/bounds/alt/level_height_m required")
        with self.lock:
            draft = self.get(identifier)
            scene = compile_scene(
                source,
                region["bounds"],
                alt=region["alt"],
                level_height_m=region["level_height_m"],
            )
            crop_osm(
                source, region["bounds"], self.directory(identifier) / "region.osm.xml"
            )
            # A new crop invalidates the previous traffic network.
            (self.directory(identifier) / "network.net.xml").unlink(missing_ok=True)
            draft.pop("network", None)
            draft["region"] = copy.deepcopy(region)
            draft["scene"] = scene
            draft["scenario"]["origin"] = scene["origin"]
            draft.pop("validation", None)
            return self._write(draft)

    def _check_behaviour_refs(self, specs: Any, identifier: str) -> None:
        root = self.directory(identifier)
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
                if hashlib.sha256(data).hexdigest() != spec.get("sha256"):
                    raise ValueError(
                        "behaviours.sha256: referenced package hash mismatch"
                    )
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

    def _base(self, document: dict[str, Any], identifier: str) -> Path:
        """Browser imports cannot widen the configured source-file scope."""
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
            snapshot = (self.directory(identifier) / registry["snapshot"]).resolve()
            if not snapshot.is_relative_to(self.directory(identifier)):
                raise ValueError("registry.snapshot: must be inside this workspace")
        if "behaviours" in document:
            self._check_behaviour_refs(document["behaviours"], identifier)
        for engine in document.get("engines", {}).values():
            if engine.get("plugin") == "behaviour" and "packages" in engine.get(
                "config", {}
            ):
                self._check_behaviour_refs(engine["config"]["packages"], identifier)
        return self.directory(identifier)

    def validate(
        self, identifier: str, scenario: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self.lock:
            draft = (
                self.save(identifier, {"scenario": scenario})
                if scenario is not None
                else self.get(identifier)
            )
            try:
                loaded = load_scenario(
                    draft["scenario"], base=self._base(draft["scenario"], identifier)
                )
                with tempfile.TemporaryDirectory(
                    prefix="validation-", dir=self.directory(identifier)
                ) as scratch:
                    simulation = Simulation(loaded, run_directory=Path(scratch))
                    try:
                        # Bootstrap checks real registry, owners, capabilities and compiler.
                        simulation.start()
                    finally:
                        simulation.close()
                result: dict[str, Any] = {
                    "valid": True,
                    "errors": [],
                    "digest": loaded.digest,
                }
            except Exception as exc:  # noqa: BLE001 - validation returns the real diagnostic
                authored: BaseException = exc
                while authored.__cause__ is not None and not hasattr(authored, "path"):
                    authored = authored.__cause__
                result = {
                    "valid": False,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "issues": [
                        {
                            "source": getattr(authored, "source", "scenario"),
                            "path": getattr(authored, "path", "$"),
                            "message": str(authored),
                        }
                    ],
                }
            draft["validation"] = result
            self._write(draft)
            return result

    def export(self, identifier: str) -> str:
        result = self.validate(identifier)
        if not result["valid"]:
            raise ValueError("export: " + "; ".join(result["errors"]))
        return yaml.safe_dump(
            self.get(identifier)["scenario"], allow_unicode=True, sort_keys=False
        )

    def import_yaml(self, identifier: str, text: str) -> dict[str, Any]:
        try:
            document = yaml.load(text, Loader=UniqueLoader)
        except yaml.YAMLError as exc:
            raise ValueError(f"import: invalid YAML: {exc}") from exc
        if not isinstance(document, dict):
            raise TypeError("import: scenario YAML mapping required")
        # Compile before replacing an existing draft. Failed imports preserve the draft.
        load_scenario(document, base=self._base(document, identifier))
        return self.save(identifier, {"scenario": document})

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
                detail = self.catalog.type_detail(type_id)
                if detail["abstract"]:
                    raise ValueError(
                        "placement.type: abstract types cannot be instantiated; choose a concrete type or explicitly author a subtype"
                    )
                selection = document["registry"]["compile"]
                if type_id not in selection["types"]:
                    selection["types"].append(type_id)
                for field in facts:
                    if (
                        field not in {f["id"] for f in document["registry"]["fields"]}
                        and field not in selection["fields"]
                    ):
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
