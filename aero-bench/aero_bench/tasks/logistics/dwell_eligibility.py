"""Deterministic pure dwell/transition eligibility kernel for one pad.

This module is the bounded Phase 4 kernel that decides whether a *contiguous
run* of already-journaled closed-stage physical observations qualifies the
declared pickup / hub handoff / delivery / charge-start transition for business
success.  It consumes only accepted contracts from
:mod:`aero_bench.tasks.logistics.observation_ingress`,
:mod:`aero_bench.tasks.logistics.physical_observations`,
:mod:`aero_bench.tasks.logistics.facility_presence`,
:mod:`aero_bench.tasks.logistics.contracts` and the immutable package.  It
implements no Provider, no RuntimeHook, no executor wiring, and no resolver
branch; a separate worker owns executor world staging.

What the kernel requires for one declared transition
-----------------------------------------------------

* **Exact declared binding.** Every observation in the window must bind the
  declared ``run_id``, flight ``provider_id``, authored ``aircraft_id``,
  canonical ``facility_id`` and ``pad_index`` exactly.  The declared facility
  must be a canonical package facility, the declared pad must be the exact
  canonical landing pad geometry, and the authored aircraft must be an expanded
  fleet unit whose fleet/asset identity matches the window.
* **Valid pose/body/clearance assessment.** Every observation's recorded
  ``FacilityPresenceAssessment`` must be eligible with every check true
  (frame, identity/time/evidence binding, explicit landed/not-in-air state, 3D
  speed gate, rotated-body horizontal clearance and contact-height agreement).
  The kernel *re-computes* each assessment with the canonical declared body
  profile from the immutable package and the declared presence tolerances, so a
  forged or substituted body profile can never be laundered through a
  self-consistent observation digest.
* **No collision.** Every observation must record ``collision_contact is
  False``; a colliding airframe never becomes dwell evidence.
* **Ordered barrier/tick sequence.** The supplied window order is
  authoritative: observations must be strictly increasing in
  ``(tick, sim_time_ns)`` (a reordered evidence set is rejected, never
  re-sorted), every observation's motion stage-barrier digest and SceneState
  digest must be unique for its closed stage, and each successive observation
  must be the *next contiguous tick* (a missing tick is a gap, never padding).
* **Minimum dwell duration.** The window must contain at least two distinct
  closed-stage observations and the elapsed authoritative simulation time
  ``(end.sim_time_ns - start.sim_time_ns)`` must reach the declared
  ``minimum_dwell_s``.
* **Verified provenance.** Business success requires an explicit
  :class:`VerifiedDwellProvenance` minted by the *independently declared
  verifier* (``package.verifier_id``) with ``provenance_verified=True``,
  ``evidence_source="independent_sealed_evidence"``, a real (non-placeholder)
  seal digest and a window digest that binds exactly this ordered observation
  window.  The current logistics observation ingress only ever produces
  ``provenance_verified=False`` caller-supplied records
  (:class:`~aero_bench.tasks.logistics.physical_observations.FacilityPadPhysicalObservation`
  forces the literal ``False``), so the ingress can never be laundered into
  business success: an observation record, a journal slice, or any
  caller-supplied object is rejected as unverified evidence with the exact
  genuine-provenance prerequisite.

Scope and contract failures
---------------------------

The kernel is a pure eligibility verdict : it returns a
:class:`DwellEligibilityAssessment` for every evidence/config condition.  It
never advances an order, never mints a business receipt, never registers a
runtime hook and never makes the Business Provider pickup/handoff/delivery/
charge succeed.  Missing or forged evidence is rejected explicitly with a
concrete failure code; there are no fallback or compatibility branches.
"""

from __future__ import annotations

import hashlib
import math
from typing import Literal, TypeAlias

from pydantic import field_validator, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import LogisticsTaskPackage
from aero_bench.tasks.logistics.facilities import (
    FacilityCapabilities,
    FacilityIdentifier,
)
from aero_bench.tasks.logistics.facility_geometry import (
    FacilityLandingPad,
    PadIndex,
    facility_landing_pads,
)
from aero_bench.tasks.logistics.facility_presence import (
    AircraftPresenceProfile,
    PresenceTolerances,
    assess_facility_presence,
)
from aero_bench.tasks.logistics.observation_ingress import (
    ObservationJournal,
    ObservationJournalRecord,
)
from aero_bench.tasks.logistics.orders import (
    LogisticsIdentifier,
    OrderRequest,
)
from aero_bench.tasks.logistics.physical_observations import (
    DeclaredAircraftPoseReference,
    FacilityPadPhysicalObservation,
    observation_digest_value,
)

