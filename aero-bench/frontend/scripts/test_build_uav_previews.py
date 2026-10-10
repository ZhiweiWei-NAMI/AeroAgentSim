"""Checks for the local STL-to-GLB preview converter."""

import importlib.util
from io import BytesIO
import json
import struct
import sys
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image


SCRIPT = Path(__file__).with_name("build-uav-previews.py")
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("build_uav_previews", SCRIPT)
assert SPEC and SPEC.loader
CONVERTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONVERTER)


class PreviewConverterTests(unittest.TestCase):
    def test_binary_and_ascii_stl_have_same_triangle(self):
        facet = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
        binary = b"Binary STL".ljust(80, b"\0") + struct.pack("<I", 1) + facet
        ascii_stl = b"""solid test
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 0 1 0
endloop
endfacet
endsolid test
"""
        expected = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype="<f4")
        np.testing.assert_array_equal(CONVERTER.parse_stl(binary, "binary"), expected)
        np.testing.assert_array_equal(CONVERTER.parse_stl(ascii_stl, "ascii"), expected)

    def test_generated_glb_has_indexed_mesh_and_source_metadata(self):
        triangles = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype="<f4")
        positions, normals, uv, indices = CONVERTER.geometry(triangles)
        glb = CONVERTER.make_glb(positions, normals, uv, indices, "source.zip", ["model.stl"], [0.3, 0.4, 0.5, 1], "fixedwing")
        magic, version, length = struct.unpack_from("<4sII", glb)
        self.assertEqual((magic, version, length), (b"glTF", 2, len(glb)))
        json_size, json_type = struct.unpack_from("<I4s", glb, 12)
        self.assertEqual(json_type, b"JSON")
        document = json.loads(glb[20:20 + json_size])
        self.assertEqual(document["asset"]["extras"]["source_archive"], "source.zip")
        self.assertEqual(document["accessors"][2]["count"], len(uv))
        primitive = document["meshes"][0]["primitives"][0]
        self.assertEqual(primitive["indices"], 3)
        self.assertEqual(primitive["attributes"]["TEXCOORD_0"], 2)
        self.assertEqual(len(document["images"]), 3)
        material = document["materials"][0]
        self.assertEqual(material["pbrMetallicRoughness"]["baseColorTexture"], {"index": 0})
        self.assertEqual(material["pbrMetallicRoughness"]["metallicRoughnessTexture"], {"index": 2})
        self.assertEqual(material["occlusionTexture"], {"index": 2})
        self.assertIn("KHR_materials_clearcoat", document["extensionsUsed"])
        self.assertEqual(document["asset"]["extras"]["livery_version"], "aviation-v1")

    def test_multirotor_materials_assign_props_and_center_separately(self):
        triangles = np.array([
            [-0.1, 0.0, -0.1], [0.1, 0.0, -0.1], [0, 0.0, 0.1],
            [1.0, 0.9, 0], [1.2, 0.9, 0], [1.1, 0.9, 0.2],
            [0.0, 0.8, 0], [0.1, 0.8, 0], [0.0, 0.8, 0.1],
        ], dtype="<f4")
        groups, materials = CONVERTER.material_scheme(triangles, np.arange(len(triangles), dtype="<u4"), [0.3, 0.4, 0.5, 1], "multirotor")
        self.assertNotEqual(groups[1], groups[2])
        self.assertEqual(materials[1][0], "Amber equipment shell")
        self.assertEqual(materials[3][0], "Graphite rotor and trim")
        self.assertEqual(materials[1][2], "paint")

    def test_3dxml_preserves_source_color_uv_and_image(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), (80, 120, 160)).save(image, format="JPEG")
        source = BytesIO()
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("CATRepImage.3dxml", '<Model_3dxml xmlns="http://www.3ds.com/xsd/3DXML"><CATRepImage><CATRepresentationImage id="1" associatedFile="urn:3DXML:image_1.jpg"/></CATRepImage></Model_3dxml>')
            archive.writestr("MaterialDef_0.3DRep", '<Osm><Feature><Attr Name="DiffuseColor" Value="[0.1,0.2,0.3]"/><Attr Name="TextureImage" Value="urn:3DXML:CATRepImage.3dxml#1"/></Feature></Osm>')
            archive.writestr("TessPart_2.3DRep", '''<XMLRepresentation xmlns="http://www.3ds.com/xsd/3DXML"><Root><Rep><Faces><Face strips="0 1 2"/></Faces><VertexBuffer><Positions>0 0 0,1 0 0,0 1 0</Positions><Normals>0 0 1,0 0 1,0 0 1</Normals><TextureCoordinates>0 0,1 0,0 1</TextureCoordinates></VertexBuffer><SurfaceAttributes><MaterialApplication><MaterialId id="urn:3DXML:CATMaterialRef.3dxml#0"/></MaterialApplication></SurfaceAttributes></Rep></Root></XMLRepresentation>''')
            archive.writestr("image_1.jpg", image.getvalue())
        glb = CONVERTER.make_3dxml_glb(source.getvalue(), "source.zip", "aircraft.3DXML")
        json_size = struct.unpack_from("<I", glb, 12)[0]
        document = json.loads(glb[20:20 + json_size])
        self.assertEqual(document["asset"]["extras"]["source_members"], ["aircraft.3DXML"])
        self.assertEqual(document["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"], [1, 1, 1, 1])
        self.assertEqual(document["materials"][0]["extras"]["source_color"], [0.1, 0.2, 0.3])
        self.assertEqual(document["materials"][0]["extras"]["source_texture"], "image_1.jpg")
        self.assertEqual(document["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"], 2)
        self.assertEqual(document["accessors"][3]["count"], 3)
        self.assertEqual(document["asset"]["extras"]["livery_version"], "aviation-v1")

    def test_source_signal_colors_and_images_are_retained(self):
        role, color, *_ = CONVERTER.source_livery_finish([1.0, 0.0, 0.0], False)
        self.assertEqual((role, color), ("Source signal marking", (1.0, 0.0, 0.0, 1.0)))
        role, color, *_ = CONVERTER.source_livery_finish([0.1, 0.2, 0.3], True)
        self.assertEqual((role, color), ("Source image", (1.0, 1.0, 1.0, 1.0)))


if __name__ == "__main__":
    unittest.main()
