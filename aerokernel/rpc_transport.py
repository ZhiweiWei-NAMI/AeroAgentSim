"""Strict, bounded, one-flight JSON-lines transport without retries."""

from __future__ import annotations

import math
import queue
import threading
import time
from collections.abc import Callable, Mapping
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
    ) -> None:
        self.framer = Framer(stream, output, budget)
        self.timeouts = timeout_policy(timeouts)
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
        }

    def taint(self) -> None:
        """Prevent any later engine call or retry after an uncertain result."""
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
                    and op in {"horizon", "advance", "react", "close"}
                )
            )
            if not legal:
                self.taint()
                raise KernelError("RPC_STATE", "operation violates handshake/lifecycle")
            self.request_id += 1
            request_id = self.request_id
            timeout = self.timeouts[op]

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
            self.state = {"hello": "hello", "reset": "ready", "close": "closed"}.get(
                op, self.state
            )
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
) -> None:
    """Serve a strict serial lifecycle; identifiable faults are returned once."""
    framer = Framer(stream, output, budget)
    policy = timeout_policy(timeouts)
    state = "new"
    request_id = 1
    while state != "closed":
        request = bounded(
            framer.read, max(policy.values()), op="read", request_id=request_id
        )
        op = request.get("op")
        if not isinstance(op, str) or op not in OPERATIONS:
            raise KernelError("RPC_OPERATION", "unknown operation")
        check_identity(request, request_id, op)
        if set(request) != {"protocol", "major", "minor", "id", "op", "payload"}:
            raise KernelError("RPC_REQUEST", "unexpected request fields")
        response = {
            key: request[key] for key in ("protocol", "major", "minor", "id", "op")
        }
        try:
            if not (
                (state == "new" and op == "hello")
                or (state == "hello" and op in {"reset", "close"})
                or (state == "ready" and op in {"horizon", "advance", "react", "close"})
            ):
                raise KernelError("RPC_STATE", "operation violates handshake/lifecycle")
            response["result"] = bounded(
                partial(handler, op, request["payload"]),
                policy[op],
                op=op,
                request_id=request_id,
                timeout_s=policy[op],
            )
        except Exception as error:
            response["error"] = {
                "code": error.code
                if isinstance(error, KernelError)
                else "ENGINE_EXCEPTION",
                "message": str(error),
            }
            bounded(partial(framer.write, response), policy[op], op="fault_write")
            raise
        bounded(
            partial(framer.write, response),
            policy[op],
            op="write",
            request_id=request_id,
        )
        state = {"hello": "hello", "reset": "ready", "close": "closed"}.get(op, state)
        request_id += 1
