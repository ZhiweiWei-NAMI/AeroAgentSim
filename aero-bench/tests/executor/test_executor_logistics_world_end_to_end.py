"""End-to-end logistics facility-world binding (strict ResolvedRun -> Gazebo).

One strict logistics ``ResolvedRunSpec`` (reproduced exactly by its pinned
persistent suite) flows through the real executor materialization seam and the
real px4.gazebo provider service:

  build_execution_plan
    -> ExecutionPlan.logistics_world + px4.gazebo derived InlineInputPlan
    -> DockerExecutor._populate_workload_inputs (non-host input volume)
    -> provider _config_payload (digest-pinned world_input)
    -> Px4GazeboService._parse_prepare (binds declaration)
    -> _bundle_world_sdf (input-volume SHA-256 pin)
    -> RealPx4Stack._start_gazebo -> ensure_paused_world_sdf -> paused copy
       passed to `gz sim --seed N -s <copy>`

The mandatory cross-module acceptance is asserted at every layer as one
SHA-256 identity: the facility-spliced world digest in
``ExecutionPlan.logistics_world`` and the derived input MUST equal
``Px4GazeboConfig.world_input``, the provider prepare payload, and the exact
bytes the stack hands to ``ensure_paused_world_sdf`` / ``gz sim``.  The paused
copy is verified with the actual pinned parser (ElementTree) and must contain
the spliced launch-pad/facility models.  Fail-closed negative bindings
(unset/mismatched world_input) are asserted against the same seam.
"""

from __future__ import annotations

from tests.executor.logistics_package_fixture import logistics_fixture_provider_registry

import hashlib
import importlib.util
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.executor import DockerExecutor
from aero_bench.executor.facility_world import (
    DEFAULT_LOGISTICS_WORLD_DESTINATION,
    LogisticsWorldMaterializationError,
    apply_logistics_world_materialization,
)
from aero_bench.executor.planning import build_execution_plan
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.world.scene_compiler import SceneOrigin
from tests.executor.logistics_package_fixture import (
    PX4_PROVIDER_ID,
    base_world_sdf_bytes,
    materialize_logistics_suite,
)

from aero_bench.world import frame_math
from aero_bench.world import workload_scenario

import aero_bench.providers.rpc as _RPC

# The standalone service image installs pure modules under the short names the
# Dockerfile copies in (``aero_frame_math``, ``workload_scenario``, ``rpc``).
# This worktree deliberately has no tests/conftest.py, so the service module
# loads here with the same module aliases the image runtime provides.
sys.modules["aero_frame_math"] = frame_math
sys.modules["workload_scenario"] = workload_scenario

_SERVICE_PATH = (
    Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "px4_gazebo_logistics_world_tests_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules["rpc"] = _RPC
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)

SESSION_TOKEN = "9" * 64


def _origin() -> SceneOrigin:
    return SceneOrigin(
        latitude_deg=39.916,
        longitude_deg=116.397,
        amsl_m=43.0,
        ellipsoid_height_m=43.0,
        geoid_undulation_m=0.0,
    )


def _docker_executor() -> DockerExecutor:
    return DockerExecutor(
        docker_binary="docker-not-installed",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        volume_keeper_mount_path="/run/aero-held",
        input_volume_size_bytes=33_554_432,
        artifact_volume_size_bytes=4_194_304,
        scratch_size_bytes=1_048_576,
        pids_limit=64,
        workload_uid=65532,
        workload_gid=65532,
        provider_bind_host="0.0.0.0",
        gateway_bind_host="0.0.0.0",
        readiness_timeout_seconds=30,
        volume_keeper_image="registry.invalid/keeper@sha256:" + "6" * 64,
        volume_keeper_command=("/bin/sleep", "infinity"),
        volume_keeper_cpu_millicores=50,
        volume_keeper_memory_mib=64,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=logistics_fixture_provider_registry(),
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _staged_world_bytes(root: Path, world_input: dict[str, str]) -> bytes:
    staged = root / world_input["path"].removeprefix("bundle/")
    staged.parent.mkdir(parents=True, exist_ok=True)
    payload = staged.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == world_input["sha256"]
    return payload


def _flight_config(root: Path) -> dict[str, object]:
    path = root / "configs/providers" / f"{PX4_PROVIDER_ID}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_and_payload(
    run, bundle_root: Path, flight_provider, config_doc: dict[str, object]
):
    from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
    from aero_bench.providers.px4_gazebo.provider import Px4GazeboProvider

    config_file = bundle_root / flight_provider.config.file.path
    config = Px4GazeboConfig.model_validate(config_doc)
    manifest = ProviderManifest(
        provider_id=flight_provider.provider_id,
        adapter=flight_provider.adapter,
        implementation=flight_provider.workload.implementation,
        runtime_image=flight_provider.workload.runtime.image,
        config_digest=hashlib.sha256(config_file.read_bytes()).hexdigest(),
        capabilities=flight_provider.capabilities,
        protocol_schema=flight_provider.protocol_schema,
        artifact_requirements=flight_provider.artifact_requirements,
    )
    provider = object.__new__(Px4GazeboProvider)
    provider._config = config
    provider._manifest = manifest
    provider._scenario = run.scenario
    provider._runtime_endpoint = RuntimeEndpoint(
        host="127.0.0.1", port=flight_provider.port
    )
    provider._run_id = run.run_id
    provider._session_token = SESSION_TOKEN
    payload = provider._config_payload()
    return config, manifest, payload


