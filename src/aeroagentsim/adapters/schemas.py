"""Typed flattened commands and native event/result records for the native adapters."""

from __future__ import annotations

from typing import Any, cast

from aerokernel.registry import MemoryRegistry, MessageDescriptor
from aerokernel.values import thaw

__all__ = [
    "BACKENDS",
    "backend_registry",
    "command_descriptors",
    "event_descriptors",
    "vehicle_estimate_schema",
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
# Closed native-primitives v1 contracts. Legacy descriptor meanings stay intact.
# ---------------------------------------------------------------------------


def _all(members: dict[str, Any]) -> dict[str, Any]:
    return _schema(members, tuple(members))


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    result = {**schema, "nullable": True}
    if "enum" in schema:
        result["enum"] = [*schema["enum"], None]
    return result


def vehicle_estimate_schema() -> dict[str, Any]:
    """Source-clock evidence, not an invented boundary-time PX4 acquisition."""
    clock_map = _all({
        "source_anchor_ns": _int(), "native_anchor_ns": _int(),
        "method": _str(), "uncertainty_ns": _nullable(_int()),
        "source_resolution_ns": {"type": "integer", "minimum": 1},
        "mapping_assumption": _str(), "clock_epoch": _str(), "source_clock": _str(),
        "residual_ns": _nullable({"type": "integer", "minimum": -1_000_000, "maximum": 1_000_000}),
    })
    raw_fields = _schema({
        "time_boot_ms": _int(),
        **{key: _num() for key in ("x", "y", "z", "vx", "vy", "vz", "q1", "q2", "q3", "q4")},
    }, ("time_boot_ms",))
    source_record = _all({
        "message_name": _enum(("LOCAL_POSITION_NED", "ATTITUDE_QUATERNION")),
        "fields": raw_fields, "px4_instance": _int(),
        "system_id": {"type": "integer", "minimum": 1, "maximum": 255},
        "component_id": {"type": "integer", "enum": [1]},
        "received_wall_time": _int(), "available_native_ns": _int(),
        "clock_epoch": _str(), "run_id": _str(), "source_ns": _int(),
    })
    return _schema({
        "status": _enum(("available", "unavailable")),
        "source": _enum(("PX4_MavlinkDirect",)),
        "run_id": _str(), "uav_id": _str(), "clock_epoch": _str(),
        "source_sim_ns": _nullable(_int()), "available_sim_ns": _int(),
        "reason": _str(), "position_enu_m": _vector(3, -_BOUND, _BOUND),
        "velocity_enu_m_s": _vector(3, -_BOUND, _BOUND),
        "attitude_body_frd_to_world_enu_xyzw": _vector(4, -1.0, 1.0),
        "attitude_source_sim_ns": _int(), "source_clock": _str(),
        "clock_map": clock_map, "origin_version": _str(),
        "local_ned_origin_world_enu_m": _vector(3, -_BOUND, _BOUND),
        "height_datum": _enum(("amsl",)),
        "origin_source": _enum(("GPS_GLOBAL_ORIGIN",)),
        "source_messages": {"type": "array", "items": source_record, "min_length": 2, "max_length": 2},
        "horizontal_sigma_m": _nullable({"type": "number", "minimum": 0}),
    }, ("status", "source", "run_id", "uav_id", "clock_epoch", "source_sim_ns", "available_sim_ns"))


def payload_state_schema() -> dict[str, Any]:
    return _schema({
        "vehicle": _str(), "package_id": _str(), "clock_epoch": _str(),
        "status": _enum(("attached", "detached", "unavailable")),
        "released": _bool(), "source": _enum(("gazebo_detachable_joint",)),
        "source_clock": _nullable(_enum(("episode_sim",))),
        "source_time_ns": _nullable(_int()), "observed_sim_ns": _int(),
        "command_id": _nullable(_str()), "reason": _str(),
        "release_interval_ns": {"type": "vector", "length": 2, "items": _int()},
    }, ("vehicle", "package_id", "clock_epoch", "status", "released", "source", "source_clock", "source_time_ns", "observed_sim_ns", "command_id"))


def receiver_receipt_schema() -> dict[str, Any]:
    return _all({
        "command_id": _str(), "vehicle": _str(), "package_id": _str(),
        "receiver_id": _str(), "receipt_id": _str(),
        "source": _enum(("gazebo_contact_sensor",)), "source_topic": _str(),
        "clock_epoch": _str(), "source_clock": _enum(("gazebo_sim",)),
        "episode_origin_ns": _int(), "contact_start_ns": _int(),
        "contact_end_ns": _int(), "contact_start_sim_ns": _int(),
        "contact_end_sim_ns": _int(), "contact_duration_ns": _int(),
    })


def payload_ack_schema() -> dict[str, Any]:
    return _schema({
        "status": _enum(("accepted", "rejected", "unavailable", "failed")),
        "command_id": _str(), "vehicle": _str(), "package_id": _str(),
        "sim_ns": _int(), "reason": _str(),
    }, ("status", "command_id", "vehicle", "package_id", "sim_ns"))


def _capture_observation() -> dict[str, Any]:
    calibration = _all({
        "K": _vector(9, -_BOUND, _BOUND),
        "width": {"type": "integer", "minimum": 1, "maximum": 4096},
        "height": {"type": "integer", "minimum": 1, "maximum": 4096},
        "distortion": {"type": "array", "items": _num(), "max_length": 16},
        "distortion_model": _enum(("plumb_bob", "rational_polynomial", "equidistant")),
        "camera_info_source_ns": _int(), "native_frame_id": _str(), "source_topic": _str(),
        "horizontal_fov_deg": {"type": "number", "minimum": 0, "maximum": 180},
        "vertical_fov_deg": {"type": "number", "minimum": 0, "maximum": 180},
        "fov_source": _str(), "registered_rgb_depth": {"type": "boolean", "enum": [True]},
        "version": _str(), "depth_convention": _enum(("optical_z",)),
        "depth_min_m": {"type": "number", "minimum": 0},
        "depth_max_m": {"type": "number", "minimum": 0},
        "invalid_depth_rule": _str(),
        "T_body_frd_from_camera_optical": {"type": "matrix", "rows": 4, "columns": 4, "items": _num()},
        "extrinsic_source": _str(),
    })
    return _all({
        "run_id": _str(), "uav_id": _str(), "sensor_id": _str(), "frame_id": _str(),
        "source": _enum(("gazebo_transport",)), "source_clock": _enum(("gazebo_sim",)),
        "source_topic": _str(), "native_frame_id": _str(),
        "source_time": _int(), "clock_epoch": _str(), "observed_at": _int(),
        "depth_observed_at": _int(), "available_sim_ns": _int(),
        "received_wall_time": _int(), "coordinate_frame": _enum(("camera_optical",)),
        "calibration": calibration, "vehicle_estimate": vehicle_estimate_schema(),
        "localization_status": _enum(("available", "unavailable")),
        "localization_reason": _nullable(_str()), "rgb_sha256": {"type": "string", "min_length": 64, "max_length": 64},
        "sensing_model": _enum(("gazebo_geometric_rgbd",)),
        "physical_ranging": {"type": "boolean", "enum": [False]},
    })


def capture_response_schema(*, read: bool) -> dict[str, Any]:
    members: dict[str, Any] = {
        "status": _enum(("succeeded", "pending", "unavailable")), "reason": _str(),
    }
    if read:
        members.update({
            "vehicle": _str(), "frame_id": _str(), "kind": _enum(("rgb", "depth")),
            "offset": _int(), "total_bytes": {"type": "integer", "minimum": 1, "maximum": 64 * 1024 * 1024},
            "data_base64": {"type": "string", "max_length": 349528}, "eof": _bool(),
        })
    else:
        blob = _all({
            "bytes": {"type": "integer", "minimum": 1, "maximum": 64 * 1024 * 1024},
            "sha256": {"type": "string", "min_length": 64, "max_length": 64},
            "encoding": _enum(("png", "zlib-float32-le-metres")),
        })
        members.update({"observation": _capture_observation(), "blobs": _all({"rgb": blob, "depth": blob})})
    return _schema(members, ("status",))


def _recorded(schema: dict[str, Any]) -> dict[str, Any]:
    return _schema({**schema["members"], "available_ns": _int(), "kernel_command_id": _str()},
                   (*schema["required"], "available_ns", "kernel_command_id"))


def _native_descriptors() -> tuple[MessageDescriptor, ...]:
    release_result = _schema({
        "status": _enum(("succeeded", "rejected")), "reason": _str(),
        "ack": payload_ack_schema(), "receipt": receiver_receipt_schema(),
        "available_ns": _int(),
    }, ("status",))
    state = payload_state_schema()
    status_result = _schema({"status": _enum(("succeeded", "rejected")), "reason": _str(), "observation": state, "available_ns": _int()}, ("status",))
    commands = (
        _descriptor("payload_release_v1", PX4_GAZEBO, _px4_payload("release", {"package_id": _str()}, ("package_id",)), release_result),
        _descriptor("payload_status_v1", PX4_GAZEBO, _px4_payload("status", {"package_id": _str()}, ("package_id",)), status_result),
        _descriptor("capture_poll_v1", PX4_GAZEBO, _px4_payload("poll", {"after_sim_ns": {"type": "integer", "minimum": -1}}, ("after_sim_ns",)), _recorded(capture_response_schema(read=False))),
        _descriptor("capture_read_v1", PX4_GAZEBO, _px4_payload("read", {"frame_id": _str(), "kind": _enum(("rgb", "depth")), "offset": _int(), "max_bytes": {"type": "integer", "minimum": 1, "maximum": 262144}}, ("frame_id", "kind", "offset", "max_bytes")), _recorded(capture_response_schema(read=True))),
    )
    return commands


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
    *,
    cancel_support: bool = False,
) -> MessageDescriptor:
    return MessageDescriptor(
        f"adapters.{backend}.{ident}", "command", payload, result_schema=result,
        cancel_support=cancel_support,
    )


