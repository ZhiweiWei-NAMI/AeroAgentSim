"""Typed native one-order parcel contract (pure runtime admission slice).

This module is the *declaration surface* of the user-authorized one-order
native parcel slice (``docs/p02-native-logistics/CONTRACT.md`` +
``contract-proposal.json``).  It defines, with no defaults and no derived
values, the exact typed identities, bindings, required carriage transform,
authored dwell policy and admitted action/RX evidence shapes that
:mod:`aero_bench.tasks.logistics.native_parcel_runtime` consumes.

Scope boundaries (deliberate, until integration review)
-------------------------------------------------------

* This is a **pure runtime admission slice**.  It never mints a
  :class:`~aero_bench.tasks.logistics.dwell_eligibility.VerifiedDwellProvenance`,
  never weakens the sealed dwell kernel, never flips
  ``LOGISTICS_RUNTIME_IMPLEMENTED``, never registers a runtime hook and never
  enables a provider/service capability.  Live admitted business state is *not*
  independently verified: the separate sealed logistics verifier must replay
  the same command, observation, dwell, attachment and transfer records before
  issuing any final verdict (``runtime_seal_distinction``).
* Facility geometry, world-frame binding, pose-reference calibration and the
  parcel body-local attachment transform are **required explicit compile
  inputs**.  A missing pose, calibration or transform is an exact error, never
  a half-height or identity-rotation default.
* All quantities preserve raw units: metres, metres/second, seconds, degrees.
  Every time is the exact :class:`~aero_bench.runtime.contracts.SimulationTime`
  (tick plus sim_time_ns); wall-clock time is never simulation time.
* All motion validation reuses the accepted pure kernel
  :func:`~aero_bench.tasks.logistics.facility_presence.assess_facility_presence`
  and its :class:`~aero_bench.tasks.logistics.physical_observations.FacilityPadPhysicalObservation`
  records; this module reimplements no geometry.
* ``UNKNOWN`` source conditions (a required fact the closed stage does not
  expose) return explicit unconfirmed outcomes; malformed or foreign identity
  raises.
"""

from __future__ import annotations

import hashlib
import math
from typing import Literal, TypeAlias

from pydantic import field_validator, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facility_presence import PresenceTolerances
from aero_bench.tasks.logistics.fleet import AssetIdentifier, FleetIdentifier
from aero_bench.tasks.logistics.orders import LogisticsIdentifier
from aero_bench.world.resolved import ResolvedQuaternion

NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-native-parcel-contract/v1"
] = "aero-bench.logistics-native-parcel-contract/v1"
NATIVE_PARCEL_STATE_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-native-parcel-state/v1"
] = "aero-bench.logistics-native-parcel-state/v1"
NATIVE_PARCEL_TRANSFER_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-native-parcel-transfer/v1"
] = "aero-bench.logistics-native-parcel-transfer/v1"
NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-native-parcel-admission/v1"
] = "aero-bench.logistics-native-parcel-admission/v1"
NATIVE_PARCEL_ACTION_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-native-parcel-action/v1"
] = "aero-bench.logistics-native-parcel-action/v1"

#: The four parcel states of the one-order slice, in lifecycle order.
ParcelState: TypeAlias = Literal[
    "awaiting_pickup", "loaded", "in_transit", "delivered"
]

#: Admitted business action kinds.  ``pickup`` moves custody from the pickup
#: facility to the carrier; ``dropoff`` (the admitted handoff) moves custody
#: from the carrier to the dropoff facility.  There is no charge transition in
#: this minimal scenario.
ParcelActionKind: TypeAlias = Literal["pickup", "dropoff"]

#: Custody holder kinds.  The holder identity is always one of the declared
#: slice identities; no other holder can ever hold the parcel.
ParcelHolderKind: TypeAlias = Literal[
    "pickup_facility", "carrier", "dropoff_facility"
]

#: Declared origin of an admitted action/RX proof.  ``closed_stage_action_rx``
#: is a caller-declared typed exact input (an admitted command and its typed
#: reception evidence bound to the exact closed stage); this slice never
#: accepts a bare boolean success, a network receipt alone, or an inspection
#: report as the proof.
ActionEvidenceBasis: TypeAlias = Literal["closed_stage_action_rx"]

