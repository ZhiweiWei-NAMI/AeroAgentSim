"""A timer for one subject must not visit an unrelated chain's guards."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from aerokernel.sdk import EngineContext
from aerokernel.values import canonical_json

from aeroagentsim.behaviours.dispatch import InstanceIndex
from aeroagentsim.behaviours.evaluation import Evaluator, Truth
from aeroagentsim.engines.behaviour import Behaviour, Instance
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader


def test_dispatch_is_subject_indexed_and_guards_retain_read_evidence(
    monkeypatch: Any,
) -> None:
    base = Path("scenarios/behaviours").resolve()
    doc = yaml.load((base / "minimal.yaml").read_text(), Loader=UniqueLoader)
    doc["bindings"]["commands"] = []
    doc["entities"].append(
        {
            "id": "task-2",
            "type": "example:Task",
            "facts": {"example.task.phase": "queued"},
        }
    )
    package = doc["behaviours"][0]
    package["chains"]["later"] = copy.deepcopy(package["chains"]["queue"])
    package["chains"]["later"]["transitions"][0]["actions"][1]["duration_ns"] = 20
    package["bindings"][0]["match"]["task"]["entity"] = "task-1"
    later = copy.deepcopy(package["bindings"][0])
    later.update(id="later", chain="later", match={"task": {"entity": "task-2"}})
    package["bindings"].append(later)
    visits: list[str] = []
    evaluations: set[tuple[int, str]] = set()
    trigger = Behaviour._trigger
    evaluate = Evaluator.run

    def counted_trigger(
        self: Behaviour, ctx: EngineContext, m: Instance, *args: Any
    ) -> Any:
        visits.append(m.roles["task"].id)
        return trigger(self, ctx, m, *args)

    def counted_evaluation(
        self: Evaluator, ctx: EngineContext, identity: str, *args: Any, **kwargs: Any
    ) -> Truth:
        key = (ctx.view.cut.index, identity)
        assert key not in evaluations, "one evaluation per immutable invocation cut"
        evaluations.add(key)
        return evaluate(self, ctx, identity, *args, **kwargs)

    monkeypatch.setattr(Behaviour, "_trigger", counted_trigger)
    monkeypatch.setattr(Evaluator, "run", counted_evaluation)
    simulation = Simulation(load_scenario(doc, base=base), provenance="full")
    try:
        simulation.start()
        visits.clear()
        simulation.run_until(10)
        assert visits and set(visits) == {"task-1"}
        # Assignment used a cached ready guard; its fact-version read remains in
        # the committed write's causes along with the recorded evaluation ref.
        writes = [
            item
            for record in simulation.kernel.iter_records()
            for item in record.get("items", [])
            if item.get("proposal", {}).get("$type") == "FactWrite"
            and item["proposal"]["fields"]["key"][1] == "example.task.phase"
            and item["proposal"]["fields"]["value"] == "assigned"
        ]
        assert len(writes) == 2
        assert all(len(item["causes"]) >= 2 for item in writes)
    finally:
        simulation.close()


def test_correlated_events_dispatch_only_the_named_subject() -> None:
    index = InstanceIndex()
    alpha, bravo = {"$ref": {"id": "alpha"}}, {"$ref": {"id": "bravo"}}
    for name, subject in (("alpha-chain", alpha), ("bravo-chain", bravo)):
        index.replace(
            name, {"event_subjects": [("award", "actor", canonical_json(subject))]}
        )
    assert index.event_instances("award", {"actor": bravo}) == {"bravo-chain"}
    assert index.event_instances("award", {}) == set()
    index.replace("bravo-chain", {})
    assert index.event_instances("award", {"actor": bravo}) == set()
