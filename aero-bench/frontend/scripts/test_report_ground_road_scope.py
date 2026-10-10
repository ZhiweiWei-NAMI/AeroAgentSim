import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from report_ground_road_scope import network_connectivity, structural_exclusions

_spec = importlib.util.spec_from_file_location("refine", Path(__file__).with_name("refine-ground-network-physical.py"))
refine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refine)


def _net(body: str) -> bytes:
    return f'<net><location convBoundary="0,0,1000,1000" netOffset="0,0"/>{body}</net>'.encode()


LINE = (  # boundary (b1 at x=0) -> j -> k, and k -> j -> boundary back
    '<junction id="b1" type="dead_end" x="0" y="500"/><junction id="j" type="priority" x="400" y="500"/>'
    '<junction id="k" type="dead_end" x="600" y="500"/>'
    '<edge id="a" from="b1" to="j"><lane id="a_0" index="0" allow="passenger"/></edge>'
    '<edge id="b" from="j" to="k"><lane id="b_0" index="0" allow="passenger"/></edge>'
    '<edge id="-b" from="k" to="j"><lane id="-b_0" index="0" allow="passenger"/></edge>'
    '<edge id="-a" from="j" to="b1"><lane id="-a_0" index="0" allow="passenger"/></edge>'
    '<connection from="a" to="b" fromLane="0" toLane="0"/><connection from="-b" to="-a" fromLane="0" toLane="0"/>'
)


class GroundScopeConnectivityTest(unittest.TestCase):
    def test_boundary_cut_roads_connect_through_the_outside(self):
        with_turn = LINE + '<connection from="b" to="-b" fromLane="0" toLane="0" dir="t"/>'
        result = network_connectivity(_net(with_turn))["passenger"]
        self.assertEqual(result["boundary_connected_share"], 1.0)
        self.assertEqual(result["interior_dead_end_edges"], 0)

    def test_interior_stub_without_turnaround_is_a_dead_end(self):
        result = network_connectivity(_net(LINE))["passenger"]
        self.assertEqual(result["interior_dead_end_edges"], 1)
        self.assertEqual(result["sample_interior_dead_ends"], ["b"])
        self.assertLess(result["boundary_connected_share"], 1.0)

    def test_pedestrians_move_between_edges_through_walking_areas(self):
        body = ('<junction id="b1" type="dead_end" x="0" y="500"/><junction id="j" type="priority" x="400" y="500"/>'
                '<junction id="b2" type="dead_end" x="1000" y="500"/>'
                '<edge id="s1" from="b1" to="j"><lane id="s1_0" index="0" allow="pedestrian"/></edge>'
                '<edge id="s2" from="j" to="b2"><lane id="s2_0" index="0" allow="pedestrian"/></edge>'
                '<edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian"/></edge>'
                '<connection from="s1" to=":j_w0" fromLane="0" toLane="0"/><connection from=":j_w0" to="s2" fromLane="0" toLane="0"/>')
        result = network_connectivity(_net(body))["pedestrian"]
        self.assertEqual(result["usable_edges"], 2)
        self.assertEqual(result["boundary_connected_share"], 1.0)

    def test_structural_exclusions_flag_name_only_cases_as_ambiguous(self):
        report = {"excluded_ways": {"1": {"highway": "trunk", "reasons": ["bridge_tag:bridge=yes", "nonzero_layer:1"]},
                                    "2": {"highway": "secondary", "reasons": ["tunnel_name_heuristic:name=人民路隧道"]}}}
        summary = structural_exclusions(report)
        self.assertEqual(summary["excluded_way_count"], 2)
        self.assertEqual(summary["reason_counts"], {"bridge_tag": 1, "nonzero_layer": 1, "tunnel_name_heuristic": 1})
        self.assertEqual([row["way_id"] for row in summary["name_only_ambiguous"]], ["2"])


