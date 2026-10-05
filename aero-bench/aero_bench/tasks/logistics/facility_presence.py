"""Bounded single-sample grounded-presence eligibility kernel for a facility pad.

The future logistics runtime may only treat pickup / hub handoff / delivery /
charging as grounded when real PX4 telemetry places the aircraft on a concrete
facility pad.  This module is the narrow, pure decision used by that runtime
(and by an independent re-checker): it compares *one* measured aircraft sample
— already converted by the caller into the explicit ``scene_east_south_m``
frame — against one :class:`FacilityLandingPad` materialized by
:mod:`aero_bench.tasks.logistics.facility_geometry`.

What the kernel needs from the actual PX4/Gazebo runtime
-----------------------------------------------------------

The real provider evidence shapes (``containers/px4-gazebo/service.py`` and
``aero_bench/providers/px4_gazebo/observations.py``) carry the raw facts this
kernel consumes, but never inside this module:

* ``aero-bench.state-sample/v1`` trajectory records publish ``pose`` with
  ``position.enu {east_m, north_m, up_m}`` plus NED/ENU velocity, ``contacts``
  (``ground.*`` / ``launch_pad.*``), and explicit ``ground_contact``,
  ``in_air``, ``landed``, ``landed_state`` attributes at ``at
  {tick, sim_time_ns}``.
* The agent-visible ``FlightTelemetryObservation`` carries the fused WGS84
  position, NED velocity, ``armed``, ``in_air``, ``landed`` (validated equal
  to ``landed_state == "ON_GROUND"``) and health flags at one closed provider
  barrier.

The selected scene frame is ``scene_east_south_m`` (x east, y up, z south).
The ENU telemetry frame is ``(east, north, up)``, so an ENU *vector* (position
offset, velocity, or yaw axis) maps to the scene as ``(x=east, y=up,
z=-north)``.  A future logistics runtime hook must perform that conversion
itself, binding the run's ENU origin the same way the scene authoring does;
this kernel never sees WGS84, never converts, and never fabricates a scene
pose or a velocity axis.

Measured motion (full 3D scene velocity, never horizontal-only ground speed)
-----------------------------------------------------------------------------

``MeasuredAircraftSample`` carries three required finite velocity components in
the scene axes — ``velocity_east_m_s`` (x), ``velocity_up_m_s`` (y) and
``velocity_south_m_s`` (z) — with no defaults.  A missing axis is a hard
validation error, never a silent zero: the caller must convert real
``velocity_ned``/``velocity_enu`` telemetry into the scene frame and pass every
axis.  The stationary gate validates the true 3D speed
``sqrt(vx^2 + vy^2 + vz^2)`` against ``max_stationary_speed_m_s``, so a body
rising vertically at 3 m/s through a pad — ``velocity_east_m_s =
velocity_south_m_s = 0`` — is rejected exactly like the same magnitude in a
horizontal channel.  There is no horizontal-only ``ground_speed`` gate.

Explicit measured body yaw and body-local footprint
-----------------------------------------------------

``AircraftPresenceProfile.body_width_m`` / ``body_depth_m`` are the airframe
*footprint dimensions in the body's own local axes* (width along the body-local
X axis, depth along the body-local Z axis).  ``MeasuredAircraftSample.body_yaw_deg``
is the *measured* body yaw in the scene frame, under the same Y-axis rotation
convention the facility authoring uses for ``FacilityLandingPad.rotation_deg``
(right-handed, east-positive).  The relative rotation to the pad is
``delta = body_yaw_deg - pad.rotation_deg``, and the body box is projected
conservatively onto the pad-local axes as the axis-aligned half extents

    half_x = abs(cos(delta)) * body_width_m/2 + abs(sin(delta)) * body_depth_m/2
    half_z = abs(sin(delta)) * body_width_m/2 + abs(cos(delta)) * body_depth_m/2

so an arbitrarily yawed body is explicitly assessed instead of silently trusted
as "already pad-local".  The kernel never invents a yaw, never assumes the body
is aligned with the pad, and never infers a pose offset from body height.

Horizontal uncertainty is conservative clearance, never pad enlargement
-------------------------------------------------------------------------

``PresenceTolerances.horizontal_uncertainty_m`` is the caller-declared bound on
the *error of the measured body-centre position*.  The kernel stays
conservative by shrinking the usable pad boundary: the body centre may be up to
``half_pad - (projected half extent) - horizontal_uncertainty_m`` away from the
pad centre.  Algebraically this is exactly

    abs(local_x_m) + pad_local_body_half_x_m + horizontal_uncertainty_m
        <= half_pad_x

Zero means the declared geometry is treated as exact.  The uncertainty is added
to the *body-occupancy* side, so it can only make an assessment stricter: an
oversized body is never rescued by a larger declared uncertainty
(``half_body + uncertainty`` grows monotonically), and the physical pad
rectangle is never enlarged.

Pose-reference contract (why contact height is never guessed)
--------------------------------------------------------------

The real Gazebo model pose / MAVSDK estimate is not proven to be the airframe
body centre at half body height.  The frontend dispatch estimator
(``city-selected-dispatch-route.ts``) lifts route points by
``aircraftBody.yM / 2`` for *planning* only; that is not measured PX4 pose
reference semantics.  This kernel therefore **requires** the caller to declare
``pose_reference_above_contact_m`` — the fixed height of the measured pose
reference above the pad contact surface when the airframe is resting on the
pad.  Half body height is never assumed.  If the pose-reference convention of
an airframe is exactly “body centre”, a caller may declare ``body_height/2``
explicitly; the kernel only ever uses the declared number.

Provenance distinction
------------------------

The assessed sample carries a caller-declared
``provenance: "caller_supplied" | "sealed_artifact_declared"`` label.  The
kernel echoes the label but always reports ``provenance_verified = False``:
this pure module performs no cryptographic seal verification and cannot prove
where a sample came from.  Proving sealed provenance is the independent
Verifier's job, never this kernel's.

Assessment identity (a hash record, never a seal)
---------------------------------------------------

``assessment_id`` is a plain SHA-256 over the canonical JSON encoding of the
complete input models — ``pad``, ``sample``, ``profile``, ``event`` and
``tolerances`` — plus the assessed result (verdict and derived geometry).  That
includes every pad coordinate/dimension/rotation, the expected run/provider/
aircraft/time/evidence context, the measured pose, velocity axes and yaw, the
profile footprint and pose-reference contract, and every tolerance.  Changing
any single fact — even one that leaves the verdict identical — changes the id,
so two physically different pads or two distinct failed expected contexts can
never collide.  The record is a deterministic hash, not a cryptographic seal.

Single-sample scope (never dwell)
-----------------------------------

This kernel evaluates exactly one measured sample and explicitly does **not**
claim continuous dwell.  Repeated equal poses, a ``flight.land`` receipt, or a
waypoint-reached notification are not acceptable inputs (the sample type
requires measured finite pose, measured finite 3D velocity, explicit yaw, and
explicit landed/not-in-air state) and never establish presence by themselves.
Future runtime must gather timestamped multi-sample evidence to prove
stationary dwell; this module deliberately stays one-sample and reports
``dwell_asserted = False``.

Geometry scope
----------------

* The body-footprint check uses the *measured* ``body_yaw_deg`` and the
  *declared body-local* dimensions to project a conservative pad-local
  bounding box (see above); no arbitrary-yaw collision box beyond that
  projection is invented.
* ``horizontal_uncertainty_m`` is used exactly once, as conservative body
  clearance; it is never silently reused, and it never widens the pad.
* No check beyond the rotated pad rectangle geometry is fabricated: there is
  no wall, rooftop-edge, or neighbour-body collision check here.

Everything is strict: missing telemetry fields, non-finite pose/speed/yaw,
unknown frames, negative tolerances, contradictory landed/in-air flags, stale
or cross-run/cross-aircraft samples, and wrong evidence references fail loudly.
"""

