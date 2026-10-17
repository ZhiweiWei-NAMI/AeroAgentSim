#!/usr/bin/env python3
"""Crop the documented SeaDronesSee photo into the mock renderer's RGB sprite.

Uses an existing local image only. This performs no download or model inference.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


SOURCE_SIZE = (3840, 2160)
CROP_XYXY = (1581, 460, 2298, 1475)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="Existing SeaDronesSee compressed validation image 16787.jpg")
    parser.add_argument("--output", required=True, type=Path,
                        help="Destination PNG; parent directories are created if needed")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if source == output:
        parser.error("The output must differ from the source image")

    try:
        with Image.open(source) as image:
            if image.size != SOURCE_SIZE:
                parser.error(f"Expected source dimensions {SOURCE_SIZE}, got {image.size}")
            sprite = image.convert("RGB").crop(CROP_XYXY)
        output.parent.mkdir(parents=True, exist_ok=True)
        sprite.save(output, format="PNG")
    except OSError as error:
        parser.error(str(error))

    print(f"Wrote {output} ({sprite.width} x {sprite.height} RGB PNG)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
