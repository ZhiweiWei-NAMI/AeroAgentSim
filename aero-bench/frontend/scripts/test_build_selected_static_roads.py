"""Focused checks for source-bound selected static road inputs and signal topology."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, box


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("selected_static_roads_tested",
                                               SCRIPTS / "build-selected-static-roads.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SelectedStaticRoadsTest(unittest.TestCase):
    def test_signal_inventory_follows_controlled_motor_lane_without_recording(self):
        network = ET.fromstring('''<net>
          <edge id="w1"><lane id="w1_0" index="0" allow="pedestrian"
            width="2" shape="0,0 0,20"/>
            <lane id="w1_1" index="1" allow="passenger" width="3.2"
            shape="3,0 3,20"/></edge>
          <connection from="w1" to="w2" fromLane="0" toLane="0" tl="j1" linkIndex="0"/>
          <connection from="w1" to="w2" fromLane="1" toLane="0" tl="j1" linkIndex="1"/>
        </net>''')
        transformer = Transformer.from_crs(CRS.from_proj4(
            "+proj=aeqd +lat_0=31.2304 +lon_0=121.4737 +datum=WGS84 +units=m +no_defs"),
            CRS.from_epsg(4326), always_xy=True)
        payload = MODULE._signal_inventory(network, transformer,
            {"latitude_deg": 31.2304, "longitude_deg": 121.4737},
            (box(100, 100, 110, 110),), "a" * 64, "b" * 64)
        self.assertEqual(payload["schema_version"], "aero-bench.city-static-signal-inventory/v1")
        self.assertEqual(len(payload["signals"]), 1)
        self.assertEqual(payload["signals"][0]["id"], "j1:w1")
        self.assertEqual(payload["signals"][0]["link"], 1)

    def test_no_controlled_approach_is_an_empty_real_inventory(self):
        network = ET.fromstring('<net><edge id="w1"><lane id="w1_0" allow="passenger" '
                                'shape="0,0 0,20"/></edge></net>')
        transformer = Transformer.from_crs(CRS.from_epsg(3857), CRS.from_epsg(4326), always_xy=True)
        payload = MODULE._signal_inventory(network, transformer,
            {"latitude_deg": 0.0, "longitude_deg": 0.0},
            (box(100, 100, 110, 110),), "a" * 64, "b" * 64)
        self.assertEqual(payload["signals"], [])

    def test_short_lane_heading_uses_connected_straight_predecessor(self):
        network = ET.fromstring('''<net>
          <edge id="p"><lane id="p_0" allow="passenger" width="3.2"
            shape="0,-4 0.1,0"/></edge>
          <edge id="e"><lane id="e_0" allow="passenger" width="3.2"
            shape="0.1,0 0.1,0.2"/></edge>
          <connection from="p" to="e" fromLane="0" toLane="0" dir="s"/>
          <connection from="e" to="f" fromLane="0" toLane="0"
            tl="j" linkIndex="3"/>
        </net>''')
        transformer = Transformer.from_crs(CRS.from_proj4(
            "+proj=aeqd +lat_0=31.2304 +lon_0=121.4737 +datum=WGS84 +units=m +no_defs"),
            CRS.from_epsg(4326), always_xy=True)
        result = MODULE._signal_inventory(network, transformer,
            {"latitude_deg": 31.2304, "longitude_deg": 121.4737},
            (box(20, 20, 25, 25),), "a" * 64, "b" * 64)
        self.assertEqual(result["signals"][0]["link"], 3)
        self.assertAlmostEqual(result["signals"][0]["heading"], 1.4, delta=.2)

    def test_signal_pole_moves_off_road_onto_displayed_walk(self):
        network = ET.fromstring('''<net><connection tl="j" from="e" to="f"
          fromLane="0" toLane="0" linkIndex="0"/></net>''')
        inventory = {"schema_version": "aero-bench.city-static-signal-inventory/v1",
                     "source_network_sha256": "a" * 64,
                     "mesh_pack_source_sha256": "b" * 64,
                     "signals": [{"id": "j:e", "tls": "j", "link": 0,
                                  "x": 3.1, "z": 0.0, "heading": 0.0}]}
        road = {"lanes": [{"id": "e_0", "kind": "motor", "width": 3.2,
                           "shape": [[0, 5], [0, 0]]}],
                "roadbed": [{"outline": [[-3, -10], [3, -10], [3, 10], [-3, 10]], "holes": []}],
                "walkbed": [{"outline": [[3, -10], [6, -10], [6, 10], [3, 10]], "holes": []}]}
        transformer = Transformer.from_crs(CRS.from_epsg(3857), CRS.from_epsg(4326), always_xy=True)
        refined = MODULE._fit_signals_to_displayed_walk(
            inventory, road, network, (box(20, 20, 25, 25),), transformer,
            {"latitude_deg": 0.0, "longitude_deg": 0.0})
        self.assertGreater(refined["signals"][0]["x"], 3.35)
        self.assertEqual(refined["signals"][0]["link"], 0)

    def test_short_controlled_lane_uses_its_connected_predecessor(self):
        network = ET.fromstring('''<net>
          <edge id="p"><lane id="p_0" allow="passenger" width="3.2"
            shape="2,1 -2,1"/></edge>
          <edge id="e"><lane id="e_0" allow="passenger" width="3.2"
            shape="0,0 0,0.2"/></edge>
          <edge id=":v" function="internal"><lane id=":v_0"
            shape="-2,1 0,0"/></edge>
          <connection from="p" to="e" fromLane="0" toLane="0"
            via=":v_0" dir="s"/>
          <connection from="e" to="f" fromLane="0" toLane="0"
            tl="j" linkIndex="3"/>
        </net>''')
        transformer = Transformer.from_crs(CRS.from_proj4(
            "+proj=aeqd +lat_0=31.2304 +lon_0=121.4737 +datum=WGS84 +units=m +no_defs"),
            CRS.from_epsg(4326), always_xy=True)
        origin = {"latitude_deg": 31.2304, "longitude_deg": 121.4737}
        inventory = {"schema_version": "aero-bench.city-static-signal-inventory/v1",
                     "source_network_sha256": "a" * 64,
                     "mesh_pack_source_sha256": "b" * 64,
                     "signals": [{"id": "j:e", "tls": "j", "link": 3,
                                  "x": 2.6, "z": 2.3, "heading": 0.0}]}
        walk = box(-3, -4.5, -.5, -3)
        road = {"lanes": [
                    {"id": "p_0", "kind": "motor", "width": 3.2,
                     "shape": [[2, -1], [-2, -1]]},
                    {"id": "e_0", "kind": "motor", "width": 3.2,
                     "shape": [[0, 0], [0, -.2]]}],
                "roadbed": [{"outline": [[-1.6, -1.8], [1.6, -1.8],
                                         [1.6, .5], [-1.6, .5]], "holes": []}],
                "walkbed": [{"outline": [[-3, -4.5], [-.5, -4.5],
                                         [-.5, -3], [-3, -3]], "holes": []}]}
        result = MODULE._fit_signals_to_displayed_walk(
            inventory, road, network, (box(20, 20, 25, 25),), transformer, origin)
        signal = result["signals"][0]
        pole = Point(signal["x"], signal["z"]).buffer(.35)
        motor = LineString(road["lanes"][0]["shape"]).buffer(1.6, cap_style=2)
        head = Point(signal["x"] - 2.82, signal["z"])
        self.assertLess(signal["x"], 0)
        self.assertTrue(walk.covers(pole))
        self.assertGreaterEqual(motor.distance(pole), .45)
        self.assertLessEqual(head.distance(Point(0, 0)), 6.2)
        self.assertEqual((signal["tls"], signal["link"], signal["heading"]),
                         ("j", 3, 0.0))

    def test_network_digest_mismatch_rejects_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            network = root / "network.net.xml"
            source = root / "source.osm"
            engineering = root / "engineering-inputs.json"
            pack = root / "manifest.json"
            output = root / "road.json"
            network.write_text('<net/>')
            source.write_text('<osm/>')
            pack.write_text(json.dumps({"source": {"sha256": "a" * 64}}))
            engineering.write_text(json.dumps({"network_sha256": "b" * 64,
                                               "source_osm_sha256": "c" * 64}))
            with self.assertRaisesRegex(ValueError, "engineering receipt"):
                MODULE.build_selected_static_roads(network_path=network,
                    engineering_inputs_path=engineering, source_osm_path=source,
                    pack_manifest_path=pack, output_path=output)
            self.assertFalse(output.exists())
            self.assertFalse((root / "road-signals.json").exists())


if __name__ == "__main__":
    unittest.main()
