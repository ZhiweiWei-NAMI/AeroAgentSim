"""Self-contained in-image selfcheck for the Logistics Business provider.

Builds a synthetic single-order logistics package, lowers it through the real
``lower_logistics_task_package`` lowering path, resolves the real world
scenario, writes the strict provider bundle, and then drives the real
``LogisticsBusinessProviderService`` over a genuine local
``JsonLineRpcServer``/``JsonLineRpcTransport`` loopback covering prepare,
reset, one authoritative staged business-environment step, a legitimate
non-physical offer/accept/assign lifecycle, an authenticated online-order
creation bound to the business principal, an explicit not-yet-supported
physical pickup refusal, read-only queries, deterministic snapshots,
finalization of the declared artifact chain, and shutdown. The finalized
artifact is re-checked to seal the full causal arrival journal, the canonical
snapshot digest, and the baseline binding, and the journal is replayed back to
the same snapshot.

The physical-observation ingress surface is then exercised with a clearly
synthetic closed motion stage: a typed px4.state.v1 ProviderEvent and
``SceneState`` are built for one aircraft resting exactly
``pose_reference_above_contact_m`` above a declared pad of ``facility-1`` (an
explicit pose-reference calibration, never an inferred half-body-height), the
accepted ``derive_physical_observations`` adapter produces the typed batch, and
the private ``logistics.observation.ingest`` operation journals it over the
real wire. Replay deduplicates atomically, and the finalized artifact persists
the immutable ``observation_journal`` plus its canonical digest. Inputs are
synthetic and clearly labeled; this is an in-image development checkpoint, not
a formal milestone run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import tempfile
from pathlib import Path

from aero_bench.config.models import FileRef, NamedValue
from aero_bench.executor.contracts import ProviderWorkloadContract
from aero_bench.providers.logistics_business.protocol import (
    LOGISTICS_ORDER_CREATE_TOOL,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import JsonLineRpcServer, JsonLineRpcTransport
from aero_bench.runtime.contracts import (
    SCENE_STATE_ROOT_DIGEST,
    BodyAngularVelocity,
    BusinessEnvironmentStageRequest,
    EnuLinearVelocity,
    NedLinearVelocity,
    ProviderEvent,
    ProviderFinalizationRequest,
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
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import PresenceTolerances
from aero_bench.tasks.logistics.observation_ingress import (
    LOGISTICS_OBSERVATION_INGEST_OPERATION,
    LogisticsObservationSpec,
    LogisticsObservationSpecItem,
    ObservationJournal,
    build_observation_batch,
    derive_physical_observations,
)
from aero_bench.tasks.logistics.order_arrivals import (
    OrderArrivalEvent,
    replay_order_arrivals,
)
from aero_bench.tasks.logistics.orders import OrderRequest
from aero_bench.tasks.logistics.physical_observations import (
    DeclaredAircraftPoseReference,
)
from aero_bench.tasks.logistics.runtime_bindings import (
    LogisticsAircraftBinding,
    LogisticsRuntimeBindings,
)
from aero_bench.world.resolved import (
    ResolvedCoordinate,
    ResolvedEcefPosition,
    ResolvedEnuPosition,
    ResolvedNedPosition,
    ResolvedPose,
    ResolvedQuaternion,
    ResolvedWgs84Position,
    ResolvedScenario,
    scenario_assets_for_workload,
)
from containers.selfcheck_scenario import (
    ScenarioProvider,
    build_resolved_scenario,
)
from service import (
    PROTOCOL_VERSION,
    LogisticsBusinessProviderService,
    WorkloadIdentity,
)

RUN_ID = "a" * 64
SESSION_TOKEN = "9" * 64
SEED = 1701
PORT = 18440
PROVIDER_ID = "logistics.business"
RUNTIME_IMAGE = "registry.invalid/logistics-business@sha256:" + "1" * 64
CAPABILITIES = ("logistics.facilities.state", "logistics.orders.authority")
ARTIFACT_REQUIREMENT = {
    "artifact_id": "artifact.logistics",
    "artifact_type": "logistics.business.state",
    "producer_id": PROVIDER_ID,
    "visibility": "private",
    "relative_path": "logistics/state.json",
    "max_size_bytes": 1_048_576,
    "source_asset_id": None,
}


class SelfcheckError(RuntimeError):
    pass


def _require(condition: object, detail: str) -> None:
    if not condition:
        raise SelfcheckError(detail)


def _canonical(value: object) -> bytes:
    return canonical_json_bytes(value)


def _vertiport(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "facility-1",
        "name": "active vertiport",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 10, "z": -20},
        "rotationDeg": 0,
        "widthM": 12,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": 3, "movementsPerHour": 30},
        "cargo": None,
        "charging": None,
    }
    value.update(overrides)
    return value


def _hub(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "hub-3",
        "name": "cargo hub",
        "kind": "hub",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 30, "z": -40},
        "rotationDeg": 0,
        "widthM": 16,
        "depthM": 12,
        "heightM": 6,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 100, "throughputPerHourKg": 50},
        "charging": None,
    }
    value.update(overrides)
    return value


def _charger(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "charger-x",
        "name": "east charger",
        "kind": "charger",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 50, "z": -60},
        "rotationDeg": 0,
        "widthM": 10,
        "depthM": 8,
        "heightM": 3.2,
        "landing": None,
        "cargo": None,
        "charging": {
            "slots": 1,
            "powerW": 5000,
            "priceAmount": 1.2,
            "priceCurrency": "CNY",
            "priceUnit": "kWh",
        },
    }
    value.update(overrides)
    return value


def _package_document() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.logistics-task/v1",
        "package_id": LOGISTICS_PACKAGE_ID,
        "task_id": "logistics.task",
        "verifier_id": "logistics.verifier",
        "scene": {
            "scene_id": "world.city-demo",
            "coordinate_frame": "scene_east_south_m",
            "origin_latitude_deg": 39.916,
            "origin_longitude_deg": 116.397,
            "origin_altitude_m": 43.0,
            "scene_source_sha256": "ab" * 32,
        },
        "facilities": [
            # facility-1 is widened to be pad-feasible (18 m -> the declared 3
            # parking slots become canonical pads) so the physical-observation
            # ingress can bind to an actual declared pad geometry.
            _vertiport(widthM=18),
            _vertiport(id="facility-2"),
            _hub(),
            _charger(),
        ],
        "fleet": [
            {
                "id": "fleet-alpha",
                "assetId": "model:logistics-drone-v1",
                "count": 1,
                "homeFacilityId": "facility-1",
                "batteryWh": 20000,
                "reserveRatio": 0.2,
                "maxPayloadKg": 5,
            }
        ],
        "performanceProfiles": [
            {
                "fleetEntryId": "fleet-alpha",
                "sourceLabel": "operator estimate",
                "provenance": "selected-city v3 planning data",
                "aircraftBody": {"xM": 0.6, "yM": 0.6, "zM": 0.3},
                "cruiseSpeedMps": 15,
                "cruisePowerW": 900,
                "hoverPowerW": 700,
                "chargeEfficiency": 0.85,
            }
        ],
        "orders": [
            {
                "id": "order-1",
                "sourceFacilityId": "facility-1",
                "destinationFacilityId": "facility-2",
                "hubHandoffFacilityId": "hub-3",
                "cargoKg": 1,
                "releaseAtS": 0,
                "deliverByS": 600,
            }
        ],
        "noFlyZones": [
            {
                "id": "nofly.city-center",
                "name": "City center keep-out",
                "polygon": [
                    {"x": 10, "z": -20},
                    {"x": 30, "z": -20},
                    {"x": 20, "z": -40},
                ],
                "floorM": 0,
                "ceilingM": 120,
                "startsAtS": 0,
                "endsAtS": None,
                "source": {"kind": "manual", "label": "planner", "uri": None},
            }
        ],
        "actors": [
            {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
            {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
            {"actor_id": "logistics.business", "role": "business"},
        ],
    }


def _task_package_document() -> dict[str, object]:
    return lower_logistics_task_package(_package_document()).model_dump(mode="json")


def _observation_config_document() -> dict[str, object]:
    """Declared digest-bound observation configuration (v2 bundle branch).

    Mirrors exactly what :func:`_observation_runtime_bindings`,
    :func:`_observation_batch` and the selfcheck's closed stage use: one
    authored aircraft bound to the native ``flight``/``uav.alpha`` identity,
    the explicit pose-reference calibration (0.1 m, never guessed from body
    height), the presence tolerances and the one (fleet-alpha:1, facility-1,
    pad 0) observation plan item.
    """
    return {
        "schema_version": "aero-bench.logistics-observation-config/v1",
        "bindings": _observation_runtime_bindings().model_dump(mode="json"),
        "pose_references": [
            {
                "aircraft_id": "fleet-alpha:1",
                "pose_reference_above_contact_m": 0.1,
            }
        ],
        "tolerances": {
            "vertical_tolerance_m": 0.05,
            "horizontal_uncertainty_m": 0.0,
            "max_stationary_speed_m_s": 0.5,
        },
        "spec": {
            "schema_version": "aero-bench.logistics-observation-spec/v1",
            "items": [
                {
                    "aircraft_id": "fleet-alpha:1",
                    "facility_id": "facility-1",
                    "pad_index": 0,
                }
            ],
        },
    }


def _config_document(task_package: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "aero-bench.logistics-business/v3",
        "scheduled_orders": [],
        "provider_id": PROVIDER_ID,
        "task_package": task_package,
        "observation": _observation_config_document(),
        "principal_bindings": [
            {
                "principal_id": "agent.provider",
                "actor_id": "fleet-alpha:1",
                "role": "aircraft_agent",
            },
            {
                "principal_id": "logistics.dispatcher",
                "actor_id": "logistics.dispatcher",
                "role": "dispatcher",
            },
            {
                "principal_id": "logistics.business",
                "actor_id": "logistics.business",
                "role": "business",
            },
        ],
    }


def _city_origin_coordinate() -> dict[str, object]:
    """The authored city origin (WGS84/ECEF/ENU) shared by the scenario patch."""
    return {
        "enu": {"east_m": 0.0, "north_m": 0.0, "up_m": 0.0},
        "ned": {"north_m": 0.0, "east_m": 0.0, "down_m": 0.0},
        "ecef": {
            "x_m": -2177918.171791884,
            "y_m": 4387964.5776559515,
            "z_m": 4070863.996633165,
        },
        "wgs84": {
            "longitude_deg": 116.397,
            "latitude_deg": 39.916,
            "ellipsoid_height_m": 43.0,
        },
        "geoid_separation_m": 0.0,
        "amsl_m": 43.0,
        "terrain_amsl_m": 43.0,
        "agl_m": 0.0,
    }


def _resolved_scenario() -> ResolvedScenario:
    raw = build_resolved_scenario(
        seed=SEED,
        providers=(
            ScenarioProvider(PROVIDER_ID, ("mission",), CAPABILITIES, "business_environment"),
            ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
        ),
        dynamic_provider_id="flight",
        verifier_id="logistics.verifier",
        world_id="world.city-demo",
    )
    # The package scene and the resolved scenario must agree on the native
    # physical aircraft, the WGS84 origin and the world asset digest so the
    # physical-observation derivation can bind the closed stage to the declared
    # facility pads without any arbitrary offset.  The shared scenario builder's
    # default (origin 0/0/0 on a ``uav.selfcheck`` entity) is therefore patched
    # in-place to the authored city frame; the ECEF position and the
    # ECEF<->ENU rotations are exactly the canonical ``EnuTransform`` values for
    # that origin (so the SceneStateAssembler accepts the frame authority), and
    # the canonical scenario digest is recomputed over the exact patched content
    # (matching ``scenario_digest_value`` semantics).
    frame_authority = raw["frame_authority"]  # type: ignore[index]
    frame_authority["origin"] = _city_origin_coordinate()
    frame_authority["origin_ecef"] = {
        "x_m": -2177918.171791884,
        "y_m": 4387964.5776559515,
        "z_m": 4070863.996633165,
    }
    frame_authority["ecef_to_enu_rotation"] = {
        "rows": [
            [-0.8957350400551208, -0.4445882792173576, 0.0],
            [0.285276222230631, -0.5747607849589736, 0.7669859953761633],
            [-0.3409929838681006, 0.6870162312899843, 0.6416638394804837],
        ]
    }
    frame_authority["enu_to_ecef_rotation"] = {
        "rows": [
            [-0.8957350400551208, 0.285276222230631, -0.3409929838681006],
            [-0.4445882792173576, -0.5747607849589736, 0.6870162312899843],
            [0.0, 0.7669859953761633, 0.6416638394804837],
        ]
    }
    raw["source_asset_digest"] = "ab" * 32
    origin_coordinate = _city_origin_coordinate()
    for entity in raw["entities"]:  # type: ignore[union-attr]
        if entity["entity_id"] == "uav.selfcheck":
            entity["entity_id"] = "uav.alpha"
            entity["owner_id"] = "flight"
            entity["source_provider_id"] = "flight"
            entity["initial_pose"]["position"] = origin_coordinate
        if entity["entity_id"] == "static.selfcheck":
            entity["initial_pose"]["position"] = origin_coordinate
    for launch_site in raw["launch_sites"]:  # type: ignore[union-attr]
        launch_site["primary_uav_entity_id"] = "uav.alpha"
        launch_site["allowed_uav_entity_ids"] = ["uav.alpha"]
        launch_site["pose"]["position"] = origin_coordinate
    digest_body = dict(raw)
    digest_body.pop("scenario_digest")
    raw["scenario_digest"] = hashlib.sha256(  # type: ignore[index]
        _canonical(digest_body)
    ).hexdigest()
    return ResolvedScenario.model_validate(raw)


def _write_run_inputs(
    root: Path,
) -> tuple[dict[str, dict[str, str]], ProviderWorkloadContract]:
    scenario = _resolved_scenario()
    task_package = _task_package_document()
    config_document = _config_document(task_package)
    config_path = root / "provider.config.json"
    config_path.write_bytes(_canonical(config_document) + b"\n")
    schema_path = root / "provider.schema.json"
    schema_path.write_bytes(
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n'
    )
    refs = {
        "config": {
            "path": "provider.config.json",
            "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        },
        "schema": {
            "path": "provider.schema.json",
            "sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
        },
    }
    contract = ProviderWorkloadContract.model_validate(
        {
            "schema_version": "aero-bench.workload-contract/v5",
            "role": "provider",
            "run_id": RUN_ID,
            "seed": SEED,
            "workload_id": PROVIDER_ID,
            "clock": {
                "authority": "provider_barrier",
                "step_ns": 1_000_000_000,
                "max_steps": 2,
                "provider_timeout_ms": 10_000,
            },
            "provider": {
                "provider_id": PROVIDER_ID,
                "adapter": "logistics.business",
                "port": PORT,
                "workload": {
                    "runtime": {
                        "image": RUNTIME_IMAGE,
                        "command": ["provider", "serve"],
                    },
                    "resources": {
                        "cpu_millicores": 1000,
                        "memory_mib": 1024,
                        "gpu_count": 0,
                    },
                    "implementation": {
                        "component_id": "logistics.business",
                        "kind": "mechanical_fixture",
                        "source_uri": "https://github.com/moby/moby",
                        "source_revision": "4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
                        "version": "test-fixture-1",
                    },
                },
                "config": {
                    "file": refs["config"],
                    "schema_file": refs["schema"],
                },
                "protocol_schema": refs["schema"],
                "capabilities": list(CAPABILITIES),
                "artifact_requirements": [ARTIFACT_REQUIREMENT],
            },
            "scenario_digest": scenario.scenario_digest,
            "scenario": scenario.model_dump(mode="json"),
            "scenario_assets": [
                asset.model_dump(mode="json")
                for asset in scenario_assets_for_workload(
                    scenario,
                    role="provider",
                    workload_id=PROVIDER_ID,
                )
            ],
        }
    )
    return refs, contract


def _authorized(payload: dict[str, object]) -> dict[str, object]:
    return {"session_token": SESSION_TOKEN, **payload}


def _prepare_payload(config_digest: str) -> dict[str, object]:
    task_package = _task_package_document()
    return _authorized(
        {
            "provider_id": PROVIDER_ID,
            "run_id": RUN_ID,
            "protocol_version": PROTOCOL_VERSION,
            "runtime_image": RUNTIME_IMAGE,
            "config_digest": config_digest,
            "artifact_requirements": [ARTIFACT_REQUIREMENT],
            "task_id": "logistics.task",
            "package_digest": hashlib.sha256(_canonical(task_package)).hexdigest(),
            "task_package": task_package,
            "capabilities": list(CAPABILITIES),
        }
    )


def _stage_request() -> BusinessEnvironmentStageRequest:
    scenario = _resolved_scenario()
    static_entity = next(
        entity for entity in scenario.entities if entity.state == "static"
    )
    static_scenario = scenario.model_copy(update={"entities": (static_entity,)})
    assembler = SceneStateAssembler(static_scenario)
    target = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
    barrier_fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": RUN_ID,
        "scenario_digest": scenario.scenario_digest,
        "at": target,
        "stage": "motion",
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
        "provider_ids": (),
        "receipts": (),
        "receipt_digests": (),
    }
    unsigned = StageBarrier.model_construct(
        **barrier_fields,
        barrier_digest="0" * 64,
    )
    barrier = StageBarrier(
        **barrier_fields,
        barrier_digest=stage_barrier_digest_value(unsigned),
    )
    scene_state = assembler.assemble(
        run_id=RUN_ID,
        at=target,
        barrier=barrier,
        contributions=(),
        previous_scene_state=None,
    )
    return BusinessEnvironmentStageRequest(
        schema_version="aero-bench.provider-stage-request/v1",
        run_id=RUN_ID,
        scenario_digest=scenario.scenario_digest,
        provider_id=PROVIDER_ID,
        target=target,
        stage="business_environment",
        scene_state=scene_state,
        scene_state_digest=scene_state.scene_state_digest,
        predecessor_barriers=(
            StageBarrierDigest(
                stage="motion",
                barrier_digest=barrier.barrier_digest,
            ),
        ),
    )


def _stage_payload() -> dict[str, object]:
    return _authorized(
        {
            "provider_id": PROVIDER_ID,
            "run_id": RUN_ID,
            "protocol_version": PROTOCOL_VERSION,
            "request": _stage_request().model_dump(mode="json"),
        }
    )


# -------------------------------------------------- physical-observation path
#
# These inputs are clearly synthetic but typed exactly as the runtime supplies
# them: one closed ``motion`` stage built around a genuine
# ``px4.state.v1`` ProviderEvent and ``aero-bench.state-sample/v1`` sample for
# the native vehicle ``uav.alpha``.  The aircraft is placed exactly
# ``pose_reference_above_contact_m`` (0.1 m) above pad 0 of ``facility-1``; the
# pose reference is explicitly declared and half-body-height is never inferred.

OBSERVATION_AT = SimulationTime(tick=1, sim_time_ns=1_000_000_000)


def _enquaternion(yaw_deg: float) -> ResolvedQuaternion:
    half = math.radians(yaw_deg) / 2.0
    return ResolvedQuaternion(
        qw=math.cos(half), qx=0.0, qy=0.0, qz=math.sin(half)
    )


def _observation_state_sample(
    *,
    run_id: str,
    scenario_digest: str,
    east_m: float,
    north_m: float,
    up_m: float,
    yaw_deg: float,
) -> StateSample:
    coordinate = ResolvedCoordinate(
        enu=ResolvedEnuPosition(east_m=east_m, north_m=north_m, up_m=up_m),
        ned=ResolvedNedPosition(north_m=north_m, east_m=east_m, down_m=-up_m),
        ecef=ResolvedEcefPosition(x_m=0.0, y_m=0.0, z_m=0.0),
        wgs84=ResolvedWgs84Position(
            longitude_deg=116.397,
            latitude_deg=39.916,
            ellipsoid_height_m=43.0 + up_m,
        ),
        geoid_separation_m=0.0,
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
        StateAttribute(name="collision_contact", value_type="bool", value=False),
        StateAttribute(name="contacts_complete", value_type="bool", value=True),
        StateAttribute(name="ground_contact", value_type="bool", value=True),
        StateAttribute(name="in_air", value_type="bool", value=False),
        StateAttribute(name="landed", value_type="bool", value=True),
        StateAttribute(name="landed_state", value_type="str", value="ON_GROUND"),
        StateAttribute(
            name="telemetry_source", value_type="str", value="gazebo-mavsdk"
        ),
    )
    candidate = StateSample.model_construct(
        schema_version="aero-bench.state-sample/v1",
        run_id=run_id,
        scenario_digest=scenario_digest,
        at=OBSERVATION_AT,
        stage="motion",
        entity_id="uav.alpha",
        provider_id="flight",
        sample_kind="dynamic",
        pose=pose,
        linear_velocity_enu=EnuLinearVelocity(
            east_mps=0.0, north_mps=0.0, up_mps=0.0
        ),
        linear_velocity_ned=NedLinearVelocity(
            north_mps=0.0, east_mps=0.0, down_mps=0.0
        ),
        angular_velocity_body=BodyAngularVelocity(
            x_radps=0.0, y_radps=0.0, z_radps=0.0
        ),
        mode="GUIDED",
        armed=True,
        contacts=("ground.pad.0",),
        attributes=attributes,
        sample_digest="0" * 64,
    )
    return StateSample.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "sample_digest": state_sample_digest_value(candidate),
        }
    )


def _observation_state_event_values(
    *,
    east_m: float,
    north_m: float,
    up_m: float,
    yaw_deg: float,
) -> dict[str, object]:
    pose = {
        "x_m": east_m,
        "y_m": north_m,
        "z_m": up_m,
        "roll_rad": 0.0,
        "pitch_rad": 0.0,
        "yaw_rad": math.radians(yaw_deg),
    }
    attitude = {"roll_rad": 0.0, "pitch_rad": 0.0, "yaw_rad": math.radians(yaw_deg)}
    velocity = {"north_m_s": 0.0, "east_m_s": 0.0, "down_m_s": 0.0}
    return {
        "vehicle_id": "uav.alpha",
        "pose_json": _canonical(pose).decode("utf-8"),
        "position_wgs84_json": _canonical(
            {"latitude_deg": 39.916, "longitude_deg": 116.397, "altitude_m": 43.0 + up_m}
        ).decode("utf-8"),
        "velocity_json": _canonical(velocity).decode("utf-8"),
        "angular_velocity_json": _canonical(
            {"x_rad_s": 0.0, "y_rad_s": 0.0, "z_rad_s": 0.0}
        ).decode("utf-8"),
        "attitude_json": _canonical(attitude).decode("utf-8"),
        "flight_mode": "GUIDED",
        "armed": True,
        "in_air": False,
        "landed": True,
        "landed_state": "ON_GROUND",
        "contacts_json": _canonical(["ground.pad.0"]).decode("utf-8"),
        "ground_contact": True,
        "battery_percent": 95.0,
        "health": _canonical({"is_global_position_ok": True}).decode("utf-8"),
        "collision_contact": False,
        "simulation_time_ns": OBSERVATION_AT.sim_time_ns,
        "evidence_path": "trajectory/evidences.jsonl",
        "evidence_sha256": "1" * 64,
    }


def _observation_scene_state(
    *,
    run_id: str,
    scenario_digest: str,
    samples: tuple[StateSample, ...],
) -> SceneState:
    samples = tuple(sorted(samples, key=lambda sample: sample.entity_id))
    receipt_fields = {
        "schema_version": "aero-bench.stage-receipt/v1",
        "run_id": run_id,
        "scenario_digest": scenario_digest,
        "at": OBSERVATION_AT,
        "stage": "motion",
        "provider_id": "flight",
        "state_digest": "a" * 64,
        "step_receipt_digest": "b" * 64,
        "contribution_digest": "c" * 64,
        "payload_digest": "d" * 64,
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
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
        "at": OBSERVATION_AT,
        "stage": "motion",
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
        "provider_ids": ("flight",),
        "receipts": (receipt,),
        "receipt_digests": (receipt.receipt_digest,),
    }
    unsigned_barrier = StageBarrier.model_construct(
        **barrier_fields, barrier_digest="0" * 64
    )
    barrier = StageBarrier(
        **barrier_fields,
        barrier_digest=stage_barrier_digest_value(unsigned_barrier),
    )
    scene_fields = {
        "schema_version": "aero-bench.scene-state/v1",
        "run_id": run_id,
        "scenario_digest": scenario_digest,
        "at": OBSERVATION_AT,
        "declared_entity_ids": tuple(sample.entity_id for sample in samples),
        "samples": samples,
        "stage_barrier": barrier,
        "contribution_digests": tuple(
            sorted(receipt.contribution_digest for receipt in barrier.receipts)
        ),
        "previous_scene_state_digest": (
            SCENE_STATE_ROOT_DIGEST if OBSERVATION_AT.tick == 1 else "f" * 64
        ),
    }
    unsigned_scene = SceneState.model_construct(
        **scene_fields, scene_state_digest="0" * 64
    )
    return SceneState(
        **scene_fields,
        scene_state_digest=scene_state_digest_value(unsigned_scene),
    )


def _observation_runtime_bindings() -> LogisticsRuntimeBindings:
    return LogisticsRuntimeBindings(
        schema_version="aero-bench.logistics-bindings/v1",
        aircraft=(
            LogisticsAircraftBinding(
                aircraft_id="fleet-alpha:1",
                fleet_entry_id="fleet-alpha",
                visual_asset_id="model:logistics-drone-v1",
                provider_id="flight",
                vehicle_id="uav.alpha",
                controlling_agent_id="agent.provider",
            ),
        ),
        principals=(),
    )


def _observation_batch():
    """Derive the typed observation batch for the synthetic closed stage."""
    scenario = _resolved_scenario()
    package = lower_logistics_task_package(_package_document())
    pad = facility_landing_pads(package.facilities.require("facility-1"))[0]
    up_m = pad.y + 0.1
    east_m = pad.x
    north_m = -pad.z
    yaw_deg = 15.0
    scenario_digest = scenario.scenario_digest

    sample = _observation_state_sample(
        run_id=RUN_ID,
        scenario_digest=scenario_digest,
        east_m=east_m,
        north_m=north_m,
        up_m=up_m,
        yaw_deg=yaw_deg,
    )
    scene_state = _observation_scene_state(
        run_id=RUN_ID,
        scenario_digest=scenario_digest,
        samples=(sample,),
    )
    values = _observation_state_event_values(
        east_m=east_m, north_m=north_m, up_m=up_m, yaw_deg=yaw_deg
    )
    event = ProviderEvent(
        provider_id="flight",
        event_id=f"state.uav.alpha.{OBSERVATION_AT.tick}",
        time=OBSERVATION_AT,
        payload_schema_id="px4.state.v1",
        payload=tuple(
            NamedValue(name=name, value=value) for name, value in values.items()
        ),
    )
    observations = derive_physical_observations(
        scene_state=scene_state,
        events=(event,),
        package=package,
        bindings=_observation_runtime_bindings(),
        scenario=scenario,
        spec=LogisticsObservationSpec(
            schema_version="aero-bench.logistics-observation-spec/v1",
            items=(
                LogisticsObservationSpecItem(
                    aircraft_id="fleet-alpha:1",
                    facility_id="facility-1",
                    pad_index=0,
                ),
            ),
        ),
        pose_references=(
            DeclaredAircraftPoseReference(
                aircraft_id="fleet-alpha:1",
                pose_reference_above_contact_m=0.1,
            ),
        ),
        tolerances=PresenceTolerances(
            vertical_tolerance_m=0.05,
            horizontal_uncertainty_m=0.0,
            max_stationary_speed_m_s=0.5,
        ),
        stage_barriers=(scene_state.stage_barrier,),
        expected_run_id=RUN_ID,
        target=OBSERVATION_AT,
    )
    return build_observation_batch(
        observations=observations,
        run_id=RUN_ID,
        scenario_digest=scenario_digest,
        at=OBSERVATION_AT,
        source_scene_state_digest=scene_state.scene_state_digest,
        source_stage_barrier_digest=scene_state.stage_barrier.barrier_digest,
    )


def _observation_ingest_payload(batch) -> dict[str, object]:
    return _authorized(
        {
            "provider_id": PROVIDER_ID,
            "run_id": RUN_ID,
            "protocol_version": PROTOCOL_VERSION,
            "batch": batch.model_dump(mode="json"),
        }
    )


def _command_payload(
    command_id: str,
    agent_id: str,
    tool_id: str,
    **arguments: object,
) -> dict[str, object]:
    return _authorized(
        {
            "provider_id": PROVIDER_ID,
            "run_id": RUN_ID,
            "request": {
                "run_id": RUN_ID,
                "command_id": command_id,
                "agent_id": agent_id,
                "tool_id": tool_id,
                "issued_at": {"tick": 1, "sim_time_ns": 1_000_000_000},
                "arguments": [
                    {"name": name, "value": value}
                    for name, value in sorted(arguments.items())
                ],
            },
        }
    )


def _query_payload(kind: str, order_id: str | None = None) -> dict[str, object]:
    query: dict[str, object] = {
        "run_id": RUN_ID,
        "query_id": f"query.{kind}",
        "kind": kind,
        "issued_at": {"tick": 1, "sim_time_ns": 1_000_000_000},
    }
    if order_id is not None:
        query["order_id"] = order_id
    return _authorized(
        {
            "provider_id": PROVIDER_ID,
            "run_id": RUN_ID,
            "query": query,
        }
    )


async def _drive_checks(identity: WorkloadIdentity) -> dict[str, object]:
    transport = await JsonLineRpcTransport.connect(
        RuntimeEndpoint(host="127.0.0.1", port=PORT),
        component="logistics-business-selfcheck",
    )
    try:
        probe = await transport.request("probe", {})
        _require(probe["status"] == "accepting", "probe must be accepted")

        prepare = await transport.request("prepare", _prepare_payload(identity.config_digest))
        _require(prepare["status"] == "ready", "prepare must become ready")

        reset = await transport.request(
            "reset",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "seed": SEED,
                }
            ),
        )
        _require(reset["receipt"]["reached"] == {"tick": 0, "sim_time_ns": 0}, "reset time")

        stage = await transport.request("step_stage", _stage_payload())
        _require(
            stage["result"]["step_receipt"]["reached"]
            == {"tick": 1, "sim_time_ns": 1_000_000_000},
            "staged step must reach tick 1",
        )

        # ---- private physical-observation ingress over the real wire --------
        # The batch is derived from the synthetic closed motion stage above
        # under the explicitly declared pose reference.  The first ingest must
        # journal exactly one immutable record; an identical replay must dedupe
        # atomically without mutating the journal.
        observation_batch = _observation_batch()
        _require(
            (observation_batch.at.tick, observation_batch.at.sim_time_ns)
            == (1, 1_000_000_000),
            "observation batch must be bound to the staged tick",
        )
        ingest = await transport.request(
            LOGISTICS_OBSERVATION_INGEST_OPERATION,
            _observation_ingest_payload(observation_batch),
        )
        _require(
            ingest["result"]["replayed"] is False
            and ingest["result"]["sequence_count"] == 1,
            "first observation ingest must journal exactly one record",
        )
        replay = await transport.request(
            LOGISTICS_OBSERVATION_INGEST_OPERATION,
            _observation_ingest_payload(observation_batch),
        )
        _require(
            replay["result"]["replayed"] is True
            and replay["result"]["sequence_count"] == 1,
            "identical observation replay must deduplicate atomically",
        )
        _require(
            ingest["result"]["journal_digest"]
            == replay["result"]["journal_digest"],
            "observation journal digest must be stable across replay",
        )

        offer = await transport.request(
            "command",
            _command_payload(
                "command.offer",
                "logistics.dispatcher",
                "logistics.order.offer",
                order_id="order-1",
                assignee_id="fleet-alpha:1",
                actor_id="logistics.dispatcher",
                expected_version=0,
            ),
        )
        _require(
            [receipt["phase"] for receipt in offer["receipts"]]
            == ["received", "accepted", "applied", "completed"],
            "offer must complete",
        )

        accept = await transport.request(
            "command",
            _command_payload(
                "command.accept",
                "agent.provider",
                "logistics.order.accept",
                order_id="order-1",
                actor_id="fleet-alpha:1",
                expected_version=1,
            ),
        )
        _require(
            [receipt["phase"] for receipt in accept["receipts"]]
            == ["received", "accepted", "applied", "completed"],
            "accept must complete",
        )

        assign = await transport.request(
            "command",
            _command_payload(
                "command.assign",
                "logistics.dispatcher",
                "logistics.order.assign",
                order_id="order-1",
                assignee_id="fleet-alpha:1",
                actor_id="logistics.dispatcher",
                capacity_kg=5,
                expected_version=2,
            ),
        )
        _require(
            [receipt["phase"] for receipt in assign["receipts"]]
            == ["received", "accepted", "applied", "completed"],
            "assign must complete within the declared aircraft payload",
        )

        pickup = await transport.request(
            "command",
            _command_payload(
                "command.pickup",
                "agent.provider",
                "logistics.order.pick_up",
                order_id="order-1",
                expected_version=3,
                evidence_ref="evidence.photo.1",
            ),
        )
        _require(
            [receipt["phase"] for receipt in pickup["receipts"]] == ["received", "failed"],
            "physical pickup must be refused with received, failed",
        )
        _require(
            "physical evidence interface pending" in pickup["receipts"][-1]["detail"],
            "physical pickup refusal must name the pending evidence interface",
        )

        create = await transport.request(
            "command",
            _command_payload(
                "command.create-online",
                "logistics.business",
                LOGISTICS_ORDER_CREATE_TOOL,
                order_id="order-online",
                origin_facility_id="facility-1",
                destination_facility_id="facility-2",
                hub_handoff_facility_id="hub-3",
                cargo_mass_kg=1,
                release_time_s=1,
                deadline_s=900,
                actor_id="logistics.business",
            ),
        )
        _require(
            [receipt["phase"] for receipt in create["receipts"]]
            == ["received", "accepted", "applied", "completed"],
            "online order creation must complete",
        )
        _require(
            create["arrival_record"]["kind"] == "created",
            "online order creation must append a created arrival record",
        )
        _require(
            create["arrival_record"]["source_identity"] == "logistics.business",
            "online order creation source must be the authenticated principal",
        )
        _require(
            create["arrival_record"]["event_id"] == "command.create-online",
            "online order creation arrival record id must equal the command id",
        )

        query = await transport.request("query", _query_payload("orders"))
        orders = query["result"]["orders"]
        _require(len(orders) == 2, "two orders must be queryable after creation")
        _require(orders[0]["order_id"] == "order-1", "order id")
        _require(orders[0]["status"] == "assigned", "order must be assigned")
        _require(orders[1]["order_id"] == "order-online", "online order id")
        _require(orders[1]["status"] == "created", "online order must be created")

        detail = await transport.request("query", _query_payload("order", "order-1"))
        _require(
            detail["result"]["orders"][0]["hub_handoff_facility_id"] == "hub-3",
            "hub handoff facility",
        )

        facilities = await transport.request("query", _query_payload("facilities"))
        _require(
            len(facilities["result"]["facilities"]) == 4,
            "facility catalogue must expose four facilities",
        )

        first_snapshot = await transport.request(
            "snapshot",
            _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID}),
        )
        second_snapshot = await transport.request(
            "snapshot",
            _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID}),
        )
        _require(
            first_snapshot["snapshot_digest"] == second_snapshot["snapshot_digest"],
            "snapshot digest must be deterministic",
        )
        _require(
            first_snapshot["snapshot_digest"] != "0" * 64,
            "snapshot digest must be non-placeholder",
        )

        finalize = await transport.request(
            "finalize",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "request": ProviderFinalizationRequest(
                        schema_version="aero-bench.provider-finalization-request/v1",
                        run_id=RUN_ID,
                        terminal_event="run.completed",
                        terminal_time=SimulationTime(
                            tick=1, sim_time_ns=1_000_000_000
                        ),
                        event_chain_root="d" * 64,
                    ).model_dump(mode="json"),
                }
            ),
        )
        _require(
            finalize["receipt"]["artifacts"][0]["artifact_id"] == "artifact.logistics",
            "finalization artifact id",
        )

        shutdown = await transport.request(
            "shutdown",
            _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID}),
        )
        _require(shutdown["status"] == "stopped", "shutdown must acknowledge stopped")

        return {
            "history": assign["history_record"],
            "snapshot_digest": first_snapshot["snapshot_digest"],
        }
    finally:
        await transport.close()


async def _run_selfcheck() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "artifacts").mkdir()
        refs, contract = _write_run_inputs(root)
        identity = WorkloadIdentity(
            run_id=RUN_ID,
            provider_id=PROVIDER_ID,
            provider_port=PORT,
            runtime_image=RUNTIME_IMAGE,
            config_digest=refs["config"]["sha256"],
            artifact_requirement=ARTIFACT_REQUIREMENT,
            seed=SEED,
            contract=contract,
        )
        service = LogisticsBusinessProviderService(
            bundle_root=root,
            artifact_root=root / "artifacts",
            rpc_port=PORT,
            workload_identity=identity,
            session_token=SESSION_TOKEN,
        )
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=PORT)
        try:
            await _drive_checks(identity)
        finally:
            await server.graceful_close()
            await handle.wait_closed()

        artifact = root / "artifacts" / "logistics/state.json"
        _require(artifact.is_file(), "artifact was not written")
        document = json.loads(artifact.read_text(encoding="utf-8"))
        history = document["history"]
        _require(len(history) == 3, "business history must hold three records")
        _require(
            document["logistics_history_root"] == history[-1]["record_hash"],
            "history root mismatch",
        )
        _require(
            document["ledger"]["orders"][0]["status"] == "assigned",
            "ledger order must be assigned",
        )
        previous_hash = "0" * 64
        for record in history:
            _require(
                record["previous_hash"] == previous_hash,
                "history hash chain is broken",
            )
            previous_hash = record["record_hash"]
        # The artifact must seal the full causal arrival journal, the canonical
        # snapshot digest, and the exact baseline binding needed for replay.
        arrival = document["arrival"]
        arrival_snapshot = document["arrival_snapshot"]
        arrival_snapshot_digest = document["arrival_snapshot_digest"]
        _require(
            arrival_snapshot_digest == service._arrival.snapshot.canonical_digest(),
            "artifact arrival snapshot digest must equal the live snapshot",
        )
        created_ids = tuple(
            record["order_id"] for record in arrival if record["kind"] == "created"
        )
        _require(
            created_ids == ("order-1", "order-online"),
            "artifact arrival journal must record baseline and online creation",
        )
        _require(
            document["initial_requests"]
            == [
                request.model_dump(mode="json")
                for request in service._initial_requests
            ],
            "artifact initial_requests must bind the exact package baseline",
        )
        _require(
            document["initial_time"] == {"tick": 0, "sim_time_ns": 0},
            "artifact initial_time must be the reset instant",
        )
        replayed_snapshot = replay_order_arrivals(
            catalogue=service._arrival.catalogue,
            fleet=service._arrival.fleet,
            actor_grants=service._arrival.actor_grants,
            initial_requests=tuple(
                OrderRequest.model_validate(item)
                for item in document["initial_requests"]
            ),
            initial_time=SimulationTime.model_validate(document["initial_time"]),
            events=tuple(
                OrderArrivalEvent.model_validate(item) for item in document["arrival"]
            ),
            current_time=SimulationTime(tick=1, sim_time_ns=1_000_000_000),
        )
        _require(
            replayed_snapshot.canonical_digest() == arrival_snapshot_digest,
            "artifact arrival journal must replay to the sealed snapshot",
        )
        _require(
            replayed_snapshot.model_dump(mode="json") == arrival_snapshot,
            "artifact arrival snapshot body must match the replay",
        )
        _require(
            replayed_snapshot.version == len(replayed_snapshot.events),
            "arrival snapshot version must equal the journal length",
        )
        # The finalized artifact must also persist the immutable observation
        # journal (exactly one record from the synthetic closed stage) sealed by
        # its canonical digest.
        observation_journal = document["observation_journal"]
        _require(
            isinstance(observation_journal, dict)
            and len(observation_journal["records"]) == 1,
            "artifact must persist exactly one observation record",
        )
        observation_record = observation_journal["records"][0]
        _require(
            observation_record["sequence"] == 1,
            "observation journal sequence must start at one",
        )
        _require(
            observation_record["observation"]["assessment"]["eligible"] is True,
            "observation record must carry the eligible presence assessment",
        )
        persisted_journal = ObservationJournal.model_validate(observation_journal)
        _require(
            document["observation_journal_digest"]
            == persisted_journal.canonical_digest(),
            "artifact observation journal digest must seal the journal",
        )
    print("logistics-business selfcheck: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(_run_selfcheck())
    except SelfcheckError as error:
        print(f"logistics-business selfcheck: FAILED: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
