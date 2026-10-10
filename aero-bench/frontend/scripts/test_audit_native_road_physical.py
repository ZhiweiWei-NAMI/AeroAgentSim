"""Diagnostic exceptions must not bypass the road producer's strict physical gate."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import audit_native_road_physical as module


class NativePhysicalGateTest(unittest.TestCase):
    def _audit(self, *, walking=(), collapsed=()):
        proof = {'status': 'BLOCKED' if walking or collapsed else 'PASS', 'rows': [],
                 'pair_count': 0, 'contact_area_sum_per_lane_m2': 0,
                 'unrenderable_generated_areas': list(walking),
                 'unrenderable_native_lanes': list(collapsed)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'network.xml'
            path.write_text('<net><location convBoundary="0,0,10,10"/></net>')
            with patch.object(module, 'load_canonical_city_geometry', return_value=SimpleNamespace(origin={}, footprints=[])), \
                 patch.object(module, 'CityEnuProjection'), \
                 patch.object(module, 'lane_building_conflicts', return_value=proof), \
                 patch.object(module, 'road_end_carriageway_crossings', return_value={x['lane_id'] for x in walking}), \
                 patch.object(module, 'straight_abutments', return_value={x['lane_id'] for x in collapsed}):
                return module.audit(path, path, path, path, path)

    def test_clear_native_geometry_passes(self):
        result = self._audit()
        self.assertEqual(result['gate']['status'], 'PASS')

    def test_carriageway_end_classification_does_not_publish_an_undrawable_area(self):
        result = self._audit(walking=[{'lane_id': ':end_w0_0'}])
        self.assertEqual(result['candidate_geometry_gate']['status'], 'PASS')
        self.assertEqual(result['gate']['status'], 'BLOCKED')
        self.assertEqual(result['gate']['undrawable_walking_areas'], 1)

    def test_straight_abutment_classification_does_not_publish_a_collapsed_lane(self):
        result = self._audit(collapsed=[{'lane_id': ':join_0_0'}])
        self.assertEqual(result['candidate_geometry_gate']['status'], 'PASS')
        self.assertEqual(result['gate']['status'], 'BLOCKED')
        self.assertEqual(result['gate']['collapsed_native_lanes'], 1)


if __name__ == '__main__':
    unittest.main()