def _service_prepare(
    run,
    flight_provider,
    bundle_root: Path,
    config_doc: dict[str, object],
    manifest,
    payload: dict[str, object],
    world_source_sdf: Path,
    world_name: str,
):
    _assert_service_pins(config_doc)
    scenario = _SERVICE.ValidatedWorkloadScenario(
        scenario_digest=run.scenario.scenario_digest,
        scenario={
            "frame_authority": {},
            "launch_sites": [],
            "semantic_targets": [],
            "buildings": [],
            "entities": [],
        },
        assets=(),
    )
    # The configured vehicle dicts are the strict provider-config vehicle
    # records; WorkloadIdentity consumes them as raw mapping values.
    from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig

    vehicles = tuple(
        vehicle.model_dump(mode="json")
        for vehicle in Px4GazeboConfig.model_validate(config_doc).vehicles
    )
    identity = _SERVICE.WorkloadIdentity(
        run_id=run.run_id,
        seed=run.seed,
        provider_id=flight_provider.provider_id,
        provider_port=flight_provider.port,
        runtime_image=flight_provider.workload.runtime.image,
        config_digest=manifest.config_digest,
        scenario_digest=run.scenario.scenario_digest,
        scenario=scenario,
        bundle_root=bundle_root,
        artifact_requirements=tuple(
            requirement.model_dump(mode="json")
            for requirement in flight_provider.artifact_requirements
        ),
        clock_step_ns=run.environment.clock.step_ns,
        provider_config=config_doc,
        world_name=world_name,
        world_source_sdf=world_source_sdf,
        vehicles=vehicles,
        inspections=(),
        inspection_target_model_sdfs=(),
        airspace_transition=None,
    )
    service = object.__new__(_SERVICE.Px4GazeboService)
    service._rpc_port = flight_provider.port
    service._workload_identity = identity
    service._session_token = SESSION_TOKEN
    prepare_payload = dict(payload)
    prepare_payload["session_token"] = SESSION_TOKEN
    prepared = service._parse_prepare(prepare_payload)
    return prepared


def _assert_service_pins(config_doc: dict[str, object]) -> None:
    px4 = config_doc["px4"]
    gazebo = config_doc["gazebo"]
    mavsdk = config_doc["mavsdk"]
    assert px4 == {"version": _SERVICE.PX4_VERSION, "commit": _SERVICE.PX4_COMMIT}
    assert gazebo == {
        "version": _SERVICE.GAZEBO_VERSION,
        "commit": _SERVICE.GAZEBO_COMMIT,
    }
    assert mavsdk == {
        "version": _SERVICE.MAVSDK_VERSION,
        "commit": _SERVICE.MAVSDK_COMMIT,
    }


