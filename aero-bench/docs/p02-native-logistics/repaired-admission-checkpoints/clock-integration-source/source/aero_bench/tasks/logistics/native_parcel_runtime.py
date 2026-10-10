"""Native parcel runtime admission state machine (pure runtime slice).

This module is the *smallest executable* one-order parcel admission state
machine for the user-authorized native slice.  It consumes the accepted
closed-stage PX4 evidence surface (already-journaled
:class:`~aero_bench.tasks.logistics.physical_observations.FacilityPadPhysicalObservation`
records — the exact records the real
:class:`~aero_bench.tasks.logistics.runtime_hook.LogisticsRuntimeHook` derives
through :func:`~aero_bench.tasks.logistics.observation_ingress.derive_physical_observations`
and journals via the private ``logistics.observation.ingest`` RPC) and drives
one parcel through ``awaiting_pickup -> loaded -> in_transit -> delivered``.

Strict semantics
----------------

* **Motion evidence is never reimplemented.**  Every sample is validated by
  re-running the accepted pure kernel
  :func:`~aero_bench.tasks.logistics.facility_presence.assess_facility_presence`
  over the record's own pad/sample/event binding with the declared canonical
  body profile and the authored policy tolerances (grounded/not-in-air, true 3D
  speed gate, rotated body footprint clearance, contact height).
* **Contiguous real closed dwell.**  The window is a strictly contiguous run of
  one-per-closed-stage samples (next tick, strictly later sim time); a gap, a
  reordered sample or a foreign time resets the window and is never padded.
  The dwell span is ``(end.sim_time_ns - start.sim_time_ns)`` over the exact
  :class:`~aero_bench.runtime.contracts.SimulationTime` records; no wall clock.
* **Action/RX proof is a typed exact input.**  Only a declared
  :class:`~aero_bench.tasks.logistics.native_parcel_contract.AdmittedParcelAction`
  (``closed_stage_action_rx`` basis, exact identity payload, RX barrier digest
  bound to the dwell window's closing stage barrier) admits a transition.  A
  bare boolean, a network receipt alone or an inspection report is never
  accepted.
* **Custody and exactly-once transfers.**  The parcel has exactly one holder;
  pickup records facility -> carrier custody once, dropoff records carrier ->
  facility custody once.  A duplicate *admitted* action id returns the original
  decision and never produces a second transfer; a different payload under the
  same id raises.  Unconfirmed decisions are retriable (they are not accepted
  decisions).
* **UNKNOWN versus error.**  A missing/gapped/ineligible evidence condition
  returns an explicit unconfirmed outcome; malformed or foreign identity raises
  :class:`NativeParcelRuntimeError`.
* **In-transit is runtime-detected from real evidence.**  The
  ``loaded -> in_transit`` flip happens only when a closed observation of the
  carrier with the parcel in custody reports the explicit measured in-air
  attributes (``in_air`` set / ``landed`` cleared).  It is never an admitted
  custody action, carries no transfer, and is bound to the exact observation
  that evidenced it.  No future binding and no renderer fabrication.
* **Carriage math reuses the existing frame math.**  The declared body-local
  attachment is composed with the measured carrier attitude using
  :class:`~aero_bench.world.frame_math.UnitQuaternion` under the explicit
  convention documented on
  :data:`~aero_bench.tasks.logistics.native_parcel_contract.ATTACHMENT_FRAME_NOTE`
  (scene mapping ``x=east, y=up, z=-north``).
* **Seek-consistent append-only snapshot.**  :class:`NativeParcelSnapshot` is a
  pure function of the absorbed evidence/decision sequence; replaying the same
  sealed stream through a fresh machine reproduces it.  A seek backward
  restores an earlier snapshot; seek-forward replay re-delivers closed stages
  from the restored frontier, observations at or before the frontier are
  ignored, and an observation that skips the frontier raises
  :class:`SeekConsistencyError`.

Scope boundaries (unchanged until integration review): this module never mints
a ``VerifiedDwellProvenance``, never weakens the sealed dwell kernel, never
flips ``LOGISTICS_RUNTIME_IMPLEMENTED``, never registers a runtime hook and
never enables a provider/service capability.  Live admitted state is not
independently verified; the independent sealed logistics verifier must replay
the same command, observation, dwell, attachment and transfer records before
issuing any final verdict.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Literal

from pydantic import model_validator

from aero_bench.config.models import StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.frame_math import (
    RotationMatrix,
    UnitQuaternion,
    Vector3,
    rpy_to_quaternion,
)
from aero_bench.tasks.logistics.facility_presence import (
    AircraftPresenceProfile,
    assess_facility_presence,
)
from aero_bench.tasks.logistics.native_parcel_contract import (
    ATTACHMENT_FRAME_NOTE,
    NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION,
    NATIVE_PARCEL_STATE_SCHEMA_VERSION,
    NATIVE_PARCEL_TRANSFER_SCHEMA_VERSION,
    UNKNOWN_SOURCE_NOTE,
    AdmittedParcelAction,
    DeclaredParcelAttachment,
    DeclaredParcelCarrier,
    NativeParcelContract,
    NativeParcelContractError,
    NativeParcelState,
    ParcelAdmissionOutcome,
    ParcelActionKey,
    ParcelActionKeyRecord,
    ParcelCustodyHolder,
    ParcelCustodyTransferRecord,
    ParcelDwellAdmission,
    ParcelStateTransitionRecord,
)
from aero_bench.tasks.logistics.orders import LogisticsIdentifier
from aero_bench.tasks.logistics.physical_observations import (
    FacilityPadPhysicalObservation,
)

NATIVE_PARCEL_SNAPSHOT_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-native-parcel-snapshot/v2"
] = "aero-bench.logistics-native-parcel-snapshot/v2"

_IN_TRANSIT_ACTION_ID = "logistics.runtime.in_transit"
_MAX_PAST_KEYS = 4096


class NativeParcelRuntimeError(NativeParcelContractError):
    """A strict native parcel runtime violation (malformed/foreign input)."""


class SeekConsistencyError(NativeParcelRuntimeError):
    """An observation stream inconsistent with the seek/replay frontier."""


def _tick_key(at: SimulationTime) -> tuple[int, int]:
    return (at.tick, at.sim_time_ns)


def _clock_relation(
    a: SimulationTime, b: SimulationTime
) -> Literal["earlier", "equal", "later", "inconsistent"]:
    """The exact monotone relation of two SimulationTime records.

    The declared simulation clock is ONE monotone relation: simulation time
    increases with tick (no tick duration is assumed without a declaration).
    ``a`` is ``later`` than ``b`` when its tick is greater AND its simulation
    time is strictly greater, or when the ticks are equal and its sim_time_ns
    is greater; ``equal`` requires both components equal.  A greater tick
    with a non-increasing sim_time_ns — or an earlier tick with a non-earlier
    sim_time_ns — is ``inconsistent``: the two components disagree, which is
    never a valid ordering (a lexicographic tuple comparison would silently
    accept it).
    """
    if a.tick == b.tick:
        if a.sim_time_ns == b.sim_time_ns:
            return "equal"
        return "later" if a.sim_time_ns > b.sim_time_ns else "earlier"
    if a.tick > b.tick:
        return "later" if a.sim_time_ns > b.sim_time_ns else "inconsistent"
    return "earlier" if a.sim_time_ns < b.sim_time_ns else "inconsistent"


def _strict_model(model_cls, value: object, label: str):
    if not isinstance(value, model_cls):
        raise NativeParcelRuntimeError(
            f"native parcel runtime requires a typed {label}"
        )
    return value


def _digest_object(body: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


# ------------------------------------------------------- canonical presence


def canonical_carrier_profile(
    contract: NativeParcelContract,
    *,
    observation: FacilityPadPhysicalObservation,
) -> AircraftPresenceProfile:
    """The declared canonical body profile of the one declared carrier.

    The profile's aircraft identity is the native vehicle id; the footprint
    dimensions come from the observation's own recorded profile (already
    canonically derived from the immutable package performance profile by the
    accepted adapter) and the pose-reference calibration must equal the
    declared carrier calibration.  A substituted profile is caught here: an
    observation whose recorded profile disagrees with the declared carrier
    calibration is foreign evidence, and a missing calibration is an exact
    error, never a half-height default.
    """
    carrier = contract.carrier
    if (
        observation.aircraft_id != contract.identities.carrier_entity_id
        or observation.native_vehicle_id != carrier.native_vehicle_id
        or observation.provider_id != carrier.provider_id
        or observation.fleet_entry_id != carrier.fleet_entry_id
        or observation.visual_asset_id != carrier.visual_asset_id
    ):
        raise NativeParcelRuntimeError(
            f"observation {observation.observation_digest} names aircraft "
            f"{observation.aircraft_id!r} on provider "
            f"{observation.provider_id!r}; the native parcel slice binds only "
            f"carrier {contract.identities.carrier_entity_id!r} (native "
            f"vehicle {carrier.native_vehicle_id!r}) — foreign evidence is "
            "rejected, never reassessed for another aircraft"
        )
    recorded = observation.profile
    if (
        recorded.aircraft_id != carrier.native_vehicle_id
        or recorded.body_width_m <= 0
        or recorded.body_depth_m <= 0
        or recorded.pose_reference_above_contact_m
        != carrier.pose_reference_above_contact_m
    ):
        raise NativeParcelRuntimeError(
            "observation presence profile does not match the declared carrier "
            "binding and pose-reference calibration; a missing or substituted "
            "calibration is an exact error, never a default"
        )
    return AircraftPresenceProfile(
        aircraft_id=carrier.native_vehicle_id,
        body_width_m=recorded.body_width_m,
        body_depth_m=recorded.body_depth_m,
        pose_reference_above_contact_m=carrier.pose_reference_above_contact_m,
    )


# ------------------------------------------------------------- carriage math


@dataclass(frozen=True, slots=True)
class ParcelCarriagePose:
    """One derived carrier-local parcel pose in raw scene metres.

    ``position_*_m`` is the parcel reference point in ``scene_east_south_m``
    ``(x=east, y=up, z=-north)`` metres; ``orientation_*`` is the parcel
    orientation as a canonical unit quaternion under the same scene Y-axis yaw
    convention as the carrier body yaw (right-handed, east-positive).  This is
    a backend derivation from the actual carrier transform; the renderer only
    displays the supplied pose and never fabricates it.
    """

    at: SimulationTime
    position_x_m: float
    position_y_m: float
    position_z_m: float
    orientation_w: float
    orientation_x: float
    orientation_y: float
    orientation_z: float
    frame_note: str = ATTACHMENT_FRAME_NOTE


def parcel_carrier_binding(
    contract: NativeParcelContract,
    observation: FacilityPadPhysicalObservation,
) -> DeclaredParcelCarrier:
    """The declared carrier binding after re-validating one observation.

    Re-runs the full canonical identity/calibration guard and returns the
    exact declared carrier record, so callers can bind the derived carriage
    pose to the declared native vehicle without weakening the check.
    """
    canonical_carrier_profile(contract, observation=observation)
    return contract.carrier


def parcel_carriage_pose(
    *,
    attachment: DeclaredParcelAttachment,
    observation: FacilityPadPhysicalObservation,
) -> ParcelCarriagePose:
    """Derive the carrier-local parcel pose from the actual carrier transform.

    The input is the closed observation record itself: the parcel position is
    the record's measured scene-frame carrier position plus the declared
    body-local offset rotated by the record's measured scene attitude
    (``measured_roll_deg``/``measured_pitch_deg``/``measured_yaw_deg``), and
    the parcel orientation is that attitude quaternion composed with the
    declared body-local orientation quaternion.

    Explicit convention (see :data:`ATTACHMENT_FRAME_NOTE`): the attachment is
    declared in carrier body-local axes (``x`` forward, ``y`` up, ``z`` right,
    right-handed) sharing the yaw convention of
    ``FacilityLandingPad.rotation_deg`` (scene Y-axis rotation, east-positive);
    the scene mapping is ``x=east, y=up, z=-north``.  The recorded attitude
    triple is the shared intrinsic ZYX rpy of the measured ENU quaternion (the
    exact transform the accepted adapter applied when it populated the record),
    so it is reconstructed with
    :func:`aero_bench.world.frame_math.rpy_to_quaternion` in ENU axes and then
    re-expressed in scene axes by conjugating with the ENU->scene axis map
    (a proper rotation, ``x=east, y=up, z=-north``).  A nonzero measured
    roll/pitch tilts the offset exactly as the real attitude dictates; no axis
    is ever flattened and no default attitude exists.
    """
    if not isinstance(attachment, DeclaredParcelAttachment):
        raise NativeParcelRuntimeError(
            "parcel carriage requires the declared body-local attachment"
        )
    _strict_model(
        FacilityPadPhysicalObservation, observation, "FacilityPadPhysicalObservation"
    )
    # The recorded attitude triple is the shared intrinsic ZYX rpy of the ENU
    # quaternion (the exact transform the accepted adapter applied), so it is
    # reconstructed in ENU axes first and then re-expressed in scene axes by
    # conjugating with the ENU->scene axis map (x=east, y=up, z=-north; a
    # proper rotation, det +1).  Applying rpy_to_quaternion directly in scene
    # axes would wrongly rotate yaw about the scene z (south) axis.
    attitude_enu = rpy_to_quaternion(
        roll_rad=math.radians(observation.measured_roll_deg),
        pitch_rad=math.radians(observation.measured_pitch_deg),
        yaw_rad=math.radians(observation.measured_yaw_deg),
    )
    enu_to_scene = RotationMatrix(
        rows=((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    )
    attitude = UnitQuaternion.from_rotation_matrix(
        enu_to_scene.compose(attitude_enu.to_rotation_matrix()).compose(
            enu_to_scene.inverse()
        )
    )
    local = UnitQuaternion(
        w=attachment.orientation.qw,
        x=attachment.orientation.qx,
        y=attachment.orientation.qy,
        z=attachment.orientation.qz,
    )
    parcel_orientation = attitude.compose(local)
    offset_scene = attitude.rotate(
        Vector3(
            attachment.offset_x_m, attachment.offset_y_m, attachment.offset_z_m
        )
    )
    return ParcelCarriagePose(
        at=observation.at,
        position_x_m=observation.sample.x + offset_scene.x,
        position_y_m=observation.sample.y + offset_scene.y,
        position_z_m=observation.sample.z + offset_scene.z,
        orientation_w=parcel_orientation.w,
        orientation_x=parcel_orientation.x,
        orientation_y=parcel_orientation.y,
        orientation_z=parcel_orientation.z,
    )


# -------------------------------------------------------- contiguous window


class ClosedParcelPresenceWindow:
    """Rolling contiguous closed-stage presence window for one declared pad.

    One sample per closed stage, strictly contiguous in ticks; the window
    resets to the single newest sample whenever the carrier is observed at a
    non-contiguous tick or anywhere outside the declared pad (a presence dwell
    is a *contiguous grounded run*, not a facility badge, and a gap is never
    padded).  Samples are never re-sorted.  An invalid, moving, airborne or
    contact-lost stage never joins the run: the presence kernel marks it
    ineligible, so it clears the qualifying suffix and the grounded run
    restarts at the next eligible stage — a later valid suffix is never
    poisoned by an earlier bad stage and never padded across it.
    """

    def __init__(
        self,
        *,
        contract: NativeParcelContract,
        run_id: str,
        facility_id: LogisticsIdentifier,
        pad_index: int,
        max_ticks: int = 4096,
    ):
        if not isinstance(contract, NativeParcelContract):
            raise NativeParcelRuntimeError(
                "presence window requires the declared NativeParcelContract"
            )
        if not isinstance(run_id, str) or not run_id:
            raise NativeParcelRuntimeError(
                "presence window requires the declared run id"
            )
        if not isinstance(facility_id, str) or not facility_id:
            raise NativeParcelRuntimeError(
                "presence window requires the declared facility id"
            )
        if (
            isinstance(pad_index, bool)
            or not isinstance(pad_index, int)
            or pad_index < 0
        ):
            raise NativeParcelRuntimeError(
                "presence window pad_index must be a nonnegative integer"
            )
        if (
            isinstance(max_ticks, bool)
            or not isinstance(max_ticks, int)
            or max_ticks < 2
        ):
            raise NativeParcelRuntimeError(
                "presence window must retain at least two ticks"
            )
        self._contract = contract
        self._run_id = run_id
        self._facility_id = facility_id
        self._pad_index = pad_index
        self._max_ticks = max_ticks
        self._last_at: SimulationTime | None = None
        self._samples: tuple[FacilityPadPhysicalObservation, ...] = ()

    @property
    def facility_id(self) -> str:
        return self._facility_id

    @property
    def pad_index(self) -> int:
        return self._pad_index

    @property
    def last_at(self) -> SimulationTime | None:
        """The exact last absorbed stage time (set even after a reset)."""
        return self._last_at

    def restore(
        self,
        samples: tuple[FacilityPadPhysicalObservation, ...],
        *,
        last_at: SimulationTime | None,
    ) -> None:
        """Restore this window's exact persisted state (no re-assessment).

        The samples are the exact qualifying contiguous run and ``last_at``
        is the exact last absorbed stage time — set even when an ineligible
        stage cleared the run, so contiguity gating and the
        dwell-ends-at-admission-tick check behave identically after a JSON
        roundtrip into a fresh machine.  Window samples are never re-sorted
        and never re-assessed here: the sealed evidence was already
        kernel-assessed when absorbed.
        """
        for sample in samples:
            _strict_model(
                FacilityPadPhysicalObservation, sample, "window sample"
            )
            if sample.run_id != self._run_id:
                raise NativeParcelRuntimeError(
                    "restored window sample belongs to run "
                    f"{sample.run_id!r}, not the declared parcel run "
                    f"{self._run_id!r}"
                )
            if (
                sample.facility_id != self._facility_id
                or sample.pad_index != self._pad_index
            ):
                raise NativeParcelRuntimeError(
                    f"restored window sample names facility "
                    f"{sample.facility_id!r} pad {sample.pad_index}; this "
                    f"window accumulates only {self._facility_id!r} pad "
                    f"{self._pad_index}"
                )
        if last_at is not None and samples:
            if _tick_key(samples[-1].at) != _tick_key(last_at):
                raise NativeParcelRuntimeError(
                    "restored window last-at does not match the window's "
                    "closing sample; exact-state restoration is impossible"
                )
        self._samples = tuple(samples)
        self._last_at = last_at

    def observe(self, observation: FacilityPadPhysicalObservation) -> bool:
        """Absorb one closed-stage observation; return True if it extends.

        The observation must bind the declared run, the one declared carrier
        identity and this window's pad exactly (foreign identity raises).  A
        stale or non-contiguous tick resets the window to just this sample.
        """
        _strict_model(
            FacilityPadPhysicalObservation,
            observation,
            "FacilityPadPhysicalObservation",
        )
        if observation.run_id != self._run_id:
            raise NativeParcelRuntimeError(
                f"observation belongs to run {observation.run_id!r}, not the "
                "declared parcel run; foreign evidence is rejected"
            )
        canonical_carrier_profile(self._contract, observation=observation)
        if (
            observation.facility_id != self._facility_id
            or observation.pad_index != self._pad_index
        ):
            raise NativeParcelRuntimeError(
                f"observation names facility {observation.facility_id!r} pad "
                f"{observation.pad_index}; this window accumulates only "
                f"{self._facility_id!r} pad {self._pad_index}"
            )
        at = observation.at
        extends = (
            self._last_at is not None
            and at.tick == self._last_at.tick + 1
            and at.sim_time_ns > self._last_at.sim_time_ns
        )
        # Every incoming stage is assessed by the accepted pure presence
        # kernel with the declared canonical profile and policy tolerances:
        # an invalid, moving, airborne/contact-lost or UNKNOWN stage can
        # never join the qualifying contiguous run.  It clears the qualifying
        # suffix outright (it is not retained as a run member), tick
        # contiguity continues from its position, and the grounded run
        # restarts at the next eligible stage — a bad stage therefore never
        # poisons a later valid suffix, and a valid suffix is never padded
        # across an invalid stage.
        profile = canonical_carrier_profile(self._contract, observation=observation)
        assessment = assess_facility_presence(
            pad=observation.pad,
            sample=observation.sample,
            profile=profile,
            event=observation.event_binding,
            tolerances=self._contract.policy.presence_tolerances(),
        )
        self._last_at = at
        if not assessment.eligible:
            self._samples = ()
            return False
        self._samples = (
            (*self._samples, observation) if extends else (observation,)
        )[-self._max_ticks :]
        return extends

    def snapshot(self) -> tuple[FacilityPadPhysicalObservation, ...]:
        """The current immutable window (never re-sorted, never padded)."""
        return self._samples


# ------------------------------------------------------------- dwell verdict


def assess_parcel_dwell(
    *,
    contract: NativeParcelContract,
    run_id: str,
    kind: Literal["pickup", "dropoff"],
    window: tuple[FacilityPadPhysicalObservation, ...],
    facility_id: str,
    pad_index: int,
) -> ParcelDwellAdmission:
    """Evaluate one contiguous presence window against the authored policy.

    Every sample is re-assessed by the accepted pure presence kernel with the
    declared canonical profile and policy tolerances.  Any UNKNOWN condition —
    an empty window, a single sample, a gap, a non-eligible assessment, a span
    below the declared minimum — yields an explicit unconfirmed verdict with
    the exact reason; presence is never inferred from labels or from missing
    observations.
    """
    if kind not in ("pickup", "dropoff"):
        raise NativeParcelRuntimeError(
            "dwell admission requires the pickup or dropoff kind"
        )
    if not isinstance(facility_id, str) or not facility_id:
        raise NativeParcelRuntimeError(
            "dwell admission requires the declared facility id"
        )
    if (
        isinstance(pad_index, bool)
        or not isinstance(pad_index, int)
        or pad_index < 0
    ):
        raise NativeParcelRuntimeError(
            "dwell admission pad_index must be a nonnegative integer"
        )
    minimum_dwell_s = (
        contract.policy.minimum_pickup_dwell_s
        if kind == "pickup"
        else contract.policy.minimum_dropoff_dwell_s
    )
    window_digest = _digest_object(
        {
            "facility_id": facility_id,
            "pad_index": pad_index,
            "kind": kind,
            "run_id": run_id,
            "observation_digests": [
                observation.observation_digest for observation in window
            ],
        }
    )
    if not window:
        confirmed, reason = False, "the closed presence window is empty"
    elif len(window) < 2:
        confirmed, reason = False, (
            "a contiguous dwell requires at least two distinct closed stages"
        )
    elif not all(
        right.at.tick == left.at.tick + 1
        for left, right in zip(window, window[1:])
    ):
        confirmed, reason = False, (
            "the presence window is not contiguous; a gapped dwell is never "
            "padded"
        )
    else:
        failure: str | None = None
        profile = canonical_carrier_profile(contract, observation=window[0])
        tolerances = contract.policy.presence_tolerances()
        for observation in window:
            assessment = assess_facility_presence(
                pad=observation.pad,
                sample=observation.sample,
                profile=profile,
                event=observation.event_binding,
                tolerances=tolerances,
            )
            if not assessment.eligible:
                failure = (
                    f"tick {observation.at.tick} presence assessment is not "
                    f"eligible: {assessment.failure_code}"
                )
                break
        if failure is not None:
            confirmed, reason = False, failure
        else:
            start, end = window[0].at, window[-1].at
            dwell_seconds = (end.sim_time_ns - start.sim_time_ns) / 1_000_000_000.0
            confirmed = dwell_seconds >= minimum_dwell_s
            reason = (
                None
                if confirmed
                else (
                    f"contiguous grounded dwell {dwell_seconds!r} s does not "
                    f"reach the declared minimum {minimum_dwell_s!r} s"
                )
            )
    start = window[0].at if window else None
    end = window[-1].at if window else None
    dwell_seconds = (
        (end.sim_time_ns - start.sim_time_ns) / 1_000_000_000.0
        if window
        else 0.0
    )
    return ParcelDwellAdmission(
        schema_version=NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION,
        kind=kind,
        facility_id=facility_id,
        pad_index=pad_index,
        run_id=run_id,
        window_size=len(window),
        dwell_seconds=dwell_seconds,
        minimum_dwell_s=minimum_dwell_s,
        dwell_window_digest=window_digest,
        confirmed=confirmed,
        unconfirmed_reason=reason,
    )


# ---------------------------------------------------------- action checks


def _action_key(action: AdmittedParcelAction) -> ParcelActionKey:
    """The exactly-once admission key ``(run_id, principal_id, action_id)``."""
    return (action.run_id, action.principal_id, action.action_id)


def _validate_action_binding(
    contract: NativeParcelContract,
    run_id: str,
    action: AdmittedParcelAction,
) -> None:
    """Raise on any foreign or malformed action identity payload."""
    _strict_model(AdmittedParcelAction, action, "AdmittedParcelAction")
    if action.run_id != run_id:
        raise NativeParcelRuntimeError(
            f"action {action.action_id!r} names run {action.run_id!r}; the "
            f"machine admits only run {run_id!r} — foreign evidence is "
            "rejected, not ignored"
        )
    ids = contract.identities
    if action.order_id != ids.order_id:
        raise NativeParcelRuntimeError(
            f"action {action.action_id!r} names order {action.order_id!r}; "
            f"the slice binds only {ids.order_id!r}"
        )
    if action.parcel_entity_id != ids.parcel_entity_id:
        raise NativeParcelRuntimeError(
            f"action {action.action_id!r} names parcel "
            f"{action.parcel_entity_id!r}; the slice binds only "
            f"{ids.parcel_entity_id!r}"
        )
    if action.carrier_entity_id != ids.carrier_entity_id:
        raise NativeParcelRuntimeError(
            f"action {action.action_id!r} names carrier "
            f"{action.carrier_entity_id!r}; the slice binds only "
            f"{ids.carrier_entity_id!r}"
        )
    if not getattr(ids, "authorized_principal_id", None):
        raise NativeParcelRuntimeError(
            "the declared contract carries no authorized principal binding; "
            "a carrier action cannot be authorized without the exact "
            "principal-to-carrier binding (no default, no inference)"
        )
    if action.principal_id != ids.authorized_principal_id:
        raise NativeParcelRuntimeError(
            f"action {action.action_id!r} names principal "
            f"{action.principal_id!r}; only the declared principal "
            f"{ids.authorized_principal_id!r} is authorized to act for the "
            "declared carrier — foreign principal evidence is rejected, not "
            "inferred"
        )


def _validate_payload_consistency(
    action: AdmittedParcelAction,
    binding: object,
) -> None:
    """Raise on a duplicate admission key with a divergent identity payload."""
    if binding is None:
        return
    from aero_bench.tasks.logistics.native_parcel_contract import (
        ParcelActionIdentityBinding,
    )

    _strict_model(
        ParcelActionIdentityBinding, binding, "ParcelActionIdentityBinding"
    )
    divergent = [
        (name, getattr(action, name), getattr(binding, name))
        for name in (
            "run_id",
            "action_id",
            "kind",
            "order_id",
            "parcel_entity_id",
            "carrier_entity_id",
            "principal_id",
        )
        if getattr(action, name) != getattr(binding, name)
    ]
    if divergent:
        details = ", ".join(
            f"{name}: action={actual!r} vs prior={prior!r}"
            for name, actual, prior in divergent
        )
        raise NativeParcelRuntimeError(
            f"admission key "
            f"{(action.run_id, action.principal_id, action.action_id)!r} "
            f"conflicts with the payload already recorded under that key "
            f"({details}); a duplicate key with a different payload is an "
            "explicit conflict, never a second decision"
        )


# ---------------------------------------------------------- snapshot record


class NativeParcelSnapshot(StrictModel):
    """The exact seek-consistent append-only parcel snapshot (schema v2).

    ``sequence`` counts accepted evidence/decision commits;
    ``past_keys`` are the closed-stage ``(tick, sim_time_ns)`` keys already
    absorbed (the global replay frontier); ``stream_digest`` binds the exact
    digest chain of every absorbed evidence/decision fact.  A JSON roundtrip
    of this snapshot into a fresh machine of the same contract/run restores
    the *exact* machine state: both qualifying presence windows (with each
    window's last absorbed time, including an invalid-reset empty window),
    the per-facility and global evidence frontiers, the current time, and the
    complete keyed decision ledger including unconfirmed/rejected tombstones
    with their original outcomes.  There is no old-schema compatibility and
    no empty default concealing lost state: every exact-state field is
    required, and a ledger key whose original outcome is missing is explicit
    corruption, never physical replay.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-snapshot/v2"]
    contract_id: str
    contract_digest: str
    run_id: str
    sequence: int
    parcel_state: NativeParcelState
    past_keys: tuple[tuple[int, int], ...]
    stream_digest: str
    current_time: SimulationTime
    last_observation_at: SimulationTime | None
    facility_frontiers: tuple[tuple[str, SimulationTime | None], ...]
    pickup_window_samples: tuple[FacilityPadPhysicalObservation, ...]
    pickup_window_last_at: SimulationTime | None
    dropoff_window_samples: tuple[FacilityPadPhysicalObservation, ...]
    dropoff_window_last_at: SimulationTime | None
    decisions: tuple[ParcelActionKeyRecord, ...]
    decision_outcomes: tuple[ParcelAdmissionOutcome, ...]
    #: The exact previous decision frontier (``None`` when no keyed decision
    #: exists).  A duplicate replay advances the current time without
    #: creating a decision, so this is not derivable from ``current_time``;
    #: it is persisted exactly and a missing field is a validation error.
    last_decision_at: SimulationTime | None

    @model_validator(mode="after")
    def exact_state_is_internally_consistent(self) -> "NativeParcelSnapshot":
        frontiers = dict(self.facility_frontiers)
        if len(frontiers) != len(self.facility_frontiers):
            raise ValueError(
                "snapshot facility frontiers name the same facility twice"
            )
        seen_keys = set()
        for record in self.decisions:
            key = (record.run_id, record.principal_id, record.action_id)
            if key in seen_keys:
                raise ValueError(
                    f"snapshot decision ledger names key {key!r} twice"
                )
            seen_keys.add(key)
            if record.content_digest not in {
                outcome.action_digest for outcome in self.decision_outcomes
            }:
                raise ValueError(
                    f"snapshot decision ledger key {key!r} has no recorded "
                    "original outcome; missing keyed state is corruption, "
                    "never physical replay"
                )
        return self


# ------------------------------------------------------------ state machine


class NativeParcelStateMachine:
    """One-order native parcel admission state machine (append-only).

    The machine binds one declared :class:`NativeParcelContract` and one run.
    Closed-stage carrier presence samples enter through :meth:`observe`;
    admitted actions enter through :meth:`admit_action`.  Custody transfers
    are exactly once, duplicate admitted actions replay their original
    decision without a second transfer, UNKNOWN evidence returns an explicit
    unconfirmed outcome and foreign/malformed identity raises.
    """

    def __init__(
        self,
        *,
        contract: NativeParcelContract,
        run_id: str,
        now: SimulationTime,
        pickup_pad_index: int = 0,
        dropoff_pad_index: int = 0,
    ):
        if not isinstance(contract, NativeParcelContract):
            raise NativeParcelRuntimeError(
                "parcel state machine requires the declared NativeParcelContract"
            )
        if not isinstance(run_id, str) or not run_id:
            raise NativeParcelRuntimeError(
                "parcel state machine requires the declared run id"
            )
        if not isinstance(now, SimulationTime):
            raise NativeParcelRuntimeError(
                "parcel state machine requires the authoritative SimulationTime"
            )
        if (
            isinstance(pickup_pad_index, bool)
            or not isinstance(pickup_pad_index, int)
            or pickup_pad_index < 0
            or isinstance(dropoff_pad_index, bool)
            or not isinstance(dropoff_pad_index, int)
            or dropoff_pad_index < 0
        ):
            raise NativeParcelRuntimeError(
                "pad indexes must be nonnegative integers"
            )
        self._contract = contract
        self._run_id = run_id
        ids = contract.identities
        self._pickup_facility_id = ids.pickup_facility_id
        self._dropoff_facility_id = ids.dropoff_facility_id
        self._pickup_pad_index = pickup_pad_index
        self._dropoff_pad_index = dropoff_pad_index
        self._pickup_window = self._fresh_window(
            self._pickup_facility_id, pickup_pad_index
        )
        self._dropoff_window = self._fresh_window(
            self._dropoff_facility_id, dropoff_pad_index
        )
        holder = ParcelCustodyHolder(
            schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
            holder_kind="pickup_facility",
            holder_id=ids.pickup_facility_id,
        )
        self._parcel_state = NativeParcelState(
            schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
            contract_id=contract.contract_id,
            run_id=run_id,
            state="awaiting_pickup",
            holder=holder,
            transitions=(),
            transfers=(),
            decided_action_keys=(),
            last_update_at=now,
        )
        self._now = now
        self._sequence = 0
        self._past_keys: tuple[tuple[int, int], ...] = ()
        self._stream_digest = _digest_object(
            {"contract_digest": contract.contract_digest(), "run_id": run_id}
        )
        self._last_observation_at: SimulationTime | None = None
        # Per-facility absorbed evidence frontiers: the first sample from
        # EACH declared facility at the identical authoritative tick/time is
        # absorbed; a duplicate or stale sample from the same facility is
        # never re-bound.  The global frontier is the maximum over all
        # absorbed samples and never rolls backward.
        self._facility_frontiers: dict[str, SimulationTime | None] = {
            self._pickup_facility_id: None,
            self._dropoff_facility_id: None,
        }
        self._admitted_history: tuple[ParcelAdmissionOutcome, ...] = ()
        # Exactly-once admission ledger keyed by
        # (run_id, principal_id, action_id) with the digest of the decided
        # content, plus the decided outcomes by content digest for idempotent
        # replay.
        self._decisions: dict[ParcelActionKey, ParcelActionKeyRecord] = {}
        self._decision_by_digest: dict[str, ParcelAdmissionOutcome] = {}
        self._last_decision_at: SimulationTime | None = None

    # ------------------------------------------------------------ internals

    def _fresh_window(
        self, facility_id: str, pad_index: int
    ) -> ClosedParcelPresenceWindow:
        return ClosedParcelPresenceWindow(
            contract=self._contract,
            run_id=self._run_id,
            facility_id=facility_id,
            pad_index=pad_index,
        )

    # ------------------------------------------------------------ properties

    @property
    def contract(self) -> NativeParcelContract:
        return self._contract

    @property
    def state(self) -> str:
        return self._parcel_state.state

    @property
    def holder(self) -> ParcelCustodyHolder:
        return self._parcel_state.holder

    @property
    def parcel_state(self) -> NativeParcelState:
        return self._parcel_state

    @property
    def sequence(self) -> int:
        return self._sequence

    # ------------------------------------------------------------- evidence

    def observe(self, observation: FacilityPadPhysicalObservation) -> bool:
        """Absorb one closed-stage carrier presence sample.

        The sample must be **clock-consistent and strictly newer** than the
        absorbed **global** evidence frontier: simulation time must increase
        with tick (a greater tick with a non-increasing sim_time_ns is an
        inconsistent clock and raises — a lexicographic tuple ordering is
        never assumed), a strictly older closed stage after a seek returns
        ``False``, and the SAME tick with a changed sim_time_ns raises.  The
        exact global frontier :class:`SimulationTime` may be shared by the
        SECOND declared facility at the identical tick **and** sim_time_ns
        only; a same-tick sample from a facility that has already absorbed
        that tick returns ``False`` and can never increment a dwell or phase
        across pads.  Each declared facility additionally keeps its **own**
        frontier: a duplicate or stale sample from the same facility returns
        ``False``.  The global frontier is the maximum over all absorbed
        samples and never rolls backward.  The sample routes to the pickup or
        dropoff pad window by its declared facility identity; any other
        facility is foreign and raises.  The pickup window accumulates only
        while the parcel awaits pickup; the dropoff window only while the
        carrier holds the parcel (``loaded``/``in_transit``).  When a loaded
        carrier is observed explicitly in air, the runtime-detected
        ``loaded -> in_transit`` flip is committed, bound to exactly that
        observation.
        """
        _strict_model(
            FacilityPadPhysicalObservation,
            observation,
            "FacilityPadPhysicalObservation",
        )
        if observation.run_id != self._run_id:
            raise NativeParcelRuntimeError(
                f"observation belongs to run {observation.run_id!r}, not the "
                "declared parcel run; foreign evidence is rejected"
            )
        if self._last_observation_at is not None:
            global_relation = _clock_relation(
                observation.at, self._last_observation_at
            )
            if global_relation == "inconsistent":
                # One declared monotone clock: a greater tick with a
                # non-increasing sim_time_ns (or the reverse) disagrees with
                # every already-absorbed stage and is never re-interpreted.
                raise NativeParcelRuntimeError(
                    f"observation at tick {observation.at.tick} "
                    f"({observation.at.sim_time_ns} ns) is clock-inconsistent "
                    f"with the absorbed global frontier tick "
                    f"{self._last_observation_at.tick} "
                    f"({self._last_observation_at.sim_time_ns} ns); the "
                    "declared simulation time must increase with tick"
                )
            if (
                observation.at.tick == self._last_observation_at.tick
                and observation.at.sim_time_ns
                != self._last_observation_at.sim_time_ns
            ):
                # The closed stage at the frontier tick has exactly ONE
                # authoritative SimulationTime.  A sample at the same tick
                # with a changed sim_time_ns is a re-stamped stage — the
                # second declared facility shares only the EXACT frontier
                # time, and no facility re-increments a dwell or phase at a
                # mutated time.
                raise NativeParcelRuntimeError(
                    f"observation at tick {observation.at.tick} carries "
                    f"sim_time_ns {observation.at.sim_time_ns} ns, but the "
                    "absorbed stage at that tick carries "
                    f"{self._last_observation_at.sim_time_ns} ns; one closed "
                    "stage has exactly one authoritative SimulationTime and "
                    "a same-tick re-stamp is never absorbed"
                )
            if global_relation == "earlier":
                # A replayed/older closed stage after a seek: never re-bind.
                return False
        if observation.facility_id not in self._facility_frontiers:
            raise NativeParcelRuntimeError(
                f"observation names facility {observation.facility_id!r}; the "
                f"slice observes only {self._pickup_facility_id!r} and "
                f"{self._dropoff_facility_id!r} — foreign evidence is rejected"
            )
        facility_frontier = self._facility_frontiers[observation.facility_id]
        if facility_frontier is not None:
            facility_relation = _clock_relation(
                observation.at, facility_frontier
            )
            if facility_relation in ("equal", "earlier"):
                # A duplicate or stale closed stage from this same facility
                # (including the same tick already absorbed by this pad):
                # never re-bound, never a second dwell increment.
                return False
            if facility_relation == "inconsistent":
                raise NativeParcelRuntimeError(
                    f"observation at tick {observation.at.tick} "
                    f"({observation.at.sim_time_ns} ns) is clock-inconsistent "
                    f"with the {observation.facility_id!r} frontier tick "
                    f"{facility_frontier.tick} "
                    f"({facility_frontier.sim_time_ns} ns); the declared "
                    "simulation time must increase with tick"
                )
        if observation.facility_id == self._pickup_facility_id:
            if self._parcel_state.state == "awaiting_pickup":
                self._pickup_window.observe(observation)
            else:
                self._pickup_window = self._fresh_window(
                    self._pickup_facility_id, self._pickup_pad_index
                )
        else:  # the only other declared facility (checked above)
            if self._parcel_state.state in ("loaded", "in_transit"):
                self._dropoff_window.observe(observation)
            else:
                self._dropoff_window = self._fresh_window(
                    self._dropoff_facility_id, self._dropoff_pad_index
                )
        canonical_carrier_profile(self._contract, observation=observation)
        if (
            self._last_observation_at is None
            or _clock_relation(observation.at, self._last_observation_at)
            == "later"
        ):
            # The global frontier is the maximum over all absorbed samples
            # and never rolls backward.
            self._last_observation_at = observation.at
        self._facility_frontiers[observation.facility_id] = observation.at
        self._sequence += 1
        self._past_keys = (
            (*self._past_keys, _tick_key(observation.at))
        )[-_MAX_PAST_KEYS:]
        self._stream_digest = _digest_object(
            {
                "previous": self._stream_digest,
                "observation_digest": observation.observation_digest,
                "at": observation.at.model_dump(mode="json"),
            }
        )
        if (
            self._parcel_state.state == "loaded"
            and (observation.sample.in_air or not observation.sample.landed)
        ):
            self._commit_in_transit(observation)
        return True

    # -------------------------------------------------- runtime-detected flip

    def _commit_in_transit(
        self, observation: FacilityPadPhysicalObservation
    ) -> None:
        """Commit ``loaded -> in_transit`` on the explicit in-air evidence.

        This is not an admitted custody action: no transfer is produced and no
        custody holder changes.  The transition record is bound to the exact
        observation that evidenced the departure.
        """
        evidence_digest = observation.observation_digest
        transition = ParcelStateTransitionRecord(
            schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
            from_state="loaded",
            to_state="in_transit",
            at=observation.at,
            action_id=_IN_TRANSIT_ACTION_ID,
            action_digest=evidence_digest,
            dwell_window_digest=_digest_object(
                {
                    "evidence": "runtime_detected_in_air_departure",
                    "observation_digest": evidence_digest,
                }
            ),
            transfer_id=None,
        )
        self._parcel_state = NativeParcelState(
            schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
            contract_id=self._contract.contract_id,
            run_id=self._run_id,
            state="in_transit",
            holder=self._parcel_state.holder,
            transitions=(*self._parcel_state.transitions, transition),
            transfers=self._parcel_state.transfers,
            decided_action_keys=self._parcel_state.decided_action_keys,
            last_update_at=observation.at,
        )
        self._now = observation.at
        self._sequence += 1
        self._stream_digest = _digest_object(
            {
                "previous": self._stream_digest,
                "in_transit_evidence": evidence_digest,
            }
        )

    # ---------------------------------------------------------------- dwell

    def dwell_admission(
        self, kind: Literal["pickup", "dropoff"]
    ) -> ParcelDwellAdmission:
        """The current typed dwell verdict for the pickup or dropoff pad."""
        if kind == "pickup":
            window = self._pickup_window.snapshot()
            facility_id = self._pickup_window.facility_id
            pad_index = self._pickup_window.pad_index
        elif kind == "dropoff":
            window = self._dropoff_window.snapshot()
            facility_id = self._dropoff_window.facility_id
            pad_index = self._dropoff_window.pad_index
        else:
            raise NativeParcelRuntimeError(
                "dwell admission requires the pickup or dropoff kind"
            )
        return assess_parcel_dwell(
            contract=self._contract,
            run_id=self._run_id,
            kind=kind,
            window=window,
            facility_id=facility_id,
            pad_index=pad_index,
        )

    # --------------------------------------------------------------- action

    def admit_action(
        self,
        action: AdmittedParcelAction,
        *,
        now: SimulationTime,
        payload_binding: object = None,
    ) -> ParcelAdmissionOutcome:
        """Evaluate one admitted action against the current parcel state.

        ``now`` is the authoritative admission decision time: it is required,
        it must be an exact :class:`SimulationTime`, it may never precede the
        absorbed evidence frontier and never move backward from a previous
        decision time — stale or rewound admission is rejected.  The
        qualifying contiguous dwell must **end at the current admission
        tick**: a window whose last stage is not exactly ``now.tick`` is a
        stale window and is never confirmed.

        The action/RX proof is a typed exact input (never a bare boolean or a
        network receipt).  Admission is exactly once per the key
        ``(run_id, principal_id, action_id)``: the same key with identical
        content returns the original decision without a second transfer, and
        the same key with any different content raises (payload conflict).
        An UNKNOWN evidence condition returns an explicit unconfirmed
        outcome; foreign or malformed identity raises.
        """
        _validate_action_binding(self._contract, self._run_id, action)
        _validate_payload_consistency(action, payload_binding)
        if not isinstance(now, SimulationTime):
            raise NativeParcelRuntimeError(
                "admission decision time is required and must be an exact "
                "SimulationTime"
            )
        if (
            self._last_observation_at is not None
            and _clock_relation(now, self._last_observation_at) == "inconsistent"
        ):
            raise NativeParcelRuntimeError(
                f"admission decision time {now.tick}/{now.sim_time_ns} ns is "
                "clock-inconsistent with the absorbed evidence frontier "
                f"{self._last_observation_at.tick}/"
                f"{self._last_observation_at.sim_time_ns} ns: the declared "
                "simulation time must increase with tick; a lexicographic "
                "tuple ordering is never accepted"
            )
        if (
            self._last_observation_at is not None
            and _clock_relation(now, self._last_observation_at) == "earlier"
        ):
            raise NativeParcelRuntimeError(
                f"admission decision time {now.tick} precedes the absorbed "
                f"evidence frontier tick {self._last_observation_at.tick}"
            )
        if (
            self._last_decision_at is not None
            and _clock_relation(now, self._last_decision_at) == "inconsistent"
        ):
            raise NativeParcelRuntimeError(
                f"admission decision time {now.tick}/{now.sim_time_ns} ns is "
                "clock-inconsistent with the previous decision time "
                f"{self._last_decision_at.tick}/{self._last_decision_at.sim_time_ns} ns: "
                "the declared simulation time must increase with tick"
            )
        if (
            self._last_decision_at is not None
            and _clock_relation(now, self._last_decision_at) == "earlier"
        ):
            raise NativeParcelRuntimeError(
                f"admission decision time {now.tick} rewinds before the "
                f"previous decision time {self._last_decision_at.tick}"
            )
        received_relation = _clock_relation(action.received_at, now)
        if received_relation == "inconsistent":
            raise NativeParcelRuntimeError(
                f"action {action.action_id!r} claims reception at "
                f"{action.received_at.tick}/{action.received_at.sim_time_ns} ns "
                f"against the admission decision time {now.tick}/{now.sim_time_ns} ns: "
                "the reception and decision clocks are inconsistent — the "
                "declared simulation time must increase with tick"
            )
        if received_relation == "later":
            raise NativeParcelRuntimeError(
                f"action {action.action_id!r} claims reception at tick "
                f"{action.received_at.tick}, after the admission decision "
                f"time tick {now.tick}; future evidence is never admitted"
            )
        if action.run_id != self._run_id:
            raise NativeParcelRuntimeError(
                f"action names run {action.run_id!r}, not the declared "
                f"parcel run {self._run_id!r}"
            )
        self._now = now
        action_digest = action.action_digest()
        key = _action_key(action)
        # Exactly-once per (run_id, principal_id, action_id): an admitted
        # decision with identical content replays the original outcome; the
        # same key with different content is an explicit conflict.  A key
        # decided unconfirmed/rejected is retriable only with the *same*
        # content replaying its recorded outcome — a different payload under
        # the same key raises above.
        prior = self._decisions.get(key)
        if prior is not None:
            if prior.content_digest != action_digest:
                raise NativeParcelRuntimeError(
                    f"admission key {key!r} was already decided with different "
                    "content; a duplicate key with a different payload is an "
                    "explicit conflict, never a second decision"
                )
            outcome = self._decision_by_digest.get(action_digest)
            if outcome is None:
                raise NativeParcelRuntimeError(
                    f"admission key {key!r} is recorded in the ledger but "
                    "its original outcome is missing; missing keyed "
                    "decision state is corruption, never a physical replay"
                )
            return outcome
        outcome = self._decide(action=action, action_digest=action_digest)
        record = ParcelActionKeyRecord(
            schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
            run_id=action.run_id,
            principal_id=action.principal_id,
            action_id=action.action_id,
            content_digest=action_digest,
        )
        self._decisions[key] = record
        self._last_decision_at = now
        self._decision_by_digest[action_digest] = outcome
        if outcome.status == "admitted":
            self._admitted_history = (*self._admitted_history, outcome)
            self._commit_admitted(
                action=action, action_digest=action_digest, outcome=outcome
            )
        return outcome

    # -------------------------------------------------------------- decision

    def _decide(
        self,
        *,
        action: AdmittedParcelAction,
        action_digest: str,
    ) -> ParcelAdmissionOutcome:
        ids = self._contract.identities
        kind = action.kind
        state_before = self._parcel_state.state
        holder_before = self._parcel_state.holder
        if kind == "pickup":
            facility_id = self._pickup_window.facility_id
            pad_index = self._pickup_window.pad_index
            window = self._pickup_window.snapshot()
            expected_states = ("awaiting_pickup",)
        else:
            facility_id = self._dropoff_window.facility_id
            pad_index = self._dropoff_window.pad_index
            window = self._dropoff_window.snapshot()
            expected_states = ("loaded", "in_transit")

        if state_before not in expected_states:
            return ParcelAdmissionOutcome(
                schema_version=NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION,
                action_id=action.action_id,
                action_digest=action_digest,
                status="rejected",
                reason=(
                    f"{kind} action {action.action_id!r} arrived while the "
                    f"parcel is {state_before!r}; the {kind} transition "
                    f"requires the parcel state {' or '.join(expected_states)!r}"
                ),
                state_before=state_before,
                state_after=state_before,
                holder_after=holder_before,
                decision_at=self._now,
            )

        dwell = assess_parcel_dwell(
            contract=self._contract,
            run_id=self._run_id,
            kind=kind,
            window=window,
            facility_id=facility_id,
            pad_index=pad_index,
        )
        if not dwell.confirmed:
            return ParcelAdmissionOutcome(
                schema_version=NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION,
                action_id=action.action_id,
                action_digest=action_digest,
                status="unconfirmed",
                reason=(
                    f"{dwell.unconfirmed_reason}; {UNKNOWN_SOURCE_NOTE} the "
                    f"{kind} action is admitted only after the contiguous "
                    "grounded closed dwell passes"
                ),
                state_before=state_before,
                state_after=state_before,
                holder_after=holder_before,
                decision_at=self._now,
            )
        # The qualifying contiguous dwell must END at the current admission
        # time exactly: the closing observation's authoritative SimulationTime
        # must equal the admission time in BOTH tick and sim_time_ns.  A
        # window that closed earlier is a stale window, and a window whose
        # closing stage matches only on the integer tick (e.g. a tick=3/3 s
        # dwell offered against a tick=3/100 s admission time) is not the
        # evidence that reaches this admission moment either — it is never
        # confirmed.
        closing_at = window[-1].at
        if _tick_key(closing_at) != _tick_key(self._now):
            relation = (
                "before"
                if _tick_key(closing_at) < _tick_key(self._now)
                else "after"
            )
            return ParcelAdmissionOutcome(
                schema_version=NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION,
                action_id=action.action_id,
                action_digest=action_digest,
                status="unconfirmed",
                reason=(
                    f"the contiguous closed dwell window closed at tick "
                    f"{closing_at.tick}, {relation} the current admission "
                    f"tick {self._now.tick}; a qualifying dwell must end "
                    f"exactly at the current admission tick; "
                    f"{UNKNOWN_SOURCE_NOTE} "
                    f"the {kind} action is admitted only after the "
                    "contiguous grounded closed dwell reaches the current "
                    "admission tick"
                ),
                state_before=state_before,
                state_after=state_before,
                holder_after=holder_before,
                decision_at=self._now,
            )
        # The RX evidence must be bound to the exact closed stage the dwell
        # window closed on: a barrier from another stage is an unconfirmed
        # binding, never silently accepted.
        closing_barrier = window[-1].source_stage_barrier_digest
        if action.rx_stage_barrier_digest != closing_barrier:
            return ParcelAdmissionOutcome(
                schema_version=NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION,
                action_id=action.action_id,
                action_digest=action_digest,
                status="unconfirmed",
                reason=(
                    f"action RX evidence binds barrier "
                    f"{action.rx_stage_barrier_digest!r} but the contiguous "
                    f"dwell window closed on barrier {closing_barrier!r}; the "
                    "admitted action and the closed dwell are not one bound "
                    "evidence moment"
                ),
                state_before=state_before,
                state_after=state_before,
                holder_after=holder_before,
                decision_at=self._now,
            )

        # ---- build the exactly-once custody transfer -----------------------
        if kind == "pickup":
            from_holder = ParcelCustodyHolder(
                schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                holder_kind="pickup_facility",
                holder_id=ids.pickup_facility_id,
            )
            to_holder = ParcelCustodyHolder(
                schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                holder_kind="carrier",
                holder_id=ids.carrier_entity_id,
            )
            state_after = "loaded"
        else:
            from_holder = ParcelCustodyHolder(
                schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                holder_kind="carrier",
                holder_id=ids.carrier_entity_id,
            )
            to_holder = ParcelCustodyHolder(
                schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                holder_kind="dropoff_facility",
                holder_id=ids.dropoff_facility_id,
            )
            state_after = "delivered"
        transfer = ParcelCustodyTransferRecord.mint(
            kind=kind,
            order_id=ids.order_id,
            parcel_entity_id=ids.parcel_entity_id,
            carrier_entity_id=ids.carrier_entity_id,
            from_holder=from_holder,
            to_holder=to_holder,
            at=self._now,
            action_id=action.action_id,
            action_digest=action_digest,
            dwell_window_digest=dwell.dwell_window_digest,
        )
        return ParcelAdmissionOutcome(
            schema_version=NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION,
            action_id=action.action_id,
            action_digest=action_digest,
            status="admitted",
            reason=(
                f"{kind} admitted: contiguous grounded closed dwell of "
                f"{dwell.window_size} samples to tick {window[-1].at.tick} "
                f"reached {dwell.dwell_seconds!r} s and the admitted action/RX "
                "proof binds the closing stage barrier"
            ),
            state_before=state_before,
            state_after=state_after,
            holder_after=to_holder,
            transfer_id=transfer.transfer_id,
            dwell_window_digest=dwell.dwell_window_digest,
            decision_at=self._now,
        )

    # -------------------------------------------------------------- mutation

    def _commit_admitted(
        self,
        *,
        action: AdmittedParcelAction,
        action_digest: str,
        outcome: ParcelAdmissionOutcome,
    ) -> None:
        kind = action.kind
        transfer_id = outcome.transfer_id
        assert transfer_id is not None  # an admitted transition carries one
        # Rebuild the exact transfer decided by _decide (fully deterministic:
        # same identities, decision time, digests and dwell window digest
        # reproduce the identical minted transfer_id).
        ids = self._contract.identities
        if kind == "pickup":
            from_holder = ParcelCustodyHolder(
                schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                holder_kind="pickup_facility",
                holder_id=ids.pickup_facility_id,
            )
            to_holder = ParcelCustodyHolder(
                schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                holder_kind="carrier",
                holder_id=ids.carrier_entity_id,
            )
            state_after = "loaded"
        else:
            from_holder = ParcelCustodyHolder(
                schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                holder_kind="carrier",
                holder_id=ids.carrier_entity_id,
            )
            to_holder = ParcelCustodyHolder(
                schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                holder_kind="dropoff_facility",
                holder_id=ids.dropoff_facility_id,
            )
            state_after = "delivered"
        transition = ParcelStateTransitionRecord(
            schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
            from_state=outcome.state_before,
            to_state=state_after,
            at=self._now,
            action_id=action.action_id,
            action_digest=action_digest,
            dwell_window_digest=outcome.dwell_window_digest,
            transfer_id=transfer_id,
        )
        transfer = ParcelCustodyTransferRecord.mint(
            kind=kind,
            order_id=ids.order_id,
            parcel_entity_id=ids.parcel_entity_id,
            carrier_entity_id=ids.carrier_entity_id,
            from_holder=from_holder,
            to_holder=to_holder,
            at=self._now,
            action_id=action.action_id,
            action_digest=action_digest,
            dwell_window_digest=outcome.dwell_window_digest,
        )
        self._parcel_state = NativeParcelState(
            schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
            contract_id=self._contract.contract_id,
            run_id=self._run_id,
            state=state_after,
            holder=transfer.to_holder,
            transitions=(*self._parcel_state.transitions, transition),
            transfers=(*self._parcel_state.transfers, transfer),
            decided_action_keys=(
                *self._parcel_state.decided_action_keys,
                ParcelActionKeyRecord(
                    schema_version=NATIVE_PARCEL_STATE_SCHEMA_VERSION,
                    run_id=action.run_id,
                    principal_id=action.principal_id,
                    action_id=action.action_id,
                    content_digest=action_digest,
                ),
            ),
            last_update_at=self._now,
        )
        if kind == "pickup":
            self._pickup_window = self._fresh_window(
                self._pickup_facility_id, self._pickup_pad_index
            )
        else:
            self._dropoff_window = self._fresh_window(
                self._dropoff_facility_id, self._dropoff_pad_index
            )
        self._sequence += 1
        self._stream_digest = _digest_object(
            {
                "previous": self._stream_digest,
                "decision": {
                    "action_digest": action_digest,
                    "status": outcome.status,
                    "state_after": state_after,
                    "transfer_id": transfer_id,
                },
            }
        )

    # -------------------------------------------------------------- snapshot

    def snapshot(self) -> NativeParcelSnapshot:
        """The exact seek-consistent append-only snapshot of the machine.

        The snapshot carries the complete exact state: both qualifying
        presence windows with their last absorbed times, the per-facility and
        global evidence frontiers, the current time, and the complete keyed
        decision ledger including unconfirmed/rejected tombstones with their
        original outcomes.  A JSON roundtrip into a fresh machine of the same
        contract/run therefore reproduces the machine exactly.
        """
        return NativeParcelSnapshot(
            schema_version=NATIVE_PARCEL_SNAPSHOT_SCHEMA_VERSION,
            contract_id=self._contract.contract_id,
            contract_digest=self._contract.contract_digest(),
            run_id=self._run_id,
            sequence=self._sequence,
            parcel_state=self._parcel_state,
            past_keys=self._past_keys,
            stream_digest=self._stream_digest,
            current_time=self._now,
            last_observation_at=self._last_observation_at,
            facility_frontiers=tuple(
                sorted(self._facility_frontiers.items())
            ),
            pickup_window_samples=self._pickup_window.snapshot(),
            pickup_window_last_at=self._pickup_window.last_at,
            dropoff_window_samples=self._dropoff_window.snapshot(),
            dropoff_window_last_at=self._dropoff_window.last_at,
            decisions=tuple(self._decisions.values()),
            decision_outcomes=tuple(self._decision_by_digest.values()),
            last_decision_at=self._last_decision_at,
        )

    def restore(self, snapshot: NativeParcelSnapshot) -> None:
        """Restore the machine to an earlier snapshot (seek backward).

        Only a snapshot of the same contract/run can be restored; anything
        else is foreign.  The exact state is restored: both qualifying
        presence windows (with their last absorbed times, including an
        invalid-reset empty window), the per-facility and global evidence
        frontiers, the current time, and the complete keyed decision ledger
        including unconfirmed/rejected tombstones with their original
        outcomes.  A ledger key whose original outcome is missing from the
        snapshot is explicit corruption and raises; it is never re-decided
        through a physical replay.  After a restore the absorbed evidence
        frontier is the snapshot's, so seek-forward replay cannot re-bind
        future evidence: observations at or before the restored frontier are
        ignored by :meth:`observe` and an observation that skips the frontier
        raises :class:`SeekConsistencyError` in :meth:`check_seek_forward`.
        """
        _strict_model(NativeParcelSnapshot, snapshot, "NativeParcelSnapshot")
        if snapshot.contract_digest != self._contract.contract_digest():
            raise NativeParcelRuntimeError(
                "snapshot belongs to a different declared contract"
            )
        if snapshot.run_id != self._run_id:
            raise NativeParcelRuntimeError(
                "snapshot belongs to another run; foreign state is rejected"
            )
        self._parcel_state = snapshot.parcel_state
        self._sequence = snapshot.sequence
        self._past_keys = snapshot.past_keys
        self._stream_digest = snapshot.stream_digest
        self._now = snapshot.current_time
        self._last_observation_at = snapshot.last_observation_at
        self._facility_frontiers = dict(snapshot.facility_frontiers)
        if set(self._facility_frontiers) != {
            self._pickup_facility_id,
            self._dropoff_facility_id,
        }:
            raise NativeParcelRuntimeError(
                "snapshot facility frontiers do not name exactly the two "
                "declared facilities; exact-state restoration is impossible"
            )
        self._pickup_window = self._fresh_window(
            self._pickup_facility_id, self._pickup_pad_index
        )
        self._dropoff_window = self._fresh_window(
            self._dropoff_facility_id, self._dropoff_pad_index
        )
        self._pickup_window.restore(
            snapshot.pickup_window_samples, last_at=snapshot.pickup_window_last_at
        )
        self._dropoff_window.restore(
            snapshot.dropoff_window_samples,
            last_at=snapshot.dropoff_window_last_at,
        )
        decided_keys = snapshot.decisions
        self._decisions = {
            (record.run_id, record.principal_id, record.action_id): record
            for record in decided_keys
        }
        outcomes = {o.action_digest: o for o in snapshot.decision_outcomes}
        for record in decided_keys:
            if record.content_digest not in outcomes:
                raise NativeParcelRuntimeError(
                    f"decision key "
                    f"{(record.run_id, record.principal_id, record.action_id)!r} "
                    "has no recorded original outcome in the snapshot; "
                    "missing keyed decision state is corruption, never a "
                    "physical replay"
                )
        self._decision_by_digest = dict(outcomes)
        # The decision frontier is restored exactly: a duplicate replay
        # advances the current time without creating a decision, so it is
        # never synthesized from the snapshot's current time.
        self._last_decision_at = snapshot.last_decision_at

    def check_seek_forward(self, observation: FacilityPadPhysicalObservation) -> None:
        """Raise when the next observation would skip the restored frontier.

        After a seek backward, the sealed replay must re-deliver the closed
        stages from the snapshot frontier onward.  With an absorbed frontier,
        exactly three shapes are permitted and everything else is rejected:

        * the EXACT frontier :class:`SimulationTime` — only for a facility
          that has NOT yet absorbed that time (the second declared pad of a
          shared closed stage); a same-pad repeat or a facility that already
          absorbed the frontier raises;
        * the exact frontier tick with a strictly greater sim_time_ns (the
          next stage at the same declared tick length);
        * the next tick with a strictly increasing sim_time_ns (the next
          declared stage; no tick duration is assumed without a declaration).

        A mismatched same-tick sim_time_ns, a jump beyond the next tick, a
        backward tick and any clock-inconsistent relation all raise
        :class:`SeekConsistencyError`: a future binding is never observed.
        """
        _strict_model(
            FacilityPadPhysicalObservation,
            observation,
            "FacilityPadPhysicalObservation",
        )
        if self._last_observation_at is None:
            return
        frontier = self._last_observation_at
        relation = _clock_relation(observation.at, frontier)
        if relation == "equal":
            # The exact frontier time is only ever the OTHER declared pad's
            # record of the same closed stage; a pad that has already
            # absorbed the frontier cannot repeat it.
            already_absorbed = self._facility_frontiers.get(
                observation.facility_id
            )
            if already_absorbed is not None and _clock_relation(
                already_absorbed, frontier
            ) in ("equal", "later"):
                raise SeekConsistencyError(
                    f"seek-forward replay names facility "
                    f"{observation.facility_id!r}, which already absorbed the "
                    f"frontier tick {frontier.tick} "
                    f"({frontier.sim_time_ns} ns); a same-pad repeat is not "
                    "a next closed stage"
                )
            if observation.facility_id not in self._facility_frontiers:
                raise NativeParcelRuntimeError(
                    f"observation names facility {observation.facility_id!r}; "
                    f"the slice observes only {self._pickup_facility_id!r} and "
                    f"{self._dropoff_facility_id!r} — foreign evidence is "
                    "rejected"
                )
            return
        if relation == "later":
            if observation.at.tick == frontier.tick:
                # The next stage may share the frontier TICK only with a
                # strictly greater sim_time_ns; a same-tick record with a
                # different ns is a re-stamped stage, not a next stage.
                raise SeekConsistencyError(
                    f"seek-forward replay observation at tick "
                    f"{observation.at.tick} carries sim_time_ns "
                    f"{observation.at.sim_time_ns} ns, but the restored "
                    f"frontier at that tick carries {frontier.sim_time_ns} ns; "
                    "one closed stage has exactly one authoritative "
                    "SimulationTime"
                )
            if observation.at.tick > frontier.tick + 1:
                raise SeekConsistencyError(
                    f"seek-forward replay expected closed stage tick "
                    f"{frontier.tick + 1} after the restored frontier "
                    f"{frontier.tick}, got {observation.at.tick}; a future "
                    "binding is never observed"
                )
            return
        if relation == "inconsistent":
            raise SeekConsistencyError(
                f"seek-forward replay observation at tick "
                f"{observation.at.tick} ({observation.at.sim_time_ns} ns) is "
                f"clock-inconsistent with the restored frontier tick "
                f"{frontier.tick} ({frontier.sim_time_ns} ns); the declared "
                "simulation time must increase with tick"
            )
        raise SeekConsistencyError(
            f"seek-forward replay expected the next closed stage after the "
            f"restored frontier tick {frontier.tick} "
            f"({frontier.sim_time_ns} ns), got tick {observation.at.tick} "
            f"({observation.at.sim_time_ns} ns); a backward stage is never "
            "observed"
        )
