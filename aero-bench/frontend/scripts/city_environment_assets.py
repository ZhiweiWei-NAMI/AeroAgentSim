"""Preserve the supplied TIFF's fourth channel that the RGB preview dropped."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

from PIL import Image

TREE_GUID = "53cd429b20f63264ca316e2c356575e9"


def decode_rgba_tiff(raw: bytes) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as image:
        tags = image.tag_v2
        if (image.format != "TIFF" or tags[277] != 4 or tuple(tags[258]) != (8, 8, 8, 8)
                or tags[259] != 1 or tags[262] != 2 or tags.get(284, 1) != 1 or tags.get(274, 1) != 1):
            raise ValueError("Supplied Tree TIFF channel layout changed; re-inspect the source")
        offsets, counts = tags[273], tags[279]
        if len(offsets) != len(counts):
            raise ValueError("Tree TIFF strip metadata is invalid")
        pixels = b"".join(raw[offset:offset + count] for offset, count in zip(offsets, counts))
        if len(pixels) != image.width * image.height * 4:
            raise ValueError("Tree TIFF strip bytes are incomplete")
        # RGB's unspecified fourth TIFF sample is the original Unity alpha channel.
        # Keep every value; do not derive transparency from colour or thresholds.
        result = Image.frombytes("RGBA", image.size, pixels)
    if result.getchannel("A").getextrema() != (0, 255):
        raise ValueError("Supplied Tree TIFF no longer has its source transparency")
    return result


def derive(package: Path, output: Path) -> dict:
    contents = {}
    with gzip.open(package, "rb") as stream, tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            guid, _, part = member.name.partition("/")
            if guid == TREE_GUID and part in {"asset", "asset.meta", "pathname"}:
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError("Tree source entry is not a file")
                contents[part] = handle.read()
                if len(contents) == 3:
                    break
    if set(contents) != {"asset", "asset.meta", "pathname"}:
        raise ValueError("Supplied Tree TIFF or metadata is unavailable")
    if contents["pathname"].decode().splitlines()[0] != "Assets/Package/Texture/Tree.tif" or b"alphaUsage: 1" not in contents["asset.meta"]:
        raise ValueError("Source importer no longer uses Tree TIFF alpha")
    image = decode_rgba_tiff(contents["asset"])
    alpha = image.getchannel("A").histogram()
    native_size = image.size
    image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "tree-atlas-v1.webp"
    image.save(target, "WEBP", quality=85, method=4, exact=True)
    report = {"schemaVersion": "aero-bench.city-environment-texture/v1", "sourceGuid": TREE_GUID,
              "sourcePath": "Assets/Package/Texture/Tree.tif", "sourceSha256": hashlib.sha256(contents["asset"]).hexdigest(),
              "sourceMetadataSha256": hashlib.sha256(contents["asset.meta"]).hexdigest(),
              "nativeSize": native_size, "nativeAlphaZeroPixels": alpha[0], "nativeAlphaOpaquePixels": alpha[255],
              "alphaSource": "Original TIFF fourth sample; Unity alphaUsage=1", "size": image.size,
              "mode": image.mode, "bytes": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    (output / "tree-atlas-v1.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.package, args.output)))
