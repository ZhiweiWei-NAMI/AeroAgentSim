"""Module tests for the explicit native-parcel task resolver / factory entry.

Synthetic CPU-only module evidence: NO PX4/Gazebo, NO executor, NO native run
launch.  The tests prove the real selection semantics over the real compiled
bundle fixtures (digest-pinned files, real BundleReader, real lowerer, real
strict native config model):

- the native package id is explicit and distinct from the base logistics id,
  so registry registration composes with the pending base entries instead of
  overwriting them;
- scenario projection proves the explicit adapter, the authority capability,
  the digest-pinned native config, the exact package binding and the clock run
  bound through the real BundleReader contract;
- the registered resolver recognizes the native provider capabilities and
  never claims the base logistics package id;
- foreign environments (wrong adapter, missing authority capability, missing
  provider, foreign clock, foreign scene) are exact errors;
- the factory entry is zero-arg constructible, ignores foreign runs by package
  id like every registry-selected factory, and constructs the real
  NativeParcelRuntimeHook over a real client for the native run.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aero_bench.config.models import (
    ProviderRef,
    SchemaBoundFile,
    TaskSpec,
)
from aero_bench.config.resolver import ResolvedRunSpec, _derive_artifact_requirements
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.logistics_business.native_parcel import (
    NATIVE_PARCEL_ADAPTER,
    NATIVE_PARCEL_CAPABILITY,
    NativeParcelBusinessConfig,
    NativeParcelBusinessProvider,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.runtime.hooks import resolve_runtime_hook
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.native_parcel_contract import (
    ATTACHMENT_FRAME_NOTE,
    NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
    SEALED_VERIFIER_REPLAY_REQUIREMENT,
)
from aero_bench.tasks.logistics.native_parcel_hook import NativeParcelRuntimeHook
from aero_bench.tasks.logistics.native_parcel_integration import (
    NATIVE_BUSINESS_PROVIDER_ID,
    NATIVE_PARCEL_PACKAGE_ID,
    NativeParcelPackageResolutionError,
    NativeParcelTaskPackageResolver,
    NativeParcelTaskRuntimeHookFactory,
)
from aero_bench.tasks.logistics.runtime_bindings import LOGISTICS_FLIGHT_CONTROL_TOOLS
from aero_bench.tasks.registry import (
    builtin_runtime_hook_factories,
    builtin_task_package_resolvers,
)
from aero_bench.world.contracts import ProviderRequirement
from aero_bench.world.resolved import (
    ResolvedTaskScenarioProjection,
    compile_resolved_scenario,
)

from tests.providers.test_logistics_business_service import IMAGE, SEED
from tests.support import fixture_provider_registry
from tests.tasks.test_logistics_package import _package_document
from tests.tasks.test_logistics_runtime_bindings import (
    _bindings_document,
    _build_task,
    _capability_map,
    _fixture_runtime,
    _flight_agent,
    _make_px4_gazebo_environment,
    _px4_config_document,
    _valid_bindings,
    _vertiport,
    _write_fixture_file,
    _world_with_uav_ids,
)
from tests.tasks.test_logistics_runtime_hook import (
    AT,
    _business_config_document,
    _facilities,
    build_fixture,
)
from tests.world.support import materialize_world_package


# ------------------------------------------------------------ synthetic slice


_TASK_ID = "logistics.native-parcel.entry.v1"
_ORDER_ID = "order.native.entry"
_PARCEL_ID = "parcel.native.entry"
_CARRIER_ID = "fleet-alpha:1"
_PICKUP_FACILITY = "facility-1"
_DROPOFF_FACILITY = "facility-2"
_CARRIER_PRINCIPAL = "uav.native.entry.carrier"
_NATIVE_PORT = 18443


def _native_package_document() -> dict[str, object]:
    """The fixture package re-identified for the explicit native slice.

    Both pad-bearing facilities are widened to ``widthM=15`` so the declared
    three-slot landing rows are derivable by the current facility geometry
    (three pads at 5.4 m pitch need at least 15 m of footprint width).  The
    package keeps one canonical order, one aircraft actor and no fly zones.
    """
    document = dict(_package_document())
    document["task_id"] = _TASK_ID
    orders = [dict(document["orders"][0])]
    orders[0]["id"] = _ORDER_ID
    orders[0]["sourceFacilityId"] = _PICKUP_FACILITY
    orders[0]["destinationFacilityId"] = _DROPOFF_FACILITY
    document["orders"] = orders
    document["facilities"] = [
        _vertiport(id="facility-1", widthM=15),
        _vertiport(id="facility-2", widthM=15),
        *_facilities()[2:],
    ]
    document["actors"] = [{"actor_id": _CARRIER_ID, "role": "aircraft_agent"}]
    document["noFlyZones"] = []
    return document


def _native_contract_document() -> dict[str, object]:
    return {
        "schema_version": NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
        "contract_id": "native.parcel.entry",
        "identities": {
            "schema_version": NATIVE_PARCEL_CONTRACT_SCHEMA_VERSION,
            "task_id": _TASK_ID,
            "order_id": _ORDER_ID,
            "parcel_entity_id": _PARCEL_ID,
            "carrier_entity_id": _CARRIER_ID,
            "authorized_principal_id": _CARRIER_PRINCIPAL,
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


def _native_config_document(
    package_document: dict[str, object] | None = None,
) -> dict[str, object]:
    """The strict native business config bound to the synthetic package."""
    package = lower_logistics_task_package(
        _native_package_document()
        if package_document is None
        else package_document
    )
    package_dump = package.model_dump(mode="json")
    pickup_pad = facility_landing_pads(package.facilities.require(_PICKUP_FACILITY))[0]
    dropoff_pad = facility_landing_pads(
        package.facilities.require(_DROPOFF_FACILITY)
    )[0]
    quad = {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0}
    return {
        "schema_version": "aero-bench.logistics-native-business/v1",
        "scheduled_orders": [],
        "provider_id": NATIVE_BUSINESS_PROVIDER_ID,
        "task_package": package_dump,
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
                        "controlling_agent_id": _CARRIER_PRINCIPAL,
                    },
                ],
                principals=[],
            ),
            "pose_references": [
                {
                    "aircraft_id": _CARRIER_ID,
                    "pose_reference_above_contact_m": 0.1,
                },
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
                "principal_id": _CARRIER_PRINCIPAL,
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
            "max_steps": 1000,
            # Authored declaration: the current codebase has NO calibration
            # digest producer; the strict model only proves the digest shape.
            "calibration_digest": "ab" * 32,
        },
    }


def _native_provider(
    root: Path,
    config_document: dict[str, object],
    *,
    port: int = _NATIVE_PORT,
    name: str = "native.entry",
    capabilities: tuple[str, ...] = (NATIVE_PARCEL_CAPABILITY, "logistics.facilities.state", "logistics.orders.authority"),
    adapter: str = NATIVE_PARCEL_ADAPTER,
    provider_id: str = NATIVE_BUSINESS_PROVIDER_ID,
) -> ProviderRef:
    """A digest-pinned native provider ref over real bundle bytes."""
    config_ref = _write_fixture_file(
        root,
        f"configs/{name}.config.json",
        canonical_json_bytes(config_document) + b"\n",
    )
    schema_ref = _write_fixture_file(
        root,
        f"configs/{name}.schema.json",
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n',
    )
    return ProviderRef(
        provider_id=provider_id,
        adapter=adapter,
        port=port,
        workload=_fixture_runtime(adapter),
        config=SchemaBoundFile(file=config_ref, schema_file=schema_ref),
        protocol_schema=_write_fixture_file(
            root,
            f"configs/{name}.protocol.json",
            b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n',
        ),
        capabilities=capabilities,
        artifact_requirements=(),
    )


def _native_world_mutate(extra_provider_req):
    def mutate(kwargs, root):
        _world_with_uav_ids(("uav.alpha",))(kwargs, root)
        reqs = list(kwargs["provider_requirements"])
        reqs.append(extra_provider_req)
        kwargs["provider_requirements"] = tuple(
            sorted(reqs, key=lambda r: r.provider_id)
        )

    return mutate


def _native_agents() -> tuple:
    """The one declared controlling agent of the native carrier binding."""
    return (_flight_agent(_CARRIER_PRINCIPAL, LOGISTICS_FLIGHT_CONTROL_TOOLS),)


def _native_fixture(root: Path, *, config_document=None) -> dict[str, object]:
    """Compile the honest native-slice fixture through the real compilers."""
    root.mkdir(parents=True, exist_ok=True)
    business_req = ProviderRequirement(
        provider_id=NATIVE_BUSINESS_PROVIDER_ID,
        roles=("mission",),
        required_capability_ids=(NATIVE_PARCEL_CAPABILITY,),
    )
    reader, world_ref, world = materialize_world_package(
        root,
        mutate_kwargs=_native_world_mutate(business_req),
    )
    raw = _native_package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    if config_document is None:
        config_document = _native_config_document(raw)
    task = _build_task(root, raw)
    task_document = task.model_dump(mode="json")
    task_document["package"]["package_id"] = NATIVE_PARCEL_PACKAGE_ID
    task_document["verifier"]["workload"]["runtime"]["image"] = (
        "registry.invalid/logistics-verifier@sha256:" + "e" * 64
    )
    task = TaskSpec.model_validate(task_document)
    provider = _native_provider(root, config_document)
    env_base = _make_px4_gazebo_environment(
        root, _px4_config_document(("uav.alpha",))
    )
    environment = env_base.model_copy(
        update={"providers": (*env_base.providers, provider)}
    )
    agents = _native_agents()
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
            p.provider_id: fixture_provider_registry().runtime_stage_for(p.adapter)
            for p in environment.providers
        },
        task,
        agents,
        projection,
    )
    package = lower_logistics_task_package(raw)
    artifact_requirements = _derive_artifact_requirements(task, environment, agents)
    run_payload = {
        "schema_version": "aero-bench.resolved-run/v5",
        "executor_kind": "docker_reference",
        "execution_scope": "executor_validation",
        "suite": _write_fixture_file(
            root, "suite.yaml", b"native-entry-suite\n"
        ).model_dump(mode="json"),
        "suite_id": "native.parcel.suite",
        "case_id": "native.parcel.case",
        "launch_site_id": scenario.selected_launch_site_id,
        "seed": SEED,
        "scenario": scenario.model_dump(mode="json"),
        "task": task.model_dump(mode="json"),
        "environment": environment.model_dump(mode="json"),
        "agents": [agent.model_dump(mode="json") for agent in agents],
        "feasibility": {
            "package_id": NATIVE_PARCEL_PACKAGE_ID,
            "feasible": True,
            "success_upper_bound": 1.0,
            "failed_conditions": [],
            "bounds": [],
        },
        "artifact_requirements": [
            requirement.model_dump(mode="json")
            for requirement in artifact_requirements
        ],
        "verification_outputs": [
            requirement.model_dump(mode="json")
            for requirement in task.verifier.output_artifacts
        ],
        "overrides": (),
    }
    run_id = hashlib.sha256(canonical_json_bytes(run_payload)).hexdigest()
    resolved_run = ResolvedRunSpec(run_id=run_id, **run_payload)
    return {
        "reader": reader,
        "task": task,
        "environment": environment,
        "agents": agents,
        "scenario": scenario,
        "package": package,
        "resolved_run": resolved_run,
        "config_document": config_document,
    }


# ------------------------------------------------------------------- tests


def test_native_package_id_is_explicit_and_distinct() -> None:
    resolver = NativeParcelTaskPackageResolver()
    factory = NativeParcelTaskRuntimeHookFactory()
    assert resolver.package_id == NATIVE_PARCEL_PACKAGE_ID
    assert factory.package_id == NATIVE_PARCEL_PACKAGE_ID
    assert NATIVE_PARCEL_PACKAGE_ID != LOGISTICS_PACKAGE_ID
    # Composability: the explicit native id does not overwrite any pending
    # base registry entry and does not collide with the runtime hook resolver.
    builtin = {entry.package_id for entry in builtin_task_package_resolvers()}
    builtin_factories = {
        entry.package_id for entry in builtin_runtime_hook_factories()
    }
    assert NATIVE_PARCEL_PACKAGE_ID in builtin
    assert NATIVE_PARCEL_PACKAGE_ID in builtin_factories
    assert resolver.runtime_available is True


def test_scenario_projection_proves_native_declarations(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    resolver = NativeParcelTaskPackageResolver()
    projection = resolver.scenario_projection(
        reader=fixture["reader"],
        task=fixture["task"],
        environment=fixture["environment"],
        agents=fixture["agents"],
    )
    # Provider-safe empty projection; every native declaration was proven.
    assert projection.logical_endpoint_ids == ()
    assert projection.logical_capability_ids == ()
    assert projection.observations == ()


def test_projection_requires_explicit_native_adapter(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    foreign = _native_provider(
        tmp_path,
        fixture["config_document"],
        port=18444,
        name="foreign.adapter",
        adapter="logistics.business",
    )
    foreign_environment = fixture["environment"].model_copy(
        update={
            "providers": tuple(
                foreign
                if item.provider_id == NATIVE_BUSINESS_PROVIDER_ID
                else item
                for item in fixture["environment"].providers
            )
        }
    )
    resolver = NativeParcelTaskPackageResolver()
    with pytest.raises(
        NativeParcelPackageResolutionError, match="not the native parcel adapter"
    ):
        resolver.scenario_projection(
            reader=fixture["reader"],
            task=fixture["task"],
            environment=foreign_environment,
            agents=fixture["agents"],
        )


def test_projection_requires_declared_parcel_authority(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    capability_less = _native_provider(
        tmp_path,
        fixture["config_document"],
        port=18445,
        name="foreign.capability",
        capabilities=("logistics.facilities.state",),
    )
    environment = fixture["environment"].model_copy(
        update={
            "providers": tuple(
                capability_less
                if item.provider_id == NATIVE_BUSINESS_PROVIDER_ID
                else item
                for item in fixture["environment"].providers
            )
        }
    )
    resolver = NativeParcelTaskPackageResolver()
    with pytest.raises(
        NativeParcelPackageResolutionError, match="parcel authority"
    ):
        resolver.scenario_projection(
            reader=fixture["reader"],
            task=fixture["task"],
            environment=environment,
            agents=fixture["agents"],
        )


def test_projection_requires_the_declared_provider(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    environment = fixture["environment"].model_copy(
        update={
            "providers": tuple(
                item
                for item in fixture["environment"].providers
                if item.provider_id != NATIVE_BUSINESS_PROVIDER_ID
            )
        }
    )
    resolver = NativeParcelTaskPackageResolver()
    with pytest.raises(
        NativeParcelPackageResolutionError, match="native parcel business provider"
    ):
        resolver.scenario_projection(
            reader=fixture["reader"],
            task=fixture["task"],
            environment=environment,
            agents=fixture["agents"],
        )


def test_projection_rejects_clock_mismatch(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    divergent = dict(fixture["config_document"])
    session = dict(divergent["native_parcel"])
    session["max_steps"] = 7
    divergent["native_parcel"] = session
    provider = _native_provider(tmp_path, divergent, port=18446, name="foreign.clock")
    environment = fixture["environment"].model_copy(
        update={
            "providers": tuple(
                provider
                if item.provider_id == NATIVE_BUSINESS_PROVIDER_ID
                else item
                for item in fixture["environment"].providers
            )
        }
    )
    resolver = NativeParcelTaskPackageResolver()
    with pytest.raises(NativeParcelPackageResolutionError, match="run bound"):
        resolver.scenario_projection(
            reader=fixture["reader"],
            task=fixture["task"],
            environment=environment,
            agents=fixture["agents"],
        )


def test_resolve_recognizes_native_runtime(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    resolver = NativeParcelTaskPackageResolver()
    assessment = resolver.resolve(
        reader=fixture["reader"],
        task=fixture["task"],
        environment=fixture["environment"],
        agents=fixture["agents"],
        scenario=fixture["scenario"],
    )
    assert assessment.package_id == NATIVE_PARCEL_PACKAGE_ID
    assert assessment.feasible is True
    assert assessment.success_upper_bound == 1.0
    assert assessment.failed_conditions == ()
    names = [bound.name for bound in assessment.bounds]
    assert "logistics.package.digest" in names
    assert "logistics.orders.count" in names
    orders_bound = next(
        bound for bound in assessment.bounds if bound.name == "logistics.orders.count"
    )
    assert orders_bound.value == 1


def test_resolve_exposes_unsupported_declared_requirement(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    task = fixture["task"].model_copy(update={"required_capabilities": ("logistics.airspace.events",)})
    assessment = NativeParcelTaskPackageResolver().resolve(
        reader=fixture["reader"], task=task, environment=fixture["environment"],
        agents=fixture["agents"], scenario=fixture["scenario"],
    )
    assert assessment.feasible is False
    assert assessment.failed_conditions == ("logistics.airspace.events",)


def test_config_binding_mismatch_names_exact_fields(tmp_path: Path) -> None:
    """Reproduce the coordinator's reported failing case, then close it.

    Part 1 runs the reported failing case unchanged on the exact saved
    fixture (expected: green -- the saved fixture is internally consistent;
    the earlier observed failure is a real config/package disagreement, not
    this fixture).  Part 2 proves the strict binding still fires on a real
    content mismatch and that the error now NAMES the differing field
    instead of failing as an opaque inequality.
    """
    import copy

    fixture = _native_fixture(tmp_path)
    resolver = NativeParcelTaskPackageResolver()
    # Part 1: the exact reported case, unchanged -- full binding holds.
    projection = resolver.scenario_projection(
        reader=fixture["reader"],
        task=fixture["task"],
        environment=fixture["environment"],
        agents=fixture["agents"],
    )
    assert projection.logical_endpoint_ids == ()
    assert projection.logical_capability_ids == ()
    assert projection.observations == ()

    # Part 2: one real canonical field of the declared config's nested
    # package is tampered (order cargo mass).  The lowered package is
    # content-keyed (no package_id field), so this is a genuine content
    # disagreement, and the binding proof must reject it explicitly.
    tampered_document = copy.deepcopy(fixture["config_document"])
    order = tampered_document["task_package"]["orders"][0]
    assert order["order_id"] == _ORDER_ID
    order["cargo_mass_kg"] = order["cargo_mass_kg"] + 0.5
    mismatched = _native_fixture(
        tmp_path / "mismatch", config_document=tampered_document
    )
    with pytest.raises(
        NativeParcelPackageResolutionError,
        match=r"orders \(\[0\]\.cargo_mass_kg\)",
    ) as exc_info:
        resolver.scenario_projection(
            reader=mismatched["reader"],
            task=mismatched["task"],
            environment=mismatched["environment"],
            agents=mismatched["agents"],
        )
    message = str(exc_info.value)
    assert "orders" in message and "cargo_mass_kg" in message
    assert "identity-only agreement is not enough" in message


def test_resolve_requires_scene_binding(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    resolver = NativeParcelTaskPackageResolver()
    # A scenario from another world cannot silently satisfy the native slice:
    # the guard under test is the exact world_id equality.
    foreign_scenario = fixture["scenario"].model_copy(
        update={"world_id": "other.world"}
    )
    with pytest.raises(ValueError, match="world_id"):
        resolver.resolve(
            reader=fixture["reader"],
            task=fixture["task"],
            environment=fixture["environment"],
            agents=fixture["agents"],
            scenario=foreign_scenario,
        )


def test_factory_entry_selects_only_native_runs(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    assert fixture["resolved_run"].task.package.package_id == (
        NATIVE_PARCEL_PACKAGE_ID
    )
    factory = NativeParcelTaskRuntimeHookFactory()
    # Registry selection semantics: a foreign run is not this factory's run.
    foreign = build_fixture(tmp_path / "foreign")
    assert foreign.resolved_run.task.package.package_id == LOGISTICS_PACKAGE_ID
    hook = resolve_runtime_hook(
        run=foreign.resolved_run,
        reader=foreign.reader,
        providers={},
        ledger=EventLedger(run_id=foreign.resolved_run.run_id),
        runtime_hook_time=lambda: AT,
        factories=(factory,),
    )
    assert hook is None


def test_factory_entry_constructs_native_hook(tmp_path: Path) -> None:
    fixture = _native_fixture(tmp_path)
    factory = NativeParcelTaskRuntimeHookFactory()
    run = fixture["resolved_run"]
    config = NativeParcelBusinessConfig.model_validate(fixture["config_document"])
    manifest = ProviderManifest(
        provider_id=NATIVE_BUSINESS_PROVIDER_ID,
        adapter=NATIVE_PARCEL_ADAPTER,
        implementation=_fixture_runtime(NATIVE_PARCEL_ADAPTER).implementation,
        runtime_image=IMAGE,
        config_digest=hashlib.sha256(
            canonical_json_bytes(fixture["config_document"]) + b"\n"
        ).hexdigest(),
        capabilities=(NATIVE_PARCEL_CAPABILITY,),
        protocol_schema=_write_fixture_file(
            tmp_path,
            "native.entry.manifest.protocol.json",
            b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n',
        ),
        artifact_requirements=(),
    )
    client = NativeParcelBusinessProvider(
        config=config,
        manifest=manifest,
        runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=_NATIVE_PORT),
        run_id=run.run_id,
        session_token=hashlib.sha256(b"native-entry-token").hexdigest(),
        scenario=fixture["scenario"],
    )
    hook = factory.create(
        run=run,
        reader=fixture["reader"],
        providers={NATIVE_BUSINESS_PROVIDER_ID: client, "flight": None},
        ledger=EventLedger(run_id=run.run_id),
        runtime_hook_time=lambda: AT,
    )
    assert isinstance(hook, NativeParcelRuntimeHook)
    assert hook.observation_hook is not None


def test_factory_entry_refuses_non_native_declared_config(
    tmp_path: Path,
) -> None:
    """A non-native declared config must not materialize a native hook."""
    from tests.tasks.test_logistics_package import _package_document as _base_doc

    base_raw = _base_doc()
    base_facilities = _facilities()
    base_facilities[0] = _vertiport(id="facility-1", widthM=18)
    base_raw["facilities"] = base_facilities
    base_document = _business_config_document(
        lower_logistics_task_package(base_raw).model_dump(mode="json"),
        bindings_document=_valid_bindings(aircraft_count=1),
    )
    fixture = _native_fixture(tmp_path, config_document=base_document)
    factory = NativeParcelTaskRuntimeHookFactory()
    with pytest.raises(
        NativeParcelPackageResolutionError,
        match="not a materializable native declaration",
    ):
        factory.create(
            run=fixture["resolved_run"],
            reader=fixture["reader"],
            providers={},
            ledger=EventLedger(run_id=fixture["resolved_run"].run_id),
            runtime_hook_time=lambda: AT,
        )
