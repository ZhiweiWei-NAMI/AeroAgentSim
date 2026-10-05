from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from aero_bench.authoring import (
    COMPILED_INTERCHANGE_ONLY,
    SHANGHAI_SOURCE_SHA256,
    RegisteredSceneSource,
    SceneSelection,
    SceneSelectionError,
    SceneSourceRegistry,
    compile_scene_selection,
    default_scene_source_registry,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.authoring.compiler import _publish_directory_no_replace
from aero_bench.world.scene_compiler import SceneOrigin


def _selection_document(**bounds: float) -> dict[str, object]:
    crop = {
        "min_east_m": -300.0,
        "max_east_m": 300.0,
        "min_north_m": -300.0,
        "max_north_m": 300.0,
    }
    crop.update(bounds)
    return {
        "schema_version": "aero-bench.scene-selection/v1",
        "source_id": "shanghai-central-osm-v1",
        "source_sha256": SHANGHAI_SOURCE_SHA256,
        "origin": {
            "latitude_deg": 31.2304,
            "longitude_deg": 121.4737,
            "ellipsoid_height_m": 50.0,
            "geoid_undulation_m": 30.0,
            "amsl_m": 20.0,
        },
        "bounds_enu_m": crop,
    }


def test_real_source_different_crops_publish_distinct_interchange(tmp_path: Path) -> None:
    source = default_scene_source_registry().verify(SceneSelection.from_document(_selection_document()))
    assert source.byte_size == 4_868_022
    assert source.bounds_wgs84.min_latitude_deg == 31.2228
    assert source.bounds_wgs84.max_longitude_deg == 121.4868

    first = SceneSelection.from_document(_selection_document())
    second = SceneSelection.from_document(_selection_document(min_east_m=-250.0, max_east_m=350.0))
    assert first.sha256 != second.sha256
    first_result = compile_scene_selection(first, output_root=tmp_path)
    second_result = compile_scene_selection(second, output_root=tmp_path)

    for selection, result in ((first, first_result), (second, second_result)):
        assert result.state == COMPILED_INTERCHANGE_ONLY
        assert result.selection_sha256 == selection.sha256
        assert result.output_dir.name == selection.sha256
        assert result.source_sha256 == SHANGHAI_SOURCE_SHA256
        assert result.building_count > 0 and result.road_count > 0
        assert result.manifest_path.is_file()
        assert result.effective_osm_path.is_file()
        assert result.manifest_sha256 == hashlib.sha256(result.manifest_path.read_bytes()).hexdigest()
        assert result.effective_osm_sha256 == hashlib.sha256(result.effective_osm_path.read_bytes()).hexdigest()
        assert result.output_dir.joinpath("authoring-selection.json").read_bytes() == canonical_json_bytes(selection.to_document())
        receipt = json.loads(result.output_dir.joinpath("authoring-receipt.json").read_bytes())
        manifest = json.loads(result.manifest_path.read_bytes())
        assert receipt["state"] == COMPILED_INTERCHANGE_ONLY
        assert receipt["selection_sha256"] == selection.sha256
        assert receipt["origin"] == selection.to_document()["origin"]
        assert receipt["bounds_enu_m"] == selection.to_document()["bounds_enu_m"]
        assert receipt["compiler_manifest"]["sha256"] == result.manifest_sha256
        assert receipt["effective_osm"]["sha256"] == result.effective_osm_sha256
        assert manifest["source"]["sha256"] == SHANGHAI_SOURCE_SHA256
        assert manifest["origin"]["latitude_deg"] == selection.origin.latitude_deg
        assert manifest["crop"]["enu_bounds_m"] == selection.to_document()["bounds_enu_m"]
        assert manifest["renderer_policy"]["custom_viewer_mesh_emitted"] is False
        assert manifest["netconvert"]["generated"] is False

    with pytest.raises(SceneSelectionError, match="already exists"):
        compile_scene_selection(first, output_root=tmp_path)


def test_second_registered_map_compiles_with_its_own_origin_and_digest(tmp_path: Path) -> None:
    registry = default_scene_source_registry()
    source = registry.read("shanghai-jingan-osm-v1")
    assert source.registration.sha256 == "778c35a2743e4f291d4257765494b6e492613283107524ac8a7ce8359778f7df"
    selection = SceneSelection.from_document({
        "schema_version": "aero-bench.scene-selection/v1",
        "source_id": source.registration.source_id,
        "source_sha256": source.registration.sha256,
        "origin": {
            "latitude_deg": 31.2235, "longitude_deg": 121.445,
            "ellipsoid_height_m": 50, "geoid_undulation_m": 30, "amsl_m": 20,
        },
        "bounds_enu_m": {
            "min_east_m": -240, "max_east_m": 210,
            "min_north_m": -240, "max_north_m": 130,
        },
    })
    result = compile_scene_selection(selection, output_root=tmp_path, registry=registry)
    assert result.source_id == "shanghai-jingan-osm-v1"
    assert result.source_sha256 == source.registration.sha256
    assert result.building_count == 43 and result.road_count == 47
    assert result.manifest_path.is_file()


def test_source_manifest_rejects_path_escape_and_digest_drift(tmp_path: Path) -> None:
    root = tmp_path
    source_file = root / "map.osm.json"
    source_file.write_bytes(canonical_json_bytes({
        "version": 0.6,
        "bounds": {"minlat": 31.0, "maxlat": 31.002, "minlon": 121.0, "maxlon": 121.002},
        "elements": [],
    }))
    entry = {
        "source_id": "local-map", "display_name": "Local Map", "path": source_file.name,
        "sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
        "origin": {"latitude_deg": 31.001, "longitude_deg": 121.001,
                   "ellipsoid_height_m": 50, "geoid_undulation_m": 30, "amsl_m": 20},
    }
    manifest_path = root / "sources.json"
    def write(value: dict[str, object]) -> None:
        manifest_path.write_bytes(canonical_json_bytes({
            "schema_version": "aero-bench.scene-source-registry/v1", "sources": [value],
        }))

    write(entry)
    assert SceneSourceRegistry.from_manifest(manifest_path, repository_root=root).read("local-map").byte_size
    write({**entry, "sha256": "0" * 64})
    with pytest.raises(SceneSelectionError, match="source_sha256"):
        SceneSourceRegistry.from_manifest(manifest_path, repository_root=root)
    write({**entry, "path": "../map.osm.json"})
    with pytest.raises(SceneSelectionError, match="path is invalid"):
        SceneSourceRegistry.from_manifest(manifest_path, repository_root=root)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(client_path="/etc/passwd"),
        lambda d: d.update(source_sha256="0" * 64),
        lambda d: d.update(source_id="unregistered"),
        lambda d: d["origin"].update(amsl_m=19.0),
        lambda d: d["origin"].update(latitude_deg=31.2305),
        lambda d: d["origin"].update(latitude_deg=True),
        lambda d: d["bounds_enu_m"].update(min_east_m=True),
        lambda d: d["bounds_enu_m"].update(max_north_m=float("nan")),
        lambda d: d["bounds_enu_m"].update(min_east_m=300.0),
        lambda d: d["bounds_enu_m"].update(max_east_m=20_000.0),
    ],
)
def test_invalid_selection_is_rejected(change) -> None:
    document = _selection_document()
    change(document)
    with pytest.raises(SceneSelectionError):
        selection = SceneSelection.from_document(document)
        default_scene_source_registry().verify(selection)


