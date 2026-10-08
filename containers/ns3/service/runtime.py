"""Owned native process; a timeout kills it and never synthesizes a result."""

import asyncio
import os

from . import config


class NativeFailure(RuntimeError):
    pass


class Runtime:
    def __init__(self):
        self.process = None
        self.aborted = False
        self.now = 0
        self.nodes = {}
        self.ids = []
        self.packets = {}
        self.seen_packets = set()
        self.sequence = 0

    async def line(self):
        data = await asyncio.wait_for(self.process.stdout.readline(), 25)
        if not data or not data.endswith(b"\n"):
            raise NativeFailure("native EOF/incomplete frame")
        text = data.decode("ascii", errors="strict").strip()
        if text.startswith("ERR "):
            raise NativeFailure(text)
        return text.split()

    async def request(self, command, expected):
        if self.process is None or self.process.returncode is not None:
            raise NativeFailure("native process is not running")
        self.process.stdin.write((command + "\n").encode("ascii"))
        await asyncio.wait_for(self.process.stdin.drain(), 10)
        response = await self.line()
        if response[:2] != ["OK", expected]:
            raise NativeFailure(f"unexpected native response {response!r}")
        return response

    async def reset(self, payload):
        spec = config.reset(payload)
        if self.process is not None:
            raise ValueError("reset is allowed once")
        self.ids = [node["id"] for node in spec["nodes"]]
        self.nodes = {name: n for n, name in enumerate(self.ids)}
        executable = os.environ.get(
            "AAS_NS3_PROVIDER", "/opt/aeroagentsim/bin/ns3-provider"
        )
        self.process = await asyncio.create_subprocess_exec(
            executable,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # Native diagnostics inherit stderr and can never pollute TCP JSON.
            stderr=None,
            limit=1024 * 1024,
        )
        ch, prop = spec["channel"], spec["propagation"]
        args = [
            spec["seed"],
            len(self.ids),
            ch["tx_power_dbm"],
            ch["rx_sensitivity_dbm"],
            prop["exponent"],
            prop["reference_loss_db"],
            ch["noise_figure_db"],
            ch["number"],
            ch["width_mhz"],
        ]
        await self.request("RESET " + " ".join(map(str, args)), "RESET")
        for n, node in enumerate(spec["nodes"]):
            await self.request(
                f"MOVE {n} 0 " + " ".join(map(str, node["position_enu"])), "MOVE"
            )
        for box in spec["scene_volumes"]:
            args = [*box["min_enu"], *box["max_enu"], box["loss_db"]]
            await self.request("VOLUME " + " ".join(map(str, args)), "VOLUME")
        return {"reached_sim_ns": 0, "configuration": spec}

    async def command(self, payload):
        try:
            packet = config.command(payload, self.now, self.nodes)
            if packet["packet_id"] in self.seen_packets:
                raise ValueError("packet_id was already submitted")
            if len(self.packets) >= 4096 or len(self.seen_packets) >= 1_000_000:
                raise ValueError("packet inventory limit exceeded")
        except ValueError as error:
            return {"status": "rejected", "reason": str(error)}
        self.sequence += 1
        args = [
            self.sequence,
            self.nodes[packet["src"]],
            self.nodes[packet["dst"]],
            packet["size"],
            packet["lifetime_ns"],
        ]
        response = await self.request("SEND " + " ".join(map(str, args)), "SEND")
        if len(response) != 3 or int(response[2]) != self.now:
            raise NativeFailure("native send frontier mismatch")
        self.packets[self.sequence] = packet
        self.seen_packets.add(packet["packet_id"])
        return {
            "status": "accepted",
            "packet_id": packet["packet_id"],
            "sim_ns": self.now,
        }

    async def advance(self, payload):
        target, updates = config.advance(payload, self.now, self.nodes)
        for at, node, position in updates:
            await self.request(
                f"MOVE {node} {at} " + " ".join(map(str, position)), "MOVE"
            )
        response = await self.request(f"STEP {target}", "STEP")
        if len(response) != 3 or int(response[2]) != target:
            raise NativeFailure("native step frontier mismatch")
        deliveries, drops, links, terminal = [], [], [], set()
        # Don't change the public frontier or packet inventory until the native
        # batch terminator and all event times have been checked.
        for _ in range(4096 + len(self.ids) * (len(self.ids) - 1) // 2 + 1):
            line = await self.line()
            if line == ["END"]:
                break
            if line[0] == "DELIVERY" and len(line) in (5, 7):
                sequence, sent, received, size = map(int, line[1:5])
                packet = self.packets[sequence]
                if (
                    sequence in terminal
                    or not sent <= received <= target
                    or size != packet["size"]
                ):
                    raise NativeFailure("invalid native delivery")
                terminal.add(sequence)
                result = {
                    "packet_id": packet["packet_id"],
                    "src": packet["src"],
                    "dst": packet["dst"],
                    "sent_ns": sent,
                    "received_ns": received,
                    "available_sim_ns": target,
                    "size": size,
                    "payload_ref": packet["payload_ref"],
                }
                if len(line) == 7:
                    result.update(rssi_dbm=float(line[5]), snr_db=float(line[6]))
                deliveries.append(result)
            elif line[0] == "DROP" and len(line) == 4:
                sequence, at = map(int, line[1:3])
                packet = self.packets[sequence]
                if sequence in terminal or not self.now <= at <= target:
                    raise NativeFailure("invalid native drop")
                terminal.add(sequence)
                drops.append(
                    {
                        "packet_id": packet["packet_id"],
                        "src": packet["src"],
                        "dst": packet["dst"],
                        "sim_ns": at,
                        "available_sim_ns": target,
                        "reason": line[3],
                    }
                )
            elif line[0] == "LINK" and len(line) == 9:
                a, b = int(line[1]), int(line[2])
                links.append(
                    {
                        "src": self.ids[a],
                        "dst": self.ids[b],
                        "sim_ns": target,
                        "available_sim_ns": target,
                        "distance_m": float(line[3]),
                        "path_loss_db": float(line[4]),
                        "predicted_rssi_dbm": float(line[5]),
                        "sent": int(line[6]),
                        "delivered": int(line[7]),
                        "dropped": int(line[8]),
                    }
                )
            else:
                raise NativeFailure(f"unexpected native record {line!r}")
        else:
            raise NativeFailure("native batch exceeds inventory budget")
        if len(links) != len(self.ids) * (len(self.ids) - 1) // 2:
            raise NativeFailure("incomplete native link inventory")
        for sequence in terminal:
            del self.packets[sequence]
        self.now = target
        return {
            "reached_sim_ns": target,
            "deliveries": deliveries,
            "drops": drops,
            "link_stats": links,
            "pending_packets": len(self.packets),
        }

    def abort(self):
        if self.process is not None and self.process.returncode is None:
            self.aborted = True
            self.process.kill()

    async def stop(self):
        process = self.process
        if process is None:
            return
        try:
            if process.returncode is None:
                await asyncio.wait_for(self.request("CLOSE", "CLOSE"), 5)
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 5)
            if process.returncode != 0 and not self.aborted:
                raise NativeFailure(f"native process exited {process.returncode}")
        finally:
            self.abort()
            await process.wait()
            self.process = None
