"""Reviewer counterexamples: interval fidelity and exact integer payloads."""

from __future__ import annotations

from typing import Any

from aerokernel import EntityRef, Instant, Interval, Stamp
from aerokernel.codec import encode
from aerokernel.ids import ItemRef
from aerokernel.operations import (
    AssertEdge,
    CancelEdge,
    CloseEdge,
    FactWrite,
    RetractFact,
)
from aerokernel.relations import Edge
from aerokernel.state import Fact

from aeroagentsim.services.projector import project


def test_h2_project_future_finite_fact_and_scoped_retraction() -> None:
    ref = EntityRef("run", "0", "restriction", 0, "regulation")
    valid = Interval(Instant(10_000_000), Instant(15_000_000))
    acquired = Stamp("source", 7, 3, "mapping")
    op = FactWrite((ref, "status"), "active", acquired, valid)
    fact = Fact(
        op.key, "active", acquired, 2, Instant(0, 1), valid, "authority", ItemRef(1, 0)
    )
    record: dict[str, Any] = {
        "index": 1,
        "instant": encode(Instant(0, 1)),
        "items": [
            {"proposal": encode(op), "partition": "authority", "fact": encode(fact)}
        ],
    }
    projected = project(record)["facts"][0]
    assert projected["validTo"] == {"ns": "15000000", "microstep": 0}
    assert projected["available"] == {"ns": "0", "microstep": 1}
    assert projected["acquired"] == {
        "clockId": "source",
        "mappingId": "mapping",
        "numerator": "7",
        "denominator": "3",
    }
    assert projected["version"] == {"journalIndex": 1, "itemOrdinal": 0}
    record["items"] = [
        {
            "proposal": encode(RetractFact(op.key, valid, "withdrawn")),
            "partition": "authority",
        }
    ]
    retracted = project(record)["retracted"][0]
    assert retracted["validFrom"]["ns"] == "10000000"
    assert retracted["validTo"]["ns"] == "15000000"


def test_h10_project_integer_value_without_precision_loss() -> None:
    ref = EntityRef("run", "0", "metric", 0, "record")
    op = FactWrite(
        (ref, "counter"),
        {"count": 2**63 + 7, "normal": 7},
        Stamp("canonical", 0, 1, "canonical"),
        Interval(Instant(0), None),
    )
    record = {
        "index": 1,
        "instant": encode(Instant(0)),
        "items": [{"proposal": encode(op), "partition": "counter"}],
    }
    value = project(record)["facts"][0]["value"]
    assert value == {"count": {"$integer": "9223372036854775815"}, "normal": 7}


def test_temporal_edge_versions_preserve_closure_and_cancellation() -> None:
    source = EntityRef("run", "0", "observation", 0, "record")
    target = EntityRef("run", "0", "signal", 0, "signal")
    acquired = Stamp("sensor", 7, 1, "sensor-map")
    interval = Interval(Instant(10), Instant(15))
    assertion = AssertEdge("edge", "subject", source, target, interval, acquired)
    for index, (operation, valid, expected) in enumerate(
        [
            (assertion, interval, "assert"),
            (CloseEdge("edge"), Interval(Instant(10), Instant(12)), "close"),
            (CancelEdge("future"), None, "cancel"),
        ],
        1,
    ):
        edge = Edge(
            "edge",
            "subject",
            source,
            target,
            valid,
            acquired,
            Instant(index),
            "records",
            ItemRef(index, 0),
        )
        record = {
            "index": index,
            "instant": encode(Instant(index)),
            "items": [
                {
                    "proposal": encode(operation),
                    "edge": encode(edge),
                    "partition": "records",
                }
            ],
        }
        projected = project(record)["edges"][0]
        assert projected["op"] == expected
        assert projected["validFrom"] == (
            None if valid is None else {"ns": "10", "microstep": 0}
        )
        assert projected["validTo"] == (
            None if valid is None else {"ns": str(valid.end.ns), "microstep": 0}
        )  # type: ignore[union-attr]


def test_lossless_tags_escape_user_records_and_preserve_large_floats() -> None:
    from aeroagentsim.services.projector import lossless

    assert lossless({"$integer": "authored string"}) == {
        "$record": {"$integer": "authored string"}
    }
    assert lossless({"$record": {"count": 2**63}}) == {
        "$record": {"$record": {"count": {"$integer": str(2**63)}}}
    }
    assert lossless(1e20) == {"$number": "1e+20"}


def test_future_finite_regulatory_fact_matches_live_and_replayed_kernel(
    monkeypatch: object,
) -> None:
    """The [10,15) ms UI counterexample is an accepted real kernel trace."""
    from pathlib import Path

    import pytest
    from aerokernel import Partition
    from aerokernel.journal import replay
    from aerokernel.sdk import ContextEngine, EngineContext
    from aerokernel.state import Absent

    from aeroagentsim.engines.common import bootstrap_owned, policies
    from aeroagentsim.platform import Simulation
    from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
    from aeroagentsim.scenario import load_scenario

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    document = load_scenario(Path("scenarios/p1-spectrum.yaml")).document
    field = "review.restriction_state"
    document["entities"] = [{"id": "restriction", "type": "oo:Regulation", "facts": {}}]
    document["bindings"] = {
        "rules": [{"writer": "authority", "type": "oo:Regulation", "fields": [field]}],
        "lifecycle": [{"controller": "authority", "type": "oo:Regulation"}],
    }
    document["engines"] = {
        "authority": {"plugin": "finite-regulatory-test", "config": {}}
    }

    class Authority(ContextEngine):
        def __init__(self, build: EngineBuild) -> None:
            self.build = build
            super().__init__(
                Partition(build.id, build.id, produces=(field,), lifecycle=True),
                policies=policies((field,)),
            )

        def bootstrap(self, ctx: EngineContext) -> None:
            bootstrap_owned(ctx, self.build)
            ctx.set(
                self.build.entities[0],
                field,
                "active",
                valid=Interval(Instant(10_000_000), Instant(15_000_000)),
            )

    original = EngineCatalog.build

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        return (
            Authority(build)
            if plugin == "finite-regulatory-test"
            else original(catalog, plugin, build)
        )

    monkeypatch.setattr(EngineCatalog, "build", factory)
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        live = simulation.run_until(20_000_000)
        recovered = replay(simulation.kernel.journal.bytes).view()
        rows = [
            f
            for r in simulation.kernel.records
            for f in project(r)["facts"]
            if f["fieldId"] == field
        ]
        assert len(rows) == 1
        row = rows[0]
        ref = simulation.scenario.manifest.entities[0]
        for ns, expected in [
            (1_000_000, False),
            (10_000_000, True),
            (15_000_000, False),
            (20_000_000, False),
        ]:
            displayed = int(row["validFrom"]["ns"]) <= ns < int(row["validTo"]["ns"])
            assert displayed is expected
            assert isinstance(live.field((ref, field), Instant(ns)), Fact) is expected
            assert (
                isinstance(recovered.field((ref, field), Instant(ns)), Absent)
                is not expected
            )
    finally:
        simulation.close()
