from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aero_bench.artifacts.contracts import SealManifest, seal_manifest
from aero_bench.runtime.ledger import EventLedger, LedgerRecord
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import ObservationMetadata, TrajectoryEvidence


def payload(record: LedgerRecord) -> dict[str, object]:
    event = record.event
    return {item.name: item.value for item in event.payload}


def canonical_models(path: Path, model: type) -> tuple[object, ...]:
    raw = path.read_bytes()
    decoded = json.loads(raw)
    if not isinstance(decoded, list):
        raise ValueError(f"{path.name} must contain a JSON array")
    values = tuple(model.model_validate(item) for item in decoded)
    if raw != canonical_json_bytes([item.model_dump(mode="json") for item in values]):
        raise ValueError(f"{path.name} is not canonical typed evidence")
    return values


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_px4_lifecycle.py SEAL_ROOT SEAL_JSON")
    root = Path(sys.argv[1])
    seal_path = Path(sys.argv[2])
    seal_bytes = seal_path.read_bytes()
    seal = SealManifest.model_validate(json.loads(seal_bytes))
    if seal_bytes != canonical_json_bytes(seal.model_dump(mode="json")) + b"\n":
        raise ValueError("seal manifest is not canonical JSON")
    verified = seal_manifest(
        root=root,
        run_id=seal.run_id,
        attempt_id=seal.attempt_id,
        execution_scope=seal.execution_scope,
        event_chain_root=seal.event_chain_root,
        artifacts=seal.artifacts,
    )
    if verified != seal or seal.execution_scope != "executor_validation":
        raise ValueError("component seal identity is invalid")

    by_type = {record.artifact_type: record for record in seal.artifacts}
    if set(by_type) != {"event.log", "observation", "trajectory"}:
        raise ValueError("component seal artifact inventory is invalid")
    ledger = EventLedger.read_jsonl(root / by_type["event.log"].relative_path)
    if ledger.chain_root != seal.event_chain_root:
        raise ValueError("event ledger root does not match the seal")
    if ledger.records[0].event.event_type != "validation.scope" or payload(
        ledger.records[0]
    ) != {"execution_scope": "executor_validation"}:
        raise ValueError("component ledger scope is invalid")
    if ledger.records[-1].event.event_type != "run.completed":
        raise ValueError("component ledger is not terminal")
    lifecycle = tuple(
        record
        for record in ledger.records
        if record.event.event_type == "px4.lifecycle-validated"
    )
    if len(lifecycle) != 1:
        raise ValueError("component ledger omitted the lifecycle validation event")
    claims = payload(lifecycle[0])
    if claims.get("command_phase") != "completed" or claims.get("paused_seconds") != 12:
        raise ValueError("deferred command or paused barrier was not validated")

    trajectories = canonical_models(
        root / by_type["trajectory"].relative_path, TrajectoryEvidence
    )
    observations = canonical_models(
        root / by_type["observation"].relative_path, ObservationMetadata
    )
    if [item.time.tick for item in trajectories] != [0, 1, 2]:
        raise ValueError("trajectory evidence does not cover exact barriers 0, 1, 2")
    if len(observations) != 1 or observations[0].time.tick != 1:
        raise ValueError("camera evidence is not bound to barrier 1")
    observation = observations[0]
    if observation.visual_kind != "external_renderer" or observation.visual_status != "reserved":
        raise ValueError("observation is not a reserved external-visual geometry record")
    if claims.get("payload_digest") != observation.payload_digest:
        raise ValueError("lifecycle ledger observation digest does not match sealed evidence")
    if claims.get("trajectory_sha256") != by_type["trajectory"].sha256:
        raise ValueError("lifecycle ledger trajectory digest does not match the seal")
    if claims.get("observation_sha256") != by_type["observation"].sha256:
        raise ValueError("lifecycle ledger observation digest does not match the seal")

    print(
        json.dumps(
            {
                "event_chain_root": seal.event_chain_root,
                "manifest_digest": seal.manifest_digest,
                "observation_records": len(observations),
                "payload_digest": observation.payload_digest,
                "status": "passed",
                "trajectory_records": len(trajectories),
                "verification_scope": "px4_component_regression",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