class RefinementRuleTest(unittest.TestCase):
    def _connectivity(self, share, dead):
        return {name: {"boundary_connected_share": share, "boundary_connected_edges": int(share * 100),
                       "interior_dead_end_edges": dead}
                for name in ("passenger", "bicycle", "pedestrian")}

    def test_removal_is_accepted_only_when_no_class_gets_worse(self):
        before = self._connectivity(0.8, 10)
        self.assertTrue(refine._not_worse(self._connectivity(0.8, 10), before))
        self.assertFalse(refine._not_worse(self._connectivity(0.79, 10), before))
        self.assertFalse(refine._not_worse(self._connectivity(0.8, 11), before))

    def test_better_percentage_cannot_hide_lost_reachable_edges(self):
        before = self._connectivity(0.8, 10)
        after = self._connectivity(0.9, 10)
        after['pedestrian']['boundary_connected_edges'] = 72
        self.assertFalse(refine._not_worse(after, before))

    def test_absent_classes_have_no_percentage_and_do_not_crash(self):
        empty = {name: {'boundary_connected_share': None, 'boundary_connected_edges': 0,
                       'interior_dead_end_edges': 0} for name in ('passenger', 'bicycle', 'pedestrian')}
        self.assertTrue(refine._not_worse(empty, empty))

    def test_individually_safe_movements_are_checked_as_a_cumulative_batch(self):
        network = _net(
            '<junction id="west" type="dead_end" x="0" y="500"/>'
            '<junction id="j" type="priority" x="500" y="500"/>'
            '<junction id="east" type="dead_end" x="1000" y="500"/>'
            '<edge id="a" from="west" to="j"><lane id="a_0" allow="passenger bicycle"/></edge>'
            '<edge id="b" from="j" to="east"><lane id="b_0" allow="passenger bicycle"/></edge>'
            '<edge id="c" from="j" to="east"><lane id="c_0" allow="passenger bicycle"/></edge>'
            '<edge id="d" from="east" to="j"><lane id="d_0" allow="passenger bicycle"/></edge>'
            '<connection from="a" to="b" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>'
            '<connection from="a" to="c" fromLane="0" toLane="0" via=":j_1_0" dir="s"/>'
            '<connection from="d" to="b" fromLane="0" toLane="0"/>'
            '<connection from="d" to="c" fromLane="0" toLane="0"/>')

        def fixture_audit(path, *_):
            root = ET.parse(path).getroot()
            rows = [{'lane_id': connection.get('via'), 'function': 'internal', 'area_m2': 1,
                     'building_id': 'fixture-building', 'classification': 'fixture-contact'}
                    for connection in root.findall('connection') if connection.get('via')]
            return {'native_lane_clearance': {'rows': rows},
                    'gate': {'status': 'BLOCKED' if rows else 'PASS'},
                    'connectivity': network_connectivity(path.read_bytes())}

        def fixture_netconvert(source, target, connections, removals):
            root = ET.parse(source).getroot()
            deleted = [item.attrib for item in ET.parse(connections).getroot()]
            for connection in list(root.findall('connection')):
                if any(all(connection.get(key) == row[key] for key in row) for row in deleted):
                    root.remove(connection)
            target.write_bytes(ET.tostring(root))
            return []

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'network.xml'; source.write_bytes(network)
            with patch.object(refine, 'audit', side_effect=fixture_audit), \
                 patch.object(refine, '_netconvert', side_effect=fixture_netconvert), \
                 patch.object(refine, '_write_engineering_receipt'):  # The receipt needs an authored network.
                result = refine.refine(source, source, Path(directory) / 'output', source, source, source)
            self.assertEqual(result['deleted_movements'], 1)
            self.assertEqual(result['final_gate']['status'], 'BLOCKED')
            self.assertEqual(result['interior_dead_ends']['passenger'], 0)


class GroundScopeExclusionsTest(unittest.TestCase):
    def _chain(self, root: Path, network_edges: list[str]):
        import hashlib, json
        source, authored, output = root / "source", root / "authored", root / "refined"
        for path in (source, authored, output):
            path.mkdir()
        report = source / "filter.json"
        report.write_text(json.dumps({"removed_edge_ids": ["up"]}))
        (source / "engineering-inputs.json").write_text(json.dumps({"source_ground_filter_report": {
            "path": "filter.json", "sha256": hashlib.sha256(report.read_bytes()).hexdigest()}}))
        (authored / "excluded-edge-ids.txt").write_text("auth\n")
        (output / "physical-removed-edges.txt").write_text("phys\n")
        network = ET.fromstring("<net>" + "".join(f'<edge id="{edge}"/>' for edge in network_edges) + "</net>")
        return {"source_network": str(source / "network.net.xml")}, authored, output, network

    def test_exclusions_of_every_stage_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = refine.ground_scope_exclusions(*self._chain(Path(tmp), ["kept"]))
        self.assertEqual(result["removed_edge_ids"], ["auth", "phys", "up"])
        self.assertEqual(result["removed_edge_ids_by_stage"]["strict_ground_filter"], ["up"])

    def test_an_excluded_edge_left_in_the_network_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "still contains excluded edges"):
                refine.ground_scope_exclusions(*self._chain(Path(tmp), ["kept", "up"]))

    def test_a_changed_upstream_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            chain = self._chain(Path(tmp), ["kept"])
            (Path(tmp) / "source" / "filter.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "differs from its engineering receipt"):
                refine.ground_scope_exclusions(*chain)


if __name__ == "__main__":
    unittest.main()
