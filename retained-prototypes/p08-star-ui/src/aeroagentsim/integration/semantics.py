"""Observation projection/history and a private, optional native-evaluator boundary.

No predicates, temporal formulas, CPA approximation, or truth tables are implemented
here. Native evaluator results are only True, False, or None.
"""

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Tuple

from .contracts import (
    SEMANTIC_SCHEMA,
    ContractError,
    FrameKey,
    ObservationFrame,
    ViewKey,
    freeze_json,
    ns,
    relative_seconds,
    sphere_surface_clearance,
)
from .normalization import canonical_bytes, token

ACTOR_FIELDS = frozenset(("actor.position_enu_m", "actor.velocity_enu_mps", "actor.speed_mps", "actor.vertical_speed_mps"))
PAIR_FIELDS = frozenset(("pair.surface_clearance_m", "pair.closing_speed_mps", "pair.cpa_time_s"))


@dataclass(frozen=True)
class SemanticBinding:
    binding_id: str  # Distinct instances of the same target need distinct IDs.
    actor_ids: Tuple[str, ...]  # Ordered physical entities, never compound owner labels.
    target_ids: Tuple[str, ...]  # Opaque IDs in the supplied versioned native registry.
    registry_revision: str
    state_fields: Mapping[str, str]  # Exact native field -> neutral source field.
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        token(self.binding_id, "binding_id")
        token(self.registry_revision, "registry revision")
        actors, targets = tuple(self.actor_ids), tuple(self.target_ids)
        if not actors or not targets or len(set(actors)) != len(actors) or len(set(targets)) != len(targets):
            raise ContractError("bindings need unique ordered actors and unique targets")
        for item in actors + targets:
            token(item, "actor/target ID")
        if not self.state_fields or not isinstance(self.parameters, Mapping):
            raise ContractError("binding requires an explicit field map and lexical parameter mapping")
        for native_field, source_field in self.state_fields.items():
            token(native_field, "native field")
            if source_field not in ACTOR_FIELDS | PAIR_FIELDS:
                raise ContractError("unresolved or unsupported observation field mapping")
            expected_actors = 1 if source_field in ACTOR_FIELDS else 2
            if len(actors) != expected_actors:
                raise ContractError("source field has incompatible actor arity")
        object.__setattr__(self, "actor_ids", actors)
        object.__setattr__(self, "target_ids", targets)
        object.__setattr__(self, "state_fields", freeze_json(self.state_fields))
        object.__setattr__(self, "parameters", freeze_json(self.parameters))


@dataclass(frozen=True)
class StateSample:
    sim_time_ns: str
    relative_seconds: float
    values: Mapping[str, Any]
    frame_key: Optional[FrameKey]
    unknown_reasons: Tuple[str, ...]
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class EvaluationRequest:
    view_key: ViewKey
    binding: SemanticBinding
    current: StateSample
    history: Tuple[StateSample, ...]  # Full binding-epoch history, not a 3/5 second ring.
    history_censored: bool
    occurrence_policy: str = "no_occurrences_emitted"


class NativeEvaluator(Protocol):
    def evaluate_batch(self, request: EvaluationRequest) -> Mapping[str, Optional[bool]]:
        """Private bridge calls real evaluate/evaluate_many once for this envelope.

        The bridge owns native parameter/reference resolution and transitive window
        requirements. It must use a fresh mutable context/cache for each binding.
        """


@dataclass(frozen=True)
class SemanticEvidence:
    request: EvaluationRequest
    truths: Mapping[str, Optional[bool]]
    evaluator_available: bool
    unknown_reasons: Mapping[str, Tuple[str, ...]]
    schema: str = field(default=SEMANTIC_SCHEMA, init=False)


