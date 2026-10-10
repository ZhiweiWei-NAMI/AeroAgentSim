"""Production authoring of a strict ``aero-bench.world/v2`` ``WorldPackage`` from
a compiled urban scene.

``tools/compile_urban_scene.py`` (``aero_bench.world.scene_compiler``) turns an
offline OSM JSON source into a deterministic, auditable
``aero-bench.urban-scene-compiler/v1`` package: a manifest with per-output
digests, a WGS84 ``osm/effective.osm.json`` render/task scene, an ENU Gazebo
polyline-extrusion SDF, a SUMO conversion record, and an ENU object manifest
(``metadata/objects.json``) carrying every building footprint with its real
base/top ENU heights and every road centreline with its OSM tags.

That compiled package is *not* a ``WorldPackage``. This module is the missing
production authoring stage: it maps a compiled scene into the strict frozen
``aero-bench.world/v2`` contract required by ``CaseSpec.world_package`` and by
``aero_bench.tasks.logistics.bundle_builder``.

Honesty rules (no invented facts, no defaults, no mock parser):

* The WGS84 frame origin is an **explicit** input and must match the compiled
  manifest origin exactly, or authoring fails closed.
* Building geometry comes only from the scene's own ``metadata/objects.json``
  footprints and its declared base/top ENU heights. The scene owns building
  height (OSM tags plus the compiler's declared simulation assumptions); this
  module never invents it.
* A building's ``render_asset_id`` must name a **real per-building renderable
  asset**. The compiled scene emits no per-building renderable artifact -- its
  ``renderer_policy`` declares ``custom_viewer_mesh_emitted`` false and its only
  renderer artifact is the whole-scene OSM XML -- so pointing ``render_asset_id``
  at the scene XML (as an earlier revision did) is not honest. Every building
  must therefore be given an explicit authored ``building_render`` asset, and
  authoring fails closed (enumerating the missing building ``object_id``s)
  otherwise. No visual quality is ever claimed from an OSM XML.
* Road centrelines come only from the scene. A road ``width_m`` is a fact the
  compiled scene does not carry -- the checked-in Shanghai sample declares no
  ``width`` OSM tag on any of its 289 roads and its SUMO record is uninvoked, so
  no lane width is available either. Every source road must therefore resolve a
  defensible width (a real OSM ``width`` tag or an explicit authored catalog
  entry); when any source road lacks one, authoring fails closed enumerating the
  missing road ``object_id``s. No source road is ever silently omitted or
  defaulted, so a strict ``WorldPackage`` is never emitted with an incomplete
  road network.
* Collision geometry is the scene's own native SDF polyline extrusions.
* Facts the compiled scene genuinely cannot supply -- the geoid correction and
  terrain height assets the vertical datum binds, the license artifact, the
  weather sample, and any road width -- are supplied as an **explicit authored
  catalog** with provenance. Missing or inconsistent catalog facts fail closed.
* Every referenced artifact is pinned by the SHA-256 of its real bytes, and the
  staged bundle contains those exact bytes so its identities resolve under
  ``BundleReader`` and re-verify under ``aero_bench.world.alignment``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from aero_bench.config.models import FileRef, Identifier, StrictModel
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.contracts import (
    ArtifactSelector,
    AssetAudience,
    AssetProvenance,
    AssetRecord,
    AssetRole,
    BuildingGeometry,
    BuildingSpec,
    EnuPoint,
    EntitySpec,
    PrecisionMetadata,
    PublicLayer,
    RoadKind,
    RoadSpec,
    SourceFrameKind,
    SpatialBounds,
    UnitDeclaration,
    VerticalDatumBinding,
    WeatherSample,
    WorldFrame,
    WorldPackage,
    WorldPose,
    world_package,
)
from aero_bench.world.frames import WGS84_FRAME_ID, LocalFrameOrigin
from aero_bench.world.scene_compiler import SceneOrigin

AUTHORING_SCHEMA_VERSION = "aero-bench.urban-world-authoring/v1"
"""Schema version of the explicit authored catalog document this stage consumes."""

SCENE_COMPILER_SCHEMA_VERSION = "aero-bench.urban-scene-compiler/v1"
OBJECTS_SCHEMA_VERSION = "aero-bench.urban-scene-objects/v1"

#: Compiled-scene files this stage is allowed to consume. Each is a
#: manifest-declared output; a missing or tampered one fails closed.
_SCENE_LAYER_RELATIVE = "osm/effective.osm.json"
_SCENE_BUILDING_SOURCE_RELATIVE = "metadata/objects.json"
_SCENE_BUILDING_COLLISION_RELATIVE = "gazebo/scene.sdf"

SCENE_ASSET_SELECTOR_PREFIX = "scene/"
LAYER_ASSET_ID = "scene.effective_osm"
BUILDING_SOURCE_ASSET_ID = "scene.objects_manifest"
BUILDING_COLLISION_ASSET_ID = "scene.gazebo_sdf"

#: Artifact-id prefix of an authored per-building render asset. The scene emits
#: no per-building renderable artifact, so every ``render_asset_id`` is an
#: explicit authored asset keyed by the building's own ``object_id`` -- never the
#: whole-scene OSM XML, which is not a per-building render asset.
BUILDING_RENDER_ASSET_ID_PREFIX = "render."

PACKAGE_RELATIVE_PATH = "world/package.json"
PROVENANCE_RELATIVE_PATH = "world/authoring-provenance.json"

_ORIGIN_TOLERANCE_DEG = 1e-9
_ORIGIN_TOLERANCE_M = 1e-6
_RELATIVE_PATH_MAX = 512

#: Explicit, closed mapping of OSM ``highway`` values to documented world road
#: kinds. An unmapped value fails closed rather than being silently coerced.
_VEHICLE_HIGHWAYS = frozenset(
    {
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "living_street",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
        "tertiary_link",
        "bus_guideway",
        "raceway",
    }
)
_PEDESTRIAN_HIGHWAYS = frozenset(
    {
        "footway",
        "pedestrian",
        "steps",
        "path",
        "cycleway",
        "track",
        "bridleway",
        "corridor",
    }
)
_SPECIAL_HIGHWAYS: dict[str, RoadKind] = {
    "service": "service_road",
    "runway": "runway",
    "taxiway": "taxiway",
}

_WORLD_ID_ADAPTER = TypeAdapter(Identifier)


class UrbanWorldAuthoringError(ValueError):
    """An explicit urban-scene -> WorldPackage authoring contract violation."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_selector_path(value: str) -> str:
    """Reject anything the strict artifact-selector grammar would reject.

    Reuses the exact contract grammar by constructing a probe selector so this
    module never re-implements (and can never drift from) it.
    """

    ArtifactSelector(
        artifact_id="selector.probe", selector=value, sha256="0" * 63 + "1"
    )
    return value


