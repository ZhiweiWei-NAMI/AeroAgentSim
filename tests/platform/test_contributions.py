"""Shared model execution, authoritative weather reads and field-wise writers."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from aerokernel import CommandRequest, Fact, Instant, Partition, Timing, replay
from aerokernel.sdk import ContextEngine, EngineContext

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.engines.kinematic import Kinematic
from aeroagentsim.models import LinearConsumption, ModelBuild, Segment
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

POS = "he.aircraft.position_enu_m"
VEL = "aas.p1.velocity_enu_m_s"
ENERGY = "aas.p1.energy_j"
WIND = "oo:digital_twin.wind.horizontalVelocity"
SECOND = 1_000_000_000


@pytest.fixture(scope="module")
def wind_base(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Pin real AeroGraph environment descriptors, without writing to AeroGraph."""
    d: dict[str, Any] = yaml.safe_load(Path("scenarios/p1-slice.yaml").read_text())
    d["registry"]["compile"]["types"].append("oo:WindField")
    d["registry"]["compile"]["fields"].append(WIND)
    mover = d["entities"][0]
    mover["facts"].update(
        {POS: [0.0, 0.0, 0.0], VEL: [0.0, 0.0, 0.0], ENERGY: 100000.0}
    )
    d["entities"] = [
        mover,
        {"id": "wind", "type": "oo:WindField", "facts": {WIND: [[0.0, 0.0]]}},
    ]
    motion = d["engines"]["motion"]
    d["engines"] = {
        "motion": motion,
        "weather": {
            "plugin": "environment",
            "config": {
                "produces": [WIND],
                "lifecycle": True,
                "profiles": {"wind": {WIND: {"mode": "calm", "value": [[0.0, 0.0]]}}},
            },
        },
    }
    d["bindings"] = {
        "rules": [
            {"writer": "motion", "type": "oo:UAV", "fields": [POS, VEL, ENERGY]},
            {"writer": "weather", "type": "oo:WindField", "fields": [WIND]},
        ],
        "lifecycle": [
            {"controller": "motion", "type": "oo:UAV"},
            {"controller": "weather", "type": "oo:WindField"},
        ],
    }
    d["run"].update(until_ns=4 * SECOND, advance_ns=100_000_000)
    cfg = motion["config"]
    cfg["consumption_model"] = {
        "plugin": "wind",
        "config": {
            "entity": "wind",
            "field": WIND,
            "vector_index": 0,
            "coefficient_w_per_m_s2": 0.5,
        },
    }
    scenario = load_scenario(d)
    path = tmp_path_factory.mktemp("wind-snapshot") / "snapshot.json"
    scenario.compiled.write_snapshot(path)
    d["registry"].pop("compile")
    d["registry"].update(snapshot=str(path), digest=scenario.compiled.digest)
    descriptor = scenario.registry.field(WIND)
    assert descriptor.declaring_type == "oo:WindField"
    assert descriptor.schema["items"]["length"] == 2
    return d


def weather_document(base: dict[str, Any], mode: str) -> dict[str, Any]:
    d = copy.deepcopy(base)
    profile = d["engines"]["weather"]["config"]["profiles"]["wind"][WIND]
    profile["mode"] = mode
    if mode == "constant":
        profile["value"] = [[-10.0, 0.0]]
        d["entities"][1]["facts"][WIND] = profile["value"]
    elif mode == "gust":
        profile["gusts"] = [
            {"start_ns": SECOND, "end_ns": 2 * SECOND, "value": [[-10.0, 0.0]]}
        ]
    return d


def fly(d: dict[str, Any]) -> tuple[Simulation, str]:
    sim = Simulation(load_scenario(d))
    sim.start()
    command = sim.kernel.submit(
        CommandRequest(
            "aas.motion.move_to",
            "motion",
            Instant(1),
            {"entity": "uav-1", "machine": "manual", "target": [10.0, 0.0, 0.0]},
        )
    )
    sim.run_until(4 * SECOND)
    sim.close()
    return sim, command


def version_key(fact: dict[str, Any]) -> tuple[int, int]:
    return fact["version"]["journalIndex"], fact["version"]["itemOrdinal"]


def cause_keys(fact: dict[str, Any]) -> set[tuple[int, int]]:
    return {
        (c["fields"]["record_index"], c["fields"]["item_index"]) for c in fact["causes"]
    }


