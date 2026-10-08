"""Acceptance is a queue decision; terminal success requires observed state."""

import asyncio
import math

from .config import fields, identifier, number

ACTIONS = ("arm", "takeoff", "goto_location", "hold", "land", "disarm")


def enu_to_wgs84(enu, origin):
    """WGS84 local tangent coordinates to geodetic coordinates (metres/degrees)."""
    lat, lon, alt = origin
    lat, lon = math.radians(lat), math.radians(lon)
    a, e2 = 6378137.0, 6.6943799901413165e-3
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    x, y, z = (
        (n + alt) * math.cos(lat) * math.cos(lon),
        (n + alt) * math.cos(lat) * math.sin(lon),
        (n * (1 - e2) + alt) * math.sin(lat),
    )
    east, north, up = enu
    x += (
        -math.sin(lon) * east
        - math.sin(lat) * math.cos(lon) * north
        + math.cos(lat) * math.cos(lon) * up
    )
    y += (
        math.cos(lon) * east
        - math.sin(lat) * math.sin(lon) * north
        + math.cos(lat) * math.sin(lon) * up
    )
    z += math.cos(lat) * north + math.sin(lat) * up
    longitude, p = math.atan2(y, x), math.hypot(x, y)
    latitude = math.atan2(z, p * (1 - e2))
    for _ in range(8):
        n = a / math.sqrt(1 - e2 * math.sin(latitude) ** 2)
        height = p / math.cos(latitude) - n
        latitude = math.atan2(z, p * (1 - e2 * n / (n + height)))
    return math.degrees(latitude), math.degrees(longitude), height


