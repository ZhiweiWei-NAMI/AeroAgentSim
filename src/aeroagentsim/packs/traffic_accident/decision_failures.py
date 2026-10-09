"""Bridge recorded graph failures into the demo's authored terminal branches."""

from __future__ import annotations

import json
from typing import Any, cast

from aerokernel import Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.agents.langgraph import RECORD_SCHEMA, RECORD_TOPIC
from aeroagentsim.platform.plugins import EngineBuild


class DecisionFailures(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        if build.config:
            raise ValueError("decision failure bridge has no configuration")
        self.triggers: dict[str, dict[str, Any]] = {}
        super().__init__(
            Partition(
                build.id,
                build.id,
                subscribes=(RECORD_TOPIC,),
                emits=("traffic.decision.failed",),
                message_targets=("traffic.decision.failed",),
            )
        )

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            if delivery.message.schema_id != RECORD_SCHEMA:
                continue
            ctx.inputs = [delivery.dispatch_ref]
            payload = cast(dict[str, Any], thaw(delivery.message.payload))
            data = json.loads(payload["data_json"])
            identity = payload["decision_id"]
            if payload["phase"] == "observation":
                self.triggers[identity] = data["initial"]["trigger"]
            elif payload["phase"] == "failure":
                trigger = self.triggers.pop(identity)
                failed = {
                    "incident": trigger["payload"]["incident"],
                    "request_id": identity,
                    "source_cut": ctx.view.cut.index,
                    "reason": f"{data['code']}: {data['message']}",
                }
                if "actor" in trigger["payload"]:
                    failed["actor"] = trigger["payload"]["actor"]
                ctx.emit(
                    "traffic.decision.failed", failed, topic="traffic.decision.failed"
                )
            elif payload["phase"] == "finished":
                del self.triggers[identity]


def build(context: EngineBuild) -> DecisionFailures:
    return DecisionFailures(context)