#: The exactly-once admission key.  A decision is recorded per
#: ``(run_id, principal_id, action_id)``; the same key with identical content
#: is the idempotent replay of one decision, and the same key with any
#: different content is an explicit payload conflict, never a second decision.
ParcelActionKey: TypeAlias = tuple[str, str, str]

#: Canonical frame disclosure of the attachment transform.  The carrier pose
#: the runtime converts is the measured scene attitude and scene position the
#: closed observation already carries; the attachment is declared in the
#: carrier **body-local** axes with the same yaw convention as
#: ``FacilityLandingPad.rotation_deg`` (scene Y-axis, east-positive).  Scene
#: mapping is ``x=east, y=up, z=-north`` (``scene_east_south_m``).  The
#: body-local set is right-handed: ``x`` forward, ``y`` up, ``z`` right, so at
#: yaw 0 the body axes align exactly with the scene axes.
ATTACHMENT_FRAME_NOTE = (
    "offset_m and orientation are declared in the carrier body-local axes "
    "(x forward/east at yaw 0, y up, z right/south at yaw 0, right-handed); "
    "the runtime rotates them by the measured scene attitude recovered from "
    "the observation record (shared intrinsic ZYX rpy) in the "
    "scene_east_south_m frame (x=east, y=up, z=-north)"
)

#: Canonical unconfirmed-outcome note.  Missing source facts stay UNKNOWN and
#: never become presence, custody, delivery or a transfer.
UNKNOWN_SOURCE_NOTE = (
    "required closed-stage source facts are missing or not assessable for this "
    "tick; the outcome is explicitly unconfirmed and no presence, custody, "
    "dwell or delivery is inferred"
)

#: Canonical sealed-verifier replay requirement.  Live admission never proves
#: provenance; the independent sealed verifier must replay everything.
SEALED_VERIFIER_REPLAY_REQUIREMENT = (
    "the independent sealed logistics verifier must replay the same command, "
    "observation, dwell, attachment and transfer records for the exact run "
    "before issuing the final verdict; live admitted business state is not "
    "independently verified and never mints VerifiedDwellProvenance"
)


class NativeParcelContractError(ValueError):
    """A strict native parcel contract violation (malformed/foreign input)."""


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _nonnegative(value: object, label: str) -> float:
    numeric = _finite(value, label)
    if numeric < 0:
        raise ValueError(f"{label} must be nonnegative")
    return numeric


def _positive(value: object, label: str) -> float:
    numeric = _finite(value, label)
    if numeric <= 0:
        raise ValueError(f"{label} must be positive")
    return numeric


def _digest(facts: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(facts)).hexdigest()


# ------------------------------------------------------------------ identities


