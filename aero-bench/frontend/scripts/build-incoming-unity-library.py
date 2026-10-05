"""Expose supplied Unity vehicle and character packages to the asset viewer.

The exported FBX and image files are browser copies. Unity prefabs, materials,
controllers and scenes stay in the source package and appear as catalog rows.
"""

from __future__ import annotations

import io
import json
import argparse
import tarfile
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
INCOMING = ROOT.parent / "validation/downloaded-assets/极速下载好的文件"
OUTPUT = ROOT / "public/models/incoming"
REVIEWED_MODELS = ROOT / "scripts/incoming-reviewed-models.json"
PACKAGES = (
    ("urban-traffic", "Urban Traffic System 20182.unitypackage", "vehicle"),
    ("citizens", "Citizens Pro 2019 v1.0.unitypackage", "character"),
)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".psd"}


def package_path(filename: str) -> Path:
    matches = list(INCOMING.rglob(filename))
    if len(matches) != 1:
        raise ValueError(f"Expected one {filename}, found {len(matches)}")
    return matches[0]


def image_as_webp(raw: bytes, target: Path) -> bool:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "WEBP", quality=90, method=2)
        return True
    except (OSError, ValueError):
        return False


def category_for_fbx(pack: str, path: str) -> str | None:
    normalized = path.lower().replace("\\", "/")
    if pack == "urban-traffic":
        return "vehicle" if "/models/cars/models/" in normalized else None
    if "/models/people" in normalized and "/animations/" not in normalized:
        return "character"
    if "/animations/" in normalized:
        return None
    return "city" if "/models/" in normalized else None


def subgroup_for(pack: str, path: str, category: str, kind: str) -> str:
    name = Path(path).stem.lower()
    if category == "vehicle":
        if any(word in name for word in ("police", "ambulance", "fire")):
            return "城市交通 · 应急车辆"
        if any(word in name for word in ("bus", "truck", "van", "trailer")):
            return "城市交通 · 公交与货运"
        if any(word in name for word in ("bicycle", "bike", "scooter", "skateboard", "gyro")):
            return "城市交通 · 两轮与微出行"
        return "城市交通 · 乘用车"
    if category == "character":
        if kind == "texture":
            return "Citizens · 角色贴图"
        if "/female/" in path.lower():
            return "Citizens · 女性角色"
        if "/male/" in path.lower():
            return "Citizens · 男性角色"
        return "Citizens · 人物与动作"
    return "新素材 · 城市设施"


def item_for(pack: str, source_relative: str, guid: str, path: str, raw: bytes | None,
             preview: bytes | None, base_category: str) -> dict:
    suffix = Path(path).suffix.lower()
    name = Path(path).stem
    is_fbx = suffix == ".fbx"
    model_category = category_for_fbx(pack, path) if is_fbx else None
    category = model_category or ("city" if pack == "urban-traffic" and "/models/cars/" not in path.lower().replace("\\", "/") else base_category)
    kind = "model" if model_category else "texture" if suffix in IMAGE_EXTENSIONS else "package"
    source_file = f"validation/downloaded-assets/极速下载好的文件/{source_relative}#{path}"
    item = {
        "id": f"incoming:{pack}:{guid}", "category": category,
        "subgroup": subgroup_for(pack, path, category, kind), "title": name,
        "kind": kind, "status": "available", "format": suffix.lstrip(".").upper() or "Unity asset",
        "source_path": source_file, "origin_path": source_file, "note": "源 Unity 包内资源；可下载源包查看 Prefab、材质或动画配置。",
        "bytes": len(raw) if raw is not None else None,
        "package_path": path,
    }
    folder = OUTPUT / pack
    if preview:
        target = folder / "previews" / f"{guid}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(preview)
        item["preview_url"] = f"/models/incoming/{pack}/previews/{guid}.png"
    if raw is not None and is_fbx:
        target = folder / "fbx" / f"{guid}.fbx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        item["source_path"] = f"public/models/incoming/{pack}/fbx/{guid}.fbx"
        if model_category:
            item["model_url"] = f"/models/incoming/{pack}/fbx/{guid}.fbx"
            item["status"] = "ready"
            item["preview_exposure"] = 1.0 if pack == "urban-traffic" else 1.25
            item["material_origin"] = "源 FBX 材质及可匹配的原包贴图；Unity Material 配置尚未完整还原"
            item["texture_alias_url"] = f"/models/incoming/{pack}/texture-aliases.json"
            item["note"] = ("可旋转的源 FBX；车辆只显示 LOD0 和无级别部件。外置贴图按包内文件名映射；"
                            "Unity Prefab 动作和材质配置需单独复核。" if category == "vehicle" else
                            "可旋转的源 FBX。外置贴图按包内文件名映射；Unity Prefab 动作和材质配置需单独复核。")
        else:
            item["note"] = "源 FBX 动作或工程资源；不是独立可显示的模型。"
    elif raw is not None and suffix in IMAGE_EXTENSIONS:
        target = folder / "images" / f"{guid}.webp"
        if image_as_webp(raw, target):
            item["source_path"] = f"public/models/incoming/{pack}/images/{guid}.webp"
            item["image_url"] = f"/models/incoming/{pack}/images/{guid}.webp"
            item["status"] = "available"
            item["note"] = "来自源 Unity 包的图片或贴图；网页显示转换后的 WebP，原图留在源包。"
    return item


