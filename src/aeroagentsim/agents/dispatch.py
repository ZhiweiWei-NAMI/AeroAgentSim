"""Example command-driven assignment workflow composed with physical engines.

All entity types, state slots, command schemas and routing are scenario inputs.
"""

from __future__ import annotations

from collections import deque
from typing import Any, cast

from aerokernel import CommandRequest, EntityRef, Partition
from aerokernel.sdk import Command, ContextEngine, EngineContext
from aerokernel.state import Absent
from aerokernel.values import thaw

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform.plugins import EngineBuild


class AssignmentWorkflow(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        self.cfg = build.config
        self.refs = {ref.id: ref for ref in build.entities}
        self.assignments: dict[str, tuple[str, Command[Any]]] = {}
        self.pending: deque[str] = deque()
        self.child_orders: dict[str, str] = {}
        fields = (
            self.cfg["state_field"],
            self.cfg["availability_field"],
            self.cfg["destination_field"],
        )
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=fields,
                commands=(self.cfg["assign_schema"],),
                emits=(self.cfg["move_schema"],),
                subscribes=(self.cfg["arrival_topic"], self.cfg["acceptance_topic"]),
                message_targets=(self.cfg["motion_target"],),
                lifecycle=True,
            ),
            policies=policies(fields),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def known(self, ctx: EngineContext, ref: EntityRef, field: str) -> Any:
        value = ctx.get(ref, field)
        if isinstance(value, Absent):
            raise TypeError(f"assignment requires produced {ref.id}.{field}")
        return thaw(value)

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            if delivery.message.kind == "command":
                command = ctx.remember(delivery, lambda value: value)
                data = cast(dict[str, Any], command.payload)
                order = self.refs[data["entity"]]
                resource = self.refs[data["resource"]]
                if (
                    order.id in self.assignments
                    or self.known(ctx, order, self.cfg["state_field"]) != "submitted"
                    or not self.known(ctx, resource, self.cfg["availability_field"])
                    or any(
                        resource.id == selected
                        for selected, _ in self.assignments.values()
                    )
                ):
                    ctx.reject(command, {"reason": "order or resource unavailable"})
                    continue
                if data["target"] != self.known(
                    ctx, order, self.cfg["destination_field"]
                ):
                    ctx.reject(
                        command, {"reason": "destination differs from authored order"}
                    )
                    continue
                ctx.accept(command)
                ctx.execute(command)
                ctx.set(order, self.cfg["state_field"], "executing")
                ctx.set(resource, self.cfg["availability_field"], False)
                self.assignments[order.id] = (resource.id, command)
                ctx.submit(
                    CommandRequest(
                        self.cfg["move_schema"],
                        self.cfg["motion_target"],
                        ctx.now,
                        {
                            "entity": resource.id,
                            "machine": order.id,
                            "target": data["target"],
                            "subject": {"$ref": resource.to_data()},
                        },
                    )
                )
                self.pending.append(order.id)
            elif delivery.message.kind == "event":
                event = cast(dict[str, Any], thaw(delivery.message.payload))
                identity = event["machine"]
                if identity not in self.assignments:
                    raise ValueError(
                        "assignment received outcome for an unassigned order"
                    )
                order = self.refs[identity]
                resource_id, command = self.assignments[identity]
                if delivery.message.schema_id == self.cfg["arrival_schema"]:
                    ctx.set(order, self.cfg["state_field"], "awaiting_acceptance")
                elif delivery.message.schema_id == self.cfg["acceptance_schema"]:
                    ctx.set(order, self.cfg["state_field"], "accepted")
                    ctx.set(
                        self.refs[resource_id], self.cfg["availability_field"], True
                    )
                    ctx.succeed(command, {"entity": identity})
                    del self.assignments[identity]
        for dirty in ctx.dirty:
            if dirty.kind != "receipt":
                continue
            row = cast(dict[str, Any], thaw(dirty.payload))
            if row["status"] == "submitted":
                self.child_orders[row["command_id"]] = self.pending.popleft()
            elif row["status"] in {"rejected", "failed", "canceled"}:
                identity = self.child_orders[row["command_id"]]
                resource_id, command = self.assignments.pop(identity)
                ctx.set(self.refs[identity], self.cfg["state_field"], "failed")
                ctx.set(self.refs[resource_id], self.cfg["availability_field"], True)
                ctx.fail(command, {"reason": f"physical action {row['status']}"})


def build(context: EngineBuild) -> AssignmentWorkflow:
    return AssignmentWorkflow(context)
