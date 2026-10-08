"""Offline DES decision partition; model latency never advances simulated time."""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, cast

from aerokernel import CommandRequest, Dependency, EntityRef, Partition
from aerokernel.errors import KernelError
from aerokernel.relations import RelationDependency
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw, typed_equal

from aeroagentsim.platform.plugins import EngineBuild

from .observation import FieldGrant, build_observation
from .provider import OpenAIProvider, Provider, ProviderError
from .tools import ToolCatalog, ToolGrant

RECORD_SCHEMA = "aas.agent.record"


class Decision(ContextEngine):
    """Model outputs are ordinary recorded events and authority-checked commands."""

    def __init__(self, build: EngineBuild, provider: Provider | None = None) -> None:
        self.build = build
        cfg = build.config
        required = {"instruction", "grants", "points", "budget"}
        if set(cfg) - required - {"provider"} or required - set(cfg):
            raise ValueError(
                "decision config requires instruction/grants/points/budget"
            )
        if not isinstance(cfg["instruction"], str) or not cfg["instruction"].strip():
            raise ValueError("decision instruction must be nonempty")
        grants = cfg["grants"]
        if set(grants) != {"fields", "relations", "events", "commands"}:
            raise ValueError("grants require fields/relations/events/commands")
        refs = {ref.id: ref for ref in build.entities}
        self.fields = tuple(
            FieldGrant(refs[item["entity"]], item["field"]) for item in grants["fields"]
        )
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("duplicate field grants")
        for grant in self.fields:
            if not build.registry.is_a(
                grant.entity.type_id, build.registry.field(grant.field).declaring_type
            ):
                raise ValueError("field grant is inapplicable to entity")
        self.relations = tuple(grants["relations"])
        if any(
            not isinstance(item, dict) or set(item) != {"schema", "topic"}
            for item in grants["events"]
        ):
            raise ValueError("event grants require explicit schema/topic")
        self.events = tuple(item["schema"] for item in grants["events"])
        for schema in self.events:
            if build.registry.message(schema).kind != "event":
                raise ValueError("event grant requires event schema")
        tool_grants = tuple(
            ToolGrant(item["schema"], item["target"], item.get("allowed", {}))
            for item in grants["commands"]
        )
        self.catalog = ToolCatalog(build.registry, tool_grants)
        self.points: list[dict[str, Any]] = cfg["points"]
        for point in self.points:
            if len(point) != 1 or not set(point) <= {
                "timer_ns",
                "event",
                "receipt",
                "predicate",
            }:
                raise ValueError("decision point requires exactly one typed trigger")
            if "timer_ns" in point and (
                type(point["timer_ns"]) is not int or point["timer_ns"] <= 0
            ):
                raise ValueError("decision timer must be positive")
            if "event" in point and point["event"] not in self.events:
                raise ValueError("decision point event is ungranted")
            if "receipt" in point and point["receipt"] not in {
                "submitted",
                "accepted",
                "executing",
                "succeeded",
                "failed",
                "rejected",
                "canceled",
                "canceling",
            }:
                raise ValueError("unknown receipt decision point")
            if "predicate" in point:
                p = point["predicate"]
                if (
                    set(p) != {"entity", "field", "value"}
                    or FieldGrant(refs[p["entity"]], p["field"]) not in self.fields
                ):
                    raise ValueError(
                        "predicate requires granted entity/field and equality value"
                    )
                build.registry.validate(
                    build.registry.field(p["field"]).schema, p["value"]
                )
        self.budget = cfg["budget"]
        if set(self.budget) != {
            "wall_timeout_s",
            "max_rounds",
            "max_calls",
            "max_tokens",
            "max_retries",
            "max_prompt_bytes",
        }:
            raise ValueError("explicit decision budget fields required")
        for key, value in self.budget.items():
            if key == "wall_timeout_s":
                if type(value) not in (float, int) or not 0 < value <= 300:
                    raise ValueError("wall timeout must be positive and <=300s")
            elif type(value) is not int or value < (0 if key == "max_retries" else 1):
                raise ValueError("decision budgets must be bounded integers")
        self.provider = (
            provider
            if provider is not None
            else OpenAIProvider.from_config(cfg.get("provider", {}))
        )
        self.sequence = 0
        self.pending: deque[tuple[str, str]] = deque()
        self.commands_by_id: dict[str, tuple[str, str]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.predicate_previous: dict[int, bool | None] = {}
        self.instruction: str = cfg["instruction"]
        descriptor = build.registry.message(RECORD_SCHEMA)
        build.registry.validate(
            descriptor.schema,
            {
                "decision_id": "check",
                "phase": "observation",
                "code": None,
                "data_json": "{}",
            },
        )
        partition = Partition(
            build.id,
            build.id,
            consumes=tuple(
                Dependency(grant.field, grant.entity.type_id, grant.entity.id)
                for grant in self.fields
            ),
            emits=tuple(
                sorted({RECORD_SCHEMA} | {grant.schema for grant in tool_grants})
            ),
            subscribes=tuple(sorted({item["topic"] for item in grants["events"]})),
            message_targets=tuple(
                sorted({"agent-records"} | {grant.target for grant in tool_grants})
            ),
            relation_consumes=tuple(
                RelationDependency(relation) for relation in self.relations
            ),
            lifecycle_reads=tuple(sorted({ref.type_id for ref in build.entities})),
        )
        super().__init__(partition)

    def record(self, ctx: EngineContext, decision: str, phase: str, data: Any) -> None:
        cause = ctx.emit(
            RECORD_SCHEMA,
            {
                "decision_id": decision,
                "phase": phase,
                "code": data.get("code") if isinstance(data, dict) else None,
                "data_json": json.dumps(
                    data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ),
            },
            topic="agent-records",
        )
        ctx.inputs.append(cause)

    def bootstrap(self, ctx: EngineContext) -> None:
        for index, point in enumerate(self.points):
            if "timer_ns" in point:
                ctx.wake_at(point["timer_ns"], {"decision_point": index})

    def on_inputs(self, ctx: EngineContext) -> None:
        triggered = False
        for dirty in ctx.dirty:
            data = thaw(dirty.payload)
            if dirty.kind == "receipt":
                payload = cast(dict[str, Any], data)
                command_id, status = payload["command_id"], payload["status"]
                if status == "submitted":
                    self.commands_by_id[command_id] = self.pending.popleft()
                decision, call_id = self.commands_by_id[command_id]
                row = {**payload, "decision_id": decision, "call_id": call_id}
                self.receipts[command_id] = row
                self.record(ctx, decision, "receipt", row)
                triggered |= any(
                    point.get("receipt") == status for point in self.points
                )
            elif dirty.kind == "timer":
                triggered |= isinstance(data, dict) and "decision_point" in data
        triggered |= any(
            point.get("event") == delivery.message.schema_id
            for point in self.points
            for delivery in ctx.inbox
        )
        for index, point in enumerate(self.points):
            if "predicate" in point:
                p = point["predicate"]
                grant = next(
                    g
                    for g in self.fields
                    if g.entity.id == p["entity"] and g.field == p["field"]
                )
                from aerokernel.state import Absent

                value = ctx.get(grant.entity, grant.field)
                current = (
                    None
                    if isinstance(value, Absent)
                    else typed_equal(value, p["value"])
                )
                previous = self.predicate_previous.get(index)
                triggered |= previous is False and current is True
                self.predicate_previous[index] = current
        if triggered:
            self.decide(ctx)

    def decide(self, ctx: EngineContext) -> None:
        self.sequence += 1
        decision = f"{self.partition.id}/{self.sequence}"
        observation = build_observation(
            ctx, self.fields, self.relations, self.events, tuple(self.commands_by_id)
        )
        observation["receipts"] = list(self.receipts.values())
        self.record(ctx, decision, "observation", observation)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.instruction},
            {
                "role": "user",
                "content": json.dumps(observation, ensure_ascii=False, allow_nan=False),
            },
        ]
        deadline = time.monotonic() + self.budget["wall_timeout_s"]
        calls = tokens = retries = 0
        seen: set[str] = set()
        for round_index in range(self.budget["max_rounds"]):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.record(ctx, decision, "failure", {"code": "WALL_TIMEOUT"})
                return
            if tokens >= self.budget["max_tokens"]:
                self.record(ctx, decision, "failure", {"code": "TOKEN_BUDGET"})
                return
            if (
                len(json.dumps(messages, ensure_ascii=False).encode())
                > self.budget["max_prompt_bytes"]
            ):
                self.record(ctx, decision, "failure", {"code": "PROMPT_BUDGET"})
                return
            self.record(
                ctx,
                decision,
                "prompt",
                {
                    "round": round_index,
                    "messages": messages,
                    "tools": self.catalog.tools,
                    "model": self.provider.model,
                    "max_tokens": self.budget["max_tokens"] - tokens,
                },
            )
            try:
                response = self.provider.complete(
                    messages,
                    self.catalog.tools,
                    timeout_s=remaining,
                    max_tokens=self.budget["max_tokens"] - tokens,
                )
            except ProviderError as exc:
                self.record(
                    ctx, decision, "failure", {"code": exc.code, "message": str(exc)}
                )
                return
            self.record(ctx, decision, "response", response)
            if time.monotonic() > deadline:
                self.record(ctx, decision, "failure", {"code": "WALL_TIMEOUT"})
                return
            usage = response["usage"]
            used = usage.get("completion_tokens") if isinstance(usage, dict) else None
            if type(used) is not int or used < 0:
                self.record(ctx, decision, "failure", {"code": "USAGE_MISSING"})
                return
            tokens += used
            if tokens > self.budget["max_tokens"]:
                self.record(ctx, decision, "failure", {"code": "TOKEN_BUDGET"})
                return
            message = response["message"]
            messages.append(message)
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                self.record(ctx, decision, "failure", {"code": "NO_TOOL_CALLS"})
                return
            if calls + len(tool_calls) > self.budget["max_calls"]:
                self.record(ctx, decision, "failure", {"code": "CALL_BUDGET"})
                return
            rejected = False
            for call in tool_calls:
                calls += 1
                call_id = call.get("id") if isinstance(call, dict) else None
                try:
                    if (
                        not isinstance(call_id, str)
                        or not call_id
                        or call_id in seen
                        or call.get("type") != "function"
                    ):
                        raise ValueError("tool call needs unique id and function type")
                    seen.add(call_id)
                    function = call["function"]
                    grant, payload, summary = self.catalog.validate(
                        function["name"], function["arguments"]
                    )
                    if grant is not None:

                        def check_reference(ref: EntityRef) -> None:
                            if ref not in self.build.entities:
                                raise ValueError(
                                    "command reference outside declared session entity scope"
                                )
                            life = ctx.view.lifecycle(ref)
                            if life.removed is not None:
                                raise ValueError("command reference is removed")

                        self.build.registry.validate(
                            self.build.registry.message(grant.schema).schema,
                            payload,
                            check_reference,
                        )
                except (ValueError, KeyError, TypeError, KernelError) as exc:
                    self.record(
                        ctx,
                        decision,
                        "validation",
                        {
                            "call": call,
                            "valid": False,
                            "code": "TOOL_REJECTED",
                            "message": str(exc),
                        },
                    )
                    if not isinstance(call_id, str) or not call_id:
                        self.record(
                            ctx, decision, "failure", {"code": "MALFORMED_CALL_ID"}
                        )
                        return
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps(
                                {
                                    "status": "rejected",
                                    "code": "TOOL_REJECTED",
                                    "message": str(exc),
                                }
                            ),
                        }
                    )
                    rejected = True
                    continue
                self.record(
                    ctx,
                    decision,
                    "validation",
                    {"call": call, "valid": True, "decision_summary": summary},
                )
                if grant is not None:
                    self.record(
                        ctx,
                        decision,
                        "command",
                        {
                            "call_id": call_id,
                            "schema": grant.schema,
                            "target": grant.target,
                            "payload": payload,
                            "decision_summary": summary,
                        },
                    )
                    ctx.submit(
                        CommandRequest(grant.schema, grant.target, ctx.now, payload)
                    )
                    self.pending.append((decision, call_id))
                    result: dict[str, Any] = {"status": "proposed", "call_id": call_id}
                else:
                    if function["name"] == "wait":
                        ctx.wake_at(
                            ctx.now.ns + payload["duration_ns"],
                            {"decision_point": "wait"},
                        )
                    result = {"status": function["name"], "decision_summary": summary}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(result),
                    }
                )
            if not rejected:
                self.record(
                    ctx,
                    decision,
                    "finished",
                    {"calls": calls, "tokens": tokens, "rounds": round_index + 1},
                )
                return
            retries += 1
            if retries > self.budget["max_retries"]:
                self.record(ctx, decision, "failure", {"code": "RETRY_BUDGET"})
                return
        self.record(ctx, decision, "failure", {"code": "ROUND_BUDGET"})


def build(context: EngineBuild) -> Decision:
    return Decision(context)