class NativeParcelIdentities(StrictModel):
    """The exact one-order slice identities (no defaults, no inference).

    These are the authored contract identities from ``contract-proposal.json``.
    Every runtime admission re-binds them exactly; a record naming any other
    order, parcel, carrier, principal or facility is foreign and rejected.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-contract/v1"]
    task_id: Identifier
    order_id: LogisticsIdentifier
    parcel_entity_id: LogisticsIdentifier
    carrier_entity_id: LogisticsIdentifier
    #: The one declared principal authorized to act for the declared carrier
    #: on this order.  An admitted action naming any other principal is
    #: foreign: there is no alias inference, no string splitting and no
    #: fallback — the principal-to-carrier authorization binding is explicit
    #: and exact, and it is part of the identity digest.
    authorized_principal_id: Identifier
    pickup_facility_id: LogisticsIdentifier
    dropoff_facility_id: LogisticsIdentifier

    @field_validator("carrier_entity_id", "parcel_entity_id")
    @classmethod
    def entity_ids_are_distinct(cls, value: str) -> str:
        return value

    @model_validator(mode="after")
    def identities_are_distinct(self) -> "NativeParcelIdentities":
        entity_ids = (
            self.order_id,
            self.parcel_entity_id,
            self.carrier_entity_id,
            self.pickup_facility_id,
            self.dropoff_facility_id,
        )
        if len(set(entity_ids)) != len(entity_ids):
            raise ValueError(
                "native parcel identities must be pairwise distinct"
            )
        if self.pickup_facility_id == self.dropoff_facility_id:
            raise ValueError(
                "pickup and dropoff facilities must be distinct facilities"
            )
        if self.authorized_principal_id != self.carrier_entity_id:
            raise ValueError(
                "the declared authorized principal must be the declared "
                "carrier entity: the principal-to-carrier binding is exact"
            )
        return self

    def identity_digest(self) -> str:
        """Deterministic digest over the exact declared identity set."""
        return _digest(self.model_dump(mode="json"))


# ------------------------------------------------------------------- carriage


class DeclaredParcelAttachment(StrictModel):
    """The required carrier body-local carriage transform (no default).

    ``offset_m`` is the parcel reference point expressed in the carrier
    **body-local** axes ``(x, y, z)``; ``orientation`` is the parcel's
    body-local orientation relative to the carrier body frame as a unit
    quaternion.  The offset is in metres and preserves raw units.  A carriage
    declaration cannot compile without this transform: ``relative_pose`` is
    never defaulted to the carrier origin and never inferred from body height.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-contract/v1"]
    parcel_entity_id: LogisticsIdentifier
    carrier_entity_id: LogisticsIdentifier
    offset_x_m: float
    offset_y_m: float
    offset_z_m: float
    orientation: ResolvedQuaternion
    frame_note: str

    @field_validator("offset_x_m", "offset_y_m", "offset_z_m", mode="before")
    @classmethod
    def offset_finite(cls, value: object, info) -> float:
        return _finite(value, info.field_name)

    @field_validator("frame_note")
    @classmethod
    def canonical_frame_note(cls, value: str) -> str:
        if value != ATTACHMENT_FRAME_NOTE:
            raise ValueError("attachment frame_note must be canonical")
        return value

    @model_validator(mode="after")
    def attachment_binds_the_declared_entities(self) -> "DeclaredParcelAttachment":
        if self.parcel_entity_id == self.carrier_entity_id:
            raise ValueError(
                "parcel and carrier entity ids must be distinct in the carriage"
            )
        return self

    def offset_tuple(self) -> tuple[float, float, float]:
        return (self.offset_x_m, self.offset_y_m, self.offset_z_m)


class DeclaredParcelCarrier(StrictModel):
    """The one real native carrier binding and its required calibration.

    ``binding`` fields are the exact native identities the accepted runtime
    bindings validate (authored fleet unit -> provider -> native vehicle).
    ``pose_reference_above_contact_m`` is the required declared pose-reference
    calibration; it is never derived from body height and a missing value is an
    exact contract error, never a half-height default.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-contract/v1"]
    carrier_entity_id: LogisticsIdentifier
    fleet_entry_id: FleetIdentifier
    visual_asset_id: AssetIdentifier
    provider_id: Identifier
    native_vehicle_id: Identifier
    pose_reference_above_contact_m: float

    @field_validator("pose_reference_above_contact_m", mode="before")
    @classmethod
    def reference_height_finite(cls, value: object) -> float:
        # Signed, exactly as the accepted kernel's
        # AircraftPresenceProfile.reference_height_finite: a nested Gazebo
        # model root below the pad surface has a negative calibration; the
        # declared number is required and never a half-height default.
        return _finite(value, "pose_reference_above_contact_m")


class DeclaredParcelPolicy(StrictModel):
    """Authored demo admission policy (declared inputs, not measurements).

    Every value is an authored demo-policy number from the contract proposal,
    consumed exactly as declared; none is a measured hardware capability.  The
    pad footprint and pose-reference calibration remain mandatory independent
    inputs carried by the package and the carrier declaration.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-contract/v1"]
    minimum_pickup_dwell_s: float
    minimum_dropoff_dwell_s: float
    vertical_tolerance_m: float
    horizontal_uncertainty_m: float
    max_stationary_speed_m_s: float

    @field_validator(
        "minimum_pickup_dwell_s",
        "minimum_dropoff_dwell_s",
        mode="before",
    )
    @classmethod
    def dwell_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)

    @field_validator(
        "vertical_tolerance_m",
        "horizontal_uncertainty_m",
        "max_stationary_speed_m_s",
        mode="before",
    )
    @classmethod
    def tolerance_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)

    def presence_tolerances(self) -> PresenceTolerances:
        """The exact :class:`PresenceTolerances` this policy declares."""
        return PresenceTolerances(
            vertical_tolerance_m=self.vertical_tolerance_m,
            horizontal_uncertainty_m=self.horizontal_uncertainty_m,
            max_stationary_speed_m_s=self.max_stationary_speed_m_s,
        )


