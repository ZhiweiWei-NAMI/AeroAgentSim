"""Synthetic committed projection costs; no budgets or benchmark results.

From the repository root, with the platform/server dependencies available:
PYTHONPATH=src:aerokernel python tools/bench/projection_costs.py --help

JSON goes to stdout. Setup, cache warming and output encoding are not timed.
The listing probe calls the real route directly, without a server or HTTP encoding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import aerokernel
from aerokernel import (
    BindingManifest,
    Emit,
    Journal,
    Kernel,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Timing,
    canonical_json,
)
from aerokernel.sdk import SimpleEngine

import aeroagentsim
from aeroagentsim import metrics_api
from aeroagentsim.packs import metric_helpers, task_metrics
from aeroagentsim.services import feed_index
from aeroagentsim.services import storage as storage_service
from aeroagentsim.services.app import create_app
from aeroagentsim.services.kpis import KPIProjector
from aeroagentsim.services.storage import RunStorage


class Completions(SimpleEngine):
    """One created/completed pair per step, with deterministic nonordered latencies."""

    def integrate(self, view: Any) -> tuple[object, ...]:
        sequence = view.instant.ns
        task = f"task:{sequence}"
        return (
            Emit("event", "lowalt.task.created", "metrics", view.instant, {
                "task": task, "kind": "medical" if sequence % 3 == 0 else "parcel",
                "priority": 1,
            }),
            Emit("event", "lowalt.task.completed", "metrics", view.instant, {
                "task": task, "vehicle": "synthetic-uav",
                "latency_s": ((sequence * 37) % 997) / 10,
            }),
        )


def build_run(path: Path, records: int, args: argparse.Namespace) -> RunStorage:
    """Use public kernel execution, not hand-authored WAL rows or fake ACKs.

    The target excludes the header; close may add a run_stop control record.
    Actual record and completion counts are reported, not inferred from the target.
    """
    path.mkdir()

    def schema(members: dict[str, Any]) -> dict[str, Any]:
        return {"type": "record", "members": members,
                "required": list(members), "extra": False}

    registry = MemoryRegistry((), messages=(
        MessageDescriptor("lowalt.task.created", "event", schema({
            "task": {"type": "string"}, "kind": {"type": "string"},
            "priority": {"type": "integer"},
        })),
        MessageDescriptor("lowalt.task.completed", "event", schema({
            "task": {"type": "string"}, "vehicle": {"type": "string"},
            "latency_s": {"type": "number"},
        })),
    ))
    document = {"id": "projection-costs", "registry": {"messages": []}}
    (path / "scenario.json").write_bytes(canonical_json(document))
    (path / "runtime.registry.json").write_bytes(canonical_json(registry.to_data()))
    storage = RunStorage(path)
    storage._atomic("index.json", [])
    storage._atomic("manifest.json", {
        "id": path.name, "kernel_run_id": "projection-costs", "status": "running",
    })
    engine = Completions(Partition(
        "completions", "completions", timing=Timing("fixed_step", 1),
        emits=("lowalt.task.created", "lowalt.task.completed"),
        message_targets=("metrics",),
    ))
    kernel = Kernel(
        journal=Journal(path / "journal.jsonl", durability=args.durability),
        configuration=document, provenance=args.provenance, root_seed=0,
    )
    try:
        kernel.bind(registry, BindingManifest("projection-costs", "0"), (engine,))
        kernel.start()
        kernel.run_until(
            records, on_poll=lambda: kernel.acknowledged_prefix[1] < records,
        )
    finally:
        kernel.close()
    # A yielded prefix is stopped, not proof of reaching the simulation limit.
    prefix = kernel.acknowledged_prefix
    storage.status("stopped", acknowledged_prefix=prefix)
    storage.publish_prefix(*prefix)
    return storage


@contextmanager
def operation_counts() -> Iterator[dict[str, int]]:
    """Count real calls/items; do not retain their inputs or replace their results."""
    counts: dict[str, int] = {}

    def wrap(owner: Any, name: str, key: str, *, result_bytes: bool = False) -> Any:
        original = getattr(owner, name)
        counts[key + "_calls"] = 0
        if result_bytes:
            counts[key + "_bytes"] = 0

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            counts[key + "_calls"] += 1
            result = original(*args, **kwargs)
            if result_bytes:
                counts[key + "_bytes"] += len(result)
            return result

        return patch.object(owner, name, wrapped)

    def sort_hook(owner: Any, key: str) -> Any:
        # Module-local builtin lookup, not a process-wide replacement of sorted.
        original = getattr(owner, "sorted", sorted)
        counts[key + "_calls"] = counts[key + "_items"] = 0

        def ordered(values: Any, *args: Any, **kwargs: Any) -> Any:
            counts[key + "_calls"] += 1
            counts[key + "_items"] += len(values)
            return original(values, *args, **kwargs)

        return patch.object(owner, "sorted", ordered, create=True)

    counts["sqlite_execute_calls"] = counts["checkpoint_write_calls"] = 0

    class CountedConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            counts["sqlite_execute_calls"] += 1
            if sql.startswith("INSERT OR REPLACE INTO checkpoint "):
                counts["checkpoint_write_calls"] += 1
            return super().execute(sql, parameters)

    connect = sqlite3.connect

    def counted_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return connect(*args, **{**kwargs, "factory": CountedConnection})

    cached_index = storage_service._read_index
    cache_misses = cached_index.cache_info().misses
    with ExitStack() as stack:
        stack.enter_context(wrap(storage_service, "_read_index", "offset_index_lookup"))
        entries_type = getattr(storage_service, "_OffsetEntries", None)
        if entries_type is not None:
            stack.enter_context(wrap(entries_type, "_detach_row", "offset_row_detach"))
        for owner, name, key in (
            (RunStorage, "__init__", "storage_construct"),
            (RunStorage, "records", "storage_records"),
            (RunStorage, "index", "storage_index"),
            (KPIProjector, "snapshot", "kpi_snapshot"),
            (metrics_api.MetricAccumulator, "cursor", "metric_cursor"),
        ):
            stack.enter_context(wrap(owner, name, key))
        stack.enter_context(wrap(feed_index, "_encode", "projection_encode", result_bytes=True))
        stack.enter_context(sort_hook(task_metrics, "latency_sort"))
        stack.enter_context(sort_hook(metrics_api, "dedup_sort"))
        stack.enter_context(sort_hook(metric_helpers, "legacy_state_sort"))
        stack.enter_context(patch.object(sqlite3, "connect", counted_connect))
        try:
            yield counts
        finally:
            counts["offset_index_cache_misses"] = cached_index.cache_info().misses - cache_misses


@contextmanager
def measured(samples: list[dict[str, Any]], name: str) -> Iterator[dict[str, Any]]:
    sample: dict[str, Any] = {"operation": name, "completed": False, "outcome": None}
    with operation_counts() as counts:
        started = time.perf_counter()
        try:
            yield sample
            sample["completed"] = True
        finally:
            sample["wall_s"] = time.perf_counter() - started
            sample["operation_counts"] = counts
            samples.append(sample)


def checkpoint(path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(path / feed_index.FILENAME)) as database:
        return feed_index._decode(database.execute(
            "SELECT data FROM checkpoint WHERE id=1"
        ).fetchone()[0])


def projection_workload(path: Path, size: int, args: argparse.Namespace,
                        result: dict[str, Any]) -> None:
    storage = build_run(path, size, args)
    end = storage.entries[-1]["index"]
    if end <= args.repeat:
        raise ValueError("Record target must leave at least repeat+1 committed records")
    initial = storage.entries[end - args.repeat]
    storage.publish_prefix(initial["offset"] + initial["length"], initial["index"])
    feed_index.update(path)  # All initial setup/warming is outside measurements.
    for index in range(initial["index"] + 1, end + 1):
        entry = storage.entries[index]
        storage.publish_prefix(entry["offset"] + entry["length"], entry["index"])
        with measured(result["samples"], "checkpoint_write") as sample:
            feed_index.update(path, max_batches=1)
        state = checkpoint(path)
        sample["outcome"] = {
            "results_cursor": state["next"],
            "completions": state["final"]["tasks_completed"]["value"],
        }

    state = checkpoint(path)
    result["workload"] = {
        "requested_committed_records": size,
        "committed_records_excluding_header": end,
        "completions": state["final"]["tasks_completed"]["value"],
        "medical_completions": (
            len(state["kpis"]["medical_latencies"])
            if "medical_latencies" in state["kpis"] else None
        ),
        "journal_bytes": storage.offset,
        "journal_sha256": hashlib.sha256((path / "journal.jsonl").read_bytes()).hexdigest(),
        "projection_provenance": state.get("projection_provenance"),
    }
    start = max(1, end - args.page_limit + 1)
    feed_index.page(path, start, args.page_limit)
    for _ in range(args.repeat):
        with measured(result["samples"], "warm_feed_index.page") as sample:
            rows = feed_index.page(path, start, args.page_limit)
        sample["outcome"] = {"start": start, "returned_cuts": len(rows)}

    registry = MemoryRegistry.from_data(json.loads((path / "runtime.registry.json").read_text()))
    projector = KPIProjector(registry)
    projector.restore_cursor(state["kpis"])
    # Separate a cold restored snapshot from subsequent observation-free reads.
    for index in range(args.repeat + 1):
        name = "kpi_snapshot_after_restore" if index == 0 else "kpi_snapshot_warm"
        with measured(result["samples"], name) as sample:
            values = projector.snapshot()
        if values != state["final"]:
            raise AssertionError("Restored KPI snapshot differs from the committed checkpoint")
        sample["outcome"] = {"completions": values["tasks_completed"]["value"],
                             "identical_to_checkpoint": True}


def listing_workload(root: Path, count: int, args: argparse.Namespace,
                     result: dict[str, Any]) -> None:
    root.mkdir()
    for index in range(count):
        storage = build_run(root / f"run-{index:06d}", args.listing_records, args)
        feed_index.update(storage.directory)
    app = create_app(root)
    endpoint = next(
        route.endpoint for route in app.routes
        if route.path == "/v1/runs" and "GET" in getattr(route, "methods", ())
    )
    result["workload"] = {"runs": count, "record_target_per_run": args.listing_records}
    for include_kpis in (False, True):
        endpoint(include_kpis=include_kpis)
        for _ in range(args.repeat):
            with measured(result["samples"], "run_listing") as sample:
                rows = endpoint(include_kpis=include_kpis)
            sample["outcome"] = {"include_kpis": include_kpis, "returned_runs": len(rows)}


def provenance(args: argparse.Namespace) -> dict[str, Any]:
    def source(package: Any) -> dict[str, Any]:
        root = Path(package.__file__).resolve().parent
        digest = hashlib.sha256()
        files = sorted(root.rglob("*.py"))
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        try:
            release = version(package.__name__)
        except PackageNotFoundError:
            release = None
        return {"path": str(root), "distribution_version": release,
                "python_source_files": len(files), "source_sha256": digest.hexdigest()}

    try:
        affinity_count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity_count = None
    return {
        "python": sys.version, "implementation": platform.python_implementation(),
        "python_build": platform.python_build(), "executable": sys.executable,
        "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor() or None, "logical_cpu_count": os.cpu_count(),
        "cpu_affinity_count": affinity_count,
        "hash_seed": os.environ.get("PYTHONHASHSEED"),
        "revision_label_unverified": args.revision,
        "kernel_provenance": args.provenance, "journal_durability": args.durability,
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "kernel": source(aerokernel), "platform_package": source(aeroagentsim),
        "metric_plugins": metrics_api.metric_plugin_fingerprint(metrics_api.discover_metric_plugins()),
    }


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=positive, default=[1000, 10000, 50000])
    parser.add_argument("--run-counts", nargs="+", type=positive, default=[1, 10, 50])
    parser.add_argument("--listing-records", type=positive, default=32)
    parser.add_argument("--repeat", type=positive, default=3)
    parser.add_argument("--page-limit", type=positive, default=32)
    parser.add_argument("--provenance", choices=("lean", "full"), default="lean")
    parser.add_argument("--durability", choices=("flush", "fsync"), default="flush")
    parser.add_argument("--work-dir", type=Path, help="Existing parent for temporary runs; defaults to system temp")
    parser.add_argument("--revision", help="Caller-supplied label; source hashes are reported separately")
    args = parser.parse_args()
    if min(args.sizes) <= args.repeat:
        parser.error("Every size must exceed repeat")
    output: dict[str, Any] = {
        "format": "aeroagentsim.projection-costs/v1", "budget": None,
        "hardware_provenance": provenance(args),
        "parameters": {key: str(value) if isinstance(value, Path) else value
                       for key, value in vars(args).items()},
        "workloads": [],
    }
    failed = False
    with TemporaryDirectory(prefix="projection-costs-", dir=args.work_dir) as directory:
        for kind, sizes, workload in (
            ("projections", args.sizes, projection_workload),
            ("listings", args.run_counts, listing_workload),
        ):
            for index, size in enumerate(sizes):
                result: dict[str, Any] = {
                    "kind": kind, "size": size, "samples": [], "workload": None,
                    "completed": False, "failure": None,
                }
                output["workloads"].append(result)
                try:
                    workload(Path(directory) / f"{kind}-{index}", size, args, result)
                    result["completed"] = True
                except Exception as exc:
                    failed = True
                    result["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
    print(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
