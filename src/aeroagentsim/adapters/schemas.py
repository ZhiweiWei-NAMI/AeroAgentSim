"""Typed flattened commands and native event/result records for the native adapters."""

from __future__ import annotations

from typing import Any

from aerokernel.registry import MemoryRegistry, MessageDescriptor

__all__ = [
    "BACKENDS",
    "backend_registry",
    "command_descriptors",
    "event_descriptors",
]

PX4_GAZEBO = "px4_gazebo"
SUMO = "sumo"
NS3 = "ns3"

BACKENDS: frozenset[str] = frozenset({PX4_GAZEBO, SUMO, NS3})

_UNKNOWN_BACKEND = "unknown backend; expected one of px4_gazebo, sumo, ns3"

# SUMO's documented vehicle classes accepted by lane_restriction, matching the
# native backend's class set exactly (containers/sumo/service/commands.py).
SUMO_VEHICLE_CLASSES: tuple[str, ...] = (
    "ignoring",
    "private",
    "emergency",
    "authority",
    "army",
    "vip",
    "pedestrian",
    "passenger",
    "hov",
    "taxi",
    "bus",
    "coach",
    "delivery",
    "truck",
    "trailer",
    "motorcycle",
    "moped",
    "bicycle",
    "evehicle",
    "tram",
    "rail_urban",
    "rail",
    "rail_electric",
    "rail_fast",
    "ship",
    "container",
    "cable_car",
    "subway",
    "aircraft",
    "wheelchair",
    "scooter",
    "drone",
    "custom1",
    "custom2",
)

_BOUND = 1e300
_STR_ARRAY: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "min_length": 1},
    "min_length": 0,
    "max_length": 65536,
}
_UNIQUE_CLASS_ARRAY: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string", "enum": list(SUMO_VEHICLE_CLASSES)},
    "min_length": 0,
    "max_length": 65536,
}


def _check_backend(backend: str) -> str:
    """Reject anything outside the known backend set."""
    if backend not in BACKENDS:
        raise ValueError(_UNKNOWN_BACKEND)
    return backend


def _str() -> dict[str, Any]:
    return {"type": "string", "min_length": 1}


def _num() -> dict[str, Any]:
    return {"type": "number"}


def _int() -> dict[str, Any]:
    return {"type": "integer", "minimum": 0}


def _bool() -> dict[str, Any]:
    return {"type": "boolean"}


