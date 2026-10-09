"""Real kernel chains: binding, causes, edge semantics and recorded replay."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from aerokernel import CommandRequest, Instant, Stamp
from aerokernel.journal import replay
from aerokernel.state import Fact
from hypothesis import given, settings
from hypothesis import strategies as st

from aeroagentsim.behaviours.evaluation import Evaluator
from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader
from aeroagentsim.services.projector import project

BASE = Path("scenarios/behaviours").resolve()


def document(*, injected: bool = False) -> dict[str, Any]:
    doc: dict[str, Any] = yaml.load(
        (BASE / "minimal.yaml").read_text(), Loader=UniqueLoader
    )
    if not injected:
        doc["bindings"]["commands"] = []
    return doc


def value(sim: Simulation) -> Any:
    view = sim.kernel.view()
    return view.field(
        (sim.scenario.manifest.entities[0], "example.task.phase"), view.instant
    ).value  # type: ignore[union-attr]


def emitted(sim: Simulation, schema: str) -> list[dict[str, Any]]:
    return [
        m["payload"]
        for rec in sim.kernel.records
        for m in project(rec)["messages"]
        if m["schemaId"] == schema
    ]


def test_nonspatial_assignment_and_completion() -> None:
    sim = Simulation(load_scenario(document(), base=BASE))
    sim.start()
    assert value(sim) == "assigned"
    sim.run_until(10)
    assert value(sim) == "completed"
    assert len(emitted(sim, "aas.behaviour.created")) == 2  # assert + recorded closure
    assert emitted(sim, "aas.behaviour.completed")[-1]["state"] == "done"
    assert not any(
        f["fieldId"].endswith("position")
        for r in sim.kernel.records
        for f in project(r)["facts"]
    )
    sim.close()


def test_template_timer_activates_at_declared_delay() -> None:
    doc = document()
    doc["behaviours"][0]["chains"]["queue"]["trigger"] = {
        "timer": "release",
        "after_ns": 3,
    }
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    assert value(sim) == "queued"
    sim.run_until(2)
    assert value(sim) == "queued"
    sim.run_until(3)
    assert value(sim) == "assigned"
    sim.run_until(13)
    assert value(sim) == "completed"
    sim.close()


def test_remove_entity_retains_recorded_instance_history() -> None:
    doc = document()
    package = doc["behaviours"][0]
    package["conflicts"] = []
    chain = package["chains"]["queue"]
    chain["transitions"] = [
        {
            "id": "retire",
            "from": "waiting",
            "on": {"instance": "activated"},
            "to": "done",
            "actions": [
                {"id": "remove", "kind": "remove_entity", "entity": {"$role": "task"}}
            ],
        }
    ]
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    task = sim.scenario.manifest.entities[0]
    assert not sim.kernel.view()._store.alive(
        task, sim.kernel.view().cut, sim.kernel.view().instant
    )
    assert emitted(sim, "aas.behaviour.completed")[-1]["roles"]["task"] == {
        "$ref": task.to_data()
    }
    sim.close()


def test_selector_unbind_cancels_outstanding_state_timer() -> None:
    doc = document()
    binding = doc["behaviours"][0]["bindings"][0]
    binding["match"]["task"].update({"field": "example.task.phase", "equals": "queued"})
    binding["on_unbind"] = "close_after_cleanup"
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    sim.run_until(20)
    assert value(sim) == "assigned"
    assert emitted(sim, "aas.behaviour.canceled")[-1]["state"] == "assigned"
    assert not emitted(sim, "aas.behaviour.completed")
    sim.close()


def test_binding_waits_for_real_variable_producer_then_continues() -> None:
    doc = document()
    doc["registry"]["fields"].append(
        {
            "id": "example.task.payload",
            "type": "example:Task",
            "schema": {"type": "string", "enum": ["completed"]},
            "metadata": {},
        }
    )
    doc["bindings"]["rules"][0]["fields"].append("example.task.payload")
    doc["engines"]["behaviour"]["config"]["produces"].append("example.task.payload")
    package = doc["behaviours"][0]
    package["bindings"][0]["variables"] = {
        "destination": {"field": "example.task.payload", "role": "task"}
    }
    package["chains"]["queue"]["transitions"][1]["actions"][0]["value"] = {
        "$variable": "destination"
    }
    package["chains"]["supply"] = {
        "roles": {"task": "example:Task"},
        "trigger": {"timer": "release", "after_ns": 3},
        "initial": "waiting",
        "states": ["waiting", "done"],
        "terminal": ["done"],
        "transitions": [
            {
                "id": "supply",
                "from": "waiting",
                "on": {"instance": "activated"},
                "to": "done",
                "actions": [
                    {
                        "id": "payload",
                        "kind": "set",
                        "entity": {"$role": "task"},
                        "field": "example.task.payload",
                        "value": "completed",
                    }
                ],
            }
        ],
    }
    package["bindings"].append(
        {
            "id": "supply",
            "chain": "supply",
            "match": {"task": {"is_a": "example:Task"}},
            "multiplicity": "once_per_entity",
            "on_unbind": "retain_until_terminal",
        }
    )
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    assert value(sim) == "queued"
    waiting = [
        record
        for record in emitted(sim, "aas.behaviour.created")
        if record["templateId"] == "queue" and record["op"] == "assert"
    ]
    assert waiting[0]["status"] == "waiting_inputs"
    assert waiting[0]["requiredInputs"][0]["field"] == "example.task.payload"
    assert waiting[0]["requiredInputs"][0]["producer"] == "behaviour"
    sim.run_until(3)
    assert value(sim) == "assigned"
    sim.run_until(13)
    assert value(sim) == "completed"
    sim.close()


def test_relation_capacity_arbitrates_candidates_without_invalidating_wave() -> None:
    doc = document()
    doc["registry"]["relations"] = [
        {
            "id": "example.assigned",
            "source_type": "example:Task",
            "target_type": "example:Task",
            "targets_per_source": {"minimum": 0, "maximum": 1},
            "sources_per_target": {"minimum": 0, "maximum": 1},
            "identity_policy": "edge_id",
            "metadata": {},
        }
    ]
    doc["bindings"]["relations"] = [
        {"writer": "behaviour", "relation": "example.assigned", "type": "example:Task"}
    ]
    doc["entities"] += [
        {"id": name, "type": "example:Task", "facts": {"example.task.phase": "queued"}}
        for name in ("worker-a", "worker-b")
    ]
    p = doc["behaviours"][0]
    p["conflicts"] = []
    chain = p["chains"]["queue"]
    chain["roles"]["worker"] = "example:Task"
    chain["transitions"] = [
        {
            "id": "assign",
            "from": "waiting",
            "on": {"instance": "activated"},
            "to": "done",
            "actions": [
                {
                    "id": "assign-edge",
                    "kind": "assert_relation",
                    "edge_id": {"$variable": "edge"},
                    "relation": "example.assigned",
                    "source": {"$role": "task"},
                    "target": {"$role": "worker"},
                }
            ],
        }
    ]
    p["bindings"] = [
        {
            "id": name,
            "chain": "queue",
            "match": {"task": {"entity": "task-1"}, "worker": {"entity": name}},
            "variables": {"edge": "assignment-" + name},
            "multiplicity": "once_per_entity",
            "on_unbind": "retain_until_terminal",
        }
        for name in ("worker-b", "worker-a")
    ]
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    view = sim.kernel.view()
    edges = view.relations("example.assigned", view.instant)
    assert len(edges) == 1
    assert edges[0].target.id == "worker-a"
    assert len(emitted(sim, "aas.behaviour.action_conflict")) == 1
    assert not any(r["type"] == "fault" for r in sim.kernel.records)
    sim.close()


def test_relation_predicate_and_once_per_assertion_binding() -> None:
    doc = document()
    doc["registry"]["relations"] = [
        {
            "id": "example.assigned",
            "source_type": "example:Task",
            "target_type": "example:Task",
            "targets_per_source": {"minimum": 0, "maximum": 1},
            "sources_per_target": {"minimum": 0, "maximum": 1},
            "identity_policy": "edge_id",
            "metadata": {},
        }
    ]
    doc["bindings"]["relations"] = [
        {"writer": "behaviour", "relation": "example.assigned", "type": "example:Task"}
    ]
    doc["entities"].append(
        {
            "id": "worker",
            "type": "example:Task",
            "facts": {"example.task.phase": "queued"},
        }
    )
    package = doc["behaviours"][0]
    package["conflicts"] = []
    package["chains"]["queue"]["roles"]["worker"] = "example:Task"
    package["predicates"]["assigned"] = {
        "profile": "committed_reactive/v1",
        "roles": {"task": "example:Task", "worker": "example:Task"},
        "expression": {
            "relation": "example.assigned",
            "sourceRole": "task",
            "targetRole": "worker",
        },
    }
    package["chains"]["queue"]["trigger"] = {"predicate": "assigned", "edge": "while"}
    binding = package["bindings"][0]
    binding["multiplicity"] = "once_per_relation"
    binding["match"] = {
        "task": {"entity": "task-1"},
        "worker": {
            "relation": "example.assigned",
            "source_role": "task",
            "target_role": "worker",
        },
    }

    def ref(identity: str) -> dict[str, Any]:
        return {
            "$ref": {
                "run_id": doc["id"],
                "epoch": "0",
                "id": identity,
                "generation": 0,
                "type_id": "example:Task",
            }
        }

    package["bootstrap_relations"] = {
        "assertions": [
            {
                "id": "assignment",
                "relation": "example.assigned",
                "source": ref("task-1"),
                "target": ref("worker"),
            }
        ]
    }
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    sim.run_until(20)
    assert (
        len(
            [
                event
                for event in emitted(sim, "aas.behaviour.created")
                if event["op"] == "assert"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                event
                for event in emitted(sim, "aas.behaviour.completed")
                if event["op"] == "assert"
            ]
        )
        == 1
    )
    assert any(
        entry["predicateId"] == "assigned" and entry["value"] is True
        for record in sim.kernel.records
        for entry in project(record).get("predicateTruth", [])
    )
    sim.close()


def test_injection_interrupts_and_conflict_uses_committed_edge() -> None:
    sim = Simulation(load_scenario(document(injected=True), base=BASE))
    sim.start()
    sim.run_until(20)
    assert value(sim) == "interrupted"
    assert len(emitted(sim, "example.conflict")) == 1
    records = [
        r
        for r in sim.kernel.records
        if any(m["schemaId"] == "example.conflict" for m in project(r)["messages"])
    ]
    assert records[0]["instant"]["fields"]["ns"] == 5
    assert all(
        item.get("causes")
        for r in records
        for item in r["items"]
        if "proposal" in item
        and item.get("message", {}).get("fields", {}).get("schema_id")
        == "example.conflict"
    )
    sim.close()


def test_zero_delay_is_next_microstep() -> None:
    doc = document()
    doc["behaviours"][0]["chains"]["queue"]["transitions"][0]["actions"][1][
        "duration_ns"
    ] = 0
    sim = Simulation(load_scenario(doc, base=BASE))
    view = sim.start()
    assert view.instant.ns == 0
    assert value(sim) == "completed"
    assigns = [
        p for p in emitted(sim, "aas.behaviour.transitioned") if p["op"] == "assert"
    ]
    assert int(assigns[-1]["validFrom"]["ns"]) == 0
    assert assigns[-1]["validFrom"]["microstep"] > assigns[0]["validFrom"]["microstep"]
    sim.close()


def test_live_named_ingress_admission_and_execution_are_separate() -> None:
    doc = document()
    doc["ingress_streams"][0]["initial_watermark_ns"] = 0
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    request = CommandRequest(
        "aas.runtime.inject_event",
        "behaviour",
        Instant(5),
        {"injection_point": "interrupt", "payload": {"reason": "live"}},
        idempotency_key="once",
    )
    receipt = sim.submit_live(
        "behaviour",
        request,
        Stamp("canonical", 5, 1, "canonical"),
        stream_id="operator",
    )
    assert value(sim) == "assigned"
    assert (
        sim.submit_live(
            "behaviour",
            request,
            Stamp("canonical", 5, 1, "canonical"),
            stream_id="operator",
        )
        == receipt
    )
    sim.advance_watermark(20, stream_id="operator")
    sim.run_until(20)
    assert value(sim) == "interrupted"
    assert len(emitted(sim, "example.interrupt")) == 1
    assert sim.kernel.view().action(receipt.command_id).status == "succeeded"  # type: ignore[arg-type]
    sim.close()


def test_replay_reconstructs_instances_without_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = RunSession(
        load_scenario(document(injected=True), base=BASE), tmp_path / "run"
    )
    session.run()
    before = [project(r) for r in session.simulation.kernel.records]

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("replay evaluated a predicate")

    monkeypatch.setattr(Evaluator, "run", forbidden)
    restored = replay(session.storage.directory / "journal.jsonl")
    assert [project(r) for r in restored.records] == before
    assert (session.storage.directory / "behaviour.ir.json").is_file()
    state = restored.view()
    task = session.scenario.manifest.entities[0]
    fact = state.field((task, "example.task.phase"), state.instant)
    assert isinstance(fact, Fact)
    assert fact.value == "interrupted"


@settings(max_examples=3, deadline=None)
@given(at_ns=st.integers(min_value=1, max_value=9))
def test_replay_equality_for_injection_availability(at_ns: int) -> None:
    doc = document(injected=True)
    doc["bindings"]["commands"][0]["at_ns"] = at_ns
    with tempfile.TemporaryDirectory(
        prefix="behaviour-replay-", dir="/tmp/aas-q/a"
    ) as scratch:
        session = RunSession(load_scenario(doc, base=BASE), Path(scratch) / "run")
        session.run()
        recorded = [project(record) for record in session.simulation.kernel.records]
        with pytest.MonkeyPatch.context() as patch:

            def forbidden(*args: Any, **kwargs: Any) -> Any:
                raise AssertionError("replay called an evaluator")

            patch.setattr(Evaluator, "run", forbidden)
            restored = replay(session.storage.directory / "journal.jsonl")
        assert [project(record) for record in restored.records] == recorded


@settings(max_examples=6, deadline=None)
@given(st.permutations(["left", "right", "other"]))
def test_independent_binding_permutation_preserves_trace(order: list[str]) -> None:
    doc = document()
    package = doc["behaviours"][0]
    package["bindings"] = [
        {
            **copy.deepcopy(package["bindings"][0]),
            "id": name,
            "match": {"task": {"is_a": "example:Task", "entity": name}},
        }
        for name in order
    ]
    doc["entities"] = [
        {"id": name, "type": "example:Task", "facts": {"example.task.phase": "queued"}}
        for name in sorted(order)
    ]

    def run(candidate: dict[str, Any]) -> list[dict[str, Any]]:
        sim = Simulation(load_scenario(candidate, base=BASE))
        sim.start()
        sim.run_until(20)
        result = [
            {"schema": m["schemaId"], "payload": m["payload"]}
            for rec in sim.kernel.records
            for m in project(rec)["messages"]
            if m["schemaId"].startswith("aas.behaviour")
        ]
        sim.close()
        return result

    canonical = copy.deepcopy(doc)
    canonical["entities"].sort(key=lambda item: item["id"])
    canonical["behaviours"][0]["bindings"].sort(key=lambda item: item["id"])
    assert run(doc) == run(canonical)


def test_chain_deadline_is_real_expiry_not_child_failure() -> None:
    doc = document()
    chain = doc["behaviours"][0]["chains"]["queue"]
    chain["deadline_ns"] = 7
    chain["transitions"].append(
        {
            "id": "deadline",
            "from": "assigned",
            "on": {"deadline": "instance"},
            "to": "interrupted",
            "actions": [
                {
                    "id": "deadline-phase",
                    "kind": "set",
                    "entity": {"$role": "task"},
                    "field": "example.task.phase",
                    "value": "interrupted",
                }
            ],
        }
    )
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    sim.run_until(20)
    assert value(sim) == "interrupted"
    assert (
        emitted(sim, "aas.behaviour.completed")[-1]["trigger"]["deadline"] == "instance"
    )
    sim.close()


def test_created_entity_automatically_binds_after_identity_and_fact_waves() -> None:
    doc = document()
    p = doc["behaviours"][0]
    p["chains"]["spawn"] = {
        "roles": {"task": "example:Task"},
        "trigger": {"lifecycle": "created"},
        "initial": "initial",
        "terminal": ["done"],
        "states": ["initial", "done"],
        "transitions": [
            {
                "id": "create",
                "from": "initial",
                "on": {"instance": "activated"},
                "to": "done",
                "actions": [
                    {
                        "id": "child-task",
                        "kind": "create_entity",
                        "entity": "task-2",
                        "type": "example:Task",
                        "facts": {"example.task.phase": "queued"},
                    }
                ],
            }
        ],
    }
    p["bindings"].append(
        {
            "id": "spawn",
            "chain": "spawn",
            "match": {"task": {"entity": "task-1"}},
            "variables": {},
            "multiplicity": "once_per_entity",
            "on_unbind": "retain_until_terminal",
        }
    )
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    sim.run_until(20)
    created = [
        row
        for row in emitted(sim, "aas.behaviour.created")
        if row["op"] == "assert" and row["templateId"] == "queue"
    ]
    assert {row["roles"]["task"]["$ref"]["id"] for row in created} == {
        "task-1",
        "task-2",
    }
    assert (
        len(
            [
                row
                for row in emitted(sim, "aas.behaviour.completed")
                if row["templateId"] == "queue"
            ]
        )
        == 2
    )
    sim.close()


def test_transition_budget_retains_actual_prefix(tmp_path: Path) -> None:
    doc = document()
    p = doc["behaviours"][0]
    p["budgets"]["max_transitions_per_instance_per_ns"] = 2
    chain = p["chains"]["queue"]
    chain["terminal"] = []
    chain["transitions"] = [
        {
            "id": "initial",
            "from": "waiting",
            "on": {"instance": "activated"},
            "to": "assigned",
            "actions": [],
        },
        {
            "id": "loop",
            "from": "assigned",
            "on": {"instance": "continued"},
            "to": "assigned",
            "actions": [],
        },
    ]
    session = RunSession(load_scenario(doc, base=BASE), tmp_path / "fault")
    with pytest.raises(RuntimeError, match="transition budget"):
        session.start()
    assert session.storage.metadata()["status"] == "faulted"
    assert session.simulation.kernel.records[-1]["type"] == "fault"
    assert emitted(session.simulation, "aas.behaviour.transitioned")