from __future__ import annotations

import hashlib
import math
from typing import Literal, TypeAlias

from pydantic import field_validator, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facilities import FacilityIdentifier
from aero_bench.tasks.logistics.facility_geometry import (
    PAD_FRAME,
    FacilityLandingPad,
    PadFrame,
    PadIndex,
    SCENE_EAST_SOUTH_M,
)

#: The only frame this kernel compares in: the accepted selected scene frame.
PresenceFrame: TypeAlias = PadFrame

#: The canonical scene frame reference echoed by every input and result.
PRESENCE_FRAME = PAD_FRAME

#: How the caller declares the origin of a measured sample.  ``sealed_artifact_declared``
#: is still a caller statement here: this kernel never verifies a cryptographic seal.
ProvenanceBasis: TypeAlias = Literal["caller_supplied", "sealed_artifact_declared"]

FailureCode: TypeAlias = Literal[
    "frame_mismatch",
    "identity_time_binding_mismatch",
    "aircraft_not_landed",
    "excessive_speed",
    "body_outside_pad",
    "contact_height_mismatch",
]

#: Assessment identity hashing follows the canonical JSON encoding used by the
#: accepted pure logistics modules (e.g. ``airspace_events``).  The resulting
#: ``assessment_id`` is a deterministic hash record, never a cryptographic seal.
_PROVENANCE_NOTE = (
    "this kernel never verifies cryptographic seals; the assessed sample "
    "provenance is caller-supplied and is not a provenance proof"
)

