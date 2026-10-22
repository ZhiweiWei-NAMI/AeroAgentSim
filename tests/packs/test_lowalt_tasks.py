"""One complete task lifecycle is driven by physical command receipts."""

from pathlib import Path

import pytest

from aeroagentsim.packs.lowalt.template import parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def test_scheduled_parcel_lifecycle() -> None:
    document = parcel_document(fleet_size=1)
    generator = document["engines"]["tasks"]["config"]["generators"][0]
    generator.update(
        tasks=["task-1"], origins=["vertiport-1"], destinations=["customer-1"]
    )
    generator["arrivals"] = {"mode": "scheduled", "times_ns": [0]}
    document["entities"] = [
        e
        for e in document["entities"]
        if e["type"] != "lowalt:Task" or e["id"] == "task-1"
    ]
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(40_000_000_000)
        events = [
            m
            for r in sim.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"].startswith("lowalt.task.")
        ]
        assert [m["schemaId"] for m in events] == [
            "lowalt.task." + phase
            for phase in ("created", "assigned", "started", "picked_up", "completed")
        ]
        assert events[-1]["payload"]["latency_s"] > 0
        assert int(events[-1]["at"]["ns"]) > int(events[-2]["at"]["ns"])
        vehicle = next(r for r in sim.scenario.manifest.entities if r.id == "drone-1")
        assert (
            sim.kernel.view()
            .field((vehicle, "lowalt.payload_kg"), sim.kernel.view().instant)
            .value
            == 0.0
        )
    finally:
        sim.close()


def test_scripted_llm_uses_existing_langgraph_decision() -> None:
    import json

    import pytest

    pytest.importorskip("langgraph")
    document = parcel_document(fleet_size=1, allocator="llm")
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group.update(tasks=["task-1"], origins=["vertiport-1"], destinations=["customer-1"])
    group["arrivals"] = {"mode": "scheduled", "times_ns": [0]}
    document["entities"] = [
        e
        for e in document["entities"]
        if e["type"] != "lowalt:Task" or e["id"] == "task-1"
    ]
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(40_000_000_000)
        messages = [m for r in sim.kernel.records for m in project(r)["messages"]]
        records = [
            m["payload"] for m in messages if m["schemaId"] == "aas.langgraph.record"
        ]
        assert any(r["phase"] == "model_call" for r in records)
        assert not any(r["phase"] == "failure" for r in records), records
        assert any(m["schemaId"] == "lowalt.task.completed" for m in messages)
        call = next(r for r in records if r["phase"] == "model_call")
        assert (
            json.loads(call["data_json"])["request"]["model"]
            == "lowalt-scripted-allocation/v1"
        )
    finally:
        sim.close()


def test_llm_provider_failure_is_a_task_failure_without_fallback() -> None:
    import pytest

    pytest.importorskip("langgraph")
    document = parcel_document(fleet_size=1, allocator="llm")
    config = document["engines"]["tasks"]["config"]
    group = config["generators"][0]
    group.update(tasks=["task-1"], origins=["vertiport-1"], destinations=["customer-1"])
    group["arrivals"] = {"mode": "scheduled", "times_ns": [0]}
    document["entities"] = [
        e
        for e in document["entities"]
        if e["type"] != "lowalt:Task" or e["id"] == "task-1"
    ]
    config["algorithm_config"]["allocation"] = {
        "provider": {
            "mode": "stub",
            "model": "injected-failure",
            "responses": {
                "allocation": [
                    {"error": {"code": "TRANSPORT", "message": "provider unavailable"}}
                ]
            },
        }
    }
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(5_000_000_000)
        events = [
            m
            for r in sim.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"].startswith("lowalt.task.")
        ]
        assert [m["schemaId"] for m in events] == [
            "lowalt.task.created",
            "lowalt.task.failed",
        ]
        assert "TRANSPORT" in events[-1]["payload"]["reason"]
    finally:
        sim.close()


@pytest.mark.parametrize("allocator", ["nearest", "llm"])
def test_idle_weather_hold_does_not_erase_next_assignment_route(allocator: str) -> None:
    if allocator == "llm":
        pytest.importorskip("langgraph")
    document = parcel_document(fleet_size=1, allocator=allocator, wind_speed=0.0)
    # Away from pickup: a successful idle hold must not count as arrival there.
    document["generators"]["fleet"]["starts"] = [[-240.0, -180.0, 50.0]]
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group.update(tasks=["task-1"], origins=["vertiport-1"], destinations=["customer-1"])
    group["arrivals"] = {"mode": "scheduled", "times_ns": [10_000_000_000]}
    document["entities"] = [
        e for e in document["entities"]
        if e["type"] != "lowalt:Task" or e["id"] == "task-1"
    ]
    document["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"] = {
        "mode": "gust", "value": [0.0, 0.0, 0.0],
        "gusts": [{"start_ns": 2_000_000_000, "end_ns": 4_000_000_000,
                   "value": [14.0, 0.0, 0.0]}],
    }
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(60_000_000_000)
        schemas = [
            m["schemaId"] for r in sim.kernel.records for m in project(r)["messages"]
        ]
        expected = [
            "lowalt.weather.hold", "lowalt.motion.hold", "lowalt.weather.release",
            "lowalt.task.assigned", "lowalt.task.started", "lowalt.motion.move_to",
            "lowalt.task.completed",
        ]
        indices = [schemas.index(schema) for schema in expected]
        assert indices == sorted(indices)
        assert "lowalt.task.failed" not in schemas
        if allocator == "llm":
            assert schemas.index("lowalt.allocation.request") < schemas.index(
                "lowalt.allocation.result"
            ) < schemas.index("lowalt.task.assigned")
    finally:
        sim.close()
