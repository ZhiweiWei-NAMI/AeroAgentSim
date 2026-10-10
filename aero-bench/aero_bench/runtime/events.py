from __future__ import annotations

import base64
import binascii
import hashlib
import threading
import weakref
from collections import OrderedDict
from functools import lru_cache
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime, StateAttribute
from aero_bench.serialization import canonical_json_bytes


RUN_EVENT_CHAIN_ROOT = "0" * 64

RunEventSourceKind: TypeAlias = Literal[
    "agent",
    "executor",
    "gateway",
    "harness",
    "provider",
    "verifier",
]
RunEventSegmentKind: TypeAlias = Literal["runtime", "verifier"]
RunEventVisibilityScope: TypeAlias = Literal[
    "agent",
    "operator",
    "private",
    "public",
    "verifier",
]
# Public engineering interaction mirrors disclose only these explicit fields.
# Raw observations, arguments, results and authentication remain private.
PUBLIC_AGENT_INTERACTION_FIELDS = {
    "agent.decision_summary.v1": frozenset({"decision_summary"}),
    "agent.tool_call.v1": frozenset({"tool_id"}),
    "agent.tool_result.v1": frozenset({"tool_id", "outcome", "failure_class"}),
    "agent.observation.v1": frozenset(),
    "agent.query_call.v1": frozenset({"query_type"}),
    "agent.query_result.v1": frozenset(
        {"query_type", "outcome", "failure_class"}
    ),
}

AgentInteractionType: TypeAlias = Literal[
    "agent.observation.v1",
    "agent.query_call.v1",
    "agent.query_result.v1",
    "agent.decision_summary.v1",
    "agent.tool_call.v1",
    "agent.tool_result.v1",
    "gateway.dispatch.v1",
    "provider.command.v1",
    "mavlink.command.v1",
    "mavlink.command_ack.v1",
    "physical.effect.v1",
    "mission.event.v1",
    "process.stdout.v1",
    "process.stderr.v1",
    "px4.telemetry.v1",
    "scene.entity_state.v1",
    "sensor.frame_ref.v2",
    "ns3.link_state.v1",
    "sumo.traffic_light.v1",
    "verifier.evidence.v1",
]

_AGENT_INTERACTION_TYPES = frozenset(
    {
        "agent.observation.v1",
        "agent.query_call.v1",
        "agent.query_result.v1",
        "agent.decision_summary.v1",
        "agent.tool_call.v1",
        "agent.tool_result.v1",
    }
)
_COMMAND_INTERACTION_TYPES = frozenset(
    {
        "agent.tool_call.v1",
        "agent.tool_result.v1",
        "gateway.dispatch.v1",
        "provider.command.v1",
        "mavlink.command.v1",
        "mavlink.command_ack.v1",
        "physical.effect.v1",
    }
)
_QUERY_INTERACTION_TYPES = frozenset(
    {"agent.query_call.v1", "agent.query_result.v1"}
)
_FORBIDDEN_REASONING_FIELDS = frozenset(
    {"chain_of_thought", "hidden_reasoning", "reasoning_trace"}
)


def _sorted_unique(label: str, values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")
    if values != tuple(sorted(values)):
        raise ValueError(f"{label} values must be sorted")


# Payload values can contain bounded process-stream chunks, so keep this cache
# small.  It primarily removes the repeated digest work while one event is
# validated; it is not intended to retain the complete run history.
@lru_cache(maxsize=256)
def _attribute_payload_digest_cached(
    attributes: tuple[tuple[str, str, object], ...],
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            [
                {
                    "name": name,
                    "value_type": value_type,
                    "value": (
                        float.fromhex(value[1])
                        if (
                            isinstance(value, tuple)
                            and len(value) == 2
                            and value[0] == "float"
                            and isinstance(value[1], str)
                        )
                        else value
                    ),
                }
                for name, value_type, value in attributes
            ]
        )
    ).hexdigest()


def _attribute_payload_digest(attributes: tuple[StateAttribute, ...]) -> str:
    cache_key = tuple(
        (
            attribute.name,
            attribute.value_type,
            (
                ("float", attribute.value.hex())
                if isinstance(attribute.value, float)
                else attribute.value
            ),
        )
        for attribute in attributes
    )
    return _attribute_payload_digest_cached(cache_key)