def extract_package(pack: str, filename: str, category: str) -> tuple[list[dict], str]:
    path = package_path(filename)
    source_relative = str(path.relative_to(INCOMING)).replace("\\", "/")
    entries = []
    with tarfile.open(path, "r:gz") as archive:
        current_guid = ""
        record: dict[str, bytes | str] = {}

        def finish() -> None:
            if "pathname" not in record:
                return
            pathname = str(record["pathname"])
            entries.append(item_for(pack, source_relative, current_guid, pathname,
                                    record.get("asset"), record.get("preview.png"), category))

        for member in archive:
            guid, _, part = member.name.partition("/")
            if current_guid and guid != current_guid:
                finish()
                record = {}
            current_guid = guid
            if part not in {"pathname", "asset", "preview.png"} or not member.isfile():
                continue
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"Missing tar payload: {member.name}")
            content = source.read()
            record[part] = content.decode("utf-8").splitlines()[0] if part == "pathname" else content
        if current_guid:
            finish()
    return entries, source_relative


def texture_aliases(pack: str, entries: list[dict]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    collisions: dict[str, list[str]] = defaultdict(list)
    images = [item for item in entries if item.get("image_url")]
    for item in images:
        basename = Path(item["package_path"]).name.lower()
        if basename in aliases and aliases[basename] != item["image_url"]:
            collisions[basename].append(item["image_url"])
        else:
            aliases[basename] = item["image_url"]
    by_stem: dict[str, set[str]] = defaultdict(set)
    for item in images:
        by_stem[Path(item["package_path"]).stem.lower()].add(item["image_url"])
    for stem, urls in by_stem.items():
        if len(urls) != 1:
            continue
        url = next(iter(urls))
        for extension in (".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif"):
            aliases.setdefault(f"{stem}{extension}", url)
    target = OUTPUT / pack / "texture-aliases.json"
    target.write_text(json.dumps(aliases, ensure_ascii=False, indent=2) + "\n")
    if collisions:
        (OUTPUT / pack / "texture-alias-collisions.json").write_text(json.dumps(collisions, ensure_ascii=False, indent=2) + "\n")
    return aliases


def apply_reviewed_models(entries: list[dict], aliases_by_pack: dict[str, dict[str, str]]) -> None:
    if REVIEWED_MODELS.is_file():
        reviewed = json.loads(REVIEWED_MODELS.read_text())
        by_id = {item["id"]: item for item in entries}
        known_images = {item["image_url"] for item in entries if item.get("image_url")}
        for conversion in reviewed:
            item = by_id.get(conversion["id"])
            if item is None or item["kind"] != "model":
                raise ValueError(f"Reviewed model has no source FBX: {conversion['id']}")
            model_path = ROOT / conversion.get("source_path", item["source_path"])
            if not model_path.is_file():
                raise ValueError(f"Reviewed model is missing: {model_path}")
            item.update({key: value for key, value in conversion.items()
                         if key not in {"id", "texture_alias_overrides"}})
            item["bytes"] = model_path.stat().st_size
            overrides = conversion.get("texture_alias_overrides")
            if overrides:
                pack = item["id"].split(":")[1]
                if not isinstance(overrides, dict) or any(
                    not isinstance(key, str) or not isinstance(url, str) or url not in known_images
                    for key, url in overrides.items()
                ):
                    raise ValueError(f"Invalid source texture override: {item['id']}")
                scoped_aliases = {**aliases_by_pack[pack],
                                  **{key.lower(): url for key, url in overrides.items()}}
                alias_path = OUTPUT / pack / "texture-aliases" / f"{item['id'].split(':')[-1]}.json"
                alias_path.parent.mkdir(parents=True, exist_ok=True)
                alias_path.write_text(json.dumps(scoped_aliases, ensure_ascii=False, indent=2) + "\n")
                item["texture_alias_url"] = f"/models/incoming/{pack}/texture-aliases/{alias_path.name}"
            elif not item.get("model_url") or str(item["model_url"]).lower().endswith(".glb"):
                item.pop("texture_alias_url", None)


def write_manifest(packages: list[dict], entries: list[dict]) -> None:
    target = OUTPUT / "manifest.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"schema_version": "aero-bench.incoming-assets/v1",
                                     "packages": packages, "entries": entries}, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(target)


