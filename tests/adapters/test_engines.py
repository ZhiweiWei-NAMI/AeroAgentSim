"""Kernel-coordinated mapping, receipts, lifecycle and atomic fault regressions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aerokernel import CommandRequest, Fact, Instant, KernelError, replay

from aeroagentsim.adapters.runner import AdapterRun
from aeroagentsim.scenario import load_scenario

from .fake_backend import FakeBackend

ROOT = Path(__file__).resolve().parents[2] / "scenarios" / "adapters"


def hello(
    request: dict[str, Any], quantum_key: str, actions: list[str]
) -> dict[str, Any]:
    return {
        "protocol": request["protocol"],
        "major": 1,
        "minor": 0,
        "capabilities": {
            "timing": "lockstep",
            "exact_stop": True,
            "hold": True,
            quantum_key: 1,
            "actions": actions,
            "max_frame_bytes": 8 * 1024 * 1024,
            "vehicles": ["x500"],
            "sensors": ["gazebo_pose", "mavsdk_telemetry", "gazebo_contacts"],
        },
    }


def px4_sample(ns: int) -> dict[str, Any]:
    return {
        "vehicle": "u1",
        "sim_ns": ns,
        "position_enu": [1.0, 2.0, 3.0],
        "velocity_enu": [0.0, 0.0, 0.0],
        "attitude_quat": [0.0, 0.0, 0.0, 1.0],
        "battery": {"remaining_fraction": 0.9, "voltage_v": 16.0},
        "armed": False,
        "flight_mode": "HOLD",
        "landed_state": "ON_GROUND",
        "freshness": {"mavsdk_source_sim_ns": None},
    }


@pytest.mark.parametrize(
    "bad", ["missing", "bad_type", "wrong_time", "duplicate", "late_contact"]
)
def test_px4_fault_publishes_no_partial_telemetry(bad: str) -> None:
    def handle(request: dict[str, Any]) -> dict[str, Any]:
        if request["op"] == "hello":
            return hello(
                request,
                "physics_step_ns",
                ["arm", "takeoff", "goto_location", "hold", "land", "disarm"],
            )
        if request["op"] == "reset":
            return {
                "reached_sim_ns": 0,
                "physics_step_ns": 1,
                "warmup_sim_ns": 10_000_000_000,
                "vehicles": ["u1"],
                "telemetry": [px4_sample(0)],
            }
        if request["op"] == "close":
            return {"closed": True}
        ns = request["payload"]["to_sim_ns"]
        sample = px4_sample(ns)
        contacts = []
        if bad == "missing":
            del sample["velocity_enu"]
        elif bad == "bad_type":
            sample["velocity_enu"] = None
        elif bad == "wrong_time":
            sample["sim_ns"] = ns - 1
        elif bad == "late_contact":
            contacts = [
                {
                    "sim_ns": ns + 1,
                    "topic": "contact",
                    "collision1": "a",
                    "collision2": "b",
                }
            ]
        return {
            "reached_sim_ns": ns,
            "telemetry": [sample, sample] if bad == "duplicate" else [sample],
            "contacts": contacts,
            "command_updates": [],
        }

    server = FakeBackend(handle)
    scenario = load_scenario(ROOT / "px4-flight.yaml")
    (
        scenario.engines["flight"]["config"]["host"],
        scenario.engines["flight"]["config"]["port"],
    ) = server.endpoint
    with AdapterRun(scenario) as run:
        run.start()
        ref = scenario.manifest.entities[0]
        initial = run.kernel.view().field((ref, "e1.px4.position"), Instant(0))
        with pytest.raises(KernelError):
            run.run_until(200_000_000)
        latest = run.kernel.view().field((ref, "e1.px4.position"), Instant(200_000_000))
        assert isinstance(initial, Fact) and isinstance(latest, Fact)
        assert initial.version == latest.version
        assert run.engines[0].client.tainted
        data = run.kernel.journal.bytes
    offline = replay(data)
    assert (
        offline.view().field((ref, "e1.px4.position"), Instant(200_000_000)).version
        == initial.version
    )
    server.close()


def test_px4_latched_command_and_observed_receipt_chain() -> None:
    pending = []

    def handle(request: dict[str, Any]) -> dict[str, Any]:
        op = request["op"]
        if op == "hello":
            return hello(
                request,
                "physics_step_ns",
                ["arm", "takeoff", "goto_location", "hold", "land", "disarm"],
            )
        if op == "reset":
            return {
                "reached_sim_ns": 0,
                "physics_step_ns": 1,
                "warmup_sim_ns": 10_000_000_000,
                "vehicles": ["u1"],
                "telemetry": [px4_sample(0)],
            }
        if op == "command":
            pending.append(request["payload"])
            return {
                "status": "accepted",
                "command_id": pending[-1]["command_id"],
                "sim_ns": 200_000_000,
            }
        if op == "close":
            return {"closed": True}
        ns = request["payload"]["to_sim_ns"]
        updates = []
        if pending:
            p = pending.pop()
            updates = [
                {
                    "command_id": p["command_id"],
                    "vehicle": p["vehicle"],
                    "action": p["action"],
                    "sim_ns": ns,
                    "status": status,
                }
                for status in ("running", "succeeded")
            ]
        return {
            "reached_sim_ns": ns,
            "telemetry": [px4_sample(ns)],
            "contacts": [],
            "command_updates": updates,
        }

    server = FakeBackend(handle)
    scenario = load_scenario(ROOT / "px4-flight.yaml")
    (
        scenario.engines["flight"]["config"]["host"],
        scenario.engines["flight"]["config"]["port"],
    ) = server.endpoint
    with AdapterRun(scenario) as run:
        run.start()
        mid = run.kernel.submit(
            CommandRequest(
                "adapters.px4_gazebo.arm",
                "flight",
                Instant(3_000_000),
                {"entity": "aircraft"},
            )
        )
        run.run_until(3_000_000)
        assert not any(r["op"] in {"advance", "command"} for r in server.requests)
        run.run_until(200_000_000)
        assert run.kernel.view().action(mid).status == "accepted"
        assert server.requests[-1]["payload"]["vehicle"] == "u1"
        run.run_until(400_000_000)
        assert run.kernel.view().action(mid).status == "succeeded"
        data = run.kernel.journal.bytes
    assert replay(data).view().action(mid).status == "succeeded"
    server.close()


def sumo_vehicle(ns: int) -> dict[str, Any]:
    return {
        "id": "v0",
        "kind": "vehicle",
        "sim_ns": ns,
        "position": {"xy": [1.0, 2.0]},
        "speed": 3.0,
        "route": ["a", "b"],
    }


@pytest.mark.parametrize("terminal", ["arrived", "removed"])
def test_sumo_dynamic_lifecycle_evidence_and_replay(terminal: str) -> None:
    def handle(request: dict[str, Any]) -> dict[str, Any]:
        if request["op"] == "hello":
            return hello(
                request,
                "step_quantum_ns",
                [
                    "set_speed",
                    "reroute",
                    "change_target",
                    "lane_restriction",
                    "tls_phase",
                    "add_vehicle",
                    "remove_vehicle",
                ],
            )
        if request["op"] == "close":
            return {"closed": True}
        if request["op"] == "reset":
            return {
                "reached_sim_ns": 0,
                "step_length_ns": 100_000_000,
                "entities": [],
                "traffic_lights": [],
            }
        ns = request["payload"]["to_sim_ns"]
        events = {
            key: []
            for key in (
                "departed",
                "arrived",
                "removed",
                "teleported_start",
                "teleported_end",
                "collisions",
            )
        }
        if ns == 100_000_000:
            events["departed"] = [{"id": "v0", "kind": "vehicle", "sim_ns": ns}]
        else:
            events[terminal] = [{"id": "v0", "kind": "vehicle", "sim_ns": ns}]
        return {
            "reached_sim_ns": ns,
            "entities": [sumo_vehicle(ns)] if ns == 100_000_000 else [],
            "traffic_lights": [],
            "command_updates": [],
            **events,
        }

    server = FakeBackend(handle)
    scenario = load_scenario(ROOT / "sumo-grid.yaml")
    (
        scenario.engines["traffic"]["config"]["host"],
        scenario.engines["traffic"]["config"]["port"],
    ) = server.endpoint
    with AdapterRun(scenario) as run:
        run.start()
        run.run_until(100_000_000)
        ref = run.engines[0].active[("vehicle", "v0")]
        fact = run.kernel.view().field(
            (ref, "e1.sumo.vehicle.speed"), Instant(100_000_000, 99)
        )
        assert isinstance(fact, Fact) and fact.value == 3.0
        run.run_until(200_000_000)
        assert run.kernel.view().lifecycle(ref).removed is not None
        data = run.kernel.journal.bytes
    assert replay(data).view().lifecycle(ref).removed is not None
    server.close()


@pytest.mark.parametrize("bad_link", [False, True])
def test_ns3_lagged_mobility_delivery_and_atomic_last_record_fault(
    bad_link: bool,
) -> None:
    def flight(request: dict[str, Any]) -> dict[str, Any]:
        if request["op"] == "hello":
            return hello(
                request,
                "physics_step_ns",
                ["arm", "takeoff", "goto_location", "hold", "land", "disarm"],
            )
        if request["op"] == "close":
            return {"closed": True}
        ns = 0 if request["op"] == "reset" else request["payload"]["to_sim_ns"]
        sample = px4_sample(ns)
        sample["position_enu"] = [ns / 1e9, 2.0, 3.0]
        result = {"reached_sim_ns": ns, "telemetry": [sample]}
        if request["op"] == "reset":
            result.update(
                physics_step_ns=1, vehicles=["u1"], warmup_sim_ns=10_000_000_000
            )
        else:
            result.update(contacts=[], command_updates=[])
        return result

    pending: list[dict[str, Any]] = []
    frontier = 0

    def radio(request: dict[str, Any]) -> dict[str, Any]:
        nonlocal frontier
        if request["op"] == "hello":
            return hello(request, "time_quantum_ns", ["send"])
        if request["op"] == "reset":
            return {"reached_sim_ns": 0, "configuration": request["payload"]}
        if request["op"] == "close":
            return {"closed": True}
        if request["op"] == "command":
            pending.append(request["payload"]["params"])
            return {
                "status": "accepted",
                "packet_id": pending[-1]["packet_id"],
                "sim_ns": frontier,
            }
        ns = request["payload"]["to_sim_ns"]
        deliveries = [
            {
                **packet,
                "sent_ns": frontier,
                "received_ns": frontier + 123,
                "available_sim_ns": ns,
            }
            for packet in pending
        ]
        for delivery in deliveries:
            del delivery["lifetime_ns"]
        pending.clear()
        link = {
            "src": "uav",
            "dst": "ground",
            "sim_ns": ns,
            "available_sim_ns": ns,
            "distance_m": 20.0,
            "path_loss_db": 30.0,
            "predicted_rssi_dbm": -10.0,
            "sent": 1,
            "delivered": 1,
            "dropped": 0,
        }
        if bad_link and deliveries:
            del link["distance_m"]
        frontier = ns
        return {
            "reached_sim_ns": ns,
            "deliveries": deliveries,
            "drops": [],
            "link_stats": [link],
            "pending_packets": 0,
        }

    flight_server, radio_server = FakeBackend(flight), FakeBackend(radio)
    original = load_scenario(ROOT / "coupled.yaml")
    document = original.document
    del document["engines"]["traffic"]
    document["bindings"]["rules"] = [
        r for r in document["bindings"]["rules"] if r["writer"] != "traffic"
    ]
    document["bindings"]["lifecycle"] = [
        r for r in document["bindings"]["lifecycle"] if r["controller"] != "traffic"
    ]
    document["bindings"]["commands"] = [
        c for c in document["bindings"]["commands"] if c["target"] == "radio"
    ][:1]
    for engine, server in (("flight", flight_server), ("radio", radio_server)):
        (
            document["engines"][engine]["config"]["host"],
            document["engines"][engine]["config"]["port"],
        ) = server.endpoint
    scenario = load_scenario(document, base=ROOT)
    with AdapterRun(scenario) as run:
        run.start()
        run.run_until(1_000_000_000)
        mid = next(
            m.id
            for m in run.kernel.view()._store.messages.values()
            if m.schema_id == "adapters.ns3.send"
        )
        assert run.kernel.view().action(mid).status == "executing"
        if bad_link:
            with pytest.raises(KernelError):
                run.run_until(1_200_000_000)
            assert run.kernel.view().action(mid).status == "executing"
            assert not any(
                m.schema_id == "adapters.ns3.delivery"
                for m in run.kernel.view()._store.messages.values()
            )
        else:
            run.run_until(1_200_000_000)
            assert run.kernel.view().action(mid).status == "succeeded"
            message = next(
                m
                for m in run.kernel.view()._store.messages.values()
                if m.schema_id == "adapters.ns3.delivery"
            )
            assert message.at.ns == 1_000_000_123
            assert message.available.ns == 1_200_000_000
            assert message.source_stamp.clock_id == "ns3.simulation"
            advances = [r for r in radio_server.requests if r["op"] == "advance"]
            assert advances[-1]["payload"]["mobility"][0]["position"] == [1.0, 2.0, 3.0]
            assert advances[-1]["payload"]["mobility"][0]["sim_ns"] == 1_000_000_000
        data = run.kernel.journal.bytes
    assert replay(data).view().action(mid).status == (
        "executing" if bad_link else "succeeded"
    )
    flight_server.close()
    radio_server.close()


@pytest.mark.parametrize(
    "case", ["bad_field", "missing_terminal", "short_lived", "reuse"]
)
def test_sumo_complete_snapshot_lifecycle_cases(case: str) -> None:
    def handle(request: dict[str, Any]) -> dict[str, Any]:
        if request["op"] == "hello":
            return hello(
                request,
                "step_quantum_ns",
                [
                    "set_speed",
                    "reroute",
                    "change_target",
                    "lane_restriction",
                    "tls_phase",
                    "add_vehicle",
                    "remove_vehicle",
                ],
            )
        if request["op"] == "close":
            return {"closed": True}
        if request["op"] == "reset":
            return {
                "reached_sim_ns": 0,
                "step_length_ns": 100_000_000,
                "entities": [],
                "traffic_lights": [],
            }
        ns = request["payload"]["to_sim_ns"]
        events = {
            key: []
            for key in (
                "departed",
                "arrived",
                "removed",
                "teleported_start",
                "teleported_end",
                "collisions",
            )
        }
        entities = []
        if ns in {100_000_000, 300_000_000}:
            events["departed"] = [{"id": "v0", "kind": "vehicle", "sim_ns": ns}]
            entities = [sumo_vehicle(ns)]
            if case == "bad_field":
                entities[0]["speed"] = None
            if case == "short_lived":
                events["arrived"] = events["departed"][:]
                entities = []
        elif case != "missing_terminal":
            events["arrived"] = [{"id": "v0", "kind": "vehicle", "sim_ns": ns}]
        return {
            "reached_sim_ns": ns,
            "entities": entities,
            "traffic_lights": [],
            "command_updates": [],
            **events,
        }

    server = FakeBackend(handle)
    scenario = load_scenario(ROOT / "sumo-grid.yaml")
    (
        scenario.engines["traffic"]["config"]["host"],
        scenario.engines["traffic"]["config"]["port"],
    ) = server.endpoint
    with AdapterRun(scenario) as run:
        run.start()
        if case == "bad_field":
            with pytest.raises(KernelError):
                run.run_until(100_000_000)
            assert not run.kernel.view()._store.lives
        else:
            run.run_until(100_000_000)
            first = next(iter(run.kernel.view()._store.lives))
            if case == "missing_terminal":
                old = run.kernel.view().field(
                    (first, "e1.sumo.vehicle.speed"), run.kernel.view().instant
                )
                with pytest.raises(KernelError):
                    run.run_until(200_000_000)
                assert run.kernel.view().lifecycle(first).removed is None
                assert (
                    run.kernel.view()
                    .field((first, "e1.sumo.vehicle.speed"), run.kernel.view().instant)
                    .version
                    == old.version
                )
            elif case == "short_lived":
                assert run.kernel.view().lifecycle(first).removed is not None
            else:
                run.run_until(300_000_000)
                second = run.engines[0].active[("vehicle", "v0")]
                assert (
                    second.id == first.id and second.generation == first.generation + 1
                )
                assert run.kernel.view().lifecycle(first).removed is not None
                assert run.kernel.view().lifecycle(second).removed is None
        data = run.kernel.journal.bytes
    assert dict(replay(data).view()._store.lives) == dict(
        run.kernel.view()._store.lives
    )
    server.close()