SelectorPath = Annotated[
    str,
    Field(max_length=_RELATIVE_PATH_MAX),
    AfterValidator(_validate_selector_path),
]


def _canonical_world_id(value: str) -> str:
    _WORLD_ID_ADAPTER.validate_python(value)
    return value


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise UrbanWorldAuthoringError(f"{label} must be a mapping document")
    document = dict(value)
    if any(not isinstance(key, str) for key in document):
        raise UrbanWorldAuthoringError(f"{label} keys must be strings")
    return document


class CatalogAsset(StrictModel):
    """An explicitly authored artifact the compiled scene cannot supply.

    ``path`` is the bundle-relative selector the produced ``WorldPackage`` pins;
    ``file`` is the real source file whose bytes are digested and staged. The
    digest is always taken from the real bytes -- the catalog never declares it.
    ``units``/``precision`` declare only the role-specific measurements; the
    authoring stage appends the real ``file_size`` declarations itself.
    """

    asset_id: Identifier
    path: SelectorPath
    file: Annotated[str, Field(min_length=1)]
    asset_role: AssetRole
    media_type: Annotated[str, Field(min_length=3, max_length=128)]
    source_frame: SourceFrameKind
    units: tuple[UnitDeclaration, ...] = ()
    precision: tuple[PrecisionMetadata, ...] = ()
    provenance: AssetProvenance


WidthSource: TypeAlias = Literal["osm_width_tag", "sumo_lane_model"]
"""Where a catalog road width came from.

``osm_width_tag``  -- the road carried a real OSM ``width`` tag (preserved, not
                      a measurement this stage invented).
``sumo_lane_model`` -- the width was *derived* from the modelled SUMO lanes and
                      netconvert's lane-width defaults; it is an **estimate**,
                      never a surveyed or tagged measurement.
"""


class RoadWidthLane(StrictModel):
    """One modelled SUMO lane that contributed to an estimated road width.

    ``width_explicit`` records whether the net declared the width itself; when
    it did not, ``sumo_lane_model`` estimates substitute netconvert's documented
    default lane width (``default_lane_width_m``). ``lane_scope`` separates the
    carriageway from netconvert's ``sidewalks.guess`` pedestrian lanes, which are
    not part of the vehicular road width.
    """

    edge_id: Annotated[str, Field(min_length=1)]
    lane_index: Annotated[int, Field(ge=0)]
    lane_width_m: Annotated[float, Field(gt=0)]
    width_explicit: bool
    lane_scope: Literal["carriageway", "sidewalk"]


class RoadWidthProvenance(StrictModel):
    """Explicit, auditable provenance of one catalog road width.

    Every width carries the algorithm that produced it, the configuration
    constants it depended on, the exact SUMO edges/lanes it was read from, and a
    SHA-256 ``evidence_sha256`` over that evidence. A ``sumo_lane_model`` width is
    always labelled ``is_estimate`` true and its ``measurement_status`` says so,
    so an estimate can never be presented as a surveyed measurement.
    """

    source: WidthSource
    is_estimate: bool
    measurement_status: Annotated[str, Field(min_length=1)]
    algorithm: Annotated[str, Field(min_length=1)]
    evidence_sha256: Annotated[str, Field(min_length=64, max_length=64)]
    # -- estimate-only evidence (derived from the SUMO net) ------------------ #
    default_lane_width_m: Annotated[float, Field(gt=0)] | None = None
    sumo_netconvert_version: str | None = None
    sumo_network_sha256: str | None = None
    synthetic_way_id: str | None = None
    representing_edges: tuple[str, ...] = ()
    lane_count: Annotated[int, Field(ge=0)] | None = None
    lanes: tuple[RoadWidthLane, ...] = ()
    # -- osm-tag-only evidence ---------------------------------------------- #
    osm_width_tag: str | None = None
    # -- cross-check against the compiled scene's own OSM tags --------------- #
    osm_lanes_tag: str | None = None
    osm_lanes_consistent: bool | None = None


