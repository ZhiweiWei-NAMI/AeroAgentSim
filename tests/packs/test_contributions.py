"""Pack budgeting and inspection motion use the configured execution model."""

from __future__ import annotations

from typing import Any

import pytest
from aerokernel import Dependency, EntityRef, Fact
from aerokernel.sdk import EngineContext

from aeroagentsim.engines.kinematic import Kinematic
from aeroagentsim.models import ConsumptionModel, Segment
from aeroagentsim.packs.inspection import Inspection
from aeroagentsim.packs.logistics import Logistics
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

from .conftest import SCENARIOS, document

WIND = "test.environment.velocity"


def add_weather(d: dict[str, Any]) -> None:
    d["registry"]["types"].append(
        {"id": "test:Environment", "parents": [], "abstract": False}
    )
    d["registry"]["fields"].append(
        {
            "id": WIND,
            "type": "test:Environment",
            "schema": {"type": "vector", "length": 3, "items": {"type": "number"}},
            "metadata": {"unit": "m/s", "frame": "enu", "clock": "canonical"},
        }
    )
    d["entities"].append(
        {"id": "wind", "type": "test:Environment", "facts": {WIND: [-10.0, 0.0, 0.0]}}
    )
    d["bindings"]["rules"].append(
        {"writer": "weather", "type": "test:Environment", "fields": [WIND]}
    )
    d["bindings"]["lifecycle"].append(
        {"controller": "weather", "type": "test:Environment"}
    )
    d["engines"]["weather"] = {
        "plugin": "environment",
        "config": {
            "produces": [WIND],
            "lifecycle": True,
            "profiles": {
                "wind": {WIND: {"mode": "constant", "value": [-10.0, 0.0, 0.0]}}
            },
        },
    }
    d["engines"]["motion"]["config"]["consumption_model"] = {
        "plugin": "wind",
        "config": {
            "entity": "wind",
            "field": WIND,
            "coefficient_w_per_m_s2": 1.0,
        },
    }


class CountingModel:
    """Count the actual shared object's planning and execution calls."""

    def __init__(self, model: ConsumptionModel) -> None:
        self.model = model
        self.budgets = 0
        self.integrations = 0

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.model.dependencies

    def budget(
        self, ctx: EngineContext, ref: EntityRef, segments: tuple[Segment, ...]
    ) -> float:
        self.budgets += 1
        return self.model.budget(ctx, ref, segments)

    def consumption(
        self, ctx: EngineContext, ref: EntityRef, segment: Segment
    ) -> float:
        self.integrations += 1
        return self.model.consumption(ctx, ref, segment)


def test_logistics_uses_motion_model_identity_and_weather_budget(
    small: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    add_weather(small)
    original = EngineCatalog.build
    instances: dict[str, Any] = {}

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        # A planner may be constructed before its motion target; object identity
        # must already be shared independently of engine ID sort order.
        engine = original(catalog, plugin, build)
        instances[build.id] = engine
        return engine

    monkeypatch.setattr(EngineCatalog, "build", factory)
    sim = Simulation(load_scenario(small, base=SCENARIOS))
    logistics = instances["logistics"]
    motion = instances["motion"]
    assert isinstance(logistics, Logistics) and isinstance(motion, Kinematic)
    assert logistics.model is motion.model
    assert logistics.consumption_model is motion.consumption_model
    assert Dependency(WIND) in logistics.partition.consumes
    shared = CountingModel(motion.consumption_model)
    logistics.consumption_model = motion.consumption_model = shared
    sim.start()
    sim.run_until(20_000_000_000)
    sim.close()
    assert shared.budgets >= 3 and shared.integrations > 0
    # Lower real stored energy makes the same weather-aware route infeasible.
    next(e for e in small["entities"] if e["id"] == "carrier-1")["facts"][
        "packs.energy"
    ] = 1000.0
    sim = Simulation(load_scenario(small, base=SCENARIOS))
    sim.start()
    sim.run_until(2_000_000_000)
    sim.close()
    assert not any(
        r["status"] == "accepted"
        for record in sim.kernel.records
        for r in project(record)["receipts"]
    )
    small["engines"]["motion"]["config"].pop("consumption_model")
    calm = Simulation(load_scenario(small, base=SCENARIOS))
    calm.start()
    calm.run_until(2_000_000_000)
    calm.close()
    assert any(
        r["status"] == "accepted"
        for record in calm.kernel.records
        for r in project(record)["receipts"]
    )


def test_inspection_observes_weather_coupled_motion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = document("inspection-small")
    add_weather(d)
    original = EngineCatalog.build
    engines: dict[str, Any] = {}

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        engine = original(catalog, plugin, build)
        engines[build.id] = engine
        return engine

    monkeypatch.setattr(EngineCatalog, "build", factory)
    sim = Simulation(load_scenario(d, base=SCENARIOS))
    assert isinstance(engines["inspection"], Inspection)
    model = CountingModel(engines["motion"].consumption_model)
    engines["motion"].consumption_model = model
    sim.start()
    sim.run_until(5_000_000_000)
    sim.close()
    assert model.budgets > 0 and model.integrations > 0
    view = sim.kernel.view()
    ref = next(r for r in sim.scenario.manifest.entities if r.id == "carrier-1")
    energy = view.field((ref, "packs.energy"), view.instant)
    assert isinstance(energy, Fact) and isinstance(energy.value, float)
    assert energy.value < 100000.0 - 5 * 10
    observations = [
        m
        for r in sim.kernel.records
        for m in project(r)["messages"]
        if m["schemaId"] == "packs.inspection.observation"
    ]
    assert observations
