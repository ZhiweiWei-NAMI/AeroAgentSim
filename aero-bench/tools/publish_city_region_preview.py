#!/usr/bin/env python3
"""Stage or publish one verified local city-region engineering preview.

The input is a validated private staging manifest.  The default mode writes a
byte-identical candidate below ``--candidate-root`` and does not modify the
frontend public tree.  ``--publish`` is deliberately explicit.  Publication is
fail-closed: an existing target must be byte-for-byte identical, and a region
tree must have exactly the same files, or the command stops without replacing
anything.

This tool publishes viewer inputs, not formal Provider evidence.  Its optional
environment document is generated from the pack's exact OSM JSON source and the
region's exact compiler objects through ``city_environment_source.py``; it does
not infer or invent ground classes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_SCHEMA = "aero-bench.city-region-publication-candidate/v1"
STAGING_SCHEMA = "aero-bench.city-region-staging/v1"
SCENE_SCHEMA = "aero-bench.city-scene-preview/v1"
ROAD_ASSETS_SCHEMA = "aero-bench.city-road-assets/v2"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REGION_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PUBLIC_URL_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")


class PublicationError(ValueError):
    """A publication input or collision failed closed."""


@dataclass(frozen=True)
class FileMeasurement:
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class TreeMeasurement:
    sha256: str
    file_count: int
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "encoding": "sorted-relative-posix-path-utf8-nul-file-bytes/v1",
            "file_count": self.file_count,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class Target:
    url: str
    source_path: Path | None = None
    content: bytes | None = None

    def __post_init__(self) -> None:
        if (self.source_path is None) == (self.content is None):
            raise AssertionError("a target must have exactly one source")

    @property
    def is_tree(self) -> bool:
        return self.source_path is not None and self.source_path.is_dir()


@dataclass(frozen=True)
class ValidatedRegion:
    manifest: dict[str, Any]
    manifest_path: Path
    manifest_measurement: FileMeasurement
    staging_root: Path
    references: tuple[dict[str, Any], ...]
    pack_root: Path
    pack_tree: TreeMeasurement
    pack: dict[str, Any]
    runtime_root: Path
    runtime_tree: TreeMeasurement
    runtime: dict[str, Any]
    runtime_source_context: FileMeasurement
    rendered_source_context: dict[str, Any]
    road_path: Path
    road: dict[str, Any]
    fixtures_path: Path
    fixtures: dict[str, Any]
    traffic_path: Path
    traffic: dict[str, Any]
    flight_path: Path
    flight: dict[str, Any]
    external_dependencies: tuple[dict[str, Any], ...]
    pass_evidence: tuple[dict[str, Any], ...]


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def measure_file(path: Path) -> FileMeasurement:
    if path.is_symlink() or not path.is_file():
        raise PublicationError(f"expected a regular file: {path}")
    data = path.read_bytes()
    return FileMeasurement(_sha256(data), len(data))


def _tree_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise PublicationError(f"expected a regular directory tree: {root}")
    result: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PublicationError(f"symbolic links are not publishable: {path}")
        if path.is_file():
            result.append(path)
        elif not path.is_dir():
            raise PublicationError(f"unsupported tree entry: {path}")
    return sorted(result, key=lambda item: item.relative_to(root).as_posix())


def measure_tree(root: Path) -> TreeMeasurement:
    """Hash a tree using the encoding already used by the C staging evidence."""

    digest = hashlib.sha256()
    total = 0
    files = _tree_files(root)
    for path in files:
        data = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        total += len(data)
    return TreeMeasurement(digest.hexdigest(), len(files), total)


def _document(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"{label} must be a JSON object: {path}")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicationError(f"{label} must be an object")
    return value


def _at(document: Mapping[str, Any], dotted: str) -> dict[str, Any]:
    value: Any = document
    for part in dotted.split("."):
        value = _mapping(value, dotted).get(part)
    return _mapping(value, dotted)


def _within(root: Path, path: Path, label: str) -> Path:
    root = root.resolve()
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PublicationError(f"{label} escapes {root}: {path}") from exc
    return path


def _staged_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "//" in value:
        raise PublicationError(f"{label} has an invalid staged path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise PublicationError(f"{label} has an unsafe staged path: {value}")
    return _within(root, root.joinpath(*pure.parts), label)


def _public_parts(url: str, *, directory: bool = False) -> tuple[str, ...]:
    if not isinstance(url, str) or "//" in url or not PUBLIC_URL_RE.fullmatch(url):
        raise PublicationError(f"invalid local public URL: {url!r}")
    if directory != url.endswith("/"):
        expected = "end in /" if directory else "name a file"
        raise PublicationError(f"public URL must {expected}: {url}")
    parts = PurePosixPath(url.rstrip("/")).parts
    if not parts or parts[0] != "/" or any(part in {"", ".", ".."} for part in parts[1:]):
        raise PublicationError(f"unsafe local public URL: {url}")
    return tuple(parts[1:])


def _public_path(root: Path, url: str, *, directory: bool = False) -> Path:
    return _within(root, root.joinpath(*_public_parts(url, directory=directory)), url)


def _assert_measurement(actual: FileMeasurement, ref: Mapping[str, Any], label: str) -> None:
    expected_hash = ref.get("sha256")
    if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
        raise PublicationError(f"{label} has an invalid sha256")
    if actual.sha256 != expected_hash:
        raise PublicationError(
            f"{label} sha256 mismatch: expected {expected_hash}, measured {actual.sha256}"
        )
    if "size_bytes" in ref:
        expected_size = ref["size_bytes"]
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 1:
            raise PublicationError(f"{label} has an invalid size_bytes")
        if actual.size_bytes != expected_size:
            raise PublicationError(
                f"{label} size mismatch: expected {expected_size}, measured {actual.size_bytes}"
            )


def _walk_path_refs(value: Any, locator: str = "") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            yield locator, value
        for key in sorted(value):
            child = f"{locator}.{key}" if locator else key
            yield from _walk_path_refs(value[key], child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_path_refs(item, f"{locator}[{index}]")


def _verify_references(
    manifest: dict[str, Any], staging_root: Path
) -> tuple[tuple[dict[str, Any], ...], dict[str, Path]]:
    verified: list[dict[str, Any]] = []
    paths: dict[str, Path] = {}
    seen: dict[str, str] = {}
    for locator, ref in _walk_path_refs(manifest):
        path = _staged_path(staging_root, ref.get("path"), locator)
        measurement = measure_file(path)
        _assert_measurement(measurement, ref, locator)
        relative = path.relative_to(staging_root).as_posix()
        if relative in seen and seen[relative] != measurement.sha256:
            raise PublicationError(f"conflicting references to {relative}")
        seen[relative] = measurement.sha256
        paths[locator] = path
        verified.append(
            {
                "locator": locator,
                "path": relative,
                **measurement.as_dict(),
            }
        )
    if not verified:
        raise PublicationError("staging manifest has no digest-bound file references")
    return tuple(verified), paths


def _required_path(paths: Mapping[str, Path], locator: str) -> Path:
    try:
        return paths[locator]
    except KeyError as exc:
        raise PublicationError(f"staging manifest lacks required reference {locator}") from exc


def _assert_content_addressed_assets(root: Path, label: str) -> int:
    assets = root / "assets"
    if not assets.is_dir() or assets.is_symlink():
        raise PublicationError(f"{label} has no regular assets directory")
    count = 0
    for path in _tree_files(assets):
        relative = path.relative_to(assets)
        if len(relative.parts) != 1 or not SHA256_RE.fullmatch(path.name):
            raise PublicationError(f"{label} asset is not content-addressed: {relative}")
        measured = measure_file(path)
        if measured.sha256 != path.name:
            raise PublicationError(f"{label} asset name does not match its bytes: {relative}")
        count += 1
    if count == 0:
        raise PublicationError(f"{label} has no content-addressed assets")
    return count


def _find_runtime_source_context(
    runtime_root: Path, runtime: Mapping[str, Any], region_id: str
) -> tuple[FileMeasurement, dict[str, Any]]:
    matches: list[tuple[Path, FileMeasurement, dict[str, Any]]] = []
    for path in _tree_files(runtime_root / "assets"):
        try:
            value = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or value.get("schema_version") != (
            "aero-bench.building-render-source-context/v1"
        ):
            continue
        scene = _mapping(value.get("scene"), "runtime source context scene")
        if scene.get("id") == region_id:
            matches.append((path, measure_file(path), value))
    if len(matches) != 1:
        raise PublicationError(
            f"runtime must contain exactly one source context for {region_id}; found {len(matches)}"
        )
    path, measured, document = matches[0]
    if path.name != measured.sha256:
        raise PublicationError("runtime source context is not stored under its digest")
    if document["scene"] != runtime.get("scene"):
        raise PublicationError("runtime manifest and runtime source context scene identities differ")
    return measured, document


def _verify_local_ref(public_root: Path, ref: Mapping[str, Any], label: str) -> dict[str, Any]:
    keys = set(ref)
    if not {"url", "sha256", "size_bytes"}.issubset(keys):
        raise PublicationError(f"{label} is not a digest-bound public asset ref")
    url = ref["url"]
    if not isinstance(url, str):
        raise PublicationError(f"{label} URL is invalid")
    path = _public_path(public_root, url)
    measured = measure_file(path)
    _assert_measurement(measured, ref, label)
    return {"label": label, "url": url, **measured.as_dict()}


def _verify_pack_dependencies(
    public_root: Path, pack: Mapping[str, Any], road_texture: str
) -> tuple[dict[str, Any], ...]:
    textures = _mapping(pack.get("textures"), "mesh pack textures")
    if road_texture not in textures:
        raise PublicationError("road surface texture is absent from the mesh-pack texture lock")
    verified: list[dict[str, Any]] = []
    for url in sorted(textures):
        ref = _mapping(textures[url], f"mesh pack texture {url}")
        if set(ref) != {"sha256", "size_bytes"}:
            raise PublicationError(f"mesh pack texture {url} has undeclared fields")
        verified.append(_verify_local_ref(public_root, {"url": url, **ref}, "mesh-pack texture"))
    return tuple(verified)


def _assert_source_context(
    context: Mapping[str, Any], *, region_id: str, pack: Mapping[str, Any],
    runtime_ref: Mapping[str, Any], network_ref: Mapping[str, Any],
    source_osm_ref: Mapping[str, Any], engineering_ref: Mapping[str, Any],
    objects_sha256: str,
) -> None:
    required = {
        "schema_version": "aero-bench.city-rendered-source-context/v1",
        "scene_id": region_id,
        "objects_json_sha256": objects_sha256,
        "building_render_manifest_sha256": runtime_ref.get("sha256"),
        "mesh_pack_manifest_sha256": pack.get("_manifest_sha256"),
        "mesh_pack_source_sha256": _mapping(pack.get("source"), "mesh pack source").get("sha256"),
        "source_network_sha256": network_ref.get("sha256"),
        "source_osm_sha256": source_osm_ref.get("sha256"),
        "engineering_inputs_sha256": engineering_ref.get("sha256"),
    }
    for key, expected in required.items():
        if context.get(key) != expected:
            raise PublicationError(
                f"rendered source context {key} mismatch: expected {expected!r}, "
                f"found {context.get(key)!r}"
            )


def _verify_audit_inputs(audit: Mapping[str, Any], expected: Mapping[str, Mapping[str, Any]]) -> None:
    inputs = _mapping(audit.get("inputs"), "motion audit inputs")
    for name, ref in expected.items():
        actual = _mapping(inputs.get(name), f"motion audit input {name}")
        for key in ("sha256", "size_bytes"):
            if actual.get(key) != ref.get(key):
                raise PublicationError(f"motion audit input {name}.{key} is not staging-bound")


def validate_region(staging_manifest: Path, public_root: Path) -> ValidatedRegion:
    manifest_path = staging_manifest.resolve()
    manifest = _document(manifest_path, "staging manifest")
    manifest_measurement = measure_file(manifest_path)
    if manifest.get("schema_version") != STAGING_SCHEMA:
        raise PublicationError("unsupported city-region staging manifest")
    region_id = manifest.get("region_id")
    if not isinstance(region_id, str) or not REGION_RE.fullmatch(region_id):
        raise PublicationError("staging region_id is invalid")
    if manifest.get("state") != "validated-private-staging-not-published":
        raise PublicationError("region is not validated private staging")
    if manifest.get("formal_run_evidence") is not False:
        raise PublicationError("viewer staging must not claim formal-run evidence")
    authorization = _mapping(manifest.get("asset_authorization"), "asset authorization")
    if authorization.get("scope") != "internal-research-staging" or (
        authorization.get("research_staging_blocked") is not False
    ):
        raise PublicationError("region is not authorized for internal research staging")

    staging_root = manifest_path.parent
    references, paths = _verify_references(manifest, staging_root)
    source = _mapping(manifest.get("source_context"), "source context refs")
    renders = _mapping(manifest.get("render_assets"), "render asset refs")
    traffic_validation = _mapping(manifest.get("traffic_validation"), "traffic validation refs")
    private = _mapping(traffic_validation.get("private_native_evidence"), "private traffic evidence")

    pack_ref = _mapping(renders.get("mesh_pack"), "mesh pack ref")
    runtime_ref = _mapping(renders.get("building_runtime"), "building runtime ref")
    network_ref = _mapping(source.get("network"), "network ref")
    source_osm_ref = _mapping(source.get("source_osm"), "source OSM ref")
    engineering_ref = _mapping(source.get("engineering_inputs"), "engineering inputs ref")
    closed_loop_ref = _mapping(source.get("closed_loop_audit"), "closed-loop audit ref")
    clearance_ref = _mapping(renders.get("road_physical_clearance"), "road clearance ref")
    road_ref = _mapping(renders.get("road"), "road ref")
    fixtures_ref = _mapping(renders.get("effective_fixtures"), "effective fixtures ref")
    traffic_ref = _mapping(renders.get("traffic"), "traffic ref")
    flight_ref = _mapping(renders.get("planned_flight"), "flight ref")
    workspace_ref = _mapping(traffic_validation.get("workspace"), "traffic workspace ref")
    audit_ref = _mapping(traffic_validation.get("independent_audit"), "motion audit ref")
    native_ref = _mapping(private.get("native_recording"), "native recording ref")
    routes_ref = _mapping(private.get("routes"), "routes ref")
    recording_ref = _mapping(private.get("recording_inputs"), "recording inputs ref")

    locator_by_ref_id = {id(ref): locator for locator, ref in _walk_path_refs(manifest)}

    def ref_path(ref: dict[str, Any]) -> Path:
        return _required_path(paths, locator_by_ref_id[id(ref)])

    pack_path = ref_path(pack_ref)
    runtime_path = ref_path(runtime_ref)
    if pack_path.name != "manifest.json" or runtime_path.name != "manifest.json":
        raise PublicationError("pack and runtime references must name manifest.json")
    pack_root = pack_path.parent
    runtime_root = runtime_path.parent
    pack_tree = measure_tree(pack_root)
    runtime_tree = measure_tree(runtime_root)
    if pack_tree.sha256 != pack_ref.get("tree_sha256"):
        raise PublicationError("mesh-pack tree hash differs from staging")
    if runtime_tree.sha256 != runtime_ref.get("tree_sha256"):
        raise PublicationError("building-runtime tree hash differs from staging")
    _assert_content_addressed_assets(pack_root, "mesh pack")
    _assert_content_addressed_assets(runtime_root, "building runtime")

    pack = _document(pack_path, "mesh pack manifest")
    runtime = _document(runtime_path, "building runtime manifest")
    pack["_manifest_sha256"] = pack_ref["sha256"]
    if pack.get("schema_version") != "aero-bench.osm2world-mesh-pack/v1":
        raise PublicationError("unsupported mesh-pack manifest")
    if runtime.get("schema_version") != "aero-bench.building-render-runtime/v2":
        raise PublicationError("unsupported building-runtime manifest")
    pack_source = _mapping(pack.get("source"), "mesh pack source")
    pack_source_asset = pack_root / "assets" / str(pack_source.get("sha256"))
    _assert_measurement(measure_file(pack_source_asset), pack_source, "mesh pack source asset")
    runtime_scene = _mapping(runtime.get("scene"), "building runtime scene")
    if runtime_scene.get("id") != region_id:
        raise PublicationError("building runtime belongs to another region")
    if runtime_scene.get("mesh_pack_manifest_sha256") != pack_ref["sha256"] or (
        runtime_scene.get("mesh_pack_source_sha256") != pack_source.get("sha256")
    ):
        raise PublicationError("building runtime and mesh pack identities differ")
    source_context_measurement, runtime_source_context = _find_runtime_source_context(
        runtime_root, runtime, region_id
    )

    road_path = ref_path(road_ref)
    fixtures_path = ref_path(fixtures_ref)
    traffic_path = ref_path(traffic_ref)
    flight_path = ref_path(flight_ref)
    road = _document(road_path, "canonical road")
    fixtures = _document(fixtures_path, "effective fixtures")
    traffic = _document(traffic_path, "SUMO traffic preview")
    flight = _document(flight_path, "planned flight preview")
    clearance = _document(ref_path(clearance_ref), "road physical-clearance audit")
    closed_loop = _document(ref_path(closed_loop_ref), "closed-loop audit")
    motion_audit = _document(ref_path(audit_ref), "motion audit")

    if closed_loop_ref.get("status") != "PASS" or closed_loop.get("final_status") != "PASS":
        raise PublicationError("closed-loop evidence is not PASS")
    rounds = closed_loop.get("rounds")
    if not isinstance(rounds, list) or not rounds or rounds[-1].get("status") != "PASS" or (
        _mapping(rounds[-1].get("gate"), "final closed-loop gate").get("status") != "PASS"
    ):
        raise PublicationError("closed-loop final gate is not PASS")
    native_clearance = _mapping(clearance.get("native_lane_clearance"), "native lane clearance")
    if clearance_ref.get("status") != "PASS" or native_clearance.get("status") != "PASS":
        raise PublicationError("native road physical clearance is not PASS")
    if audit_ref.get("status") != "PASS" or audit_ref.get("failures") != 0 or (
        motion_audit.get("status") != "PASS" or motion_audit.get("failures") != []
    ):
        raise PublicationError("independent SUMO motion audit is not PASS")
    if traffic.get("schema_version") != "aero-bench.city-sumo-preview/v2" or (
        traffic.get("artifact_class") != "offline-engineering-preview"
        or traffic.get("source_kind") != traffic_ref.get("source_kind")
        or traffic_ref.get("formal_provider_evidence") is not False
    ):
        raise PublicationError("traffic asset is not the declared offline engineering preview")
    if flight.get("schema_version") != "aero-bench.city-planned-flight-preview/v2" or (
        flight.get("source_kind") != flight_ref.get("source_kind")
        or flight.get("physical_simulation") is not False
        or flight_ref.get("physical_simulation") is not False
    ):
        raise PublicationError("flight asset is not the declared non-physical planned preview")

    contexts = [
        _mapping(item.get("source_context"), label)
        for item, label in (
            (road, "road source context"),
            (clearance, "clearance source context"),
            (fixtures, "fixtures source context"),
            (traffic, "traffic source context"),
            (motion_audit, "motion-audit source context"),
            (flight, "flight source context"),
        )
    ]
    if any(context != contexts[0] for context in contexts[1:]):
        raise PublicationError("regional road, fixtures, traffic, audit and flight contexts differ")
    objects_sha256 = runtime_scene.get("objects_json_sha256")
    if not isinstance(objects_sha256, str) or not SHA256_RE.fullmatch(objects_sha256):
        raise PublicationError("runtime objects identity is invalid")
    _assert_source_context(
        contexts[0], region_id=region_id, pack=pack, runtime_ref=runtime_ref,
        network_ref=network_ref, source_osm_ref=source_osm_ref,
        engineering_ref=engineering_ref, objects_sha256=objects_sha256,
    )
    if road.get("displayed_surface_sha256") != road_ref.get("displayed_surface_sha256") or (
        fixtures.get("displayed_surface_sha256") != road_ref.get("displayed_surface_sha256")
    ):
        raise PublicationError("road and effective fixtures displayed-surface identities differ")
    if _mapping(road.get("physical_clearance"), "road physical clearance").get("status") != "PASS":
        raise PublicationError("published road document does not embed PASS clearance")
    if _mapping(traffic.get("visual_obstacle_basis"), "traffic obstacle basis").get(
        "effective_fixture_geometry_sha256"
    ) != fixtures_ref.get("sha256"):
        raise PublicationError("traffic obstacle basis is not bound to the staged effective fixtures")

    _verify_audit_inputs(
        motion_audit,
        {
            "effective_fixtures": fixtures_ref,
            "native": native_ref,
            "network": network_ref,
            "pack": pack_ref,
            "recording_inputs": recording_ref,
            "render_manifest": runtime_ref,
            "road": road_ref,
            "routes": routes_ref,
            "source_osm": source_osm_ref,
            "traffic": traffic_ref,
            "workspace": workspace_ref,
        },
    )

    models = _mapping(fixtures.get("models"), "effective-fixture model refs")
    signal_ref = _mapping(models.get("signal"), "source signal model")
    dependencies = list(_verify_pack_dependencies(
        public_root, pack, str(road.get("source_asphalt_texture"))
    ))
    for name in sorted(models):
        model_ref = _mapping(models[name], f"fixture model {name}")
        dependencies.append(_verify_local_ref(public_root, model_ref, f"fixture model {name}"))
    signal_verified = next(
        (item for item in dependencies if item["url"] == signal_ref.get("url")), None
    )
    if signal_verified is None:
        raise PublicationError("source signal model was not verified")

    pass_evidence = (
        {"kind": "closed-loop-ground", "path": closed_loop_ref["path"], "status": "PASS",
         "sha256": closed_loop_ref["sha256"]},
        {"kind": "native-road-physical-clearance", "path": clearance_ref["path"], "status": "PASS",
         "sha256": clearance_ref["sha256"]},
        {"kind": "independent-sumo-motion", "path": audit_ref["path"], "status": "PASS",
         "sha256": audit_ref["sha256"]},
    )
    return ValidatedRegion(
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_measurement=manifest_measurement,
        staging_root=staging_root,
        references=references,
        pack_root=pack_root,
        pack_tree=pack_tree,
        pack=pack,
        runtime_root=runtime_root,
        runtime_tree=runtime_tree,
        runtime=runtime,
        runtime_source_context=source_context_measurement,
        rendered_source_context=contexts[0],
        road_path=road_path,
        road=road,
        fixtures_path=fixtures_path,
        fixtures=fixtures,
        traffic_path=traffic_path,
        traffic=traffic,
        flight_path=flight_path,
        flight=flight,
        external_dependencies=tuple(dependencies),
        pass_evidence=pass_evidence,
    )


def _environment_bytes(region: ValidatedRegion, objects_path: Path) -> tuple[bytes, dict[str, Any]]:
    objects_path = _within(region.staging_root, objects_path, "environment objects")
    objects_measurement = measure_file(objects_path)
    expected_objects = _mapping(region.runtime.get("scene"), "runtime scene")["objects_json_sha256"]
    if objects_measurement.sha256 != expected_objects:
        raise PublicationError("environment objects do not match the runtime objects identity")
    pack_source = _mapping(region.pack.get("source"), "mesh pack source")
    osm_path = region.pack_root / "assets" / pack_source["sha256"]
    osm_bytes = osm_path.read_bytes()
    objects_bytes = objects_path.read_bytes()

    generator_path = ROOT / "frontend/scripts/city_environment_source.py"
    spec = importlib.util.spec_from_file_location("_city_environment_source_publication", generator_path)
    if spec is None or spec.loader is None:
        raise PublicationError("could not load the source environment generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    derivative = module.extract(osm_bytes, objects_bytes)
    content = module.canonical_bytes(derivative)
    source = _mapping(derivative.get("source"), "generated environment source identity")
    if source.get("osmSha256") != pack_source["sha256"] or (
        source.get("objectsSha256") != expected_objects
    ):
        raise PublicationError("generated environment source is not bound to the region")
    evidence = {
        "generator": {
            "path": generator_path.relative_to(ROOT).as_posix(),
            **measure_file(generator_path).as_dict(),
        },
        "objects": {
            "path": objects_path.relative_to(region.staging_root).as_posix(),
            **objects_measurement.as_dict(),
        },
        "source_osm": {
            "asset": f"assets/{pack_source['sha256']}",
            **measure_file(osm_path).as_dict(),
        },
        "inspection": derivative.get("inspection"),
        "output": FileMeasurement(_sha256(content), len(content)).as_dict(),
        "classification_basis": "source OSM tags through city_environment_source.py; no inferred classes",
    }
    return content, evidence


def _asset_ref(url: str, path: Path) -> dict[str, Any]:
    return {"url": url, **measure_file(path).as_dict()}


def _asset_ref_bytes(url: str, content: bytes) -> dict[str, Any]:
    return {"url": url, "sha256": _sha256(content), "size_bytes": len(content)}


def _scene_and_targets(
    region: ValidatedRegion,
    *, publication_slug: str,
    scene_url: str,
    scene_name: str,
    environment_objects: Path | None,
    runtime_revision: str | None,
) -> tuple[bytes, tuple[Target, ...], dict[str, Any] | None, dict[str, str]]:
    if not SLUG_RE.fullmatch(publication_slug):
        raise PublicationError("publication slug must be lowercase hyphen-separated words")
    _public_parts(scene_url)
    if not scene_url.startswith(f"/city-presentation/{publication_slug}") or not scene_url.endswith(".json"):
        raise PublicationError(
            "scene URL must be a city-presentation JSON name starting with the publication slug"
        )
    if not scene_name.strip():
        raise PublicationError("scene name must not be empty")
    region_id = region.manifest["region_id"]
    pack_url = f"/osm2world/packs/{region_id}/"
    if runtime_revision is not None and not SLUG_RE.fullmatch(runtime_revision):
        raise PublicationError("runtime revision must be lowercase hyphen-separated words")
    runtime_slug = region_id if runtime_revision is None else f"{region_id}-{runtime_revision}"
    runtime_url = f"/building-renders/{runtime_slug}/"
    urls = {
        "road": f"/city-presentation/{publication_slug}-road-v3.json",
        "effective_fixtures": f"/city-presentation/{publication_slug}-effective-fixtures-v1.json",
        "traffic": f"/city-presentation/{publication_slug}-traffic-v2.json",
        "flight": f"/city-presentation/{publication_slug}-planned-flight-v2.json",
        "environment": f"/city-presentation/{publication_slug}-source-ground-cover-v1.json",
        "scene": scene_url,
        "mesh_pack": pack_url,
        "building_runtime": runtime_url,
    }
    if len(set(urls.values())) != len(urls):
        raise PublicationError("publication target URLs collide")

    road_ref = _asset_ref(urls["road"], region.road_path)
    fixtures_ref = _asset_ref(urls["effective_fixtures"], region.fixtures_path)
    traffic_ref = _asset_ref(urls["traffic"], region.traffic_path)
    flight_ref = _asset_ref(urls["flight"], region.flight_path)
    renders = _mapping(region.manifest["render_assets"], "render assets")
    pack_ref = _mapping(renders["mesh_pack"], "pack ref")
    runtime_ref = _mapping(renders["building_runtime"], "runtime ref")
    source = _mapping(region.manifest["source_context"], "source refs")
    signal_ref = _mapping(
        _mapping(region.fixtures["models"], "fixture models")["signal"], "source signal model"
    )

    environment_content: bytes | None = None
    environment_evidence: dict[str, Any] | None = None
    environment_ref: dict[str, Any] | None = None
    if environment_objects is not None:
        environment_content, environment_evidence = _environment_bytes(region, environment_objects)
        environment_ref = _asset_ref_bytes(urls["environment"], environment_content)

    scene: dict[str, Any] = {
        "building_render": {
            "base_url": runtime_url,
            "manifest": {
                "sha256": runtime_ref["sha256"],
                "size_bytes": runtime_ref["size_bytes"],
            },
            "source_context": region.runtime_source_context.as_dict(),
            "source_scene_id": region_id,
        },
        "initial_mood": "day",
        "mesh_pack": {
            "base_url": pack_url,
            "manifest": {"sha256": pack_ref["sha256"], "size_bytes": pack_ref["size_bytes"]},
            "road_surface_texture": region.road["source_asphalt_texture"],
        },
        "name": scene_name,
        "road_assets": {
            "displayed_surface_sha256": renders["road"]["displayed_surface_sha256"],
            "effective_fixtures": fixtures_ref,
            "flight": flight_ref,
            "mesh_pack_manifest_sha256": pack_ref["sha256"],
            "mesh_pack_source_sha256": region.pack["source"]["sha256"],
            "road": road_ref,
            "schema_version": ROAD_ASSETS_SCHEMA,
            "source_network_sha256": source["network"]["sha256"],
            "source_osm_sha256": source["source_osm"]["sha256"],
            "source_scene_id": region_id,
            "traffic": traffic_ref,
        },
        "schema_version": SCENE_SCHEMA,
        "traffic_signal_model": {
            "url": signal_ref["url"],
            "sha256": signal_ref["sha256"],
            "size_bytes": signal_ref["size_bytes"],
        },
    }
    if environment_ref is not None:
        scene["environment_source"] = environment_ref
    scene_content = _json_bytes(scene)

    targets = [
        Target(pack_url, source_path=region.pack_root),
        Target(runtime_url, source_path=region.runtime_root),
        Target(urls["road"], source_path=region.road_path),
        Target(urls["effective_fixtures"], source_path=region.fixtures_path),
        Target(urls["traffic"], source_path=region.traffic_path),
        Target(urls["flight"], source_path=region.flight_path),
    ]
    if environment_content is not None:
        targets.append(Target(urls["environment"], content=environment_content))
    targets.append(Target(scene_url, content=scene_content))
    return scene_content, tuple(targets), environment_evidence, urls


def _target_measurement(target: Target) -> FileMeasurement | TreeMeasurement:
    if target.source_path is not None:
        return measure_tree(target.source_path) if target.is_tree else measure_file(target.source_path)
    assert target.content is not None
    return FileMeasurement(_sha256(target.content), len(target.content))


def _target_path(root: Path, target: Target) -> Path:
    return _public_path(root, target.url, directory=target.is_tree)


def _matches_target(path: Path, target: Target) -> bool:
    expected = _target_measurement(target)
    if isinstance(expected, TreeMeasurement):
        return path.is_dir() and not path.is_symlink() and measure_tree(path) == expected
    return path.is_file() and not path.is_symlink() and measure_file(path) == expected


def _preflight_targets(root: Path, targets: Iterable[Target], label: str) -> None:
    for target in targets:
        path = _target_path(root, target)
        if path.exists() or path.is_symlink():
            if not _matches_target(path, target):
                raise PublicationError(
                    f"{label} collision at {path}; existing bytes do not match the verified region"
                )


def _write_file_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise PublicationError(f"target appeared during publication: {path}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_tree_exclusive(path: Path, source: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent))
    temporary.rmdir()
    try:
        shutil.copytree(source, temporary, copy_function=shutil.copyfile)
        try:
            temporary.rename(path)
        except FileExistsError as exc:
            raise PublicationError(f"target appeared during publication: {path}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _write_targets(root: Path, targets: Iterable[Target]) -> None:
    for target in targets:
        path = _target_path(root, target)
        if path.exists():
            continue
        if target.is_tree:
            assert target.source_path is not None
            _write_tree_exclusive(path, target.source_path)
        else:
            assert target.source_path is not None or target.content is not None
            content = (
                target.content
                if target.content is not None
                else target.source_path.read_bytes()  # type: ignore[union-attr]
            )
            _write_file_exclusive(path, content)


def _receipt(
    region: ValidatedRegion,
    *, mode: str,
    scene_content: bytes,
    targets: tuple[Target, ...],
    urls: Mapping[str, str],
    environment_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    target_rows = []
    for target in targets:
        measurement = _target_measurement(target)
        target_rows.append(
            {
                "kind": "tree" if isinstance(measurement, TreeMeasurement) else "file",
                "url": target.url,
                **measurement.as_dict(),
            }
        )
    source_context_bytes = _json_bytes(region.rendered_source_context)
    return {
        "schema_version": PUBLICATION_SCHEMA,
        "artifact_class": "local-internal-research-engineering-preview-publication",
        "formal_run_evidence": False,
        "mode": mode,
        "status": "candidate-byte-verified" if mode == "stage-only" else "published-byte-verified",
        "region_id": region.manifest["region_id"],
        "staging_manifest": {
            "path": region.manifest_path.relative_to(ROOT).as_posix()
            if region.manifest_path.is_relative_to(ROOT) else str(region.manifest_path),
            **region.manifest_measurement.as_dict(),
            "state": region.manifest["state"],
        },
        "verified_staged_references": list(region.references),
        "verified_trees": {
            "mesh_pack": region.pack_tree.as_dict(),
            "building_runtime": region.runtime_tree.as_dict(),
        },
        "pass_evidence": list(region.pass_evidence),
        "shared_rendered_source_context": {
            "canonical_json_sha256": _sha256(source_context_bytes),
            "scene_id": region.rendered_source_context["scene_id"],
        },
        "runtime_source_context": region.runtime_source_context.as_dict(),
        "external_local_dependencies": list(region.external_dependencies),
        "environment_source": environment_evidence,
        "targets": target_rows,
        "scene": {"url": urls["scene"], "sha256": _sha256(scene_content), "size_bytes": len(scene_content)},
        "asset_authorization": region.manifest["asset_authorization"],
        "publication_guards": {
            "existing_target_policy": "exact bytes/tree or fail; never overwrite",
            "mesh_pack_url": urls["mesh_pack"],
            "building_runtime_url": urls["building_runtime"],
            "external_redistribution_authorized": False,
        },
    }


def prepare_publication(
    *,
    staging_manifest: Path,
    candidate_root: Path,
    public_root: Path,
    publication_slug: str,
    scene_url: str,
    scene_name: str,
    environment_objects: Path | None,
    publish: bool,
    runtime_revision: str | None = None,
) -> dict[str, Any]:
    public_root = public_root.resolve()
    if not public_root.is_dir() or public_root.is_symlink():
        raise PublicationError(f"public root must be an existing regular directory: {public_root}")
    candidate_root = candidate_root.resolve()
    candidate_public = candidate_root / "public"
    if (
        candidate_root == public_root
        or candidate_root.is_relative_to(public_root)
        or public_root.is_relative_to(candidate_root)
    ):
        raise PublicationError("candidate root must be outside the public tree")
    region = validate_region(staging_manifest, public_root)
    if candidate_root.is_relative_to(region.staging_root):
        # The retained C publication candidate is a sibling of second-region,
        # not a child of a copied source tree.  A child could recursively copy itself.
        for source_root in (region.pack_root, region.runtime_root):
            if candidate_root.is_relative_to(source_root):
                raise PublicationError("candidate root must not be inside a source tree")
    objects = None if environment_objects is None else environment_objects.resolve()
    scene_content, targets, environment_evidence, urls = _scene_and_targets(
        region,
        publication_slug=publication_slug,
        scene_url=scene_url,
        scene_name=scene_name,
        environment_objects=objects,
        runtime_revision=runtime_revision,
    )
    _preflight_targets(candidate_public, targets, "candidate")
    _preflight_targets(public_root, targets, "public")
    receipt = _receipt(
        region,
        mode="publish" if publish else "stage-only",
        scene_content=scene_content,
        targets=targets,
        urls=urls,
        environment_evidence=environment_evidence,
    )
    receipt_path = candidate_root / ("publish-receipt.json" if publish else "stage-receipt.json")
    receipt_target = Target(
        f"/{receipt_path.name}",
        content=_json_bytes(receipt),
    )
    _preflight_targets(candidate_root, (receipt_target,), "candidate receipt")

    _write_targets(candidate_public, targets)
    if publish:
        _write_targets(public_root, targets)
        _preflight_targets(public_root, targets, "published")
    _write_targets(candidate_root, (receipt_target,))
    _preflight_targets(candidate_public, targets, "final candidate")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-manifest", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--publication-slug", required=True)
    parser.add_argument("--scene-url", required=True)
    parser.add_argument("--scene-name", required=True)
    parser.add_argument("--runtime-revision", help="Explicit immutable runtime URL suffix; retains the source scene identity.")
    parser.add_argument(
        "--environment-objects",
        type=Path,
        help="Exact compiler objects.json used to build a source-tag environment derivative.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Copy the verified candidate into --public-root. Omit for private staging only.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt = prepare_publication(
            staging_manifest=args.staging_manifest,
            candidate_root=args.candidate_root,
            public_root=args.public_root,
            publication_slug=args.publication_slug,
            scene_url=args.scene_url,
            scene_name=args.scene_name,
            environment_objects=args.environment_objects,
            publish=args.publish,
            runtime_revision=args.runtime_revision,
        )
    except (OSError, PublicationError) as exc:
        print(f"city region publication failed: {exc}", file=sys.stderr)
        return 2
    print(_json_bytes({
        "mode": receipt["mode"],
        "region_id": receipt["region_id"],
        "scene": receipt["scene"],
        "status": receipt["status"],
    }).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
