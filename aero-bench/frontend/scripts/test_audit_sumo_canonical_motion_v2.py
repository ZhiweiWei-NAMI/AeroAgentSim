"""Contract fixtures for v2. Synthetic rows here are never recording evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("audit-sumo-canonical-motion-v2.py")
AUDIT = runpy.run_path(str(SCRIPT))
BUILDER = runpy.run_path(str(Path(__file__).with_name("build-sumo-city-preview.py")))


class UnitProjection:
    def point(self, x, y):
        return round(x,2), round(-y,2)
    def heading(self, x, y, heading):
        return round(heading % 360,1)
    def metadata(self):
        return {"axes":"x-east,y-up,z-south-meters", "position_precision_m":.01,
                "heading_precision_deg":.1, "origin_wgs84":{"latitude_deg":31.2288,"longitude_deg":121.481,
                "ellipsoid_height_m":50.,"amsl_height_m":20.,"geoid_undulation_m":30.}}


def trace_fixture():
    context={"source_network_sha256":"network", "source_osm_sha256":"osm", "mesh_pack_source_sha256":"source"}
    basis={"policy":"canonical-rendered-building-footprints-and-effective-fixtures"}
    parameters={"duration_seconds":.25,"step_seconds":.25,"seed":24427,
                "bicycle_cycle_indices":[2,3,6,7,9,10],"excluded_walk_edges":[],"departure_offsets_seconds":{},
                "fleet_width_contract":AUDIT["fleet_width_contract"](), "authored_vehicle_counts":{"sedan":1,"bus":0,"bicycle":0},
                "display_requirements":{"minimum_displayed_vehicle_count":0,"minimum_displayed_person_count":1},
                "paved_bicycle_cycle_count":6, "route_requirements":{"require_multiple_motor_lane_edge":False,
                "require_bidirectional_motor_roads":False,"minimum_authored_person_routes":0}}
    inputs={"schema_version":AUDIT["INPUT_SCHEMA"],"source_context":context,"visual_obstacle_basis":basis,
            "recording_parameters":parameters,"expected_route_sha256":"route"}
    pins={"native_sha256":"native","native_size_bytes":123,"route_sha256":"route","recording_inputs_sha256":"inputs"}
    frames=[{"second":0.,"vehicles":[["v",10.,20.,0.,"sedan",0.,4.5,1.8]],"persons":[["p",0.,2.,90.,0.]],"tls":{"tl":"G"}},
            {"second":.25,"vehicles":[["v",10.,21.,0.,"sedan",0.,4.5,1.8]],"persons":[["p",1.,2.,90.,0.]],"tls":{"tl":"G"}}]
    native={"schema_version":AUDIT["NATIVE_SCHEMA"], "artifact_class":"offline-engineering-preview",
        "source_kind":"offline-sumo-engineering-preview", "source_context":context,"visual_obstacle_basis":basis,
        "source_network_sha256":"network","source_osm_sha256":"osm","mesh_pack_source_sha256":"source",
        "recording_inputs_sha256":"inputs","route_sha256":"route","sumo_image_id":AUDIT["SUMO_IMAGE"],
        "sumo_version":"Eclipse SUMO sumo 1.27.1", "vehicle_position_reference":"native-TraCI-front-bumper",
        "vehicle_fields":list(AUDIT["NATIVE_VEHICLE_FIELDS"]),"person_fields":list(AUDIT["NATIVE_PERSON_FIELDS"]),"signals":[],
        "step_seconds":.25,"duration_seconds":.25,"seed":24427,"paved_bicycle_cycle_indices":[2,3,6,7,9,10],
        "excluded_static_or_motor_crossing_walk_edges":[],"demand_departure_offsets_seconds":{},
        "vehicle_lane_use":{"v":["lane"]},"bicycle_lane_use":{},"person_route_edges":{"p":"edge"},
        "display_requirements":parameters["display_requirements"],"paved_bicycle_cycle_count":6,
        "demand":{"authored":parameters["authored_vehicle_counts"],"observed":{"vehicles":1,"persons":1,"sedan":1}},"frames":frames}
    public=deepcopy(native)
    public.update({"schema_version":AUDIT["PUBLIC_SCHEMA"], "vehicle_position_reference":"center-derived-from-native-TraCI-front-bumper-and-length",
        "vehicle_fields":list(AUDIT["PUBLIC_VEHICLE_FIELDS"]), "person_fields":list(AUDIT["PUBLIC_PERSON_FIELDS"]),
        "projection":UnitProjection().metadata(), "signal_pole_placement":{"policy":"native-curb-derived-source-placement-with-explicit-effective-omissions",
            "source_signal_count":0,"extra_relocated_count":0},
        "visual_vehicle_filter":{"policy":"native-sumo-whole-actor-routes-with-rendered-static-or-body-pair-contacts-omitted", "sumo_observed_vehicle_count":1,"displayed_vehicle_count":1,
            "excluded_static_overlap_ids":[],"excluded_pair_overlap_ids":[]},
        "native_motion_source":{"schema_version":AUDIT["NATIVE_SCHEMA"],"sha256":"native","size_bytes":123,"provider":"SUMO-TraCI",
            "sumo_image_id":AUDIT["SUMO_IMAGE"],"sumo_version":native["sumo_version"],"source_network_sha256":"network",
            "source_osm_sha256":"osm","route_sha256":"route","recording_inputs_sha256":"inputs",
            "seed":24427,"duration_seconds":.25,"step_seconds":.25}})
    for key in AUDIT["PRIVATE_KEYS"]:
        public.pop(key,None)
    public["frames"]=[{"second":0.,"vehicles":[["v",10.,-17.75,0.,"sedan",.075]],"persons":[["p",0.,-2.,90.,.225]],"tls":{"tl":"G"}},
                       {"second":.25,"vehicles":[["v",10.,-18.75,0.,"sedan",.075]],"persons":[["p",1.,-2.,90.,.225]],"tls":{"tl":"G"}}]
    return public,native,context,basis,inputs,pins


def workspace_bytes(*, seed: int = 24427, vehicles: int = 1,
                    pedestrians: int = 0, bicycles: int = 0) -> bytes:
    document = {
        "purpose": "scenario-authoring",
        "schema_version": "aero-bench.city-workspace/v3",
        "name": "Audit fixture",
        "scenePath": "/city-presentation/default-scene-v1.json",
        "seed": seed,
        "environment": {
            "cloudCover": 0, "precipitation": "none", "precipitationRateMmPerH": 0,
            "visibilityM": 10000, "windMps": 0, "windDirectionDeg": 0,
            "timeOfDay": "day", "reflectionsEnabled": True,
        },
        "fleet": [],
        "traffic": {"vehicles": vehicles, "pedestrians": pedestrians, "bicycles": bicycles},
        "facilities": [], "airspace": [],
        "algorithms": {
            "mode": "centralized", "assignment": "greedy", "routing": "astar",
            "energy": "reserve_threshold", "parameters": {},
        },
        "deployment": {"executor": "docker_reference", "imageRef": ""},
        "events": [], "actionRules": [], "stateKeyframes": [], "labelRules": [],
        "orders": [],
        "orderGeneration": {
            "seed": seed, "maxOrders": 0, "startAtS": 0, "endAtS": 3600,
            "cargoMinKg": 0.1, "cargoMaxKg": 1, "deadlineLeadS": 600,
        },
        "performanceProfiles": [], "authoredLandscape": [],
    }
    return json.dumps(document, separators=(",", ":")).encode()


class NativeTraceContractTest(unittest.TestCase):
    def setUp(self):
        self.public,self.native,self.context,self.basis,self.inputs,self.pins=trace_fixture()
    def check(self):
        return AUDIT["check_native_trace"](self.public,self.native,UnitProjection(),[],[],self.context,self.basis,self.inputs,self.pins)
    def test_small_explicit_trace_binds_native_center_heading_and_time(self):
        self.assertEqual(self.check()["samples_checked"],{"vehicles":2,"persons":2,"tls":2})
    def test_builder_maps_front_bumper_using_measured_length(self):
        payload=deepcopy(self.native)
        BUILDER["align_native_payload"](payload,UnitProjection())
        self.assertEqual(payload["frames"][0]["vehicles"][0][:5],["v",10.,-17.75,0.,"sedan"])
    def test_position_heading_or_type_drift_is_rejected(self):
        for column,new_value in [(1,10.01),(2,-17.74),(3,.1),(4,"taxi")]:
            with self.subTest(column=column):
                self.public=trace_fixture()[0]
                self.public["frames"][0]["vehicles"][0][column]=new_value
                with self.assertRaisesRegex(ValueError,"Public vehicles drift"):
                    self.check()
    def test_native_elevation_or_dimension_is_not_replaced_by_render_height(self):
        for column,value in [(5,.01),(6,0.),(7,float("nan"))]:
            with self.subTest(column=column):
                self.native=trace_fixture()[1]
                self.native["frames"][0]["vehicles"][0][column]=value
                with self.assertRaisesRegex(ValueError,"native TraCI vehicle"):
                    self.check()
    def test_origin_axis_unit_or_precision_drift_is_rejected(self):
        for key,value in [("axes","x-east,y-north,z-up-meters"),("position_precision_m",1.),
                          ("heading_precision_deg",1.),("origin_wgs84",{"ellipsoid_height_m":20.})]:
            with self.subTest(key=key):
                self.public=trace_fixture()[0]
                self.public["projection"][key]=value
                with self.assertRaisesRegex(ValueError,"origin/axes/units/heading"):
                    self.check()
    def test_missing_frame_or_actor_sample_is_rejected(self):
        for mutation in (lambda p:p["frames"].pop(), lambda p:p["frames"][0]["vehicles"].clear(),
                         lambda p:p["frames"][0]["persons"].clear()):
            self.public=trace_fixture()[0]
            mutation(self.public)
            with self.assertRaises(ValueError):
                self.check()
    def test_time_or_tls_rewriting_is_rejected(self):
        self.public["frames"][1]["second"]=.5
        with self.assertRaisesRegex(ValueError,"time drift"):
            self.check()
        self.public=trace_fixture()[0]
        self.public["frames"][0]["tls"]["tl"]="r"
        with self.assertRaisesRegex(ValueError,"actual TraCI"):
            self.check()
    def test_native_bytes_route_or_inputs_pin_drift_is_rejected(self):
        for key,value in [("sha256","changed"),("route_sha256","changed"),("recording_inputs_sha256","changed")]:
            self.public=trace_fixture()[0]
            self.public["native_motion_source"][key]=value
            with self.assertRaisesRegex(ValueError,"actual native bytes"):
                self.check()
    def test_native_backend_must_print_the_pinned_version_line(self):
        for value in ["Eclipse SUMO sumo 1.26.0","Eclipse SUMO sumo 1.27.1-dev","sumo 1.27.1"]:
            with self.subTest(value=value):
                self.public,self.native,self.context,self.basis,self.inputs,self.pins=trace_fixture()
                self.native["sumo_version"]=value
                with self.assertRaisesRegex(ValueError,"pinned SUMO backend/version"):
                    self.check()
    def test_private_state_or_formal_claim_in_public_is_rejected(self):
        self.public["vehicle_lane_use"]={"v":["lane"]}
        with self.assertRaisesRegex(ValueError,"private state"):
            self.check()
        self.public=trace_fixture()[0]
        self.public["artifact_class"]="sealed-formal-run"
        with self.assertRaisesRegex(ValueError,"offline engineering"):
            self.check()
    def test_current_context_and_new_obstacle_policy_are_required(self):
        self.public["source_context"]={**self.context,"source_network_sha256":"old"}
        with self.assertRaisesRegex(ValueError,"source context"):
            self.check()
        self.public=trace_fixture()[0]
        self.public["visual_obstacle_basis"]={"policy":"old-646-union"}
        with self.assertRaisesRegex(ValueError,"obstacle basis"):
            self.check()
    def test_observed_census_is_computed_from_native_rows(self):
        self.native["demand"]["observed"]["vehicles"]=55
        self.public["demand"]=deepcopy(self.native["demand"])
        with self.assertRaisesRegex(ValueError,"actual TraCI rows"):
            self.check()
    def test_materialized_fleet_widths_or_whole_actor_policy_drift_rejects(self):
        self.inputs["recording_parameters"]["fleet_width_contract"]["vehicles"][1]["native_width_m"]=1.85
        with self.assertRaisesRegex(ValueError,"fleet dimensions"):
            self.check()
        self.inputs=trace_fixture()[4]
        self.public["visual_vehicle_filter"]["policy"]="clip-and-rewrite-positions"
        with self.assertRaisesRegex(ValueError,"omission policy"):
            self.check()
    def test_arbitrary_static_omission_is_rejected_without_actual_contact(self):
        from shapely.geometry import box
        public,native,_,_,_,_=trace_fixture()
        public["visual_vehicle_filter"]["excluded_static_overlap_ids"]=["v"]
        with self.assertRaisesRegex(ValueError,"actual contact witnesses"):
            AUDIT["check_omission_witnesses"](public,native,UnitProjection(),[box(100,100,101,101)])
    def test_matching_native_and_public_frozen_tls_is_still_rejected_by_program(self):
        import xml.etree.ElementTree as ET
        network=ET.fromstring('<net><tlLogic id="tl" offset="0"><phase duration="10" state="G"/><phase duration="10" state="r"/></tlLogic></net>')
        traffic={"signals":[], "frames":[{"second":10.25,"tls":{"tl":"G"}}]}
        failures=[]
        result=AUDIT["check_signal_timing"](traffic,network,{"tl"},failures)
        self.assertEqual(result["state_at_wrong_time"],1)
        self.assertTrue(failures)
    def test_vehicle_floor_and_each_declared_displayed_family_are_required(self):
        traffic={"demand":{"authored":{name:1 for name in AUDIT["GROUND_FLEET"]}},
                 "display_requirements":{"minimum_displayed_vehicle_count":50,"minimum_displayed_person_count":1},
                 "frames":[{"vehicles":[[f"b{i}",0,0,0,"bicycle",0] for i in range(51)],"persons":[["p",0,0,0,0]]}]}
        failures=[]
        AUDIT["check_class_preservation"](traffic,failures)
        self.assertTrue(any("fleet types absent" in failure for failure in failures))
        traffic["frames"][0]["vehicles"]=[[name,0,0,0,name,0] for name in AUDIT["GROUND_FLEET"]]
        failures=[]
        AUDIT["check_class_preservation"](traffic,failures)
        self.assertTrue(any("fewer than 50" in failure for failure in failures))
    def test_legacy_preview_or_axis_fields_cannot_enter_v2_audit(self):
        self.public["schema_version"]="aero-bench.city-sumo-preview/v1"
        with self.assertRaisesRegex(ValueError,"Only explicit"):
            self.check()
        self.public=trace_fixture()[0]
        self.public["vehicle_fields"][1]="native_front_x_m"
        with self.assertRaisesRegex(ValueError,"center/axis/unit"):
            self.check()
    def test_zero_requested_types_are_not_required_by_generic_audit(self):
        traffic={"demand":{"authored":{"sedan":1,"bus":0,"bicycle":0}},
            "display_requirements":{"minimum_displayed_vehicle_count":1,"minimum_displayed_person_count":0},
            "frames":[{"vehicles":[["v",0,0,0,"sedan",0]],"persons":[]}]}
        failures=[]
        result=AUDIT["check_class_preservation"](traffic,failures)
        self.assertEqual(failures,[])
        self.assertEqual(result["configured_active_vehicle_types"],["sedan"])
        traffic["demand"]["authored"]["bus"]=1
        AUDIT["check_class_preservation"](traffic,failures)
        self.assertTrue(any("bus" in failure for failure in failures))
    def test_off_surface_gap_is_measured_to_actual_nearest_geometry(self):
        from shapely.geometry import box
        traffic={"frames":[{"vehicles":[["v",5,5,0,"sedan",0]],"persons":[]}]}
        failures=[]
        result=AUDIT["check_surface_coverage"](traffic,[box(0,0,1,1)],failures)
        self.assertEqual(result["max_vehicle_distance_m"],5.6569)
        self.assertEqual(result["vehicle_off_surface"],1)
        self.assertTrue(failures)
    def test_private_recording_output_is_separate_and_restricts_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            public=Path(tmp)/"public/trace.json"
            private=BUILDER["private_recording_directory"](Path(tmp)/"private",public)
            self.assertEqual(private.stat().st_mode & 0o777,0o700)
            with self.assertRaisesRegex(ValueError,"separate private"):
                BUILDER["private_recording_directory"](public.parent,public)
            private.chmod(0o755)
            with self.assertRaisesRegex(ValueError,"other users"):
                BUILDER["private_recording_directory"](private,public)

    def test_workspace_demand_claim_is_bound_to_exact_validated_bytes(self):
        raw = workspace_bytes()
        source = {
            "schema_version": "aero-bench.city-traffic-preview-demand/v1",
            "workspace_schema_version": "aero-bench.city-workspace/v3",
            "workspace_sha256": hashlib.sha256(raw).hexdigest(),
            "workspace_size_bytes": len(raw),
            "seed": 24427,
            "traffic": {"vehicles": 1, "pedestrians": 0, "bicycles": 0},
        }
        self.inputs["recording_parameters"]["authoring_source"] = source
        for document in (self.native, self.public):
            document["demand_authoring"] = source
            document["demand"]["persons"] = 0
        self.assertEqual(
            AUDIT["check_workspace_demand_authoring"](
                self.public, self.native, self.inputs, raw,
            ),
            source,
        )
        self.public["demand_authoring"] = {**source, "workspace_sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "differs from the exact workspace"):
            AUDIT["check_workspace_demand_authoring"](
                self.public, self.native, self.inputs, raw,
            )

    def test_workspace_claim_without_workspace_input_is_rejected(self):
        self.assertIsNone(AUDIT["check_workspace_demand_authoring"](
            self.public, self.native, self.inputs, None,
        ))
        self.public["demand_authoring"] = {"schema_version": "invented"}
        with self.assertRaisesRegex(ValueError, "explicit workspace audit input"):
            AUDIT["check_workspace_demand_authoring"](
                self.public, self.native, self.inputs, None,
            )


if __name__ == "__main__":
    unittest.main()
