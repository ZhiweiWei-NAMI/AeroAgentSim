from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from aero_bench.authoring.workspace import (
    CityAuthoredLandscape, CityOrderGeneration, CityOrderRequest,
    CityPerformanceProfile, CityWorkspaceDraft, CityWorkspaceDraftV2,
    CityWorkspaceDraftV3, WORKSPACE_V3_SCHEMA,
    migrate_workspace_v2, migrate_workspace_v2_payload,
)
from aero_bench.providers.rpc import ProviderRpcError


def workspace_v2() -> dict:
    return {
        "purpose": "scenario-authoring",
        "schema_version": "aero-bench.city-workspace/v2",
        "name": "Shanghai Huangpu draft",
        "scenePath": "/city-presentation/default-scene-v1.json",
        "seed": 1,
        "environment": {
            "cloudCover": 0, "precipitation": "none", "precipitationRateMmPerH": 0,
            "visibilityM": 10000, "windMps": 0, "windDirectionDeg": 0,
            "timeOfDay": "day", "reflectionsEnabled": True,
        },
        "fleet": [{
            "id": "uav.01", "assetId": "model:holybro-x500", "count": 1,
            "homeFacilityId": None, "batteryWh": 500, "reserveRatio": 0.2,
        }],
        "traffic": {"vehicles": 0, "pedestrians": 0, "bicycles": 0},
        "facilities": [], "airspace": [],
        "algorithms": {
            "mode": "centralized", "assignment": "greedy", "routing": "astar",
            "energy": "reserve_threshold", "parameters": {},
        },
        "deployment": {"executor": "docker_reference", "imageRef": ""},
        "events": [], "actionRules": [], "stateKeyframes": [], "labelRules": [],
    }


def workspace() -> dict:
    return migrate_workspace_v2_payload(workspace_v2())


def zone() -> dict:
    return {
        "id": "zone.1", "name": "Airspace", "polygon": [
            {"x": 0, "z": 0}, {"x": 10, "z": 0}, {"x": 0, "z": 10},
        ], "floorM": 0, "ceilingM": 20, "startsAtS": 0, "endsAtS": None,
        "source": {"kind": "manual", "label": "Authored"},
    }


def facility(facility_id: str, kind: str) -> dict:
    return {
        "id": facility_id, "name": facility_id, "kind": kind,
        "position": {"x": 0, "z": 0}, "rotationDeg": 0,
        "widthM": 10, "depthM": 10, "heightM": 2,
        "capacity": 1, "chargingPowerW": 0,
    }


def order(**changes) -> dict:
    result = {
        "id": "order.1", "sourceFacilityId": "facility.source",
        "destinationFacilityId": "facility.destination",
        "hubHandoffFacilityId": "facility.hub", "cargoKg": 2.5,
        "releaseAtS": 10, "deliverByS": 70,
    }
    result.update(changes)
    return result


def performance_profile(**changes) -> dict:
    result = {
        "fleetEntryId": "uav.01", "sourceLabel": "operator",
        "provenance": "authored estimate",
        "aircraftBody": {"xM": 0.5, "yM": 0.3, "zM": 0.5},
        "cruiseSpeedMps": 12, "cruisePowerW": 420,
        "hoverPowerW": 500, "chargeEfficiency": 0.9,
    }
    result.update(changes)
    return result


def workspace_v3() -> dict:
    raw = workspace()
    raw["facilities"] = [
        facility("facility.source", "vertiport"),
        facility("facility.destination", "vertiport"),
        facility("facility.hub", "hub"),
        facility("facility.charger", "charger"),
    ]
    raw["orders"] = [order()]
    raw["performanceProfiles"] = [performance_profile()]
    raw["authoredLandscape"] = [{
        "id": "green.1", "label": "Courtyard", "provenance": "authored",
        "kind": "green", "polygon": [
            {"x": 0, "z": 0}, {"x": 5, "z": 0}, {"x": 0, "z": 5},
        ],
    }]
    return raw


def test_current_browser_shape_roundtrips_without_adding_fields():
    raw = workspace()
    raw["airspace"] = [zone()]
    draft = CityWorkspaceDraft.from_json_bytes(json.dumps(raw).encode())
    assert draft.snapshot() == raw
    assert "uri" not in draft.snapshot()["airspace"][0]["source"]


