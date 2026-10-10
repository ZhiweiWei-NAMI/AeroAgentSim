"""Module tests for the source summary builder using the recorded canonical inputs."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("build-building-render-source-context.py")
SPEC = importlib.util.spec_from_file_location("building_render_source_context", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
ROOT = Path(__file__).resolve().parents[2]


class SourceContextBuilderTests(unittest.TestCase):
    def inputs(self) -> dict:
        return {
            "scene_id": "shanghai-huangpu-east-v1",
            "objects_path": ROOT / "validation/scene-compiler-shanghai-huangpu-east-v1/metadata/objects.json",
            "scaleout_path": ROOT / "validation/building-render-scaleout-mimo-20260929/manifest.json",
            "fragment_path": ROOT / "validation/building-render-scaleout-mimo-20260929/building-render-catalog-fragment.json",
            "pack_path": ROOT / "frontend/public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json",
            "mesh_source_path": ROOT / "validation/scene-compiler-shanghai-huangpu-east-v1/osm/effective.osm.json",
            "canonical_assets": ROOT / "validation/building-render-scaleout-mimo-20260929/assets",
        }

    def changed_input(self, key: str, change, error: str) -> None:
        inputs = self.inputs()
        data = json.loads(inputs[key].read_text())
        change(data)
        with tempfile.TemporaryDirectory(prefix="building-context-builder-test-") as directory:
            path = Path(directory) / "changed.json"
            path.write_text(json.dumps(data))
            inputs[key] = path
            with self.assertRaisesRegex(ValueError, error):
                builder.build_context(**inputs)

    def test_real_canonical_source_and_pack_inventory(self) -> None:
        context = builder.build_context(**self.inputs())
        self.assertEqual(len(context["buildings"]), 414)
        self.assertEqual(context["sources"]["scaleout_glb_combined_sha256"],
                         "ab6c82860157928e85f5a26b6baec1144b98915901c383873aaecf3acfbf256a")
        self.assertEqual(sum(entry["geometry"]["vertices"] for entry in context["buildings"]), 10720)
        self.assertEqual(sum(entry["geometry"]["triangles"] for entry in context["buildings"]), 5604)
        self.assertEqual(sum(bool(entry["pack_target_ids"]) for entry in context["buildings"]), 410)

    def test_wrong_mesh_source_bytes(self) -> None:
        self.changed_input("mesh_source_path", lambda data: data.update(extra="changed"), "supplied verified source bytes")

    def test_scaleout_origin_mismatch(self) -> None:
        self.changed_input("scaleout_path", lambda data: data["scene"]["origin_wgs84"].update(latitude_deg=30), "scaleout origin")

    def test_objects_digest_mismatch(self) -> None:
        self.changed_input("scaleout_path", lambda data: data["scene"].update(objects_json_sha256="1" * 64), "objects digest")

    def test_duplicate_canonical_objects(self) -> None:
        self.changed_input("scaleout_path", lambda data: data["buildings"].append(data["buildings"][0]), "duplicate object ids")

    def test_canonical_asset_digest_mismatch(self) -> None:
        self.changed_input("scaleout_path", lambda data: data["buildings"][0].update(glb_sha256="2" * 64), "canonical digest/bytes")

    def test_catalog_asset_mismatch(self) -> None:
        self.changed_input("fragment_path", lambda data: data["glb_digests"][next(iter(data["glb_digests"]))].update(bytes=1), "catalog canonical")

    def test_pack_origin_mismatch(self) -> None:
        self.changed_input("pack_path", lambda data: data["projection"]["origin"].update(longitude_deg=120), "mesh pack origin")

    def test_pack_target_representation_collision(self) -> None:
        def collide(data: dict) -> None:
            item = next(item for item in data["objects"] if item["id"].startswith("w")
                        and abs(int(item["id"][1:])) > 2 ** 56)
            data["objects"].append({"id": item["id"][0] + str(int(float(item["id"][1:])) + 1), "tags": dict(item["tags"])})
        self.changed_input("pack_path", collide, "collide in the target representation")


if __name__ == "__main__":
    unittest.main()
