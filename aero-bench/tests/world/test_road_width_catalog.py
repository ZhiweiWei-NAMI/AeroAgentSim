"""Focused module-level tests for the production road-width catalog authoring tool.

``aero_bench.world.road_width_catalog.build_road_width_catalog`` derives a width
for every source road of a compiled urban scene from that scene's own
provenance-tagged SUMO conversion, and emits a fragment whose ``road_width_m``
section integrates with ``aero-bench.urban-world-authoring/v1``
:class:`UrbanWorldCatalog`.

These tests drive the real checked-in Shanghai sample (414 buildings, 289 roads)
and assert honest behaviour:

  * every one of the 289 source roads is covered with its **exact** ``object_id``;
  * every derived width is labelled an **estimate** carrying its algorithm,
    configuration constants, exact SUMO edges/lanes and an evidence digest --
    never presented as a surveyed measurement;
  * a real OSM ``width`` tag is preserved and never overwritten by an estimate;
  * an unmatchable road, a missing SUMO network or a digest mismatch fails closed
    and exposes the offending ``object_id`` rather than silently omitting it;
  * identical inputs reproduce a byte-identical fragment;
  * the fragment round-trips through :class:`UrbanWorldCatalog` and through a
    real ``author_urban_world_package`` run.

All test-only catalogue assets (geoid/terrain/render bytes) are clearly fixtures.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from aero_bench.world.contracts import WorldPackage
from aero_bench.world.road_width_catalog import (
    ROAD_WIDTH_CATALOG_SCHEMA_VERSION,
    SUMO_DEFAULT_LANE_WIDTH_M,
    SUMO_LANE_MODEL_ALGORITHM,
    RoadWidthCatalogError,
    build_road_width_catalog,
    merge_fragment_into_catalog,
    write_catalog_fragment,
)
from aero_bench.world.scene_authoring import (
    CatalogRoadWidth,
    RoadWidthProvenance,
    UrbanWorldAuthoringError,
    UrbanWorldAuthoringRequest,
    UrbanWorldCatalog,
    author_urban_world_package,
)
from aero_bench.world.scene_compiler import SceneOrigin
from tests.world.test_scene_authoring import (
    SAMPLE_SCENE,
    _road_ids,
    _write_catalog,
)

_COPIED_FILES = (
    "manifest.json",
    "metadata/objects.json",
    "osm/sumo-network.osm",
    "sumo/network.net.xml",
    "sumo/engineering-inputs.json",
)

#: Widths the real sample must reproduce (metres), recomputed independently of
#: the tool: netconvert modelled lane count x SUMO default lane width (3.2 m), or
#: an explicit lane width where the net declares one.
_EXPECTED_WIDTHS = {
    # secondary, oneway, 2 modelled carriageway lanes -> 2 x 3.2
    "road.way.10629715.component.0": 6.4,
    # secondary, oneway, 4 modelled carriageway lanes -> 4 x 3.2
    "road.way.15419875.component.0": 12.8,
    # residential, bidirectional, 1 carriageway lane per direction -> 2 x 3.2
    "road.way.15417580.component.0": 6.4,
    # footway: a single pedestrian-only lane with an explicit 2.00 m width
    "road.way.178583143.component.0": 2.0,
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _copy_scene(tmp_path: Path, name: str = "scene") -> Path:
    """Copy only the five scene files this tool reads."""

    destination = tmp_path / name
    for relative in _COPIED_FILES:
        source = SAMPLE_SCENE / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return destination


def _repin(scene: Path) -> None:
    """Re-stamp manifest/engineering digests after a deliberate scene edit."""

    manifest = json.loads((scene / "manifest.json").read_text(encoding="utf-8"))
    for output in manifest["outputs"]:
        path = scene / output["path"]
        if path.is_file():
            raw = path.read_bytes()
            output["sha256"] = _sha256(raw)
            output["byte_size"] = len(raw)
    (scene / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    engineering = json.loads(
        (scene / "sumo/engineering-inputs.json").read_text(encoding="utf-8")
    )
    engineering["source_osm_sha256"] = _sha256(
        (scene / "osm/sumo-network.osm").read_bytes()
    )
    engineering["network_sha256"] = _sha256(
        (scene / "sumo/network.net.xml").read_bytes()
    )
    (scene / "sumo/engineering-inputs.json").write_text(
        json.dumps(engineering), encoding="utf-8"
    )


def _catalog_document(result) -> dict[str, object]:
    """Build a full UrbanWorldCatalog document around the derived widths."""

    return {
        "schema_version": "aero-bench.urban-world-authoring/v1",
        "license_id": "CC-BY-4.0",
        "license_path": "licenses/cc-by-4.0.txt",
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
                    "recorded_by": "world-authoring",
                    "source_dataset": "fixture.geoid",
                    "source_version": "2026.09.29",
                    "runtime_download": False,
                },
            }
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
    }


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


# --------------------------------------------------------------------------- #
# coverage on the real sample
# --------------------------------------------------------------------------- #


def test_real_sample_covers_every_road_with_exact_object_ids() -> None:
    result = build_road_width_catalog(SAMPLE_SCENE)

    expected_ids = set(_road_ids())
    assert len(expected_ids) == 289
    assert {entry.object_id for entry in result.entries} == expected_ids
    assert result.road_count == 289
    assert result.matched_count == 289
    assert result.unmatched_road_ids == ()
    assert result.osm_tagged_count == 0
    assert result.estimated_count == 289
    assert result.fragment_document["schema_version"] == (
        ROAD_WIDTH_CATALOG_SCHEMA_VERSION
    )
    assert result.fragment_document["unmatched_road_ids"] == []


def test_real_sample_width_distribution_matches_recomputed_lane_model() -> None:
    result = build_road_width_catalog(SAMPLE_SCENE)
    widths = sorted(entry.width_m for entry in result.entries)
    assert widths.count(6.4) == 183
    assert widths.count(3.2) == 70
    assert widths.count(2.0) == 18
    assert widths.count(9.6) == 10
    assert widths.count(16.0) == 6
    assert widths.count(12.8) == 2
    assert all(width > 0 for width in widths)

    by_id = {entry.object_id: entry.width_m for entry in result.entries}
    for object_id, expected in _EXPECTED_WIDTHS.items():
        assert by_id[object_id] == expected, object_id


# --------------------------------------------------------------------------- #
# honesty: estimates are labelled, never presented as measurements
# --------------------------------------------------------------------------- #


def test_every_derived_width_is_labelled_an_estimate_with_provenance() -> None:
    result = build_road_width_catalog(SAMPLE_SCENE)

    for entry in result.entries:
        assert entry.source == "sumo_lane_model", entry.object_id
        assert entry.is_estimate is True, entry.object_id
        provenance = entry.provenance
        assert provenance is not None, entry.object_id
        assert provenance.source == "sumo_lane_model"
        assert provenance.is_estimate is True
        assert provenance.measurement_status.startswith("estimate_from_sumo_lane_model")
        assert "not a surveyed" in provenance.measurement_status
        assert provenance.algorithm == SUMO_LANE_MODEL_ALGORITHM
        assert provenance.default_lane_width_m == SUMO_DEFAULT_LANE_WIDTH_M
        assert provenance.sumo_netconvert_version == "1.27.1"
        assert provenance.sumo_network_sha256 == result.sumo_network_sha256
        assert provenance.synthetic_way_id is not None
        assert provenance.representing_edges
        assert provenance.lane_count is not None and provenance.lane_count > 0
        assert provenance.lanes
        assert len(provenance.evidence_sha256) == 64
        # the recorded width is exactly the sum of the recorded modelled lanes
        assert provenance.lane_count == len(provenance.lanes)
        assert round(sum(lane.lane_width_m for lane in provenance.lanes), 6) == (
            entry.width_m
        )

    # evidence digests identify each road's own evidence
    digests = {entry.provenance.evidence_sha256 for entry in result.entries}
    assert len(digests) == len(result.entries) == 289


def test_network_is_pinned_and_netconvert_version_is_recorded() -> None:
    result = build_road_width_catalog(SAMPLE_SCENE)
    engineering = json.loads(
        (SAMPLE_SCENE / "sumo/engineering-inputs.json").read_text(encoding="utf-8")
    )
    assert result.sumo_network_sha256 == engineering["network_sha256"]
    assert result.sumo_netconvert_version == "1.27.1"
    assert result.sumo_network_sha256 == _sha256(
        (SAMPLE_SCENE / "sumo/network.net.xml").read_bytes()
    )


def test_schema_refuses_to_label_an_estimate_as_measured() -> None:
    provenance = RoadWidthProvenance(
        source="sumo_lane_model",
        is_estimate=True,
        measurement_status="estimate_from_sumo_lane_model",
        algorithm=SUMO_LANE_MODEL_ALGORITHM,
        evidence_sha256="0" * 64,
        lane_count=2,
    )
    with pytest.raises(ValidationError, match="must be labelled is_estimate true"):
        CatalogRoadWidth(
            object_id="road.way.1.component.0",
            width_m=6.4,
            source="sumo_lane_model",
            is_estimate=False,
            provenance=provenance,
        )

    with pytest.raises(ValidationError, match="must carry provenance"):
        CatalogRoadWidth(
            object_id="road.way.1.component.0",
            width_m=6.4,
            source="sumo_lane_model",
            is_estimate=True,
        )

    with pytest.raises(ValidationError, match="requires an explicit source"):
        CatalogRoadWidth(
            object_id="road.way.1.component.0",
            width_m=6.4,
            provenance=provenance,
        )

    with pytest.raises(ValidationError, match="must be labelled is_estimate false"):
        CatalogRoadWidth(
            object_id="road.way.1.component.0",
            width_m=6.4,
            source="osm_width_tag",
            is_estimate=True,
            provenance=RoadWidthProvenance(
                source="osm_width_tag",
                is_estimate=True,
                measurement_status="preserved OSM width tag",
                algorithm="osm_width_tag_passthrough",
                evidence_sha256="1" * 64,
            ),
        )


# --------------------------------------------------------------------------- #
# OSM width tags are preserved, never replaced by an estimate
# --------------------------------------------------------------------------- #


def test_osm_width_tag_is_preserved_and_never_overwritten_by_an_estimate(
    tmp_path: Path,
) -> None:
    scene = _copy_scene(tmp_path)
    objects_path = scene / "metadata/objects.json"
    objects = json.loads(objects_path.read_text(encoding="utf-8"))
    target = "road.way.10629715.component.0"
    for road in objects["roads"]:
        if road["object_id"] == target:
            assert road["width_osm_tag"] is None
            road["width_osm_tag"] = "8.5 m"
            break
    else:  # pragma: no cover - the id is known to exist in the sample
        raise AssertionError(target)
    objects_path.write_text(json.dumps(objects), encoding="utf-8")
    _repin(scene)

    result = build_road_width_catalog(scene)

    by_id = {entry.object_id: entry for entry in result.entries}
    tagged = by_id[target]
    assert tagged.source == "osm_width_tag"
    assert tagged.is_estimate is False
    assert tagged.width_m == 8.5
    assert tagged.provenance is not None
    assert tagged.provenance.osm_width_tag == "8.5 m"
    assert tagged.provenance.is_estimate is False
    assert "estimate" not in tagged.provenance.measurement_status

    assert result.osm_tagged_count == 1
    assert result.estimated_count == 288
    # every other road still comes from the lane model
    assert all(
        entry.is_estimate is True
        for entry in result.entries
        if entry.object_id != target
    )


# --------------------------------------------------------------------------- #
# fail-closed behaviour
# --------------------------------------------------------------------------- #


def test_missing_sumo_network_fails_closed(tmp_path: Path) -> None:
    scene = _copy_scene(tmp_path)
    (scene / "sumo/network.net.xml").unlink()

    with pytest.raises(RoadWidthCatalogError, match="network.net.xml"):
        build_road_width_catalog(scene)


def test_network_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    scene = _copy_scene(tmp_path)
    network_path = scene / "sumo/network.net.xml"
    network_path.write_bytes(network_path.read_bytes() + b"\n<!-- tampered -->\n")

    with pytest.raises(RoadWidthCatalogError, match="network_sha256"):
        build_road_width_catalog(scene)


def test_objects_manifest_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    scene = _copy_scene(tmp_path)
    objects_path = scene / "metadata/objects.json"
    objects = json.loads(objects_path.read_text(encoding="utf-8"))
    objects["roads"].pop()
    objects_path.write_text(json.dumps(objects), encoding="utf-8")

    with pytest.raises(RoadWidthCatalogError, match="objects.json"):
        build_road_width_catalog(scene)


def test_unmatchable_road_fails_closed_enumerating_its_object_id(
    tmp_path: Path,
) -> None:
    scene = _copy_scene(tmp_path)
    osm_path = scene / "osm/sumo-network.osm"
    text = osm_path.read_text(encoding="utf-8")
    # the source_id also appears on a boundary node, so edit only the way
    source_tag = '<tag k="aero_bench:source_id" v="10629715"/>'
    way_match = next(
        match
        for match in re.finditer(r"<way\b[^>]*>.*?</way>", text, flags=re.DOTALL)
        if source_tag in match.group(0)
    )
    way_block = way_match.group(0)
    assert way_block.count(source_tag) == 1
    patched = (
        text[: way_match.start()]
        + way_block.replace(source_tag, "")
        + text[way_match.end() :]
    )
    assert text.count(source_tag) == patched.count(source_tag) + 1
    osm_path.write_text(patched, encoding="utf-8")
    _repin(scene)

    # default: fail closed and expose the exact object_id
    with pytest.raises(RoadWidthCatalogError) as excinfo:
        build_road_width_catalog(scene)
    message = str(excinfo.value)
    assert "road.way.10629715.component.0" in message
    assert "1 of 289" in message
    assert "no width is invented for them" in message

    # audit mode: expose the same object_id instead of failing
    result = build_road_width_catalog(scene, require_complete=False)
    assert result.unmatched_road_ids == ("road.way.10629715.component.0",)
    assert result.matched_count == 288
    assert len(result.entries) == 288
    assert result.fragment_document["unmatched_road_ids"] == [
        "road.way.10629715.component.0"
    ]


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


def test_catalog_is_deterministic(tmp_path: Path) -> None:
    first = build_road_width_catalog(SAMPLE_SCENE)
    second = build_road_width_catalog(SAMPLE_SCENE)

    assert first.fragment_sha256 == second.fragment_sha256
    assert [(entry.object_id, entry.width_m) for entry in first.entries] == [
        (entry.object_id, entry.width_m) for entry in second.entries
    ]

    first_path = tmp_path / "a.json"
    second_path = tmp_path / "b.json"
    assert write_catalog_fragment(first, first_path) == write_catalog_fragment(
        second, second_path
    )
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_path.read_bytes() == second_path.read_bytes()
    assert _sha256(first_path.read_bytes()) == first.fragment_sha256


# --------------------------------------------------------------------------- #
# integration with UrbanWorldCatalog
# --------------------------------------------------------------------------- #


def test_fragment_round_trips_through_urban_world_catalog() -> None:
    result = build_road_width_catalog(SAMPLE_SCENE)
    document = _catalog_document(result)
    document["road_width_m"] = [
        entry.model_dump(mode="json", exclude_none=True) for entry in result.entries
    ]

    catalog = UrbanWorldCatalog.model_validate(document)
    assert len(catalog.road_width_m) == 289
    widths = {item.object_id: item for item in catalog.road_width_m}
    assert set(widths) == set(_road_ids())
    assert widths["road.way.10629715.component.0"].width_m == 6.4
    assert widths["road.way.10629715.component.0"].is_estimate is True
    assert widths["road.way.10629715.component.0"].provenance is not None


def test_merge_fragment_into_catalog_replaces_same_object_ids() -> None:
    result = build_road_width_catalog(SAMPLE_SCENE)
    base = _catalog_document(result)
    base["road_width_m"] = [
        {"object_id": "road.way.10629715.component.0", "width_m": 1.0},
        {"object_id": "road.way.unknown.component.0", "width_m": 5.0},
    ]

    merged = merge_fragment_into_catalog(base, result)

    ids = [entry["object_id"] for entry in merged["road_width_m"]]
    assert ids == sorted(ids)
    assert "road.way.unknown.component.0" in ids
    # the derived entry replaces the placeholder for the same object_id
    replacement = next(
        entry
        for entry in merged["road_width_m"]
        if entry["object_id"] == "road.way.10629715.component.0"
    )
    assert replacement["width_m"] == 6.4
    assert replacement["is_estimate"] is True
    # 289 derived entries plus the unrelated placeholder that was preserved
    assert len(merged["road_width_m"]) == 290


def test_real_authoring_produces_worldpackage_with_derived_widths(
    tmp_path: Path,
) -> None:
    result = build_road_width_catalog(SAMPLE_SCENE)

    # Without any widths the strict compiler still fails closed...
    empty_catalog = _write_catalog(tmp_path / "catalog-empty", road_widths={})
    with pytest.raises(UrbanWorldAuthoringError, match="no defensible width"):
        author_urban_world_package(
            UrbanWorldAuthoringRequest(
                scene_root=SAMPLE_SCENE,
                output_root=tmp_path / "bundle-empty",
                world_id="world.shanghai-huangpu-east",
                origin=_sample_origin(),
                catalog_path=empty_catalog,
            )
        )

    # ...and with the derived catalog it authors all 289 roads.
    catalog_path = _write_catalog(tmp_path / "catalog", road_widths={})
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    document["road_width_m"] = [
        entry.model_dump(mode="json", exclude_none=True) for entry in result.entries
    ]
    catalog_path.write_text(json.dumps(document), encoding="utf-8")

    authored = author_urban_world_package(
        UrbanWorldAuthoringRequest(
            scene_root=SAMPLE_SCENE,
            output_root=tmp_path / "bundle",
            world_id="world.shanghai-huangpu-east",
            origin=_sample_origin(),
            catalog_path=catalog_path,
        )
    )
    assert authored.road_count == 289
    package: WorldPackage = authored.world_package
    widths = {road.road_id: road.width_m for road in package.roads}
    assert set(widths) == set(_road_ids())
    for object_id, expected in _EXPECTED_WIDTHS.items():
        assert widths[object_id] == expected, object_id
    assert all(width > 0 for width in widths.values())
