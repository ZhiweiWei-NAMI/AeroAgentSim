"""Real move-like command bursts, complete receipts and file-WAL byte rates.

Each scalar mover accepts a target at bootstrap, starts on the next native step,
and succeeds only after measured position reaches it. A DES producer sends new
targets every five seconds. No domain assumptions are added to the kernel.
"""

import argparse
import hashlib
import json
import resource
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from aerokernel import (
    BindingManifest,
    BindingRule,
    CommandRequest,
    Create,
    Emit,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    Journal,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Receipt,
    Stamp,
    Timing,
    TypeDescriptor,
)
from aerokernel.sdk import SimpleEngine

SECOND = 1_000_000_000


def workload(entities, seconds, path, *, bootstrap_only=False):
    """Measure actual bootstrap and periodic execution, failing on any lost work."""
    refs = tuple(
        EntityRef("burst", "epoch", str(i), 0, "Mover") for i in range(entities)
    )
    schema = {
        "type": "record",
        "members": {"entity": {"type": "integer"}, "target": {"type": "integer"}},
        "required": ["entity", "target"],
        "extra": False,
    }

    class Movers(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "movers",
                    "movers",
                    produces=("position",),
                    commands=("move",),
                    timing=Timing("fixed_step", SECOND),
                    lifecycle=True,
                )
            )
            self.positions = [0] * entities
            self.active = {}
            self.accepted = self.executing = self.succeeded = 0

        def write(self, view, i, causes=()):
            return FactWrite(
                (refs[i], "position"),
                self.positions[i],
                Stamp("canonical", view.instant.ns, 1, "canonical"),
                Interval(view.instant, None),
                causes,
            )

        def initialize(self, view):
            return tuple(Create(r) for r in refs) + tuple(
                self.write(view, i) for i in range(entities)
            )

        def on_react(self, view, inbox, dirty):
            ops = []
            for delivery in inbox:
                assert delivery.message.kind == "command"
                i = delivery.message.payload["entity"]
                assert i not in self.active
                self.active[i] = (delivery, delivery.message.payload["target"], False)
                head = view.action(delivery.message.id).head
                ops.append(
                    Receipt(
                        delivery.message.id,
                        "accepted",
                        causes=(
                            delivery.dispatch_ref,
                            head,
                        ),
                    )
                )
                self.accepted += 1
            return tuple(ops)

        def integrate(self, view):
            ops = []
            for i, (delivery, target, started) in tuple(self.active.items()):
                command_id = delivery.message.id
                head = view.action(command_id).head
                causes = (delivery.dispatch_ref, head)
                if not started:
                    ops.append(Receipt(command_id, "executing", causes=causes))
                    self.active[i] = (delivery, target, True)
                    self.executing += 1
                old = view.field((refs[i], "position"), view.instant)
                self.positions[i] += min(1, target - self.positions[i])
                assert self.positions[i] <= target
                write_index = len(ops)
                ops.append(self.write(view, i, (delivery.dispatch_ref, old.version)))
                if self.positions[i] == target:
                    # Completion is backed by this invocation's actual state write.
                    from aerokernel import LocalCause

                    ops.append(
                        Receipt(
                            command_id,
                            "succeeded",
                            causes=(
                                *causes,
                                LocalCause(write_index),
                            ),
                        )
                    )
                    del self.active[i]
                    self.succeeded += 1
            return tuple(ops)

    class Producer(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "producer",
                    "producer",
                    commands=(),
                    emits=("move",),
                    message_targets=("movers",),
                )
            )
            self.wakeup_ns = 5 * SECOND if seconds > 5 else None

        def integrate(self, view):
            cohort = view.instant.ns // (5 * SECOND)
            following = view.instant.ns + 5 * SECOND
            self.wakeup_ns = following if following < seconds * SECOND else None
            return tuple(
                Emit(
                    "command",
                    "move",
                    "movers",
                    view.instant,
                    {"entity": i, "target": 2 * (cohort + 1)},
                )
                for i in range(entities)
            )

    movers, producer = Movers(), Producer()
    registry = MemoryRegistry(
        (TypeDescriptor("Mover"),),
        (FieldDescriptor("position", "Mover", {"type": "integer"}),),
        messages=(MessageDescriptor("move", "command", schema),),
    )
    manifest = BindingManifest(
        "burst",
        "epoch",
        refs,
        rules=(BindingRule("movers", "Mover", ("position",)),),
        lifecycle=(LifecycleRule("movers", "Mover"),),
        bootstrap_commands=tuple(
            CommandRequest(
                "move",
                "movers",
                Instant(0),
                {"entity": i, "target": 2},
            )
            for i in range(entities)
        ),
    )
    journal = Journal(path)
    k = Kernel(journal=journal)
    start = time.perf_counter()
    try:
        k.bind(registry, manifest, (movers, producer))
        k.start()
        bootstrap_s = time.perf_counter() - start
        bootstrap_bytes = journal.acknowledged_bytes
        assert movers.accepted == entities and len(movers.active) == entities
        run_start = time.perf_counter()
        if not bootstrap_only:
            k.run_until(seconds * SECOND)
            cohorts = 1 + (seconds - 1) // 5
            assert (
                movers.accepted
                == movers.executing
                == movers.succeeded
                == cohorts * entities
            )
            assert not movers.active and k._store.sealed_ns == seconds * SECOND
            for ref in refs:
                assert (
                    k.view().field((ref, "position"), Instant(seconds * SECOND)).value
                    == 2 * cohorts
                )
            assert all(
                state.status == "succeeded"
                for state in k._store.actions.states.values()
            )
        run_s = time.perf_counter() - run_start
        wall_s = time.perf_counter() - start
        records = len(k._store.records)
    finally:
        k.close()
        journal.close()
    digest = hashlib.sha256()
    largest = 0
    with path.open("rb") as source:
        for line in source:
            digest.update(line)
            largest = max(largest, len(line))
    size = path.stat().st_size
    return {
        "entities": entities,
        "simulated_s": 0 if bootstrap_only else seconds,
        "bootstrap_s": bootstrap_s,
        "run_s": run_s,
        "wall_s": wall_s,
        "accepted": movers.accepted,
        "executing": movers.executing,
        "succeeded": movers.succeeded,
        "journal_bytes": size,
        "bootstrap_bytes": bootstrap_bytes,
        "journal_bytes_per_simulated_s": None if bootstrap_only else size / seconds,
        "periodic_bytes_per_simulated_s": None
        if bootstrap_only
        else (size - bootstrap_bytes) / seconds,
        "largest_line_bytes": largest,
        "journal_sha256": digest.hexdigest(),
        "records": records,
        "rss_peak_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entities", type=int, default=1000)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--bootstrap-only", action="store_true")
    parser.add_argument("--check-targets", action="store_true")
    parser.add_argument("--check-scaling", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.entities < 1 or args.seconds < 2 or args.seconds % 5 or args.timeout < 1:
        parser.error("positive entities/timeout and seconds divisible by five required")
    if args.check_scaling:
        if args.bootstrap_only or args.seconds != 60:
            parser.error("scaling requires complete 60-second runs")
        results = []
        for count in (100, 1000):
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--entities",
                str(count),
                "--timeout",
                str(args.timeout),
                "--check-targets",
            ]
            if args.journal is not None:
                path = args.journal.with_name(
                    f"{args.journal.stem}-{count}{args.journal.suffix}"
                )
                command.extend(("--journal", str(path)))
            completed = subprocess.run(
                command, capture_output=True, text=True, check=True
            )
            results.append(json.loads(completed.stdout))
        ratio = results[1]["run_s"] / results[0]["run_s"]
        byte_ratio = results[1]["journal_bytes"] / results[0]["journal_bytes"]
        print(
            json.dumps(
                {
                    "runs": results,
                    "run_time_ratio_1000_over_100": ratio,
                    "journal_byte_ratio_1000_over_100": byte_ratio,
                    "scaling_limit": 15,
                }
            ),
            flush=True,
        )
        # 10x entities may use up to 15x wall time on this host. Byte growth
        # should track the actual 10x command/fact count without superlinear WAL.
        if ratio > 15 or not 8 <= byte_ratio <= 12:
            raise SystemExit("command burst scaling target exceeded")
        return

    def timeout(*_):
        raise TimeoutError("command burst did not finish within wall-time budget")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(args.timeout)
    context = (
        tempfile.TemporaryDirectory(prefix=".command-burst-", dir=Path(__file__).parent)
        if args.journal is None
        else nullcontext()
    )
    with context as scratch:
        path = args.journal if args.journal is not None else Path(scratch) / "run.jsonl"
        result = workload(
            args.entities, args.seconds, path, bootstrap_only=args.bootstrap_only
        )
    signal.alarm(0)
    print(json.dumps(result), flush=True)
    if args.check_targets:
        if args.entities == 1000 and result["bootstrap_s"] >= 5:
            raise SystemExit("bootstrap acceptance target exceeded")
        if not args.bootstrap_only and args.seconds != 60:
            parser.error("complete targets require 60 simulated seconds")


if __name__ == "__main__":
    main()
