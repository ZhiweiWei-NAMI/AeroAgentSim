"""Accident compatibility semantics over real typed kernel deliveries."""

from __future__ import annotations

import json
import os
import socket
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from aerokernel.journal import replay

from aeroagentsim.agents.langgraph import journal_decisions, replay_graph
from aeroagentsim.agents.langgraph_client import ScriptedProvider
from aeroagentsim.agents.provider import OpenAIProvider
from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project


@pytest.fixture
def example() -> dict[str, Any]:
    scenario = load_scenario("scenarios/agents/accident-graph.json")
    document = deepcopy(scenario.document)
    document["registry"]["snapshot"] = str(
        Path("scenarios/realtime-registry.snapshot.json").resolve()
    )
    return document


def test_accident_report_parallel_bid_and_award(
    example: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    simulation = Simulation(load_scenario(example), provenance="full")
    simulation.start()
    simulation.run_until(2)
    data = simulation.kernel.journal.bytes
    result = replay_graph(data, "graph/1")
    assert result["winner_id"] == "uav.bravo"
    assert [
        (b["uav_id"], b["interruptible"], b["eligible"]) for b in result["bids"]
    ] == [("uav.alpha", False, False), ("uav.bravo", True, True)]
    rows = journal_decisions(data)["graph/1"]
    calls = [r["data"] for r in rows if r["phase"] == "model_call"]
    assert [(c["identity"][0], c["identity"][1]) for c in calls] == [
        (1, "vehicle_report"),
        (3, "uav_alpha"),
        (3, "uav_bravo"),
        (4, "edge_award"),
    ]
    assert all(
        c["request"]["messages"][1]["content"].startswith("实时观测：") for c in calls
    )
    monkeypatch.setattr(
        OpenAIProvider, "complete", lambda *a, **kw: pytest.fail("network replay")
    )
    monkeypatch.setattr(
        ScriptedProvider, "scripted", lambda *a, **kw: pytest.fail("script replay")
    )
    assert replay_graph(data, "graph/1") == result
    assert replay(data).view().cut == simulation.kernel.view().cut
    # Actual output carries the trigger dispatch and all model record causes.
    messages = [
        item["message"]["fields"]
        for record in simulation.kernel.records[1:]
        for item in record["items"]
        if item.get("kind") == "operation" and "message" in item
    ]
    award = next(m for m in messages if m["schema_id"] == "example.award")
    assert len(award["causes"]) >= 6
    simulation.close()


@pytest.mark.parametrize("bad", ["interruptibility", "winner", "provider", "no_bids"])
def test_accident_invalid_recommendations_and_failure_are_not_awards(
    example: dict[str, Any], bad: str
) -> None:
    cfg = example["engines"]["graph"]["config"]
    cfg["budget"]["max_retries"] = 0
    responses = cfg["provider"]["responses"]
    if bad == "provider":
        responses["uav_alpha"] = [
            {"error": {"code": "WALL_TIMEOUT", "message": "deadline expired"}}
        ]
    elif bad == "winner":
        responses["edge_award"][0]["message"]["content"] = json.dumps(
            {"winner_id": "uav.alpha", "reason": "wrong", "action": "dispatch"}
        )
    else:
        key = "uav_alpha" if bad == "interruptibility" else "uav_bravo"
        value = json.loads(responses[key][0]["message"]["content"])
        value["accept"] = bad == "interruptibility"
        responses[key][0]["message"]["content"] = json.dumps(value)
    simulation = Simulation(load_scenario(example))
    simulation.start()
    simulation.run_until(2)
    rows = journal_decisions(simulation.kernel.journal.bytes)["graph/1"]
    assert any(row["phase"] == "failure" for row in rows)
    assert not any(
        m["schemaId"] == "example.award"
        for r in simulation.kernel.records[1:]
        for m in project(r)["messages"]
    )
    simulation.close()


@pytest.mark.llm
def test_live_accident_graph_and_zero_model_call_replays(
    example: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        with socket.create_connection(("127.0.0.1", 8788), timeout=3):
            pass
    except OSError as exc:
        pytest.skip(f"local endpoint unreachable: {exc}")
    cfg = example["engines"]["graph"]["config"]
    cfg["provider"] = {
        "mode": "live",
        "base_url": "http://127.0.0.1:8788/v1",
        "model": "glm-5.3-flash",
    }
    cfg["budget"]["wall_timeout_s"] = 300
    cfg["budget"]["max_tokens"] = 24000
    calls = 0
    original = OpenAIProvider.complete

    def complete(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(
            self, messages, tools, timeout_s=timeout_s, max_tokens=max_tokens
        )

    monkeypatch.setattr(OpenAIProvider, "complete", complete)
    directory = (
        Path(os.environ["AAS_LANGGRAPH_LIVE_OUT"])
        if "AAS_LANGGRAPH_LIVE_OUT" in os.environ
        else tmp_path / "live"
    )
    with RunSession(load_scenario(example), directory) as session:
        session.run()
        data = session.simulation.kernel.journal.bytes
        rows = journal_decisions(data)["graph/1"]
        assert any(r["phase"] == "finished" for r in rows), rows[-1]
        assert calls >= 4
        monkeypatch.setattr(
            OpenAIProvider,
            "complete",
            lambda *a, **kw: pytest.fail("model called in replay"),
        )
        monkeypatch.setattr(
            ScriptedProvider,
            "scripted",
            lambda *a, **kw: pytest.fail("script called in replay"),
        )
        result = replay_graph(data, "graph/1")
        recovered = replay(data)
        assert (
            recovered.view().cut == session.simulation.kernel.view().cut
            and not recovered.incomplete
        )
        metrics = {
            "model": "glm-5.3-flash",
            "calls": calls,
            "graph_replay_calls": 0,
            "wal_replay_calls": 0,
            "winner_id": result["winner_id"],
            "journal": str(directory / "journal.jsonl"),
            "call_latencies_s": [
                r["data"]["latency_s"] for r in rows if r["phase"] == "model_call"
            ],
        }
        (directory / "langgraph-metrics.json").write_text(
            json.dumps(metrics, indent=2) + "\n"
        )
