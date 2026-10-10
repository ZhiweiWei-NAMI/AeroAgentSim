"""Focused module tests for the building-render fragment -> catalog merge stage.

``aero_bench.world.building_render_merge`` validates the strict, accepted
414-entry Shanghai fragment against the real checked-in compiled scene and
merges it into an authored ``UrbanWorldCatalog`` so
``author_urban_world_package`` stages the fragment's exact bytes.

All positive paths run on the real checked-in inputs (fragment, 414 real GLBs,
compiled Shanghai scene). Negative controls mutate real bytes or real documents
in a temp directory and always assert a targeted rejection -- never a skip.
Catalog geoid/terrain/weather/license values here are clearly test-only
fixtures (as in ``test_scene_authoring``); the production demonstration of
missing inputs lives in ``validation/building-package-integration-mimo-20260929``.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import FileRef
from aero_bench.world.building_render_merge import (
    BuildingRenderMergeError,
    assess_authoring_prerequisites,
    merge_building_render_fragment,
    validate_building_render_fragment,
    verify_staged_render_bytes,
)
from aero_bench.world.contracts import WorldPackage
from aero_bench.world.scene_authoring import (
    UrbanWorldAuthoringRequest,
    author_urban_world_package,
)
from aero_bench.world.scene_compiler import SceneOrigin

REPO = Path(__file__).resolve().parents[2]
SCALEOUT = REPO / "validation/building-render-scaleout-mimo-20260929"
FRAGMENT = SCALEOUT / "building-render-catalog-fragment.json"
SCENE = REPO / "validation/scene-compiler-shanghai-huangpu-east-v1"

# Published digests of the accepted scale-out run (independent review re-derived them).
PUBLISHED_FRAGMENT_SHA256 = (
    "3cf7cc30939c8553e8d5d67fe5345029bdc44835cb785d81fe986c223996c449"
)
PUBLISHED_COMBINED_SHA256 = (
    "ab6c82860157928e85f5a26b6baec1144b98915901c383873aaecf3acfbf256a"
)
PUBLISHED_OBJECTS_SHA256 = (
    "c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc"
)

RENDER_COUNT = 414
RENDER_TOTAL_BYTES = 101_076_596


# --------------------------------------------------------------------------
# real-input helpers
# --------------------------------------------------------------------------


def _fragment_document() -> dict[str, object]:
    return json.loads(FRAGMENT.read_text(encoding="utf-8"))


def _building_ids(document: dict[str, object]) -> list[str]:
    return [entry["object_id"] for entry in document["building_render"]]  # type: ignore[index]


def _entry(document: dict[str, object], object_id: str) -> dict[str, object]:
    for entry in document["building_render"]:  # type: ignore[union-attr]
        if entry["object_id"] == object_id:
            return entry
    raise AssertionError(object_id)


def _write_fragment(directory: Path, document: dict[str, object]) -> Path:
    """Write a fragment copy whose ``file`` values are absolute real paths.

    Relocating the document into a temp directory must not change which bytes
    are validated, so every relative source path is pinned to the real
    scale-out asset directory.
    """

    for entry in document["building_render"]:  # type: ignore[union-attr]
        source = Path(entry["file"])
        if not source.is_absolute():
            entry["file"] = str(SCALEOUT / source)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fragment.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_glb(data: bytes) -> tuple[dict[str, object], bytes, bytes]:
    """Independent minimal GLB reader (deliberately not the module's parser)."""

    magic, version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and version == 2 and length == len(data)
    offset = 12
    document: dict[str, object] | None = None
    binary = b""
    json_raw = b""
    while offset < len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8 : offset + 8 + chunk_length]
        if chunk_type == 0x4E4F534A:
            json_raw = chunk
            document = json.loads(chunk)
        elif chunk_type == 0x004E4942:
            binary = chunk
        offset += 8 + chunk_length
    assert document is not None
    return document, json_raw, binary


def _rebuild_glb(document: dict[str, object], binary: bytes) -> bytes:
    json_raw = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_raw += b" " * ((-len(json_raw)) % 4)
    binary += b"\x00" * ((-len(binary)) % 4)
    total = 12 + 8 + len(json_raw) + 8 + len(binary)
    out = struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(json_raw), 0x4E4F534A) + json_raw
    out += struct.pack("<II", len(binary), 0x004E4942) + binary
    return out


