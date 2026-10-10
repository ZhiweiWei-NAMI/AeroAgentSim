"""Immutable provider-neutral contracts; these are not AERO_BENCH serializers."""

import math
import re
from dataclasses import dataclass, field, fields as dataclass_fields, is_dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

FRAME_SCHEMA = "aeroagentsim.observation/v1"
REPLAY_SCHEMA = "aeroagentsim.replay-index/v1"
SEMANTIC_SCHEMA = "aeroagentsim.semantic-evidence/v1"
SELECTION_SCHEMA = "aeroagentsim.selection/v1"
VIEW_SCHEMA = "aeroagentsim.shared-view/v1"
Vec3 = Tuple[float, float, float]


class ContractError(ValueError):
    """An input does not meet this integration's own contract."""


def ns(value: Any) -> int:
    """Parse canonical, nonnegative decimal strings without float conversion."""
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise ContractError("nanoseconds must be a canonical nonnegative decimal string")
    return int(value)


def relative_seconds(time_ns: str, origin_ns: str) -> float:
    return (ns(time_ns) - ns(origin_ns)) / 1_000_000_000


def freeze_json(value: Any) -> Any:
    """Copy JSON-compatible values into deeply immutable containers."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("JSON mapping keys must be strings")
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    raise ContractError("only finite JSON-compatible values are supported")


def thaw_json(value: Any) -> Any:
    """Make a fresh native-evaluator context; never share mutable caches."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


def contract_payload(value: Any) -> Any:
    """Export neutral dataclass contracts as a fresh JSON-compatible payload."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: contract_payload(getattr(value, item.name)) for item in dataclass_fields(value)}
    if isinstance(value, Mapping):
        return {key: contract_payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [contract_payload(item) for item in value]
    return thaw_json(freeze_json(value))


@dataclass(frozen=True)
class RunIdentity:
    attachment_id: str
    run_id: str
    run_epoch: str
    manifest_revision: str


@dataclass(frozen=True)
class FrameKey:
    run: RunIdentity
    frame_seq: int
    frame_hash: str
    stage_evidence_key: str


@dataclass(frozen=True)
class ViewKey:
    frame: FrameKey
    evaluation_revision: str
    binding_epoch: str


@dataclass(frozen=True)
class Gap:
    start_ns: str
    end_ns: str
    reason: str

    def contains(self, time_ns: str) -> bool:
        return ns(self.start_ns) <= ns(time_ns) < ns(self.end_ns)


@dataclass(frozen=True)
class SphereBody:
    """Declared physical sphere centered at position_enu_m, never a proxy radius."""

    radius_m: float


@dataclass(frozen=True)
class EntityObservation:
    entity_id: str
    kind: str
    born_ns: str
    ended_ns: Optional[str]
    sample_time_ns: str
    valid_until_ns: str  # Exclusive: state becomes null at this boundary.
    position_enu_m: Optional[Vec3]
    velocity_enu_mps: Optional[Vec3]
    body: Optional[SphereBody]
    provenance: Mapping[str, Any]
    unknown_reasons: Tuple[str, ...] = ()

    def valid_at(self, time_ns: str) -> bool:
        time = ns(time_ns)
        return (
            ns(self.born_ns) <= time
            and (self.ended_ns is None or time < ns(self.ended_ns))
            and ns(self.sample_time_ns) <= time < ns(self.valid_until_ns)
        )


@dataclass(frozen=True)
class ObservationFrame:
    key: FrameKey
    sim_time_ns: str
    engine_origin_ns: str
    entities: Tuple[EntityObservation, ...]
    provenance: Mapping[str, Any]
    gaps_before: Tuple[Gap, ...] = ()  # Replay wrapper metadata, outside source hash.
    schema: str = field(default=FRAME_SCHEMA, init=False)

    @property
    def relative_seconds(self) -> float:
        return relative_seconds(self.sim_time_ns, self.engine_origin_ns)


def enu_to_render(position: Vec3) -> Vec3:
    """ENU [east, north, up] -> rendering [east, up, -north]."""
    east, north, up = position
    return east, up, -north


def render_to_enu(position: Vec3) -> Vec3:
    east, up, negative_north = position
    return east, -negative_north, up


def sphere_surface_clearance(a: EntityObservation, b: EntityObservation) -> Optional[float]:
    if a.body is None or b.body is None or a.position_enu_m is None or b.position_enu_m is None:
        return None
    center_distance = math.sqrt(sum((x - y) ** 2 for x, y in zip(a.position_enu_m, b.position_enu_m)))
    return center_distance - a.body.radius_m - b.body.radius_m