class NativeParcelContract(StrictModel):
    """The complete immutable contract of the one-order native parcel slice.

    Everything the runtime needs is explicit here: identities, the one real
    carrier binding with its required calibration, the required body-local
    attachment transform, the authored dwell/tolerance policy and the
    independent sealed-verifier replay requirement.  Nothing is inferred and
    nothing carries a default.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-contract/v1"]
    contract_id: Identifier
    identities: NativeParcelIdentities
    carrier: DeclaredParcelCarrier
    attachment: DeclaredParcelAttachment
    policy: DeclaredParcelPolicy
    sealed_verifier_replay_requirement: str

    @field_validator("sealed_verifier_replay_requirement")
    @classmethod
    def canonical_replay_requirement(cls, value: str) -> str:
        if value != SEALED_VERIFIER_REPLAY_REQUIREMENT:
            raise ValueError(
                "sealed_verifier_replay_requirement must be canonical"
            )
        return value

    @model_validator(mode="after")
    def contract_is_internally_bound(self) -> "NativeParcelContract":
        ids = self.identities
        if self.attachment.parcel_entity_id != ids.parcel_entity_id:
            raise ValueError(
                "attachment names a parcel entity other than the declared one"
            )
        if self.attachment.carrier_entity_id != ids.carrier_entity_id:
            raise ValueError(
                "attachment names a carrier entity other than the declared one"
            )
        if self.carrier.carrier_entity_id != ids.carrier_entity_id:
            raise ValueError(
                "carrier binding names a carrier entity other than the "
                "declared one"
            )
        if self.carrier.provider_id == ids.parcel_entity_id:
            raise ValueError(
                "carrier provider identity collides with the parcel identity"
            )
        return self

    def contract_digest(self) -> str:
        """Deterministic digest over the complete declared contract."""
        return _digest(self.model_dump(mode="json"))


# --------------------------------------------------------- admitted action/RX


class AdmittedParcelAction(StrictModel):
    """One typed admitted action and its exact RX evidence binding.

    This is the *exact typed input* the runtime admits; a bare boolean success,
    a bare command receipt, or an inspection report is never an accepted proof
    shape.  ``rx`` is the typed closed-stage reception evidence: the exact
    ``SimulationTime`` the admission was received at and the closed motion
    stage-barrier digest it is bound to.  ``evidence_basis`` is structurally
    restricted to ``closed_stage_action_rx`` so a network receipt alone can
    never satisfy a transition.

    The exactly-once admission key is ``(run_id, principal_id, action_id)``:
    re-admitting the same key with identical content is the idempotent replay
    of one decision, while the same key with any different content is an
    explicit payload conflict, never a second decision.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-action/v1"]
    run_id: Sha256
    action_id: Identifier
    kind: ParcelActionKind
    evidence_basis: ActionEvidenceBasis
    order_id: LogisticsIdentifier
    parcel_entity_id: LogisticsIdentifier
    carrier_entity_id: LogisticsIdentifier
    principal_id: Identifier
    received_at: SimulationTime
    rx_stage_barrier_digest: Sha256

    def action_digest(self) -> str:
        """Deterministic digest over the complete admitted action content."""
        return _digest(self.model_dump(mode="json"))