def _glb_source(document: dict[str, object], object_id: str) -> Path:
    entry = _entry(document, object_id)
    return SCALEOUT / Path(entry["file"])


def _install_glb(
    tmp_path: Path,
    fragment_path: Path,
    document: dict[str, object],
    object_id: str,
    payload: bytes,
) -> None:
    """Point one entry at ``payload`` and repin its glb_digests record."""

    target = tmp_path / f"mutated-{object_id.replace('/', '_')}.glb"
    target.write_bytes(payload)
    _entry(document, object_id)["file"] = str(target)
    document["glb_digests"][object_id] = {  # type: ignore[index]
        "sha256": _sha256(payload),
        "bytes": len(payload),
    }
    fragment_path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def _flip_bin_byte(data: bytes) -> bytes:
    """Flip one byte inside the BIN chunk (image payload), structure intact."""

    mutable = bytearray(data)
    mutable[-1] ^= 0x01
    return bytes(mutable)


def _set_stale_objects_sha(data: bytes, stale_sha: str) -> bytes:
    document, _json_raw, binary = _read_glb(data)
    asset = document["asset"]  # type: ignore[index]
    asset["extras"]["scene"]["objects_json_sha256"] = stale_sha  # type: ignore[index]
    return _rebuild_glb(document, binary)


def _shift_positions_y(data: bytes, delta: float) -> bytes:
    """Shift every POSITION vertex up by ``delta`` and repin accessor min/max.

    Independent of the module under test, so the envelope gate has to catch a
    genuinely inconsistent render rather than a signature of its own parser.
    """

    document, _json_raw, binary = _read_glb(data)
    mutable = bytearray(binary)
    accessors = document["accessors"]  # type: ignore[index]
    views = document["bufferViews"]  # type: ignore[index]
    for mesh in document["meshes"]:  # type: ignore[index]
        for primitive in mesh["primitives"]:
            accessor = accessors[primitive["attributes"]["POSITION"]]
            view = views[accessor["bufferView"]]
            start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            stride = view.get("byteStride") or 12
            minimum = [float("inf")] * 3
            maximum = [float("-inf")] * 3
            for vertex in range(accessor["count"]):
                position = start + vertex * stride
                x, y, z = struct.unpack_from("<3f", mutable, position)
                y += delta
                struct.pack_into("<3f", mutable, position, x, y, z)
                for axis, value in enumerate((x, y, z)):
                    minimum[axis] = min(minimum[axis], value)
                    maximum[axis] = max(maximum[axis], value)
            accessor["min"] = minimum
            accessor["max"] = maximum
    return _rebuild_glb(document, bytes(mutable))


# --------------------------------------------------------------------------
# test-only authored catalog (fixture geoid/terrain/weather/license, as in
# tests/world/test_scene_authoring.py -- never a production input)
# --------------------------------------------------------------------------


def _origin() -> SceneOrigin:
    manifest = json.loads((SCENE / "manifest.json").read_text(encoding="utf-8"))
    origin = manifest["origin"]
    return SceneOrigin(
        latitude_deg=origin["latitude_deg"],
        longitude_deg=origin["longitude_deg"],
        ellipsoid_height_m=origin["ellipsoid_height_m"],
        geoid_undulation_m=origin["geoid_undulation_m"],
        amsl_m=origin["amsl_m"],
    )


