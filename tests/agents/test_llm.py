"""Real sixty-second dispatcher comparison; persists measured outcomes only."""

from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from aerokernel.journal import replay
from aerokernel.state import Fact
from aerokernel.values import thaw

from aeroagentsim.agents.provider import OpenAIProvider
from aeroagentsim.platform.simulation import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project
from aeroagentsim.services.storage import RunStorage


@pytest.mark.llm
def test_live_dispatch_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with urllib.request.urlopen(
        "http://127.0.0.1:8788/v1/models", timeout=10
    ) as response:
        models = json.load(response)
    assert os.environ["AAS_LLM_MODEL"] in [item["id"] for item in models["models"]]
    results: dict[str, Any] = {
        "model": os.environ["AAS_LLM_MODEL"],
        "simulated_s": 60,
        "runs": {},
    }
    model_calls = 0
    original = OpenAIProvider.complete

    def spy(
        self: OpenAIProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        nonlocal model_calls
        model_calls += 1
        return original(
            self, messages, tools, timeout_s=timeout_s, max_tokens=max_tokens
        )

    monkeypatch.setattr(OpenAIProvider, "complete", spy)
    live: Simulation | None = None
    for name, path in [
        ("llm", "scenarios/agents/llm-dispatch.yaml"),
        ("deterministic", "scenarios/p1-slice.yaml"),
    ]:
        start = time.perf_counter()
        simulation = Simulation(load_scenario(path))
        simulation.start()
        view = simulation.run_until(60_000_000_000)
        wall = time.perf_counter() - start
        completed: list[float] = []
        entries: list[dict[str, Any]] = []
        for ref in simulation.scenario.manifest.entities:
            if simulation.scenario.registry.is_a(ref.type_id, "oo:Order"):
                fact = view.field((ref, "aas.p1.order_state"), view.instant)
                if isinstance(fact, Fact) and thaw(fact.value) == "accepted":
                    completed.append(fact.valid.start.ns / 1e9)
        for record in simulation.kernel.iter_records():
            if record.get("type") == "header":
                continue
            for message in project(record)["messages"]:
                if message["schemaId"] == "aas.agent.record":
                    row = message["payload"]
                    entries.append(
                        {
                            **row,
                            "data": json.loads(row["data_json"]),
                            "sim_ns": message["at"]["ns"],
                        }
                    )
        summary: dict[str, Any] = {
            "orders_completed": len(completed),
            "mean_completion_time_s": sum(completed) / len(completed)
            if completed
            else None,
            "wall_s": wall,
        }
        if name == "llm":
            summary.update(
                decisions=len(
                    [row for row in entries if row["phase"] == "observation"]
                ),
                valid_tool_calls=len(
                    [
                        row
                        for row in entries
                        if row["phase"] == "validation" and row["data"]["valid"]
                    ]
                ),
                invalid_tool_calls=len(
                    [
                        row
                        for row in entries
                        if row["phase"] == "validation" and not row["data"]["valid"]
                    ]
                ),
                failures=[row["data"] for row in entries if row["phase"] == "failure"],
                model_calls=model_calls,
            )
            summary["command_proposals"] = sum(
                row["phase"] == "command" for row in entries
            )
            statuses = Counter(
                row["data"]["status"] for row in entries if row["phase"] == "receipt"
            )
            summary.update(
                {f"receipt_{status}": count for status, count in statuses.items()}
            )
            live = simulation
        else:
            simulation.close()
        results["runs"][name] = summary
    assert live is not None
    before = model_calls
    monkeypatch.setattr(
        OpenAIProvider, "complete", lambda *a, **kw: pytest.fail("replay called model")
    )
    start = time.perf_counter()
    recovered = replay(live.kernel.journal.bytes)
    results["replay"] = {
        "model_calls": model_calls - before,
        "wall_s": time.perf_counter() - start,
        "same_cut": recovered.view().cut == live.kernel.view().cut,
        "incomplete": recovered.incomplete,
    }
    # Live-model artifacts go outside the source tree unless explicitly requested.
    output = Path(os.environ.get("AEROAGENTSIM_LLM_ARTIFACTS", tmp_path))
    directory = output / f"live-60s-{uuid.uuid4().hex[:8]}"
    directory.parent.mkdir(parents=True, exist_ok=True)
    storage = RunStorage(directory)
    storage.prepare(live.scenario)
    (directory / "journal.jsonl").write_bytes(live.kernel.journal.bytes)
    storage.index()
    storage.status("completed")
    results["artifact"] = str(directory)
    (directory.parent / "llm-metrics.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )
    # Preserve actual prompts/responses and typed outcomes for review and CLI replay.
    print(json.dumps(results, indent=2))
    assert results["replay"]["same_cut"] and not recovered.incomplete
    assert model_calls >= 1
    assert results["runs"]["llm"]["valid_tool_calls"] >= 1
    assert results["runs"]["llm"]["orders_completed"] >= 1
    live.close()
