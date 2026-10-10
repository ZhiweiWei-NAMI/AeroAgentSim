#!/usr/bin/env python3
"""Module tests for ground-only SUMO network filtering."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


SCRIPT = Path(__file__).with_name("build-ground-city-network.py")
SPEC = importlib.util.spec_from_file_location("build_ground_city_network", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


class GroundCityNetworkTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path]:
        osm = root / "source.osm"
        osm.write_text("""<osm version="0.6">
  <node id="1" lat="31.0" lon="121.0"/>
  <node id="2" lat="31.0" lon="121.1"/>
  <node id="3" lat="31.1" lon="121.0"><tag k="ele" v="2.5"/></node>
  <node id="4" lat="31.1" lon="121.1"/>
  <node id="5" lat="31.2" lon="121.0"><tag k="ele" v="3"/></node>
  <node id="6" lat="31.2" lon="121.1"><tag k="ele" v="3"/></node>
  <way id="-11"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/>
    <tag k="bridge" v="yes"/><tag k="layer" v="1"/><tag k="aero_bench:source_id" v="1100"/></way>
  <way id="-22"><nd ref="3"/><nd ref="4"/><tag k="highway" v="secondary"/>
    <tag k="name" v="Example tunnel approach"/><tag k="aero_bench:source_id" v="2200"/></way>
  <way id="-33"><nd ref="5"/><nd ref="6"/><tag k="highway" v="residential"/>
    <tag k="layer" v="0"/></way>
  <way id="44"><nd ref="5"/><nd ref="6"/><tag k="highway" v="residential"/></way>
  <way id="-55"><nd ref="5"/><nd ref="6"/><tag k="building" v="yes"/>
    <tag k="bridge" v="yes"/><tag k="layer" v="2"/></way>
</osm>""", encoding="utf-8")
        network = root / "network.net.xml"
        network.write_text("""<net>
  <location netOffset="0,0" convBoundary="0,0,100,100" origBoundary="0,0,1,1" projParameter="+proj=aeqd +lat_0=31 +lon_0=121"/>
  <edge id="--11#0"><lane id="--11#0_0" index="0" shape="0,0 1,0"/></edge>
  <edge id="-11#1"><lane id="-11#1_0" index="0" shape="1,0 2,0"/></edge>
  <edge id="--22"><lane id="--22_0" index="0" shape="0,0 1,0"/></edge>
  <edge id="--33" from="6" to="5"><lane id="--33_0" index="0" shape="0,0 1,0"/></edge>
  <edge id="-44" from="6" to="5"><lane id="-44_0" index="0" shape="0,0 1,0"/></edge>
  <edge id=":1_0" function="internal"><lane id=":1_0_0" index="0" shape="0,0 1,0"/></edge>
  <edge id=":1_c0" function="crossing"><lane id=":1_c0_0" index="0" shape="0,0 1,0"/></edge>
  <connection from="--11#0" to="--22" fromLane="0" toLane="0" via=":1_0_0"/>
