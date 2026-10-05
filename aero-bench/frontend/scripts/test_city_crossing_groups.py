"""Unit tests for SUMO crossing expansion and pedestrian lane patches."""
import unittest
import xml.etree.ElementTree as ET

from city_crossing_groups import (crossing_patch, crossing_patch_plan,
                                  crossing_replacements, crossing_sidewalk_patch)


def _network(second_crossing: str = "") -> ET.Element:
    return ET.fromstring(f"""<net>
      <type id="road" disallow="tram" />
      <type id="shared" allow="pedestrian passenger" />
      <type id="walk" allow="pedestrian" />
      <edge id="--way#4" from="j" to="west" type="road" shape="0,0 -30,0">
        <lane id="--way#4_0" index="0" allow="bus bicycle" width="3.2"
              shape="0,1.8 -30,1.8" />
      </edge>
      <edge id="-way#4" from="west" to="j" type="road" shape="-30,-1.8 0,-1.8">
        <lane id="-way#4_0" index="0" width="3.2" shape="-30,-1.8 0,-1.8" />
        <lane id="-way#4_1" index="1" width="3.2" shape="-30,-5.0 0,-5.0" />
        <lane id="-way#4_2" index="2" width="3.2" shape="-30,-8.2 0,-8.2" />
        <lane id="-way#4_3" index="3" width="3.2" shape="-30,-11.4 0,-11.4" />
      </edge>
      <edge id="-shared" from="j" to="south" type="shared" shape="0,0 0,-30">
        <lane id="-shared_0" index="0" allow="pedestrian passenger" width="3.2"
              shape="0,-1.8 0,-30" />
      </edge>
      <edge id="-walk" from="j" to="east" type="walk" shape="0,0 30,0">
        <lane id="-walk_0" index="0" allow="pedestrian" width="2"
              shape="0,-1.8 30,-1.8" />
      </edge>
      <edge id=":j_c0" function="crossing" crossingEdges="--way#4">
        <lane id=":j_c0_0" index="0" allow="pedestrian" width="4" length="3.2"
              shape="0,1.8 0,-1.4" />
      </edge>
      {second_crossing}
      <junction id="j" type="traffic_light" x="0" y="0" />
    </net>""")


class CrossingGroupTests(unittest.TestCase):
    def test_expands_reverse_direction_across_all_main_lanes(self) -> None:
        replacements = crossing_replacements(_network())
        self.assertEqual(len(replacements), 1)
        self.assertEqual(replacements[0].junction_id, "j")
        self.assertEqual(set(replacements[0].replacement_edges), {"--way#4", "-way#4"})
        self.assertEqual(replacements[0].width_m, 4.0)

    def test_keeps_same_segment_candidate_that_only_approaches_far_away(self) -> None:
        network = ET.fromstring("""<net>
          <type id="road" />
          <edge id="--curve#0" from="j" to="remote" type="road"
                shape="0,0 0,25 10,50">
            <lane id="--curve#0_0" index="0" width="3.2" allow="passenger"
                  shape="0,0 0,25 10,50" />
          </edge>
          <edge id="-curve#0" from="remote" to="j" type="road"
                shape="10,50 5,25 5,0">
            <lane id="-curve#0_0" index="0" width="3.2" allow="passenger"
                  shape="10,50 5,25 5,0" />
          </edge>
          <edge id=":j_c0" function="crossing" crossingEdges="--curve#0">
            <lane id=":j_c0_0" index="0" allow="pedestrian" width="4" length="3.2"
                  shape="0,1.8 0,-1.4" />
          </edge>
          <junction id="j" type="traffic_light" x="0" y="0" />
        </net>""")
        self.assertEqual(crossing_replacements(network), ())

    def test_deduplicates_addition_and_discards_short_and_existing_full_group(self) -> None:
        full_crossing = """<edge id=":j_c1" function="crossing"
            crossingEdges="--way#4 -way#4">
          <lane id=":j_c1_0" index="0" allow="pedestrian" width="4"
                length="16" shape="0,1.8 0,-14.2" />
        </edge>"""
        plan = crossing_patch_plan(_network(full_crossing))
        self.assertEqual(plan.discarded, (("j", ("--way#4",)),
                                          ("j", ("--way#4", "-way#4"))))
        self.assertEqual(len(plan.replacements), 1)
        patch = crossing_patch(_network(full_crossing))
        items = patch.findall("crossing")
        self.assertEqual(sum(item.get("discard") == "true" for item in items), 2)
        additions = [item for item in items if item.get("discard") != "true"]
        self.assertEqual(len(additions), 1)
        self.assertEqual(set(additions[0].get("edges", "").split()),
                         {"--way#4", "-way#4"})

    def test_sidewalk_patch_is_scoped_and_preserves_shared_and_walk_edges(self) -> None:
        network = _network()
        patch = crossing_sidewalk_patch(network, {"-way#4", "-shared", "-walk"})
        edges = patch.findall("edge")
        self.assertEqual([edge.get("id") for edge in edges], ["-way#4"])
        self.assertEqual(edges[0].get("sidewalkWidth"), "2")
        self.assertFalse(patch.findall("lane"))

    def test_allow_all_is_shared_and_not_restricted(self) -> None:
        network = ET.fromstring("""<net>
          <type id="road" disallow="pedestrian" />
          <edge id="all-lane" from="a" to="b" type="road">
            <lane id="all-lane_0" index="0" allow="all" />
          </edge>
        </net>""")
        self.assertEqual(crossing_sidewalk_patch(network, {"all-lane"}).findall("edge"), [])

    def test_lane_shared_allow_overrides_parent_type_disallow(self) -> None:
        network = ET.fromstring("""<net>
          <type id="road" disallow="pedestrian" />
          <edge id="shared-lane" from="a" to="b" type="road">
            <lane id="shared-lane_0" index="0"
                  allow="pedestrian passenger" width="3.2" />
          </edge>
        </net>""")
        self.assertEqual(
            crossing_sidewalk_patch(network, {"shared-lane"}).findall("edge"), [])


if __name__ == "__main__":
    unittest.main()
