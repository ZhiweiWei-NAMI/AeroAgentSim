#!/usr/bin/env python3
"""Measure real backend flight runs; preserve actual errors and partial traces.

``--step-ms`` accepts either one cadence in milliseconds (``20``, the historical
form) or a comma-separated sweep of cadences (``4,20,100,200``). Every cadence
is flown ``--runs`` times, each run on its own fresh connection and reset, and
every run records its own ``step_ns``. Trajectory comparisons are taken only
between runs that share one cadence, never across cadences. A single cadence
keeps the historical output schema (top-level ``step_ns``, ``runs`` and
``trajectory_comparisons``); a sweep groups runs under ``cadences`` with
per-cadence ``step_ns`` and comparisons instead of any top-level ``step_ns``.

A run may also carry a ``barrier_profile`` summary built from the optional
``profile_wall_ns`` diagnostic object enabled with AAS_PROFILE=1 on the service.
When that field is absent,
the summary is omitted entirely instead of being filled with zeros or guesses.
"""

import argparse
import json
import math
import socket
import statistics
import sys
import time
from pathlib import Path

PROTOCOL = "aeroagentsim.px4/v1"
LIMIT = 8 * 1024 * 1024
TIMEOUTS = {"hello": 10, "reset": 180, "advance": 30, "command": 30, "close": 20}


def parse_step_ms(value):
    """Parse one cadence in ms or a comma-separated sweep of cadences."""
    try:
        steps = [int(token.strip()) for token in str(value).split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid step-ms {value!r}") from None
    if not steps or any(step < 1 for step in steps):
        raise argparse.ArgumentTypeError("step-ms values must be positive integers")
    if len(set(steps)) != len(steps):
        raise argparse.ArgumentTypeError("step-ms sweep values must be distinct")
    return steps


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key {key}")
        result[key] = value
    return result


def finite(token):
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"nonfinite JSON value {token}")
    return value


def invalid(token):
    raise ValueError(f"invalid JSON constant {token}")


class Client:
    def __init__(self, host, port):
        self.socket = socket.create_connection((host, port), timeout=10)
        self.buffer = bytearray()
        self.id = 0
        self.tainted = False

    def request(self, op, payload):
        if self.tainted:
            raise RuntimeError(
                "connection tainted; stateful requests cannot be retried"
            )
        self.id += 1
        deadline = time.monotonic() + TIMEOUTS[op]
        self.socket.settimeout(TIMEOUTS[op])
        frame = (
            json.dumps(
                dict(
                    protocol=PROTOCOL,
                    major=1,
                    minor=0,
                    id=self.id,
                    op=op,
                    payload=payload,
                ),
                allow_nan=False,
            )
            + "\n"
        ).encode()
        if len(frame) > LIMIT:
            raise ValueError("request oversized")
        try:
            self.socket.sendall(frame)
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{op} exceeded its operation deadline")
                self.socket.settimeout(remaining)
                data = self.socket.recv(65536)
                if not data:
                    raise ConnectionError("EOF before complete response")
                self.buffer.extend(data)
                if len(self.buffer) > LIMIT:
                    raise ValueError("response exceeds frame limit")
            index = self.buffer.index(b"\n") + 1
            frame = bytes(self.buffer[:index])
            del self.buffer[:index]
            if self.buffer:
                raise ValueError("unexpected unsolicited response data")
            response = json.loads(
                frame.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_float=finite,
                parse_constant=invalid,
            )
            for key, expected in [
                ("protocol", PROTOCOL),
                ("major", 1),
                ("minor", 0),
                ("id", self.id),
            ]:
                if (
                    type(response[key]) is not type(expected)
                    or response[key] != expected
                ):
                    raise ValueError(f"wrong response {key}")
            if ("result" in response) == ("error" in response):
                raise ValueError("response must contain exactly one result or error")
            if "error" in response:
                raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
            return response["result"]
        except Exception:
            self.tainted = True
            raise

    def close(self):
        try:
            if not self.tainted:
                self.request("close", {})
        finally:
            self.socket.close()


def ready_client(args):
    # Only NEW/hello readiness is retried. No reset, command or advance is ever
    # retried, and each failed hello attempt drops its connection.
    deadline = time.monotonic() + 10
    while True:
        client = None
        try:
            client = Client(args.host, args.port)
            hello = client.request("hello", {})
            return client, hello
        except (ConnectionError, OSError) as error:
            if client is not None:
                client.socket.close()
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"hello not ready within 10 wall seconds: {error}"
                ) from error
            time.sleep(0.05)
        except Exception:
            if client is not None:
                client.socket.close()
            raise


def summarize(values):
    if not values:
        return None
    values = sorted(values)
    return {
        "min": values[0],
        "median": statistics.median(values),
        "p95": values[min(len(values) - 1, int(0.95 * len(values)))],
        "max": values[-1],
    }


def barrier_profile(result):
    """Validate the optional service phase timings without inventing values."""
    if "profile_wall_ns" not in result:
        return None
    profile = result["profile_wall_ns"]
    if not isinstance(profile, dict):
        raise ValueError("profile_wall_ns must be an object when present")
    for name, duration in profile.items():
        if not isinstance(name, str) or type(duration) is not int or duration < 0:
            raise ValueError("profile phases must be nonnegative integer nanoseconds")
    return profile


