"""Bounded adapter from a closed PX4 motion stage to facility-pad presence.

This module is the narrow bridge between the *actual current* PX4/Gazebo
runtime evidence and the accepted pure presence kernel
(:mod:`aero_bench.tasks.logistics.facility_presence`).  It consumes only real
existing contracts and never fabricates a provider, synthetic movement, a
default landed state, a default yaw or a default velocity.

Inputs are the exact declared inputs a future logistics runtime hook and an
independent re-checker both have:

* the closed motion :class:`~aero_bench.runtime.contracts.SceneState`
  (``aero-bench.scene-state/v1``) whose samples are the run-authoritative
  ``aero-bench.state-sample/v1`` PX4/Gazebo state samples;
* the closed-stage :class:`~aero_bench.runtime.contracts.ProviderEvent` tuple
  (what ``RuntimeHook.on_stage_barriers_closed`` receives), carrying the exact
  existing ``px4.state.v1`` source events with the sealed trajectory
  ``evidence_path`` / ``evidence_sha256``;
* the supplied closed-stage :class:`~aero_bench.runtime.contracts.StageBarrier`
  tuple for the tick (the motion closure plus any later closed stages), which
  must be non-empty and contain exactly one motion barrier;
* the *caller-required* external context ``expected_run_id`` / ``target`` that
  anchors the whole batch.  The adapter never infers the expected run or tick
  from the incoming batch itself: a self-consistent batch built for a foreign
  run or a stale tick is rejected before any assessment;
* the accepted :class:`~aero_bench.tasks.logistics.contracts.LogisticsTaskPackage`
  (canonical scene binding, facility catalogue, fleet and performance
  profiles), the validated
  :class:`~aero_bench.tasks.logistics.runtime_bindings.LogisticsRuntimeBindings`
  (authored aircraft -> native provider/vehicle/agent), and the
  :class:`~aero_bench.world.resolved.ResolvedScenario` (the actual ENU origin).

What the adapter establishes
----------------------------

* Exact caller-context binding: ``expected_run_id`` and ``target`` are
  authoritative; ``scene_state.run_id/at``, the source state sample, the
  source ``px4.state.v1`` events and every supplied stage barrier are bound to
  that exact context.  Other closed stages in the hook tuple must match the
  same run/scenario/target; their contents are never assumed to prove the
  caller's run identity.
* Exact identity binding: one requested *authored* aircraft is bound through
  its explicit native binding to exactly one native vehicle, exactly one
  ``px4.state.v1`` source event, and exactly one dynamic Gazebo-physics UAV
  sample inside the closed motion stage.  Each step rejects wrong run,
  wrong provider, wrong native vehicle, wrong time, duplicate samples/events,
  stale events and cross-provider source events with a concrete message —
  never by "picking the first".
* Same-record fact agreement: the source event's ``armed`` / ``flight_mode``
  must agree with the state sample's ``armed`` / ``mode`` exactly as the
  existing landed/ground-contact/collision facts already must; a missing
  required sample field is an exact API-gap error, never a default.
* Source/frame authority: the package scene identity/provenance digest and the
  package WGS84 scene origin must agree with the actual ResolvedScenario frame
  authority (the integration resolver's exact tolerance).  ENU state is then
  mapped to the accepted scene frame ``scene_east_south_m``
  (``x=east, y=up, z=-north``; ENU yaw == scene Y-axis yaw) with no bounding
  box recentering and no arbitrary WGS84 offset.
* A deterministic, immutable observation record that carries the source
  identity/digests, the measured ENU roll/pitch/yaw, the footprint-basis flag,
  the single-sample grounded-presence assessment for one *requested* canonical
  facility pad, and a complete ``observation_digest`` over the whole record so
  any change in the full input source identity changes the digest.

What the adapter never establishes
----------------------------------

* It performs no cryptographic seal verification: ``provenance_verified`` is
  always ``False`` and the provenance label is ``caller_supplied``.  Hash-based
  identity/digest agreement is not a provenance proof.
* It assesses exactly one point-in-time sample.  No continuous dwell, no
  command receipt, no waypoint-completion, no overflight, no actual-delivery
  success and no charging amount ever becomes presence.
* The horizontal-clearance verdict is the **yaw-only** projection of the
  *declared* body footprint onto the pad-local rectangle under the measured
  ENU yaw.  It is **not** a full tilted-3D body or global collision test: the
  record's ``footprint_basis``/``clearance_note`` disclose that scope and the
  measured roll/pitch are carried in the record and its digest so downstream
  cannot mistake the result for a full-3D collision guarantee.  No new
  collision framework is added, and the NED orientation is deliberately **not**
  cross-checked for attitude (the adapter reads the ENU orientation only).
* A sample whose native ``collision_contact`` is ``True`` is **refused** with
  a :class:`PhysicalObservationError` before any record is produced; a
  colliding airframe never becomes a usable grounded-presence observation, so
  the only ``collision_contact`` value a record may carry is ``False``.
* It never registers a runtime hook, never implements a Provider, and never
  accesses user-frontend or private-Agent truth.  The body footprint is the
  package's *declared* performance profile dimensions (never labelled
  measured).  ``pose_reference_above_contact_m`` is a **required declared
  calibration input** — a per-aircraft measured-pose-reference-above-contact
  offset that this adapter does **not** measure; actual source physics may
  later verify it, but no empirical claim is made here.  When an actual public
  provider field is absent, the adapter reports an exact unsupported/API-gap
  error instead of defaulting it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Literal, TypeAlias

from pydantic import field_validator, model_validator

from aero_bench.config.models import Identifier, NamedValue, Sha256, StrictModel
from aero_bench.runtime.contracts import (
    SCENE_STATE_ROOT_DIGEST,
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import LogisticsTaskPackage
from aero_bench.tasks.logistics.facilities import FacilityIdentifier
from aero_bench.tasks.logistics.facility_geometry import (
    PAD_FRAME,
    FacilityLandingPad,
    PadFrame,
    PadIndex,
    facility_landing_pads,
)
from aero_bench.tasks.logistics.facility_presence import (
    AircraftPresenceProfile,
    FacilityPresenceAssessment,
    MeasuredAircraftSample,
    PresenceEventBinding,
    PresenceTolerances,
    assess_facility_presence,
)
from aero_bench.tasks.logistics.fleet import AssetIdentifier, FleetIdentifier
from aero_bench.tasks.logistics.orders import LogisticsIdentifier
from aero_bench.tasks.logistics.runtime_bindings import (
    LogisticsAircraftBinding,
    LogisticsRuntimeBindings,
)
from aero_bench.world.resolved import (
    ResolvedQuaternion,
    ResolvedScenario,
)

LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-physical-observation/v1"
] = "aero-bench.logistics-physical-observation/v1"

#: Exact ``payload_schema_id`` of the existing PX4 stage source event that
#: carries the sealed trajectory evidence reference and raw pose/velocity/
#: attitude/landed state.  It is the schema id the current ``px4.gazebo``
#: backend itself publishes (``Px4GazeboProvider._STATE_SCHEMA``).
PX4_STATE_PAYLOAD_SCHEMA_ID = "px4.state.v1"

#: Exact payload field inventory of that source event, mirroring
#: ``Px4GazeboProvider._REQUIRED_STATE_FIELDS``.  A source event whose public
#: inventory differs (missing or extra) is rejected; a missing required fact is
#: reported as an exact API gap, never defaulted.
PX4_STATE_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "vehicle_id",
        "pose_json",
        "position_wgs84_json",
        "velocity_json",
        "angular_velocity_json",
        "attitude_json",
        "flight_mode",
        "armed",
        "in_air",
        "landed",
        "landed_state",
        "contacts_json",
        "ground_contact",
        "battery_percent",
        "health",
        "collision_contact",
        "simulation_time_ns",
        "evidence_path",
        "evidence_sha256",
    }
)

#: The compiled ResolvedScenario frame-authority origin is the frame-math
#: round-trip of the authored origin, so the adapter compares the package scene
#: origin with the exact same tolerances the accepted logistics resolver
#: integration uses (``integration._SCENE_ORIGIN_TOLERANCE_DEG/_M``).
SCENE_ORIGIN_TOLERANCE_DEG = 1e-9
SCENE_ORIGIN_TOLERANCE_M = 1e-6

#: Source ``px4.state.v1`` events use the exact existing event id
#: ``state.{vehicle_id}.{tick}`` on the current backend.
_STATE_EVENT_ID_PREFIX = "state."

#: Canonical provenance note of every observation record.
_OBSERVATION_PROVENANCE_NOTE = (
    "the observed sample is a caller-supplied conversion of closed-harness "
    "stage evidence; identity, scene-origin and digest agreement are verified "
    "here, but no cryptographic seal provenance is claimed"
)

#: Canonical disclosure of the horizontal-clearance scope of every record.
#: The adapter (and the pure presence kernel it wraps) projects the *declared*
#: body footprint onto the pad-local rectangle using the measured ENU yaw only.
#: This is not a full tilted-3D body or global collision test, and the NED
#: orientation is deliberately not cross-checked for attitude.
CLEARANCE_SCOPE_NOTE = (
    "horizontal clearance is the yaw-only projection of the declared body "
    "footprint onto the pad-local rectangle under the measured ENU yaw; it is "
    "not a full tilted-3D body or global collision test, and the NED "
    "orientation is not cross-checked for attitude"
)

#: How the caller may declare the origin of the observed sample.  The adapter only
#: ever supplies ``caller_supplied``: digest agreement is verified but a closed
#: harness stage is not a cryptographic seal proof.
ObservedProvenance: TypeAlias = Literal["caller_supplied"]

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class PhysicalObservationError(ValueError):
    """A strict physical-observation adapter violation."""


# ------------------------------------------------------------------ primitives


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


def _require_state_field(payload: dict[str, object], name: str) -> object:
    """Return an exact existing ``px4.state.v1`` field or report the API gap."""
    try:
        return payload[name]
    except KeyError as error:
        raise PhysicalObservationError(
            f"px4.state.v1 source event lacks the required field {name!r}; the "
            "current provider contract does not expose this fact, so the "
            "observation is unsupported rather than defaulted"
        ) from error


def _event_payload(event: ProviderEvent) -> dict[str, object]:
    values: dict[str, object] = {}
    for item in event.payload:
        if item.name in values:
            raise PhysicalObservationError(
                f"source event {event.event_id!r} contains duplicate payload fields"
            )
        values[item.name] = item.value
    return values


def _parse_canonical_json_object(
    payload: dict[str, object], name: str
) -> dict[str, object]:
    value = _require_state_field(payload, name)
    if not isinstance(value, str):
        raise PhysicalObservationError(
            f"px4.state.v1 field {name!r} must be a canonical JSON object string"
        )
    try:
        imported = json.loads(value)
    except (TypeError, ValueError) as error:
        raise PhysicalObservationError(
            f"px4.state.v1 field {name!r} is invalid JSON"
        ) from error
    if not isinstance(imported, dict):
        raise PhysicalObservationError(f"px4.state.v1 field {name!r} must be an object")
    canonical = json.dumps(
        imported,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    if canonical != value:
        raise PhysicalObservationError(
            f"px4.state.v1 field {name!r} is not canonical JSON"
        )
    return imported


def _parse_canonical_json_array(
    payload: dict[str, object], name: str
) -> tuple[object, ...]:
    value = _require_state_field(payload, name)
    if not isinstance(value, str):
        raise PhysicalObservationError(
            f"px4.state.v1 field {name!r} must be a canonical JSON array string"
        )
    try:
        imported = json.loads(value)
    except (TypeError, ValueError) as error:
        raise PhysicalObservationError(
            f"px4.state.v1 field {name!r} is invalid JSON"
        ) from error
    if not isinstance(imported, list):
        raise PhysicalObservationError(f"px4.state.v1 field {name!r} must be an array")
    canonical = json.dumps(
        imported,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    if canonical != value:
        raise PhysicalObservationError(
            f"px4.state.v1 field {name!r} is not canonical JSON"
        )
    return tuple(imported)


def _close(left: float, right: float, *, abs_tol: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=abs_tol)


def scene_position_from_enu(
    *,
    east_m: float,
    north_m: float,
    up_m: float,
) -> tuple[float, float, float]:
    """Map one ENU position vector to the accepted ``scene_east_south_m`` frame.

    The selected scene frame is ``(x=east, y=up, z=-north)``.  Every component
    is required and finite; there is no default axis and no WGS84 offset.
    """
    east = _finite(east_m, "east_m")
    north = _finite(north_m, "north_m")
    up = _finite(up_m, "up_m")
    return east, up, -north


def scene_velocity_from_enu(
    *,
    east_mps: float,
    north_mps: float,
    up_mps: float,
) -> tuple[float, float, float]:
    """Map one ENU velocity vector to the accepted scene velocity axes."""
    east = _finite(east_mps, "east_mps")
    north = _finite(north_mps, "north_mps")
    up = _finite(up_mps, "up_mps")
    return east, up, -north


def scene_attitude_deg_from_enu_quaternion(
    orientation_enu: ResolvedQuaternion,
) -> tuple[float, float, float]:
    """Return the measured ENU roll, pitch, yaw (degrees) for one quaternion.

    The convention is the shared intrinsic ZYX rpy of the frame-math module
    (``aero_bench.world.frame_math.quaternion_to_rpy``), so roll and pitch are
    recovered alongside yaw::

        roll  = atan2(2*(w*x + y*z), 1 - 2*(x^2 + y^2))
        pitch = asin(clamp(2*(w*y - z*x)))
        yaw   = atan2(2*(w*z + x*y), 1 - 2*(y^2 + z^2))

    Only the ENU orientation is used; the NED orientation is deliberately not
    cross-checked for attitude (the adapter never performs an unverified
    NED->scene frame conversion).  The result is a measured attitude, never a
    default.
    """
    if not isinstance(orientation_enu, ResolvedQuaternion):
        raise TypeError("scene attitude conversion requires a ResolvedQuaternion")
    w = orientation_enu.qw
    x = orientation_enu.qx
    y = orientation_enu.qy
    z = orientation_enu.qz
    roll_rad = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch_rad = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw_rad = math.atan2(
        2.0 * (w * z + x * y),
        1.0 - 2.0 * (y * y + z * z),
    )
    return (
        float(math.degrees(roll_rad)),
        float(math.degrees(pitch_rad)),
        float(math.degrees(yaw_rad)),
    )


def scene_yaw_deg_from_enu_quaternion(orientation_enu: ResolvedQuaternion) -> float:
    """Extract the measured body yaw in the scene frame from the ENU quaternion.

    ``orientation_enu`` is the real ``ResolvedPose.orientation_enu`` carried by
    the ``aero-bench.state-sample/v1`` sample, materialised from the Gazebo rpy
    under the shared current frame-math convention (intrinsic ZYX) whose yaw is
    a rotation about the ENU up axis measured from east toward north.  The
    accepted scene yaw uses the same right-handed, east-positive Y-axis
    convention as ``FacilityLandingPad.rotation_deg``, so the ENU yaw maps to
    the scene yaw directly:

        yaw_rad = atan2(2*(w*z + x*y), 1 - 2*(y^2 + z^2))

    The result is a measured scene ``body_yaw_deg``, never a default.
    """
    return scene_attitude_deg_from_enu_quaternion(orientation_enu)[2]


# ---------------------------------------------------------------- typed records


class SourceFrameOrigin(StrictModel):
    """The actual resolved scenario ENU origin the scene conversion is bound to.

    ``frame_id`` and ``scene_frame`` are closed literals.  The three coordinates
    are the exact ResolvedFrameAuthority WGS84 origin the package scene binding
    must agree with; no arbitrary WGS84 offset is ever applied.
    """

    frame_id: Literal["WGS84"] = "WGS84"
    latitude_deg: float
    longitude_deg: float
    ellipsoid_height_m: float
    scene_frame: PadFrame

    @field_validator("latitude_deg", "longitude_deg", "ellipsoid_height_m", mode="before")
    @classmethod
    def finite_origin_coordinates(cls, value: object, info) -> float:
        return _finite(value, info.field_name)


class DeclaredAircraftPoseReference(StrictModel):
    """One *required declared calibration input*: the pose-reference height.

    The native PX4/Gazebo physics pose reference is not proven to be the
    airframe body centre at half body height, so the caller **must declare** the
    signed vertical displacement of the measured pose reference from the pad
    contact surface when resting. A Gazebo model root below the surface has a
    negative calibration. This adapter does **not** measure
    this offset and makes no empirical claim about it here; actual source
    physics may verify it later.  The number is never derived from the declared
    body height (``aircraft_body.y_m``), which the frontend only uses to lift
    planning route points.
    """

    aircraft_id: LogisticsIdentifier
    pose_reference_above_contact_m: float

    @field_validator("pose_reference_above_contact_m", mode="before")
    @classmethod
    def declared_offset_finite(cls, value: object) -> float:
        return _finite(value, "pose_reference_above_contact_m")


class FacilityPadPhysicalObservation(StrictModel):
    """Immutable typed result: one authored aircraft on one requested pad.

    The record carries the exact source identity/digests (run, provider, native
    vehicle, the source ``px4.state.v1`` event, sealed trajectory evidence
    refs, state-sample / scene-state / stage-barrier digests and the actual
    scenario ENU origin) together with the converted scene-frame sample, the
    declared presence profile, the expected event binding and the single-sample
    ``FacilityPresenceAssessment`` for the requested canonical ``facility_id``
    and ``pad_index``.

    ``aircraft_id`` is the *authored* logistics identity; the assessment,
    sample, profile and event binding are evaluated on the *native* vehicle
    identity (``native_vehicle_id``) the accepted kernel's ``Identifier``
    aircraft fields require.
    """

    schema_version: Literal["aero-bench.logistics-physical-observation/v1"]
    facility_id: FacilityIdentifier
    pad_index: PadIndex
    aircraft_id: LogisticsIdentifier
    fleet_entry_id: FleetIdentifier
    visual_asset_id: AssetIdentifier
    provider_id: Identifier
    native_vehicle_id: Identifier
    run_id: Sha256
    scenario_digest: Sha256
    at: SimulationTime
    frame: PadFrame
    pad: FacilityLandingPad
    sample: MeasuredAircraftSample
    profile: AircraftPresenceProfile
    event_binding: PresenceEventBinding
    assessment: FacilityPresenceAssessment
    source_event_id: Identifier
    source_state_sample_digest: Sha256
    source_scene_state_digest: Sha256
    source_stage_barrier_digest: Sha256
    source_evidence_path: str
    source_evidence_sha256: Sha256
    source_frame_origin: SourceFrameOrigin
    single_point_in_time: Literal[True] = True
    provenance_declared: ObservedProvenance = "caller_supplied"
    provenance_verified: Literal[False] = False
    provenance_note: str
    measured_roll_deg: float
    measured_pitch_deg: float
    measured_yaw_deg: float
    collision_contact: Literal[False]
    footprint_basis: Literal["yaw_only_declared_body_footprint"]
    clearance_note: str
    observation_digest: Sha256

    @field_validator("source_evidence_path")
    @classmethod
    def evidence_path_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("source_evidence_path must be non-empty")
        return value

    @field_validator("measured_roll_deg", "measured_pitch_deg", "measured_yaw_deg", mode="before")
    @classmethod
    def measured_attitude_finite(cls, value: object, info) -> float:
        return _finite(value, info.field_name)

    @model_validator(mode="after")
    def observation_is_consistent(self) -> "FacilityPadPhysicalObservation":
        if self.schema_version != LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("physical observation schema_version is invalid")
        if self.frame != PAD_FRAME:
            raise ValueError("physical observation frame must be scene_east_south_m")
        if self.single_point_in_time is not True:
            raise ValueError("a physical observation always evaluates one point in time")
        if self.provenance_verified is not False:
            raise ValueError("this adapter never verifies seal provenance")
        if self.provenance_note != _OBSERVATION_PROVENANCE_NOTE:
            raise ValueError("provenance note must be canonical")
        if self.measured_yaw_deg != self.sample.body_yaw_deg:
            raise ValueError("record measured yaw differs from the converted sample yaw")
        if self.collision_contact is not False:
            raise ValueError(
                "a colliding sample never produces a physical observation record"
            )
        if self.footprint_basis != "yaw_only_declared_body_footprint":
            raise ValueError(
                "footprint basis must be the canonical yaw-only declared footprint"
            )
        if self.clearance_note != CLEARANCE_SCOPE_NOTE:
            raise ValueError("clearance note must be canonical")
        if self.observation_digest != observation_digest_value(self):
            raise ValueError("observation_digest does not match observation content")
        if self.source_frame_origin.scene_frame != PAD_FRAME:
            raise ValueError("source frame origin must bind the scene frame")
        if self.pad.frame != PAD_FRAME:
            raise ValueError("materialised pad must use the scene frame")
        if (
            self.pad.facility_id != self.facility_id
            or self.pad.pad_index != self.pad_index
        ):
            raise ValueError("requested pad identity is inconsistent")
        if self.assessment.facility_id != self.facility_id:
            raise ValueError("assessment facility differs from the requested pad")
        if self.assessment.pad_index != self.pad_index:
            raise ValueError("assessment pad differs from the requested pad")
        if self.assessment.run_id != self.run_id:
            raise ValueError("assessment run differs from the observation run")
        if self.assessment.at != self.at:
            raise ValueError("assessment time differs from the observation time")
        if self.assessment.sample_ref != self.source_event_id:
            raise ValueError("assessment sample_ref differs from the source event")
        if self.assessment.evidence_ref != self.source_event_id:
            raise ValueError("assessment evidence_ref differs from the source event")
        if self.assessment.aircraft_id != self.native_vehicle_id:
            raise ValueError("assessment native aircraft differs from the bound vehicle")
        if self.sample.run_id != self.run_id:
            raise ValueError("sample run_id differs from the observation run")
        if self.sample.at != self.at:
            raise ValueError("sample time differs from the observation time")
        if self.sample.provider_id != self.provider_id:
            raise ValueError("sample provider differs from the bound provider")
        if self.sample.aircraft_id != self.native_vehicle_id:
            raise ValueError("sample aircraft differs from the bound vehicle")
        if self.sample.sample_ref != self.source_event_id:
            raise ValueError("sample_ref differs from the source event identity")
        if self.sample.evidence_ref != self.source_event_id:
            raise ValueError("evidence_ref differs from the source event identity")
        if self.profile.aircraft_id != self.native_vehicle_id:
            raise ValueError("presence profile aircraft differs from the bound vehicle")
        if self.profile.body_width_m <= 0 or self.profile.body_depth_m <= 0:
            raise ValueError("presence profile body footprint must be positive")
        for binding_field, expected in (
            ("run_id", self.run_id),
            ("provider_id", self.provider_id),
            ("aircraft_id", self.native_vehicle_id),
            ("at", self.at),
            ("evidence_ref", self.source_event_id),
        ):
            if getattr(self.event_binding, binding_field) != expected:
                raise ValueError(
                    f"event binding {binding_field} differs from the observed context"
                )
        return self


def observation_digest_value(observation: FacilityPadPhysicalObservation) -> str:
    """Deterministic SHA-256 over the complete observation record.

    The digest covers every field of the canonical record except the digest
    itself: source identity (run, provider, native vehicle, source event,
    state-sample / scene-state / stage-barrier digests, evidence references,
    frame origin), the measured attitude, the footprint-basis flag, the
    declared presence profile, the converted sample, the expected event binding
    and the single-sample assessment.  Changing any one full input source
    identity fact changes the digest.  The digest is a deterministic hash
    record, never a cryptographic seal.
    """
    return hashlib.sha256(
        canonical_json_bytes(
            observation.model_dump(mode="json", exclude={"observation_digest"})
        )
    ).hexdigest()


# ------------------------------------------------------------- validation steps


def _validate_scene_origin(
    *,
    package: LogisticsTaskPackage,
    scenario: ResolvedScenario,
) -> SourceFrameOrigin:
    if not isinstance(package, LogisticsTaskPackage):
        raise TypeError("physical observation requires a LogisticsTaskPackage")
    if not isinstance(scenario, ResolvedScenario):
        raise TypeError("physical observation requires a ResolvedScenario")
    if package.scene.coordinate_frame != PAD_FRAME:
        raise PhysicalObservationError(
            "logistics package must bind the accepted scene_east_south_m frame"
        )
    if package.scene.scene_id != scenario.world_id:
        raise PhysicalObservationError(
            f"logistics scene_id {package.scene.scene_id!r} does not match the "
            f"ResolvedScenario world_id {scenario.world_id!r}"
        )
    if package.scene.scene_source_sha256 != scenario.source_asset_digest:
        raise PhysicalObservationError(
            "logistics scene source digest does not match the ResolvedScenario "
            "world asset provenance"
        )
    origin = scenario.frame_authority.origin.wgs84
    if not _close(
        origin.longitude_deg,
        package.scene.origin_longitude_deg,
        abs_tol=SCENE_ORIGIN_TOLERANCE_DEG,
    ) or not _close(
        origin.latitude_deg,
        package.scene.origin_latitude_deg,
        abs_tol=SCENE_ORIGIN_TOLERANCE_DEG,
    ):
        raise PhysicalObservationError(
            "package scene WGS84 origin does not agree with the resolved "
            "scenario ENU origin; no arbitrary WGS84 offset is applied"
        )
    if not _close(
        origin.ellipsoid_height_m,
        package.scene.origin_altitude_m,
        abs_tol=SCENE_ORIGIN_TOLERANCE_M,
    ):
        raise PhysicalObservationError(
            "package scene origin altitude does not agree with the resolved "
            "scenario ENU origin; the vertical reference cannot be bounded "
            "without an arbitrary offset"
        )
    return SourceFrameOrigin(
        latitude_deg=origin.latitude_deg,
        longitude_deg=origin.longitude_deg,
        ellipsoid_height_m=origin.ellipsoid_height_m,
        scene_frame=PAD_FRAME,
    )


def _require_closed_motion_stage(
    *,
    scene_state: SceneState,
    scenario: ResolvedScenario,
    stage_barriers: tuple[StageBarrier, ...],
    expected_run_id: str,
    target: SimulationTime,
) -> StageBarrier:
    """Bind the closed stage strictly to the caller's external context.

    ``expected_run_id`` and ``target`` are authoritative and are never inferred
    from the batch itself: a fully self-consistent SceneState / barrier /
    sample / event build for a foreign run or a stale tick is rejected here,
    before any assessment.  The supplied barrier tuple must be non-empty and
    contain exactly one motion barrier whose digest is the barrier the
    SceneState was assembled around.  Every other closed stage the runtime hook
    supplies must match the same run/scenario/target; their contents are not
    assumed to prove the caller's identity.
    """
    if not isinstance(scene_state, SceneState):
        raise TypeError("physical observation requires a SceneState")
    if not stage_barriers:
        raise PhysicalObservationError(
            "the closed stage barrier tuple is empty; the caller must supply "
            "the actual motion closure (and any later closed stages) for the "
            "target tick"
        )
    for barrier in stage_barriers:
        if not isinstance(barrier, StageBarrier):
            raise TypeError("closed stage barriers must be StageBarrier records")
    if scene_state.stage_barrier.stage != "motion":
        raise PhysicalObservationError(
            f"closed stage {scene_state.stage_barrier.stage!r} is not the motion "
            "barrier; only the current closed physical motion stage is observable"
        )
    if scene_state.run_id != expected_run_id:
        raise PhysicalObservationError(
            f"SceneState run {scene_state.run_id!r} differs from the caller's "
            f"expected run {expected_run_id!r}; the closed batch belongs to a "
            "different run and is rejected before assessment"
        )
    if scene_state.at != target:
        raise PhysicalObservationError(
            f"SceneState at {scene_state.at.tick}:{scene_state.at.sim_time_ns} "
            f"differs from the caller's target {target.tick}:{target.sim_time_ns}; "
            "the closed batch is not the requested tick and is rejected before "
            "assessment"
        )
    if scene_state.scenario_digest != scenario.scenario_digest:
        raise PhysicalObservationError(
            "SceneState belongs to a different ResolvedScenario than the request"
        )
    if scene_state.stage_barrier.at != scene_state.at:
        raise PhysicalObservationError(
            "SceneState barrier time does not match the SceneState time"
        )
    for barrier in stage_barriers:
        if barrier.run_id != expected_run_id:
            raise PhysicalObservationError(
                f"closed stage barrier run {barrier.run_id!r} differs from the "
                f"caller's expected run {expected_run_id!r}"
            )
        if barrier.scenario_digest != scenario.scenario_digest:
            raise PhysicalObservationError(
                f"closed stage barrier {barrier.stage!r} scenario digest differs "
                "from the ResolvedScenario"
            )
        if barrier.at != target:
            raise PhysicalObservationError(
                f"closed stage barrier {barrier.stage!r} at "
                f"{barrier.at.tick}:{barrier.at.sim_time_ns} differs from the "
                f"caller's target {target.tick}:{target.sim_time_ns}"
            )
    motion_barriers = [
        barrier for barrier in stage_barriers if barrier.stage == "motion"
    ]
    if len(motion_barriers) != 1:
        raise PhysicalObservationError(
            "the closed stage must supply exactly one motion barrier"
        )
    source_barrier = motion_barriers[0]
    if source_barrier.barrier_digest != scene_state.stage_barrier.barrier_digest:
        raise PhysicalObservationError(
            "the supplied motion barrier is not the source barrier the "
            "SceneState was assembled around (digest mismatch); the source "
            "event closure is the wrong stage barrier for this run"
        )
    return source_barrier


def _require_aircraft_binding(
    *,
    package: LogisticsTaskPackage,
    bindings: LogisticsRuntimeBindings,
    aircraft_id: LogisticsIdentifier,
) -> LogisticsAircraftBinding:
    """Return the explicit native binding for the requested authored aircraft."""
    if not isinstance(bindings, LogisticsRuntimeBindings):
        raise TypeError("physical observation requires LogisticsRuntimeBindings")
    bound = next(
        (
            binding
            for binding in bindings.aircraft
            if binding.aircraft_id == aircraft_id
        ),
        None,
    )
    if bound is None:
        raise PhysicalObservationError(
            f"requested authored aircraft {aircraft_id!r} has no runtime binding; "
            "bindings must be validated against the environment before observation"
        )
    unit = next(
        (
            unit
            for unit in package.aircraft_units()
            if unit.aircraft_id == aircraft_id
        ),
        None,
    )
    if unit is None:
        raise PhysicalObservationError(
            f"requested authored aircraft {aircraft_id!r} is not a declared "
            "participating fleet aircraft"
        )
    if bound.fleet_entry_id != unit.fleet_entry_id:
        raise PhysicalObservationError(
            f"aircraft binding {aircraft_id!r} fleet_entry_id does not match the "
            "expanded fleet unit"
        )
    if bound.visual_asset_id != unit.visual_asset_id:
        raise PhysicalObservationError(
            f"aircraft binding {aircraft_id!r} visual_asset_id does not match the "
            "expanded fleet unit; asset identity is distinct from the native "
            "vehicle id"
        )
    return bound


def _require_resolved_native_aircraft(
    *,
    scenario: ResolvedScenario,
    binding: LogisticsAircraftBinding,
) -> None:
    entity_map = {entity.entity_id: entity for entity in scenario.entities}
    entity = entity_map.get(binding.vehicle_id)
    if entity is None:
        raise PhysicalObservationError(
            f"native vehicle {binding.vehicle_id!r} has no resolved scenario entity"
        )
    if (
        entity.kind != "uav"
        or entity.state != "dynamic"
        or entity.authority_kind != "gazebo_physics"
        or entity.owner_kind != "provider"
        or entity.owner_id != binding.provider_id
    ):
        raise PhysicalObservationError(
            f"native vehicle {binding.vehicle_id!r} is not a dynamic "
            "Gazebo-physics UAV owned by provider "
            f"{binding.provider_id!r} (cross-provider or wrong-authority binding)"
        )


def _require_native_sample(
    *,
    scene_state: SceneState,
    binding: LogisticsAircraftBinding,
    expected_run_id: str,
    target: SimulationTime,
):
    samples = tuple(
        sample
        for sample in scene_state.samples
        if sample.entity_id == binding.vehicle_id
        and sample.provider_id == binding.provider_id
    )
    if not samples:
        raise PhysicalObservationError(
            f"closed motion stage has no state sample for native vehicle "
            f"{binding.vehicle_id!r} on provider {binding.provider_id!r}"
        )
    if len(samples) > 1:
        raise PhysicalObservationError(
            f"closed motion stage carries duplicate state samples for native "
            f"vehicle {binding.vehicle_id!r} on provider {binding.provider_id!r}"
        )
    sample = samples[0]
    if sample.run_id != expected_run_id:
        raise PhysicalObservationError(
            f"state sample run {sample.run_id!r} differs from the caller's "
            f"expected run {expected_run_id!r} (cross-run sample)"
        )
    if sample.run_id != scene_state.run_id:
        raise PhysicalObservationError(
            f"state sample run {sample.run_id!r} differs from the SceneState run "
            f"{scene_state.run_id!r} (cross-run sample)"
        )
    if sample.at != target:
        raise PhysicalObservationError(
            f"state sample at {sample.at.tick}:{sample.at.sim_time_ns} differs "
            f"from the caller's target {target.tick}:{target.sim_time_ns}"
        )
    if sample.scenario_digest != scene_state.scenario_digest:
        raise PhysicalObservationError(
            "state sample scenario digest differs from the SceneState"
        )
    if sample.at != scene_state.at:
        raise PhysicalObservationError(
            f"state sample at {sample.at.tick}:{sample.at.sim_time_ns} differs "
            f"from the closed stage at "
            f"{scene_state.at.tick}:{scene_state.at.sim_time_ns}"
        )
    if sample.stage != "motion" or sample.sample_kind != "dynamic":
        raise PhysicalObservationError(
            "observed state sample is not a dynamic motion-stage sample"
        )
    if binding.provider_id not in scene_state.stage_barrier.provider_ids:
        raise PhysicalObservationError(
            f"provider {binding.provider_id!r} is absent from the closed motion "
            "barrier"
        )
    return sample


def _require_source_state_event(
    *,
    events: tuple[ProviderEvent, ...],
    binding: LogisticsAircraftBinding,
    at: SimulationTime,
) -> tuple[ProviderEvent, dict[str, object]]:
    for event in events:
        if not isinstance(event, ProviderEvent):
            raise TypeError("closed source events must be ProviderEvent records")
    state_events = [
        event
        for event in events
        if event.provider_id == binding.provider_id
        and event.payload_schema_id == PX4_STATE_PAYLOAD_SCHEMA_ID
    ]
    if not state_events:
        raise PhysicalObservationError(
            f"closed stage has no px4.state.v1 source event for provider "
            f"{binding.provider_id!r}"
        )
    # Cross-provider evidence for the same native vehicle is never silently
    # ignored: it makes the physical ownership of the observed state ambiguous.
    foreign_events = [
        event
        for event in events
        if event.payload_schema_id == PX4_STATE_PAYLOAD_SCHEMA_ID
        and event.provider_id != binding.provider_id
        and _event_payload(event).get("vehicle_id") == binding.vehicle_id
    ]
    if foreign_events:
        raise PhysicalObservationError(
            f"px4.state.v1 source data for native vehicle {binding.vehicle_id!r} "
            "is also published by another provider; the physical ownership of "
            "the observed state is ambiguous (cross-provider data is rejected)"
        )
    vehicle_events = [
        event
        for event in state_events
        if _event_payload(event).get("vehicle_id") == binding.vehicle_id
    ]
    if not vehicle_events:
        raise PhysicalObservationError(
            f"closed stage has no px4.state.v1 source event for native vehicle "
            f"{binding.vehicle_id!r} on provider {binding.provider_id!r}"
        )
    if len(vehicle_events) > 1:
        raise PhysicalObservationError(
            f"closed stage carries duplicate px4.state.v1 source events for "
            f"native vehicle {binding.vehicle_id!r}"
        )
    event = vehicle_events[0]
    if event.time != at:
        raise PhysicalObservationError(
            f"px4.state.v1 source event time {event.time.tick}:{event.time.sim_time_ns} "
            f"differs from the closed stage {at.tick}:{at.sim_time_ns}; the "
            "source event is stale or belongs to another stage closure"
        )
    if event.event_id != f"{_STATE_EVENT_ID_PREFIX}{binding.vehicle_id}.{at.tick}":
        raise PhysicalObservationError(
            f"px4.state.v1 source event id {event.event_id!r} does not match the "
            "exact state.<vehicle>.<tick> identity"
        )
    payload = _event_payload(event)
    fields = set(payload)
    missing = PX4_STATE_REQUIRED_FIELDS - fields
    extra = fields - PX4_STATE_REQUIRED_FIELDS
    if missing or extra:
        raise PhysicalObservationError(
            f"px4.state.v1 source event fields differ from the strict provider "
            f"inventory: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    if payload["vehicle_id"] != binding.vehicle_id:
        raise PhysicalObservationError(
            "px4.state.v1 source event vehicle identity mismatch"
        )
    simulation_time_ns = payload["simulation_time_ns"]
    if (
        isinstance(simulation_time_ns, bool)
        or not isinstance(simulation_time_ns, int)
        or simulation_time_ns != at.sim_time_ns
    ):
        raise PhysicalObservationError(
            "px4.state.v1 source event simulation_time_ns differs from the "
            "closed stage time"
        )
    return event, payload


def _require_state_facts(
    *,
    payload: dict[str, object],
    sample,
) -> tuple[str, Sha256]:
    """Cross-check the raw ``px4.state.v1`` event against the state sample.

    Every fact the kernel needs (measured pose, 3D velocity, measured yaw and
    an explicit landed/not-in-air state) must be present in the source event
    *and* agree with the compiled ``aero-bench.state-sample/v1`` sample; a
    missing or disagreeing field is reported as an exact unsupported/API-gap
    error, never defaulted.
    """
    self_pose = _parse_canonical_json_object(payload, "pose_json")
    _parse_canonical_json_object(payload, "position_wgs84_json")
    self_velocity = _parse_canonical_json_object(payload, "velocity_json")
    _parse_canonical_json_object(payload, "angular_velocity_json")
    self_attitude = _parse_canonical_json_object(payload, "attitude_json")
    _parse_canonical_json_object(payload, "health")
    contacts = _parse_canonical_json_array(payload, "contacts_json")
    if any(not isinstance(value, str) or not value for value in contacts) or tuple(
        contacts
    ) != tuple(sorted(set(contacts))):
        raise PhysicalObservationError(
            "px4.state.v1 contacts_json is not a canonical unique inventory"
        )

    pose_fields = {"x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad"}
    attitude_fields = {"roll_rad", "pitch_rad", "yaw_rad"}
    velocity_fields = {"north_m_s", "east_m_s", "down_m_s"}
    if set(self_pose) != pose_fields:
        raise PhysicalObservationError(
            f"px4.state.v1 pose_json fields are incomplete: "
            f"missing={sorted(pose_fields - set(self_pose))}, "
            f"extra={sorted(set(self_pose) - pose_fields)}"
        )
    if set(self_attitude) != attitude_fields:
        raise PhysicalObservationError(
            "px4.state.v1 attitude_json fields are incomplete"
        )
    if set(self_velocity) != velocity_fields:
        raise PhysicalObservationError(
            f"px4.state.v1 velocity_json fields are incomplete: "
            f"missing={sorted(velocity_fields - set(self_velocity))}"
        )
    for name, label in (
        ("x_m", "pose_json.x_m"),
        ("y_m", "pose_json.y_m"),
        ("z_m", "pose_json.z_m"),
        ("roll_rad", "pose_json.roll_rad"),
        ("pitch_rad", "pose_json.pitch_rad"),
        ("yaw_rad", "pose_json.yaw_rad"),
    ):
        _finite(self_pose[name], label)
    for name, label in (
        ("roll_rad", "attitude_json.roll_rad"),
        ("pitch_rad", "attitude_json.pitch_rad"),
        ("yaw_rad", "attitude_json.yaw_rad"),
    ):
        _finite(self_attitude[name], label)
    for name, label in (
        ("north_m_s", "velocity_json.north_m_s"),
        ("east_m_s", "velocity_json.east_m_s"),
        ("down_m_s", "velocity_json.down_m_s"),
    ):
        _finite(self_velocity[name], label)
    if any(
        not _close(self_pose[name], self_attitude[name], abs_tol=1e-12)
        for name in ("roll_rad", "pitch_rad", "yaw_rad")
    ):
        raise PhysicalObservationError(
            "px4.state.v1 pose and attitude receipt evidence disagree"
        )
    pos_enu = sample.pose.position.enu
    pos_ned = sample.pose.position.ned
    if any(
        (
            not _close(self_pose["x_m"], pos_enu.east_m, abs_tol=1e-9),
            not _close(self_pose["y_m"], pos_enu.north_m, abs_tol=1e-9),
            not _close(self_pose["z_m"], pos_enu.up_m, abs_tol=1e-9),
            not _close(pos_ned.east_m, pos_enu.east_m, abs_tol=1e-9),
            not _close(pos_ned.north_m, pos_enu.north_m, abs_tol=1e-9),
            not _close(pos_ned.down_m, -pos_enu.up_m, abs_tol=1e-9),
        )
    ):
        raise PhysicalObservationError(
            "px4.state.v1 pose evidence differs from the state sample position"
        )
    velocity_ned = sample.linear_velocity_ned
    if any(
        (
            not _close(self_velocity["north_m_s"], velocity_ned.north_mps, abs_tol=1e-9),
            not _close(self_velocity["east_m_s"], velocity_ned.east_mps, abs_tol=1e-9),
            not _close(self_velocity["down_m_s"], velocity_ned.down_mps, abs_tol=1e-9),
        )
    ):
        raise PhysicalObservationError(
            "px4.state.v1 velocity evidence differs from the state sample velocity"
        )
    measured_roll_deg, measured_pitch_deg, measured_yaw_deg = (
        scene_attitude_deg_from_enu_quaternion(sample.pose.orientation_enu)
    )
    measured_yaw_rad = math.radians(measured_yaw_deg)
    if not _close(self_pose["roll_rad"], math.radians(measured_roll_deg), abs_tol=1e-9):
        raise PhysicalObservationError(
            "px4.state.v1 roll evidence differs from the state sample orientation"
        )
    if not _close(
        self_pose["pitch_rad"], math.radians(measured_pitch_deg), abs_tol=1e-9
    ):
        raise PhysicalObservationError(
            "px4.state.v1 pitch evidence differs from the state sample orientation"
        )
    if not _close(self_pose["yaw_rad"], measured_yaw_rad, abs_tol=1e-9):
        raise PhysicalObservationError(
            "px4.state.v1 yaw evidence differs from the state sample orientation"
        )
    if not _close(
        self_attitude["roll_rad"], math.radians(measured_roll_deg), abs_tol=1e-9
    ):
        raise PhysicalObservationError(
            "px4.state.v1 attitude roll evidence differs from the state sample "
            "orientation"
        )
    if not _close(
        self_attitude["pitch_rad"], math.radians(measured_pitch_deg), abs_tol=1e-9
    ):
        raise PhysicalObservationError(
            "px4.state.v1 attitude pitch evidence differs from the state sample "
            "orientation"
        )
    if not _close(self_attitude["yaw_rad"], measured_yaw_rad, abs_tol=1e-9):
        raise PhysicalObservationError(
            "px4.state.v1 attitude yaw evidence differs from the state sample "
            "orientation"
        )

    event_landed = _require_state_field(payload, "landed")
    event_in_air = _require_state_field(payload, "in_air")
    event_ground_contact = _require_state_field(payload, "ground_contact")
    event_collision = _require_state_field(payload, "collision_contact")
    armed = _require_state_field(payload, "armed")
    for flag, label in (
        (event_landed, "landed"),
        (event_in_air, "in_air"),
        (event_ground_contact, "ground_contact"),
        (event_collision, "collision_contact"),
        (armed, "armed"),
    ):
        if not isinstance(flag, bool):
            raise PhysicalObservationError(
                f"px4.state.v1 field {label!r} must be an explicit boolean"
            )
    event_landed_state = _require_state_field(payload, "landed_state")
    if not isinstance(event_landed_state, str) or not event_landed_state:
        raise PhysicalObservationError(
            "px4.state.v1 field 'landed_state' must be a non-empty string"
        )
    flight_mode = _require_state_field(payload, "flight_mode")
    if not isinstance(flight_mode, str) or not flight_mode:
        raise PhysicalObservationError(
            "px4.state.v1 field 'flight_mode' must be a non-empty string"
        )
    battery_percent = _require_state_field(payload, "battery_percent")
    _finite(battery_percent, "battery_percent")
    # The state sample's own arming/mode facts must be present and must not
    # contradict the source event, exactly as landed/ground-contact/collision
    # are already cross-checked below.  A missing required sample field is an
    # explicit API-gap error, never a default.
    if not isinstance(sample.armed, bool):
        raise PhysicalObservationError(
            "state sample 'armed' is missing or not an explicit boolean; the "
            "current contract does not expose it, so the observation is "
            "unsupported rather than defaulted"
        )
    if not isinstance(sample.mode, str) or not sample.mode:
        raise PhysicalObservationError(
            "state sample 'mode' is missing or not a non-empty string; the "
            "current contract does not expose it, so the observation is "
            "unsupported rather than defaulted"
        )
    if sample.armed != armed:
        raise PhysicalObservationError(
            "px4.state.v1 armed differs from the state sample armed"
        )
    if sample.mode != flight_mode:
        raise PhysicalObservationError(
            "px4.state.v1 flight_mode differs from the state sample mode"
        )
    if event_landed != (event_landed_state == "ON_GROUND"):
        raise PhysicalObservationError(
            "px4.state.v1 landed differs from landed_state"
        )
    observed_ground_contact = any(
        str(value).startswith(("ground.", "launch_pad.")) for value in contacts
    )
    if event_ground_contact != observed_ground_contact:
        raise PhysicalObservationError(
            "px4.state.v1 ground_contact differs from the contact inventory"
        )
    # The event collision flag must agree with the contact inventory exactly as
    # the provider derives it: any non-ground/non-pad contact is a collision
    # contact, so a contradictory flag is same-record evidence that never
    # establishes a usable grounded sample.
    observed_collision_contact = any(
        not str(value).startswith(("ground.", "launch_pad.")) for value in contacts
    )
    if event_collision != observed_collision_contact:
        raise PhysicalObservationError(
            "px4.state.v1 collision_contact differs from the contact inventory"
        )

    attributes = {attribute.name: attribute.value for attribute in sample.attributes}
    for label in ("landed", "in_air", "ground_contact", "collision_contact"):
        from_sample = attributes.get(label)
        from_event = payload[label]
        if not isinstance(from_sample, bool):
            raise PhysicalObservationError(
                f"state sample attribute {label!r} is missing or not an explicit "
                "boolean; the current contract does not expose it"
            )
        if from_sample != from_event:
            raise PhysicalObservationError(
                f"state sample attribute {label!r} differs from the px4.state.v1 "
                "source event"
            )
    landed_state_attribute = attributes.get("landed_state")
    if not isinstance(landed_state_attribute, str) or not landed_state_attribute:
        raise PhysicalObservationError(
            "state sample attribute 'landed_state' is missing or invalid"
        )
    if landed_state_attribute != event_landed_state:
        raise PhysicalObservationError(
            "state sample landed_state differs from the px4.state.v1 source event"
        )
    if tuple(sorted(contacts)) != sample.contacts:
        raise PhysicalObservationError(
            "px4.state.v1 contact inventory differs from the state sample contacts"
        )

    evidence_path = _require_state_field(payload, "evidence_path")
    if not isinstance(evidence_path, str) or not evidence_path:
        raise PhysicalObservationError(
            "px4.state.v1 evidence_path must be a non-empty string"
        )
    evidence_sha256 = _require_state_field(payload, "evidence_sha256")
    if isinstance(evidence_sha256, bool) or not isinstance(evidence_sha256, str):
        raise PhysicalObservationError(
            "px4.state.v1 evidence_sha256 must be a SHA-256 digest"
        )
    if _SHA256_RE.fullmatch(evidence_sha256) is None:
        raise PhysicalObservationError(
            "px4.state.v1 evidence_sha256 must be a SHA-256 digest"
        )
    if evidence_sha256 == "0" * 64:
        raise PhysicalObservationError(
            "px4.state.v1 evidence_sha256 cannot be a placeholder digest"
        )
    return evidence_path, evidence_sha256


def _require_landed_state(sample) -> tuple[bool, bool]:
    attributes = {attribute.name: attribute.value for attribute in sample.attributes}
    for label in ("landed", "in_air"):
        value = attributes.get(label)
        if not isinstance(value, bool):
            raise PhysicalObservationError(
                f"state sample attribute {label!r} is missing or not an explicit "
                "boolean; the current provider contract does not expose it"
            )
    landed = bool(attributes["landed"])
    in_air = bool(attributes["in_air"])
    if landed and in_air:
        raise PhysicalObservationError(
            "state sample declares landed and in_air simultaneously; a "
            "contradictory landed state never establishes presence"
        )
    return landed, in_air


def _presence_profile(
    *,
    package: LogisticsTaskPackage,
    binding: LogisticsAircraftBinding,
    pose_reference: DeclaredAircraftPoseReference,
    aircraft_id: LogisticsIdentifier,
) -> AircraftPresenceProfile:
    if not isinstance(pose_reference, DeclaredAircraftPoseReference):
        raise TypeError("physical observation requires a declared pose-reference offset")
    if pose_reference.aircraft_id != aircraft_id:
        raise PhysicalObservationError(
            f"declared pose-reference offset names {pose_reference.aircraft_id!r}, "
            f"not the requested aircraft {aircraft_id!r}"
        )
    performance = next(
        (
            profile
            for profile in package.performance_profiles
            if profile.fleet_entry_id == binding.fleet_entry_id
        ),
        None,
    )
    if performance is None:
        raise PhysicalObservationError(
            f"no declared performance profile for fleet entry {binding.fleet_entry_id!r}"
        )
    body = performance.aircraft_body
    if body.x_m <= 0 or body.z_m <= 0:
        raise PhysicalObservationError(
            "declared aircraft body footprint must be positive"
        )
    # The body-local X extent is the footprint width and the body-local Z
    # extent the footprint depth in the accepted presence-kernel axes.  These
    # are configured/declared package dimensions, never labelled measured.
    return AircraftPresenceProfile(
        aircraft_id=binding.vehicle_id,
        body_width_m=body.x_m,
        body_depth_m=body.z_m,
        pose_reference_above_contact_m=pose_reference.pose_reference_above_contact_m,
    )


def _requested_pad(
    *,
    package: LogisticsTaskPackage,
    facility_id: FacilityIdentifier,
    pad_index: int,
) -> FacilityLandingPad:
    if isinstance(pad_index, bool) or not isinstance(pad_index, int) or pad_index < 0:
        raise PhysicalObservationError("pad_index must be a nonnegative integer")
    try:
        facility = package.facilities.require(facility_id)
    except ValueError as error:
        raise PhysicalObservationError(
            f"facility {facility_id!r} is not a canonical package facility"
        ) from error
    pads = facility_landing_pads(facility)
    matches = [pad for pad in pads if pad.pad_index == pad_index]
    if len(matches) != 1:
        raise PhysicalObservationError(
            f"facility {facility_id!r} has no canonical landing pad index {pad_index}"
        )
    pad = matches[0]
    if pad.frame != PAD_FRAME:
        raise PhysicalObservationError(
            "materialised landing pad must use the scene_east_south_m frame"
        )
    return pad


# ------------------------------------------------------------------ entrypoint


def observe_facility_pad_presence(
    *,
    scene_state: SceneState,
    events: tuple[ProviderEvent, ...],
    package: LogisticsTaskPackage,
    bindings: LogisticsRuntimeBindings,
    scenario: ResolvedScenario,
    aircraft_id: LogisticsIdentifier,
    facility_id: FacilityIdentifier,
    pad_index: int,
    pose_reference: DeclaredAircraftPoseReference,
    tolerances: PresenceTolerances,
    stage_barriers: tuple[StageBarrier, ...],
    expected_run_id: Sha256,
    target: SimulationTime,
) -> FacilityPadPhysicalObservation:
    """Convert one closed PX4 motion-stage sample and assess one pad.

    ``expected_run_id`` and ``target`` are the *required external* caller
    context; the adapter never infers run or tick from the incoming batch
    itself.  ``stage_barriers`` is the closed-stage barrier tuple the runtime
    hook supplies (non-empty, with exactly one motion barrier whose digest must
    be the barrier the ``scene_state`` was assembled around).  Every supplied
    barrier must bind the same run/scenario/target.  The source ``events`` are
    the closed-stage ProviderEvents (exactly what a future runtime hook's
    ``on_stage_barriers_closed`` receives); the same declared inputs are
    sufficient for an independent re-checker.

    Stale, missing, ambiguous, duplicate and cross-provider data is rejected; a
    required public provider field that is absent is reported as an exact API
    gap; source armed/flight_mode must agree with the state sample; and a
    native ``collision_contact=True`` sample is refused outright.  The returned
    record is immutable and carries source identity/digests, measured ENU
    roll/pitch/yaw, the yaw-only footprint-basis disclosure and the
    single-sample presence assessment; it never claims dwell, delivery success,
    charge amount, or cryptographic seal provenance.
    """
    source_frame_origin = _validate_scene_origin(
        package=package,
        scenario=scenario,
    )
    _require_closed_motion_stage(
        scene_state=scene_state,
        scenario=scenario,
        stage_barriers=stage_barriers,
        expected_run_id=expected_run_id,
        target=target,
    )
    binding = _require_aircraft_binding(
        package=package,
        bindings=bindings,
        aircraft_id=aircraft_id,
    )
    _require_resolved_native_aircraft(scenario=scenario, binding=binding)
    sample = _require_native_sample(
        scene_state=scene_state,
        binding=binding,
        expected_run_id=expected_run_id,
        target=target,
    )
    event, payload = _require_source_state_event(
        events=events,
        binding=binding,
        at=target,
    )
    evidence_path, evidence_sha256 = _require_state_facts(
        payload=payload,
        sample=sample,
    )
    landed, in_air = _require_landed_state(sample)

    attributes = {attribute.name: attribute.value for attribute in sample.attributes}
    collision_contact = attributes.get("collision_contact")
    if not isinstance(collision_contact, bool):
        raise PhysicalObservationError(
            "state sample collision_contact must be an explicit boolean"
        )
    if collision_contact:
        raise PhysicalObservationError(
            f"native vehicle {binding.vehicle_id!r} reports collision_contact=True "
            f"at {target.tick}:{target.sim_time_ns}; a colliding airframe never "
            "yields a usable grounded-presence observation, so the adapter "
            "refuses to qualify this sample"
        )

    position_enu = sample.pose.position.enu
    velocity_enu = sample.linear_velocity_enu
    velocity_ned = sample.linear_velocity_ned
    if not (
        _close(velocity_ned.north_mps, velocity_enu.north_mps, abs_tol=1e-9)
        and _close(velocity_ned.east_mps, velocity_enu.east_mps, abs_tol=1e-9)
        and _close(velocity_ned.down_mps, -velocity_enu.up_mps, abs_tol=1e-9)
    ):
        raise PhysicalObservationError(
            "state sample ENU and NED linear velocities do not describe one "
            "vector (NED vertical axis disagrees with ENU up)"
        )
    x, y, z = scene_position_from_enu(
        east_m=position_enu.east_m,
        north_m=position_enu.north_m,
        up_m=position_enu.up_m,
    )
    velocity_east_m_s, velocity_up_m_s, velocity_south_m_s = scene_velocity_from_enu(
        east_mps=velocity_enu.east_mps,
        north_mps=velocity_enu.north_mps,
        up_mps=velocity_enu.up_mps,
    )
    measured_roll_deg, measured_pitch_deg, measured_yaw_deg = (
        scene_attitude_deg_from_enu_quaternion(sample.pose.orientation_enu)
    )
    body_yaw_deg = measured_yaw_deg

    measured = MeasuredAircraftSample(
        sample_ref=event.event_id,
        run_id=scene_state.run_id,
        provider_id=binding.provider_id,
        aircraft_id=binding.vehicle_id,
        at=scene_state.at,
        frame=PAD_FRAME,
        x=x,
        y=y,
        z=z,
        body_yaw_deg=body_yaw_deg,
        velocity_east_m_s=velocity_east_m_s,
        velocity_up_m_s=velocity_up_m_s,
        velocity_south_m_s=velocity_south_m_s,
        landed=landed,
        in_air=in_air,
        provenance="caller_supplied",
        evidence_ref=event.event_id,
    )
    profile = _presence_profile(
        package=package,
        binding=binding,
        pose_reference=pose_reference,
        aircraft_id=aircraft_id,
    )
    pad = _requested_pad(
        package=package,
        facility_id=facility_id,
        pad_index=pad_index,
    )
    event_binding = PresenceEventBinding(
        run_id=scene_state.run_id,
        provider_id=binding.provider_id,
        aircraft_id=binding.vehicle_id,
        at=scene_state.at,
        evidence_ref=event.event_id,
    )
    assessment = assess_facility_presence(
        pad=pad,
        sample=measured,
        profile=profile,
        event=event_binding,
        tolerances=tolerances,
    )
    record_fields = {
        "schema_version": LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION,
        "facility_id": facility_id,
        "pad_index": pad_index,
        "aircraft_id": aircraft_id,
        "fleet_entry_id": binding.fleet_entry_id,
        "visual_asset_id": binding.visual_asset_id,
        "provider_id": binding.provider_id,
        "native_vehicle_id": binding.vehicle_id,
        "run_id": scene_state.run_id,
        "scenario_digest": scene_state.scenario_digest,
        "at": scene_state.at,
        "frame": PAD_FRAME,
        "pad": pad,
        "sample": measured,
        "profile": profile,
        "event_binding": event_binding,
        "assessment": assessment,
        "source_event_id": event.event_id,
        "source_state_sample_digest": sample.sample_digest,
        "source_scene_state_digest": scene_state.scene_state_digest,
        "source_stage_barrier_digest": scene_state.stage_barrier.barrier_digest,
        "source_evidence_path": evidence_path,
        "source_evidence_sha256": evidence_sha256,
        "source_frame_origin": source_frame_origin,
        "provenance_note": _OBSERVATION_PROVENANCE_NOTE,
        "measured_roll_deg": measured_roll_deg,
        "measured_pitch_deg": measured_pitch_deg,
        "measured_yaw_deg": measured_yaw_deg,
        "collision_contact": collision_contact,
        "footprint_basis": "yaw_only_declared_body_footprint",
        "clearance_note": CLEARANCE_SCOPE_NOTE,
    }
    candidate = FacilityPadPhysicalObservation.model_construct(
        **record_fields,
        observation_digest=SCENE_STATE_ROOT_DIGEST,
    )
    return FacilityPadPhysicalObservation(
        **record_fields,
        observation_digest=observation_digest_value(candidate),
    )


__all__ = [
    "CLEARANCE_SCOPE_NOTE",
    "DeclaredAircraftPoseReference",
    "FacilityPadPhysicalObservation",
    "LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION",
    "ObservedProvenance",
    "PX4_STATE_PAYLOAD_SCHEMA_ID",
    "PX4_STATE_REQUIRED_FIELDS",
    "SCENE_ORIGIN_TOLERANCE_DEG",
    "SCENE_ORIGIN_TOLERANCE_M",
    "PhysicalObservationError",
    "SourceFrameOrigin",
    "observe_facility_pad_presence",
    "observation_digest_value",
    "scene_attitude_deg_from_enu_quaternion",
    "scene_position_from_enu",
    "scene_velocity_from_enu",
    "scene_yaw_deg_from_enu_quaternion",
]
