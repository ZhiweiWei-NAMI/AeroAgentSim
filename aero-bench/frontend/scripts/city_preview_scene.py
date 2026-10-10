"""Resolve the browser's city scene manifest to local pipeline inputs."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend/public"
# The legacy preview builders consume `assets`-style scenes. The browser default scene is a
# building_render scene with verified canonical road_assets, built by the canonical pipeline.
DEFAULT_SCENE = PUBLIC / "city-presentation/huangpu-night-scene-v1.json"
DEFAULT_NETWORK = ROOT / "validation/city-walking-continuity-20260927/normalized-urban/network.net.xml"


@dataclass(frozen=True)
class ScenePaths:
    traffic: Path
    pack: Path
    road_surface_texture: str
    placement: Path
    road: Path
    flight: Path


def public_asset(url: str) -> Path:
    if not isinstance(url, str) or not url.startswith("/") or "?" in url or "#" in url:
        raise ValueError(f"Scene asset must be an absolute public URL: {url!r}")
    parts = url.split("/")[1:]
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Scene asset has invalid path segments: {url!r}")
    return PUBLIC.joinpath(*parts)


def read_scene_paths(scene_path: Path, *, pack_manifest: Path | None = None) -> ScenePaths:
    """Resolve scene inputs; `pack_manifest` names an unpublished local copy of the pinned pack manifest."""
    scene = json.loads(scene_path.read_text())
    if scene["schema_version"] != "aero-bench.city-scene-preview/v1":
        raise ValueError("Unsupported city scene manifest")
    if "assets" not in scene:
        raise ValueError("The legacy preview pipeline requires an `assets` scene; "
                         "building_render scenes are built by the canonical road pipeline")
    assets = scene["assets"]
    base = scene["mesh_pack"]["base_url"]
    if not isinstance(base, str) or not base.endswith("/"):
        raise ValueError("Mesh pack base URL must end in /")
    pack = public_asset(base + "manifest.json") if pack_manifest is None else Path(pack_manifest).resolve()
    road_surface_texture = scene["mesh_pack"]["road_surface_texture"]
    public_asset(road_surface_texture)
    pack_bytes = pack.read_bytes()
    expected = scene["mesh_pack"]["manifest"]
    if len(pack_bytes) != expected["size_bytes"] or hashlib.sha256(pack_bytes).hexdigest() != expected["sha256"]:
        raise ValueError("Scene mesh pack manifest differs from its pinned file reference")
    return ScenePaths(public_asset(assets["traffic"]), pack, road_surface_texture,
                      public_asset(assets["building_placement"]), public_asset(assets["road"]),
                      public_asset(assets["flight"]))
