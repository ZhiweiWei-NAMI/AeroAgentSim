"""Portable schemas validate exact authored command shapes and native outcomes."""

from typing import Any

import pytest
from aerokernel import KernelError, MemoryRegistry

from aeroagentsim.adapters.schemas import command_descriptors, event_descriptors

CASES = [
    ("px4_gazebo", "arm", {"entity": "uav"}),
    ("px4_gazebo", "takeoff", {"entity": "uav", "altitude_m": 5.0}),
    (
        "px4_gazebo",
        "goto",
        {"entity": "uav", "position_enu": [1.0, 2.0, 3.0], "yaw_deg": 90.0},
    ),
    ("px4_gazebo", "hold", {"entity": "uav"}),
    ("px4_gazebo", "land", {"entity": "uav"}),
    ("px4_gazebo", "disarm", {"entity": "uav"}),
    ("sumo", "set_speed", {"vehicle": "v0", "speed": 3.0}),
    ("sumo", "reroute", {"vehicle": "v0"}),
    ("sumo", "change_target", {"vehicle": "v0", "edge": "e1"}),
    ("sumo", "lane_restriction", {"lane": "l0", "disallowed": []}),
    ("sumo", "tls_phase", {"tls": "t0", "phase": 1}),
    ("sumo", "add_vehicle", {"vehicle": "v1", "route_id": "r0"}),
    ("sumo", "remove_vehicle", {"vehicle": "v0"}),
    (
        "ns3",
        "send",
        {"packet_id": "p1", "src": "a", "dst": "b", "size": 10, "payload_ref": "r"},
    ),
]


@pytest.mark.parametrize("backend,action,payload", CASES)
def test_exact_flattened_commands(
    backend: str, action: str, payload: dict[str, Any]
) -> None:
    registry = MemoryRegistry(
        (), messages=command_descriptors(backend) + event_descriptors(backend)
    )
    schema = registry.message(f"adapters.{backend}.{action}").schema
    registry.validate(schema, payload)
    with pytest.raises(KernelError):
        registry.validate(schema, {**payload, "undeclared": 0})
    for key in schema["required"]:
        with pytest.raises(KernelError):
            registry.validate(schema, {k: v for k, v in payload.items() if k != key})


@pytest.mark.parametrize("backend", ["px4_gazebo", "sumo", "ns3"])
def test_unknown_backend_rejected(backend: str) -> None:
    assert event_descriptors(backend)
    with pytest.raises(ValueError):
        command_descriptors("unsupported")


def test_ns3_terminal_result_preserves_delivery_times() -> None:
    registry = MemoryRegistry((), messages=command_descriptors("ns3"))
    schema = registry.message("adapters.ns3.send").result_schema
    assert schema is not None
    registry.validate(
        schema,
        {
            "packet_id": "p1",
            "src": "a",
            "dst": "b",
            "size": 10,
            "sent_ns": 10,
            "received_ns": 30,
            "available_sim_ns": 100,
            "available_ns": 100,
            "payload_ref": "r",
        },
    )
