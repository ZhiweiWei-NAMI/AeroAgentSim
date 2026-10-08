"""PX4/Gazebo telemetry and observed action outcomes, with authored field IDs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aerokernel import ClockMapping, EntityRef, KernelError, Partition, Stamp, Timing
from aerokernel.sdk import Command, EngineContext

if TYPE_CHECKING:
    from aeroagentsim.platform.plugins import EngineBuild

from .lockstep_base import LockstepEngine, integer, obj, records

FIELDS = {
    "position": "position_enu",
    "velocity": "velocity_enu",
    "attitude": "attitude_quat",
    "battery": "battery",
    "armed": "armed",
    "mode": "flight_mode",
    "landed": "landed_state",
    "freshness": "freshness",
}
ACTIONS = ("arm", "takeoff", "goto", "hold", "land", "disarm")


class PX4GazeboEngine(LockstepEngine):
    backend = "px4_gazebo"
    protocol = "aeroagentsim.px4/v1"
    quantum_key = "physics_step_ns"
    actions = ("arm", "takeoff", "goto_location", "hold", "land", "disarm")

    def __init__(self, context: EngineBuild) -> None:
        config = context.config
        self.fields = obj(config["fields"])
        if not self.fields or set(self.fields) - FIELDS.keys():
            raise ValueError(
                "fields must map supported telemetry names to declared field IDs"
            )
        self.refs: dict[str, EntityRef] = {}
        declared = {ref.id: ref for ref in context.entities}
        for entity, vehicle in obj(config["vehicles"]).items():
            ref = declared[entity]
            if vehicle in self.refs:
                raise ValueError("vehicle mapping must be one-to-one")
            if not set(self.fields.values()) <= set(context.owned_fields(ref)):
                raise ValueError(
                    "mapped fields require this partition's writer bindings"
                )
            self.refs[vehicle] = ref
        for semantic in ("position", "velocity", "attitude"):
            if semantic in self.fields:
                metadata = context.registry.field(self.fields[semantic]).metadata
                if str(metadata.get("frame", "")).lower() != "enu":
                    raise ValueError(
                        f"{semantic} descriptor must explicitly declare ENU frame"
                    )
        super().__init__(
            context,
            Partition(
                context.id,
                context.id,
                produces=tuple(self.fields.values()),
                lifecycle=True,
                commands=tuple(f"adapters.px4_gazebo.{action}" for action in ACTIONS),
                emits=("adapters.px4_gazebo.contact",),
                message_targets=(config["event_topic"],),
                timing=Timing(
                    "lockstep",
                    integer(config["step_ns"], "step_ns", 1),
                    certified_hold=True,
                ),
            ),
        )

    @property
    def pose_clock_mapping(self) -> ClockMapping:
        config = self.build.config
        return ClockMapping(
            config["pose_mapping_id"],
            config["pose_clock_id"],
            offset_ns=-integer(config["reset"]["warmup"], "warmup", 1),
        )

    def pose_stamp(self, ns: int) -> Stamp:
        config = self.build.config
        return Stamp(
            config["pose_clock_id"],
            ns + integer(config["reset"]["warmup"], "warmup", 1),
            1,
            config["pose_mapping_id"],
        )

    def hello(self) -> None:
        super().hello()
        models = self.capabilities["vehicles"]
        sensors = self.capabilities["sensors"]
        if not isinstance(models, list) or not isinstance(sensors, list):
            raise KernelError("ADAPTER_CAPABILITY", "invalid PX4 model/sensor catalog")
        if any(
            vehicle["model"] not in models
            for vehicle in records(self.build.config["reset"]["vehicles"])
        ):
            raise KernelError("ADAPTER_CAPABILITY", "configured PX4 model unavailable")
        if not {"gazebo_pose", "mavsdk_telemetry", "gazebo_contacts"} <= set(sensors):
            raise KernelError(
                "ADAPTER_CAPABILITY", "required native telemetry unavailable"
            )

    def check_reset(self, result: dict[str, Any]) -> None:
        if (
            integer(result["warmup_sim_ns"], "native warmup")
            != self.build.config["reset"]["warmup"]
        ):
            raise KernelError(
                "ADAPTER_CLOCK", "Gazebo origin differs from pinned warmup mapping"
            )
        if set(result["vehicles"]) != set(self.refs):
            raise KernelError(
                "ADAPTER_MAPPING", "reset vehicle inventory differs from entity mapping"
            )
        quantum = integer(result["physics_step_ns"], "actual physics quantum", 1)
        step = self.partition.timing.step_ns
        if step is None or step % quantum:
            raise KernelError(
                "ADAPTER_BOUNDARY", "actual physics step not communication-aligned"
            )

    def project(
        self, ctx: EngineContext, result: dict[str, Any], *, bootstrap: bool
    ) -> None:
        if bootstrap:
            for ref in sorted(self.refs.values(), key=lambda r: r.id):
                ctx.create(ref)
        samples = records(result["telemetry"])
        if len(samples) != len(self.refs) or {s["vehicle"] for s in samples} != set(
            self.refs
        ):
            raise KernelError(
                "ADAPTER_MAPPING", "incomplete or duplicate telemetry inventory"
            )
        for sample in sorted(samples, key=lambda s: s["vehicle"]):
            at = integer(sample["sim_ns"], "pose source time")
            if at != ctx.now.ns:
                raise KernelError("ADAPTER_TIME", "stale Gazebo pose at boundary")
            freshness = obj(sample["freshness"])
            if freshness["mavsdk_source_sim_ns"] is not None:
                raise KernelError(
                    "ADAPTER_CLOCK",
                    "unexpected MAVSDK source mapping; revise adapter contract",
                )
            for semantic, field in sorted(self.fields.items()):
                # Cached MAVSDK values are observations at this boundary. Their
                # native acquisition time is NOT known; freshness retains that fact.
                self.write(
                    ctx,
                    self.refs[sample["vehicle"]],
                    field,
                    sample[FIELDS[semantic]],
                    at,
                    source_stamp=self.pose_stamp(at)
                    if semantic in {"position", "attitude"}
                    else self.stamp(at),
                )
        if not bootstrap:
            for contact in records(result["contacts"]):
                self.emit(
                    ctx,
                    "contact",
                    contact,
                    contact["sim_ns"],
                    source_stamp=self.pose_stamp(
                        integer(contact["sim_ns"], "contact time")
                    ),
                )
            self.command_updates(ctx, result["command_updates"])

    def command_payload(
        self, command: Command[dict[str, Any]], backend_id: str
    ) -> dict[str, Any]:
        payload = dict(command.payload)
        entity = payload.pop("entity")
        vehicle = obj(self.build.config["vehicles"])[entity]
        action = command.delivery.message.schema_id.rsplit(".", 1)[1]
        if action == "goto":
            action = "goto_location"
        return {
            "command_id": backend_id,
            "vehicle": vehicle,
            "action": action,
            "params": payload,
        }


def build(context: EngineBuild) -> PX4GazeboEngine:
    return PX4GazeboEngine(context)