def barrier_profile_summary(profiles):
    if not profiles:
        return None
    phases = {}
    for profile in profiles:
        for name, duration in profile.items():
            phases.setdefault(name, []).append(duration)
    return {
        "advance_count": len(profiles),
        "phases": {
            name: {"count": len(values), **summarize(values)}
            for name, values in phases.items()
        },
    }


def run(args, number, step_ms, record):
    before_hello = time.monotonic()
    client, hello = ready_client(args)
    after_hello = time.monotonic()
    record["hello_ready_wall_s"] = after_hello - before_hello
    if args.container_start_monotonic is not None and number == 0:
        record["container_to_hello_wall_s"] = (
            after_hello - args.container_start_monotonic
        )
    record["hello"] = hello
    vehicles = [
        dict(id=f"v{i}", model="x500", spawn=[0, 8 * i, 0, 0, 0, 0])
        for i in range(args.vehicles)
    ]
    record.update(
        trajectory=[],
        command_updates=[],
        phases={},
        contacts_count=0,
        barrier_profiles=[],
    )
    freshness, advance_wall = {}, []
    try:
        started = time.monotonic()
        reset = client.request(
            "reset",
            dict(
                seed=args.seed,
                world=args.world,
                vehicles=vehicles,
                warmup=10_000_000_000,
            ),
        )
        record["reset_wall_s"] = time.monotonic() - started
        record["hello_to_reset_wall_s"] = time.monotonic() - after_hello
        if "container_to_hello_wall_s" in record:
            record["container_to_reset_wall_s"] = (
                time.monotonic() - args.container_start_monotonic
            )
        if type(reset["reached_sim_ns"]) is not int or reset["reached_sim_ns"] != 0:
            raise ValueError("reset did not establish integer time zero")
        if step_ms * 1_000_000 % reset["physics_step_ns"]:
            raise ValueError("step does not align to world physics interval")
        step_ns = step_ms * 1_000_000
        record["step_ns"] = step_ns
        sim_ns, samples = 0, reset["telemetry"]
        flight_started = time.monotonic()
        for phase in ("arm", "takeoff", "goto_location", "land"):
            pending = set()
            phase_started, phase_sim = time.monotonic(), sim_ns
            for i, vehicle in enumerate(vehicles):
                cid = f"r{number}_{phase}_{vehicle['id']}"
                params = (
                    {"altitude_m": 10}
                    if phase == "takeoff"
                    else {"position_enu": [50, 8 * i, 10], "yaw_deg": 0}
                    if phase == "goto_location"
                    else {}
                )
                accepted = client.request(
                    "command",
                    dict(
                        vehicle=vehicle["id"],
                        action=phase,
                        params=params,
                        command_id=cid,
                    ),
                )
                if accepted["status"] != "accepted":
                    raise RuntimeError(f"{phase} rejected: {accepted}")
                pending.add(cid)
            count = 0
            while pending:
                target = sim_ns + step_ns
                start = time.monotonic()
                result = client.request("advance", {"to_sim_ns": target})
                advance_wall.append(time.monotonic() - start)
                profile = barrier_profile(result)
                if profile is not None:
                    record["barrier_profiles"].append(profile)
                if (
                    type(result["reached_sim_ns"]) is not int
                    or result["reached_sim_ns"] != target
                ):
                    raise ValueError(
                        f"advance frontier mismatch {result['reached_sim_ns']} != {target}"
                    )
                sim_ns, samples = target, result["telemetry"]
                record["last_sim_ns"] = sim_ns
                record["contacts_count"] += len(result["contacts"])
                if {s["vehicle"] for s in samples} != {v["id"] for v in vehicles}:
                    raise ValueError("telemetry vehicle inventory mismatch")
                record["trajectory"].append({"sim_ns": sim_ns, "telemetry": samples})
                for sample in samples:
                    if sample["sim_ns"] != sim_ns:
                        raise ValueError(
                            f"stale Gazebo pose time {sample['sim_ns']} at {sim_ns}"
                        )
                    f = sample["freshness"]
                    freshness.setdefault("gazebo_pose_age_sim_ns", []).append(
                        f["gazebo_pose_age_sim_ns"]
                    )
                    for field, age in f["mavsdk_receipt_age_wall_ns"].items():
                        freshness.setdefault(field + "_receipt_age_wall_ns", []).append(
                            age
                        )
                for update in result["command_updates"]:
                    record["command_updates"].append(update)
                    if update["command_id"] in pending:
                        if update["status"] == "failed":
                            raise RuntimeError(f"observed command failed: {update}")
                        if update["status"] == "succeeded":
                            pending.remove(update["command_id"])
                count += 1
                if count % 250 == 0:
                    print(
                        f"run {number + 1} {phase}: {sim_ns / 1e9:.2f}s sim, pending {len(pending)}",
                        flush=True,
                    )
                if sim_ns - phase_sim > 180_000_000_000:
                    raise TimeoutError(
                        f"{phase}: no observed terminal state for {sorted(pending)}"
                    )
            record["phases"][phase] = {
                "sim_s": (sim_ns - phase_sim) / 1e9,
                "wall_s": time.monotonic() - phase_started,
                "final_telemetry": samples,
            }
            if phase == "goto_location":
                record["goto_error_m"] = {
                    s["vehicle"]: math.dist(
                        s["position_enu"], [50, 8 * int(s["vehicle"][1:]), 10]
                    )
                    for s in samples
                }
            if phase == "land":
                record["land_horizontal_error_m"] = {
                    s["vehicle"]: math.dist(
                        s["position_enu"][:2], [50, 8 * int(s["vehicle"][1:])]
                    )
                    for s in samples
                }
                if any(s["armed"] or s["landed_state"] != "ON_GROUND" for s in samples):
                    raise RuntimeError(
                        "land terminal update disagrees with observed ground/disarmed state"
                    )
            print(
                f"run {number + 1} {phase} succeeded at {sim_ns / 1e9:.2f}s sim",
                flush=True,
            )
        record["flight_wall_s"] = time.monotonic() - flight_started
        record["flight_sim_s"] = sim_ns / 1e9
        record["real_time_factor"] = record["flight_sim_s"] / record["flight_wall_s"]
        record["advance_wall_s"] = summarize(advance_wall)
        record["freshness"] = {
            key: summarize(value) for key, value in freshness.items()
        }
        # A failed run keeps its raw partial barrier_profiles as a trace; only a
        # completed run collapses them into one summary, and the key is omitted
        # entirely when the service sent no profile_wall_ns at all.
        profile = barrier_profile_summary(record.pop("barrier_profiles"))
        if profile is not None:
            record["barrier_profile"] = profile
        record["success"] = True
    finally:
        # Close errors are real failures as well; disconnect cleanup is used on
        # a tainted connection and never constitutes a successful run.
        primary_error = sys.exc_info()[1]
        try:
            client.close()
        except Exception as error:
            record["cleanup_error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            if primary_error is None:
                raise
            print(f"cleanup also failed: {error}", file=sys.stderr)


def compare(runs):
    """Compare trajectory positions only between runs of one same cadence."""
    comparisons = []
    if len(runs) < 2:
        return comparisons
    base = {
        t["sim_ns"]: {s["vehicle"]: s["position_enu"] for s in t["telemetry"]}
        for t in runs[0].get("trajectory", [])
    }
    for index, record in enumerate(runs[1:], 2):
        errors = []
        for point in record.get("trajectory", []):
            if point["sim_ns"] in base:
                for sample in point["telemetry"]:
                    errors.append(
                        math.dist(
                            sample["position_enu"],
                            base[point["sim_ns"]][sample["vehicle"]],
                        )
                    )
        comparisons.append(
            {
                "run": index,
                "reference_run": 1,
                "shared_vehicle_samples": len(errors),
                "position_rms_m": math.sqrt(sum(e * e for e in errors) / len(errors))
                if errors
                else None,
                "position_max_m": max(errors) if errors else None,
                "bitwise_identical_positions": all(e == 0 for e in errors)
                if errors
                else None,
            }
        )
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19000)
    parser.add_argument("--vehicles", type=int, choices=(1, 3), default=1)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--world", default="default")
    parser.add_argument(
        "--step-ms",
        type=parse_step_ms,
        default=[20],
        help="advance cadence in ms; one value or a comma-separated sweep, e.g. 4,20,100,200",
    )
    parser.add_argument("--container-start-monotonic", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("runs must be positive")
    sweep = args.step_ms
    output = {
        "protocol": PROTOCOL,
        "seed": args.seed,
        "vehicles": args.vehicles,
    }
    status = 0
    record = None
    try:
        if len(sweep) == 1:
            # Historical single-cadence schema, unchanged.
            output["step_ns"] = sweep[0] * 1_000_000
            output["runs"] = []
            for index in range(args.runs):
                record = {"run": index + 1, "success": False}
                output["runs"].append(record)
                run(args, index, sweep[0], record)
        else:
            # Each cadence is measured independently on fresh connections and
            # resets; comparisons stay inside one cadence, and no top-level
            # step_ns or cross-cadence comparison exists.
            output["cadences"] = []
            for step_ms in sweep:
                group = {
                    "step_ms": step_ms,
                    "step_ns": step_ms * 1_000_000,
                    "runs": [],
                }
                output["cadences"].append(group)
                for index in range(args.runs):
                    record = {"run": index + 1, "success": False}
                    group["runs"].append(record)
                    run(args, index, step_ms, record)
    except Exception as error:
        status = 1
        if record is not None:
            record["success"] = False
        output["error"] = {"type": type(error).__name__, "message": str(error)}
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
    finally:
        if "runs" in output:
            output["trajectory_comparisons"] = compare(output["runs"])
        for group in output.get("cadences", []):
            group["trajectory_comparisons"] = compare(group["runs"])
        args.output.write_text(json.dumps(output, allow_nan=False, indent=2) + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
