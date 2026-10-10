"""Checks for BigCity's browser preview extraction helpers."""

import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).with_name("build-bigcity-library.py")
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("build_bigcity_library", SCRIPT)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class BigCityLibraryTests(unittest.TestCase):
    def test_groups_fbx_and_lightmaps_by_actual_resource_type(self):
        self.assertEqual(BUILDER.subgroup("Assets/Package/FBX/Modern Building 08.FBX"), "BigCity · 建筑")
        self.assertEqual(BUILDER.subgroup("Assets/Package/FBX/Road Part 06.FBX"), "BigCity · 道路与结构")
        self.assertEqual(BUILDER.subgroup("Assets/Package/Scene/Lightmap-0_comp_light.exr"), "BigCity · 烘焙光照")

    def test_image_preview_is_browser_readable_and_bounded(self):
        source = io.BytesIO()
        Image.new("RGB", (2000, 1000), "#446688").save(source, "PNG")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "preview.webp"
            BUILDER.save_image_preview(source.getvalue(), target)
            with Image.open(target) as image:
                self.assertEqual(image.size, (1280, 640))
                self.assertEqual(image.format, "WEBP")


if __name__ == "__main__":
    unittest.main()
