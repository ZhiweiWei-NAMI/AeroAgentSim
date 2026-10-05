"""Tests for the per-building render GLB prototypes and their catalog mapping.

These tests treat the checked-in artifacts under
``validation/building-render-prototype-mimo-20260929/`` as the subject:

  * the standalone validator must pass every check (GLB container, three-way
    dimension cross-check against ``objects.json``/``scene.sdf``, material
    presence, frame honesty, catalog contract);
  * archetype selection and regeneration must be byte-deterministic;
  * the catalog fragment must validate against the strict
    ``CatalogBuildingRender`` contract with ``source_frame: asset_local``
    (glTF content is asset-local; WGS84 is rejected);
  * a full ``author_urban_world_package`` run over the real 414-building
    scene -- 3 entries backed by the real prototype GLBs, the remainder by
    clearly test-only fixture bytes -- must produce a resolvable WorldPackage
    whose render assets are honestly ``asset_local`` and byte-pinned to the
    real GLB files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import FileRef
from aero_bench.world.contracts import WorldPackage
from aero_bench.world.scene_authoring import (
    CatalogBuildingRender,
    UrbanWorldAuthoringRequest,
    author_urban_world_package,
)
from tests.world.test_scene_authoring import (
    _all_road_widths,
    _building_ids,
    _geoid_asset,
    _render_entry,
    _sample_origin,
    _terrain_asset,
    _weather,
)

VALIDATION_DIR = (
    Path(__file__).resolve().parents[2]
    / "validation/building-render-prototype-mimo-20260929"
)
if str(VALIDATION_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATION_DIR))

import generate_prototypes  # noqa: E402
import validate_prototypes  # noqa: E402

RENDER_MEDIA_TYPE = "model/gltf-binary"
FRAGMENT_PATH = VALIDATION_DIR / "building-render-catalog-fragment.json"
MANIFEST_PATH = VALIDATION_DIR / "prototypes/manifest.json"


def _objects() -> dict:
    return json.loads(
        (validate_prototypes.SCENE_ROOT / "metadata/objects.json").read_text(
            encoding="utf-8"
        )
    )


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _fragment() -> dict:
    return json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))


def test_validator_passes_all_checks() -> None:
    report = validate_prototypes.run_checks()
    assert report.failures == [], "\n".join(report.failures)
    assert report.passed >= 90


def _glb_doc(path: Path) -> dict:
    data = path.read_bytes()
    json_len = int.from_bytes(data[12:16], "little")
    return json.loads(data[20 : 20 + json_len].decode("utf-8"))


def _rewrite_glb_json(source: Path, target: Path, materials: list[dict]) -> None:
    """Rewrite a GLB with replacement materials; the BIN chunk is untouched."""

    data = source.read_bytes()
    json_len = int.from_bytes(data[12:16], "little")
    doc = json.loads(data[20 : 20 + json_len].decode("utf-8"))
    doc["materials"] = materials
    raw = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    bin_chunk = data[20 + json_len :]
    out = bytearray()
    out += b"glTF"
    out += (2).to_bytes(4, "little")
    out += (12 + 8 + len(raw) + len(bin_chunk)).to_bytes(4, "little")
    out += len(raw).to_bytes(4, "little")
    out += (0x4E4F534A).to_bytes(4, "little")
    out += raw
    out += bin_chunk
    target.write_bytes(bytes(out))


def test_materials_use_spec_pbr_factors() -> None:
    """Both materials carry the two standard factors, never the invalid key."""

    for proto in _manifest()["prototypes"]:
        materials = _glb_doc(VALIDATION_DIR / proto["glb"])["materials"]
        assert len(materials) == 2, proto["object_id"]
        for material in materials:
            pbr = material["pbrMetallicRoughness"]
            assert "metallicRoughnessFactor" not in pbr, material["name"]
            assert pbr.get("metallicFactor") == 0.0, material["name"]
            assert pbr.get("roughnessFactor") == 0.5, material["name"]


def test_validator_rejects_invalid_pbr_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GLB reintroducing metallicRoughnessFactor must be flagged, and only
    for that (plus the byte-hash drift the mutation itself causes)."""

    proto_dir = tmp_path / "prototypes"
    proto_dir.mkdir(parents=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for index, proto in enumerate(manifest["prototypes"]):
        source = VALIDATION_DIR / proto["glb"]
        target = proto_dir / source.name
        if index == 0:
            bad_materials = []
            for material in _glb_doc(source)["materials"]:
                pbr = dict(material["pbrMetallicRoughness"])
                pbr.pop("metallicFactor", None)
                pbr.pop("roughnessFactor", None)
                pbr["metallicRoughnessFactor"] = [0.0, 0.5]
                bad_materials.append({**material, "pbrMetallicRoughness": pbr})
            _rewrite_glb_json(source, target, bad_materials)
            proto["glb_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            proto["glb_bytes"] = target.stat().st_size
        else:
            shutil.copy2(source, target)
    (proto_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    monkeypatch.setattr(validate_prototypes, "HERE", tmp_path)
    monkeypatch.setattr(validate_prototypes, "PROTOTYPE_DIR", proto_dir)
    report = validate_prototypes.run_checks()

    rejected = [f for f in report.failures if "non-spec" in f]
    missing = [
        f
        for f in report.failures
        if "non-spec" not in f and ("metallicFactor" in f or "roughnessFactor" in f)
    ]
    drift = [f for f in report.failures if "regeneration drift" in f]
    other = [
        f
        for f in report.failures
        if f not in rejected and f not in missing and f not in drift
    ]

    # one rejection per material of the mutated prototype
    assert len(rejected) == 2, "\n".join(report.failures)
    # both standard fields are also required, not merely the key's absence
    assert any("metallicFactor" in f for f in missing), "\n".join(missing)
    assert any("roughnessFactor" in f for f in missing), "\n".join(missing)
    # the mutation changes GLB bytes, so the regeneration cross-check may
    # legitimately report drift; nothing else may fail
    assert len(drift) <= 1, "\n".join(drift)
    assert other == [], "\n".join(other)


def test_archetype_selection_is_deterministic_and_data_derived() -> None:
    objects = _objects()
    first = generate_prototypes.select_buildings(objects["buildings"])
    second = generate_prototypes.select_buildings(list(reversed(objects["buildings"])))
    ids = lambda selection: {  # noqa: E731
        arch: building["object_id"] for arch, building in selection.items()
    }
    assert ids(first) == ids(second)
    assert ids(first) == {
        entry["archetype"]: entry["object_id"] for entry in _manifest()["prototypes"]
    }

    heights = [b["extrusion_height_m"] for b in objects["buildings"]]
    modal_height = max(set(heights), key=lambda h: (heights.count(h), -h))

    tower = first["tallest_tower"]
    assert tower["extrusion_height_m"] == max(heights)
    typical = first["typical_mid_rise"]
    assert typical["extrusion_height_m"] == modal_height
    complex_item = first["complex_footprint"]
    excluded = {tower["object_id"], typical["object_id"]}
    max_rest = max(
        len(b["footprint_enu_m"]) - 1
        for b in objects["buildings"]
        if b["object_id"] not in excluded
    )
    assert len(complex_item["footprint_enu_m"]) - 1 == max_rest


def test_regeneration_is_byte_identical(tmp_path: Path) -> None:
    result = generate_prototypes.generate(tmp_path / "out")
    for fresh, checked in zip(result["prototypes"], _manifest()["prototypes"]):
        assert fresh["glb_sha256"] == checked["glb_sha256"], checked["object_id"]
    assert (tmp_path / "out/manifest.json").read_bytes() == MANIFEST_PATH.read_bytes()


def test_catalog_fragment_is_honest_asset_local() -> None:
    entries = _fragment()["building_render"]
    assert len(entries) == 3
    manifest_ids = {p["object_id"] for p in _manifest()["prototypes"]}
    for entry in entries:
        model = CatalogBuildingRender.model_validate(entry)
        assert model.source_frame == "asset_local"
        assert model.media_type == RENDER_MEDIA_TYPE
        assert model.path == f"world/building-render/{model.object_id}.glb"
        assert model.object_id in manifest_ids
        assert (VALIDATION_DIR / model.file).is_file()

    # The honesty guard: a glTF render must never be labelled WGS84.
    mislabelled = dict(entries[0])
    mislabelled["source_frame"] = "WGS84"
    with pytest.raises(ValueError, match="asset_local|WGS84"):
        CatalogBuildingRender.model_validate(mislabelled)


def _write_full_catalog(directory: Path, real_entries: list[dict]) -> Path:
    """Catalog over the real 414-building scene.

    ``real_entries`` (the 3 prototype records) point at the checked-in GLB
    bytes; every other building gets a clearly test-only fixture render file.
    """

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "LICENSE.txt").write_text(
        "CC-BY-4.0 fixture license\n", encoding="utf-8"
    )
    (directory / "geoid.bin").write_bytes(b"geoid-grid")
    (directory / "terrain.tif").write_bytes(b"terrain-grid")

    real_by_id = {entry["object_id"]: entry for entry in real_entries}
    render_entries: list[dict] = []
    for object_id in _building_ids():
        if object_id in real_by_id:
            relative = Path(real_by_id[object_id]["file"])
            source = VALIDATION_DIR / relative
            target_dir = directory / relative.parent
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_dir / source.name)
            render_entries.append(real_by_id[object_id])
        else:
            render_entries.append(_render_entry(directory, object_id))

    document = {
        "schema_version": "aero-bench.urban-world-authoring/v1",
        "license_id": "CC-BY-4.0",
        "license_path": "licenses/cc-by-4.0.txt",
        "license_file": "LICENSE.txt",
        "assets": [_geoid_asset(), _terrain_asset()],
        "weather": [_weather()],
        "road_width_m": [
            {"object_id": object_id, "width_m": width}
            for object_id, width in _all_road_widths(7.0).items()
        ],
        "building_render": render_entries,
    }
    catalog_path = directory / "catalog.json"
    catalog_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return catalog_path


def test_authored_package_carries_real_prototypes_honestly(tmp_path: Path) -> None:
    entries = _fragment()["building_render"]
    catalog = _write_full_catalog(tmp_path / "catalog", entries)
    result = author_urban_world_package(
        UrbanWorldAuthoringRequest(
            scene_root=validate_prototypes.SCENE_ROOT,
            output_root=tmp_path / "bundle",
            world_id="world.shanghai-huangpu-east",
            origin=_sample_origin(),
            catalog_path=catalog,
        )
    )

    reader = BundleReader(result.bundle_root)
    resolved = reader.resolve_file(
        FileRef(path=result.package_path, sha256=result.package_ref.sha256)
    )
    world = WorldPackage.model_validate(json.loads(resolved.read_bytes()))
    assert len(world.buildings) == 414

    asset_by_id = {asset.artifact.artifact_id: asset for asset in world.assets}
    real_by_id = {entry["object_id"]: entry for entry in entries}

    # Every building still maps one-to-one onto a building_render asset.
    for building in world.buildings:
        assert building.render_asset_id == f"render.{building.building_id}"
        render_asset = asset_by_id[building.render_asset_id]
        assert render_asset.asset_role == "building_render"
        assert render_asset.source_frame == "asset_local"
        assert render_asset.media_type == RENDER_MEDIA_TYPE

    # The 3 real prototypes are byte-pinned to the checked-in GLB files.
    for object_id, entry in real_by_id.items():
        asset = asset_by_id[f"render.{object_id}"]
        glb_path = VALIDATION_DIR / entry["file"]
        glb_bytes = glb_path.read_bytes()
        assert asset.byte_size == len(glb_bytes)
        assert asset.artifact.sha256 == hashlib.sha256(glb_bytes).hexdigest()
        assert asset.artifact.selector == f"world/building-render/{object_id}.glb"
        staged = reader.resolve_file(
            FileRef(path=asset.artifact.selector, sha256=asset.artifact.sha256)
        )
        assert staged.read_bytes() == glb_bytes
        # and the staged bytes parse as the honest asset-local GLB
        doc, _, _ = validate_prototypes.parse_glb(glb_path)
        assert doc["asset"]["extras"]["source_frame"] == "asset_local"
        assert doc["asset"]["extras"]["object_id"] == object_id
