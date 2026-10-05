"""Generation layer contracts against a real canonical source GLB."""
from __future__ import annotations

import importlib.util
import contextlib
import copy
import hashlib
import json
import math
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).with_name("build-building-render-runtime.py")
ROOT = SCRIPT.parents[2]
SCENE = ROOT / "frontend/public/city-presentation/building-render-scene-v1.json"
SCENE_ROOT = ROOT / "validation/scene-compiler-shanghai-huangpu-east-v1"
CANONICAL_RENDER_DIR = ROOT / "validation/building-render-scaleout-mimo-20260929"
PACK_MANIFEST = ROOT / "frontend/public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json"
SOURCE_CONTEXT = (ROOT / "frontend/public/building-renders/shanghai-huangpu-east-v1/assets/"
                  "4e82facc9e7c0d22d19208a250e5ba27bbba201f85e7135551968ee02e68cbb2")
spec = importlib.util.spec_from_file_location("building_render_runtime", SCRIPT)
assert spec is not None and spec.loader is not None
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)


def command_arguments(*extra: str, output_dir: Path | None = None) -> list[str]:
    output = output_dir or ROOT / "validation/test-building-runtime/shanghai-huangpu-east-v1"
    return [
        "--scene", str(SCENE),
        "--source-context", str(SOURCE_CONTEXT),
        "--mesh-pack-manifest", str(PACK_MANIFEST),
        "--scene-root", str(SCENE_ROOT),
        "--canonical-render-dir", str(CANONICAL_RENDER_DIR),
        "--output-dir", str(output),
        "--scene-output", str(output.parent / "scene.json"),
        *extra,
    ]