class CatalogRoadWidth(StrictModel):
    """An explicit authored road width for one scene road ``object_id``.

    The legacy form (``object_id`` + ``width_m`` only) is still accepted: an
    author may assert a width directly. A width produced by the production
    road-width-catalog tool always carries ``source``, ``is_estimate`` and a
    :class:`RoadWidthProvenance`, and a ``sumo_lane_model`` width must be labelled
    an estimate with provenance or validation fails closed.
    """

    object_id: Identifier
    width_m: Annotated[float, Field(gt=0)]
    source: WidthSource | None = None
    is_estimate: bool | None = None
    provenance: RoadWidthProvenance | None = None

    @field_validator("width_m")
    @classmethod
    def finite_width(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("road width_m must be finite")
        return value

    @model_validator(mode="after")
    def label_estimates(self) -> "CatalogRoadWidth":
        if self.source is None:
            # Legacy authored width: neither an OSM tag nor a modelled estimate.
            if self.provenance is not None:
                raise ValueError("a road width provenance requires an explicit source")
            return self
        if self.provenance is None:
            raise ValueError(
                "a road width with an explicit source must carry provenance"
            )
        if self.provenance.source != self.source:
            raise ValueError("road width source and provenance.source disagree")
        if self.source == "sumo_lane_model":
            if self.is_estimate is not True or self.provenance.is_estimate is not True:
                raise ValueError(
                    "a sumo_lane_model road width is an estimate and must be "
                    "labelled is_estimate true; it is never a surveyed measurement"
                )
        else:  # osm_width_tag
            if (
                self.is_estimate is not False
                or self.provenance.is_estimate is not False
            ):
                raise ValueError(
                    "an osm_width_tag road width is a preserved OSM tag and must "
                    "be labelled is_estimate false"
                )
        return self


class CatalogBuildingRender(StrictModel):
    """An explicit authored per-building render asset.

    The compiled scene emits no per-building renderable artifact, so an honest
    ``render_asset_id`` cannot be the whole-scene OSM XML. Each building must be
    given one real renderable asset here (role ``building_render``).
    ``path``/``file`` mirror :class:`CatalogAsset`: the selector pinned in the
    package and the real source file whose bytes are digested and staged.

    ``source_frame`` declares the frame of the asset's own content. A glTF/GLB
    mesh is expressed in the glTF 2.0 asset-local Y-up metre frame and **must**
    declare ``asset_local`` -- labelling it ``WGS84`` is rejected here rather
    than passed through, because geodetic coordinates cannot be represented in
    core glTF. ``WGS84`` stays available for render assets whose own content is
    geodetic (legacy coordinate JSON), preserving pre-existing catalog
    documents that omit the field.
    """

    object_id: Identifier
    path: SelectorPath
    file: Annotated[str, Field(min_length=1)]
    media_type: Annotated[str, Field(min_length=3, max_length=128)]
    source_frame: SourceFrameKind = "WGS84"
    provenance: AssetProvenance

    @model_validator(mode="after")
    def gltf_render_is_asset_local(self) -> "CatalogBuildingRender":
        if (
            self.media_type in ("model/gltf-binary", "model/gltf+json")
            and self.source_frame == "WGS84"
        ):
            raise ValueError(
                "a glTF building render is expressed in the glTF 2.0 "
                "asset-local metre frame and cannot be labelled WGS84; "
                "declare source_frame asset_local"
            )
        return self


class UrbanWorldCatalog(StrictModel):
    """The explicit authored inputs the compiled urban scene cannot provide."""

    schema_version: Literal["aero-bench.urban-world-authoring/v1"]
    license_id: Annotated[str, Field(min_length=1, max_length=128)]
    license_path: SelectorPath
    license_file: Annotated[str, Field(min_length=1)]
    assets: tuple[CatalogAsset, ...] = Field(min_length=1)
    weather: tuple[WeatherSample, ...] = Field(min_length=1)
    road_width_m: tuple[CatalogRoadWidth, ...] = ()
    building_render: tuple[CatalogBuildingRender, ...] = ()


@dataclass(frozen=True, slots=True)
class UrbanWorldAuthoringRequest:
    """Explicit, deterministic inputs of one urban-scene -> WorldPackage authoring.

    ``scene_root`` is a compiled ``aero-bench.urban-scene-compiler/v1`` package;
    ``world_id`` is the explicit scene identity; ``origin`` is the explicit
    WGS84 frame origin, validated against the compiled manifest; ``catalog_path``
    is the explicit authored catalog supplying the facts the scene cannot.
    """

    scene_root: Path
    output_root: Path
    world_id: str
    origin: SceneOrigin
    catalog_path: Path


@dataclass(frozen=True, slots=True)
class UrbanWorldAuthoringResult:
    """Identity of one produced WorldPackage bundle."""

    bundle_root: Path
    package_path: str
    package_ref: FileRef
    world_package: WorldPackage
    world_digest: str
    asset_digest: str
    scene_source_sha256: str
    scene_manifest_sha256: str
    catalog_sha256: str
    building_count: int
    road_count: int
    entity_count: int


def _read_scene_file(scene_root: Path, relative: str) -> bytes:
    path = scene_root / relative
    if not path.is_file():
        raise UrbanWorldAuthoringError(
            f"compiled urban scene is missing required file {relative!r}: {path}"
        )
    try:
        return path.read_bytes()
    except OSError as exc:  # pragma: no cover - the is_file() check precedes this
        raise UrbanWorldAuthoringError(
            f"cannot read compiled urban scene file {relative!r}: {exc}"
        ) from exc


def _verify_scene_manifest(
    scene_root: Path,
) -> tuple[dict[str, object], str, dict[str, str], dict[str, int]]:
    """Verify every declared scene output against its real bytes.

    Returns the parsed manifest, its own SHA-256, and the verified digest and
    byte-size of each manifest-declared output. Any missing, invalid or
    digest/size-mismatched output fails closed.
    """

    manifest_path = scene_root / "manifest.json"
    if not manifest_path.is_file():
        raise UrbanWorldAuthoringError(
            f"compiled urban scene has no manifest.json: {scene_root}"
        )
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UrbanWorldAuthoringError(
            f"compiled urban scene manifest is not valid JSON: {exc}"
        ) from exc
    manifest = _require_mapping(manifest, "compiled urban scene manifest")
    if manifest.get("schema_version") != SCENE_COMPILER_SCHEMA_VERSION:
        raise UrbanWorldAuthoringError(
            "compiled urban scene manifest schema_version must be "
            f"{SCENE_COMPILER_SCHEMA_VERSION!r}, found {manifest.get('schema_version')!r}"
        )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise UrbanWorldAuthoringError(
            "compiled urban scene manifest must declare a non-empty outputs list"
        )
    digests: dict[str, str] = {}
    byte_sizes: dict[str, int] = {}
    for index, record in enumerate(outputs):
        entry = _require_mapping(
            record, f"compiled urban scene manifest.outputs[{index}]"
        )
        relative = entry.get("path")
        digest = entry.get("sha256")
        byte_size = entry.get("byte_size")
        if not isinstance(relative, str):
            raise UrbanWorldAuthoringError(
                f"manifest.outputs[{index}].path must be a string"
            )
        if not isinstance(digest, str) or len(digest) != 64:
            raise UrbanWorldAuthoringError(
                f"manifest.outputs[{index}].sha256 must be a SHA-256 hex digest"
            )
        if isinstance(byte_size, bool) or not isinstance(byte_size, int):
            raise UrbanWorldAuthoringError(
                f"manifest.outputs[{index}].byte_size must be an integer"
            )
        actual = _read_scene_file(scene_root, relative)
        if _sha256_bytes(actual) != digest:
            raise UrbanWorldAuthoringError(
                f"compiled urban scene output {relative!r} does not match its manifest digest"
            )
        if len(actual) != byte_size:
            raise UrbanWorldAuthoringError(
                f"compiled urban scene output {relative!r} does not match its manifest byte_size"
            )
        if relative in digests:
            raise UrbanWorldAuthoringError(
                f"compiled urban scene manifest declares duplicate output {relative!r}"
            )
        digests[relative] = digest
        byte_sizes[relative] = byte_size
    return manifest, _sha256_bytes(manifest_bytes), digests, byte_sizes


def _verify_origin(manifest: dict[str, object], origin: SceneOrigin) -> None:
    document = _require_mapping(
        manifest.get("origin"), "compiled urban scene manifest.origin"
    )
    _compare_origin(document, origin, "compiled urban scene manifest.origin")


def _compare_origin(
    document: Mapping[str, object], origin: SceneOrigin, label: str
) -> None:
    checks = (
        ("latitude_deg", float(origin.latitude_deg), _ORIGIN_TOLERANCE_DEG),
        ("longitude_deg", float(origin.longitude_deg), _ORIGIN_TOLERANCE_DEG),
        ("ellipsoid_height_m", float(origin.ellipsoid_height_m), _ORIGIN_TOLERANCE_M),
        ("geoid_undulation_m", float(origin.geoid_undulation_m), _ORIGIN_TOLERANCE_M),
        ("amsl_m", float(origin.amsl_m), _ORIGIN_TOLERANCE_M),
    )
    for name, expected, tolerance in checks:
        declared = document.get(name)
        if isinstance(declared, bool) or not isinstance(declared, (int, float)):
            raise UrbanWorldAuthoringError(f"{label}.{name} must be a number")
        if not math.isclose(expected, float(declared), rel_tol=0.0, abs_tol=tolerance):
            raise UrbanWorldAuthoringError(
                "explicit scene origin does not match the compiled scene: "
                f"{label}.{name}={declared} vs {expected}"
            )


def _scene_asset(
    *,
    asset_id: str,
    relative: str,
    digests: Mapping[str, str],
    byte_sizes: Mapping[str, int],
    asset_role: AssetRole,
    media_type: str,
    source_frame: SourceFrameKind,
    license_id: str,
    license_file: ArtifactSelector,
) -> AssetRecord:
    if relative not in digests:
        raise UrbanWorldAuthoringError(
            f"compiled urban scene does not declare a manifest output {relative!r}"
        )
    byte_size = byte_sizes[relative]
    return AssetRecord(
        artifact=ArtifactSelector(
            artifact_id=asset_id,
            selector=f"{SCENE_ASSET_SELECTOR_PREFIX}{relative}",
            sha256=digests[relative],
        ),
        asset_role=asset_role,
        byte_size=byte_size,
        media_type=media_type,
        visibility="public",
        audiences=(AssetAudience(audience_kind="public", audience_id="all"),),
        units=(UnitDeclaration(quantity="file_size", unit="B"),),
        source_frame=source_frame,
        precision=(
            PrecisionMetadata(
                quantity="file_size",
                kind="exact_bytes",
                unit="B",
                exact_bytes=byte_size,
                absolute_tolerance=None,
            ),
        ),
        provenance=AssetProvenance(
            source_kind="bundled_offline",
            recorded_by="urban-scene-authoring",
            source_dataset="aero-bench.urban-scene-compiler",
            source_version=SCENE_COMPILER_SCHEMA_VERSION,
            runtime_download=False,
        ),
        license_id=license_id,
        license_file=license_file,
    )


def _road_kind(highway: str) -> RoadKind:
    value = highway.strip().lower()
    if value in _SPECIAL_HIGHWAYS:
        return _SPECIAL_HIGHWAYS[value]
    if value in _VEHICLE_HIGHWAYS:
        return "vehicle_lane"
    if value in _PEDESTRIAN_HIGHWAYS:
        return "pedestrian_path"
    raise UrbanWorldAuthoringError(
        f"compiled scene road declares unsupported highway {highway!r}; "
        "no road kind is invented for it"
    )


def _parse_length_m(value: object, label: str) -> float:
    if not isinstance(value, str):
        raise UrbanWorldAuthoringError(f"{label} must be a string")
    text = value.strip()
    if text.endswith("m"):
        text = text[:-1].strip()
    try:
        parsed = float(text)
    except ValueError as exc:
        raise UrbanWorldAuthoringError(
            f"{label} must be a numeric metre value, found {value!r}"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise UrbanWorldAuthoringError(f"{label} must be a positive metre value")
    return parsed


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UrbanWorldAuthoringError(f"{label} must be a finite number")
    as_float = float(value)
    if not math.isfinite(as_float):
        raise UrbanWorldAuthoringError(f"{label} must be a finite number")
    return as_float


def _open_footprint(raw: object, label: str) -> tuple[EnuPoint, ...]:
    """Convert a closed scene footprint ring into strict distinct ENU vertices."""

    if not isinstance(raw, list) or len(raw) < 3:
        raise UrbanWorldAuthoringError(f"{label} must be a footprint array")
    points: list[tuple[float, float]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, list) or len(item) != 2:
            raise UrbanWorldAuthoringError(
                f"{label}[{index}] must be an [east, north] pair"
            )
        points.append(
            (
                _number(item[0], f"{label}[{index}][0]"),
                _number(item[1], f"{label}[{index}][1]"),
            )
        )
    if points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3 or len(set(points)) != len(points):
        raise UrbanWorldAuthoringError(
            f"{label} must contain at least three distinct footprint vertices"
        )
    area = 0.0
    for index, (east, north) in enumerate(points):
        nxt_east, nxt_north = points[(index + 1) % len(points)]
        area += east * nxt_north - nxt_east * north
    if abs(area) * 0.5 <= 0.0:
        raise UrbanWorldAuthoringError(f"{label} must enclose a positive area")
    return tuple(EnuPoint(east_m=east, north_m=north) for east, north in points)


def _load_objects(scene_root: Path, origin: SceneOrigin) -> dict[str, object]:
    objects_bytes = _read_scene_file(scene_root, _SCENE_BUILDING_SOURCE_RELATIVE)
    try:
        document = json.loads(objects_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UrbanWorldAuthoringError(
            "compiled scene metadata/objects.json is not valid JSON"
        ) from exc
    document = _require_mapping(document, "compiled scene metadata/objects.json")
    if document.get("schema_version") != OBJECTS_SCHEMA_VERSION:
        raise UrbanWorldAuthoringError(
            "compiled scene metadata/objects.json schema_version must be "
            f"{OBJECTS_SCHEMA_VERSION!r}"
        )
    if document.get("coordinate_frame") != "ENU":
        raise UrbanWorldAuthoringError(
            "compiled scene metadata/objects.json coordinate_frame must be 'ENU'"
        )
    scene_origin = _require_mapping(
        document.get("origin"), "compiled scene metadata/objects.json.origin"
    )
    for name, expected, tolerance in (
        ("latitude_deg", float(origin.latitude_deg), _ORIGIN_TOLERANCE_DEG),
        ("longitude_deg", float(origin.longitude_deg), _ORIGIN_TOLERANCE_DEG),
        ("ellipsoid_height_m", float(origin.ellipsoid_height_m), _ORIGIN_TOLERANCE_M),
    ):
        declared = scene_origin.get(name)
        if isinstance(declared, bool) or not isinstance(declared, (int, float)):
            raise UrbanWorldAuthoringError(
                f"compiled scene objects origin.{name} must be a number"
            )
        if not math.isclose(expected, float(declared), rel_tol=0.0, abs_tol=tolerance):
            raise UrbanWorldAuthoringError(
                "explicit scene origin does not match metadata/objects.json: "
                f"{name}={expected} vs {declared}"
            )
    return document


def _build_catalog_asset(
    *,
    asset: CatalogAsset,
    catalog_dir: Path,
    license_id: str,
    license_file: ArtifactSelector,
) -> tuple[AssetRecord, bytes]:
    source = Path(asset.file)
    if not source.is_absolute():
        source = catalog_dir / source
    if not source.is_file():
        raise UrbanWorldAuthoringError(
            f"catalog asset {asset.asset_id!r} file does not exist: {source}"
        )
    data = source.read_bytes()
    record = AssetRecord(
        artifact=ArtifactSelector(
            artifact_id=asset.asset_id,
            selector=asset.path,
            sha256=_sha256_bytes(data),
        ),
        asset_role=asset.asset_role,
        byte_size=len(data),
        media_type=asset.media_type,
        visibility="public",
        audiences=(AssetAudience(audience_kind="public", audience_id="all"),),
        units=(UnitDeclaration(quantity="file_size", unit="B"), *asset.units),
        source_frame=asset.source_frame,
        precision=(
            PrecisionMetadata(
                quantity="file_size",
                kind="exact_bytes",
                unit="B",
                exact_bytes=len(data),
                absolute_tolerance=None,
            ),
            *asset.precision,
        ),
        provenance=asset.provenance,
        license_id=license_id,
        license_file=license_file,
    )
    return record, data


def _build_building_render(
    *,
    entry: CatalogBuildingRender,
    catalog_dir: Path,
    license_id: str,
    license_file: ArtifactSelector,
) -> tuple[AssetRecord, bytes]:
    """Turn one authored per-building render entry into a byte-pinned record.

    The artifact id is derived from the building's own ``object_id`` so the
    mapping building <-> render asset is exact and one-to-one.
    """

    source = Path(entry.file)
    if not source.is_absolute():
        source = catalog_dir / source
    if not source.is_file():
        raise UrbanWorldAuthoringError(
            f"building_render asset for {entry.object_id!r} does not exist: {source}"
        )
    data = source.read_bytes()
    record = AssetRecord(
        artifact=ArtifactSelector(
            artifact_id=f"{BUILDING_RENDER_ASSET_ID_PREFIX}{entry.object_id}",
            selector=entry.path,
            sha256=_sha256_bytes(data),
        ),
        asset_role="building_render",
        byte_size=len(data),
        media_type=entry.media_type,
        visibility="public",
        audiences=(AssetAudience(audience_kind="public", audience_id="all"),),
        units=(UnitDeclaration(quantity="file_size", unit="B"),),
        source_frame=entry.source_frame,
        precision=(
            PrecisionMetadata(
                quantity="file_size",
                kind="exact_bytes",
                unit="B",
                exact_bytes=len(data),
                absolute_tolerance=None,
            ),
        ),
        provenance=entry.provenance,
        license_id=license_id,
        license_file=license_file,
    )
    return record, data


def _catalog_precision(
    assets: Sequence[AssetRecord], asset_id: str, label: str
) -> float:
    """Read the authored absolute elevation tolerance for a vertical datum asset."""

    record = next(asset for asset in assets if asset.artifact.artifact_id == asset_id)
    for precision in record.precision:
        if precision.kind == "absolute_tolerance" and precision.absolute_tolerance:
            return float(precision.absolute_tolerance)
    raise UrbanWorldAuthoringError(
        f"catalog {label} asset {asset_id!r} must declare an absolute_tolerance "
        "precision for the vertical datum binding"
    )


def _staging_directory(output_root: Path) -> Path:
    parent = output_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent / f".{output_root.name}.staging-{uuid.uuid4().hex}"


def _reject_overlapping_roots(*, scene_root: Path, output_root: Path) -> None:
    scene_resolved = scene_root.resolve()
    output_resolved = output_root.resolve()
    if (
        output_resolved == scene_resolved
        or output_resolved.is_relative_to(scene_resolved)
        or scene_resolved.is_relative_to(output_resolved)
    ):
        raise UrbanWorldAuthoringError(
            "output root overlaps the compiled scene root: "
            f"scene={scene_resolved}, output={output_resolved}"
        )


def _commit(staged_root: Path, output_root: Path) -> None:
    if output_root.is_symlink():
        raise UrbanWorldAuthoringError(
            f"output root must not be a symbolic link: {output_root}"
        )
    if output_root.exists():
        if not output_root.is_dir():
            raise UrbanWorldAuthoringError(
                f"output root exists and is not a directory: {output_root}"
            )
        if any(output_root.iterdir()):
            raise UrbanWorldAuthoringError(
                f"output root must not already contain files: {output_root}"
            )
        output_root.rmdir()
    os.replace(staged_root, output_root)


def _load_catalog(catalog_path: Path) -> tuple[UrbanWorldCatalog, bytes]:
    catalog_bytes = catalog_path.read_bytes()
    try:
        document = json.loads(catalog_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UrbanWorldAuthoringError(
            f"authored world catalog is not valid JSON: {exc}"
        ) from exc
    try:
        catalog = UrbanWorldCatalog.model_validate(document)
    except ValueError as exc:
        raise UrbanWorldAuthoringError(
            f"authored world catalog is invalid: {exc}"
        ) from exc
    return catalog, catalog_bytes


def author_urban_world_package(
    request: UrbanWorldAuthoringRequest,
) -> UrbanWorldAuthoringResult:
    """Author a strict ``WorldPackage`` bundle from a compiled urban scene.

    Raises :class:`UrbanWorldAuthoringError` for every missing, tampered,
    inconsistent or unsupported input. Output is staged in a private sibling
    directory and committed with a single atomic rename, so a failure never
    leaves a partial bundle at ``output_root``.
    """

    if not isinstance(request, UrbanWorldAuthoringRequest):
        raise UrbanWorldAuthoringError(
            "author_urban_world_package requires an UrbanWorldAuthoringRequest"
        )
    scene_root = Path(request.scene_root)
    output_root = Path(request.output_root)
    catalog_path = Path(request.catalog_path)
    if not isinstance(request.origin, SceneOrigin):
        raise UrbanWorldAuthoringError("origin must be a SceneOrigin")
    try:
        _canonical_world_id(request.world_id)
    except ValueError as exc:
        raise UrbanWorldAuthoringError(
            f"world_id is not a valid identifier: {exc}"
        ) from exc

    if not scene_root.is_dir():
        raise UrbanWorldAuthoringError(
            f"compiled urban scene root does not exist: {scene_root}"
        )
    _reject_overlapping_roots(scene_root=scene_root, output_root=output_root)
    if output_root.is_symlink():
        raise UrbanWorldAuthoringError(
            f"output root must not be a symbolic link: {output_root}"
        )
    if output_root.exists() and (
        not output_root.is_dir() or any(output_root.iterdir())
    ):
        raise UrbanWorldAuthoringError(
            f"output root must be absent or empty: {output_root}"
        )
    if not catalog_path.is_file():
        raise UrbanWorldAuthoringError(
            f"authored world catalog does not exist: {catalog_path}"
        )

    # ---- compiled-scene manifest, origin and object manifest (fail closed) - #
    manifest, manifest_sha256, digests, byte_sizes = _verify_scene_manifest(scene_root)
    _verify_origin(manifest, request.origin)
    objects = _load_objects(scene_root, request.origin)
    scene_source = _require_mapping(manifest.get("source"), "manifest.source")
    scene_source_sha256 = scene_source.get("sha256")
    if not isinstance(scene_source_sha256, str) or len(scene_source_sha256) != 64:
        raise UrbanWorldAuthoringError(
            "manifest.source.sha256 must be a SHA-256 digest"
        )

    # ---- explicit authored catalog ---------------------------------------- #
    catalog, catalog_bytes = _load_catalog(catalog_path)
    catalog_dir = catalog_path.parent

    license_source = Path(catalog.license_file)
    if not license_source.is_absolute():
        license_source = catalog_dir / license_source
    if not license_source.is_file():
        raise UrbanWorldAuthoringError(
            f"catalog license file does not exist: {license_source}"
        )
    license_bytes = license_source.read_bytes()
    license_file = ArtifactSelector(
        artifact_id=f"license.{catalog.license_id.lower()}",
        selector=catalog.license_path,
        sha256=_sha256_bytes(license_bytes),
    )

    # ---- scene geometry assets, pinned to their real manifest bytes -------- #
    scene_assets = (
        _scene_asset(
            asset_id=LAYER_ASSET_ID,
            relative=_SCENE_LAYER_RELATIVE,
            digests=digests,
            byte_sizes=byte_sizes,
            asset_role="layer_tiles",
            media_type="application/json",
            source_frame="WGS84",
            license_id=catalog.license_id,
            license_file=license_file,
        ),
        _scene_asset(
            asset_id=BUILDING_SOURCE_ASSET_ID,
            relative=_SCENE_BUILDING_SOURCE_RELATIVE,
            digests=digests,
            byte_sizes=byte_sizes,
            asset_role="building_source",
            media_type="application/json",
            source_frame="asset_local",
            license_id=catalog.license_id,
            license_file=license_file,
        ),
        _scene_asset(
            asset_id=BUILDING_COLLISION_ASSET_ID,
            relative=_SCENE_BUILDING_COLLISION_RELATIVE,
            digests=digests,
            byte_sizes=byte_sizes,
            asset_role="building_collision",
            media_type="application/xml",
            source_frame="asset_local",
            license_id=catalog.license_id,
            license_file=license_file,
        ),
    )

    # ---- catalog assets -> real byte-pinned records ------------------------ #
    catalog_assets: list[AssetRecord] = []
    catalog_stage: list[tuple[str, bytes]] = []
    for asset in catalog.assets:
        record, data = _build_catalog_asset(
            asset=asset,
            catalog_dir=catalog_dir,
            license_id=catalog.license_id,
            license_file=license_file,
        )
        catalog_assets.append(record)
        catalog_stage.append((asset.path, data))
    geoid_ids = [
        asset.artifact.artifact_id
        for asset in catalog_assets
        if asset.asset_role == "geoid_model"
    ]
    terrain_ids = [
        asset.artifact.artifact_id
        for asset in catalog_assets
        if asset.asset_role == "terrain_model"
    ]
    if len(geoid_ids) != 1 or len(terrain_ids) != 1:
        raise UrbanWorldAuthoringError(
            "authored catalog must declare exactly one geoid_model and one "
            f"terrain_model asset, found {len(geoid_ids)} and {len(terrain_ids)}"
        )

    # ---- roads: complete coverage required, widths never invented ---------- #
    authored_widths = {
        item.object_id: float(item.width_m) for item in catalog.road_width_m
    }
    if len(authored_widths) != len(catalog.road_width_m):
        raise UrbanWorldAuthoringError(
            "authored catalog road_width_m object_ids must be unique"
        )
    raw_roads = objects.get("roads")
    if not isinstance(raw_roads, list):
        raise UrbanWorldAuthoringError("metadata/objects.json.roads must be an array")
    if not raw_roads:
        raise UrbanWorldAuthoringError(
            "compiled urban scene declares no roads; a strict WorldPackage with a "
            "complete road network cannot be authored from an empty road set"
        )
    roads: list[RoadSpec] = []
    road_centerlines: list[tuple[EnuPoint, ...]] = []
    known_road_ids: set[str] = set()
    missing_width_ids: list[str] = []
    for index, raw in enumerate(raw_roads):
        label = f"metadata/objects.json.roads[{index}]"
        road = _require_mapping(raw, label)
        object_id = road.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise UrbanWorldAuthoringError(
                f"{label}.object_id must be a non-empty string"
            )
        known_road_ids.add(object_id)
        if road.get("kind") != "road":
            raise UrbanWorldAuthoringError(f"{label}.kind must be 'road'")
        width: float | None = None
        width_tag = road.get("width_osm_tag")
        if width_tag is not None:
            width = _parse_length_m(width_tag, f"{label}.width_osm_tag")
        elif object_id in authored_widths:
            width = authored_widths[object_id]
        if width is None:
            # Neither a real OSM width tag nor an explicit authored width. This
            # source road must never be dropped silently, so record it and fail
            # closed below with the full set.
            missing_width_ids.append(object_id)
            continue
        raw_centerline = road.get("centerline_enu_m")
        if not isinstance(raw_centerline, list) or len(raw_centerline) < 2:
            raise UrbanWorldAuthoringError(
                f"{label}.centerline_enu_m must have >= 2 points"
            )
        centerline: list[EnuPoint] = []
        for point_index, item in enumerate(raw_centerline):
            if not isinstance(item, list) or len(item) != 2:
                raise UrbanWorldAuthoringError(
                    f"{label}.centerline_enu_m[{point_index}] must be an [east, north] pair"
                )
            centerline.append(
                EnuPoint(
                    east_m=_number(
                        item[0], f"{label}.centerline_enu_m[{point_index}][0]"
                    ),
                    north_m=_number(
                        item[1], f"{label}.centerline_enu_m[{point_index}][1]"
                    ),
                )
            )
        deduped: list[EnuPoint] = []
        for point in centerline:
            if not deduped or (point.east_m, point.north_m) != (
                deduped[-1].east_m,
                deduped[-1].north_m,
            ):
                deduped.append(point)
        if len({(p.east_m, p.north_m) for p in deduped}) < 2:
            raise UrbanWorldAuthoringError(
                f"{label}.centerline_enu_m collapses to fewer than two distinct "
                "points; the source road cannot be authored and would otherwise "
                "be silently omitted"
            )
        highway = road.get("highway")
        if not isinstance(highway, str) or not highway:
            raise UrbanWorldAuthoringError(
                f"{label}.highway must be a non-empty string"
            )
        roads.append(
            RoadSpec(
                road_id=object_id,
                kind=_road_kind(highway),
                centerline_enu_m=tuple(deduped),
                width_m=width,
            )
        )
        road_centerlines.append(tuple(deduped))
    unknown = sorted(set(authored_widths) - known_road_ids)
    if unknown:
        raise UrbanWorldAuthoringError(
            f"authored catalog road_width_m references unknown road object_ids: {unknown}"
        )
    if missing_width_ids:
        missing = sorted(missing_width_ids)
        raise UrbanWorldAuthoringError(
            f"{len(missing)} of {len(raw_roads)} compiled-scene roads have no "
            "defensible width: neither a real OSM 'width' tag nor an explicit "
            "authored catalog road_width_m entry. A strict WorldPackage is not "
            f"emitted with an incomplete road network. Missing road object_ids: {missing}"
        )

    # ---- buildings: one authored per-building render asset each ------------ #
    raw_buildings = objects.get("buildings")
    if not isinstance(raw_buildings, list):
        raise UrbanWorldAuthoringError(
            "metadata/objects.json.buildings must be an array"
        )
    if not raw_buildings:
        raise UrbanWorldAuthoringError(
            "compiled urban scene declares no buildings; a strict WorldPackage "
            "requires at least one entity and this stage authors none without a "
            "real authored scene fact"
        )
    render_by_object_id: dict[str, CatalogBuildingRender] = {}
    for entry in catalog.building_render:
        if entry.object_id in render_by_object_id:
            raise UrbanWorldAuthoringError(
                "authored catalog building_render object_ids must be unique"
            )
        render_by_object_id[entry.object_id] = entry
    raw_building_ids: list[str] = []
    for index, raw in enumerate(raw_buildings):
        label = f"metadata/objects.json.buildings[{index}]"
        building = _require_mapping(raw, label)
        object_id = building.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise UrbanWorldAuthoringError(
                f"{label}.object_id must be a non-empty string"
            )
        if building.get("kind") != "building":
            raise UrbanWorldAuthoringError(f"{label}.kind must be 'building'")
        raw_building_ids.append(object_id)
    unknown_render = sorted(set(render_by_object_id) - set(raw_building_ids))
    if unknown_render:
        raise UrbanWorldAuthoringError(
            "authored catalog building_render references unknown building "
            f"object_ids: {unknown_render}"
        )
    missing_render_ids = sorted(set(raw_building_ids) - set(render_by_object_id))
    if missing_render_ids:
        raise UrbanWorldAuthoringError(
            f"{len(missing_render_ids)} of {len(raw_building_ids)} compiled-scene "
            "buildings have no explicit authored per-building render asset. The "
            "compiled scene emits no per-building renderable artifact and a "
            "whole-scene OSM XML is not a per-building render asset. Missing "
            f"building object_ids: {missing_render_ids}"
        )

    buildings: list[BuildingSpec] = []
    entities: list[EntitySpec] = []
    footprints: list[tuple[EnuPoint, ...]] = []
    building_render_assets: list[AssetRecord] = []
    building_render_stage: list[tuple[str, bytes]] = []
    min_up = 0.0
    max_up: float | None = None
    for index, raw in enumerate(raw_buildings):
        label = f"metadata/objects.json.buildings[{index}]"
        building = _require_mapping(raw, label)
        object_id = building["object_id"]
        render_entry = render_by_object_id[object_id]
        render_record, render_data = _build_building_render(
            entry=render_entry,
            catalog_dir=catalog_dir,
            license_id=catalog.license_id,
            license_file=license_file,
        )
        render_asset_id = render_record.artifact.artifact_id
        footprint = _open_footprint(
            building.get("footprint_enu_m"), f"{label}.footprint_enu_m"
        )
        base = _number(building.get("base_enu_up_m"), f"{label}.base_enu_up_m")
        top = _number(building.get("top_enu_up_m"), f"{label}.top_enu_up_m")
        if top <= base:
            raise UrbanWorldAuthoringError(
                f"{label} top_enu_up_m must exceed base_enu_up_m"
            )
        entity_id = f"static.{object_id}"
        buildings.append(
            BuildingSpec(
                building_id=object_id,
                entity_id=entity_id,
                source_asset_id=BUILDING_SOURCE_ASSET_ID,
                render_asset_id=render_asset_id,
                collision_asset_id=BUILDING_COLLISION_ASSET_ID,
                geometry=BuildingGeometry(
                    footprint_enu_m=footprint,
                    base_altitude_m=base,
                    top_altitude_m=top,
                    height_reference="enu_up",
                ),
            )
        )
        centroid_east = sum(point.east_m for point in footprint) / len(footprint)
        centroid_north = sum(point.north_m for point in footprint) / len(footprint)
        entities.append(
            EntitySpec(
                entity_id=entity_id,
                kind="static_asset",
                provider_id=None,
                authority_kind="scenario_static",
                state="static",
                model_asset_id=render_asset_id,
                pose=WorldPose(
                    frame_id="ENU",
                    east_m=centroid_east,
                    north_m=centroid_north,
                    up_m=base,
                    qw=1.0,
                    qx=0.0,
                    qy=0.0,
                    qz=0.0,
                ),
            )
        )
        building_render_assets.append(render_record)
        building_render_stage.append((render_entry.path, render_data))
        footprints.append(footprint)
        min_up = min(min_up, base)
        max_up = top if max_up is None else max(max_up, top)

    # ---- spatial extent derived from the real declared geometry ------------- #
    east_values = [p.east_m for footprint in footprints for p in footprint]
    north_values = [p.north_m for footprint in footprints for p in footprint]
    east_values.extend(p.east_m for centerline in road_centerlines for p in centerline)
    north_values.extend(
        p.north_m for centerline in road_centerlines for p in centerline
    )
    east_min, east_max = min(east_values), max(east_values)
    north_min, north_max = min(north_values), max(north_values)
    up_min = min_up
    up_max = max_up if max_up is not None else min_up
    if east_min >= east_max:
        east_max = math.nextafter(east_min, math.inf)
    if north_min >= north_max:
        north_max = math.nextafter(north_min, math.inf)
    if up_min >= up_max:
        up_max = math.nextafter(up_min, math.inf)

    frame = WorldFrame(
        geodetic_frame_id="WGS84",
        ecef_frame_id="ECEF",
        enu_frame_id="ENU",
        ned_frame_id="NED",
        transform_chain="WGS84->ECEF->ENU->NED",
        datum="WGS84",
        semi_major_axis_m=6_378_137.0,
        inverse_flattening=298.257_223_563,
        ellipsoid_height_reference="wgs84_ellipsoid",
        amsl_height_reference="orthometric_msl",
        agl_height_reference="terrain_relative",
        origin=LocalFrameOrigin(
            frame_id=WGS84_FRAME_ID,
            longitude_deg=float(request.origin.longitude_deg),
            latitude_deg=float(request.origin.latitude_deg),
            altitude_m=float(request.origin.ellipsoid_height_m),
        ),
        origin_height_reference="wgs84_ellipsoid",
        spatial_extent=SpatialBounds(
            min_east_m=east_min,
            max_east_m=east_max,
            min_north_m=north_min,
            max_north_m=north_max,
            min_up_m=up_min,
            max_up_m=up_max,
            vertical_reference="enu_up",
        ),
        vertical_datum=VerticalDatumBinding(
            geoid_correction_asset_id=geoid_ids[0],
            terrain_height_asset_id=terrain_ids[0],
            geoid_interpolation="bilinear",
            terrain_interpolation="bilinear",
            geoid_precision_m=_catalog_precision(catalog_assets, geoid_ids[0], "geoid"),
            terrain_precision_m=_catalog_precision(
                catalog_assets, terrain_ids[0], "terrain"
            ),
        ),
    )

    layers = (
        PublicLayer(
            layer_id="layer.osm-scene",
            kind="osm_scene",
            asset_id=LAYER_ASSET_ID,
            visibility="public",
            default_visible=True,
        ),
    )

    try:
        package = world_package(
            world_id=request.world_id,
            frame=frame,
            assets=(*scene_assets, *catalog_assets, *building_render_assets),
            provider_requirements=(),
            base_layers=(),
            layers=layers,
            buildings=tuple(buildings),
            roads=tuple(roads),
            regions=(),
            launch_sites=(),
            entities=tuple(entities),
            sensors=(),
            semantic_targets=(),
            weather=catalog.weather,
            engine_frame_bindings=(),
            sumo=None,
            network=None,
            mission_requirements=(),
            expected_public_assets=(),
        )
    except ValueError as exc:
        raise UrbanWorldAuthoringError(
            f"authored WorldPackage failed strict contract validation: {exc}"
        ) from exc

    catalog_sha256 = _sha256_bytes(catalog_bytes)
    provenance = {
        "schema_version": AUTHORING_SCHEMA_VERSION,
        "world_id": package.world_id,
        "world_digest": package.world_digest,
        "asset_digest": package.asset_digest,
        "scene_manifest_sha256": manifest_sha256,
        "scene_source_sha256": scene_source_sha256,
        "catalog_sha256": catalog_sha256,
        "building_count": len(buildings),
        "building_render_count": len(building_render_assets),
        "road_count": len(roads),
        "entity_count": len(entities),
    }
    provenance_bytes = canonical_json_bytes(provenance) + b"\n"
    package_bytes = canonical_json_bytes(package.model_dump(mode="json")) + b"\n"

    # ---- stage + atomic commit --------------------------------------------- #
    staged_root = _staging_directory(output_root)
    try:
        staged_root.mkdir(parents=True, exist_ok=False)
        for relative in (
            _SCENE_LAYER_RELATIVE,
            _SCENE_BUILDING_SOURCE_RELATIVE,
            _SCENE_BUILDING_COLLISION_RELATIVE,
        ):
            destination = staged_root / SCENE_ASSET_SELECTOR_PREFIX / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((scene_root / relative).read_bytes())
        for relative, data in (*catalog_stage, *building_render_stage):
            destination = staged_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        license_destination = staged_root / catalog.license_path
        license_destination.parent.mkdir(parents=True, exist_ok=True)
        license_destination.write_bytes(license_bytes)
        package_destination = staged_root / PACKAGE_RELATIVE_PATH
        package_destination.parent.mkdir(parents=True, exist_ok=True)
        package_destination.write_bytes(package_bytes)
        provenance_destination = staged_root / PROVENANCE_RELATIVE_PATH
        provenance_destination.parent.mkdir(parents=True, exist_ok=True)
        provenance_destination.write_bytes(provenance_bytes)
        _commit(staged_root, output_root)
    except BaseException:
        shutil.rmtree(staged_root, ignore_errors=True)
        raise

    return UrbanWorldAuthoringResult(
        bundle_root=output_root.resolve(),
        package_path=PACKAGE_RELATIVE_PATH,
        package_ref=FileRef(
            path=PACKAGE_RELATIVE_PATH, sha256=_sha256_bytes(package_bytes)
        ),
        world_package=package,
        world_digest=package.world_digest,
        asset_digest=package.asset_digest,
        scene_source_sha256=scene_source_sha256,
        scene_manifest_sha256=manifest_sha256,
        catalog_sha256=catalog_sha256,
        building_count=len(buildings),
        road_count=len(roads),
        entity_count=len(entities),
    )


__all__ = [
    "AUTHORING_SCHEMA_VERSION",
    "SCENE_ASSET_SELECTOR_PREFIX",
    "SCENE_COMPILER_SCHEMA_VERSION",
    "CatalogAsset",
    "CatalogBuildingRender",
    "CatalogRoadWidth",
    "RoadWidthLane",
    "RoadWidthProvenance",
    "UrbanWorldAuthoringError",
    "UrbanWorldAuthoringRequest",
    "UrbanWorldAuthoringResult",
    "UrbanWorldCatalog",
    "WidthSource",
    "author_urban_world_package",
]
