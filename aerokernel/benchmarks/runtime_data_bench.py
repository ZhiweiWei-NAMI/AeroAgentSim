"""Deterministic action-history and dirty-routing probes; no performance budgets.

From the repository root (no installation needed):
PYTHONPATH=aerokernel python aerokernel/benchmarks/runtime_data_bench.py --help

Only JSON is written to stdout. Counters wrap real operations, not cost estimates;
wall time and tracemalloc include their overhead. See docs/performance/runtime-data.md.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import sys
import time
import tracemalloc
from contextlib import ExitStack, contextmanager
from functools import wraps
from itertools import count
from pathlib import Path
from unittest.mock import patch

import aerokernel
import aerokernel.messages as messages
import aerokernel.transactions as transactions
from aerokernel import (
    BindingManifest,
    BindingRule,
    Create,
    Delivery,
    Dependency,
    EntityRef,
    FactWrite,
    Feedback,
    FieldDescriptor,
    Instant,
    Interval,
    ItemRef,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    Message,
    MessageDescriptor,
    Partition,
    Receipt,
    Stamp,
    Timing,
    TypeDescriptor,
    replay,
)
from aerokernel.messages import Actions
from aerokernel.sdk import SimpleEngine
from aerokernel.state import Store
from aerokernel.storage import Overlay

STEP_NS = 1_000_000


@contextmanager
def operation_counts():
    """Install serial, scoped wrappers without retaining arguments or results."""
    counts = {
        "action_history_freeze_calls": 0,
        "routing_partition_checks": 0,
        "field_dirty_items": 0,
    }
    depths = {"action": 0, "notify": 0}

    def wrap(owner, name, key, scope=None, observe=None):
        original = getattr(owner, name)
        counts[key] = 0

        @wraps(original)
        def wrapped(*args, **kwargs):
            counts[key] += 1
            if observe is not None:
                observe(*args, **kwargs)
            if scope is not None:
                depths[scope] += 1
            try:
                return original(*args, **kwargs)
            finally:
                if scope is not None:
                    depths[scope] -= 1

        return patch.object(owner, name, wrapped)

    def frozen(*_args, **_kwargs):
        if depths["action"]:
            counts["action_history_freeze_calls"] += 1

    def matched(*_args, **_kwargs):
        if depths["notify"]:
            counts["routing_partition_checks"] += 1

    def added(_candidate, item):
        if depths["notify"] and item.get("kind") == "dirty":
            counts["field_dirty_items"] += 1

    with ExitStack() as stack:
        for name in ("pending", "submit", "receipt", "feedback", "clone"):
            stack.enter_context(wrap(Actions, name, f"actions_{name}_calls"))
        stack.enter_context(wrap(Actions, "action", "actions_action_calls", "action"))
        stack.enter_context(
            wrap(messages, "freeze", "messages_freeze_calls", observe=frozen)
        )
        stack.enter_context(wrap(messages, "normalize", "messages_normalize_calls"))
        stack.enter_context(wrap(messages, "replace", "action_state_replace_calls"))
        stack.enter_context(wrap(Overlay, "fork", "overlay_fork_calls"))
        stack.enter_context(wrap(Store, "clone", "store_clone_calls"))
        stack.enter_context(
            wrap(transactions.Candidate, "notify_field", "notify_field_calls", "notify")
        )
        stack.enter_context(
            wrap(transactions, "dirty_matches", "dirty_matches_calls", observe=matched)
        )
        stack.enter_context(
            wrap(transactions.Candidate, "add", "candidate_add_calls", observe=added)
        )
        yield counts


@contextmanager
def measured(phases, name):
    """Measure one phase; peak includes previously traced live workload objects."""
    with operation_counts() as counts:
        baseline = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        started = time.perf_counter()
        completed = False
        try:
            yield
            completed = True
        finally:
            wall_s = time.perf_counter() - started
            current, peak = tracemalloc.get_traced_memory()
            phases.append(
                {
                    "name": name,
                    "completed": completed,
                    "operation_counts": counts,
                    "wall_s": wall_s,
                    "tracemalloc_start_bytes": baseline,
                    "tracemalloc_end_bytes": current,
                    "tracemalloc_peak_bytes": peak,
                }
            )


def action_workload(args, phases):
    """Seed real histories, then append on forks while retaining the initial view."""
    refs = count(1)

    def next_ref():
        return ItemRef(next(refs), 0)

    with measured(phases, "seed_histories"):
        registry = MemoryRegistry(
            (),
            messages=(
                MessageDescriptor(
                    "work", "command", {"type": "integer"},
                    feedback_schema={"type": "integer"},
                ),
            ),
        )
        actions = Actions(registry)
        actions.provenance = args.provenance
        dispatched = []
        for number in range(args.commands):
            origin = next_ref()
            message = Message(
                f"command:{number}", "command", "work", "ingress", "host", number,
                Instant(0), Instant(0), number, (), origin=origin,
            )
            actions.pending(message.id, "target", "ingress", "host", origin)
            submitted = next_ref()
            actions.submit(message, submitted)
            delivery = Delivery(message, "target", Instant(0, 1), origin, next_ref())
            actions.record_dispatch(delivery)
            head = submitted
            for status in ("accepted", "executing"):
                out = next_ref()
                actions.receipt(
                    "target", Receipt(message.id, status), out, (),
                    (delivery.dispatch_ref, head),
                )
                head = out
            dispatched.append((message.id, delivery.dispatch_ref, head))
            for progress in range(args.history):
                actions.feedback(
                    "target", Feedback(message.id, progress), next_ref(), (),
                    (delivery.dispatch_ref, head),
                )

    with measured(phases, "read_initial_views"):
        initial = actions
        views = tuple(actions.action(mid) for mid, _, _ in dispatched)

    with measured(phases, "append_feedback"):
        for progress in range(args.history, args.history + args.appends):
            actions = actions.clone()
            for mid, dispatch, head in dispatched:
                actions.feedback(
                    "target", Feedback(mid, progress), next_ref(), (), (dispatch, head)
                )

    with measured(phases, "verify_action_views"):
        digest = hashlib.sha256()
        history_entries = 0
        for (mid, _, head), old in zip(dispatched, views, strict=True):
            current = actions.action(mid)
            assert initial.action(mid) == old, "initial action store changed"
            assert len(old.history) == args.history + 3, "initial history changed"
            assert current.history[: len(old.history)] == old.history, "prefix changed"
            assert len(current.history) == args.history + args.appends + 3, (
                "feedback history length differs"
            )
            assert current.status == old.status == "executing", "feedback ended action"
            assert current.head == head, "feedback changed receipt head"
            history_entries += len(current.history)
            # Stream one entry at a time, without whole-history JSON expansion.
            for index, item in enumerate(current.history):
                if index >= 3:
                    assert item["kind"] == "feedback" and item["payload"] == index - 3, (
                        "feedback payload sequence differs"
                    )
                digest.update(
                    json.dumps(
                        messages.thaw(item), sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                )
                digest.update(b"\n")
    return {
        "commands": len(dispatched),
        "history_entries": history_entries,
        "status": "executing",
        "initial_views_unchanged": True,
        "history_sha256": digest.hexdigest(),
        "replay_checked": None,  # Direct Actions calls do not produce a journal.
    }


def routing_workload(args, phases):
    """Use real publication, dispatch and retained WAL across fixed-step waves."""
    ref = EntityRef("growth", "epoch", "entity", 0, "Entity")
    key = (ref, "value")

    class Writer(SimpleEngine):
        steps = 0

        def write(self, view):
            return FactWrite(
                key,
                self.steps,
                Stamp("canonical", view.instant.ns, 1, "canonical"),
                Interval(view.instant, None),
            )

        def initialize(self, view):
            return (Create(ref), self.write(view))

        def integrate(self, view):
            self.steps += 1
            return (self.write(view),)

    class Reader(SimpleEngine):
        field_notifications = 0

        def on_react(self, view, inbox, dirty):
            for notification in dirty:
                if notification.kind == "field":
                    assert notification.key == key, "unexpected dirty field"
                    self.field_notifications += 1
            return ()

    kernel = Kernel(provenance=args.provenance, root_seed=0)
    try:
        with measured(phases, "bind_and_start"):
            writer = Writer(
                Partition(
                    "writer", "writer", produces=("value",),
                    lifecycle=True, timing=Timing("fixed_step", STEP_NS),
                )
            )
            readers = tuple(
                Reader(
                    Partition(
                        f"reader:{i:06d}", f"reader:{i:06d}",
                        consumes=(Dependency("value"),) if i < args.subscribers else (),
                    )
                )
                for i in range(args.partitions - 1)
            )
            registry = MemoryRegistry(
                (TypeDescriptor("Entity"),),
                (FieldDescriptor("value", "Entity", {"type": "integer"}),),
            )
            manifest = BindingManifest(
                "growth", "epoch", (ref,),
                rules=(BindingRule("writer", "Entity", ("value",)),),
                lifecycle=(LifecycleRule("writer", "Entity"),),
            )
            kernel.bind(registry, manifest, (writer, *readers))
            kernel.start()
            initial = kernel.view()

        with measured(phases, "run_steps"):
            kernel.run_until(args.steps * STEP_NS)

        with measured(phases, "verify_routing"):
            assert writer.steps == args.steps, "writer did not finish requested steps"
            assert kernel.sealed_ns == args.steps * STEP_NS, "target is not sealed"
            final_at = Instant(kernel.sealed_ns)
            assert kernel.view().field(key, final_at).value == args.steps, "wrong value"
            assert initial.field(key, Instant(0)).value == 0, "initial view changed"
            assert len(kernel._store.facts[key]) == args.steps + 1, "history incomplete"
            for index, reader in enumerate(readers):
                expected = args.steps + 1 if index < args.subscribers else 0
                assert reader.field_notifications == expected, (
                    f"reader {index}: {reader.field_notifications} deliveries, "
                    f"expected {expected}"
                )
            journal = kernel.journal.bytes
            result = {
                "writer_steps": writer.steps,
                "sealed_ns": kernel.sealed_ns,
                "fact_versions": len(kernel._store.facts[key]),
                "records": len(kernel._store.records),
                "trace_entries": len(kernel._trace),
                "field_dirty_deliveries": sum(r.field_notifications for r in readers),
                "readers_notified": sum(r.field_notifications > 0 for r in readers),
                "journal_bytes": len(journal),
                "journal_sha256": hashlib.sha256(journal).hexdigest(),
                "initial_view_unchanged": True,
                "replay_checked": None,
            }
        if args.replay:
            with measured(phases, "replay_and_compare"):
                restored = replay(journal)
                try:
                    assert not restored.incomplete, "replay is incomplete"
                    assert restored.sealed_ns == kernel.sealed_ns, "replay seal differs"
                    assert restored.view().field(key, final_at).value == args.steps, (
                        "replay final value differs"
                    )
                    assert len(restored._store.records) == len(kernel._store.records), (
                        "replay record count differs"
                    )
                    for left, right in zip(
                        restored.iter_records(), kernel.iter_records(), strict=True
                    ):
                        assert left == right, f"replay record {left['index']} differs"
                finally:
                    restored.close()
                result["replay_checked"] = True
        return result
    finally:
        kernel.close()


def run_workload(name, workload, args):
    """Keep failures explicit and never report an unfinished workload as success."""
    gc.collect()
    result = {"name": name, "phases": [], "outcome": None, "failure": None}
    # This standalone CLI owns tracing, including when -X tracemalloc was used.
    tracemalloc.stop()
    tracemalloc.start()
    try:
        result["outcome"] = workload(args, result["phases"])
    except Exception as exc:
        result["failure"] = {"type": type(exc).__name__, "detail": str(exc)}
    finally:
        tracemalloc.stop()
    return result


def provenance(revision):
    """Hash the actually imported kernel sources, not a guessed checkout version."""
    package = Path(aerokernel.__file__).resolve().parent
    digest = hashlib.sha256()
    sources = sorted(package.rglob("*.py"))
    for path in sources:
        digest.update(path.relative_to(package).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_build": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine() or None,
        "processor": platform.processor() or None,
        "cpu_count": os.cpu_count(),
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
        "kernel_version": aerokernel.__version__,
        "kernel_package_path": str(package),
        "kernel_source_files": len(sources),
        "kernel_sources_sha256": digest.hexdigest(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "revision_label": revision,
        "root_seed": 0,
        "random_workload": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workload", choices=("all", "actions", "routing"), default="all"
    )
    parser.add_argument("--commands", type=int, default=4)
    parser.add_argument(
        "--history", type=int, default=64,
        help="existing feedback entries per command (excludes receipts)",
    )
    parser.add_argument(
        "--appends", type=int, default=16,
        help="new feedback rounds; one append per command per round",
    )
    parser.add_argument(
        "--partitions", type=int, default=16,
        help="total routing partitions, including one writer",
    )
    parser.add_argument("--subscribers", type=int, default=1)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--provenance", choices=("lean", "full"), default="lean")
    parser.add_argument(
        "--replay", action="store_true",
        help="also replay and compare the routing journal",
    )
    parser.add_argument("--revision", help="optional caller-supplied checkout label")
    args = parser.parse_args()
    if args.commands < 1 or args.history < 0 or args.appends < 1:
        parser.error("commands/appends must be positive; history must be nonnegative")
    if (
        args.partitions < 1
        or not 0 <= args.subscribers < args.partitions
        or args.steps < 1
    ):
        parser.error("need partitions/steps > 0 and 0 <= subscribers < partitions")
    if not __debug__:
        parser.error("run without -O: semantic assertions must remain enabled")

    results = []
    hardware = provenance(args.revision)
    for name, workload in (("actions", action_workload), ("routing", routing_workload)):
        if args.workload in ("all", name):
            results.append(run_workload(name, workload, args))
    print(
        json.dumps(
            {
                "format": "aerokernel.runtime-data-bench/v1",
                "parameters": vars(args),
                "hardware_provenance": hardware,
                "budget": None,
                "results": results,
            },
            sort_keys=True,
            allow_nan=False,
        ),
        flush=True,
    )
    return int(any(result["failure"] is not None for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
