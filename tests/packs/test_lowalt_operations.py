"""Interrupted physical recalls and committed-snapshot optimization equivalence."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aerokernel import Instant
from aerokernel.sdk import EngineContext

from aeroagentsim.packs.lowalt.core import SnapshotIndex, refs, snapshot
from aeroagentsim.packs.lowalt.tasks import LowaltTasks
from aeroagentsim.packs.lowalt.template import parcel_document
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def _run(document: dict[str, Any], seconds: int) -> list[dict[str, Any]]:
    sim = Simulation(load_scenario(document, base=BASE))
    try:
        sim.start()
        sim.run_until(seconds * 10**9)
        return [project(record) for record in sim.kernel.iter_records()]
    finally:
        sim.close()


def _messages(commits: list[dict[str, Any]], schema: str) -> list[dict[str, Any]]:
    return [
        m for record in commits for m in record["messages"] if m["schemaId"] == schema
    ]


def test_avoidance_resumes_charging_trip_and_finishes_physical_recall() -> None:
    document = parcel_document(fleet_size=1)
    document["engines"]["tasks"]["config"]["generators"][0]["arrivals"]["start_ns"] = (
        100 * 10**9
    )
    document["generators"]["fleet"]["starts"] = [[-240.0, -220.0, 140.0]]
    document["generators"]["fleet"]["facts"].update(
        {"lowalt.soc": 0.1, "lowalt.motion_energy_j": 24000.0}
    )
    healthy = copy.deepcopy(document["generators"]["fleet"]["facts"])
    healthy.update(
        {
            "lowalt.position": [-239.0, -220.0, 100.0],
            "lowalt.soc": 1.0,
            "lowalt.motion_energy_j": 240000.0,
        }
    )
    document["entities"].append({"id": "alpha", "type": "lowalt:UAV", "facts": healthy})
    # The hovering neighbor leaves after the conflict, at a real rest boundary.
    # Avoidance must then resume charging, not leave a permanently open recall.
    document["bindings"]["commands"] = [
        {
            "schema": "lowalt.motion.move_to",
            "target": "motion",
            "at_ns": 18 * 10**9,
            "payload": {"entity": "alpha", "target": [-180.0, -220.0, 100.0]},
        }
    ]
    commits = _run(document, 100)
    assert _messages(commits, "lowalt.airspace.conflict")
    assert _messages(commits, "lowalt.energy.charging_started")
    assert _messages(commits, "lowalt.energy.charging_finished")
    recall_flags = [
        f["value"]
        for record in commits
        for f in record["facts"]
        if f["entity"]["id"] == "drone-1"
        and f["fieldId"] == "lowalt.returning_to_charge"
    ]
    assert recall_flags == [False, True, False]


def test_snapshot_index_preserves_value_and_every_committed_read_in_order() -> None:
    scenario = load_scenario(parcel_document(fleet_size=3), base=BASE)
    build = EngineBuild(
        "tasks",
        scenario.engines["tasks"]["config"],
        scenario.registry,
        scenario.manifest,
        scenario.manifest.entities,
        scenario.initial,
    )
    reads: list[tuple[str, str]] = []
    wind = scenario.engines["environment"]["config"]["profiles"]["weather"][
        "lowalt.wind"
    ]["value"]

    def get(ref: Any, field: str) -> Any:
        reads.append((ref.id, field))
        # Environment bootstrap publishes its authored profile, not an initial
        # entity fact. Supply that same committed observation to both paths.
        return wind if field == "lowalt.wind" else scenario.initial[ref.id][field]

    ctx = cast(EngineContext, SimpleNamespace(now=Instant(0), get=get))
    expected = snapshot(ctx, build)
    original_reads = reads[:]
    reads.clear()
    index = SnapshotIndex(build)
    assert snapshot(ctx, build, index=index) == expected
    assert reads == original_reads
    assert all(index.select(kind) == refs(build, kind) for kind in index.groups)
    with pytest.raises(ValueError, match="different engine build"):
        snapshot(ctx, replace(build), index=index)


def test_callback_snapshot_reuse_preserves_entire_committed_journal(
    monkeypatch: Any,
) -> None:
    document = parcel_document(fleet_size=2)
    cached = _run(document, 30)

    def uncached(self: LowaltTasks, ctx: EngineContext) -> Any:
        return snapshot(ctx, self.build, index=self.snapshot_index)

    monkeypatch.setattr(LowaltTasks, "_snapshot", uncached)
    assert _run(document, 30) == cached
