"""Bootstrap pinned route model inputs; no movement or task orchestration."""

from typing import Any

from aerokernel import EntityRef, Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import Value

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.packs.common import vec
from aeroagentsim.platform.plugins import EngineBuild


class RouteInputs(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        if set(build.config) != {"fields", "next_command"}:
            raise ValueError(
                "route_inputs.config: explicit fields and next_command required"
            )
        self.refs = {r.id: r for r in build.entities}
        fields = tuple(build.config["fields"])
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=fields,
                lifecycle=True,
                commands=(build.config["next_command"],),
            ),
            policies=policies(fields),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            command = ctx.remember(delivery, _payload)
            ref = EntityRef.from_data(command.payload["route"]["$ref"])
            if self.refs.get(ref.id) != ref:
                ctx.reject(command, {"reason": "route generation not configured"})
                continue
            index = command.payload["index"]
            points = ctx.get(ref, "traffic.route.points_enu_m")
            if (
                not isinstance(points, tuple)
                or type(index) is not int
                or not 0 <= index < len(points)
            ):
                ctx.reject(command, {"reason": "route index outside actual geometry"})
                continue
            ctx.accept(command)
            ctx.execute(command)
            ctx.succeed(
                command,
                {
                    "target": list(vec(points[index])),
                    "next_index": (index + 1) % len(points),
                },
            )


def _payload(value: Value) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("route command requires a typed record")
    return value


def build(context: EngineBuild) -> RouteInputs:
    return RouteInputs(context)