def _project(frame: ObservationFrame, binding: SemanticBinding) -> StateSample:
    by_id = {entity.entity_id: entity for entity in frame.entities}
    actors = tuple(by_id.get(actor_id) for actor_id in binding.actor_ids)
    reasons = set()
    for actor in actors:
        if actor is None:
            reasons.add("entity_not_active")
        else:
            reasons.update(actor.unknown_reasons)
            if not actor.valid_at(frame.sim_time_ns):
                reasons.add("observation_expired")
    values = {}
    for native_field, source in binding.state_fields.items():
        value = None
        if all(actor is not None and actor.valid_at(frame.sim_time_ns) for actor in actors):
            actor = actors[0]
            if source == "actor.position_enu_m":
                value = actor.position_enu_m
            elif source == "actor.velocity_enu_mps":
                value = actor.velocity_enu_mps
            elif source == "actor.speed_mps" and actor.velocity_enu_mps is not None:
                value = math.hypot(*actor.velocity_enu_mps[:2])
            elif source == "actor.vertical_speed_mps" and actor.velocity_enu_mps is not None:
                value = actor.velocity_enu_mps[2]
            elif source == "pair.surface_clearance_m":
                value = sphere_surface_clearance(*actors)
                if any(item.body is None for item in actors):
                    reasons.add("physical_body_geometry_unavailable")
            elif source in ("pair.closing_speed_mps", "pair.cpa_time_s"):
                a, b = actors
                if all(item.position_enu_m is not None and item.velocity_enu_mps is not None for item in actors):
                    r = tuple(y - x for x, y in zip(a.position_enu_m, b.position_enu_m))
                    v = tuple(y - x for x, y in zip(a.velocity_enu_mps, b.velocity_enu_mps))
                    denominator = (
                        math.sqrt(sum(x * x for x in r)) if source == "pair.closing_speed_mps" else sum(x * x for x in v)
                    )
                    if denominator > 0:
                        value = -sum(x * y for x, y in zip(r, v)) / denominator
                    else:
                        reasons.add("undefined_pair_denominator")
        if value is None:
            reasons.add("state_unavailable:" + native_field)
        values[native_field] = value
    provenance = {
        "frame": frame.provenance,
        "actors": {
            actor.entity_id: {
                "source": actor.provenance,
                "sample_time_ns": actor.sample_time_ns,
                "valid_until_ns": actor.valid_until_ns,
                "born_ns": actor.born_ns,
                "ended_ns": actor.ended_ns,
            }
            for actor in actors
            if actor is not None
        },
        "field_map": binding.state_fields,
        "geometry_policy": "declared_physical_spheres_only",
    }
    return StateSample(
        frame.sim_time_ns,
        frame.relative_seconds,
        freeze_json(values),
        frame.key,
        tuple(sorted(reasons)),
        freeze_json(provenance),
    )


def _expiry(frame: ObservationFrame, binding: SemanticBinding) -> Optional[int]:
    relevant = [entity for entity in frame.entities if entity.entity_id in binding.actor_ids]
    boundaries = [ns(entity.valid_until_ns) for entity in relevant]
    boundaries.extend(ns(entity.ended_ns) for entity in relevant if entity.ended_ns is not None)
    return min(boundaries) if boundaries else None


