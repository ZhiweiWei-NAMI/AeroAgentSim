#!/usr/bin/env python3
"""Bake alphaMap.g into map.a while retaining the source RGB bytes."""

import sys
from pathlib import Path

import numpy as np
from PIL import Image

source, output = map(Path, sys.argv[1:3])
rgba = np.asarray(Image.open(source).convert("RGBA"), dtype=np.uint16).copy()
rgba[:, :, 3] = (rgba[:, :, 3] * rgba[:, :, 1] + 127) // 255

# GLTFExporter flips source textures vertically when writing a GLB.
Image.fromarray(rgba.astype(np.uint8), "RGBA").transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(output, format="PNG")
print(f"Baked {source.name} -> {output} ({rgba.shape[1]}x{rgba.shape[0]})")
