"""Acceptance cannot mutate native state; integer time and rejection are exact."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.commands import Commands


class Vehicle:
    def getIDList(self):
        return ["v"]

    def getLoadedIDList(self):
        return ["v"]

    def setSpeed(self, *args):
        raise AssertionError("acceptance must not execute native commands")


def commands():
    c = SimpleNamespace(
        vehicle=Vehicle(),
        lane=SimpleNamespace(getIDList=lambda: ["lane"]),
        route=SimpleNamespace(getIDList=lambda: ["r"]),
        vehicletype=SimpleNamespace(getIDList=lambda: ["car"]),
    )
    return Commands(c, 100_000_000)


def submit(c, action, params, current=0, ident="test"):
    return c.submit({"command_id": ident, "action": action, "params": params}, current)


def test_acceptance_only_queues_and_id_is_never_reused():
    c = commands()
    assert submit(c, "set_speed", {"vehicle": "v", "speed": 3})["status"] == "accepted"
    assert not c.pending[0].applied
    assert submit(c, "set_speed", {"vehicle": "v", "speed": 3})["status"] == "rejected"


def test_integer_deadline_never_roundtrips_through_float_seconds():
    c = commands()
    current = 990000000123456789
    assert (
        submit(c, "set_speed", {"vehicle": "v", "speed": 0}, current)["status"]
        == "accepted"
    )
    assert c.pending[0].deadline == current + 30_000_000_000


def test_numeric_departure_decimal_nanoseconds_are_preserved():
    c = commands()
    assert (
        submit(
            c, "add_vehicle", {"vehicle": "new", "route_id": "r", "depart": 2.000000009}
        )["status"]
        == "accepted"
    )
    assert c.pending[0].deadline == 32_000_000_009


@pytest.mark.parametrize("speed", [None, True, -1, float("nan"), float("inf"), "3"])
def test_invalid_speeds_reject_without_queuing_or_defaulting(speed):
    c = commands()
    assert (
        submit(c, "set_speed", {"vehicle": "v", "speed": speed})["status"] == "rejected"
    )
    assert not c.pending


@pytest.mark.parametrize("depart", [None, True, float("nan"), float("inf"), -1])
def test_invalid_departures_are_typed_rejections(depart):
    c = commands()
    assert (
        submit(c, "add_vehicle", {"vehicle": "new", "route_id": "r", "depart": depart})[
            "status"
        ]
        == "rejected"
    )
    assert not c.pending


@pytest.mark.parametrize(
    "classes",
    [
        None,
        "passenger",
        [["passenger"]],
        [{}],
        [True],
        ["passenger", "passenger"],
        ["not-a-class"],
    ],
)
def test_invalid_restriction_classes_reject(classes):
    c = commands()
    assert (
        submit(c, "lane_restriction", {"lane": "lane", "disallowed": classes})["status"]
        == "rejected"
    )
    assert not c.pending


def test_scheduled_restriction_requires_exact_native_boundary():
    c = commands()
    assert (
        submit(
            c,
            "lane_restriction",
            {"lane": "lane", "disallowed": ["passenger"], "at_sim_ns": 1},
        )["status"]
        == "rejected"
    )


def test_missing_target_is_rejected_without_representing_it_as_active():
    c = commands()
    assert (
        submit(c, "set_speed", {"vehicle": "absent", "speed": 0})["status"]
        == "rejected"
    )
    assert not c.pending