#: Failure attribute -> explicit failure code, in inspection order.  The first
#: failed check names the ``failure_code`` carried by an ineligible assessment.
_CHECK_CODES: tuple[tuple[str, FailureCode], ...] = (
    ("frame_ok", "frame_mismatch"),
    ("binding_ok", "identity_time_binding_mismatch"),
    ("landed_state_ok", "aircraft_not_landed"),
    ("speed_ok", "excessive_speed"),
    ("horizontal_clearance_ok", "body_outside_pad"),
    ("contact_height_ok", "contact_height_mismatch"),
)


def _reject_bool(value: object, label: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{label} cannot be a boolean")


def _finite(value: object, label: str) -> float:
    _reject_bool(value, label)
    if not isinstance(value, (int, float)):
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


class AircraftPresenceProfile(StrictModel):
    """Declared airframe body-local footprint and pose-reference contract.

    ``body_width_m`` is the declared body footprint extent along the *body-local*
    X axis and ``body_depth_m`` along the *body-local* Z axis.  The body's
    rotation in the scene is a *measured* number carried by the sample
    (``MeasuredAircraftSample.body_yaw_deg``); this kernel projects the body
    box into pad-local axes and never assumes a body is pad-aligned.  The
    measured pose reference is the horizontal centre of that footprint.  The
    vertical contract is ``pose_reference_above_contact_m``: the fixed height
    of the measured pose reference above the pad contact surface when the
    airframe is resting on the pad.  This value is required and never guessed
    from body height.
    """

    aircraft_id: Identifier
    body_width_m: float
    body_depth_m: float
    pose_reference_above_contact_m: float

    @field_validator("body_width_m", "body_depth_m", mode="before")
    @classmethod
    def body_footprint_positive(cls, value: object, info) -> float:
        return _positive(value, info.field_name)

    @field_validator("pose_reference_above_contact_m", mode="before")
    @classmethod
    def reference_height_nonnegative(cls, value: object) -> float:
        return _nonnegative(value, "pose_reference_above_contact_m")


class PresenceEventBinding(StrictModel):
    """The expected event context a validated sample must bind to.

    ``run_id``, ``provider_id``, ``aircraft_id``, the exact
    :class:`SimulationTime` tick and ``evidence_ref`` are the caller's declared
    expectations for this runtime event.  A sample that does not match every
    one of them exactly is rejected as stale, cross-run, or wrong-aircraft;
    there is no time tolerance.
    """

    run_id: Sha256
    provider_id: Identifier
    aircraft_id: Identifier
    at: SimulationTime
    evidence_ref: Identifier


class MeasuredAircraftSample(StrictModel):
    """One caller-supplied, already scene-frame-converted measured sample.

    ``frame`` must be exactly ``scene_east_south_m``; the caller performs the
    ENU -> scene mapping (``x=east, y=up, z=-north``) before constructing this
    model.  ``body_yaw_deg`` is the *measured* body yaw about the scene Y axis,
    under the same right-handed, east-positive rotation convention used by
    ``FacilityLandingPad.rotation_deg`` (ENU yaw to this scene yaw is the
    caller's explicit step).  The three ``velocity_*_m_s`` components are the
    measured finite scene-axis velocity, all required and never defaulted:
    there is no horizontal-only ground-speed gate, so a purely vertical rise at
    3 m/s is declarable and is rejected by the 3D speed magnitude check.
    ``landed`` and ``not-in-air`` are explicit fields with no default; a
    contradictory state (``landed`` with ``in_air``) never establishes
    presence.  ``provenance`` is a caller statement, never verified here.
    """

    sample_ref: Identifier
    run_id: Sha256
    provider_id: Identifier
    aircraft_id: Identifier
    at: SimulationTime
    frame: PresenceFrame
    x: float
    y: float
    z: float
    body_yaw_deg: float
    velocity_east_m_s: float
    velocity_up_m_s: float
    velocity_south_m_s: float
    landed: bool
    in_air: bool
    provenance: ProvenanceBasis
    evidence_ref: Identifier

    @field_validator("x", "y", "z", "body_yaw_deg", mode="before")
    @classmethod
    def pose_and_yaw_finite(cls, value: object, info) -> float:
        return _finite(value, info.field_name)

    @field_validator(
        "velocity_east_m_s", "velocity_up_m_s", "velocity_south_m_s", mode="before"
    )
    @classmethod
    def velocity_components_finite(cls, value: object, info) -> float:
        # Velocity components are signed (motion may point either way along an
        # axis); only finiteness is required.
        return _finite(value, info.field_name)

    @model_validator(mode="after")
    def sample_state_is_explicit(self) -> "MeasuredAircraftSample":
        if not isinstance(self.landed, bool) or not isinstance(self.in_air, bool):
            raise ValueError("landed and in_air must be explicit booleans")
        if self.frame != PAD_FRAME:
            raise ValueError("sample frame must be the accepted scene_east_south_m frame")
        return self


class PresenceTolerances(StrictModel):
    """Declared finite nonnegative tolerances; there are no silent defaults.

    ``vertical_tolerance_m`` bounds the contact-height agreement.
    ``horizontal_uncertainty_m`` is the declared bound on the measured body
    centre position error; it is applied *conservatively* as body clearance
    (``abs(local centre) + projected half extent + uncertainty <= half pad``)
    and is never used to enlarge the pad.  Zero means the declared geometry is
    treated as exact.  ``max_stationary_speed_m_s`` is the 3D speed-magnitude
    threshold a landed airframe must not exceed.
    """

    vertical_tolerance_m: float
    horizontal_uncertainty_m: float
    max_stationary_speed_m_s: float

    @field_validator(
        "vertical_tolerance_m", "horizontal_uncertainty_m", mode="before"
    )
    @classmethod
    def geometry_tolerance_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)

    @field_validator("max_stationary_speed_m_s", mode="before")
    @classmethod
    def speed_tolerance_nonnegative(cls, value: object) -> float:
        return _nonnegative(value, "max_stationary_speed_m_s")