class ParcelActionIdentityBinding(StrictModel):
    """The exact identity binding an admitted action must carry.

    The duplicate admission key is ``(run_id, principal_id, action_id)``.  A
    duplicate key with a *different* payload is an explicit conflict; the same
    key with identical content is the idempotent replay of one decision and
    never produces a second transfer.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-action/v1"]
    run_id: Sha256
    action_id: Identifier
    kind: ParcelActionKind
    order_id: LogisticsIdentifier
    parcel_entity_id: LogisticsIdentifier
    carrier_entity_id: LogisticsIdentifier
    principal_id: Identifier


# ------------------------------------------------------------------- custody


class ParcelCustodyHolder(StrictModel):
    """One typed custody holder bound to a declared slice identity."""

    schema_version: Literal["aero-bench.logistics-native-parcel-state/v1"]
    holder_kind: ParcelHolderKind
    holder_id: LogisticsIdentifier


class ParcelCustodyTransferRecord(StrictModel):
    """One exactly-once admitted custody transfer record.

    ``transfer_id`` is the deterministic digest of the full record content, so
    two transfers can never collide and a replayed decision returns the *same*
    record.  ``kind`` is ``pickup`` (pickup facility -> carrier) or ``dropoff``
    (carrier -> dropoff facility); every record binds the dwell window digest
    and the admitted action digest that justified it.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-transfer/v1"]
    transfer_id: Sha256
    kind: ParcelActionKind
    order_id: LogisticsIdentifier
    parcel_entity_id: LogisticsIdentifier
    carrier_entity_id: LogisticsIdentifier
    from_holder: ParcelCustodyHolder
    to_holder: ParcelCustodyHolder
    at: SimulationTime
    action_id: Identifier
    action_digest: Sha256
    dwell_window_digest: Sha256

    @model_validator(mode="after")
    def transfer_is_directionally_bound(self) -> "ParcelCustodyTransferRecord":
        if self.kind == "pickup":
            expected_from, expected_to = "pickup_facility", "carrier"
        else:
            expected_from, expected_to = "carrier", "dropoff_facility"
        if self.from_holder.holder_kind != expected_from:
            raise ValueError(
                f"{self.kind} transfer from_holder kind must be {expected_from}"
            )
        if self.to_holder.holder_kind != expected_to:
            raise ValueError(
                f"{self.kind} transfer to_holder kind must be {expected_to}"
            )
        if self.from_holder.holder_id == self.to_holder.holder_id:
            raise ValueError("a custody transfer must change the holder")
        if not self.dwell_window_digest or len(self.dwell_window_digest) != 64:
            raise ValueError("transfer dwell_window_digest must be a digest")
        expected_id = self._computed_transfer_id()
        if self.transfer_id != expected_id:
            raise ValueError("transfer_id does not match the transfer content")
        return self

    def _computed_transfer_id(self) -> str:
        content = self.model_dump(mode="json", exclude={"transfer_id"})
        return hashlib.sha256(canonical_json_bytes(content)).hexdigest()

    @classmethod
    def mint(
        cls,
        *,
        kind: ParcelActionKind,
        order_id: str,
        parcel_entity_id: str,
        carrier_entity_id: str,
        from_holder: "ParcelCustodyHolder",
        to_holder: "ParcelCustodyHolder",
        at: SimulationTime,
        action_id: str,
        action_digest: str,
        dwell_window_digest: str,
    ) -> "ParcelCustodyTransferRecord":
        """Mint one exactly-once custody transfer record.

        ``transfer_id`` is the deterministic SHA-256 over the canonical content
        with the id itself excluded, so identical content mints the identical
        id (a duplicate replay returns the same record) and any content
        divergence changes the id.
        """
        fields: dict[str, object] = {
            "schema_version": NATIVE_PARCEL_TRANSFER_SCHEMA_VERSION,
            "kind": kind,
            "order_id": order_id,
            "parcel_entity_id": parcel_entity_id,
            "carrier_entity_id": carrier_entity_id,
            "from_holder": from_holder,
            "to_holder": to_holder,
            "at": at,
            "action_id": action_id,
            "action_digest": action_digest,
            "dwell_window_digest": dwell_window_digest,
        }
        candidate = cls.model_construct(**fields, transfer_id="0" * 64)
        transfer_id = candidate._computed_transfer_id()
        record = cls(**fields, transfer_id=transfer_id)
        # _computed_transfer_id is content-excluding-id, so the validated
        # record must reproduce its own id exactly (a fixed point).
        assert record.transfer_id == transfer_id
        return record


