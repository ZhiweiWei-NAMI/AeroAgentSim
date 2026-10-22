"""Lockstep lifecycle and physical barriers, independent of benchmark workload."""

import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from .commands import Commands
from .config import fields, identifier, integer, prepare
from .processes import Processes
from .telemetry import Telemetry
from .transport import Gazebo


class Runtime:
    def __init__(self):
        self.directory = None
        self.processes = self.gazebo = self.telemetry = self.commands = None
        self.config = None
        self.sensors = self.payload_adapter = None
        self.epoch = None
        self.origin_ns = self.sim_ns = 0

    async def reset(self, payload):
        self.directory = Path(tempfile.mkdtemp(prefix="aas-px4-"))
        self.config = prepare(payload, self.directory)
        partition = "aas-" + uuid.uuid4().hex
        self.epoch = partition
        native_run_id = os.environ.get("AAS_RUN_ID", partition)
        if not 1 <= len(native_run_id) <= 96 or any(
            not (c.isascii() and (c.isalnum() or c in "_-")) for c in native_run_id
        ):
            raise ValueError("AAS_RUN_ID must be a bounded ASCII run identifier")
        completion_source = os.environ.get("AAS_COMPLETION_SOURCE", "legacy")
        if completion_source not in {"legacy", "px4_estimate"}:
            raise ValueError("unknown AAS_COMPLETION_SOURCE")
        if completion_source == "px4_estimate" and self.config.altitude_datum != "amsl":
            raise ValueError("estimate-based completion requires a declared AMSL world origin")
        self.processes = Processes(self.directory, partition)
        config = self.config
        await self.processes.gazebo(config)
        self.gazebo = Gazebo(
            partition, config.world, [v.model_name for v in config.vehicles]
        )
        await self.gazebo.start()
        for vehicle in config.vehicles:
            await self.processes.px4(vehicle, config)
        for vehicle in config.vehicles:
            await self.processes.wait_log(
                "px4-" + vehicle.id, "Startup script returned successfully"
            )
            await self.processes.mavsdk(vehicle)
        # PX4 emits an initial heartbeat at startup, but subsequent heartbeats
        # require simulation to run. Connect all gRPC streams before warmup.
        self.telemetry = Telemetry(config.vehicles)
        self.telemetry.configure_estimates(
            native_run_id, partition, config.origin_wgs84,
            lambda: self.gazebo.sim_ns, altitude_datum=config.altitude_datum,
            native_paused_callable=lambda: self.gazebo.stats is not None
                and self.gazebo.stats[1] and not self.gazebo.step_in_flight,
            log_directory=self.directory,
        )
        await self.telemetry.connect()
        if config.rgbd_camera is not None:
            from .sensor_streams import SensorStreams

            self.sensors = SensorStreams(self.gazebo, self.telemetry, config.vehicles,
                native_run_id, partition, camera=config.rgbd_camera)
            await self.sensors.start()
        if config.payload is not None:
            from .payload_adapter import PayloadAdapter

            self.payload_adapter = PayloadAdapter(config.payload, self.gazebo,
                self.directory, partition, lambda: self.gazebo.sim_ns - self.origin_ns,
                physics_step_ns=config.physics_step_ns)
            await self.payload_adapter.start()
        steps = config.warmup // config.physics_step_ns
        for offset in range(0, steps, 125):
            await self.telemetry.heartbeat.pulse(self.gazebo.sim_ns)
            await self.telemetry.pulse_estimates()
            await self.gazebo.step(min(125, steps - offset), config.physics_step_ns)
            self.processes.check()
        await self.telemetry.wait_ready()
        self.origin_ns = self.gazebo.sim_ns
        self.sim_ns = 0
        self.telemetry.set_episode_origin(self.origin_ns)
        if self.sensors is not None:
            self.sensors.set_episode_origin(self.origin_ns)
        payload_events, receiver_receipts = [], []
        if self.payload_adapter is not None:
            self.payload_adapter.set_episode_origin(self.origin_ns)
            carrier = next(v for v in config.vehicles if v.id == config.payload["carrier"])
            await self.payload_adapter.initialize_attachment(carrier.model_name)
            payload_events, receiver_receipts = self.payload_adapter.drain(0)
        poses = await self.poses()
        altitude_offsets = {
            vehicle.id: self.telemetry.cache[vehicle.id]["global_position"][
                "absolute_altitude_m"
            ]
            - poses[vehicle.model_name]["position_enu"][2]
            for vehicle in config.vehicles
        }
        if completion_source == "px4_estimate":
            altitude_offsets = {v.id: config.origin_wgs84[2] for v in config.vehicles}
        self.commands = Commands(config, self.telemetry, altitude_offsets,
                                 estimate_completion=completion_source == "px4_estimate")
        self.gazebo.drain_contacts()
        result = {
            "run_id": native_run_id,
            "clock_epoch": partition,
            "reached_sim_ns": 0,
            "physics_step_ns": config.physics_step_ns,
            "warmup_sim_ns": self.origin_ns,
            "vehicles": [v.id for v in config.vehicles],
            "frame_bindings": {
                "world_up_to_px4_absolute_altitude_offset_m": altitude_offsets
            },
            "contact_topics": self.gazebo.contact_topics,
            "telemetry": self.telemetry.samples(0, poses, self.origin_ns),
        }
        if self.payload_adapter is not None:
            result.update(payload_events=payload_events, receiver_receipts=receiver_receipts)
        return result

    async def poses(self):
        result = {}
        for vehicle in self.config.vehicles:
            result[vehicle.model_name] = await self.gazebo.sample(vehicle.model_name)
        return result

    async def advance(self, payload):
        fields(payload, ("to_sim_ns",))
        target = integer(payload["to_sim_ns"], "to_sim_ns")
        if target < self.sim_ns or (target - self.sim_ns) % self.config.physics_step_ns:
            raise ValueError(
                "target must be nondecreasing and align to the physics step"
            )
        self.processes.check()
        self.telemetry.check()
        if target == self.sim_ns:
            # A hold has no native integration and does not dispatch commands.
            result = {
                "reached_sim_ns": target,
                "telemetry": self.telemetry.samples(
                    target, await self.poses(), self.origin_ns
                ),
                "contacts": [],
                "command_updates": [],
            }
            if self.payload_adapter is not None:
                result.update(payload_events=[], receiver_receipts=[])
            return result
        started = time.perf_counter_ns()
        profile = {}
        mark = time.perf_counter_ns()
        await self.commands.dispatch(self.sim_ns)
        profile["command_dispatch"] = time.perf_counter_ns() - mark
        steps = (target - self.sim_ns) // self.config.physics_step_ns
        samples = []
        payload_events, receiver_receipts = [], []
        for offset in range(0, steps, 125):
            if offset:
                # A pending original SDK response may settle during a batch.
                # Progress cancellation at each paused frontier, not only at the
                # start of a potentially multi-second advance request.
                await self.commands.dispatch(self.sim_ns)
            mark = time.perf_counter_ns()
            await self.telemetry.heartbeat.pulse(self.gazebo.sim_ns)
            await self.telemetry.pulse_estimates()
            profile["heartbeat_release"] = (
                profile.get("heartbeat_release", 0) + time.perf_counter_ns() - mark
            )
            await self.gazebo.step(
                min(125, steps - offset), self.config.physics_step_ns
            )
            for key, duration in self.gazebo.profile.items():
                profile[key] = profile.get(key, 0) + duration
            mark = time.perf_counter_ns()
            self.processes.check()
            self.sim_ns = self.gazebo.sim_ns - self.origin_ns
            poses = await self.poses()
            samples = self.telemetry.samples(self.sim_ns, poses, self.origin_ns)
            profile["telemetry_projection"] = (
                profile.get("telemetry_projection", 0) + time.perf_counter_ns() - mark
            )
            mark = time.perf_counter_ns()
            self.commands.observe(samples, self.sim_ns)
            if self.payload_adapter is not None:
                events, receipts = self.payload_adapter.drain(self.sim_ns)
                payload_events.extend(events)
                receiver_receipts.extend(receipts)
            profile["command_observation"] = (
                profile.get("command_observation", 0) + time.perf_counter_ns() - mark
            )
        if self.sim_ns != target:
            raise RuntimeError(f"native frontier mismatch {self.sim_ns} != {target}")
        mark = time.perf_counter_ns()
        contacts = self.gazebo.drain_contacts()
        for event in contacts:
            event["sim_ns"] -= self.origin_ns
        profile["contacts_drain"] = time.perf_counter_ns() - mark
        profile["runtime_total"] = time.perf_counter_ns() - started
        result = {
            "reached_sim_ns": self.sim_ns,
            "telemetry": samples,
            "contacts": contacts,
            "command_updates": self.commands.drain(),
        }

        if self.payload_adapter is not None:
            result.update(payload_events=payload_events, receiver_receipts=receiver_receipts)
        if os.environ.get("AAS_PROFILE") == "1":
            result["profile_wall_ns"] = profile
        return result

    def command(self, payload):
        return self.commands.accept(payload, self.sim_ns)

    def cancel(self, payload):
        return self.commands.cancel(payload, self.sim_ns)

    def vehicle(self, value):
        vehicle = identifier(value)
        if self.config is None or vehicle not in {v.id for v in self.config.vehicles}:
            raise ValueError("unknown vehicle")
        return vehicle

    def payload(self, payload):
        if not isinstance(payload, dict) or payload.get("action") not in ("release", "status"):
            raise ValueError("unknown payload action")
        action = payload["action"]
        required = ("action", "vehicle", "package_id", "command_id") if action == "release" else ("action", "vehicle", "package_id")
        fields(payload, required)
        vehicle = self.vehicle(payload["vehicle"])
        package_id = identifier(payload["package_id"])
        if action == "release":
            command_id = identifier(payload["command_id"])
        if self.payload_adapter is None:
            result = {"status": "unavailable", "reason": "no physical payload configured"}
            if action == "release":
                result.update(command_id=command_id, vehicle=vehicle, package_id=package_id, sim_ns=self.sim_ns)
            return result
        if action == "release":
            return self.payload_adapter.release(vehicle, package_id, self.sim_ns, command_id)
        return self.payload_adapter.status(vehicle, package_id)

    def capture(self, payload):
        if not isinstance(payload, dict) or payload.get("action") not in ("poll", "read"):
            raise ValueError("unknown capture action")
        action = payload["action"]
        if action == "poll":
            fields(payload, ("action", "vehicle"), ("after_sim_ns",))
            after = integer(payload.get("after_sim_ns", -1), "after_sim_ns", minimum=-1)
        else:
            fields(payload, ("action", "vehicle", "frame_id", "kind"), ("offset", "max_bytes"))
            frame_id = payload["frame_id"]
            if not isinstance(frame_id, str) or not 1 <= len(frame_id) <= 256 or any(
                ord(c) < 33 or ord(c) > 126 for c in frame_id
            ):
                raise ValueError("invalid capture frame_id")
            kind = payload["kind"]
            if kind not in ("rgb", "depth"):
                raise ValueError("capture kind must be rgb or depth")
            offset = integer(payload.get("offset", 0), "offset", maximum=64 * 1024 * 1024)
            maximum = integer(payload.get("max_bytes", 262144), "max_bytes", 1, 262144)
        vehicle = self.vehicle(payload["vehicle"])
        if self.sensors is None:
            return {"status": "unavailable", "reason": "RGB-D sensor profile is disabled"}
        if action == "poll":
            return self.sensors.poll(vehicle, after)
        return self.sensors.read_chunk(vehicle, frame_id, kind, offset, maximum)

    async def stop(self):
        errors = []
        for subsystem in (self.commands, self.sensors, self.payload_adapter, self.telemetry, self.gazebo, self.processes):
            if subsystem is not None:
                try:
                    await subsystem.stop()
                except Exception as error:
                    errors.append(f"{type(subsystem).__name__}: {error}")
        if self.directory is not None:
            diagnostic = __import__("os").environ.get("AAS_DIAGNOSTIC_DIR")
            if diagnostic:
                destination = Path(diagnostic) / self.directory.name
                destination.mkdir(parents=True, exist_ok=True)
                for pattern in ("*.log", "*.jsonl"):
                    for path in self.directory.glob(pattern):
                        shutil.copy2(path, destination / path.name)
                sdf = self.directory / "world.sdf"
                if sdf.is_file():
                    shutil.copy2(sdf, destination / sdf.name)
            shutil.rmtree(self.directory)
            self.directory = None
        self.commands = self.telemetry = self.gazebo = self.processes = None
        self.sensors = self.payload_adapter = None
        if errors:
            raise RuntimeError("cleanup failed: " + "; ".join(errors))
