"""Read-only source-timed PX4 estimates through the existing MAVSDK owner.

Clock anchors use a TIMESYNC response while the Gazebo native clock remains
paused at the request frontier. A response across a physics step cannot anchor
that clock. Receive wall time is diagnostic only, never a sample timestamp.
"""

import asyncio
import json
import math
import time
from collections import deque


def geodetic_to_enu(point, origin):
    # Same WGS84 local tangent convention as commands.enu_to_wgs84.
    def ecef(value):
        latitude, longitude, height = value
        lat, lon = math.radians(latitude), math.radians(longitude)
        a, e2 = 6378137.0, 6.6943799901413165e-3
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        return (
            (n + height) * math.cos(lat) * math.cos(lon),
            (n + height) * math.cos(lat) * math.sin(lon),
            (n * (1 - e2) + height) * math.sin(lat),
        )

    delta = [a - b for a, b in zip(ecef(point), ecef(origin))]
    lat, lon = map(math.radians, origin[:2])
    x, y, z = delta
    return [
        -math.sin(lon) * x + math.cos(lon) * y,
        -math.sin(lat) * math.cos(lon) * x
        - math.sin(lat) * math.sin(lon) * y
        + math.cos(lat) * z,
        math.cos(lat) * math.cos(lon) * x
        + math.cos(lat) * math.sin(lon) * y
        + math.sin(lat) * z,
    ]


def quaternion_product(a, b):
    x, y, z, w = a
    xx, yy, zz, ww = b
    return [
        w * xx + x * ww + y * zz - z * yy,
        w * yy - x * zz + y * ww + z * xx,
        w * zz + x * yy - y * xx + z * ww,
        w * ww - x * xx - y * yy - z * zz,
    ]