class RegionBuildResolutionTests(unittest.TestCase):
    def test_resolves_region_identity_and_pins_from_scene(self) -> None:
        resolved = runtime.resolve_region_build(runtime.parse_options(command_arguments()))
        self.assertEqual(resolved.scene_id, "shanghai-huangpu-east-v1")
        self.assertEqual(resolved.expected_buildings, 414)
        self.assertEqual(resolved.objects_path, SCENE_ROOT / "metadata/objects.json")
        self.assertEqual(resolved.canonical_assets, CANONICAL_RENDER_DIR / "assets")
        self.assertEqual(
            resolved.pack_manifest_path,
            PACK_MANIFEST,
        )
        self.assertEqual(
            resolved.pins["objects_json"],
            "c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc",
        )

    def test_all_region_inputs_are_required(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runtime.parse_options([])

    def test_output_directory_must_name_the_scene(self) -> None:
        command = runtime.parse_options(command_arguments(
            output_dir=ROOT / "validation/test-building-runtime/wrong-region"))
        with self.assertRaisesRegex(runtime.BuildError, "must end with the explicit scene id"):
            runtime.resolve_region_build(command)

    def test_source_context_reference_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            scene = json.loads(SCENE.read_text())
            scene["building_render"]["source_context"]["size_bytes"] += 1
            scene_path = directory / "scene.json"
            scene_path.write_text(json.dumps(scene))
            arguments = command_arguments()
            arguments[1] = str(scene_path)
            with self.assertRaisesRegex(runtime.BuildError, "bytes differ from the scene reference"):
                runtime.resolve_region_build(runtime.parse_options(arguments))

    def test_explicit_mesh_pack_bytes_must_match_scene(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            changed = Path(raw_directory) / "manifest.json"
            document = json.loads(PACK_MANIFEST.read_text())
            document["source"]["size_bytes"] += 1
            changed.write_text(json.dumps(document))
            arguments = command_arguments()
            index = arguments.index("--mesh-pack-manifest") + 1
            arguments[index] = str(changed)
            with self.assertRaisesRegex(runtime.BuildError, "bytes differ from the scene reference"):
                runtime.resolve_region_build(runtime.parse_options(arguments))


class BuildingRenderLayersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        objects = json.loads((SCENE_ROOT / "metadata/objects.json").read_text())
        cls.building = sorted(objects["buildings"], key=lambda entry: entry["object_id"])[0]
        cls.label = cls.building["object_id"]
        cls.source = CANONICAL_RENDER_DIR / "assets" / f"{cls.label}.glb"
        cls.document, cls.binary = runtime.read_glb(cls.source)
        cls.ring = runtime.ring_from_footprint(cls.building["footprint_enu_m"])
        cls.anchor = runtime.footprint_centroid(cls.building["footprint_enu_m"])
        cls.height = cls.building["extrusion_height_m"]
        cls.base_up = cls.building["base_enu_up_m"]
        cls.style = cls.document["asset"]["extras"]["style"]

    def derive(self, options=None, *, label=None, ring=None, anchor=None):
        return runtime.derive_art_detail_glb(
            self.document, self.binary, label or self.label,
            ring=ring or self.ring, base_up=self.base_up, height=self.height,
            top_up=self.base_up + self.height, anchor=anchor or self.anchor,
            style=self.style,
            options=runtime.ArtDetailOptions() if options is None else options,
        )

    def test_default_roof_detail_preserves_canonical_facade(self) -> None:
        raw, _, stats = self.derive()
        document, binary = runtime.read_glb_bytes(raw, self.label)
        self.assertEqual(stats["art"]["layers"], {"roof_detail": True, "facade_relief": False})
        self.assertFalse(stats["entry"]["relief"])
        self.assertTrue(stats["entry"]["parapet"])
        for index in (0, 1, 3):
            self.assertEqual(runtime.read_accessor(self.document, self.binary, index, self.label),
                             runtime.read_accessor(document, binary, index, self.label))

    def test_facade_relief_requires_explicit_opt_in(self) -> None:
        self.assertFalse(runtime.parse_options(command_arguments()).art_detail.facade_relief)
        options = runtime.parse_options(command_arguments("--facade-relief")).art_detail
        self.assertTrue(options.facade_relief)
        _, _, roof = self.derive()
        _, _, full = self.derive(options)
        self.assertTrue(full["entry"]["relief"])
        self.assertGreater(full["entry"]["triangles"], roof["entry"]["triangles"])

    def test_disabled_layers_keep_all_source_geometry(self) -> None:
        options = runtime.parse_options(command_arguments(
            "--no-roof-detail", "--no-facade-relief")).art_detail
        raw, _, stats = self.derive(options)
        document, binary = runtime.read_glb_bytes(raw, self.label)
        self.assertFalse(stats["entry"]["parapet"])
        self.assertFalse(stats["entry"]["relief"])
        self.assertEqual(stats["entry"]["roof_structures"], 0)
        for index in (0, 1, 3, 4, 5, 6):
            self.assertEqual(runtime.read_accessor(self.document, self.binary, index, self.label),
                             runtime.read_accessor(document, binary, index, self.label))

    def test_reproducible_roof_art_uses_generic_enu_inputs(self) -> None:
        # Move the same verified local mesh to another map frame and use an
        # unrelated object label. No source-city IDs select a detail path.
        offset = (10000.0, -20000.0)
        ring = [(east + offset[0], north + offset[1]) for east, north in self.ring]
        anchor = (self.anchor[0] + offset[0], self.anchor[1] + offset[1])
        first, textures, stats = self.derive(label="region-x.building.17", ring=ring, anchor=anchor)
        second, repeated_textures, repeated_stats = self.derive(
            label="region-x.building.17", ring=ring, anchor=anchor)
        self.assertEqual(first, second)
        self.assertEqual(textures, repeated_textures)
        self.assertEqual(stats, repeated_stats)
        document, binary = runtime.read_glb_bytes(first, "region-x.building.17")
        lo, hi = runtime.position_bounds(document, binary)
        self.assertAlmostEqual(lo[1], 0.0)
        self.assertAlmostEqual(hi[1], self.height)
        for mesh in document["meshes"]:
            for primitive in mesh["primitives"]:
                for x, up, z in runtime.read_accessor(document, binary, primitive["attributes"]["POSITION"], "test"):
                    self.assertGreaterEqual(up, 0.0)
                    self.assertLessEqual(up, self.height)
                    self.assertTrue(runtime._inside_or_on((anchor[0] + x, anchor[1] - z), ring))

    def test_source_outline_drift_raises(self) -> None:
        ring = [(east + 2.0, north) for east, north in self.ring]
        with self.assertRaisesRegex(runtime.BuildError, "canonical facade disagrees"):
            self.derive(ring=ring)


class FacadeMetreTilingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.objects = json.loads((SCENE_ROOT / "metadata/objects.json").read_text())
        cls.source_manifest = json.loads((CANONICAL_RENDER_DIR / "manifest.json").read_text())
        cls.by_id = {entry["object_id"]: entry for entry in cls.objects["buildings"]}
        cls.samples = {}
        for entry in cls.source_manifest["buildings"]:
            cls.samples.setdefault(entry["style_id"], cls.by_id[entry["object_id"]])

    def source(self, style_id="modern_dark_grid"):
        building = self.samples[style_id]
        label = building["object_id"]
        document, binary = runtime.read_glb(CANONICAL_RENDER_DIR / "assets" / f"{label}.glb")
        return building, label, document, binary

    def derive(self, building, label, document, binary, *, options=None):
        ring = runtime.ring_from_footprint(building["footprint_enu_m"])
        anchor = runtime.footprint_centroid(building["footprint_enu_m"])
        return runtime.derive_art_detail_glb(
            document, binary, label, ring=ring, base_up=building["base_enu_up_m"],
            height=building["extrusion_height_m"], top_up=building["top_enu_up_m"],
            anchor=anchor, style=document["asset"]["extras"]["style"],
            options=runtime.ArtDetailOptions() if options is None else options,
        )

    def test_all_fifteen_source_styles_keep_spatial_and_texture_payloads(self):
        self.assertEqual(set(self.samples), set(runtime.FACADE_TILE_PROFILES))
        for style_id in sorted(self.samples):
            with self.subTest(style_id=style_id):
                building, label, source, source_bin = self.source(style_id)
                raw, extracted, stats = self.derive(building, label, source, source_bin)
                derived, derived_bin = runtime.read_glb_bytes(raw, label)
                for index in (0, 1, 3):
                    self.assertEqual(runtime.read_accessor(source, source_bin, index, label),
                                     runtime.read_accessor(derived, derived_bin, index, label))
                for key in ("materials", "textures", "samplers", "nodes"):
                    self.assertEqual(source[key], derived[key])
                for image in source["images"]:
                    view = source["bufferViews"][image["bufferView"]]
                    data = source_bin[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
                    self.assertIn(data, extracted.values())
                profile = runtime.FACADE_TILE_PROFILES[style_id]
                positions = runtime.read_accessor(derived, derived_bin, 0, label)
                uv = runtime.read_accessor(derived, derived_bin, 2, label)
                for wall in range(0, len(positions), 4):
                    a, b, c, _ = positions[wall:wall + 4]
                    width = math.hypot(b[0] - a[0], b[2] - a[2])
                    self.assertAlmostEqual((uv[wall + 1][0] - uv[wall][0]) / width,
                                           1 / profile.repeat_m[0], places=6)
                    self.assertAlmostEqual((uv[wall + 2][1] - uv[wall + 1][1]) / (c[1] - b[1]),
                                           -1 / profile.repeat_m[1], places=6)
                    self.assertEqual(uv[wall][1], 1.0)
                self.assertEqual(stats["art"]["detail"]["facade_tiling"]["class"], runtime.ART_DETAIL_CLASS)

    def test_tallest_source_glass_has_metre_sized_panes(self):
        building, label, document, binary = self.source()
        self.assertEqual(building["extrusion_height_m"], 120)
        raw, _, stats = self.derive(building, label, document, binary)
        derived, derived_bin = runtime.read_glb_bytes(raw, label)
        uv = runtime.read_accessor(derived, derived_bin, 2, label)
        self.assertGreater(1 - min(v for _, v in uv), 10)
        detail = stats["art"]["detail"]["facade_tiling"]
        self.assertEqual(detail["reference_window_m"], [1.6, 1.37777778])
        self.assertEqual(detail["authored_storey_pitch_m"], 3.2)
        self.assertEqual(detail["pane_rows_per_storey"], 2)
        self.assertAlmostEqual(120 / detail["pane_pitch_m"][1], 75)

    def test_density_is_independent_of_height_and_map_coordinates(self):
        profile = runtime.FACADE_TILE_PROFILES["modern_dark_grid"]
        for height in (2.7, 33, 120):
            positions = [(30000.0, 0, -20000.0), (30037.0, 0, -20000.0),
                         (30037.0, height, -20000.0), (30000.0, height, -20000.0)]
            uv = runtime.metric_facade_uvs(positions, profile, "another-region-asset")
            self.assertAlmostEqual(uv[1][0] / 37, 1 / profile.repeat_m[0])
            self.assertAlmostEqual((uv[1][1] - uv[2][1]) / height, 1 / profile.repeat_m[1])
            self.assertLess(uv[2][1], uv[1][1])

    def test_optional_relief_and_parapet_keep_the_same_vertical_uv_phase(self):
        building, label, document, binary = self.source()
        raw, _, _ = self.derive(building, label, document, binary,
                               options=runtime.ArtDetailOptions(facade_relief=True))
        derived, derived_bin = runtime.read_glb_bytes(raw, label)
        positions = runtime.read_accessor(derived, derived_bin, 0, label)
        uv = runtime.read_accessor(derived, derived_bin, 2, label)
        tile_height = runtime.FACADE_TILE_PROFILES["modern_dark_grid"].repeat_m[1]
        for position, texcoord in zip(positions, uv):
            self.assertAlmostEqual(texcoord[1], 1 - position[1] / tile_height, places=5)

    def test_roof_accessor_payloads_equal_roof_only_baseline(self):
        # These roof accessor hashes came from the accepted roof-only runtime.
        # UVs must not change the deck, parapet, structures or roof indices.
        expected = {
            'classical_dark_brick': '371a0aa620c8e14a4af230c82d15fdd7fa650762e63654651825946dde4b3959',
            'classical_ornate': '2902850cf0885f20be149eeb6d504c7d89f92a8e5fc72ab0d7b2113523faac3a',
            'classical_red_brick': '8f80da77f0a3b568d8d5a48da5aded47733f02ad65aaef30c2e74369318fccd1',
            'classical_stone_arch': 'e45c91620753073a13aed75988e4f31958fecbcef02d7f0d79c655ce086fba74',
            'classical_tan_stone': 'e0b3eec1d168c5f3f6d0c7406f865081839bea29086c07e6d400e39d93cb5451',
            'modern_beige_grid': '51ed224a6379f8f288e40a511c8eb7e89527da37ac165021ff40191799d34c1c',
            'modern_beige_squares': '5cea6ecb74a6d01e5210baacbcdab18e02864f65dba581aee42f1a8dda2441c1',
            'modern_blue_banded': '043abed07f3c1454bad10be5bf52d86839944ee2978b593acb76190c3138d68e',
            'modern_bw_bands': '2c1485529e203160de974f167f6a9e61969c2d759d06c9900b2f026620bec11d',
            'modern_concrete_band': '2310dd802d27e76a50b1c61f815ae82611ba018f3c1001e400afd3c2b05966d2',
            'modern_dark_curtain': '8ac73751654fa726e313884e758876698587e744f71f4bd8ef8ff3d56d22b8ba',
            'modern_dark_grid': '8a94ac8a113f90ec30b23d0da202178cc5e3f6b90824b99a02ef81e561a976a2',
            'modern_grey_glass': '00897d5e5ebe5b2965f27f2a64a04455700e1c014520770b357b26b988a72e51',
            'modern_grey_grid': '7091726660953616b9116b5872984d93f4566e2c5fa56077fc25d850bf940d46',
            'modern_xbrace_glass': '06f2214f1f4b34ee0e212dd49f83435eec2557b401e19a2e3757e9e62aa196d5',
        }
        for style_id, digest in expected.items():
            building, label, document, binary = self.source(style_id)
            raw, _, _ = self.derive(building, label, document, binary)
            derived, derived_bin = runtime.read_glb_bytes(raw, label)
            payloads = []
            for index in (4, 5, 6):
                view = derived["bufferViews"][derived["accessors"][index]["bufferView"]]
                offset = view.get("byteOffset", 0)
                payloads.append(derived_bin[offset:offset + view["byteLength"]])
            self.assertEqual(hashlib.sha256(b"".join(payloads)).hexdigest(), digest)

    def test_unknown_texture_profile_blocks(self):
        building, label, document, binary = self.source()
        document = copy.deepcopy(document)
        document["asset"]["extras"]["style"]["style_id"] = "unknown-source-texture"
        with self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*no authored tile profile"):
            self.derive(building, label, document, binary)

    def test_source_crop_drift_blocks(self):
        building, label, document, binary = self.source()
        document = copy.deepcopy(document)
        document["asset"]["extras"]["style"]["region_px"][0] += 1
        with self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*crop disagrees"):
            self.derive(building, label, document, binary)

    def test_missing_facade_texture_blocks(self):
        building, label, document, binary = self.source()
        document = copy.deepcopy(document)
        document["materials"][0]["pbrMetallicRoughness"].pop("baseColorTexture")
        with self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*albedo texture is missing"):
            self.derive(building, label, document, binary)

    def test_clamped_or_mirrored_sampling_blocks(self):
        building, label, document, binary = self.source()
        for axis in ("wrapS", "wrapT"):
            for wrap in (33071, 33648):
                changed = copy.deepcopy(document)
                changed["samplers"][0][axis] = wrap
                with self.subTest(axis=axis, wrap=wrap), self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*requires REPEAT"):
                    self.derive(building, label, changed, binary)

    def test_channels_cannot_use_separate_uv_sets_or_transforms(self):
        building, label, document, binary = self.source()
        for field in ("normalTexture", "emissiveTexture"):
            for change in ({"texCoord": 1}, {"extensions": {"KHR_texture_transform": {"scale": [2, 2]}}}):
                changed = copy.deepcopy(document)
                changed["materials"][0][field].update(change)
                with self.subTest(channel=field, change=change), self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*TEXCOORD_0"):
                    self.derive(building, label, changed, binary)

    def test_centimetre_or_undeclared_source_units_block(self):
        building, label, document, binary = self.source()
        for convention in (None, "glTF 2.0: right-handed, Y up, centimetres"):
            changed = copy.deepcopy(document)
            changed["asset"]["extras"]["frame"]["convention"] = convention
            with self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*Y-up metres"):
                self.derive(building, label, changed, binary)

    def test_height_in_centimetres_disagrees_with_real_source_geometry(self):
        building, label, document, binary = self.source()
        changed = copy.deepcopy(building)
        changed["extrusion_height_m"] *= 100
        changed["top_enu_up_m"] *= 100
        with self.assertRaisesRegex(runtime.BuildError, "canonical facade disagrees"):
            self.derive(changed, label, document, binary)

    def test_metric_uv_verification_rejects_height_normalised_payload(self):
        building, label, document, binary = self.source()
        raw, _, stats = self.derive(building, label, document, binary)
        derived, derived_bin = runtime.read_glb_bytes(raw, label)
        uv_view = derived["bufferViews"][derived["accessors"][2]["bufferView"]]
        old_uv = runtime.read_accessor(document, binary, 2, label)
        changed = bytearray(raw)
        json_length = struct.unpack_from("<I", raw, 12)[0]
        uv_start = 20 + json_length + 8 + uv_view["byteOffset"]
        for index, uv in enumerate(old_uv):
            struct.pack_into("<2f", changed, uv_start + index * 8, *uv)
        ring = runtime.ring_from_footprint(building["footprint_enu_m"])
        anchor = runtime.footprint_centroid(building["footprint_enu_m"])
        lo, hi = runtime.position_bounds(document, binary)
        with self.assertRaisesRegex(runtime.BuildError, "facade UVs disagree"):
            runtime.verify_derivation(CANONICAL_RENDER_DIR / "assets" / f"{label}.glb", bytes(changed), label,
                ring=ring, height=building["extrusion_height_m"], top_up=building["top_enu_up_m"],
                anchor=anchor, expected_lo=lo, expected_hi=hi, expected_art=stats["art"])


class FacadeMiddleTileTests(unittest.TestCase):
    setUpClass = FacadeMetreTilingTests.__dict__["setUpClass"]
    source = FacadeMetreTilingTests.source
    derive = FacadeMetreTilingTests.derive

    def enabled(self, building, label, document, binary):
        return self.derive(building, label, document, binary,
                           options=runtime.ArtDetailOptions(facade_middle_tiles=True))

    def verify(self, building, label, document, binary, raw, stats):
        lo, hi = runtime.position_bounds(document, binary)
        runtime.verify_derivation(
            CANONICAL_RENDER_DIR / "assets" / f"{label}.glb", raw, label,
            ring=runtime.ring_from_footprint(building["footprint_enu_m"]),
            height=building["extrusion_height_m"], top_up=building["top_enu_up_m"],
            anchor=runtime.footprint_centroid(building["footprint_enu_m"]),
            expected_lo=lo, expected_hi=hi, expected_art=stats["art"])

    def repack(self, document, binary):
        raw = json.dumps(document, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
        raw += b" " * (-len(raw) % 4)
        return (struct.pack("<4sII", b"glTF", 2, 28 + len(raw) + len(binary))
                + struct.pack("<II", len(raw), 0x4E4F534A) + raw
                + struct.pack("<II", len(binary), 0x004E4942) + binary)

    def test_middle_tiles_are_explicit_and_keep_geometry_layer_schema(self):
        self.assertFalse(runtime.parse_options(command_arguments()).art_detail.facade_middle_tiles)
        self.assertTrue(runtime.parse_options(command_arguments(
            "--facade-middle-tiles")).art_detail.facade_middle_tiles)
        building, label, document, binary = self.source()
        raw, _, stats = self.enabled(building, label, document, binary)
        self.assertEqual(stats["art"]["layers"], {"roof_detail": True, "facade_relief": False})
        self.verify(building, label, document, binary, raw, stats)

    def test_all_fifteen_styles_use_lossless_aligned_shared_crops_and_exact_roof(self):
        archive = {}
        active = {}
        self.assertEqual(set(self.samples), set(runtime.FACADE_MIDDLE_TILE_PROFILES))
        for style_id in sorted(self.samples):
            with self.subTest(style=style_id):
                building, label, source, source_bin = self.source(style_id)
                before_raw, before_images, before_stats = self.derive(building, label, source, source_bin)
                before, before_bin = runtime.read_glb_bytes(before_raw, label)
                raw, images, stats = self.enabled(building, label, source, source_bin)
                after, after_bin = runtime.read_glb_bytes(raw, label)
                self.verify(building, label, source, source_bin, raw, stats)
                self.assertEqual(len(after_bin), len(before_bin))
                for index in (0, 1, 3, 4, 5, 6):
                    self.assertEqual(runtime.read_accessor(before, before_bin, index, label),
                                     runtime.read_accessor(after, after_bin, index, label))
                for key in ("nodes", "meshes", "textures", "samplers"):
                    self.assertEqual(before[key], after[key])
                self.assertEqual(before_stats["entry"], stats["entry"])
                crop = runtime.FACADE_MIDDLE_TILE_PROFILES[style_id].crop_px
                profile = after["materials"][0]["extras"]["authoredFacadePaneProfile"]
                width, height = profile["tile_size_px"]
                self.assertEqual([width, height], [crop[2] - crop[0], crop[3] - crop[1]])
                self.assertEqual(profile["coordinate_system"], "image-top-left-pixel")
                self.assertEqual(profile["provenance"], "source-texture-art")
                self.assertTrue(profile["glass_rectangles_px"])
                for left, top, right, bottom in profile["glass_rectangles_px"]:
                    self.assertTrue(0 <= left < right <= width and 0 <= top < bottom <= height)
                for polygon in profile["opaque_polygons_px"]:
                    self.assertGreaterEqual(len(polygon), 3)
                    self.assertTrue(all(0 <= x <= width and 0 <= y <= height for x, y in polygon))
                self.assertEqual(stats["archived_source_images"], before_images)
                for index, image in enumerate(after["images"]):
                    source_image = source["images"][index]
                    view = source["bufferViews"][source_image["bufferView"]]
                    start = view.get("byteOffset", 0)
                    source_bytes = source_bin[start:start + view["byteLength"]]
                    expected = Image.open(io.BytesIO(source_bytes)).crop(crop)
                    digest = image["uri"][len("assets/"):]
                    actual = Image.open(io.BytesIO(images[digest]))
                    self.assertEqual(actual.size, expected.size)
                    self.assertEqual(actual.mode, "RGB")
                    self.assertEqual(actual.tobytes(), expected.tobytes())
                archive.update(stats["archived_source_images"])
                active.update(images)
        self.assertEqual(len(archive), 45)
        self.assertEqual(len(active), 45)
        self.assertEqual(sum(map(len, archive.values())), 2725934)

    def test_unknown_middle_profile_and_missing_distinct_channel_block(self):
        building, label, document, binary = self.source()
        profile = runtime.FACADE_MIDDLE_TILE_PROFILES.pop("modern_dark_grid")
        try:
            with self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*no authored middle tile"):
                self.enabled(building, label, document, binary)
        finally:
            runtime.FACADE_MIDDLE_TILE_PROFILES["modern_dark_grid"] = profile
        changed = copy.deepcopy(document)
        changed["materials"][0]["normalTexture"]["index"] = 0
        with self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*three distinct"):
            self.enabled(building, label, changed, binary)

    def test_middle_tile_wrong_units_and_out_of_bounds_crop_block(self):
        building, label, document, binary = self.source()
        changed = copy.deepcopy(document)
        changed["asset"]["extras"]["frame"]["convention"] = "Y up, centimetres"
        with self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*Y-up metres"):
            self.enabled(building, label, changed, binary)
        view = document["bufferViews"][document["images"][0]["bufferView"]]
        start = view.get("byteOffset", 0)
        data = binary[start:start + view["byteLength"]]
        with self.assertRaisesRegex(runtime.BuildError, "BLOCKED.*leaves"):
            runtime.crop_source_png(data, (0, 0, 241, 240))

    def test_independent_verifier_rejects_channel_swap_pane_drift_and_pbr_changes(self):
        building, label, document, binary = self.source()
        raw, _, stats = self.enabled(building, label, document, binary)
        after, after_bin = runtime.read_glb_bytes(raw, label)
        changed = copy.deepcopy(after)
        changed["images"][1]["uri"] = changed["images"][0]["uri"]
        with self.assertRaisesRegex(runtime.BuildError, "digest.*lossless source crop"):
            self.verify(building, label, document, binary, self.repack(changed, after_bin), stats)
        changed = copy.deepcopy(after)
        changed["materials"][0]["extras"]["authoredFacadePaneProfile"]["glass_rectangles_px"][0][0] += 1
        with self.assertRaisesRegex(runtime.BuildError, "glass panes disagree"):
            self.verify(building, label, document, binary, self.repack(changed, after_bin), stats)
        changed = copy.deepcopy(after)
        changed["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = 0.1
        with self.assertRaisesRegex(runtime.BuildError, "material parameters"):
            self.verify(building, label, document, binary, self.repack(changed, after_bin), stats)

    def test_source_bytes_and_crops_are_reused_for_unrelated_scene_label(self):
        building, label, document, binary = self.source()
        _, textures, stats = self.enabled(building, label, document, binary)
        _, repeated, other_stats = self.enabled(building, "different-region.building.7", document, binary)
        self.assertEqual(textures, repeated)
        self.assertEqual(stats["archived_source_images"], other_stats["archived_source_images"])
        self.assertEqual(stats["art"]["detail"]["facade_tiling"]["channels"],
                         other_stats["art"]["detail"]["facade_tiling"]["channels"])


if __name__ == "__main__":
    unittest.main()
