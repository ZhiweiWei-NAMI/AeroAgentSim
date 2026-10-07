"""Closed-motion airspace segments with explicit reference-point interpolation.

This records sampled motion evidence; it neither enforces a flight fence nor
certifies a runtime seal. Segments are not maximal whole-flight incursions.
The independent verifier must reconstruct them from the sealed source stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
from typing import Literal

from pydantic import Field, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.airspace_events import (
    NoFlyEventsReport, PositionSample, detect_no_fly_events,
)
from aero_bench.tasks.logistics.contracts import LogisticsTaskPackage
from aero_bench.tasks.logistics.observation_ingress import LogisticsObservationBatch
from aero_bench.tasks.logistics.orders import LogisticsIdentifier
from aero_bench.tasks.logistics.physical_observations import (
    FacilityPadPhysicalObservation, SCENE_ORIGIN_TOLERANCE_DEG, SCENE_ORIGIN_TOLERANCE_M,
)
from aero_bench.tasks.logistics.runtime_bindings import LogisticsRuntimeBindings


class AirspaceMotionWitness(StrictModel):
    aircraft_id: LogisticsIdentifier
    provider_id: Identifier
    native_vehicle_id: Identifier
    at: SimulationTime
    position: PositionSample
    source_event_id: Identifier
    source_state_sample_digest: Sha256
    source_scene_state_digest: Sha256
    source_stage_barrier_digest: Sha256
    source_evidence_path: str
    source_evidence_sha256: Sha256
    observation_digests: tuple[Sha256, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_motion_witness(self) -> AirspaceMotionWitness:
        if self.position.sample_ref != self.source_event_id or self.position.time_s != self.at.sim_time_ns / 1e9:
            raise ValueError("airspace position must bind the exact source event and native sample time")
        if self.observation_digests != tuple(sorted(set(self.observation_digests))):
            raise ValueError("airspace source observation digests must be sorted and unique")
        return self


class AirspaceSegmentRecord(StrictModel):
    schema_version: Literal["aero-bench.logistics-airspace-segment/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    package_digest: Sha256
    recorded_at: SimulationTime
    source: Literal["closed_motion_observation_stream"] = "closed_motion_observation_stream"
    provenance_verified: Literal[False] = False
    scope: Literal["sample_segment_reference_point_only"] = "sample_segment_reference_point_only"
    witnesses: tuple[AirspaceMotionWitness, ...] = Field(min_length=1, max_length=2)
    report: NoFlyEventsReport

    @model_validator(mode="after")
    def coherent_segment(self) -> AirspaceSegmentRecord:
        if self.recorded_at != self.witnesses[-1].at or self.report.sample_count != len(self.witnesses):
            raise ValueError("airspace report must bind its exact source window")
        if not self.report.violations:
            raise ValueError("airspace segment must contain a detected violation")
        for index, witness in enumerate(self.witnesses):
            if witness.aircraft_id != self.report.subject_id:
                raise ValueError("airspace witness and detector subject differ")
            if index and witness.at.sim_time_ns <= self.witnesses[index - 1].at.sim_time_ns:
                raise ValueError("airspace source witnesses must have increasing time")
        refs = {witness.source_event_id for witness in self.witnesses}
        if any(item.bound_start_sample_ref not in refs or item.bound_end_sample_ref not in refs
               for item in self.report.violations):
            raise ValueError("airspace report names an unavailable source sample")
        return self

    def canonical_digest(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedAirspaceBatch:
    batch_digest: str
    previous_batch_digest: str | None
    at: SimulationTime
    moments: tuple[AirspaceMotionWitness, ...]
    segments: tuple[AirspaceSegmentRecord, ...]
    replayed: bool
    owner: object = field(repr=False)


def _motion_identity(observation: FacilityPadPhysicalObservation) -> dict:
    # Pad assessments may differ for the same vehicle; the source motion must
    # not. Include every measured sample field and every source/frame identity.
    return {
        "sample": observation.sample.model_dump(mode="json"),
        "provider_id": observation.provider_id, "native_vehicle_id": observation.native_vehicle_id,
        "source_event_id": observation.source_event_id,
        "source_state_sample_digest": observation.source_state_sample_digest,
        "source_scene_state_digest": observation.source_scene_state_digest,
        "source_stage_barrier_digest": observation.source_stage_barrier_digest,
        "source_evidence_path": observation.source_evidence_path,
        "source_evidence_sha256": observation.source_evidence_sha256,
        "source_frame_origin": observation.source_frame_origin.model_dump(mode="json"),
    }


class ClosedMotionAirspaceTracker:
    """Prepare before RPC; commit only after the Business journal acknowledges."""

    def __init__(self, *, run_id: str, scenario_digest: str, step_ns: int,
                 package: LogisticsTaskPackage, bindings: LogisticsRuntimeBindings):
        if type(step_ns) is not int or step_ns <= 0:
            raise ValueError("airspace tracking requires the declared positive tick duration")
        self._run_id = run_id
        self._scenario_digest = scenario_digest
        self._step_ns = step_ns
        self._package = LogisticsTaskPackage.model_validate(package.model_dump(mode="json"))
        self._bindings = {item.aircraft_id: item for item in bindings.aircraft}
        if set(self._bindings) != {unit.aircraft_id for unit in package.aircraft_units()}:
            raise ValueError("airspace tracking must cover the exact declared native fleet")
        self._last_at: SimulationTime | None = None
        self._last_batch_digest: str | None = None
        self._moments: dict[str, AirspaceMotionWitness] = {}

    def prepare(self, batch: LogisticsObservationBatch) -> PreparedAirspaceBatch:
        if not isinstance(batch, LogisticsObservationBatch):
            raise TypeError("airspace tracking requires a typed closed-motion batch")
        batch = LogisticsObservationBatch.model_validate(batch.model_dump(mode="json"))
        if (batch.run_id, batch.scenario_digest) != (self._run_id, self._scenario_digest):
            raise ValueError("airspace observation belongs to another run or scenario")
        digest = hashlib.sha256(canonical_json_bytes(batch.model_dump(mode="json"))).hexdigest()
        if batch.at == self._last_at:
            if digest != self._last_batch_digest:
                raise ValueError("airspace observation conflicts with the committed barrier")
            return PreparedAirspaceBatch(digest, digest, batch.at, (), (), True, self)
        expected_tick = 1 if self._last_at is None else self._last_at.tick + 1
        expected_ns = self._step_ns if self._last_at is None else self._last_at.sim_time_ns + self._step_ns
        if batch.at != SimulationTime(tick=expected_tick, sim_time_ns=expected_ns):
            raise ValueError("airspace motion must cover every declared consecutive barrier from tick one")
        grouped: dict[str, list[FacilityPadPhysicalObservation]] = {}
        for observation in batch.observations:
            binding = self._bindings.get(observation.aircraft_id)
            if binding is None or (observation.provider_id, observation.native_vehicle_id,
                                   observation.fleet_entry_id, observation.visual_asset_id) != (
                binding.provider_id, binding.vehicle_id, binding.fleet_entry_id, binding.visual_asset_id,
            ):
                raise ValueError("airspace observation differs from its exact native aircraft binding")
            origin = observation.source_frame_origin
            declared = self._package.scene
            for actual, expected, tolerance in (
                (origin.latitude_deg, declared.origin_latitude_deg, SCENE_ORIGIN_TOLERANCE_DEG),
                (origin.longitude_deg, declared.origin_longitude_deg, SCENE_ORIGIN_TOLERANCE_DEG),
                (origin.ellipsoid_height_m, declared.origin_altitude_m, SCENE_ORIGIN_TOLERANCE_M),
            ):
                if not math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance):
                    raise ValueError("airspace observation frame origin differs from the declared native scene")
            grouped.setdefault(observation.aircraft_id, []).append(observation)
        if set(grouped) != set(self._bindings):
            raise ValueError("airspace observation batch omits part of the declared native fleet")
        moments = []
        segments = []
        for aircraft_id, observations in sorted(grouped.items()):
            first = observations[0]
            identity = _motion_identity(first)
            if any(_motion_identity(item) != identity for item in observations[1:]):
                raise ValueError("different pad observations carry conflicting native motion for one aircraft")
            sample = first.sample
            moment = AirspaceMotionWitness(
                aircraft_id=aircraft_id, provider_id=first.provider_id, native_vehicle_id=first.native_vehicle_id,
                at=batch.at,
                position=PositionSample(sample_ref=first.source_event_id, time_s=batch.at.sim_time_ns / 1e9,
                                        x=sample.x, y=sample.y, z=sample.z),
                source_event_id=first.source_event_id,
                source_state_sample_digest=first.source_state_sample_digest,
                source_scene_state_digest=first.source_scene_state_digest,
                source_stage_barrier_digest=first.source_stage_barrier_digest,
                source_evidence_path=first.source_evidence_path, source_evidence_sha256=first.source_evidence_sha256,
                observation_digests=tuple(sorted(item.observation_digest for item in observations)),
            )
            moments.append(moment)
            previous = self._moments.get(aircraft_id)
            witnesses = (moment,) if previous is None else (previous, moment)
            for zone in self._package.no_fly_zones:
                report = detect_no_fly_events(zone, tuple(item.position for item in witnesses), subject_id=aircraft_id)
                if report.violations:
                    segments.append(AirspaceSegmentRecord(
                        schema_version="aero-bench.logistics-airspace-segment/v1", run_id=self._run_id,
                        scenario_digest=self._scenario_digest, package_digest=self._package.canonical_digest(),
                        recorded_at=batch.at, witnesses=witnesses, report=report,
                    ))
        return PreparedAirspaceBatch(digest, self._last_batch_digest, batch.at,
                                     tuple(moments), tuple(segments), False, self)

    def commit(self, prepared: PreparedAirspaceBatch) -> None:
        if not isinstance(prepared, PreparedAirspaceBatch) or prepared.owner is not self:
            raise ValueError("airspace preparation belongs to another tracker")
        if prepared.previous_batch_digest != self._last_batch_digest:
            raise ValueError("airspace preparation became stale before journal acknowledgment")
        if prepared.replayed:
            return
        self._last_at = prepared.at
        self._last_batch_digest = prepared.batch_digest
        self._moments = {item.aircraft_id: item for item in prepared.moments}
