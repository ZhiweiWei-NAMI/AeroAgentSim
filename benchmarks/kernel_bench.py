"""File-WAL, complete-history DES/fixed-step benchmark; stdlib runtime only.

Run with the editable package installed: .venv/bin/python benchmarks/kernel_bench.py
--entities 1000 --steps 600 --journal benchmarks/run.jsonl --check-targets
The journal path must not exist. JSON reports measured completion, never an
extrapolation from a failed or partially sealed workload. Linux ru_maxrss is KiB.
"""

import argparse
import hashlib
import json
import resource
import signal
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from aerokernel import (
    BindingManifest,
    BindingRule,
    Create,
    Dependency,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Interval,
    Journal,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    Partition,
    Stamp,
    Timing,
    TypeDescriptor,
)
from aerokernel.sdk import SimpleEngine

STEP = 100_000_000


def dense_binding(n=40):
    """Compile a dense DAG with n(n-1)/2 declared field dependency edges."""
    fields = tuple(f"f{i}" for i in range(n))
    registry = MemoryRegistry(
        (TypeDescriptor("T"),),
        tuple(FieldDescriptor(f, "T", {"type": "integer"}) for f in fields),
    )
    engines = tuple(
        SimpleEngine(
            Partition(
                f"p{i}",
                f"e{i}",
                produces=(fields[i],),
                consumes=tuple(Dependency(f) for f in fields[:i]),
            )
        )
        for i in range(n)
    )
    manifest = BindingManifest(
        "r", "e", rules=tuple(BindingRule(f"p{i}", "T", (fields[i],)) for i in range(n))
    )
    start = time.perf_counter()
    Kernel().bind(registry, manifest, engines)
    return {
        "partitions": n,
        "dag_edges": n * (n - 1) // 2,
        "bind_wall_s": time.perf_counter() - start,
    }


def workload(entities, steps, path):
    """Write every entity's three integer fields every native 100-ms step."""
    refs = tuple(
        EntityRef("bench", "epoch", f"id{i:04d}", 0, "Entity") for i in range(entities)
    )
    fields = ("a", "b", "c")

    class Fixed(SimpleEngine):
        steps = 0

        def writes(self, view):
            acquired = Stamp("canonical", view.instant.ns, 1, "canonical")
            valid = Interval(view.instant, None)
            return tuple(
                FactWrite((r, f), self.steps, acquired, valid)
                for r in refs
                for f in fields
            )

        def initialize(self, view):
            return tuple(Create(r) for r in refs) + self.writes(view)

        def integrate(self, view):
            self.steps += 1
            return self.writes(view)

    fixed = Fixed(
        Partition(
            "fixed",
            "fixed",
            produces=fields,
            timing=Timing("fixed_step", STEP),
            lifecycle=True,
        )
    )
    des = SimpleEngine(Partition("des", "des"))
    registry = MemoryRegistry(
        (TypeDescriptor("Entity"),),
        tuple(FieldDescriptor(f, "Entity", {"type": "integer"}) for f in fields),
    )
    manifest = BindingManifest(
        "bench",
        "epoch",
        refs,
        rules=(BindingRule("fixed", "Entity", fields),),
        lifecycle=(LifecycleRule("fixed", "Entity"),),
    )
    journal = Journal(path)
    k = Kernel(journal=journal)
    start = time.perf_counter()
    bootstrap_s = None
    failure = None
    try:
        k.bind(registry, manifest, (fixed, des))
        k.start()
        bootstrap_s = time.perf_counter() - start
        k.run_until(steps * STEP)
        assert fixed.steps == steps and k._store.sealed_ns == steps * STEP
        assert len(k._store.facts) == entities * 3
        assert all(len(versions) == steps + 1 for versions in k._store.facts.values())
        for r in refs:
            for f in fields:
                assert k.view().field((r, f), k.view().cut.instant).value == steps
    except Exception as exc:
        failure = {
            "type": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "detail": str(exc),
        }
    wall_s = time.perf_counter() - start
    versions = sum(map(len, k._store.facts.values())) if hasattr(k, "_store") else 0
    journal.close()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    result = {
        "entities": entities,
        "fields": 3,
        "requested_steps": steps,
        "target_simulated_s": steps * 0.1,
        "wall_s": wall_s,
        "bootstrap_s": bootstrap_s,
        "rss_peak_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "journal_bytes": path.stat().st_size,
        "journal_sha256": digest.hexdigest(),
        "bytes_per_fact_version": path.stat().st_size / versions if versions else None,
        "journal_lines": len(k._store.records) + 1 if hasattr(k, "_store") else 0,
        "sealed_ns": k._store.sealed_ns if hasattr(k, "_store") else None,
        "fact_versions": versions,
        "failure": failure,
    }
    return result


def main():
    """Measure actual work and optionally enforce this host's acceptance targets."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--entities", type=int, choices=(100, 1000), default=1000)
    parser.add_argument("--steps", type=int, default=600)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--bind-only", action="store_true")
    parser.add_argument("--check-targets", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.steps < 1 or args.timeout < 1:
        parser.error("steps and timeout must be positive")

    def timeout(*_):
        raise TimeoutError("benchmark wall-time budget exhausted")

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(args.timeout)
    binding = dense_binding()
    if args.bind_only:
        print(json.dumps(binding), flush=True)
        if args.check_targets and binding["bind_wall_s"] >= 0.5:
            raise SystemExit(1)
        return
    # Default scratch lives beside this script, never outside the workspace.
    scratch_context = (
        tempfile.TemporaryDirectory(prefix=".kernel-bench-", dir=Path(__file__).parent)
        if args.journal is None
        else nullcontext()
    )
    with scratch_context as scratch:
        path = args.journal if args.journal is not None else Path(scratch) / "run.jsonl"
        result = workload(args.entities, args.steps, path)
    signal.alarm(0)
    result["dense_binding"] = binding
    print(json.dumps(result), flush=True)
    if result["failure"] is not None:
        raise SystemExit(1)
    if args.check_targets:
        if args.steps != 600:
            parser.error("full acceptance targets require --steps 600")
        wall_limit, rss_limit = (10, 300) if args.entities == 100 else (90, 1536)
        if (
            result["wall_s"] > wall_limit
            or result["rss_peak_mib"] > rss_limit
            or binding["bind_wall_s"] >= 0.5
        ):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