class FacilityPresenceAssessment(StrictModel):
    """Deterministic single-sample presence verdict for one pad.

    ``eligible`` is ``True`` only when every check passes: frame agreement,
    exact identity/time/evidence binding, explicit landed/not-in-air state,
    finite 3D measured speed within the declared stationary threshold, body
    footprint clearance inside the rotated pad rectangle (measured yaw and
    conservative horizontal uncertainty included), and contact-height agreement
    under the declared pose-reference offset and tolerance.  An ineligible
    assessment carries the first failed check as ``failure_code``.
    """

    assessment_id: Sha256
    eligible: bool
    failure_code: FailureCode | None = None
    facility_id: FacilityIdentifier
    pad_index: PadIndex
    aircraft_id: Identifier
    run_id: Sha256
    provider_id: Identifier
    at: SimulationTime
    evidence_ref: Identifier
    sample_ref: Identifier
    frame: PresenceFrame
    local_x_m: float
    local_z_m: float
    body_yaw_deg: float
    pad_local_body_half_x_m: float
    pad_local_body_half_z_m: float
    contact_error_m: float
    velocity_east_m_s: float
    velocity_up_m_s: float
    velocity_south_m_s: float
    speed_magnitude_m_s: float
    frame_ok: bool
    binding_ok: bool
    landed_state_ok: bool
    speed_ok: bool
    horizontal_clearance_ok: bool
    contact_height_ok: bool
    single_sample_evaluated: Literal[1] = 1
    dwell_asserted: Literal[False] = False
    provenance_declared: ProvenanceBasis
    provenance_verified: Literal[False] = False
    provenance_note: str

    @field_validator(
        "local_x_m",
        "local_z_m",
        "contact_error_m",
        "body_yaw_deg",
        "velocity_east_m_s",
        "velocity_up_m_s",
        "velocity_south_m_s",
        mode="before",
    )
    @classmethod
    def assessed_geometry_and_motion_finite(cls, value: object, info) -> float:
        return _finite(value, info.field_name)

    @field_validator(
        "pad_local_body_half_x_m",
        "pad_local_body_half_z_m",
        "speed_magnitude_m_s",
        mode="before",
    )
    @classmethod
    def assessed_extents_nonnegative(cls, value: object, info) -> float:
        return _nonnegative(value, info.field_name)

    @model_validator(mode="after")
    def assessment_is_consistent(self) -> "FacilityPresenceAssessment":
        if self.single_sample_evaluated != 1:
            raise ValueError("an assessment always evaluates exactly one sample")
        if self.dwell_asserted is not False:
            raise ValueError("a single-sample assessment cannot assert continuous dwell")
        if self.provenance_verified is not False:
            raise ValueError("this kernel never verifies seal provenance")
        if self.provenance_note != _PROVENANCE_NOTE:
            raise ValueError("provenance note must be canonical")
        if self.frame != PAD_FRAME:
            raise ValueError("assessment frame must be scene_east_south_m")
        checks = tuple(bool(getattr(self, name)) for name, _ in _CHECK_CODES)
        if self.eligible:
            if any(not ok for ok in checks) or self.failure_code is not None:
                raise ValueError("eligible assessment cannot contain a failed check")
            return self
        first_failed = next(
            (
                code
                for (_name, code), ok in zip(_CHECK_CODES, checks)
                if not ok
            ),
            None,
        )
        if first_failed is None:
            raise ValueError("ineligible assessment has no failed check")
        if self.failure_code != first_failed:
            raise ValueError("failure_code must name the first failed check")
        return self


