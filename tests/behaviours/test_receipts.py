"""Child completion consumes real typed command receipts, never invented success."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from aerokernel import EntityRef, Partition
from aerokernel.engine import Engine
from aerokernel.sdk import ContextEngine, EngineContext

from aeroagentsim.engines.behaviour import Behaviour, Child, Instance
from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import BUILTINS, EngineBuild, EngineCatalog
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader
from aeroagentsim.services.projector import project


class Acknowledge(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        super().__init__(Partition(build.id, build.id, commands=("example.ack",)))

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            command = ctx.remember(delivery, lambda value: cast(dict[str, Any], value))
            ctx.accept(command)
            ctx.execute(command)
            ctx.succeed(command, {"job": command.payload["job"]})


def test_any_receipt_requires_a_fresh_matching_child() -> None:
    engine = Behaviour.__new__(Behaviour)
    instance = Instance(
        EntityRef("test", "0", "instance", 0, "aas:BehaviourInstance"),
        {},
        "digest",
        {},
        "queue",
        {},
        {},
        "waiting",
    )
    instance.children = {
        "old": Child("old", status="succeeded"),
        "new": Child("new", status="rejected"),
    }
    context = cast(EngineContext, SimpleNamespace(dirty=[]))
    assert (
        engine._trigger(
            context,
            instance,
            {"receipt": ["old", "new"], "status": "succeeded", "policy": "any"},
            set(),
            [],
            [],
            [(instance.ref.id, "new", "rejected", None)],
        )
        is None
    )


def test_child_result_and_completion_policy_use_real_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = Path("scenarios/behaviours").resolve()
    doc: dict[str, Any] = yaml.load(
        (base / "minimal.yaml").read_text(), Loader=UniqueLoader
    )
    doc["bindings"]["commands"] = []
    record = {
        "type": "record",
        "members": {"job": {"type": "string"}},
        "required": ["job"],
        "extra": False,
    }
    doc["registry"]["messages"].append(
        {
            "id": "example.ack",
            "kind": "command",
            "schema": record,
            "result_schema": record,
        }
    )
    doc["engines"]["owner"] = {"plugin": "test-acknowledge", "config": {}}
    p = doc["behaviours"][0]
    p["conflicts"] = []
    chain = p["chains"]["queue"]
    chain["completion_policy"] = {
        "children": ["ack"],
        "statuses": ["succeeded"],
        "policy": "all",
    }
    chain["transitions"][0]["actions"] = [
        {
            "id": "ack",
            "kind": "command",
            "schema": "example.ack",
            "target": "owner",
            "payload": {"job": "task-1"},
            "deadline_ns": 10,
        }
    ]
    chain["transitions"][1]["on"] = {
        "receipt": "ack",
        "status": "succeeded",
        "result_matches": {"job": "task-1"},
    }
    original = EngineCatalog.factory

    def factory(catalog: EngineCatalog, plugin: str) -> Any:
        if plugin == "test-acknowledge":
            return lambda build: cast(Engine, Acknowledge(build))
        return original(catalog, plugin)

    monkeypatch.setattr(EngineCatalog, "factory", factory)
    monkeypatch.setitem(
        BUILTINS, "test-acknowledge", "aeroagentsim.engines.ingress_consumer"
    )
    sim = Simulation(load_scenario(doc, base=base))
    sim.start()
    sim.run_until(20)
    completions = [
        entry
        for record in sim.kernel.records
        for entry in project(record).get("chainInstances", [])
        if entry["lifecycle"] == "completed" and entry["op"] == "assert"
    ]
    assert len(completions) == 1
    child = completions[0]["children"]["ack"]
    assert child["commandId"] and child["status"] == "succeeded" and child["receipt"]
    action = sim.kernel.view().action(child["commandId"])
    assert action.status == "succeeded"
    assert action.history[-1]["result"] == {"job": "task-1"}
    assert completions[0]["trigger"]["result"] == {"job": "task-1"}
    assert not any(record["type"] == "fault" for record in sim.kernel.records)
    sim.close()
