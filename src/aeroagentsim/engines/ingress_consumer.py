"""A generic live command consumer with explicit recorded result receipts."""

from __future__ import annotations

from aerokernel import Partition, Timing
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario.loader import contract, text


class IngressConsumer(ContextEngine):
    """Echo actual supplied payloads as results, without lifecycle authority.

    Independent instances have no shared state or routes. Their execution
    receipts expose early service while a different stream still blocks sealing.
    The authored command result schema must accept the supplied payload schema.
    """

    def __init__(self, build: EngineBuild) -> None:
        path = f"engines.{build.id}.config"
        cfg = contract(build.config, path, {"command"})
        command = text(cfg["command"], path + ".command")
        descriptor = build.registry.message(command)
        if descriptor.kind != "command" or descriptor.result_schema is None:
            raise ValueError(path + ".command: command with a result schema required")
        if descriptor.result_schema != descriptor.schema:
            raise ValueError(path + ".command: payload and result schemas must match")
        super().__init__(
            Partition(
                build.id, build.id, commands=(command,), timing=Timing("real_time")
            )
        )

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            command = ctx.remember(delivery, lambda value: value)
            ctx.accept(command)
            ctx.execute(command)
            ctx.succeed(command, thaw(delivery.message.payload))


def build(context: EngineBuild) -> IngressConsumer:
    return IngressConsumer(context)
