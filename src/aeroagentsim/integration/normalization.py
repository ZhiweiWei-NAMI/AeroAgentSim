"""Strict decoding of our neutral observation format, with no source I/O."""

import hashlib
import json
import math
from typing import Any, Mapping, Optional

from .contracts import (
    FRAME_SCHEMA,
    ContractError,
    EntityObservation,
    FrameKey,
    ObservationFrame,
    RunIdentity,
    SphereBody,
    Vec3,
    freeze_json,
    ns,
    thaw_json,
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(thaw_json(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON object key: " + key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ContractError("nonfinite JSON number: " + value)


def decode_json(raw: bytes) -> Mapping[str, Any]:
    if not isinstance(raw, bytes):
        raise ContractError("artifact reader must return bytes")
    try:
        data = json.loads(raw, object_pairs_hook=_object, parse_constant=_nonfinite)
    except (ValueError, UnicodeError) as exc:
        raise ContractError("invalid JSON artifact") from exc
    if not isinstance(data, dict):
        raise ContractError("artifact must contain a JSON object")
    return data


def require_keys(data: Any, keys, name: str) -> None:
    if not isinstance(data, dict) or set(data) != set(keys):
        raise ContractError(name + " has missing or unsupported fields")


def token(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(name + " must be a nonempty string")
    return value


def run_identity(data: Any) -> RunIdentity:
    require_keys(data, ("attachment_id", "run_id", "run_epoch", "manifest_revision"), "run identity")
    return RunIdentity(**{key: token(value, key) for key, value in data.items()})


def vector(data: Any) -> Optional[Vec3]:
    if data is None:
        return None
    if (
        not isinstance(data, list)
        or len(data) != 3
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in data)
    ):
        raise ContractError("ENU vectors must contain three finite numbers or be null")
    return tuple(float(value) for value in data)


def _entity(data: Any, time_ns: str) -> EntityObservation:
    require_keys(
        data,
        (
            "entity_id",
            "kind",
            "born_ns",
            "ended_ns",
            "sample_time_ns",
            "valid_until_ns",
            "position_enu_m",
            "velocity_enu_mps",
            "body",
            "provenance",
            "unknown_reasons",
        ),
        "entity observation",
    )
    for key in ("born_ns", "sample_time_ns", "valid_until_ns"):
        ns(data[key])
    ended = data["ended_ns"]
    if ended is not None and ns(ended) <= ns(data["born_ns"]):
        raise ContractError("entity lifetime must be nonempty")
    if not ns(data["born_ns"]) <= ns(data["sample_time_ns"]) <= ns(time_ns) or ns(data["valid_until_ns"]) < ns(
        data["sample_time_ns"]
    ):
        raise ContractError("invalid observation sample/expiry ordering")
    if ns(data["born_ns"]) > ns(time_ns) or (ended is not None and ns(time_ns) >= ns(ended)):
        raise ContractError("frame contains an entity outside its declared lifetime")
    if data["kind"] not in ("aerial", "ground", "static"):
        raise ContractError("unsupported neutral entity kind")
    body = None
    if data["body"] is not None:
        require_keys(data["body"], ("shape", "radius_m"), "physical body")
        radius = data["body"]["radius_m"]
        if data["body"]["shape"] != "sphere" or type(radius) not in (int, float) or not math.isfinite(radius) or radius <= 0:
            raise ContractError("only declared positive-radius physical spheres are supported")
        body = SphereBody(float(radius))
    if not isinstance(data["provenance"], dict) or not isinstance(data["unknown_reasons"], list):
        raise ContractError("invalid entity provenance/unknown reasons")
    return EntityObservation(
        entity_id=token(data["entity_id"], "entity_id"),
        kind=data["kind"],
        born_ns=data["born_ns"],
        ended_ns=ended,
        sample_time_ns=data["sample_time_ns"],
        valid_until_ns=data["valid_until_ns"],
        position_enu_m=vector(data["position_enu_m"]),
        velocity_enu_mps=vector(data["velocity_enu_mps"]),
        body=body,
        provenance=freeze_json(data["provenance"]),
        unknown_reasons=tuple(token(item, "unknown reason") for item in data["unknown_reasons"]),
    )


def normalize_frame(raw: bytes, expected_run: RunIdentity, engine_origin_ns: str) -> ObservationFrame:
    """Validate bytes and retain their exact content hash; never default absent motion to zero."""
    data = decode_json(raw)
    require_keys(
        data,
        (
            "schema",
            "run",
            "frame_seq",
            "stage_evidence_key",
            "sim_time_ns",
            "engine_origin_ns",
            "coordinate_frame",
            "units",
            "entities",
            "provenance",
        ),
        "observation frame",
    )
    if data["schema"] != FRAME_SCHEMA or data["coordinate_frame"] != "ENU":
        raise ContractError("unsupported observation schema or coordinate frame")
    if data["units"] != {"position": "m", "velocity": "m/s", "time": "ns"}:
        raise ContractError("observation units must be explicit m, m/s, ns")
    if run_identity(data["run"]) != expected_run:
        raise ContractError("frame belongs to another attachment/run/epoch/manifest")
    ns(engine_origin_ns)
    if data["engine_origin_ns"] != engine_origin_ns or ns(data["sim_time_ns"]) < ns(engine_origin_ns):
        raise ContractError("engine origin must be fixed and no later than frame time")
    if type(data["frame_seq"]) is not int or data["frame_seq"] < 0:
        raise ContractError("frame_seq must be a nonnegative integer")
    if not isinstance(data["entities"], list) or not isinstance(data["provenance"], dict):
        raise ContractError("invalid frame entities/provenance")
    entities = tuple(_entity(entity, data["sim_time_ns"]) for entity in data["entities"])
    if len({entity.entity_id for entity in entities}) != len(entities):
        raise ContractError("duplicate entity_id in frame")
    return ObservationFrame(
        key=FrameKey(
            expected_run,
            data["frame_seq"],
            hashlib.sha256(raw).hexdigest(),
            token(data["stage_evidence_key"], "stage evidence"),
        ),
        sim_time_ns=data["sim_time_ns"],
        engine_origin_ns=engine_origin_ns,
        entities=entities,
        provenance=freeze_json(data["provenance"]),
    )
