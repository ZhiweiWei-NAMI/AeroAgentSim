"""Offline request admission, legacy replay and prompt-only history projection."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Iterator
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aerokernel import (
    BindingManifest,
    CommandRequest,
    Instant,
    Kernel,
    MemoryRegistry,
    Partition,
)
from aerokernel.engine import Engine
from aerokernel.journal import replay
from aerokernel.registry import MessageDescriptor
from aerokernel.sdk import ContextEngine, EngineContext
from langgraph.graph import END, START, StateGraph

from aeroagentsim.agents.decision import RECORD_SCHEMA, Decision
from aeroagentsim.agents.langgraph import (
    LangGraphDecision,
    journal_decisions,
    record_descriptor,
    replay_graph,
)
from aeroagentsim.agents.langgraph_client import JournalClient, ScriptedProvider
from aeroagentsim.agents.provider import OpenAIProvider, Provider, ProviderError
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.services.projector import project


def completion(tokens: Any = 1, tool: str = "noop") -> dict[str, Any]:
    arguments: dict[str, Any] = {"decision_summary": "Proposal, not completion"}
    if tool not in {"noop", "wait"}:
        arguments["value"] = 1
    message = {
        "role": "assistant",
        "content": "{}",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(arguments)},
            }
        ],
    }
    return {
        "message": message,
        "raw": {"choices": [{"message": message}]},
        "usage": {"completion_tokens": tokens},
    }


class FakeProvider:
    model = "budget-scripted"
    mode = "scripted"

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = deepcopy(responses) if responses is not None else None
        self.requests: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        self.requests.append(
            deepcopy(
                {
                    "model": self.model,
                    "messages": messages,
                    "tools": tools,
                    "max_tokens": max_tokens,
                    "stream": False,
                }
            )
        )
        assert timeout_s > 0
        if self.responses is None:
            return completion()
        assert self.responses, "unexpected provider call"
        response = self.responses.pop(0)
        if "error" in response:
            error = response["error"]
            if "status_code" in error:
                raise ProviderError(
                    error["code"], error["message"], status_code=error["status_code"]
                )
            raise ProviderError(error["code"], error["message"])
        return response


@pytest.fixture
def graph_budget(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # Exercise the real client with stable graph call identities, no graph I/O.
    monkeypatch.setattr(
        "langgraph.config.get_config",
        lambda: {"metadata": {"langgraph_step": 1, "langgraph_node": "node"}},
    )
    return {
        "wall_timeout_s": 2,
        "max_calls": 4,
        "max_tokens": 10,
        "max_retries": 1,
        "max_prompt_bytes": 10000,
    }


MESSAGES = [{"role": "user", "content": "Return JSON"}]


def test_graph_exhaustion_prevents_another_provider_call(
    graph_budget: dict[str, Any],
) -> None:
    provider = FakeProvider([completion(10)])
    client = JournalClient(provider, provider.model, graph_budget)

    async def run() -> None:
        await client.complete("first", MESSAGES)
        with pytest.raises(ProviderError, match="TOKEN_BUDGET"):
            await client.complete("second", MESSAGES)

    asyncio.run(run())
    assert len(provider.requests) == 1
    assert client.records[-1]["failure_code"] == "TOKEN_BUDGET"
    assert client.records[-1]["request"]["max_tokens"] == 0
    client.verify()


def test_graph_partial_usage_caps_the_next_provider_request(
    graph_budget: dict[str, Any],
) -> None:
    provider = FakeProvider([completion(3), completion(4)])
    client = JournalClient(provider, provider.model, graph_budget)

    async def run() -> None:
        await client.complete("first", MESSAGES)
        await client.complete("second", MESSAGES)

    asyncio.run(run())
    assert [request["max_tokens"] for request in provider.requests] == [10, 7]
    assert [row["token_allowance"] for row in client.records] == [10, 7]
    client.verify()


@pytest.mark.parametrize("used", [3, 10])
def test_graph_pending_call_reserves_allowance_before_await(
    graph_budget: dict[str, Any], monkeypatch: pytest.MonkeyPatch, used: int
) -> None:
    provider = FakeProvider([completion(used), completion(1)])
    client = JournalClient(provider, provider.model, graph_budget)
    original = client._exchange

    async def run() -> None:
        entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        async def second_call() -> dict[str, Any]:
            queued.set()
            return await client.complete("second", MESSAGES)

        monkeypatch.setattr(client, "_exchange", exchange)
        first = asyncio.create_task(client.complete("first", MESSAGES))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(second_call())
            await asyncio.wait_for(queued.wait(), 1)
            assert not second.done()
            assert client._reserved_tokens == 10
            assert not provider.requests
            release.set()
            await first
            if used == 10:
                with pytest.raises(ProviderError, match="TOKEN_BUDGET"):
                    await second
            else:
                await second
        finally:
            release.set()
            await asyncio.gather(
                first, *([] if second is None else [second]), return_exceptions=True
            )

    asyncio.run(run())
    assert [request["max_tokens"] for request in provider.requests] == (
        [10] if used == 10 else [10, 7]
    )
    assert client.records[-1]["failure_code"] == (
        "TOKEN_BUDGET" if used == 10 else None
    )
    client.verify()


@pytest.mark.parametrize("used", [11, None, -1, True])
def test_graph_failed_or_unknown_usage_does_not_restore_allowance(
    graph_budget: dict[str, Any], used: Any
) -> None:
    provider = FakeProvider([completion(used)])
    client = JournalClient(provider, provider.model, graph_budget)

    async def run() -> None:
        with pytest.raises(
            ProviderError,
            match="TOKEN_BUDGET" if used == 11 else "USAGE_MISSING",
        ):
            await client.complete("first", MESSAGES)
        with pytest.raises(ProviderError, match="TOKEN_BUDGET"):
            await client.complete("second", MESSAGES)

    asyncio.run(run())
    assert len(provider.requests) == 1


@pytest.mark.parametrize("max_retries", [1, 2])
@pytest.mark.parametrize("recovers", [False, True], ids=["exhausted", "recovered"])
@pytest.mark.parametrize("legacy_message", [False, True], ids=["structured", "live-demo"])
def test_http_rejection_releases_reservation_for_configured_retries(
    graph_budget: dict[str, Any], max_retries: int, recovers: bool, legacy_message: bool
) -> None:
    graph_budget["max_retries"] = max_retries
    error: dict[str, Any] = {"code": "HTTP_STATUS"}
    if legacy_message:
        # Match the unchanged live-demo double's two-argument ProviderError.
        error["message"] = "HTTP 401: authentication rejected"
    else:
        error.update(message="upstream returned HTTP 401", status_code=401)
    rejection = {"error": error}
    provider = FakeProvider(
        [rejection] * max_retries + [completion(3) if recovers else rejection]
    )
    client = JournalClient(provider, provider.model, graph_budget)

    async def run(target: JournalClient) -> None:
        if recovers:
            result = await target.ask_json(
                "first", "Return JSON", {}, lambda value: None
            )
            assert result == {}
        else:
            with pytest.raises(ProviderError, match="HTTP_STATUS"):
                await target.ask_json("first", "Return JSON", {}, lambda value: None)
        target.verify()

    asyncio.run(run(client))
    assert len(provider.requests) == max_retries + 1
    assert [request["max_tokens"] for request in provider.requests] == [10] * (
        max_retries + 1
    )
    assert [row["attempt"] for row in client.records] == list(range(max_retries + 1))
    expected_failures = ["HTTP_STATUS"] * max_retries + [
        None if recovers else "HTTP_STATUS"
    ]
    assert [row["failure_code"] for row in client.records] == expected_failures
    rejected_rows = client.records[:-1] if recovers else client.records
    assert all("response" not in row for row in rejected_rows)
    assert all(row["error"]["status_code"] == 401 for row in rejected_rows)
    assert client._reserved_tokens == 0
    assert client._spent_tokens == (3 if recovers else 0)
    offline = JournalClient(None, provider.model, graph_budget, replay=client.records)
    asyncio.run(run(offline))
    assert offline.records == client.records
    assert len(provider.requests) == max_retries + 1


@pytest.mark.parametrize("status", [401, 504])
def test_openai_http_error_carries_status_without_changing_message(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    response = SimpleNamespace(status=status, read=lambda limit: b"private error body")
    connection = SimpleNamespace(
        connect=lambda: None,
        request=lambda *args: None,
        getresponse=lambda: response,
        close=lambda: None,
    )
    monkeypatch.setattr(
        "aeroagentsim.agents.provider.http.client.HTTPConnection",
        lambda *args, **kwargs: connection,
    )
    provider = OpenAIProvider("http://127.0.0.1/v1", "test-model")
    with pytest.raises(ProviderError) as raised:
        provider.complete(MESSAGES, [], timeout_s=2, max_tokens=10)
    assert raised.value.code == "HTTP_STATUS"
    assert raised.value.status_code == status
    assert str(raised.value) == f"HTTP_STATUS: upstream returned HTTP {status}"


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        ("HTTP 401: authentication rejected", None, 401),
        ("HTTP 504: gateway timeout", None, 504),
        ("upstream returned HTTP 401", 401, 401),
        ("HTTP 401: authentication rejected", 504, 504),
        ("gateway failed after HTTP 401: retry", None, None),
        ("unknown upstream status", None, None),
        ("unknown upstream status", "401", None),
        ("unknown upstream status", True, None),
        ("unknown upstream status", 401.0, None),
        ("unknown upstream status", 600, None),
    ],
)
def test_provider_error_normalizes_only_explicit_or_legacy_status(
    message: str, status: Any, expected: int | None
) -> None:
    error = ProviderError("HTTP_STATUS", message, status_code=status)
    assert error.code == "HTTP_STATUS"
    assert str(error) == f"HTTP_STATUS: {message}"
    assert error.status_code == expected
    assert ProviderError("TRANSPORT", message).status_code is None


@pytest.mark.parametrize(
    ("message", "status"),
    [
        ("upstream returned HTTP 504", 504),
        ("HTTP 504: gateway timeout", None),
        ("upstream returned HTTP 502", 502),
        ("upstream returned HTTP 503", 503),
        ("upstream returned HTTP 500", 500),
        ("unknown upstream status", None),
        ("gateway failed after HTTP 401: retry", None),
        ("unknown upstream status", "401"),
        ("unknown upstream status", True),
        ("unknown upstream status", 401.0),
        ("unexpected redirect", 302),
    ],
)
def test_ambiguous_http_failure_keeps_reservation_and_blocks_retry(
    graph_budget: dict[str, Any], message: str, status: Any
) -> None:
    provider = FakeProvider(
        [
            {"error": {"code": "HTTP_STATUS", "message": message, "status_code": status}},
            completion(10),
        ]
    )
    client = JournalClient(provider, provider.model, graph_budget)

    async def run(target: JournalClient) -> None:
        with pytest.raises(ProviderError, match="TOKEN_BUDGET"):
            await target.ask_json("first", "Return JSON", {}, lambda value: None)
        target.verify()

    asyncio.run(run(client))
    assert len(provider.requests) == 1
    assert [row["failure_code"] for row in client.records] == ["HTTP_STATUS", "TOKEN_BUDGET"]
    assert [row["request"]["max_tokens"] for row in client.records] == [10, 0]
    assert all("response" not in row for row in client.records)
    assert client._reserved_tokens == 10
    assert client._spent_tokens == 0
    offline = JournalClient(None, provider.model, graph_budget, replay=client.records)
    asyncio.run(run(offline))
    assert offline.records == client.records
    assert len(provider.requests) == 1


def test_http_status_survives_client_recording_and_replay(
    graph_budget: dict[str, Any],
) -> None:
    provider = FakeProvider(
        [{"error": {"code": "HTTP_STATUS", "message": "gateway timeout", "status_code": 504}}]
    )
    client = JournalClient(provider, provider.model, graph_budget)

    async def run(target: JournalClient) -> None:
        with pytest.raises(ProviderError) as raised:
            await target.complete("first", MESSAGES)
        assert raised.value.code == "HTTP_STATUS"
        assert raised.value.status_code == 504

    asyncio.run(run(client))
    assert client.records[0]["error"]["status_code"] == 504
    offline = JournalClient(None, provider.model, graph_budget, replay=client.records)
    asyncio.run(run(offline))
    assert offline.records == client.records
    assert len(provider.requests) == 1


def test_unknown_transport_usage_cannot_fund_another_request(
    graph_budget: dict[str, Any],
) -> None:
    provider = FakeProvider(
        [{"error": {"code": "TRANSPORT", "message": "connection lost after send"}}]
    )
    client = JournalClient(provider, provider.model, graph_budget)

    async def run() -> None:
        with pytest.raises(ProviderError, match="TOKEN_BUDGET"):
            await client.ask_json("first", "Return JSON", {}, lambda value: None)

    asyncio.run(run())
    assert len(provider.requests) == 1
    assert [row["failure_code"] for row in client.records] == ["TRANSPORT", "TOKEN_BUDGET"]
    assert "response" not in client.records[0]
    assert client._reserved_tokens == 10


def test_timeout_after_send_keeps_conservative_reservation(
    graph_budget: dict[str, Any],
) -> None:
    provider = FakeProvider(
        [{"error": {"code": "WALL_TIMEOUT", "message": "timed out after sending request"}}]
    )
    client = JournalClient(provider, provider.model, graph_budget)

    async def run() -> None:
        with pytest.raises(ProviderError, match="WALL_TIMEOUT"):
            await client.complete("first", MESSAGES)
        with pytest.raises(ProviderError, match="TOKEN_BUDGET"):
            await client.complete("first", MESSAGES, attempt=1)

    asyncio.run(run())
    assert len(provider.requests) == 1
    assert [row["failure_code"] for row in client.records] == ["WALL_TIMEOUT", "TOKEN_BUDGET"]
    assert "response" not in client.records[0]
    assert client._reserved_tokens == 10
    assert client._spent_tokens == 0


@pytest.mark.parametrize("code", ["HTTP_STATUS", "TRANSPORT"])
def test_scripted_faults_preserve_retries_without_inventing_usage(
    graph_budget: dict[str, Any], code: str
) -> None:
    provider = ScriptedProvider(
        "scripted",
        {"first": [{"error": {"code": code, "message": "offline fault"}}, completion()]},
    )
    client = JournalClient(provider, provider.model, graph_budget)
    result = asyncio.run(
        client.ask_json("first", "Return JSON", {}, lambda value: None)
    )
    assert result == {}
    assert provider.calls == 2
    assert [row["failure_code"] for row in client.records] == [code, None]
    assert [row["request"]["max_tokens"] for row in client.records] == [10, 10]
    assert "response" not in client.records[0]
    client.verify()


@pytest.mark.parametrize("cancel", [False, True], ids=["deadline", "cancellation"])
def test_pending_failure_never_reopens_a_provider_dispatch(
    graph_budget: dict[str, Any], monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    provider = FakeProvider([completion(3)])
    client = JournalClient(provider, provider.model, graph_budget)
    original = client._exchange

    async def run() -> None:
        entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        async def second_call() -> dict[str, Any]:
            queued.set()
            return await client.complete("second", MESSAGES)

        monkeypatch.setattr(client, "_exchange", exchange)
        first = asyncio.create_task(client.complete("first", MESSAGES))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(second_call())
            await asyncio.wait_for(queued.wait(), 1)
            if cancel:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            else:
                client._deadline = 0
                release.set()
                with pytest.raises(ProviderError, match="WALL_TIMEOUT"):
                    await first
            with pytest.raises(
                ProviderError, match="TOKEN_BUDGET" if cancel else "WALL_TIMEOUT"
            ):
                await second
        finally:
            release.set()
            await asyncio.gather(
                first, *([] if second is None else [second]), return_exceptions=True
            )

    asyncio.run(run())
    assert len(provider.requests) == (0 if cancel else 1)
    assert client.records[0]["failure_code"] == "WALL_TIMEOUT"
    assert client._reserved_tokens == (10 if cancel else 0)


def test_queued_cancellation_is_journaled_and_successful_graph_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider([completion(3)])
    clients: list[JournalClient] = []

    def factory(client: JournalClient, options: dict[str, Any]) -> Any:
        clients.append(client)
        graph = StateGraph(dict)

        async def node(state: dict[str, Any]) -> dict[str, Any]:
            release = asyncio.Event()
            original = client._exchange

            async def exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
                await release.wait()
                return await original(*args, **kwargs)

            monkeypatch.setattr(client, "_exchange", exchange)
            active = asyncio.create_task(client.complete("active", MESSAGES))
            queued = None
            try:
                await asyncio.sleep(0)
                queued = asyncio.create_task(
                    client.complete("queued", MESSAGES, attempt=2)
                )
                await asyncio.sleep(0)
                queued.cancel()
                try:
                    await queued
                except asyncio.CancelledError:
                    pass
                except ProviderError as exc:
                    if exc.code != "WALL_TIMEOUT":
                        raise
                if not active.done():
                    assert client._admission.locked()
                    assert client._reserved_tokens == 10
                    assert client._spent_tokens == 0
                    assert not provider.requests
                release.set()
                await active
                return {"outputs": []}
            finally:
                release.set()
                await asyncio.gather(
                    active,
                    *([] if queued is None else [queued]),
                    return_exceptions=True,
                )

        graph.add_node("node", node)
        graph.add_edge(START, "node")
        graph.add_edge("node", END)
        return graph

    # Keep the factory importable for the actual conformance replay entry point.
    monkeypatch.setattr(
        sys.modules[__name__], "queued_cancellation_graph", factory, raising=False
    )
    descriptor = record_descriptor()
    registry = MemoryRegistry(
        (),
        (),
        (
            MessageDescriptor(descriptor["id"], "event", descriptor["schema"]),
            MessageDescriptor(
                "example.trigger",
                "command",
                {"type": "record", "members": {}, "required": [], "extra": False},
            ),
        ),
    )
    manifest = BindingManifest(
        "queued-cancellation",
        "0",
        bootstrap_commands=(
            CommandRequest("example.trigger", "graph", Instant(1), {}),
        ),
    )
    budget = {
        "wall_timeout_s": 2,
        "sim_deadline_ns": 10,
        "max_calls": 4,
        "max_tokens": 10,
        "max_retries": 1,
        "max_prompt_bytes": 10000,
        "recursion_limit": 10,
    }
    config = {
        "factory": __name__ + ":queued_cancellation_graph",
        "options": {},
        "triggers": [{"schema": "example.trigger", "target": "graph"}],
        "grants": {
            "fields": [],
            "relations": [],
            "events": [],
            "commands": [],
            "facts": [],
        },
        "budget": budget,
        "provider": {"mode": "stub", "model": provider.model, "responses": {}},
    }
    build = EngineBuild("graph", config, registry, manifest, (), {})
    engine = LangGraphDecision(build, provider)
    build.partitions["graph"] = engine.partition
    kernel = Kernel()
    kernel.bind(registry, manifest, (cast(Engine, engine),))
    try:
        kernel.start()
        kernel.run_until(2)
        data = kernel.journal.bytes
        rows = journal_decisions(data)["graph/1"]
        assert rows[-1] == {"phase": "finished", "data": {"result": {"outputs": []}}}
        live = clients[0]
        assert [
            row["data"] for row in rows if row["phase"] == "model_call"
        ] == live.records
        active, queued = live.records
        assert active["failure_code"] is None
        assert queued["identity"] == [1, "node", "queued", 2]
        assert queued["attempt"] == 2
        assert queued["failure_code"] == "WALL_TIMEOUT"
        assert queued["error"]["code"] == "WALL_TIMEOUT"
        assert queued["token_allowance"] == queued["request"]["max_tokens"] == 0
        assert queued["request"]["messages"] == MESSAGES
        assert "response" not in queued
        assert live._reserved_tokens == 0
        assert live._spent_tokens == 3
        assert not live._pending
        assert [request["max_tokens"] for request in provider.requests] == [10]
        monkeypatch.setattr(
            FakeProvider,
            "complete",
            lambda *args, **kwargs: pytest.fail("provider in conformance replay"),
        )
        assert replay_graph(data, "graph/1") == {"outputs": []}
        assert clients[-1].records == live.records
        assert not clients[-1]._pending
        assert len(provider.requests) == 1
    finally:
        kernel.close()


@pytest.mark.parametrize("code", ["CALL_BUDGET", "PROMPT_BUDGET", "WALL_TIMEOUT"])
def test_graph_other_preflight_checks_still_prevent_dispatch(
    graph_budget: dict[str, Any], code: str
) -> None:
    provider = FakeProvider()
    if code == "CALL_BUDGET":
        graph_budget["max_calls"] = 0
    if code == "PROMPT_BUDGET":
        graph_budget["max_prompt_bytes"] = 1
    client = JournalClient(provider, provider.model, graph_budget)
    if code == "WALL_TIMEOUT":
        client._deadline = 0
    with pytest.raises(ProviderError, match=code):
        asyncio.run(client.complete("first", MESSAGES))
    assert not provider.requests


def test_graph_prompt_budget_still_counts_tool_schemas(
    graph_budget: dict[str, Any],
) -> None:
    graph_budget["max_prompt_bytes"] = 1000
    provider = FakeProvider()
    client = JournalClient(provider, provider.model, graph_budget)
    tools = [{"type": "function", "function": {"description": "schema " * 1000}}]
    with pytest.raises(ProviderError, match="PROMPT_BUDGET"):
        asyncio.run(client.complete("first", MESSAGES, tools))
    assert not provider.requests


@pytest.fixture
def legacy_graph_rows() -> list[dict[str, Any]]:
    # Literal pre-reservation requests: BOTH calls asked for the full cap.
    requests = json.loads(
        '[{"identity": [1, "node", "first", 0], "request": '
        '{"messages": [{"role": "user", "content": "Return JSON"}], '
        '"tools": [], "model": "budget-scripted", "max_tokens": 10, "stream": false}}, '
        '{"identity": [1, "node", "second", 0], "request": '
        '{"messages": [{"role": "user", "content": "Return JSON"}], '
        '"tools": [], "model": "budget-scripted", "max_tokens": 10, "stream": false}}]'
    )
    for row, used in zip(requests, (3, 4)):
        row["response"] = completion(used)
    return cast(list[dict[str, Any]], requests)


@pytest.mark.parametrize("aggregate_failure", [False, True])
def test_legacy_graph_requests_replay_with_original_caps(
    graph_budget: dict[str, Any],
    legacy_graph_rows: list[dict[str, Any]],
    aggregate_failure: bool,
) -> None:
    if aggregate_failure:
        legacy_graph_rows[-1]["response"]["usage"]["completion_tokens"] = 8
    client = JournalClient(
        None, FakeProvider.model, graph_budget, replay=legacy_graph_rows
    )

    async def run() -> None:
        for key in ("first", "second"):
            await client.complete(key, MESSAGES)

    asyncio.run(run())
    assert [row["request"] for row in client.records] == [
        row["request"] for row in legacy_graph_rows
    ]
    assert all("token_allowance" not in row for row in client.records)
    if aggregate_failure:
        with pytest.raises(ProviderError, match="TOKEN_BUDGET"):
            client.verify()
    else:
        client.verify()


def test_reserved_requests_and_preflight_failure_replay_without_provider(
    graph_budget: dict[str, Any],
) -> None:
    provider = FakeProvider([completion(3), completion(7)])
    live = JournalClient(provider, provider.model, graph_budget)

    async def run(client: JournalClient) -> None:
        await client.complete("first", MESSAGES)
        await client.complete("second", MESSAGES)
        with pytest.raises(ProviderError, match="TOKEN_BUDGET"):
            await client.complete("third", MESSAGES)
        client.verify()

    asyncio.run(run(live))
    offline = JournalClient(None, provider.model, graph_budget, replay=live.records)
    asyncio.run(run(offline))
    assert offline.records == live.records
    assert len(provider.requests) == 2


@pytest.mark.parametrize("allowance", [None, True, -1, 11, 9])
def test_replay_rejects_invalid_or_inconsistent_recorded_allowance(
    graph_budget: dict[str, Any],
    legacy_graph_rows: list[dict[str, Any]],
    allowance: Any,
) -> None:
    legacy_graph_rows[0]["token_allowance"] = allowance
    client = JournalClient(
        None, FakeProvider.model, graph_budget, replay=legacy_graph_rows
    )
    with pytest.raises(ProviderError, match="REPLAY_MISMATCH"):
        asyncio.run(client.complete("first", MESSAGES))


@pytest.mark.parametrize(
    "field", ["messages", "tools", "model", "max_tokens", "identity"]
)
def test_replay_still_rejects_modified_request_members(
    graph_budget: dict[str, Any], legacy_graph_rows: list[dict[str, Any]], field: str
) -> None:
    row = legacy_graph_rows[0]
    if field == "identity":
        row["identity"][1] = "different-node"
    else:
        row["request"][field] = {
            "messages": [{"role": "user", "content": "Changed"}],
            "tools": [{"type": "function"}],
            "model": "different-model",
            "max_tokens": 9,
        }[field]
    client = JournalClient(
        None, FakeProvider.model, graph_budget, replay=legacy_graph_rows
    )
    with pytest.raises(ProviderError, match="REPLAY_MISMATCH"):
        asyncio.run(client.complete("first", MESSAGES))


@pytest.mark.parametrize(
    "field", ["messages", "tools", "model", "max_tokens", "identity"]
)
def test_queued_cancellation_replay_compares_the_whole_request(
    graph_budget: dict[str, Any], legacy_graph_rows: list[dict[str, Any]], field: str
) -> None:
    row = legacy_graph_rows[0]
    del row["response"]
    row["token_allowance"] = row["request"]["max_tokens"] = 0
    row["error"] = {"code": "WALL_TIMEOUT", "message": "graph invocation canceled"}
    row["failure_code"] = "WALL_TIMEOUT"
    if field == "identity":
        row["identity"][1] = "different-node"
    else:
        row["request"][field] = {
            "messages": [{"role": "user", "content": "Changed"}],
            "tools": [{"type": "function"}],
            "model": "different-model",
            "max_tokens": 1,
        }[field]
    client = JournalClient(None, FakeProvider.model, graph_budget, replay=[row])
    with pytest.raises(ProviderError, match="REPLAY_MISMATCH"):
        asyncio.run(client.complete("first", MESSAGES))


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
def make_decision() -> Iterator[Callable[..., tuple[Decision, Kernel]]]:
    kernels: list[Kernel] = []

    def make(
        provider: Provider,
        budget: dict[str, Any] | None = None,
        timers: tuple[int, ...] = (1,),
        provenance: str = "full",
    ) -> tuple[Decision, Kernel]:
        registry = MemoryRegistry(
            (),
            (),
            (
                MessageDescriptor(
                    RECORD_SCHEMA, "event", record_descriptor()["schema"]
                ),
                MessageDescriptor(
                    "example.save",
                    "command",
                    {
                        "type": "record",
                        "members": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "extra": False,
                    },
                ),
            ),
        )
        config = {
            "instruction": "Observe recorded history.",
            "grants": {
                "fields": [],
                "relations": [],
                "events": [],
                "commands": [{"schema": "example.save", "target": "sink"}],
            },
            "points": [{"timer_ns": ns} for ns in timers],
            "budget": {
                "wall_timeout_s": 2,
                "max_rounds": 2,
                "max_calls": 2,
                "max_tokens": 10,
                "max_retries": 1,
                "max_prompt_bytes": 100000,
                **(budget or {}),
            },
        }
        manifest = BindingManifest("budgets", "0")
        build = EngineBuild("agent", config, registry, manifest, (), {})
        engine = Decision(build, provider)
        build.partitions["agent"] = engine.partition
        kernel = Kernel(provenance=provenance)
        kernels.append(kernel)
        kernel.bind(registry, manifest, (cast(Engine, engine), cast(Engine, Sink())))
        return engine, kernel

    try:
        yield make
    finally:
        for kernel in reversed(kernels):
            kernel.close()


def records(kernel: Kernel) -> list[dict[str, Any]]:
    return [
        {
            "phase": message["payload"]["phase"],
            "data": json.loads(message["payload"]["data_json"]),
        }
        for record in kernel.records[1:]
        for message in project(record)["messages"]
        if message["schemaId"] == RECORD_SCHEMA
    ]


def test_schema_heavy_catalog_exceeds_prompt_budget_before_provider(
    make_decision: Callable[..., tuple[Decision, Kernel]],
) -> None:
    provider = FakeProvider()
    engine, kernel = make_decision(provider, {"max_prompt_bytes": 8000})
    parameters = engine.catalog.tools[0]["function"]["parameters"]
    for index in range(100):
        name = f"schema_field_{index}"
        parameters["properties"][name] = {
            "type": "string",
            "description": "模式/schema " * 30,
        }
        parameters["required"].append(name)
    kernel.start()
    kernel.run_until(2)
    rows = records(kernel)
    observation = next(row["data"] for row in rows if row["phase"] == "observation")
    messages = [
        {"role": "system", "content": engine.instruction},
        {
            "role": "user",
            "content": json.dumps(observation, ensure_ascii=False, allow_nan=False),
        },
    ]
    assert len(json.dumps(messages, ensure_ascii=False).encode()) < 8000
    assert len(json.dumps(engine.catalog.tools, ensure_ascii=False).encode()) > 8000
    assert not provider.requests
    assert rows[-1] == {"phase": "failure", "data": {"code": "PROMPT_BUDGET"}}


@pytest.mark.parametrize("opencode", [False, True])
def test_prompt_byte_boundary_uses_the_serialization_sent(
    make_decision: Callable[..., tuple[Decision, Kernel]],
    monkeypatch: pytest.MonkeyPatch,
    opencode: bool,
) -> None:
    captured = FakeProvider()
    provider: Provider = captured
    if opencode:
        provider = OpenAIProvider("https://opencode.ai/v1", captured.model)
        monkeypatch.setattr(
            OpenAIProvider,
            "complete",
            lambda self, *args, **kwargs: captured.complete(*args, **kwargs),
        )
    _, baseline = make_decision(provider)
    baseline.start()
    baseline.run_until(2)
    request = captured.requests[0]
    measured = len(json.dumps(request, ensure_ascii=False, allow_nan=False).encode())
    if opencode:
        assert all("strict" not in tool["function"] for tool in request["tools"])
        prompt = next(
            row["data"] for row in records(baseline) if row["phase"] == "prompt"
        )
        assert all(tool["function"]["strict"] for tool in prompt["tools"])
    for limit, admitted in ((measured, True), (measured - 1, False)):
        captured.requests.clear()
        _, kernel = make_decision(provider, {"max_prompt_bytes": limit})
        kernel.start()
        kernel.run_until(2)
        if admitted:
            assert captured.requests == [request]
        else:
            assert not captured.requests
            assert records(kernel)[-1]["data"]["code"] == "PROMPT_BUDGET"


@pytest.mark.parametrize("window", [None, 1, 2, 10])
@pytest.mark.parametrize("provenance", ["lean", "full"])
def test_history_window_bounds_prompts_and_preserves_wal_evidence(
    make_decision: Callable[..., tuple[Decision, Kernel]],
    monkeypatch: pytest.MonkeyPatch,
    window: int | None,
    provenance: str,
) -> None:
    provider = FakeProvider()
    engine, kernel = make_decision(
        provider,
        {} if window is None else {"history_window": window},
        timers=(1, 2, 3, 4),
        provenance=provenance,
    )
    command_name = engine.catalog.tools[0]["function"]["name"]
    provider.responses = [completion(tool=command_name) for _ in range(3)]
    provider.responses.append(completion())
    kernel.start()
    kernel.run_until(5)
    rows = records(kernel)
    prompts = [row["data"] for row in rows if row["phase"] == "prompt"]
    assert len(prompts) == len(provider.requests) == 4
    observations = [row["data"] for row in rows if row["phase"] == "observation"]
    assert [len(obs["actions"]) for obs in observations] == [0, 1, 2, 3]
    for prompt, request, full in zip(prompts, provider.requests, observations):
        assert prompt["messages"] == request["messages"]
        actual = json.loads(request["messages"][1]["content"])
        selected = full["actions"] if window is None else full["actions"][-window:]
        assert actual["actions"] == selected
        ids = {row["command_id"] for row in selected}
        assert actual["receipts"] == [
            row for row in full["receipts"] if row["command_id"] in ids
        ]
        assert actual.keys() == full.keys()
        for key in full.keys() - {"actions", "receipts"}:
            assert actual[key] == full[key]
        if window is None:
            assert "history_window" not in prompt
        else:
            assert prompt["history_window"] == window
    assert len(engine.commands_by_id) == len(engine.receipts) == 3
    assert sum(row["phase"] == "command" for row in rows) == 3
    receipts = [row["data"] for row in rows if row["phase"] == "receipt"]
    assert len(receipts) == 12
    for command_id in engine.commands_by_id:
        statuses = [
            row["status"] for row in receipts if row["command_id"] == command_id
        ]
        assert statuses == [
            "submitted",
            "accepted",
            "executing",
            "succeeded",
        ]
    monkeypatch.setattr(
        FakeProvider,
        "complete",
        lambda *args, **kwargs: pytest.fail("provider in replay"),
    )
    recovered = replay(kernel.journal.bytes)
    assert recovered.view().cut == kernel.view().cut
    assert not recovered.incomplete


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "2", None])
def test_history_window_requires_an_explicit_positive_integer(
    make_decision: Callable[..., tuple[Decision, Kernel]], invalid: Any
) -> None:
    with pytest.raises(ValueError, match="bounded integers"):
        make_decision(FakeProvider(), {"history_window": invalid})


@pytest.fixture
def legacy_user_prompt() -> bytes:
    # Frozen pre-window serialization with multiple commands/receipts; no current
    # observation helper is used to construct this expected byte string.
    return (
        b'{"valid_at": {"ns": "9", "microstep": 2}, '
        b'"known_at": {"index": 7, "ns": "8", "microstep": 1}, '
        b'"fields": [], "relations": [], "events": [], "actions": '
        b'[{"command_id": "old", "status": "succeeded"}, '
        b'{"command_id": "recent", "status": "accepted"}], '
        b'"receipts": [{"command_id": "old", "status": "succeeded"}, '
        b'{"command_id": "recent", "status": "accepted"}]}'
    )


def test_absent_history_key_preserves_legacy_prompt_bytes(
    make_decision: Callable[..., tuple[Decision, Kernel]],
    monkeypatch: pytest.MonkeyPatch,
    legacy_user_prompt: bytes,
) -> None:
    provider = FakeProvider()
    engine, _ = make_decision(provider)
    engine.commands_by_id = {"old": ("agent/1", "a"), "recent": ("agent/2", "b")}
    engine.receipts = {
        "old": {"command_id": "old", "status": "succeeded"},
        "recent": {"command_id": "recent", "status": "accepted"},
    }
    view = SimpleNamespace(
        cut=SimpleNamespace(index=7, instant=Instant(8, 1)),
        action=lambda key: SimpleNamespace(
            status=engine.receipts[key]["status"], head=None
        ),
    )
    ctx = cast(
        EngineContext,
        SimpleNamespace(now=Instant(9, 2), view=view, inbox=[], inputs=[]),
    )
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        engine,
        "record",
        lambda ctx, decision, phase, data: recorded.append(
            {"phase": phase, "data": deepcopy(data)}
        ),
    )
    engine.decide(ctx)
    expected = [
        {"role": "system", "content": "Observe recorded history."},
        {"role": "user", "content": legacy_user_prompt.decode()},
    ]
    sent_bytes = json.dumps(
        provider.requests[0]["messages"], ensure_ascii=False
    ).encode()
    assert sent_bytes == json.dumps(expected, ensure_ascii=False).encode()
    prompt = next(row["data"] for row in recorded if row["phase"] == "prompt")
    assert json.dumps(prompt, ensure_ascii=False).encode() == json.dumps(
        {
            "round": 0,
            "messages": expected,
            "tools": engine.catalog.tools,
            "model": "budget-scripted",
            "provider_mode": "scripted",
            "provider_profile": None,
            "execution_mode": "scripted",
            "transport_transformations": None,
            "max_tokens": 10,
        },
        ensure_ascii=False,
    ).encode()
