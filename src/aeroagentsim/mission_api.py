"""Domain-neutral, versioned mission commands and portable result contracts.

The frozen dataclasses are value containers. Builders validate even directly
constructed instances; validators accept wire records or kernel-frozen records
and return detached, typed values. Invalid inputs raise ``ValueError``. These
helpers perform no I/O and impose no vehicle taxonomy or coordinate frame.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, TypeAlias, cast

from aerokernel.ids import EntityRef

MISSION_API_VERSION = 1

RESERVE_SCHEMA = "aas.mission.reserve.v1"
DIVERT_SCHEMA = "aas.mission.divert.v1"
RESUME_SCHEMA = "aas.mission.resume.v1"
RELEASE_SCHEMA = "aas.mission.release.v1"
STATUS_SCHEMA = "aas.mission.status.v1"
MISSION_STATUS_TOPIC = "aas.mission.status"
MISSION_COMMAND_SCHEMAS = (
    RESERVE_SCHEMA,
    DIVERT_SCHEMA,
    RESUME_SCHEMA,
    RELEASE_SCHEMA,
)


class ResultCode(str, Enum):
    """Stable mission outcome codes, shared by results, feedback and status."""

    OK = "ok"
    INVALID_PAYLOAD = "invalid_payload"
    UNKNOWN_VEHICLE = "unknown_vehicle"
    STALE_ENTITY = "stale_entity"
    STALE_REVISION = "stale_revision"
    ALREADY_RESERVED = "already_reserved"
    NOT_RESERVED = "not_reserved"
    UNKNOWN_TOKEN = "unknown_token"
    STALE_TOKEN = "stale_token"
    EXPIRED = "expired"
    DUPLICATE_REQUEST = "duplicate_request"
    REQUEST_CONFLICT = "request_conflict"
    BUSY = "busy"
    PROTECTED = "protected"
    SAFETY_PAUSED = "safety_paused"
    INSUFFICIENT_ENERGY = "insufficient_energy"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    UNSUPPORTED_FRAME = "unsupported_frame"
    ROUTE_UNAVAILABLE = "route_unavailable"
    MOTION_FAILED = "motion_failed"
    ARRIVAL_UNCONFIRMED = "arrival_unconfirmed"
    CANCELED = "canceled"
    REVOKED = "revoked"


@dataclass(frozen=True)
class ReservePayload:
    vehicle: EntityRef
    mission_id: str
    request_id: str
    deadline_ns: int
    expires_ns: int
    expected_owner_revision: int | None = None


@dataclass(frozen=True)
class DivertPayload:
    vehicle: EntityRef
    mission_id: str
    request_id: str
    deadline_ns: int
    reservation_token: str
    expected_owner_revision: int
    target: tuple[float, float, float]
    frame: str


@dataclass(frozen=True)
class ResumePayload:
    vehicle: EntityRef
    mission_id: str
    request_id: str
    deadline_ns: int
    reservation_token: str
    expected_owner_revision: int


@dataclass(frozen=True)
class ReleasePayload:
    vehicle: EntityRef
    mission_id: str
    request_id: str
    deadline_ns: int
    reservation_token: str
    expected_owner_revision: int
    disposition: Literal["resume_saved", "idle"]


MissionPayload: TypeAlias = (
    ReservePayload | DivertPayload | ResumePayload | ReleasePayload
)


@dataclass(frozen=True)
class MissionResult:
    vehicle: EntityRef
    mission_id: str
    request_id: str
    command_id: str
    code: ResultCode
    owner_revision: int
    reservation_token: str | None = None
    downstream_command_ids: tuple[str, ...] = ()
    downstream_receipt_refs: tuple[tuple[int, int], ...] = ()
    detail: str = ""


_COMMON_KEYS = ("vehicle", "mission_id", "request_id", "deadline_ns")
_OWNER_KEYS = ("reservation_token", "expected_owner_revision")
_RESULT_KEYS = (
    "vehicle",
    "mission_id",
    "request_id",
    "command_id",
    "code",
    "owner_revision",
)
_RESULT_OPTIONAL_KEYS = (
    "reservation_token",
    "downstream_command_ids",
    "downstream_receipt_refs",
    "detail",
)
_PAYLOAD_SCHEMAS = {
    ReservePayload: RESERVE_SCHEMA,
    DivertPayload: DIVERT_SCHEMA,
    ResumePayload: RESUME_SCHEMA,
    ReleasePayload: RELEASE_SCHEMA,
}


def _record(
    value: object,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    *,
    name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(f"{name}: expected a record with string keys")
    missing = set(required) - set(value)
    extra = set(value) - set(required) - set(optional)
    if missing or extra:
        raise ValueError(
            f"{name}: missing keys {sorted(missing)!r}; extra keys {sorted(extra)!r}"
        )
    return cast(Mapping[str, Any], value)


def _text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        expected = "a string" if allow_empty else "nonempty text"
        raise ValueError(f"{name}: expected {expected}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name}: expected valid UTF-8 text") from exc
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name}: expected an integer >= {minimum}")
    return value


def _sequence(value: object, name: str) -> list[Any] | tuple[Any, ...]:
    if type(value) not in (list, tuple):
        raise ValueError(f"{name}: expected an array")
    return cast("list[Any] | tuple[Any, ...]", value)


def _vector(value: object) -> tuple[float, float, float]:
    coordinates = _sequence(value, "target")
    if len(coordinates) != 3:
        raise ValueError("target: expected exactly three finite coordinates")
    numbers = []
    for coordinate in coordinates:
        if type(coordinate) not in (int, float):
            raise ValueError("target: expected finite numeric coordinates, not bool")
        try:
            number = float(coordinate)
        except (OverflowError, ValueError) as exc:
            raise ValueError("target: coordinate exceeds finite binary64 range") from exc
        if not math.isfinite(number):
            raise ValueError("target: expected finite coordinates")
        numbers.append(0.0 if number == 0 else number)
    return numbers[0], numbers[1], numbers[2]


def _vehicle(value: object) -> EntityRef:
    tagged = _record(value, ("$ref",), name="vehicle")
    identity = _record(
        tagged["$ref"],
        ("run_id", "epoch", "id", "generation", "type_id"),
        name="vehicle.$ref",
    )
    return EntityRef.from_data(dict(identity))


def _vehicle_data(vehicle: EntityRef) -> dict[str, Any]:
    if not isinstance(vehicle, EntityRef):
        raise ValueError("vehicle: expected an EntityRef")
    # Revalidate identity fields as well as retaining the complete generation scope.
    return {"$ref": _vehicle({"$ref": vehicle.to_data()}).to_data()}


def validate_payload(schema_id: str, payload: Mapping[str, Any]) -> MissionPayload:
    """Decode an exact command record, including deadlines and revision checks.

    Deadlines are positive nanoseconds, expiry cannot precede the reserve
    deadline, and revisions are nonnegative integers. No current-time, ownership
    or supported-frame policy is evaluated here.
    """
    if type(schema_id) is not str or schema_id not in MISSION_COMMAND_SCHEMAS:
        raise ValueError(f"unknown mission command schema: {schema_id!r}")
    required: tuple[str, ...] = _COMMON_KEYS
    optional: tuple[str, ...] = ()
    if schema_id == RESERVE_SCHEMA:
        required += ("expires_ns",)
        optional = ("expected_owner_revision",)
    else:
        required += _OWNER_KEYS
        if schema_id == DIVERT_SCHEMA:
            required += ("target", "frame")
        elif schema_id == RELEASE_SCHEMA:
            required += ("disposition",)
    data = _record(payload, required, optional, name="mission payload")
    common: dict[str, Any] = {
        "vehicle": _vehicle(data["vehicle"]),
        "mission_id": _text(data["mission_id"], "mission_id"),
        "request_id": _text(data["request_id"], "request_id"),
        "deadline_ns": _integer(data["deadline_ns"], "deadline_ns", minimum=1),
    }
    if schema_id == RESERVE_SCHEMA:
        expires_ns = _integer(data["expires_ns"], "expires_ns", minimum=1)
        if expires_ns < common["deadline_ns"]:
            raise ValueError("expires_ns: cannot precede deadline_ns")
        revision = data.get("expected_owner_revision")
        if revision is not None:
            revision = _integer(revision, "expected_owner_revision")
        return ReservePayload(
            **common, expires_ns=expires_ns, expected_owner_revision=revision
        )
    owner: dict[str, Any] = {
        "reservation_token": _text(data["reservation_token"], "reservation_token"),
        "expected_owner_revision": _integer(
            data["expected_owner_revision"], "expected_owner_revision"
        ),
    }
    if schema_id == DIVERT_SCHEMA:
        return DivertPayload(
            **common,
            **owner,
            target=_vector(data["target"]),
            frame=_text(data["frame"], "frame"),
        )
    if schema_id == RESUME_SCHEMA:
        return ResumePayload(**common, **owner)
    disposition = _text(data["disposition"], "disposition")
    if disposition not in ("resume_saved", "idle"):
        raise ValueError("disposition: expected 'resume_saved' or 'idle'")
    return ReleasePayload(
        **common,
        **owner,
        disposition=cast("Literal['resume_saved', 'idle']", disposition),
    )


def _payload_data(payload: MissionPayload) -> dict[str, Any]:
    data: dict[str, Any] = {
        "vehicle": _vehicle_data(payload.vehicle),
        "mission_id": payload.mission_id,
        "request_id": payload.request_id,
        "deadline_ns": payload.deadline_ns,
    }
    if isinstance(payload, ReservePayload):
        data.update(
            expires_ns=payload.expires_ns,
            expected_owner_revision=payload.expected_owner_revision,
        )
    else:
        data.update(
            reservation_token=payload.reservation_token,
            expected_owner_revision=payload.expected_owner_revision,
        )
        if isinstance(payload, DivertPayload):
            data.update(
                target=list(_sequence(payload.target, "target")), frame=payload.frame
            )
        elif isinstance(payload, ReleasePayload):
            data["disposition"] = payload.disposition
    return data


def build_payload(payload: MissionPayload) -> dict[str, Any]:
    """Validate a payload dataclass and return a fresh portable wire record."""
    schema_id = _PAYLOAD_SCHEMAS.get(type(payload))
    if schema_id is None:
        raise ValueError("payload: expected a mission payload dataclass")
    validated = validate_payload(schema_id, _payload_data(payload))
    return _payload_data(validated)


def _receipt_ref(value: object) -> tuple[int, int]:
    ref = _record(
        value, ("record_index", "item_index"), name="downstream_receipt_refs[]"
    )
    return (
        _integer(ref["record_index"], "record_index"),
        _integer(ref["item_index"], "item_index"),
    )


def validate_result(payload: Mapping[str, Any]) -> MissionResult:
    """Decode a mission result, feedback or status record without side effects."""
    data = _record(
        payload, _RESULT_KEYS, _RESULT_OPTIONAL_KEYS, name="mission result"
    )
    code = _text(data["code"], "code")
    try:
        result_code = ResultCode(code)
    except ValueError as exc:
        raise ValueError(f"code: unknown mission result code {code!r}") from exc
    token = data.get("reservation_token")
    if token is not None:
        token = _text(token, "reservation_token")
    command_ids = tuple(
        _text(command_id, "downstream_command_ids[]")
        for command_id in _sequence(
            data.get("downstream_command_ids", ()), "downstream_command_ids"
        )
    )
    receipt_refs = tuple(
        _receipt_ref(ref)
        for ref in _sequence(
            data.get("downstream_receipt_refs", ()), "downstream_receipt_refs"
        )
    )
    return MissionResult(
        vehicle=_vehicle(data["vehicle"]),
        mission_id=_text(data["mission_id"], "mission_id"),
        request_id=_text(data["request_id"], "request_id"),
        command_id=_text(data["command_id"], "command_id"),
        code=result_code,
        owner_revision=_integer(data["owner_revision"], "owner_revision"),
        reservation_token=token,
        downstream_command_ids=command_ids,
        downstream_receipt_refs=receipt_refs,
        detail=_text(data.get("detail", ""), "detail", allow_empty=True),
    )


def _result_data(result: MissionResult) -> dict[str, Any]:
    if not isinstance(result.code, ResultCode):
        raise ValueError("code: expected a ResultCode")
    receipt_refs = []
    for ref in _sequence(result.downstream_receipt_refs, "downstream_receipt_refs"):
        pair = _sequence(ref, "downstream_receipt_refs[]")
        if len(pair) != 2:
            raise ValueError("downstream_receipt_refs[]: expected two indices")
        receipt_refs.append({"record_index": pair[0], "item_index": pair[1]})
    return {
        "vehicle": _vehicle_data(result.vehicle),
        "mission_id": result.mission_id,
        "request_id": result.request_id,
        "command_id": result.command_id,
        "code": result.code.value,
        "owner_revision": result.owner_revision,
        "reservation_token": result.reservation_token,
        "downstream_command_ids": list(
            _sequence(result.downstream_command_ids, "downstream_command_ids")
        ),
        "downstream_receipt_refs": receipt_refs,
        "detail": result.detail,
    }


def build_result(result: MissionResult) -> dict[str, Any]:
    """Validate a result dataclass and serialize receipt coordinates as records."""
    if type(result) is not MissionResult:
        raise ValueError("result: expected a MissionResult dataclass")
    return _result_data(validate_result(_result_data(result)))


def _string_schema(*, allow_empty: bool = False, nullable: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "min_length": 0 if allow_empty else 1,
    }
    if nullable:
        schema["nullable"] = True
    return schema


def _integer_schema(*, minimum: int = 0, nullable: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer", "minimum": minimum}
    if nullable:
        schema["nullable"] = True
    return schema


def _record_schema(
    members: dict[str, Any], optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "type": "record",
        "members": members,
        "required": [key for key in members if key not in optional],
        "extra": False,
    }


def _payload_schema(schema_id: str) -> dict[str, Any]:
    members: dict[str, Any] = {
        # An unrestricted ref needs no registered root type or domain import.
        "vehicle": {"type": "ref"},
        "mission_id": _string_schema(),
        "request_id": _string_schema(),
        "deadline_ns": _integer_schema(minimum=1),
    }
    if schema_id == RESERVE_SCHEMA:
        members.update(
            expires_ns=_integer_schema(minimum=1),
            expected_owner_revision=_integer_schema(nullable=True),
        )
        return _record_schema(members, ("expected_owner_revision",))
    members.update(
        reservation_token=_string_schema(),
        expected_owner_revision=_integer_schema(),
    )
    if schema_id == DIVERT_SCHEMA:
        members.update(
            target={"type": "vector", "length": 3, "items": {"type": "number"}},
            frame=_string_schema(),
        )
    elif schema_id == RELEASE_SCHEMA:
        members["disposition"] = {
            "type": "string",
            "enum": ["resume_saved", "idle"],
        }
    return _record_schema(members)


def _result_schema() -> dict[str, Any]:
    return _record_schema(
        {
            "vehicle": {"type": "ref"},
            "mission_id": _string_schema(),
            "request_id": _string_schema(),
            "command_id": _string_schema(),
            "code": {"type": "string", "enum": [code.value for code in ResultCode]},
            "owner_revision": _integer_schema(),
            "reservation_token": _string_schema(nullable=True),
            "downstream_command_ids": {
                "type": "array",
                "items": _string_schema(),
                "min_length": 0,
            },
            "downstream_receipt_refs": {
                "type": "array",
                "items": _record_schema(
                    {
                        "record_index": _integer_schema(),
                        "item_index": _integer_schema(),
                    }
                ),
                "min_length": 0,
            },
            "detail": _string_schema(allow_empty=True),
        },
        _RESULT_OPTIONAL_KEYS,
    )


def mission_descriptors() -> tuple[dict[str, Any], ...]:
    """Return fresh portable descriptors: four commands and a status event.

    Result, feedback and status records share one shape. Accepted/executing
    receipts may omit their result entirely, as permitted by the kernel action
    lifecycle; an actual result or feedback record must satisfy this contract.
    The portable schema cannot express expiry/deadline cross-field ordering,
    which is additionally enforced by ``validate_payload``.
    """
    commands = tuple(
        {
            "id": schema_id,
            "kind": "command",
            "schema": _payload_schema(schema_id),
            "result_schema": _result_schema(),
            "feedback_schema": _result_schema(),
            "cancel_support": False,
        }
        for schema_id in MISSION_COMMAND_SCHEMAS
    )
    return (
        *commands,
        {"id": STATUS_SCHEMA, "kind": "event", "schema": _result_schema()},
    )


__all__ = [
    "MISSION_API_VERSION",
    "RESERVE_SCHEMA",
    "DIVERT_SCHEMA",
    "RESUME_SCHEMA",
    "RELEASE_SCHEMA",
    "STATUS_SCHEMA",
    "MISSION_STATUS_TOPIC",
    "MISSION_COMMAND_SCHEMAS",
    "ReservePayload",
    "DivertPayload",
    "ResumePayload",
    "ReleasePayload",
    "MissionPayload",
    "ResultCode",
    "MissionResult",
    "build_payload",
    "validate_payload",
    "build_result",
    "validate_result",
    "mission_descriptors",
]
