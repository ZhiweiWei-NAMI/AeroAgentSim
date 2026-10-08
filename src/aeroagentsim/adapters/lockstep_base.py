"""Host lockstep transport and atomic proposal construction for native services."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar, cast

from aerokernel import (
    ClockMapping,
    EntityRef,
    Instant,
    Interval,
    KernelError,
    Partition,
    Stamp,
)
from aerokernel.messages import Delivery, Dirty
from aerokernel.sdk import Command, EngineContext, SimpleEngine
from aerokernel.state import StateView
from aerokernel.values import ResourceBudget, Value, canonical_json, parse_json

if TYPE_CHECKING:
    from aeroagentsim.platform.plugins import EngineBuild

T = TypeVar("T")
TIMEOUTS = {
    "hello": 10.0,
    "reset": 180.0,
    "advance": 30.0,
    "command": 30.0,
    "close": 20.0,
}


def obj(value: object) -> dict[str, Any]:
    """Require an object, without coercion or default data."""
    if not isinstance(value, dict):
        raise KernelError("ADAPTER_SHAPE", "expected JSON object")
    return cast(dict[str, Any], value)


def records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise KernelError("ADAPTER_SHAPE", "expected record array")
    return [obj(item) for item in value]


def integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise KernelError("ADAPTER_INTEGER", f"invalid {name}")
    return value


class JsonLinesClient:
    """One owner, bounded LF framing, lossless JSON and no stateful retries."""

    def __init__(
        self,
        host: str,
        port: int,
        protocol: str,
        *,
        timeouts: dict[str, float] | None = None,
        max_frame_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.host, self.port, self.protocol = host, port, protocol
        self.timeouts = dict(TIMEOUTS)
        if timeouts is not None:
            self.timeouts.update(timeouts)
        if any(
            type(v) not in (int, float) or not 0 < v < float("inf")
            for v in self.timeouts.values()
        ):
            raise ValueError("timeouts must be positive finite seconds")
        self.budget = ResourceBudget(frame_bytes=max_frame_bytes)
        self.socket: socket.socket | None = None
        self.sequence = 0
        self.tainted = False
        self.buffer = bytearray()

    def request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.tainted:
            raise KernelError(
                "ADAPTER_TAINTED",
                "stateful request cannot be retried",
                operation=operation,
            )
        try:
            timeout = self.timeouts[operation]
            deadline = time.monotonic() + timeout
            if self.socket is None:
                self.socket = socket.create_connection(
                    (self.host, self.port), timeout=timeout
                )
            self.sequence += 1
            request = {
                "protocol": self.protocol,
                "major": 1,
                "minor": 0,
                "id": self.sequence,
                "op": operation,
                "payload": payload,
            }
            frame = canonical_json(request, self.budget)
            if len(frame) > self.budget.frame_bytes:
                raise KernelError("ADAPTER_FRAME", "request exceeds frame limit")
            self.socket.settimeout(max(0.000001, deadline - time.monotonic()))
            self.socket.sendall(frame)
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("operation deadline")
                self.socket.settimeout(remaining)
                chunk = self.socket.recv(
                    min(65536, self.budget.frame_bytes + 1 - len(self.buffer))
                )
                if not chunk:
                    raise KernelError("ADAPTER_EOF", "EOF before complete response")
                self.buffer.extend(chunk)
                if len(self.buffer) > self.budget.frame_bytes:
                    raise KernelError("ADAPTER_FRAME", "response exceeds frame limit")
            response_frame, _, trailing = self.buffer.partition(b"\n")
            if trailing:
                raise KernelError("ADAPTER_FRAME", "unsolicited response bytes")
            self.buffer.clear()
            response = obj(parse_json(bytes(response_frame), self.budget))
            keys = {"protocol", "major", "minor", "id"}
            if set(response) not in (keys | {"result"}, keys | {"error"}):
                raise KernelError("ADAPTER_ENVELOPE", "invalid response envelope")
            if (
                response["protocol"] != self.protocol
                or type(response["major"]) is not int
                or response["major"] != 1
                or type(response["minor"]) is not int
                or response["minor"] != 0
                or type(response["id"]) is not int
                or response["id"] != self.sequence
            ):
                raise KernelError(
                    "ADAPTER_IDENTITY", "response protocol/version/id mismatch"
                )
            if "error" in response:
                error = obj(response["error"])
                raise KernelError(
                    "ADAPTER_BACKEND",
                    "backend fault",
                    operation=operation,
                    backend=error,
                )
            return obj(response["result"])
        except Exception as error:
            self.abort()
            if isinstance(error, KernelError):
                raise
            code = (
                "ADAPTER_TIMEOUT"
                if isinstance(error, TimeoutError)
                else "ADAPTER_TRANSPORT"
            )
            raise KernelError(code, str(error), operation=operation) from error

    def abort(self) -> None:
        self.tainted = True
        self.disconnect()

    def disconnect(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class LockstepEngine(SimpleEngine):
    """Build complete proposals before kernel publication; faults taint the owner.

    Native integration precedes boundary reactions. Between configured grid points
    the adapter certifies a hold, publishing no manufactured observations.
    """

    backend: str
    protocol: str
    quantum_key: str
    actions: tuple[str, ...]
    version = "e1/1"

    def __init__(self, build: EngineBuild, partition: Partition) -> None:
        super().__init__(partition)
        self.build = build
        config = build.config
        self.client = JsonLinesClient(
            config["host"],
            integer(config["port"], "port", 1),
            self.protocol,
            timeouts=config.get("timeouts_s"),
        )
        self.commands: dict[str, Command[dict[str, Any]]] = {}
        self.pending: dict[str, Command[dict[str, Any]]] = {}
        self.statuses: dict[str, str] = {}
        self.pending_times: dict[str, int] = {}
        self.capabilities: dict[str, Any] = {}
        self.command_sequence = 0
        self.ready = False
        self.confirmed_ns = 0
        self.reset_result: dict[str, Any] | None = None
        self.clock_id = str(config["clock_id"])
        self.mapping_id = str(config["mapping_id"])

    @property
    def clock_mapping(self) -> ClockMapping:
        return ClockMapping(self.mapping_id, self.clock_id)

    def stamp(self, ns: object) -> Stamp:
        return Stamp(self.clock_id, integer(ns, "source time"), 1, self.mapping_id)

    def guarded(self, operation: str, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except Exception as error:
            self.client.abort()
            if isinstance(error, KernelError):
                raise
            raise KernelError(
                "ADAPTER_OUTPUT",
                str(error),
                operation=operation,
                partition=self.partition.id,
            ) from error

    def hello(self) -> None:
        response = self.client.request("hello", {})
        if (
            response["protocol"] != self.protocol
            or type(response["major"]) is not int
            or response["major"] != 1
            or type(response["minor"]) is not int
            or response["minor"] != 0
        ):
            raise KernelError("ADAPTER_CAPABILITY", "hello version mismatch")
        caps = obj(response["capabilities"])
        self.capabilities = caps
        if (
            caps["timing"] != "lockstep"
            or caps["exact_stop"] is not True
            or caps["hold"] is not True
        ):
            raise KernelError(
                "ADAPTER_CAPABILITY", "exact stop and certified hold required"
            )
        quantum = integer(caps[self.quantum_key], "native quantum", 1)
        step = self.partition.timing.step_ns
        if step is None or step % quantum:
            raise KernelError(
                "ADAPTER_BOUNDARY", "communication step not native-aligned"
            )
        if not isinstance(caps["actions"], list) or not set(self.actions) <= set(
            caps["actions"]
        ):
            raise KernelError("ADAPTER_CAPABILITY", "required actions unavailable")
        max_frame = integer(caps["max_frame_bytes"], "frame bytes", 1)
        self.client.budget = ResourceBudget(
            frame_bytes=min(self.client.budget.frame_bytes, max_frame)
        )

    def reset_payload(self) -> dict[str, Any]:
        assert self.context is not None
        payload = dict(obj(self.build.config["reset"]))
        if "seed" in payload and payload["seed"] != self.context.root_seed:
            raise KernelError(
                "ADAPTER_SEED", "backend seed differs from pinned root seed"
            )
        payload["seed"] = self.context.root_seed
        return payload

    def initialize(self, view: StateView) -> tuple[object, ...]:
        def bootstrap() -> tuple[object, ...]:
            self.hello()
            result = self.client.request("reset", self.reset_payload())
            if integer(result["reached_sim_ns"], "reset frontier") != 0:
                raise KernelError("ADAPTER_TIME", "reset must confirm zero frontier")
            self.check_reset(result)
            ctx = EngineContext(view, commands=self.commands)
            self.project(ctx, result, bootstrap=True)
            self.reset_result = result
            self.ready = True
            return tuple(ctx.ops)

        return self.guarded("reset", bootstrap)

    def check_reset(self, result: dict[str, Any]) -> None:
        """Backend-specific negotiation after actual native reset."""

    def advance_payload(self, ctx: EngineContext) -> dict[str, Any]:
        return {"to_sim_ns": ctx.now.ns}

    def project(
        self, ctx: EngineContext, result: dict[str, Any], *, bootstrap: bool
    ) -> None:
        raise NotImplementedError

    def integrate(self, view: StateView) -> tuple[object, ...]:
        def integrate_native() -> tuple[object, ...]:
            ctx = EngineContext(view, commands=self.commands)
            result = self.client.request("advance", self.advance_payload(ctx))
            reached = integer(result["reached_sim_ns"], "native frontier")
            if reached != view.instant.ns or reached <= self.confirmed_ns:
                raise KernelError(
                    "ADAPTER_TIME", "native stop differs from granted boundary"
                )
            self.project(ctx, result, bootstrap=False)
            self.confirmed_ns = reached
            return tuple(ctx.ops)

        return self.guarded("advance", integrate_native)

    def write(
        self,
        ctx: EngineContext,
        ref: EntityRef,
        field: str,
        value: object,
        at: object,
        *,
        source_stamp: Stamp | None = None,
    ) -> None:
        ns = integer(at, "sample time")
        if ns > ctx.now.ns:
            raise KernelError("ADAPTER_TIME", "future sample")
        self.build.registry.validate(self.build.registry.field(field).schema, value)
        ctx.set(
            ref,
            field,
            value,
            acquired=self.stamp(ns) if source_stamp is None else source_stamp,
            valid=Interval(Instant(ns), None),
        )

    def emit(
        self,
        ctx: EngineContext,
        name: str,
        record: dict[str, Any],
        at: object,
        *,
        source_stamp: Stamp | None = None,
    ) -> None:
        ns = integer(at, "event time")
        if ns > ctx.now.ns:
            raise KernelError("ADAPTER_TIME", "event occurs after availability")
        schema = f"adapters.{self.backend}.{name}"
        payload = {**record, "available_ns": ctx.now.ns}
        self.build.registry.validate(
            self.build.registry.message(schema).schema, payload
        )
        ctx.emit(
            schema,
            payload,
            topic=self.build.config["event_topic"],
            at=Instant(ns),
            stamp=self.stamp(ns) if source_stamp is None else source_stamp,
        )

    def command_payload(
        self, command: Command[dict[str, Any]], backend_id: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def decode_command(value: Value) -> dict[str, Any]:
        return obj(value)

    def on_react(
        self, view: StateView, inbox: tuple[Delivery, ...], dirty: tuple[Dirty, ...]
    ) -> tuple[object, ...]:
        def apply() -> tuple[object, ...]:
            ctx = EngineContext(view, inbox, dirty, self.commands)
            for delivery in inbox:
                if delivery.message.kind != "command":
                    raise KernelError("ADAPTER_COMMAND", "unsupported input kind")
                command = ctx.remember(delivery, self.decode_command)
                self.command_sequence += 1
                backend_id = f"c{self.command_sequence}"
                result = self.client.request(
                    "command", self.command_payload(command, backend_id)
                )
                status = result["status"]
                if status == "rejected":
                    self.validate_result(
                        command, {"status": "rejected", "reason": result["reason"]}
                    )
                    ctx.reject(
                        command, {"status": "rejected", "reason": result["reason"]}
                    )
                elif status == "accepted":
                    self.check_accept(result, backend_id, command)
                    ctx.accept(command)
                    self.pending[backend_id] = command
                    self.statuses[backend_id] = "accepted"
                    self.pending_times[backend_id] = self.confirmed_ns
                    if self.backend == "ns3":
                        ctx.execute(command)
                        self.statuses[backend_id] = "executing"
                else:
                    raise KernelError("ADAPTER_COMMAND", "invalid acceptance status")
            return tuple(ctx.ops)

        return self.guarded("command", apply)

    def check_accept(
        self, result: dict[str, Any], backend_id: str, command: Command[dict[str, Any]]
    ) -> None:
        if result["command_id"] != backend_id:
            raise KernelError("ADAPTER_COMMAND", "backend command identity mismatch")

    def validate_result(self, command: Command[dict[str, Any]], result: object) -> None:
        schema = self.build.registry.message(
            command.delivery.message.schema_id
        ).result_schema
        if schema is None:
            raise KernelError(
                "ADAPTER_COMMAND", "typed terminal result schema required"
            )
        self.build.registry.validate(schema, result)

    def command_updates(self, ctx: EngineContext, updates: object) -> None:
        for update in records(updates):
            backend_id = update["command_id"]
            command = self.pending[backend_id]
            if integer(update["sim_ns"], "command observation") > ctx.now.ns:
                raise KernelError("ADAPTER_TIME", "future command observation")
            action = command.delivery.message.schema_id.rsplit(".", 1)[1]
            expected_action = (
                "goto_location"
                if self.backend == "px4_gazebo" and action == "goto"
                else action
            )
            if (
                update["action"] != expected_action
                or integer(update["sim_ns"], "command time")
                < self.pending_times[backend_id]
            ):
                raise KernelError(
                    "ADAPTER_COMMAND", "command observation identity/time mismatch"
                )
            if (
                self.backend == "px4_gazebo"
                and update["vehicle"]
                != self.build.config["vehicles"][command.payload["entity"]]
            ):
                raise KernelError(
                    "ADAPTER_COMMAND", "command observation vehicle mismatch"
                )
            status = update["status"]
            previous = self.statuses[backend_id]
            if status in ("running", "executing") and previous == "accepted":
                ctx.execute(command)
                self.statuses[backend_id] = "executing"
            elif status in ("succeeded", "failed"):
                if status == "succeeded" and previous != "executing":
                    raise KernelError("ADAPTER_COMMAND", "completion before execution")
                result = {**update, "available_ns": ctx.now.ns}
                self.validate_result(command, result)
                if status == "succeeded":
                    ctx.succeed(command, result)
                else:
                    ctx.fail(command, result)
                del self.pending[backend_id]
                del self.statuses[backend_id]
                del self.pending_times[backend_id]
            else:
                raise KernelError(
                    "ADAPTER_COMMAND", "invalid backend action transition"
                )

    def close(self) -> None:
        if self.closed:
            return
        try:
            if self.ready and not self.client.tainted:
                result = self.client.request("close", {})
                if result != {"closed": True}:
                    raise KernelError("ADAPTER_CLOSE", "native cleanup not confirmed")
        finally:
            self.client.disconnect()
            super().close()
