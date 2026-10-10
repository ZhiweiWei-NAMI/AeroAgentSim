"""PX4/Gazebo bundle-world consumption seam (world-v2 surface).

The px4.gazebo service consumes the executor's staged bundle world from the
non-host input volume and ONLY that world for a declared bundle-world config:

    Px4GazeboConfig(world_input=BundleWorldInput(...), world_name=...)
        -> provider._config_payload carries the digest-pinned declaration
        -> Px4GazeboService._parse_prepare binds it (exact-match or reject)
        -> _bundle_world_sdf/startup resolves the staged SDF from
           AERO_BENCH_BUNDLE_DIR with symlink escape rejection + SHA-256 pin
        -> _start_gazebo passes EXACTLY the verified bytes through
           ensure_paused_world_sdf to the paused copy `gz sim -s` launches

Provider configs without ``world_input`` keep the explicit non-logistics
world contract unchanged; there is no image/scenario fallback for a declared
bundle world.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aero_bench.world import frame_math
from aero_bench.world import workload_scenario

import aero_bench.providers.rpc as _RPC
from aero_bench.providers.px4_gazebo.config import BundleWorldInput, Px4GazeboConfig

# The standalone service image installs pure modules under the short names the
# Dockerfile copies in (``aero_frame_math``, ``workload_scenario``, ``rpc``).
# This worktree deliberately has no tests/conftest.py (no non-deliverable
# baseline dependency file), so the service module loads here with the same
# module aliases the image runtime provides.
sys.modules["aero_frame_math"] = frame_math
sys.modules["workload_scenario"] = workload_scenario

_SERVICE_PATH = Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
_SPEC = importlib.util.spec_from_file_location(
    "px4_gazebo_bundle_world_tests_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules["rpc"] = _RPC
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)

RUN_ID = "a" * 64
SESSION_TOKEN = "9" * 64
RUNTIME_IMAGE = "registry.test/px4-gazebo@sha256:" + "1" * 64
CONFIG_DIGEST = "b" * 64
SCENARIO_DIGEST = "e" * 64
STEP_LENGTH_NS = 50_000_000
PHYSICS_STEP_NS = 1_000_000
SERVICE_PORT = 17631
PROVIDER_ID = "flight"
WORLD_NAME = "logistics_city"
BUNDLE_RELATIVE = "facility/logistics_world.sdf"
WORLD_INPUT_PATH = f"bundle/{BUNDLE_RELATIVE}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _staged_world_bytes() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sdf version="1.10">\n'
        f'  <world name="{WORLD_NAME}">\n'
        "    <paused>false</paused>\n"
        "    <gravity>0 0 -9.81</gravity>\n"
        '    <model name="ground"><static>true</static>'
        '<link name="ground_link">'
        '<collision name="ground_collision"><geometry><plane>'
        '<normal>0 0 1</normal><size>1000 1000</size></plane></geometry></collision>'
        "</link></model>\n"
        '    <model name="launch_pad.facility-1"><static>true</static>'
        "<pose>10.0 5.0 0.07 0 0 0</pose>"
        '<link name="pad">'
        '<collision name="box"><geometry><box><size>3 3 0.1</size></box></geometry></collision>'
        '<visual name="v"><geometry><box><size>3 3 0.1</size></box></geometry></visual>'
        "</link></model>\n"
        '    <model name="facility_facility-1"><static>true</static>'
        "<pose>-20.0 12.0 0 0 0 0</pose></model>\n"
        "  </world>\n"
        "</sdf>\n"
    ).encode("utf-8")


def _stage_bundle_world(bundle_root: Path) -> bytes:
    destination = bundle_root / BUNDLE_RELATIVE
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = _staged_world_bytes()
    destination.write_bytes(payload)
    return payload


def _world_input_dict(*, sha256: str | None = None) -> dict[str, str]:
    payload = _staged_world_bytes()
    return {
        "schema_version": "aero-bench.px4-gazebo/bundle-world/v1",
        "source": "bundle",
        "path": WORLD_INPUT_PATH,
        "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
    }


def _vehicle() -> dict[str, object]:
    return {
        "vehicle_id": "logistics_drone_1",
        "system_id": 1,
        "mavsdk_udp_port": 14540,
        "px4_mavlink_udp_port": 14570,
        "mavsdk_grpc_port": 50051,
        "sys_autostart": 4001,
        "model": "gz_x500_mono_cam",
        "gazebo_model_name": "x500_mono_cam_0",
        "gazebo_resource": "x500_mono_cam",
        "initial_pose": {
            "x_m": 0.0,
            "y_m": 0.0,
            "z_m": 0.1,
            "roll_rad": 0.0,
            "pitch_rad": 0.0,
            "yaw_rad": 0.0,
        },
    }


def _physical_completion_policy() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.px4-physical-completion-policy/v2",
        "physical_sim_timeout_ns": 500_000_000,
        "min_settle_samples": 2,
        "settle_duration_ns": 20_000_000,
        "takeoff_altitude_tolerance_m": 0.25,
        "goto_horizontal_tolerance_m": 1.0,
        "goto_vertical_tolerance_m": 0.5,
        "goto_minimum_progress_m": 5.0,
        "hold_drift_radius_m": 0.75,
        "max_horizontal_settled_speed_m_s": 0.4,
        "max_vertical_settled_speed_m_s": 0.3,
        "landing_max_speed_m_s": 0.35,
        "landing_max_height_proxy_m": 0.2,
        "disarm_requires_contact": True,
        "arm_allowed_modes": ["READY"],
        "disarm_allowed_modes": ["LAND"],
        "takeoff_allowed_modes": ["TAKEOFF"],
        "goto_allowed_modes": ["MISSION"],
        "hold_allowed_modes": ["HOLD"],
        "land_allowed_modes": ["LAND"],
    }


def _parsed_policy() -> dict[str, object]:
    return _SERVICE._physical_completion_policy(_physical_completion_policy())


def _provider_config_dict(
    *, world_input: dict[str, str] | None,
) -> dict[str, object]:
    return {
        "provider_id": PROVIDER_ID,
        "schema_version": "aero-bench.px4-gazebo/v3",
        "px4": {"version": _SERVICE.PX4_VERSION, "commit": _SERVICE.PX4_COMMIT},
        "gazebo": {
            "version": _SERVICE.GAZEBO_VERSION,
            "commit": _SERVICE.GAZEBO_COMMIT,
        },
        "mavsdk": {
            "version": _SERVICE.MAVSDK_VERSION,
            "commit": _SERVICE.MAVSDK_COMMIT,
        },
        "engine_binding_id": "binding.gazebo",
        "physics_step_ns": PHYSICS_STEP_NS,
        "px4_executable": "px4",
        "gazebo_executable": "gz",
        "mavsdk_server_executable": "mavsdk-server",
        "vehicles": [_vehicle()],
        "required_commands": ["gz", "mavsdk-server", "px4"],
        "command_timeout_ms": 1_000,
        "physical_completion_policy": _parsed_policy(),
        "maximum_agent_decision_wall_time_ms": 1_000,
        "heartbeat_timeout_fixed_margin_ms": 100,
        "world_name": WORLD_NAME,
        "world_input": world_input,
    }


def _artifact_requirement() -> dict[str, object]:
    return {
        "artifact_id": "artifact.px4.trajectory",
        "artifact_type": "trajectory",
        "producer_id": PROVIDER_ID,
        "visibility": "private",
        "relative_path": "trajectory/evidence.jsonl",
        "max_size_bytes": 1_048_576,
        "source_asset_id": None,
    }


def _identity(
    *,
    provider_config: dict[str, object], bundle_root: Path,
    world_source_sdf: Path | None = None,
) -> _SERVICE.WorkloadIdentity:
    staged = world_source_sdf or bundle_root / BUNDLE_RELATIVE
    return _SERVICE.WorkloadIdentity(
        run_id=RUN_ID,
        seed=7,
        provider_id=PROVIDER_ID,
        provider_port=SERVICE_PORT,
        runtime_image=RUNTIME_IMAGE,
        config_digest=CONFIG_DIGEST,
        scenario_digest=SCENARIO_DIGEST,
        scenario=_SERVICE.ValidatedWorkloadScenario(
            scenario_digest=SCENARIO_DIGEST,
            scenario={
                "frame_authority": {},
                "launch_sites": [],
                "semantic_targets": [],
                "buildings": [],
                "entities": [],
            },
            assets=(),
        ),
        bundle_root=bundle_root,
        artifact_requirements=(_artifact_requirement(),),
        clock_step_ns=STEP_LENGTH_NS,
        provider_config=provider_config,
        world_name=WORLD_NAME,
        world_source_sdf=staged,
        vehicles=(_vehicle(),),
        inspections=(),
        inspection_target_model_sdfs=(),
    )


def _service(identity: _SERVICE.WorkloadIdentity) -> _SERVICE.Px4GazeboService:
    service = object.__new__(_SERVICE.Px4GazeboService)
    service._rpc_port = SERVICE_PORT
    service._workload_identity = identity
    service._session_token = SESSION_TOKEN
    return service


def _prepare_payload(*, world_input: dict[str, str] | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider_id": PROVIDER_ID,
        "run_id": RUN_ID,
        "protocol_version": _SERVICE.PROTOCOL_VERSION,
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": CONFIG_DIGEST,
        "scenario_digest": SCENARIO_DIGEST,
        "artifact_requirements": [_artifact_requirement()],
        "session_token": SESSION_TOKEN,
        "endpoint": {"host": "127.0.0.1", "port": SERVICE_PORT},
    }
    if world_input is not None:
        payload["world_input"] = world_input
    return payload


# --------------------------------------------------------------------------- #
# typed digest-pinned bundle-world input contract (config)
# --------------------------------------------------------------------------- #

def test_bundle_world_input_typed_shape() -> None:
    declared = BundleWorldInput.model_validate(_world_input_dict())
    assert declared.source == "bundle"
    assert declared.path == WORLD_INPUT_PATH
    assert len(declared.sha256) == 64


@pytest.mark.parametrize(
    "field,value",
    (
        ("schema_version", "aero-bench.px4-gazebo/bundle-world/v2"),
        ("source", "host"),
        ("path", "facility/logistics_world.sdf"),
        ("path", "bundle/../logistics_world.sdf"),
        ("path", "/bundle/facility/logistics_world.sdf"),
        ("sha256", "f" * 63),
    ),
)
def test_bundle_world_input_rejects_bad_fields(field: str, value: str) -> None:
    payload = _world_input_dict()
    payload[field] = value
    with pytest.raises(ValidationError):
        BundleWorldInput.model_validate(payload)


def test_bundle_world_config_requires_world_name() -> None:
    base = _provider_config_dict(world_input=_world_input_dict())
    base["world_name"] = None
    with pytest.raises(ValidationError):
        Px4GazeboConfig.model_validate(base)


def test_bundle_world_config_keeps_existing_world_v2_fields_valid() -> None:
    config = Px4GazeboConfig.model_validate(
        _provider_config_dict(world_input=_world_input_dict())
    )
    assert config.schema_version == "aero-bench.px4-gazebo/v3"
    assert config.world_input is not None
    assert config.world_name == WORLD_NAME
    assert config.physics_step_ns == PHYSICS_STEP_NS
    assert config.physical_completion_policy.schema_version == (
        "aero-bench.px4-physical-completion-policy/v2"
    )
    assert config.airspace_transition is None


# --------------------------------------------------------------------------- #
# prepare binds declaration (provider _config_payload -> service _parse_prepare)
# --------------------------------------------------------------------------- #

def test_prepare_binds_matching_world_input(tmp_path: Path) -> None:
    payload = _staged_world_bytes()
    declared = _world_input_dict(sha256=hashlib.sha256(payload).hexdigest())
    staged = tmp_path / BUNDLE_RELATIVE
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(payload)
    identity = _identity(
        provider_config=_provider_config_dict(world_input=declared),
        bundle_root=tmp_path,
    )
    prepared = _service(identity)._parse_prepare(
        _prepare_payload(world_input=declared)
    )
    assert prepared.world_input == declared
    assert prepared.world_name == WORLD_NAME
    assert prepared.world_sdf == f"{WORLD_NAME}.sdf"
    assert prepared.world_source_sdf == staged


def test_prepare_rejects_diverging_world_input_digest(tmp_path: Path) -> None:
    declared = _world_input_dict()
    staged = tmp_path / BUNDLE_RELATIVE
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(_staged_world_bytes())
    identity = _identity(
        provider_config=_provider_config_dict(world_input=declared),
        bundle_root=tmp_path,
    )
    forged = dict(declared)
    forged["sha256"] = "f" * 64
    with pytest.raises(_SERVICE.Px4ServiceError):
        _service(identity)._parse_prepare(_prepare_payload(world_input=forged))


def test_prepare_requires_world_input_when_config_pins(tmp_path: Path) -> None:
    declared = _world_input_dict()
    staged = tmp_path / BUNDLE_RELATIVE
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(_staged_world_bytes())
    identity = _identity(
        provider_config=_provider_config_dict(world_input=declared),
        bundle_root=tmp_path,
    )
    with pytest.raises(_SERVICE.Px4ServiceError):
        _service(identity)._parse_prepare(_prepare_payload(world_input=None))


def test_prepare_rejects_world_input_when_config_does_not_pin(
    tmp_path: Path,
) -> None:
    identity = _identity(
        provider_config=_provider_config_dict(world_input=None),
        bundle_root=tmp_path,
    )
    with pytest.raises(_SERVICE.Px4ServiceError):
        _service(identity)._parse_prepare(_prepare_payload(world_input=_world_input_dict()))


def test_prepare_without_world_input_backward_compatible(tmp_path: Path) -> None:
    staged = tmp_path / "logistics_city.sdf"
    staged.write_text(
        f"<sdf><world name='{WORLD_NAME}'><model name='ground'>"
        "<static>true</static></model></world></sdf>",
        encoding="utf-8",
    )
    identity = _identity(
        provider_config=_provider_config_dict(world_input=None),
        bundle_root=tmp_path,
        world_source_sdf=staged,
    )
    prepared = _service(identity)._parse_prepare(_prepare_payload(world_input=None))
    assert prepared.world_input is None


def test_parse_world_input_rejects_escapes() -> None:
    raw = _world_input_dict()
    raw["path"] = "bundle/facility/../logistics_world.sdf"
    with pytest.raises(_SERVICE.Px4ServiceError):
        _SERVICE._parse_world_input_value(raw)


# --------------------------------------------------------------------------- #
# startup reads/stats/hash-verifies exact staged SDF; no image fallback
# --------------------------------------------------------------------------- #

def test_bundle_world_sdf_reads_staged_input_volume_only(tmp_path: Path) -> None:
    payload = _stage_bundle_world(tmp_path)
    declared = _world_input_dict(sha256=hashlib.sha256(payload).hexdigest())
    world_name, source = _SERVICE._bundle_world_sdf(
        provider_config=_provider_config_dict(world_input=declared),
        bundle_root=tmp_path,
    )
    assert world_name == WORLD_NAME
    assert source == tmp_path / BUNDLE_RELATIVE
    assert source.read_bytes() == payload


def test_bundle_world_sdf_rejects_missing_staged_world(tmp_path: Path) -> None:
    declared = _world_input_dict()
    with pytest.raises(_SERVICE.Px4ServiceError):
        _SERVICE._bundle_world_sdf(
            provider_config=_provider_config_dict(world_input=declared),
            bundle_root=tmp_path,
        )


def test_bundle_world_sdf_rejects_tampered_staged_world(tmp_path: Path) -> None:
    _stage_bundle_world(tmp_path)
    declared = _world_input_dict(sha256="f" * 64)
    with pytest.raises(_SERVICE.Px4ServiceError):
        _SERVICE._bundle_world_sdf(
            provider_config=_provider_config_dict(world_input=declared),
            bundle_root=tmp_path,
        )


def test_bundle_world_sdf_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.sdf"
    outside.write_bytes(_staged_world_bytes())
    bundle_root = tmp_path / "bundle-root"
    destination = bundle_root / BUNDLE_RELATIVE
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(outside)
    declared = _world_input_dict()
    with pytest.raises(_SERVICE.Px4ServiceError):
        _SERVICE._bundle_world_sdf(
            provider_config=_provider_config_dict(world_input=declared),
            bundle_root=bundle_root,
        )


def test_bundle_world_sdf_rejects_world_name_mismatch(tmp_path: Path) -> None:
    _stage_bundle_world(tmp_path)
    declared = _world_input_dict()
    config = _provider_config_dict(world_input=declared)
    config["world_name"] = "some_other_world"
    with pytest.raises(_SERVICE.Px4ServiceError):
        _SERVICE._bundle_world_sdf(provider_config=config, bundle_root=tmp_path)


def test_start_gazebo_passes_verified_bundle_bytes_to_paused_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _stage_bundle_world(tmp_path)
    declared = _world_input_dict(sha256=hashlib.sha256(payload).hexdigest())
    staged = tmp_path / BUNDLE_RELATIVE
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path / "stack-tmp", bundle_root=tmp_path)
    (tmp_path / "stack-tmp" / "worlds").mkdir(parents=True, exist_ok=True)
    spawned: list[tuple[tuple[str, ...], object]] = []

    async def spawn(argv: tuple[str, ...], *, cwd, env, log_name: str) -> None:
        spawned.append((argv, env))

    async def wait_for_world(config: object) -> None:
        del config

    monkeypatch.setattr(stack, "_spawn_process", spawn)
    monkeypatch.setattr(stack, "_wait_for_world", wait_for_world)
    monkeypatch.setattr(stack, "_gz_bin", "gz")

    config = _SERVICE.PreparedConfig(
        provider_id=PROVIDER_ID,
        run_id=RUN_ID,
        protocol_version=_SERVICE.PROTOCOL_VERSION,
        runtime_image=RUNTIME_IMAGE,
        config_digest=CONFIG_DIGEST,
        artifact_requirements=(_artifact_requirement(),),
        endpoint_host="127.0.0.1",
        endpoint_port=SERVICE_PORT,
        world_name=WORLD_NAME,
        world_sdf=f"{WORLD_NAME}.sdf",
        world_source_sdf=staged,
        world_input=declared,
        physics_step_ns=PHYSICS_STEP_NS,
        step_length_ns=STEP_LENGTH_NS,
        px4_executable="px4",
        gazebo_executable="gz",
        mavsdk_server_executable="mavsdk-server",
        vehicles=(),
        required_commands=("gz", "mavsdk-server", "px4"),
        command_timeout_ms=1_000,
        physical_completion_policy=_parsed_policy(),
        maximum_agent_decision_wall_time_ms=1_000,
        heartbeat_timeout_fixed_margin_ms=100,
        inspections=(),
        inspection_target_model_sdfs=(),
    )

    import asyncio

    asyncio.run(stack._start_gazebo(config, seed=7))

    world_copy = tmp_path / "stack-tmp" / "worlds" / f"{WORLD_NAME}.sdf"
    assert world_copy.is_file()
    root = ET.fromstring(world_copy.read_text(encoding="utf-8"))
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
    assert {"launch_pad.facility-1", "facility_facility-1"} <= model_names
    assert len(spawned) == 1
    argv, _env = spawned[0]
    assert argv[0] == "gz"
    assert "sim" in argv
    assert argv[-1] == str(world_copy)
