"""Restriction mechanics use test doubles here; formal evidence requires native runs."""

import copy
from types import SimpleNamespace

import pytest

from aero_bench.providers.sumo.config import SumoTrafficRestriction
from aero_bench.providers.sumo.restriction_evidence import (
    SumoRestrictionApplication,
    validate_restriction_application,
)
from containers.sumo.service import SumoService, SumoServiceError, _parse_restrictions


def application():
    return {
        "schema_version": "sumo.traffic.restricted.v2",
        "request": {
            "event_id": "restrict.1",
            "at_tick": 2,
            "edge_id": "A1A2",
            "disallowed_classes": ["passenger"],
        },
        "native_sim_time_ns": 1_000_000_000,
        "permissions": [
            {
                "lane_id": "A1A2_0",
                "before_disallowed": ["bus"],
                "requested_disallowed": ["bus", "passenger"],
                "after_disallowed": ["bus", "passenger"],
                "traci_acknowledged": True,
            }
        ],
        "route_effects": [
            {
                "vehicle_id": "veh.01",
                "vehicle_class": "passenger",
                "lifecycle": "active",
                "before_route": ["A0A1", "A1A2", "B0A0"],
                "before_route_index": 0,
                "before_road_id": "A0A1",
                "after_route": ["A0A1", "A1B1", "B0A0"],
                "after_route_index": 0,
                "after_road_id": "A0A1",
                "traci_acknowledged": True,
            }
        ],
    }


def validate(raw, *, require_route_effect=True):
    app = SumoRestrictionApplication.model_validate(raw)
    validate_restriction_application(
        app,
        expected=SumoTrafficRestriction.model_validate(application()["request"]),
        step_ns=500_000_000,
        require_route_effect=require_route_effect,
    )


def test_permission_and_route_witness_require_real_changes():
    validate(application())


@pytest.mark.parametrize(
    "mutation",
    [
        "no-ack",
        "wrong-tick",
        "wrong-request",
        "lost-exclusion",
        "no-permission-effect",
        "no-route-effect",
        "unchanged-route",
        "restricted-route",
        "changed-destination",
        "wrong-class",
        "moving-road",
        "invalid-index",
        "duplicate-lane",
        "duplicate-vehicle",
    ],
)
def test_restriction_witness_rejects_invalid_application(mutation):
    raw = application()
    lane = raw["permissions"][0]
    vehicle = raw["route_effects"][0]
    if mutation == "no-ack":
        lane["traci_acknowledged"] = False
    elif mutation == "wrong-tick":
        raw["native_sim_time_ns"] += 1
    elif mutation == "wrong-request":
        raw["request"]["at_tick"] += 1
    elif mutation == "lost-exclusion":
        lane["after_disallowed"] = ["passenger"]
    elif mutation == "no-permission-effect":
        lane["before_disallowed"] = ["bus", "passenger"]
    elif mutation == "no-route-effect":
        raw["route_effects"] = []
    elif mutation == "unchanged-route":
        vehicle["after_route"] = vehicle["before_route"]
    elif mutation == "restricted-route":
        vehicle["after_route"][1] = "A1A2"
    elif mutation == "changed-destination":
        vehicle["after_route"][-1] = "B1B0"
    elif mutation == "wrong-class":
        vehicle["vehicle_class"] = "bus"
    elif mutation == "moving-road":
        vehicle["after_road_id"] = "A1B1"
    elif mutation == "invalid-index":
        vehicle["after_route_index"] = 3
    elif mutation == "duplicate-lane":
        raw["permissions"].append(copy.deepcopy(lane))
    elif mutation == "duplicate-vehicle":
        raw["route_effects"].append(copy.deepcopy(vehicle))
    with pytest.raises(ValueError):
        validate(raw)


def test_adapter_accepts_a_permission_only_receipt_but_verifier_rejects_missing_route_effect():
    raw = application()
    raw["route_effects"] = []
    validate(raw, require_route_effect=False)
    with pytest.raises(ValueError, match="affected active vehicle route"):
        validate(raw)


def test_pending_native_route_effect_is_explicit_and_does_not_replace_an_active_effect():
    raw = application()
    pending = raw["route_effects"][0]
    pending.update(
        lifecycle="pending",
        before_route_index=-1073741824,
        after_route_index=-1073741824,
        before_road_id="",
        after_road_id="",
    )
    validate(raw, require_route_effect=False)
    with pytest.raises(ValueError, match="affected active vehicle route"):
        validate(raw)
    pending["before_route_index"] = -1
    with pytest.raises(ValueError, match="native invalid indices"):
        validate(raw, require_route_effect=False)


@pytest.mark.parametrize(
    "change",
    [
        {"at_tick": True},
        {"at_tick": 0},
        {"at_tick": 11},
        {"edge_id": ":internal"},
        {"disallowed_classes": ["bus"]},
        {"unexpected": True},
    ],
)
def test_service_schedule_parser_rejects_invalid_request(change):
    with pytest.raises(SumoServiceError):
        _parse_restrictions([{**application()["request"], **change}], 10)


def test_service_applies_once_at_native_tick_and_preserves_existing_permissions():
    request = application()["request"]
    permissions = {"A1A2_0": ["bus"]}
    route = ["A0A1", "A1A2", "B0A0"]
    calls = []

    def set_permissions(lane, classes):
        calls.append(("permission", lane, list(classes)))
        permissions[lane] = list(classes)

    def reroute(vehicle, *, currentTravelTimes):
        calls.append(("reroute", vehicle, currentTravelTimes))
        route[:] = ["A0A1", "A1B1", "B0A0"]

    connection = SimpleNamespace(
        simulation=SimpleNamespace(getTime=lambda: 1),
        edge=SimpleNamespace(getIDList=lambda: ["A1A2"]),
        lane=SimpleNamespace(
            getIDList=lambda: ["A1A2_0"],
            getEdgeID=lambda _: "A1A2",
            getDisallowed=lambda lane: permissions[lane],
            setDisallowed=set_permissions,
        ),
        vehicle=SimpleNamespace(
            getIDList=lambda: ["veh.01"],
            getVehicleClass=lambda _: "passenger",
            getRoute=lambda _: tuple(route),
            getRouteIndex=lambda _: 0,
            getRoadID=lambda _: "A0A1",
            rerouteTraveltime=reroute,
        ),
    )
    config = SimpleNamespace(
        restrictions=(request,),
        object_bindings=(
            {"kind": "vehicle", "sumo_object_id": "veh.01", "entity_id": "vehicle.01"},
        ),
    )
    service = object.__new__(SumoService)
    service._applied_restrictions = set()
    service._entity_lifecycle = {"vehicle.01": "active"}
    assert service._apply_due_restrictions(config, connection, (1, 500_000_000)) == []
    assert not calls
    result = service._apply_due_restrictions(config, connection, (2, 1_000_000_000))
    validate(result[0])
    assert calls == [
        ("permission", "A1A2_0", ["bus", "passenger"]),
        ("reroute", "veh.01", False),
    ]
    with pytest.raises(SumoServiceError, match="more than once"):
        service._apply_due_restrictions(config, connection, (2, 1_000_000_000))
    assert len(calls) == 2


def test_service_rejects_application_outside_native_tick():
    service = object.__new__(SumoService)
    service._applied_restrictions = set()
    config = SimpleNamespace(restrictions=(application()["request"],))
    connection = SimpleNamespace(simulation=SimpleNamespace(getTime=lambda: 0.5))
    with pytest.raises(SumoServiceError, match="native barrier"):
        service._apply_due_restrictions(config, connection, (2, 1_000_000_000))
