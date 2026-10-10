"""Strict offline source and ENU selection contract for scene authoring."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.frame_math import Vector3
from aero_bench.world.scene_compiler import EnuBounds, SceneCompilationError, SceneOrigin


SCENE_SELECTION_SCHEMA_VERSION = "aero-bench.scene-selection/v1"
SHANGHAI_SOURCE_ID = "shanghai-central-osm-v1"
SHANGHAI_SOURCE_SHA256 = "d0f3f30f846e8145aecd497e20ee95bc58e5cb0b3d4b4f46d1339e285217b847"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_ID_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_ORIGIN_KEYS = frozenset({
    "latitude_deg", "longitude_deg", "ellipsoid_height_m",
    "geoid_undulation_m", "amsl_m",
})
_BOUNDS_KEYS = frozenset({
    "min_east_m", "max_east_m", "min_north_m", "max_north_m",
})


class SceneSelectionError(ValueError):
    """A client selection or a registered source failed explicit validation."""


def _object(value: object, keys: frozenset[str], label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SceneSelectionError(f"{label} must have exactly: {', '.join(sorted(keys))}")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneSelectionError(f"{label} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise SceneSelectionError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise SceneSelectionError(f"{label} must be a finite number")
    return number


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SceneSelectionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise SceneSelectionError(f"non-JSON numeric constant: {value}")


@dataclass(frozen=True, slots=True)
class SceneSelection:
    source_id: str
    source_sha256: str
    origin: SceneOrigin
    bounds_enu_m: EnuBounds

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise SceneSelectionError("source_id must be a non-empty string")
        if not isinstance(self.source_sha256, str) or not _SHA256_RE.fullmatch(self.source_sha256):
            raise SceneSelectionError("source_sha256 must be lowercase 64-hex SHA-256")
        if not isinstance(self.origin, SceneOrigin) or not isinstance(self.bounds_enu_m, EnuBounds):
            raise SceneSelectionError("origin and bounds_enu_m must be validated scene coordinates")

    @classmethod
    def from_document(cls, value: object) -> SceneSelection:
        document = _object(
            value,
            frozenset({"schema_version", "source_id", "source_sha256", "origin", "bounds_enu_m"}),
            "SceneSelection",
        )
        if document["schema_version"] != SCENE_SELECTION_SCHEMA_VERSION:
            raise SceneSelectionError("unsupported SceneSelection schema_version")
        source_id = document["source_id"]
        source_sha256 = document["source_sha256"]
        if not isinstance(source_id, str) or not source_id:
            raise SceneSelectionError("source_id must be a non-empty string")
        if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
            raise SceneSelectionError("source_sha256 must be lowercase 64-hex SHA-256")
        raw_origin = _object(document["origin"], _ORIGIN_KEYS, "origin")
        raw_bounds = _object(document["bounds_enu_m"], _BOUNDS_KEYS, "bounds_enu_m")
        try:
            origin = SceneOrigin(**{key: _number(raw_origin[key], f"origin.{key}") for key in _ORIGIN_KEYS})
            bounds = EnuBounds(**{key: _number(raw_bounds[key], f"bounds_enu_m.{key}") for key in _BOUNDS_KEYS})
        except SceneCompilationError as exc:
            raise SceneSelectionError(str(exc)) from exc
        return cls(source_id, source_sha256, origin, bounds)

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> SceneSelection:
        try:
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SceneSelectionError("SceneSelection must be valid UTF-8 JSON") from exc
        return cls.from_document(document)

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": SCENE_SELECTION_SCHEMA_VERSION,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "origin": {
                "latitude_deg": self.origin.latitude_deg,
                "longitude_deg": self.origin.longitude_deg,
                "ellipsoid_height_m": self.origin.ellipsoid_height_m,
                "geoid_undulation_m": self.origin.geoid_undulation_m,
                "amsl_m": self.origin.amsl_m,
            },
            "bounds_enu_m": self.bounds_enu_m.manifest_document(),
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.to_document())).hexdigest()


@dataclass(frozen=True, slots=True)
class Wgs84Bounds:
    min_latitude_deg: float
    max_latitude_deg: float
    min_longitude_deg: float
    max_longitude_deg: float

    @classmethod
    def from_osm_document(cls, document: object) -> Wgs84Bounds:
        if not isinstance(document, dict):
            raise SceneSelectionError("registered OSM source must be a JSON object")
        raw = _object(
            document.get("bounds"),
            frozenset({"minlat", "maxlat", "minlon", "maxlon"}),
            "registered OSM source.bounds",
        )
        bounds = cls(
            _number(raw["minlat"], "source.bounds.minlat"),
            _number(raw["maxlat"], "source.bounds.maxlat"),
            _number(raw["minlon"], "source.bounds.minlon"),
            _number(raw["maxlon"], "source.bounds.maxlon"),
        )
        if not (-90.0 < bounds.min_latitude_deg < bounds.max_latitude_deg < 90.0):
            raise SceneSelectionError("registered source latitude bounds are invalid")
        if not (-180.0 <= bounds.min_longitude_deg < bounds.max_longitude_deg <= 180.0):
            raise SceneSelectionError("registered source longitude bounds are invalid")
        return bounds


@dataclass(frozen=True, slots=True)
class RegisteredSceneSource:
    source_id: str
    path: Path
    sha256: str
    origin: SceneOrigin = SceneOrigin()
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class VerifiedSceneSource:
    registration: RegisteredSceneSource
    raw_bytes: bytes
    bounds_wgs84: Wgs84Bounds

    @property
    def byte_size(self) -> int:
        return len(self.raw_bytes)


class SceneSourceRegistry:
    """Trusted server-side source IDs; client data never supplies a file path."""

    def __init__(self, sources: Mapping[str, RegisteredSceneSource]):
        if not sources:
            raise ValueError("source registry cannot be empty")
        for source_id, source in sources.items():
            if (source_id != source.source_id or not _SOURCE_ID_RE.fullmatch(source_id)
                    or not _SHA256_RE.fullmatch(source.sha256)
                    or not isinstance(source.display_name, str)):
                raise ValueError("invalid source registration")
        self._sources = dict(sources)

    @classmethod
    def from_manifest(cls, manifest_path: Path, *, repository_root: Path) -> SceneSourceRegistry:
        """Load trusted, digest-pinned OSM registrations relative to the repository."""

        root = repository_root.resolve()
        try:
            raw = manifest_path.read_bytes()
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
                                  parse_constant=_reject_non_json_constant)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SceneSelectionError("source registry manifest must be readable UTF-8 JSON") from exc
        manifest = _object(document, frozenset({"schema_version", "sources"}), "source registry")
        if manifest["schema_version"] != "aero-bench.scene-source-registry/v1":
            raise SceneSelectionError("unsupported source registry schema_version")
        entries = manifest["sources"]
        if not isinstance(entries, list) or not entries:
            raise SceneSelectionError("source registry sources must be a non-empty array")
        sources: dict[str, RegisteredSceneSource] = {}
        for index, value in enumerate(entries):
            item = _object(value, frozenset({"source_id", "display_name", "path", "sha256", "origin"}),
                           f"source registry.sources[{index}]")
            source_id, name, relative, digest = (item[key] for key in
                                                  ("source_id", "display_name", "path", "sha256"))
            if (not isinstance(source_id, str) or not _SOURCE_ID_RE.fullmatch(source_id)
                    or not isinstance(name, str) or not name.strip()
                    or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
                    or not isinstance(relative, str) or not relative):
                raise SceneSelectionError(f"source registry.sources[{index}] identity is invalid")
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts or relative_path.suffix != ".json":
                raise SceneSelectionError(f"source registry.sources[{index}] path is invalid")
            path = (root / relative_path).resolve()
            if not path.is_relative_to(root):
                raise SceneSelectionError(f"source registry.sources[{index}] path leaves repository")
            raw_origin = _object(item["origin"], _ORIGIN_KEYS, f"source registry.sources[{index}].origin")
            try:
                origin = SceneOrigin(**{key: _number(raw_origin[key], f"origin.{key}")
                                        for key in _ORIGIN_KEYS})
            except SceneCompilationError as exc:
                raise SceneSelectionError(str(exc)) from exc
            if source_id in sources:
                raise SceneSelectionError(f"duplicate source registration: {source_id}")
            sources[source_id] = RegisteredSceneSource(source_id, path, digest, origin, name)
        registry = cls(sources)
        for source_id in sources:
            registry.read(source_id)
        return registry

    def read(self, source_id: str) -> VerifiedSceneSource:
        """Read only a registered source and verify its pinned original bytes."""

        registration = self._sources.get(source_id)
        if registration is None:
            raise SceneSelectionError(f"source_id is not registered: {source_id}")
        raw = registration.path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != registration.sha256:
            raise SceneSelectionError("registered source bytes do not match pinned source_sha256")
        try:
            source_document = json.loads(raw.decode("utf-8"), parse_constant=_reject_non_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SceneSelectionError("registered OSM source must be valid UTF-8 JSON") from exc
        bounds = Wgs84Bounds.from_osm_document(source_document)
        if (
            abs(bounds.min_latitude_deg) > 60.0
            or abs(bounds.max_latitude_deg) > 60.0
            or bounds.max_latitude_deg - bounds.min_latitude_deg > 0.02
            or bounds.max_longitude_deg - bounds.min_longitude_deg > 0.025
        ):
            raise SceneSelectionError("registered source exceeds the v1 local WGS84 bounds policy")
        origin = registration.origin
        if not (
            -500.0 <= origin.ellipsoid_height_m <= 10_000.0
            and bounds.min_latitude_deg <= origin.latitude_deg <= bounds.max_latitude_deg
            and bounds.min_longitude_deg <= origin.longitude_deg <= bounds.max_longitude_deg
        ):
            raise SceneSelectionError("registered source origin exceeds the v1 local WGS84 bounds policy")
        return VerifiedSceneSource(registration, raw, bounds)

    def verify(self, selection: SceneSelection) -> VerifiedSceneSource:
        verified = self.read(selection.source_id)
        if selection.source_sha256 != verified.registration.sha256:
            raise SceneSelectionError("selection source_sha256 differs from registered source")
        if selection.origin != verified.registration.origin:
            raise SceneSelectionError("selection origin differs from registered source origin")
        _check_selection_inside_source(selection, verified.bounds_wgs84)
        return verified


def default_scene_source_registry(repository_root: Path | None = None) -> SceneSourceRegistry:
    root = repository_root or Path(__file__).resolve().parents[2]
    return SceneSourceRegistry.from_manifest(root / "aero_bench/authoring/sources.json",
                                             repository_root=root)


def _check_selection_inside_source(selection: SceneSelection, source: Wgs84Bounds) -> None:
    """Require the ENU rectangle to remain inside the OSM declared WGS84 area.

    The four corners alone are insufficient: latitude can have an extremum
    between corners on a constant-north edge. Registry.read limits this v1
    policy to local boxes at latitudes within +/-60 degrees and at most
    0.02 by 0.025 degrees, with a registered origin between -500 and 10000 m
    ellipsoid height. That limits edge sample spacing to about 22 m for the
    WGS84 local tangent plane; the inward 2e-8-degree margin covers edge
    curvature between samples at this scale. Larger and polar sources fail
    closed until a new policy is added.
    """

    origin = selection.origin
    if not (
        source.min_latitude_deg <= origin.latitude_deg <= source.max_latitude_deg
        and source.min_longitude_deg <= origin.longitude_deg <= source.max_longitude_deg
    ):
        raise SceneSelectionError("origin is outside registered OSM source bounds")

    transform = origin.enu_transform()
    bounds = selection.bounds_enu_m
    # First reject distant requests without evaluating an unbounded ENU plane.
    projected = [
        transform.geodetic_to_enu(
            longitude_deg=longitude,
            latitude_deg=latitude,
            altitude_m=origin.ellipsoid_height_m,
        )
        for latitude in (source.min_latitude_deg, source.max_latitude_deg)
        for longitude in (source.min_longitude_deg, source.max_longitude_deg)
    ]
    east = [point.x for point in projected]
    north = [point.y for point in projected]
    if (
        bounds.min_east_m < min(east) - 1.0
        or bounds.max_east_m > max(east) + 1.0
        or bounds.min_north_m < min(north) - 1.0
        or bounds.max_north_m > max(north) + 1.0
    ):
        raise SceneSelectionError("ENU selection is outside registered OSM source bounds")

    inset_deg = 2e-8
    for index in range(129):
        t = index / 128
        e = bounds.min_east_m + t * (bounds.max_east_m - bounds.min_east_m)
        n = bounds.min_north_m + t * (bounds.max_north_m - bounds.min_north_m)
        for sample_e, sample_n in (
            (e, bounds.min_north_m), (e, bounds.max_north_m),
            (bounds.min_east_m, n), (bounds.max_east_m, n),
        ):
            longitude, latitude, _ = transform.enu_to_geodetic(Vector3(sample_e, sample_n, 0.0))
            if not (
                source.min_latitude_deg + inset_deg <= latitude <= source.max_latitude_deg - inset_deg
                and source.min_longitude_deg + inset_deg <= longitude <= source.max_longitude_deg - inset_deg
            ):
                raise SceneSelectionError("ENU selection crosses registered OSM source bounds")