def test_explicit_v2_model_builds_the_disabled_v3_migration_payload():
    raw = workspace_v2()
    raw["seed"] = 71
    raw["airspace"] = [zone()]
    v2 = CityWorkspaceDraftV2.model_validate(raw)
    assert v2.snapshot() == raw

    migrated = migrate_workspace_v2_payload(v2)
    assert migrated["schema_version"] == WORKSPACE_V3_SCHEMA
    assert migrated["seed"] == raw["seed"]
    for field in raw.keys() - {"schema_version"}:
        assert migrated[field] == raw[field]
    assert migrated["orders"] == []
    assert migrated["performanceProfiles"] == []
    assert migrated["authoredLandscape"] == []
    assert migrated["orderGeneration"] == {
        "seed": 71,
        "maxOrders": 0,
        "startAtS": 0,
        "endAtS": 3600,
        "cargoMinKg": 0.1,
        "cargoMaxKg": 1,
        "deadlineLeadS": 600,
    }
    assert migrate_workspace_v2(v2).snapshot() == migrated


def test_v2_migration_path_remains_strict_and_separate_from_active_parser():
    raw = workspace_v2()
    raw["orders"] = []
    with pytest.raises(ValidationError, match="Extra inputs"):
        migrate_workspace_v2_payload(raw)
    raw = workspace_v2()
    raw["schema_version"] = WORKSPACE_V3_SCHEMA
    with pytest.raises(ValidationError, match="schema_version"):
        CityWorkspaceDraftV2.model_validate(raw)


def test_complete_v3_model_is_the_active_contract_and_rejects_v2():
    active_schema = CityWorkspaceDraft.model_json_schema()
    v3_schema = CityWorkspaceDraftV3.model_json_schema()
    assert active_schema["properties"]["schema_version"]["const"] == WORKSPACE_V3_SCHEMA
    assert v3_schema["properties"]["schema_version"]["const"] == WORKSPACE_V3_SCHEMA
    for field in ("orders", "orderGeneration", "performanceProfiles", "authoredLandscape"):
        assert field in active_schema["properties"]
        assert field in v3_schema["properties"]

    raw = workspace_v3()
    assert CityWorkspaceDraftV3.model_validate(raw).snapshot() == raw
    assert CityWorkspaceDraft.model_validate(raw).snapshot() == raw
    with pytest.raises(ValidationError, match="schema_version|Field required"):
        CityWorkspaceDraft.model_validate(workspace_v2())


def test_v3_requires_every_new_field():
    raw = workspace_v3()
    for field in ("orders", "orderGeneration", "performanceProfiles", "authoredLandscape"):
        incomplete = copy.deepcopy(raw)
        del incomplete[field]
        with pytest.raises(ValidationError, match=field):
            CityWorkspaceDraftV3.model_validate(incomplete)


@pytest.mark.parametrize("changes,match", [
    ({"sourceFacilityId": "facility.missing"}, "unknown source"),
    ({"destinationFacilityId": "facility.missing"}, "unknown destination"),
    ({"sourceFacilityId": "facility.charger"}, "without cargo transfer"),
    ({"hubHandoffFacilityId": "facility.destination"}, "differ from its endpoints"),
    ({"hubHandoffFacilityId": "facility.charger"}, "logistics hub"),
    ({"hubHandoffFacilityId": "facility.missing"}, "unknown logistics hub"),
    ({"hubHandoffFacilityId": None}, "explicit hub handoff"),
])
def test_v3_order_references_are_bound_to_known_facilities(changes, match):
    raw = workspace_v3()
    raw["orders"] = [order(**changes)]
    with pytest.raises(ValidationError, match=match):
        CityWorkspaceDraftV3.model_validate(raw)


def test_v3_accepts_null_handoff_only_when_an_endpoint_is_the_hub():
    raw = workspace_v3()
    raw["orders"] = [order(
        sourceFacilityId="facility.hub", hubHandoffFacilityId=None,
    )]
    assert CityWorkspaceDraftV3.model_validate(raw).snapshot() == raw


def test_v3_order_validation_does_not_invent_missing_capacity_or_payload():
    raw = workspace_v3()
    raw["orders"] = [order(cargoKg=1_000_000)]
    assert CityWorkspaceDraftV3.model_validate(raw).orders[0].cargoKg == 1_000_000


