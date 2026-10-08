"""Full examples: same-seed bytes and engine-free dynamic state replay."""

from __future__ import annotations

from pathlib import Path

import pytest
from aerokernel.journal import replay

from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario


@pytest.mark.parametrize("name", ["p1-slice", "p1-scale", "p1-spectrum"])
def test_all_examples_have_identical_bytes_and_replay_dynamic_slots(
    name: str, tmp_path: Path
) -> None:
    scenario = load_scenario(Path("scenarios") / f"{name}.yaml")
    with RunSession(scenario, tmp_path / "first") as first:
        first.run()
    with RunSession(scenario, tmp_path / "second") as second:
        second.run()
    data = first.simulation.kernel.journal.bytes
    assert data == second.simulation.kernel.journal.bytes
    recovered = replay(data)
    live = first.simulation.kernel.view()
    retained = recovered.view()
    assert not recovered.incomplete
    assert live.cut == retained.cut
    # Include generated observation fields/generations, not only bootstrap facts.
    for key in live._store.facts:
        assert live.field(key, live.instant) == retained.field(key, retained.instant)
    for relation in scenario.registry.relations:
        assert live.relations(relation.id, live.instant) == retained.relations(
            relation.id, retained.instant
        )
    for spec in scenario.manifest.samples:
        assert live.sample_frames(spec.context_id) == retained.sample_frames(
            spec.context_id
        )
    assert (
        first.storage.metadata()["status"]
        == second.storage.metadata()["status"]
        == "completed"
    )
