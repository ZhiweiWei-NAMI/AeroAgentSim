"""Focused regressions for actual mesh projection and source fixture binding."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from city_fixture_geometry import (FOOTPRINT_PRECISION_M, displayed_fixture_triangles, fixture_in_height_band,
                                    glb_triangles, projected_fixture)


class FixtureGeometryTest(unittest.TestCase):
    def test_vertical_clip_rejects_an_arm_above_a_low_building(self):
        triangles = np.array([[[0, 6, 0], [2, 6, 0], [0, 6, 1]]], dtype=float)
        self.assertEqual(projected_fixture(triangles).area, 1)
        self.assertTrue(fixture_in_height_band(triangles, 0, 5).is_empty)
        self.assertEqual(fixture_in_height_band(triangles, 0, 7).area, 1)

    def test_footprint_union_is_snapped_to_the_declared_micrometre_grid(self):
        # Two triangles sharing an edge that differs by far less than the grid, plus a
        # collapsed triangle: the union is one valid square with vertices on the grid.
        triangles = np.array([[[0, 0, 0], [1, 0, 0], [1, 0, 1]],
                              [[0, 0, 0], [1 + 1e-9, 0, 1 - 1e-9], [0, 0, 1]],
                              [[0, 0, 0], [.5, 0, 0], [1, 0, 0]]], dtype=float)
        footprint = projected_fixture(triangles)
        self.assertTrue(footprint.is_valid)
        self.assertEqual(footprint.geom_type, 'Polygon')
        self.assertAlmostEqual(footprint.area, 1, places=9)
        grid = np.asarray(footprint.exterior.coords) / FOOTPRINT_PRECISION_M
        np.testing.assert_allclose(grid, np.round(grid), atol=1e-6)

    def test_vertical_clip_uses_the_intersection_at_the_roof(self):
        triangles = np.array([[[0, 0, 0], [2, 2, 0], [0, 2, 2]]], dtype=float)
        self.assertAlmostEqual(fixture_in_height_band(triangles, 0, 1).area, .5)
        self.assertAlmostEqual(fixture_in_height_band(triangles, 1, 2).area, 1.5)

    def test_displayed_signal_keeps_the_measured_pole_anchor(self):
        triangles = np.array([[[1.12309, 0, -.24263], [2.12309, 2, -.24263],
                               [1.12309, 2, .75737]]])
        result = displayed_fixture_triangles(triangles, 'signal')
        np.testing.assert_allclose(result[0, 0], [0, 0, 0])
        np.testing.assert_allclose(result[0, 1], [3.2, 6.4, 0])

    def test_displayed_signal_arm_clears_the_declared_motor_height_band(self):
        from city_effective_fixtures import MOTOR_TOP_UP_M, POLE_CLEARANCE_M
        model = Path(__file__).resolve().parents[1] / 'public/models/incoming/furniture/glb/traffic_light_4.glb'
        displayed = displayed_fixture_triangles(glb_triangles(model), 'signal')
        # The arm extends along -x over the carriageway; faces reaching past the pole base
        # on that side are the arm and its heads.
        pole_half_width = np.abs(displayed[displayed[:, :, 1].max(axis=1) < .4][:, :, [0, 2]]).max()
        arm = displayed[:, :, 0].min(axis=1) < -(pole_half_width + .3)
        self.assertGreater(arm.sum(), 0)
        self.assertGreaterEqual(displayed[arm][:, :, 1].min(), MOTOR_TOP_UP_M + POLE_CLEARANCE_M)

    def test_collapsed_model_and_invalid_glb_are_explicit_errors(self):
        with self.assertRaisesRegex(ValueError, 'measured height'):
            displayed_fixture_triangles(np.zeros((1, 3, 3)), 'signal')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.glb'
            path.write_bytes(b'not-a-glb')
            with self.assertRaisesRegex(ValueError, 'GLB header'):
                glb_triangles(path)


if __name__ == '__main__':
    unittest.main()
