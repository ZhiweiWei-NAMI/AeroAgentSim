"""Live MAVSDK streams; receipt times are not invented source timestamps."""

import asyncio
import time
from types import SimpleNamespace

from .config import number
from .heartbeat import Heartbeat


class Telemetry:
    def __init__(self, vehicles):
        self.vehicles = vehicles
        self.sessions = {}
        self.cache = {v.id: {} for v in vehicles}
        self.received = {v.id: {} for v in vehicles}
        self.versions = {v.id: {} for v in vehicles}
        self.tasks = []
        self.failure = None

    async def connect(self):
        from mavsdk.action import Action
        from mavsdk.async_plugin_manager import AsyncPluginManager
        from mavsdk.core import Core
        from mavsdk.mavlink_direct import MavlinkDirect, MavlinkMessage
        from mavsdk.telemetry import Telemetry as MavTelemetry

        for vehicle in self.vehicles:
            manager = await asyncio.wait_for(
                AsyncPluginManager.create(host="127.0.0.1", port=vehicle.grpc_port), 30
            )
            session = SimpleNamespace(
                action=Action(manager),
                core=Core(manager),
                mavlink_direct=MavlinkDirect(manager),
                telemetry=MavTelemetry(manager),
                _channel=manager.channel,
            )
            self.sessions[vehicle.id] = session
            await asyncio.wait_for(self.connected(session), 45)
            streams = {
                "global_position": session.telemetry.position(),
                "velocity_enu": session.telemetry.position_velocity_ned(),
                "battery": session.telemetry.battery(),
                "armed": session.telemetry.armed(),
                "flight_mode": session.telemetry.flight_mode(),
                "landed_state": session.telemetry.landed_state(),
                "health": session.telemetry.health(),
            }
            for field, stream in streams.items():
                self.tasks.append(
                    asyncio.create_task(self.consume(vehicle.id, field, stream))
                )

        self.heartbeat = Heartbeat(self.sessions, MavlinkMessage)

    @staticmethod
    async def connected(session):
        async for state in session.core.connection_state():
            if state.is_connected:
                return
        raise RuntimeError("MAVSDK connection stream ended")

    @staticmethod
    def decode(field, sample):
        if field == "global_position":
            return {
                key: number(getattr(sample, key), key)
                for key in (
                    "latitude_deg",
                    "longitude_deg",
                    "absolute_altitude_m",
                    "relative_altitude_m",
                )
            }
        if field == "velocity_enu":
            v = sample.velocity
            return [
                number(v.east_m_s, "east velocity"),
                number(v.north_m_s, "north velocity"),
                -number(v.down_m_s, "down velocity"),
            ]
        if field == "battery":
            remaining = number(sample.remaining_percent, "battery remaining percent")
            if not 0 <= remaining <= 100:
                raise ValueError(
                    "MAVSDK battery remaining_percent must be percent in [0,100]"
                )
            return {
                "remaining_fraction": remaining / 100,
                "voltage_v": number(sample.voltage_v, "battery voltage"),
            }
        if field == "armed":
            if type(sample) is not bool:
                raise ValueError("MAVSDK armed must be bool")
            return sample
        if field in ("flight_mode", "landed_state"):
            if not isinstance(sample.name, str):
                raise ValueError(f"MAVSDK {field} missing enum name")
            return sample.name
        if field == "health":
            flags = {
                key: getattr(sample, key)
                for key in (
                    "is_gyrometer_calibration_ok",
                    "is_accelerometer_calibration_ok",
                    "is_magnetometer_calibration_ok",
                    "is_local_position_ok",
                    "is_global_position_ok",
                    "is_home_position_ok",
                    "is_armable",
                )
            }
            if any(type(value) is not bool for value in flags.values()):
                raise ValueError("MAVSDK health must contain bool flags")
            return flags
        raise ValueError(f"unsupported telemetry {field}")

    async def consume(self, vehicle, field, stream):
        try:
            async for sample in stream:
                self.cache[vehicle][field] = self.decode(field, sample)
                self.received[vehicle][field] = time.monotonic_ns()
                self.versions[vehicle][field] = self.versions[vehicle].get(field, 0) + 1
            raise RuntimeError("stream ended")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.failure = RuntimeError(f"MAVSDK stream {vehicle}/{field}: {error}")

    def check(self):
        if self.failure:
            raise self.failure

    def ready(self):
        self.check()
        required = {
            "global_position",
            "velocity_enu",
            "battery",
            "armed",
            "flight_mode",
            "landed_state",
            "health",
        }
        return all(required <= cache.keys() for cache in self.cache.values())

    async def wait_ready(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready():
                return
            await asyncio.sleep(0.01)
        missing = {
            vehicle: sorted(
                {
                    "global_position",
                    "velocity_enu",
                    "battery",
                    "armed",
                    "flight_mode",
                    "landed_state",
                    "health",
                }
                - cache.keys()
            )
            for vehicle, cache in self.cache.items()
        }
        raise TimeoutError(f"PX4 telemetry not initialized: {missing}")

    def samples(self, sim_ns, poses, origin_ns):
        self.check()
        now = time.monotonic_ns()
        samples = []
        for vehicle in self.vehicles:
            cache = self.cache[vehicle.id]
            pose = poses[vehicle.model_name]
            # Gazebo carries a real source timestamp. MAVSDK has only local
            # receipt times; leave its source time explicitly unmapped.
            sample = dict(
                vehicle=vehicle.id,
                sim_ns=pose["sim_ns"] - origin_ns,
                position_enu=pose["position_enu"],
                attitude_quat=pose["attitude_quat"],
            )
            sample.update(
                {
                    key: cache[key]
                    for key in (
                        "velocity_enu",
                        "battery",
                        "armed",
                        "flight_mode",
                        "landed_state",
                        "health",
                    )
                }
            )
            sample["freshness"] = {
                "gazebo_pose_age_sim_ns": sim_ns - sample["sim_ns"],
                "mavsdk_source_sim_ns": None,
                "mavsdk_receipt_age_wall_ns": {
                    key: now - timestamp
                    for key, timestamp in self.received[vehicle.id].items()
                },
                "mavsdk_versions": dict(self.versions[vehicle.id]),
            }
            samples.append(sample)
        return samples

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        for session in self.sessions.values():
            # Own the shared gRPC channel explicitly; create only the three
            # native plugins used by this service rather than all System plugins.
            channel = session._channel
            await channel.close()
        self.sessions.clear()
