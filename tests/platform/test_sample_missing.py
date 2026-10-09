"""Retraction is a real unknown frame, never an inherited false baseline."""

from __future__ import annotations

from typing import Any, cast

import pytest
from aerokernel import Interval, Partition, Timing
from aerokernel.sdk import ContextEngine, EngineContext

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

POS = "he.aircraft.position_enu_m"


class RetractionWriter(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=(POS,),
                lifecycle=True,
                timing=Timing("fixed_step", 1_000_000_000),
            ),
            policies=policies((POS,)),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def step(self, ctx: EngineContext) -> None:
        ref = self.build.entities[0]
        second = ctx.now.ns // 1_000_000_000
        if second == 2:
            ctx.retract(ref, POS, Interval(ctx.now, None), "test source unavailable")
        else:
            ctx.set(ref, POS, [0.0 if second == 4 else 20.0, 0.0, 0.0])


def test_unknown_breaks_transition_baseline(
    document: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    document["entities"] = [document["entities"][0]]
    entity = document["entities"][0]
    entity["facts"] = {POS: [0.0, 0.0, 0.0]}
    threshold = document["engines"]["threshold"]
    document["engines"] = {
        "motion": {"plugin": "retraction-fixture", "config": {}},
        "threshold": threshold,
    }
    document["registry"]["relations"] = []
    sample = document["bindings"]["samples"][0]
    sample.update(
        upstream=["motion"],
        bindings={entity["id"]: entity["id"]},
        sources={entity["id"]: "motion"},
        clocks={entity["id"]: ["canonical", "canonical"]},
    )
    document["bindings"] = {
        "rules": [{"writer": "motion", "type": entity["type"], "fields": [POS]}],
        "lifecycle": [{"controller": "motion", "type": entity["type"]}],
        "samples": [sample],
    }
    original = EngineCatalog.build

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        return (
            RetractionWriter(build)
            if plugin == "retraction-fixture"
            else original(catalog, plugin, build)
        )

    monkeypatch.setattr(EngineCatalog, "build", factory)
    simulation = Simulation(load_scenario(document))
    simulation.start()
    view = simulation.run_until(5_000_000_000)
    frames = view.sample_frames(sample["context"])
    assert [
        cast(dict[str, Any], frame.frame.result)["states"][entity["id"]]["status"]
        for frame in frames
    ] == ["known", "known", "required_input", "known", "known", "known"]
    events = [
        m
        for record in simulation.kernel.records
        for m in project(record)["messages"]
        if m["schemaId"] == "aas.p1.reached_x"
    ]
    assert [int(event["at"]["ns"]) for event in events] == [
        1_000_000_000,
        5_000_000_000,
    ]
    simulation.close()
