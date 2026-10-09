"""Typed outputs, concurrent call ordering, budgets and zero-I/O graph replay."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from copy import deepcopy
from typing import Annotated, Any, TypedDict, cast

import pytest
from aerokernel import (
    BindingManifest,
    CommandRequest,
    EntityRef,
    Instant,
    Kernel,
    MemoryRegistry,
    Partition,
)
from aerokernel.binding import BindingRule, LifecycleRule
from aerokernel.engine import Engine
from aerokernel.journal import replay
from aerokernel.registry import FieldDescriptor, MessageDescriptor, TypeDescriptor
from aerokernel.sdk import ContextEngine, EngineContext
from langgraph.graph import END, START, StateGraph

from aeroagentsim.agents.langgraph import (
    LangGraphDecision,
    journal_decisions,
    record_descriptor,
    replay_graph,
)
from aeroagentsim.agents.langgraph_client import JournalClient, ScriptedProvider
from aeroagentsim.agents.provider import OpenAIProvider, ProviderError
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.services.projector import project

VALUE = {
    "type": "record",
    "members": {"value": {"type": "integer"}},
    "required": ["value"],
    "extra": False,
}


def merge(left: list[int], right: list[int]) -> list[int]:
    return sorted(left + right)


class State(TypedDict):
    observation: dict[str, Any]
    trigger: dict[str, Any]
    values: Annotated[list[int], merge]
    outputs: list[dict[str, Any]]


def toy_graph(
    client: JournalClient, options: dict[str, Any]
) -> StateGraph[State, None, State, State]:
    graph = StateGraph(State)

    async def node(key: str) -> dict[str, Any]:
        def validate(value: dict[str, Any]) -> None:
            if set(value) != {"value"} or type(value["value"]) is not int:
                raise ValueError("integer value required")

        if options.get("sleep"):
            await asyncio.sleep(options["sleep"])
        value = await client.ask_json(
            key, "Return JSON integer value", {"key": key}, validate
        )
        return {"values": [value["value"]]}

    async def alpha(state: State) -> dict[str, Any]:
        return await node("alpha")

    async def bravo(state: State) -> dict[str, Any]:
        return await node("bravo")

    def finish(state: State) -> dict[str, Any]:
        value = sum(state["values"])
        if options.get("invalid"):
            value = True
        return {
            "outputs": [
                {
                    "kind": "event",
                    "schema": "example.out",
                    "topic": "out",
                    "payload": {"value": value},
                },
                {
                    "kind": "command",
                    "schema": "example.save",
                    "target": "sink",
                    "payload": {"value": value},
                },
                {
                    "kind": "fact",
                    "entity": "record",
                    "field": "example.value",
                    "value": value,
                },
            ]
        }

    graph.add_node("alpha", alpha)
    graph.add_node("bravo", bravo)
    graph.add_node("finish", finish)
    graph.add_edge(START, "alpha")
    graph.add_edge(START, "bravo")
    graph.add_edge(["alpha", "bravo"], "finish")
    graph.add_edge("finish", END)
    return graph


def completion(value: Any) -> dict[str, Any]:
    message = {"role": "assistant", "content": json.dumps(value)}
    return {
        "message": message,
        "raw": {"choices": [{"message": message}]},
        "usage": {"completion_tokens": 2},
    }


def empty_graph(
    client: JournalClient, options: dict[str, Any]
) -> StateGraph[State, None, State, State]:
    graph = StateGraph(State)
    graph.add_node("finish", lambda state: {"outputs": []})
    graph.add_edge(START, "finish")
    graph.add_edge("finish", END)
    return graph


@pytest.mark.parametrize("provenance", ["lean", "full"])
def test_event_triggers_match_complete_schema_topic_pairs(
    config: dict[str, Any], provenance: str
) -> None:
    class Source(ContextEngine):
        def __init__(self) -> None:
            super().__init__(
                Partition(
                    "source",
                    "source",
                    emits=("A", "B"),
                    message_targets=("topic-A", "topic-B"),
                )
            )

        def bootstrap(self, ctx: EngineContext) -> None:
            for schema, topic in (("A", "topic-A"), ("B", "topic-B"), ("A", "topic-B")):
                ctx.emit(schema, {"value": 1}, topic=topic)

    descriptor = record_descriptor()
    registry = MemoryRegistry(
        (),
        (),
        (
            MessageDescriptor(descriptor["id"], "event", descriptor["schema"]),
            MessageDescriptor("A", "event", VALUE),
            MessageDescriptor("B", "event", VALUE),
        ),
    )
    config["factory"] = __name__ + ":empty_graph"
    config["triggers"] = [
        {"schema": "A", "topic": "topic-A"},
        {"schema": "B", "topic": "topic-B"},
    ]
    config["grants"] = {
        "fields": [],
        "relations": [],
        "events": [],
        "commands": [],
        "facts": [],
    }
    manifest = BindingManifest("pairs", "0")
    engine = LangGraphDecision(EngineBuild("graph", config, registry, manifest, (), {}))
    kernel = Kernel(provenance=provenance)
    kernel.bind(registry, manifest, (cast(Engine, Source()), cast(Engine, engine)))
    try:
        kernel.start()
        kernel.run_until(1)
        rows = journal_decisions(kernel.journal.bytes)
        triggers = [
            r["data"]["initial"]["trigger"]
            for decision in rows.values()
            for r in decision
            if r["phase"] == "observation"
        ]
        assert [(r["schema"], r["topic"]) for r in triggers] == [
            ("A", "topic-A"),
            ("B", "topic-B"),
        ]
        assert engine.sequence == 2
    finally:
        kernel.close()


class Sink(ContextEngine):
    def __init__(self) -> None:
        super().__init__(Partition("sink", "sink", commands=("example.save",)))

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            command = ctx.remember(delivery, lambda value: value)
            ctx.accept(command)
            ctx.execute(command)
            ctx.succeed(command)


@pytest.fixture
def config() -> dict[str, Any]:
    return {
        "factory": __name__ + ":toy_graph",
        "options": {},
        "triggers": [{"schema": "example.trigger", "target": "graph"}],
        "grants": {
            "fields": [],
            "relations": [],
            "events": [{"schema": "example.out", "topic": "out"}],
            "commands": [{"schema": "example.save", "target": "sink"}],
            "facts": [{"entity": "record", "field": "example.value"}],
        },
        "budget": {
            "wall_timeout_s": 2,
            "sim_deadline_ns": 10,
            "max_calls": 6,
            "max_tokens": 20,
            "max_retries": 1,
            "max_prompt_bytes": 10000,
            "recursion_limit": 10,
        },
        "provider": {
            "mode": "stub",
            "model": "scripted",
            "responses": {
                "alpha": [completion({"value": 2})],
                "bravo": [completion({"value": 3})],
            },
        },
    }


def run(
    config: dict[str, Any], provider: Any = None
) -> tuple[Kernel, LangGraphDecision]:
    descriptor = record_descriptor()
    registry = MemoryRegistry(
        (TypeDescriptor("example:Record"),),
        (FieldDescriptor("example.value", "example:Record", {"type": "integer"}),),
        (
            MessageDescriptor(descriptor["id"], "event", descriptor["schema"]),
            MessageDescriptor("example.trigger", "command", VALUE),
            MessageDescriptor("example.save", "command", VALUE),
            MessageDescriptor("example.out", "event", VALUE),
        ),
    )
    entity = EntityRef("run", "epoch", "record", 0, "example:Record")
    manifest = BindingManifest(
        "run",
        "epoch",
        (entity,),
        rules=(BindingRule("graph", "example:Record", ("example.value",)),),
        lifecycle=(LifecycleRule("graph", "example:Record"),),
        bootstrap_commands=(
            CommandRequest("example.trigger", "graph", Instant(1), {"value": 7}),
        ),
    )
    build = EngineBuild(
        "graph", config, registry, manifest, (entity,), {"record": {"example.value": 0}}
    )
    engine = LangGraphDecision(build, provider)
    build.partitions["graph"] = engine.partition
    kernel = Kernel(root_seed=7)
    kernel.bind(registry, manifest, (cast(Engine, engine), cast(Engine, Sink())))
    kernel.start()
    kernel.run_until(2)
    return kernel, engine


def test_typed_outputs_both_replays_and_deterministic_stub_bytes(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel, engine = run(config)
    data = kernel.journal.bytes
    rows = journal_decisions(data)["graph/1"]
    assert [
        row["data"]["identity"][1] for row in rows if row["phase"] == "model_call"
    ] == ["alpha", "bravo"]
    assert isinstance(engine.provider, ScriptedProvider) and engine.provider.calls == 2
    commits = [project(r) for r in kernel.records[1:]]
    assert [
        m["payload"]
        for c in commits
        for m in c["messages"]
        if m["schemaId"] == "example.out"
    ] == [{"value": 5}]
    assert any(r["status"] == "succeeded" for c in commits for r in c["receipts"])
    monkeypatch.setattr(
        OpenAIProvider, "complete", lambda *a, **kw: pytest.fail("live model called")
    )
    monkeypatch.setattr(
        ScriptedProvider, "scripted", lambda *a, **kw: pytest.fail("script called")
    )
    assert replay_graph(data, "graph/1")["values"] == [2, 3]
    recovered = replay(data)
    assert recovered.view().cut == kernel.view().cut and not recovered.incomplete
    monkeypatch.undo()
    other, _ = run(config)
    assert data == other.journal.bytes


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("deadline", "SIM_TIMEOUT"),
        ("invalid", "TOOL_REJECTED"),
        ("exhausted", "SCRIPT_EXHAUSTED"),
        ("prompt", "PROMPT_BUDGET"),
        ("tokens", "TOKEN_BUDGET"),
        ("calls", "CALL_BUDGET"),
        ("timeout", "WALL_TIMEOUT"),
        ("http", "HTTP_STATUS"),
    ],
)
def test_failures_never_publish_actions(
    config: dict[str, Any], change: str, code: str
) -> None:
    if change == "deadline":
        config["budget"]["sim_deadline_ns"] = 0
    elif change == "invalid":
        config["options"]["invalid"] = True
    elif change == "exhausted":
        config["provider"]["responses"]["alpha"] = []
    elif change == "prompt":
        config["budget"]["max_prompt_bytes"] = 1
    elif change == "tokens":
        config["budget"]["max_tokens"] = 3
    elif change == "calls":
        config["budget"]["max_calls"] = 1
    elif change == "timeout":
        config["options"]["sleep"] = 0.1
        config["budget"]["wall_timeout_s"] = 0.02
    else:
        config["budget"]["max_retries"] = 0
        config["provider"]["responses"]["alpha"] = [
            {"error": {"code": "HTTP_STATUS", "message": "HTTP 503"}}
        ]
    kernel, _ = run(config)
    rows = journal_decisions(kernel.journal.bytes)["graph/1"]
    assert next(r["data"]["code"] for r in rows if r["phase"] == "failure") == code
    assert not any(r["phase"] in {"output", "finished"} for r in rows)
    assert replay(kernel.journal.bytes).view().cut == kernel.view().cut
    receipts = [r for c in kernel.records[1:] for r in project(c)["receipts"]]
    assert receipts[-1]["status"] == "failed"
    if change == "timeout":
        with pytest.raises(ProviderError, match="REPLAY_INCOMPLETE"):
            replay_graph(kernel.journal.bytes, "graph/1")
    else:
        assert replay_graph(kernel.journal.bytes, "graph/1")["failure"]["code"] == code


def test_invalid_json_retry_is_recorded_and_replayed(config: dict[str, Any]) -> None:
    config["provider"]["responses"]["alpha"].insert(0, completion({"value": False}))
    kernel, _ = run(config)
    rows = journal_decisions(kernel.journal.bytes)["graph/1"]
    assert any(
        r["phase"] == "validation" and r["data"]["code"] == "TOOL_REJECTED"
        for r in rows
    )
    assert replay_graph(kernel.journal.bytes, "graph/1")["values"] == [2, 3]


def test_parallel_io_overlaps_and_late_reply_cannot_publish(
    config: dict[str, Any],
) -> None:
    class Provider:
        model = "measured"

        def __init__(self) -> None:
            self.barrier = threading.Barrier(2)

        def complete(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]],
            *,
            timeout_s: float,
            max_tokens: int,
        ) -> dict[str, Any]:
            self.barrier.wait(timeout=1)
            time.sleep(0.05 if "alpha" in messages[1]["content"] else 0.01)
            return completion({"value": 2})

    kernel, _ = run(config, Provider())
    rows = journal_decisions(kernel.journal.bytes)["graph/1"]
    assert [r["data"]["identity"][1] for r in rows if r["phase"] == "model_call"] == [
        "alpha",
        "bravo",
    ]
    assert replay_graph(kernel.journal.bytes, "graph/1")["values"] == [2, 2]
    config["budget"]["wall_timeout_s"] = 0.005
    kernel, _ = run(config, Provider())
    before = kernel.journal.bytes
    time.sleep(0.08)
    assert kernel.journal.bytes == before
    rows = journal_decisions(before)["graph/1"]
    assert any(
        r["phase"] == "failure" and r["data"]["code"] == "WALL_TIMEOUT" for r in rows
    )


def test_replay_detects_modified_request(config: dict[str, Any]) -> None:
    kernel, _ = run(config)
    data = kernel.journal.bytes
    # This is a graph transcript integrity check; kernel replay separately verifies WAL hashes.
    modified = data.replace(b"Return JSON integer value", b"Changed JSON instruction")
    with pytest.raises(ProviderError, match="REPLAY_MISMATCH"):
        replay_graph(modified, "graph/1")


def test_catalog_and_invalid_fact_grant(config: dict[str, Any]) -> None:
    assert "langgraph" in EngineCatalog().names()
    bad = deepcopy(config)
    bad["grants"]["facts"][0]["field"] = "foreign"
    with pytest.raises(Exception, match="unknown|foreign|owned"):
        run(bad)


class ReceiptState(State):
    receipt: dict[str, Any]


def receipt_graph(
    client: JournalClient, options: dict[str, Any]
) -> StateGraph[ReceiptState, None, ReceiptState, ReceiptState]:
    if "setup_sleep" in options:
        time.sleep(options["setup_sleep"])
    graph = StateGraph(ReceiptState)

    def finish(state: ReceiptState) -> dict[str, Any]:
        return {"outputs": [], "receipt": {"value": options["value"]}}

    graph.add_node("finish", finish)
    graph.add_edge(START, "finish")
    graph.add_edge("finish", END)
    return graph


@pytest.mark.parametrize("value", [4, False])
def test_command_trigger_typed_success_result(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    import sys

    original = MessageDescriptor

    def descriptor(
        identity: str, kind: str, schema: dict[str, Any]
    ) -> MessageDescriptor:
        return original(
            identity,
            kind,
            schema,
            result_schema=VALUE if identity == "example.trigger" else None,
        )

    monkeypatch.setattr(sys.modules[__name__], "MessageDescriptor", descriptor)
    config["factory"] = __name__ + ":receipt_graph"
    config["options"] = {"value": value}
    kernel, _ = run(config)
    receipts = [
        receipt
        for record in kernel.records[1:]
        for receipt in project(record)["receipts"]
    ]
    assert receipts[-1]["status"] == ("succeeded" if type(value) is int else "failed")
    if type(value) is int:
        assert receipts[-1]["result"] == {"value": 4}
        assert replay_graph(kernel.journal.bytes, "graph/1")["receipt"] == {"value": 4}
    else:
        assert (
            replay_graph(kernel.journal.bytes, "graph/1")["failure"]["code"]
            == "TOOL_REJECTED"
        )


def test_wall_deadline_includes_graph_factory(config: dict[str, Any]) -> None:
    config["factory"] = __name__ + ":receipt_graph"
    config["options"] = {"setup_sleep": 0.3, "value": 4}
    config["budget"]["wall_timeout_s"] = 0.01
    started = time.monotonic()
    kernel, _ = run(config)
    assert time.monotonic() - started < 0.2
    rows = journal_decisions(kernel.journal.bytes)["graph/1"]
    assert rows[-1]["data"]["code"] == "WALL_TIMEOUT"
    assert not any(row["phase"] == "output" for row in rows)