def test_calm_headwind_gust_energy_causes_and_replay(wind_base: dict[str, Any]) -> None:
    used: dict[str, float] = {}
    for mode in ("calm", "constant", "gust"):
        sim, command = fly(weather_document(wind_base, mode))
        assert sim.kernel.action(command).status == "succeeded"
        ref = sim.scenario.manifest.entities[0]
        view = sim.kernel.view()
        fact = view.field((ref, ENERGY), view.instant)
        assert isinstance(fact, Fact) and isinstance(fact.value, float)
        used[mode] = 100000.0 - fact.value
        # Check committed FactWrite causes, not merely a dirty weather notification.
        weather_versions = {
            version_key(f)
            for r in sim.kernel.records
            for f in project(r)["facts"]
            if f["fieldId"] == WIND
        }
        energy_facts = [
            f
            for r in sim.kernel.records
            for f in project(r)["facts"]
            if f["fieldId"] == ENERGY
        ]
        assert all(
            any(c in weather_versions for c in cause_keys(f)) for f in energy_facts[1:]
        )
        if mode == "gust":
            # Both the gust and restored calm versions must actually drive energy facts.
            cited = {
                c for f in energy_facts for c in cause_keys(f) if c in weather_versions
            }
            assert len(cited) == 3
        data = sim.kernel.journal.bytes
        recovered = replay(data)
        assert not recovered.incomplete
        assert recovered.view().field((ref, ENERGY), recovered.view().instant) == fact
        again, _ = fly(weather_document(wind_base, mode))
        assert data == again.kernel.journal.bytes
    assert used["constant"] > used["gust"] > used["calm"]
    assert used["calm"] == pytest.approx(20.0 * 4 + 5.0 * 10)


def test_weather_budget_reserve_typed_rejection(wind_base: dict[str, Any]) -> None:
    d = weather_document(wind_base, "constant")
    d["engines"]["motion"]["config"]["reserve_j"] = 100.0
    d["entities"][0]["facts"][ENERGY] = 300.0
    sim, command = fly(d)
    assert sim.kernel.action(command).status == "rejected"
    receipts = [
        x
        for r in sim.kernel.records
        for x in project(r)["receipts"]
        if x["status"] == "rejected"
    ]
    assert receipts[0]["result"] == {"reason": "energy reserve would be violated"}
    weather_version = next(
        version_key(f)
        for r in sim.kernel.records
        for f in project(r)["facts"]
        if f["fieldId"] == WIND
    )
    rejected = next(
        item
        for record in sim.kernel.records
        for item in record["items"]
        if "receipt" in item and item["receipt"]["status"] == "rejected"
    )
    assert weather_version in cause_keys(rejected)
    ref = sim.scenario.manifest.entities[0]
    fact = sim.kernel.view().field((ref, POS), sim.kernel.view().instant)
    assert isinstance(fact, Fact) and fact.value == (0.0, 0.0, 0.0)


class EnergyWriter(ContextEngine):
    """Independent measured-energy producer; no copied motion/energy equation."""

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=(ENERGY,),
                timing=Timing("fixed_step", SECOND),
            ),
            policies=policies((ENERGY,)),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def step(self, ctx: EngineContext) -> None:
        ref = self.build.entities[0]
        ctx.set(ref, ENERGY, 1.0)