def _command_descriptors(backend: str) -> tuple[MessageDescriptor, ...]:
    """Backend command descriptors, ordered by descriptor ID."""
    if backend == PX4_GAZEBO:
        px4_result = _result_schema(PX4_GAZEBO)
        legacy = (
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
        cancel = tuple(
            _descriptor(
                f"{descriptor.id.rsplit('.', 1)[1]}_cancel_v1", PX4_GAZEBO,
                cast(dict[str, Any], thaw(descriptor.schema)), px4_result, cancel_support=True,
            )
            for descriptor in legacy
        )
        return tuple(sorted(legacy + cancel + _native_descriptors(), key=lambda d: d.id))
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
        events = (
            MessageDescriptor(
                f"adapters.{PX4_GAZEBO}.contact", "event", _contact_event()
            ),
            MessageDescriptor(f"adapters.{PX4_GAZEBO}.payload_release_ack_v1", "event", _recorded(payload_ack_schema())),
            MessageDescriptor(f"adapters.{PX4_GAZEBO}.payload_state_v1", "event", _schema({**payload_state_schema()["members"], "available_ns": _int()}, (*payload_state_schema()["required"], "available_ns"))),
            MessageDescriptor(f"adapters.{PX4_GAZEBO}.receiver_receipt_v1", "event", _schema({**receiver_receipt_schema()["members"], "available_ns": _int()}, (*receiver_receipt_schema()["required"], "available_ns"))),
            MessageDescriptor(f"adapters.{PX4_GAZEBO}.capture_frame_v1", "event", _recorded(capture_response_schema(read=False))),
            MessageDescriptor(f"adapters.{PX4_GAZEBO}.capture_chunk_v1", "event", _recorded(capture_response_schema(read=True))),
        )
        return tuple(sorted(events, key=lambda descriptor: descriptor.id))
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