def _write_base_catalog(
    directory: Path, *, license_path: str = "licenses/cc-by-4.0.txt"
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "LICENSE.txt").write_text(
        "CC-BY-4.0 fixture license\n", encoding="utf-8"
    )
    (directory / "geoid.bin").write_bytes(b"geoid-grid")
    (directory / "terrain.tif").write_bytes(b"terrain-grid")
    objects = json.loads((SCENE / "metadata/objects.json").read_text(encoding="utf-8"))
    document = {
        "schema_version": "aero-bench.urban-world-authoring/v1",
        "license_id": "CC-BY-4.0",
        "license_path": license_path,
        "license_file": "LICENSE.txt",
        "assets": [
            {
                "asset_id": "geoid.cn",
                "path": "world/geoid/grid.bin",
                "file": "geoid.bin",
                "asset_role": "geoid_model",
                "media_type": "application/octet-stream",
                "source_frame": "raster_pixel",
                "units": [{"quantity": "height", "unit": "m"}],
                "precision": [
                    {
                        "quantity": "height",
                        "kind": "absolute_tolerance",
                        "unit": "m",
                        "exact_bytes": None,
                        "absolute_tolerance": 0.05,
                    }
                ],
                "provenance": {
                    "source_kind": "bundled_offline",
                    "recorded_by": "building-merge-test",
                    "source_dataset": "fixture.geoid",
                    "source_version": "2026.09.29",
                    "runtime_download": False,
                },
            },
            {
                "asset_id": "terrain.heights",
                "path": "world/terrain/heights.tif",
                "file": "terrain.tif",
                "asset_role": "terrain_model",
                "media_type": "image/tiff",
                "source_frame": "raster_pixel",
                "units": [{"quantity": "height", "unit": "m"}],
                "precision": [
                    {
                        "quantity": "height",
                        "kind": "absolute_tolerance",
                        "unit": "m",
                        "exact_bytes": None,
                        "absolute_tolerance": 0.2,
                    }
                ],
                "provenance": {
                    "source_kind": "bundled_offline",
                    "recorded_by": "building-merge-test",
                    "source_dataset": "fixture.terrain",
                    "source_version": "2026.09.29",
                    "runtime_download": False,
                },
            },
        ],
        "weather": [
            {
                "sample_id": "weather.day",
                "mode": "deterministic_constant",
                "wind": {"east_mps": 1.0, "north_mps": 0.5, "up_mps": 0.0},
                "visibility_m": 10_000.0,
                "precipitation": "none",
                "precipitation_rate_mm_per_h": 0.0,
                "temperature_c": 20.0,
                "pressure_pa": 101_325.0,
            }
        ],
        "road_width_m": [
            {"object_id": road["object_id"], "width_m": 7.0}
            for road in objects["roads"]
        ],
    }
    path = directory / "catalog.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _validate(
    tmp_path: Path,
    document: dict[str, object] | None = None,
) -> None:
    fragment_path = (
        FRAGMENT if document is None else _write_fragment(tmp_path, document)
    )
    validate_building_render_fragment(fragment_path=fragment_path, scene_root=SCENE)


def _expect(tmp_path: Path, document: dict[str, object], match: str) -> None:
    with pytest.raises(BuildingRenderMergeError, match=match):
        _validate(tmp_path, document)


# --------------------------------------------------------------------------
# positive: the real checked-in Shanghai inputs
# --------------------------------------------------------------------------


def test_real_fragment_validates_against_real_scene() -> None:
    result = validate_building_render_fragment(fragment_path=FRAGMENT, scene_root=SCENE)
    evidence = result.evidence
    assert len(result.entries) == len(result.staged) == RENDER_COUNT
    assert evidence["fragment"]["sha256"] == PUBLISHED_FRAGMENT_SHA256
    assert evidence["fragment"]["entry_count"] == RENDER_COUNT
    assert evidence["fragment"]["digest_count"] == RENDER_COUNT
    assert evidence["scene"]["objects_json_sha256"] == PUBLISHED_OBJECTS_SHA256
    assert evidence["scene"]["building_count"] == RENDER_COUNT
    assert evidence["scene"]["road_count"] == 289
    assert evidence["staged"]["total_bytes"] == RENDER_TOTAL_BYTES
    # Cross-check against the accepted scale-out run's published combined digest.
    assert evidence["staged"]["combined_sha256"] == PUBLISHED_COMBINED_SHA256
    assert set(evidence["checks"]) >= {
        "building_id_set_equality",
        "geometry_envelope",
        "glb_source_scene_identity",
        "path_aliasing",
        "sha256_and_size_match",
    }
    # The staged plan pins exactly the real bytes on disk.
    sample = result.staged[0]
    assert sample.sha256 == _sha256(sample.source.read_bytes())
    assert all(entry.source_frame == "asset_local" for entry in result.entries)
    assert all(entry.media_type == "model/gltf-binary" for entry in result.entries)


