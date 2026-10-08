"""Strict, bounded, one-flight JSON-lines transport without retries."""

from __future__ import annotations

import math
import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import partial
from typing import Any, TypeVar

from .errors import KernelError
from .values import ResourceBudget, canonical_json, parse_json

PROTOCOL = "aerokernel.rpc"
MAJOR = 1
MINOR = 0
OPERATIONS = frozenset({"hello", "reset", "horizon", "advance", "react", "close"})
T = TypeVar("T")


def bounded(call: Callable[[], T], timeout: float, **context: Any) -> T:
    """Bound even a blocking foreign stream/callback; never reuse a timed-out call."""
    result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def work() -> None:
        try:
            result.put((True, call()))
        except Exception as error:
            result.put((False, error))

    deadline = time.monotonic() + timeout
    thread = threading.Thread(target=work, daemon=True, name="aerokernel-rpc")
    thread.start()
    try:
        ok, value = result.get(timeout=max(0.0, deadline - time.monotonic()))
    except queue.Empty as error:
        raise KernelError(
            "RPC_TIMEOUT", "operation wall-clock deadline", **context
        ) from error
    if time.monotonic() > deadline:
        raise KernelError("RPC_TIMEOUT", "operation wall-clock deadline", **context)
    if not ok:
        raise value
    return value  # type: ignore[no-any-return]


def timeout_policy(values: Mapping[str, float] | None) -> dict[str, float]:
    """Require a finite positive deadline for every supported operation."""
    policy = {op: 5.0 for op in OPERATIONS}
    if values is not None:
        if set(values) - OPERATIONS:
            raise KernelError("RPC_POLICY", "unknown operation timeout")
        policy.update(values)
    if any(
        type(v) not in (int, float) or not math.isfinite(v) or v <= 0
        for v in policy.values()
    ):
        raise KernelError("RPC_POLICY", "finite positive timeout required")
    return policy


@dataclass(frozen=True)
class PausePolicy:
    """Separate finite request-arrival and explicit wall-clock lease budgets."""

    idle_timeout_s: float = 3600.0
    max_hold_s: float = 3600.0
    frame_timeout_s: float = 5.0

    def __post_init__(self) -> None:
        for value in (self.idle_timeout_s, self.max_hold_s, self.frame_timeout_s):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise KernelError("RPC_POLICY", "finite positive pause budget required")

    def check_hold(self, duration_s: float) -> None:
        """Validate an explicit lease without weakening operation deadlines."""
        if (
            type(duration_s) not in (int, float)
            or not math.isfinite(duration_s)
            or not 0 < duration_s <= self.max_hold_s
        ):
            raise KernelError("RPC_HOLD", "lease must fit declared maximum")


def pause_policy_from_data(data: object) -> PausePolicy:
    """Reconstruct a complete pinned declaration without missing-value defaults."""
    if not isinstance(data, dict) or set(data) != {
        "idle_timeout_s",
        "max_hold_s",
        "frame_timeout_s",
    }:
        raise KernelError("RPC_POLICY", "complete pause declaration required")
    return PausePolicy(**data)


class Framer:
    """Binary stream framing; LF counts toward the pinned byte budget."""

    def __init__(
        self, stream: Any, output: Any = None, budget: ResourceBudget | None = None
    ) -> None:
        self.stream = stream
        self.output = stream if output is None else output
        self.budget = ResourceBudget() if budget is None else budget
        self.buffer = bytearray()

    def read(self) -> dict[str, Any]:
        """Read exactly one strict UTF-8 JSON object without consuming its successor."""
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                if newline + 1 > self.budget.frame_bytes:
                    raise KernelError(
                        "RPC_OVERSIZE", "frame including LF exceeds budget"
                    )
                line = bytes(self.buffer[: newline + 1])
                del self.buffer[: newline + 1]
                if line.endswith(b"\r\n") or line.startswith(b"\xef\xbb\xbf"):
                    raise KernelError(
                        "RPC_FRAMING", "LF and UTF-8 without BOM required"
                    )
                value = parse_json(line, self.budget)
                if not isinstance(value, dict):
                    raise KernelError("RPC_FRAMING", "object envelope required")
                return value
            if len(self.buffer) >= self.budget.frame_bytes:
                raise KernelError("RPC_OVERSIZE", "unterminated frame exceeds budget")
            self._fill()

    def _fill(self) -> None:
        if hasattr(self.stream, "recv"):
            data = self.stream.recv(min(65536, self.budget.frame_bytes))
        elif hasattr(self.stream, "read1"):
            data = self.stream.read1(min(65536, self.budget.frame_bytes))
        else:
            data = self.stream.read(1)
        if not isinstance(data, bytes):
            raise KernelError("RPC_FRAMING", "binary stream required")
        if not data:
            raise KernelError("RPC_EOF", "EOF before complete response")
        self.buffer.extend(data)

    def read_request(self, arrival_s: float, frame_s: float) -> dict[str, Any]:
        """Wait for arrival separately from draining/parsing a started frame."""
        if not self.buffer:
            bounded(self._fill, arrival_s, op="read", timeout_s=arrival_s)
        return bounded(self.read, frame_s, op="drain", timeout_s=frame_s)

    def write(self, value: object) -> None:
        """Write a complete canonical frame; short writes are completed, not retried."""
        data = canonical_json(value, self.budget)
        if hasattr(self.output, "sendall"):
            self.output.sendall(data)
        else:
            offset = 0
            while offset < len(data):
                count = self.output.write(data[offset:])
                if type(count) is not int or count <= 0 or count > len(data) - offset:
                    raise KernelError("RPC_WRITE", "stream did not acknowledge write")
                offset += count
            self.output.flush()


