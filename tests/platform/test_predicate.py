"""Committed sample provenance, exact timers, fail-closed admission and replay."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast

import pytest
from aerokernel import Fact, Interval, Journal, Partition, Stamp, replay
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import FrozenValue, thaw

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import ScenarioError, load_scenario
from aeroagentsim.scenario.paths import source_path
from aeroagentsim.services.projector import project


def demo() -> dict[str, Any]:
    return copy.deepcopy(load_scenario(Path("scenarios/predicates-demo.yaml")).document)


def test_temporal_event_exact_ns_and_identical_replay(tmp_path: Path) -> None:
    logs = []
    for index in range(2):
        path = tmp_path / f"run-{index}.jsonl"
        sim = Simulation(
            load_scenario(Path("scenarios/predicates-demo.yaml")), journal=Journal(path)
        )
        try:
            sim.start()
            sim.run_until(3_999_999_999)
            assert not [
                m
                for r in sim.kernel.records
                for m in project(r)["messages"]
                if m["schemaId"] == "demo.stationary.entered"
            ]
            sim.run_until(4_000_000_000)
            events = [
                m
                for r in sim.kernel.records
                for m in project(r)["messages"]
                if m["schemaId"] == "demo.stationary.entered"
            ]
            assert len(events) == 1
            assert events[0]["payload"]["to_ns"] == 4_000_000_000
            assert all(
                item["causes"]
                for record in sim.kernel.records
                for item in record["items"]
                if isinstance(item.get("message"), dict)
                and item["message"].get("fields", {}).get("schema_id")
                == "demo.stationary.entered"
            )
            sim.run_until(7_000_000_000)
            frames = sim.kernel.view().sample_frames("stationary")
            assert [f.frame.physical_ns for f in frames] == [
                0,
                1_000_000_000,
                3_000_000_000,
                4_000_000_000,
                5_000_000_000,
            ]
            assert all(f.frame.causes for f in frames)
            ref = sim.scenario.manifest.entities[0]
            for frame in frames:
                fact = sim.kernel.view().field(
                    (ref, "hu.actor.speed_mps"), frame.available, frame.frame.cut
                )
                assert isinstance(fact, Fact) and fact.version in frame.frame.causes
                observed = cast(
                    dict[str, Any], thaw(cast(FrozenValue, frame.frame.result))
                )["input"]["fields"]["original:owner-role:97"]["hu.actor.speed_mps"]
                assert observed == thaw(fact.value)
        finally:
            sim.close()
        restored = replay(path.read_bytes())
        assert not restored.incomplete
        assert [
            cast(dict[str, Any], thaw(cast(FrozenValue, f.frame.result)))
            for f in restored.view().sample_frames("stationary")
        ] == [
            cast(dict[str, Any], thaw(cast(FrozenValue, f.frame.result)))
            for f in frames
        ]
        logs.append(path.read_bytes())
    assert logs[0] == logs[1]


@pytest.mark.parametrize(
    "construct",
    [
        "js",
        "external_function",
        "within",
        "since",
        "relation_traversal",
        "convert_unit",
        "aggregate",
    ],
)
def test_unsupported_construct_rejected_at_load(construct: str) -> None:
    document = demo()
    cfg = document["engines"]["predicate"]["config"]
    cfg["definitions"][cfg["target"]]["expression"] = {"op": construct, "args": []}
    with pytest.raises(
        ScenarioError, match=f"hu.predicate.actor_stationary.*{construct}"
    ):
        load_scenario(document)


def test_definition_changes_need_no_pin_and_parameters_remain_required() -> None:
    document = demo()
    cfg = document["engines"]["predicate"]["config"]
    cfg["definitions"][cfg["target"]]["expression"] = {"literal": True}
    assert (
        load_scenario(document).engines["predicate"]["config"]["definitions"]
        == cfg["definitions"]
    )
    document = demo()
    document["engines"]["predicate"]["config"]["parameters"] = {}
    document["bindings"]["samples"][0]["parameters"] = {}
    with pytest.raises(ScenarioError, match="undeclared parameter"):
        Simulation(load_scenario(document))


def test_applicability_and_computed_duration_rejected_at_load() -> None:
    for construct in ("applicability", "computed_duration"):
        document = demo()
        cfg = document["engines"]["predicate"]["config"]
        target = cfg["definitions"][cfg["target"]]
        if construct == "applicability":
            target["applicability"] = {"literal": True}
        else:
            target["expression"] = {
                "op": "hold",
                "args": [
                    {"literal": True},
                    {"op": "add", "args": [{"literal": 1}, {"literal": 2}]},
                ],
            }
        with pytest.raises(
            ScenarioError, match=f"hu.predicate.actor_stationary.*{construct}"
        ):
            load_scenario(document)


def test_null_window_does_not_create_event_or_timer() -> None:
    document = demo()
    cfg = document["engines"]["predicate"]["config"]
    cfg["definitions"][cfg["target"]]["expression"] = {
        "op": "hold",
        "args": [{"literal": True}, {"literal": None}],
    }
    sim = Simulation(load_scenario(document))
    try:
        sim.start()
        sim.run_until(7_000_000_000)
        rows = [
            cast(dict[str, Any], thaw(cast(FrozenValue, f.frame.result)))
            for f in sim.kernel.view().sample_frames("stationary")
        ]
        assert rows and all(
            row["status"] == "required_input" and row["value"] is None for row in rows
        )
        assert not [
            m
            for r in sim.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"] == "demo.stationary.entered"
        ]
    finally:
        sim.close()


def test_original_dialect_rejects_non_native_leaves() -> None:
    for construct, expression in (
        ("time", {"time": True}),
        ("field_path", {"field": "hu.actor.speed_mps", "role": "subject", "path": [0]}),
        ("relation", {"relation": "demo.R", "sourceRole": "a", "targetRole": "b"}),
    ):
        document = demo()
        cfg = document["engines"]["predicate"]["config"]
        cfg["definitions"][cfg["target"]]["expression"] = expression
        with pytest.raises(
            ScenarioError, match=f"hu.predicate.actor_stationary.*{construct}"
        ):
            load_scenario(document)


def test_sample_source_mismatch_records_diagnostic_without_event() -> None:
    document = demo()
    # This is a real existing partition, but it did not produce the selected fact.
    document["bindings"]["samples"][0]["sources"]["original:owner-role:97"] = (
        "predicate"
    )
    sim = Simulation(load_scenario(document))
    try:
        sim.start()
        sim.run_until(7_000_000_000)
        rows = [
            cast(dict[str, Any], thaw(cast(FrozenValue, f.frame.result)))
            for f in sim.kernel.view().sample_frames("stationary")
        ]
        assert rows and all(
            row["status"] == "invalid_input" and row["value"] is None for row in rows
        )
        assert not [
            m
            for r in sim.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"] == "demo.stationary.entered"
        ]
    finally:
        sim.close()


class EdgeWriter(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=("hu.actor.speed_mps",),
                relation_produces=("demo.R",),
                lifecycle=True,
            ),
            policies=policies(("hu.actor.speed_mps",)),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)
        ctx.wake_at(1_000_000_000)

    def on_inputs(self, ctx: EngineContext) -> None:
        if ctx.now.ns == 1_000_000_000:
            ctx.relate(
                "edge-1",
                "demo.R",
                self.build.entities[0],
                self.build.entities[1],
                valid=Interval(ctx.now, None),
                acquired=Stamp("canonical", ctx.now.ns, 1, "canonical"),
            )
            ctx.wake_at(2_000_000_000)
        elif ctx.now.ns == 2_000_000_000:
            ctx.unrelate("edge-1")
            ctx.wake_at(3_000_000_000)
        elif ctx.now.ns == 3_000_000_000:
            # Removal itself dispatches a lifecycle notification back to its
            # owner at this instant; only the original timer removes the life.
            if ctx.view.lifecycle(self.build.entities[1]).removed is None:
                ctx.remove(self.build.entities[1])


def test_committed_relation_assertion_closure_and_causes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = demo()
    document["entities"].append(
        {
            "id": "observation-2",
            "type": "oo:ActorObservation",
            "facts": {"hu.actor.speed_mps": 0.0},
        }
    )
    document["registry"]["relations"] = [
        {
            "id": "demo.R",
            "source_type": "oo:ActorObservation",
            "target_type": "oo:ActorObservation",
            "targets_per_source": {"minimum": 0, "maximum": None},
            "sources_per_target": {"minimum": 0, "maximum": None},
            "identity_policy": "edge_id",
            "metadata": {},
        }
    ]
    document["bindings"]["relations"] = [
        {"writer": "observations", "relation": "demo.R", "type": "oo:ActorObservation"}
    ]
    cfg = document["engines"]["predicate"]["config"]
    target = "predicate:relation"
    cfg.update(
        target=target,
        definitions={
            target: {
                "schemaVersion": "aerograph.predicate-definition/v1",
                "kind": "predicate",
                "execution": {"nativeDialect": "expanded_typed_ast"},
                "expression": {
                    "relation": "demo.R",
                    "sourceRole": "source",
                    "targetRole": "target",
                },
            }
        },
        parameters={},
        relation_profiles={
            "demo.R": {"source": "observations", "clock": ["canonical", "canonical"]}
        },
        native_references=[
            {
                "path": str(
                    source_path(
                        "${AEROAGENTSIM_AEROGRAPH_ROOT}/semantic-directory/src/expanded_runtime.js"
                    )
                ),
            }
        ],
    )
    sample = document["bindings"]["samples"][0]
    sample.update(
        bindings={"source": "observation-1", "target": "observation-2"},
        sources={"source": "observations", "target": "observations"},
        clocks={
            "source": ["canonical", "canonical"],
            "target": ["canonical", "canonical"],
        },
        parameters={},
    )
    document["engines"]["observations"] = {"plugin": "edge-writer", "config": {}}
    original = EngineCatalog.build

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        return (
            EdgeWriter(build)
            if plugin == "edge-writer"
            else original(catalog, plugin, build)
        )

    monkeypatch.setattr(EngineCatalog, "build", factory)
    sim = Simulation(load_scenario(document))
    try:
        sim.start()
        sim.run_until(2_000_000_000)
        frames = sim.kernel.view().sample_frames("stationary")
        assert [
            cast(dict[str, Any], thaw(cast(FrozenValue, f.frame.result)))["value"]
            for f in frames
        ] == [False, True, False]
        assert all(f.frame.causes for f in frames)
        events = [
            m
            for r in sim.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"] == cfg["event"]
        ]
        assert len(events) == 1 and events[0]["payload"]["to_ns"] == 1_000_000_000
        sim.run_until(3_000_000_000)
        last = sim.kernel.view().sample_frames("stationary")[-1]
        result = cast(dict[str, Any], thaw(cast(FrozenValue, last.frame.result)))
        assert result["value"] is None and result["status"] == "required_input"
        assert any("not alive" in item["reason"] for item in result["diagnostics"])
    finally:
        sim.close()
