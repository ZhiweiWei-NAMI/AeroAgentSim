"""Schema/topic grants remain pairs through committed dispatch and observation."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any, TypedDict, cast

import pytest
from aerokernel import BindingManifest, Kernel, MemoryRegistry, Partition
from aerokernel.engine import Engine
from aerokernel.messages import Delivery
from aerokernel.registry import MessageDescriptor
from aerokernel.sdk import ContextEngine, EngineContext
from langgraph.graph import END, START, StateGraph

from aeroagentsim.agents.decision import RECORD_SCHEMA as DECISION_RECORD
from aeroagentsim.agents.decision import Decision
from aeroagentsim.agents.langgraph import RECORD_SCHEMA as GRAPH_RECORD
from aeroagentsim.agents.langgraph import LangGraphDecision, record_descriptor
from aeroagentsim.agents.langgraph_client import JournalClient
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.services.projector import project

VALUE = {
    "type": "record",
    "members": {"value": {"type": "integer"}},
    "required": ["value"],
    "extra": False,
}
GRANTED_PAIRS = (("A", "topic-A"), ("B", "topic-B"))
# Distinct payloads identify a publication without assuming observations expose topics.
PUBLICATIONS = (
    ("A", "topic-A", 11),
    ("B", "topic-B", 22),
    ("A", "topic-B", 12),
    ("B", "topic-A", 21),
)
Publication = tuple[str, str, int]
Run = tuple[Kernel, Decision | LangGraphDecision, list[Delivery]]


class State(TypedDict):
    observation: dict[str, Any]
    trigger: dict[str, Any]
    outputs: list[dict[str, Any]]


def empty_graph(
    client: JournalClient, options: dict[str, Any]
) -> StateGraph[State, None, State, State]:
    """Exercise the real graph engine without model calls or domain adapters."""
    graph = StateGraph(State)
    graph.add_node("finish", lambda state: {"outputs": []})
    graph.add_edge(START, "finish")
    graph.add_edge("finish", END)
    return graph


class NoopProvider:
    model = "grant-pairs-stub"

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "noop-1",
                    "type": "function",
                    "function": {
                        "name": "noop",
                        "arguments": json.dumps(
                            {"decision_summary": "Observe only granted pairs"}
                        ),
                    },
                }
            ],
        }
        return {
            "message": message,
            "raw": {"choices": [{"message": message}]},
            "usage": {"completion_tokens": 1},
        }


class Source(ContextEngine):
    def __init__(self, publications: tuple[Publication, ...]) -> None:
        super().__init__(
            Partition(
                "source",
                "source",
                emits=("A", "B"),
                message_targets=("topic-A", "topic-B"),
            )
        )
        self.publications = publications

    def bootstrap(self, ctx: EngineContext) -> None:
        # Ordinary Emit proposals: the kernel supplies origins and dispatches.
        for schema, topic, value in self.publications:
            ctx.emit(schema, {"value": value}, topic=topic)


@pytest.fixture(params=("decision", "langgraph"))
def engine_kind(request: pytest.FixtureRequest) -> str:
    return cast(str, request.param)


@pytest.fixture(params=("lean", "full"))
def provenance(request: pytest.FixtureRequest) -> str:
    return cast(str, request.param)


@pytest.fixture
def run_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., Run]]:
    kernels: list[Kernel] = []

    def run(
        engine_kind: str,
        provenance: str,
        publications: tuple[Publication, ...],
        *,
        output_events: tuple[tuple[str, str], ...] = (),
    ) -> Run:
        descriptor = record_descriptor()
        registry = MemoryRegistry(
            (),
            (),
            (
                MessageDescriptor(DECISION_RECORD, "event", descriptor["schema"]),
                MessageDescriptor(GRAPH_RECORD, "event", descriptor["schema"]),
                MessageDescriptor("A", "event", VALUE),
                MessageDescriptor("B", "event", VALUE),
            ),
        )
        grants: dict[str, Any] = {
            "fields": [],
            "relations": [],
            "events": [
                {"schema": schema, "topic": topic} for schema, topic in GRANTED_PAIRS
            ],
            "commands": [],
        }
        budget = {
            "wall_timeout_s": 2,
            "max_calls": 1,
            "max_tokens": 10,
            "max_retries": 0,
            "max_prompt_bytes": 100000,
        }
        if engine_kind == "decision":
            config = {
                "instruction": "Observe only explicitly granted event pairs.",
                "grants": grants,
                "points": [{"event": "A"}, {"event": "B"}],
                "budget": {**budget, "max_rounds": 1},
            }
        else:
            config = {
                "factory": __name__ + ":empty_graph",
                "options": {},
                "triggers": grants["events"],
                "grants": {
                    **grants,
                    "events": [
                        {"schema": schema, "topic": topic}
                        for schema, topic in output_events
                    ],
                    "facts": [],
                },
                "budget": {**budget, "sim_deadline_ns": 10, "recursion_limit": 10},
                "provider": {"mode": "stub", "model": "unused", "responses": {}},
            }
        manifest = BindingManifest("grant-pairs", "0")
        build = EngineBuild("agent", config, registry, manifest, (), {})
        engine: Decision | LangGraphDecision = (
            Decision(build, NoopProvider())
            if engine_kind == "decision"
            else LangGraphDecision(build)
        )
        build.partitions["agent"] = engine.partition
        deliveries: list[Delivery] = []
        original = engine.on_inputs

        def capture(ctx: EngineContext) -> None:
            # Observe real deliveries only; do not replace routing or observations.
            deliveries.extend(ctx.inbox)
            original(ctx)

        monkeypatch.setattr(engine, "on_inputs", capture)
        kernel = Kernel(provenance=provenance)
        kernels.append(kernel)
        kernel.bind(
            registry,
            manifest,
            (cast(Engine, Source(publications)), cast(Engine, engine)),
        )
        kernel.start()
        kernel.run_until(1)
        return kernel, engine, deliveries

    try:
        yield run
    finally:
        for kernel in reversed(kernels):
            kernel.close()


def records(kernel: Kernel, engine_kind: str) -> list[dict[str, Any]]:
    schema = DECISION_RECORD if engine_kind == "decision" else GRAPH_RECORD
    return [
        {
            "phase": message["payload"]["phase"],
            "data": json.loads(message["payload"]["data_json"]),
        }
        for record in kernel.records[1:]
        for message in project(record)["messages"]
        if message["schemaId"] == schema
    ]


def initial_observations(
    rows: list[dict[str, Any]], engine_kind: str
) -> list[dict[str, Any]]:
    return [
        row["data"] if engine_kind == "decision" else row["data"]["initial"]["observation"]
        for row in rows
        if row["phase"] == "observation"
    ]


def assert_pair_observations(kernel: Kernel, engine_kind: str) -> None:
    rows = records(kernel, engine_kind)
    observations = initial_observations(rows, engine_kind)
    expected_count = 1 if engine_kind == "decision" else 2
    assert len(observations) == expected_count
    assert not [row for row in rows if row["phase"] == "failure"]
    assert sum(row["phase"] == "finished" for row in rows) == expected_count
    for observation in observations:
        assert sorted(
            (event["schema"], event["payload"]["value"])
            for event in observation["events"]
        ) == [("A", 11), ("B", 22)]


@pytest.mark.parametrize(
    ("publication", "allowed"),
    [
        (PUBLICATIONS[0], True),
        (PUBLICATIONS[1], True),
        (PUBLICATIONS[2], False),
        (PUBLICATIONS[3], False),
    ],
    ids=("A-topic-A", "B-topic-B", "A-topic-B-forbidden", "B-topic-A-forbidden"),
)
def test_event_triggers_reject_cross_pairs(
    run_pairs: Callable[..., Run],
    engine_kind: str,
    provenance: str,
    publication: Publication,
    allowed: bool,
) -> None:
    kernel, engine, deliveries = run_pairs(engine_kind, provenance, (publication,))
    # Even a forbidden pair must actually arrive via the subscribed topic.
    assert len(deliveries) == 1
    assert deliveries[0].recipient == "agent"  # Never the publication's topic.
    assert deliveries[0].message.origin is not None
    assert deliveries[0].message.schema_id == publication[0]
    assert engine.sequence == int(allowed)
    rows = records(kernel, engine_kind)
    observations = initial_observations(rows, engine_kind)
    assert len(observations) == int(allowed)
    assert sum(row["phase"] == "finished" for row in rows) == int(allowed)
    assert not [row for row in rows if row["phase"] == "failure"]
    if allowed:
        assert [
            (event["schema"], event["payload"]["value"])
            for event in observations[0]["events"]
        ] == [(publication[0], publication[2])]
        if engine_kind == "langgraph":
            trigger = next(
                row["data"]["initial"]["trigger"]
                for row in rows
                if row["phase"] == "observation"
            )
            assert (trigger["schema"], trigger["topic"]) == publication[:2]


def test_observations_exclude_cross_pairs_in_the_same_inbox(
    run_pairs: Callable[..., Run], engine_kind: str, provenance: str
) -> None:
    kernel, engine, deliveries = run_pairs(engine_kind, provenance, PUBLICATIONS)
    assert len(deliveries) == 4
    assert all(delivery.recipient == "agent" for delivery in deliveries)
    assert all(delivery.message.origin is not None for delivery in deliveries)
    assert engine.sequence == (1 if engine_kind == "decision" else 2)
    assert_pair_observations(kernel, engine_kind)


def test_langgraph_observation_pairs_come_from_triggers_not_output_grants(
    run_pairs: Callable[..., Run], provenance: str
) -> None:
    # Deliberately grant only the crossed pairs as OUTPUT authority. Neither
    # those pairs nor the lack of matching output grants may affect input access.
    kernel, engine, deliveries = run_pairs(
        "langgraph",
        provenance,
        PUBLICATIONS,
        output_events=(("A", "topic-B"), ("B", "topic-A")),
    )
    assert len(deliveries) == 4
    assert engine.sequence == 2
    assert_pair_observations(kernel, "langgraph")
