"""Atomic on-disk drafts exporting the platform's versioned scenario YAML."""

from __future__ import annotations

import copy
import json
import math
import re
import threading
import uuid
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
                    "scenario": starter(identifier, self.ontology_root),
                }
            )

    def list(self) -> list[dict[str, Any]]:
        return [
            self.get(p.name)
            for p in sorted(self.root.glob("studio-*"))
            if (p / "draft.json").is_file()
        ]

    def get(self, identifier: str) -> dict[str, Any]:
        path = self.directory(identifier) / "draft.json"
        if not path.is_file():
            raise FileNotFoundError("workspace: not found")
        draft: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
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

                draft["scenario"] = normalize_wire(draft["scenario"], self.catalog)
            draft.pop("validation", None)
            return self._write(draft)

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
                simulation = Simulation(loaded)
                try:
                    # Bootstrap catches lifecycle/initial authority failures before Run now.
                    simulation.start()
                finally:
                    simulation.close()
                result: dict[str, Any] = {
                    "valid": True,
                    "errors": [],
                    "digest": loaded.digest,
                }
            except Exception as exc:  # noqa: BLE001 - validation returns the real diagnostic
                result = {"valid": False, "errors": [f"{type(exc).__name__}: {exc}"]}
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