class EstimateStreams:
    def __init__(self, vehicles):
        self.vehicles = {v.id: v for v in vehicles}
        self.positions = {v.id: deque(maxlen=1024) for v in vehicles}
        self.attitudes = {v.id: deque(maxlen=1024) for v in vehicles}
        self.origins, self.clock_maps, self.pending_sync = {}, {}, {}
        self.raw = {v.id: deque(maxlen=4096) for v in vehicles}
        self.native_clock = None
        self.native_paused = None
        self.epoch = None
        self.run_id = None
        self.world_origin = None
        self.episode_origin_ns = 0
        self.last_request_ns = {}
        self.streams_requested = set()
        self.sync_events = {v.id: asyncio.Event() for v in vehicles}
        self.altitude_datum = None
        self.failure = {}
        self.log_directory = None

    def configure(
        self,
        run_id,
        clock_epoch,
        world_origin_wgs84,
        native_clock_callable,
        episode_origin_ns=0,
        altitude_datum=None,
        log_directory=None,
        native_paused_callable=None,
    ):
        if self.epoch is not None and self.epoch != clock_epoch:
            self.origins.clear()
            self.clock_maps.clear()
            self.pending_sync.clear()
            self.last_request_ns.clear()
            self.streams_requested.clear()
            self.failure.clear()
            for records in (*self.positions.values(), *self.attitudes.values(), *self.raw.values()):
                records.clear()
        self.run_id, self.epoch = run_id, clock_epoch
        self.world_origin, self.native_clock = world_origin_wgs84, native_clock_callable
        self.episode_origin_ns = episode_origin_ns
        # GPS_GLOBAL_ORIGIN.altitude is AMSL. Never silently mix an ellipsoid datum.
        self.altitude_datum = altitude_datum
        self.log_directory = log_directory
        self.native_paused = native_paused_callable

    def receive(self, vehicle, message):
        config = self.vehicles[vehicle]
        if message.system_id != config.index + 1 or message.component_id != 1:
            return
        fields = json.loads(message.fields_json)
        name = message.message_name
        now = self.native_clock() if self.native_clock else None
        record = {
            "message_name": name,
            "fields": fields,
            "px4_instance": config.index,
            "system_id": message.system_id,
            "component_id": message.component_id,
            "received_wall_time": time.time_ns(),
            "available_native_ns": now,
            "clock_epoch": self.epoch,
        }
        self.raw[vehicle].append(record)
        if self.log_directory is not None:
            with (self.log_directory / (vehicle + "-raw-mavlink-estimates.jsonl")).open(
                "a"
            ) as log:
                log.write(json.dumps(record) + "\n")
        if name == "TIMESYNC":
            token = int(fields.get("ts1", 0))
            pending = self.pending_sync.pop((vehicle, token), None)
            if (
                pending is not None
                and now is not None
                and pending == now
                and self.native_paused
                and self.native_paused()
            ):
                source = int(fields["tc1"])
                if source <= 0:
                    return
                self.clock_maps[vehicle] = {
                    "source_anchor_ns": source,
                    "native_anchor_ns": now,
                    "method": "MAVLink TIMESYNC response at unchanged paused Gazebo frontier",
                    "uncertainty_ns": None,
                    "source_resolution_ns": 1_000_000,
                    "mapping_assumption": "PX4 lockstep boot clock at unchanged paused frontier; residual not yet measured",
                    "clock_epoch": self.epoch,
                    "source_clock": f"px4_boot:{config.index}",
                }
                self.sync_events[vehicle].set()
            return
        if name == "GPS_GLOBAL_ORIGIN":
            self.origins[vehicle] = record
            return
        if name not in ("LOCAL_POSITION_NED", "ATTITUDE_QUATERNION"):
            return
        timestamp = int(fields["time_boot_ms"]) * 1_000_000
        record["source_ns"] = timestamp
        records = (
            self.positions[vehicle]
            if name == "LOCAL_POSITION_NED"
            else self.attitudes[vehicle]
        )
        if records and timestamp < records[-1]["source_ns"]:
            self.failure[vehicle] = (
                "PX4 boot clock regressed; reset epoch and origin before using samples"
            )
            self.clock_maps.pop(vehicle, None)
            records.clear()
            return
        records.append(record)

    async def request(self, sessions, message_type):
        if (
            self.native_clock is None
            or self.native_paused is None
            or not self.native_paused()
        ):
            return
        native_ns = self.native_clock()
        awaiting = []
        for vehicle, session in sessions.items():
            # Retry at most once per native second; the owner may call between
            # barriers. A missing response remains unavailable, not a deadlock.
            if native_ns - self.last_request_ns.get(vehicle, -(10**12)) < 1_000_000_000:
                continue
            self.last_request_ns[vehicle] = native_ns
            self.sync_events[vehicle].clear()
            awaiting.append(self.sync_events[vehicle])
            token = time.monotonic_ns()
            self.pending_sync = {
                key: value
                for key, value in self.pending_sync.items()
                if key[0] != vehicle
            }
            self.pending_sync[(vehicle, token)] = native_ns
            await session.mavlink_direct.send_message(
                message_type(
                    "TIMESYNC",
                    0,
                    0,
                    self.vehicles[vehicle].index + 1,
                    1,
                    json.dumps({"tc1": 0, "ts1": token}),
                )
            )
            if vehicle not in self.streams_requested:
                # Explicitly request timestamp-bearing attitude and local pose.
                # MAV_CMD_SET_MESSAGE_INTERVAL: param1 message ID, param2 usec.
                # https://mavlink.io/en/messages/common.html#MAV_CMD_SET_MESSAGE_INTERVAL
                for message_id in (31, 32):
                    await session.mavlink_direct.send_message(
                        message_type(
                            "COMMAND_LONG", 0, 0, self.vehicles[vehicle].index + 1, 1,
                            json.dumps({
                                "command": 511, "confirmation": 0,
                                "param1": message_id, "param2": 20_000,
                                "param3": 0, "param4": 0, "param5": 0,
                                "param6": 0, "param7": 0,
                            }),
                        )
                    )
                self.streams_requested.add(vehicle)
            if vehicle not in self.origins:
                await session.mavlink_direct.send_message(
                    message_type(
                        "COMMAND_LONG",
                        0,
                        0,
                        self.vehicles[vehicle].index + 1,
                        1,
                        json.dumps(
                            {
                                "command": 512,
                                "confirmation": 0,
                                "param1": 49,
                                "param2": 0,
                                "param3": 0,
                                "param4": 0,
                                "param5": 0,
                                "param6": 0,
                                "param7": 0,
                            }
                        ),
                    )
                )
        if awaiting:
            try:
                # Real reply may arrive on another native thread. Keep the
                # world paused for a bounded wall window, never advance here.
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in awaiting)), timeout=.05)
            except asyncio.TimeoutError:
                pass  # no invented map: estimates remain unavailable

    def sample(self, vehicle, now_sim_ns, target_sim_ns=None, max_age_ns=250_000_000):
        unavailable = {
            "status": "unavailable",
            "source": "PX4_MavlinkDirect",
            "source_sim_ns": None,
            "clock_epoch": self.epoch,
        }
        if self.failure.get(vehicle):
            return {**unavailable, "reason": self.failure[vehicle]}
        clock, origin = self.clock_maps.get(vehicle), self.origins.get(vehicle)
        if clock is None or origin is None or self.world_origin is None:
            return {
                **unavailable,
                "reason": "source clock mapping or PX4 global origin unavailable",
            }
        if self.altitude_datum != "amsl":
            return {
                **unavailable,
                "reason": "world/PX4 AMSL height datum has not been declared",
            }

        def mapped(record):
            return (
                record["source_ns"]
                - clock["source_anchor_ns"]
                + clock["native_anchor_ns"]
                - self.episode_origin_ns
            )

        target = now_sim_ns if target_sim_ns is None else target_sim_ns
        positions = [
            p
            for p in self.positions[vehicle]
            if p["clock_epoch"] == self.epoch and mapped(p) <= now_sim_ns
        ]
        attitudes = [
            p
            for p in self.attitudes[vehicle]
            if p["clock_epoch"] == self.epoch and mapped(p) <= now_sim_ns
        ]
        if not positions or not attitudes:
            return {
                **unavailable,
                "reason": "source position or attitude unavailable at this boundary",
            }
        position = min(positions, key=lambda p: abs(mapped(p) - target))
        attitude = min(attitudes, key=lambda p: abs(mapped(p) - mapped(position)))
        if (
            abs(mapped(position) - target) > max_age_ns
            or abs(mapped(attitude) - mapped(position)) > 40_000_000
        ):
            return {
                **unavailable,
                "reason": "PX4 position/attitude sources stale or unsynchronized",
            }
        p, q, gps = position["fields"], attitude["fields"], origin["fields"]
        offset = geodetic_to_enu(
            [gps["latitude"] / 1e7, gps["longitude"] / 1e7, gps["altitude"] / 1000],
            self.world_origin,
        )
        body_to_ned = [q["q2"], q["q3"], q["q4"], q["q1"]]
        body_to_enu = quaternion_product(
            [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0], body_to_ned
        )
        return {
            "status": "available",
            "source": "PX4_MavlinkDirect",
            "position_enu_m": [
                offset[0] + p["y"],
                offset[1] + p["x"],
                offset[2] - p["z"],
            ],
            "velocity_enu_m_s": [p["vy"], p["vx"], -p["vz"]],
            "attitude_body_frd_to_world_enu_xyzw": body_to_enu,
            "source_sim_ns": mapped(position),
            "attitude_source_sim_ns": mapped(attitude),
            "available_sim_ns": now_sim_ns,
            "source_clock": clock["source_clock"],
            "clock_epoch": self.epoch,
            "clock_map": clock,
            "origin_version": f"{self.epoch}:{vehicle}",
            "local_ned_origin_world_enu_m": offset,
            "height_datum": "amsl",
            "origin_source": "GPS_GLOBAL_ORIGIN",
            "source_messages": [position, attitude],
            "horizontal_sigma_m": None,
        }