def _enum(values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _vector(length: int, minimum: float, maximum: float) -> dict[str, Any]:
    return {
        "type": "vector",
        "length": length,
        "items": {"type": "number", "minimum": minimum, "maximum": maximum},
    }


def _schema(
    members: dict[str, Any], required: tuple[str, ...] = (), extra: bool = False
) -> dict[str, Any]:
    """Explicit closed portable record schema (required travels as a list)."""
    return {
        "type": "record",
        "members": members,
        "required": list(required),
        "extra": extra,
    }


def _record(members: dict[str, Any]) -> dict[str, Any]:
    """Closed nested record: every member typed, no extra members."""
    return _schema(members, (), extra=False)


# ---------------------------------------------------------------------------
# Nested native command observation members, exactly as the backends return.
# ---------------------------------------------------------------------------


def _sumo_observation() -> dict[str, Any]:
    """SUMO readback observations from the later tick's native projection."""
    return {
        "applied_at_sim_ns": _int(),
        "reason": _str(),
        "speed": _num(),
        "requested_speed": _num(),
        "route": _STR_ARRAY,
        "expected_route": _STR_ARRAY,
        "before_route": _STR_ARRAY,
        "requested_target": _str(),
        "lane": _str(),
        "disallowed": _UNIQUE_CLASS_ARRAY,
        "before_disallowed": _UNIQUE_CLASS_ARRAY,
        "requested_disallowed": _UNIQUE_CLASS_ARRAY,
        "tls": _str(),
        "phase": _int(),
        "requested_phase": _int(),
        "state": _str(),
        "vehicle": _str(),
        "present": _bool(),
        "type": _str(),
    }


def _observation(backend: str) -> dict[str, Any]:
    if backend == SUMO:
        return _record(_sumo_observation())
    # ns-3 returns its terminal outcome fields directly; no nested observation.
    return _record({})


# ---------------------------------------------------------------------------
# Command result schemas: acceptance receipt and later updates in one union.
# ---------------------------------------------------------------------------

_RESULT_STATUS: tuple[str, ...] = (
    "accepted",
    "rejected",
    "queued",
    "running",
    "executing",
    "succeeded",
    "failed",
)


def _result_schema(backend: str) -> dict[str, Any]:
    """Structured result covering receipts, updates and terminal outcomes.

    The member set is the union named by the adapter contract:
    ``command_id``, ``vehicle``, ``action``, ``status``, ``sim_ns``,
    ``reason``, ``observation``, plus the ns-3 packet outcome fields
    (``packet_id``, ``src``, ``dst``, ``sent_ns``, ``received_ns``,
    ``available_sim_ns``, ``size``, ``payload_ref``, ``rssi_dbm``,
    ``snr_db``) and the kernel publication boundary ``available_ns``. Acceptance is
    queued work rather than success, so no member is required here; ns-3's
    synchronous receipt additionally carries its own required ``status``
    share the same terminal outcome contract.
    """
    return _schema(
        {
            "command_id": _str(),
            "vehicle": _str(),
            "action": _str(),
            "status": _enum(_RESULT_STATUS),
            "sim_ns": _int(),
            "reason": _str(),
            "observation": _observation(backend),
            "packet_id": _str(),
            "src": _str(),
            "dst": _str(),
            "sent_ns": _int(),
            "received_ns": _int(),
            "available_sim_ns": _int(),
            "size": {"type": "integer", "minimum": 1},
            "payload_ref": _str(),
            "rssi_dbm": _num(),
            "snr_db": _num(),
            "available_ns": _int(),
        },
        (),
        extra=False,
    )


# ---------------------------------------------------------------------------
# Native event payload members.
# ---------------------------------------------------------------------------


def _entity_event() -> dict[str, Any]:
    """SUMO departed/arrived/removed/teleport record with availability."""
    return _schema(
        {
            "id": _str(),
            "kind": _enum(("vehicle", "person")),
            "sim_ns": _int(),
            "available_sim_ns": _int(),
            "available_ns": _int(),
        },
        ("id", "kind", "sim_ns", "available_ns"),
        extra=False,
    )


def _collision_event() -> dict[str, Any]:
    """SUMO native collision record with participants, speeds and lane."""
    return _schema(
        {
            "collider": _str(),
            "victim": _str(),
            "collider_type": _str(),
            "victim_type": _str(),
            "collider_speed": _num(),
            "victim_speed": _num(),
            "type": _str(),
            "lane": _str(),
            "position": _num(),
            "sim_ns": _int(),
            "available_sim_ns": _int(),
            "available_ns": _int(),
        },
        (
            "collider",
            "victim",
            "collider_type",
            "victim_type",
            "collider_speed",
            "victim_speed",
            "type",
            "lane",
            "position",
            "sim_ns",
            "available_ns",
        ),
        extra=False,
    )


def _contact_event() -> dict[str, Any]:
    """Gazebo contact record: raw collision names and source topic/time."""
    return _schema(
        {
            "sim_ns": _int(),
            "topic": _str(),
            "collision1": _str(),
            "collision2": _str(),
            "available_sim_ns": _int(),
            "available_ns": _int(),
        },
        ("sim_ns", "topic", "collision1", "collision2", "available_ns"),
        extra=False,
    )


def _delivery_event() -> dict[str, Any]:
    """ns-3 UDP delivery with observed RSSI/SNR when the monitor saw it."""
    return _schema(
        {
            "packet_id": _str(),
            "src": _str(),
            "dst": _str(),
            "sent_ns": _int(),
            "received_ns": _int(),
            "available_sim_ns": _int(),
            "size": {"type": "integer", "minimum": 1},
            "payload_ref": _str(),
            "rssi_dbm": _num(),
            "snr_db": _num(),
            "available_ns": _int(),
        },
        (
            "packet_id",
            "src",
            "dst",
            "sent_ns",
            "received_ns",
            "size",
            "payload_ref",
            "available_ns",
        ),
        extra=False,
    )


def _drop_event() -> dict[str, Any]:
    """ns-3 application-lifetime expiry or native UDP send failure."""
    return _schema(
        {
            "packet_id": _str(),
            "src": _str(),
            "dst": _str(),
            "sim_ns": _int(),
            "available_sim_ns": _int(),
            "reason": _enum(("delivery_timeout", "udp_send_failed")),
            "available_ns": _int(),
        },
        ("packet_id", "src", "dst", "sim_ns", "reason", "available_ns"),
        extra=False,
    )


def _link_event() -> dict[str, Any]:
    """ns-3 cumulative bidirectional link record at the returned boundary."""
    return _schema(
        {
            "src": _str(),
            "dst": _str(),
            "sim_ns": _int(),
            "available_sim_ns": _int(),
            "distance_m": _num(),
            "path_loss_db": _num(),
            "predicted_rssi_dbm": _num(),
            "sent": _int(),
            "delivered": _int(),
            "dropped": _int(),
            "available_ns": _int(),
        },
        (
            "src",
            "dst",
            "sim_ns",
            "distance_m",
            "path_loss_db",
            "predicted_rssi_dbm",
            "sent",
            "delivered",
            "dropped",
            "available_ns",
        ),
        extra=False,
    )


# ---------------------------------------------------------------------------
# Command payload schemas (flattened backend parameters).
# ---------------------------------------------------------------------------


def _px4_payload(
    action: str,
    extra_members: dict[str, Any] | None = None,
    extra_required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """PX4 command payload: portable entity plus native action parameters.

    ``entity`` names the portable agent entity; the host adapter translates it
    to the native vehicle ID; the command schema ID selects the action.
    """
    members: dict[str, Any] = {"entity": _str()}
    if extra_members is not None:
        members.update(extra_members)
    return _schema(members, ("entity", *extra_required), extra=False)


def _sumo_payload(
    action: str,
    extra_members: dict[str, Any] | None = None,
    extra_required: tuple[str, ...] = (),
    *,
    target: str = "vehicle",
) -> dict[str, Any]:
    """SUMO command payload: exact backend parameters, flattened.

    The target keeps its backend name: ``vehicle``, ``lane`` or ``tls``.
    """
    members: dict[str, Any] = {
        target: _str(),
    }
    if extra_members is not None:
        members.update(extra_members)
    return _schema(members, (target, *extra_required), extra=False)


def _ns3_payload() -> dict[str, Any]:
    """ns-3 send command payload: exact native parameters, flattened."""
    return _schema(
        {
            "packet_id": _str(),
            "src": _str(),
            "dst": _str(),
            "size": {"type": "integer", "minimum": 1, "maximum": 61440},
            "payload_ref": _str(),
            "lifetime_ns": {
                "type": "integer",
                "minimum": 1,
                "maximum": 60_000_000_000,
            },
        },
        ("packet_id", "src", "dst", "size", "payload_ref"),
        extra=False,
    )


def _descriptor(
    ident: str,
    backend: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> MessageDescriptor:
    return MessageDescriptor(
        f"adapters.{backend}.{ident}", "command", payload, result_schema=result
    )


def _command_descriptors(backend: str) -> tuple[MessageDescriptor, ...]:
    """Backend command descriptors, ordered by descriptor ID."""
    if backend == PX4_GAZEBO:
        px4_result = _result_schema(PX4_GAZEBO)
        return (
            _descriptor("arm", PX4_GAZEBO, _px4_payload("arm"), px4_result),
            _descriptor("disarm", PX4_GAZEBO, _px4_payload("disarm"), px4_result),
            _descriptor(
                "goto",
                PX4_GAZEBO,
                _px4_payload(
                    "goto",
                    {
                        "position_enu": _vector(3, -_BOUND, _BOUND),
                        "yaw_deg": _num(),
                    },
                    ("position_enu", "yaw_deg"),
                ),
                px4_result,
            ),
            _descriptor("hold", PX4_GAZEBO, _px4_payload("hold"), px4_result),
            _descriptor("land", PX4_GAZEBO, _px4_payload("land"), px4_result),
            _descriptor(
                "takeoff",
                PX4_GAZEBO,
                _px4_payload("takeoff", {"altitude_m": _num()}, ("altitude_m",)),
                px4_result,
            ),
        )
    if backend == SUMO:
        sumo_result = _result_schema(SUMO)
        return (
            _descriptor(
                "add_vehicle",
                SUMO,
                _sumo_payload(
                    "add_vehicle",
                    {
                        "route_id": _str(),
                        "type": _str(),
                        "depart": _num(),
                    },
                    ("route_id",),
                ),
                sumo_result,
            ),
            _descriptor(
                "change_target",
                SUMO,
                _sumo_payload("change_target", {"edge": _str()}, ("edge",)),
                sumo_result,
            ),
            _descriptor(
                "lane_restriction",
                SUMO,
                _sumo_payload(
                    "lane_restriction",
                    {
                        "disallowed": _UNIQUE_CLASS_ARRAY,
                        "at_sim_ns": _int(),
                    },
                    ("disallowed",),
                    target="lane",
                ),
                sumo_result,
            ),
            _descriptor(
                "remove_vehicle", SUMO, _sumo_payload("remove_vehicle"), sumo_result
            ),
            _descriptor(
                "reroute",
                SUMO,
                _sumo_payload("reroute", {"edges": _STR_ARRAY}),
                sumo_result,
            ),
            _descriptor(
                "set_speed",
                SUMO,
                _sumo_payload(
                    "set_speed",
                    {"speed": {"type": "number", "minimum": 0.0}},
                    ("speed",),
                ),
                sumo_result,
            ),
            _descriptor(
                "tls_phase",
                SUMO,
                _sumo_payload(
                    "tls_phase",
                    {"phase": _int(), "duration_s": {"type": "number"}},
                    ("phase",),
                    target="tls",
                ),
                sumo_result,
            ),
        )
    if backend == NS3:
        return (_descriptor("send", NS3, _ns3_payload(), _result_schema(NS3)),)
    raise ValueError(_UNKNOWN_BACKEND)


def _event_descriptors(backend: str) -> tuple[MessageDescriptor, ...]:
    """Backend event descriptors, ordered by descriptor ID."""
    if backend == PX4_GAZEBO:
        return (
            MessageDescriptor(
                f"adapters.{PX4_GAZEBO}.contact", "event", _contact_event()
            ),
        )
    if backend == SUMO:
        return (
            MessageDescriptor(f"adapters.{SUMO}.arrived", "event", _entity_event()),
            MessageDescriptor(
                f"adapters.{SUMO}.collisions", "event", _collision_event()
            ),
            MessageDescriptor(f"adapters.{SUMO}.departed", "event", _entity_event()),
            MessageDescriptor(f"adapters.{SUMO}.removed", "event", _entity_event()),
            MessageDescriptor(
                f"adapters.{SUMO}.teleported_end", "event", _entity_event()
            ),
            MessageDescriptor(
                f"adapters.{SUMO}.teleported_start", "event", _entity_event()
            ),
        )
    if backend == NS3:
        return (
            MessageDescriptor(f"adapters.{NS3}.delivery", "event", _delivery_event()),
            MessageDescriptor(f"adapters.{NS3}.drop", "event", _drop_event()),
            MessageDescriptor(f"adapters.{NS3}.link", "event", _link_event()),
        )
    raise ValueError(_UNKNOWN_BACKEND)


def command_descriptors(backend: str) -> tuple[MessageDescriptor, ...]:
    """Portable command descriptors for one backend; unknown backends reject."""
    _check_backend(backend)
    return _command_descriptors(backend)


def event_descriptors(backend: str) -> tuple[MessageDescriptor, ...]:
    """Portable event descriptors for one backend; unknown backends reject."""
    _check_backend(backend)
    return _event_descriptors(backend)


def backend_registry(backend: str) -> MemoryRegistry:
    """Compile both descriptor families for one backend into a registry."""
    _check_backend(backend)
    return MemoryRegistry(
        (), messages=_command_descriptors(backend) + _event_descriptors(backend)
    )
