"""Focused module-level tests for the ``building_render`` source_frame contract.

The strict ``WorldPackage`` demands one real renderable asset per building.
glTF/GLB content follows the glTF 2.0 coordinate convention -- right-handed,
Y-up, metre units, **asset-local** -- and core glTF cannot represent geodetic
WGS84 coordinates at all. The contract therefore:

* allows the ``building_render`` role to declare ``asset_local`` (so an honest
  local-frame GLB can be recorded), and
* rejects any glTF ``building_render`` that claims ``WGS84``, at both the
  ``AssetRecord`` layer and the authored-catalog layer, so a local GLB can
  never be mislabelled to satisfy validation.

Pre-existing render assets whose own content is geodetic coordinate JSON keep
their ``WGS84`` label. Building *placement* in the package stays with
``BuildingGeometry`` (ENU footprint plus base/top altitude); the render asset
only carries the visual mesh.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aero_bench.world.contracts import ASSET_ROLE_SOURCE_FRAMES, AssetRecord
from aero_bench.world.scene_authoring import CatalogBuildingRender
from aero_bench.world.workload_scenario import (
    _ASSET_ROLE_SOURCE_FRAMES as SCENARIO_ROLE_FRAMES,
    _validate_world_asset_metadata,
    WorkloadScenarioError,
)
from tests.world.support import asset_record

GLTF_BINARY = "model/gltf-binary"
_LEGACY_JSON_SHA = "a" * 64
_FILE_SHA = "b" * 64


def _render_record(*, media_type: str, source_frame: str) -> AssetRecord:
    return asset_record(
        asset_id="render.building.way.1.component.0",
        selector="world/building-render/building.way.1.component.0.glb",
        sha256=_FILE_SHA,
        asset_role="building_render",
        media_type=media_type,
        visibility="public",
        source_frame=source_frame,
        byte_size=128,
    )


def test_contract_tables_allow_asset_local_for_building_render() -> None:
    """Both validator tables accept the honest frame for a local-frame mesh."""
    for table in (ASSET_ROLE_SOURCE_FRAMES, SCENARIO_ROLE_FRAMES):
        allowed = table["building_render"]
        assert "asset_local" in allowed
        assert "WGS84" in allowed


def test_asset_record_rejects_gltf_building_render_labelled_wgs84() -> None:
    with pytest.raises(ValidationError, match="must not be labelled WGS84"):
        _render_record(media_type=GLTF_BINARY, source_frame="WGS84")
    # The honest declaration validates.
    record = _render_record(media_type=GLTF_BINARY, source_frame="asset_local")
    assert record.source_frame == "asset_local"


def test_asset_record_keeps_geodetic_json_render_labelled_wgs84() -> None:
    """A legacy coordinate-JSON render artifact is still honestly WGS84."""
    record = _render_record(media_type="application/json", source_frame="WGS84")
    assert record.asset_role == "building_render"
    assert record.source_frame == "WGS84"


def _catalog_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "object_id": "building.way.1.component.0",
        "path": "world/building-render/building.way.1.component.0.glb",
        "file": "building.way.1.component.0.glb",
        "media_type": GLTF_BINARY,
        "provenance": {
            "source_kind": "bundled_offline",
            "recorded_by": "world-authoring",
            "source_dataset": "fixture.building-render",
            "source_version": "2026.09.29",
            "runtime_download": False,
        },
    }
    entry.update(overrides)
    return entry


def test_catalog_rejects_gltf_entry_claiming_wgs84() -> None:
    """The authored catalog fails closed on a mislabelled local GLB."""
    with pytest.raises(ValidationError, match="cannot be labelled WGS84"):
        CatalogBuildingRender.model_validate(_catalog_entry(source_frame="WGS84"))


def test_catalog_accepts_honest_asset_local_gltf_entry() -> None:
    entry = CatalogBuildingRender.model_validate(
        _catalog_entry(source_frame="asset_local")
    )
    assert entry.source_frame == "asset_local"


def test_catalog_defaults_preserve_legacy_json_but_reject_omitted_gltf() -> None:
    """v1 documents that omit the field keep their frame -- except glTF.

    A non-glTF (geodetic coordinate JSON) entry omitting ``source_frame`` still
    defaults to ``WGS84``, preserving pre-existing catalog documents. A glTF
    entry that omits it fails closed: the default would mislabel an
    asset-local mesh as WGS84, so the frame must be declared explicitly.
    """
    legacy = CatalogBuildingRender.model_validate(
        _catalog_entry(media_type="application/json")
    )
    assert legacy.source_frame == "WGS84"
    with pytest.raises(ValidationError, match="cannot be labelled WGS84"):
        CatalogBuildingRender.model_validate(_catalog_entry())


def _world_asset_payload(*, media_type: str, source_frame: str) -> dict[str, object]:
    return {
        "asset_role": "building_render",
        "media_type": media_type,
        "units": [{"quantity": "file_size", "unit": "B"}],
        "precision": [
            {
                "quantity": "file_size",
                "kind": "exact_bytes",
                "unit": "B",
                "exact_bytes": 128,
                "absolute_tolerance": None,
            }
        ],
        "source_frame": source_frame,
        "provenance": {
            "source_kind": "bundled_offline",
            "recorded_by": "world-authoring",
            "source_dataset": "fixture.building-render",
            "source_version": "2026.09.29",
            "runtime_download": False,
        },
        "selector": "world/building-render/building.way.1.component.0.glb",
        "selector_fragment": None,
        "license": {
            "license_id": "CC-BY-4.0",
            "selector": "licenses/cc-by-4.0.txt",
            "selector_fragment": None,
            "file": {"path": "licenses/cc-by-4.0.txt", "sha256": _LEGACY_JSON_SHA},
            "byte_size": 32,
        },
    }


def test_workload_scenario_validator_rejects_gltf_wgs84_render() -> None:
    """The resolved-scenario validator applies the same honesty guard."""
    file_ref = {
        "path": "world/building-render/building.way.1.component.0.glb",
        "sha256": _FILE_SHA,
    }
    with pytest.raises(
        WorkloadScenarioError, match="must be asset_local for a glTF building_render"
    ):
        _validate_world_asset_metadata(
            _world_asset_payload(media_type=GLTF_BINARY, source_frame="WGS84"),
            file_ref=file_ref,
            label="ResolvedScenario.assets[0].world",
        )
    validated = _validate_world_asset_metadata(
        _world_asset_payload(media_type=GLTF_BINARY, source_frame="asset_local"),
        file_ref=file_ref,
        label="ResolvedScenario.assets[0].world",
    )
    assert validated["source_frame"] == "asset_local"