def test_json_duplicate_and_nonfinite_values_are_rejected() -> None:
    raw = canonical_json_bytes(_selection_document())
    assert SceneSelection.from_json_bytes(raw).sha256 == SceneSelection.from_document(_selection_document()).sha256
    with pytest.raises(SceneSelectionError, match="duplicate JSON key"):
        SceneSelection.from_json_bytes(raw[:-1] + b',"source_id":"other"}')
    with pytest.raises(SceneSelectionError, match="non-JSON numeric"):
        SceneSelection.from_json_bytes(raw.replace(b'300.0', b'NaN', 1))


def test_source_hash_is_verified_against_registered_raw_bytes(tmp_path: Path) -> None:
    original = default_scene_source_registry().verify(SceneSelection.from_document(_selection_document()))
    tampered = tmp_path / "source.osm.json"
    tampered.write_bytes(original.registration.path.read_bytes() + b"\n")
    registry = SceneSourceRegistry({
        original.registration.source_id: RegisteredSceneSource(
            original.registration.source_id, tampered, SHANGHAI_SOURCE_SHA256
        )
    })
    with pytest.raises(SceneSelectionError, match="source_sha256"):
        registry.verify(SceneSelection.from_document(_selection_document()))


def test_enu_edge_center_cannot_escape_declared_source_bounds() -> None:
    # At north=742.865 m, the two east corners remain inside the source's
    # 31.2371-degree north edge, while the midpoint crosses it. Four-corner
    # checks would accept this rectangle.
    selection = SceneSelection.from_document(_selection_document(
        min_east_m=-500.0, max_east_m=500.0,
        min_north_m=700.0, max_north_m=742.865,
    ))
    with pytest.raises(SceneSelectionError, match="crosses registered"):
        default_scene_source_registry().verify(selection)


