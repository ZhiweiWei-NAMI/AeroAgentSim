"""Canonical source binding and coordinate tests for road/traffic shared conversion."""
import json
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from pyproj import CRS, Transformer
from city_preview_coordinates import (CityEnuProjection, CityMeshPackProjection, canonical_origin,
    footprint_from_enu_rings, load_canonical_city_geometry, source_context_bytes)

ROOT = Path(__file__).resolve().parents[2]
OBJECTS = ROOT/"validation/scene-compiler-shanghai-huangpu-east-v1/metadata/objects.json"
RENDER = ROOT/"frontend/public/building-renders/shanghai-huangpu-east-v1/manifest.json"
PACK = ROOT/"frontend/public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json"
NETWORK = ROOT/"validation/city-ground-roads-20260927/sumo/network.net.xml"


class CanonicalCityCoordinatesTest(unittest.TestCase):
    def test_all_real_buildings_join_their_source_byte_pins_and_metres(self):
        geometry = load_canonical_city_geometry(OBJECTS, RENDER, PACK)
        self.assertEqual(len(geometry.footprints), 414)
        self.assertEqual(geometry.objects_sha256, "c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc")
        self.assertEqual(geometry.footprints[0].outline[0], (441.22009487255167, 357.26085102524297))
        self.assertEqual(geometry.footprint_contract, "urban-scene-objects/v1-outer-rings-with-no-hole-field")
        self.assertEqual(geometry.model_footprint_audit["count"],414)
        self.assertLess(geometry.model_footprint_audit["maximum_boundary_error_m"],1e-3)
        self.assertTrue(all(not footprint.rendered_polygon.is_empty for footprint in geometry.footprints))

    def test_real_osm_vertex_matches_source_building_enu(self):
        objects = json.loads(OBJECTS.read_bytes())
        item = objects["buildings"][0]
        vertex_id = str(item["source_footprint_vertex_node_ids"][0])
        source = ET.parse(ROOT/"validation/scene-compiler-shanghai-huangpu-east-v1/audit/raw-closure.osm").getroot()
        vertex = next(node for node in source.findall("node") if node.get("id") == vertex_id)
        network = ET.parse(NETWORK).getroot()
        proj = network.find("location").get("projParameter")
        to_native = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_proj4(proj), always_xy=True)
        native = to_native.transform(float(vertex.get("lon")), float(vertex.get("lat")))
        point = CityEnuProjection(network, objects["origin"]).point(*native)
        # Exact rectangular cropping rotates the ring start; source node order
        # and the current footprint start vertex need not be the same.
        expected_vertices = [(east,-north) for east,north in item["footprint_enu_m"]]
        self.assertLess(min(math.dist(point,expected) for expected in expected_vertices), .008)

    def test_sumo_offset_does_not_translate_the_viewer(self):
        origin = json.loads(OBJECTS.read_bytes())["origin"]
        network = ET.parse(NETWORK).getroot()
        first = CityEnuProjection(network, origin)
        network.find("location").set("netOffset", "123.5,-46.25")
        shifted = CityEnuProjection(network, origin)
        self.assertEqual(first.point(250, -350), shifted.point(373.5, -396.25))
        self.assertAlmostEqual(first.heading(250, -350, 37), shifted.heading(373.5, -396.25, 37), places=9)

    def test_cardinal_heading_and_surface_axes_share_one_transform(self):
        origin = json.loads(OBJECTS.read_bytes())["origin"]
        projection = CityEnuProjection(ET.parse(NETWORK).getroot(), origin)
        self.assertEqual(projection.point(0, 0), (0.0, 0.0))
        self.assertEqual(projection.shape("0,0 0,0 0,10"), [[0.0, 0.0], [0.0, -10.0]])
        self.assertAlmostEqual(projection.heading(0, 0, 0), 0, places=6)
        self.assertAlmostEqual(projection.heading(0, 0, 90), 90, places=4)
        self.assertEqual(projection.metadata()["axes"], "x-east,y-up,z-south-meters")

    def test_missing_origin_elevation_or_bad_projection_fails(self):
        origin = json.loads(OBJECTS.read_bytes())["origin"]
        del origin["ellipsoid_height_m"]
        with self.assertRaisesRegex(ValueError, "five geographic/vertical"):
            canonical_origin(origin)
        origin = json.loads(OBJECTS.read_bytes())["origin"]
        origin["amsl_m"] += 1
        with self.assertRaisesRegex(ValueError, "vertical relation"):
            canonical_origin(origin)
        network = ET.parse(NETWORK).getroot()
        network.find("location").set("projParameter", "!")
        with self.assertRaisesRegex(ValueError, "explicit geographic projection"):
            CityEnuProjection(network, json.loads(OBJECTS.read_bytes())["origin"])

    def test_non_ground_elevation_and_missing_shapes_fail(self):
        projection = CityEnuProjection(ET.parse(NETWORK).getroot(), json.loads(OBJECTS.read_bytes())["origin"])
        for shape in ("0,0,1 5,5,1", ""):
            with self.assertRaises(ValueError):
                projection.shape(shape)
        with self.assertRaisesRegex(ValueError, "must be finite"):
            projection.point(math.nan, 0)

    def test_wrong_geographic_origin_or_valid_but_wrong_crs_fails(self):
        origin = json.loads(OBJECTS.read_bytes())["origin"]
        network = ET.parse(NETWORK).getroot()
        origin["latitude_deg"] += .01
        with self.assertRaisesRegex(ValueError, "differs from the canonical origin"):
            CityEnuProjection(network,origin)
        network.find("location").set("projParameter", "+proj=aeqd +lat_0=31.2288 +lon_0=121.491 +datum=WGS84 +units=m +no_defs")
        with self.assertRaisesRegex(ValueError, "differs from the canonical origin"):
            CityEnuProjection(network,json.loads(OBJECTS.read_bytes())["origin"])

    def test_explicit_holes_survive_and_invalid_rings_are_not_repaired(self):
        arguments = dict(object_id="source-building", base_up_m=0, top_up_m=10, geometry_sha256="a"*64)
        outer = [[0,0],[4,0],[4,4],[0,4],[0,0]]
        hole = [[1,1],[3,1],[3,3],[1,3],[1,1]]
        footprint = footprint_from_enu_rings(outline=outer, holes=[hole], **arguments)
        self.assertEqual(footprint.polygon.area, 12)
        self.assertEqual(footprint.holes[0][0], (1.0,-1.0))
        with self.assertRaisesRegex(ValueError, "actual render GLB has not been verified"):
            footprint.rendered_polygon
        with self.assertRaisesRegex(ValueError, "no geometry repair"):
            footprint_from_enu_rings(outline=[[0,0],[4,4],[0,4],[4,0],[0,0]], holes=[], **arguments)

    def test_render_source_byte_and_inventory_drift_fail(self):
        for mutate in (lambda r:r["scene"].update(objects_json_sha256="0"*64), lambda r:r["buildings"].pop(), lambda r:r["scene"]["origin_wgs84"].update(ellipsoid_height_m=51)):
            render = json.loads(RENDER.read_bytes()); mutate(render)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/"changed-render.json";path.write_text(json.dumps(render))
                with self.assertRaises(ValueError):
                    load_canonical_city_geometry(OBJECTS,path,PACK)

    def test_source_context_binds_actual_network_and_osm_bytes(self):
        import hashlib
        geometry = load_canonical_city_geometry(OBJECTS,RENDER,PACK)
        osm = ROOT/"validation/scene-compiler-shanghai-huangpu-east-v1/osm/sumo-network.osm"
        context = geometry.source_context(NETWORK,osm)
        self.assertEqual(context["source_network_sha256"],hashlib.sha256(NETWORK.read_bytes()).hexdigest())
        self.assertEqual(context["source_osm_sha256"],hashlib.sha256(osm.read_bytes()).hexdigest())
        self.assertEqual(context["building_render_manifest_sha256"],hashlib.sha256(RENDER.read_bytes()).hexdigest())
        self.assertEqual(context["engineering_inputs_sha256"],hashlib.sha256((NETWORK.parent/"engineering-inputs.json").read_bytes()).hexdigest())
        self.assertEqual(context["mesh_pack_projection"]["source_projection"],"MetricMapProjection")
        self.assertEqual(source_context_bytes(context),source_context_bytes(json.loads(source_context_bytes(context))))

    def test_missing_or_drifting_engineering_inputs_fail(self):
        import hashlib
        geometry = load_canonical_city_geometry(OBJECTS,RENDER,PACK)
        osm = ROOT/"validation/scene-compiler-shanghai-huangpu-east-v1/osm/sumo-network.osm"
        original = json.loads((NETWORK.parent/"engineering-inputs.json").read_bytes())
        changes = (
            {"schema_version":"aero-bench.urban-engineering-traffic-inputs/v0"},
            {"network_sha256":"0"*64}, {"source_osm_sha256":"0"*64},
            {"projection":"+proj=aeqd +lat_0=31.2288 +lon_0=121.491 +datum=WGS84 +units=m +no_defs"},
            {"net_offset":"1,0"}, {"net_offset":None}, {"net_offset_required":"1,0"}, {"net_offset_required":None},
        )
        with tempfile.TemporaryDirectory() as directory:
            receipt=Path(directory)/"engineering-inputs.json"
            with self.assertRaises(FileNotFoundError):
                geometry.source_context(NETWORK,osm,receipt)
            for change in changes:
                receipt.write_text(json.dumps({**original,**change}))
                with self.assertRaises(ValueError):
                    geometry.source_context(NETWORK,osm,receipt)
            # Even a correctly updated network digest cannot conceal a wrong
            # offset while the declared engineering origin remains unchanged.
            network=ET.parse(NETWORK).getroot()
            network.find("location").set("netOffset","1.00,0.00")
            network_path=Path(directory)/"network.net.xml"
            network_path.write_bytes(ET.tostring(network))
            receipt.write_text(json.dumps({**original,"network_sha256":hashlib.sha256(network_path.read_bytes()).hexdigest()}))
            with self.assertRaisesRegex(ValueError,"netOffset differs"):
                geometry.source_context(network_path,osm,receipt)
            shifted={**original,"network_sha256":hashlib.sha256(network_path.read_bytes()).hexdigest(),"net_offset":"1,0"}
            receipt.write_text(json.dumps(shifted))
            with self.assertRaisesRegex(ValueError,"engineering profile requirement"):
                geometry.source_context(network_path,osm,receipt)
            # Nonzero offsets remain supported when the declared profile
            # actually requires that same offset, with matching byte identity.
            receipt.write_text(json.dumps({**shifted,"net_offset_required":"1,0"}))
            self.assertEqual(geometry.source_context(network_path,osm,receipt)["projection"]["sumo_net_offset_m"],[1.0,0.0])

    def test_same_osm_ids_with_different_source_coordinates_fail(self):
        geometry = load_canonical_city_geometry(OBJECTS,RENDER,PACK)
        osm = ROOT/"validation/scene-compiler-shanghai-huangpu-east-v1/osm/sumo-network.osm"
        source = ET.parse(osm).getroot()
        source.find("node").set("lon","121.49")
        with tempfile.TemporaryDirectory() as directory:
            changed=Path(directory)/"changed.osm"
            changed.write_bytes(ET.tostring(source))
            with self.assertRaisesRegex(ValueError,"network/OSM bytes differ"):
                geometry.source_context(NETWORK,changed,NETWORK.parent/"engineering-inputs.json")

    def test_changed_model_bytes_are_rejected_before_polygon_truth(self):
        import shutil
        render = json.loads(RENDER.read_bytes())
        first=render["buildings"][0]
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path=base/"manifest.json";path.write_bytes(RENDER.read_bytes())
            asset=base/first["derived_glb"]["path"];asset.parent.mkdir()
            shutil.copyfile(RENDER.parent/first["derived_glb"]["path"],asset)
            raw=bytearray(asset.read_bytes());raw[-1]^=1;asset.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,"actual render model bytes differ"):
                load_canonical_city_geometry(OBJECTS,path,PACK)

    def test_rehashed_model_with_changed_transform_fails_true_geometry(self):
        import hashlib
        import struct
        render = json.loads(RENDER.read_bytes())
        first = render["buildings"][0]
        original = (RENDER.parent/first["derived_glb"]["path"]).read_bytes()
        chunks=[];offset=12
        while offset < len(original):
            size,kind=struct.unpack_from("<II",original,offset)
            data=original[offset+8:offset+8+size]
            if kind == 0x4e4f534a:
                gltf=json.loads(data)
                for index in gltf["scenes"][gltf.get("scene",0)]["nodes"]:
                    node=gltf["nodes"][index]
                    if "matrix" in node:
                        node["matrix"][12] += .02
                    else:
                        translation=list(node.get("translation",[0,0,0]))
                        translation[0] += .02;node["translation"]=translation
                data=json.dumps(gltf,separators=(",",":")).encode()
                data += b" "*((-len(data))%4)
            chunks.append(struct.pack("<II",len(data),kind)+data);offset += size+8
        content=b"".join(chunks)
        changed=struct.pack("<III",0x46546c67,2,len(content)+12)+content
        digest=hashlib.sha256(changed).hexdigest()
        first["derived_glb"].update(sha256=digest,path=f"assets/{digest}",bytes=len(changed))
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path=base/"manifest.json";path.write_text(json.dumps(render))
            asset=base/first["derived_glb"]["path"];asset.parent.mkdir();asset.write_bytes(changed)
            with self.assertRaisesRegex(ValueError,"actual GLB footprint/height differs"):
                load_canonical_city_geometry(OBJECTS,path,PACK)

    def test_mesh_frame_inverts_actual_converter_origin_and_float32_shift(self):
        import struct
        from aero_bench.world.frame_math import EnuTransform
        pack=json.loads(PACK.read_bytes())
        source=json.loads((PACK.parent/"assets"/pack["source"]["sha256"]).read_bytes())
        origin=json.loads(OBJECTS.read_bytes())["origin"]
        projection=CityMeshPackProjection(pack,source,origin)
        nodes=[node for node in source["elements"] if node["type"]=="node"]
        enu=EnuTransform.from_origin(longitude_deg=origin["longitude_deg"],
            latitude_deg=origin["latitude_deg"],altitude_m=origin["ellipsoid_height_m"])
        for node in (nodes[0],nodes[-1],min(nodes,key=lambda n:n["lat"]),max(nodes,key=lambda n:n["lat"])):
            x=projection.scale*(node["lon"]-projection.converter_origin["longitude_deg"])/360
            north=projection.scale*(projection._mercator_y(node["lat"])-projection.converter_mercator_y)
            # Match native 1 mm quantization and both pack Float32 writes.
            x=math.floor(x*1000+.5)/1000
            z=-math.floor(north*1000+.5)/1000
            x=struct.unpack("<f",struct.pack("<f",x))[0]+projection.translation_xz[0]
            z=struct.unpack("<f",struct.pack("<f",z))[0]+projection.translation_xz[1]
            x,z=struct.unpack("<ff",struct.pack("<ff",x,z))
            expected=enu.geodetic_to_enu(longitude_deg=node["lon"],latitude_deg=node["lat"],altitude_m=origin["ellipsoid_height_m"])
            self.assertLess(math.dist(projection.point(x,z),(expected.x,-expected.y)),.0008)
        self.assertNotEqual(projection.converter_origin["latitude_deg"],origin["latitude_deg"])
        self.assertEqual(projection.metadata()["output_rounding"],"none; preserve source triangle precision")

    def test_mesh_coordinate_contract_drift_fails_before_conversion(self):
        import copy
        pack=json.loads(PACK.read_bytes())
        source=json.loads((PACK.parent/"assets"/pack["source"]["sha256"]).read_bytes())
        origin=json.loads(OBJECTS.read_bytes())["origin"]
        mutations=(
            lambda p:p.pop("coordinate_contract"),
            lambda p:p["coordinate_contract"].update(stored_translation_xz_m=[0,1]),
            lambda p:p["coordinate_contract"].update(converter_origin={"latitude_deg":31.2,"longitude_deg":121.481}),
            lambda p:p["coordinate_contract"].update(source_json_sha256="a"*64),
            lambda p:p["coordinate_contract"].update(producer_sha256="missing"),
        )
        for mutate in mutations:
            changed=copy.deepcopy(pack);mutate(changed)
            with self.subTest(contract=changed.get("coordinate_contract")):
                with self.assertRaises(ValueError):
                    CityMeshPackProjection(changed,source,origin)

    def test_mesh_source_byte_drift_and_unexpected_frame_fail(self):
        import copy
        pack=json.loads(PACK.read_bytes())
        raw=(PACK.parent/"assets"/pack["source"]["sha256"]).read_bytes()
        source=json.loads(raw)
        wrong=copy.deepcopy(pack);wrong["projection"]["axes"]="ENU"
        with self.assertRaisesRegex(ValueError,"declared MetricMapProjection"):
            CityMeshPackProjection(wrong,source,json.loads(OBJECTS.read_bytes())["origin"])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"manifest.json";path.write_bytes(PACK.read_bytes())
            asset=path.parent/"assets"/pack["source"]["sha256"];asset.parent.mkdir()
            source["elements"][0]["lat"] += .001
            asset.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError,"Actual mesh pack source bytes differ"):
                load_canonical_city_geometry(OBJECTS,RENDER,path)


if __name__ == "__main__":
    unittest.main()
