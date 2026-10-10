from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from aero_bench.providers import rpc as _rpc

from aero_bench.providers.px4_gazebo.config import (
    AirspaceTransitionSpec,
    Point3D,
    Pose,
    Px4GazeboConfig,
    SoftwareIdentity,
    VehicleSpec,
)


_SERVICE_PATH = Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
sys.modules["rpc"] = _rpc
_SERVICE_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_px4_gazebo_service", _SERVICE_PATH
)
assert _SERVICE_SPEC is not None and _SERVICE_SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SERVICE_SPEC)
sys.modules[_SERVICE_SPEC.name] = _SERVICE
_SERVICE_SPEC.loader.exec_module(_SERVICE)


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _airspace(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "world_id": "default",
        "world_digest": _DIGEST_A,
        "region_id": "airspace.no-fly.1",
        "region_digest": _DIGEST_B,
        "incident_vehicle": "x500_mono_cam_0",
        "transition_topic": "/aero_bench/airspace/transitions",
        "min_east_m": -10.0,
        "max_east_m": 10.0,
        "min_north_m": -10.0,
        "max_north_m": 10.0,
        "min_up_m": 0.0,
        "max_up_m": 20.0,
    }
    value.update(overrides)
    return value


def _config(airspace: dict[str, object] | None) -> Px4GazeboConfig:
    vehicle = VehicleSpec(
        vehicle_id="uav.1",
        system_id=1,
        mavsdk_udp_port=14540,
        px4_mavlink_udp_port=14580,
        mavsdk_grpc_port=15040,
        sys_autostart=4001,
        model="gz_x500_mono_cam",
        gazebo_model_name="x500_mono_cam_0",
        gazebo_resource="x500_mono_cam",
        initial_pose=Pose(
            x_m=0.0,
            y_m=0.0,
            z_m=0.1,
            roll_rad=0.0,
            pitch_rad=0.0,
            yaw_rad=0.0,
        ),
    )
    return Px4GazeboConfig(
        schema_version="aero-bench.px4-gazebo/v1",
        provider_id="flight",
        px4=SoftwareIdentity(version="px4", commit="a" * 40),
        gazebo=SoftwareIdentity(version="gazebo", commit="b" * 40),
        mavsdk=SoftwareIdentity(version="mavsdk", commit="c" * 40),
        world_name="default",
        world_sdf="default.sdf",
        physics_step_ns=4_000_000,
        step_length_ns=2_000_000_000,
        px4_executable="px4",
        gazebo_executable="gz",
        mavsdk_server_executable="mavsdk-server",
        vehicles=(vehicle,),
        required_commands=("px4", "gz", "mavsdk-server"),
        command_timeout_ms=120_000,
        maximum_agent_decision_wall_time_ms=120_000,
        heartbeat_timeout_fixed_margin_ms=10_000,
        airspace_transition=airspace,
    )


def test_airspace_transition_accepts_ordered_real_identity() -> None:
    spec = AirspaceTransitionSpec.model_validate(_airspace())
    assert spec.incident_vehicle == "x500_mono_cam_0"


@pytest.mark.parametrize(
    "overrides",
    (
        {"world_digest": "0" * 64},
        {"region_digest": "0" * 64},
        {"min_east_m": 2.0, "max_east_m": 1.0},
        {"min_north_m": 2.0, "max_north_m": 1.0},
        {"min_up_m": 2.0, "max_up_m": 1.0},
        {"min_east_m": math.inf},
        {"transition_topic": "relative/topic"},
    ),
)
def test_airspace_transition_rejects_unsafe_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AirspaceTransitionSpec.model_validate(_airspace(**overrides))


def test_config_rejects_airspace_world_mismatch() -> None:
    with pytest.raises(ValidationError):
        _config(_airspace(world_id="other"))


def test_config_rejects_undeclared_incident_vehicle() -> None:
    with pytest.raises(ValidationError):
        _config(_airspace(incident_vehicle="other_vehicle"))


def test_world_preparation_injects_read_only_airspace_plugin(tmp_path: Path) -> None:
    source = tmp_path / "source.sdf"
    destination = tmp_path / "worlds" / "prepared.sdf"
    source.write_text(
        '<sdf version="1.9"><world name="default" /></sdf>', encoding="utf-8"
    )

    _SERVICE.ensure_paused_world_sdf(
        source,
        destination,
        airspace_transition=_airspace(),
    )

    root = ElementTree.parse(destination).getroot()
    world = next(element for element in root if element.tag == "world")
    plugins = [element for element in world if element.tag == "plugin"]
    plugin = next(
        element
        for element in plugins
        if element.attrib.get("name") == "aero_bench::gazebo::AirspaceTransition"
    )
    assert plugin.attrib == {
        "filename": "libaero_bench_gazebo_airspace_transition.so",
        "name": "aero_bench::gazebo::AirspaceTransition",
    }
    assert [
        (element.attrib["filename"], element.attrib["name"])
        for element in plugins
        if element is not plugin
    ] == list(_SERVICE.GAZEBO_SERVER_SYSTEM_PLUGINS)
    sensors = next(
        element
        for element in plugins
        if element.attrib.get("name") == _SERVICE.GAZEBO_SENSORS_SYSTEM_NAME
    )
    assert sensors.findtext("render_engine") == "ogre2"
    assert {element.tag for element in plugin} == {
        "world_id",
        "world_digest",
        "region_id",
        "region_digest",
        "incident_vehicle",
        "transition_topic",
        "min_east_m",
        "max_east_m",
        "min_north_m",
        "max_north_m",
        "min_up_m",
        "max_up_m",
    }
    assert next(element for element in world if element.tag == "paused").text == "true"


def test_world_preparation_rejects_duplicate_airspace_plugin(tmp_path: Path) -> None:
    source = tmp_path / "source.sdf"
    source.write_text(
        """<sdf version=\"1.9\"><world name=\"default\">
        <plugin name=\"aero_bench::gazebo::AirspaceTransition\"
                filename=\"existing.so\" />
        </world></sdf>""",
        encoding="utf-8",
    )
    with pytest.raises(_SERVICE.Px4ServiceError):
        _SERVICE.ensure_paused_world_sdf(
            source, tmp_path / "prepared.sdf", airspace_transition=_airspace()
        )