DWELL_TRANSITION_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-dwell-transition/v1"
] = "aero-bench.logistics-dwell-transition/v1"
DWELL_PROVENANCE_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-dwell-provenance/v1"
] = "aero-bench.logistics-dwell-provenance/v1"
DWELL_ELIGIBILITY_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-dwell-eligibility/v1"
] = "aero-bench.logistics-dwell-eligibility/v1"

#: The four physical stage transitions the kernel can make eligible.  Notice it
#: only evaluates *eligibility*; it never performs the business transition.
DwellStageKind: TypeAlias = Literal[
    "pickup", "handoff", "delivery", "charge_start"
]

#: The only provenance source that can satisfy the kernel.  ``caller_supplied``
#: or any business/ingress-derived label is never a verified provenance.
VerifiedEvidenceSource: TypeAlias = Literal["independent_sealed_evidence"]

DwellFailureCode: TypeAlias = Literal[
    "empty_window",
    "run_mismatch",
    "provider_mismatch",
    "scenario_mismatch",
    "aircraft_mismatch",
    "facility_mismatch",
    "pad_mismatch",
    "evidence_laundering",
    "forged_evidence",
    "unknown_facility",
    "facility_kind_mismatch",
    "order_mismatch",
    "profile_forged",
    "assessment_ineligible",
    "collision",
    "reordered_time",
    "barrier_out_of_order",
    "tick_gap",
    "insufficient_dwell",
    "missing_provenance",
    "unverified_provenance",
    "wrong_verifier",
    "forged_provenance",
]

DwellProvenanceFailure: TypeAlias = Literal[
    "missing_provenance",
    "unverified_provenance",
    "wrong_verifier",
    "forged_provenance",
]

#: Canonical text the kernel reports whenever genuine verified provenance is
#: missing, unverified or forged.  The exact prerequisite is a sealed, verified
#: evidence binding produced by the *independent* declared verifier, never by
#: the observation ingress (which is structurally ``provenance_verified=False``).
VERIFIED_PROVENANCE_PREREQUISITE = (
    "genuine verified provenance requires the independently declared verifier "
    "(package verifier_id) to certify a cryptographic seal over the sealed "
    "closed-stage source evidence for the exact run, binding every observation's "
    "SceneState digest, motion stage-barrier digest and evidence SHA-256 to the "
    "declared run/aircraft/facility/pad window and recording "
    "provenance_verified=True with evidence_source=independent_sealed_evidence. "
    "The logistics observation ingress only ever produces caller-supplied "
    "provenance_verified=False records and can never satisfy this prerequisite."
)

#: Canonical verification note carried by a :class:`VerifiedDwellProvenance`.
DWELL_PROVENANCE_NOTE = (
    "the independent verifier certifies a cryptographic seal over the sealed "
    "closed-stage source evidence and binds the exact dwell observation window "
    "digest for the declared run/aircraft/facility/pad; provenance_verified=True "
    "is recorded only for that independently verified outcome"
)

#: Ordered check -> failure-code map.  The first failed check (in this order) is
#: the deterministic ``failure_code`` of an ineligible assessment, exactly like
#: the accepted single-sample presence kernel.
_CHECK_CODES: tuple[tuple[str, DwellFailureCode], ...] = (
    ("window_ok", "empty_window"),
    ("run_ok", "run_mismatch"),
    ("provider_ok", "provider_mismatch"),
    ("scenario_ok", "scenario_mismatch"),
    ("aircraft_ok", "aircraft_mismatch"),
    ("facility_ok", "facility_mismatch"),
    ("pad_ok", "pad_mismatch"),
    ("ingress_ok", "evidence_laundering"),
    ("record_digest_ok", "forged_evidence"),
    ("canonical_facility_ok", "unknown_facility"),
    ("canonical_aircraft_ok", "aircraft_mismatch"),
    ("canonical_pad_ok", "pad_mismatch"),
    ("facility_kind_ok", "facility_kind_mismatch"),
    ("order_ok", "order_mismatch"),
    ("profile_ok", "profile_forged"),
    ("assessment_ok", "assessment_ineligible"),
    ("collision_ok", "collision"),
    ("recompute_ok", "assessment_ineligible"),
    ("sequence_ok", "reordered_time"),
    ("barrier_sequence_ok", "barrier_out_of_order"),
    ("contiguity_ok", "tick_gap"),
    ("dwell_ok", "insufficient_dwell"),
)


class DwellEligibilityError(ValueError):
    """A strict dwell-eligibility kernel input violation (not an evidence verdict)."""


