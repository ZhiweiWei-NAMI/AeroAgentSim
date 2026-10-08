#!/usr/bin/env python3
"""Real TCP smoke, falling delivery rate, seed replay and 100 ms timing."""

import argparse
import json
import socket
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from service.wire import (
    MAX_FRAME,
    PROTOCOL,
    TIMEOUTS,
    check_tree,
    encode,
    nonfinite,
    pairs,
)


class Client:
    def __init__(self, host, port):
        self.socket = socket.create_connection((host, port), TIMEOUTS["hello"])
        self.file = self.socket.makefile("rb")
        self.sequence = 0

    def call(self, op, payload):
        self.sequence += 1
        request = {
            "protocol": PROTOCOL,
            "major": 1,
            "minor": 0,
            "id": self.sequence,
            "op": op,
            "payload": payload,
        }
        self.socket.settimeout(TIMEOUTS[op])
        self.socket.sendall(encode(request))
        frame = self.file.readline(MAX_FRAME + 1)
        if len(frame) > MAX_FRAME or not frame.endswith(b"\n") or b"\r" in frame:
            raise RuntimeError("RPC EOF or invalid response frame")
        response = json.loads(
            frame.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite
        )
        check_tree(response)
        if any(
            response.get(key) != request[key]
            for key in ("protocol", "major", "minor", "id")
        ):
            raise RuntimeError("RPC identity mismatch")
        if ("result" in response) == ("error" in response):
            raise RuntimeError("RPC result/error envelope mismatch")
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"], frame

    def close(self):
        try:
            self.call("close", {})
        finally:
            self.file.close()
            self.socket.close()


def run(host, port, path):
    started = time.perf_counter()
    client = Client(host, port)
    frames, latency, deliveries, drops = [], [], [], []
    accepted = 0
    try:
        _, frame = client.call("hello", {})
        frames.append(frame)
        _, frame = client.call(
            "reset",
            {
                "seed": 713,
                "nodes": [
                    {"id": f"n{n}", "position_enu": [n * 2, 0, 0]} for n in range(5)
                ],
            },
        )
        frames.append(frame)
        startup = time.perf_counter() - started
        sim_started = time.perf_counter()
        for step in range(600):
            now = step * 100_000_000
            # Four UDP packets every 100 ms; no retry masquerading as delivery.
            for n in range(1, 5):
                result, frame = client.call(
                    "command",
                    {
                        "action": "send",
                        "params": {
                            "packet_id": f"p{step:03d}-{n}",
                            "src": "n0",
                            "dst": f"n{n}",
                            "size": 512,
                            "payload_ref": f"opaque:p{step:03d}-{n}",
                            "lifetime_ns": 500_000_000,
                        },
                    },
                )
                frames.append(frame)
                if result["status"] != "accepted":
                    raise RuntimeError(f"send rejected: {result}")
                accepted += 1
            # Each node moves outward at a different rate, 2..8 m/s. The
            # timestamp is the interval START, not a future pose used early.
            updates = [
                {
                    "node": f"n{n}",
                    "sim_ns": now,
                    "position": [n * (2 + step * 0.2), 0, 0],
                }
                for n in range(5)
            ]
            before = time.perf_counter_ns()
            result, frame = client.call(
                "advance", {"to_sim_ns": now + 100_000_000, "mobility": updates}
            )
            latency.append((time.perf_counter_ns() - before) / 1e6)
            frames.append(frame)
            if result["reached_sim_ns"] != now + 100_000_000:
                raise RuntimeError("native time mismatch")
            deliveries.extend(result["deliveries"])
            drops.extend(result["drops"])
        # Flush explicit application lifetimes after the 60 s experiment.
        result, frame = client.call("advance", {"to_sim_ns": 61_000_000_000})
        frames.append(frame)
        deliveries.extend(result["deliveries"])
        drops.extend(result["drops"])
        wall = time.perf_counter() - sim_started
        if result["pending_packets"] or len(deliveries) + len(drops) != accepted:
            raise RuntimeError("missing or duplicate packet terminal outcomes")
        path.write_bytes(b"".join(frames))
        by_id = {entry["packet_id"]: entry for entry in deliveries}
        bins = []
        for start in range(0, 60, 10):
            count = sum(
                f"p{step:03d}-{n}" in by_id
                for step in range(start * 10, (start + 10) * 10)
                for n in range(1, 5)
            )
            bins.append(
                {"start_s": start, "sent": 400, "delivered": count, "rate": count / 400}
            )
        near_rate = (
            sum(f"p{step:03d}-{n}" in by_id for step in range(10) for n in range(1, 5))
            / 40
        )
        if near_rate < 0.9 or bins[-1]["rate"] > 0.1 or not drops:
            raise RuntimeError(f"distance attenuation smoke failed: {bins}")
        for item in deliveries:
            if not item["sent_ns"] <= item["received_ns"] <= item["available_sim_ns"]:
                raise RuntimeError("occurrence/availability violation")
        ordered = sorted(latency)
        return {
            "startup_s": startup,
            "wall_s": wall,
            "rtf_61s": 61 / wall,
            "advance_100ms_latency_ms": {
                "median": statistics.median(latency),
                "p95": ordered[int(0.95 * (len(ordered) - 1))],
                "max": max(latency),
            },
            "near_0_to_1s_delivery_rate": near_rate,
            "accepted": accepted,
            "delivered": len(deliveries),
            "dropped": len(drops),
            "delivery_bins": bins,
            "rssi_samples": sum("rssi_dbm" in d for d in deliveries),
            "drop_reasons": {
                reason: sum(d["reason"] == reason for d in drops)
                for reason in sorted({d["reason"] for d in drops})
            },
        }
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="authorized output directory for response transcripts/metrics",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    a, b = args.output / "run-a.jsonl", args.output / "run-b.jsonl"
    results = [run(args.host, args.port, a), run(args.host, args.port, b)]
    equal = a.read_bytes() == b.read_bytes()
    report = {"same_seed_byte_equal": equal, "runs": results}
    (args.output / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not equal:
        raise SystemExit("same-seed response transcripts differ")


if __name__ == "__main__":
    main()