class Commands:
    def __init__(self, config, telemetry, altitude_offsets):
        self.config, self.telemetry = config, telemetry
        self.altitude_offsets = altitude_offsets
        self.records, self.updates = {}, []

    def accept(self, payload, sim_ns):
        fields(payload, ("vehicle", "action", "params", "command_id"))
        cid = identifier(payload["command_id"])
        vehicle = identifier(payload["vehicle"])
        action, params = payload["action"], payload["params"]
        if cid in self.records:
            return {
                "status": "rejected",
                "reason": "command_id already used",
                "command_id": cid,
            }
        if vehicle not in self.telemetry.sessions or action not in ACTIONS:
            return {
                "status": "rejected",
                "reason": "unknown vehicle or unsupported action",
                "command_id": cid,
            }
        if any(
            r["vehicle"] == vehicle and r["status"] in ("queued", "running")
            for r in self.records.values()
        ):
            return {
                "status": "rejected",
                "reason": "vehicle already has active command",
                "command_id": cid,
            }
        if action == "takeoff":
            fields(params, ("altitude_m",))
            height = number(params["altitude_m"], "altitude_m")
            if not 0 < height <= 1000:
                raise ValueError("takeoff altitude_m outside (0,1000]")
            params = {"altitude_m": height}
        elif action == "goto_location":
            fields(params, ("position_enu", "yaw_deg"))
            position = params["position_enu"]
            if not isinstance(position, list) or len(position) != 3:
                raise ValueError("position_enu requires 3 numbers")
            params = {
                "position_enu": [number(v, "position_enu") for v in position],
                "yaw_deg": number(params["yaw_deg"], "yaw_deg"),
            }
        else:
            fields(params, ())
        self.records[cid] = dict(
            command_id=cid,
            vehicle=vehicle,
            action=action,
            params=params,
            status="queued",
            submitted_ns=sim_ns,
            task=None,
            observed_since=None,
        )
        return {"status": "accepted", "command_id": cid, "sim_ns": sim_ns}

    async def issue(self, record):
        session = self.telemetry.sessions[record["vehicle"]]
        action, params = record["action"], record["params"]
        if action == "takeoff":
            await session.action.set_takeoff_altitude(params["altitude_m"])
            await session.action.takeoff()
        elif action == "goto_location":
            lat, lon, _ = enu_to_wgs84(params["position_enu"], self.config.origin_wgs84)
            # Use the real PX4 vertical datum measured at stationary reset,
            # rather than equating ellipsoidal altitude with PX4 AMSL.
            alt = params["position_enu"][2] + self.altitude_offsets[record["vehicle"]]
            await session.action.goto_location(lat, lon, alt, params["yaw_deg"])
        else:
            await getattr(session.action, action)()

    async def dispatch(self, sim_ns):
        dispatched = False
        for record in self.records.values():
            if record["status"] == "queued":
                dispatched = True
                record["status"] = "running"
                record["versions_at_dispatch"] = dict(
                    self.telemetry.versions[record["vehicle"]]
                )
                record["task"] = asyncio.create_task(
                    asyncio.wait_for(self.issue(record), 30)
                )
                self.emit(record, "running", sim_ns)
        # Allow grpc dispatch and native subscriber threads to latch commands
        # while physics remains paused. No hidden integration occurs here.
        if dispatched:
            await asyncio.sleep(0.01)

    def emit(self, record, status, sim_ns, reason=None):
        record["status"] = status
        update = {
            key: record[key] for key in ("command_id", "vehicle", "action", "status")
        }
        update["sim_ns"] = sim_ns
        if reason is not None:
            update["reason"] = reason
        self.updates.append(update)

    def observe(self, samples, sim_ns):
        by_vehicle = {s["vehicle"]: s for s in samples}
        for record in self.records.values():
            if record["status"] != "running":
                continue
            task = record["task"]
            if task.done() and task.exception() is not None:
                self.emit(record, "failed", sim_ns, str(task.exception()))
                continue
            if sim_ns - record["submitted_ns"] > 180_000_000_000:
                task.cancel()
                self.emit(
                    record,
                    "failed",
                    sim_ns,
                    "observed completion exceeded 180 sim seconds",
                )
                continue
            if not task.done():
                continue
            sample, action = by_vehicle[record["vehicle"]], record["action"]
            keys = {
                "arm": ("armed",),
                "disarm": ("armed",),
                "hold": ("flight_mode", "velocity_enu"),
                "takeoff": ("landed_state", "armed", "velocity_enu", "global_position"),
                "land": ("landed_state", "armed"),
                "goto_location": ("velocity_enu", "armed", "landed_state"),
            }[action]
            # Require each field used by the completion predicate to have been
            # observed after the action acknowledgment, not just queue dispatch.
            if "ack_versions" not in record:
                record["ack_versions"] = dict(
                    self.telemetry.versions[record["vehicle"]]
                )
            if sample["sim_ns"] != sim_ns:
                raise RuntimeError("command observation has a stale physical pose")
            ages = sample["freshness"]["mavsdk_receipt_age_wall_ns"]
            if any(ages[key] > 30_000_000_000 for key in keys):
                record["observed_since"] = None
                continue
            versions = self.telemetry.versions[record["vehicle"]]
            if not all(versions[key] > record["ack_versions"][key] for key in keys):
                continue
            speed = math.sqrt(sum(v * v for v in sample["velocity_enu"]))
            if action == "arm":
                reached = sample["armed"]
            elif action == "disarm":
                reached = not sample["armed"]
            elif action == "land":
                reached = sample["landed_state"] == "ON_GROUND" and not sample["armed"]
            elif action == "hold":
                reached = sample["flight_mode"] == "HOLD" and speed < 1
            elif action == "takeoff":
                cache = self.telemetry.cache[record["vehicle"]]["global_position"]
                reached = (
                    sample["landed_state"] == "IN_AIR"
                    and sample["armed"]
                    and abs(
                        cache["relative_altitude_m"] - record["params"]["altitude_m"]
                    )
                    <= 1
                    and speed < 1.5
                )
            else:
                reached = (
                    sample["armed"]
                    and sample["landed_state"] == "IN_AIR"
                    and speed < 1.5
                    and math.dist(
                        sample["position_enu"], record["params"]["position_enu"]
                    )
                    <= 2
                )
            if reached:
                if record["observed_since"] is None:
                    record["observed_since"] = sim_ns
                delay = 0 if action in ("arm", "disarm") else 1_000_000_000
                if sim_ns - record["observed_since"] >= delay:
                    self.emit(record, "succeeded", sim_ns)
            else:
                record["observed_since"] = None

    def drain(self):
        updates, self.updates = self.updates, []
        return updates

    async def stop(self):
        tasks = [r["task"] for r in self.records.values() if r["task"] is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