def test_v3_order_profile_and_landscape_keys_are_unique_and_bound():
    raw = workspace_v3()
    raw["orders"].append(copy.deepcopy(raw["orders"][0]))
    with pytest.raises(ValidationError, match="orders contains duplicate"):
        CityWorkspaceDraftV3.model_validate(raw)
    raw = workspace_v3()
    raw["performanceProfiles"].append(copy.deepcopy(raw["performanceProfiles"][0]))
    with pytest.raises(ValidationError, match="performanceProfiles contains duplicate"):
        CityWorkspaceDraftV3.model_validate(raw)
    raw = workspace_v3()
    raw["performanceProfiles"][0]["fleetEntryId"] = "uav.missing"
    with pytest.raises(ValidationError, match="unknown fleet entry"):
        CityWorkspaceDraftV3.model_validate(raw)
    raw = workspace_v3()
    raw["authoredLandscape"].append(copy.deepcopy(raw["authoredLandscape"][0]))
    with pytest.raises(ValidationError, match="authoredLandscape contains duplicate"):
        CityWorkspaceDraftV3.model_validate(raw)


def test_v3_order_component_uses_exact_camel_case_domain_shape():
    raw = order()
    assert CityOrderRequest.model_validate(raw).model_dump(mode="json") == raw
    for field, value in (
        ("sourceFacilityId", "bad facility"), ("cargoKg", 0),
        ("releaseAtS", -1), ("deliverByS", 10),
    ):
        invalid = {**raw, field: value}
        if field == "deliverByS":
            invalid["releaseAtS"] = 10
        with pytest.raises(ValidationError):
            CityOrderRequest.model_validate(invalid)


def test_v3_order_generation_component_matches_current_bounds_and_defaults():
    expected = {
        "seed": 17, "maxOrders": 0, "startAtS": 0, "endAtS": 3600,
        "cargoMinKg": 0.1, "cargoMaxKg": 1, "deadlineLeadS": 600,
    }
    assert CityOrderGeneration.disabled(17).model_dump(mode="json") == expected
    for mutation in (
        {"maxOrders": 10_001}, {"seed": True}, {"endAtS": 0},
        {"cargoMaxKg": 0.05}, {"deadlineLeadS": 0},
    ):
        with pytest.raises(ValidationError):
            CityOrderGeneration.model_validate({**expected, **mutation})


def test_v3_performance_profile_component_preserves_declared_estimates():
    raw = performance_profile()
    assert CityPerformanceProfile.model_validate(raw).model_dump(mode="json") == raw
    with pytest.raises(ValidationError):
        CityPerformanceProfile.model_validate({**raw, "chargeEfficiency": 0})
    with pytest.raises(ValidationError):
        CityPerformanceProfile.model_validate({**raw, "aircraftBody": {
            **raw["aircraftBody"], "xM": float("nan"),
        }})


def test_v3_authored_landscape_component_validates_declared_geometry():
    raw = {
        "id": "green.1", "label": "Courtyard", "provenance": "authored",
        "kind": "green", "polygon": [
            {"x": 0, "z": 0}, {"x": 5, "z": 0}, {"x": 0, "z": 5},
        ],
    }
    assert CityAuthoredLandscape.model_validate(raw).model_dump(mode="json") == raw
    for mutation in (
        {"label": "  "}, {"provenance": "osm"}, {"kind": "water"},
        {"polygon": [
            {"x": 0, "z": 0}, {"x": 2, "z": 2},
            {"x": 0, "z": 2}, {"x": 2, "z": 0},
        ]},
    ):
        with pytest.raises(ValidationError):
            CityAuthoredLandscape.model_validate({**raw, **mutation})


def test_every_top_level_field_is_required():
    raw = workspace()
    for name in raw:
        incomplete = copy.deepcopy(raw)
        del incomplete[name]
        with pytest.raises(ValidationError):
            CityWorkspaceDraft.model_validate(incomplete)


@pytest.mark.parametrize("value", [True, "1", 0.5, -1, 2**53, float("inf")])
def test_invalid_seed(value):
    raw = workspace()
    raw["seed"] = value
    with pytest.raises(ValidationError):
        CityWorkspaceDraft.model_validate(raw)


def test_javascript_integral_number_is_accepted_as_safe_integer():
    raw = workspace()
    raw["seed"] = 3.0
    raw["fleet"][0]["count"] = 2.0
    draft = CityWorkspaceDraft.model_validate(raw)
    assert draft.seed == 3
    assert draft.fleet[0].count == 2


@pytest.mark.parametrize("field", [
    "cloudCover", "precipitationRateMmPerH", "visibilityM", "windMps", "windDirectionDeg",
])
@pytest.mark.parametrize("value", [True, "2", float("nan"), float("inf")])
def test_weather_rejects_scalar_coercion_and_nonfinite_numbers(field, value):
    raw = workspace()
    raw["environment"][field] = value
    with pytest.raises(ValidationError):
        CityWorkspaceDraft.model_validate(raw)


