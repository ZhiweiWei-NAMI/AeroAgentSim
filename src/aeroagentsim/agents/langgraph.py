"""Domain-neutral LangGraph decision engine with typed, causal WAL outputs."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable
from importlib import import_module
from importlib.metadata import version
from typing import Any, cast

from aerokernel import CommandRequest, Dependency, Partition
from aerokernel.codec import decode_record
from aerokernel.errors import KernelError
from aerokernel.journal import iter_records
from aerokernel.messages import Delivery, Emit
from aerokernel.relations import RelationDependency
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.services.projector import project

from .langgraph_client import JournalClient, ScriptedProvider, json_copy
from .observation import FieldGrant, build_observation
from .provider import OpenAIProvider, Provider, ProviderError

RECORD_SCHEMA = "aas.langgraph.record"
RECORD_TOPIC = "langgraph-records"


def delivery_route(ctx: EngineContext, delivery: Delivery) -> str:
    """Recover the actual route from this delivery's committed publication.

    Delivery.recipient names a partition, even for topic fanout. The event's
    origin points to its losslessly retained Emit proposal in both WAL modes.
    """
    message = delivery.message
    if message.kind == "command":
        return delivery.recipient
    origin = message.origin
    if origin is None or origin.record_index > ctx.view.cut.index:
        raise ValueError("event delivery has no visible committed publication")
    item = ctx.view._store.records.item(origin.record_index - 1, origin.item_index)
    proposal = decode_record(item["proposal"])
    if not isinstance(proposal, Emit) or proposal.schema_id != message.schema_id:
        raise ValueError("event delivery origin is not its Emit proposal")
    return proposal.target_or_topic


def record_descriptor() -> dict[str, Any]:
    """Scenario-owned portable event descriptor (no registry mutation)."""
    return {
        "id": RECORD_SCHEMA,
        "kind": "event",
        "schema": {
            "type": "record",
            "members": {
                "decision_id": {"type": "string"},
                "phase": {"type": "string"},
                "code": {"type": "string", "nullable": True},
                "data_json": {"type": "string"},
            },
            "required": ["decision_id", "phase", "code", "data_json"],
            "extra": False,
        },
    }


def factory_info(path: str) -> tuple[Callable[..., Any], dict[str, str]]:
    parts = path.split(":")
    if len(parts) != 2 or not all(parts):
        raise ValueError("graph factory must be importable module:function")
    module = import_module(parts[0])
    factory = getattr(module, parts[1])
    if not callable(factory):
        raise TypeError("graph factory must be callable")
    return factory, {
        "factory": path,
        "langgraph_version": version("langgraph"),
    }


async def invoke_graph(
    factory: Callable[..., Any],
    client: JournalClient,
    options: dict[str, Any],
    initial: dict[str, Any],
) -> dict[str, Any]:
    from langgraph.graph import StateGraph

    def compile_graph() -> Any:
        graph = factory(client, json_copy(options))
        if not isinstance(graph, StateGraph):
            raise TypeError("factory must return an uncompiled StateGraph")
        return graph.compile()

    # Construction/compilation belongs to the same wall deadline as node I/O.
    compiled = await asyncio.to_thread(compile_graph)
    result = await compiled.ainvoke(
        json_copy(initial), {"recursion_limit": client.budget["recursion_limit"]}
    )
    if not isinstance(result, dict) or not isinstance(result.get("outputs"), list):
        raise ProviderError("TOOL_REJECTED", "graph must return an outputs list")
    client.verify()
    return cast(dict[str, Any], json_copy(result))


def execute_graph(
    factory: Callable[..., Any],
    client: JournalClient,
    options: dict[str, Any],
    initial: dict[str, Any],
    *,
    offline: bool = False,
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        if offline:
            return await invoke_graph(factory, client, options, initial)
        return await asyncio.wait_for(
            invoke_graph(factory, client, options, initial),
            client.budget["wall_timeout_s"],
        )

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(run())
    finally:
        # Unlike asyncio.run, do not wait for a canceled synchronous node's
        # executor work. Its result has no kernel publication capability.
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()


class LangGraphDecision(ContextEngine):
    """Each dispatched trigger is an independent decision at its simulated instant."""

    def __init__(self, context: EngineBuild, provider: Provider | None = None) -> None:
        self.build = context
        cfg = context.config
        if set(cfg) != {
            "factory",
            "options",
            "triggers",
            "grants",
            "budget",
            "provider",
        }:
            raise ValueError(
                "langgraph requires factory/options/triggers/grants/budget/provider"
            )
        self.factory, self.pin = factory_info(cfg["factory"])
        self.cfg = cfg
        self.budget = cfg["budget"]
        required_budget = {
            "wall_timeout_s",
            "max_calls",
            "max_tokens",
            "max_retries",
            "max_prompt_bytes",
            "recursion_limit",
        }
        if set(self.budget) not in (
            required_budget | {"sim_deadline_ns"},
            required_budget | {"sim_timeout_ns"},
        ):
            raise ValueError("explicit graph budgets required")
        for key, value in self.budget.items():
            if key == "wall_timeout_s":
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 < value <= 300
                ):
                    raise ValueError(
                        "wall_timeout_s must be finite, positive and <=300"
                    )
            elif type(value) is not int or value < (
                0 if key in {"max_retries", "sim_deadline_ns"} else 1
            ):
                raise ValueError("graph budgets must be bounded integers")
        self.refs = {ref.id: ref for ref in context.entities}
        grants = cfg["grants"]
        if set(grants) != {"fields", "relations", "commands", "events", "facts"}:
            raise ValueError("grants require fields/relations/commands/events/facts")
        for group, keys in {
            "fields": {"entity", "field"},
            "facts": {"entity", "field"},
            "commands": {"schema", "target"},
            "events": {"schema", "topic"},
        }.items():
            if any(
                not isinstance(row, dict) or set(row) != keys for row in grants[group]
            ):
                raise ValueError(f"{group} grants require exact members {sorted(keys)}")
        self.fields = tuple(
            FieldGrant(self.refs[row["entity"]], row["field"])
            for row in grants["fields"]
        )
        self.relations = tuple(grants["relations"])
        for grant in self.fields:
            if not context.registry.is_a(
                grant.entity.type_id, context.registry.field(grant.field).declaring_type
            ):
                raise ValueError("field grant does not apply to entity")
        self.fact_grants = {(row["entity"], row["field"]) for row in grants["facts"]}
        if len(self.fact_grants) != len(grants["facts"]):
            raise ValueError("duplicate fact grants")
        for entity, field in self.fact_grants:
            if field not in context.owned_fields(self.refs[entity]):
                raise ValueError("fact grant requires this engine's owned field")
        self.event_grants = {(row["schema"], row["topic"]) for row in grants["events"]}
        self.command_grants = {
            (row["schema"], row["target"]) for row in grants["commands"]
        }
        self.triggers = cfg["triggers"]
        if not self.triggers:
            raise ValueError("at least one typed trigger required")
        topics: set[str] = set()
        commands: set[str] = set()
        for row in self.triggers:
            descriptor = context.registry.message(row["schema"])
            if descriptor.kind == "event" and set(row) == {"schema", "topic"}:
                topics.add(row["topic"])
            elif (
                descriptor.kind == "command"
                and set(row) == {"schema", "target"}
                and row["target"] == context.id
            ):
                commands.add(row["schema"])
            else:
                raise ValueError(
                    "trigger requires event schema/topic or command schema/engine target"
                )
        for schema, _ in self.event_grants:
            if context.registry.message(schema).kind != "event":
                raise ValueError("event output grant requires event schema")
        for schema, _ in self.command_grants:
            if context.registry.message(schema).kind != "command":
                raise ValueError("command output grant requires command schema")
        context.registry.validate(
            context.registry.message(RECORD_SCHEMA).schema,
            {
                "decision_id": "check",
                "phase": "failure",
                "code": None,
                "data_json": "{}",
            },
        )
        pcfg = cfg["provider"]
        if provider is not None:
            self.provider = provider
        elif pcfg["mode"] == "stub" and set(pcfg) == {"mode", "model", "responses"}:
            self.provider = ScriptedProvider(pcfg["model"], pcfg["responses"])
        elif pcfg["mode"] == "live" and set(pcfg) == {"mode", "profile"}:
            self.provider = OpenAIProvider.from_config(
                {k: v for k, v in pcfg.items() if k != "mode"}
            )
        else:
            raise ValueError("provider mode must be explicit stub or live")
        self.sequence = 0
        produces = tuple(sorted({field for _, field in self.fact_grants}))
        super().__init__(
            Partition(
                context.id,
                context.id,
                produces=produces,
                consumes=tuple(
                    Dependency(g.field, g.entity.type_id, g.entity.id)
                    for g in self.fields
                ),
                relation_consumes=tuple(RelationDependency(r) for r in self.relations),
                emits=tuple(
                    sorted(
                        {RECORD_SCHEMA}
                        | {s for s, _ in self.event_grants | self.command_grants}
                    )
                ),
                commands=tuple(sorted(commands)),
                subscribes=tuple(sorted(topics)),
                message_targets=tuple(
                    sorted(
                        {RECORD_TOPIC}
                        | {t for _, t in self.event_grants | self.command_grants}
                    )
                ),
                lifecycle=True,
                lifecycle_reads=tuple(sorted({r.type_id for r in context.entities})),
            ),
            policies=policies(produces),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def record(
        self, ctx: EngineContext, decision: str, phase: str, data: dict[str, Any]
    ) -> None:
        cause = ctx.emit(
            RECORD_SCHEMA,
            {
                "decision_id": decision,
                "phase": phase,
                "code": data.get("code"),
                "data_json": json.dumps(
                    data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ),
            },
            topic=RECORD_TOPIC,
        )
        ctx.inputs.append(cause)

    def validate_outputs(self, ctx: EngineContext, outputs: list[Any]) -> None:
        facts: set[tuple[str, str]] = set()

        def check_ref(ref: Any) -> None:
            if (
                ref not in self.build.entities
                or ctx.view.lifecycle(ref).removed is not None
            ):
                raise ValueError("output references entity outside live session scope")

        for row in outputs:
            if not isinstance(row, dict):
                raise TypeError("output must be a typed mapping")
            kind = row["kind"]
            if kind in {"event", "command"}:
                route = "topic" if kind == "event" else "target"
                if set(row) != {"kind", "schema", route, "payload"}:
                    raise ValueError("invalid message output members")
                permitted = (
                    self.event_grants if kind == "event" else self.command_grants
                )
                if (row["schema"], row[route]) not in permitted:
                    raise ValueError("ungranted output schema or destination")
                descriptor = self.build.registry.message(row["schema"])
                self.build.registry.validate(
                    descriptor.schema, row["payload"], check_ref
                )
            elif kind == "fact":
                if set(row) != {"kind", "entity", "field", "value"}:
                    raise ValueError("invalid fact output members")
                key = (row["entity"], row["field"])
                if key not in self.fact_grants or key in facts:
                    raise ValueError("unowned or duplicate fact output")
                facts.add(key)
                check_ref(self.refs[row["entity"]])
                self.build.registry.validate(
                    self.build.registry.field(row["field"]).schema,
                    row["value"],
                    check_ref,
                )
            else:
                raise ValueError("unknown output kind")

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            message = delivery.message
            if message.kind not in {"event", "command"} or not any(
                row["schema"] == message.schema_id for row in self.triggers
            ):
                continue
            route = delivery_route(ctx, delivery)
            route_key = "topic" if message.kind == "event" else "target"
            if not any(
                row["schema"] == message.schema_id and row.get(route_key) == route
                for row in self.triggers
            ):
                continue
            self.sequence += 1
            decision = f"{self.partition.id}/{self.sequence}"
            command = (
                ctx.remember(delivery, lambda value: value)
                if message.kind == "command"
                else None
            )
            if command is not None:
                ctx.accept(command)
                ctx.execute(command)
            initial = {
                "observation": build_observation(
                    ctx,
                    self.fields,
                    self.relations,
                    tuple(row["schema"] for row in self.triggers if "topic" in row),
                    (),
                ),
                "trigger": {
                    "id": message.id,
                    "schema": message.schema_id,
                    route_key: route,
                    "payload": thaw(message.payload),
                    "at_ns": message.at.ns,
                },
            }
            budget = dict(self.budget)
            if "sim_timeout_ns" in budget:
                budget["sim_deadline_ns"] = ctx.now.ns + budget.pop("sim_timeout_ns")
            invocation = {
                "initial": initial,
                "options": self.cfg["options"],
                "pin": self.pin,
                "budget": budget,
                "model": self.provider.model,
            }
            self.record(ctx, decision, "observation", invocation)
            client = JournalClient(self.provider, self.provider.model, budget)
            result: dict[str, Any] | None = None
            failure: dict[str, Any] | None = None
            try:
                if ctx.now.ns > budget["sim_deadline_ns"]:
                    raise ProviderError(
                        "SIM_TIMEOUT", "absolute simulated deadline expired"
                    )
                result = execute_graph(
                    self.factory, client, self.cfg["options"], initial
                )
                self.validate_outputs(ctx, result["outputs"])
                if command is not None:
                    descriptor = self.build.registry.message(message.schema_id)
                    if descriptor.result_schema is not None:
                        if "receipt" not in result:
                            raise ValueError(
                                "command trigger requires its typed receipt result"
                            )
                        self.build.registry.validate(
                            descriptor.result_schema, result["receipt"]
                        )
            except (ValueError, KeyError, TypeError, KernelError) as exc:
                failure = {"code": "TOOL_REJECTED", "message": str(exc)}
            except TimeoutError as exc:
                failure = {"code": "WALL_TIMEOUT", "message": str(exc)}
            except ProviderError as exc:
                failure = {"code": exc.code, "message": str(exc)}
            except Exception as exc:  # noqa: BLE001 -- plugin faults become explicit failure events
                failure = {"code": "GRAPH_ERROR", "message": str(exc)}
            for row in client.records:
                self.record(ctx, decision, "model_call", row)
                if "rejection" in row:
                    self.record(ctx, decision, "validation", row["rejection"])
            if failure is not None:
                if result is not None:
                    failure["result"] = result
                self.record(ctx, decision, "failure", failure)
                if command is not None:
                    ctx.fail(command)
                continue
            assert result is not None
            for output in result["outputs"]:
                self.record(ctx, decision, "output", output)
                if output["kind"] == "event":
                    ctx.emit(output["schema"], output["payload"], topic=output["topic"])
                elif output["kind"] == "command":
                    ctx.submit(
                        CommandRequest(
                            output["schema"],
                            output["target"],
                            ctx.now,
                            output["payload"],
                        )
                    )
                else:
                    ctx.set(
                        self.refs[output["entity"]], output["field"], output["value"]
                    )
            self.record(ctx, decision, "finished", {"result": result})
            if command is not None:
                ctx.succeed(command, result.get("receipt"))


def journal_decisions(data: bytes) -> dict[str, list[dict[str, Any]]]:
    """Read graph transcripts from the canonical WAL, never a sidecar log."""
    result: dict[str, list[dict[str, Any]]] = {}
    for record in iter_records(data):
        if record.get("type") == "header":
            continue
        for message in project(record)["messages"]:
            if message["schemaId"] == RECORD_SCHEMA:
                payload = message["payload"]
                result.setdefault(payload["decision_id"], []).append(
                    {
                        "phase": payload["phase"],
                        "data": json.loads(payload["data_json"]),
                    }
                )
    return result


def replay_graph(data: bytes, decision: str) -> dict[str, Any]:
    """Re-execute a graph with matched requests and ZERO I/O; preserve failures."""
    rows = journal_decisions(data)[decision]
    invocation = next(row["data"] for row in rows if row["phase"] == "observation")
    factory, _metadata = factory_info(invocation["pin"]["factory"])
    expected = next(
        (row["data"]["result"] for row in rows if row["phase"] == "finished"), None
    )
    failure = next((row["data"] for row in rows if row["phase"] == "failure"), None)
    calls = [row["data"] for row in rows if row["phase"] == "model_call"]
    if failure is not None and failure["code"] == "SIM_TIMEOUT":
        if invocation["initial"]["observation"]["valid_at"]["ns"] is None:
            raise ProviderError("REPLAY_MISMATCH", "missing simulated instant")
        if (
            int(invocation["initial"]["observation"]["valid_at"]["ns"])
            <= invocation["budget"]["sim_deadline_ns"]
        ):
            raise ProviderError("REPLAY_MISMATCH", "simulated deadline differs")
        return {"failure": failure}
    if expected is None and (
        failure is None or (failure["code"] == "WALL_TIMEOUT" and not calls)
    ):
        raise ProviderError(
            "REPLAY_INCOMPLETE",
            "graph interrupted before a model boundary; use kernel WAL replay",
        )
    client = JournalClient(
        None, invocation["model"], invocation["budget"], replay=calls
    )
    try:
        result = execute_graph(
            factory, client, invocation["options"], invocation["initial"], offline=True
        )
    except ProviderError as exc:
        if failure is None or exc.code != failure["code"]:
            raise ProviderError("REPLAY_MISMATCH", "graph failure changed") from exc
        try:
            client.verify()
        except ProviderError as verification:
            if verification.code != exc.code:
                raise
        return {"failure": failure}
    if failure is not None:
        if result != failure.get("result"):
            raise ProviderError("REPLAY_MISMATCH", "rejected graph result changed")
        return {"failure": failure}
    if result != expected:
        raise ProviderError("REPLAY_MISMATCH", "graph result changed")
    return result


def build(context: EngineBuild) -> LangGraphDecision:
    return LangGraphDecision(context)