def test_merge_authorizes_package_with_exact_staged_bytes(tmp_path: Path) -> None:
    catalog = _write_base_catalog(tmp_path / "catalog")
    merge = merge_building_render_fragment(
        fragment_path=FRAGMENT,
        scene_root=SCENE,
        catalog_path=catalog,
        output_catalog_path=tmp_path / "merged.json",
    )
    assert merge.validated.evidence["fragment"]["sha256"] == PUBLISHED_FRAGMENT_SHA256
    assert merge.evidence["catalog"]["merged_building_render_count"] == RENDER_COUNT

    # Prerequisites are all satisfied by this fixture catalog: the tool reports
    # ready instead of inventing anything.
    report = assess_authoring_prerequisites(
        scene_root=SCENE,
        catalog_path=merge.output_catalog_path,
        validated_fragment=merge.validated,
    )
    assert report["ready"] is True, report["blocking"]

    result = author_urban_world_package(
        UrbanWorldAuthoringRequest(
            scene_root=SCENE,
            output_root=tmp_path / "bundle",
            world_id="world.shanghai-huangpu-east",
            origin=_origin(),
            catalog_path=merge.output_catalog_path,
        )
    )
    assert result.building_count == RENDER_COUNT
    assert result.entity_count == RENDER_COUNT
    assert result.road_count == 289

    reader = BundleReader(result.bundle_root)
    world = WorldPackage.model_validate(
        json.loads(
            reader.resolve_file(
                FileRef(path=result.package_path, sha256=result.package_ref.sha256)
            ).read_bytes()
        )
    )
    assert world.world_digest == result.world_digest
    render_assets = [
        asset for asset in world.assets if asset.asset_role == "building_render"
    ]
    assert len(render_assets) == RENDER_COUNT
    for asset in render_assets:
        assert asset.media_type == "model/gltf-binary"
        assert asset.source_frame == "asset_local"
        assert asset.artifact.selector.startswith("world/building-render/")
        assert asset.artifact.selector.endswith(".glb")

    # Exact bytes: every staged selector re-hashes to the pinned fragment bytes,
    # and a spot check equals the on-disk scale-out asset.
    assert (
        verify_staged_render_bytes(result.bundle_root, merge.validated.staged)
        == RENDER_COUNT
    )
    sample = merge.validated.staged[0]
    staged = reader.resolve_file(FileRef(path=sample.selector, sha256=sample.sha256))
    assert staged.read_bytes() == sample.source.read_bytes()

    # Merge output is byte-deterministic: a second run writes identical bytes.
    second = merge_building_render_fragment(
        fragment_path=FRAGMENT,
        scene_root=SCENE,
        catalog_path=catalog,
        output_catalog_path=tmp_path / "merged-again.json",
    )
    assert second.output_catalog_sha256 == merge.output_catalog_sha256


def test_package_identity_tracks_real_render_bytes(tmp_path: Path) -> None:
    """Resolved-package immutability + Run ID semantics: world identity must
    change when the pinned render bytes change, and reproduce when they do not."""

    catalog = _write_base_catalog(tmp_path / "catalog")
    clean = merge_building_render_fragment(
        fragment_path=FRAGMENT,
        scene_root=SCENE,
        catalog_path=catalog,
        output_catalog_path=tmp_path / "merged-clean.json",
    )
    clean_result = author_urban_world_package(
        UrbanWorldAuthoringRequest(
            scene_root=SCENE,
            output_root=tmp_path / "bundle-clean",
            world_id="world.shanghai-huangpu-east",
            origin=_origin(),
            catalog_path=clean.output_catalog_path,
        )
    )

    # Flip one byte of one real GLB (inside the embedded image payload) and
    # repin its digest: the fragment stays structurally valid, so only the
    # pinned bytes -- and therefore the package identity -- may change.
    document = _fragment_document()
    object_id = min(_building_ids(document))
    original = _glb_source(document, object_id).read_bytes()
    fragment_path = _write_fragment(tmp_path / "tampered", document)
    _install_glb(
        tmp_path / "tampered",
        fragment_path,
        document,
        object_id,
        _flip_bin_byte(original),
    )
    tampered = merge_building_render_fragment(
        fragment_path=fragment_path,
        scene_root=SCENE,
        catalog_path=catalog,
        output_catalog_path=tmp_path / "merged-tampered.json",
    )
    assert tampered.output_catalog_sha256 != clean.output_catalog_sha256
    tampered_result = author_urban_world_package(
        UrbanWorldAuthoringRequest(
            scene_root=SCENE,
            output_root=tmp_path / "bundle-tampered",
            world_id="world.shanghai-huangpu-east",
            origin=_origin(),
            catalog_path=tampered.output_catalog_path,
        )
    )
    assert tampered_result.world_digest != clean_result.world_digest
    assert tampered_result.asset_digest != clean_result.asset_digest
    assert tampered_result.package_ref.sha256 != clean_result.package_ref.sha256
    # Both packages resolve immutably through the strict loader.
    for result in (clean_result, tampered_result):
        reader = BundleReader(result.bundle_root)
        world = WorldPackage.model_validate(
            json.loads(
                reader.resolve_file(
                    FileRef(path=result.package_path, sha256=result.package_ref.sha256)
                ).read_bytes()
            )
        )
        assert world.world_digest == result.world_digest


