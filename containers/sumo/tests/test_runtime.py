"""Barrier invariants: observe each native step; never overshoot or mutate holds."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.runtime import Runtime


class Commands:
    def __init__(self):
        self.applied = []
        self.observed = []

    def before_step(self, at):
        self.applied.append(at)
        return []

    def after_step(self, at, events):
        self.observed.append(at)
        return []


class Connection:
    def __init__(self, faulty=False):
        self.ns = 0
        self.faulty = faulty
        self.simulation = self

    def getPendingVehicles(self):
        return []

    def simulationStep(self):
        self.ns += 200_000_000 if self.faulty else 100_000_000


def runtime(faulty=False):
    r = Runtime()
    r.step_ns = 100_000_000
    r.connection = Connection(faulty)
    r.commands = Commands()
    r.native_ns = lambda: r.connection.ns
    r.entities = list
    r.traffic_lights = list
    r.events = lambda: {
        k: []
        for k in (
            "departed",
            "arrived",
            "teleported_start",
            "teleported_end",
            "collisions",
            "removed",
        )
    }
    return r


def test_hold_integrates_no_steps_and_preserves_pending_command():
    r = runtime()
    result = r.advance({"to_sim_ns": 0})
    assert result["reached_sim_ns"] == 0
    assert not r.commands.applied
    assert not r.commands.observed


def test_multistep_observes_each_exact_native_frontier():
    r = runtime()
    result = r.advance({"to_sim_ns": 300_000_000})
    assert result["reached_sim_ns"] == 300_000_000
    assert r.commands.applied == [0, 100_000_000, 200_000_000]
    assert r.commands.observed == [100_000_000, 200_000_000, 300_000_000]


@pytest.mark.parametrize("target", [True, -1, 1, 100_000_001, None])
def test_invalid_grant_integrates_nothing(target):
    r = runtime()
    with pytest.raises(ValueError):
        r.advance({"to_sim_ns": target})
    assert r.connection.ns == 0
    assert not r.commands.applied


def test_native_mismatch_faults_before_any_sample_or_terminal_publication():
    r = runtime(True)
    with pytest.raises(RuntimeError, match="native frontier"):
        r.advance({"to_sim_ns": 100_000_000})
    assert r.sim_ns == 0
    assert not r.commands.observed


def test_native_termination_returns_real_diagnostic_and_no_samples(tmp_path):
    class NativeClosed(Exception):
        pass

    def fail():
        raise NativeClosed("connection closed")

    r = runtime()
    r.native_errors = (NativeClosed,)
    path = tmp_path / "native.log"
    path.write_bytes(b"Error: Vehicle source32 has no valid route.\n")
    r.log = type("Log", (), {"name": str(path)})()
    r.connection.simulationStep = fail
    with pytest.raises(RuntimeError, match="source32 has no valid route"):
        r.advance({"to_sim_ns": 100_000_000})
    assert r.sim_ns == 0
    assert not r.commands.observed
