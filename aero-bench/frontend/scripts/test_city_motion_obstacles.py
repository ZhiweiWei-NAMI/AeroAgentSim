"""Re-read actual furniture GLBs against small explicit fixture contract inputs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from city_fixture_geometry import displayed_fixture_triangles, fixture_display_transform, glb_triangles, projected_fixture
from city_effective_fixtures import MOTOR_TOP_UP_M
from city_motion_obstacles import document_sha256, fixture_model_path, fixture_polygon, load_effective_fixtures, world_projected_fixture


def polygon_parts(polygon):
    parts=list(polygon.geoms) if hasattr(polygon,"geoms") else [polygon]
    return [{"outline":[list(point) for point in part.exterior.coords],
             "holes":[[list(point) for point in hole.coords] for hole in part.interiors]} for part in parts]


class EffectiveFixtureContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context={"schema_version":"aero-bench.city-rendered-source-context/v1", "origin_wgs84":{"ellipsoid_height_m":50.},
                     "building_geometry":{"count":2,"source_footprint_contract":"declared-unit-rings"}}
        cls.signals=[{"id":"tl:e","tls":"tl","link":0,"x":10.,"z":20.,"heading":30.}]
        cls.lamps=[{"x":30.,"z":40.,"rotation_deg":60.}]
        models={}; effective=[]
        for kind,url,location in [("signal","/models/incoming/furniture/glb/traffic_light_4.glb",cls.signals[0]),
                                  ("street_lamp","/models/incoming/furniture/glb/street_light_8.glb",cls.lamps[0])]:
            path=fixture_model_path(url); digest=hashlib.sha256(path.read_bytes()).hexdigest()
            triangles=displayed_fixture_triangles(glb_triangles(path),kind)
            occupied=world_projected_fixture(triangles,kind,location)
            motion=world_projected_fixture(triangles,kind,location,(0,MOTOR_TOP_UP_M))
            models[kind]={"url":url,"sha256":digest,"display_transform":fixture_display_transform(kind)}
            effective.append({"kind":kind,"source_index":0,"source_location":location,"footprints":polygon_parts(occupied),
                              "motion_footprints":polygon_parts(motion),
                              "base_up_m":float(triangles[:,:,1].min()),"top_up_m":float(triangles[:,:,1].max()),"model_sha256":digest})
        cls.fixture={"schema_version":"aero-bench.city-effective-fixture-geometry/v1", "source_context":cls.context,
                     "models":models,"source_inventories":{"signals":cls.signals,"street_lamps":cls.lamps},
                     "source_inventory_sha256":{"signals":document_sha256(cls.signals),"street_lamps":document_sha256(cls.lamps)},
                     "displayed_surface_sha256":"unit-surfaces", "effective_fixtures":effective,
                     "omitted_signals":[],"omitted_street_lamps":[]}
    def setUp(self):
        self.data=deepcopy(self.fixture)
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/"unit-fixtures.json"
    def tearDown(self):
        self.directory.cleanup()
    def load(self):
        self.path.write_text(json.dumps(self.data,allow_nan=False))
        return load_effective_fixtures(self.path,self.context,self.signals,self.lamps,"unit-surfaces")
    def test_actual_two_glbs_close_model_projection_and_height(self):
        polygons,basis=self.load()
        self.assertGreater(len(polygons),0)
        self.assertEqual(basis["effective_fixture_count"],2)
        self.assertEqual(basis["rendered_footprint_count"],2)
        self.assertEqual(basis["policy"],"canonical-rendered-building-footprints-and-effective-fixtures")
    def test_context_inventory_or_surface_drift_is_rejected(self):
        for key,value in [("source_context",{}),("source_inventories",{}),("displayed_surface_sha256","other")]:
            with self.subTest(key=key):
                self.data=deepcopy(self.fixture);self.data[key]=value
                with self.assertRaises(ValueError): self.load()
    def test_model_byte_pin_or_display_transform_drift_is_rejected(self):
        for key,value in [("sha256","0"*64),("display_transform",{})]:
            with self.subTest(key=key):
                self.data=deepcopy(self.fixture);self.data["models"]["signal"][key]=value
                with self.assertRaises(ValueError):self.load()
    def test_source_location_cannot_be_relabelled_old_metric_coordinates(self):
        self.data["effective_fixtures"][0]["source_location"]["x"]+=1.
        with self.assertRaisesRegex(ValueError,"inventories|placement"):
            self.load()
    def test_actual_footprint_or_height_drift_is_rejected(self):
        self.data["effective_fixtures"][0]["footprints"]=[{"outline":[[0,0],[1,0],[1,1],[0,1],[0,0]],"holes":[]}]
        with self.assertRaisesRegex(ValueError,"actual transformed model"):
            self.load()
        self.data=deepcopy(self.fixture);self.data["effective_fixtures"][0]["top_up_m"]+=.01
        with self.assertRaisesRegex(ValueError,"vertical extent"):
            self.load()
    def test_motion_obstacles_are_the_actual_motor_band_clip_not_overhead_parts(self):
        polygons,basis=self.load()
        full=[fixture_polygon(part) for item in self.data["effective_fixtures"] for part in item["footprints"]]
        self.assertEqual(basis["motor_height_band_up_m"],[0,MOTOR_TOP_UP_M])
        # The signal arm and lamp head lie above the band, so the motion area is strictly smaller.
        self.assertLess(sum(p.area for p in polygons),sum(p.area for p in full)-.1)
        self.data["effective_fixtures"][0]["motion_footprints"]=self.data["effective_fixtures"][0]["footprints"]
        with self.assertRaisesRegex(ValueError,"motor-band footprint"):
            self.load()
        del self.data["effective_fixtures"][0]["motion_footprints"]
        with self.assertRaises(KeyError):
            self.load()
    def test_effective_omission_partition_is_complete_and_disjoint(self):
        self.data["effective_fixtures"].pop()
        with self.assertRaisesRegex(ValueError,"complete source inventory"):
            self.load()
        self.data=deepcopy(self.fixture)
        self.data["omitted_signals"]=[{"source_index":0,"source_location":self.signals[0],"reason":"unit-contact"}]
        with self.assertRaisesRegex(ValueError,"omissions"):
            self.load()
    def test_explicit_omission_preserves_full_source_inventory(self):
        self.data["effective_fixtures"].pop()
        self.data["omitted_street_lamps"]=[{"source_index":0,"source_location":self.lamps[0],"reason":"unit-contact"}]
        self.assertEqual(self.load()[1]["effective_fixture_count"],1)
    def test_polygon_holes_are_kept_and_invalid_rings_reject_without_repair(self):
        part={"outline":[[0,0],[5,0],[5,5],[0,5],[0,0]],"holes":[[[1,1],[1,4],[4,4],[4,1],[1,1]]]}
        self.assertEqual(fixture_polygon(part).area,16)
        part["outline"].pop()
        with self.assertRaisesRegex(ValueError,"explicitly closed"):
            fixture_polygon(part)
    def test_non_public_model_url_or_nan_inventory_is_rejected(self):
        with self.assertRaises(ValueError):fixture_model_path("/models/../private.glb")
        with self.assertRaises(ValueError):document_sha256({"x":float("nan")})


if __name__ == "__main__":
    unittest.main()
