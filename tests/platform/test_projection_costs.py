"""Exact projection semantics and operation counts, never wall-time budgets."""

from __future__ import annotations

import argparse
import json
import math
import operator
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from aerokernel import Instant, MemoryRegistry, canonical_json
from aerokernel.codec import encode
from fastapi.testclient import TestClient

from aeroagentsim.metrics_api import (
    MetricAccumulator,
    MetricDescriptor,
    MetricPlugin,
    builtin_metric_plugins,
)
from aeroagentsim.packs import task_metrics
from aeroagentsim.runs import RecordedMessage
from aeroagentsim.services import feed_index, storage
from aeroagentsim.services.app import create_app
from aeroagentsim.services.kpis import KPIProjector, project_run
from tools.bench.projection_costs import build_run, listing_workload


def message(index: int, schema: str, payload: dict[str, Any]) -> RecordedMessage:
    return RecordedMessage(
        cursor=(index, 0), message_id=f"projection-costs/{index}", kind="event",
        schema_id=schema, source_kind="partition", source="completions",
        at_ns=index * 1_000_000_000, microstep=0, payload=payload,
    )


def completions(count: int) -> Iterator[RecordedMessage]:
    for index in range(count):
        yield message(index * 2 + 1, "lowalt.task.created", {
            "task": f"task:{index}", "kind": "medical" if index % 3 == 0 else "parcel",
            "priority": 1,
        })
        yield message(index * 2 + 2, "lowalt.task.completed", {
            "task": f"task:{index}", "vehicle": "synthetic-uav", "latency_s": latency(index),
        })


def latency(index: int) -> float:
    # Nonordered, repeated fractional values plus an order-sensitive mean sample.
    return 1e16 if index == 4 else ((index * 37) % 997) / 10


