"""Expose the supplied Unity package as a read-only browser asset catalog.

Only FBX geometry and derived image previews are exported. The original
Unity scenes, materials, and lightmaps remain authoritative in the package.
"""

from __future__ import annotations

import json
import io
import tarfile
from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT.parent / "validation/downloaded-assets/baidu-bigcity/BigCity.unitypackage"
OUTPUT = ROOT / "public/models/bigcity"


def subgroup(path: str) -> str:
    name = Path(path).stem.lower()
    suffix = Path(path).suffix.lower()
    if suffix == ".fbx":
        if name == "scene":
            return "BigCity · 场景总览"
        if any(word in name for word in ("building", "shop", "hotel", "motel", "club", "church")):
            return "BigCity · 建筑"
        if any(word in name for word in ("road", "walk", "overpass", "floor", "wall", "bridge")):
            return "BigCity · 道路与结构"
        if any(word in name for word in ("tree", "bush", "plant", "grass")):
            return "BigCity · 植被"
        return "BigCity · 街道设施"
    if suffix == ".unity":
        return "BigCity · Unity 场景"
    if "lightmap" in name or suffix == ".exr":
        return "BigCity · 烘焙光照"
    if suffix in {".png", ".tif", ".psd"}:
        return "BigCity · 纹理图片"
    if "lens flares" in path.lower() or suffix == ".flare":
        return "BigCity · 镜头光效"
    return "BigCity · 工程资源"


def save_image_preview(source: bytes, target: Path) -> None:
    with Image.open(io.BytesIO(source)) as image:
        frame = ImageOps.exif_transpose(image)
        frame.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        if frame.mode not in {"RGB", "RGBA"}:
            frame = frame.convert("RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.save(target, "WEBP", quality=82, method=1)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries = []

    def finish(guid: str, record: dict) -> None:
        path = record.get("package_path")
        source = record.get("asset")
        if not path or source is None:
            return
        suffix = Path(path).suffix.lower()
        row = {
            "guid": guid, "package_path": path, "title": Path(path).stem,
            "format": suffix.lstrip(".").upper() or "Unity asset", "bytes": len(source),
            "subgroup": subgroup(path),
        }
        if record.get("preview"):
            preview = OUTPUT / "previews" / f"{guid}.png"
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_bytes(record["preview"])
            row["preview_url"] = f"/models/bigcity/previews/{guid}.png"
        if suffix == ".fbx":
            model = OUTPUT / "fbx" / f"{guid}.fbx"
            model.parent.mkdir(parents=True, exist_ok=True)
            model.write_bytes(source)
            row["model_url"] = f"/models/bigcity/fbx/{guid}.fbx"
        elif suffix in {".png", ".tif", ".psd"}:
            output = OUTPUT / "images" / f"{guid}.webp"
            try:
                save_image_preview(source, output)
                row["image_url"] = f"/models/bigcity/images/{guid}.webp"
            except (OSError, ValueError) as error:
                row["preview_error"] = str(error)
        entries.append(row)

    with tarfile.open(PACKAGE, "r:gz") as archive:
        current_guid = ""
        record = {}
        for member in archive:
            guid, _, part = member.name.partition("/")
            if current_guid and guid != current_guid:
                finish(current_guid, record)
                record = {}
            current_guid = guid
            if part == "pathname":
                record["package_path"] = archive.extractfile(member).read().decode("utf-8").splitlines()[0]
            elif part in {"asset", "preview.png"}:
                record["preview" if part == "preview.png" else "asset"] = archive.extractfile(member).read()
        if current_guid:
            finish(current_guid, record)

    scene = next((row for row in entries if row["package_path"] == "Assets/Package/FBX/Scene.FBX"), None)
    if scene:
        for row in entries:
            if row["format"] == "UNITY":
                row["model_url"] = scene["model_url"]
                row["preview_url"] = scene.get("preview_url")
    entries.sort(key=lambda row: row["package_path"])
    texture_names = {
        "buildings modern.png": "Modern Building", "classical building.png": "Classical Building",
        "floor.png": "Floor", "floor.tif": "Floor", "road.png": "Road", "road.tif": "Road",
        "tree.tif": "Tree", "tree.psd": "Tree", "banner texture.png": "banner texture",
        "prop texture1.png": "prop texture1", "prop texture2.png": "prop texture2",
    }
    by_title = {row["title"].lower(): row["image_url"] for row in entries if row.get("image_url")}
    aliases = {source: by_title[title.lower()] for source, title in texture_names.items() if title.lower() in by_title}
    missing = set(texture_names) - set(aliases)
    if missing:
        raise ValueError(f"Missing BigCity texture assets for FBX references: {sorted(missing)}")
    (OUTPUT / "texture-aliases.json").write_text(json.dumps(aliases, ensure_ascii=False, indent=2))
    (OUTPUT / "manifest.json").write_text(json.dumps({"source": "BigCity.unitypackage", "entries": entries}, ensure_ascii=False, indent=2))
    print(f"{len(entries)} Unity assets indexed; {sum(bool(row.get('model_url')) for row in entries)} 3D views; {sum(bool(row.get('image_url')) for row in entries)} image views")


if __name__ == "__main__":
    main()
