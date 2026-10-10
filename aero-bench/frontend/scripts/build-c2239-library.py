"""Stage and catalog the supplied C2239 OBJ/MTL street furniture pack.

Use ``stage`` before the browser GLB converter, then ``finalize`` after it.
The staging source files can be removed with ``clean-stage`` after validation;
the original ZIPs and the source inventory remain untouched.
"""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT.parent / "validation/downloaded-assets/极速下载好的文件"
OUTPUT = ROOT / "public/models/incoming/furniture"
INVENTORY = OUTPUT / "source-inventory.json"
CATEGORY_NAMES = {
    "barrier": "护栏与路障", "billboard": "广告牌", "traffic_light": "交通信号灯",
    "bin": "垃圾桶", "street_light": "道路照明", "bus_stop": "公交站",
    "street_pointer": "路牌指示", "bench": "座椅", "street_lamp": "街道照明",
    "fence": "围栏", "wall_billboard": "墙面广告牌", "street_technique": "市政设备",
    "hydrant": "消防栓", "pillar": "立柱",
}


def source_zip(filename: str) -> Path:
    matches = list(BASE.rglob(filename))
    if len(matches) != 1:
        raise ValueError(f"Expected one {filename}, found {len(matches)}")
    return matches[0]


def inventory() -> list[dict]:
    data = json.loads(INVENTORY.read_text())
    assets = data["assets"]
    if len(assets) != 100 or {asset["category"] for asset in assets} != set(CATEGORY_NAMES):
        raise ValueError("C2239 inventory must contain 100 models in 14 categories")
    return assets


def stage() -> None:
    assets = inventory()
    obj_zip = source_zip("OBJ-C2239.zip")
    preview_zip = source_zip("目录-C2239.zip")
    with zipfile.ZipFile(obj_zip) as models, zipfile.ZipFile(preview_zip) as previews:
        for asset in assets:
            for key in ("obj", "mtl"):
                member = asset[key].removeprefix("source/")
                target = OUTPUT / "source" / Path(member).name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(models.read(member))
                if target.stat().st_size != asset[f"{key}_bytes"]:
                    raise ValueError(f"C2239 source size mismatch: {member}")
            member = asset["preview_png"].removeprefix("source/")
            target = OUTPUT / "previews" / f"{asset['id']}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(previews.read(member))
            if target.stat().st_size != asset["png_bytes"]:
                raise ValueError(f"C2239 preview size mismatch: {member}")
    print("Staged 100 OBJ, 100 MTL and 100 PNG previews")


def finalize() -> None:
    assets = inventory()
    obj_zip = source_zip("OBJ-C2239.zip")
    relative_zip = str(obj_zip.relative_to(ROOT.parent)).replace("\\", "/")
    entries = []
    for asset in assets:
        model = OUTPUT / "glb" / f"{asset['id']}.glb"
        if not model.is_file() or model.stat().st_size == 0:
            raise ValueError(f"C2239 converted GLB is missing: {model}")
        preview = OUTPUT / "previews" / f"{asset['id']}.png"
        if not preview.is_file():
            raise ValueError(f"C2239 preview is missing: {preview}")
        source_member = asset["obj"].removeprefix("source/")
        entries.append({
            "id": f"incoming:furniture:{asset['id']}", "category": "city",
            "subgroup": f"C2239 · {CATEGORY_NAMES[asset['category']]}", "title": asset["id"],
            "kind": "model", "status": "ready", "format": "GLB",
            "source_path": f"public/models/incoming/furniture/glb/{asset['id']}.glb",
            "origin_path": f"{relative_zip}#{source_member}",
            "model_url": f"/models/incoming/furniture/glb/{asset['id']}.glb",
            "preview_url": f"/models/incoming/furniture/previews/{asset['id']}.png",
            "preview_exposure": 1.1,
            "material_origin": "源 OBJ 的 MTL 漫反射色；镜面和光泽转换为近似 PBR 参数",
            "note": "源 OBJ/MTL 已转 GLB。PNG 是目录展示图，不是贴在模型表面的纹理。源文件标注厘米单位，预览按米缩放。",
            "bytes": model.stat().st_size,
            "details": {"source_faces": asset["faces"], "source_materials": asset["used_material_count"]},
        })
    (OUTPUT / "manifest.json").write_text(json.dumps({"schema_version": "aero-bench.c2239/v1",
                                                  "source_archive": relative_zip, "entries": entries},
                                                 ensure_ascii=False, indent=2) + "\n")
    print(f"Cataloged {len(entries)} street furniture GLBs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("stage", "finalize", "clean-stage"))
    args = parser.parse_args()
    if args.command == "stage":
        stage()
    elif args.command == "finalize":
        finalize()
    else:
        shutil.rmtree(OUTPUT / "source")


if __name__ == "__main__":
    main()
