"""Read closed motion inputs from a seal without producing a task verdict.

This checks the declared artifact inventory, authoritative runtime ledger,
SceneState history and source-event placement. Domain verifiers must still
check their own Provider evidence and evaluate their declared goals.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import stat

from aero_bench.artifacts.contracts import ArtifactRecord, SealManifest, seal_manifest
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.config.models import NamedValue
from aero_bench.runtime.contracts import ProviderEvent, SceneState, StageBarrier
from aero_bench.runtime.evidence import (
    _reconstruct_stage_barrier,
    _reconstruct_stage_receipt,
    load_sealed_event_ledger,
)
from aero_bench.runtime.ledger import EventLedger, LedgerRecord
from aero_bench.runtime.scene_history import scene_state_history_from_jsonl_bytes
from aero_bench.serialization import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class SealedMotionFrame:
    scene_state: SceneState
    stage_barriers: tuple[StageBarrier, ...]
    events: tuple[ProviderEvent, ...]
    motion_event_sequences: tuple[int, ...]
    commit_sequence: int


@dataclass(frozen=True, slots=True)
class SealedMotionEvidence:
    seal: SealManifest
    ledger: EventLedger
    history_artifact: ArtifactRecord
    frames: tuple[SealedMotionFrame, ...]


def _payload(record: LedgerRecord) -> dict[str, object]:
    return {item.name: item.value for item in record.event.payload}


def load_sealed_motion_evidence(
    *, run: ResolvedRunSpec, seal: SealManifest, seal_root: Path,
    manifest_name: str | None = "seal-manifest.json",
) -> SealedMotionEvidence:
    """Reconstruct exact closed-stage inputs; never infer a missing sample.

    ``manifest_name=None`` explicitly selects a seal whose manifest is external
    to its artifact directory. There is no probe or fallback between layouts.
    """
    run = ResolvedRunSpec.model_validate(run.model_dump(mode="json"))
    seal = SealManifest.model_validate(seal.model_dump(mode="json"))
    if (seal.run_id, seal.execution_scope) != (run.run_id, run.execution_scope):
        raise ValueError("motion seal belongs to another run or execution scope")
    if run.execution_scope != "formal_benchmark":
        raise ValueError("sealed motion reconstruction requires formal benchmark authority")
    if stat.S_ISLNK(seal_root.lstat().st_mode):
        raise ValueError("motion seal root cannot be a symbolic link")
    requirements = {item.artifact_id: item for item in run.artifact_requirements}
    if {item.artifact_id for item in seal.artifacts} != set(requirements):
        raise ValueError("motion seal does not cover the exact declared artifact inventory")
    for artifact in seal.artifacts:
        requirement = requirements[artifact.artifact_id]
        if (artifact.artifact_type, artifact.producer_id, artifact.visibility, artifact.relative_path) != (
            requirement.artifact_type, requirement.producer_id, requirement.visibility, requirement.relative_path,
        ) or artifact.size_bytes > requirement.max_size_bytes:
            raise ValueError("motion seal artifact differs from its declared requirement")
    verified = seal_manifest(
        root=seal_root, run_id=run.run_id, attempt_id=seal.attempt_id,
        execution_scope=run.execution_scope, event_chain_root=seal.event_chain_root,
        artifacts=seal.artifacts, manifest_name=manifest_name,
    )
    if verified != seal:
        raise ValueError("motion seal differs from its verified canonical manifest")
    ledger = load_sealed_event_ledger(run=run, seal=seal, seal_root=seal_root)
    histories = tuple(item for item in seal.artifacts if item.artifact_type == "scene.state-history")
    if len(histories) != 1 or (histories[0].producer_id, histories[0].visibility) != ("harness", "public"):
        raise ValueError("motion evidence requires one declared Harness SceneState history")
    history = histories[0]
    commits = tuple(item for item in ledger.records if item.event.event_type == "scene.state.committed")
    terminal = ledger.records[-1].event
    states = scene_state_history_from_jsonl_bytes(
        (seal_root / history.relative_path).read_bytes(),
        aborted_before_first_motion=terminal.event_type == "run.aborted" and not commits,
    )
    if len(states) != len(commits):
        raise ValueError("sealed history does not cover every committed SceneState")
    by_tick: dict[int, list[LedgerRecord]] = {}
    for record in ledger.records:
        by_tick.setdefault(record.event.time.tick, []).append(record)
    frames = []
    previous_tick_commit = -1
    for state, scene_commit in zip(states, commits, strict=True):
        if (state.run_id, state.scenario_digest) != (run.run_id, run.scenario.scenario_digest):
            raise ValueError("sealed SceneState belongs to another run or scenario")
        expected_commit = {
            "barrier_digest": state.stage_barrier.barrier_digest,
            "contribution_digests": canonical_json_bytes(list(state.contribution_digests)).decode(),
            "provider_ids": canonical_json_bytes(list(state.stage_barrier.provider_ids)).decode(),
            "receipt_digests": canonical_json_bytes(list(state.stage_barrier.receipt_digests)).decode(),
            "run_id": run.run_id, "scenario_digest": run.scenario.scenario_digest,
            "scene_state_digest": state.scene_state_digest, "stage": "motion",
            "target_tick": state.at.tick, "target_sim_time_ns": state.at.sim_time_ns,
        }
        if scene_commit.event.time != state.at or _payload(scene_commit) != expected_commit:
            raise ValueError("sealed SceneState disagrees with its authoritative commit")
        records = by_tick[state.at.tick]
        receipts = {
            record.event.source: _reconstruct_stage_receipt(_payload(record))
            for record in records if record.event.event_type == "provider.step-receipt"
        }
        closures = tuple(record for record in records if record.event.event_type == "stage.barrier-closed")
        barriers = tuple(_reconstruct_stage_barrier(
            _payload(record), tuple(receipts[key] for key in json.loads(_payload(record)["provider_ids"])),
        ) for record in closures)
        if not barriers or barriers[0] != state.stage_barrier:
            raise ValueError("sealed SceneState does not bind its exact closed motion barrier")
        source_records = tuple(record for record in records
                               if record.event.payload_schema_id == "px4.state.v1")
        motion_receipt_sequences = {
            record.event.source: record.sequence for record in records
            if record.event.event_type == "provider.step-receipt" and _payload(record)["stage"] == "motion"
        }
        for record in source_records:
            event = record.event
            if (event.source_kind != "provider" or event.source != event.provider_id
                or event.source not in motion_receipt_sequences or event.time != state.at
                or not previous_tick_commit < record.sequence < motion_receipt_sequences[event.source]):
                raise ValueError("native state source event is outside its closed motion stage")
        events = tuple(ProviderEvent(
            provider_id=record.event.source, event_id=record.event.event_type,
            time=record.event.time, payload_schema_id=record.event.payload_schema_id,
            payload=tuple(NamedValue(name=item.name, value=item.value) for item in record.event.payload),
        ) for record in source_records)
        tick_commit = next(record for record in records if record.event.event_type == "barrier.committed")
        frames.append(SealedMotionFrame(state, barriers, events,
                                       tuple(record.sequence for record in source_records), tick_commit.sequence))
        previous_tick_commit = tick_commit.sequence
    return SealedMotionEvidence(seal, ledger, history, tuple(frames))