class ParcelActionKeyRecord(StrictModel):
    """One exactly-once admission key with the digest of its decided content.

    ``content_digest`` is the ``action_digest`` of the exact
    :class:`AdmittedParcelAction` decided under the key, so a later admission
    under the same ``(run_id, principal_id, action_id)`` with any different
    content is an explicit conflict even after a seek/restore.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-state/v1"]
    run_id: Sha256
    principal_id: Identifier
    action_id: Identifier
    content_digest: Sha256


class ParcelStateTransitionRecord(StrictModel):
    """One immutable parcel state transition with its admission justification."""

    schema_version: Literal["aero-bench.logistics-native-parcel-state/v1"]
    from_state: ParcelState
    to_state: ParcelState
    at: SimulationTime
    action_id: Identifier
    action_digest: Sha256
    dwell_window_digest: Sha256
    transfer_id: Sha256 | None = None


class NativeParcelState(StrictModel):
    """The append-only parcel state snapshot (exactly-once records).

    ``state`` is the current lifecycle state, ``holder`` the current custody
    holder, and ``transitions``/``transfers`` are append-only records: an
    admitted decision is recorded exactly once and a duplicate action returns
    the original decision without a second record.  ``decided_action_digests``
    are the digests of actions already decided (accepted or rejected) in
    arrival order.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-state/v1"]
    contract_id: Identifier
    run_id: Sha256
    state: ParcelState
    holder: ParcelCustodyHolder
    transitions: tuple[ParcelStateTransitionRecord, ...] = ()
    transfers: tuple[ParcelCustodyTransferRecord, ...] = ()
    decided_action_keys: tuple[ParcelActionKeyRecord, ...] = ()
    last_update_at: SimulationTime | None = None

    @model_validator(mode="after")
    def state_is_coherent(self) -> "NativeParcelState":
        if self.state == "awaiting_pickup":
            if self.holder.holder_kind != "pickup_facility":
                raise ValueError(
                    "an awaiting_pickup parcel is held by the pickup facility"
                )
            if self.transfers or self.transitions:
                raise ValueError(
                    "an awaiting_pickup parcel cannot already carry records"
                )
        if self.state == "loaded":
            if self.holder.holder_kind != "carrier":
                raise ValueError("a loaded parcel is held by the carrier")
        if self.state == "in_transit":
            if self.holder.holder_kind != "carrier":
                raise ValueError("an in_transit parcel is held by the carrier")
        if self.state == "delivered":
            if self.holder.holder_kind != "dropoff_facility":
                raise ValueError(
                    "a delivered parcel is held by the dropoff facility"
                )
        keys = self.decided_action_keys
        unique_keys = {
            (key.run_id, key.principal_id, key.action_id) for key in keys
        }
        if len(keys) != len(unique_keys):
            raise ValueError(
                "decided action keys must be unique per "
                "(run_id, principal_id, action_id)"
            )
        for key in keys:
            if key.run_id != self.run_id:
                raise ValueError(
                    f"decided action key names run {key.run_id!r}; the parcel "
                    f"state records only run {self.run_id!r}"
                )
        transfer_ids = [transfer.transfer_id for transfer in self.transfers]
        if len(transfer_ids) != len(set(transfer_ids)):
            raise ValueError("custody transfers must be exactly once")
        return self


# ------------------------------------------------------------------ admissions