# Digest helpers are called both while constructing an event and while checking
# the same immutable event during sealing/replay.  Cache canonical bytes by
# object identity, rather than storing them in a Pydantic PrivateAttr: Pydantic
# copies private attributes in ``model_copy(update=...)`` and that can otherwise
# reuse a digest for a different (tampered) model.  Weak references keep this
# bounded cache from retaining a complete run history.
_CANONICAL_DIGEST_CACHE_MAXSIZE = 1024
_canonical_digest_cache: OrderedDict[
    int, tuple[weakref.ReferenceType[object], bytes]
] = OrderedDict()
_canonical_digest_cache_lock = threading.Lock()


def _canonical_digest_bytes(
    model: object,
    *,
    exclude: set[str],
) -> bytes:
    key = id(model)
    with _canonical_digest_cache_lock:
        cached = _canonical_digest_cache.get(key)
        if cached is not None:
            reference, value = cached
            if reference() is model:
                _canonical_digest_cache.move_to_end(key)
                return value
            # An object id can be reused after collection.  Never return the
            # bytes belonging to the old object.
            _canonical_digest_cache.pop(key, None)

    # StrictModel instances are weak-referenceable.  Keep a defensive path for
    # an alternate validated model implementation so digesting still works.
    value = canonical_json_bytes(
        model.model_dump(mode="json", exclude=exclude)  # type: ignore[attr-defined]
    )
    try:
        reference = weakref.ref(model)
    except TypeError:
        return value
    with _canonical_digest_cache_lock:
        _canonical_digest_cache[key] = (reference, value)
        _canonical_digest_cache.move_to_end(key)
        while len(_canonical_digest_cache) > _CANONICAL_DIGEST_CACHE_MAXSIZE:
            _canonical_digest_cache.popitem(last=False)
    return value


class RunEventAudience(StrictModel):
    scope: RunEventVisibilityScope
    audience_id: Identifier | None = None

    @model_validator(mode="after")
    def scoped_audience(self) -> "RunEventAudience":
        if self.scope == "agent" and self.audience_id is None:
            raise ValueError("agent event visibility requires an audience_id")
        if self.scope != "agent" and self.audience_id is not None:
            raise ValueError("only agent visibility may name an audience_id")
        return self


ProcessStreamName: TypeAlias = Literal["stdout", "stderr"]
ProcessStreamRole: TypeAlias = Literal["agent", "agent_driver", "harness", "provider", "verifier"]
ProcessStreamContentClass: TypeAlias = Literal["explicit_output", "system_log"]


class ProcessStreamChunk(StrictModel):
    """One bounded, exact workload stream chunk presented for audit ingestion."""

    schema_version: Literal["aero-bench.process-stream-chunk/v1"]
    run_id: Sha256
    workload_id: Identifier
    workload_role: ProcessStreamRole
    stream: ProcessStreamName
    content_class: ProcessStreamContentClass
    sequence: Annotated[int, Field(ge=0)]
    byte_offset: Annotated[int, Field(ge=0)]
    captured_wall_time_ns: Annotated[int, Field(ge=0)]
    payload_base64: Annotated[str, Field(max_length=87_384)]
    payload_size_bytes: Annotated[int, Field(ge=0, le=65_536)]
    payload_sha256: Sha256
    final: bool
    truncated: bool = False

    @model_validator(mode="after")
    def exact_bounded_chunk(self) -> "ProcessStreamChunk":
        try:
            payload = base64.b64decode(
                self.payload_base64.encode("ascii"),
                validate=True,
            )
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ValueError("process stream payload is not canonical base64") from error
        if base64.b64encode(payload).decode("ascii") != self.payload_base64:
            raise ValueError("process stream payload base64 is not canonical")
        if len(payload) != self.payload_size_bytes:
            raise ValueError("process stream payload size is invalid")
        if hashlib.sha256(payload).hexdigest() != self.payload_sha256:
            raise ValueError("process stream payload digest is invalid")
        if not self.final and not payload:
            raise ValueError("non-final process stream chunk cannot be empty")
        if self.truncated and not self.final:
            raise ValueError("process stream truncation must close the stream")
        if self.workload_role == "agent" and self.content_class != "explicit_output":
            raise ValueError("Agent streams may contain explicit output only")
        return self


