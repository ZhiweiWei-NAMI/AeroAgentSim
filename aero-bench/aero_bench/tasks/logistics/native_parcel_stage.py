"""Closed-stage observation batch consumer for the native parcel slice.

This module is the *stage* integration surface between the journaled
observation ingress and the accepted native parcel admission state machine.
It consumes one :class:`~aero_bench.tasks.logistics.observation_ingress.LogisticsObservationBatch`
— the exact typed observable moment the runtime hook derives per closed motion
stage — and feeds its records to
:class:`~aero_bench.tasks.logistics.native_parcel_runtime.NativeParcelStateMachine`
through :meth:`~aero_bench.tasks.logistics.native_parcel_runtime.NativeParcelStateMachine.observe`.

Strict batch semantics
----------------------

* **Whole-batch validation before any mutation.** The batch must bind the
  machine's exact run, one declared scenario digest shared by every record,
  one exact authoritative :class:`~aero_bench.runtime.contracts.SimulationTime`
  (tick **and** sim_time_ns equal across every record), one declared stage
  barrier shared by every record, records from exactly the two declared
  facility/pad bindings of the declared carrier, and one and the same
  physical carrier observation shared across every pad record (see below).
  Duplicates, missing required pads, foreign runs and foreign identities raise
  :class:`NativeParcelRuntimeError` before the machine is touched.
* **One physical carrier observation per batch.** Both declared pad records
  bind the SAME measured sample, source event/evidence identity, measured
  attitude, frame origin and native carrier identity/profile; only the
  facility/pad geometry, event binding frame, presence assessment and record
  digest are the expected per-pad divergence.  A batch that presents one
  physical position as two is rejected before any mutation.
* **One batch per tick.** The consumer tombstones each accepted batch by its
  ``(run_id, at)`` identity: a repeated batch at the same authoritative time
  is explicitly rejected (the observation ingest journal owns RPC-level
  repeat dedup; this surface never re-delivers a consumed batch), and so is
  any other batch at an already-consumed time.
* **One seek check per batch.** :meth:`NativeParcelStateMachine.check_seek_forward`
  runs exactly once on the batch's first record — the global replay frontier
  is one frontier, not a per-pad one — so the two declared pads may appear at
  the same authoritative tick in either order and both are accepted.  The
  batch must start at exactly the next closed stage; a skipped or stale stage
  raises :class:`SeekConsistencyError`.
* **Atomic error rollback.** The machine is mutated only after the whole
  batch has validated.  If a record nevertheless fails while being observed,
  the pre-batch :meth:`NativeParcelStateMachine.snapshot` is restored, the
  failed batch is not tombstoned, and the original error propagates: a
  rejected batch never leaves partial state.
* **No fabricated evidence.** Every record is the already-journaled
  kernel-assessed closed-stage observation; this consumer never mints a
  presence fact, never derives a wall-clock time and never presents one
  physical position as two.  The batch binds BOTH declared pad records to the
  SAME physical carrier observation — the identical
  :class:`~aero_bench.tasks.logistics.facility_presence.MeasuredAircraftSample`,
  source event/evidence identity
  (``source_event_id`` / ``source_state_sample_digest`` / evidence path and
  hash), measured roll/pitch/yaw, ``source_frame_origin`` and native carrier
  identity/profile — with only the per-pad facility/pad geometry, event
  binding frame, presence assessment and observation digest expected to
  differ.  A second declared pad record is a re-assessment of the one shared
  sample, never an independent second position.
"""

from __future__ import annotations

from typing import Literal

from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.logistics.native_parcel_runtime import (
    NativeParcelRuntimeError,
    NativeParcelStateMachine,
    _tick_key,
)
from aero_bench.tasks.logistics.observation_ingress import (
    LogisticsObservationBatch,
)

NATIVE_PARCEL_STAGE_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-native-parcel-stage/v1"
] = "aero-bench.logistics-native-parcel-stage/v1"

#: The exact record fields that must be IDENTICAL across every pad record of
#: one closed-stage batch: one physical carrier observation, one source event,
#: one evidence identity, one measured attitude, one frame origin and one
#: native carrier identity/profile.  Everything else (facility, pad, pad
#: geometry, per-pad event-binding frame, presence assessment, record digest)
#: is the expected per-pad divergence.
SHARED_CARRIER_SOURCE_FIELDS: tuple[str, ...] = (
    "source_state_sample_digest",
    "source_event_id",
    "source_evidence_path",
    "source_evidence_sha256",
    "source_frame_origin",
    "sample",
    "measured_roll_deg",
    "measured_pitch_deg",
    "measured_yaw_deg",
    "provider_id",
    "native_vehicle_id",
    "aircraft_id",
    "fleet_entry_id",
    "visual_asset_id",
    "profile",
    "event_binding",
)