def test_end_to_end_logistics_world_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    bundle_root, run = materialize_logistics_suite(
        tmp_path / "bundle", origin=origin, base_world=base
    )

    plan = build_execution_plan(
        run,
        executor_kind="docker_reference",
        bundle_root=bundle_root,
        task_package_resolvers=builtin_task_package_resolvers(),
        resolved_scene_origin=origin,
        base_world_sdf=base,
        provider_registry=logistics_fixture_provider_registry(),
    )
    assert plan.logistics_world is not None
    assert plan.logistics_world.provider_id == PX4_PROVIDER_ID
    flight_workload = next(
        workload
        for workload in plan.runtime_workloads
        if workload.workload_id == PX4_PROVIDER_ID
    )
    derived = flight_workload.derived_inputs[0]
    assert derived.destination == DEFAULT_LOGISTICS_WORLD_DESTINATION
    assert derived.sha256 == plan.logistics_world.world_sha256
    identity_bytes = derived.content_utf8.encode("utf-8")
    assert hashlib.sha256(identity_bytes).hexdigest() == derived.sha256

    # The provider config on the materialization bundle is the SAME digest the
    # executor derived (the fail-closed binding already re-read and validated it
    # inside apply_logistics_world_materialization).
    flight_provider = next(
        provider
        for provider in run.environment.providers
        if provider.provider_id == PX4_PROVIDER_ID
    )
    config_doc = _flight_config(bundle_root)
    assert config_doc["world_input"] == {
        "schema_version": "aero-bench.px4-gazebo/bundle-world/v1",
        "source": "bundle",
        "path": DEFAULT_LOGISTICS_WORLD_DESTINATION,
        "sha256": derived.sha256,
    }
    configured_world_name = config_doc["world_name"]
    assert configured_world_name == _world_name_of(identity_bytes)

    # ---- Docker input staging ---------------------------------------------- #
    executor = _docker_executor()
    capture = tmp_path / "staged-input-volume"
    captured_roots: list[Path] = []

    def fake_copy_tree_into_volume(
        workload,
        *,
        volume: str,
        source_root: Path,
        seed_name: str,
        destination: str,
        handle: object,
    ) -> None:
        del volume, seed_name, destination, handle
        captured_roots.append(source_root)
        shutil.copytree(source_root, capture)

    monkeypatch.setattr(
        executor, "_copy_tree_into_volume", fake_copy_tree_into_volume
    )
    executor._populate_workload_inputs(
        plan,
        flight_workload,
        "input-volume",
        "logistics",
        handle=SimpleNamespace(
            run_id=run.run_id, owner_token="o" * 64, staging_containers=[]
        ),
    )
    assert len(captured_roots) == 1
    staged_world = capture / DEFAULT_LOGISTICS_WORLD_DESTINATION
    assert staged_world.is_file()
    staged_bytes = staged_world.read_bytes()
    assert hashlib.sha256(staged_bytes).hexdigest() == derived.sha256
    assert staged_bytes == identity_bytes

    # ---- Provider prepare payload ------------------------------------------ #
    config, manifest, payload = _manifest_and_payload(
        run, bundle_root, flight_provider, config_doc
    )
    assert payload["world_input"] == config_doc["world_input"]
    assert payload["world_input"]["sha256"] == derived.sha256

    # ---- Service world selection on the staged input volume ----------------- #
    bundle_root_input = capture / "bundle"
    world_name, source = _SERVICE._bundle_world_sdf(
        provider_config=config_doc, bundle_root=bundle_root_input
    )
    assert world_name == configured_world_name
    assert source.read_bytes() == staged_bytes
    assert hashlib.sha256(source.read_bytes()).hexdigest() == derived.sha256

    prepared = _service_prepare(
        run,
        flight_provider,
        bundle_root_input,
        config_doc,
        manifest,
        payload,
        world_source_sdf=source,
        world_name=world_name,
    )
    assert prepared.world_input["sha256"] == derived.sha256
    assert prepared.world_source_sdf == source
    assert prepared.world_name == configured_world_name

    # ---- RealPx4Stack hands EXACTLY the verified bytes to Gazebo ------------- #
    stack_tmp = tmp_path / "stack-tmp"
    (stack_tmp / "worlds").mkdir(parents=True, exist_ok=True)
    stack = _SERVICE.RealPx4Stack(
        tmp_dir=stack_tmp, bundle_root=bundle_root_input
    )
    spawned: list[tuple[tuple[str, ...], object]] = []

    async def fake_spawn(argv, *, cwd, env, log_name):
        spawned.append((argv, env))

    async def fake_wait_for_world(config):
        del config

    monkeypatch.setattr(stack, "_spawn_process", fake_spawn)
    monkeypatch.setattr(stack, "_wait_for_world", fake_wait_for_world)
    monkeypatch.setattr(stack, "_gz_bin", "gz")

    import asyncio

    asyncio.run(stack._start_gazebo(prepared, seed=run.seed))

    paused_copy = stack_tmp / "worlds" / f"{configured_world_name}.sdf"
    assert paused_copy.is_file()
    assert len(spawned) == 1
    argv, _env = spawned[0]
    assert argv[0] == "gz"
    assert "sim" in argv
    assert argv[-1] == str(paused_copy)
    assert "-r" not in argv and "--run" not in argv

    # ---- Pinned parser verification of the paused world ---------------------- #
    paused_bytes = paused_copy.read_bytes()
    ET.fromstring(paused_bytes)
    root = ET.fromstring(paused_bytes)
    paused = next(
        child
        for child in root.iter()
        if _local_name(child.tag) == "paused"
    )
    assert paused.text.strip() == "true"
    model_names = {
        child.attrib.get("name")
        for child in root.iter()
        if _local_name(child.tag) == "model" and child.attrib.get("name")
    }
    launch_pads = {name for name in model_names if name.startswith("launch_pad.")}
    facility_models = {name for name in model_names if name.startswith("facility_")}
    assert len(launch_pads) == 9  # 2 vertiport + 2 hub + 3 charger (x2)
    assert len(facility_models) >= 2
    # The executor-staged SDF identity itself carries the spliced models; the
    # paused copy re-serializes through the service's minidom writer, but the
    # model inventory of the paused world must be exactly the spliced set.
    assert b"launch_pad." in identity_bytes and b"facility_" in identity_bytes


