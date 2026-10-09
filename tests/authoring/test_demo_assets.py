"""City assets use inventory IDs without pinning their content."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aeroagentsim.authoring import demo


def test_city_assets_allow_edits_and_keep_source_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, assets = tmp_path / "source", tmp_path / "assets"
    (source / "inputs").mkdir(parents=True)
    (assets / "buildings").mkdir(parents=True)
    names = ("scene.json", "buildings/city.glb", "escape.glb")
    inventory = {
        "assets": [
            {
                "path": "web/assets/" + name,
                "asset_id": "city/" + name,
                "sha256": "obsolete-pin",
                "bytes": 1,
            }
            for name in names
        ]
    }
    (source / "inputs/city-manifest.json").write_text(json.dumps(inventory))
    (assets / "scene.json").write_text(
        json.dumps({"buildings": [{"url": "/assets/buildings/city.glb"}]})
    )
    mesh = assets / "buildings/city.glb"
    mesh.write_bytes(b"actual modified mesh content")
    monkeypatch.setenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", str(assets))
    monkeypatch.setattr(demo, "demo_source", lambda name: source)
    assert demo.city_file("buildings/city.glb") == mesh
    manifest = json.loads(demo.capture_manifest())
    assert manifest["asset_id"] == demo.CITY_ASSET_ID
    assert manifest["files"] == [
        {"url": "/v1/studio/demo-assets/" + name, "asset_id": "city/" + name}
        for name in sorted(names[:2])
    ]
    document = {
        "engines": {
            "decisions": {"config": {}},
            "capture": {"config": {"renderer": {"browser_executable": "/browser"}}},
            "capture_bridge": {"config": {}},
        }
    }
    demo.configure_console(document, source)
    assert (
        document["engines"]["capture_bridge"]["config"]["asset_digest"]
        == demo.CITY_ASSET_ID
    )
    with pytest.raises(FileNotFoundError, match="inventory"):
        demo.city_file("unlisted.glb")
    outside = tmp_path / "outside.glb"
    outside.write_bytes(b"outside assets")
    (assets / "escape.glb").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        demo.city_file("escape.glb")
    mesh.unlink()
    with pytest.raises(FileNotFoundError, match="missing"):
        demo.capture_manifest()