def _validate_shared_carrier_source(batch: LogisticsObservationBatch) -> None:
    """Bind every pad record to the SAME physical carrier observation.

    All shared source fields must be identical across every record of the
    batch; the expected per-pad divergence (facility, pad, pad geometry,
    assessment, observation digest) is exactly everything else.  A batch that
    presents one physical position as two — or a re-stamped sample — is
    foreign evidence, rejected before any mutation.
    """
    if len(batch.observations) < 2:
        return
    reference = batch.observations[0]
    for observation in batch.observations[1:]:
        for field in SHARED_CARRIER_SOURCE_FIELDS:
            if getattr(observation, field) != getattr(reference, field):
                raise NativeParcelRuntimeError(
                    f"observation {observation.observation_digest} diverges "
                    f"from {reference.observation_digest} in shared carrier "
                    f"source field {field!r}; one closed-stage batch binds "
                    "every declared pad record to the SAME physical carrier "
                    "observation — a second pad is a re-assessment of one "
                    "sample, never a second position"
                )


def _validate_batch_for_machine(
    machine: NativeParcelStateMachine,
    batch: LogisticsObservationBatch,
) -> None:
    """Validate the whole batch against the declared machine; raise first.

    No machine state is touched here: every foreign/duplicate/missing
    condition is an explicit error raised before the first observation is
    absorbed.
    """
    if not isinstance(batch, LogisticsObservationBatch):
        raise NativeParcelRuntimeError(
            "parcel stage consumption requires a typed "
            "LogisticsObservationBatch"
        )
    contract = machine._contract
    ids = contract.identities
    if batch.run_id != machine._run_id:
        raise NativeParcelRuntimeError(
            f"observation batch belongs to run {batch.run_id!r}; the parcel "
            f"stage consumes only run {machine._run_id!r} — foreign evidence "
            "is rejected, not ignored"
        )
    if not batch.observations:
        raise NativeParcelRuntimeError(
            "observation batch carries no records; an empty stage batch is "
            "never consumed"
        )
    if len(
        {observation.observation_digest for observation in batch.observations}
    ) != len(batch.observations):
        raise NativeParcelRuntimeError(
            "observation batch repeats one observation digest; a repeated "
            "record is an explicit error, never a second absorb"
        )
    declared_pads = {
        (ids.pickup_facility_id, machine._pickup_pad_index),
        (ids.dropoff_facility_id, machine._dropoff_pad_index),
    }
    seen_pads: set[tuple[str, int]] = set()
    for observation in batch.observations:
        # One declared scenario digest per batch: every record re-binds it
        # exactly (the typed batch model enforces the same equality), so a
        # mixed-scenario batch is foreign evidence, never absorbed.
        if observation.run_id != batch.run_id:
            raise NativeParcelRuntimeError(
                f"observation {observation.observation_digest} belongs to run "
                f"{observation.run_id!r}, not the batch run "
                f"{batch.run_id!r} — foreign evidence is rejected"
            )
        if observation.scenario_digest != batch.scenario_digest:
            raise NativeParcelRuntimeError(
                f"observation {observation.observation_digest} names scenario "
                f"{observation.scenario_digest!r}; the batch declares "
                f"{batch.scenario_digest!r} — foreign evidence is rejected"
            )
        if _tick_key(observation.at) != _tick_key(batch.at):
            raise NativeParcelRuntimeError(
                f"observation {observation.observation_digest} is stamped at "
                f"tick {observation.at.tick} ({observation.at.sim_time_ns} ns); "
                f"the batch declares tick {batch.at.tick} "
                f"({batch.at.sim_time_ns} ns) — one closed stage per batch"
            )
        if (
            observation.source_stage_barrier_digest
            != batch.source_stage_barrier_digest
        ):
            raise NativeParcelRuntimeError(
                f"observation {observation.observation_digest} binds stage "
                "barrier "
                f"{observation.source_stage_barrier_digest!r}; the batch "
                f"declares {batch.source_stage_barrier_digest!r} — one "
                "authoritative barrier per closed stage"
            )
        if observation.aircraft_id != ids.carrier_entity_id:
            raise NativeParcelRuntimeError(
                f"observation {observation.observation_digest} names aircraft "
                f"{observation.aircraft_id!r}; the slice binds only "
                f"{ids.carrier_entity_id!r} — foreign evidence is rejected"
            )
        pad_binding = (observation.facility_id, observation.pad_index)
        if pad_binding not in declared_pads:
            raise NativeParcelRuntimeError(
                f"observation {observation.observation_digest} names facility "
                f"{observation.facility_id!r} pad {observation.pad_index}; the "
                f"slice observes only {sorted(declared_pads)} — foreign "
                "evidence is rejected"
            )
        if pad_binding in seen_pads:
            raise NativeParcelRuntimeError(
                f"observation batch repeats facility "
                f"{observation.facility_id!r} pad {observation.pad_index}; "
                "one record per declared pad per closed stage"
            )
        seen_pads.add(pad_binding)
    if seen_pads != declared_pads:
        raise NativeParcelRuntimeError(
            f"observation batch covers only {sorted(seen_pads)} of the "
            f"declared pads {sorted(declared_pads)}; every declared pad of "
            "the closed stage must be present exactly once before the batch "
            "is consumed — a missing declared pad is an explicit error, "
            "never a partially consumed stage"
        )
    _validate_shared_carrier_source(batch)


