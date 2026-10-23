"""Pack KPIs from the authoritative journal; no engines or sensor calls on replay."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from dataclasses import asdict
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from aerokernel import Kernel, replay
from aerokernel.codec import decode_record
from aerokernel.values import thaw

from aeroagentsim.runs import (
    NoCommittedPrefix, RecordedMessage, RunReplaced, iter_record_messages, open_run,
)

from .geometry import Polygon, area, clip, contains, union_area


def _ratio(numerator: float, denominator: float) -> float | None:
    """Undefined empty-population metrics stay null, with counts alongside them."""
    return numerator / denominator if denominator else None


def compute(kernel: Kernel) -> dict[str, Any]:
    """Consume committed in-memory records through the public message normalizer."""
    records = list(kernel.records)
    header = getattr(kernel, "header", {})
    manifest = header.get("manifest", {})
    partitions = frozenset(row["fields"]["id"] for row in header.get("partitions", []))
    messages = (
        message
        for record in records
        for message in iter_record_messages(
            record, partitions=partitions,
            ingress_source=manifest.get("ingress_source"),
            control_source=manifest.get("control_source"),
        )
    )
    return _compute(thaw(kernel.configuration), records, messages)


def _compute(
    configuration: Any,
    records: list[dict[str, Any]],
    messages: Iterable[RecordedMessage],
) -> dict[str, Any]:
    if not isinstance(configuration, dict) or "scenario" not in configuration:
        raise ValueError("metrics require a scenario pinned in the journal header")
    config = cast(dict[str, Any], configuration)
    engines = config["scenario"]["engines"]
    end = max((decode_record(record["instant"]).ns for record in records), default=0)
    # Identity, not payload equality or delivery count, selects the population.
    originals: dict[str, RecordedMessage] = {}
    for message in messages:
        originals.setdefault(message.message_id, message)
    events = [message for message in originals.values() if message.kind == "event"]
    result: dict[str, Any] = {
        "run_id": config["scenario"]["id"],
        "duration_s": end / 1e9,
    }
    for engine_id, engine in engines.items():
        c = engine["config"]
        if engine["plugin"] == "logistics":
            rows = [
                e
                for e in events
                if e.source == engine_id and e.schema_id == c["event_schema"]
            ]
            released, delivered, accepted, rejected = set(), {}, set(), set()
            active: dict[str, int] = {}
            busy_ns = 0
            for event in rows:
                p, at = event.payload, event.at_ns
                if at is None:
                    raise ValueError("logistics metrics require a recorded event time")
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
                e.payload
                for e in events
                if e.source == engine_id and e.schema_id == c["event_schema"]
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
    if any(engine["plugin"] == "lowalt_tasks" and "rescue" in engine["config"] for engine in engines.values()):
        from aeroagentsim.metrics_api import (
            MetricAccumulator,
            MetricPlugin,
            discover_metric_plugins,
        )
        from .lowalt.metrics import RESCUE_METRICS, RescueReducer

        # This historical report exposes flat rescue scalars, but uses the same
        # public reducer contract/discovery as the service instead of importing a
        # private service accumulator. Keep whole reducer ownership intact.
        plugins = tuple(
            MetricPlugin(plugin.name, tuple(
                RescueReducer(configured_report=True) if type(reducer) is RescueReducer else reducer
                for reducer in plugin.reducers
                if any(item.id in RESCUE_METRICS for item in reducer.descriptors)
            ), api_version=plugin.api_version)
            for plugin in discover_metric_plugins()
        )
        accumulator = MetricAccumulator(plugins)
        accumulator.feed(originals.values())
        values = accumulator.snapshot()
        for name in RESCUE_METRICS:
            if name in values:  # A whole-provider replacement may omit legacy IDs.
                result[name] = values[name]
    return result


def _verified(data: bytes) -> Kernel:
    # Reader v1 has no semantic replay-verification capability. Retain this
    # engine-free check; an admitted ACK alone does not imply complete replay.
    offline = replay(data)
    if offline.incomplete:
        raise ValueError(
            "metrics require a complete journal; replay reports incomplete data"
        )
    return offline


def metrics(run: Path | str) -> dict[str, Any]:
    """Verify an ACK-bounded run or a historical offline journal before KPIs."""
    path = Path(run)
    if not path.is_dir():
        # Reader v1 has no standalone-file admission. Preserve the historical
        # JSONL entry point without presenting visible bytes as an ACK.
        data = path.read_bytes()
        offline = _verified(data)
        return {
            **compute(offline),
            "source_evidence": {
                "reader_api_version": None,
                "cut_index": offline.records[-1]["index"],
                "cut_bytes": len(data),
                "provenance": offline.provenance,
                "acknowledged": False,
                "verification": "kernel-semantic-replay",
                "redaction": "none",
            },
        }
    try:
        source = open_run(path)
    except NoCommittedPrefix:
        ack = path / "committed-prefix.json"
        if ack.exists() or ack.is_symlink():
            # An invalid published ACK is not permission to consume visible tails.
            raise
        # Bare journals predate the service ACK contract. Keep main's semantic
        # admission and exact report shape, including its incomplete-data error.
        # compute() normalizes messages through the public iter_record_messages.
        return compute(_verified((path / "journal.jsonl").read_bytes()))
    with source:
        snapshot = source.snapshot()
        # Reader v1 has no raw-byte stream for the retained semantic verifier.
        # Bound that legacy access to the reader's exact ACK and source identity.
        with (path / "journal.jsonl").open("rb") as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != snapshot.header.journal_identity:
                raise RunReplaced("Metrics source journal was replaced")
            data = stream.read(snapshot.cut_bytes)
        if len(data) != snapshot.cut_bytes:
            raise RunReplaced("Metrics source journal was truncated")
        _verified(data)
        records = list(snapshot.iter_records())
        result = _compute(
            records[0]["configuration"], records, snapshot.iter_messages()
        )
        result["source_evidence"] = {
            "reader_api_version": snapshot.api_version,
            "cut_index": snapshot.cut_index,
            "cut_bytes": snapshot.cut_bytes,
            "header": asdict(snapshot.header),
            "verification": "kernel-semantic-replay",
            "redaction": "none",
        }
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(metrics(args.run), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
