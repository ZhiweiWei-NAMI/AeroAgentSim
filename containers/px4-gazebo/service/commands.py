"""Acceptance is a queue decision; terminal success requires observed state."""

import asyncio
import math

from .config import fields, identifier, number

ACTIONS = ("arm", "takeoff", "goto_location", "hold", "land", "disarm")
ACTIVE = ("queued", "running", "cancel_requested")
OBSERVATION_KEYS = {
    "arm": ("armed",),
    "disarm": ("armed",),
    "hold": ("flight_mode", "velocity_enu"),
    "takeoff": ("landed_state", "armed", "velocity_enu", "global_position"),
    "land": ("landed_state", "armed"),
    "goto_location": ("velocity_enu", "armed", "landed_state"),
}
CANCEL_TIMEOUT_NS = 5_000_000_000
OBSERVATION_TIMEOUT_NS = 180_000_000_000
RECEIPT_MAX_AGE_NS = 30_000_000_000
ESTIMATE_MAX_AGE_NS = 1_000_000_000


def terminal_action_rejection(error):
    """Only an actual, definitive SDK action result can release an old request."""
    # Keep host-side imports independent of MAVSDK. A matching exception class
    # name, transport failure, UNKNOWN, or SDK TIMEOUT proves no such result.
    try:
        from mavsdk.action import ActionError, ActionResult
    except ImportError:
        return False
    if not isinstance(error, ActionError):
        return False
    result = getattr(error, "_result", None)
    return (
        isinstance(result, ActionResult)
        and isinstance(result.result, ActionResult.Result)
        and result.result.name
        in (
            "COMMAND_DENIED",
            "COMMAND_DENIED_LANDED_STATE_UNKNOWN",
            "COMMAND_DENIED_NOT_LANDED",
            "NO_VTOL_TRANSITION_SUPPORT",
            "PARAMETER_ERROR",
            "UNSUPPORTED",
            "FAILED",
            "INVALID_ARGUMENT",
        )
    )


