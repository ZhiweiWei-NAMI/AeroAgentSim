"""Module tests use small authored XML; production proof uses actual native SUMO."""
import copy
import unittest
import xml.etree.ElementTree as ET

from city_pedestrian_sidewalks import (audit_completed_crossings, audit_motor_lanes,
    crossing_walk_ports, plan_crossing_sidewalks)


def inputs():
    source = ET.fromstring('''<osm><node id="j"><tag k="crossing" v="zebra" /></node>
      <way id="42"><tag k="highway" v="residential" /><tag k="oneway" v="yes" /></way>
      <way id="43"><tag k="highway" v="footway" /></way></osm>''')
    net = ET.fromstring('''<net>
      <junction id="a" x="0" y="0" /><junction id="j" x="10" y="0" />
      <junction id="b" x="20" y="0" />
      <edge id="42" from="a" to="j" spreadType="center">
        <lane id="42_0" index="0" speed="13.89" width="3.2" shape="0,0 10,0" /></edge>
      <edge id="43" from="j" to="b"><lane id="43_0" index="0" allow="pedestrian"
        speed="1.4" width="2" shape="10,0 20,0" /></edge>
      <edge id=":j_c0" function="crossing" crossingEdges="42">
        <lane id=":j_c0_0" index="0" allow="pedestrian" /></edge>
      <edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian" /></edge>
      <edge id=":j_w1" function="walkingarea"><lane id=":j_w1_0" index="0" allow="pedestrian" /></edge>
      <connection from="42" to=":j_w0" fromLane="0" toLane="0" />
      <connection from=":j_w0" to=":j_c0" fromLane="0" toLane="0" />
      <connection from=":j_c0" to=":j_w1" fromLane="0" toLane="0" />
      <connection from=":j_w1" to="43" fromLane="0" toLane="0" />
    </net>''')
    return net, source


def converted(before):
    after = copy.deepcopy(before)
    road = after.find("edge[@id='42']")
    road.find('lane').set('id', '42_1')
    road.find('lane').set('index', '1')
    road.find('lane').set('disallow', 'pedestrian')
    road.insert(0, ET.Element('lane', id='42_0', index='0', allow='pedestrian', width='2', speed='13.89', shape='0,-2.6 10,-2.6'))
    reverse = ET.SubElement(after, 'edge', id='-42', **{'from': 'j', 'to': 'a'})
    ET.SubElement(reverse, 'lane', id='-42_0', index='0', allow='pedestrian', width='2', speed='13.89')
    return after


class NativeSidewalkPolicyTest(unittest.TestCase):
    def test_policy_derives_both_sides_without_rewriting_source_tags(self):
        net, source = inputs()
        original = ET.tostring(source)
        patch, plan = plan_crossing_sidewalks(net, source)
        self.assertEqual(ET.tostring(source), original)
        self.assertEqual(len(plan['missing_source_endpoints']), 1)
        self.assertFalse(plan['surveyed_sidewalk'])
        self.assertEqual(patch.find("edge[@id='42']").get('sidewalkWidth'), '2')
        reverse = patch.find("edge[@id='-42']")
        self.assertEqual((reverse.get('from'), reverse.get('to'), reverse.get('allow')), ('j', 'a', 'pedestrian'))
        self.assertEqual(plan['derived_edges'][1]['source_way_id'], '42')

    def test_explicit_source_side_and_foot_prohibitions_are_not_overridden(self):
        for key, value in [('foot', 'no'), ('sidewalk', 'no'), ('sidewalk:right', 'no')]:
            net, source = inputs()
            ET.SubElement(source.find("way[@id='42']"), 'tag', k=key, v=value)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'forbids inferred sidewalk'):
                plan_crossing_sidewalks(net, source)

    def test_mixed_pedestrian_permission_is_not_an_exclusive_walk_port(self):
        net, _ = inputs()
        net.find("edge[@id='42']/lane").set('allow', 'pedestrian passenger')
        rows = crossing_walk_ports(net, {':j_c0'})
        self.assertFalse(rows[0]['exclusive_walk_port'])
        self.assertTrue(rows[1]['exclusive_walk_port'])

    def test_native_lane_renumbering_preserves_vehicle_rights(self):
        net, _ = inputs()
        result = audit_motor_lanes(net, converted(net), {'-42'})
        self.assertEqual(result['motor_lanes_preserved'], 1)
        self.assertEqual(result['normal_edges_after'], 3)

    def test_reverse_pedestrian_edge_cannot_introduce_vehicle_contraflow(self):
        net, _ = inputs()
        after = converted(net)
        after.find("edge[@id='-42']/lane").set('allow', 'pedestrian passenger')
        with self.assertRaisesRegex(ValueError, 'vehicle contraflow'):
            audit_motor_lanes(net, after, {'-42'})

    def test_sidewalk_conversion_cannot_reduce_vehicle_lanes_width_speed_or_rights(self):
        for field, value in [('width', '2'), ('speed', '8.33'), ('allow', 'bicycle')]:
            net, _ = inputs()
            after = converted(net)
            after.find("edge[@id='42']/lane[@index='1']").set(field, value)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'rights/speed/width'):
                audit_motor_lanes(net, after, {'-42'})
        net, _ = inputs()
        after = converted(net)
        after.find("edge[@id='42']").remove(after.find("edge[@id='42']/lane[@index='1']"))
        with self.assertRaisesRegex(ValueError, 'motor lane count'):
            audit_motor_lanes(net, after, {'-42'})

    def test_vehicle_connection_loss_is_exposed(self):
        net, _ = inputs()
        branch = ET.SubElement(net, 'edge', id='77', **{'from': 'j', 'to': 'b'})
        ET.SubElement(branch, 'lane', id='77_0', index='0', width='3.2', speed='13.89', allow='passenger')
        connection = ET.SubElement(net, 'connection', **{'from': '42', 'to': '77', 'fromLane': '0', 'toLane': '0'})
        after = converted(net)
        after.remove(after.find("connection[@to='77']"))
        with self.assertRaisesRegex(ValueError, 'removed motor route connections'):
            audit_motor_lanes(net, after, {'-42'})

    def test_native_success_cannot_hide_a_discarded_crossing(self):
        net, source = inputs()
        _, plan = plan_crossing_sidewalks(net, source)
        after = converted(net)
        after.remove(after.find("edge[@id=':j_c0']"))
        with self.assertRaisesRegex(ValueError, 'discarded an unplanned source crossing'):
            audit_completed_crossings(net, after, plan)

    def test_complete_source_connections_still_require_exclusive_walk_lanes(self):
        net, source = inputs()
        _, plan = plan_crossing_sidewalks(net, source)
        with self.assertRaisesRegex(ValueError, 'still lacks exclusive'):
            audit_completed_crossings(net, net, plan)
        result = audit_completed_crossings(net, converted(net), plan)
        self.assertTrue(result['all_selected_endpoints_have_exclusive_walk_ports'])


if __name__ == '__main__':
    unittest.main()
