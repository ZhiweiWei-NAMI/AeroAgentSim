"""Bind closed PX4 motion events to their sealed native snapshot stream.

The non-inspection workload writes canonical JSONL snapshots. Each state event
names the hash of the file prefix at its step, not the final artifact hash.
This reader checks that source relationship; it produces no task verdict and
does not accept the inspection trajectory-array format.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from aero_bench.artifacts.contracts import ArtifactRecord, SealManifest
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.sealed_motion import (
    SealedMotionEvidence,
    SealedMotionFrame,
    load_sealed_motion_evidence,
)
from aero_bench.serialization import canonical_json_bytes


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("PX4 snapshot JSON contains duplicate keys")
        document[key] = value
    return document


def _constant(value: str) -> object:
    raise ValueError(f"PX4 snapshot JSON contains a non-finite value: {value}")


def _json(raw: bytes | str) -> object:
    return json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)


class NativeVehicleSnapshot(StrictModel):
    vehicle_id: Identifier
    pose_json: str
    position_wgs84_json: str
    velocity_json: str
    angular_velocity_json: str
    attitude_json: str
    flight_mode: str
    armed: bool
    in_air: bool
    landed: bool
    landed_state: str
    battery_percent: Annotated[float, Field(ge=0, le=100)]
    health: str
    contacts: tuple[str, ...]
    ground_contact: bool
    collision_contact: bool
    simulation_time_ns: Annotated[int, Field(ge=0)]

    @field_validator(
        "pose_json",
        "position_wgs84_json",
        "velocity_json",
        "angular_velocity_json",
        "attitude_json",
        "health",
    )
    @classmethod
    def canonical_object(cls, value: str) -> str:
        parsed = _json(value)
        if (
            not isinstance(parsed, dict)
            or canonical_json_bytes(parsed).decode() != value
        ):
            raise ValueError("native telemetry must contain canonical JSON objects")
        return value

    def event_payload(self) -> dict[str, object]:
        values = self.model_dump(mode="json")
        values["contacts_json"] = canonical_json_bytes(values.pop("contacts")).decode()
        return values


class NativeSnapshot(StrictModel):
    sim_time_ns: Annotated[int, Field(ge=0)]
    vehicles: tuple[NativeVehicleSnapshot, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_vehicle_times(self) -> NativeSnapshot:
        ids = [item.vehicle_id for item in self.vehicles]
        if len(ids) != len(set(ids)):
            raise ValueError("native snapshot contains duplicate vehicle identities")
        if any(item.simulation_time_ns != self.sim_time_ns for item in self.vehicles):
            raise ValueError("native telemetry time differs from its snapshot")
        return self


class NativeSnapshotRecord(StrictModel):
    schema_version: Literal["aero-bench.px4-evidence/v1"]
    provider_id: Identifier
    run_id: Sha256
    operation: Literal["reset", "step_stage", "snapshot"]
    tick: Annotated[int, Field(ge=0)]
    sim_time_ns: Annotated[int, Field(ge=0)]
    snapshot: NativeSnapshot
    snapshot_sha256: Sha256
    px4_version: str
    px4_commit: str
    gazebo_version: str
    gazebo_commit: str
    mavsdk_version: str
    mavsdk_commit: str
    runtime_image: str
    config_digest: Sha256
    artifact_id: Identifier
    artifact_type: Literal["trajectory"]

    @model_validator(mode="after")
    def exact_snapshot_digest(self) -> NativeSnapshotRecord:
        if self.snapshot.sim_time_ns != self.sim_time_ns:
            raise ValueError("native snapshot record time differs from its state")
        if (
            hashlib.sha256(
                canonical_json_bytes(self.snapshot.model_dump(mode="json"))
            ).hexdigest()
            != self.snapshot_sha256
        ):
            raise ValueError("native snapshot digest differs from its recorded state")
        return self


@dataclass(frozen=True, slots=True)
class NativeMotionSourceEvidence:
    motion: SealedMotionEvidence
    artifact: ArtifactRecord
    records: tuple[NativeSnapshotRecord, ...]


def validate_px4_snapshot_stream(
    *,
    raw: bytes,
    artifact: ArtifactRecord,
    run_id: str,
    config: Px4GazeboConfig,
    config_digest: str,
    runtime_image: str,
    step_ns: int,
    frames: tuple[SealedMotionFrame, ...],
) -> tuple[NativeSnapshotRecord, ...]:
    """Check source bytes against explicit authority, without verifying a seal.

    Callers needing seal authority must use ``load_sealed_px4_motion``. This
    low-level function neither issues provenance nor changes business state.
    """
    if type(step_ns) is not int or step_ns <= 0:
        raise ValueError(
            "native source checks require the declared positive tick duration"
        )
    if (
        artifact.artifact_type != "trajectory"
        or artifact.producer_id != config.provider_id
    ):
        raise ValueError(
            "native source artifact differs from the declared PX4 Provider"
        )
    if (
        len(raw) != artifact.size_bytes
        or hashlib.sha256(raw).hexdigest() != artifact.sha256
    ):
        raise ValueError(
            "native source artifact bytes differ from the declared hash or size"
        )
    records = []
    steps: dict[int, tuple[NativeSnapshotRecord, str]] = {}
    current = SimulationTime(tick=0, sim_time_ns=0)
    prefix = hashlib.sha256()
    declared_vehicles = {vehicle.vehicle_id for vehicle in config.vehicles}
    for index, line in enumerate(raw.splitlines(keepends=True)):
        record = NativeSnapshotRecord.model_validate(_json(line))
        if canonical_json_bytes(record.model_dump(mode="json")) + b"\n" != line:
            raise ValueError(
                "native trajectory must be canonical newline-terminated JSONL"
            )
        expected = {
            "run_id": run_id,
            "provider_id": config.provider_id,
            "config_digest": config_digest,
            "runtime_image": runtime_image,
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type,
            "px4_version": config.px4.version,
            "px4_commit": config.px4.commit,
            "gazebo_version": config.gazebo.version,
            "gazebo_commit": config.gazebo.commit,
            "mavsdk_version": config.mavsdk.version,
            "mavsdk_commit": config.mavsdk.commit,
        }
        if any(getattr(record, key) != value for key, value in expected.items()):
            raise ValueError(
                "native snapshot identity differs from the pinned run, Provider or software"
            )
        if {
            vehicle.vehicle_id for vehicle in record.snapshot.vehicles
        } != declared_vehicles:
            raise ValueError(
                "native snapshot must cover the exact declared vehicle inventory"
            )
        at = SimulationTime(tick=record.tick, sim_time_ns=record.sim_time_ns)
        prefix.update(line)
        if index == 0:
            if record.operation != "reset" or at != current:
                raise ValueError(
                    "native snapshot stream must start with its tick-zero reset"
                )
        elif record.operation == "step_stage":
            expected_time = SimulationTime(
                tick=current.tick + 1, sim_time_ns=current.sim_time_ns + step_ns
            )
            if at != expected_time:
                raise ValueError(
                    "native snapshot steps must cover consecutive declared barriers"
                )
            current = at
            steps[at.tick] = (record, prefix.hexdigest())
        elif record.operation != "snapshot" or at != current:
            raise ValueError(
                "native snapshot operation is outside the current paused barrier"
            )
        records.append(record)
    if not records or set(steps) != {frame.scene_state.at.tick for frame in frames}:
        raise ValueError(
            "native snapshot stream does not cover the exact closed motion history"
        )
    for frame in frames:
        record, prefix_digest = steps[frame.scene_state.at.tick]
        if frame.scene_state.at != SimulationTime(
            tick=record.tick, sim_time_ns=record.sim_time_ns
        ):
            raise ValueError("native snapshot differs from its closed motion target")
        receipts = [
            item
            for item in frame.scene_state.stage_barrier.receipts
            if item.provider_id == config.provider_id
        ]
        if len(receipts) != 1 or receipts[0].state_digest != record.snapshot_sha256:
            raise ValueError(
                "closed native step receipt does not bind its exact snapshot digest"
            )
        events = [
            event for event in frame.events if event.provider_id == config.provider_id
        ]
        expected_events = {
            f"state.{vehicle.vehicle_id}.{record.tick}": {
                **vehicle.event_payload(),
                "evidence_path": artifact.relative_path,
                "evidence_sha256": prefix_digest,
            }
            for vehicle in record.snapshot.vehicles
        }
        if len(events) != len(expected_events) or {
            event.event_id for event in events
        } != set(expected_events):
            raise ValueError(
                "closed native source events omit or repeat a declared vehicle"
            )
        for event in events:
            if (
                event.payload_schema_id != "px4.state.v1"
                or event.time != frame.scene_state.at
            ):
                raise ValueError(
                    "closed native source event has another schema or time"
                )
            payload = {item.name: item.value for item in event.payload}
            if len(payload) != len(event.payload) or canonical_json_bytes(
                payload
            ) != canonical_json_bytes(expected_events[event.event_id]):
                raise ValueError(
                    "closed native event facts or trajectory prefix differ from sealed source"
                )
    return tuple(records)


def load_sealed_px4_motion(
    *,
    run: ResolvedRunSpec,
    reader: BundleReader,
    seal: SealManifest,
    seal_root: Path,
    provider_id: str,
    manifest_name: str | None = "seal-manifest.json",
) -> NativeMotionSourceEvidence:
    """Verify a non-inspection PX4 source stream within the complete runtime seal."""
    motion = load_sealed_motion_evidence(
        run=run, seal=seal, seal_root=seal_root, manifest_name=manifest_name
    )
    providers = [
        item for item in run.environment.providers if item.provider_id == provider_id
    ]
    if len(providers) != 1 or providers[0].adapter != "px4.gazebo":
        raise ValueError("native source checks require the declared px4.gazebo adapter")
    provider = providers[0]
    reader.validate_schema_bound_file(provider.config)
    config = Px4GazeboConfig.model_validate(reader.load_document(provider.config.file))
    if config.provider_id != provider_id:
        raise ValueError("native source config names another Provider")
    if any(
        binding.endpoint_id == provider_id and binding.inspection is not None
        for binding in run.scenario.task.observations
    ):
        raise ValueError(
            "inspection trajectory arrays are outside the native snapshot-stream profile"
        )
    artifacts = [
        item
        for item in seal.artifacts
        if item.producer_id == provider_id and item.artifact_type == "trajectory"
    ]
    if len(artifacts) != 1:
        raise ValueError(
            "native motion requires exactly one declared PX4 trajectory artifact"
        )
    artifact = artifacts[0]
    records = validate_px4_snapshot_stream(
        raw=(seal_root / artifact.relative_path).read_bytes(),
        artifact=artifact,
        run_id=run.run_id,
        config=config,
        config_digest=provider.config.file.sha256,
        runtime_image=provider.workload.runtime.image,
        step_ns=run.environment.clock.step_ns,
        frames=motion.frames,
    )
    resets = [
        item
        for item in motion.ledger.records
        if item.event.event_type == "provider.reset"
        and item.event.source == provider_id
    ]
    if len(resets) != 1 or {
        item.name: item.value for item in resets[0].event.payload
    } != {
        "state_digest": records[0].snapshot_sha256,
    }:
        raise ValueError("sealed native reset receipt does not bind its exact snapshot")
    return NativeMotionSourceEvidence(motion, artifact, records)
