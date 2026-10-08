"""Dynamic scalar capture uses the committed identity, never bootstrap caches."""

from __future__ import annotations

from pathlib import Path

import pytest
from aerokernel.state import Fact

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario


def test_h8_dynamic_scalar_subject_is_captured() -> None:
    document = load_scenario(Path("scenarios/p1-spectrum.yaml")).document
    document["engines"].pop("threshold")
    document["bindings"].pop("samples")
    cfg = document["engines"]["scanner"]["config"]
    cfg["emits"] = ["review.spectrum.overlimit"]
    cfg["targets"] = ["spectrum"]
    cfg["machines"][0]["states"]["finished"]["on_enter"] = [
        {
            "kind": "emit",
            "schema": "review.spectrum.overlimit",
            "topic": "spectrum",
            "payload": {"subject": "new-receiver"},
        }
    ]
    cfg["machines"][0]["states"]["busy"]["on_enter"] = [
        {
            "kind": "create",
            "entity": "new-receiver",
            "type": "oo:RadioSignalObservation",
            "facts": {
                "he.radio.branch_a_power_dbm": -10.0,
                "review.scan_phase": "finished",
            },
        }
    ]
    cfg["machines"][0]["states"]["finished"]["on_enter"][0]["payload"] = {
        "subject": "new-receiver"
    }
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(100_000_000)
        refs = [
            ref for ref in view._store.lives if ref.type_id == "review:SpectrumSample"
        ]
        assert len(refs) == 1
        fact = view.field((refs[0], "review.power_sample"), view.instant)
        assert isinstance(fact, Fact) and fact.value == -10.0
        edge = view.relations("oo:relation:observation-subject", view.instant)[0]
        assert edge.target.id == "new-receiver" and edge.target.generation == 0
    finally:
        simulation.close()


def test_records_preserve_native_clock_in_configured_stamp_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real producer uses its pinned sensor clock; record headers preserve it."""
    from typing import Any

    from aerokernel import Partition, Timing
    from aerokernel.sdk import ContextEngine, EngineContext
    from aerokernel.time import Stamp

    from aeroagentsim.engines.common import bootstrap_owned, policies
    from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog

    document = load_scenario(Path("scenarios/p1-spectrum.yaml")).document
    document["engines"].pop("threshold")
    document["bindings"].pop("samples")
    document["clock_mappings"] = [
        {"mapping_id": "canonical", "clock_id": "canonical"},
        {"mapping_id": "sensor-map", "clock_id": "sensor", "p": 1_000_000},
    ]
    document["registry"]["fields"].append(
        {
            "id": "review.source_stamp",
            "type": "review:SpectrumSample",
            "schema": {
                "type": "record",
                "members": {
                    "clockRef": {"type": "string", "enum": ["sensor"]},
                    "mappingRef": {"type": "string"},
                    "numerator": {"type": "integer"},
                    "denominator": {"type": "integer"},
                },
                "required": ["clockRef", "mappingRef", "numerator", "denominator"],
                "extra": False,
            },
            "metadata": {"clock": "sensor"},
        }
    )
    slots = document["engines"]["samples"]["config"]["slots"]
    slots["acquired"] = "review.source_stamp"
    slots.pop("available")
    document["engines"]["samples"]["config"].update(
        time_encoding="stamp", clock_ref="source"
    )
    document["bindings"]["rules"][2]["fields"] = list(slots.values())
    document["engines"]["scanner"] = {"plugin": "native-clock-test", "config": {}}

    class NativeSource(ContextEngine):
        def __init__(self, build: EngineBuild) -> None:
            self.build = build
            fields = ("review.scan_phase", "he.radio.branch_a_power_dbm")
            super().__init__(
                Partition(
                    build.id,
                    build.id,
                    produces=fields,
                    emits=("review.spectrum.overlimit",),
                    message_targets=("spectrum",),
                    lifecycle=True,
                    timing=Timing("fixed_step", 1_000_000),
                ),
                policies=policies(fields),
            )

        def bootstrap(self, ctx: EngineContext) -> None:
            bootstrap_owned(ctx, self.build)

        def step(self, ctx: EngineContext) -> None:
            if ctx.now.ns == 1_000_000:
                ctx.set(
                    self.build.entities[0],
                    "he.radio.branch_a_power_dbm",
                    -20.0,
                    acquired=Stamp("sensor", 1, 1, "sensor-map"),
                )
                ctx.emit(
                    "review.spectrum.overlimit",
                    {"subject": "receiver"},
                    topic="spectrum",
                )

    original = EngineCatalog.build

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        return (
            NativeSource(build)
            if plugin == "native-clock-test"
            else original(catalog, plugin, build)
        )

    monkeypatch.setattr(EngineCatalog, "build", factory)
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(30_000_000)
        ref = next(
            ref for ref in view._store.lives if ref.type_id == "review:SpectrumSample"
        )
        fact = view.field((ref, "review.source_stamp"), view.instant)
        assert isinstance(fact, Fact)
        assert fact.value["clockRef"] == "sensor" and fact.value["numerator"] == 1
        assert fact.acquired == Stamp("sensor", 1, 1, "sensor-map")
    finally:
        simulation.close()
