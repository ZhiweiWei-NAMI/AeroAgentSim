"""Read-only, source-timed PX4 estimates through the existing MAVSDK owner.

A matching TIMESYNC reply can anchor the PX4 boot clock only while Gazebo is
still paused at the request's native frontier. Receipt time is diagnostic, not
source time. Unknown, stale, future, reset or reordered sources never fall back
to Gazebo truth or timestamp-free MAVSDK telemetry. Native imports remain lazy.
"""

import asyncio
import json
import math
import time
from collections import deque

SOURCE_RESOLUTION_NS = 1_000_000
CLOCK_MAP_MAX_AGE_NS = 5_000_000_000
ATTITUDE_ALIGNMENT_NS = 40_000_000
MAX_ESTIMATE_AGE_NS = 1_000_000_000
MAX_SYNC_WALL_NS = 250_000_000


def finite(value, label):
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be finite numeric data")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{label} must be finite numeric data") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite numeric data")
    return result


def integer(value, label, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def geodetic_to_enu(point, origin):
    """WGS84 local tangent conversion; both heights must use the same datum."""
    def ecef(value):
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ValueError("WGS84 coordinates require latitude, longitude, height")
        latitude, longitude, height = [finite(v, "WGS84 coordinate") for v in value]
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("WGS84 latitude/longitude out of bounds")
        lat, lon = math.radians(latitude), math.radians(longitude)
        a, e2 = 6378137.0, 6.6943799901413165e-3
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        return ((n + height) * math.cos(lat) * math.cos(lon),
                (n + height) * math.cos(lat) * math.sin(lon),
                (n * (1 - e2) + height) * math.sin(lat))

    delta = [a - b for a, b in zip(ecef(point), ecef(origin))]
    lat, lon = map(math.radians, origin[:2])
    x, y, z = delta
    result = [
        -math.sin(lon) * x + math.cos(lon) * y,
        -math.sin(lat) * math.cos(lon) * x - math.sin(lat) * math.sin(lon) * y + math.cos(lat) * z,
        math.cos(lat) * math.cos(lon) * x + math.cos(lat) * math.sin(lon) * y + math.sin(lat) * z,
    ]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("WGS84 conversion overflow")
    return result


def quaternion_product(a, b):
    x, y, z, w = a
    xx, yy, zz, ww = b
    return [
        w * xx + x * ww + y * zz - z * yy,
        w * yy - x * zz + y * ww + z * xx,
        w * zz + x * yy - y * xx + z * ww,
        w * ww - x * xx - y * yy - z * zz,
    ]


def _invalid_constant(value):
    raise ValueError(f"nonfinite MAVLink JSON constant {value}")


class EstimateStreams:
    def __init__(self, vehicles):
        self.vehicles = {vehicle.id: vehicle for vehicle in vehicles}
        self.positions = {vehicle: deque(maxlen=1024) for vehicle in self.vehicles}
        self.attitudes = {vehicle: deque(maxlen=1024) for vehicle in self.vehicles}
        self.origins, self.clock_maps, self.pending_sync = {}, {}, {}
        self.native_clock = self.native_paused = None
        self.run_id = self.epoch = self.world_origin = self.altitude_datum = None
        self.episode_origin_ns = 0
        self.last_native_ns = None
        self.last_request_ns, self.last_request_wall_ns, self.last_source = {}, {}, {}
        self.streams_requested = set()
        self.sync_events = {vehicle: asyncio.Event() for vehicle in self.vehicles}
        self.failure = {}

    def configure(
        self, run_id, clock_epoch, world_origin_wgs84, native_clock_callable,
        episode_origin_ns=0, altitude_datum=None, log_directory=None,
        native_paused_callable=None,
    ):
        """Configure an explicit epoch. log_directory is compatibility-only.

        Source evidence is returned in samples, not written to a side journal.
        A new run/epoch is the only recovery from clock reset or origin change.
        """
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("estimate run_id must be a nonempty string")
        if (type(clock_epoch) not in (str, int) or clock_epoch == ""
                or (type(clock_epoch) is int and clock_epoch < 0)):
            raise ValueError("estimate clock_epoch must be a nonempty epoch identity")
        integer(episode_origin_ns, "episode origin")
        if native_clock_callable is not None and not callable(native_clock_callable):
            raise ValueError("native clock must be callable")
        if native_paused_callable is not None and not callable(native_paused_callable):
            raise ValueError("native paused state must be callable")
        origin = None
        if world_origin_wgs84 is not None:
            geodetic_to_enu(world_origin_wgs84, world_origin_wgs84)
            origin = tuple(world_origin_wgs84)
        changed_epoch = (self.run_id, self.epoch) != (run_id, clock_epoch)
        if changed_epoch:
            for records in (*self.positions.values(), *self.attitudes.values()):
                records.clear()
            for mapping in (self.origins, self.clock_maps, self.pending_sync, self.failure,
                            self.last_request_ns, self.last_request_wall_ns, self.last_source):
                mapping.clear()
            self.streams_requested.clear()
            self.last_native_ns = None
            for event in self.sync_events.values():
                event.clear()
        elif self.world_origin != origin or self.altitude_datum != altitude_datum:
            for vehicle in self.vehicles:
                self._invalidate(vehicle, "world origin or altitude datum changed within estimate epoch")
        self.run_id, self.epoch = run_id, clock_epoch
        self.world_origin, self.altitude_datum = origin, altitude_datum
        self.native_clock, self.native_paused = native_clock_callable, native_paused_callable
        self.episode_origin_ns = episode_origin_ns

    def _invalidate(self, vehicle, reason):
        self.failure.setdefault(vehicle, str(reason)[:512])
        self.clock_maps.pop(vehicle, None)
        self.positions[vehicle].clear()
        self.attitudes[vehicle].clear()
        for key in [key for key in self.pending_sync if key[0] == vehicle]:
            del self.pending_sync[key]
        self.sync_events[vehicle].set()

    def _native_now(self):
        if self.native_clock is None:
            return None
        try:
            now = integer(self.native_clock(), "native source clock")
        except Exception as error:
            for vehicle in self.vehicles:
                self._invalidate(vehicle, f"native source clock unavailable: {error}")
            return None
        if self.last_native_ns is not None and now < self.last_native_ns:
            for vehicle in self.vehicles:
                self._invalidate(vehicle, "native source clock regressed; new epoch required")
            return None
        self.last_native_ns = now
        return now

    def _paused(self):
        if self.native_paused is None:
            return False
        try:
            paused = self.native_paused()
            if type(paused) is not bool:
                raise ValueError("native pause state must be bool")
            return paused
        except Exception as error:
            for vehicle in self.vehicles:
                self._invalidate(vehicle, f"native pause state unavailable: {error}")
            return False

    def set_episode_origin(self, native_origin_ns):
        integer(native_origin_ns, "episode origin")
        if native_origin_ns < self.episode_origin_ns:
            for vehicle in self.vehicles:
                self._invalidate(vehicle, "episode clock regressed; new epoch required")
            return
        self.episode_origin_ns = native_origin_ns
        for vehicle in self.vehicles:
            clock = self.clock_maps.get(vehicle)
            for records in (self.positions[vehicle], self.attitudes[vehicle]):
                if clock is None:
                    records.clear()
                else:
                    while records and self._mapped_native(records[0], clock) < native_origin_ns:
                        records.popleft()

    @staticmethod
    def _mapped_native(record, clock):
        return record["source_ns"] - clock["source_anchor_ns"] + clock["native_anchor_ns"]

    def receive(self, vehicle, message):
        if vehicle not in self.vehicles:
            raise ValueError("unknown estimate owner")
        config = self.vehicles[vehicle]
        if (message.system_id != config.index + 1 or message.component_id != 1
                or type(message.system_id) is not int or type(message.component_id) is not int):
            return False
        if self.epoch is None or vehicle in self.failure:
            return False
        try:
            self._receive(vehicle, message)
            return True
        except Exception as error:
            self._invalidate(vehicle, f"invalid PX4 estimate source: {error}")
            return False

    def _receive(self, vehicle, message):
        config = self.vehicles[vehicle]
        name = message.message_name
        if name not in ("LOCAL_POSITION_NED", "ATTITUDE_QUATERNION", "GPS_GLOBAL_ORIGIN", "TIMESYNC"):
            return
        if not isinstance(message.fields_json, str) or len(message.fields_json) > 16_384:
            raise ValueError("oversized or non-string MAVLink estimate JSON")
        fields = json.loads(message.fields_json, parse_constant=_invalid_constant)
        if not isinstance(fields, dict):
            raise ValueError("MAVLink estimate fields must be an object")
        now = self._native_now()
        if vehicle in self.failure:
            return
        if name == "TIMESYNC":
            token = integer(fields.get("ts1"), "TIMESYNC token")
            pending = self.pending_sync.pop((vehicle, token), None)
            if pending is None:
                return
            source = integer(fields.get("tc1"), "TIMESYNC source clock", minimum=1)
            if (now is None or pending["native_ns"] != now or not self._paused()
                    or time.monotonic_ns() - pending["wall_ns"] > MAX_SYNC_WALL_NS):
                return
            previous = self.clock_maps.get(vehicle)
            residual = None
            if previous is not None:
                source_delta = source - previous["source_anchor_ns"]
                native_delta = now - previous["native_anchor_ns"]
                residual = source_delta - native_delta
                if source_delta < 0 or abs(residual) > SOURCE_RESOLUTION_NS:
                    self._invalidate(vehicle, "PX4 clock mapping regressed or diverged; new epoch required")
                    return
            self.clock_maps[vehicle] = {
                "source_anchor_ns": source, "native_anchor_ns": now,
                "method": "MAVLink TIMESYNC response at unchanged paused Gazebo frontier",
                "uncertainty_ns": SOURCE_RESOLUTION_NS,
                "source_resolution_ns": SOURCE_RESOLUTION_NS,
                "mapping_assumption": "PX4 lockstep boot clock; subsequent paused anchors checked within 1 ms",
                "clock_epoch": self.epoch, "source_clock": f"px4_boot:{config.index}",
                "residual_ns": residual,
            }
            self.sync_events[vehicle].set()
            return
        if name == "GPS_GLOBAL_ORIGIN":
            normalized = {
                "latitude": integer(fields["latitude"], "origin latitude", -900_000_000, 900_000_000),
                "longitude": integer(fields["longitude"], "origin longitude", -1_800_000_000, 1_800_000_000),
                "altitude": integer(fields["altitude"], "origin AMSL altitude", -(2**31), 2**31 - 1),
            }
        else:
            timestamp = integer(fields["time_boot_ms"], "PX4 boot milliseconds", maximum=2**32 - 1)
            normalized = {"time_boot_ms": timestamp}
            keys = (("x", "y", "z", "vx", "vy", "vz") if name == "LOCAL_POSITION_NED"
                    else ("q1", "q2", "q3", "q4"))
            normalized.update({key: finite(fields[key], key) for key in keys})
            if name == "ATTITUDE_QUATERNION":
                norm2 = sum(normalized[key] ** 2 for key in keys)
                if not 0.99 <= norm2 <= 1.01:
                    raise ValueError("PX4 attitude quaternion is not normalized")
                norm = math.sqrt(norm2)
                normalized.update({key: normalized[key] / norm for key in keys})
        record = {
            "message_name": name, "fields": normalized, "px4_instance": config.index,
            "system_id": message.system_id, "component_id": message.component_id,
            "received_wall_time": time.time_ns(), "available_native_ns": now,
            "clock_epoch": self.epoch, "run_id": self.run_id,
        }
        if name == "GPS_GLOBAL_ORIGIN":
            previous = self.origins.get(vehicle)
            if previous and previous["fields"] != normalized:
                self._invalidate(vehicle, "PX4 global origin changed within epoch; new epoch required")
                return
            self.origins[vehicle] = record
            return
        source_ns = normalized["time_boot_ms"] * SOURCE_RESOLUTION_NS
        record["source_ns"] = source_ns
        key = vehicle, name
        previous_ns = self.last_source.get(key)
        records = self.positions[vehicle] if name == "LOCAL_POSITION_NED" else self.attitudes[vehicle]
        if previous_ns is not None and source_ns < previous_ns:
            self._invalidate(vehicle, "PX4 source stamp reordered or boot clock reset; new epoch required")
            return
        if records and source_ns == records[-1]["source_ns"]:
            if normalized != records[-1]["fields"]:
                self._invalidate(vehicle, "conflicting PX4 source content at one boot timestamp")
            return
        self.last_source[key] = source_ns
        records.append(record)

    async def request(self, sessions, message_type):
        if self.epoch is None or message_type is None or not self._paused():
            return
        native_ns = self._native_now()
        if native_ns is None:
            return
        awaiting = []
        for vehicle, session in sessions.items():
            if vehicle not in self.vehicles or vehicle in self.failure:
                continue
            wall_ns = time.monotonic_ns()
            # Retry an unanswered paused frontier after one wall second, too;
            # neither repeated pulses nor a lost response can busy-loop/deadlock.
            if (native_ns - self.last_request_ns.get(vehicle, -(10**12)) < 1_000_000_000
                    and wall_ns - self.last_request_wall_ns.get(vehicle, -(10**18)) < 1_000_000_000):
                continue
            self.last_request_ns[vehicle], self.last_request_wall_ns[vehicle] = native_ns, wall_ns
            self.sync_events[vehicle].clear()
            awaiting.append(self.sync_events[vehicle])
            for key in [key for key in self.pending_sync if key[0] == vehicle]:
                del self.pending_sync[key]
            token = time.monotonic_ns()
            self.pending_sync[(vehicle, token)] = {"native_ns": native_ns, "wall_ns": wall_ns}
            system = self.vehicles[vehicle].index + 1

            async def send(name, fields):
                await asyncio.wait_for(session.mavlink_direct.send_message(
                    message_type(name, 0, 0, system, 1, json.dumps(fields, allow_nan=False))
                ), 0.5)

            try:
                await send("TIMESYNC", {"tc1": 0, "ts1": token})
                if vehicle not in self.streams_requested:
                    for message_id in (31, 32):
                        # MAV_CMD_SET_MESSAGE_INTERVAL, 50 Hz timestamped sources.
                        await send("COMMAND_LONG", {
                            "command": 511, "confirmation": 0,
                            "param1": message_id, "param2": 20_000,
                            "param3": 0, "param4": 0, "param5": 0, "param6": 0, "param7": 0,
                        })
                    self.streams_requested.add(vehicle)
                if vehicle not in self.origins:
                    # MAV_CMD_REQUEST_MESSAGE: GPS_GLOBAL_ORIGIN (49).
                    await send("COMMAND_LONG", {
                        "command": 512, "confirmation": 0, "param1": 49,
                        "param2": 0, "param3": 0, "param4": 0,
                        "param5": 0, "param6": 0, "param7": 0,
                    })
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._invalidate(vehicle, f"PX4 estimate stream request failed: {error}")
        if awaiting:
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in awaiting)), 0.05)
            except asyncio.TimeoutError:
                pass  # Missing replies leave sources unmapped, not fabricated.

    def sample(self, vehicle, now_sim_ns, target_sim_ns=None, max_age_ns=250_000_000):
        unavailable = {
            "status": "unavailable", "source": "PX4_MavlinkDirect",
            "run_id": self.run_id, "uav_id": vehicle, "clock_epoch": self.epoch,
            "source_sim_ns": None,
            "available_sim_ns": now_sim_ns if type(now_sim_ns) is int else None,
        }

        def missing(reason):
            return {**unavailable, "reason": str(reason)}

        if vehicle not in self.vehicles:
            return missing("unknown estimate owner")
        target = now_sim_ns if target_sim_ns is None else target_sim_ns
        if (type(now_sim_ns) is not int or not 0 <= now_sim_ns <= 2**63 - 1
                or type(target) is not int or not 0 <= target <= now_sim_ns
                or type(max_age_ns) is not int or not 0 <= max_age_ns <= MAX_ESTIMATE_AGE_NS):
            return missing("invalid estimate time boundary, target or freshness bound")
        now_native = self._native_now()
        if self.failure.get(vehicle):
            return missing(self.failure[vehicle])
        if now_native is None or now_native != now_sim_ns + self.episode_origin_ns:
            return missing("native source clock unavailable or does not match requested boundary")
        clock, origin = self.clock_maps.get(vehicle), self.origins.get(vehicle)
        if clock is None or origin is None or self.world_origin is None:
            return missing("source clock mapping or PX4 global origin unavailable")
        if (clock.get("clock_epoch") != self.epoch or origin["clock_epoch"] != self.epoch
                or origin["run_id"] != self.run_id):
            return missing("source clock/origin belongs to a different epoch")
        map_age = now_native - clock["native_anchor_ns"]
        if not 0 <= map_age <= CLOCK_MAP_MAX_AGE_NS:
            return missing("PX4 source clock map is stale or anchored in the future")
        if self.altitude_datum != "amsl":
            return missing("world/PX4 AMSL height datum has not been declared")

        def mapped(record):
            return self._mapped_native(record, clock) - self.episode_origin_ns

        positions, attitudes = self.positions[vehicle], self.attitudes[vehicle]
        if any(records and mapped(records[-1]) > now_sim_ns for records in (positions, attitudes)):
            return missing("PX4 source position or attitude timestamp is in the future")
        positions = [record for record in positions
                     if record["clock_epoch"] == self.epoch and record["run_id"] == self.run_id
                     and 0 <= mapped(record) <= target]
        attitudes = [record for record in attitudes
                     if record["clock_epoch"] == self.epoch and record["run_id"] == self.run_id
                     and 0 <= mapped(record) <= target]
        if not positions or not attitudes:
            return missing("causal episode-native PX4 position or attitude unavailable")
        position = positions[-1]
        attitude = min(attitudes, key=lambda record: abs(mapped(record) - mapped(position)))
        if (target - mapped(position) > max_age_ns or target - mapped(attitude) > max_age_ns
                or abs(mapped(attitude) - mapped(position)) > ATTITUDE_ALIGNMENT_NS):
            return missing("PX4 position/attitude sources stale or unsynchronized")
        p, q, gps = position["fields"], attitude["fields"], origin["fields"]
        try:
            offset = geodetic_to_enu(
                [gps["latitude"] / 1e7, gps["longitude"] / 1e7, gps["altitude"] / 1000],
                self.world_origin,
            )
            enu_position = [offset[0] + p["y"], offset[1] + p["x"], offset[2] - p["z"]]
            if not all(math.isfinite(value) for value in enu_position):
                return missing("PX4 position transform overflow")
        except (ValueError, OverflowError) as error:
            return missing(error)
        body_to_ned = [q["q2"], q["q3"], q["q4"], q["q1"]]
        body_to_enu = quaternion_product([math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0], body_to_ned)
        return {
            "status": "available", "source": "PX4_MavlinkDirect",
            "run_id": self.run_id, "uav_id": vehicle,
            "position_enu_m": enu_position,
            "velocity_enu_m_s": [p["vy"], p["vx"], -p["vz"]],
            "attitude_body_frd_to_world_enu_xyzw": body_to_enu,
            "source_sim_ns": mapped(position), "attitude_source_sim_ns": mapped(attitude),
            "available_sim_ns": now_sim_ns, "source_clock": clock["source_clock"],
            "clock_epoch": self.epoch, "clock_map": dict(clock),
            "origin_version": f"{self.epoch}:{vehicle}",
            "local_ned_origin_world_enu_m": offset, "height_datum": "amsl",
            "origin_source": "GPS_GLOBAL_ORIGIN", "source_messages": [position, attitude],
            "horizontal_sigma_m": None,
        }
