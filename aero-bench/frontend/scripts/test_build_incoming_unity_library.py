"""Tests for refreshing a fully indexed Unity asset catalog."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("build-incoming-unity-library.py")
SPEC = importlib.util.spec_from_file_location("build_incoming_unity_library", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RefreshIncomingCatalogTest(unittest.TestCase):
    def test_restores_indexed_fbx_then_applies_reviewed_models_without_package_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            frontend = repository / "frontend"
            output = frontend / "public/models/incoming"
            (repository / "validation/downloaded-assets").mkdir(parents=True)
            source_package = repository / "validation/downloaded-assets/Urban.unitypackage"
            source_package.write_bytes(b"source package")
            source_stat = source_package.stat()
            fbx_folder = output / "urban-traffic/fbx"
            glb_folder = output / "urban-traffic/glb"
            fbx_folder.mkdir(parents=True)
            glb_folder.mkdir(parents=True)
            bike_id, gyro_id, image_id = "a" * 32, "b" * 32, "c" * 32
            (fbx_folder / f"{bike_id}.fbx").write_bytes(b"original bicycle")
            (fbx_folder / f"{gyro_id}.fbx").write_bytes(b"original scooter")
            (glb_folder / "bike.glb").write_bytes(b"reviewed bicycle")
            image_url = f"/models/incoming/urban-traffic/images/{image_id}.webp"
            image_path = output / "urban-traffic/images" / f"{image_id}.webp"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"image")
            entries = [
                {"id": f"incoming:urban-traffic:{bike_id}", "category": "vehicle",
                 "subgroup": "城市交通 · 乘用车", "kind": "image", "format": "PNG 预览 / FBX 源",
                 "package_path": "Assets/UTS_PRO/Models/Cars/Models/Bicycle_Man_34.FBX",
                 "source_path": f"public/models/incoming/urban-traffic/fbx/{bike_id}.fbx",
                 "model_url": None, "image_url": "/models/incoming/urban-traffic/previews/a.png"},
                {"id": f"incoming:urban-traffic:{gyro_id}", "category": "vehicle",
                 "subgroup": "城市交通 · 乘用车", "kind": "model", "format": "FBX",
                 "package_path": "Assets/UTS_PRO/Models/Cars/Models/Gyroscooter_2_Girl_11.FBX",
                 "source_path": f"public/models/incoming/urban-traffic/fbx/{gyro_id}.fbx"},
                {"id": f"incoming:urban-traffic:{image_id}", "category": "vehicle",
                 "subgroup": "城市交通 · 乘用车", "kind": "texture", "format": "PNG",
                 "package_path": "Assets/UTS_PRO/Textures/hair.png", "image_url": image_url},
            ]
            output.mkdir(parents=True, exist_ok=True)
            (output / "manifest.json").write_text(json.dumps({
                "schema_version": "aero-bench.incoming-assets/v1",
                "packages": [{"name": "Urban.unitypackage", "path": "validation/downloaded-assets/Urban.unitypackage",
                              "source_bytes": source_stat.st_size, "source_mtime_ns": source_stat.st_mtime_ns,
                              "entries": 3, "browser_models": 2, "texture_aliases": 0}],
                "entries": entries,
            }))
            reviewed = frontend / "scripts/incoming-reviewed-models.json"
            reviewed.parent.mkdir(parents=True)
            reviewed.write_text(json.dumps([
                {"id": f"incoming:urban-traffic:{bike_id}", "kind": "model", "format": "GLB",
                 "source_path": "public/models/incoming/urban-traffic/glb/bike.glb",
                 "model_url": "/models/incoming/urban-traffic/glb/bike.glb",
                 "note": "只含自行车"},
                {"id": f"incoming:urban-traffic:{gyro_id}",
                 "texture_alias_overrides": {"missing_hair.tga": image_url},
                 "material_overrides": {"hair11": {"alphaMap": None, "alphaTest": 0.5}}},
            ]))
            with patch.multiple(MODULE, ROOT=frontend, OUTPUT=output, REVIEWED_MODELS=reviewed,
                                PACKAGES=(("urban-traffic", "Urban.unitypackage", "vehicle"),)), \
                    patch.object(MODULE, "extract_package", side_effect=AssertionError("source rescan")):
                MODULE.refresh_existing()
                valid_manifest = json.loads((output / "manifest.json").read_text())
                unsigned = json.loads(json.dumps(valid_manifest))
                unsigned["packages"][0].pop("source_bytes")
                unsigned["packages"][0].pop("source_mtime_ns")
                (output / "manifest.json").write_text(json.dumps(unsigned))
                with self.assertRaisesRegex(ValueError, "lacks provenance"):
                    MODULE.refresh_existing()
                rogue = json.loads(json.dumps(valid_manifest))
                rogue["packages"].append({"name": "Rogue.unitypackage", "entries": 1})
                rogue["entries"].append({"id": "incoming:rogue:" + "d" * 32})
                (output / "manifest.json").write_text(json.dumps(rogue))
                with self.assertRaisesRegex(ValueError, "unexpected source packages"):
                    MODULE.refresh_existing()
                (output / "manifest.json").write_text(json.dumps(valid_manifest))
                source_package.write_bytes(b"changed source package")
                with self.assertRaisesRegex(ValueError, "source package changed"):
                    MODULE.refresh_existing()
            refreshed = json.loads((output / "manifest.json").read_text())
            indexed = {item["id"]: item for item in refreshed["entries"]}
            bike = indexed[f"incoming:urban-traffic:{bike_id}"]
            self.assertEqual(bike["model_url"], "/models/incoming/urban-traffic/glb/bike.glb")
            self.assertEqual(bike["kind"], "model")
            self.assertNotIn("image_url", bike)
            self.assertEqual(bike["subgroup"], "城市交通 · 两轮与微出行")
            gyro = indexed[f"incoming:urban-traffic:{gyro_id}"]
            self.assertEqual(gyro["material_overrides"]["hair11"]["alphaTest"], 0.5)
            self.assertEqual(gyro["subgroup"], "城市交通 · 两轮与微出行")
            scoped = output / "urban-traffic/texture-aliases" / f"{gyro_id}.json"
            self.assertEqual(json.loads(scoped.read_text())["missing_hair.tga"], image_url)


if __name__ == "__main__":
    unittest.main()
