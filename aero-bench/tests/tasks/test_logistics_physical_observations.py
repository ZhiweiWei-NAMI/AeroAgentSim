"""Focused physical-observation adapter tests (WP2, owned adapter).

The adapter converts one closed PX4/Gazebo motion-stage sample — the real
current ``px4.state.v1`` source events plus the compiled
``aero-bench.scene-state/v1`` snapshot — into the accepted presence kernel's
scene-frame inputs and returns an immutable observation/result carrying source
identity/digests, the measured ENU roll/pitch/yaw, the yaw-only footprint-basis
disclosure and the single-sample presence assessment for one requested
canonical facility pad.

These tests construct the closed stage with the *real current* event/sample
constructors (``ProviderEvent``, ``StateSample``, ``StageBarrier``,
``SceneState``) and synthetic unit fixture values, exactly as the runtime
provides them; they are not formal runtime evidence.  Rejection tests cover a
foreign expected run, stale/older target batches, a wrong target tick and
sim-time separately, empty stage barriers, a wrong motion-barrier digest,
wrong run/provider/vehicle/time/frame, missing velocity/yaw/landed/armed/mode
fields, duplicate and stale events, and a cross-provider source event.  The
presence kernel is never modified; only the adapter's strict binding and
conversions are asserted.
"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from aero_bench.config.models import NamedValue
from aero_bench.runtime.contracts import (
    SCENE_STATE_ROOT_DIGEST,
    BodyAngularVelocity,
    EnuLinearVelocity,
    NedLinearVelocity,
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    StageReceipt,
    StateAttribute,
    StateSample,
    scene_state_digest_value,
    stage_barrier_digest_value,
    stage_receipt_digest_value,
    state_sample_digest_value,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import lower_logistics_task_package
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import PresenceTolerances
from aero_bench.tasks.logistics.physical_observations import (
    CLEARANCE_SCOPE_NOTE,
    DeclaredAircraftPoseReference,
    FacilityPadPhysicalObservation,
    LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION,
    PhysicalObservationError,
    observe_facility_pad_presence,
    observation_digest_value,
    scene_position_from_enu,
    scene_velocity_from_enu,
    scene_yaw_deg_from_enu_quaternion,
)
from aero_bench.tasks.logistics.runtime_bindings import (
    validate_logistics_runtime_bindings,
)
from aero_bench.world.frame_math import rpy_to_quaternion
from aero_bench.world.resolved import (
    ResolvedCoordinate,
    ResolvedEcefPosition,
    ResolvedEnuPosition,
    ResolvedNedPosition,
    ResolvedPose,
    ResolvedQuaternion,
    ResolvedWgs84Position,
)
from tests.tasks.test_logistics_runtime_bindings import (
    _build_runtime_fixture,
    _facilities,
    _package_document,
    _standard_agents,
    _valid_bindings,
    _vertiport,
)

RUN_ID = "a" * 64
AT = SimulationTime(tick=1, sim_time_ns=1_000_000_000)

_POSE_FIELDS = ("x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad")


def json_loads(value: str) -> dict[str, object]:
    return json.loads(value)


def _canonical(value: dict[str, object] | list[object]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _enquaternion(yaw_deg: float) -> ResolvedQuaternion:
    """Build the real ENU orientation quaternion for a yaw (ZYX intrinsic)."""
    half = math.radians(yaw_deg) / 2.0
    return ResolvedQuaternion(
        qw=math.cos(half),
        qx=0.0,
        qy=0.0,
        qz=math.sin(half),
    )


def _state_sample(
    target: SimulationTime,
    *,
    run_id: str,
    scenario_digest: str,
    vehicle_id: str = "uav.alpha",
    provider_id: str = "flight",
    east_m: float,
    north_m: float,
    up_m: float,
    yaw_deg: float,
    east_mps: float = 0.0,
    north_mps: float = 0.0,
    up_mps: float = 0.0,
    landed: bool,
    in_air: bool,
    landed_state: str,
    ground_contact: bool,
    contacts: tuple[str, ...] = (),
    collision_contact: bool = False,
    armed: bool | None = True,
    mode: str | None = "GUIDED",
) -> StateSample:
    """A genuine ``aero-bench.state-sample/v1`` dynamic motion sample."""
    coordinate = ResolvedCoordinate(
        enu=ResolvedEnuPosition(east_m=east_m, north_m=north_m, up_m=up_m),
        ned=ResolvedNedPosition(north_m=north_m, east_m=east_m, down_m=-up_m),
        ecef=ResolvedEcefPosition(x_m=0.0, y_m=0.0, z_m=0.0),
        wgs84=ResolvedWgs84Position(
            longitude_deg=116.397,
            latitude_deg=39.916,
            ellipsoid_height_m=43.0 + up_m,
        ),
        geoid_separation_m=31.0,
        amsl_m=43.0 + up_m,
        terrain_amsl_m=43.0,
        agl_m=up_m,
    )
    pose = ResolvedPose(
        position=coordinate,
        orientation_enu=_enquaternion(yaw_deg),
        orientation_ned=_enquaternion(yaw_deg),
    )
    attributes = (
        StateAttribute(
            name="collision_contact", value_type="bool", value=collision_contact
        ),
        StateAttribute(name="contacts_complete", value_type="bool", value=True),
        StateAttribute(
            name="ground_contact", value_type="bool", value=ground_contact
        ),
        StateAttribute(name="in_air", value_type="bool", value=in_air),
        StateAttribute(name="landed", value_type="bool", value=landed),
        StateAttribute(
            name="landed_state", value_type="str", value=landed_state
        ),
        StateAttribute(
            name="telemetry_source", value_type="str", value="gazebo-mavsdk"
        ),
    )
    candidate = StateSample.model_construct(
        schema_version="aero-bench.state-sample/v1",
        run_id=run_id,
        scenario_digest=scenario_digest,
        at=target,
        stage="motion",
        entity_id=vehicle_id,
        provider_id=provider_id,
        sample_kind="dynamic",
        pose=pose,
        linear_velocity_enu=EnuLinearVelocity(
            east_mps=east_mps, north_mps=north_mps, up_mps=up_mps
        ),
        linear_velocity_ned=NedLinearVelocity(
            north_mps=north_mps, east_mps=east_mps, down_mps=-up_mps
        ),
        angular_velocity_body=BodyAngularVelocity(
            x_radps=0.0, y_radps=0.0, z_radps=0.0
        ),
        mode=mode,
        armed=armed,
        contacts=contacts,
        attributes=attributes,
        sample_digest="0" * 64,
    )
    return StateSample.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "sample_digest": state_sample_digest_value(candidate),
        }
    )


def _state_event_values(
    *,
    vehicle_id: str,
    east_m: float,
    north_m: float,
    up_m: float,
    yaw_deg: float,
    east_mps: float = 0.0,
    north_mps: float = 0.0,
    up_mps: float = 0.0,
    landed: bool,
    in_air: bool,
    landed_state: str,
    ground_contact: bool,
    contacts: tuple[str, ...] = (),
    collision_contact: bool = False,
    simulation_time_ns: int,
) -> dict[str, object]:
    pose = {
        "x_m": east_m,
        "y_m": north_m,
        "z_m": up_m,
        "roll_rad": 0.0,
        "pitch_rad": 0.0,
        "yaw_rad": math.radians(yaw_deg),
    }
    attitude = {
        "roll_rad": 0.0,
        "pitch_rad": 0.0,
        "yaw_rad": math.radians(yaw_deg),
    }
    velocity = {
        "north_m_s": north_mps,
        "east_m_s": east_mps,
        "down_m_s": -up_mps,
    }
    return {
        "vehicle_id": vehicle_id,
        "pose_json": _canonical(pose),
        "position_wgs84_json": _canonical(
            {"latitude_deg": 39.916, "longitude_deg": 116.397, "altitude_m": 43.0 + up_m}
        ),
        "velocity_json": _canonical(velocity),
        "angular_velocity_json": _canonical(
            {"x_rad_s": 0.0, "y_rad_s": 0.0, "z_rad_s": 0.0}
        ),
        "attitude_json": _canonical(attitude),
        "flight_mode": "GUIDED",
        "armed": True,
        "in_air": in_air,
        "landed": landed,
        "landed_state": landed_state,
        "contacts_json": _canonical(list(contacts)),
        "ground_contact": ground_contact,
        "battery_percent": 95.0,
        "health": _canonical({"is_global_position_ok": True}),
        "collision_contact": collision_contact,
        "simulation_time_ns": simulation_time_ns,
        "evidence_path": "trajectory/evidences.jsonl",
        "evidence_sha256": "1" * 64,
    }


def _state_event(
    target: SimulationTime,
    *,
    vehicle_id: str = "uav.alpha",
    provider_id: str = "flight",
    values: dict[str, object] | None = None,
) -> ProviderEvent:
    if values is None:
        raise AssertionError("state event values are required")
    return ProviderEvent(
        provider_id=provider_id,
        event_id=f"state.{vehicle_id}.{target.tick}",
        time=target,
        payload_schema_id="px4.state.v1",
        payload=tuple(
            NamedValue(name=name, value=value)
            for name, value in values.items()
        ),
    )


def _any_stage_barrier(
    *,
    stage: str,
    run_id: str,
    scenario_digest: str,
    at: SimulationTime,
    provider_id: str = "flight",
    input_scene_state_digest: str | None = None,
    predecessor_barriers: tuple[StageBarrierDigest, ...] = (),
    contribution_digest: str = "c" * 64,
) -> StageBarrier:
    receipt_fields = {
        "schema_version": "aero-bench.stage-receipt/v1",
        "run_id": run_id,
        "scenario_digest": scenario_digest,
        "at": at,
        "stage": stage,
        "provider_id": provider_id,
        "state_digest": "a" * 64,
        "step_receipt_digest": "b" * 64,
        "contribution_digest": contribution_digest,
        "payload_digest": "d" * 64,
        "input_scene_state_digest": input_scene_state_digest,
        "predecessor_barriers": predecessor_barriers,
    }
    unsigned_receipt = StageReceipt.model_construct(
        **receipt_fields, receipt_digest="0" * 64
    )
    receipt = StageReceipt(
        **receipt_fields,
        receipt_digest=stage_receipt_digest_value(unsigned_receipt),
    )
    barrier_fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": run_id,
        "scenario_digest": scenario_digest,
        "at": at,
        "stage": stage,
        "input_scene_state_digest": input_scene_state_digest,
        "predecessor_barriers": predecessor_barriers,
        "provider_ids": (provider_id,),
        "receipts": (receipt,),
        "receipt_digests": (receipt.receipt_digest,),
    }
    unsigned_barrier = StageBarrier.model_construct(
        **barrier_fields, barrier_digest="0" * 64
    )
    return StageBarrier(
        **barrier_fields,
        barrier_digest=stage_barrier_digest_value(unsigned_barrier),
    )


def _motion_barrier(
    *,
    run_id: str,
    scenario_digest: str,
    at: SimulationTime,
    provider_id: str = "flight",
    contribution_digest: str = "c" * 64,
) -> StageBarrier:
    return _any_stage_barrier(
        stage="motion",
        run_id=run_id,
        scenario_digest=scenario_digest,
        at=at,
        provider_id=provider_id,
        contribution_digest=contribution_digest,
    )


def _scene_state(
    *,
    run_id: str,
    scenario_digest: str,
    at: SimulationTime,
    samples: tuple[StateSample, ...],
    provider_id: str = "flight",
) -> SceneState:
    samples = tuple(sorted(samples, key=lambda sample: sample.entity_id))
    barrier = _motion_barrier(
        run_id=run_id,
        scenario_digest=scenario_digest,
        at=at,
        provider_id=provider_id,
    )
    scene_fields = {
        "schema_version": "aero-bench.scene-state/v1",
        "run_id": run_id,
        "scenario_digest": scenario_digest,
        "at": at,
        "declared_entity_ids": tuple(sample.entity_id for sample in samples),
        "samples": samples,
        "stage_barrier": barrier,
        "contribution_digests": tuple(
            sorted(receipt.contribution_digest for receipt in barrier.receipts)
        ),
        "previous_scene_state_digest": (
            SCENE_STATE_ROOT_DIGEST if at.tick == 1 else "f" * 64
        ),
    }
    unsigned_scene = SceneState.model_construct(
        **scene_fields, scene_state_digest="0" * 64
    )
    return SceneState(
        **scene_fields,
        scene_state_digest=scene_state_digest_value(unsigned_scene),
    )


# ------------------------------------------------------------------ runtime


@pytest.fixture
def context(tmp_path) -> SimpleNamespace:
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    # The authored fixture vertiport (12 x 8, 3 parking slots) is deliberately
    # *not* pad-feasible, so tests lower a package whose facility-1 pad row
    # actually materializes (18 m wide -> exactly the 3 declared pads).  The
    # scene binding, fleet, performance profiles, orders and actor grants are
    # the accepted fixture values.
    raw = _package_document(aircraft_count=1)
    raw["scene"]["scene_source_sha256"] = fixture.package.scene.scene_source_sha256
    facilities = _facilities()
    facilities[0] = _vertiport(id="facility-1", widthM=18)
    raw["facilities"] = facilities
    package = lower_logistics_task_package(raw)
    bindings = validate_logistics_runtime_bindings(
        bindings_document=_valid_bindings(aircraft_count=1),
        reader=fixture.reader,
        environment=fixture.environment,
        agents=fixture.agents,
        package=package,
        scenario=fixture.scenario,
    )
    assert bindings.aircraft[0].vehicle_id == "uav.alpha"
    assert len(facility_landing_pads(package.facilities.require("facility-1"))) == 3
    return SimpleNamespace(
        fixture=fixture,
        package=package,
        bindings=bindings,
        scenario=fixture.scenario,
        run_id=RUN_ID,
        scenario_digest=fixture.scenario.scenario_digest,
    )


def _landed_context(
    context: SimpleNamespace,
    *,
    facility_id: str = "facility-1",
    pad_index: int = 0,
    pose_reference_m: float = 0.1,
    yaw_deg: float = 15.0,
    at: SimulationTime = AT,
    package=None,
):
    package = context.package if package is None else package
    pad = facility_landing_pads(package.facilities.require(facility_id))[
        pad_index
    ]
    east_m = pad.x
    north_m = -pad.z
    up_m = pad.y + pose_reference_m
    sample = _state_sample(
        at,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        east_m=east_m,
        north_m=north_m,
        up_m=up_m,
        yaw_deg=yaw_deg,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
    )
    scene_state = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=at,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=east_m,
        north_m=north_m,
        up_m=up_m,
        yaw_deg=yaw_deg,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=at.sim_time_ns,
    )
    event = _state_event(at, values=values)
    return pad, scene_state, (event,)


def _landed_batch(
    context: SimpleNamespace,
    *,
    at: SimulationTime,
    run_id: str,
) -> tuple[object, SceneState, tuple[ProviderEvent, ...]]:
    """A complete internally consistent grounded batch for an arbitrary run/time."""
    pad = facility_landing_pads(context.package.facilities.require("facility-1"))[0]
    east_m = pad.x
    north_m = -pad.z
    up_m = pad.y + 0.1
    sample = _state_sample(
        at,
        run_id=run_id,
        scenario_digest=context.scenario_digest,
        east_m=east_m,
        north_m=north_m,
        up_m=up_m,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
    )
    scene_state = _scene_state(
        run_id=run_id,
        scenario_digest=context.scenario_digest,
        at=at,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=east_m,
        north_m=north_m,
        up_m=up_m,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=at.sim_time_ns,
    )
    return pad, scene_state, (_state_event(at, values=values),)


def _rolled_landed_batch(
    context: SimpleNamespace,
    *,
    roll_deg: float = 0.0,
    pitch_deg: float = 0.0,
    yaw_deg: float = 15.0,
) -> tuple[object, SceneState, tuple[ProviderEvent, ...]]:
    """A grounded batch whose measured attitude carries explicit roll/pitch."""
    pad = facility_landing_pads(context.package.facilities.require("facility-1"))[0]
    sample = _state_sample(
        AT,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=yaw_deg,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
    )
    quat = rpy_to_quaternion(
        roll_rad=math.radians(roll_deg),
        pitch_rad=math.radians(pitch_deg),
        yaw_rad=math.radians(yaw_deg),
    )
    tilted = ResolvedQuaternion(qw=quat.w, qx=quat.x, qy=quat.y, qz=quat.z)
    pose = ResolvedPose(
        position=sample.pose.position,
        orientation_enu=tilted,
        orientation_ned=tilted,
    )
    # The changed sample keeps every nested field as a genuine validated model
    # instance, so the digest document serializes without dict-vs-model noise.
    changed = sample.model_copy(update={"pose": pose})
    candidate = type(changed).model_construct(
        schema_version=changed.schema_version,
        run_id=changed.run_id,
        scenario_digest=changed.scenario_digest,
        at=changed.at,
        stage=changed.stage,
        entity_id=changed.entity_id,
        provider_id=changed.provider_id,
        sample_kind=changed.sample_kind,
        pose=pose,
        linear_velocity_enu=changed.linear_velocity_enu,
        linear_velocity_ned=changed.linear_velocity_ned,
        angular_velocity_body=changed.angular_velocity_body,
        mode=changed.mode,
        armed=changed.armed,
        contacts=changed.contacts,
        attributes=changed.attributes,
        sample_digest="0" * 64,
    )
    adjusted = type(changed).model_validate(
        {
            **changed.model_dump(mode="json"),
            "sample_digest": state_sample_digest_value(candidate),
        }
    )
    scene_state = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        samples=(adjusted,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=yaw_deg,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=AT.sim_time_ns,
    )
    pose_json = json_loads(values["pose_json"])
    pose_json["roll_rad"] = math.radians(roll_deg)
    pose_json["pitch_rad"] = math.radians(pitch_deg)
    values["pose_json"] = _canonical(pose_json)
    attitude_json = json_loads(values["attitude_json"])
    attitude_json["roll_rad"] = math.radians(roll_deg)
    attitude_json["pitch_rad"] = math.radians(pitch_deg)
    values["attitude_json"] = _canonical(attitude_json)
    return pad, scene_state, (_state_event(AT, values=values),)


def _observe(
    context: SimpleNamespace,
    *,
    scene_state: SceneState,
    events: tuple[ProviderEvent, ...],
    package=None,
    bindings=None,
    scenario=None,
    aircraft_id: str = "fleet-alpha:1",
    facility_id: str = "facility-1",
    pad_index: int = 0,
    pose_reference: DeclaredAircraftPoseReference | None = None,
    tolerances: PresenceTolerances | None = None,
    stage_barriers: tuple[StageBarrier, ...] | None = None,
    expected_run_id: str = RUN_ID,
    target: SimulationTime = AT,
) -> FacilityPadPhysicalObservation:
    if pose_reference is None:
        pose_reference = DeclaredAircraftPoseReference(
            aircraft_id="fleet-alpha:1",
            pose_reference_above_contact_m=0.1,
        )
    if tolerances is None:
        tolerances = PresenceTolerances(
            vertical_tolerance_m=0.05,
            horizontal_uncertainty_m=0.0,
            max_stationary_speed_m_s=0.5,
        )
    if stage_barriers is None:
        stage_barriers = (scene_state.stage_barrier,)
    return observe_facility_pad_presence(
        scene_state=scene_state,
        events=events,
        package=package if package is not None else context.package,
        bindings=bindings if bindings is not None else context.bindings,
        scenario=scenario if scenario is not None else context.scenario,
        aircraft_id=aircraft_id,
        facility_id=facility_id,
        pad_index=pad_index,
        pose_reference=pose_reference,
        tolerances=tolerances,
        stage_barriers=stage_barriers,
        expected_run_id=expected_run_id,
        target=target,
    )


# ------------------------------------------------------- pure frame conversions


def test_scene_north_sign_yaw_and_ned_axis_conversions() -> None:
    assert scene_position_from_enu(east_m=1.0, north_m=2.0, up_m=3.0) == (
        1.0,
        3.0,
        -2.0,
    )
    assert scene_velocity_from_enu(east_mps=1.0, north_mps=2.0, up_mps=3.0) == (
        1.0,
        3.0,
        -2.0,
    )
    # ENU yaw 0 == east and maps directly to the scene Y-axis (east-positive)
    # rotation the pad geometry uses.
    assert scene_yaw_deg_from_enu_quaternion(_enquaternion(0.0)) == pytest.approx(0.0)
    assert scene_yaw_deg_from_enu_quaternion(_enquaternion(90.0)) == pytest.approx(
        90.0
    )
    assert scene_yaw_deg_from_enu_quaternion(_enquaternion(-45.0)) == pytest.approx(
        -45.0
    )
    assert scene_yaw_deg_from_enu_quaternion(_enquaternion(15.0)) == pytest.approx(
        15.0
    )


# ---------------------------------------------------------------- positive path


def test_positive_grounded_assessment_binds_authored_to_native(
    context: SimpleNamespace,
) -> None:
    pad, scene_state, events = _landed_context(context)
    observed = _observe(context, scene_state=scene_state, events=events)

    assert isinstance(observed, FacilityPadPhysicalObservation)
    assert (
        observed.schema_version == LOGISTICS_PHYSICAL_OBSERVATION_SCHEMA_VERSION
    )
    # Authored logistics identity is preserved verbatim; the kernel evaluates
    # on the native vehicle.
    assert observed.aircraft_id == "fleet-alpha:1"
    assert observed.fleet_entry_id == "fleet-alpha"
    assert observed.visual_asset_id == "model:logistics-drone-v1"
    assert observed.provider_id == "flight"
    assert observed.native_vehicle_id == "uav.alpha"
    assert observed.sample.aircraft_id == "uav.alpha"
    assert observed.profile.aircraft_id == "uav.alpha"
    assert observed.event_binding.aircraft_id == "uav.alpha"
    assert observed.assessment.aircraft_id == "uav.alpha"

    assert observed.source_event_id == "state.uav.alpha.1"
    assert observed.sample.sample_ref == "state.uav.alpha.1"
    assert observed.sample.evidence_ref == "state.uav.alpha.1"
    assert observed.assessment.evidence_ref == "state.uav.alpha.1"
    assert observed.assessment.sample_ref == "state.uav.alpha.1"
    assert observed.sample.frame == "scene_east_south_m"
    # The record carries source digests verbatim.
    assert observed.source_scene_state_digest.isascii()
    assert len(observed.source_scene_state_digest) == 64
    assert observed.source_evidence_path == "trajectory/evidences.jsonl"
    assert observed.source_evidence_sha256 == "1" * 64
    assert observed.source_frame_origin.longitude_deg == pytest.approx(116.397)
    assert observed.provenance_declared == "caller_supplied"
    assert observed.provenance_verified is False
    assert observed.single_point_in_time is True

    # Measured ENU attitude is surfaced; the yaw-only footprint basis and the
    # clearance scope are disclosed so the result is never mistaken for a full
    # tilted-3D body or global collision test.
    assert observed.measured_roll_deg == pytest.approx(0.0)
    assert observed.measured_pitch_deg == pytest.approx(0.0)
    assert observed.measured_yaw_deg == pytest.approx(15.0)
    assert observed.collision_contact is False
    assert observed.footprint_basis == "yaw_only_declared_body_footprint"
    assert observed.clearance_note == CLEARANCE_SCOPE_NOTE
    assert "not a full tilted-3D body" in observed.clearance_note
    assert observation_digest_value(observed) == observed.observation_digest

    # The scene conversion places the ENU (east, north, up) pose on the
    # materialized pad, matching the accepted scene_east_south_m frame.
    assert pad.x == pytest.approx(observed.sample.x)
    assert pad.y + 0.1 == pytest.approx(observed.sample.y)
    assert pad.z == pytest.approx(observed.sample.z)
    assert observed.sample.x == pytest.approx(4.6)
    assert observed.sample.y == pytest.approx(0.82)
    assert observed.sample.z == pytest.approx(-19.92)
    assert observed.sample.body_yaw_deg == pytest.approx(15.0)

    assert observed.assessment.eligible
    assert observed.assessment.failure_code is None
    assert observed.assessment.single_sample_evaluated == 1
    assert observed.assessment.dwell_asserted is False
    assert observed.assessment.provenance_verified is False


def test_roof_pad_height_uses_explicit_pose_reference_offset(
    context: SimpleNamespace, tmp_path
) -> None:
    # A rooftop vertiport lifts the pad contact surface by the declared,
    # already-bound support height; the measured pose reference above contact is
    # a required declared calibration input and is never guessed from body
    # height, and this adapter makes no empirical claim about its value.
    raw = _package_document(aircraft_count=1)
    raw["scene"]["scene_source_sha256"] = context.package.scene.scene_source_sha256
    raw["facilities"].append(
        _vertiport(
            id="facility-roof",
            widthM=18,
            placement="rooftop",
            buildingId="building.ofc",
            supportHeightM=3.0,
        )
    )
    package = lower_logistics_task_package(raw)
    roof_pad = facility_landing_pads(package.facilities.require("facility-roof"))[0]
    assert roof_pad.y == pytest.approx(0.72 + 3.0)
    assert roof_pad.frame == "scene_east_south_m"

    pose_reference_m = 0.2
    pad, scene_state, events = _landed_context(
        context,
        package=package,
        facility_id="facility-roof",
        pose_reference_m=pose_reference_m,
    )
    observed = _observe(
        context,
        package=package,
        facility_id="facility-roof",
        scene_state=scene_state,
        events=events,
        pose_reference=DeclaredAircraftPoseReference(
            aircraft_id="fleet-alpha:1",
            pose_reference_above_contact_m=pose_reference_m,
        ),
    )
    assert observed.pad.y == pytest.approx(3.72)
    assert observed.sample.y == pytest.approx(3.72 + pose_reference_m)
    assert observed.profile.pose_reference_above_contact_m == pytest.approx(
        pose_reference_m
    )
    assert observed.assessment.landed_state_ok
    assert observed.assessment.contact_height_ok
    assert observed.assessment.eligible


def test_airborne_sample_is_a_valid_but_ineligible_observation(
    context: SimpleNamespace,
) -> None:
    pad = facility_landing_pads(context.package.facilities.require("facility-1"))[0]
    east_m = pad.x
    north_m = -pad.z
    up_m = pad.y + 1.5  # airborne, never grounded on the pad
    sample = _state_sample(
        AT,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        east_m=east_m,
        north_m=north_m,
        up_m=up_m,
        yaw_deg=0.0,
        landed=False,
        in_air=True,
        landed_state="IN_AIR",
        ground_contact=False,
        contacts=(),
    )
    scene_state = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=east_m,
        north_m=north_m,
        up_m=up_m,
        yaw_deg=0.0,
        landed=False,
        in_air=True,
        landed_state="IN_AIR",
        ground_contact=False,
        contacts=(),
        simulation_time_ns=AT.sim_time_ns,
    )
    event = _state_event(AT, values=values)
    observed = _observe(context, scene_state=scene_state, events=(event,))
    assert observed.assessment.eligible is False
    assert observed.assessment.failure_code == "aircraft_not_landed"
    assert observed.assessment.landed_state_ok is False
    # An overflight is never coerced into presence by the adapter.
    assert observed.assessment.dwell_asserted is False


# -------- external caller context (run/target anchor, stage barrier tuple) -----


def test_foreign_run_complete_batch_rejects(context: SimpleNamespace) -> None:
    # A fully internally consistent batch built for a different run id is
    # rejected by the caller-supplied expected_run_id anchor.
    _, scene_state, events = _landed_batch(context, at=AT, run_id="b" * 64)
    with pytest.raises(PhysicalObservationError, match="expected run"):
        _observe(context, scene_state=scene_state, events=events)


def test_stale_complete_batch_rejects(context: SimpleNamespace) -> None:
    # A complete valid batch at an earlier tick is rejected by the target.
    stale_at = SimulationTime(tick=2, sim_time_ns=2_000_000_000)
    target = SimulationTime(tick=3, sim_time_ns=3_000_000_000)
    _, scene_state, events = _landed_batch(context, at=stale_at, run_id=RUN_ID)
    with pytest.raises(PhysicalObservationError, match="not the requested tick"):
        _observe(context, scene_state=scene_state, events=events, target=target)


def test_target_tick_mismatch_rejects(context: SimpleNamespace) -> None:
    # The same sim-time ns with a different tick is still rejected.
    wrong_tick = SimulationTime(tick=2, sim_time_ns=3_000_000_000)
    target = SimulationTime(tick=3, sim_time_ns=3_000_000_000)
    _, scene_state, events = _landed_batch(context, at=wrong_tick, run_id=RUN_ID)
    with pytest.raises(PhysicalObservationError, match="2:3000000000"):
        _observe(context, scene_state=scene_state, events=events, target=target)


def test_target_sim_time_nanos_mismatch_rejects(context: SimpleNamespace) -> None:
    # The same tick with a different sim_time_ns is rejected separately.
    wrong_ns = SimulationTime(tick=3, sim_time_ns=2_000_000_000)
    target = SimulationTime(tick=3, sim_time_ns=3_000_000_000)
    _, scene_state, events = _landed_batch(context, at=wrong_ns, run_id=RUN_ID)
    with pytest.raises(PhysicalObservationError, match="3:2000000000"):
        _observe(context, scene_state=scene_state, events=events, target=target)


def test_empty_stage_barriers_reject(context: SimpleNamespace) -> None:
    _, scene_state, events = _landed_context(context)
    with pytest.raises(PhysicalObservationError, match="empty"):
        _observe(context, scene_state=scene_state, events=events, stage_barriers=())


def test_same_run_wrong_motion_barrier_digest_rejects(
    context: SimpleNamespace,
) -> None:
    # Same run/scenario/target but a different motion-barrier digest than the
    # one the SceneState was assembled around: the closure is rejected.
    _, scene_state, events = _landed_context(context)
    wrong_barrier = _motion_barrier(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        contribution_digest="e" * 64,
    )
    with pytest.raises(PhysicalObservationError, match="wrong stage barrier"):
        _observe(
            context,
            scene_state=scene_state,
            events=events,
            stage_barriers=(wrong_barrier,),
        )


def test_foreign_run_barrier_rejects(context: SimpleNamespace) -> None:
    _, scene_state, events = _landed_context(context)
    foreign = _motion_barrier(
        run_id="b" * 64,
        scenario_digest=context.scenario_digest,
        at=AT,
    )
    with pytest.raises(PhysicalObservationError, match="expected run"):
        _observe(
            context,
            scene_state=scene_state,
            events=events,
            stage_barriers=(foreign,),
        )


def test_actual_full_hook_barrier_tuple_is_accepted(context: SimpleNamespace) -> None:
    # The real runtime hook supplies the motion, network and business barriers
    # of the tick; every closure binds the same run/scenario/target, so the
    # adapter accepts the full tuple and the ground-only assessment is eligible.
    _, scene_state, events = _landed_context(context)
    motion = scene_state.stage_barrier
    network = _any_stage_barrier(
        stage="network",
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        input_scene_state_digest=scene_state.scene_state_digest,
        predecessor_barriers=(
            StageBarrierDigest(
                stage="motion", barrier_digest=motion.barrier_digest
            ),
        ),
    )
    business = _any_stage_barrier(
        stage="business_environment",
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        input_scene_state_digest=scene_state.scene_state_digest,
        predecessor_barriers=(
            StageBarrierDigest(
                stage="motion", barrier_digest=motion.barrier_digest
            ),
            StageBarrierDigest(
                stage="network", barrier_digest=network.barrier_digest
            ),
        ),
    )
    observed = _observe(
        context,
        scene_state=scene_state,
        events=events,
        stage_barriers=(motion, network, business),
    )
    assert observed.assessment.eligible
    assert observed.source_stage_barrier_digest == motion.barrier_digest


# -------------------------------------------------------------- rejection path


def test_wrong_provider_rejects_missing_native_sample(
    context: SimpleNamespace,
) -> None:
    sample = _state_sample(
        AT,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        provider_id="perception",
        east_m=0.0,
        north_m=0.0,
        up_m=1.0,
        yaw_deg=0.0,
        landed=False,
        in_air=True,
        landed_state="IN_AIR",
        ground_contact=False,
    )
    scene_state = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        samples=(sample,),
        provider_id="perception",
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=0.0,
        north_m=0.0,
        up_m=1.0,
        yaw_deg=0.0,
        landed=False,
        in_air=True,
        landed_state="IN_AIR",
        ground_contact=False,
        simulation_time_ns=AT.sim_time_ns,
    )
    event = _state_event(AT, provider_id="perception", values=values)
    with pytest.raises(PhysicalObservationError, match="no state sample"):
        _observe(context, scene_state=scene_state, events=(event,))


def test_wrong_vehicle_evidence_rejects(
    context: SimpleNamespace,
) -> None:
    _, scene_state, _ = _landed_context(context)
    # The closed stage carries the bound vehicle's sample but the source event
    # names a different native vehicle; the source evidence is never picked.
    values = _state_event_values(
        vehicle_id="uav.bravo",
        east_m=4.6,
        north_m=19.92,
        up_m=0.82,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=AT.sim_time_ns,
    )
    event = _state_event(AT, vehicle_id="uav.bravo", values=values)
    with pytest.raises(PhysicalObservationError, match="uav.alpha"):
        _observe(context, scene_state=scene_state, events=(event,))


def test_wrong_frame_binding_rejects(context: SimpleNamespace) -> None:
    pad, scene_state, events = _landed_context(context)
    broken_package = context.package.model_copy(
        update={
            "scene": context.package.scene.model_copy(
                update={"coordinate_frame": "ENU"}
            )
        }
    )
    with pytest.raises(PhysicalObservationError, match="scene_east_south_m frame"):
        _observe(
            context,
            package=broken_package,
            scene_state=scene_state,
            events=events,
        )


def test_scene_origin_disagreement_rejects_arbitrary_offset(
    context: SimpleNamespace,
) -> None:
    pad, scene_state, events = _landed_context(context)
    broken = context.package.scene.model_copy(
        update={"origin_longitude_deg": 116.397 + 1e-5}
    )
    with pytest.raises(PhysicalObservationError, match="no arbitrary WGS84 offset"):
        _observe(
            context,
            package=context.package.model_copy(update={"scene": broken}),
            scene_state=scene_state,
            events=events,
        )


def test_stale_event_rejects(context: SimpleNamespace) -> None:
    _, scene_state, _ = _landed_context(context)
    stale_at = SimulationTime(tick=0, sim_time_ns=0)
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=4.6,
        north_m=19.92,
        up_m=0.82,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=stale_at.sim_time_ns,
    )
    stale = _state_event(stale_at, values=values)
    with pytest.raises(PhysicalObservationError, match="stale"):
        _observe(context, scene_state=scene_state, events=(stale,))


def test_duplicate_source_events_reject(context: SimpleNamespace) -> None:
    _, scene_state, events = _landed_context(context)
    with pytest.raises(PhysicalObservationError, match="duplicate px4.state.v1"):
        _observe(context, scene_state=scene_state, events=events + events)


def test_cross_provider_source_data_rejects(context: SimpleNamespace) -> None:
    _, scene_state, events = _landed_context(context)
    foreign_values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=4.6,
        north_m=19.92,
        up_m=0.82,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=AT.sim_time_ns,
    )
    foreign = _state_event(AT, provider_id="traffic", values=foreign_values)
    with pytest.raises(PhysicalObservationError, match="cross-provider"):
        _observe(context, scene_state=scene_state, events=events + (foreign,))


def test_missing_velocity_axis_reports_exact_api_gap(
    context: SimpleNamespace,
) -> None:
    _, scene_state, events = _landed_context(context)
    values = {item.name: item.value for item in events[0].payload}
    velocity = json_loads(values["velocity_json"])
    del velocity["down_m_s"]
    values["velocity_json"] = _canonical(velocity)
    broken = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="velocity_json"):
        _observe(context, scene_state=scene_state, events=(broken,))


def test_missing_yaw_field_reports_exact_api_gap(context: SimpleNamespace) -> None:
    _, scene_state, events = _landed_context(context)
    values = {item.name: item.value for item in events[0].payload}
    pose = json_loads(values["pose_json"])
    del pose["yaw_rad"]
    values["pose_json"] = _canonical(pose)
    broken = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="pose_json"):
        _observe(context, scene_state=scene_state, events=(broken,))


def test_missing_landed_field_reports_exact_api_gap(
    context: SimpleNamespace,
) -> None:
    _, scene_state, events = _landed_context(context)
    values = {item.name: item.value for item in events[0].payload}
    del values["landed"]
    broken = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="landed"):
        _observe(context, scene_state=scene_state, events=(broken,))


def test_possess_reference_declared_for_wrong_aircraft_rejects(
    context: SimpleNamespace,
) -> None:
    _, scene_state, events = _landed_context(context)
    with pytest.raises(PhysicalObservationError, match="declared pose"):
        _observe(
            context,
            scene_state=scene_state,
            events=events,
            pose_reference=DeclaredAircraftPoseReference(
                aircraft_id="fleet-alpha:2",
                pose_reference_above_contact_m=0.1,
            ),
        )


# ------------------------- source armed / flight_mode cross-checks -------------


def test_event_armed_mismatch_rejects(context: SimpleNamespace) -> None:
    _, scene_state, events = _landed_context(context)
    values = {item.name: item.value for item in events[0].payload}
    values["armed"] = False
    broken = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="armed"):
        _observe(context, scene_state=scene_state, events=(broken,))


def test_event_flight_mode_mismatch_rejects(context: SimpleNamespace) -> None:
    _, scene_state, events = _landed_context(context)
    values = {item.name: item.value for item in events[0].payload}
    values["flight_mode"] = "MANUAL"
    broken = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="flight_mode"):
        _observe(context, scene_state=scene_state, events=(broken,))


def test_missing_sample_armed_reports_exact_api_gap(
    context: SimpleNamespace,
) -> None:
    pad, _, _ = _landed_context(context)
    sample = _state_sample(
        AT,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        armed=None,
    )
    scene_state = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=AT.sim_time_ns,
    )
    event = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="armed"):
        _observe(context, scene_state=scene_state, events=(event,))


def test_missing_sample_mode_reports_exact_api_gap(
    context: SimpleNamespace,
) -> None:
    pad, _, _ = _landed_context(context)
    sample = _state_sample(
        AT,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        mode=None,
    )
    scene_state = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=AT.sim_time_ns,
    )
    event = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="mode"):
        _observe(context, scene_state=scene_state, events=(event,))


# -------------------------------- collision & footprint-scope regressions ------


def test_collision_contact_sample_refuses_qualification(
    context: SimpleNamespace,
) -> None:
    # An otherwise eligible grounded sample whose native collision_contact is
    # True is refused outright; it never becomes a usable presence record.
    pad, _, _ = _landed_context(context)
    sample = _state_sample(
        AT,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0", "obstacle.wall"),
        collision_contact=True,
    )
    scene_state = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0", "obstacle.wall"),
        collision_contact=True,
        simulation_time_ns=AT.sim_time_ns,
    )
    event = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="collision_contact=True"):
        _observe(context, scene_state=scene_state, events=(event,))


def test_ground_only_noncollision_sample_is_eligible(context: SimpleNamespace) -> None:
    # The ground-only regression control: no native collision and no obstacle
    # contact keeps the record usable and eligible.
    _, scene_state, events = _landed_context(context)
    observed = _observe(context, scene_state=scene_state, events=events)
    assert observed.collision_contact is False
    assert observed.assessment.eligible
    assert observed.assessment.failure_code is None


def test_collision_flag_must_agree_with_contact_inventory(
    context: SimpleNamespace,
) -> None:
    # A grounded contact inventory cannot silently carry collision_contact=True;
    # the same-record facts must not contradict.
    pad, _, _ = _landed_context(context)
    sample = _state_sample(
        AT,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        collision_contact=False,
    )
    scene_state = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        collision_contact=True,  # contradicts the ground-only inventory
        simulation_time_ns=AT.sim_time_ns,
    )
    event = _state_event(AT, values=values)
    with pytest.raises(PhysicalObservationError, match="contact inventory"):
        _observe(context, scene_state=scene_state, events=(event,))


def test_measured_roll_pitch_surface_while_footprint_stays_yaw_only(
    context: SimpleNamespace,
) -> None:
    # The measured ENU roll/pitch are carried on the record so downstream can
    # see the attitude, while the clearance verdict stays the yaw-only declared
    # body-footprint projection (never a full tilted-3D collision test).
    pad, scene_state, events = _rolled_landed_batch(
        context, roll_deg=15.0, pitch_deg=-10.0, yaw_deg=15.0
    )
    observed = _observe(context, scene_state=scene_state, events=events)
    assert observed.measured_roll_deg == pytest.approx(15.0)
    assert observed.measured_pitch_deg == pytest.approx(-10.0)
    assert observed.measured_yaw_deg == pytest.approx(15.0)
    assert observed.sample.body_yaw_deg == pytest.approx(15.0)
    assert observed.assessment.horizontal_clearance_ok
    assert observed.footprint_basis == "yaw_only_declared_body_footprint"
    assert observed.clearance_note == CLEARANCE_SCOPE_NOTE
    assert "not a full tilted-3D body" in observed.clearance_note
    assert observed.collision_contact is False


def test_observation_digest_is_consistent_and_source_sensitive(
    context: SimpleNamespace,
) -> None:
    pad, scene_state, events = _landed_context(context)
    observed = _observe(context, scene_state=scene_state, events=events)
    assert observation_digest_value(observed) == observed.observation_digest
    repeat = _observe(context, scene_state=scene_state, events=events)
    assert repeat.observation_digest == observed.observation_digest

    # Only the sealed evidence reference changes; the verdict stays eligible but
    # the observation digest must change because the source event identity
    # changed.
    values = {item.name: item.value for item in events[0].payload}
    values["evidence_sha256"] = "2" * 64
    changed_event = _state_event(AT, values=values)
    changed = _observe(context, scene_state=scene_state, events=(changed_event,))
    assert changed.assessment.eligible
    assert changed.source_evidence_sha256 == "2" * 64
    assert changed.observation_digest != observed.observation_digest

    # A physically different source sample (different measured yaw/digest) also
    # changes the observation digest even when the verdict stays eligible.
    sample = _state_sample(
        AT,
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=25.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
    )
    ss = _scene_state(
        run_id=context.run_id,
        scenario_digest=context.scenario_digest,
        at=AT,
        samples=(sample,),
    )
    values2 = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=pad.y + 0.1,
        yaw_deg=25.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=AT.sim_time_ns,
    )
    changed2 = _observe(
        context, scene_state=ss, events=(_state_event(AT, values=values2),)
    )
    assert changed2.assessment.eligible
    assert changed2.observation_digest != observed.observation_digest
