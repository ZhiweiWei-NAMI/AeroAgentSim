"""Typed logistics observation ingress and immutable observation journal.

This module is the *narrow*, strict wiring contract between the future
logistics runtime hook (the only legitimate producer of closed-motion
physical observations) and the real Logistics Business provider workload (the
only authority that persists them).  It deliberately does **not** implement a
Provider, a RuntimeHook, or an agent tool.

Everything an observation can name is encoded in the accepted
:class:`~aero_bench.tasks.logistics.physical_observations.FacilityPadPhysicalObservation`
record produced by :func:`observe_facility_pad_presence` under explicitly
declared pose-reference calibrations.  The batch is the typed on-wire payload
for the private harness-to-business RPC operation
``logistics.observation.ingest``; the journal is the immutable, deduped,
replay-stable persistence the business provider seals into its declared
artifact.

Design rules enforced here
--------------------------

* No arbitrary ``evidence_ref`` string is ever invented or accepted.  The only
  source authority is the sealed motion ``SceneState`` / stage-barrier digest
  and the bound ``px4.state.v1`` source event carried by each immutable
  observation record.
* ``pose_reference_above_contact_m`` is a *required declared calibration* input
  per authored aircraft.  It is never derived from body height and it is never
  defaulted; a missing calibration is an exact error.
* Replay / deduplication / reject-conflict are explicit and atomic:
  :func:`append_observations_batch` never mutates the supplied journal; it
  returns a new journal and the appended records, or raises before anything
  changes.  A fully re-sent barrier batch is an idempotent replay; any
  divergence (missing/extra observations, conflicting content, a digest already
  seen under another barrier, or a stale time) is rejected as a conflict.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import (
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import LogisticsTaskPackage
from aero_bench.tasks.logistics.facilities import FacilityIdentifier
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import PresenceTolerances
from aero_bench.tasks.logistics.orders import LogisticsIdentifier
from aero_bench.tasks.logistics.physical_observations import (
    DeclaredAircraftPoseReference,
    FacilityPadPhysicalObservation,
    PhysicalObservationError,
    observe_facility_pad_presence,
)
from aero_bench.tasks.logistics.runtime_bindings import LogisticsRuntimeBindings
from aero_bench.world.resolved import ResolvedScenario

#: The private harness-to-business RPC operation.  The Harness session is the
#: only legitimate client; this operation is never exposed as an agent tool and
#: it never invents pose/evidence.
LOGISTICS_OBSERVATION_INGEST_OPERATION = "logistics.observation.ingest"

LOGISTICS_OBSERVATION_BATCH_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-observation-batch/v1"
] = "aero-bench.logistics-observation-batch/v1"
LOGISTICS_OBSERVATION_SPEC_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-observation-spec/v1"
] = "aero-bench.logistics-observation-spec/v1"
LOGISTICS_OBSERVATION_JOURNAL_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-observation-journal/v1"
] = "aero-bench.logistics-observation-journal/v1"
LOGISTICS_OBSERVATION_INGEST_RESULT_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-observation-ingest-result/v1"
] = "aero-bench.logistics-observation-ingest-result/v1"


class ObservationIngressError(ValueError):
    """A strict logistics observation ingress contract violation."""


# ---------------------------------------------------------------- typed spec


class LogisticsObservationSpecItem(StrictModel):
    """One explicitly declared (authored aircraft, facility, pad) assessment."""

    aircraft_id: LogisticsIdentifier
    facility_id: FacilityIdentifier
    pad_index: int

    @field_validator("pad_index", mode="before")
    @classmethod
    def pad_index_strict_nonnegative(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("pad_index must be a nonnegative integer")
        return value


class LogisticsObservationSpec(StrictModel):
    """The explicit observation plan.  Nothing is inferred.

    Every (aircraft, facility, pad) triple must be declared exactly once; an
    aircraft may be assessed against several declared pads, but never the same
    pad twice.  The plan is immutable and deterministic once declared.
    """

    schema_version: Literal["aero-bench.logistics-observation-spec/v1"]
    items: tuple[LogisticsObservationSpecItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def observation_plan_is_unique(self) -> "LogisticsObservationSpec":
        triples = [
            (item.aircraft_id, item.facility_id, item.pad_index)
            for item in self.items
        ]
        if len(triples) != len(set(triples)):
            raise ValueError(
                "logistics observation spec must declare each "
                "(aircraft, facility, pad) triple exactly once"
            )
        return self

    @property
    def declared_aircraft_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.aircraft_id for item in self.items}))

    @property
    def observation_pairs(self) -> tuple[tuple[str, str, int], ...]:
        return tuple(
            (item.aircraft_id, item.facility_id, item.pad_index)
            for item in self.items
        )


# ---------------------------------------------------------------- typed batch


class LogisticsObservationBatch(StrictModel):
    """One typed observable moment: the closed motion stage at exactly ``at``.

    Every observation in the batch is derived from the *same* sealed
    ``SceneState`` / motion stage-barrier at the *same* authoritative run,
    scenario and simulation time.  The barrier digest is the caller-declared
    source barrier that must agree with every record.
    """

    schema_version: Literal["aero-bench.logistics-observation-batch/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    at: SimulationTime
    source_scene_state_digest: Sha256
    source_stage_barrier_digest: Sha256
    observations: tuple[FacilityPadPhysicalObservation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def canonical_observation_batch(self) -> "LogisticsObservationBatch":
        if self.at.tick < 1:
            raise ValueError("observation batch tick must start at 1")
        for observation in self.observations:
            if observation.run_id != self.run_id:
                raise ValueError(
                    "observation batch record run_id differs from the batch run"
                )
            if observation.scenario_digest != self.scenario_digest:
                raise ValueError(
                    "observation batch record scenario digest differs from the batch"
                )
            if observation.at != self.at:
                raise ValueError(
                    "observation batch record time differs from the batch time"
                )
            if observation.source_scene_state_digest != self.source_scene_state_digest:
                raise ValueError(
                    "observation batch record SceneState digest differs from the batch"
                )
            if (
                observation.source_stage_barrier_digest
                != self.source_stage_barrier_digest
            ):
                raise ValueError(
                    "observation batch record stage-barrier digest differs from "
                    "the batch"
                )
        digests = [
            observation.observation_digest for observation in self.observations
        ]
        if len(digests) != len(set(digests)):
            raise ValueError(
                "observation batch must not contain duplicate observation digests"
            )
        return self

    @property
    def observation_digests(self) -> tuple[Sha256, ...]:
        return tuple(
            observation.observation_digest for observation in self.observations
        )


# ------------------------------------------------- immutable observation journal


class ObservationJournalRecord(StrictModel):
    """One immutable, sequence-bound journal record.

    ``sequence`` is the strict 1-based position in the journal.  The full
    observation record (with its source/barrier/evidence refs and digests) is
    persisted verbatim; ``received_at`` must equal the observation's own
    authoritative time.
    """

    sequence: Annotated[int, Field(ge=1)]
    observation: FacilityPadPhysicalObservation
    received_at: SimulationTime

    @model_validator(mode="after")
    def record_is_temporally_consistent(self) -> "ObservationJournalRecord":
        if self.observation.at != self.received_at:
            raise ValueError(
                "journal record received_at differs from the observation time"
            )
        return self

    @property
    def observation_digest(self) -> Sha256:
        return self.observation.observation_digest


class ObservationJournal(StrictModel):
    """Immutable append-only observation journal for one run.

    Records are strictly ordered by 1-based sequence, every observation digest
    is unique, and every record is bound to the journal run.  The journal is
    the exact object the business provider persists inside its declared
    artifact; a journal parsed from an artifact must reconstruct the identical
    run.
    """

    schema_version: Literal["aero-bench.logistics-observation-journal/v1"]
    run_id: Sha256
    provider_id: Identifier
    records: tuple[ObservationJournalRecord, ...] = ()

    @model_validator(mode="after")
    def canonical_journal(self) -> "ObservationJournal":
        if self.records:
            sequences = [record.sequence for record in self.records]
            if sequences != list(range(1, len(self.records) + 1)):
                raise ValueError("observation journal sequences must be 1..n in order")
            for record in self.records:
                if record.observation.run_id != self.run_id:
                    raise ValueError(
                        "observation journal record belongs to another run"
                    )
        digests = [record.observation_digest for record in self.records]
        if len(digests) != len(set(digests)):
            raise ValueError("observation journal digests must be unique")
        return self

    @property
    def last_sequence(self) -> int:
        return self.records[-1].sequence if self.records else 0

    @property
    def observation_digests(self) -> tuple[Sha256, ...]:
        return tuple(record.observation_digest for record in self.records)

    @property
    def barrier_digests(self) -> tuple[Sha256, ...]:
        return tuple(
            record.observation.source_stage_barrier_digest for record in self.records
        )

    def canonical_digest(self) -> str:
        """Deterministic SHA-256 over the complete journal content."""
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


class ObservationIngestResult(StrictModel):
    """Typed acknowledgment returned over the private RPC operation.

    ``replayed`` is ``True`` exactly when the submitted barrier batch was
    already fully present and the journal was returned unchanged.  When
    ``replayed`` is ``False``, ``accepted_records`` are the newly appended
    journal records and their sequence numbers are the tail of the journal.
    """

    schema_version: Literal["aero-bench.logistics-observation-ingest-result/v1"]
    run_id: Sha256
    provider_id: Identifier
    at: SimulationTime
    journal_digest: Sha256
    sequence_count: int
    accepted_records: tuple[ObservationJournalRecord, ...] = ()
    replayed: bool

    @model_validator(mode="after")
    def canonical_ingest_result(self) -> "ObservationIngestResult":
        if self.sequence_count < 0:
            raise ValueError("sequence_count must be nonnegative")
        if self.replayed:
            if self.accepted_records:
                raise ValueError("a replay must not carry accepted records")
        elif not self.accepted_records:
            raise ValueError("a non-replay ingest must accept at least one record")
        sequences = [record.sequence for record in self.accepted_records]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError(
                "accepted records must be a strictly increasing sequence list"
            )
        if self.accepted_records and self.sequence_count < sequences[-1]:
            raise ValueError("sequence_count is smaller than the last accepted sequence")
        for record in self.accepted_records:
            if record.observation.run_id != self.run_id:
                raise ValueError("accepted observation record belongs to another run")
            if record.received_at != self.at:
                raise ValueError("accepted observation record time differs from result time")
        return self


@dataclass(frozen=True, slots=True)
class ObservationAppendOutcome:
    """Atomic result of :func:`append_observations_batch` (no mutation)."""

    journal: ObservationJournal
    accepted_records: tuple[ObservationJournalRecord, ...]
    replayed: bool


def empty_observation_journal(*, run_id: Sha256, provider_id: str) -> ObservationJournal:
    """Return the empty journal bound to a run/provider.  Used after reset."""
    return ObservationJournal(
        schema_version=LOGISTICS_OBSERVATION_JOURNAL_SCHEMA_VERSION,
        run_id=run_id,
        provider_id=provider_id,
        records=(),
    )


def append_observations_batch(
    *,
    journal: ObservationJournal | None,
    batch: LogisticsObservationBatch,
    provider_id: str,
) -> ObservationAppendOutcome:
    """Atomically append one typed batch into the journal.

    The supplied ``journal`` is never mutated.  A full re-send of an already
    ingested barrier batch returns ``replayed=True`` with the unchanged journal
    and no accepted records.  A batch that diverges from the recorded journal —
    a missing/extra observation for an already-seen barrier, a conflicting
    observation digest, a digest already recorded under a *different* barrier,
    a foreign run, a different provider identity or a stale time — raises
    :class:`ObservationIngressError` and leaves the journal untouched.
    """
    if not isinstance(batch, LogisticsObservationBatch):
        raise TypeError("observation ingest requires a LogisticsObservationBatch")
    if not isinstance(provider_id, str) or not provider_id:
        raise ObservationIngressError("observation ingest provider_id is required")
    if journal is None:
        current = empty_observation_journal(
            run_id=batch.run_id,
            provider_id=provider_id,
        )
    else:
        if not isinstance(journal, ObservationJournal):
            raise TypeError("observation journal is invalid")
        current = journal
    if current.run_id != batch.run_id:
        raise ObservationIngressError(
            f"observation batch run {batch.run_id} differs from the journal run "
            f"{current.run_id}; the payload belongs to a foreign run and is rejected"
        )
    if current.provider_id != provider_id:
        raise ObservationIngressError(
            "observation ingest provider identity differs from the journal provider"
        )
    if current.records:
        trapped = current.records[-1].observation.at
        if (batch.at.tick, batch.at.sim_time_ns) < (
            trapped.tick,
            trapped.sim_time_ns,
        ):
            raise ObservationIngressError(
                f"observation batch at {batch.at.tick}:{batch.at.sim_time_ns} is "
                f"stale; the journal already observes {trapped.tick}:{trapped.sim_time_ns}"
            )

    # Group by barrier digest (the batch declares exactly one moment, so this
    # is one group in practice; the grouping keeps the invariant explicit).
    by_digest: dict[Sha256, ObservationJournalRecord] = {
        record.observation_digest: record for record in current.records
    }
    by_barrier: dict[Sha256, dict[Sha256, ObservationJournalRecord]] = {}
    for record in current.records:
        barrier = record.observation.source_stage_barrier_digest
        by_barrier.setdefault(barrier, {})[record.observation_digest] = record

    barrier_groups: dict[Sha256, list[FacilityPadPhysicalObservation]] = {}
    for observation in batch.observations:
        barrier_groups.setdefault(
            observation.source_stage_barrier_digest, []
        ).append(observation)

    freshly_accepted: list[FacilityPadPhysicalObservation] = []
    all_replayed = True
    for barrier, observations in barrier_groups.items():
        batch_digests = {
            observation.observation_digest for observation in observations
        }
        if len(batch_digests) != len(observations):
            raise ObservationIngressError(
                f"observation batch repeats a digest for barrier {barrier}"
            )
        recorded = by_barrier.get(barrier)
        if recorded is not None:
            if set(recorded) != batch_digests:
                raise ObservationIngressError(
                    f"barrier {barrier} was already ingested; the resubmitted "
                    "batch diverges (missing or extra observations) and is "
                    "rejected as a conflict"
                )
            for observation in observations:
                prior = recorded[observation.observation_digest]
                if prior.observation != observation:
                    raise ObservationIngressError(
                        "observation digest conflicts with an already ingested "
                        "record of different content"
                    )
            # Full identical re-send of a recorded barrier: idempotent replay.
            continue
        for observation in observations:
            prior = by_digest.get(observation.observation_digest)
            if prior is not None:
                raise ObservationIngressError(
                    "observation digest is already recorded under a different "
                    "barrier; the payload is rejected as a conflict"
                )
        all_replayed = False
        freshly_accepted.extend(observations)

    if all_replayed:
        return ObservationAppendOutcome(
            journal=current,
            accepted_records=(),
            replayed=True,
        )

    ordered = tuple(sorted(freshly_accepted, key=lambda obs: obs.observation_digest))
    new_records = tuple(
        ObservationJournalRecord(
            sequence=current.last_sequence + index,
            observation=observation,
            received_at=batch.at,
        )
        for index, observation in enumerate(ordered, start=1)
    )
    updated = ObservationJournal(
        schema_version=LOGISTICS_OBSERVATION_JOURNAL_SCHEMA_VERSION,
        run_id=current.run_id,
        provider_id=current.provider_id,
        records=(*current.records, *new_records),
    )
    return ObservationAppendOutcome(
        journal=updated,
        accepted_records=new_records,
        replayed=False,
    )


def observation_ingest_result(
    *,
    outcome: ObservationAppendOutcome,
    provider_id: str,
) -> ObservationIngestResult:
    if outcome.accepted_records:
        observed_at = outcome.accepted_records[0].received_at
    elif outcome.journal.records:
        observed_at = outcome.journal.records[-1].observation.at
    else:  # pragma: no cover - a successful outcome always has a non-empty side
        raise ObservationIngressError(
            "observation ingest outcome has no observation time"
        )
    return ObservationIngestResult(
        schema_version=LOGISTICS_OBSERVATION_INGEST_RESULT_SCHEMA_VERSION,
        run_id=outcome.journal.run_id,
        provider_id=provider_id,
        at=observed_at,
        journal_digest=outcome.journal.canonical_digest(),
        sequence_count=len(outcome.journal.records),
        accepted_records=outcome.accepted_records,
        replayed=outcome.replayed,
    )


# ------------------------------------------------- derivation (closed stage)


def derive_physical_observations(
    *,
    scene_state: SceneState,
    events: tuple[ProviderEvent, ...],
    package: LogisticsTaskPackage,
    bindings: LogisticsRuntimeBindings,
    scenario: ResolvedScenario,
    spec: LogisticsObservationSpec,
    pose_references: tuple[DeclaredAircraftPoseReference, ...],
    tolerances: PresenceTolerances,
    stage_barriers: tuple[StageBarrier, ...],
    expected_run_id: Sha256,
    target: SimulationTime,
) -> tuple[FacilityPadPhysicalObservation, ...]:
    """Derive the typed observation batch for one closed motion stage.

    This is the *accepted adapter* between the runtime hook inputs and the
    immutable :class:`FacilityPadPhysicalObservation` records.  The caller must
    supply explicit pose-reference calibrations for every aircraft named by the
    observation spec; a missing calibration is an exact error and half body
    height is never inferred.  ``expected_run_id`` / ``target`` are the required
    external caller context (the hook's run and target) and are never inferred
    from the incoming batch itself.
    """
    if not isinstance(package, LogisticsTaskPackage):
        raise TypeError("physical observation derivation requires a LogisticsTaskPackage")
    if not isinstance(bindings, LogisticsRuntimeBindings):
        raise TypeError("physical observation derivation requires LogisticsRuntimeBindings")
    if not isinstance(scenario, ResolvedScenario):
        raise TypeError("physical observation derivation requires a ResolvedScenario")
    if not isinstance(spec, LogisticsObservationSpec):
        raise TypeError("physical observation derivation requires a LogisticsObservationSpec")
    if not isinstance(tolerances, PresenceTolerances):
        raise TypeError("physical observation derivation requires PresenceTolerances")
    if not stage_barriers:
        raise ObservationIngressError(
            "physical observation derivation requires the closed stage barriers"
        )

    calibration_by_aircraft = {
        reference.aircraft_id: reference for reference in pose_references
    }
    if len(calibration_by_aircraft) != len(pose_references):
        raise ObservationIngressError(
            "pose-reference calibrations must be unique per aircraft"
        )
    spec_aircraft = set(spec.declared_aircraft_ids)
    if set(calibration_by_aircraft) != spec_aircraft:
        raise ObservationIngressError(
            "pose-reference calibrations must exactly cover every aircraft in "
            f"the observation spec; missing={sorted(spec_aircraft - set(calibration_by_aircraft)) or 'none'}, "
            f"extra={sorted(set(calibration_by_aircraft) - spec_aircraft) or 'none'}"
        )

    observations: list[FacilityPadPhysicalObservation] = []
    for item in spec.items:
        calibration = calibration_by_aircraft[item.aircraft_id]
        try:
            observed = observe_facility_pad_presence(
                scene_state=scene_state,
                events=events,
                package=package,
                bindings=bindings,
                scenario=scenario,
                aircraft_id=item.aircraft_id,
                facility_id=item.facility_id,
                pad_index=item.pad_index,
                pose_reference=calibration,
                tolerances=tolerances,
                stage_barriers=stage_barriers,
                expected_run_id=expected_run_id,
                target=target,
            )
        except PhysicalObservationError as error:
            raise ObservationIngressError(
                f"physical observation derivation failed for aircraft "
                f"{item.aircraft_id!r} facility {item.facility_id!r} pad "
                f"{item.pad_index}: {error}"
            ) from error
        observations.append(observed)

    return tuple(
        sorted(
            observations,
            key=lambda obs: (obs.aircraft_id, obs.facility_id, obs.pad_index),
        )
    )


def build_observation_batch(
    *,
    observations: tuple[FacilityPadPhysicalObservation, ...],
    run_id: Sha256,
    scenario_digest: Sha256,
    at: SimulationTime,
    source_scene_state_digest: Sha256,
    source_stage_barrier_digest: Sha256,
) -> LogisticsObservationBatch:
    """Typed immutable batch over a deterministic observation tuple."""
    if not observations:
        raise ObservationIngressError("an observation batch must be non-empty")
    return LogisticsObservationBatch(
        schema_version=LOGISTICS_OBSERVATION_BATCH_SCHEMA_VERSION,
        run_id=run_id,
        scenario_digest=scenario_digest,
        at=at,
        source_scene_state_digest=source_scene_state_digest,
        source_stage_barrier_digest=source_stage_barrier_digest,
        observations=observations,
    )


def validate_observation_against_package(
    observation: FacilityPadPhysicalObservation,
    *,
    package: LogisticsTaskPackage,
) -> None:
    """Bind one observation to the declared canonical facility/pad/aircraft.

    The facility must be a canonical package facility, the pad must be the exact
    declared pad geometry (the adapter materialized it from the same package),
    and the authored aircraft must be an expanded fleet unit.  This is the
    declared-facility-pad binding a foreign or mismatched-config payload cannot
    pass.
    """
    facility = package.facilities.require(observation.facility_id)
    pads = facility_landing_pads(facility)
    candidates = [pad for pad in pads if pad.pad_index == observation.pad_index]
    if len(candidates) != 1:
        raise ObservationIngressError(
            f"observation names pad index {observation.pad_index} that is not a "
            f"declared canonical pad of facility {observation.facility_id!r}"
        )
    if candidates[0] != observation.pad:
        raise ObservationIngressError(
            "observation pad geometry differs from the declared facility pad "
            "(mismatched config payload)"
        )
    if observation.pad.facility_id != observation.facility_id:
        raise ObservationIngressError(
            "observation pad facility identity differs from the record"
        )
    if not any(
        unit.aircraft_id == observation.aircraft_id for unit in package.aircraft_units()
    ):
        raise ObservationIngressError(
            f"observation names authored aircraft {observation.aircraft_id!r} that "
            "is not a declared fleet unit"
        )


def validate_batch_against_package(
    batch: LogisticsObservationBatch,
    *,
    package: LogisticsTaskPackage,
) -> None:
    """Validate every observation in a batch against the canonical package."""
    for observation in batch.observations:
        validate_observation_against_package(observation, package=package)


class LogisticsObservationIngestClient(Protocol):
    """The concrete ProviderSession surface the runtime hook may use.

    Only the real Logistics Business provider client implements this; there is
    no agent-facing tool and no generic arbitrary evidence ingestion.
    """

    async def ingest_observations(
        self, batch: LogisticsObservationBatch
    ) -> ObservationIngestResult: ...


__all__ = [
    "LOGISTICS_OBSERVATION_BATCH_SCHEMA_VERSION",
    "LOGISTICS_OBSERVATION_INGEST_OPERATION",
    "LOGISTICS_OBSERVATION_INGEST_RESULT_SCHEMA_VERSION",
    "LOGISTICS_OBSERVATION_JOURNAL_SCHEMA_VERSION",
    "LOGISTICS_OBSERVATION_SPEC_SCHEMA_VERSION",
    "LogisticsObservationBatch",
    "LogisticsObservationIngestClient",
    "LogisticsObservationSpec",
    "LogisticsObservationSpecItem",
    "ObservationAppendOutcome",
    "ObservationIngestError",
    "ObservationIngestResult",
    "ObservationJournal",
    "ObservationJournalRecord",
    "append_observations_batch",
    "build_observation_batch",
    "derive_physical_observations",
    "empty_observation_journal",
    "observation_ingest_result",
    "validate_batch_against_package",
    "validate_observation_against_package",
]
