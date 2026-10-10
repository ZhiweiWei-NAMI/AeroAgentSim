"""Focused module-level tests for the urban-scene -> WorldPackage authoring stage.

``aero_bench.world.scene_authoring.author_urban_world_package`` is the production
bridge between a compiled ``aero-bench.urban-scene-compiler/v1`` package and the
strict frozen ``aero-bench.world/v2`` WorldPackage contract.

These tests drive the real checked-in Shanghai scene sample end to end and
assert honest, fail-closed behaviour:

  * a complete roaded renderable package is authored only when every one of the
    scene's 289 source roads has a defensible width and every one of the 414
    buildings has an explicit authored per-building render asset;
  * a missing width or a missing per-building render asset fails closed,
    enumerating the exact missing ``object_id``s -- no source road or building is
    ever silently omitted, and the whole-scene OSM XML is never passed off as a
    per-building render asset;
  * the produced package is grounded in the scene's own object-manifest fields
    (footprints, base/top ENU heights, road centrelines) and resolves through the
    real strict loader and alignment verifier;
  * identical inputs reproduce a byte-identical package;
  * a catalog change changes the produced world identity;
  * every missing, tampered, inconsistent or unsupported input fails closed
    without leaving a partial bundle behind.

All catalog values here are clearly test-only fixtures (synthetic geoid/terrain
bytes, ``render-fixture:`` render bytes); they are never real production assets.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import FileRef
from aero_bench.world.alignment import (
    GeneratorIdentity,
    alignment_manifest,
    verify_alignment,
)
from aero_bench.world.contracts import WorldPackage
from aero_bench.world.scene_authoring import (
    UrbanWorldAuthoringError,
    UrbanWorldAuthoringRequest,
    author_urban_world_package,
)
from aero_bench.world.scene_compiler import SceneOrigin

SAMPLE_SCENE = (
    Path(__file__).resolve().parents[2]
    / "validation/scene-compiler-shanghai-huangpu-east-v1"
)
SCENE_LAYER = "osm/effective.osm.json"
SCENE_BUILDING_SOURCE = "metadata/objects.json"
SCENE_BUILDING_COLLISION = "gazebo/scene.sdf"

RENDER_MEDIA_TYPE = "model/gltf-binary"


def _sample_origin() -> SceneOrigin:
    manifest = json.loads((SAMPLE_SCENE / "manifest.json").read_text(encoding="utf-8"))
    origin = manifest["origin"]
    return SceneOrigin(
        latitude_deg=origin["latitude_deg"],
        longitude_deg=origin["longitude_deg"],
        ellipsoid_height_m=origin["ellipsoid_height_m"],
        geoid_undulation_m=origin["geoid_undulation_m"],
        amsl_m=origin["amsl_m"],
    )


def _objects() -> dict[str, object]:
    return json.loads(
        (SAMPLE_SCENE / SCENE_BUILDING_SOURCE).read_text(encoding="utf-8")
    )


def _building_ids() -> list[str]:
    return [building["object_id"] for building in _objects()["buildings"]]


def _road_ids() -> list[str]:
    return [road["object_id"] for road in _objects()["roads"]]


def _all_road_widths(width: float = 4.0) -> dict[str, float]:
    return {road_id: width for road_id in _road_ids()}


def _geoid_asset() -> dict[str, object]:
    return {
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
            "recorded_by": "world-authoring",
            "source_dataset": "fixture.geoid",
            "source_version": "2026.09.29",
            "runtime_download": False,
        },
    }


def _terrain_asset() -> dict[str, object]:
    return {
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
            "recorded_by": "world-authoring",
            "source_dataset": "fixture.terrain",
            "source_version": "2026.09.29",
            "runtime_download": False,
        },
    }


def _weather() -> dict[str, object]:
    return {
        "sample_id": "weather.day",
        "mode": "deterministic_constant",
        "wind": {"east_mps": 1.0, "north_mps": 0.5, "up_mps": 0.0},
        "visibility_m": 10_000.0,
        "precipitation": "none",
        "precipitation_rate_mm_per_h": 0.0,
        "temperature_c": 20.0,
        "pressure_pa": 101_325.0,
    }


def _render_selector(object_id: str) -> str:
    return f"world/building-render/{object_id}.glb"


def _write_render_asset(directory: Path, object_id: str) -> Path:
    """Write one clearly test-only per-building render file and return its path."""

    source = directory / f"render-{object_id}.glb"
    source.write_bytes(f"render-fixture:{object_id}\n".encode("utf-8"))
    return source


def _render_entry(directory: Path, object_id: str) -> dict[str, object]:
    source = _write_render_asset(directory, object_id)
    return {
        "object_id": object_id,
        "path": _render_selector(object_id),
        "file": source.name,
        "media_type": RENDER_MEDIA_TYPE,
        # glTF content is asset-local per the glTF 2.0 coordinate convention;
        # declaring WGS84 for it is rejected by the catalog schema.
        "source_frame": "asset_local",
        "provenance": {
            "source_kind": "bundled_offline",
            "recorded_by": "world-authoring",
            "source_dataset": "fixture.building-render",
            "source_version": "2026.09.29",
            "runtime_download": False,
        },
    }


def _write_catalog(
    directory: Path,
    *,
    road_widths: dict[str, float] | None = None,
    render_object_ids: Sequence[str] | None = None,
    include_geoid: bool = True,
    include_terrain: bool = True,
    asset_path_override: str | None = None,
) -> Path:
    """Author a test-only catalog.

    ``render_object_ids`` defaults to every scene building (a complete render
    catalog); pass an explicit list to omit some per-building render assets.
    """

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "LICENSE.txt").write_text(
        "CC-BY-4.0 fixture license\n", encoding="utf-8"
    )
    (directory / "geoid.bin").write_bytes(b"geoid-grid")
    (directory / "terrain.tif").write_bytes(b"terrain-grid")
    assets: list[dict[str, object]] = []
    if include_geoid:
        assets.append(_geoid_asset())
    if include_terrain:
        assets.append(_terrain_asset())
    if asset_path_override is not None and assets:
        assets[0]["path"] = asset_path_override
    render_ids = (
        _building_ids() if render_object_ids is None else list(render_object_ids)
    )
    document = {
        "schema_version": "aero-bench.urban-world-authoring/v1",
        "license_id": "CC-BY-4.0",
        "license_path": "licenses/cc-by-4.0.txt",
        "license_file": "LICENSE.txt",
        "assets": assets,
        "weather": [_weather()],
        "road_width_m": [
            {"object_id": object_id, "width_m": width}
            for object_id, width in (road_widths or {}).items()
        ],
        "building_render": [
            _render_entry(directory, object_id) for object_id in render_ids
        ],
    }
    catalog_path = directory / "catalog.json"
    catalog_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return catalog_path


def _request(
    *,
    output: Path,
    catalog: Path,
    scene: Path = SAMPLE_SCENE,
    world_id: str = "world.shanghai-huangpu-east",
    origin: SceneOrigin | None = None,
) -> UrbanWorldAuthoringRequest:
    return UrbanWorldAuthoringRequest(
        scene_root=scene,
        output_root=output,
        world_id=world_id,
        origin=origin if origin is not None else _sample_origin(),
        catalog_path=catalog,
    )


def test_authors_complete_roaded_renderable_world_from_real_scene(
    tmp_path: Path,
) -> None:
    objects = _objects()
    catalog = _write_catalog(tmp_path / "catalog", road_widths=_all_road_widths(7.0))
    result = author_urban_world_package(
        _request(output=tmp_path / "bundle", catalog=catalog)
    )

    assert result.building_count == len(objects["buildings"]) == 414
    assert result.entity_count == 414
    assert result.road_count == len(objects["roads"]) == 289
    assert (
        result.scene_manifest_sha256
        == hashlib.sha256((SAMPLE_SCENE / "manifest.json").read_bytes()).hexdigest()
    )

    reader = BundleReader(result.bundle_root)
    resolved = reader.resolve_file(
        FileRef(path=result.package_path, sha256=result.package_ref.sha256)
    )
    world = WorldPackage.model_validate(json.loads(resolved.read_bytes()))
    assert world.world_id == "world.shanghai-huangpu-east"
    assert world.schema_version == "aero-bench.world/v2"
    assert world.world_digest == result.world_digest
    assert world.asset_digest == result.asset_digest
    # Re-serializing and re-validating reproduces the sealed digests exactly.
    assert world.model_dump(mode="json") == result.world_package.model_dump(mode="json")

    # Buildings carry the scene's own object ids, footprints, and ENU heights.
    by_id = {building.building_id: building for building in world.buildings}
    source_buildings = {item["object_id"]: item for item in objects["buildings"]}
    assert set(by_id) == set(source_buildings)
    sample_id = objects["buildings"][0]["object_id"]
    sample_source = source_buildings[sample_id]
    sample = by_id[sample_id]
    closed_ring = [(point[0], point[1]) for point in sample_source["footprint_enu_m"]]
    assert closed_ring[0] == closed_ring[-1]  # the scene ring is closed
    assert [(p.east_m, p.north_m) for p in sample.geometry.footprint_enu_m] == (
        closed_ring[:-1]
    )
    assert sample.geometry.base_altitude_m == sample_source["base_enu_up_m"]
    assert sample.geometry.top_altitude_m == sample_source["top_enu_up_m"]

    # Every building has a distinct, real per-building render asset (never the
    # whole-scene OSM XML) and a compiler-owned static entity that uses it.
    asset_by_id = {asset.artifact.artifact_id: asset for asset in world.assets}
    render_ids = {building.render_asset_id for building in world.buildings}
    assert len(render_ids) == len(world.buildings) == 414
    entities = {entity.entity_id: entity for entity in world.entities}
    for building in world.buildings:
        assert building.render_asset_id == f"render.{building.building_id}"
        render_asset = asset_by_id[building.render_asset_id]
        assert render_asset.asset_role == "building_render"
        # The fixture bytes are a glTF-labelled artifact: honestly asset_local,
        # never WGS84 (geodetic coordinates are not representable in glTF).
        assert render_asset.source_frame == "asset_local"
        assert render_asset.media_type == RENDER_MEDIA_TYPE
        assert render_asset.artifact.selector == _render_selector(building.building_id)
        entity = entities[building.entity_id]
        assert entity.kind == "static_asset"
        assert entity.state == "static"
        assert entity.authority_kind == "scenario_static"
        assert entity.provider_id is None
        assert entity.model_asset_id == building.render_asset_id

    # The whole-scene OSM XML is not referenced by any asset or building.
    assert not any(
        asset.artifact.selector.endswith("effective.osm") for asset in world.assets
    )

    # Every source road is declared with its authored width and real centreline.
    assert {road.road_id for road in world.roads} == set(_road_ids())
    source_roads = {item["object_id"]: item for item in objects["roads"]}
    for road in world.roads:
        assert road.width_m == 7.0
        assert [(p.east_m, p.north_m) for p in road.centerline_enu_m] == [
            (point[0], point[1])
            for point in source_roads[road.road_id]["centerline_enu_m"]
        ]

    # The scene layer/source/collision assets are pinned to the real scene bytes.
    layer = next(layer for layer in world.layers if layer.kind == "osm_scene")
    layer_asset = next(
        asset for asset in world.assets if asset.artifact.artifact_id == layer.asset_id
    )
    assert layer_asset.source_frame == "WGS84"
    assert layer_asset.media_type == "application/json"
    for relative, asset_id in (
        (SCENE_LAYER, layer.asset_id),
        (SCENE_BUILDING_SOURCE, "scene.objects_manifest"),
        (SCENE_BUILDING_COLLISION, "scene.gazebo_sdf"),
    ):
        asset = next(
            item for item in world.assets if item.artifact.artifact_id == asset_id
        )
        assert asset.artifact.selector == f"scene/{relative}"
        staged = reader.resolve_file(
            FileRef(path=asset.artifact.selector, sha256=asset.artifact.sha256)
        )
        assert staged.read_bytes() == (SAMPLE_SCENE / relative).read_bytes()

    # Every per-building render asset's staged bytes are the authored bytes.
    staged_render = reader.resolve_file(
        FileRef(
            path=asset_by_id[sample.render_asset_id].artifact.selector,
            sha256=asset_by_id[sample.render_asset_id].artifact.sha256,
        )
    )
    assert staged_render.read_bytes() == f"render-fixture:{sample_id}\n".encode("utf-8")


def test_real_sample_without_road_widths_fails_closed_enumerating_roads(
    tmp_path: Path,
) -> None:
    # A complete render catalog but no authored road widths: the checked-in
    # sample declares no OSM width tag on any of its 289 roads, so authoring
    # must fail closed rather than emit a 0-road (or partial) world.
    catalog = _write_catalog(tmp_path / "catalog")
    with pytest.raises(UrbanWorldAuthoringError) as excinfo:
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog)
        )
    message = str(excinfo.value)
    assert "289 of 289 compiled-scene roads have no defensible width" in message
    assert _road_ids()[0] in message
    assert not (tmp_path / "bundle").exists()
    assert not any(tmp_path.glob(".bundle.staging-*"))


def test_partial_road_widths_fail_closed_enumerating_missing_only(
    tmp_path: Path,
) -> None:
    road_ids = _road_ids()
    authored = {road_id: 5.0 for road_id in road_ids[:2]}
    catalog = _write_catalog(tmp_path / "catalog", road_widths=authored)
    with pytest.raises(UrbanWorldAuthoringError) as excinfo:
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog)
        )
    message = str(excinfo.value)
    assert "287 of 289 compiled-scene roads have no defensible width" in message
    assert road_ids[2] in message
    assert road_ids[0] not in message
    assert not (tmp_path / "bundle").exists()


def test_real_sample_without_building_renders_fails_closed_enumerating_buildings(
    tmp_path: Path,
) -> None:
    # All road widths authored, but no per-building render assets: the scene
    # emits none and the whole-scene OSM XML is not a per-building render asset,
    # so authoring must fail closed enumerating every missing building.
    catalog = _write_catalog(
        tmp_path / "catalog",
        road_widths=_all_road_widths(),
        render_object_ids=[],
    )
    with pytest.raises(UrbanWorldAuthoringError) as excinfo:
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog)
        )
    message = str(excinfo.value)
    assert "414 of 414 compiled-scene buildings have no explicit authored" in message
    assert _building_ids()[0] in message
    assert not (tmp_path / "bundle").exists()
    assert not any(tmp_path.glob(".bundle.staging-*"))


def test_partial_building_renders_fail_closed_enumerating_missing_only(
    tmp_path: Path,
) -> None:
    building_ids = _building_ids()
    catalog = _write_catalog(
        tmp_path / "catalog",
        road_widths=_all_road_widths(),
        render_object_ids=building_ids[:1],
    )
    with pytest.raises(UrbanWorldAuthoringError) as excinfo:
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog)
        )
    message = str(excinfo.value)
    assert "413 of 414 compiled-scene buildings have no explicit authored" in message
    assert building_ids[1] in message
    assert building_ids[0] not in message
    assert not (tmp_path / "bundle").exists()


def test_authoring_is_deterministic(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path / "catalog", road_widths=_all_road_widths())
    trees: list[dict[str, str]] = []
    digests: list[str] = []
    for name in ("one", "two"):
        result = author_urban_world_package(
            _request(output=tmp_path / f"bundle-{name}", catalog=catalog)
        )
        digests.append(result.world_digest)
        trees.append(
            {
                path.relative_to(result.bundle_root).as_posix(): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in result.bundle_root.rglob("*")
                if path.is_file()
            }
        )
    assert digests[0] == digests[1]
    assert trees[0] == trees[1]


def test_catalog_change_changes_world_identity(tmp_path: Path) -> None:
    road_id = _road_ids()[0]
    widths = _all_road_widths()
    narrow = author_urban_world_package(
        _request(
            output=tmp_path / "narrow",
            catalog=_write_catalog(
                tmp_path / "catalog-a",
                road_widths={**widths, road_id: 3.5},
            ),
        )
    )
    wide = author_urban_world_package(
        _request(
            output=tmp_path / "wide",
            catalog=_write_catalog(
                tmp_path / "catalog-b",
                road_widths={**widths, road_id: 7.0},
            ),
        )
    )
    assert wide.building_count == narrow.building_count
    assert wide.world_digest != narrow.world_digest
    # Road width lives in content, not the asset manifest, so the sealed asset
    # digest is unchanged while the world digest moves.
    assert wide.asset_digest == narrow.asset_digest


def test_render_asset_bytes_change_world_identity(tmp_path: Path) -> None:
    widths = _all_road_widths()
    first = _write_catalog(tmp_path / "catalog-a", road_widths=widths)
    second = _write_catalog(tmp_path / "catalog-b", road_widths=widths)
    # Corrupt one authored per-building render file's bytes; its pinned digest
    # and therefore the sealed asset digest must move.
    target = _building_ids()[0]
    (second.parent / f"render-{target}.glb").write_bytes(b"render-fixture:changed\n")
    a = author_urban_world_package(_request(output=tmp_path / "a", catalog=first))
    b = author_urban_world_package(_request(output=tmp_path / "b", catalog=second))
    assert a.asset_digest != b.asset_digest


def test_scene_tamper_fails_closed(tmp_path: Path) -> None:
    scene = tmp_path / "scene"
    shutil.copytree(SAMPLE_SCENE, scene)
    target = scene / SCENE_BUILDING_COLLISION
    target.write_bytes(target.read_bytes() + b"<!-- tampered -->\n")
    catalog = _write_catalog(tmp_path / "catalog", road_widths=_all_road_widths())
    with pytest.raises(
        UrbanWorldAuthoringError, match="does not match its manifest digest"
    ):
        author_urban_world_package(
            _request(scene=scene, output=tmp_path / "bundle", catalog=catalog)
        )
    assert not (tmp_path / "bundle").exists()
    assert not any(tmp_path.glob(".bundle.staging-*"))


def test_origin_mismatch_fails_closed(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path / "catalog", road_widths=_all_road_widths())
    wrong = SceneOrigin(
        latitude_deg=40.0,
        longitude_deg=116.0,
        ellipsoid_height_m=50.0,
        geoid_undulation_m=30.0,
        amsl_m=20.0,
    )
    with pytest.raises(UrbanWorldAuthoringError, match="origin does not match"):
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog, origin=wrong)
        )
    assert not (tmp_path / "bundle").exists()


def test_missing_inputs_fail_closed(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path / "catalog", road_widths=_all_road_widths())
    with pytest.raises(UrbanWorldAuthoringError, match="catalog does not exist"):
        author_urban_world_package(
            _request(
                output=tmp_path / "bundle",
                catalog=tmp_path / "absent-catalog.json",
            )
        )
    with pytest.raises(UrbanWorldAuthoringError, match="scene root does not exist"):
        author_urban_world_package(
            _request(
                scene=tmp_path / "absent-scene",
                output=tmp_path / "bundle",
                catalog=catalog,
            )
        )
    assert not (tmp_path / "bundle").exists()


def test_nonempty_output_root_fails_closed(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path / "catalog", road_widths=_all_road_widths())
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "occupied.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(UrbanWorldAuthoringError, match="absent or empty"):
        author_urban_world_package(_request(output=output, catalog=catalog))
    assert (output / "occupied.txt").read_text(encoding="utf-8") == "occupied"


def test_missing_vertical_datum_assets_fail_closed(tmp_path: Path) -> None:
    catalog = _write_catalog(
        tmp_path / "catalog",
        road_widths=_all_road_widths(),
        include_geoid=True,
        include_terrain=False,
    )
    with pytest.raises(UrbanWorldAuthoringError, match="exactly one geoid_model"):
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog)
        )
    assert not (tmp_path / "bundle").exists()


def test_unknown_authored_road_id_fails_closed(tmp_path: Path) -> None:
    widths = _all_road_widths()
    widths["road.way.999999.component.0"] = 5.0
    catalog = _write_catalog(tmp_path / "catalog", road_widths=widths)
    with pytest.raises(UrbanWorldAuthoringError, match="unknown road object_ids"):
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog)
        )


def test_unknown_authored_render_id_fails_closed(tmp_path: Path) -> None:
    catalog = _write_catalog(
        tmp_path / "catalog",
        road_widths=_all_road_widths(),
        render_object_ids=[*_building_ids(), "building.way.999999.component.0"],
    )
    with pytest.raises(UrbanWorldAuthoringError, match="unknown building object_ids"):
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog)
        )


def test_catalog_scene_selector_collision_fails_closed(tmp_path: Path) -> None:
    catalog = _write_catalog(
        tmp_path / "catalog",
        road_widths=_all_road_widths(),
        asset_path_override=f"scene/{SCENE_LAYER}",
    )
    with pytest.raises(
        UrbanWorldAuthoringError, match="failed strict contract validation"
    ):
        author_urban_world_package(
            _request(output=tmp_path / "bundle", catalog=catalog)
        )


def test_alignment_validation_path(tmp_path: Path) -> None:
    catalog = _write_catalog(tmp_path / "catalog", road_widths=_all_road_widths())
    result = author_urban_world_package(
        _request(output=tmp_path / "bundle", catalog=catalog)
    )

    reader = BundleReader(result.bundle_root)
    generator = GeneratorIdentity(
        generator_id="aero-bench.urban-scene-authoring",
        version="v1",
        source_revision="a" * 40,
    )
    manifest = alignment_manifest(
        reader=reader, world=result.world_package, generator=generator
    )
    assert len(manifest.alignments) == result.building_count
    verify_alignment(
        manifest, reader=reader, world=result.world_package, generator=generator
    )

    # A changed building geometry no longer matches the sealed alignment.
    original = result.world_package.buildings[0]
    tampered_geometry = original.geometry.model_copy(
        update={"top_altitude_m": original.geometry.top_altitude_m + 1.0}
    )
    tampered = original.model_copy(update={"geometry": tampered_geometry})
    broken_world = result.world_package.model_copy(
        update={"buildings": (tampered, *result.world_package.buildings[1:])}
    )
    with pytest.raises(ValueError, match="geometry digest does not match"):
        verify_alignment(
            manifest, reader=reader, world=broken_world, generator=generator
        )