# --------------------------------------------------------------------------
# negative controls: every rejection class, asserted fail-closed
# --------------------------------------------------------------------------


def test_missing_building_id_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    dropped = document["building_render"].pop()  # type: ignore[union-attr]
    # Keep the fragment's own digest coverage consistent so the rejection comes
    # from the 1:1 scene comparison, not from an earlier internal check.
    del document["glb_digests"][dropped["object_id"]]  # type: ignore[index]
    _expect(
        tmp_path,
        document,
        r"have no building_render fragment entry; missing object_ids",
    )


def test_extra_building_id_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    clone = dict(document["building_render"][0])  # type: ignore[index]
    clone["object_id"] = "building.way.999999999.component.0"
    clone["path"] = "world/building-render/building.way.999999999.component.0.glb"
    document["building_render"].append(clone)  # type: ignore[union-attr]
    document["glb_digests"][clone["object_id"]] = {  # type: ignore[index]
        "sha256": document["glb_digests"][  # type: ignore[index]
            document["building_render"][0]["object_id"]  # type: ignore[index]
        ]["sha256"],
        "bytes": 1,
    }
    _expect(
        tmp_path, document, r"not buildings of the compiled scene; extra object_ids"
    )


def test_duplicate_object_id_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    document["building_render"].append(  # type: ignore[union-attr]
        dict(document["building_render"][0])  # type: ignore[index]
    )
    _expect(tmp_path, document, r"duplicate building_render object_id")


def test_duplicate_selector_aliasing_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    entries = document["building_render"]  # type: ignore[assignment]
    entries[1]["path"] = entries[0]["path"]
    _expect(tmp_path, document, r"path aliasing: selector .* claimed by more than one")


def test_selector_prefix_shadowing_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    entries = document["building_render"]  # type: ignore[assignment]
    entries[1]["path"] = entries[0]["path"] + "/nested.glb"
    _expect(
        tmp_path, document, r"path aliasing: selector .* shadows .* directory prefix"
    )


def test_gltf_labelled_wgs84_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    document["building_render"][0]["source_frame"] = "WGS84"  # type: ignore[index]
    _expect(tmp_path, document, r"violates the strict contract|WGS84")


def test_non_gltf_render_wrong_source_frame_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    entry = document["building_render"][0]  # type: ignore[index]
    entry["media_type"] = "application/json"
    entry["source_frame"] = "WGS84"
    _expect(tmp_path, document, r"wrong source frame")


def test_wrong_media_type_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    document["building_render"][0]["media_type"] = "application/octet-stream"  # type: ignore[index]
    _expect(tmp_path, document, r"wrong media_type")


def test_sha256_mismatch_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    object_id = min(_building_ids(document))
    record = document["glb_digests"][object_id]  # type: ignore[index]
    record["sha256"] = "0" * 63 + "1"
    _expect(tmp_path, document, r"sha256 mismatch")


def test_size_mismatch_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    object_id = min(_building_ids(document))
    record = document["glb_digests"][object_id]  # type: ignore[index]
    record["bytes"] = int(record["bytes"]) + 1
    _expect(tmp_path, document, r"size mismatch")


def test_stale_source_scene_identity_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    object_id = min(_building_ids(document))
    original = _glb_source(document, object_id).read_bytes()
    payload = _set_stale_objects_sha(original, "f" * 64)
    fragment_path = _write_fragment(tmp_path, document)
    _install_glb(tmp_path, fragment_path, document, object_id, payload)
    with pytest.raises(BuildingRenderMergeError, match=r"stale source scene identity"):
        validate_building_render_fragment(fragment_path=fragment_path, scene_root=SCENE)


def test_geometry_envelope_violation_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    object_id = min(_building_ids(document))
    original = _glb_source(document, object_id).read_bytes()
    payload = _shift_positions_y(original, 0.05)  # 5 cm taller than the scene
    fragment_path = _write_fragment(tmp_path, document)
    _install_glb(tmp_path, fragment_path, document, object_id, payload)
    with pytest.raises(BuildingRenderMergeError, match=r"geometry envelope violation"):
        validate_building_render_fragment(fragment_path=fragment_path, scene_root=SCENE)


