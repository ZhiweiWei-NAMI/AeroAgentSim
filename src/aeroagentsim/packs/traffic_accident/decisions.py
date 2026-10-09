"""Explicit stub decisions; typed proposals, never task or motion authority."""

from __future__ import annotations

import json
from typing import Any, cast

from aerokernel import Dependency, EntityRef, Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.packs.common import read
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario.paths import source_path

REPORT = "traffic.proposal.report"
BID = "traffic.proposal.bid"


class Decisions(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        cfg = build.config
        # Old scenarios may supply this annotation; fixture bytes are not pinned.
        if (
            set(cfg) - {"fixture_sha256"}
            != {
                "fixture_path",
                "task_id",
                "candidates",
                "latency_ns",
                "mode",
            }
            or cfg["mode"] != "stub"
        ):
            raise ValueError(
                "traffic decisions: select explicit stub fixture, candidates and latencies"
            )
        raw = source_path(cfg["fixture_path"]).read_bytes()
        self.fixture = json.loads(raw)
        if self.fixture["mode"] != "scripted":
            raise ValueError(
                "traffic decisions: fixture must declare scripted provenance"
            )
        self.refs = {ref.id: ref for ref in build.entities}
        self.task = self.refs[cfg["task_id"]]
        self.candidates = {
            self.refs[identity]: node for identity, node in cfg["candidates"].items()
        }
        self.latencies = cfg["latency_ns"]
        if set(self.latencies) != {"vehicle_report", *self.candidates.values()} or any(
            type(lag) is not int or lag <= 0 for lag in self.latencies.values()
        ):
            raise ValueError(
                "traffic decisions: positive authored latency for every node required"
            )
        self.responses: dict[str, dict[str, Any]] = {}
        for node in self.latencies:
            entries = self.fixture["responses"][node]
            if len(entries) != 1:
                raise ValueError(
                    f"traffic decisions: one explicit response required for {node}"
                )
            self.responses[node] = entries[0]
        self.pending: dict[str, tuple[str, dict[str, Any]]] = {}
        self.seen: set[tuple[str, str]] = set()
        super().__init__(
            Partition(
                build.id,
                build.id,
                consumes=tuple(
                    Dependency(f)
                    for f in (
                        "traffic.actor.current_task",
                        "traffic.task.interruptible",
                        "traffic.task.capture_altitude_m",
                    )
                ),
                emits=(REPORT, BID),
                subscribes=("traffic.incident.detected", "traffic.broadcast"),
                message_targets=(REPORT, BID),
            )
        )

    def _schedule(
        self,
        ctx: EngineContext,
        event: str,
        node: str,
        schema: str,
        payload: dict[str, Any],
    ) -> None:
        key = (event, node)
        if key in self.seen:
            return
        self.build.registry.validate(
            self.build.registry.message(schema).schema, payload
        )
        self.seen.add(key)
        identity = event + "/" + node
        self.pending[identity] = (schema, payload)
        ctx.wake_at(
            ctx.now.ns + self.latencies[node], {"decision": identity, "node": node}
        )

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            ctx.inputs = [delivery.dispatch_ref]
            message = delivery.message
            payload = cast(dict[str, Any], thaw(message.payload))
            if message.schema_id == "traffic.incident.detected":
                self._schedule(
                    ctx,
                    message.id,
                    "vehicle_report",
                    REPORT,
                    {
                        "actor": payload["actor"],
                        "incident": payload["incident"],
                        **self.responses["vehicle_report"],
                    },
                )
            elif message.schema_id == "traffic.broadcast":
                altitude = read(ctx, self.task, "traffic.task.capture_altitude_m")
                if altitude != self.fixture["input_expectations"]["capture_altitude_m"]:
                    raise ValueError(
                        "traffic decisions: fixture capture altitude mismatches actual task"
                    )
                for ref, node in sorted(
                    self.candidates.items(), key=lambda item: item[0].id
                ):
                    actual = read(ctx, ref, "traffic.actor.current_task")
                    current = EntityRef.from_data(cast(dict[str, Any], actual)["$ref"])
                    interruptible = read(ctx, current, "traffic.task.interruptible")
                    expected = self.fixture["input_expectations"][
                        node.removeprefix("uav_") + "_interruptible"
                    ]
                    if type(interruptible) is not bool or interruptible != expected:
                        raise ValueError(
                            f"traffic decisions: {node} fixture mismatches actual task lock"
                        )
                    self._schedule(
                        ctx,
                        message.id,
                        node,
                        BID,
                        {
                            "actor": {"$ref": ref.to_data()},
                            "task": {"$ref": self.task.to_data()},
                            **self.responses[node],
                            "input_cut": ctx.view.cut.index,
                            "decision_status": "succeeded",
                        },
                    )
            else:
                raise ValueError("traffic decisions: undeclared input event")
        for dirty in ctx.dirty:
            if dirty.kind != "timer":
                continue
            ctx.inputs = [dirty.cause]
            key = cast(dict[str, Any], thaw(dirty.payload))["decision"]
            schema, payload = self.pending.pop(key)
            ctx.emit(schema, payload, topic=schema)


def build(context: EngineBuild) -> Decisions:
    return Decisions(context)