def test_motion_energy_have_independent_writers(
    wind_base: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    d = weather_document(wind_base, "calm")
    d["bindings"]["rules"][0]["fields"] = [POS, VEL]
    d["bindings"]["rules"].append(
        {"writer": "battery", "type": "oo:UAV", "fields": [ENERGY]}
    )
    d["engines"]["battery"] = {"plugin": "test-energy", "config": {}}
    original = EngineCatalog.build
    movers: list[Kinematic] = []

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        if plugin == "test-energy":
            return EnergyWriter(build)
        engine = original(catalog, plugin, build)
        if isinstance(engine, Kinematic):
            movers.append(engine)
        return engine

    monkeypatch.setattr(EngineCatalog, "build", factory)
    sim = Simulation(load_scenario(d))
    sim.start()
    sim.run_until(SECOND)
    command = sim.kernel.submit(
        CommandRequest(
            "aas.motion.move_to",
            "motion",
            Instant(SECOND + 1),
            {"entity": "uav-1", "machine": "manual", "target": [10.0, 0.0, 0.0]},
        )
    )
    sim.run_until(2 * SECOND)
    sim.close()
    assert sim.kernel.action(command).status == "rejected"
    assert set(movers[0].partition.produces) == {POS, VEL}
    facts = [
        f
        for r in sim.kernel.records
        for f in project(r)["facts"]
        if f["fieldId"] == ENERGY
    ]
    assert {f["producer"] for f in facts} == {"battery"}


def test_entry_point_reads_are_retained_without_engine_subclass(
    wind_base: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    class InstalledModel(LinearConsumption):
        dependencies = (WIND,)

        def consumption(self, ctx: EngineContext, ref: Any, segment: Segment) -> float:
            wind = next(r for r in wind_base["entities"] if r["id"] == "wind")
            # Resolve the selected typed ref from the actual build, not a synthetic fact.
            assert wind["type"] == "oo:WindField"
            ctx.get(self.weather, WIND)
            return super().consumption(ctx, ref, segment)

        def __init__(self, build: ModelBuild) -> None:
            super().__init__(build)
            self.weather = next(r for r in build.entities if r.id == "wind")

    class Entry:
        name = "installed-model"

        def load(self) -> Any:
            return InstalledModel

    monkeypatch.setattr("aeroagentsim.models.entry_points", lambda **_: [Entry()])
    d = weather_document(wind_base, "calm")
    d["engines"]["motion"]["config"]["consumption_model"] = {
        "plugin": "installed-model",
        "config": {},
    }
    sim, _ = fly(d)
    weather_version = next(
        version_key(f)
        for r in sim.kernel.records
        for f in project(r)["facts"]
        if f["fieldId"] == WIND
    )
    energy = [
        f
        for r in sim.kernel.records
        for f in project(r)["facts"]
        if f["fieldId"] == ENERGY
    ]
    assert all(weather_version in cause_keys(f) for f in energy[1:])


def test_reads_before_super_hooks_keep_their_versions(
    wind_base: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from aerokernel import Dependency

    class Prefetch(Kinematic):
        def __init__(self, build: EngineBuild) -> None:
            super().__init__(build)
            self.weather = next(r for r in build.entities if r.id == "wind")
            self.partition = replace(self.partition, consumes=(Dependency(WIND),))
            self.partitions = (self.partition,)

        def step(self, ctx: EngineContext) -> None:
            ctx.get(self.weather, WIND)
            super().step(ctx)

        def on_inputs(self, ctx: EngineContext) -> None:
            ctx.get(self.weather, WIND)
            super().on_inputs(ctx)

    original = EngineCatalog.build

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        return (
            Prefetch(build)
            if plugin == "kinematic"
            else original(catalog, plugin, build)
        )

    monkeypatch.setattr(EngineCatalog, "build", factory)
    d = weather_document(wind_base, "calm")
    # The model itself does not read weather; only the extension's pre-super read
    # supplies this cause, reproducing the review's lost-provenance failure.
    d["engines"]["motion"]["config"].pop("consumption_model")
    sim, _ = fly(d)
    weather_version = next(
        version_key(f)
        for r in sim.kernel.records
        for f in project(r)["facts"]
        if f["fieldId"] == WIND
    )
    facts = [
        f
        for r in sim.kernel.records
        for f in project(r)["facts"]
        if f["fieldId"] == ENERGY
    ]
    assert all(weather_version in cause_keys(f) for f in facts[1:])
    accepted = [
        item
        for record in sim.kernel.records
        for item in record["items"]
        if "receipt" in item and item["receipt"]["status"] == "accepted"
    ]
    assert accepted and weather_version in cause_keys(accepted[0])


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "fixtures/model_cases.json").read_text()),
    ids=lambda c: c["name"],
)
def test_directional_consumption_case_table(
    wind_base: dict[str, Any], case: dict[str, Any]
) -> None:
    from aeroagentsim.models import WindConsumption

    d = weather_document(wind_base, "constant")
    wind = [[float(v) for v in case["wind"][:2]]]
    d["entities"][1]["facts"][WIND] = wind
    d["engines"]["weather"]["config"]["profiles"]["wind"][WIND]["value"] = wind
    sim = Simulation(load_scenario(d))
    sim.start()
    config = d["engines"]["motion"]["config"]
    model = WindConsumption(
        ModelBuild(
            config["consumption_model"]["config"],
            config["energy"],
            sim.scenario.registry,
            sim.scenario.manifest.entities,
        )
    )
    displacement = tuple(float(v) for v in case["displacement"])
    segment = Segment(
        (displacement[0], displacement[1], displacement[2]),
        float(case["elapsed_s"]),
        float(case["distance_m"]),
    )
    ctx = EngineContext(sim.kernel.view())
    ref = sim.scenario.manifest.entities[0]
    assert model.consumption(ctx, ref, segment) == case["expected_consumption_j"]
    assert model.budget(ctx, ref, (segment,)) == case["expected_consumption_j"]
    assert ctx.inputs  # actual weather version read, including the calm case
    sim.close()


def test_energy_only_kinematic_is_not_a_static_energy_fallback(
    wind_base: dict[str, Any],
) -> None:
    d = weather_document(wind_base, "calm")
    d["bindings"]["rules"][0]["fields"] = [ENERGY]
    with pytest.raises(ValueError, match="energy-only"):
        Simulation(load_scenario(d))


def test_unselected_plugins_do_not_change_legacy_model(
    wind_base: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aeroagentsim.models.entry_points",
        lambda **_: pytest.fail("unselected model plugin discovery"),
    )
    d = weather_document(wind_base, "calm")
    d["engines"]["motion"]["config"].pop("consumption_model")
    sim, command = fly(d)
    assert sim.kernel.action(command).status == "succeeded"
    ref = sim.scenario.manifest.entities[0]
    fact = sim.kernel.view().field((ref, ENERGY), sim.kernel.view().instant)
    assert isinstance(fact, Fact) and fact.value == pytest.approx(100000.0 - 130.0)