def _finite_nonnegative(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return numeric


def _finite_positive(value: object, label: str) -> float:
    numeric = _finite_nonnegative(value, label)
    if numeric <= 0:
        raise ValueError(f"{label} must be positive")
    return numeric


# ------------------------------------------------------------------ declared


class DeclaredDwellTransition(StrictModel):
    """One explicit pickup / handoff / delivery / charge-start declaration.

    ``run_id``, ``provider_id`` (the flight provider that closed the motion
    stages), ``aircraft_id`` (authored logistics identity), ``facility_id`` and
    ``pad_index`` are the exact bindings every observation must match.
    ``minimum_dwell_s`` is the required continuous grounded dwell duration in
    simulation seconds.  ``order_id`` is required for the three order stages and
    forbidden for ``charge_start`` (charging is facility-bound, never
    parcel-bound).
    """

    schema_version: Literal["aero-bench.logistics-dwell-transition/v1"]
    kind: DwellStageKind
    run_id: Sha256
    provider_id: Identifier
    aircraft_id: LogisticsIdentifier
    facility_id: FacilityIdentifier
    pad_index: PadIndex
    order_id: LogisticsIdentifier | None = None
    minimum_dwell_s: float

    @field_validator("minimum_dwell_s", mode="before")
    @classmethod
    def minimum_dwell_is_numbers(cls, value: object) -> float:
        return _finite_nonnegative(value, "minimum_dwell_s")

    @model_validator(mode="after")
    def transition_is_consistent(self) -> "DeclaredDwellTransition":
        if self.kind == "charge_start":
            if self.order_id is not None:
                raise ValueError(
                    "charge_start cannot carry an order_id; charging is a "
                    "facility-bound transition"
                )
        elif self.order_id is None:
            raise ValueError(f"{self.kind} requires an order_id")
        return self


# ------------------------------------------------------------------- verified


class VerifiedDwellProvenance(StrictModel):
    """The only provenance that can satisfy the dwell kernel.

    This record is minted by the *independently declared verifier*
    (``package.verifier_id``), never by the observation ingress or the business
    provider.  ``provenance_verified`` is structurally ``True`` here, exactly
    opposite to :class:`FacilityPadPhysicalObservation` whose
    ``provenance_verified`` is structurally ``False`` — the two types can never
    be confused or laundered into one another.  ``window_digest`` binds the
    exact ordered dwell observation window (SHA-256 over the ordered immutable
    observation digests), ``seal_digest`` is the verifier's real cryptographic
    seal digest, and ``evidence_source`` must be the strict
    ``independent_sealed_evidence`` source.
    """

    schema_version: Literal["aero-bench.logistics-dwell-provenance/v1"]
    run_id: Sha256
    verifier_id: Identifier
    evidence_source: VerifiedEvidenceSource
    provenance_verified: Literal[True] = True
    window_digest: Sha256
    seal_digest: Sha256
    verification_note: str

    @field_validator("verification_note")
    @classmethod
    def canonical_verification_note(cls, value: str) -> str:
        if value != DWELL_PROVENANCE_NOTE:
            raise ValueError("verification note must be canonical")
        return value

    @model_validator(mode="after")
    def seal_and_window_are_real_digests(self) -> "VerifiedDwellProvenance":
        if self.window_digest == "0" * 64:
            raise ValueError("verified window_digest cannot be a placeholder")
        if self.seal_digest == "0" * 64:
            raise ValueError("verified seal_digest cannot be a placeholder")
        return self


# ------------------------------------------------------------------ assessment


class DwellEligibilityAssessment(StrictModel):
    """Deterministic pure dwell/transition eligibility verdict.

    ``eligible`` is ``True`` only when every check passes: non-empty window,
    exact declared run/provider/scenario/aircraft/facility/pad binding, no
    evidence laundering (every observation ``provenance_verified is False``),
    intact observation record digests, canonical package facility/aircraft/pad
    binding, facility-kind and order-contract agreement, canonical declared body
    profile, recorded eligible pose/body/clearance assessment, no collision,
    successful canonical-profile recomputation, ordered barrier/tick sequence,
    contiguous ticks, minimum dwell duration and an authentic
    :class:`VerifiedDwellProvenance` from the independently declared verifier.
    An ineligible verdict carries the first failed check as ``failure_code``.
    """

    schema_version: Literal["aero-bench.logistics-dwell-eligibility/v1"]
    eligible: bool
    failure_code: DwellFailureCode | None = None
    provenance_failure: DwellProvenanceFailure | None = None
    kind: DwellStageKind
    run_id: Sha256
    provider_id: Identifier
    aircraft_id: LogisticsIdentifier
    facility_id: FacilityIdentifier
    pad_index: PadIndex
    order_id: LogisticsIdentifier | None = None
    minimum_dwell_s: float
    window_size: int
    window_digest: Sha256
    window_start: SimulationTime | None = None
    window_end: SimulationTime | None = None
    dwell_seconds: float | None = None
    window_ok: bool
    run_ok: bool
    provider_ok: bool
    scenario_ok: bool
    aircraft_ok: bool
    facility_ok: bool
    pad_ok: bool
    ingress_ok: bool
    record_digest_ok: bool
    canonical_facility_ok: bool
    canonical_aircraft_ok: bool
    canonical_pad_ok: bool
    facility_kind_ok: bool
    order_ok: bool
    profile_ok: bool
    assessment_ok: bool
    collision_ok: bool
    recompute_ok: bool
    sequence_ok: bool
    barrier_sequence_ok: bool
    contiguity_ok: bool
    dwell_ok: bool
    provenance_ok: bool
    provenance_verified: bool
    verifier_id: Identifier | None = None
    message: str

    @field_validator("window_size", mode="before")
    @classmethod
    def window_size_is_nonnegative_int(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("window_size must be a nonnegative integer")
        return value

    @field_validator("minimum_dwell_s", mode="before")
    @classmethod
    def minimum_dwell_is_nonnegative(cls, value: object) -> float:
        return _finite_nonnegative(value, "minimum_dwell_s")

    @field_validator("dwell_seconds", mode="before")
    @classmethod
    def dwell_seconds_is_nonnegative_or_none(cls, value: object) -> float | None:
        if value is None:
            return None
        return _finite_nonnegative(value, "dwell_seconds")

    @field_validator("message")
    @classmethod
    def message_is_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("message must be non-empty")
        return value

    @model_validator(mode="after")
    def assessment_is_consistent(self) -> "DwellEligibilityAssessment":
        checks = {name: bool(getattr(self, name)) for name, _ in _CHECK_CODES}
        derived_failure: DwellFailureCode | None = None
        for name, code in _CHECK_CODES:
            if not checks[name]:
                derived_failure = code
                break
        if derived_failure is None and self.provenance_failure is not None:
            derived_failure = self.provenance_failure
        if self.eligible != (derived_failure is None):
            raise ValueError("eligible flag does not match the failed checks")
        if self.failure_code != derived_failure:
            raise ValueError("failure_code must name the first failed check")
        if self.provenance_ok != (self.provenance_failure is None):
            raise ValueError("provenance_ok must match the provenance failure")
        if self.provenance_verified != self.provenance_ok:
            raise ValueError(
                "provenance_verified must be True exactly when provenance is ok"
            )
        if not self.provenance_ok and self.verifier_id is not None:
            raise ValueError("an unproven assessment cannot carry a verifier_id")
        if self.provenance_ok and self.verifier_id is None:
            raise ValueError("a proven assessment must carry the verifier_id")
        if self.window_ok != (self.window_size > 0):
            raise ValueError("window_ok must match a non-empty observation window")
        if not self.window_ok:
            if (
                self.window_start is not None
                or self.window_end is not None
                or self.dwell_seconds is not None
            ):
                raise ValueError("an empty window cannot carry time bounds")
            return self
        if (
            self.window_size < 1
            or self.window_start is None
            or self.window_end is None
            or self.dwell_seconds is None
        ):
            raise ValueError("a non-empty window must carry complete time bounds")
        start_key = (self.window_start.tick, self.window_start.sim_time_ns)
        end_key = (self.window_end.tick, self.window_end.sim_time_ns)
        if end_key < start_key:
            raise ValueError("window end precedes window start")
        elapsed_s = (end_key[1] - start_key[1]) / 1_000_000_000.0
        if not math.isclose(self.dwell_seconds, elapsed_s, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("dwell_seconds must be the window's elapsed sim time")
        return self


# ------------------------------------------------------------------- helpers


def dwell_window_digest(
    observations: tuple[FacilityPadPhysicalObservation, ...],
) -> str:
    """Deterministic window digest over the ordered immutable observation digests.

    The digest binds exactly the supplied window order: reordering the
    observations (or substituting any one record) changes the digest, so a
    :class:`VerifiedDwellProvenance` can never be relocated onto a different or
    reordered evidence window.
    """
    body = {
        "observation_digests": [
            observation.observation_digest for observation in observations
        ]
    }
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _tick_key(at: SimulationTime) -> tuple[int, int]:
    return (at.tick, at.sim_time_ns)


def collect_dwell_window(
    *,
    journal: ObservationJournal,
    aircraft_id: str,
    facility_id: str,
    pad_index: int,
) -> tuple[FacilityPadPhysicalObservation, ...]:
    """Extract the ordered dwell window for one authored aircraft/facility/pad.

    The journal is immutable and time-ordered; the per-triple subsequence is
    returned in journal-sequence order (which is strictly increasing in
    ``(tick, sim_time_ns)``).  A journal whose per-triple subsequence is not
    strictly increasing is rejected as a hard error — reordering is never
    silently repaired.
    """
    if not isinstance(journal, ObservationJournal):
        raise TypeError("collect_dwell_window requires an ObservationJournal")
    records = [
        record
        for record in journal.records
        if record.observation.aircraft_id == aircraft_id
        and record.observation.facility_id == facility_id
        and record.observation.pad_index == pad_index
    ]
    observations = tuple(record.observation for record in records)
    keys = [_tick_key(observation.at) for observation in observations]
    if keys and any(right <= left for left, right in zip(keys, keys[1:])):
        raise DwellEligibilityError(
            "journal dwell window is not strictly time-ordered; a reordered "
            "evidence set is rejected, never re-sorted"
        )
    return observations


def _canonical_facility(
    package: LogisticsTaskPackage,
    facility_id: str,
) -> FacilityCapabilities | None:
    try:
        return package.facilities.require(facility_id)
    except ValueError:
        return None


def _requested_canonical_pad(
    facility: FacilityCapabilities,
    pad_index: int,
) -> FacilityLandingPad | None:
    try:
        pads = facility_landing_pads(facility)
    except ValueError:
        return None
    matches = [pad for pad in pads if pad.pad_index == pad_index]
    if len(matches) != 1:
        return None
    return matches[0]


def _canonical_profile(
    *,
    package: LogisticsTaskPackage,
    observation: FacilityPadPhysicalObservation,
    pose_reference: DeclaredAircraftPoseReference,
) -> AircraftPresenceProfile | None:
    """Reconstruct the canonical declared body profile from the immutable package.

    The presence profile's aircraft identity is the observation's native vehicle
    id (the kernel's ``Identifier`` aircraft fields); the body dimensions come
    from the package performance profile of the authored aircraft's fleet entry
    and the declared pose-reference calibration, exactly like the accepted
    physical-observation adapter does.  A forged or substituted profile can
    never match this reconstruction.
    """
    unit = next(
        (
            unit
            for unit in package.aircraft_units()
            if unit.aircraft_id == observation.aircraft_id
        ),
        None,
    )
    if unit is None:
        return None
    performance = next(
        (
            profile
            for profile in package.performance_profiles
            if profile.fleet_entry_id == unit.fleet_entry_id
        ),
        None,
    )
    if performance is None:
        return None
    body = performance.aircraft_body
    return AircraftPresenceProfile(
        aircraft_id=observation.native_vehicle_id,
        body_width_m=body.x_m,
        body_depth_m=body.z_m,
        pose_reference_above_contact_m=pose_reference.pose_reference_above_contact_m,
    )


def _facility_matches_order(
    *,
    kind: DwellStageKind,
    facility_id: str,
    order: OrderRequest,
) -> bool:
    if kind == "pickup":
        return facility_id == order.origin_facility_id
    if kind == "handoff":
        return facility_id == order.hub_handoff_facility_id
    if kind == "delivery":
        return facility_id == order.destination_facility_id
    return False


def _failure_message(
    *,
    kind: DwellStageKind,
    failure_code: DwellFailureCode,
    provenance_failure: DwellProvenanceFailure | None,
) -> str:
    if failure_code in {
        "missing_provenance",
        "unverified_provenance",
        "wrong_verifier",
        "forged_provenance",
    }:
        return (
            f"{kind} dwell eligibility rejected: {failure_code} evidence; "
            f"{VERIFIED_PROVENANCE_PREREQUISITE}"
        )
    if failure_code == "empty_window":
        return (
            f"{kind} dwell eligibility rejected: the window contains no "
            "closed-stage observations; a single sample or a missing journal "
            "never establishes dwell"
        )
    if failure_code == "insufficient_dwell":
        return (
            f"{kind} dwell eligibility rejected: the window does not reach the "
            "declared minimum dwell duration"
        )
    return f"{kind} dwell eligibility rejected: {failure_code}"


# ------------------------------------------------------------------ entrypoint


def assess_dwell_eligibility(
    *,
    transition: DeclaredDwellTransition,
    observations: tuple[FacilityPadPhysicalObservation, ...],
    package: LogisticsTaskPackage,
    pose_reference: DeclaredAircraftPoseReference,
    tolerances: PresenceTolerances,
    provenance: VerifiedDwellProvenance | object | None = None,
) -> DwellEligibilityAssessment:
    """Evaluate one declared transition against one contiguous dwell window.

    This is the pure kernel decision surface.  It never mutates the journal,
    never advances an order, never registers a runtime, and never makes the
    Business Provider pickup/handoff/delivery/charge succeed; it returns an
    explicit eligibility verdict or raises :class:`DwellEligibilityError` for a
    malformed input (wrong types, an observation that is not a physical record,
    or a pose-reference calibration naming another aircraft).
    """
    if not isinstance(transition, DeclaredDwellTransition):
        raise TypeError("dwell eligibility requires a DeclaredDwellTransition")
    if not isinstance(observations, tuple) or not all(
        isinstance(observation, FacilityPadPhysicalObservation)
        for observation in observations
    ):
        raise TypeError(
            "dwell eligibility requires an immutable tuple of physical "
            "observation records; no fallback or compatibility branch exists"
        )
    if not isinstance(package, LogisticsTaskPackage):
        raise TypeError("dwell eligibility requires a LogisticsTaskPackage")
    if not isinstance(pose_reference, DeclaredAircraftPoseReference):
        raise TypeError(
            "dwell eligibility requires the declared pose-reference calibration"
        )
    if pose_reference.aircraft_id != transition.aircraft_id:
        raise DwellEligibilityError(
            f"declared pose-reference calibration names "
            f"{pose_reference.aircraft_id!r}, not the transition aircraft "
            f"{transition.aircraft_id!r}; a missing calibrated profile is an "
            "exact error, never a default"
        )
    if not isinstance(tolerances, PresenceTolerances):
        raise TypeError("dwell eligibility requires PresenceTolerances")
    if provenance is not None and not isinstance(
        provenance, VerifiedDwellProvenance
    ):
        # An ordinary caller-supplied object (for example a physical observation
        # record whose provenance_verified is structurally False, a journal
        # slice, a query result or any other non-verified evidence) is an
        # *explicit unverified* evidence condition, not a type error: the kernel
        # reports the exact proven-provenance prerequisite.
        provenance_input: VerifiedDwellProvenance | None = None
        provenance_unverified_evidence = True
    elif provenance is None:
        provenance_input = None
        provenance_unverified_evidence = False
    else:
        provenance_input = provenance
        provenance_unverified_evidence = False

    window_size = len(observations)
    window_digest = dwell_window_digest(observations)

    # ----- binding -----------------------------------------------------------
    run_ok = all(observation.run_id == transition.run_id for observation in observations)
    provider_ok = all(
        observation.provider_id == transition.provider_id
        for observation in observations
    )
    scenario_digests = {
        observation.scenario_digest for observation in observations
    }
    scenario_ok = len(scenario_digests) == 1 and bool(scenario_digests)
    aircraft_ok = all(
        observation.aircraft_id == transition.aircraft_id
        for observation in observations
    )
    facility_ok = all(
        observation.facility_id == transition.facility_id
        for observation in observations
    )
    pad_ok = all(
        observation.pad_index == transition.pad_index
        for observation in observations
    )

    # ----- evidence soundness ------------------------------------------------
    # The current ingress is structurally provenance_verified=False.  Any
    # observation record that claims otherwise is laundered evidence.
    ingress_ok = all(
        observation.provenance_verified is False for observation in observations
    )
    # Structural integrity: every record's observation digest must match its own
    # complete content (a replaced/substituted record is caught before any
    # semantic check that could be fooled by a re-computed digest).
    record_digest_ok = all(
        observation.observation_digest == observation_digest_value(observation)
        for observation in observations
    )

    # ----- canonical package binding ------------------------------------------
    facility = _canonical_facility(package, transition.facility_id)
    canonical_facility_ok = facility is not None
    units = {
        unit.aircraft_id: unit for unit in package.aircraft_units()
    }
    unit = units.get(transition.aircraft_id)
    canonical_aircraft_ok = bool(
        unit is not None
        and all(
            observation.fleet_entry_id == unit.fleet_entry_id
            and observation.visual_asset_id == unit.visual_asset_id
            for observation in observations
        )
    )
    canonical_pad = (
        _requested_canonical_pad(facility, transition.pad_index)
        if facility is not None
        else None
    )
    canonical_pad_ok = bool(
        canonical_pad is not None
        and all(observation.pad == canonical_pad for observation in observations)
    )

    # ----- facility kind / order contract ------------------------------------
    if facility is None:
        facility_kind_ok = False
    else:
        if transition.kind == "charge_start":
            facility_kind_ok = bool(
                facility.kind == "charger" and facility.charging is not None
            )
        else:
            facility_kind_ok = bool(facility.landing is not None)
            if transition.kind == "handoff":
                facility_kind_ok = bool(facility_kind_ok and facility.kind == "hub")

    order: OrderRequest | None = None
    if transition.order_id is not None:
        order = next(
            (
                candidate
                for candidate in package.orders
                if candidate.order_id == transition.order_id
            ),
            None,
        )
    if transition.kind == "charge_start":
        order_ok = True
    else:
        order_ok = bool(
            order is not None
            and _facility_matches_order(
                kind=transition.kind,
                facility_id=transition.facility_id,
                order=order,
            )
        )

    # ----- canonical declared body profile and assessment validity ------------
    profile_ok = True
    assessment_ok = True
    collision_ok = True
    recompute_ok = True
    for observation in observations:
        canonical = _canonical_profile(
            package=package,
            observation=observation,
            pose_reference=pose_reference,
        )
        if canonical is None:
            profile_ok = False
            continue
        if observation.profile != canonical:
            profile_ok = False
        recorded = observation.assessment
        if not (
            recorded.eligible is True
            and recorded.frame_ok
            and recorded.binding_ok
            and recorded.landed_state_ok
            and recorded.speed_ok
            and recorded.horizontal_clearance_ok
            and recorded.contact_height_ok
            and recorded.failure_code is None
        ):
            assessment_ok = False
        if observation.collision_contact is not False:
            collision_ok = False
        if canonical is not None:
            recomputed = assess_facility_presence(
                pad=observation.pad,
                sample=observation.sample,
                profile=canonical,
                event=observation.event_binding,
                tolerances=tolerances,
            )
            if not recomputed.eligible:
                recompute_ok = False

    # ----- ordered barrier/tick sequence --------------------------------------
    keys = [_tick_key(observation.at) for observation in observations]
    sequence_ok = all(
        right > left for left, right in zip(keys, keys[1:])
    )
    barrier_digests = {
        observation.source_stage_barrier_digest for observation in observations
    }
    scene_digests = {
        observation.source_scene_state_digest for observation in observations
    }
    barrier_sequence_ok = bool(
        len(barrier_digests) == window_size
        and len(scene_digests) == window_size
    )
    contiguity_ok = all(
        right[0] == left[0] + 1 and right[1] > left[1]
        for left, right in zip(keys, keys[1:])
    )

    # ----- minimum dwell duration ----------------------------------------------
    if window_size >= 2:
        # The window's time span is its min/max authoritative time; a reordered
        # window has no directional span, so the bounds are the ordered extents
        # and orderedness is judged separately below (a non-increasing window is
        # rejected as ``reordered_time`` and never carries negative durations).
        ats = [observation.at for observation in observations]
        start = min(ats, key=_tick_key)
        end = max(ats, key=_tick_key)
        dwell_seconds = (end.sim_time_ns - start.sim_time_ns) / 1_000_000_000.0
        dwell_ok = dwell_seconds >= transition.minimum_dwell_s
    else:
        start = observations[0].at if window_size == 1 else None
        end = start
        dwell_seconds = 0.0 if window_size == 1 else None
        dwell_ok = False  # a dwell is a run of at least two distinct closed stages

    # ----- provenance -----------------------------------------------------------
    provenance_ok = False
    provenance_failure: DwellProvenanceFailure | None = None
    verifier_id: Identifier | None = None
    if provenance_unverified_evidence:
        provenance_failure = "unverified_provenance"
    elif provenance_input is None:
        provenance_failure = "missing_provenance"
    else:
        if provenance_input.run_id != transition.run_id:
            provenance_failure = "forged_provenance"
        elif provenance_input.verifier_id != package.verifier_id:
            provenance_failure = "wrong_verifier"
        elif provenance_input.evidence_source != "independent_sealed_evidence":
            provenance_failure = "unverified_provenance"
        elif provenance_input.provenance_verified is not True:
            provenance_failure = "unverified_provenance"
        elif provenance_input.window_digest != window_digest:
            provenance_failure = "forged_provenance"
        else:
            provenance_ok = True
            verifier_id = provenance_input.verifier_id

    # ----- deterministic verdict -------------------------------------------------
    checks: dict[str, bool] = {
        "window_ok": window_size > 0,
        "run_ok": run_ok,
        "provider_ok": provider_ok,
        "scenario_ok": scenario_ok,
        "aircraft_ok": aircraft_ok,
        "facility_ok": facility_ok,
        "pad_ok": pad_ok,
        "ingress_ok": ingress_ok,
        "record_digest_ok": record_digest_ok,
        "canonical_facility_ok": canonical_facility_ok,
        "canonical_aircraft_ok": canonical_aircraft_ok,
        "canonical_pad_ok": canonical_pad_ok,
        "facility_kind_ok": facility_kind_ok,
        "order_ok": order_ok,
        "profile_ok": profile_ok,
        "assessment_ok": assessment_ok,
        "collision_ok": collision_ok,
        "recompute_ok": recompute_ok,
        "sequence_ok": sequence_ok,
        "barrier_sequence_ok": barrier_sequence_ok,
        "contiguity_ok": contiguity_ok,
        "dwell_ok": dwell_ok,
    }
    failure_code: DwellFailureCode | None = None
    for name, code in _CHECK_CODES:
        if not checks[name]:
            failure_code = code
            break
    if failure_code is None and provenance_failure is not None:
        failure_code = provenance_failure
    eligible = failure_code is None
    message = _failure_message(
        kind=transition.kind,
        failure_code=failure_code if failure_code is not None else "eligible",
        provenance_failure=provenance_failure,
    )
    if eligible:
        message = (
            f"{transition.kind} dwell eligible: window of {window_size} contiguous "
            f"closed-stage observations "
            f"(tick {observations[0].at.tick}..{observations[-1].at.tick}) on "
            f"{transition.facility_id} pad {transition.pad_index} satisfies "
            f"binding, assessment, no-collision, ordered barrier/tick, minimum "
            f"dwell {transition.minimum_dwell_s:g} s and independent verified "
            f"provenance"
        )
    return DwellEligibilityAssessment(
        schema_version=DWELL_ELIGIBILITY_SCHEMA_VERSION,
        eligible=eligible,
        failure_code=failure_code,
        provenance_failure=provenance_failure,
        kind=transition.kind,
        run_id=transition.run_id,
        provider_id=transition.provider_id,
        aircraft_id=transition.aircraft_id,
        facility_id=transition.facility_id,
        pad_index=transition.pad_index,
        order_id=transition.order_id,
        minimum_dwell_s=transition.minimum_dwell_s,
        window_size=window_size,
        window_digest=window_digest,
        window_start=start,
        window_end=end,
        dwell_seconds=dwell_seconds,
        window_ok=checks["window_ok"],
        run_ok=checks["run_ok"],
        provider_ok=checks["provider_ok"],
        scenario_ok=checks["scenario_ok"],
        aircraft_ok=checks["aircraft_ok"],
        facility_ok=checks["facility_ok"],
        pad_ok=checks["pad_ok"],
        ingress_ok=checks["ingress_ok"],
        record_digest_ok=checks["record_digest_ok"],
        canonical_facility_ok=checks["canonical_facility_ok"],
        canonical_aircraft_ok=checks["canonical_aircraft_ok"],
        canonical_pad_ok=checks["canonical_pad_ok"],
        facility_kind_ok=checks["facility_kind_ok"],
        order_ok=checks["order_ok"],
        profile_ok=checks["profile_ok"],
        assessment_ok=checks["assessment_ok"],
        collision_ok=checks["collision_ok"],
        recompute_ok=checks["recompute_ok"],
        sequence_ok=checks["sequence_ok"],
        barrier_sequence_ok=checks["barrier_sequence_ok"],
        contiguity_ok=checks["contiguity_ok"],
        dwell_ok=checks["dwell_ok"],
        provenance_ok=provenance_ok,
        provenance_verified=provenance_ok,
        verifier_id=verifier_id,
        message=message,
    )


def journal_slice_eligibility(
    *,
    journal: ObservationJournal,
    transition: DeclaredDwellTransition,
    package: LogisticsTaskPackage,
    pose_reference: DeclaredAircraftPoseReference,
    tolerances: PresenceTolerances,
    provenance: VerifiedDwellProvenance | object | None = None,
) -> DwellEligibilityAssessment:
    """Evaluate the journaled contiguous dwell window for one declared transition.

    Thin convenience over :func:`assess_dwell_eligibility`: it extracts the
    ordered window for the declared aircraft/facility/pad from the immutable
    journal and evaluates it.  The journal is never mutated.
    """
    if not isinstance(journal, ObservationJournal):
        raise TypeError("journal dwell eligibility requires an ObservationJournal")
    observations = collect_dwell_window(
        journal=journal,
        aircraft_id=transition.aircraft_id,
        facility_id=transition.facility_id,
        pad_index=transition.pad_index,
    )
    return assess_dwell_eligibility(
        transition=transition,
        observations=observations,
        package=package,
        pose_reference=pose_reference,
        tolerances=tolerances,
        provenance=provenance,
    )


__all__ = [
    "DWELL_ELIGIBILITY_SCHEMA_VERSION",
    "DWELL_PROVENANCE_NOTE",
    "DWELL_PROVENANCE_SCHEMA_VERSION",
    "DWELL_TRANSITION_SCHEMA_VERSION",
    "DeclaredDwellTransition",
    "DwellEligibilityAssessment",
    "DwellEligibilityError",
    "DwellFailureCode",
    "DwellProvenanceFailure",
    "DwellStageKind",
    "VERIFIED_PROVENANCE_PREREQUISITE",
    "VerifiedDwellProvenance",
    "VerifiedEvidenceSource",
    "assess_dwell_eligibility",
    "collect_dwell_window",
    "dwell_window_digest",
    "journal_slice_eligibility",
]
