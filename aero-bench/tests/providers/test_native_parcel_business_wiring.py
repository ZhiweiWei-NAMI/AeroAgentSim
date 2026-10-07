"""One focused actual service-wire test for the native parcel business slice.

A **synthetic test** over the real JSON-line service + the real
``NativeParcelBusinessProvider`` adapter (same harness mechanics as
``test_logistics_business_observations.py``): module evidence, NOT native
execution — no PX4/Gazebo, no executor, no resolver wiring, no run launch.

Covers, on the real wire:
- authority: the native-parcel adapter identity is enforced by the real
  service config loader, and the parcel command path enforces the carrier
  principal grant (a foreign principal is refused with zero machine mutation);
- transition lifecycle: two-second pickup dwell -> airborne carriage ->
  two-second dropoff dwell -> completed receipts;
- same-frame projection: ``parcel_scene_frame`` at the exact current service
  tick revalidates the typed frame against the live projection;
- retry: identical ingest and an identical command are deduplicated — no
  second admitted action, no custody change.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from aero_bench.config.models import (
    ArtifactRequirement,
    EnvironmentSpec,
    FileRef,
    ImplementationIdentity,
    NamedValue,
    ProviderRef,
    SchemaBoundFile,
    TaskSpec,
)
from aero_bench.executor.contracts import ProviderWorkloadContract
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.logistics_business.native_parcel import (
    NATIVE_PARCEL_ADAPTER,
    NATIVE_PARCEL_CAPABILITY,
    NativeParcelBusinessConfig,
    NativeParcelBusinessProvider,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import JsonLineRpcServer
from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageRequest,
    CommandRequest,
    SCENE_STATE_ROOT_DIGEST,
    SceneState,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    stage_barrier_digest_value,
    scene_state_digest_value,
)
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.config.resolver import _derive_artifact_requirements
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import lower_logistics_task_package
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.native_parcel_contract import (
    ATTACHMENT_FRAME_NOTE,
    NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
    SEALED_VERIFIER_REPLAY_REQUIREMENT,
)
from aero_bench.tasks.logistics.native_parcel_rpc import (
    PARCEL_DROPOFF_TOOL,
    PARCEL_PICKUP_TOOL,
)
from aero_bench.tasks.logistics.runtime_bindings import (
    validate_logistics_runtime_bindings,
)
from aero_bench.world.resolved import scenario_assets_for_workload

from aero_bench.world.contracts import ProviderRequirement
from aero_bench.world.resolved import (
    ResolvedTaskScenarioProjection,
    compile_resolved_scenario,
    scenario_assets_for_workload,
)

from tests.providers.test_logistics_business_service import (
    _SERVICE,
    CAPABILITIES,
    IMAGE,
    PROVIDER_ID,
    SEED,
    SESSION_TOKEN,
    _requirement,
    _resolved_scenario,
)
from tests.tasks.test_logistics_package import (
    _facilities,
    _vertiport,
)
from tests.tasks.test_logistics_runtime_bindings import (
    _bindings_document,
    _build_task,
    _capability_map,
    _dispatcher_agent,
    _fixture_runtime,
    _make_px4_gazebo_environment,
    _order,
    _package_document,
    _placeholder_ref,
    _px4_config_document,
    _world_with_uav_ids,
)
from tests.tasks.test_logistics_runtime_hook import (
    AT,
    _CohesiveFixture,
    _build_resolved_run,
    _scene_state,
    _state_event,
    _state_event_values,
    _state_sample,
)
from tests.support import fixture_provider_registry
from tests.world.support import materialize_world_package

_NATIVE_PROVIDER_ID = "logistics.native-business"
_NATIVE_PORT = 18441
_NATIVE_PRINCIPAL = "uav.native.carrier"
_FOREIGN_PRINCIPAL = "uav.native.stranger"
_TASK_ID = "logistics.native-parcel-wire.v1"
_ORDER_ID = "order.native.wire"
_PARCEL_ID = "parcel.native.wire"
_CARRIER_ID = "fleet-alpha:1"
_PICKUP_FACILITY = "facility-1"
_DROPOFF_FACILITY = "facility-2"
_NATIVE_MAX_STEPS = 8
# The real service source this test imports and drives over the loopback.
_SERVICE_SHA256 = (
    "f3f415eae1945d89818cae23faf8302d7a1b7c936770036e261a10277dc0e27b"
)


def _native_implementation_identity() -> ImplementationIdentity:
    """The ONE shared native fixture implementation identity.

    ``component_id`` is the native adapter id (``logistics.native-parcel``) —
    the production invariant ``workload.implementation.component_id == adapter``
    makes these the same declaration everywhere (ProviderRef, contract and
    manifest). The source identifies the actual loaded service file: the
    ``containers/logistics-business/service.py`` bytes this test imports via
    ``tests.providers.test_logistics_business_service._SERVICE``, pinned by
    blob hash and measured file SHA-256 instead of a placeholder source.
    """
    return ImplementationIdentity(
        component_id=NATIVE_PARCEL_ADAPTER,
        kind="mechanical_fixture",
        source_uri=(
            "https://api.github.com/repos/ZhiweiWei-NAMI/AeroAgentSim/git/blobs/"
            "e72a6ea13577d8772ab6846e31a2f4271893dc57"
        ),
        source_revision="e72a6ea13577d8772ab6846e31a2f4271893dc57",
        version=f"native-parcel-wire-fixture-1+{_SERVICE_SHA256[:16]}",
    )


def _native_capabilities() -> tuple[str, ...]:
    """The ONE native capability declaration.

    The original business capabilities (same mission role) PLUS the native
    parcel authority — exactly what the world requirement, the environment
    ProviderRef, the client manifest and the workload contract declare.
    """
    return tuple(sorted(CAPABILITIES)) + (NATIVE_PARCEL_CAPABILITY,)


def _native_requirement() -> ArtifactRequirement:
    """The native business artifact requirement.

    The existing fixture requirement with ONLY its producer re-identified to
    the native provider id, so the service loader's
    ``producer_id == provider_id`` check holds for the native identity.
    """
    return ArtifactRequirement.model_validate(
        {
            **_requirement(),
            "producer_id": _NATIVE_PROVIDER_ID,
        }
    )


def _native_observation_batch(
    fixture: _CohesiveFixture,
    *,
    at: SimulationTime,
    previous_scene_state: SceneState | None,
    rest_facility_id: str = _PICKUP_FACILITY,
    airborne: bool = False,
):
    """Derive both pad records from one carrier sample at the submitted tick.

    Use the native config's pose calibration, tolerances and observation plan.
    Bind the SceneState to the previously submitted state; reassess both pads
    against the same physical source. Tick 4 uses an airborne source.
    """
    from aero_bench.tasks.logistics.facility_presence import (
        assess_facility_presence,
    )
    from aero_bench.tasks.logistics.observation_ingress import (
        build_observation_batch,
        derive_physical_observations,
    )

    observation = fixture.business_config.observation
    assert observation is not None
    spec = observation.spec
    run_id = fixture.resolved_run.run_id
    scenario_digest = fixture.scenario.scenario_digest

    rest_pad = facility_landing_pads(
        fixture.package.facilities.require(rest_facility_id)
    )[0]
    up_m = rest_pad.y + observation.pose_references[0].pose_reference_above_contact_m
    if airborne:
        up_m += 10.0
    contact_values = {
        "landed": not airborne,
        "in_air": airborne,
        "landed_state": "IN_AIR" if airborne else "ON_GROUND",
        "ground_contact": not airborne,
        "contacts": () if airborne else ("ground.pad.0",),
    }
    sample = _state_sample(
        at,
        run_id=run_id,
        scenario_digest=scenario_digest,
        east_m=rest_pad.x,
        north_m=-rest_pad.z,
        up_m=up_m,
        yaw_deg=15.0,
        **contact_values,
    )
    scene_state = _scene_state(
        run_id=run_id,
        scenario_digest=scenario_digest,
        at=at,
        samples=(sample,),
    )
    scene_state = scene_state.model_copy(update={
        "previous_scene_state_digest": (
            SCENE_STATE_ROOT_DIGEST if at.tick == 1
            else previous_scene_state.scene_state_digest
        ),
    })
    scene_state = SceneState.model_validate({
        **scene_state.model_dump(mode="json"),
        "scene_state_digest": scene_state_digest_value(scene_state),
    })
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=rest_pad.x,
        north_m=-rest_pad.z,
        up_m=up_m,
        yaw_deg=15.0,
        **contact_values,
        simulation_time_ns=at.sim_time_ns,
    )
    event = _state_event(at, values=values)
    observations = derive_physical_observations(
        scene_state=scene_state,
        events=(event,),
        package=fixture.package,
        bindings=observation.bindings,
        scenario=fixture.scenario,
        spec=spec,
        pose_references=observation.pose_references,
        tolerances=observation.tolerances,
        stage_barriers=(scene_state.stage_barrier,),
        expected_run_id=run_id,
        target=at,
    )
    # The approved assertion: every recomputed pad assessment must exactly
    # match an independent recomputation under the SAME native config — the
    # declared pad, the record's own sample/profile/event binding and the
    # config's declared tolerances. This mirrors the service's own gate
    # without reading or copying its expected assessment.
    for record in observations:
        recomputed = assess_facility_presence(
            pad=record.pad,
            sample=record.sample,
            profile=record.profile,
            event=record.event_binding,
            tolerances=observation.tolerances,
        )
        assert recomputed == record.assessment, (
            "recomputed pad assessment does not match the derived record "
            f"for aircraft {record.aircraft_id!r} facility "
            f"{record.facility_id!r} pad {record.pad_index}"
        )
    return scene_state, build_observation_batch(
        observations=observations,
        run_id=run_id,
        scenario_digest=scenario_digest,
        at=at,
        source_scene_state_digest=scene_state.scene_state_digest,
        source_stage_barrier_digest=scene_state.stage_barrier.barrier_digest,
    )


# ------------------------------------------------------------- config fixture


def _native_contract_document() -> dict[str, object]:
    """The native parcel contract document bound to the synthetic package."""
    return {
        "schema_version": NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
        "contract_id": "native.parcel.wire",
        "identities": {
            "schema_version": NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
            "task_id": _TASK_ID,
            "order_id": _ORDER_ID,
            "parcel_entity_id": _PARCEL_ID,
            "carrier_entity_id": _CARRIER_ID,
            "authorized_principal_id": _NATIVE_PRINCIPAL,
            "pickup_facility_id": _PICKUP_FACILITY,
            "dropoff_facility_id": _DROPOFF_FACILITY,
        },
        "carrier": {
            "schema_version": NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
            "carrier_entity_id": _CARRIER_ID,
            "fleet_entry_id": "fleet-alpha",
            "visual_asset_id": "model:logistics-drone-v1",
            "provider_id": "flight",
            "native_vehicle_id": "uav.alpha",
            "pose_reference_above_contact_m": 0.1,
        },
        "attachment": {
            "schema_version": NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
            "parcel_entity_id": _PARCEL_ID,
            "carrier_entity_id": _CARRIER_ID,
            "offset_x_m": 0.0,
            "offset_y_m": -0.2,
            "offset_z_m": 0.0,
            "orientation": {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0},
            "frame_note": ATTACHMENT_FRAME_NOTE,
        },
        "policy": {
            "schema_version": NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
            "minimum_pickup_dwell_s": 2.0,
            "minimum_dropoff_dwell_s": 2.0,
            "vertical_tolerance_m": 0.15,
            "horizontal_uncertainty_m": 0.1,
            "max_stationary_speed_m_s": 0.2,
        },
        "sealed_verifier_replay_requirement": SEALED_VERIFIER_REPLAY_REQUIREMENT,
    }


def _native_config_document(package) -> dict[str, object]:
    """The synthetic native-parcel config document the real service loads.

    ``package`` is THE package lowered from the SAME declaration document the
    task/world/environment/scenario are built from (no second lowering), so
    the config's task_package, pads and order binding are exactly the ones the
    task pin pins. ``NativeParcelBusinessConfig`` cross-validates: one
    canonical order, both exact declared pads, the two-pad observation plan,
    matching tolerances, matching carrier calibration and the
    principal-to-carrier grant.
    """
    pickup_pad = facility_landing_pads(package.facilities.require(_PICKUP_FACILITY))[0]
    dropoff_pad = facility_landing_pads(
        package.facilities.require(_DROPOFF_FACILITY)
    )[0]
    quad = {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0}
    return {
        "schema_version": "aero-bench.logistics-native-business/v1",
        "scheduled_orders": [],
        "provider_id": _NATIVE_PROVIDER_ID,
        "task_package": package.model_dump(mode="json"),
        "observation": {
            "schema_version": "aero-bench.logistics-observation-config/v1",
            "bindings": _bindings_document(
                aircraft=[
                    {
                        "aircraft_id": _CARRIER_ID,
                        "fleet_entry_id": "fleet-alpha",
                        "visual_asset_id": "model:logistics-drone-v1",
                        "provider_id": "flight",
                        "vehicle_id": "uav.alpha",
                        "controlling_agent_id": _NATIVE_PRINCIPAL,
                    },
                ],
                principals=[],
            ),
            "pose_references": [
                {"aircraft_id": _CARRIER_ID, "pose_reference_above_contact_m": 0.1},
            ],
            "tolerances": {
                "vertical_tolerance_m": 0.15,
                "horizontal_uncertainty_m": 0.1,
                "max_stationary_speed_m_s": 0.2,
            },
            "spec": {
                "schema_version": "aero-bench.logistics-observation-spec/v1",
                "items": [
                    {
                        "aircraft_id": _CARRIER_ID,
                        "facility_id": _PICKUP_FACILITY,
                        "pad_index": 0,
                    },
                    {
                        "aircraft_id": _CARRIER_ID,
                        "facility_id": _DROPOFF_FACILITY,
                        "pad_index": 0,
                    },
                ],
            },
        },
        "principal_bindings": [
            {
                "principal_id": _NATIVE_PRINCIPAL,
                "actor_id": _CARRIER_ID,
                "role": "aircraft_agent",
            },
        ],
        "native_parcel": {
            "schema_version": "aero-bench.native-parcel-session/v1",
            "contract": _native_contract_document(),
            "pickup_pad": pickup_pad.model_dump(mode="json"),
            "dropoff_pad": dropoff_pad.model_dump(mode="json"),
            "initial_parcel_pose": {
                "x_m": pickup_pad.x,
                "y_m": pickup_pad.y,
                "z_m": pickup_pad.z,
                "orientation": quad,
            },
            "final_parcel_pose": {
                "x_m": dropoff_pad.x,
                "y_m": dropoff_pad.y,
                "z_m": dropoff_pad.z,
                "orientation": quad,
            },
            "step_ns": 1_000_000_000,
            "max_steps": _NATIVE_MAX_STEPS,
            "calibration_digest": "ab" * 32,
        },
    }


def _native_manifest(config_digest: str) -> ProviderManifest:
    return ProviderManifest(
        provider_id=_NATIVE_PROVIDER_ID,
        adapter=NATIVE_PARCEL_ADAPTER,
        implementation=_native_implementation_identity(),
        runtime_image=IMAGE,
        config_digest=config_digest,
        capabilities=_native_capabilities(),
        protocol_schema=FileRef(path="protocol.json", sha256="f" * 64),
        artifact_requirements=(_native_requirement(),),
    )


def _write_native_bundle(root: Path, config_document: dict) -> dict:
    """Real digest-pinned config + schema bytes for the native provider."""
    config_path = root / "native.provider.config.json"
    config_path.write_bytes(canonical_json_bytes(config_document) + b"\n")
    schema_path = root / "native.provider.schema.json"
    schema_path.write_bytes(
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n'
    )
    return {
        "config": {
            "path": "native.provider.config.json",
            "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        },
        "schema": {
            "path": "native.provider.schema.json",
            "sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
        },
    }


def _native_contract(refs: dict, port: int, scenario, *, run_id: str
                     ) -> ProviderWorkloadContract:
    """The workload contract pinned to the native adapter identity."""
    return ProviderWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="provider",
        run_id=run_id,
        seed=SEED,
        workload_id=_NATIVE_PROVIDER_ID,
        clock={
            "authority": "provider_barrier",
            "step_ns": 1_000_000_000,
            "max_steps": _NATIVE_MAX_STEPS,
            "provider_timeout_ms": 10_000,
        },
        provider={
            "provider_id": _NATIVE_PROVIDER_ID,
            "adapter": NATIVE_PARCEL_ADAPTER,
            "port": port,
            "workload": _fixture_runtime(_NATIVE_PROVIDER_ID)
            .model_copy(
                update={"implementation": _native_implementation_identity()},
            )
            .model_dump(mode="json"),
            "config": {"file": refs["config"], "schema_file": refs["schema"]},
            "protocol_schema": refs["schema"],
            "capabilities": list(_native_capabilities()),
            "artifact_requirements": [_native_requirement()],
        },
        scenario_digest=scenario.scenario_digest,
        scenario=scenario,
        scenario_assets=scenario_assets_for_workload(
            scenario,
            role="provider",
            workload_id=_NATIVE_PROVIDER_ID,
        ),
    )


def _native_world_mutate() -> Callable[[dict, Path], None]:
    """The shared world mutator with the business requirement re-identified.

    Mirrors the hook fixture's ``_world_mutate`` and the shared world default
    (provider_requirements sorted by provider_id), with ONLY the original
    business ProviderRequirement replaced during materialize_world_package:
    native provider id, the SAME mission role, the original required
    capabilities PLUS the native parcel authority; the four other provider
    requirements stay untouched.
    """
    business_req = ProviderRequirement(
        provider_id=_NATIVE_PROVIDER_ID,
        roles=("mission",),
        required_capability_ids=(
            tuple(sorted(CAPABILITIES)) + (NATIVE_PARCEL_CAPABILITY,)
        ),
    )

    def mutate(kwargs: dict, root: Path) -> None:
        _world_with_uav_ids(("uav.alpha",))(kwargs, root)
        reqs = [
            req
            for req in kwargs["provider_requirements"]
            if req.provider_id != PROVIDER_ID
        ]
        reqs.append(business_req)
        kwargs["provider_requirements"] = tuple(
            sorted(reqs, key=lambda r: r.provider_id)
        )

    return mutate


def _native_cohesive_fixture(root: Path) -> _CohesiveFixture:
    """The declaration-derived cohesive fixture with the native business provider.

    The SAME declaration pipeline as the hook fixture's ``build_fixture`` —
    ONE materialize_world_package world, the SAME map/assets/launch site, the
    same 18 m two-pad vertiport geometry and single canonical order — with
    ONLY the original business ProviderRequirement re-identified at the
    declaration stage (``_native_world_mutate``). The environment ProviderRef,
    the resolved scenario, the bindings and the resolved run are then ALL
    derived from that one new world; no scenario entry is patched after
    compilation and no stale provider-id/hash/tuple is reused.
    """
    root.mkdir(parents=True, exist_ok=True)
    reader, world_ref, world = materialize_world_package(
        root,
        mutate_kwargs=_native_world_mutate(),
    )
    raw = _package_document(aircraft_count=1)
    raw["noFlyZones"] = []
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    # The single synthetic geometry correction (the SAME one the approved
    # native fixture carried): a 3-pad vertiport row at 5.4 m pitch needs an
    # 18 m footprint, so BOTH declared pad-bearing fixture facilities are
    # widened to widthM=18 through the SAME authored _vertiport helper —
    # pickup (facility-1) AND dropoff (facility-2), because the native slice
    # derives landing pads from both. Parking slots, pad pitch and every
    # other declared value stay untouched; the correction lives in the ONE
    # declaration document so task, package, config, scenario assets and the
    # service bundle all share it.
    facilities = _facilities()
    # Native-approved layout repair: the two generic fixture facilities were
    # both authored at (10, -20), so their 18 m pads overlapped and BOTH pad
    # observations were correctly eligible for one resting pose. The native
    # dropoff facility is re-authored at (30, -20) (pickup stays (10, -20));
    # both keep widthM=18, depthM=8, rotationDeg=0, pad index 0. The authored
    # world range east [-200, 500] / north [-200, 500] legally contains both
    # footprints (pickup x in [1, 19], dropoff x in [21, 39], north [16, 24]),
    # and standard geometry then derives the distinct dropoff pad center
    # (x=24.6 m) — a derived pad is never patched.
    facilities[0] = _vertiport(widthM=18)
    facilities[1] = _vertiport(id="facility-2", widthM=18, position={"x": 30, "z": -20})
    raw["facilities"] = facilities
    # The approved native actor set: exactly the parcel carrier, no dispatcher
    # or business actor (the native slice carries its own principal grant).
    raw["actors"] = [{"actor_id": _CARRIER_ID, "role": "aircraft_agent"}]
    # The approved single canonical order re-identified for the native slice;
    # its facility binding already matches the native pickup/dropoff ids.
    raw["orders"] = [_order(id=_ORDER_ID)]
    # Native restoration of the established declaration (approved): the task
    # identity is re-pinned BEFORE _build_task / lowering / config / scenario,
    # so every derived ID and pin still flows from the existing compiler chain.
    raw["task_id"] = _TASK_ID
    raw["noFlyZones"] = []
    task = _build_task(root, raw)
    task_document = task.model_dump(mode="json")
    task_document["verifier"]["workload"]["runtime"]["image"] = (
        "registry.invalid/logistics-verifier@sha256:" + "e" * 64
    )
    task = TaskSpec.model_validate(task_document)
    package = lower_logistics_task_package(raw)
    config_document = _native_config_document(package=package)
    config = NativeParcelBusinessConfig.model_validate(config_document)
    refs = _write_native_bundle(root, config.model_dump(mode="json"))
    env_base = _make_px4_gazebo_environment(
        root, _px4_config_document(("uav.alpha",))
    )
    native_provider = ProviderRef(
        provider_id=_NATIVE_PROVIDER_ID,
        adapter=NATIVE_PARCEL_ADAPTER,
        port=_NATIVE_PORT,
        workload=_fixture_runtime(_NATIVE_PROVIDER_ID).model_copy(
            update={"implementation": _native_implementation_identity()},
        ),
        config=SchemaBoundFile(
            file=FileRef(**refs["config"]),
            schema_file=FileRef(**refs["schema"]),
        ),
        protocol_schema=_placeholder_ref(
            "schemas/protocol/logistics-business.json"
        ),
        capabilities=tuple(sorted(CAPABILITIES)) + (NATIVE_PARCEL_CAPABILITY,),
        artifact_requirements=(_native_requirement(),),
    )
    environment = env_base.model_copy(
        update={
            "clock": env_base.clock.model_copy(update={"max_steps": _NATIVE_MAX_STEPS}),
            "providers": (
                *env_base.providers,
                native_provider,
            )
        }
    )
    agents = (_dispatcher_agent(_NATIVE_PRINCIPAL),)
    projection = ResolvedTaskScenarioProjection(
        logical_endpoint_ids=(),
        logical_capability_ids=(),
        observations=(),
    )
    scenario = compile_resolved_scenario(
        reader,
        world_ref,
        "launch.alpha",
        SEED,
        _capability_map(environment),
        {
            provider.provider_id: fixture_provider_registry().runtime_stage_for(
                provider.adapter
            )
            for provider in environment.providers
        },
        task,
        agents,
        projection,
    )
    bindings = validate_logistics_runtime_bindings(
        bindings_document=_bindings_document(
            aircraft=[
                {
                    "aircraft_id": _CARRIER_ID,
                    "fleet_entry_id": "fleet-alpha",
                    "visual_asset_id": "model:logistics-drone-v1",
                    "provider_id": "flight",
                    "vehicle_id": "uav.alpha",
                    "controlling_agent_id": _NATIVE_PRINCIPAL,
                },
            ],
            principals=[],
        ),
        reader=reader,
        environment=environment,
        agents=agents,
        package=package,
        scenario=scenario,
    )
    artifact_requirements = _derive_artifact_requirements(
        task, environment, agents
    )
    resolved_run = _build_resolved_run(
        task=task,
        environment=environment,
        agents=agents,
        scenario=scenario,
        artifact_requirements=artifact_requirements,
    )
    return _CohesiveFixture(
        reader=reader,
        task=task,
        environment=environment,
        agents=agents,
        scenario=scenario,
        package=package,
        bindings=bindings,
        business_config=config,
        artifact_requirements=artifact_requirements,
        resolved_run=resolved_run,
    )


def _native_stage_request(
    fixture: _CohesiveFixture,
    *,
    at: SimulationTime = AT,
    previous_scene_state=None,
    motion_scene_state: SceneState | None = None,
) -> BusinessEnvironmentStageRequest:
    """The deterministic motion-stage request at the closed tick (native id).

    ``at`` is the actual authoritative SimulationTime of the closed stage and
    replaces every timestamp this helper derives (the stage target, the motion
    StageBarrier and the assembled SceneState's at/sample clock); the default
    keeps the original tick-1 fixture behavior exactly.  The unchanged
    ``previous_scene_state`` keyword is the assembler's real chain predecessor,
    which the tick >= 2 SceneState rule requires (tick 1 keeps ``None``).
    """
    if motion_scene_state is None:
        static_entity = next(
            entity for entity in fixture.scenario.entities if entity.state == "static"
        )
        static_scenario = fixture.scenario.model_copy(
            update={"entities": (static_entity,)}
        )
        assembler = SceneStateAssembler(static_scenario)
        barrier_fields = {
            "schema_version": "aero-bench.stage-barrier/v1",
            "run_id": fixture.resolved_run.run_id,
            "scenario_digest": fixture.scenario.scenario_digest,
            "at": at,
            "stage": "motion",
            "input_scene_state_digest": None,
            "predecessor_barriers": (),
            "provider_ids": (),
            "receipts": (),
            "receipt_digests": (),
        }
        unsigned = StageBarrier.model_construct(**barrier_fields, barrier_digest="0" * 64)
        barrier = StageBarrier(
            **barrier_fields,
            barrier_digest=stage_barrier_digest_value(unsigned),
        )
        scene_state = assembler.assemble(
            run_id=fixture.resolved_run.run_id,
            at=at,
            barrier=barrier,
            contributions=(),
            previous_scene_state=previous_scene_state,
        )
    else:
        scene_state = motion_scene_state
        barrier = scene_state.stage_barrier
    return BusinessEnvironmentStageRequest(
        schema_version="aero-bench.provider-stage-request/v1",
        run_id=fixture.resolved_run.run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        provider_id=_NATIVE_PROVIDER_ID,
        target=at,
        stage="business_environment",
        scene_state=scene_state,
        scene_state_digest=scene_state.scene_state_digest,
        predecessor_barriers=(
            StageBarrierDigest(stage="motion", barrier_digest=barrier.barrier_digest),
        ),
    )


@asynccontextmanager
async def _native_running_stack(fixture: _CohesiveFixture, root: Path):
    """Real service + REAL native adapter client over a JSON-line loopback.

    Mirrors the shared ``_running_stack`` mechanics with the native workload
    identity: prepare/reset starts at tick 0; the caller submits each actual
    closed motion stage before ingesting its matching observation batch.
    """
    refs = _write_native_bundle(root, fixture.business_config.model_dump(mode="json"))
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    identity = _SERVICE.WorkloadIdentity(
        run_id=fixture.resolved_run.run_id,
        provider_id=_NATIVE_PROVIDER_ID,
        provider_port=_NATIVE_PORT,
        runtime_image=IMAGE,
        config_digest=refs["config"]["sha256"],
        artifact_requirement=_native_requirement().model_dump(mode="json"),
        seed=SEED,
        contract=_native_contract(refs, _NATIVE_PORT, fixture.scenario,
                                  run_id=fixture.resolved_run.run_id),
    )
    service = _SERVICE.LogisticsBusinessProviderService(
        bundle_root=root,
        artifact_root=root / "artifacts",
        rpc_port=_NATIVE_PORT,
        workload_identity=identity,
        session_token=SESSION_TOKEN,
    )
    server = JsonLineRpcServer(service.serve_rpc)
    handle = await server.start(host="127.0.0.1", port=0)
    client_port = handle.sockets[0].getsockname()[1]
    client = NativeParcelBusinessProvider(
        config=fixture.business_config,
        manifest=_native_manifest(refs["config"]["sha256"]),
        runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=client_port),
        run_id=fixture.resolved_run.run_id,
        session_token=SESSION_TOKEN,
        scenario=fixture.scenario,
    )
    try:
        await client.prepare()
        await client.reset(seed=SEED)
        yield service, client
    finally:
        if client._transport is not None:
            await client._transport.close()
        await server.graceful_close()
        await handle.wait_closed()


def _pickup_request(fixture: _CohesiveFixture, *, command_id: str,
                    principal: str = _NATIVE_PRINCIPAL,
                    at: SimulationTime = AT,
                    tool_id: str = PARCEL_PICKUP_TOOL) -> CommandRequest:
    """The exact parcel pickup command the state machine admits."""
    return CommandRequest(
        run_id=fixture.resolved_run.run_id,
        command_id=command_id,
        agent_id=principal,
        tool_id=tool_id,
        issued_at=at,
        arguments=(
            NamedValue(name="order_id", value=_ORDER_ID),
            NamedValue(name="parcel_id", value=_PARCEL_ID),
            NamedValue(name="actor_id", value=_CARRIER_ID),
        ),
    )


@pytest.fixture(scope="module")
def native_fixture(tmp_path_factory) -> _CohesiveFixture:
    return _native_cohesive_fixture(
        Path(tmp_path_factory.mktemp("native-parcel-wiring")),
    )


def test_native_stage_request_uses_the_actual_passed_simulation_time(
    native_fixture: _CohesiveFixture,
) -> None:
    """Pure helper validation: the stage request binds the exact passed time.

    Synthetic fixture-only check (no service, no RPC, no flight): the default
    call keeps the original tick-1 request byte-for-byte, and an explicit
    ``at`` re-derives every declared stage/barrier/SceneState clock at that
    actual SimulationTime through the same construction APIs.
    """
    fixture = native_fixture
    tick2 = SimulationTime(tick=2, sim_time_ns=2 * fixture.business_config
                           .native_parcel.step_ns)

    # The explicit tick-1 call must equal the original default call exactly.
    default_request = _native_stage_request(fixture)
    explicit_t1 = _native_stage_request(fixture, at=AT)
    assert explicit_t1.model_dump(mode="json") == default_request.model_dump(
        mode="json"
    )
    assert default_request.target == AT

    # An explicit tick-2 time re-derives every clock field at that time, with
    # the assembler's real tick-1 chain predecessor.
    tick1_scene = _native_stage_request(fixture).scene_state
    tick2_request = _native_stage_request(
        fixture, at=tick2, previous_scene_state=tick1_scene
    )
    dump = tick2_request.model_dump(mode="json")
    assert tick2_request.target == tick2
    assert dump["target"] == {"tick": 2, "sim_time_ns": tick2.sim_time_ns}
    assert tick2_request.scene_state.at == tick2
    assert dump["scene_state"]["at"] == {
        "tick": 2,
        "sim_time_ns": tick2.sim_time_ns,
    }
    assert tick2_request.scene_state.stage_barrier.at == tick2
    assert dump["scene_state"]["stage_barrier"]["at"] == {
        "tick": 2,
        "sim_time_ns": tick2.sim_time_ns,
    }
    # Every static SceneState sample is stamped at the actual passed time and
    # re-digested over that content.
    for sample in tick2_request.scene_state.samples:
        assert sample.at == tick2
        assert sample.model_dump(mode="json")["at"] == {
            "tick": 2,
            "sim_time_ns": tick2.sim_time_ns,
        }
    assert tick2_request.scene_state_digest == (
        tick2_request.scene_state.scene_state_digest
    )
    assert tick2_request.predecessor_barriers[0].barrier_digest == (
        tick2_request.scene_state.stage_barrier.barrier_digest
    )
    # The tick-2 scene state binds the exact tick-1 chain predecessor.
    assert tick2_request.scene_state.previous_scene_state_digest == (
        tick1_scene.scene_state_digest
    )


def test_native_parcel_authority_lifecycle_projection_retry_and_foreign_principal(
    native_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """Seven real loopback ticks, followed by transport reconnection at tick 8."""
    fixture = native_fixture

    async def run() -> None:
        async with _native_running_stack(fixture, tmp_path) as (service, client):
            parcel = service._native_parcel
            assert parcel is not None
            assert parcel.config.contract.identities.parcel_entity_id == _PARCEL_ID
            assert parcel.run_id == fixture.resolved_run.run_id
            assert fixture.resolved_run.environment.clock.step_ns == 1_000_000_000
            assert fixture.resolved_run.environment.clock.max_steps == _NATIVE_MAX_STEPS
            previous_scene = None
            early_pickup = None
            early_dropoff = None
            expected_states = (
                "awaiting_pickup", "awaiting_pickup", "loaded", "in_transit",
                "in_transit", "in_transit", "delivered",
            )

            async def close_tick(tick: int):
                nonlocal previous_scene
                at = SimulationTime(tick=tick, sim_time_ns=tick * 1_000_000_000)
                scene, batch = _native_observation_batch(
                    fixture, at=at, previous_scene_state=previous_scene,
                    rest_facility_id=(
                        _PICKUP_FACILITY if tick <= 4 else _DROPOFF_FACILITY
                    ),
                    airborne=tick == 4,
                )
                if previous_scene is not None:
                    assert scene.previous_scene_state_digest == previous_scene.scene_state_digest
                request = _native_stage_request(
                    fixture, at=at, motion_scene_state=scene,
                )
                result = await client.step_stage(request)
                assert result.step_receipt.reached == at
                ingest = await client.ingest_observations(batch)
                assert ingest.replayed is False
                stored = parcel.batch
                assert stored.model_dump(mode="json") == batch.model_dump(mode="json")
                assert {o.facility_id for o in stored.observations} == {
                    _PICKUP_FACILITY, _DROPOFF_FACILITY,
                }
                assert len(stored.observations) == 2
                left, right = stored.observations
                assert left.sample == right.sample
                assert left.source_state_sample_digest == right.source_state_sample_digest
                assert left.source_event_id == right.source_event_id
                assert all(o.at == at for o in stored.observations)
                snapshot = parcel.machine.snapshot()
                assert snapshot.last_observation_at == at
                assert dict(snapshot.facility_frontiers) == {
                    _PICKUP_FACILITY: at, _DROPOFF_FACILITY: at,
                }
                assert len(parcel.stage_consumer.snapshot().consumed_batch_keys) == tick
                previous_scene = request.scene_state
                return at, batch, request

            for tick in range(1, 8):
                at, batch, stage = await close_tick(tick)
                if tick == 1:
                    before = parcel.machine.snapshot()
                    consumed = parcel.stage_consumer.snapshot()
                    assert len(before.pickup_window_samples) == 1
                    replay = await client.ingest_observations(batch)
                    assert replay.replayed is True
                    assert parcel.machine.snapshot() == before
                    assert parcel.stage_consumer.snapshot() == consumed
                    with pytest.raises(Exception, match="monoton"):
                        await client.step_stage(stage)
                    assert parcel.machine.snapshot() == before
                    early_pickup = _pickup_request(
                        fixture, command_id="cmd.native.pickup.early", at=at,
                    )
                    early_pickup_result = await client.handle_command(early_pickup)
                    assert early_pickup_result.receipts[-1].phase == "failed"
                    assert parcel.actions[-1]["outcome"]["status"] == "unconfirmed"
                    assert parcel.machine.parcel_state.transfers == ()
                elif tick == 2:
                    window = parcel.machine.snapshot().pickup_window_samples
                    assert tuple(o.at.tick for o in window) == (1, 2)
                    assert parcel.machine.dwell_admission("pickup").confirmed is False
                elif tick == 3:
                    dwell = parcel.machine.dwell_admission("pickup")
                    assert dwell.confirmed is True
                    assert dwell.dwell_seconds == 2.0
                    before = parcel.machine.snapshot()
                    assert await client.handle_command(early_pickup) == early_pickup_result
                    assert parcel.machine.snapshot() == before
                    assert len(parcel.actions) == 1
                    foreign = _pickup_request(
                        fixture, command_id="cmd.native.pickup.foreign", at=at,
                        principal=_FOREIGN_PRINCIPAL,
                    )
                    with pytest.raises(Exception, match="principal"):
                        await client.handle_command(foreign)
                    assert parcel.machine.snapshot() == before
                    pickup = _pickup_request(
                        fixture, command_id="cmd.native.pickup.3", at=at,
                    )
                    pickup_result = await client.handle_command(pickup)
                    assert pickup_result.receipts[-1].phase == "completed"
                    assert parcel.actions[-1]["outcome"]["status"] == "admitted"
                    assert len(parcel.machine.parcel_state.transfers) == 1
                    before = parcel.machine.snapshot()
                    assert await client.handle_command(pickup) == pickup_result
                    assert parcel.machine.snapshot() == before
                    altered = pickup.model_copy(update={"tool_id": PARCEL_DROPOFF_TOOL})
                    with pytest.raises(Exception, match="reused with different request content"):
                        await client.handle_command(altered)
                    assert parcel.machine.snapshot() == before
                    assert len(parcel.actions) == 2
                elif tick == 4:
                    assert parcel.machine.parcel_state.state == "in_transit"
                    assert len(parcel.machine.parcel_state.transfers) == 1
                    assert batch.observations[0].sample.in_air is True
                elif tick == 5:
                    early_dropoff = _pickup_request(
                        fixture, command_id="cmd.native.dropoff.early", at=at,
                        tool_id=PARCEL_DROPOFF_TOOL,
                    )
                    early_dropoff_result = await client.handle_command(early_dropoff)
                    assert early_dropoff_result.receipts[-1].phase == "failed"
                    assert parcel.actions[-1]["outcome"]["status"] == "unconfirmed"
                    assert len(parcel.machine.parcel_state.transfers) == 1
                elif tick == 6:
                    window = parcel.machine.snapshot().dropoff_window_samples
                    assert tuple(o.at.tick for o in window) == (5, 6)
                    assert parcel.machine.dwell_admission("dropoff").confirmed is False
                elif tick == 7:
                    dwell = parcel.machine.dwell_admission("dropoff")
                    assert dwell.confirmed is True
                    assert dwell.dwell_seconds == 2.0
                    before = parcel.machine.snapshot()
                    assert await client.handle_command(early_dropoff) == early_dropoff_result
                    assert parcel.machine.snapshot() == before
                    dropoff = _pickup_request(
                        fixture, command_id="cmd.native.dropoff.7", at=at,
                        tool_id=PARCEL_DROPOFF_TOOL,
                    )
                    dropoff_result = await client.handle_command(dropoff)
                    assert dropoff_result.receipts[-1].phase == "completed"
                    assert parcel.actions[-1]["outcome"]["status"] == "admitted"
                    assert len(parcel.machine.parcel_state.transfers) == 2
                    before = parcel.machine.snapshot()
                    assert await client.handle_command(dropoff) == dropoff_result
                    assert await client.handle_command(pickup) == pickup_result
                    assert await client.handle_command(early_pickup) == early_pickup_result
                    assert parcel.machine.snapshot() == before
                    assert len(parcel.actions) == 4

                frame = await client.parcel_scene_frame(at=at)
                assert frame.scene_state == stage.scene_state
                assert frame.parcel.state == expected_states[tick - 1]
                assert frame.parcel.source_scene_state_digest == stage.scene_state_digest
                assert frame.parcel.source_stage_barrier_digest == batch.source_stage_barrier_digest
                if tick in (3, 4, 5, 6):
                    assert frame.parcel.custody_holder_id == _CARRIER_ID
                    assert frame.parcel.carrier_entity_id == "uav.alpha"
                    assert frame.parcel.pose.y_m == pytest.approx(
                        frame.scene_state.samples[0].pose.position.enu.up_m - 0.2,
                    )
                elif tick == 7:
                    assert frame.parcel.custody_holder_id == _DROPOFF_FACILITY
                    assert frame.parcel.custody_holder_kind == "dropoff_facility"
                    assert frame.parcel.carrier_entity_id is None
                    assert frame.parcel.pose == fixture.business_config.native_parcel.final_parcel_pose
                print(f"tick={tick} state={frame.parcel.state} "
                      f"transfers={len(parcel.machine.parcel_state.transfers)}")

            # Reconnect the real transport without resetting the server or client journals.
            before = parcel.machine.snapshot()
            digest = await client.snapshot_digest()
            await client._transport.close()
            client._transport = None
            assert await client.snapshot_digest() == digest
            assert await client.parcel_scene_frame(at=at) == frame
            assert parcel.machine.snapshot() == before
            at, _, _ = await close_tick(8)
            before = parcel.machine.snapshot()
            assert await client.handle_command(dropoff) == dropoff_result
            assert await client.handle_command(pickup) == pickup_result
            assert await client.handle_command(early_pickup) == early_pickup_result
            assert await client.handle_command(early_dropoff) == early_dropoff_result
            assert parcel.machine.snapshot() == before
            assert len(parcel.actions) == 4
            assert len(parcel.machine.parcel_state.transfers) == 2
            assert (await client.parcel_scene_frame(at=at)).parcel.state == "delivered"
            print("tick=8 reconnected=True state=delivered transfers=2 actions=4")

    asyncio.run(run())