def refresh_existing() -> None:
    """Refresh catalog metadata from a previously complete source extraction."""
    manifest = json.loads((OUTPUT / "manifest.json").read_text())
    if manifest.get("schema_version") != "aero-bench.incoming-assets/v1":
        raise ValueError("Incoming manifest schema changed; run a full source extraction")
    entries = manifest["entries"]
    if len(entries) != sum(package["entries"] for package in manifest["packages"]):
        raise ValueError("Incoming manifest is incomplete; run a full source extraction")
    if len({item["id"] for item in entries}) != len(entries):
        raise ValueError("Incoming manifest contains duplicate IDs")
    packages = manifest["packages"]
    expected_packs = {pack for pack, _, _ in PACKAGES}
    if {item["name"] for item in packages} != {filename for _, filename, _ in PACKAGES} or len(packages) != len(PACKAGES):
        raise ValueError("Incoming manifest has unexpected source packages")
    if any(item["id"].split(":")[1] not in expected_packs for item in entries):
        raise ValueError("Incoming manifest has entries from an unexpected package")
    aliases_by_pack: dict[str, dict[str, str]] = {}
    for pack, filename, _ in PACKAGES:
        package = next((item for item in packages if item["name"] == filename), None)
        if package is None or not (ROOT.parent / package["path"]).is_file():
            raise ValueError(f"Missing indexed Unity source package: {filename}")
        source_stat = (ROOT.parent / package["path"]).stat()
        if "source_bytes" not in package or "source_mtime_ns" not in package:
            raise ValueError(f"Unity source package lacks provenance; run a full source extraction: {filename}")
        if package["source_bytes"] != source_stat.st_size or package["source_mtime_ns"] != source_stat.st_mtime_ns:
            raise ValueError(f"Unity source package changed; run a full source extraction: {filename}")
        rows = [item for item in entries if item["id"].split(":")[1] == pack]
        if len(rows) != package["entries"]:
            raise ValueError(f"Incomplete {filename} index; run a full source extraction")
        for item in rows:
            if Path(item["package_path"]).suffix.lower() == ".fbx" and category_for_fbx(pack, item["package_path"]):
                guid = item["id"].split(":")[-1]
                fbx_path = OUTPUT / pack / "fbx" / f"{guid}.fbx"
                if not fbx_path.is_file():
                    raise ValueError(f"Missing indexed FBX: {fbx_path}")
                item.update({"kind": "model", "status": "ready", "format": "FBX",
                             "source_path": str(fbx_path.relative_to(ROOT)),
                             "model_url": f"/models/incoming/{pack}/fbx/{guid}.fbx",
                             "bytes": fbx_path.stat().st_size,
                             "preview_exposure": 1.0 if pack == "urban-traffic" else 1.25,
                             "material_origin": "源 FBX 材质及可匹配的原包贴图；Unity Material 配置尚未完整还原",
                             "texture_alias_url": f"/models/incoming/{pack}/texture-aliases.json"})
                item.pop("image_url", None)
                item.pop("material_overrides", None)
                item.pop("animation_clip", None)
            item["subgroup"] = subgroup_for(pack, item["package_path"], item["category"], item["kind"])
            if pack == "urban-traffic" and item["kind"] == "model" and item["format"] == "FBX":
                item["note"] = ("可旋转的源 FBX；车辆只显示 LOD0 和无级别部件。外置贴图按包内文件名映射；"
                                "Unity Prefab 动作和材质配置需单独复核。")
        aliases_by_pack[pack] = texture_aliases(pack, rows)
        package["texture_aliases"] = len(aliases_by_pack[pack])
    apply_reviewed_models(entries, aliases_by_pack)
    write_manifest(packages, entries)
    print(f"Refreshed {len(entries)} indexed assets and {sum(bool(item.get('model_url')) for item in entries)} browser models")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries = []
    packages = []
    aliases_by_pack: dict[str, dict[str, str]] = {}
    for pack, filename, category in PACKAGES:
        rows, source_relative = extract_package(pack, filename, category)
        aliases = texture_aliases(pack, rows)
        aliases_by_pack[pack] = aliases
        entries.extend(rows)
        source_stat = package_path(filename).stat()
        packages.append({"name": filename, "path": f"validation/downloaded-assets/极速下载好的文件/{source_relative}",
                         "source_bytes": source_stat.st_size, "source_mtime_ns": source_stat.st_mtime_ns,
                         "entries": len(rows), "browser_models": sum(bool(row.get("model_url")) for row in rows),
                         "texture_aliases": len(aliases)})
        print(f"{filename}: {len(rows)} assets, {packages[-1]['browser_models']} browser models, {len(aliases)} texture aliases", flush=True)
    entries.sort(key=lambda item: item["id"])
    apply_reviewed_models(entries, aliases_by_pack)
    write_manifest(packages, entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-reviewed", action="store_true",
                        help="apply reviewed models and current subgroups to an existing complete source index")
    options = parser.parse_args()
    refresh_existing() if options.refresh_reviewed else main()