class NativeParcelStageSnapshot:
    """The exact persisted stage-consumer state.

    The machine's own state is persisted through
    :meth:`NativeParcelStateMachine.snapshot`; this record adds only the
    tombstones of consumed batches, keyed by run and exact authoritative
    time.
    """

    def __init__(
        self,
        *,
        schema_version: str,
        consumed_batch_keys: tuple[tuple[str, dict], ...],
    ):
        self.schema_version = schema_version
        self.consumed_batch_keys = consumed_batch_keys

    def __eq__(self, other: object) -> bool:
        return isinstance(other, NativeParcelStageSnapshot) and (
            self.schema_version == other.schema_version
            and self.consumed_batch_keys == other.consumed_batch_keys
        )


class NativeParcelStageConsumer:
    """Consumes exactly one closed-stage batch per authoritative tick.

    The consumer binds one declared machine.  Repeated or skipped batches are
    explicitly rejected without partial state; a batch that fails mid-way is
    rolled back to the pre-batch snapshot through the machine's own
    snapshot/restore contract.
    """

    def __init__(self, machine: NativeParcelStateMachine):
        if not isinstance(machine, NativeParcelStateMachine):
            raise NativeParcelRuntimeError(
                "parcel stage consumer requires the declared "
                "NativeParcelStateMachine"
            )
        self._machine = machine
        # Tombstones of consumed batches by (run_id, authoritative time).
        self._consumed: set[tuple[str, SimulationTime]] = set()

    @property
    def machine(self) -> NativeParcelStateMachine:
        return self._machine

    def consume_batch(self, batch: LogisticsObservationBatch) -> bool:
        """Consume one closed-stage batch; return True when absorbed.

        The whole batch is validated first (run, scenario, exact time,
        barrier, carrier, both declared pads, duplicates, and the ONE shared
        physical carrier observation binding every pad record).  The seek
        check runs exactly once on the first record — the global replay
        frontier is one frontier, so both declared pads may share the tick in
        either order — and every accepted record is absorbed.  On any failure
        the pre-batch machine snapshot is restored and the batch is not
        tombstoned: a rejected batch never leaves partial state.
        """
        _validate_batch_for_machine(self._machine, batch)
        identity = (batch.run_id, batch.at)
        if identity in self._consumed:
            raise NativeParcelRuntimeError(
                f"observation batch at tick {batch.at.tick} "
                f"({batch.at.sim_time_ns} ns) of run {batch.run_id!r} was "
                "already consumed; a repeated stage batch is explicitly "
                "rejected (the ingest journal owns RPC repeat dedup)"
            )
        pre_batch = self._machine.snapshot()
        try:
            for index, observation in enumerate(batch.observations):
                if index == 0:
                    # The global replay frontier is ONE frontier: the seek
                    # check runs exactly once on the batch's first record,
                    # never per pad, so both declared pads at the same
                    # authoritative tick — in either order — are accepted.
                    self._machine.check_seek_forward(observation)
                self._machine.observe(observation)
        except Exception:
            self._machine.restore(pre_batch)
            raise
        self._consumed.add(identity)
        return True

    def restore(self, snapshot: NativeParcelStageSnapshot) -> None:
        """Restore the exact consumed-batch tombstones of one snapshot.

        Only a snapshot of this schema version is accepted; the machine's own
        state is restored separately through
        :meth:`NativeParcelStateMachine.restore`.
        """
        if snapshot.schema_version != NATIVE_PARCEL_STAGE_SCHEMA_VERSION:
            raise NativeParcelRuntimeError(
                f"stage snapshot schema {snapshot.schema_version!r} is not "
                f"the declared {NATIVE_PARCEL_STAGE_SCHEMA_VERSION!r}; "
                "exact-state restoration is impossible"
            )
        restored: set[tuple[str, SimulationTime]] = set()
        for run_id, at_json in snapshot.consumed_batch_keys:
            identity = (run_id, SimulationTime.model_validate(at_json))
            if identity in restored:
                raise NativeParcelRuntimeError(
                    f"stage snapshot names batch identity {identity!r} twice; "
                    "missing or duplicated tombstone state is corruption, "
                    "never a re-consumed batch"
                )
            restored.add(identity)
        self._consumed = restored

    def snapshot(self) -> NativeParcelStageSnapshot:
        """The exact consumer state (consumed-batch tombstones)."""
        return NativeParcelStageSnapshot(
            schema_version=NATIVE_PARCEL_STAGE_SCHEMA_VERSION,
            consumed_batch_keys=tuple(
                (run_id, at.model_dump(mode="json"))
                for run_id, at in sorted(
                    self._consumed,
                    key=lambda item: (item[0], item[1].tick, item[1].sim_time_ns),
                )
            ),
        )