class SemanticSession:
    """Forward observation history. Backward seek requires a fresh, warmed session.

    Caches/history are separated by physical binding, epoch, and attachment. Native
    latches may use the entire history; only explicit epoch changes start it anew.
    """

    def __init__(self, evaluator: Optional[NativeEvaluator] = None):
        self._evaluator = evaluator
        self._history = {}
        self._signatures = {}
        self._revisions = {}
        self._previous = {}

    def evaluate(
        self, frame: ObservationFrame, bindings: Tuple[SemanticBinding, ...], evaluation_revision: str, binding_epoch: str
    ) -> Tuple[SemanticEvidence, ...]:
        token(evaluation_revision, "evaluation revision")
        token(binding_epoch, "binding epoch")
        bindings = tuple(bindings)
        if len({binding.binding_id for binding in bindings}) != len(bindings):
            raise ContractError("duplicate binding instance in one observation")
        view_key = ViewKey(frame.key, evaluation_revision, binding_epoch)
        staged = []
        # Validate the whole batch before modifying session state.
        for binding in bindings:
            key = (frame.key.run, binding_epoch, binding.binding_id)
            signature = (binding.actor_ids, tuple(sorted(binding.state_fields.items())))
            if key in self._signatures and signature != self._signatures[key]:
                raise ContractError("actor/field mapping changes require a new binding epoch")
            revision_key = (key, evaluation_revision)
            revision = hashlib.sha256(
                canonical_bytes(
                    {
                        "targets": binding.target_ids,
                        "registry": binding.registry_revision,
                        "parameters": binding.parameters,
                    }
                )
            ).hexdigest()
            if revision_key in self._revisions and revision != self._revisions[revision_key]:
                raise ContractError("parameter/registry/target changes require a new evaluation revision")
            previous = self._previous.get(key)
            if previous is not None:
                previous_frame, _ = previous
                if previous_frame.engine_origin_ns != frame.engine_origin_ns:
                    raise ContractError("engine origin changed inside one binding epoch")
                if frame.key != previous_frame.key and (
                    ns(frame.sim_time_ns) <= ns(previous_frame.sim_time_ns)
                    or frame.key.frame_seq <= previous_frame.key.frame_seq
                ):
                    raise ContractError("backward/out-of-order observation; warm a new session")
            history = self._history.get(key, ())
            if previous is not None and previous[0].key != frame.key:
                previous_frame, previous_binding = previous
                boundaries = {}
                expiry = _expiry(previous_frame, previous_binding)
                if expiry is not None and ns(previous_frame.sim_time_ns) < expiry < ns(frame.sim_time_ns):
                    boundaries.setdefault(expiry, set()).add("observation_expired")
                for gap in frame.gaps_before:
                    if ns(previous_frame.sim_time_ns) < ns(gap.start_ns) <= ns(frame.sim_time_ns):
                        boundaries.setdefault(ns(gap.start_ns), set()).add("replay_gap:" + gap.reason)
                for time, reasons in sorted(boundaries.items()):
                    history += (
                        StateSample(
                            str(time),
                            relative_seconds(str(time), frame.engine_origin_ns),
                            freeze_json({field: None for field in binding.state_fields}),
                            None,
                            tuple(sorted(reasons)),
                            freeze_json({"synthetic_boundary": True}),
                        ),
                    )
            current = _project(frame, binding)
            if previous is None or previous[0].key != frame.key:
                history += (current,)
            censored = ns(history[0].sim_time_ns) > ns(frame.engine_origin_ns) or any(
                any(value is None for value in sample.values.values()) for sample in history
            )
            request = EvaluationRequest(view_key, binding, current, history, censored)
            if self._evaluator is None:
                truths = {target: None for target in binding.target_ids}
                unknown = {target: ("evaluator_unavailable",) for target in binding.target_ids}
            else:
                truths = self._evaluator.evaluate_batch(request)
                if (
                    not isinstance(truths, Mapping)
                    or set(truths) != set(binding.target_ids)
                    or any(value is not None and type(value) is not bool for value in truths.values())
                ):
                    raise ContractError("native truth results must match targets and contain only True/False/None")
                unknown = {
                    target: ("native_evaluation_unknown",) + current.unknown_reasons
                    for target, value in truths.items()
                    if value is None
                }
            evidence = SemanticEvidence(request, freeze_json(truths), self._evaluator is not None, freeze_json(unknown))
            staged.append((key, signature, revision_key, revision, history, binding, evidence))
        for key, signature, revision_key, revision, history, binding, _ in staged:
            self._history[key] = history
            self._signatures[key] = signature
            self._revisions[revision_key] = revision
            self._previous[key] = (frame, binding)
        return tuple(item[-1] for item in staged)
