"""Single-owner JSON-lines service; stateful calls are never retried."""

import asyncio
import logging
import os
import signal
from concurrent.futures import ThreadPoolExecutor

from .runtime import Runtime
from .wire import MAX_FRAME, PROTOCOL, TIMEOUTS, decode, encode, fields

LOG = logging.getLogger("sumo-backend")
ACTIONS = [
    "set_speed",
    "reroute",
    "change_target",
    "lane_restriction",
    "tls_phase",
    "add_vehicle",
    "remove_vehicle",
]


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
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sumo-owner")
        loop = asyncio.get_running_loop()
        state, last_id = "NEW", 0

        async def native(method, *args):
            return await loop.run_in_executor(pool, method, *args)

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
                            await native(runtime.stop)
                            state = "CLOSED"
                            return {"closed": True}
                        if state == "TAINTED":
                            raise ValueError("tainted connection; cleanup only")
                        if state == "NEW" and op == "hello":
                            fields(payload, ())
                            version = await native(runtime.version)
                            state = "HELLO"
                            return {
                                "protocol": PROTOCOL,
                                "major": 1,
                                "minor": 0,
                                "sumo_version": version,
                                "step_length_ns": 100_000_000,
                                "capabilities": {
                                    "timing": "lockstep",
                                    "exact_stop": True,
                                    "hold": True,
                                    "entities": ["vehicle", "person"],
                                    "actions": ACTIONS,
                                    "step_quantum_ns": 1_000_000,
                                    "default_step_length_ns": 100_000_000,
                                    "max_frame_bytes": MAX_FRAME,
                                    "timeouts_s": TIMEOUTS,
                                    "idle_timeout_s": 3600,
                                },
                            }
                        if state == "HELLO" and op == "reset":
                            state = "RESET"
                            result = await native(runtime.reset, payload)
                            state = "READY"
                            return result
                        if state == "READY" and op in ("advance", "command"):
                            return await native(getattr(runtime, op), payload)
                        raise ValueError(f"{op} invalid in state {state}")

                    result = await asyncio.wait_for(execute(), TIMEOUTS[op])
                    response = {
                        k: request[k] for k in ("protocol", "major", "minor", "id")
                    }
                    response["result"] = result
                    frame = encode(response)
                except Exception as error:
                    if isinstance(error, asyncio.TimeoutError):
                        runtime.abort()
                    state = "TAINTED"
                    LOG.exception("request fault")
                    if request is None:
                        break
                    response = {
                        k: request[k] for k in ("protocol", "major", "minor", "id")
                    }
                    response["error"] = {
                        "code": type(error).__name__,
                        "message": str(error),
                        "state": state,
                    }
                    frame = encode(response)
                writer.write(frame)
                await asyncio.wait_for(writer.drain(), 10)
        except (ConnectionError, asyncio.TimeoutError, ValueError) as error:
            LOG.warning("connection fault: %s", error)
        finally:
            runtime.abort()
            # Same executor serializes cleanup behind any timed-out native call.
            # Native sockets have a shorter deadline than the operation timeout.
            try:
                await asyncio.shield(native(runtime.stop))
            except Exception:
                LOG.exception("native cleanup failed")
            pool.shutdown(wait=False, cancel_futures=True)
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


if __name__ == "__main__":
    main()
