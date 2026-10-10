from __future__ import annotations

import pytest
from pydantic import ValidationError

from aero_bench.world.contracts import (
    WORLD_SCHEMA_VERSION,
    PublicLayer,
    WorldPackageContent,
)
from tests.world.support import asset_record, content_kwargs


def _content():
    values = content_kwargs()
    values["schema_version"] = WORLD_SCHEMA_VERSION
    original = values["assets"]
    source = asset_record(asset_id="asset.osm", selector="osm/source.json", sha256="9" * 64, asset_role="layer_tiles", media_type="application/json", visibility="public", source_frame="WGS84", byte_size=100)
    mesh = asset_record(asset_id="asset.mesh", selector="osm/manifest.json", sha256="8" * 64, asset_role="layer_tiles", media_type="application/json", visibility="public", source_frame="asset_local", byte_size=200)
    values["assets"] = tuple(sorted((*original, source, mesh), key=lambda item: item.artifact.artifact_id))
    values["layers"] = tuple(sorted((*values["layers"],
        PublicLayer(layer_id="layer.osm", kind="osm_scene", asset_id="asset.osm", visibility="public", default_visible=True),
        PublicLayer(layer_id="layer.mesh", kind="osm_mesh", asset_id="asset.mesh", visibility="public", default_visible=True),
    ), key=lambda item: item.layer_id))
    return values


def test_mesh_layer_requires_explicit_local_manifest_and_osm_source() -> None:
    values = _content()
    WorldPackageContent(**values)
    values["layers"] = tuple(layer for layer in values["layers"] if layer.kind != "osm_scene")
    with pytest.raises(ValidationError, match="source osm_scene"):
        WorldPackageContent(**values)


def test_mesh_layer_cannot_relabel_wgs84_source_as_mesh() -> None:
    values = _content()
    values["layers"] = tuple(layer.model_copy(update={"asset_id": "asset.osm"}) if layer.kind == "osm_mesh" else layer for layer in values["layers"])
    with pytest.raises(ValidationError, match="asset_local"):
        WorldPackageContent(**values)
