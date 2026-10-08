"""ns-3 mobility coupling and packet outcomes retain native occurrence times."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aerokernel import (
    Dependency,
    EntityRef,
    Fact,
    Instant,
    KernelError,
    Partition,
    Timing,
)
from aerokernel.sdk import Command, EngineContext
from aerokernel.values import thaw

if TYPE_CHECKING:
    from aeroagentsim.platform.plugins import EngineBuild

from .lockstep_base import LockstepEngine, integer, obj, records


class NS3Engine(LockstepEngine):
    backend = "ns3"
    protocol = "aeroagentsim.ns3/v1"
    quantum_key = "time_quantum_ns"
    actions = ("send",)

    def __init__(self, context: EngineBuild) -> None:
        config = context.config
        self.mobility = records(config["mobility"])
        self.refs = {ref.id: ref for ref in context.entities}
        self.lag_ns = integer(config["consumer_lag_ns"], "consumer lag", 1)
        step_ns = integer(config["step_ns"], "step_ns", 1)
        if self.lag_ns < step_ns:
            raise ValueError(
                "mobility lag must cover the whole native interval (lag >= step)"
            )
        consumes: list[Dependency] = []
        for item in self.mobility:
            ref = self.refs[item["entity"]]
            field = item["field"]
            if (
                str(context.registry.field(field).metadata.get("frame", "")).lower()
                != "enu"
            ):
                raise ValueError(
                    "mobility descriptor must explicitly declare ENU frame"
                )
            consumes.append(Dependency(field, ref.type_id, ref.id, self.lag_ns))
        super().__init__(
            context,
            Partition(
                context.id,
                context.id,
                consumes=tuple(consumes),
                commands=("adapters.ns3.send",),
                emits=tuple(
                    f"adapters.ns3.{event}" for event in ("delivery", "drop", "link")
                ),
                message_targets=(config["event_topic"],),
                timing=Timing("lockstep", step_ns, certified_hold=True),
            ),
        )
        self.packet_commands: dict[str, str] = {}

    def check_reset(self, result: dict[str, Any]) -> None:
        configured = {n["id"] for n in records(obj(result["configuration"])["nodes"])}
        if any(item["node"] not in configured for item in self.mobility):
            raise KernelError(
                "ADAPTER_MAPPING", "mobility node absent from native configuration"
            )

    def advance_payload(self, ctx: EngineContext) -> dict[str, Any]:
        updates = []
        at = ctx.now.ns - self.lag_ns
        # Bootstrap native positions are explicitly authored reset inputs. The
        # first lag interval retains them; no unavailable read becomes zero.
        if at >= 0:
            for item in self.mobility:
                ref: EntityRef = self.refs[item["entity"]]
                fact = ctx.view.field((ref, item["field"]), Instant(at))
                if not isinstance(fact, Fact):
                    raise KernelError(
                        "ADAPTER_MOBILITY", "no real position at declared consumer lag"
                    )
                ctx.inputs.append(fact.version)
                updates.append(
                    {
                        "node": item["node"],
                        "sim_ns": self.confirmed_ns,
                        "position": thaw(fact.value),
                    }
                )
        return {"to_sim_ns": ctx.now.ns, "mobility": updates}

    def project(
        self, ctx: EngineContext, result: dict[str, Any], *, bootstrap: bool
    ) -> None:
        if bootstrap:
            return
        for event, key, time_key in (
            ("delivery", "deliveries", "received_ns"),
            ("drop", "drops", "sim_ns"),
        ):
            for record in records(result[key]):
                if integer(record["available_sim_ns"], "availability") != ctx.now.ns:
                    raise KernelError(
                        "ADAPTER_TIME", "packet availability differs from boundary"
                    )
                backend_id = self.packet_commands[record["packet_id"]]
                command = self.pending[backend_id]
                self.emit(ctx, event, record, record[time_key])
                outcome = {**record, "available_ns": ctx.now.ns}
                self.validate_result(command, outcome)
                if event == "delivery":
                    ctx.succeed(command, outcome)
                else:
                    ctx.fail(command, outcome)
                del self.pending[backend_id]
                del self.statuses[backend_id]
                del self.packet_commands[record["packet_id"]]
        for record in records(result["link_stats"]):
            self.emit(ctx, "link", record, record["sim_ns"])

    def command_payload(
        self, command: Command[dict[str, Any]], backend_id: str
    ) -> dict[str, Any]:
        return {"action": "send", "params": command.payload}

    def check_accept(
        self, result: dict[str, Any], backend_id: str, command: Command[dict[str, Any]]
    ) -> None:
        packet_id = command.payload["packet_id"]
        if (
            result["packet_id"] != packet_id
            or integer(result["sim_ns"], "send time") != self.confirmed_ns
        ):
            raise KernelError("ADAPTER_COMMAND", "native send identity/time mismatch")
        if packet_id in self.packet_commands:
            raise KernelError("ADAPTER_COMMAND", "packet identity reused")
        self.packet_commands[packet_id] = backend_id


def build(context: EngineBuild) -> NS3Engine:
    return NS3Engine(context)