def _world_name_of(world_bytes: bytes) -> str:
    root = ET.fromstring(world_bytes)
    worlds = [
        element for element in root if _local_name(element.tag) == "world"
    ]
    assert len(worlds) == 1
    return worlds[0].attrib["name"]


def _modified_bundle(root: Path, target: Path, mutate) -> Path:
    shutil.copytree(root, target)
    config_path = target / "configs/providers" / f"{PX4_PROVIDER_ID}.json"
    doc = json.loads(config_path.read_text(encoding="utf-8"))
    mutate(doc)
    config_path.write_text(
        json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return target


@pytest.mark.parametrize(
    "mutate,match",
    (
        (
            lambda doc: doc.__setitem__("world_input", None),
            "must declare world_input",
        ),
        (
            lambda doc: doc["world_input"].__setitem__("sha256", "f" * 64),
            "world_input.sha256 does not match",
        ),
        (
            lambda doc: doc["world_input"].__setitem__(
                "path", "bundle/alias/world.sdf"
            ),
            "world_input.path does not match",
        ),
        (
            lambda doc: doc.__setitem__("world_name", "some_other_world"),
            "world_name does not match",
        ),
    ),
)
def test_end_to_end_binding_fails_closed(
    tmp_path: Path, mutate, match: str
) -> None:
    from aero_bench.config.loader import BundleReader
    from aero_bench.executor.facility_world import logistics_package_for_run

    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    bundle_root, run = materialize_logistics_suite(
        tmp_path / "valid-bundle", origin=origin, base_world=base
    )
    plan = build_execution_plan(
        run,
        executor_kind="docker_reference",
        bundle_root=bundle_root,
        task_package_resolvers=builtin_task_package_resolvers(),
        resolved_scene_origin=origin,
        base_world_sdf=base,
        provider_registry=logistics_fixture_provider_registry(),
    )
    package = logistics_package_for_run(
        reader=BundleReader(bundle_root), run=run
    )
    bad_root = _modified_bundle(
        bundle_root, tmp_path / "bad-bundle", mutate
    )
    bad_plan = plan.model_copy(update={"bundle_root": str(bad_root)})
    with pytest.raises(LogisticsWorldMaterializationError, match=match):
        apply_logistics_world_materialization(
            bad_plan,
            package=package,
            origin=origin,
            base_world=base,
        )
    assert plan.logistics_world is not None
    assert bad_plan.run == plan.run  # the immutable ResolvedRun is untouched


def test_build_execution_plan_requires_origin_and_base_world(
    tmp_path: Path,
) -> None:
    origin = _origin()
    base = base_world_sdf_bytes(origin=origin)
    bundle_root, run = materialize_logistics_suite(
        tmp_path / "bundle", origin=origin, base_world=base
    )
    with pytest.raises(LogisticsWorldMaterializationError, match="resolved SceneOrigin"):
        build_execution_plan(
            run,
            executor_kind="docker_reference",
            bundle_root=bundle_root,
            task_package_resolvers=builtin_task_package_resolvers(),
            base_world_sdf=base,
            provider_registry=logistics_fixture_provider_registry(),
        )
    with pytest.raises(LogisticsWorldMaterializationError, match="compiled base world"):
        build_execution_plan(
            run,
            executor_kind="docker_reference",
            bundle_root=bundle_root,
            task_package_resolvers=builtin_task_package_resolvers(),
            resolved_scene_origin=origin,
            provider_registry=logistics_fixture_provider_registry(),
        )
