"""Profile the full actor set without changing engine outputs or journal encoding."""

from __future__ import annotations

import argparse
import cProfile
import json
import pstats
import resource
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aerokernel import Journal
from aerokernel.journal import replay

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario

SCENARIO = Path(__file__).resolve().parent


def histogram(path: Path) -> dict[str, Any]:
    """Stream physical wire bytes; never expand or retain the whole journal."""
    sizes: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    keys: Counter[str] = Counter()
    largest = (0, 0)
    with path.open("rb") as stream:
        for line in stream:
            record = json.loads(line)
            kind = record["type"] + "/" + record.get("phase", "")
            sizes[kind] += len(line)
            counts[kind] += 1
            if len(line) > largest[0]:
                largest = (len(line), record["index"])
            for key, value in record.items():
                keys[kind + "." + key] += len(
                    json.dumps(
                        value, separators=(",", ":"), ensure_ascii=False
                    ).encode("utf-8")
                )
    return {
        "bytes": path.stat().st_size,
        "records": sum(counts.values()),
        "types": [
            {"type": kind, "records": counts[kind], "bytes": size}
            for kind, size in sizes.most_common()
        ],
        "keys": keys.most_common(20),
        "largest_record": {"bytes": largest[0], "index": largest[1]},
    }


def timed(
    method: Callable[..., Any], name: str, times: dict[str, list[float]]
) -> Callable[..., Any]:
    def invoke(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return method(*args, **kwargs)
        finally:
            row = times.setdefault(name, [0, 0])
            row[0] += 1
            row[1] += time.perf_counter() - started

    return invoke


def run(hz: int, seconds: int, directory: Path) -> dict[str, Any]:
    """Time a single complete grant, including loading/binding/bootstrap/close."""
    started = time.perf_counter()
    scenario = load_scenario(SCENARIO / "scenario.yaml")
    for item in scenario.document["engines"].values():
        if item["plugin"] in {"kinematic", "traffic_road_motion"}:
            item["config"]["step_ns"] = round(1_000_000_000 / hz)
    scenario = load_scenario(scenario.document, base=SCENARIO)
    simulation = Simulation(
        scenario,
        run_directory=directory,
        journal=Journal(directory / "journal.jsonl"),
    )
    times: dict[str, list[float]] = {}
    for name, engine in simulation.kernel._engines.items():
        for phase in ("reset", "advance", "react"):
            setattr(
                engine,
                phase,
                timed(getattr(engine, phase), name + "." + phase, times),
            )
    try:
        simulation.start()
        bootstrap_wall = time.perf_counter() - started
        simulation.run_until(seconds * 1_000_000_000)
    finally:
        simulation.close()
    elapsed = time.perf_counter() - started
    return {
        "physics_hz": hz,
        "simulated_s": seconds,
        "elapsed_wall_s": elapsed,
        "bootstrap_wall_s": bootstrap_wall,
        "rtf": seconds / elapsed,
        "cpu_s": time.process_time(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "journal_bytes": (directory / "journal.jsonl").stat().st_size,
        "engine_times": dict(sorted(times.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--out", type=Path, help="New benchmark output directory")
    modes.add_argument("--replay", type=Path, help="Existing journal.jsonl")
    modes.add_argument("--histogram", type=Path, help="Existing journal.jsonl")
    parser.add_argument("--hz", type=int, choices=(1, 5, 15), default=15)
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.histogram is not None:
        result = histogram(args.histogram)
    elif args.replay is not None:
        started = time.perf_counter()
        kernel = replay(args.replay)
        result = {
            "elapsed_wall_s": time.perf_counter() - started,
            "cpu_s": time.process_time(),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "cut": kernel.view().cut.index,
            "incomplete": kernel.incomplete,
        }
    else:
        args.out.mkdir(parents=True, exist_ok=False)
        profile = cProfile.Profile()
        if args.profile:
            profile.enable()
        try:
            result = run(args.hz, args.seconds, args.out)
        finally:
            if args.profile:
                profile.disable()
                profile.dump_stats(str(args.out / "cpu.prof"))
                with (args.out / "cpu.txt").open("w") as stream:
                    pstats.Stats(profile, stream=stream).sort_stats(
                        "cumulative"
                    ).print_stats(60)
        (args.out / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
