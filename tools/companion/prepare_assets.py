#!/usr/bin/env python3
"""Prepare offline scene resources and a bounded, real SeaDronesSee ODv2 subset.

No training corpus is downloaded. Dataset labels are evaluation-only and must
never be mounted into a companion or passed to its detector.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
from urllib.parse import quote

from prepare_models import download, file_record, write_json

MAKEHUMAN_REVISION = "a8bc2d54ff0ac92e78ff71431b1023eda42bf482"
MAKEHUMAN_RAW = f"https://raw.githubusercontent.com/makehumancommunity/makehuman/{MAKEHUMAN_REVISION}/"
# This is the author's public share ID, published by /dataset/ODv2_redirecter.
# It is not an account password. The share is read-only and needs no user login.
SDS_PUBLIC_SHARE = "ZZxX65FGnQ8zjBP"
SDS_DAV = "https://cloud.cs.uni-tuebingen.de/public.php/webdav/"
SDS_LICENSE = "https://raw.githubusercontent.com/Ben93kie/SeaDronesSee/main/README.md"


def public_dataset_file(remote: str, local: Path) -> None:
    if local.is_file() and local.stat().st_size:
        return
    local.parent.mkdir(parents=True, exist_ok=True)
    part = local.with_suffix(local.suffix + ".partial")
    subprocess.run(["curl", "--fail", "--location", "--retry", "2", "--max-time", "180",
                    "--user", SDS_PUBLIC_SHARE + ":", "--output", str(part),
                    SDS_DAV + quote(remote, safe="/")], check=True)
    part.replace(local)


def prepare_human(cache: Path) -> dict[str, object]:
    root = cache / "scene" / "makehuman_person"
    mesh = root / "meshes"
    mesh.mkdir(parents=True, exist_ok=True)
    source = root / "source-base.obj"
    download(MAKEHUMAN_RAW + "makehuman/data/3dobjs/base.obj", source)
    download(MAKEHUMAN_RAW + "LICENSE.ASSETS.md", root / "LICENSE.ASSETS.md")
    vertices, faces, body = [], [], False
    for line in source.read_text().splitlines():
        if line.startswith("v "):
            vertices.append(tuple(float(x) for x in line.split()[1:4]))
        elif line.startswith("g "):
            body = line.strip() == "g body"
        elif line.startswith("f ") and body:
            faces.append([int(x.split("/")[0]) - 1 for x in line.split()[1:]])
    used = sorted({v for face in faces for v in face})
    lookup = {old: new + 1 for new, old in enumerate(used)}
    floor = min(vertices[v][1] for v in used)
    height = max(vertices[v][1] for v in used) - floor
    scale = 1.75 / height
    converted = [(vertices[i][0] * scale, -vertices[i][2] * scale,
                  (vertices[i][1] - floor) * scale) for i in used]
    lines = ["# Derived from MakeHuman core CC0 base; helper meshes removed.",
             "# Changed: Y-up to Z-up; grounded; scaled to 1.75 m; solid materials.",
             "mtllib person.mtl", "o makehuman_person"]
    lines += [f"v {x:.7f} {y:.7f} {z:.7f}" for x, y, z in converted]
    previous = None
    for face in faces:
        z = sum((vertices[i][1] - floor) * scale for i in face) / len(face)
        material = "skin" if z > 1.49 else ("shirt" if z > 0.85 else "trousers")
        if material != previous:
            lines.append("usemtl " + material)
            previous = material
        # Explicit triangulation avoids importer-dependent polygon handling.
        for i in range(1, len(face) - 1):
            lines.append("f " + " ".join(str(lookup[v]) for v in (face[0], face[i], face[i + 1])))
    (mesh / "person.obj").write_text("\n".join(lines) + "\n")
    (mesh / "person.mtl").write_text(
        "newmtl skin\nKa 0.55 0.35 0.24\nKd 0.65 0.43 0.31\n"
        "newmtl shirt\nKa 0.8 0.2 0.05\nKd 0.95 0.3 0.08\n"
        "newmtl trousers\nKa 0.04 0.07 0.12\nKd 0.08 0.12 0.2\n")
    (root / "model.config").write_text(
        '<model><name>makehuman_person</name><version>1.0</version>'
        '<sdf version="1.9">model.sdf</sdf><description>CC0 MakeHuman core visual mesh; no dynamic body.</description></model>\n')
    (root / "model.sdf").write_text(
        '<sdf version="1.9"><model name="makehuman_person"><static>true</static>'
        '<link name="body"><visual name="human"><geometry><mesh>'
        '<uri>meshes/person.obj</uri></mesh></geometry></visual></link></model></sdf>\n')
    return {"name": "makehuman_person", "license": "CC0-1.0",
            "source": MAKEHUMAN_RAW + "makehuman/data/3dobjs/base.obj",
            "revision": MAKEHUMAN_REVISION, "height_m": 1.75,
            "vertices": len(used), "body_polygons": len(faces),
            "adaptation": "body-only, Y-up to Z-up, grounded, scaled, solid clothing colors",
            "render_status": "NOT_RUN: actual Gazebo camera verification required",
            "physics": "visual static person; parcel and receiver require separate collision models",
            "files": [file_record(p) for p in sorted(root.rglob("*")) if p.is_file()]}


def prepare_dataset(cache: Path, count: int) -> dict[str, object]:
    from PIL import Image

    if not 10 <= count <= 200:
        raise ValueError("This bounded preparation supports 10 to 200 evaluation images")
    root = cache / "seadronessee-odv2"
    root.mkdir(parents=True, exist_ok=True)
    annotations = root / "instances_val.json"
    public_dataset_file("Uncompressed Version/annotations/instances_val.json", annotations)
    download(SDS_LICENSE, root / "AUTHOR-README-LICENSE.md")
    data = json.loads(annotations.read_text())
    categories = {c["id"]: c["name"] for c in data["categories"]}
    swimmer = next(k for k, v in categories.items() if v.lower() == "swimmer")
    by_image = defaultdict(list)
    for annotation in data["annotations"]:
        by_image[annotation["image_id"]].append(annotation)
    # Freeze before any inference. Spread selection through each source video
    # when metadata is available instead of taking neighbouring initial frames.
    images = sorted(data["images"], key=lambda x: x["id"])
    negative = [im for im in images if not any(a["category_id"] == swimmer for a in by_image[im["id"]])]
    confuser = [im for im in images if any(a["category_id"] != swimmer for a in by_image[im["id"]])]
    target_negatives = min(50, count // 4)

    def spread(items: list, n: int) -> list:
        if not items or n <= 0:
            return []
        if len(items) <= n:
            return items
        return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)] if n > 1 else [items[0]]

    selected = spread(negative, target_negatives)
    seen = {im["id"] for im in selected}
    if len(selected) < target_negatives:
        selected += spread([im for im in confuser if im["id"] not in seen], target_negatives - len(selected))
    seen = {im["id"] for im in selected}
    selected += spread([im for im in images if im["id"] not in seen], count - len(selected))
    selected = sorted(selected, key=lambda x: x["id"])
    manifest = {"dataset": "SeaDronesSee Object Detection v2", "split": "official validation",
                "license": "CC0-1.0", "selection_before_inference": True,
                "count": len(selected), "negative_count": sum(im in negative for im in selected),
                "negative_or_confuser_count": sum(im in negative or im in confuser for im in selected),
                "mapping": {"detector_person_label": 0, "dataset_swimmer_category": swimmer,
                            "ignored_dataset_categories": {str(k): v for k, v in categories.items() if k != swimmer}},
                "categories": data["categories"], "images": selected,
                "scope": "external real-image evaluation; separate from rooftop and localization acceptance",
                "split_limitation": "No model adaptation performed. Official validation split retained; video-disjoint training comparison is not established by this manifest."}
    frozen = root / f"frozen-{count}-manifest.json"
    if frozen.exists() and json.loads(frozen.read_text())["images"] != selected:
        raise RuntimeError("Existing frozen image selection differs; preserve it and choose a new cache")
    write_json(frozen, manifest)

    def fetch_image(im: dict) -> dict:
        original = im["file_name"]
        name = Path(original).with_suffix(".jpg").name
        path = root / "images" / name
        public_dataset_file("Compressed Version/images/val/" + name, path)
        with Image.open(path) as image:
            image.load()
            dimensions = list(image.size)
        return {**file_record(path), "image_id": im["id"], "original_file_name": original,
                "width_height": dimensions,
                "source_url": SDS_DAV + quote("Compressed Version/images/val/" + name, safe="/")}

    with ThreadPoolExecutor(max_workers=4) as workers:
        files = list(workers.map(fetch_image, selected))
    selected_ids = {im["id"] for im in selected}
    write_json(root / f"subset-{count}-annotations.json", {
        **{k: v for k, v in data.items() if k not in {"images", "annotations"}},
        "images": selected, "annotations": [a for a in data["annotations"] if a["image_id"] in selected_ids],
    })
    write_json(root / f"downloaded-{count}.json", files)
    return {"dataset": manifest["dataset"], "license": "CC0-1.0", "count": len(files),
            "negative_count": manifest["negative_count"],
            "negative_or_confuser_count": manifest["negative_or_confuser_count"],
            "bytes": sum(f["bytes"] for f in files), "images": str(root / "images"),
            "frozen_manifest": str(frozen), "file_records": str(root / f"downloaded-{count}.json"),
            "annotations": str(root / f"subset-{count}-annotations.json"),
            "status": "DOWNLOADED_NOT_SCORED"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("data/companion-assets"))
    parser.add_argument("--lock", type=Path, default=Path("deploy/rescue/assets.lock.json"))
    parser.add_argument("--seadronessee-count", type=int, default=0)
    args = parser.parse_args()
    result: dict[str, object] = {"schema": "aeroagentsim.companion-assets/v1", "runtime_downloads": False}
    try:
        result["human"] = prepare_human(args.cache.resolve())
        if args.seadronessee_count:
            result["external_dataset"] = prepare_dataset(args.cache.resolve(), args.seadronessee_count)
    except Exception as exc:
        result["preparation_error"] = f"{type(exc).__name__}: {exc}"
        write_json(args.lock, result)
        raise
    write_json(args.lock, result)
    print(f"Prepared offline assets: {args.lock}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