class AgentInteraction(StrictModel):
    """One explicit, replayable interaction embedded in a RunEvent.

    This records only explicit Agent output and externally observable system data.
    Hidden model reasoning is neither requested nor represented by this contract.
    """

    schema_version: Literal["aero-bench.agent-interaction/v1"]
    segment_kind: RunEventSegmentKind
    interaction_type: AgentInteractionType
    run_id: Sha256
    sequence: Annotated[int, Field(ge=0)]
    event_id: Identifier
    time: SimulationTime
    wall_time_ns: Annotated[int, Field(ge=0)]
    source_kind: RunEventSourceKind
    source: Identifier
    workload_id: Identifier
    correlation_id: Identifier
    parent_event_id: Identifier | None = None
    causal_event_ids: tuple[Identifier, ...] = ()
    visibility: tuple[RunEventAudience, ...] = Field(min_length=1)
    frame_id: Annotated[str, Field(min_length=1)] | None = None
    agent_id: Identifier | None = None
    provider_id: Identifier | None = None
    vehicle_id: Identifier | None = None
    entity_id: Identifier | None = None
    command_id: Identifier | None = None
    query_id: Identifier | None = None
    observation_id: Identifier | None = None
    payload_schema_id: Identifier
    payload: tuple[StateAttribute, ...]
    payload_digest: Sha256
    interaction_digest: Sha256
    @model_validator(mode="after")
    def canonical_interaction(self) -> "AgentInteraction":
        _sorted_unique(
            "AgentInteraction payload",
            tuple(attribute.name for attribute in self.payload),
        )
        _sorted_unique("AgentInteraction causal event", self.causal_event_ids)
        visibility_keys = tuple(
            f"{item.scope}:{item.audience_id or ''}" for item in self.visibility
        )
        _sorted_unique("AgentInteraction visibility", visibility_keys)
        if (
            self.parent_event_id is not None
            and self.parent_event_id not in self.causal_event_ids
        ):
            raise ValueError("parent_event_id must be present in causal_event_ids")
        if self.interaction_type in _AGENT_INTERACTION_TYPES and self.agent_id is None:
            raise ValueError("Agent interaction requires agent_id")
        if self.interaction_type in _COMMAND_INTERACTION_TYPES and self.command_id is None:
            raise ValueError("command interaction requires command_id")
        if self.interaction_type in _QUERY_INTERACTION_TYPES and self.query_id is None:
            raise ValueError("query interaction requires query_id")
        if (
            self.interaction_type == "agent.observation.v1"
            and self.observation_id is None
        ):
            raise ValueError("Agent observation interaction requires observation_id")
        if self.interaction_type == "scene.entity_state.v1" and self.entity_id is None:
            raise ValueError("scene entity interaction requires entity_id")
        if self.interaction_type in {"sensor.frame_ref.v2"} and (
            self.frame_id is None or self.observation_id is None
        ):
            raise ValueError(
                "sensor frame interaction requires frame and observation IDs"
            )
        if self.interaction_type == "px4.telemetry.v1" and (
            self.provider_id is None or self.entity_id is None
        ):
            raise ValueError("PX4 telemetry interaction requires provider and entity")
        if self.interaction_type == "ns3.link_state.v1" and self.provider_id is None:
            raise ValueError("ns-3 interaction requires provider_id")
        if self.interaction_type in {"process.stdout.v1", "process.stderr.v1"}:
            if self.source_kind == "agent" and self.agent_id is None:
                raise ValueError("Agent process stream requires agent_id")
            if self.source_kind == "provider" and self.provider_id is None:
                raise ValueError("Provider process stream requires provider_id")
        if self.interaction_type == "verifier.evidence.v1" and (
            self.segment_kind != "verifier" or self.source_kind != "verifier"
        ):
            raise ValueError("verifier evidence must be in the verifier segment")
        names = {attribute.name for attribute in self.payload}
        if names.intersection(_FORBIDDEN_REASONING_FIELDS):
            raise ValueError("AgentInteraction cannot contain hidden reasoning fields")
        if self.interaction_type == "agent.decision_summary.v1":
            summaries = tuple(
                attribute.value
                for attribute in self.payload
                if attribute.name == "decision_summary"
            )
            if (
                len(summaries) != 1
                or not isinstance(summaries[0], str)
                or not 1 <= len(summaries[0]) <= 4096
            ):
                raise ValueError(
                    "decision summary interaction requires one bounded explicit summary"
                )
        if self.payload_digest != _attribute_payload_digest(self.payload):
            raise ValueError("AgentInteraction payload_digest is invalid")
        if self.interaction_digest != agent_interaction_digest_value(self):
            raise ValueError("AgentInteraction interaction_digest is invalid")
        return self