def test_missing_source_file_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    object_id = min(_building_ids(document))
    fragment_path = _write_fragment(tmp_path, document)
    _entry(document, object_id)["file"] = str(tmp_path / "does-not-exist.glb")
    fragment_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(
        BuildingRenderMergeError, match=r"source file for .* does not exist"
    ):
        validate_building_render_fragment(fragment_path=fragment_path, scene_root=SCENE)


def test_tampered_scene_fails_closed(tmp_path: Path) -> None:
    import shutil

    scene_copy = tmp_path / "scene"
    shutil.copytree(SCENE, scene_copy)
    objects_path = scene_copy / "metadata/objects.json"
    payload = bytearray(objects_path.read_bytes())
    payload[len(payload) // 2] ^= 0x01
    objects_path.write_bytes(bytes(payload))
    with pytest.raises(
        BuildingRenderMergeError, match=r"metadata/objects.json' sha256 mismatch"
    ):
        validate_building_render_fragment(fragment_path=FRAGMENT, scene_root=scene_copy)


def test_wrong_fragment_section_fails_closed(tmp_path: Path) -> None:
    document = _fragment_document()
    document["section"] = "road_width_m"
    _expect(tmp_path, document, r"fragment section must be")


def test_duplicate_json_keys_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "fragment.json"
    path.write_text(
        '{"schema_version": "aero-bench.urban-world-authoring/v1",'
        ' "schema_version": "aero-bench.urban-world-authoring/v1"}',
        encoding="utf-8",
    )
    with pytest.raises(BuildingRenderMergeError, match=r"duplicate JSON object key"):
        validate_building_render_fragment(fragment_path=path, scene_root=SCENE)


def test_merge_refuses_to_overwrite_existing_building_render(tmp_path: Path) -> None:
    catalog = _write_base_catalog(tmp_path / "catalog")
    document = json.loads(catalog.read_text(encoding="utf-8"))
    document["building_render"] = _fragment_document()["building_render"][:1]
    catalog.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BuildingRenderMergeError, match=r"never overwrites one"):
        merge_building_render_fragment(
            fragment_path=FRAGMENT,
            scene_root=SCENE,
            catalog_path=catalog,
            output_catalog_path=tmp_path / "merged.json",
        )


def test_merge_rejects_selector_colliding_with_catalog_license(tmp_path: Path) -> None:
    catalog = _write_base_catalog(
        tmp_path / "catalog", license_path="licenses/cc-by-4.0.glb"
    )
    document = _fragment_document()
    document["building_render"][0]["path"] = "licenses/cc-by-4.0.glb"  # type: ignore[index]
    with pytest.raises(BuildingRenderMergeError, match=r"path aliasing"):
        merge_building_render_fragment(
            fragment_path=_write_fragment(tmp_path, document),
            scene_root=SCENE,
            catalog_path=catalog,
            output_catalog_path=tmp_path / "merged.json",
        )


# --------------------------------------------------------------------------
# prerequisite report: precise, and never invents terrain/license/weather
# --------------------------------------------------------------------------


def test_prerequisite_report_blocks_without_inventing_inputs(tmp_path: Path) -> None:
    validated = validate_building_render_fragment(
        fragment_path=FRAGMENT, scene_root=SCENE
    )
    report = assess_authoring_prerequisites(
        scene_root=SCENE,
        catalog_path=None,
        validated_fragment=validated,
        road_width_catalog_path=(
            REPO / "validation/road-width-catalog-mimo-20260929/road-width-catalog.json"
        ),
    )
    assert report["ready"] is False
    assert report["building_render"]["status"] == "validated"
    assert report["building_render"]["entry_count"] == RENDER_COUNT
    assert report["scene"]["building_count"] == RENDER_COUNT
    assert report["road_widths"]["fragment_input"]["status"] == "complete"  # type: ignore[index]
    requirements = {item["requirement"].lower() for item in report["requirements"]}
    assert any("weather" in item for item in requirements)
    assert any("geoid" in item for item in requirements)
    assert any("terrain" in item for item in requirements)
    assert any("license" in item for item in requirements)
    assert any("catalog" in item.lower() for item in report["blocking"])
    assert "ever generated" in report["note"]
