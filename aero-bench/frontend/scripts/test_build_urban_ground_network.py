import runpy
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

module = runpy.run_path(str(Path(__file__).with_name("build-urban-ground-network.py")))


class UrbanGroundNetworkTest(unittest.TestCase):
    def test_netconvert_result_rejects_phantom_contraflow_and_wrong_bus_side(self):
        network = ET.fromstring('''<net><edge id="-123#0"><lane index="0" allow="pedestrian"/>
          <lane index="1" allow="passenger bicycle"/><lane index="2" allow="bus"/>
        </edge></net>''')
        changes = [{"way_id": "-123", "lane_count": 2}]
        module["verify_busway_lanes"](network, changes)
        reverse = ET.SubElement(network, "edge", id="--123#0")
        with self.assertRaisesRegex(ValueError, "contraflow"):
            module["verify_busway_lanes"](network, changes)
        network.remove(reverse)
        network.find("edge").findall("lane")[-1].set("allow", "passenger")
        with self.assertRaisesRegex(ValueError, "bus-only"):
            module["verify_busway_lanes"](network, changes)

    def test_same_direction_left_bus_lane_keeps_direction_and_count(self):
        source = b'<osm><way id="10"><tag k="oneway" v="yes"/><tag k="lanes" v="4"/><tag k="busway:left" v="lane"/></way></osm>'
        result, changes = module["normalize_busway_tags"](source, b'<types/>')
        tags = {v.attrib['k']: v.attrib['v'] for v in ET.fromstring(result).findall('way/tag')}
        self.assertEqual(tags, {'oneway': 'yes', 'lanes': '4', 'bus:lanes': 'designated|yes|yes|yes'})
        self.assertEqual(changes[0]['lane_count_source'], 'osm_lanes')

    def test_explicit_contraflow_permission_is_not_removed(self):
        source = b'<osm><way id="10"><tag k="oneway" v="yes"/><tag k="oneway:bus" v="no"/><tag k="busway:left" v="lane"/></way></osm>'
        result, changes = module["normalize_busway_tags"](source, b'<types/>')
        self.assertEqual(changes, [])
        self.assertEqual(len(ET.fromstring(result).findall('way/tag')), 3)

    def test_missing_lane_count_uses_declared_type_and_reports_assumption(self):
        source = b'<osm><way id="10"><tag k="highway" v="primary"/><tag k="oneway" v="yes"/><tag k="busway:left" v="lane"/></way></osm>'
        _, changes = module["normalize_busway_tags"](source, b'<types><type id="highway.primary" numLanes="2"/></types>')
        self.assertEqual(changes[0]['staged_bus_lanes'], 'designated|yes')
        self.assertEqual(changes[0]['lane_count_source'], 'declared_typemap_default')

    def test_typemap_keeps_unrelated_types_and_lane_permissions(self):
        root = ET.Element("types")
        for name in module["URBAN_SPEEDS_MPS"]:
            ET.SubElement(root, "type", id="highway." + name, numLanes="2",
                          oneway="false", disallow="rail", priority="12", speed="27.78")
        ET.SubElement(root, "type", id="highway.footway", allow="pedestrian", width="2")
        ET.SubElement(root, "type", id="highway.service", allow="delivery pedestrian bicycle")
        bicycle = ET.Element("types")
        for name in module["BICYCLE_TYPES"]:
            ET.SubElement(bicycle, "type", id=name, bikeLaneWidth="1.25", allow="bicycle")
        result = ET.fromstring(module["urban_typemap"](ET.tostring(root), ET.tostring(bicycle)))
        self.assertEqual(len(root) + len(bicycle), len(result))
        before = {item.attrib["id"]: item for item in root}
        for item in result:
            if item.attrib["id"] in module["BICYCLE_TYPES"]:
                self.assertEqual(item.attrib["allow"], "bicycle")
                continue
            previous = before[item.attrib["id"]]
            if item.attrib["id"] in ("highway.footway", "highway.service"):
                self.assertEqual(item.attrib, previous.attrib)
            else:
                for key in ("numLanes", "oneway", "disallow", "priority"):
                    self.assertEqual(item.attrib[key], previous.attrib[key])

    def test_ground_filter_keeps_node_coordinates_and_road_tags(self):
        source = b'<osm><node id="1" lat="31.2" lon="121.4"/><way id="a"><nd ref="1"/><tag k="maxspeed" v="60"/></way><way id="b"/><relation id="r"><member type="way" ref="a"/><member type="way" ref="b"/></relation></osm>'
        result = ET.fromstring(module["ground_osm"](source, {"b"}))
        self.assertEqual([w.attrib["id"] for w in result.findall("way")], ["a"])
        self.assertEqual(result.find("node").attrib["lat"], "31.2")
        self.assertEqual(result.find("way/tag").attrib["v"], "60")
        self.assertEqual(len(result.findall("relation/member")), 1)

    def test_opposite_bicycle_lane_requires_reverse_bike_only_edge(self):
        source = b'<osm><way id="42"><tag k="highway" v="tertiary"/><tag k="oneway" v="yes"/><tag k="cycleway" v="opposite_lane"/></way></osm>'
        network = ET.fromstring('<net><edge id="42"><lane allow="passenger bicycle"/></edge><edge id="-42"><lane allow="bicycle"/><lane allow="pedestrian"/></edge></net>')
        audit = module["verify_bicycle_lanes"](source, network)
        self.assertEqual(audit[0]["direction"], "reverse")
        reverse = network.findall("edge")[1]
        ET.SubElement(reverse, "lane", allow="passenger bicycle")
        with self.assertRaisesRegex(ValueError, "admits motor traffic"):
            module["verify_bicycle_lanes"](source, network)
        reverse.remove(reverse.findall("lane")[-1])
        reverse.findall("lane")[0].set("allow", "passenger")
        with self.assertRaisesRegex(ValueError, "lacks dedicated bike lane"):
            module["verify_bicycle_lanes"](source, network)

    def test_directional_track_tags_report_unsurveyed_geometry_per_direction(self):
        network = ET.fromstring('<net><edge id="42"><lane allow="bicycle"/></edge><edge id="-42"><lane allow="bicycle"/></edge></net>')
        for tags, expected in (
            ('<tag k="cycleway:right" v="track"/>', {"forward": ["cycleway:right"]}),
            ('<tag k="cycleway:left" v="track"/>', {"reverse": ["cycleway:left"]}),
            ('<tag k="cycleway:both" v="track"/>', {
                "forward": ["cycleway:both"], "reverse": ["cycleway:both"]}),
        ):
            with self.subTest(tags=tags):
                source = f'<osm><way id="42"><tag k="highway" v="tertiary"/>{tags}</way></osm>'.encode()
                audit = module["verify_bicycle_lanes"](source, network)
                self.assertEqual({item["direction"]: item["track_tag_sources"] for item in audit}, expected)
                self.assertTrue(all("separate surveyed track geometry unavailable" in item["track_geometry_policy"]
                                    for item in audit))


if __name__ == "__main__":
    unittest.main()
