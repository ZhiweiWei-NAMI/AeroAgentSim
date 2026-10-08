"""Single-owner TCP lockstep profile. Never retry stateful requests."""

import asyncio
import logging
import os
import signal

from .runtime import Runtime
from .wire import MAX_FRAME, PROTOCOL, TIMEOUTS, decode, encode, fields

LOG = logging.getLogger("ns3-backend")


class Server:
    def __init__(self, runtime_factory=Runtime):
        self.runtime_factory = runtime_factory
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
        runtime = self.runtime_factory()
        state, last_id = "NEW", 0
        try:
            while state != "CLOSED":
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

                    async def execute(op=op, payload=payload):
                        nonlocal state
                        if op == "close":
                            fields(payload, ())
                            await runtime.stop()
                            state = "CLOSED"
                            return {"closed": True}
                        if state == "TAINTED":
                            raise ValueError("tainted connection; cleanup only")
                        if op == "hello" and state == "NEW":
                            fields(payload, ())
                            state = "HELLO"
                            return {
                                "protocol": PROTOCOL,
                                "major": 1,
                                "minor": 0,
                                "ns3_version": "3.48",
                                "capabilities": {
                                    "timing": "lockstep",
                                    "exact_stop": True,
                                    "hold": True,
                                    "early_return": False,
                                    "time_quantum_ns": 1,
                                    "actions": ["send"],
                                    "max_nodes": 128,
                                    "max_payload_bytes": 61440,
                                    "max_pending_packets": 4096,
                                    "wifi_standard": "802.11n",
                                    "medium": "shared_adhoc",
                                    "data_mode": "HtMcs7",
                                    "propagation": "log_distance_with_enu_aabb_volumes",
                                    "delivery_signal": "monitor_sniffer_rx_when_available",
                                    "max_frame_bytes": MAX_FRAME,
                                    "timeouts_s": TIMEOUTS,
                                    "idle_timeout_s": 3600,
                                },
                            }
                        if op == "reset" and state == "HELLO":
                            state = "RESET"
                            result = await runtime.reset(payload)
                            state = "READY"
                            return result
                        if state == "READY" and op in ("advance", "command"):
                            return await getattr(runtime, op)(payload)
                        raise ValueError(f"{op} invalid in state {state}")

                    result = await asyncio.wait_for(execute(), TIMEOUTS[op])
                    response = {
                        key: request[key]
                        for key in ("protocol", "major", "minor", "id")
                    }
                    response["result"] = result
                    frame = encode(response)
                except Exception as error:
                    runtime.abort()
                    state = "TAINTED"
                    LOG.exception("request fault")
                    if request is None:
                        break
                    response = {
                        key: request[key]
                        for key in ("protocol", "major", "minor", "id")
                    }
                    response["error"] = {
                        "code": fault_code(error),
                        "message": str(error),
                        "state": state,
                    }
                    frame = encode(response)
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


def fault_code(error):
    if isinstance(error, asyncio.TimeoutError):
        return "TIMEOUT"
    if isinstance(error, ValueError):
        return "INVALID_REQUEST"
    return "NATIVE_FAILURE"


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    asyncio.run(Server().run())


if __name__ == "__main__":
    main()
