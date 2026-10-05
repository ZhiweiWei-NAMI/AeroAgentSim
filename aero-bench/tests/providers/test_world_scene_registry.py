from __future__ import annotations

from aero_bench.providers import builtin_provider_registry


def test_builtin_registry_excludes_world_scene_projection_adapter() -> None:
    """Static scene authority remains compiler-owned, never registry-owned."""

    adapters = builtin_provider_registry().adapters
    assert adapters == (
        "inspection.business",
        "logistics.business",
        "ns3.rpc",
        "px4.gazebo",
        "sumo.traci",
    )
    assert "world.scene.rpc" not in adapters
