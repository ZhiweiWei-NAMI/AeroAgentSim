"""Verify browser copies preserve supplied model geometry and texture pixels."""

from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path

from PIL import Image, ImageChops


SCRIPT = Path(__file__).with_name("optimize-city-models.py")
SPEC = importlib.util.spec_from_file_location("optimize_city_models", SCRIPT)
assert SPEC and SPEC.loader
OPTIMIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPTIMIZER)


class CityModelOptimizationTest(unittest.TestCase):
    def test_geometry_and_texture_pixels_match_supplied_models(self) -> None:
        for relative in OPTIMIZER.SOURCES:
            with self.subTest(model=relative):
                source, source_bin = OPTIMIZER.read_glb(OPTIMIZER.PUBLIC / relative)
                result, result_bin = OPTIMIZER.read_glb(
                    OPTIMIZER.OUTPUT / (OPTIMIZER.PUBLIC / relative).name
                )
                self.assertEqual(len(source["accessors"]), len(result["accessors"]))
                self.assertEqual(len(source.get("images", [])), len(result.get("images", [])))
                self.assertEqual(source.get("animations"), result.get("animations"))
                for old, new in zip(source["accessors"], result["accessors"], strict=True):
                    if "bufferView" not in old:
                        continue
                    old_view = source["bufferViews"][old["bufferView"]]
                    new_view = result["bufferViews"][new["bufferView"]]
                    old_at = old_view.get("byteOffset", 0)
                    new_at = new_view.get("byteOffset", 0)
                    self.assertEqual(
                        source_bin[old_at:old_at + old_view["byteLength"]],
                        result_bin[new_at:new_at + new_view["byteLength"]],
                    )
                for old, new in zip(source.get("images", []), result.get("images", []), strict=True):
                    view = source["bufferViews"][old["bufferView"]]
                    offset = view.get("byteOffset", 0)
                    source_image = source_bin[offset:offset + view["byteLength"]]
                    with Image.open(io.BytesIO(source_image)) as before, Image.open(
                        OPTIMIZER.OUTPUT / new["uri"]
                    ) as after:
                        difference = ImageChops.difference(before.convert("RGBA"), after.convert("RGBA"))
                        self.assertIsNone(difference.getbbox())


if __name__ == "__main__":
    unittest.main()
