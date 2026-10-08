"""Lockstep lifecycle and physical barriers, independent of benchmark workload."""

import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from .commands import Commands
from .config import integer, prepare
from .processes import Processes
from .telemetry import Telemetry
from .transport import Gazebo


class Runtime:
    def __init__(self):
        self.directory = None
        self.processes = self.gazebo = self.telemetry = self.commands = None
        self.config = None
        self.origin_ns = self.sim_ns = 0

    async def reset(self, payload):
        self.directory = Path(tempfile.mkdtemp(prefix="aas-px4-"))
        self.config = prepare(payload, self.directory)
        partition = "aas-" + uuid.uuid4().hex
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
        await self.telemetry.connect()
        steps = config.warmup // config.physics_step_ns
        for offset in range(0, steps, 125):
            await self.telemetry.heartbeat.pulse(self.gazebo.sim_ns)
            await self.gazebo.step(min(125, steps - offset), config.physics_step_ns)
            self.processes.check()
        await self.telemetry.wait_ready()
        self.origin_ns = self.gazebo.sim_ns
        self.sim_ns = 0
        poses = await self.poses()
        altitude_offsets = {
            vehicle.id: self.telemetry.cache[vehicle.id]["global_position"][
                "absolute_altitude_m"
            ]
            - poses[vehicle.model_name]["position_enu"][2]
            for vehicle in config.vehicles
        }
        self.commands = Commands(config, self.telemetry, altitude_offsets)
        self.gazebo.drain_contacts()
        return {
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

    async def poses(self):
        result = {}
        for vehicle in self.config.vehicles:
            result[vehicle.model_name] = await self.gazebo.sample(vehicle.model_name)
        return result

    async def advance(self, payload):
        from .config import fields

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
            return {
                "reached_sim_ns": target,
                "telemetry": self.telemetry.samples(
                    target, await self.poses(), self.origin_ns
                ),
                "contacts": [],
                "command_updates": [],
            }
        started = time.perf_counter_ns()
        profile = {}
        mark = time.perf_counter_ns()
        await self.commands.dispatch(self.sim_ns)
        profile["command_dispatch"] = time.perf_counter_ns() - mark
        steps = (target - self.sim_ns) // self.config.physics_step_ns
        samples = []
        for offset in range(0, steps, 125):
            mark = time.perf_counter_ns()
            await self.telemetry.heartbeat.pulse(self.gazebo.sim_ns)
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

        if os.environ.get("AAS_PROFILE") == "1":
            result["profile_wall_ns"] = profile
        return result

    def command(self, payload):
        return self.commands.accept(payload, self.sim_ns)

    async def stop(self):
        errors = []
        for subsystem in (self.commands, self.telemetry, self.gazebo, self.processes):
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
                for path in self.directory.glob("*.log"):
                    shutil.copy2(path, destination / path.name)
                sdf = self.directory / "world.sdf"
                if sdf.is_file():
                    shutil.copy2(sdf, destination / sdf.name)
            shutil.rmtree(self.directory)
            self.directory = None
        self.commands = self.telemetry = self.gazebo = self.processes = None
        if errors:
            raise RuntimeError("cleanup failed: " + "; ".join(errors))
