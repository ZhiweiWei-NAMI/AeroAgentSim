"""PX4/Gazebo telemetry and observed action outcomes, with authored field IDs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from typing import TYPE_CHECKING, Any

from aerokernel import ClockMapping, EntityRef, KernelError, Partition, Stamp, Timing
from aerokernel.messages import Delivery
from aerokernel.sdk import Command, EngineContext
from aerokernel.values import thaw

if TYPE_CHECKING:
    from aeroagentsim.platform.plugins import EngineBuild

from .lockstep_base import (
    LEGACY_OPERATIONS,
    NATIVE_OPERATIONS,
    LockstepEngine,
    integer,
    obj,
    records,
    string_set,
)
from .schemas import (
    capture_response_schema,
    payload_ack_schema,
    payload_state_schema,
    receiver_receipt_schema,
    vehicle_estimate_schema,
)

FIELDS = {
    "position": "position_enu",
    "velocity": "velocity_enu",
    "attitude": "attitude_quat",
    "battery": "battery",
    "armed": "armed",
    "mode": "flight_mode",
    "landed": "landed_state",
    "freshness": "freshness",
    "estimate": "vehicle_estimate",
}
ACTIONS = ("arm", "takeoff", "goto", "hold", "land", "disarm")


class PX4GazeboEngine(LockstepEngine):
    backend = "px4_gazebo"
    protocol = "aeroagentsim.px4/v1"
    quantum_key = "physics_step_ns"
    actions = ("arm", "takeoff", "goto_location", "hold", "land", "disarm")
    hello_minors = (0, 1)
    version = "e1/2"

    def __init__(self, context: EngineBuild) -> None:
        config = context.config
        self.fields = obj(config["fields"])
        if not self.fields or set(self.fields) - FIELDS.keys():
            raise ValueError(
                "fields must map supported telemetry names to declared field IDs"
            )
        enabled = string_set(config.get("native_operations", []), "configured native operation")
        if enabled - NATIVE_OPERATIONS:
            raise ValueError("native_operations supports only cancel, payload and capture")
        reset = obj(config["reset"])
        for key, operation in (("rgbd_camera", "capture"), ("payload", "payload")):
            if key in reset:
                obj(reset[key])
                enabled = enabled | {operation}
        self.required_operations = LEGACY_OPERATIONS | enabled
        features = set()
        for operation, feature in (("cancel", "observed_cancel_hold"), ("payload", "payload_contact_receipts"), ("capture", "rgbd_camera")):
            if operation in enabled:
                features.add(feature)
        if "estimate" in self.fields or "altitude_datum" in reset:
            features.add("source_clock_estimates")
        if "capture" in enabled:
            features.add("source_clock_estimates")
        self.required_features = frozenset(features)
        self.native_epoch: str | None = None
        self.native_run_id: str | None = None
        self.estimate_times: dict[str, tuple[int, int]] = {}
        self.payload_state: dict[str, Any] | None = None
        self.release: dict[str, Any] | None = None
        self.release_attempt: dict[str, Any] | None = None
        self.release_ack: dict[str, Any] | None = None
        self.detached_ns: int | None = None
        self.receipt_ids: set[str] = set()
        # Bounded correlation metadata only; frame bytes live in recorded events,
        # never in an externally consumed mutable native cache.
        self.frames: dict[tuple[str, str], dict[str, Any]] = {}
        self.frame_times: dict[str, int] = {}
        commands = [f"adapters.px4_gazebo.{action}" for action in ACTIONS]
        emits = ["adapters.px4_gazebo.contact"]
        if "cancel" in enabled:
            commands.extend(f"adapters.px4_gazebo.{action}_cancel_v1" for action in ACTIONS)
        if "payload" in enabled:
            commands.extend(f"adapters.px4_gazebo.{action}_v1" for action in ("payload_release", "payload_status"))
            emits.extend(f"adapters.px4_gazebo.{event}_v1" for event in ("payload_state", "receiver_receipt", "payload_release_ack"))
        if "capture" in enabled:
            commands.extend(f"adapters.px4_gazebo.{action}_v1" for action in ("capture_poll", "capture_read"))
            emits.extend(f"adapters.px4_gazebo.{event}_v1" for event in ("capture_frame", "capture_chunk"))
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
        for semantic in ("position", "velocity", "attitude", "estimate"):
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
                commands=tuple(commands),
                emits=tuple(emits),
                message_targets=(config["event_topic"],),
                timing=Timing(
                    "lockstep",
                    integer(config["step_ns"], "step_ns", 1),
                    certified_hold=True,
                ),
            ),
        )
        self.control_step_ns = integer(
            config.get("control_step_ns", config["step_ns"]), "control_step_ns", 1
        )
        if config["step_ns"] % self.control_step_ns:
            raise ValueError("control step must divide the communication step")

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

    def check_capabilities(self) -> None:
        if not {"vehicles", "sensors"} <= set(self.capabilities):
            raise KernelError("ADAPTER_CAPABILITY", "missing PX4 model/sensor catalog")
        models = string_set(self.capabilities["vehicles"], "PX4 model")
        sensors = string_set(self.capabilities["sensors"], "PX4 sensor")
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
        if self.required_features:
            self.native_epoch = result.get("clock_epoch")
            if type(self.native_epoch) is not str or not self.native_epoch:
                raise KernelError("ADAPTER_IDENTITY", "native reset epoch required")
        if "source_clock_estimates" in self.required_features:
            self.native_run_id = result.get("run_id")
            if type(self.native_run_id) is not str or not self.native_run_id:
                raise KernelError("ADAPTER_IDENTITY", "native reset run identity required")
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
        if step is None or step % quantum or self.control_step_ns % quantum:
            raise KernelError(
                "ADAPTER_BOUNDARY", "actual physics step not communication-aligned"
            )

    def advance_native(self, ctx: EngineContext) -> dict[str, Any]:
        """Couple PX4 at fine native barriers, publish once at the granted boundary.

        A large Gazebo multi_step can outrun asynchronous PX4 feedback. Native
        contacts and command updates retain their original times; only their
        availability and the final telemetry use the communication boundary.
        """
        boundaries = range(
            self.confirmed_ns + self.control_step_ns,
            ctx.now.ns + 1,
            self.control_step_ns,
        )
        if not boundaries or boundaries[-1] != ctx.now.ns:
            raise KernelError(
                "ADAPTER_BOUNDARY", "control step misses granted boundary"
            )
        collected: dict[str, list[dict[str, Any]]] = {"contacts": [], "command_updates": []}
        if self.build.config["reset"].get("payload") is not None:
            collected.update(payload_events=[], receiver_receipts=[])
        result: dict[str, Any]
        lower = self.confirmed_ns
        for at in boundaries:
            result = self.request("advance", {"to_sim_ns": at})
            if integer(result["reached_sim_ns"], "native frontier") != at:
                raise KernelError("ADAPTER_TIME", "native control step missed frontier")
            for key, entries in collected.items():
                for entry in records(result[key]):
                    time_key = {"payload_events": "observed_sim_ns", "receiver_receipts": "contact_end_sim_ns"}.get(key, "sim_ns")
                    when = integer(entry[time_key], "native event time")
                    if when > at or (key in {"command_updates", "payload_events"} and when < lower):
                        raise KernelError("ADAPTER_TIME", "native event outside control frontier")
                    entries.append(entry)
            for key in ("payload_events", "receiver_receipts"):
                if key not in collected and result.get(key):
                    raise KernelError("ADAPTER_CAPABILITY", "unconfigured native payload events")
            lower = at
        return {**result, **collected}

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
                # Only the explicitly negotiated estimate has a known mapped
                # acquisition time. Cached legacy telemetry stays boundary-time.
                source_at = at
                if semantic == "estimate":
                    source_at = self.check_estimate(sample[FIELDS[semantic]], sample["vehicle"], at, track=True)
                self.write(
                    ctx,
                    self.refs[sample["vehicle"]],
                    field,
                    sample[FIELDS[semantic]],
                    source_at,
                    source_stamp=self.pose_stamp(at)
                    if semantic in {"position", "attitude"}
                    else self.stamp(source_at),
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
        if self.build.config["reset"].get("payload") is not None:
            self.payload_observations(ctx, result)

    def check_estimate(
        self, value: object, vehicle: str, frontier: int, *, track: bool = False,
        target: int | None = None, max_age_ns: int = 250_000_000,
    ) -> int:
        self.build.registry.validate(vehicle_estimate_schema(), value)
        estimate = obj(value)
        if (estimate["uav_id"] != vehicle or estimate["run_id"] != self.native_run_id
                or estimate["clock_epoch"] != self.native_epoch
                or estimate["available_sim_ns"] != frontier):
            raise KernelError("ADAPTER_IDENTITY", "estimate namespace/frontier mismatch")
        common = {"status", "source", "run_id", "uav_id", "clock_epoch", "source_sim_ns", "available_sim_ns"}
        if estimate["status"] == "unavailable":
            if set(estimate) != common | {"reason"} or estimate["source_sim_ns"] is not None:
                raise KernelError("ADAPTER_SHAPE", "invalid unavailable estimate")
            return frontier  # availability of absence, never an invented source time
        required = set(vehicle_estimate_schema()["members"]) - {"reason"}
        if set(estimate) != required or estimate["source_sim_ns"] is None:
            raise KernelError("ADAPTER_SHAPE", "incomplete source-clock estimate")
        source, attitude = estimate["source_sim_ns"], estimate["attitude_source_sim_ns"]
        target = frontier if target is None else target
        if source > target or attitude > target or target - source > max_age_ns or target - attitude > max_age_ns or abs(attitude - source) > 40_000_000:
            raise KernelError("ADAPTER_TIME", "future, stale or unsynchronized estimate")
        if track and vehicle in self.estimate_times and any(a < b for a, b in zip((source, attitude), self.estimate_times[vehicle])):
            raise KernelError("ADAPTER_CLOCK", "estimate source clock regressed")
        index = next(i for i, item in enumerate(self.build.config["reset"]["vehicles"]) if item["id"] == vehicle)
        clock = estimate["clock_map"]
        if (estimate["source_clock"] != f"px4_boot:{index}"
                or clock["source_clock"] != estimate["source_clock"]
                or clock["clock_epoch"] != self.native_epoch
                or not 0 <= frontier + self.build.config["reset"]["warmup"] - clock["native_anchor_ns"] <= 5_000_000_000
                or estimate["origin_version"] != f"{self.native_epoch}:{vehicle}"):
            raise KernelError("ADAPTER_CLOCK", "estimate source mapping mismatch")
        messages = estimate["source_messages"]
        if [message["message_name"] for message in messages] != ["LOCAL_POSITION_NED", "ATTITUDE_QUATERNION"]:
            raise KernelError("ADAPTER_SHAPE", "estimate source pair mismatch")
        for message, mapped in zip(messages, (source, attitude)):
            fields = message["fields"]
            needed = {"time_boot_ms", "x", "y", "z", "vx", "vy", "vz"} if message["message_name"] == "LOCAL_POSITION_NED" else {"time_boot_ms", "q1", "q2", "q3", "q4"}
            if (set(fields) != needed or message["px4_instance"] != index
                    or message["run_id"] != self.native_run_id
                    or message["system_id"] != index + 1 or message["clock_epoch"] != self.native_epoch
                    or message["source_ns"] != fields["time_boot_ms"] * 1_000_000
                    or message["available_native_ns"] > frontier + self.build.config["reset"]["warmup"]
                    or mapped != message["source_ns"] - clock["source_anchor_ns"] + clock["native_anchor_ns"] - self.build.config["reset"]["warmup"]):
                raise KernelError("ADAPTER_CLOCK", "estimate raw source identity/time mismatch")
        if not math.isclose(sum(x * x for x in estimate["attitude_body_frd_to_world_enu_xyzw"]), 1.0, abs_tol=0.01):
            raise KernelError("ADAPTER_SHAPE", "estimate attitude is not normalized")
        if track:
            self.estimate_times[vehicle] = (source, attitude)
        return source

    def on_cancel(self, ctx: EngineContext, delivery: Delivery) -> None:
        if "cancel" not in self.admitted_operations or not delivery.message.schema_id.endswith("_cancel_v1"):
            raise KernelError("ADAPTER_CAPABILITY", "cancellation not negotiated")
        payload = obj(thaw(delivery.message.payload))
        if set(payload) != {"command_id"} or payload["command_id"] not in self.commands:
            raise KernelError("ADAPTER_COMMAND", "unknown cancellation identity")
        command = self.commands[payload["command_id"]]
        if command.delivery.message.schema_id != delivery.message.schema_id:
            raise KernelError("ADAPTER_COMMAND", "cancel command schema mismatch")
        backend_id = next((identity for identity, pending in self.pending.items() if pending.id == command.id), None)
        if backend_id is None:
            ctx.decide_cancel(command, delivery, False, "command already terminal")
            return
        vehicle = self.build.config["vehicles"][command.payload["entity"]]
        result = self.request("cancel", {"vehicle": vehicle, "command_id": backend_id})
        required = {"status", "command_id", "sim_ns"}
        if (set(result) - (required | {"reason"}) or not required <= set(result)
                or result["status"] not in {"accepted", "rejected"}
                or result["command_id"] != backend_id
                or integer(result["sim_ns"], "cancel admission") != self.confirmed_ns
                or ("reason" in result and (type(result["reason"]) is not str or not result["reason"]))
                or (result["status"] == "rejected" and "reason" not in result)):
            raise KernelError("ADAPTER_COMMAND", "invalid cancellation admission")
        accepted = result["status"] == "accepted"
        ctx.decide_cancel(command, delivery, accepted, result.get("reason"))
        if accepted:
            self.statuses[backend_id] = "canceling"
            self.cancel_times[backend_id] = self.confirmed_ns

    def command_updates(self, ctx: EngineContext, updates: object) -> None:
        for update in records(updates):
            required = {"command_id", "vehicle", "action", "status", "sim_ns"}
            if not required <= set(update) or set(update) - (required | {"reason"}):
                raise KernelError("ADAPTER_SHAPE", "invalid native command update")
            if any(type(update[key]) is not str or not update[key] for key in ("command_id", "vehicle", "action", "status")):
                raise KernelError("ADAPTER_SHAPE", "native command identity/status must be text")
            if "reason" in update and (type(update["reason"]) is not str or not update["reason"]):
                raise KernelError("ADAPTER_SHAPE", "native command reason must be nonempty text")
            if update["command_id"] not in self.pending:
                raise KernelError("ADAPTER_COMMAND", "unknown native command update")
        super().command_updates(ctx, updates)

    def payload_identity(self, record: dict[str, Any]) -> dict[str, Any]:
        config = obj(self.build.config["reset"]["payload"])
        if record["vehicle"] != config["carrier"] or record["package_id"] != config["package_id"]:
            raise KernelError("ADAPTER_IDENTITY", "native payload identity mismatch")
        return config

    def check_payload_state(self, state: dict[str, Any], frontier: int) -> None:
        self.build.registry.validate(payload_state_schema(), state)
        self.payload_identity(state)
        at = state["observed_sim_ns"]
        if (state["clock_epoch"] != self.native_epoch or at > frontier
                or (self.payload_state is not None and at < self.payload_state["observed_sim_ns"])):
            raise KernelError("ADAPTER_TIME", "payload epoch/frontier regressed")
        source = state["source_time_ns"]
        if (source is None) != (state["source_clock"] is None) or (source is not None and source > at):
            raise KernelError("ADAPTER_CLOCK", "invalid joint source clock")
        if state["released"] != (state["status"] == "detached"):
            raise KernelError("ADAPTER_COMMAND", "joint state/release mismatch")
        expected = None if self.release_attempt is None else self.release_attempt["command_id"]
        if state["command_id"] != expected:
            raise KernelError("ADAPTER_COMMAND", "joint release correlation mismatch")
        if state["status"] == "unavailable" and not state.get("reason"):
            raise KernelError("ADAPTER_SHAPE", "unavailable joint requires reason")
        if self.payload_state is not None and self.payload_state["released"] and not state["released"]:
            raise KernelError("ADAPTER_COMMAND", "released payload reattached")
        if state["released"]:
            if self.release_attempt is None or state.get("release_interval_ns") != [self.release_attempt["sim_ns"], at] or at < self.release_attempt["sim_ns"]:
                raise KernelError("ADAPTER_COMMAND", "unobserved or invalid release interval")

    def record_payload_state(self, ctx: EngineContext, state: dict[str, Any]) -> None:
        self.check_payload_state(state, ctx.now.ns)
        self.emit(ctx, "payload_state_v1", state, state["observed_sim_ns"])
        self.payload_state = dict(state)
        if state["released"] and self.release is not None:
            if self.detached_ns is None:
                self.detached_ns = state["observed_sim_ns"]
            backend_id = self.release["command_id"]
            if self.statuses.get(backend_id) == "accepted":
                ctx.execute(self.pending[backend_id])
                self.statuses[backend_id] = "executing"

    def payload_observations(self, ctx: EngineContext, result: dict[str, Any]) -> None:
        for state in records(result["payload_events"]):
            self.record_payload_state(ctx, state)
        for receipt in records(result["receiver_receipts"]):
            self.build.registry.validate(receiver_receipt_schema(), receipt)
            config = self.payload_identity(receipt)
            backend_id = receipt["command_id"]
            start, end = receipt["contact_start_sim_ns"], receipt["contact_end_sim_ns"]
            origin = self.build.config["reset"]["warmup"]
            if (self.release is None or backend_id != self.release["command_id"]
                    or self.statuses.get(backend_id) != "executing"
                    or self.payload_state is None or not self.payload_state["released"]
                    or receipt["clock_epoch"] != self.native_epoch
                    or receipt["receiver_id"] != config["receiver_id"]
                    or receipt["source_topic"] != config["contact_topic"]
                    or receipt["receipt_id"] in self.receipt_ids
                    or receipt["episode_origin_ns"] != origin
                    or receipt["contact_start_ns"] != start + origin
                    or receipt["contact_end_ns"] != end + origin
                    or self.detached_ns is None
                    or not self.release["sim_ns"] <= self.detached_ns <= start <= end <= ctx.now.ns
                    or receipt["contact_duration_ns"] != end - start
                    or end - start < config["contact_duration_ns"]):
                raise KernelError("ADAPTER_COMMAND", "unmatched or invalid payload contact receipt")
            self.emit(ctx, "receiver_receipt_v1", receipt, end, source_stamp=self.pose_stamp(end))
            command = self.pending[backend_id]
            terminal = {"status": "succeeded", "ack": self.release_ack, "receipt": receipt, "available_ns": ctx.now.ns}
            self.validate_result(command, terminal)
            ctx.succeed(command, terminal)
            self.receipt_ids.add(receipt["receipt_id"])
            del self.pending[backend_id]
            del self.statuses[backend_id]
            del self.pending_times[backend_id]

    def native_command(self, ctx: EngineContext, command: Command[dict[str, Any]], backend_id: str) -> bool:
        action = self.action_name(command)
        if action not in {"payload_release_v1", "payload_status_v1", "capture_poll_v1", "capture_read_v1"}:
            return False
        self.build.registry.validate(self.build.registry.message(command.delivery.message.schema_id).schema, command.payload)
        entity = command.payload["entity"]
        if entity not in self.build.config["vehicles"]:
            raise KernelError("ADAPTER_MAPPING", "command entity is not a mapped vehicle")
        vehicle = self.build.config["vehicles"][entity]
        if action.startswith("capture_"):
            self.capture_command(ctx, command, vehicle)
            return True
        config = self.build.config["reset"].get("payload")
        if config is None or vehicle != config["carrier"] or command.payload["package_id"] != config["package_id"]:
            rejection = {"status": "rejected", "reason": "no matching configured payload", "available_ns": ctx.now.ns}
            self.validate_result(command, rejection)
            ctx.reject(command, rejection)
            return True
        request = {"action": "release" if action == "payload_release_v1" else "status", "vehicle": vehicle, "package_id": command.payload["package_id"]}
        if action == "payload_release_v1":
            request["command_id"] = backend_id
            result = self.request("payload", request)
            self.build.registry.validate(payload_ack_schema(), result)
            if any(result[key] != request[key] for key in ("command_id", "vehicle", "package_id")) or result["sim_ns"] != self.confirmed_ns:
                raise KernelError("ADAPTER_COMMAND", "payload admission identity/time mismatch")
            self.emit(ctx, "payload_release_ack_v1", {**result, "kernel_command_id": command.id}, result["sim_ns"])
            if result["status"] in {"accepted", "failed"}:
                if self.release_attempt is not None:
                    raise KernelError("ADAPTER_COMMAND", "duplicate physical release attempt admitted")
                self.release_attempt = dict(result)
            if result["status"] != "accepted":
                if not result.get("reason"):
                    raise KernelError("ADAPTER_SHAPE", "payload rejection requires reason")
                rejection = {"status": "rejected", "reason": result["reason"], "ack": result, "available_ns": ctx.now.ns}
                self.validate_result(command, rejection)
                ctx.reject(command, rejection)
            else:
                if self.release is not None:
                    raise KernelError("ADAPTER_COMMAND", "duplicate physical release admitted")
                self.release, self.release_ack = dict(result), dict(result)
                ctx.accept(command)
                self.pending[backend_id] = command
                self.statuses[backend_id] = "accepted"
                self.pending_times[backend_id] = self.confirmed_ns
        else:
            state = self.request("payload", request)
            self.record_payload_state(ctx, state)
            terminal = {"status": "rejected" if state["status"] == "unavailable" else "succeeded", "observation": state, "available_ns": ctx.now.ns}
            if state["status"] == "unavailable":
                terminal["reason"] = state["reason"]
            self.validate_result(command, terminal)
            if terminal["status"] == "rejected":
                ctx.reject(command, terminal)
            else:
                ctx.accept(command)
                ctx.execute(command)
                ctx.succeed(command, terminal)
        return True

    @staticmethod
    def camera_transform(pose: list[float]) -> list[list[float]]:
        x, y, z, roll, pitch, yaw = pose
        cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
        rotation = [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                    [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                    [-sp, cp * sr, cp * cr]]
        optical = [[0, 0, 1], [-1, 0, 0], [0, -1, 0]]
        return [[sign * sum(rotation[i][k] * optical[k][j] for k in range(3)) for j in range(3)] + [sign * (x, y, z)[i]]
                for i, sign in enumerate((1, -1, -1))] + [[0, 0, 0, 1]]

    def check_frame(self, result: dict[str, Any], vehicle: str, after: int) -> int:
        observation, blobs = result["observation"], result["blobs"]
        camera = obj(self.build.config["reset"]["rgbd_camera"])
        origin, frontier = self.build.config["reset"]["warmup"], self.confirmed_ns
        observed, native = observation["observed_at"], observation["source_time"]
        root = f"{camera['topic_prefix'].rstrip('/')}/{vehicle}/{camera['sensor_id']}"
        native_frame = f"aas_{vehicle}::aas_rgbd_{camera['sensor_id']}_link::{camera['sensor_id']}"
        frame_id = f"{self.native_run_id}:{self.native_epoch}:{vehicle}:rgbd:{native}"
        max_age = max(250_000_000, int(min(10_000_000_000, 2_000_000_000 / camera["update_rate_hz"])))
        if (observation["run_id"] != self.native_run_id or observation["clock_epoch"] != self.native_epoch
                or observation["uav_id"] != vehicle or observation["sensor_id"] != camera["sensor_id"]
                or observation["frame_id"] != frame_id or observation["source_topic"] != root + "/image"
                or observation["native_frame_id"] != native_frame
                or native != observed + origin or not after < observed <= frontier
                or observation["depth_observed_at"] != observed
                or observation["available_sim_ns"] != frontier or frontier - observed > max_age
                or observed < self.frame_times.get(vehicle, 0)):
            raise KernelError("ADAPTER_CAPTURE", "capture identity/source/frontier mismatch")
        calibration = observation["calibration"]
        width, height, k = calibration["width"], calibration["height"], calibration["K"]
        if (width != camera["width"] or height != camera["height"] or width * height > 1_048_576
                or calibration["source_topic"] != root + "/camera_info"
                or calibration["native_frame_id"] != native_frame
                or not origin <= calibration["camera_info_source_ns"] <= native
                or calibration["depth_min_m"] != camera["near_m"] or calibration["depth_max_m"] != camera["far_m"]
                or not 0 < calibration["depth_min_m"] < calibration["depth_max_m"]
                or k[0] <= 0 or k[4] <= 0 or not 0 <= k[2] < width or not 0 <= k[5] < height
                or any(abs(k[i]) > 1e-9 for i in (1, 3, 6, 7)) or abs(k[8] - 1) > 1e-9
                or len(calibration["distortion"]) not in (0, 4, 5, 8, 12, 14)):
            raise KernelError("ADAPTER_CAPTURE", "capture calibration/dimensions mismatch")
        for actual, expected in zip(calibration["T_body_frd_from_camera_optical"], self.camera_transform(camera["pose_flu"])):
            if any(not math.isclose(a, b, abs_tol=1e-9) for a, b in zip(actual, expected)):
                raise KernelError("ADAPTER_CAPTURE", "capture mount transform mismatch")
        for dimension, center, focal, field in ((width, k[2], k[0], "horizontal_fov_deg"), (height, k[5], k[4], "vertical_fov_deg")):
            expected = math.degrees(math.atan(center / focal) + math.atan((dimension - center) / focal))
            if not math.isclose(calibration[field], expected, abs_tol=1e-9):
                raise KernelError("ADAPTER_CAPTURE", "capture FOV/intrinsics mismatch")
        version_content = {key: value for key, value in calibration.items() if key not in {"version", "camera_info_source_ns"}}
        version = f"{self.native_epoch}:{camera['sensor_id']}:" + hashlib.sha256(json.dumps(version_content, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if calibration["version"] != version:
            raise KernelError("ADAPTER_CAPTURE", "capture calibration version mismatch")
        self.check_estimate(observation["vehicle_estimate"], vehicle, frontier, target=observed, max_age_ns=40_000_000)
        if (observation["localization_status"] != observation["vehicle_estimate"]["status"]
                or observation["localization_reason"] != observation["vehicle_estimate"].get("reason")):
            raise KernelError("ADAPTER_CAPTURE", "capture localization correlation mismatch")
        for kind, encoding in (("rgb", "png"), ("depth", "zlib-float32-le-metres")):
            blob = blobs[kind]
            if blob["encoding"] != encoding or any(char not in "0123456789abcdef" for char in blob["sha256"]):
                raise KernelError("ADAPTER_CAPTURE", "invalid capture blob descriptor")
        if sum(blob["bytes"] for blob in blobs.values()) > 64 * 1024 * 1024 or observation["rgb_sha256"] != blobs["rgb"]["sha256"]:
            raise KernelError("ADAPTER_CAPTURE", "capture blob budget/hash mismatch")
        previous = self.frames.get((vehicle, frame_id))
        if previous is not None and (previous["blobs"] != blobs or previous["observation"]["calibration"]["version"] != version):
            raise KernelError("ADAPTER_CAPTURE", "conflicting recorded frame identity")
        return observed

    def capture_command(self, ctx: EngineContext, command: Command[dict[str, Any]], vehicle: str) -> None:
        read = self.action_name(command) == "capture_read_v1"
        params = {key: value for key, value in command.payload.items() if key != "entity"}
        frame = self.frames.get((vehicle, params.get("frame_id", ""))) if read else None
        if self.build.config["reset"].get("rgbd_camera") is None:
            result = {"status": "unavailable", "reason": "no configured native camera"}
        elif read and (frame is None or params["offset"] > frame["blobs"][params["kind"]]["bytes"]):
            result = {"status": "unavailable", "reason": "frame/offset absent from recorded capture evidence"}
        elif not read and params["after_sim_ns"] > self.confirmed_ns:
            result = {"status": "unavailable", "reason": "capture poll frontier is in the future"}
        else:
            result = self.request("capture", {"action": "read" if read else "poll", "vehicle": vehicle, **params})
        self.build.registry.validate(capture_response_schema(read=read), result)
        if result["status"] != "succeeded":
            if set(result) != {"status", "reason"}:
                raise KernelError("ADAPTER_CAPTURE", "unavailable capture requires explicit reason only")
            terminal = {**result, "kernel_command_id": command.id, "available_ns": ctx.now.ns}
            self.validate_result(command, terminal)
            ctx.reject(command, terminal)
            return
        if read:
            required = {"status", "vehicle", "frame_id", "kind", "offset", "total_bytes", "data_base64", "eof"}
            if set(result) != required or frame is None:
                raise KernelError("ADAPTER_CAPTURE", "incomplete capture chunk")
            blob = frame["blobs"][params["kind"]]
            if (result["vehicle"] != vehicle or any(result[key] != params[key] for key in ("frame_id", "kind", "offset"))
                    or result["total_bytes"] != blob["bytes"]):
                raise KernelError("ADAPTER_CAPTURE", "capture chunk identity/size mismatch")
            try:
                data = base64.b64decode(result["data_base64"], validate=True)
            except (ValueError, binascii.Error) as error:
                raise KernelError("ADAPTER_CAPTURE", "invalid capture chunk base64") from error
            remaining = result["total_bytes"] - result["offset"]
            if (len(data) != min(params["max_bytes"], remaining)
                    or result["eof"] != (len(data) == remaining)
                    or (result["offset"] == 0 and result["eof"] and hashlib.sha256(data).hexdigest() != blob["sha256"])):
                raise KernelError("ADAPTER_CAPTURE", "capture chunk bounds/hash mismatch")
            at, event = frame["observation"]["observed_at"], "capture_chunk_v1"
        else:
            if set(result) != {"status", "observation", "blobs"}:
                raise KernelError("ADAPTER_CAPTURE", "incomplete recorded capture frame")
            at, event = self.check_frame(result, vehicle, params["after_sim_ns"]), "capture_frame_v1"
            identity = (vehicle, result["observation"]["frame_id"])
            self.frames[identity] = result
            self.frame_times[vehicle] = at
            owned = [key for key in self.frames if key[0] == vehicle]
            for key in owned[:-4]:
                del self.frames[key]
        terminal = {**result, "kernel_command_id": command.id, "available_ns": ctx.now.ns}
        self.validate_result(command, terminal)
        ctx.accept(command)
        ctx.execute(command)
        self.emit(ctx, event, {**result, "kernel_command_id": command.id}, at, source_stamp=self.pose_stamp(at))
        ctx.succeed(command, terminal)

    def command_payload(
        self, command: Command[dict[str, Any]], backend_id: str
    ) -> dict[str, Any]:
        payload = dict(command.payload)
        entity = payload.pop("entity")
        vehicle = obj(self.build.config["vehicles"])[entity]
        action = self.action_name(command)
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
