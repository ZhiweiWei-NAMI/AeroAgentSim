"""Reject road PASS labels that contradict their actual native/model geometry."""
from copy import deepcopy
import unittest
import xml.etree.ElementTree as ET
from shapely.geometry import box
from city_motion_obstacles import native_road_geometry_gate, validate_recording_road
from city_surface_identity import displayed_surface_sha256


def part(polygon):
    # The road builder serializes open rings (no repeated closing vertex).
    return {"outline":[list(point) for point in polygon.exterior.coords][:-1],"holes":[]}


def road_fixture():
    road={"schema_version":"aero-bench.city-road-preview/v3","source_context":{"origin":"unit"},
        "roadbed":[part(box(-2,-2,12,2))],"walkbed":[part(box(-2,4,12,7))],
        "physical_clearance":{"status":"PASS", "native_lane_clearance":{"status":"PASS",
            "policy":"actual-rendered-building-footprints-and-native-lane-ribbons","pair_count":0,
            "contact_area_sum_per_lane_m2":0.,"rows":[],"unrenderable_generated_areas":[],"numerical_area_epsilon_m2":1e-8},
            "native_lane_surface_coverage":{"status":"PASS","rows":[],"numerical_area_epsilon_m2":1e-8,
                "surface_seam_tolerance_m":.003}}}
    road["displayed_surface_sha256"]=displayed_surface_sha256(road["roadbed"],road["walkbed"])
    return road


class IdentityProjection:
    def shape(self,value):
        return [list(map(float,point.split(","))) for point in value.split()]


class NativeRoadGateTest(unittest.TestCase):
    def test_complete_gate_and_same_surface_identity_pass(self):
        road=road_fixture()
        self.assertEqual(validate_recording_road(road,{"origin":"unit"}),road["displayed_surface_sha256"])
    def test_top_level_pass_does_not_override_blocked_or_missing_nested_gate(self):
        for key in ("native_lane_clearance","native_lane_surface_coverage"):
            with self.subTest(key=key):
                road=road_fixture();road["physical_clearance"][key]["status"]="BLOCKED"
                with self.assertRaisesRegex(ValueError,"complete native/model"):
                    validate_recording_road(road,{"origin":"unit"})
                road["physical_clearance"][key]={}
                with self.assertRaises(ValueError):validate_recording_road(road,{"origin":"unit"})
    def test_context_surface_count_or_tolerance_drift_rejects(self):
        for mutation in (lambda r:r.update(source_context={}),lambda r:r.update(displayed_surface_sha256="changed"),
                         lambda r:r["physical_clearance"]["native_lane_clearance"].update(pair_count=1),
                         lambda r:r["physical_clearance"]["native_lane_surface_coverage"].update(surface_seam_tolerance_m=.5)):
            road=road_fixture();mutation(road)
            with self.assertRaises(ValueError):validate_recording_road(road,{"origin":"unit"})
    def test_actual_ribbon_contact_rejects_even_when_road_labels_say_pass(self):
        network=ET.fromstring('<net><edge id="e"><lane id="e_0" width="2" shape="0,0 10,0"/></edge></net>')
        proof=native_road_geometry_gate(network,IdentityProjection(),IdentityProjection(),[box(4,.5,5,3)],road_fixture())
        self.assertEqual(proof["status"],"BLOCKED")
        self.assertGreater(proof["model_contacts"][0]["area_m2"],0)
    def test_clipped_paving_cannot_hide_real_native_lane(self):
        network=ET.fromstring('<net><edge id="e"><lane id="e_0" width="2" shape="0,0 10,0"/></edge></net>')
        road=road_fixture();road["roadbed"]=[part(box(0,-1,4,1))]
        proof=native_road_geometry_gate(network,IdentityProjection(),IdentityProjection(),[box(20,20,21,21)],road)
        self.assertEqual(proof["status"],"BLOCKED")
        self.assertGreater(proof["missing_surface_ribbons"][0]["missing_area_m2"],0)
    def test_actual_valid_native_ribbon_and_paving_pass(self):
        network=ET.fromstring('<net><edge id="e"><lane id="e_0" width="2" shape="0,0 10,0"/></edge></net>')
        proof=native_road_geometry_gate(network,IdentityProjection(),IdentityProjection(),[box(20,20,21,21)],road_fixture())
        self.assertEqual(proof["status"],"PASS")
        self.assertEqual(proof["native_ribbons_checked"],1)
    def test_internal_lane_is_checked_as_aligned_to_its_adjoining_lane_ends(self):
        # The internal lane turns 11 degrees between two closed lanes; paving only its aligned
        # ribbon passes, while a flat cap would reach across both adjoining lane end lines.
        from city_road_physical_clearance import lane_ribbon
        network=ET.fromstring('<net><edge id="a"><lane id="a_0" width="2" disallow="all" shape="-5,0 0,0"/></edge>'
            '<edge id=":j_0" function="internal"><lane id=":j_0_0" width="2" shape="0,0 5,1"/></edge>'
            '<edge id="b"><lane id="b_0" width="2" disallow="all" shape="5,1 10,1"/></edge>'
            '<connection from="a" to="b" fromLane="0" toLane="0" via=":j_0_0"/>'
            '<connection from=":j_0" to="b" fromLane="0" toLane="0"/></net>')
        road=road_fixture();road["roadbed"]=[part(lane_ribbon([[0,0],[5,1]],2,[[-5,0],[0,0]],[[5,1],[10,1]]))]
        proof=native_road_geometry_gate(network,IdentityProjection(),IdentityProjection(),[box(20,20,21,21)],road)
        self.assertEqual((proof["status"],proof["native_ribbons_checked"]),("PASS",1))
        road["roadbed"]=[part(piece) for piece in lane_ribbon([[0,0],[5,1]],2,[[-5,0],[0,0]],[[5,1],[10,1]]).difference(box(2,-2,2.5,3)).geoms]
        proof=native_road_geometry_gate(network,IdentityProjection(),IdentityProjection(),[box(20,20,21,21)],road)
        self.assertEqual(proof["status"],"BLOCKED")
    def test_road_parts_use_the_builder_open_ring_contract_without_repair(self):
        from city_motion_obstacles import road_surface_polygon
        self.assertEqual(road_surface_polygon(part(box(0,0,2,3))).area,6)
        closed={"outline":[list(point) for point in box(0,0,2,3).exterior.coords],"holes":[]}
        with self.assertRaisesRegex(ValueError,"open outer and hole rings"):road_surface_polygon(closed)
        with self.assertRaisesRegex(ValueError,"repair is not permitted"):
            road_surface_polygon({"outline":[[0,0],[2,2],[2,0],[0,2]],"holes":[]})

if __name__ == "__main__":
    unittest.main()