@pytest.mark.parametrize("field,value", [
    ("cloudCover", 1.01), ("visibilityM", 0), ("windMps", -1),
    ("windDirectionDeg", 360), ("timeOfDay", "dusk"), ("reflectionsEnabled", 1),
])
def test_current_environment_bounds(field, value):
    raw = workspace()
    raw["environment"][field] = value
    with pytest.raises(ValidationError):
        CityWorkspaceDraft.model_validate(raw)


def test_precipitation_rate_consistency():
    raw = workspace()
    raw["environment"]["precipitationRateMmPerH"] = 2
    with pytest.raises(ValidationError, match="zero precipitation rate"):
        CityWorkspaceDraft.model_validate(raw)
    raw["environment"]["precipitation"] = "rain"
    assert CityWorkspaceDraft.model_validate(raw).environment.precipitation == "rain"


@pytest.mark.parametrize("mutation", [
    {"schema_version": "aero-bench.city-workspace/v1"},
    {"schema_version": "aero-bench.city-workspace/v2"},
    {"purpose": "verified-replay"}, {"origin": {"latitude": 31}},
    {"scenePath": "/city-presentation/../secret.json"},
])
def test_no_legacy_or_unknown_authority_fields(mutation):
    raw = workspace()
    raw.update(mutation)
    with pytest.raises(ValidationError):
        CityWorkspaceDraft.model_validate(raw)


def test_workspace_fleet_cannot_be_reinterpreted_as_selected_fleet():
    raw = workspace()
    raw["fleet"][0]["maxPayloadKg"] = 5
    with pytest.raises(ValidationError, match="Extra inputs"):
        CityWorkspaceDraft.model_validate(raw)


def test_duplicate_and_missing_home_facility_rejected():
    raw = workspace()
    raw["fleet"].append(copy.deepcopy(raw["fleet"][0]))
    with pytest.raises(ValidationError, match="duplicate IDs"):
        CityWorkspaceDraft.model_validate(raw)
    raw["fleet"].pop()
    raw["fleet"][0]["homeFacilityId"] = "missing"
    with pytest.raises(ValidationError, match="unknown home facility"):
        CityWorkspaceDraft.model_validate(raw)


@pytest.mark.parametrize("kind", ["airspace.activated", "charger.outage"])
def test_event_target_must_exist(kind):
    raw = workspace()
    raw["events"] = [{"id": "event.1", "atS": 0, "type": kind,
                      "targetId": "missing", "payload": {}}]
    with pytest.raises(ValidationError, match="unknown"):
        CityWorkspaceDraft.model_validate(raw)


def test_closed_airspace_ring_is_preserved_as_authored():
    raw = workspace()
    raw["airspace"] = [zone()]
    raw["airspace"][0]["polygon"].append({"x": 0, "z": 0})
    assert CityWorkspaceDraft.model_validate(raw).snapshot() == raw


@pytest.mark.parametrize("mutation", [
    {"ceilingM": 0}, {"startsAtS": 2, "endsAtS": 2},
    {"polygon": [{"x": 0, "z": 0}, {"x": 1, "z": 0}, {"x": 2, "z": 0}]},
    {"polygon": [{"x": 0, "z": 0}, {"x": 2, "z": 2},
                 {"x": 0, "z": 2}, {"x": 2, "z": 0}]},
    {"source": {"kind": "manual", "label": "Authored", "uri": None}},
])
def test_invalid_airspace(mutation):
    raw = workspace()
    raw["airspace"] = [{**zone(), **mutation}]
    with pytest.raises(ValidationError):
        CityWorkspaceDraft.model_validate(raw)


@pytest.mark.parametrize("payload", [
    {"nested": [float("inf")]}, {"nested": {"a": float("nan")}}, {"a": {1: "x"}},
])
def test_arbitrary_event_payload_is_still_strict_finite_json(payload):
    raw = workspace()
    raw["events"] = [{"id": "event.1", "atS": 0, "type": "weather.changed",
                      "targetId": "", "payload": payload}]
    with pytest.raises(ValidationError):
        CityWorkspaceDraft.model_validate(raw)


@pytest.mark.parametrize("raw", [b'{"seed":1,"seed":2}', b'{"seed":NaN}', b'[]'])
def test_wire_rejects_duplicate_keys_nonfinite_and_nonobject(raw):
    with pytest.raises(ProviderRpcError):
        CityWorkspaceDraft.from_json_bytes(raw)