def percentile(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * 0.95
    lo, hi = math.floor(index), math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


@pytest.mark.parametrize("count", [0, 1, 257, 2048])
def test_many_completions_match_original_reducers_exactly(count: int) -> None:
    plugins = builtin_metric_plugins()
    original = MetricAccumulator(plugins)
    projector = KPIProjector(MemoryRegistry(()), plugins=plugins)
    for observation in completions(count):
        original.consume(observation)
        projector.consume(observation)
    expected = original.snapshot()
    actual = projector.snapshot()
    assert {name: row["value"] for name, row in actual.items()} == expected
    for prefix, samples in (
        ("", [latency(index) for index in range(count)]),
        ("medical_", [latency(index) for index in range(count) if index % 3 == 0]),
    ):
        assert actual[prefix + "mean_task_latency_s"]["value"] == (
            sum(samples) / len(samples) if samples else None
        )
        assert actual[prefix + "p95_task_latency_s"]["value"] == percentile(samples)
    assert actual["tasks_completed"]["value"] == count
    assert actual["energy_used_kj"]["value"] is None
    assert actual["rescue_success"]["value"] is None
    expected_rows = {
        name: {
            "value": expected[name], "unit": descriptor.unit, "label": descriptor.label,
            "label_zh": descriptor.label_zh, "direction": descriptor.direction,
            "aggregation": descriptor.aggregation,
            "missing_data_policy": descriptor.missing_data_policy,
        }
        for name, descriptor in projector.descriptors.items()
    }
    assert canonical_json(actual) == canonical_json(expected_rows)
    cursor = projector.cursor()
    assert {key: value for key, value in cursor.items() if key != "message_context"} == original.cursor()
    restored = KPIProjector.from_cursor(
        MemoryRegistry(()), json.loads(json.dumps(cursor)), plugins=plugins,
    )
    assert canonical_json(restored.snapshot()) == canonical_json(actual)
    next_message = message(count * 2 + 1, "lowalt.task.completed", {
        "task": "after-restore", "vehicle": "synthetic-uav", "latency_s": 0.1,
    })
    original.consume(next_message)
    restored.consume(next_message)
    assert {name: row["value"] for name, row in restored.snapshot().items()} == original.snapshot()


@pytest.mark.parametrize("count", [64, 1024])
def test_latency_sorts_follow_new_observations_not_snapshot_count(
    count: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    projector = KPIProjector(MemoryRegistry(()), plugins=builtin_metric_plugins())
    observations = list(completions(count))
    for observation in observations:
        projector.consume(observation)
    sorts: list[int] = []

    def ordered(values: list[float]) -> list[float]:
        sorts.append(len(values))
        return sorted(values)

    monkeypatch.setattr(task_metrics, "sorted", ordered, raising=False)
    for index in range(40):
        projector.snapshot()
        projector.consume(observations[-1])  # Exact message-ID duplicate.
        projector.consume({"index": count * 2 + index + 1,
                           "instant": encode(Instant(index)), "items": []})
        projector.consume(message(count * 3 + index, "lowalt.weather.hold", {
            "vehicle": "synthetic-uav", "reason": "synthetic",
        }))  # A different reducer changes, not the task population.
    assert sorts == []

    new = message(count * 4 + 1, "lowalt.task.completed", {
        "task": "new-completion", "vehicle": "synthetic-uav", "latency_s": 3.25,
    })
    projector.consume(new)
    assert sorts == [count + 1, len(range(0, count, 3))]
    for _ in range(40):
        projector.consume(new)
        projector.snapshot()
    assert len(sorts) == 2

    restored = KPIProjector.from_cursor(
        MemoryRegistry(()), projector.cursor(), plugins=builtin_metric_plugins(),
    )
    restored.snapshot()
    assert len(sorts) == 4  # One cold evaluation per nonempty restored population.
    for _ in range(40):
        restored.snapshot()
    assert len(sorts) == 4


class _ExternalReducer:
    descriptors = (MetricDescriptor(
        "external.samples", "Samples", "样本", "count", None,
        "Count matching observations.", "None before the first observation.",
    ),)
    input_schemas = ("external.sample",)

    def __init__(self) -> None:
        self.snapshot_calls = 0

    def initial(self) -> dict[str, int]:
        return {"samples": 0}

    def consume(self, state: dict[str, int], observation: RecordedMessage) -> None:
        state["samples"] += 1

    def snapshot(self, state: dict[str, int]) -> dict[str, int | None]:
        self.snapshot_calls += 1
        return {"external.samples": state["samples"] or None}


def test_snapshot_cache_preserves_external_reducer_identity_and_restore() -> None:
    reducer = _ExternalReducer()
    plugins = (MetricPlugin("external", (reducer,)),)
    projector = KPIProjector(MemoryRegistry(()), plugins=plugins)
    assert projector.snapshot()["external.samples"]["value"] is None
    projector.snapshot()
    assert reducer.snapshot_calls == 1
    sample = message(1, "external.sample/v1", {})
    projector.consume(sample)
    assert reducer.snapshot_calls == 2
    projector.consume(sample)
    projector.consume(message(2, "unrelated", {}))
    assert reducer.snapshot_calls == 2
    checkpoint = projector.cursor()
    original = MetricAccumulator(plugins)
    original.consume(sample)
    original.consume(message(2, "unrelated", {}))
    assert checkpoint["selection"] == original.cursor()["selection"]
    projector.restore_cursor(checkpoint)
    assert projector.snapshot()["external.samples"]["value"] == 1
    assert reducer.snapshot_calls == 3
    # Changing a returned presentation row cannot poison a later cached read.
    values = projector.snapshot()
    values["external.samples"]["value"] = 99
    assert projector.snapshot()["external.samples"]["value"] == 1


def test_failed_reducer_invalidates_cached_snapshot_and_requires_restore() -> None:
    class FailingReducer(_ExternalReducer):
        def consume(self, state: dict[str, int], observation: RecordedMessage) -> None:
            super().consume(state, observation)
            if observation.payload.get("fail"):
                raise RuntimeError("reducer failed after mutation")

    reducer = FailingReducer()
    projector = KPIProjector(
        MemoryRegistry(()), plugins=(MetricPlugin("external", (reducer,)),),
    )
    expected = projector.consume(message(1, "external.sample/v1", {}))
    checkpoint = projector.cursor()
    with pytest.raises(RuntimeError, match="failed after mutation"):
        projector.consume(message(2, "external.sample/v1", {"fail": True}))
    # Diagnostic reads must expose the actual partially mutated state, not the
    # previously cached success. It must still be impossible to checkpoint it.
    assert projector.snapshot()["external.samples"]["value"] == 2
    with pytest.raises(ValueError, match="cannot checkpoint partial reduction"):
        projector.cursor()
    projector.restore_cursor(checkpoint)
    assert projector.snapshot() == expected
    assert projector.cursor() == checkpoint


def test_restore_invalidates_even_when_a_reducer_reuses_its_state_object() -> None:
    class ReusedStateReducer(_ExternalReducer):
        version = "1"

        def initial(self) -> dict[str, int]:
            self.state = super().initial()
            return self.state

        def checkpoint(self, state: dict[str, int]) -> dict[str, int]:
            return dict(state)

        def restore(self, checkpoint: dict[str, int]) -> dict[str, int]:
            self.state.clear()
            self.state.update(checkpoint)
            return self.state

    reducer = ReusedStateReducer()
    projector = KPIProjector(
        MemoryRegistry(()), plugins=(MetricPlugin("external", (reducer,)),),
    )
    empty = projector.cursor()
    projector.consume(message(1, "external.sample/v1", {}))
    assert projector.snapshot()["external.samples"]["value"] == 1
    projector.restore_cursor(empty)
    assert projector.snapshot()["external.samples"]["value"] is None
    assert reducer.snapshot_calls == 2


@pytest.fixture(params=["lean", "full"])
def committed_run(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    build_run(tmp_path / "run", 300, argparse.Namespace(
        durability="flush", provenance=request.param,
    ))
    return tmp_path / "run"


def test_paged_series_equal_unpaged_and_leave_summary_bytes_unchanged(
    committed_run: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    monkeypatch.delenv("AEROAGENTSIM_CONSOLE_URL", raising=False)
    full = feed_index.kpi_result(committed_run)
    summary_bytes = canonical_json(feed_index.kpi_result(committed_run, include_series=False))
    assert project_run(committed_run) == full
    assert canonical_json(project_run(committed_run, include_series=False)) == summary_bytes
    with TestClient(create_app(committed_run.parent), base_url="http://localhost") as client:
        url = f"/v1/runs/{committed_run.name}/kpis"
        default = client.get(url)
        assert default.status_code == 200, default.text
        assert default.json() == {**full, "run_id": committed_run.name}
        seen: list[dict[str, Any]] = []
        start = 0
        for _ in range(len(full["series"]) // 17 + 2):
            response = client.get(url, params={"series_from": start, "series_limit": 17})
            assert response.status_code == 200, response.text
            page = response.json()
            assert {key: value for key, value in page.items() if key != "series"} == {
                key: value for key, value in default.json().items() if key != "series"
            }
            assert len(page["series"]) <= 17
            if not page["series"]:
                break
            seen.extend(page["series"])
            start = page["series"][-1]["commitIndex"] + 1
        else:
            pytest.fail("Series paging did not reach the end of the fixed prefix")
        assert seen == full["series"]
        assert client.get(url, params={"series_limit": 3}).json()["series"] == seen[:3]
        start = seen[12]["commitIndex"]
        assert client.get(url, params={"series_from": start}).json()["series"] == seen[12:]
        future = client.get(url, params={"series_from": 1 << 80, "series_limit": 3})
        assert future.status_code == 200, future.text
        assert future.json() == {**default.json(), "series": []}
        assert canonical_json(project_run(
            committed_run, include_series=False, series_from=5, series_limit=3,
        )) == summary_bytes
        assert canonical_json(feed_index.kpi_result(committed_run, include_series=False)) == summary_bytes
        for params in ({"series_from": -1}, {"series_limit": 0},
                       {"series_limit": -1}, {"series_limit": 4097},
                       {"series_limit": "not-an-integer"}):
            assert client.get(url, params=params).status_code == 422


def test_series_window_is_bounded_in_sql(
    committed_run: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = feed_index.kpi_result(committed_run)
    queries: list[tuple[str, Any]] = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.startswith("SELECT idx, at_ns, kpis FROM cuts"):
                queries.append((sql, parameters))
            return super().execute(sql, parameters)

    def tracked(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return connect(*args, **{**kwargs, "factory": TrackedConnection})

    monkeypatch.setattr(sqlite3, "connect", tracked)
    result = project_run(committed_run, series_from=5, series_limit=3)
    assert result["series"] == [row for row in full["series"] if row["commitIndex"] >= 5][:3]
    assert queries == [(
        "SELECT idx, at_ns, kpis FROM cuts WHERE idx>=? ORDER BY idx LIMIT ?", (5, 3),
    )]


def stored_projection(path: Path) -> tuple[Any, ...]:
    with closing(sqlite3.connect(path / feed_index.FILENAME)) as database:
        return (
            database.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()[0],
            database.execute("SELECT next, signature FROM projection_progress WHERE id=1").fetchone(),
            database.execute("SELECT idx, payload, kpis, cut_bytes FROM cuts ORDER BY idx").fetchall(),
        )


def test_failed_batch_keeps_all_checkpoint_cursors_and_cuts_unchanged(
    committed_run: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    feed_index.update(committed_run, through_index=65)
    before = stored_projection(committed_run)
    original = feed_index.NarrativeProjector.consume

    def fail_after_consuming(self: Any, record: dict[str, Any]) -> Any:
        result = original(self, record)
        if record["index"] == 70:
            raise RuntimeError("Interrupted after KPI and narrative consumption")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(feed_index.NarrativeProjector, "consume", fail_after_consuming)
        with pytest.raises(RuntimeError, match="Interrupted after KPI"):
            feed_index.update(committed_run, max_batches=1)
    # Exact checkpoint bytes cover subjects, KPI dedup/sample state, narratives,
    # next cursor, final snapshot and provenance, not just projection_progress.
    assert stored_projection(committed_run) == before
    assert feed_index.results_state(committed_run)["results_cursor"] == 65
    feed_index.update(committed_run)
    projector = KPIProjector.from_directory(committed_run)
    source = storage.RunStorage(committed_run)
    source.refresh_committed()
    for record in source.records(1, len(source.entries)):
        projector.consume(record)
    assert feed_index.kpi_result(committed_run)["final"] == projector.snapshot()


@pytest.mark.parametrize("count", [32, 2048])
def test_warm_storage_construction_detaches_only_accessed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int,
) -> None:
    rows = [{"index": index, "offset": index * 10, "length": 10} for index in range(count)]
    owner = storage.RunStorage(tmp_path)
    owner._atomic("index.json", rows)
    warm = storage.RunStorage(tmp_path)
    assert isinstance(warm.entries, storage._OffsetEntries)
    shared = warm.entries._shared
    detached: list[int] = []
    original = storage._OffsetEntries._detach_row

    def detach(self: Any, index: int) -> dict[str, int]:
        detached.append(index)
        return original(self, index)

    monkeypatch.setattr(storage._OffsetEntries, "_detach_row", detach)
    readers = [storage.RunStorage(tmp_path) for _ in range(12)]
    assert detached == [count - 1] * len(readers)
    for reader in readers:
        assert isinstance(reader.entries, storage._OffsetEntries)
        assert reader.entries._shared is shared
        assert len(reader.entries._private) == 1
    first, second = readers[:2]
    first.entries[0]["length"] = 99
    first.entries.append({"index": count, "offset": count * 10, "length": 5})
    assert len(detached) == len(readers) + 1  # Append does not copy the prefix.
    with pytest.raises(TypeError):
        operator.setitem(shared[0], "length", 99)
    assert second.entries == rows
    assert storage.RunStorage(tmp_path).entries == rows
    first._atomic("index.json", first.entries)
    assert storage.RunStorage(tmp_path).entries == first.entries
    assert second.entries == rows  # Atomic publication does not mutate old views.


def test_shared_offset_entries_retain_mutable_sequence_operations() -> None:
    entries = storage._OffsetEntries((MappingProxyType({"index": 0, "offset": 0, "length": 10}),))
    entries.append({"index": 1, "offset": 10, "length": 20})
    expected = list(entries)
    assert entries[::-1] == expected[::-1]
    entries.insert(1, {"index": 2, "offset": 30, "length": 5})
    del entries[0]
    expected.insert(1, {"index": 2, "offset": 30, "length": 5})
    del expected[0]
    assert entries == expected
    entries[:] = expected[:1]
    assert entries == expected[:1]


def test_bench_listing_probe_measures_both_listing_modes(tmp_path: Path) -> None:
    """The listing probe calls the GET route, not POST /v1/runs."""
    args = argparse.Namespace(listing_records=8, repeat=1, provenance="lean", durability="flush")
    result: dict[str, Any] = {"samples": []}
    listing_workload(tmp_path / "runs", 2, args, result)
    modes = [sample["outcome"]["include_kpis"] for sample in result["samples"]]
    assert modes == [False, True]
    assert all(sample["outcome"]["returned_runs"] == 2 for sample in result["samples"])

