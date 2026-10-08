"""Preserve native systems and timing semantics when removing publication waits."""

import asyncio
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
from service import config
from service.runtime import Runtime


@pytest.mark.parametrize("world_scene", [False, True])
def test_pose_rate_preserves_systems_world_and_physics(
    tmp_path, monkeypatch, world_scene
):
    root = tmp_path / "native"
    (root / "share/gz").mkdir(parents=True)
    scene = '<plugin name="gz::sim::systems::SceneBroadcaster" filename="scene">'
    (root / "share/gz/server.config").write_text(
        '<server_config><plugins><plugin name="Physics" filename="physics"/>'
        + scene
        + "<dynamic_pose_hertz>60</dynamic_pose_hertz></plugin>"
        '<plugin name="Imu" filename="imu"><custom>keep</custom></plugin>'
        "</plugins></server_config>"
    )
    world = tmp_path / "source.sdf"
    world.write_text(
        '<sdf version="1.9"><world name="custom"><physics><max_step_size>0.004</max_step_size>'
        "<real_time_factor>1</real_time_factor></physics><spherical_coordinates>"
        "<world_frame_orientation>ENU</world_frame_orientation><latitude_deg>47</latitude_deg>"
        "<longitude_deg>8</longitude_deg><elevation>100</elevation></spherical_coordinates>"
        + (
            scene + "<dynamic_pose_hertz>30</dynamic_pose_hertz></plugin>"
            if world_scene
            else ""
        )
        + "</world></sdf>"
    )
    monkeypatch.setattr(config, "ROOT", root)
    monkeypatch.setattr(config, "catalog", lambda: {"x500": 4001})
    output = tmp_path / "run"
    output.mkdir()
    result = config.prepare(
        dict(
            seed=42,
            world=str(world),
            vehicles=[dict(id="v0", model="x500", spawn=[1, 2, 3, 0, 0, 0])],
            warmup=1_000_000_000,
        ),
        output,
    )
    plugins = ET.parse(output / "server.config").getroot().find("plugins")
    assert [p.get("name") for p in plugins] == [
        "Physics",
        "gz::sim::systems::SceneBroadcaster",
        "Imu",
    ]
    assert plugins[2].findtext("custom") == "keep"
    assert plugins[1].findtext("dynamic_pose_hertz") == "1000"
    rewritten = ET.parse(result.sdf).getroot().find("world")
    assert result.physics_step_ns == 4_000_000
    assert rewritten.findtext("physics/max_step_size") == "0.004"
    assert rewritten.findtext("physics/real_time_factor") == "0"
    assert rewritten.findtext("include/pose") == "1 2 3 0 0 0"
    if world_scene:
        assert rewritten.findtext("plugin/dynamic_pose_hertz") == "1000"
    assert ET.parse(world).getroot().findtext("world/physics/real_time_factor") == "1"


def test_equal_time_advance_keeps_pending_commands_and_native_world_held():
    async def scenario():
        runtime = Runtime()
        runtime.config = SimpleNamespace(
            physics_step_ns=4_000_000,
            vehicles=[SimpleNamespace(id="v0", model_name="aas_v0")],
        )
        runtime.processes = SimpleNamespace(check=lambda: None)
        sample = {"vehicle": "v0", "sim_ns": 0}
        runtime.telemetry = SimpleNamespace(
            check=lambda: None, samples=lambda *args: [sample]
        )

        async def native_pose(model):
            assert model == "aas_v0"
            return {"sim_ns": 0}

        # No step/dispatch/drain methods: calling them is a test failure.
        runtime.gazebo = SimpleNamespace(sample=native_pose)
        runtime.commands = SimpleNamespace()
        assert await runtime.advance({"to_sim_ns": 0}) == {
            "reached_sim_ns": 0,
            "telemetry": [sample],
            "contacts": [],
            "command_updates": [],
        }
        with pytest.raises(ValueError, match="align"):
            await runtime.advance({"to_sim_ns": 1})

    asyncio.run(scenario())
