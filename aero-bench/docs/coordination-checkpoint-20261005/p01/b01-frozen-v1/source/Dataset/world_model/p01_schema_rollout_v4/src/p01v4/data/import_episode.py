"""S01.02 read-only episode import into canonical records.

Imports one complete source episode into p01v4 canonical records with full
per-class coverage counts and source provenance (path + line) preserved on
every record.  Sources are opened read-only; every write goes to a new
versioned derivative under the results root.

Imported streams (episode L4-1_v1__seed00 engineering pilot):
  truth_frames.jsonl              -> frame records (pose/motion/annotations)
  global_entity_roster.json       -> entity records (roster)
  world_truth_graph_deltas.jsonl  -> edge records (relation; per-operation)
  event_occurrences.jsonl         -> event records (event)
  rgb/lidar capture json/npz      -> observation records (observation)
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from p01v4.contracts.schema import (
    SchemaError,
    validate_record,
)

REPO = Path(__file__).resolve().parents[6]

# Non-present markers: only emitted where the source itself distinguishes them.
V = {"missing": {"kind": "missing"}}


def _present(value: Any) -> Any:
    return value


def _frame_vec3(values: Any) -> Any:
    if values is None:
        return V["missing"]
    if len(values) != 3:
        return V["missing"]
    return {"frame": "world_enu_m", "value": [float(c) for c in values]}


@dataclass
class ImportReport:
    """Per-class coverage of one episode import."""

    episode_id: str
    sources: dict[str, str] = dc_field(default_factory=dict)
    source_records: Counter = dc_field(default_factory=Counter)
    canonical_records: Counter = dc_field(default_factory=Counter)
    ticks_covered: list[int] = dc_field(default_factory=list)
    modalities: Counter = dc_field(default_factory=Counter)
    missing_modalities: list[str] = dc_field(default_factory=list)
    event_traceability: dict[str, Any] = dc_field(default_factory=dict)
    errors: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "sources": self.sources,
            "source_records": dict(self.source_records),
            "canonical_records": dict(self.canonical_records),
            "ticks_covered": [self.ticks_covered[0], self.ticks_covered[-1],
                              len(self.ticks_covered)],
            "modalities": dict(self.modalities),
            "missing_modalities": self.missing_modalities,
            "event_traceability": self.event_traceability,
            "errors": self.errors,
        }


class CanonicalEpisode:
    """In-memory canonical record set for one episode."""

    def __init__(self, episode_id: str) -> None:
        self.episode_id = episode_id
        self.records: list[dict[str, Any]] = []
        self._entity_ids: set[str] = set()

    def add(self, record: dict[str, Any], *, check_id: bool = False) -> None:
        rec_episode = record.get("episode_id")
        if rec_episode is None:
            record["episode_id"] = self.episode_id
        elif rec_episode != self.episode_id:
            raise SchemaError(
                f"cross-episode record rejected: record episode_id '{rec_episode}' "
                f"!= canonical episode '{self.episode_id}'")
        if check_id:
            eid = record["fields"].get("roster.entity_id")
            if eid is not None:
                if eid in self._entity_ids:
                    raise SchemaError(f"duplicate entity_id '{eid}' in episode index")
                self._entity_ids.add(eid)
        paths = validate_record(record)
        record["_validated_paths"] = paths
        self.records.append(record)

    def by_kind(self, kind: str) -> Iterator[dict[str, Any]]:
        for r in self.records:
            if r["record_kind"] == kind:
                yield r


def _open_lines(path: Path) -> tuple[list[str], int]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    return lines, len(lines)


def import_truth_frames(episode: CanonicalEpisode, path: Path,
                        report: ImportReport) -> None:
    lines, n = _open_lines(path)
    report.source_records["truth_frames.jsonl"] = n
    for lineno, line in enumerate(lines, start=1):
        src = json.loads(line)
        tick = src["tick"]
        for ent in src["entities"]:
            tp = ent.get("truth_pose", {})
            ann = ent.get("annotations", {})
            rp = ent.get("render_presence", {})
            fields: dict[str, Any] = {
                "pose.position_enu_m": _frame_vec3(tp.get("position_enu_m")),
                "pose.velocity_enu_mps": _frame_vec3(tp.get("velocity_enu_mps")),
                "motion.speed_mps": (
                    float(ann["speed_mps"]) if isinstance(ann.get("speed_mps"), (int, float))
                    else V["missing"]),
                "annotations.activity_type": (
                    str(ann["activity_type"]) if ann.get("activity_type") is not None
                    else V["missing"]),
                "annotations.visibility_state": (
                    str(rp.get("visibility_state"))
                    if rp.get("visibility_state") is not None else V["missing"]),
            }
            yaw = tp.get("rotation_deg", {}).get("yaw_deg")
            if yaw is not None:
                fields["pose.yaw_deg"] = float(yaw)
            episode.add({
                "record_kind": "frame",
                "id": f"{src['frame_id']}|{ent['entity_id']}",
                "tick": tick,
                "source": {"path": str(path), "line": lineno},
                "entity_id": ent["entity_id"],
                "fields": fields,
            })
    report.canonical_records["frame"] = sum(1 for _ in episode.by_kind("frame"))
    report.ticks_covered = sorted({r["tick"] for r in episode.by_kind("frame")})


def import_roster(episode: CanonicalEpisode, path: Path,
                  report: ImportReport) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    entities = doc["entities"]
    report.source_records["global_entity_roster.json"] = len(entities)
    observer_entity_id = report.event_traceability.get("_observer_entity_id")
    for ent in entities:
        role = "background"
        if observer_entity_id is not None and ent["entity_id"] == observer_entity_id:
            role = "observer"
        episode.add({
            "record_kind": "entity",
            "id": ent["entity_id"],
            "source": {"path": str(path), "line": None},
            "fields": {
                "roster.entity_id": ent["entity_id"],
                "roster.entity_category": ent.get("entity_category", ""),
                "roster.entity_kind": ent.get("entity_kind", ""),
                "roster.observer_role": role,
            },
        }, check_id=True)
    report.canonical_records["entity"] = sum(1 for _ in episode.by_kind("entity"))


def import_truth_deltas(episode: CanonicalEpisode, path: Path,
                        report: ImportReport) -> None:
    lines, n = _open_lines(path)
    report.source_records["world_truth_graph_deltas.jsonl"] = n
    count = 0
    for lineno, line in enumerate(lines, start=1):
        delta = json.loads(line)
        tick = delta["tick"]
        for op_idx, op in enumerate(delta["operations"]):
            op_kind = op.get("operation")
            op = op.get("assertion", op)  # add_predicate_assertion nests the body
            to_value = op.get("to_value")
            if to_value is None:
                to_value = op.get("truth_value")
            if to_value is None:
                to_value = op.get("value")
            if to_value is None:
                to_value = op.get("from_value", "unknown")
            fields: dict[str, Any] = {
                "relation.predicate_id": op["predicate_id"],
                "relation.tuple_id": op["tuple_id"],
                "relation.value": str(to_value),
                "relation.bindings": dict(op.get("bindings", {})),
            }
            episode.add({
                "record_kind": "edge",
                "id": f"{delta['delta_id']}|op{op_idx}",
                "tick": tick,
                "source": {"path": str(path), "line": lineno,
                           "operation": op_kind,
                           "assertion_id": op.get("assertion_id"),
                           "delta_id": delta["delta_id"]},
                "fields": fields,
            })
            count += 1
    report.canonical_records["edge"] = count


def import_events(episode: CanonicalEpisode, path: Path,
                  report: ImportReport) -> None:
    lines, n = _open_lines(path)
    report.source_records["event_occurrences.jsonl"] = n
    first_mid: dict[str, Any] | None = None
    for lineno, line in enumerate(lines, start=1):
        ev = json.loads(line)
        end_tick = ev.get("end_tick")
        fields: dict[str, Any] = {
            "event.event_id": ev["event_id"],
            "event.event_family_id": ev["event_family_id"],
            "event.detection_tick": int(ev["detection_tick"]),
            "event.end_tick": (int(end_tick) if end_tick is not None else V["missing"]),
            "event.bindings": dict(ev.get("bindings", {})),
        }
        episode.add({
            "record_kind": "event",
            "id": ev["event_id"],
            "tick": ev["detection_tick"],
            "source": {"path": str(path), "line": lineno},
            "fields": fields,
        })
        if first_mid is None and 0 < lineno <= n:
            first_mid = {
                "event_id": ev["event_id"],
                "source_path": str(path),
                "source_line": lineno,
                "detection_tick": ev["detection_tick"],
                "end_tick": end_tick,
                "canonical_id": ev["event_id"],
            }
    report.canonical_records["event"] = sum(1 for _ in episode.by_kind("event"))
    report.event_traceability["mid_event_sample"] = first_mid




def _npz_point_count(npz_path: Path) -> int | None:
    import numpy as np
    try:
        with np.load(npz_path, allow_pickle=False) as z:
            if "points_sensor_ned_m" in z:
                return int(z["points_sensor_ned_m"].shape[0])
    except FileNotFoundError:
        return None
    return None


def import_capture_path(episode: CanonicalEpisode, capture_root: Path,
                        view_dir_name: str, report: ImportReport) -> None:
    view_dir = capture_root / view_dir_name
    if not view_dir.is_dir():
        report.missing_modalities.extend(["rgb", "lidar"])
        return
    seen_modalities: set[str] = set()
    for modality, ext, array_tag in (("rgb", ".json", "image"),
                                     ("lidar", ".json", "points")):
        mod_dir = view_dir / modality
        if not mod_dir.is_dir():
            report.missing_modalities.append(modality)
            continue
        metas = sorted(p for p in mod_dir.glob(f"*{ext}") if p.name.endswith(ext))
        for meta_path in metas:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            tick = int(meta["tick"])
            payload_path = modality_dir_payload(mod_dir, meta_path, modality)
            record_count: int | None
            if payload_path is not None and payload_path.exists():
                if modality == "lidar":
                    record_count = _npz_point_count(payload_path)
                else:
                    record_count = int(payload_path.stat().st_size)
            else:
                record_count = None
            if record_count is None:
                fields_obs = {
                    "observation.path": str(meta_path.relative_to(capture_root)),
                    "observation.modality": modality,
                    "observation.tick": tick,
                    "observation.observer_entity_id": str(meta.get("uav_entity_id") or
                                                            meta.get("source_uav_entity_id") or ""),
                    "observation.record_count": {"kind": "missing"},
                }
            else:
                fields_obs = {
                    "observation.path": str(meta_path.relative_to(capture_root)),
                    "observation.modality": modality,
                    "observation.tick": tick,
                    "observation.observer_entity_id": str(meta.get("uav_entity_id") or
                                                          meta.get("source_uav_entity_id") or ""),
                    "observation.record_count": record_count,
                }
            episode.add({
                "record_kind": "observation",
                "id": f"{meta.get('logical_sample_id', meta_path.stem)}|{modality}",
                "tick": tick,
                "source": {"path": str(meta_path), "line": None},
                "fields": fields_obs,
            })
            seen_modalities.add(modality)
            report.modalities[modality] += 1
    for m in ("rgb", "lidar"):
        if m not in seen_modalities and m not in report.missing_modalities:
            report.missing_modalities.append(m)
    report.canonical_records["observation"] = sum(1 for _ in episode.by_kind("observation"))


def modality_dir_payload(mod_dir: Path, meta_path: Path, modality: str) -> Path | None:
    if modality == "lidar":
        cand = meta_path.with_suffix(".npz")
        return cand if cand.exists() else None
    cand = meta_path.with_suffix(".png")
    return cand if cand.exists() else None


def import_episode(render_ready_root: Path, semantic_truth_root: Path,
                   capture_root: Path | None, episode_id: str,
                   view_dir_name: str | None, observer_entity_id: str | None) -> tuple[
        CanonicalEpisode, ImportReport]:
    """Import one complete episode from read-only sources."""
    ep_dir = render_ready_root / episode_id
    sem_dir = semantic_truth_root / episode_id
    report = ImportReport(episode_id=episode_id)
    report.event_traceability["_observer_entity_id"] = observer_entity_id
    report.sources = {
        "truth_frames": str(ep_dir / "truth_frames.jsonl"),
        "roster": str(ep_dir / "global_entity_roster.json"),
        "world_truth_deltas": str(sem_dir / "world_truth_graph_deltas.jsonl"),
        "events": str(sem_dir / "event_occurrences.jsonl"),
        "capture": str(capture_root) if capture_root else None,
    }
    episode = CanonicalEpisode(episode_id)
    import_roster(episode, ep_dir / "global_entity_roster.json", report)
    import_truth_frames(episode, ep_dir / "truth_frames.jsonl", report)
    import_truth_deltas(episode, sem_dir / "world_truth_graph_deltas.jsonl", report)
    import_events(episode, sem_dir / "event_occurrences.jsonl", report)
    if capture_root is not None and view_dir_name:
        import_capture_path(episode, capture_root, view_dir_name, report)
    else:
        report.missing_modalities.extend(["rgb", "lidar"])
    # final tick-coverage consistency: frame stream must be gapless over its span
    ticks = report.ticks_covered
    if ticks:
        expected = list(range(ticks[0], ticks[-1] + 1))
        if ticks != expected:
            report.errors.append(
                f"frame tick coverage gap: observed {len(ticks)} ticks over "
                f"{ticks[0]}..{ticks[-1]} (expected {len(expected)})")
    return episode, report


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    import os
    import time

    ap = argparse.ArgumentParser(description="S01.02 read-only episode import")
    ap.add_argument("--episode", required=True)
    ap.add_argument("--view", required=True, help="capture view directory name")
    ap.add_argument("--observer", required=True, help="observer entity id")
    ap.add_argument("--render-ready-root", default=str(REPO / "aw_data/render_ready_episodes"))
    ap.add_argument("--semantic-truth-root", default=str(REPO / "aw_data/objective_semantic_truth"))
    ap.add_argument("--capture-root", required=True)
    ap.add_argument("--out-derived", required=True, help="output canonical records jsonl")
    ap.add_argument("--out-manifest", required=True, help="output import manifest json")
    args = ap.parse_args(argv)

    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    t0 = time.time()
    episode, report = import_episode(
        render_ready_root=Path(args.render_ready_root),
        semantic_truth_root=Path(args.semantic_truth_root),
        capture_root=Path(args.capture_root),
        episode_id=args.episode,
        view_dir_name=args.view,
        observer_entity_id=args.observer,
    )
    out = Path(args.out_derived)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for rec in episode.records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = report.to_dict()
    manifest["canonical_records_jsonl"] = str(out)
    manifest["canonical_record_total"] = len(episode.records)
    manifest["wall_seconds"] = round(time.time() - t0, 3)
    mp = Path(args.out_manifest)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"episode": args.episode, "records": len(episode.records),
                      "wall_seconds": manifest["wall_seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