def request_state(task):
    if task is None:
        return "ambiguous", "low-level request state is unknown"
    if not task.done():
        return "pending", None
    if task.cancelled():
        return "ambiguous", "low-level request canceled without confirmed termination"
    error = task.exception()
    if error is None:
        return "acknowledged", None
    if terminal_action_rejection(error):
        return "rejected", str(error)
    return "ambiguous", f"low-level request has no confirmed terminal response: {error}"


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def vector3(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(finite_number(component) for component in value)
    )


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
        # Only a reset (a new Commands instance) can clear ambiguous native work.
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
        if vehicle in self.blocked:
            return {
                "status": "rejected",
                "reason": "vehicle quarantined until reset",
                "command_id": cid,
            }
        if any(
            r["vehicle"] == vehicle and r["status"] in ACTIVE
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
            dispatched=False,
            observed_since=None,
        )
        return {"status": "accepted", "command_id": cid, "sim_ns": sim_ns}

    def cancel(self, payload, sim_ns):
        """Accept a request, not a claim that a native action has been stopped."""
        fields(payload, ("vehicle", "command_id"))
        vehicle = identifier(payload["vehicle"])
        cid = identifier(payload["command_id"])
        record = self.records.get(cid)
        if record is None or record["vehicle"] != vehicle:
            return {
                "status": "rejected",
                "reason": "unknown command for this vehicle",
                "command_id": cid,
                "sim_ns": sim_ns,
            }
        if record["status"] == "cancel_requested":
            return {
                "status": "accepted",
                "command_id": cid,
                "sim_ns": record["cancel_accepted_ns"],
            }
        # In particular, observed completion winning the race cannot be changed
        # to cancellation, nor can a completed ACK alone win that race.
        if record["status"] not in ("queued", "running"):
            return {
                "status": "rejected",
                "reason": "command already terminal",
                "command_id": cid,
                "sim_ns": sim_ns,
            }
        record["cancel_accepted_ns"] = sim_ns
        self.reset_observation(record)
        if record["status"] == "queued":
            if record["task"] is None and not record["dispatched"]:
                self.emit(record, "canceled", sim_ns, "canceled before dispatch")
            else:
                self.quarantine(record, sim_ns, "pre-dispatch state is unconfirmed")
        else:
            # Never cancel the grpc Future and equate that with stopped MAVLink
            # retries. HOLD must not race the original low-level request.
            record["original_task"] = record["task"]
            record["cancel_hold_started"] = False
            self.emit(record, "cancel_requested", sim_ns)
        return {"status": "accepted", "command_id": cid, "sim_ns": sim_ns}

    def quarantine(self, record, sim_ns, reason):
        self.blocked.add(record["vehicle"])
        record["observed_since"] = None
        self.emit(record, "failed", sim_ns, reason + "; vehicle quarantined until reset")

    @staticmethod
    def reset_observation(record):
        record["observed_since"] = None
        for key in (
            "ack_versions",
            "ack_observed_ns",
            "observation_versions",
            "last_observation_ns",
            "estimate_identity",
            "estimate_source_ns",
            "estimate_attitude_ns",
        ):
            record.pop(key, None)

    async def issue_hold(self, record):
        await self.telemetry.sessions[record["vehicle"]].action.hold()

    async def dispatch_cancel(self, record, sim_ns):
        if sim_ns - record["cancel_accepted_ns"] > CANCEL_TIMEOUT_NS:
            self.quarantine(record, sim_ns, "cancel did not observe HOLD within 5 sim seconds")
            return False
        if record["cancel_hold_started"]:
            return False
        state, reason = request_state(record["original_task"])
        if state == "pending":
            return False
        if state == "ambiguous":
            self.quarantine(record, sim_ns, reason)
            return False
        # A success or definitive action rejection terminates the original SDK
        # request. Neither proves stopped motion: obtain HOLD and observe it.
        record["task"] = asyncio.create_task(
            asyncio.wait_for(self.issue_hold(record), 30)
        )
        record["cancel_hold_started"] = True
        self.reset_observation(record)
        return True

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
            if record["status"] == "cancel_requested":
                dispatched = await self.dispatch_cancel(record, sim_ns) or dispatched
            elif record["status"] == "queued":
                if record["vehicle"] in self.blocked:
                    self.quarantine(record, sim_ns, "native work remains ambiguous")
                    continue
                dispatched = True
                record["dispatched"] = True
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

    def fresh_observation(self, record, sample, action, sim_ns):
        if not isinstance(sample, dict) or sample.get("sim_ns") != sim_ns:
            return False
        previous_ns = record.get("last_observation_ns")
        if previous_ns is not None and sim_ns <= previous_ns:
            return False
        record["last_observation_ns"] = sim_ns
        freshness = sample.get("freshness", {})
        if not isinstance(freshness, dict):
            return False
        ages = freshness.get("mavsdk_receipt_age_wall_ns", {})
        # Prefer the snapshot's versions; a newer live stream must not make an
        # older sample appear fresh. Older legacy producers expose only live versions.
        versions = freshness.get(
            "mavsdk_versions", self.telemetry.versions[record["vehicle"]]
        )
        if not isinstance(ages, dict) or not isinstance(versions, dict):
            return False
        previous = record.get("observation_versions", {})
        for key in OBSERVATION_KEYS[action]:
            age, version = ages.get(key), versions.get(key)
            if (
                type(age) is not int
                or not 0 <= age <= RECEIPT_MAX_AGE_NS
                or type(version) is not int
                or version <= record["ack_versions"].get(key, 0)
                or version < previous.get(key, 0)
            ):
                return False
        record["observation_versions"] = {
            key: versions[key] for key in OBSERVATION_KEYS[action]
        }
        return True

    def completion_estimate(self, record, sample, sim_ns):
        estimate = sample.get("vehicle_estimate")
        if not isinstance(estimate, dict) or estimate.get("status") != "available":
            return None
        source_ns = estimate.get("source_sim_ns")
        attitude_ns = estimate.get("attitude_source_sim_ns")
        if (
            type(estimate.get("available_sim_ns")) is not int
            or estimate["available_sim_ns"] != sim_ns
            or type(source_ns) is not int
            or type(attitude_ns) is not int
            or source_ns < 0
            or attitude_ns < 0
            or source_ns <= record["ack_observed_ns"]
            or not 0 <= sim_ns - source_ns <= ESTIMATE_MAX_AGE_NS
            or not 0 <= sim_ns - attitude_ns <= ESTIMATE_MAX_AGE_NS
            or not vector3(estimate.get("position_enu_m"))
            or not vector3(estimate.get("velocity_enu_m_s"))
        ):
            return None
        identity = tuple(
            estimate.get(key)
            for key in ("run_id", "uav_id", "clock_epoch", "origin_version")
        )
        if (
            any(value is None for value in identity)
            or identity[1] != record["vehicle"]
            or not estimate.get("source_clock")
            or estimate.get("clock_map") is None
        ):
            return None
        if record.get("estimate_identity") != identity:
            record["observed_since"] = None
            record["estimate_identity"] = identity
            record["estimate_source_ns"] = source_ns
            record["estimate_attitude_ns"] = attitude_ns
        else:
            # A still-valid repeated estimate preserves dwell, but cannot advance
            # it. Keep the watermark on reordering so a replay cannot restore an
            # interrupted duration by moving the watermark backward.
            if (
                source_ns < record["estimate_source_ns"]
                or attitude_ns < record["estimate_attitude_ns"]
            ):
                return None
            record["estimate_source_ns"] = source_ns
            record["estimate_attitude_ns"] = attitude_ns
        return estimate

    def reached(self, record, sample, action, sim_ns):
        if action == "arm":
            return sample.get("armed") is True
        if action == "disarm":
            return sample.get("armed") is False
        if action == "land":
            return sample.get("landed_state") == "ON_GROUND" and sample.get("armed") is False
        velocity, position = sample.get("velocity_enu"), sample.get("position_enu")
        if self.estimate_completion:
            estimate = self.completion_estimate(record, sample, sim_ns)
            if estimate is None:
                return False
            velocity, position = estimate["velocity_enu_m_s"], estimate["position_enu_m"]
        if not vector3(velocity):
            return False
        speed = math.sqrt(sum(value * value for value in velocity))
        if action == "hold":
            return sample.get("flight_mode") == "HOLD" and speed < 1
        if action == "takeoff":
            # The relative-altitude predicate remains the legacy PX4 predicate;
            # telemetry exposes it in the sample rather than a private cache.
            altitude = sample.get("relative_altitude_m")
            return (
                sample.get("landed_state") == "IN_AIR"
                and sample.get("armed") is True
                and finite_number(altitude)
                and abs(altitude - record["params"]["altitude_m"]) <= 1
                and speed < 1.5
            )
        return (
            sample.get("armed") is True
            and sample.get("landed_state") == "IN_AIR"
            and speed < 1.5
            and vector3(position)
            and math.dist(position, record["params"]["position_enu"]) <= 2
        )

    def observe(self, samples, sim_ns):
        by_vehicle = {sample["vehicle"]: sample for sample in samples}
        for record in self.records.values():
            canceling = record["status"] == "cancel_requested"
            if record["status"] not in ("running", "cancel_requested"):
                continue
            if canceling:
                if sim_ns - record["cancel_accepted_ns"] > CANCEL_TIMEOUT_NS:
                    self.quarantine(record, sim_ns, "cancel did not observe HOLD within 5 sim seconds")
                    continue
                if not record["cancel_hold_started"]:
                    # Notice ambiguity even when the next dispatch has not run.
                    state, reason = request_state(record["original_task"])
                    if state == "ambiguous":
                        self.quarantine(record, sim_ns, reason)
                    continue
            state, reason = request_state(record["task"])
            if state == "ambiguous":
                self.quarantine(record, sim_ns, reason)
                continue
            if state == "rejected":
                if canceling:
                    self.quarantine(record, sim_ns, "cancel HOLD rejected: " + reason)
                else:
                    self.emit(record, "failed", sim_ns, reason)
                continue
            if not canceling and sim_ns - record["submitted_ns"] > OBSERVATION_TIMEOUT_NS:
                # Do not cancel a Future and pretend the SDK's native retry has
                # ended. Retain pending work for reset/stop to collect.
                self.quarantine(record, sim_ns, "observed completion exceeded 180 sim seconds")
                continue
            if state != "acknowledged":
                continue
            action = "hold" if canceling else record["action"]
            # ACK is not completion. Snapshot the receipt versions and require
            # every predicate's telemetry to have been received after this ACK.
            if "ack_versions" not in record:
                record["ack_versions"] = dict(
                    self.telemetry.versions[record["vehicle"]]
                )
                record["ack_observed_ns"] = sim_ns
            sample = by_vehicle.get(record["vehicle"])
            if not self.fresh_observation(record, sample, action, sim_ns):
                record["observed_since"] = None
                continue
            if self.reached(record, sample, action, sim_ns):
                # Estimate-backed motion predicates accrue source time, not the
                # faster polling frontier at which a cached estimate is exposed.
                dwell_ns = (
                    record["estimate_source_ns"]
                    if self.estimate_completion and action in ("hold", "takeoff", "goto_location")
                    else sim_ns
                )
                if record["observed_since"] is None:
                    record["observed_since"] = dwell_ns
                delay = 0 if action in ("arm", "disarm") else 1_000_000_000
                if dwell_ns - record["observed_since"] >= delay:
                    self.emit(record, "canceled" if canceling else "succeeded", sim_ns)
            else:
                record["observed_since"] = None

    def drain(self):
        updates, self.updates = self.updates, []
        return updates

    async def stop(self):
        # Both the original request and a later HOLD remain owned until reset,
        # including requests quarantined while still running.
        tasks = {
            record[key]
            for record in self.records.values()
            for key in ("task", "original_task")
            if record.get(key) is not None
        }
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
