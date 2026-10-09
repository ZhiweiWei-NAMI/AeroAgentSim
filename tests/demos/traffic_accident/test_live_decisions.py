"""Live profile uses the journaled graph, with rule authority and explicit failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from aerokernel import BindingManifest, EntityRef, Kernel, Partition
from aerokernel.binding import BindingRule, LifecycleRule
from aerokernel.engine import Engine
from aerokernel.journal import replay
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.values import thaw

from aeroagentsim.agents.langgraph import (
    LangGraphDecision,
    journal_decisions,
    replay_graph,
)
from aeroagentsim.agents.langgraph_client import ScriptedProvider
from aeroagentsim.agents.provider import OpenAIProvider, ProviderError
from aeroagentsim.authoring.demo import live_decisions
from aeroagentsim.engines.common import policies
from aeroagentsim.packs.traffic_accident.decision_failures import DecisionFailures
from aeroagentsim.platform import RunSession
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario import Scenario, load_scenario
from aeroagentsim.scenario.profiles import apply_engine_profile
from aeroagentsim.services.projector import project
from tests.demos.conftest import SCENARIO
from tests.demos.traffic_accident.test_end_to_end import trace
from tests.demos.traffic_accident.test_policy import evaluate

PROFILE = SCENARIO / "profiles/live-llm.yaml"


def test_shipped_live_demo_budget_is_per_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aeroagentsim.services.demo import demo_document

    document, base = demo_document()
    live_decisions(document, {"profile": "default"})
    budget = document["engines"]["decisions"]["config"]["budget"]
    assert budget["sim_timeout_ns"] == 1_000_000_000
    calls = 0

    def fail_provider(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise ProviderError("HTTP_STATUS", "HTTP 401: authentication rejected")

    monkeypatch.setattr(OpenAIProvider, "complete", fail_provider)
    directory = tmp_path / "run"
    with RunSession(load_scenario(document, base=base), directory) as session:
        session.start()
        session.run_until(20_000_000_000)
    rows = journal_decisions((directory / "journal.jsonl").read_bytes())
    invocation = next(
        r["data"] for rs in rows.values() for r in rs if r["phase"] == "observation"
    )
    start_ns = int(invocation["initial"]["observation"]["valid_at"]["ns"])
    assert start_ns > 1_000_000_000
    assert (
        invocation["budget"]["sim_deadline_ns"] == start_ns + budget["sim_timeout_ns"]
    )
    failures = [
        r["data"]["code"] for rs in rows.values() for r in rs if r["phase"] == "failure"
    ]
    expected_calls = budget["max_retries"] + 1
    assert calls == expected_calls and failures == ["HTTP_STATUS"]
    assert (
        replay_graph((directory / "journal.jsonl").read_bytes(), next(iter(rows)))[
            "failure"
        ]["code"]
        == "HTTP_STATUS"
    )
    assert calls == expected_calls


@pytest.fixture(scope="module")
def live_scenario() -> Scenario:
    return apply_engine_profile(load_scenario(SCENARIO / "scenario.yaml"), PROFILE)


def completion(value: Any) -> dict[str, Any]:
    message = {"role": "assistant", "content": json.dumps(value)}
    return {
        "message": message,
        "raw": {"choices": [{"message": message}]},
        "usage": {"completion_tokens": 2},
    }


class SnapshotSource(ContextEngine):
    """An explicit test observation source; no model fallback or physical simulation."""

    def __init__(
        self,
        refs: tuple[EntityRef, ...],
        fields: dict[str, list[str]],
        initial: dict[str, dict[str, Any]],
    ) -> None:
        self.refs, self.fields, self.initial = refs, fields, initial
        super().__init__(
            Partition(
                "source",
                "source",
                produces=tuple(sorted({f for fs in fields.values() for f in fs})),
                lifecycle=True,
                emits=("traffic.incident.detected", "traffic.broadcast"),
                subscribes=("traffic.proposal.report",),
                message_targets=("traffic.incident.detected", "traffic.broadcast"),
            ),
            policies=policies(tuple(sorted({f for fs in fields.values() for f in fs}))),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        for ref in self.refs:
            ctx.create(ref)
            for field in self.fields[ref.id]:
                ctx.set(ref, field, self.initial[ref.id][field])
        ctx.wake_at(1)

    def on_inputs(self, ctx: EngineContext) -> None:
        refs = {r.id: {"$ref": r.to_data()} for r in self.refs}
        if any(dirty.kind == "timer" for dirty in ctx.dirty):
            ctx.emit(
                "traffic.incident.detected",
                {"actor": refs["vehicle.reporter"], "incident": refs["incident.01"]},
                topic="traffic.incident.detected",
            )
        for delivery in ctx.inbox:
            if delivery.message.schema_id == "traffic.proposal.report":
                ctx.inputs = [delivery.dispatch_ref]
                ctx.emit(
                    "traffic.broadcast",
                    {"incident": refs["incident.01"]},
                    topic="traffic.broadcast",
                )


def run_graph(
    scenario: Scenario, failure: str | None = None
) -> tuple[Kernel, LangGraphDecision]:
    cfg = deepcopy(scenario.engines["decisions"]["config"])
    cfg["budget"]["max_retries"] = 0
    responses = json.loads((SCENARIO / "fixtures/decisions.json").read_text())[
        "responses"
    ]
    responses["uav_alpha"][0]["accept"] = True
    scripted = {
        key: [completion(row) for row in rows] for key, rows in responses.items()
    }
    if failure == "invalid":
        scripted["vehicle_report"] = [completion({"decision": "report"})]
    elif failure == "numeric":
        cfg["budget"]["max_retries"] = 1
        correct = responses["uav_alpha"][0]
        scripted["uav_alpha"] = [
            completion({**correct, "alt_target_m": 70}),
            completion(correct),
        ]
    elif failure is not None:
        scripted["uav_bravo" if failure == "HTTP_STATUS" else "vehicle_report"] = [
            {"error": {"code": failure, "message": "authored test failure"}}
        ]
    cfg["provider"] = {"mode": "stub", "model": "live-path-test", "responses": scripted}
    fields: dict[str, list[str]] = {}
    for row in cfg["grants"]["fields"]:
        fields.setdefault(row["entity"], []).append(row["field"])
    refs = tuple(r for r in scenario.manifest.entities if r.id in fields)
    initial = deepcopy(scenario.initial)
    initial["medical-delivery-01"]["traffic.task.phase"] = "executing"
    initial["district-patrol-02"]["traffic.task.phase"] = "executing"
    manifest = BindingManifest(
        scenario.run_id,
        "0",
        refs,
        rules=tuple(
            BindingRule("source", r.type_id, tuple(fields[r.id]), r.id) for r in refs
        ),
        lifecycle=tuple(LifecycleRule("source", r.type_id, r.id) for r in refs),
    )
    build = EngineBuild("decisions", cfg, scenario.registry, manifest, refs, initial)
    graph = LangGraphDecision(build)
    bridge = DecisionFailures(
        EngineBuild("failures", {}, scenario.registry, manifest, refs, initial)
    )
    source = SnapshotSource(refs, fields, initial)
    build.partitions.update(
        {e.partition.id: e.partition for e in (source, graph, bridge)}
    )
    kernel = Kernel(root_seed=42)
    kernel.bind(
        scenario.registry,
        manifest,
        tuple(cast(Engine, e) for e in (source, graph, bridge)),
    )
    kernel.start()
    kernel.run_until(10)
    return kernel, graph


def test_graph_proposals_medical_rule_feed_and_zero_call_replay(
    live_scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel, graph = run_graph(live_scenario)
    commits = [project(row) for row in kernel.records[1:]]
    messages = [m for row in commits for m in row["messages"]]
    bids = {
        m["payload"]["actor"]["$ref"]["id"]: m["payload"]
        for m in messages
        if m["schemaId"] == "traffic.proposal.bid"
    }
    assert bids["uav.alpha"]["accept"] is True
    alpha = next(r for r in graph.build.entities if r.id == "medical-delivery-01")
    task = {}
    for f in (
        "traffic.task.kind",
        "traffic.task.phase",
        "traffic.task.interruptible",
    ):
        fact = kernel.view().field((alpha, f), kernel.view().instant)
        assert isinstance(fact, Fact)
        task[f] = thaw(fact.value)
    assert evaluate("medical_locked", {"task": task}) is True
    assert (
        evaluate(
            "bid_eligible",
            {
                "task": task,
                "bid": {
                    "traffic.bid.decision_status": "succeeded",
                    "traffic.bid.recommendation": "accept",
                },
                "assessment": {
                    "traffic.assessment.in_region": True,
                    "traffic.assessment.feasible": True,
                },
                "capture_task": {"traffic.task.phase": "inactive"},
            },
        )
        is False
    )
    assert isinstance(graph.provider, ScriptedProvider) and graph.provider.calls == 3
    console = [m for m in messages if m["schemaId"] == "aas.agent.record"]
    assert {m["payload"]["phase"] for m in console} >= {
        "observation",
        "prompt",
        "response",
        "validation",
        "finished",
    }
    assert all(
        json.loads(m["payload"]["data_json"])["source_schema"] == "aas.langgraph.record"
        for m in console
    )
    data = kernel.journal.bytes
    monkeypatch.setattr(
        OpenAIProvider, "complete", lambda *a, **kw: pytest.fail("network replay")
    )
    monkeypatch.setattr(
        ScriptedProvider, "scripted", lambda *a, **kw: pytest.fail("script replay")
    )
    assert len(replay_graph(data, "decisions/1")["outputs"]) == 1
    assert len(replay_graph(data, "decisions/2")["outputs"]) == 2
    assert replay(data).view().cut == kernel.view().cut


@pytest.mark.parametrize("failure", ["WALL_TIMEOUT", "HTTP_STATUS", "invalid"])
def test_graph_failure_is_typed_and_routes_to_authored_branch(
    live_scenario: Scenario, failure: str
) -> None:
    kernel, _ = run_graph(live_scenario, failure)
    messages = [m for row in kernel.records[1:] for m in project(row)["messages"]]
    failed = [m for m in messages if m["schemaId"] == "traffic.decision.failed"]
    code = "TOOL_REJECTED" if failure == "invalid" else failure
    assert len(failed) == 1 and failed[0]["payload"]["reason"].startswith(code)
    assert not any(m["schemaId"] == "traffic.proposal.bid" for m in messages)
    decision = "decisions/2" if failure == "HTTP_STATUS" else "decisions/1"
    assert replay_graph(kernel.journal.bytes, decision)["failure"]["code"] == code
    package = yaml.safe_load((SCENARIO / "behaviours.yaml").read_text())
    for chain, destination in (
        ("traffic.incident_report", "failed"),
        ("traffic.solicit_award", "no_candidate"),
    ):
        branch = next(
            t
            for t in package["chains"][chain]["transitions"]
            if t["id"] == "decision-failure"
        )
        assert (
            branch["on"]["event"] == failed[0]["schemaId"]
            and branch["to"] == destination
        )


def test_integer_altitude_is_journaled_rejection_then_explicit_retry(
    live_scenario: Scenario,
) -> None:
    kernel, graph = run_graph(live_scenario, "numeric")
    rows = journal_decisions(kernel.journal.bytes)["decisions/2"]
    rejected = [row for row in rows if row["phase"] == "validation"]
    assert len(rejected) == 1 and rejected[0]["data"]["code"] == "TOOL_REJECTED"
    assert "floating-point 70.0" in rejected[0]["data"]["message"]
    calls = [row["data"] for row in rows if row["phase"] == "model_call"]
    assert [
        call["identity"][3] for call in calls if call["identity"][1] == "uav_alpha"
    ] == [0, 1]
    assert isinstance(graph.provider, ScriptedProvider) and graph.provider.calls == 4
    assert len(replay_graph(kernel.journal.bytes, "decisions/2")["outputs"]) == 2


def test_stub_profile_rules_reject_alpha_acceptance_on_committed_scene(
    live_scenario: Scenario, tmp_path: Path
) -> None:
    document = deepcopy(live_scenario.document)
    cfg = document["engines"]["decisions"]["config"]
    responses = json.loads((SCENARIO / "fixtures/decisions.json").read_text())[
        "responses"
    ]
    responses["uav_alpha"][0]["accept"] = True
    cfg["provider"] = {
        "mode": "stub",
        "model": "live-path-rule-counterexample",
        "responses": {
            key: [completion(row) for row in rows] for key, rows in responses.items()
        },
    }
    document["run"]["until_ns"] = 17_000_000_000
    scenario = load_scenario(document, base=SCENARIO)
    with RunSession(scenario, tmp_path / "run") as session:
        session.run()
    rows = trace(tmp_path / "run")
    assert any(
        schema == "traffic.proposal.bid"
        and p["actor"]["$ref"]["id"] == "uav.alpha"
        and p["accept"] is True
        for _, schema, p in rows
    )
    assert {
        p["actor"]["$ref"]["id"]
        for _, schema, p in rows
        if schema == "traffic.award.proposed"
    } == {"uav.bravo"}
    assert any(schema == "traffic.award.committed" for _, schema, _ in rows)


@pytest.mark.llm
def test_live_flagship_cli_and_zero_call_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reuse the explicitly supplied completed CLI run in verification; otherwise
    # this marked test performs a complete new live run.
    existing = os.environ.get("AAS_LIVE_ACCIDENT_RUN")
    if existing is None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "aeroagentsim.services.cli",
                "run",
                str(SCENARIO / "scenario.yaml"),
                "--engine-profile",
                str(PROFILE),
                "--out",
                str(tmp_path / "runs"),
            ],
            text=True,
            capture_output=True,
            timeout=2100,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        path = Path(json.loads(result.stdout)["run"])
    else:
        path = Path(existing)
    rows = trace(path)
    assert any(schema == "traffic.capture.accepted" for _, schema, _ in rows)
    assert any(
        schema == "aas.behaviour.completed"
        and p["templateId"] == "traffic.capture_execute"
        and p["state"] == "completed"
        for _, schema, p in rows
    )
    assert {
        p["actor"]["$ref"]["id"]
        for _, schema, p in rows
        if schema == "traffic.award.proposed"
    } == {"uav.bravo"}
    with (path / "journal.jsonl").open("rb") as stream:
        data = b"".join(line for line in stream if b"aas.langgraph.record" in line)
    transcripts = journal_decisions(data)
    assert len(transcripts) == 2
    calls = [
        row["data"]
        for rows in transcripts.values()
        for row in rows
        if row["phase"] == "model_call"
    ]
    assert len(calls) >= 3
    expected_model = os.environ["AAS_LLM_MODEL"]
    assert all(call["request"]["model"] == expected_model for call in calls)
    console = [
        m
        for record in (json.loads(line) for line in data.splitlines())
        if record["type"] != "header"
        for m in project(record)["messages"]
        if m["schemaId"] == "aas.agent.record"
    ]
    assert sum(m["payload"]["phase"] == "response" for m in console) == len(calls)
    monkeypatch.setattr(
        OpenAIProvider, "complete", lambda *a, **kw: pytest.fail("network replay")
    )
    monkeypatch.setattr(
        ScriptedProvider, "scripted", lambda *a, **kw: pytest.fail("script replay")
    )
    for decision in transcripts:
        assert replay_graph(data, decision)["outputs"]
    assert not replay(path / "journal.jsonl").incomplete
