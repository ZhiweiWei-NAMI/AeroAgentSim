"""Strict LF-framed TCP profile, with one connection owning the native world."""

import asyncio
import json
import logging
import os
import signal

from .config import catalog, fields, integer
from .runtime import Runtime

PROTOCOL = "aeroagentsim.px4/v1"
MAX_FRAME = 8 * 1024 * 1024  # Includes LF.
TIMEOUTS = {"hello": 10, "reset": 180, "advance": 30, "command": 30, "close": 20}
LOG = logging.getLogger("px4-backend")


def pairs(items):
    value = {}
    for key, entry in items:
        if key in value:
            raise ValueError(f"duplicate JSON key {key}")
        value[key] = entry
    return value


def nonfinite(value):
    raise ValueError(f"nonfinite JSON number {value}")


def decode(data):
    if len(data) > MAX_FRAME or not data.endswith(b"\n"):
        raise ValueError("incomplete or oversized LF frame")
    value = json.loads(
        data.decode("utf-8", errors="strict"),
        object_pairs_hook=pairs,
        parse_constant=nonfinite,
    )

    # JSON exponent overflow (e.g. 1e999) is not parse_constant.
    def check(node):
        import math

        if isinstance(node, float) and not math.isfinite(node):
            raise ValueError("nonfinite JSON numeric value")
        if isinstance(node, dict):
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)

    check(value)
    fields(value, ("protocol", "major", "minor", "id", "op", "payload"))
    if (
        value["protocol"] != PROTOCOL
        or type(value["major"]) is not int
        or value["major"] != 1
    ):
        raise ValueError("unsupported protocol/major")
    integer(value["minor"], "minor", maximum=0)
    integer(value["id"], "id", minimum=1)
    if not isinstance(value["op"], str) or not isinstance(value["payload"], dict):
        raise ValueError("op must be string and payload object")
    return value


class Server:
    def __init__(self):
        self.owner = False
        self.connections = set()
        self.stop_event = asyncio.Event()

    async def client(self, reader, writer):
        if self.owner:
            writer.close()
            await writer.wait_closed()
            return
        self.owner = True
        task = asyncio.current_task()
        self.connections.add(task)
        runtime, state, last_id = Runtime(), "NEW", 0
        try:
            while state != "CLOSED":
                # A paused world can remain idle while the agent thinks. The
                # inactivity deadline is explicitly bounded by the heartbeat
                # policy, and separate from operation execution deadlines.
                data = await asyncio.wait_for(reader.readline(), 3600)
                if not data:
                    break
                request = None
                try:
                    request = decode(data)
                    if request["id"] <= last_id:
                        raise ValueError("request id must strictly increase")
                    last_id = request["id"]
                    op, payload = request["op"], request["payload"]
                    if op not in TIMEOUTS:
                        raise ValueError("unknown operation")

                    async def execute():
                        nonlocal state
                        if op == "close":
                            fields(payload, ())
                            await runtime.stop()
                            state = "CLOSED"
                            return {"closed": True}
                        if state == "TAINTED":
                            raise ValueError("connection tainted; cleanup only")
                        if op == "hello" and state == "NEW":
                            fields(payload, ())
                            state = "HELLO"
                            return {
                                "protocol": PROTOCOL,
                                "major": 1,
                                "minor": 0,
                                "capabilities": {
                                    "physics_step_ns": 4_000_000,
                                    "vehicles": sorted(catalog()),
                                    "sensors": [
                                        "gazebo_pose",
                                        "mavsdk_telemetry",
                                        "gazebo_contacts",
                                    ],
                                    "actions": [
                                        "arm",
                                        "takeoff",
                                        "goto_location",
                                        "hold",
                                        "land",
                                        "disarm",
                                    ],
                                    "max_frame_bytes": MAX_FRAME,
                                    "timeouts_s": TIMEOUTS,
                                    "idle_timeout_s": 3600,
                                    "timing": "lockstep",
                                    "exact_stop": True,
                                    "hold": True,
                                },
                            }
                        if op == "reset" and state == "HELLO":
                            state = "RESET"
                            result = await runtime.reset(payload)
                            state = "READY"
                            return result
                        if state == "READY" and op == "advance":
                            return await runtime.advance(payload)
                        if state == "READY" and op == "command":
                            return runtime.command(payload)
                        raise ValueError(f"{op} is invalid in state {state}")

                    result = await asyncio.wait_for(execute(), TIMEOUTS[op])
                    response = {
                        key: request[key]
                        for key in ("protocol", "major", "minor", "id")
                    }
                    response["result"] = result
                except Exception as error:
                    state = "TAINTED"
                    LOG.exception("request failed")
                    if request is None:
                        break  # No trustworthy identity to echo.
                    response = {
                        key: request[key]
                        for key in ("protocol", "major", "minor", "id")
                    }
                    response["error"] = {
                        "code": type(error).__name__,
                        "message": str(error),
                        "state": state,
                    }
                frame = (
                    json.dumps(response, allow_nan=False, separators=(",", ":")) + "\n"
                ).encode()
                if len(frame) > MAX_FRAME:
                    raise ValueError("response exceeds frame budget")
                writer.write(frame)
                await asyncio.wait_for(writer.drain(), 10)
        except (ConnectionError, asyncio.TimeoutError, ValueError) as error:
            LOG.warning("connection fault: %s", error)
        finally:
            try:
                await runtime.stop()
            except Exception:
                LOG.exception("native cleanup failed")
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.owner = False
            self.connections.discard(task)

    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop_event.set)
        server = await asyncio.start_server(
            self.client,
            "0.0.0.0",
            int(os.environ.get("AAS_PORT", "9000")),
            limit=MAX_FRAME,
        )
        async with server:
            LOG.info("listening on %s", server.sockets[0].getsockname())
            await self.stop_event.wait()
        for task in self.connections.copy():
            task.cancel()
        await asyncio.gather(*self.connections, return_exceptions=True)


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    asyncio.run(Server().run())