def envelope(request_id: int, op: str, payload: object) -> dict[str, Any]:
    """Versioned request identity with no implicit correlation."""
    return {
        "protocol": PROTOCOL,
        "major": MAJOR,
        "minor": MINOR,
        "id": request_id,
        "op": op,
        "payload": payload,
    }


def check_identity(value: Mapping[str, Any], request_id: int, op: str) -> None:
    """Validate versions and exact request identity, including int/bool distinction."""
    if (
        value.get("protocol") != PROTOCOL
        or type(value.get("major")) is not int
        or value["major"] != MAJOR
        or type(value.get("minor")) is not int
        or value["minor"] != MINOR
        or type(value.get("id")) is not int
        or value["id"] != request_id
        or value.get("op") != op
    ):
        raise KernelError("RPC_IDENTITY", "wrong protocol/version/request identity")


class RPCConnection:
    """Serial client; any uncertain response permanently taints the connection."""

    def __init__(
        self,
        stream: Any,
        output: Any = None,
        *,
        budget: ResourceBudget | None = None,
        timeouts: Mapping[str, float] | None = None,
        pause_policy: PausePolicy | None = None,
    ) -> None:
        self.framer = Framer(stream, output, budget)
        self.timeouts = timeout_policy(timeouts)
        if pause_policy is not None and not isinstance(pause_policy, PausePolicy):
            raise KernelError("RPC_POLICY", "typed pause declaration required")
        self.pause_policy = pause_policy
        self._state_lock = threading.Lock()
        self.state = "new"
        self.request_id = 0
        self._lock = threading.Lock()

    @property
    def profile(self) -> dict[str, Any]:
        """Pinned negotiated transport and wall-clock policy for the run header."""
        from .codec import encode

        return {
            "protocol": PROTOCOL,
            "major": MAJOR,
            "minor": MINOR,
            "timeouts": self.timeouts.copy(),
            "budget": encode(self.framer.budget),
            **(
                {"pause_policy": asdict(self.pause_policy)} if self.pause_policy else {}
            ),
        }

    def taint(self) -> None:
        """Prevent any later engine call or retry after an uncertain result."""
        with self._state_lock:
            self.state = "tainted"

    def call(self, op: str, payload: object) -> Any:
        """One request, one matching response, one total write/read deadline."""
        if not self._lock.acquire(blocking=False):
            self.taint()
            raise KernelError("RPC_IN_FLIGHT", "only one request may be in flight")
        try:
            legal = (
                (self.state == "new" and op == "hello")
                or (self.state == "hello" and op in {"reset", "close"})
                or (
                    self.state == "ready"
                    and (
                        op in {"horizon", "advance", "react", "close"}
                        or (op == "hold" and self.pause_policy is not None)
                    )
                )
            )
            if not legal:
                self.taint()
                raise KernelError("RPC_STATE", "operation violates handshake/lifecycle")
            self.request_id += 1
            request_id = self.request_id
            timeout = self.timeouts["horizon" if op == "hold" else op]

            def exchange() -> Any:
                self.framer.write(envelope(request_id, op, payload))
                response = self.framer.read()
                check_identity(response, request_id, op)
                common = {"protocol", "major", "minor", "id", "op"}
                if set(response) == common | {"error"}:
                    error = response["error"]
                    if (
                        not isinstance(error, dict)
                        or set(error) != {"code", "message"}
                        or not isinstance(error["code"], str)
                        or not error["code"]
                        or not isinstance(error["message"], str)
                    ):
                        raise KernelError("RPC_RESPONSE", "malformed remote fault")
                    raise KernelError(
                        "RPC_REMOTE", error["message"], remote_code=error["code"]
                    )
                if set(response) != common | {"result"}:
                    raise KernelError(
                        "RPC_RESPONSE", "exact result/error response required"
                    )
                return response["result"]

            try:
                result = bounded(
                    exchange, timeout, op=op, request_id=request_id, timeout_s=timeout
                )
            except Exception as error:
                self.taint()
                code = error.code if isinstance(error, KernelError) else "RPC_IO"
                raise KernelError(
                    code,
                    str(error),
                    op=op,
                    request_id=request_id,
                    timeout_s=timeout,
                    policy=self.profile,
                ) from error
            with self._state_lock:
                if self.state == "tainted":
                    raise KernelError(
                        "RPC_STATE", "in-flight result discarded after taint"
                    )
                self.state = {
                    "hello": "hello",
                    "reset": "ready",
                    "close": "closed",
                }.get(op, self.state)
            return result
        finally:
            self._lock.release()


