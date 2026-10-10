"""Module tests for bounded, collision-aware roadside surface completion."""
import unittest
from shapely.affinity import rotate, translate
from shapely import get_precision
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box
from shapely.ops import unary_union

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import city_street_surfaces
from city_street_surfaces import build_sidewalk_layout

class SidewalkLayoutTest(unittest.TestCase):
    def test_connection_paving_fills_declared_gap_without_covering_motor_or_building(self):
        road, walk = box(0, 0, 10, 4), box(0, 5, 10, 7)
        building = box(8, 4.1, 10, 5)
        motor = box(0, 0, 10, 3.8)
        corridor = box(3, 3, 4.5, 6)
        result = city_street_surfaces.complete_pedestrian_port_paving(
            road, walk, [corridor, box(8, 3, 9, 6)], motor, building)
        self.assertTrue(result["walkbed"].covers(box(3, 4.01, 4.5, 6)))
        self.assertEqual(result["added_walkbed"].intersection(road).area, 0)
        self.assertEqual(result["added_walkbed"].intersection(building.buffer(.15)).area, 0)
        self.assertEqual(result["added_walkbed"].intersection(motor.buffer(.15)).area, 0)
        self.assertFalse(result["provenance"]["surveyed"])

    def test_connection_paving_does_not_fill_unrelated_empty_space(self):
        result = city_street_surfaces.complete_pedestrian_port_paving(
            box(0, 0, 10, 4), box(0, 5, 10, 7), [], Polygon(), Polygon())
        self.assertTrue(result["added_walkbed"].is_empty)

    def test_declared_pedestrian_corridor_has_no_artificial_separator_gap(self):
        road, walk = box(0, 0, 10, 4), box(0, 4.003, 10, 6)
        corridor = box(3, 3, 4.5, 5)
        result = city_street_surfaces.close_pedestrian_port_seams(road, walk, [corridor], Polygon())
        self.assertTrue(unary_union([result["roadbed"], walk]).buffer(.003).covers(corridor))
        self.assertEqual(result["roadbed"].intersection(walk).area, 0)
        self.assertFalse(result["roadbed"].covers(box(6, 4, 7, 4.003)))

    def test_connection_seam_does_not_extend_sharp_road_tip_beyond_one_cm(self):
        road = Polygon([(0, 0), (1, -.05), (1, .05)])
        result = city_street_surfaces.close_pedestrian_port_seams(
            road, Polygon(), [box(-.1, -.1, .1, .1)], Polygon())
        self.assertLess(result["added_roadbed"].difference(road.buffer(.01)).area, 1e-12)
        parts = city_street_surfaces._polygons(result["added_roadbed"])
        self.assertLessEqual(max(Point(p).distance(road) for part in parts
                                 for p in part.exterior.coords), .01 + 1e-10)

    def test_seam_respects_original_walk_edge_before_grid_rounding(self):
        road, walk = box(0, 0, 10, 1), box(0, 1.0026, 10, 2)
        result = city_street_surfaces.close_pedestrian_port_seams(
            road, walk, [box(2, .5, 8, 1.5)], Polygon())
        self.assertLess(result["roadbed"].intersection(walk).area, 1e-12)

    def setUp(self):
        self.roadbed = box(0, 0, 40, 8)
        self.motor_core = box(1, 1, 39, 7)
        self.eligible = box(-4, -4, 44, 12)
        self.no_buildings = []
        self.no_cuts = []

    def build(self, **overrides):
        values = {
            "roadbed": self.roadbed,
            "walkbed": Polygon(),
            "motor_core": self.motor_core,
            "building_footprints": self.no_buildings,
            "eligible_corridors": self.eligible,
            "crossing_cuts": self.no_cuts,
        }
        values.update(overrides)
        return build_sidewalk_layout(**values)

    def test_derives_both_sides_without_covering_traffic_or_source_road(self):
        layout = self.build()
        sidewalk = layout["derived_walkbed"]

        self.assertGreater(sidewalk.area, 0)
        self.assertTrue(sidewalk.intersects(box(5, -2.1, 35, -0.1)))
        self.assertTrue(sidewalk.intersects(box(5, 8.1, 35, 10.1)))
        self.assertLess(sidewalk.intersection(self.roadbed).area, 1e-8)
        self.assertAlmostEqual(sidewalk.distance(layout["roadbed"]), 0.003, places=6)
        self.assertLess(sidewalk.intersection(self.motor_core.buffer(0.15)).area, 1e-8)
        self.assertEqual(get_precision(layout["roadbed"]), 0.001)
        self.assertLess(layout["walkbed"].intersection(layout["roadbed"]).area, 1e-8)
        self.assertEqual(layout["provenance"]["sumo_lane_topology_modified"], False)
        self.assertEqual(layout["provenance"]["sidewalk_kind"],
                         "derived_paved_sidewalk_not_surveyed")

    def test_crossing_opens_curb_without_breaking_sidewalk_surface(self):
        crossing = box(19, -0.3, 21, 0.3)
        layout = self.build(crossing_cuts=[crossing])
        curb = layout["curb_lines"]

        self.assertFalse(curb.intersects(LineString([(19.5, 0), (20.5, 0)])))
        south_sidewalk = LineString([(20, -2.1), (20, -0.1)])
        self.assertTrue(layout["derived_walkbed"].covers(south_sidewalk))
        self.assertEqual(layout["provenance"]["crossing_cuts_affect"], "curb_lines_only")

    def test_building_clearance_removes_sidewalk_without_overlap(self):
        building = box(10, -1.8, 12, -1.0)
        layout = self.build(building_footprints=[building])

        self.assertLess(layout["derived_walkbed"].intersection(building.buffer(0.15)).area, 1e-8)
        self.assertGreater(layout["stats"]["building_clearance_excluded_area_m2"], 0)
        self.assertGreater(layout["stats"]["unclassified_remaining_area_m2"], 0)

    def test_preserves_existing_walkbed_and_does_not_double_pave_it(self):
        source = box(5, -1.5, 15, -0.5)
        layout = self.build(walkbed=source)

        self.assertTrue(layout["walkbed"].covers(source))
        self.assertLess(layout["derived_walkbed"].intersection(source).area, 1e-8)
        self.assertGreater(layout["stats"]["source_walkbed_overlap_area_m2"], 0)

    def test_source_walk_is_clipped_to_the_same_normalized_roadbed(self):
        source = box(5, -0.2, 15, 1.0)
        layout = self.build(walkbed=source)

        self.assertTrue(layout["roadbed"].is_valid)
        self.assertLess(layout["walkbed"].intersection(layout["roadbed"]).area, 1e-8)
        self.assertAlmostEqual(layout["walkbed"].distance(layout["roadbed"]), 0.003,
                               places=6)
        self.assertLess(layout["derived_walkbed"].intersection(layout["roadbed"]).area, 1e-8)

    def test_large_block_interior_is_not_filled(self):
        ring = box(0, 0, 100, 60).difference(box(10, 10, 90, 50))
        broad_mask = box(-5, -5, 105, 65)
        layout = build_sidewalk_layout(ring, Polygon(), box(2, 2, 98, 58), [],
                                       broad_mask, [])

        self.assertLess(layout["derived_walkbed"].intersection(box(20, 20, 80, 40)).area, 1e-8)
        self.assertEqual(layout["median_beds"].area, 0)

    def test_only_long_narrow_enclosed_band_is_separated_as_paved_median(self):
        road_ring = box(0, 0, 100, 20).difference(box(10, 8, 90, 12))
        inner_motor_lanes = road_ring.buffer(-1.0)
        layout = build_sidewalk_layout(road_ring, Polygon(), inner_motor_lanes, [],
                                       box(-5, -5, 105, 25), [])

        self.assertGreater(layout["median_beds"].area, 0)
        self.assertLess(layout["median_beds"].intersection(layout["derived_walkbed"]).area, 1e-8)
        self.assertEqual(layout["provenance"]["median_kind"],
                         "derived_paved_narrow_enclosed_strip_not_surveyed")

    def test_tile_partition_seams_do_not_create_curbs(self):
        # Unioning adjacent mesh tiles must recover the same source topology.
        split_roadbed = unary_union([box(0, 0, 20, 8), box(20, 0, 40, 8)])
        layout_whole = self.build()
        layout_split = self.build(roadbed=split_roadbed)

        self.assertAlmostEqual(layout_whole["curb_lines"].length,
                               layout_split["curb_lines"].length, places=6)
        self.assertLess(layout_split["curb_lines"].intersection(
            LineString([(20, -3), (20, 11)])).length, 1e-8)

    def test_corridor_mask_limits_derived_surfaces(self):
        layout = self.build(eligible_corridors=box(0, 2, 40, 6))

        self.assertLess(layout["derived_walkbed"].area, 1e-8)
        self.assertGreater(layout["stats"]["outside_eligible_corridor_area_m2"], 0)

    def test_submillimeter_shared_edge_is_snapped_without_thin_overlay_sliver(self):
        source_walk = box(10, 8.0004, 30, 9.4004)
        corridor = box(-4, -4, 44, 10.2004)
        layout = self.build(walkbed=source_walk, eligible_corridors=corridor)

        for name in ("walkbed", "derived_walkbed", "median_beds", "curb_lines"):
            geometry = layout[name]
            self.assertTrue(geometry.is_valid, name)
            self.assertEqual(get_precision(geometry), 0.001, name)
        self.assertLess(layout["derived_walkbed"].intersection(source_walk).area, 1e-8)
        self.assertEqual(layout["stats"]["precision_grid_m"], 0.001)

    def test_polygon_overlay_discards_line_remnants_from_mixed_geometry_collection(self):
        mixed_corridor = GeometryCollection([
            box(0, 0, 10, 10),
            LineString([(0, 0), (10, 10)]),
        ])
        overlap = city_street_surfaces._polygon_intersection(
            mixed_corridor, box(5, 5, 15, 15), "mixed dimension overlay")

        self.assertEqual(overlap.geom_type, "Polygon")
        self.assertTrue(overlap.is_valid)
        self.assertEqual(get_precision(overlap), 0.001)
        self.assertAlmostEqual(overlap.area, 25.0)

    def test_invalid_polygon_is_rejected_and_line_only_corridor_still_fails(self):
        invalid_building = Polygon([(10, -2), (12, -1), (10, -1), (12, -2)])
        with self.assertRaisesRegex(ValueError, "building_footprints must be valid"):
            self.build(building_footprints=[invalid_building])
        with self.assertRaisesRegex(ValueError, "eligible_corridors.*polygonal"):
            self.build(eligible_corridors=LineString([(0, 4), (40, 4)]))

    def test_precision_grid_keeps_requested_raw_building_and_motor_clearance(self):
        for phase_m in (0.0001, 0.00049, 0.0009):
            with self.subTest(phase_m=phase_m):
                building = translate(
                    rotate(box(-1.5, -0.5, 1.5, 0.5), 45, origin=(0, 0)),
                    xoff=20 + phase_m, yoff=-1.5 + phase_m)
                motor = translate(
                    rotate(box(-8, -0.08, 8, 0.08), 45, origin=(0, 0)),
                    xoff=20 + phase_m, yoff=-0.1 + phase_m)
                layout = self.build(building_footprints=[building], motor_core=motor)

                self.assertGreaterEqual(layout["derived_walkbed"].distance(building), 0.15)
                self.assertGreaterEqual(layout["derived_walkbed"].distance(motor), 0.15)
                self.assertGreaterEqual(layout["stats"]["effective_building_clearance_m"], 0.153)
                self.assertGreaterEqual(layout["stats"]["effective_vehicle_clearance_m"], 0.153)


if __name__ == "__main__":
    unittest.main()