def test_reference_closure_nodes_do_not_extend_selectable_area() -> None:
    # The source's closure includes nodes far outside its top-level declared
    # map bounds. Selection is constrained to the declared map rectangle.
    selection = SceneSelection.from_document(_selection_document(
        min_east_m=-1_500.0, max_east_m=-1_200.0,
    ))
    with pytest.raises(SceneSelectionError, match="outside registered"):
        default_scene_source_registry().verify(selection)


def test_larger_registered_source_requires_a_new_boundary_policy(tmp_path: Path) -> None:
    raw = canonical_json_bytes({
        "version": 0.6,
        "bounds": {"minlat": 30.0, "maxlat": 30.1, "minlon": 121.0, "maxlon": 121.1},
        "elements": [],
    })
    path = tmp_path / "large.osm.json"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    registry = SceneSourceRegistry({"large": RegisteredSceneSource("large", path, digest)})
    with pytest.raises(SceneSelectionError, match="v1 local WGS84 bounds policy"):
        registry.read("large")


def test_registry_origin_height_is_limited_to_the_local_surface() -> None:
    registered = default_scene_source_registry().read("shanghai-central-osm-v1").registration
    high_origin = SceneOrigin(ellipsoid_height_m=20_000.0, geoid_undulation_m=30.0, amsl_m=19_970.0)
    registry = SceneSourceRegistry({registered.source_id: RegisteredSceneSource(
        registered.source_id, registered.path, registered.sha256, high_origin,
    )})
    with pytest.raises(SceneSelectionError, match="source origin exceeds"):
        registry.read(registered.source_id)


def test_atomic_publication_never_replaces_a_competing_directory(tmp_path: Path) -> None:
    staged = [tmp_path / "stage-a", tmp_path / "stage-b"]
    for index, path in enumerate(staged):
        path.mkdir()
        (path / "identity").write_text(str(index))
    final = tmp_path / "published"
    start = Barrier(2)

    def publish(path: Path) -> bool:
        start.wait()
        try:
            _publish_directory_no_replace(path, final)
        except SceneSelectionError as exc:
            assert "already exists" in str(exc)
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, staged))
    assert sorted(results) == [False, True]
    winner = final.joinpath("identity").read_text()
    assert winner in {"0", "1"}
    assert staged[int(winner)].exists() is False
    assert staged[1 - int(winner)].joinpath("identity").exists()
