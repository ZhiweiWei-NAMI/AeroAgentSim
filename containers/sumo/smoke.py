#!/usr/bin/env python3
"""Real backend RPC measurement and full-trajectory same-seed comparison."""

import argparse
import hashlib
import json
import socket
import statistics
import time
from pathlib import Path

PROTOCOL = "aeroagentsim.sumo/v1"
MAX_FRAME = 8 * 1024 * 1024
TIMEOUTS = {"hello": 10, "reset": 180, "advance": 30, "command": 30, "close": 20}


class Client:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.file = self.sock.makefile("rb")
        self.ident = 0
        self.valid = True

    def call(self, op, payload=None):
        self.ident += 1
        req = {
            "protocol": PROTOCOL,
            "major": 1,
            "minor": 0,
            "id": self.ident,
            "op": op,
            "payload": {} if payload is None else payload,
        }
        raw = (json.dumps(req, allow_nan=False, separators=(",", ":")) + "\n").encode()
        self.sock.settimeout(TIMEOUTS[op])
        started = time.perf_counter()
        self.valid = False
        self.sock.sendall(raw)
        data = self.file.readline(MAX_FRAME + 1)
        elapsed = time.perf_counter() - started
        if not data.endswith(b"\n") or len(data) > MAX_FRAME:
            raise RuntimeError("EOF/incomplete/oversized response")
        response = json.loads(data)
        for key in ("protocol", "major", "minor", "id"):
            if type(response[key]) is not type(req[key]) or response[key] != req[key]:
                raise RuntimeError(f"response identity mismatch: {key}")
        if ("result" in response) == ("error" in response):
            raise RuntimeError("response must contain exactly result or error")
        if "error" in response:
            raise RuntimeError(response["error"])
        self.valid = True
        return response["result"], elapsed

    def close(self):
        try:
            if self.valid:
                self.call("close")
        finally:
            self.file.close()
            self.sock.close()


def canonical(value):
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def issue(client, command_id, action, params, records):
    result, _ = client.call(
        "command", {"command_id": command_id, "action": action, "params": params}
    )
    if result["status"] != "accepted":
        raise AssertionError(f"{command_id} was not accepted: {result}")
    records[command_id] = {"action": action, "params": params, "acceptance": result}