def _pad_local_components(
    pad: FacilityLandingPad, sample_x: float, sample_z: float
) -> tuple[float, float]:
    """Project a scene point into the pad-local XZ frame.

    The canonical facility rotation maps local ``(px, pz)`` to scene offsets
    ``dx = cos a * px + sin a * pz`` and ``dz = -sin a * px + cos a * pz``
    (see ``facility_landing_pads``); the inverse used here is the same rotation
    transposed, evaluated with no hidden epsilon.
    """
    angle = math.radians(pad.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    dx = sample_x - pad.x
    dz = sample_z - pad.z
    return cosine * dx - sine * dz, sine * dx + cosine * dz


def _pad_local_body_half_extents(
    *,
    body_width_m: float,
    body_depth_m: float,
    body_yaw_deg: float,
    pad_rotation_deg: float,
) -> tuple[float, float]:
    """Project the body-local footprint onto pad-local axes.

    ``delta = body_yaw_deg - pad_rotation_deg`` is the body's rotation relative
    to the pad under the shared scene Y-rotation convention.  A body-local
    point ``(bx, bz)`` maps to pad-local ``(px, pz)`` with
    ``px = cos(delta) * bx + sin(delta) * bz`` and
    ``pz = -sin(delta) * bx + cos(delta) * bz``, so the conservative
    axis-aligned half extents of the body box in pad-local axes are the
    abs(cos/sin)-weighted sums required by the assessed geometry contract.
    """
    delta = math.radians(body_yaw_deg - pad_rotation_deg)
    cosine = abs(math.cos(delta))
    sine = abs(math.sin(delta))
    half_width = body_width_m / 2.0
    half_depth = body_depth_m / 2.0
    return cosine * half_width + sine * half_depth, sine * half_width + cosine * half_depth


def _speed_magnitude_m_s(
    velocity_east_m_s: float,
    velocity_up_m_s: float,
    velocity_south_m_s: float,
) -> float:
    return math.sqrt(
        velocity_east_m_s * velocity_east_m_s
        + velocity_up_m_s * velocity_up_m_s
        + velocity_south_m_s * velocity_south_m_s
    )


def _assessment_id(facts: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(facts)).hexdigest()


def assess_facility_presence(
    *,
    pad: FacilityLandingPad,
    sample: MeasuredAircraftSample,
    profile: AircraftPresenceProfile,
    event: PresenceEventBinding,
    tolerances: PresenceTolerances,
) -> FacilityPresenceAssessment:
    """Compare one scene-frame-converted measured sample with one landing pad.

    The verdict never proves provenance and never claims dwell.  A caller whose
    sample does not bind exactly to ``event`` (run/provider/aircraft/evidence
    and tick plus sim-time) receives an ineligible ``identity_time_binding_mismatch``
    verdict; stale, cross-run, or wrong-aircraft samples are never accepted.

    Motion: the three scene-axis velocity components give one true 3D speed
    magnitude, gated by ``max_stationary_speed_m_s`` (vertical-only motion is
    fully declarable and is rejected).

    Geometry: the measured ``x``/``z`` are projected into the pad-local frame
    with the pad's canonical rotation.  The body's *measured* yaw and *declared
    body-local* footprint are projected to conservative pad-local half extents,
    and the body centre plus those half extents plus the declared
    ``horizontal_uncertainty_m`` (conservative clearance) must fit inside the
    actual pad rectangle — the pad is never enlarged.  The measured ``y`` must
    agree with ``pad.y + profile.pose_reference_above_contact_m`` within the
    declared vertical tolerance.
    """
    local_x_m, local_z_m = _pad_local_components(pad, sample.x, sample.z)
    half_pad_x = pad.width_m / 2.0
    half_pad_z = pad.depth_m / 2.0
    pad_local_body_half_x_m, pad_local_body_half_z_m = _pad_local_body_half_extents(
        body_width_m=profile.body_width_m,
        body_depth_m=profile.body_depth_m,
        body_yaw_deg=sample.body_yaw_deg,
        pad_rotation_deg=pad.rotation_deg,
    )
    horizontal_clearance_ok = bool(
        abs(local_x_m) + pad_local_body_half_x_m + tolerances.horizontal_uncertainty_m
        <= half_pad_x
        and abs(local_z_m)
        + pad_local_body_half_z_m
        + tolerances.horizontal_uncertainty_m
        <= half_pad_z
    )
    contact_error_m = sample.y - (pad.y + profile.pose_reference_above_contact_m)
    contact_height_ok = abs(contact_error_m) <= tolerances.vertical_tolerance_m
    speed_magnitude_m_s = _speed_magnitude_m_s(
        sample.velocity_east_m_s,
        sample.velocity_up_m_s,
        sample.velocity_south_m_s,
    )
    speed_ok = speed_magnitude_m_s <= tolerances.max_stationary_speed_m_s
    landed_state_ok = bool(sample.landed and not sample.in_air)
    frame_ok = bool(sample.frame == pad.frame == SCENE_EAST_SOUTH_M)
    binding_ok = bool(
        sample.run_id == event.run_id
        and sample.provider_id == event.provider_id
        and sample.aircraft_id == event.aircraft_id
        and sample.aircraft_id == profile.aircraft_id
        and sample.evidence_ref == event.evidence_ref
        and sample.at.tick == event.at.tick
        and sample.at.sim_time_ns == event.at.sim_time_ns
    )
    checks = (
        frame_ok,
        binding_ok,
        landed_state_ok,
        speed_ok,
        horizontal_clearance_ok,
        contact_height_ok,
    )
    first_failed_code: FailureCode | None = None
    for (_name, code), ok in zip(_CHECK_CODES, checks):
        if not ok:
            first_failed_code = code
            break
    eligible = first_failed_code is None
    facts: dict[str, object] = {
        # Complete canonical input models; every verdict-determining fact is
        # hashed, including all pad geometry (x/y/z, width/depth/rotation,
        # facility/pad identity), the full measured sample (pose, yaw, 3D
        # velocity, landed state, provenance), the body profile, the expected
        # event context, and every tolerance.
        "pad": pad.model_dump(mode="json"),
        "sample": sample.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json"),
        "event": event.model_dump(mode="json"),
        "tolerances": tolerances.model_dump(mode="json"),
        "result": {
            "eligible": eligible,
            "failure_code": first_failed_code,
            "local_x_m": local_x_m,
            "local_z_m": local_z_m,
            "pad_local_body_half_x_m": pad_local_body_half_x_m,
            "pad_local_body_half_z_m": pad_local_body_half_z_m,
            "speed_magnitude_m_s": speed_magnitude_m_s,
            "contact_error_m": contact_error_m,
        },
    }
    return FacilityPresenceAssessment(
        assessment_id=_assessment_id(facts),
        eligible=eligible,
        failure_code=first_failed_code,
        facility_id=pad.facility_id,
        pad_index=pad.pad_index,
        aircraft_id=sample.aircraft_id,
        run_id=sample.run_id,
        provider_id=sample.provider_id,
        at=sample.at,
        evidence_ref=sample.evidence_ref,
        sample_ref=sample.sample_ref,
        frame=PAD_FRAME,
        local_x_m=local_x_m,
        local_z_m=local_z_m,
        body_yaw_deg=sample.body_yaw_deg,
        pad_local_body_half_x_m=pad_local_body_half_x_m,
        pad_local_body_half_z_m=pad_local_body_half_z_m,
        contact_error_m=contact_error_m,
        velocity_east_m_s=sample.velocity_east_m_s,
        velocity_up_m_s=sample.velocity_up_m_s,
        velocity_south_m_s=sample.velocity_south_m_s,
        speed_magnitude_m_s=speed_magnitude_m_s,
        frame_ok=frame_ok,
        binding_ok=binding_ok,
        landed_state_ok=landed_state_ok,
        speed_ok=speed_ok,
        horizontal_clearance_ok=horizontal_clearance_ok,
        contact_height_ok=contact_height_ok,
        provenance_declared=sample.provenance,
        provenance_note=_PROVENANCE_NOTE,
    )


class FacilityPresenceError(RuntimeError):
    """Raised when a measured sample cannot establish grounded pad presence."""


def require_grounded_presence(
    *,
    pad: FacilityLandingPad,
    sample: MeasuredAircraftSample,
    profile: AircraftPresenceProfile,
    event: PresenceEventBinding,
    tolerances: PresenceTolerances,
) -> FacilityPresenceAssessment:
    """Like :func:`assess_facility_presence` but raises on an ineligible verdict.

    A thin pure escalation for the future runtime hook: it never registers a
    runtime, never emits telemetry, and never verifies seals.
    """
    assessment = assess_facility_presence(
        pad=pad,
        sample=sample,
        profile=profile,
        event=event,
        tolerances=tolerances,
    )
    if not assessment.eligible:
        raise FacilityPresenceError(
            f"{assessment.provider_id} aircraft {assessment.aircraft_id} is not "
            f"grounded on pad {assessment.pad_index} of facility "
            f"{assessment.facility_id}: {assessment.failure_code}"
        )
    return assessment


__all__ = [
    "AircraftPresenceProfile",
    "FacilityPresenceAssessment",
    "FacilityPresenceError",
    "FailureCode",
    "MeasuredAircraftSample",
    "PRESENCE_FRAME",
    "PresenceEventBinding",
    "PresenceFrame",
    "PresenceTolerances",
    "ProvenanceBasis",
    "assess_facility_presence",
    "require_grounded_presence",
]