def serve_requests(
    handler: Callable[[str, Any], Any],
    stream: Any,
    output: Any = None,
    *,
    budget: ResourceBudget | None = None,
    timeouts: Mapping[str, float] | None = None,
    pause_policy: PausePolicy | None = None,
) -> None:
    """Serve a strict serial lifecycle; identifiable faults are returned once."""
    framer = Framer(stream, output, budget)
    policy = timeout_policy(timeouts)
    if pause_policy is not None and not isinstance(pause_policy, PausePolicy):
        raise KernelError("RPC_POLICY", "typed pause declaration required")
    pauses = PausePolicy() if pause_policy is None else pause_policy
    state = "new"
    request_id = 0
    arrival_budget = pauses.idle_timeout_s
    while state != "closed":
        request = framer.read_request(arrival_budget, pauses.frame_timeout_s)
        op = request.get("op")
        if not isinstance(op, str) or op not in OPERATIONS | {"hold"}:
            raise KernelError("RPC_OPERATION", "unknown operation")
        incoming_id = request.get("id")
        if type(incoming_id) is not int or incoming_id <= request_id:
            raise KernelError("RPC_IDENTITY", "positive increasing request ID required")
        check_identity(request, incoming_id, op)
        request_id = incoming_id
        if set(request) != {"protocol", "major", "minor", "id", "op", "payload"}:
            raise KernelError("RPC_REQUEST", "unexpected request fields")
        response = {
            key: request[key] for key in ("protocol", "major", "minor", "id", "op")
        }
        operation_budget = policy["horizon" if op == "hold" else op]
        try:
            if not (
                (state == "new" and op == "hello")
                or (state == "hello" and op in {"reset", "close"})
                or (
                    state == "ready"
                    and (
                        op in {"horizon", "advance", "react", "close"}
                        or (op == "hold" and pause_policy is not None)
                    )
                )
            ):
                raise KernelError("RPC_STATE", "operation violates handshake/lifecycle")
            if op == "hold":
                payload = request["payload"]
                if not isinstance(payload, dict) or "duration_s" not in payload:
                    raise KernelError("RPC_HOLD", "explicit duration required")
                pauses.check_hold(payload["duration_s"])
            response["result"] = bounded(
                partial(handler, op, request["payload"]),
                operation_budget,
                op=op,
                request_id=request_id,
                timeout_s=operation_budget,
            )
        except Exception as error:
            response["error"] = {
                "code": error.code
                if isinstance(error, KernelError)
                else "ENGINE_EXCEPTION",
                "message": str(error),
            }
            bounded(partial(framer.write, response), operation_budget, op="fault_write")
            raise
        bounded(
            partial(framer.write, response),
            operation_budget,
            op="write",
            request_id=request_id,
        )
        state = {"hello": "hello", "reset": "ready", "close": "closed"}.get(op, state)
        arrival_budget = pauses.idle_timeout_s
        if op == "hold":
            duration = request["payload"]["duration_s"]
            pauses.check_hold(duration)
            arrival_budget = duration
