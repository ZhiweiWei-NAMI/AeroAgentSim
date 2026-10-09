"""A generic stream observation engine driven only by supplied telemetry."""

from __future__ import annotations

from aerokernel import Partition, Timing
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario.loader import contract, text

from .common import bootstrap_owned, policies


class Telemetry(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        cfg = contract(
            build.config, f"engines.{build.id}.config", {"entity", "field", "command"}
        )
        self.entity = {ref.id: ref for ref in build.entities}[
            text(cfg["entity"], "telemetry.entity")
        ]
        self.field = text(cfg["field"], "telemetry.field")
        command = text(cfg["command"], "telemetry.command")
        if build.writers(self.entity).get(self.field) != build.id:
            raise ValueError("telemetry.field: engine must be the declared writer")
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=(self.field,),
                commands=(command,),
                lifecycle=True,
                timing=Timing("real_time"),
            ),
            policies=policies((self.field,)),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            command = ctx.remember(delivery, lambda value: value)
            payload = thaw(delivery.message.payload)
            if not isinstance(payload, dict):
                raise TypeError("telemetry command requires a value mapping")
            ctx.accept(command)
            ctx.execute(command)
            ctx.set(
                self.entity,
                self.field,
                payload["value"],
                acquired=delivery.message.source_stamp,
            )
            ctx.succeed(command)


def build(context: EngineBuild) -> Telemetry:
    return Telemetry(context)
