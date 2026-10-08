"""Pack KPIs from the authoritative journal; no engines or sensor calls on replay."""

from __future__ import annotations

import argparse
import json
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from aerokernel import Kernel, replay
from aerokernel.values import thaw

from aeroagentsim.services.projector import project

from .geometry import Polygon, area, clip, contains, union_area


def _ratio(numerator: float, denominator: float) -> float | None:
    """Undefined empty-population metrics stay null, with counts alongside them."""
    return numerator / denominator if denominator else None


def compute(kernel: Kernel) -> dict[str, Any]:
    """Consume committed records using the journal's own scenario/configuration."""
    configuration = thaw(kernel.configuration)
    if not isinstance(configuration, dict) or "scenario" not in configuration:
        raise ValueError("metrics require a scenario pinned in the journal header")
    config = cast(dict[str, Any], configuration)
    engines = config["scenario"]["engines"]
    commits = [project(r) for r in kernel.records]
    end = max((int(c["at"]["ns"]) for c in commits), default=0)
    # Each committed message is counted once, independent of topic fan-out.
    events = {
        m["id"]: m for c in commits for m in c["messages"] if m["kind"] == "event"
    }
    result: dict[str, Any] = {
        "run_id": config["scenario"]["id"],
        "duration_s": end / 1e9,
    }
    for engine_id, engine in engines.items():
        c = engine["config"]
        if engine["plugin"] == "logistics":
            rows = [
                e
                for e in events.values()
                if e["source"] == engine_id and e["schemaId"] == c["event_schema"]
            ]
            released, delivered, accepted, rejected = set(), {}, set(), set()
            active: dict[str, int] = {}
            busy_ns = 0
            for event in rows:
                p, at = event["payload"], int(event["at"]["ns"])
                order, state = p["order"], p["state"]
                if state == "queued":
                    released.add(order)
                elif state == "pickup":
                    active[order] = at
                elif state in {"delivered", "failed", "canceled"}:
                    if order in active:
                        busy_ns += at - active.pop(order)
                    if state == "delivered":
                        delivered[order] = (at, p)
                elif state == "accepted":
                    accepted.add(order)
                elif state == "rejected":
                    rejected.add(order)
            busy_ns += sum(end - at for at in active.values())
            on_time = sum(at <= p["deadline_ns"] for at, p in delivered.values())
            total_time = sum(
                (at - p["release_ns"]) / 1e9 for at, p in delivered.values()
            )
            energy = sum(p["energy_used_j"] for _, p in delivered.values())
            result[engine_id] = {
                "pack": "logistics",
                "released": len(released),
                "delivered": len(delivered),
                "accepted": len(accepted),
                "rejected": len(rejected),
                "on_time_rate": _ratio(on_time, len(released)),
                "mean_delivery_time_s": _ratio(total_time, len(delivered)),
                "energy_per_parcel_j": _ratio(energy, len(delivered)),
                "utilization": _ratio(busy_ns, end * len(c["fleet"])),
            }
        elif engine["plugin"] == "inspection":
            rows = [
                e["payload"]
                for e in events.values()
                if e["source"] == engine_id and e["schemaId"] == c["event_schema"]
            ]
            target_metrics: dict[str, Any] = {}
            for target in c["targets"]:
                covered: list[Polygon] = []
                visits: set[int] = set()
                waypoint_seen = False
                for row in rows:
                    sample = row["sample"]
                    if sample["valid"] is not True:
                        continue
                    polygon = tuple(
                        (float(p[0]), float(p[1])) for p in sample["footprint"]
                    )
                    if target["kind"] == "waypoint":
                        if contains(polygon, tuple(target["point"])):
                            visits.add(row["acquired_ns"])
                            waypoint_seen = True
                    else:
                        x0, y0, x1, y1 = target["bounds"]
                        intersection = clip(
                            polygon, ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
                        )
                        if area(intersection) > 1e-12:
                            covered.append(intersection)
                            visits.add(row["acquired_ns"])
                if target["kind"] == "waypoint":
                    coverage = 100.0 if waypoint_seen else 0.0
                else:
                    x0, y0, x1, y1 = target["bounds"]
                    coverage = min(
                        100.0,
                        100 * union_area(tuple(covered)) / ((x1 - x0) * (y1 - y0)),
                    )
                times = sorted(visits)
                revisit = [(b - a) / 1e9 for a, b in pairwise(times)]
                target_metrics[target["id"]] = {
                    "coverage_pct": coverage,
                    "acquisitions": len(times),
                    "revisit_times_s": revisit,
                    "mean_revisit_s": _ratio(sum(revisit), len(revisit)),
                    "max_revisit_s": max(revisit) if revisit else None,
                }
            result[engine_id] = {
                "pack": "inspection",
                "observations": len(rows),
                "invalid_observations": sum(
                    row["sample"]["valid"] is False for row in rows
                ),
                "coverage_pct": sum(m["coverage_pct"] for m in target_metrics.values())
                / len(target_metrics),
                "targets": target_metrics,
            }
    return result


def metrics(run: Path | str) -> dict[str, Any]:
    """Validate replay integrity, then derive KPIs for a directory or JSONL file."""
    path = Path(run)
    path = path / "journal.jsonl" if path.is_dir() else path
    offline = replay(path)
    if offline.incomplete:
        raise ValueError(
            "metrics require a complete journal; replay reports incomplete data"
        )
    return compute(offline)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(metrics(args.run), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