def run(args, repeat, output):
    client = Client(args.host, args.port)
    records = {}
    expected_updates = {}
    trajectory_path = output / f"trajectory-{args.step_ms}ms-{repeat}.jsonl"
    digest = hashlib.sha256()
    latencies = []
    event_counts = {
        key: 0
        for key in (
            "departed",
            "arrived",
            "teleported_start",
            "teleported_end",
            "collisions",
            "removed",
        )
    }
    try:
        hello, hello_s = client.call("hello")
        payload = {"seed": 7, "step_length_ns": args.native_step_ms * 1_000_000}
        if args.config:
            payload["config_file"] = args.config
        else:
            payload["scenario"] = {
                "kind": "grid",
                "vehicles": args.vehicles,
                "persons": 3,
                "depart_interval_s": 0.1,
            }
        reset, reset_s = client.call("reset", payload)
        assert reset["reached_sim_ns"] == 0
        native_step = reset["step_length_ns"]
        cadence = args.step_ms * 1_000_000
        assert cadence % native_step == 0
        maximum = int(args.seconds * 1_000_000_000)
        target = 0
        active_peak = {"vehicle": 0, "person": 0}
        first_tls = reset["traffic_lights"][0] if reset["traffic_lights"] else None
        issued = False
        initial_v0 = None
        removed_seen = False
        departed_ids = {"vehicle": set(), "person": set()}
        started = time.perf_counter()
        with trajectory_path.open("wb") as stream:
            while target < maximum:
                target += cadence
                sample, elapsed = client.call("advance", {"to_sim_ns": target})
                latencies.append(elapsed)
                assert sample["reached_sim_ns"] == target
                for kind, count in active_peak.items():
                    active_peak[kind] = max(
                        count, sum(e["kind"] == kind for e in sample["entities"])
                    )
                for key in event_counts:
                    event_counts[key] += len(sample[key])
                for event in sample["departed"]:
                    assert event["id"] not in departed_ids[event["kind"]]
                    departed_ids[event["kind"]].add(event["id"])
                for update in sample["command_updates"]:
                    records[update["command_id"]].setdefault("updates", []).append(
                        update
                    )
                    if update["status"] in ("succeeded", "failed"):
                        expected_updates[update["command_id"]] = update
                v0 = next((e for e in sample["entities"] if e["id"] == "v0"), None)
                if not args.config and v0 is not None and not issued:
                    initial_v0 = v0
                    # Native v0 starts on A0B0. Replace its perimeter path with
                    # an explicit connected interior detour; observable route change.
                    assert v0["edge"] == "A0B0"
                    issue(
                        client,
                        "reroute",
                        "reroute",
                        {
                            "vehicle": "v0",
                            "edges": ["A0B0", "B0B1", "B1C1", "C1C2", "C2B2", "B2A2"],
                        },
                        records,
                    )
                    issue(
                        client,
                        "speed",
                        "set_speed",
                        {"vehicle": "v0", "speed": 3.0},
                        records,
                    )
                    issue(
                        client,
                        "restrict",
                        "lane_restriction",
                        {
                            "lane": "B0C0_1",
                            "disallowed": ["passenger"],
                            "at_sim_ns": target + 2_000_000_000,
                        },
                        records,
                    )
                    assert first_tls is not None
                    issue(
                        client,
                        "tls",
                        "tls_phase",
                        {"tls": first_tls["id"], "phase": 1, "duration_s": 60},
                        records,
                    )
                    issue(
                        client,
                        "add",
                        "add_vehicle",
                        {"vehicle": "added", "route_id": "r8", "type": "car"},
                        records,
                    )
                    issued = True
                    # Hold must preserve trajectory and leave commands queued.
                    held, _ = client.call("advance", {"to_sim_ns": target})
                    assert held["entities"] == sample["entities"]
                    assert not held["command_updates"]
                if (
                    not args.config
                    and "reroute" in expected_updates
                    and "target" not in records
                    and v0 is not None
                ):
                    issue(
                        client,
                        "target",
                        "change_target",
                        {"vehicle": "v0", "edge": "C2B2"},
                        records,
                    )
                added = next(
                    (e for e in sample["entities"] if e["id"] == "added"), None
                )
                if added and "add" in expected_updates and "remove" not in records:
                    issue(
                        client,
                        "remove",
                        "remove_vehicle",
                        {"vehicle": "added"},
                        records,
                    )
                if any(e["id"] == "added" for e in sample["removed"]):
                    removed_seen = True
                    assert not any(e["id"] == "added" for e in sample["arrived"])
                raw = canonical(sample)
                digest.update(raw)
                stream.write(raw)
        wall_s = time.perf_counter() - started
        if not args.config:
            required = {
                "reroute",
                "speed",
                "restrict",
                "tls",
                "add",
                "target",
                "remove",
            }
            assert set(expected_updates) == required, expected_updates
            for ident, update in expected_updates.items():
                assert update["status"] == "succeeded", (ident, update)
            assert removed_seen
            assert active_peak["person"] == 3
            assert departed_ids["person"] == {"p0", "p1", "p2"}
            initial = {f"v{i}" for i in range(args.vehicles)}
            assert (departed_ids["vehicle"] - {"added"}) | set(
                sample["pending_vehicle_ids"]
            ) == initial
            assert not (departed_ids["vehicle"] & set(sample["pending_vehicle_ids"]))
            assert "added" in departed_ids["vehicle"]
            assert len(departed_ids["vehicle"]) >= 0.9 * args.vehicles
            # Routing must actually differ, not merely acknowledge reroute.
            observation = expected_updates["reroute"]["observation"]
            assert observation["route"] != initial_v0["route"], observation
        return {
            "repeat": repeat,
            "seed": 7,
            "native_step_ms": args.native_step_ms,
            "rpc_step_ms": args.step_ms,
            "sim_s": args.seconds,
            "wall_s": wall_s,
            "rtf": args.seconds / wall_s,
            "hello_s": hello_s,
            "reset_s": reset_s,
            "advance_latency_ms": {
                "median": statistics.median(latencies) * 1000,
                "p95": percentile(latencies, 0.95) * 1000,
                "max": max(latencies) * 1000,
            },
            "active_peak": active_peak,
            "event_counts": event_counts,
            "final_pending_vehicle_ids": sample["pending_vehicle_ids"],
            "trajectory_sha256": digest.hexdigest(),
            "trajectory": str(trajectory_path),
            "commands": records,
            "hello": hello,
        }
    finally:
        client.close()


def same_bytes(left, right):
    with Path(left).open("rb") as a, Path(right).open("rb") as b:
        while True:
            chunk = a.read(1024 * 1024)
            if chunk != b.read(1024 * 1024):
                return False
            if not chunk:
                return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19003)
    parser.add_argument("--step-ms", type=int, default=100)
    parser.add_argument("--native-step-ms", type=int, default=100)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--vehicles", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--config", help="absolute configuration path inside container")
    parser.add_argument("--output", default="/tmp/aas-p3a/results")
    args = parser.parse_args()
    if (
        args.seconds <= 0
        or args.step_ms <= 0
        or args.native_step_ms <= 0
        or args.repeats < 1
    ):
        parser.error("positive seconds, step lengths and repeats required")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    results = [run(args, i + 1, output) for i in range(args.repeats)]
    deterministic = len({r["trajectory_sha256"] for r in results}) == 1
    byte_equal = all(
        same_bytes(results[0]["trajectory"], r["trajectory"]) for r in results[1:]
    )
    deterministic = deterministic and byte_equal
    report = {
        "runs": results,
        "full_trajectory_bitwise_equal": deterministic,
        "byte_compare_equal": byte_equal,
        "comparison": "all advance results, entities/events/TLS/command updates; no wall timings",
    }
    report_path = output / f"report-{args.step_ms}ms.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not deterministic:
        raise AssertionError("same-seed full trajectories differ")


if __name__ == "__main__":
    main()