class RunEvent(StrictModel):
    """The sole authoritative event envelope for runtime and verifier segments."""

    schema_version: Literal["aero-bench.run-event/v1"]
    segment_kind: RunEventSegmentKind
    run_id: Sha256
    sequence: Annotated[int, Field(ge=0)]
    event_id: Identifier
    source_kind: RunEventSourceKind
    source: Identifier
    workload_id: Identifier
    event_type: Identifier
    time: SimulationTime
    wall_time_ns: Annotated[int, Field(ge=0)]
    correlation_id: Identifier
    parent_event_id: Identifier | None = None
    causal_event_ids: tuple[Identifier, ...] = ()
    visibility: tuple[RunEventAudience, ...] = Field(min_length=1)
    frame_id: Annotated[str, Field(min_length=1)] | None = None
    agent_id: Identifier | None = None
    provider_id: Identifier | None = None
    vehicle_id: Identifier | None = None
    entity_id: Identifier | None = None
    command_id: Identifier | None = None
    query_id: Identifier | None = None
    observation_id: Identifier | None = None
    payload_schema_id: Identifier
    payload: tuple[StateAttribute, ...]
    payload_digest: Sha256
    interaction: AgentInteraction | None = None
    previous_event_digest: Sha256
    event_digest: Sha256
    @model_validator(mode="after")
    def canonical_event(self) -> "RunEvent":
        if self.event_id != f"event.{self.sequence:016d}":
            raise ValueError("RunEvent event_id does not match its global sequence")
        _sorted_unique(
            "RunEvent payload",
            tuple(attribute.name for attribute in self.payload),
        )
        _sorted_unique("RunEvent causal event", self.causal_event_ids)
        visibility_keys = tuple(
            f"{item.scope}:{item.audience_id or ''}" for item in self.visibility
        )
        _sorted_unique("RunEvent visibility", visibility_keys)
        if (
            self.parent_event_id is not None
            and self.parent_event_id not in self.causal_event_ids
        ):
            raise ValueError("parent_event_id must be present in causal_event_ids")
        if self.sequence == 0:
            if self.previous_event_digest != RUN_EVENT_CHAIN_ROOT:
                raise ValueError("first RunEvent must bind the chain root")
        elif self.previous_event_digest == RUN_EVENT_CHAIN_ROOT:
            raise ValueError("later RunEvent must bind a prior event digest")
        if self.payload_digest != _attribute_payload_digest(self.payload):
            raise ValueError("RunEvent payload_digest is invalid")
        if self.interaction is not None:
            interaction = self.interaction
            common_values = (
                (interaction.segment_kind, self.segment_kind),
                (interaction.run_id, self.run_id),
                (interaction.sequence, self.sequence),
                (interaction.event_id, self.event_id),
                (interaction.time, self.time),
                (interaction.wall_time_ns, self.wall_time_ns),
                (interaction.source_kind, self.source_kind),
                (interaction.source, self.source),
                (interaction.workload_id, self.workload_id),
                (interaction.correlation_id, self.correlation_id),
                (interaction.parent_event_id, self.parent_event_id),
                (interaction.causal_event_ids, self.causal_event_ids),
                (interaction.visibility, self.visibility),
                (interaction.frame_id, self.frame_id),
                (interaction.agent_id, self.agent_id),
                (interaction.provider_id, self.provider_id),
                (interaction.vehicle_id, self.vehicle_id),
                (interaction.entity_id, self.entity_id),
                (interaction.command_id, self.command_id),
                (interaction.query_id, self.query_id),
                (interaction.observation_id, self.observation_id),
                (interaction.payload_schema_id, self.payload_schema_id),
                (interaction.payload, self.payload),
                (interaction.payload_digest, self.payload_digest),
            )
            if any(left != right for left, right in common_values):
                raise ValueError("RunEvent and AgentInteraction bindings differ")
        if self.event_digest != run_event_digest_value(self):
            raise ValueError("RunEvent event_digest is invalid")
        return self


def agent_interaction_digest_value(interaction: AgentInteraction) -> str:
    canonical = _canonical_digest_bytes(
        interaction,
        exclude={"interaction_digest"},
    )
    return hashlib.sha256(canonical).hexdigest()


def run_event_digest_value(event: RunEvent) -> str:
    canonical = _canonical_digest_bytes(event, exclude={"event_digest"})
    return hashlib.sha256(canonical).hexdigest()


def run_event_payload_digest_value(
    payload: tuple[StateAttribute, ...],
) -> str:
    return _attribute_payload_digest(payload)


__all__ = [
    "RUN_EVENT_CHAIN_ROOT",
    "AgentInteraction",
    "AgentInteractionType",
    "ProcessStreamChunk",
    "ProcessStreamContentClass",
    "ProcessStreamName",
    "ProcessStreamRole",
    "RunEvent",
    "RunEventAudience",
    "RunEventSegmentKind",
    "RunEventSourceKind",
    "RunEventVisibilityScope",
    "agent_interaction_digest_value",
    "run_event_digest_value",
    "run_event_payload_digest_value",
]
