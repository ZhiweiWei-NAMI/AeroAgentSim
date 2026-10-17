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
    def __init__(self, config, telemetry, altitude_offsets, *, estimate_completion=False):
        self.config, self.telemetry = config, telemetry
        self.altitude_offsets = altitude_offsets
        self.estimate_completion = estimate_completion
        self.records, self.updates = {}, []
        self.blocked = set()

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
        if vehicle in self.blocked or any(
            r["vehicle"] == vehicle and r["status"] in ("queued", "running", "cancel_requested")
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

    def cancel(self, payload, sim_ns):
        """Register cancellation without occupying the world's advance connection."""
        fields(payload, ("vehicle", "command_id"))
        cid, vehicle = payload["command_id"], payload["vehicle"]
        record = self.records.get(cid)
        if record is None or record["vehicle"] != vehicle:
            return {"status": "rejected", "reason": "unknown command for this vehicle"}
        if record["status"] == "cancel_requested":
            return {"status": "accepted", "command_id": cid,
                    "sim_ns": record["cancel_accepted_ns"]}
        if record["status"] not in ("queued", "running"):
            return {"status": "rejected", "reason": "command already terminal"}
        record["cancel_accepted_ns"] = sim_ns
        record["observed_since"] = None
        if record["status"] == "queued":
            self.emit(record, "canceled", sim_ns, "canceled before dispatch")
        else:
            # Do NOT cancel the grpc Future and mistake it for canceled MAVLink
            # retries. Wait for the original SDK call's terminal response first.
            # A timeout leaves the vehicle quarantined rather than allowing an
            # old request to race a new route.
            record["status"] = "cancel_requested"
            record["original_task"] = record["task"]
            record["cancel_hold_started"] = False
        return {"status": "accepted", "command_id": cid, "sim_ns": sim_ns}

    def fail_cancel(self, record, sim_ns, reason):
        self.blocked.add(record["vehicle"])
        self.emit(record, "failed", sim_ns, reason + "; vehicle quarantined until reset")

    async def dispatch_cancel(self, record, sim_ns):
        if sim_ns - record["cancel_accepted_ns"] > 5_000_000_000:
            self.fail_cancel(record, sim_ns, "cancel did not observe hold within 5 sim seconds")
            return
        if record["cancel_hold_started"]:
            return
        old = record["original_task"]
        if not old.done():
            return
        if old.cancelled() or isinstance(old.exception(), (TimeoutError, asyncio.CancelledError)):
            self.fail_cancel(record, sim_ns, "old low-level request termination is unconfirmed")
            return
        if old.exception() is not None:
            # Unknown transport errors do not prove that server retries stopped.
            # A normal MAVSDK action rejection is a completed command response.
            if type(old.exception()).__name__ != "ActionError":
                self.fail_cancel(record, sim_ns, "old low-level request failed without a terminal PX4 response")
                return
        session = self.telemetry.sessions[record["vehicle"]]
        record["task"] = asyncio.create_task(asyncio.wait_for(session.action.hold(), 30))
        record["cancel_hold_started"] = True
        record.pop("ack_versions", None)

    async def issue(self, record):
        session = self.telemetry.sessions[record["vehicle"]]
        action, params = record["action"], record["params"]
        if action == "takeoff":
            await session.action.set_takeoff_altitude(params["altitude_m"])
            await session.action.takeoff()
        elif action == "goto_location":
            lat, lon, _ = enu_to_wgs84(params["position_enu"], self.config.origin_wgs84)
            # Companion uses the declared public world AMSL origin. Legacy
            # adapter profiles retain their stationary-reset datum mapping.
            alt = params["position_enu"][2] + self.altitude_offsets[record["vehicle"]]
            await session.action.goto_location(lat, lon, alt, params["yaw_deg"])
        else:
            await getattr(session.action, action)()

    async def dispatch(self, sim_ns):
        dispatched = False
        for record in self.records.values():
            if record["status"] == "cancel_requested":
                await self.dispatch_cancel(record, sim_ns)
                dispatched = dispatched or record.get("cancel_hold_started", False)
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
            canceling = record["status"] == "cancel_requested"
            if record["status"] not in ("running", "cancel_requested"):
                continue
            if canceling and sim_ns - record["cancel_accepted_ns"] > 5_000_000_000:
                mode = by_vehicle[record["vehicle"]].get("flight_mode", "unknown")
                self.fail_cancel(record, sim_ns, f"cancel hold deadline exceeded; current mode={mode}")
                continue
            if canceling and not record.get("cancel_hold_started"):
                continue
            task = record["task"]
            if task.done() and task.cancelled():
                self.blocked.add(record["vehicle"])
                self.emit(record, "failed", sim_ns, "low-level request canceled without confirmed termination")
                continue
            if task.done() and task.exception() is not None:
                if canceling or isinstance(task.exception(), TimeoutError):
                    self.blocked.add(record["vehicle"])
                self.emit(record, "failed", sim_ns, str(task.exception()))
                continue
            if sim_ns - record["submitted_ns"] > 180_000_000_000:
                task.cancel()
                self.blocked.add(record["vehicle"])
                self.emit(
                    record,
                    "failed",
                    sim_ns,
                    "observed completion exceeded 180 sim seconds",
                )
                continue
            if not task.done():
                continue
            sample = by_vehicle[record["vehicle"]]
            action = "hold" if canceling else record["action"]
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
            estimate = sample.get("vehicle_estimate", {})
            use_estimate = self.estimate_completion or canceling
            if use_estimate and action in ("goto_location", "hold", "takeoff"):
                if estimate.get("status") != "available":
                    record["observed_since"] = None
                    continue
                speed = math.sqrt(sum(v * v for v in estimate["velocity_enu_m_s"]))
            else:
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
                        estimate["position_enu_m"] if use_estimate else sample["position_enu"],
                        record["params"]["position_enu"]
                    )
                    <= 2
                )
            if reached:
                if record["observed_since"] is None:
                    record["observed_since"] = sim_ns
                delay = 0 if action in ("arm", "disarm") else 1_000_000_000
                if sim_ns - record["observed_since"] >= delay:
                    self.emit(record, "canceled" if canceling else "succeeded", sim_ns)
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