class ParcelDwellAdmission(StrictModel):
    """The typed result of evaluating one contiguous closed dwell window.

    ``confirmed`` is ``True`` only when the window is a real contiguous run of
    eligible grounded closed-stage presence assessments on the declared
    facility/pad reaching the declared minimum dwell.  Any ``UNKNOWN`` source
    condition (no sample at the tick, a gapped window, a non-eligible
    assessment, an insufficient span) yields ``confirmed=False`` with the exact
    ``unconfirmed_reason``; it never fabricates presence or dwell.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-admission/v1"]
    kind: ParcelActionKind
    facility_id: LogisticsIdentifier
    pad_index: int
    run_id: Sha256
    window_size: int
    dwell_seconds: float
    minimum_dwell_s: float
    dwell_window_digest: Sha256
    confirmed: bool
    unconfirmed_reason: str | None = None

    @field_validator("dwell_seconds", "minimum_dwell_s", mode="before")
    @classmethod
    def seconds_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)

    @model_validator(mode="after")
    def admission_is_consistent(self) -> "ParcelDwellAdmission":
        if self.confirmed and self.unconfirmed_reason is not None:
            raise ValueError(
                "a confirmed dwell admission cannot carry an unconfirmed reason"
            )
        if not self.confirmed and not self.unconfirmed_reason:
            raise ValueError(
                "an unconfirmed dwell admission must name its exact reason"
            )
        return self


class ParcelAdmissionOutcome(StrictModel):
    """The typed outcome of one admitted runtime decision.

    ``status`` is exactly one of:

    * ``admitted`` -- the action identity, dwell and motion evidence all bind;
      a new transfer/state record may have been produced (or the decision is a
      duplicate replay, see ``duplicate_replay``);
    * ``unconfirmed`` -- an ``UNKNOWN`` source condition (missing/gapped dwell,
      moving/in-air carrier, absent handoff evidence); no custody or state
      change and the exact reason is carried;
    * ``rejected`` -- the evidence is present but contradicts the contract
      (wrong state, wrong facility, wrong holder, payload conflict);
      :class:`NativeParcelContractError` cases raise instead.

    A decision is never inferred from a label, a network message receipt, a
    waypoint notification or an inspection report.
    """

    schema_version: Literal["aero-bench.logistics-native-parcel-admission/v1"]
    action_id: Identifier
    action_digest: Sha256
    status: Literal["admitted", "unconfirmed", "rejected"]
    reason: str
    duplicate_replay: bool = False
    unconfirmed: bool = False
    state_before: ParcelState
    state_after: ParcelState
    holder_after: ParcelCustodyHolder
    transfer_id: Sha256 | None = None
    dwell_window_digest: Sha256 | None = None
    decision_at: SimulationTime | None = None

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "ParcelAdmissionOutcome":
        if self.status == "admitted":
            if self.unconfirmed:
                raise ValueError("an admitted outcome is never unconfirmed")
            if self.decision_at is None:
                raise ValueError(
                    "an admitted outcome carries its exact decision time"
                )
        else:
            if self.duplicate_replay or self.transfer_id is not None:
                raise ValueError(
                    "only an admitted outcome carries a replay flag or a "
                    "transfer record"
                )
            if self.state_after != self.state_before:
                raise ValueError(
                    "an unconfirmed/rejected outcome never changes the state"
                )
        return self


__all__ = [
    "ATTACHMENT_FRAME_NOTE",
    "ActionEvidenceBasis",
    "AdmittedParcelAction",
    "DeclaredParcelAttachment",
    "DeclaredParcelCarrier",
    "DeclaredParcelPolicy",
    "NATIVE_PARCEL_ADMISSION_SCHEMA_VERSION",
    "NATIVE_PARCEL_ACTION_SCHEMA_VERSION",
    "NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION",
    "NATIVE_PARCEL_STATE_SCHEMA_VERSION",
    "NATIVE_PARCEL_TRANSFER_SCHEMA_VERSION",
    "NativeParcelContract",
    "NativeParcelContractError",
    "NativeParcelIdentities",
    "NativeParcelState",
    "ParcelActionIdentityBinding",
    "ParcelActionKey",
    "ParcelActionKeyRecord",
    "ParcelActionKind",
    "ParcelAdmissionOutcome",
    "ParcelCustodyHolder",
    "ParcelCustodyTransferRecord",
    "ParcelDwellAdmission",
    "ParcelHolderKind",
    "ParcelState",
    "ParcelStateTransitionRecord",
    "SEALED_VERIFIER_REPLAY_REQUIREMENT",
    "UNKNOWN_SOURCE_NOTE",
]
