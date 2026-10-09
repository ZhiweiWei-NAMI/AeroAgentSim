"""Exact legacy/2.0 evidence and committed cache reuse."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aerokernel import Journal
from aerokernel.compact import expand_record
from aerokernel.ids import FramePrefix, ItemRef
from aerokernel.journal import iter_records
from aerokernel.sdk import EngineContext
from aerokernel.state import StateView

from aeroagentsim.engines import predicate
from aeroagentsim.engines.predicate import SampleHistory, sdk_frame_causes
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario


class FullHistory(SampleHistory):
    """The unchanged Q6 evaluator with every preceding sampled input retained."""

    def append(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)
        self.times.append(frame["t"])


def expanded(path: Path) -> list[dict[str, Any]]:
    frames: dict[str, list[Any]] = {}

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            result = []
            for child in value:
                if isinstance(child, dict) and child.get("$type") == "FramePrefix":
                    f = child["fields"]
                    prior = frames[f["context_id"]]
                    assert (prior[f["count"] - 1] if f["count"] else None) == f[
                        "through"
                    ]
                    result.extend(prior[: f["count"]])
                else:
                    result.append(visit(child))
            return result
        if isinstance(value, dict):
            return {k: visit(v) for k, v in value.items()}
        return value

    records = []
    for wire in iter_records(path):
        record = expand_record(wire)
        if record.get("major") == 2:
            record = {
                k: v
                for k, v in record.items()
                if k not in {"codec", "semantic_version"}
            }
            record.update(major=1, minor=wire["semantic_version"])
        records.append(visit(record))
        for item in record.get("items", []):
            if "sample_frame" in item:
                f = item["sample_frame"]["fields"]
                frames.setdefault(f["frame"]["fields"]["context_id"], []).append(
                    f["version"]
                )
    return records


def test_full_history_legacy_and_incremental_prefix_records_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_type = SampleHistory
    calls = 0
    original = StateView.sample_frames

    def observed(view: StateView, context_id: str, known_at: Any = None) -> Any:
        nonlocal calls
        calls += 1
        return original(view, context_id, known_at)

    paths = []
    for name, codec, implementation in (
        ("full", "json", FullHistory),
        ("bounded", "positional-deflate", history_type),
    ):
        monkeypatch.setattr(predicate, "SampleHistory", implementation)
        monkeypatch.setattr(StateView, "sample_frames", observed)
        calls = 0
        path = tmp_path / f"{name}.jsonl"
        sim = Simulation(
            load_scenario(Path("scenarios/predicates-demo.yaml")),
            journal=Journal(path, codec=codec),
        )
        try:
            sim.start()
            sim.run_until(7_000_000_000)
            if codec == "positional-deflate":
                assert calls == 1  # Cold start only; no history traversal per callback.
                engine = sim.kernel._engines["predicate"]
                assert isinstance(engine, predicate.Predicate)
                assert len(engine.history.frames) <= 4
        finally:
            sim.close()
        paths.append(path)
    assert expanded(paths[0]) == expanded(paths[1])


def test_sdk_prefix_overlap_preserves_order_and_raw_duplicates(tmp_path: Path) -> None:
    sim = Simulation(
        load_scenario(Path("scenarios/predicates-demo.yaml")),
        journal=Journal(tmp_path / "overlap.jsonl", codec="positional-deflate"),
    )
    try:
        sim.start()
        sim.run_until(7_000_000_000)
        view = sim.kernel.view()
        prefix = view.sample_frame_prefix("stationary")
        refs = [f.version for f in view.sample_frames("stationary")]
        ctx = EngineContext(view)
        earlier = [refs[1], refs[1]]
        later = [refs[-1], refs[0], refs[1]]
        ctx.inputs = [*earlier, prefix, *later]
        raw = tuple(ctx.inputs)
        sdk_frame_causes(ctx, prefix, len(earlier))

        def flatten(causes: tuple[Any, ...]) -> list[ItemRef]:
            return [
                ref
                for cause in causes
                for ref in (
                    refs[: cause.count] if isinstance(cause, FramePrefix) else [cause]
                )
            ]

        assert flatten(raw) == [*earlier, *refs, *later]
        assert flatten(ctx._causes()) == list(dict.fromkeys([*earlier, *refs, *later]))
    finally:
        sim.close()