</net>""", encoding="utf-8")
        return network, osm

    def test_plan_excludes_tagged_grade_separation_and_name_only_tunnel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            network, osm = self._inputs(Path(temp_dir))
            plan = builder.plan_filter(network, osm)

        self.assertEqual(set(plan["ways"]), {"-11", "-22"})
        self.assertEqual(plan["removed_edge_ids"], ["--11#0", "--22", "-11#1"])
        self.assertNotIn(":1_0", plan["removed_edge_ids"])
        self.assertEqual(plan["ways"]["-11"]["original_osm_way_id"], "1100")
        self.assertIn("bridge_tag:yes", plan["ways"]["-11"]["reasons"])
        self.assertIn("nonzero_layer:1", plan["ways"]["-11"]["reasons"])
        self.assertNotIn("explicit_nonzero_ele:node:3=2.5", plan["ways"]["-22"]["reasons"])
        self.assertIn("tunnel_name_heuristic:name=Example tunnel approach", plan["ways"]["-22"]["reasons"])
        self.assertEqual(plan["projection"], "+proj=aeqd +lat_0=31 +lon_0=121")
        self.assertEqual(plan["net_offset"], "0,0")
        self.assertEqual(plan["input_counts"]["internal_edges"], 1)
        self.assertEqual(plan["input_counts"]["crossings"], 1)
        self.assertEqual(plan["ignored_non_highway_grade_way_count"], 1)
        self.assertEqual(builder.edge_source_way_id("-44", {"44"}), "44")
        self.assertEqual(builder.edge_source_way_id("-44", {"-44"}), "-44")
        with self.assertRaises(ValueError):
            builder.edge_source_way_id("-44", {"44", "-44"})

    def test_numeric_zero_layer_and_ele_do_not_trigger_filter(self):
        tags = {"highway": "primary", "layer": "0", "ele": "0 m", "name": "Ground Road"}
        self.assertEqual(builder.way_filter_reasons(tags), [])

    def test_absolute_ele_is_metadata_and_nonflat_profile_is_excluded(self):
        self.assertEqual(builder.way_filter_reasons({"ele": "3 m"}), [])
        with tempfile.TemporaryDirectory() as temp_dir:
            network, osm = self._inputs(Path(temp_dir))
            source = ET.parse(osm).getroot()
            source.find("node[@id='6']/tag").set("v", "7.0")
            ET.ElementTree(source).write(osm, encoding="utf-8")
            plan = builder.plan_filter(network, osm)
        self.assertIn("-33", plan["ways"])
        self.assertIn("nonflat_ele_profile:3.0..7.0", plan["ways"]["-33"]["reasons"])
        ground_slope = next(row for row in plan["elevation_review"] if row["osm_way_id"] == "-33")
        self.assertEqual(ground_slope["profile_delta_m"], 4.0)

    def test_nonzero_net_offset_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            network, osm = self._inputs(Path(temp_dir))
            root = ET.parse(network).getroot()
            root.find("location").set("netOffset", "12,0")
            ET.ElementTree(root).write(network, encoding="unicode")
            with self.assertRaisesRegex(ValueError, "requires netOffset=0,0"):
                builder.plan_filter(network, osm)

    def test_unparsed_layer_is_fail_closed_and_reported(self):
        self.assertEqual(builder.way_filter_reasons({"layer": "above"}),
                         ["unparsed_non_ground_layer:above"])

    def test_build_writes_provenance_and_ground_scope_without_touching_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            network, osm = self._inputs(root)
            before_network = builder.file_sha256(network)
            before_osm = builder.file_sha256(osm)
            output_dir = root / "out"

            def fake_netconvert(source: Path, removed: Path, staged: Path) -> list[str]:
                # The output retains ground normal edges and freshly built internal geometry.
                staged.write_text("""<net>
  <location netOffset="0,0" convBoundary="0,0,100,100" origBoundary="0,0,1,1" projParameter="+proj=aeqd +lat_0=31 +lon_0=121"/>
  <edge id="--33" from="6" to="5"><lane id="--33_0" index="0" shape="0,0 1,0"/></edge>
  <edge id="-44" from="6" to="5"><lane id="-44_0" index="0" shape="0,0 1,0"/></edge>
  <edge id=":2_0" function="internal"><lane id=":2_0_0" index="0" shape="0,0 1,0"/></edge>
  <edge id=":2_c0" function="crossing" crossingEdges="--33"><lane id=":2_c0_0" index="0" shape="0,0 1,0"/></edge>
  <connection from="--33" to="--33" fromLane="0" toLane="0" via=":2_0_0"/>
  <junction id="5" incLanes="--33_0 -44_0" intLanes="" shape="0,0 1,0"/>
  <junction id="6" incLanes="" intLanes="" shape="0,0 1,0"/>
</net>""", encoding="utf-8")
                return ["netconvert", "--sumo-net-file", str(source)]

            with mock.patch.object(builder, "verify_netconvert_image", return_value="1.27.1"), \
                    mock.patch.object(builder, "_run_netconvert", side_effect=fake_netconvert):
                report = builder.build_ground_network(network, osm, output_dir)

            engineering = json.loads((output_dir / "engineering-inputs.json").read_text())
            saved_report = json.loads((output_dir / "ground-filter-report.json").read_text())
            self.assertEqual(engineering["road_scope"], "ground-only")
            self.assertEqual(engineering["ground_filter_report"], "ground-filter-report.json")
            self.assertEqual(engineering["net_offset"], "0,0")
            self.assertEqual(engineering["net_offset_required"], "0,0")
            self.assertEqual(engineering["source_osm"], str(osm.resolve()))
            self.assertEqual(engineering["source_osm_sha256"], before_osm)
            self.assertEqual(engineering["source_network_sha256"], before_network)
            self.assertTrue((output_dir / "ground.osm").is_file())
            self.assertTrue((output_dir / "ground-network-proof.json").is_file())
            self.assertEqual(saved_report["filtered_osm"]["sha256"], builder.file_sha256(output_dir / "ground.osm"))
            self.assertFalse(saved_report["netconvert"]["source_osm_rebuilt"])
            self.assertTrue(saved_report["netconvert"]["filtered_osm_materialized_for_audit"])
            self.assertIn("--sumo-net-file", saved_report["netconvert"]["command"])
            self.assertNotIn("--osm-files", saved_report["netconvert"]["command"])
            self.assertEqual(saved_report["excluded_way_ids"], ["-11", "-22"])
            self.assertEqual(saved_report["removed_edge_ids"], ["--11#0", "--22", "-11#1"])
            self.assertEqual(saved_report["network_after"]["internal_edges"], 1)
            self.assertEqual(report["projection"]["preserved_exactly"], True)
            self.assertEqual(report["projection"]["net_offset"], "0,0")
            self.assertEqual(builder.file_sha256(network), before_network)
            self.assertEqual(builder.file_sha256(osm), before_osm)


if __name__ == "__main__":
    unittest.main()
