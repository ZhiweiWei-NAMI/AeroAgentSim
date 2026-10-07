from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
import traceback
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class RpcEndpoint(Protocol):
    host: str
    port: int


class ProviderRpcError(RuntimeError):
    pass


class ProviderRemoteError(ProviderRpcError):
    def __init__(self, *, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"provider RPC failed ({code}): {detail}")


class _ResponseDrainError(ProviderRpcError):
    pass


_OPERATION_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _validate_operation(operation: str) -> None:
    if not _OPERATION_PATTERN.fullmatch(operation):
        raise ValueError("provider operation must match ^[A-Za-z][A-Za-z0-9._-]*$")


def canonical_json_line(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProviderRpcError("provider RPC value is not canonical JSON") from exc
    return encoded + b"\n"


def parse_json_object(frame: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            frame,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ProviderRpcError("provider RPC frame is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ProviderRpcError("provider RPC frame must be a JSON object")
    return value


class JsonLineRpcTransport:
    MAX_FRAME_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        component: str,
    ):
        self._reader = reader
        self._writer = writer
        self._component = component
        self._closed = False
        self._request_lock = asyncio.Lock()

    @classmethod
    async def connect(
        cls, endpoint: RpcEndpoint, *, component: str
    ) -> JsonLineRpcTransport:
        try:
            reader, writer = await asyncio.open_connection(
                endpoint.host,
                endpoint.port,
                limit=cls.MAX_FRAME_BYTES + 1,
            )
        except OSError as exc:
            raise ProviderRpcError(
                f"cannot connect to {component} endpoint {endpoint.host}:{endpoint.port}"
            ) from exc
        return cls(reader, writer, component=component)

    async def request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self._closed:
            raise ProviderRpcError(f"{self._component} transport is closed")
        _validate_operation(operation)
        if "operation" in payload:
            raise ValueError("provider payload cannot redefine operation")
        encoded = canonical_json_line({"operation": operation, **dict(payload)})
        if len(encoded) > self.MAX_FRAME_BYTES:
            raise ProviderRpcError("provider request exceeds the frame limit")
        async with self._request_lock:
            self._writer.write(encoded)
            try:
                await self._writer.drain()
                response_bytes = await self._reader.readline()
            except (OSError, ValueError) as exc:
                raise ProviderRpcError("provider RPC transport I/O failed") from exc
        if not response_bytes:
            raise ProviderRpcError("provider closed the RPC stream")
        if len(response_bytes) > self.MAX_FRAME_BYTES:
            raise ProviderRpcError("provider response exceeds the frame limit")
        if not response_bytes.endswith(b"\n"):
            raise ProviderRpcError("provider response is not newline terminated")
        response = parse_json_object(response_bytes)
        if "error" in response:
            if set(response) != {"error"}:
                raise ProviderRpcError("provider error response is malformed")
            remote_error = response["error"]
            if not isinstance(remote_error, dict):
                raise ProviderRpcError("provider error response is malformed")
            if set(remote_error) != {"code", "detail"}:
                raise ProviderRpcError("provider error response is malformed")
            code = remote_error.get("code")
            detail = remote_error.get("detail")
            if not isinstance(code, str) or not isinstance(detail, str):
                raise ProviderRpcError("provider error response is malformed")
            raise ProviderRemoteError(code=code, detail=detail)
        return response

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except OSError:
            pass


RpcHandler = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
ResponseDrainCallback = Callable[[str, Mapping[str, Any], bool], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class ResponseDrainEvidence:
    operation: str
    drained: bool


class JsonLineRpcServer:
    MAX_FRAME_BYTES = JsonLineRpcTransport.MAX_FRAME_BYTES

    def __init__(
        self,
        handler: RpcHandler,
        *,
        response_drained: ResponseDrainCallback | None = None,
    ):
        self._handler = handler
        self._response_drained = response_drained
        self._response_drain_events: list[ResponseDrainEvidence] = []
        self._server: asyncio.AbstractServer | None = None
        self._clients: dict[asyncio.Task[None], _RpcClient] = {}
        self._clients_drained = asyncio.Event()
        self._clients_drained.set()
        self._stopping = False

    @property
    def stopping(self) -> bool:
        return self._stopping

    @property
    def response_drain_events(self) -> tuple[ResponseDrainEvidence, ...]:
        return tuple(self._response_drain_events)

    async def start(self, *, host: str, port: int) -> "JsonLineRpcServerHandle":
        if self._server is not None:
            raise ProviderRpcError("JSON-line RPC server is already started")
        self._stopping = False
        self._response_drain_events.clear()
        self._server = await asyncio.start_server(
            self._handle_client,
            host=host,
            port=port,
            limit=self.MAX_FRAME_BYTES + 1,
        )
        return JsonLineRpcServerHandle(self, self._server)

    async def serve(self, *, host: str, port: int) -> None:
        server = await self.start(host=host, port=port)
        async with server:
            await server.serve_forever()

    async def graceful_close(self, *, timeout_seconds: float | None = None) -> None:
        """Stop accepts/frames and wait for every response drain to finish."""

        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._stopping = True
        server = self._server
        if server is not None:
            server.close()
        for client in tuple(self._clients.values()):
            if not client.handler_active:
                client.writer.close()
        try:
            if timeout_seconds is None:
                await self._clients_drained.wait()
                if server is not None:
                    await server.wait_closed()
            else:
                deadline = asyncio.get_running_loop().time() + timeout_seconds
                await asyncio.wait_for(
                    self._clients_drained.wait(), timeout=timeout_seconds
                )
                if server is not None:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    await asyncio.wait_for(server.wait_closed(), timeout=remaining)
        except asyncio.TimeoutError as error:
            await self.force_close()
            raise ProviderRpcError(
                "JSON-line RPC graceful close exceeded its explicit deadline"
            ) from error
        self._server = None

    async def force_close(self) -> None:
        """Cancel active clients and await their finalizers after a close timeout."""

        self._stopping = True
        server = self._server
        if server is not None:
            server.close()
        tasks = tuple(self._clients)
        for client in tuple(self._clients.values()):
            client.writer.close()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        self._clients.clear()
        self._clients_drained.set()
        self._server = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        client = _RpcClient(writer=writer)
        if task is not None:
            self._clients[task] = client
            self._clients_drained.clear()
        try:
            while not self._stopping and (frame := await reader.readline()):
                if self._stopping:
                    break
                client.handler_active = True
                operation = "<unparsed>"
                if len(frame) > self.MAX_FRAME_BYTES:
                    await self._write_error(
                        writer,
                        code="frame.too-large",
                        detail="provider request exceeds the frame limit",
                    )
                    break
                if not frame.endswith(b"\n"):
                    await self._write_error(
                        writer,
                        code="frame.incomplete",
                        detail="provider request is not newline terminated",
                    )
                    break
                try:
                    request = parse_json_object(frame)
                    operation = request.pop("operation", None)
                    if not isinstance(operation, str):
                        raise ProviderRpcError("provider operation must be a string")
                    _validate_operation(operation)
                    response = await self._handler(operation, request)
                    encoded = canonical_json_line(response)
                    if len(encoded) > self.MAX_FRAME_BYTES:
                        raise ProviderRpcError(
                            "provider response exceeds the frame limit"
                        )
                    await self._write_response(
                        writer,
                        operation=operation,
                        response=response,
                        encoded=encoded,
                    )
                    client.handler_active = False
                except (ProviderRpcError, ValueError) as exc:
                    if isinstance(exc, _ResponseDrainError):
                        break
                    self._log_handler_failure(operation, exc)
                    await self._write_error(
                        writer, code="request.invalid", detail=str(exc)
                    )
                    client.handler_active = False
                except Exception as exc:
                    # An unexpected provider bug must not disappear as a silent
                    # connection close.  Keep the wire fail-closed, but emit the
                    # operation and traceback to the workload's stderr so the
                    # executor can report the actual failure cause.  The request
                    # payload is intentionally excluded to avoid logging tokens
                    # or provider-private state.
                    self._log_handler_failure(operation, exc)
                    raise
        finally:
            if task is not None:
                self._clients.pop(task, None)
                if not self._clients:
                    self._clients_drained.set()
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    @staticmethod
    def _log_handler_failure(operation: str, error: BaseException) -> None:
        print(
            "provider RPC handler failed "
            f"operation={operation!r} "
            f"exception={type(error).__name__}: {error}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exception(error, file=sys.stderr)

    @staticmethod
    async def _write_error(
        writer: asyncio.StreamWriter, *, code: str, detail: str
    ) -> None:
        writer.write(canonical_json_line({"error": {"code": code, "detail": detail}}))
        await writer.drain()

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        *,
        operation: str,
        response: Mapping[str, Any],
        encoded: bytes,
    ) -> None:
        try:
            if writer.is_closing():
                raise ConnectionError("response writer is closing")
            writer.write(encoded)
            await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._record_response_drain(operation, response, drained=False)
            raise _ResponseDrainError("provider response drain failed") from error
        await self._record_response_drain(operation, response, drained=True)

    async def _record_response_drain(
        self,
        operation: str,
        response: Mapping[str, Any],
        *,
        drained: bool,
    ) -> None:
        self._response_drain_events.append(
            ResponseDrainEvidence(operation=operation, drained=drained)
        )
        callback = self._response_drained
        if callback is None:
            return
        try:
            result = callback(operation, response, drained)
            if inspect.isawaitable(result):
                await result
        except BaseException:
            # The transport evidence remains authoritative even if an observer
            # cannot consume it. The observer must not corrupt the response wire.
            return


@dataclass(slots=True)
class _RpcClient:
    writer: asyncio.StreamWriter
    handler_active: bool = False


class JsonLineRpcServerHandle:
    """Small server handle retaining the historical close/wait surface."""

    def __init__(self, owner: JsonLineRpcServer, server: asyncio.AbstractServer):
        self._owner = owner
        self._server = server

    @property
    def sockets(self):
        return self._server.sockets

    def is_serving(self) -> bool:
        return self._server.is_serving()

    def close(self) -> None:
        self._owner._stopping = True
        self._server.close()

    async def wait_closed(self) -> None:
        await self._server.wait_closed()

    @property
    def response_drain_events(self) -> tuple[ResponseDrainEvidence, ...]:
        return self._owner.response_drain_events

    async def graceful_close(self, *, timeout_seconds: float | None = None) -> None:
        await self._owner.graceful_close(timeout_seconds=timeout_seconds)

    async def force_close(self) -> None:
        await self._owner.force_close()

    async def serve_forever(self) -> None:
        await self._server.serve_forever()

    async def __aenter__(self) -> "JsonLineRpcServerHandle":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.graceful_close()
