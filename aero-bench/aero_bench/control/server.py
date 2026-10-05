from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import socket
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, StrictStr, ValidationError, field_validator, model_validator

from aero_bench.config.models import StrictModel
from aero_bench.control.contracts import (
    ControlApiError,
    ControlApiErrorResponse,
    PublicRunEventStreamEvent,
    ReplayAccessRequest,
    RunTransitionEvent,
    RuntimeControlRequest,
    SceneStateStreamEvent,
    StartRunRequest,
)
from aero_bench.control.manager import ControlManagerError, ControlRunManager
from aero_bench.control.sealed_replay import SealedReplayManager
from aero_bench.providers.rpc import parse_json_object
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.projector import project_public_run_event


_RUN_PATH = re.compile(r"^/v1/runs/([0-9a-f]{64})$")
_REPLAY_ACCESS_PATH = re.compile(r"^/v1/runs/([0-9a-f]{64})/replay-access$")
_EVENTS_PATH = re.compile(r"^/v1/runs/([0-9a-f]{64})/events$")
_ASSET_PATH = re.compile(r"^/v1/runs/([0-9a-f]{64})/assets/([0-9a-f]{64})$")
_PUBLIC_TRACE_PATH = re.compile(r"^/v1/runs/([0-9a-f]{64})/public/trace$")
_PUBLIC_REPLAY_MANIFEST_PATH = re.compile(
    r"^/v1/runs/([0-9a-f]{64})/public/replay-manifest$"
)
_CONTROL_PATH = re.compile(
    r"^/v1/runs/([0-9a-f]{64})/controls/(pause|resume|step|stop)$"
)
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ControlHttpConfig(StrictModel):
    schema_version: Literal["aero-bench.control-http-config/v1"]
    bind_host: StrictStr
    port: Annotated[int, Field(ge=1024, le=65535)]
    allowed_origins: tuple[StrictStr, ...] = Field(min_length=1)
    allowed_hosts: tuple[StrictStr, ...] = Field(min_length=1)
    max_request_body_bytes: Annotated[int, Field(ge=1024, le=1_048_576)] = 16_384
    requests_per_minute: Annotated[int, Field(ge=10, le=10_000)] = 120
    max_concurrent_requests: Annotated[int, Field(ge=2, le=256)] = 32

    @field_validator("bind_host")
    @classmethod
    def loopback_bind_only(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError("control bind_host must be a loopback IP address") from error
        if not address.is_loopback:
            raise ValueError("control HTTP service may bind only to loopback")
        return value

    @model_validator(mode="after")
    def canonical_authorities(self) -> "ControlHttpConfig":
        if self.allowed_origins != tuple(sorted(set(self.allowed_origins))):
            raise ValueError("allowed_origins must be sorted and unique")
        if self.allowed_hosts != tuple(sorted(set(self.allowed_hosts))):
            raise ValueError("allowed_hosts must be sorted and unique")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("allowed origin must be an exact HTTP origin")
        for host in self.allowed_hosts:
            if (
                not host
                or any(character.isspace() for character in host)
                or "/" in host
                or "@" in host
            ):
                raise ValueError("allowed Host authority is invalid")
        return self


class ControlHttpServerError(RuntimeError):
    pass


class _RateLimiter:
    def __init__(self, *, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            if key not in self._requests and len(self._requests) >= 4096:
                stale = tuple(
                    existing_key
                    for existing_key, existing_requests in self._requests.items()
                    if not existing_requests or existing_requests[-1] <= cutoff
                )
                for existing_key in stale:
                    self._requests.pop(existing_key, None)
                if len(self._requests) >= 4096:
                    return False
            requests = self._requests[key]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self._limit:
                return False
            requests.append(now)
            return True


@dataclass(slots=True)
class _ServerContext:
    manager: ControlRunManager | SealedReplayManager
    config: ControlHttpConfig
    bootstrap_token: str = field(repr=False)
    bootstrap_csrf_token: str = field(repr=False)
    limiter: _RateLimiter
    request_slots: threading.BoundedSemaphore


class _ControlRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AEROBenchControl/1"
    sys_version = ""
    context: _ServerContext

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15.0)
        self._response_started = False

    def do_GET(self) -> None:  # noqa: N802
        self._within_request_slot(self._do_get)

    def do_POST(self) -> None:  # noqa: N802
        self._within_request_slot(self._do_post)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._within_request_slot(self._do_options)

    def do_HEAD(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method.unsupported", "method unsupported")

    def do_PUT(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method.unsupported", "method unsupported")

    def do_DELETE(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method.unsupported", "method unsupported")

    def do_PATCH(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method.unsupported", "method unsupported")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _within_request_slot(self, operation: Callable[[], None]) -> None:
        self._response_started = False
        if not self.context.request_slots.acquire(blocking=False):
            self._error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "service.capacity",
                "control service request capacity is exhausted",
            )
            return
        try:
            operation()
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            if self._response_started:
                self.close_connection = True
                return
            try:
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "service.failed",
                    "control service failed",
                )
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            self.context.request_slots.release()

    def _do_get(self) -> None:
        parsed = self._validate_request_boundary()
        if parsed is None:
            return
        path = parsed.path
        if path == "/v1/catalog" and not parsed.query:
            bootstrap_token = self._require_bootstrap_auth()
            if bootstrap_token is None or not self._require_rate_limit(
                bootstrap_token
            ):
                return
            self._json(HTTPStatus.OK, self.context.manager.catalog.model_dump(mode="json"))
            return

        run_match = _RUN_PATH.fullmatch(path)
        if run_match is not None and not parsed.query:
            token = self._require_bearer()
            if token is None or not self._require_rate_limit(token):
                return
            try:
                response = self.context.manager.status(
                    run_match.group(1),
                    operator_token=token,
                )
            except ControlManagerError as error:
                self._manager_error(error)
                return
            self._json(HTTPStatus.OK, response.model_dump(mode="json"))
            return

        asset_match = _ASSET_PATH.fullmatch(path)
        if asset_match is not None and not parsed.query:
            token = self._require_bearer()
            if token is None or not self._require_rate_limit(token):
                return
            try:
                data, media_type = self.context.manager.public_asset(
                    asset_match.group(1), asset_match.group(2), operator_token=token,
                )
            except ControlManagerError as error:
                self._manager_error(error)
                return
            self._response_started = True
            self.send_response(HTTPStatus.OK)
            self._security_headers()
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        public_trace_match = _PUBLIC_TRACE_PATH.fullmatch(path)
        if public_trace_match is not None and not parsed.query:
            token = self._require_bearer()
            if token is None or not self._require_rate_limit(token):
                return
            try:
                data = self.context.manager.public_trace_document(
                    public_trace_match.group(1), operator_token=token,
                )
            except ControlManagerError as error:
                self._manager_error(error)
                return
            self._response_started = True
            self.send_response(HTTPStatus.OK)
            self._security_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        public_replay_manifest_match = _PUBLIC_REPLAY_MANIFEST_PATH.fullmatch(path)
        if public_replay_manifest_match is not None and not parsed.query:
            token = self._require_bearer()
            if token is None or not self._require_rate_limit(token):
                return
            try:
                data = self.context.manager.public_replay_manifest_document(
                    public_replay_manifest_match.group(1), operator_token=token,
                )
            except ControlManagerError as error:
                self._manager_error(error)
                return
            self._response_started = True
            self.send_response(HTTPStatus.OK)
            self._security_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        events_match = _EVENTS_PATH.fullmatch(path)
        if events_match is not None:
            token = self._require_bearer()
            if token is None or not self._require_rate_limit(token):
                return
            cursors = self._parse_event_cursors(parsed.query)
            if cursors is None:
                return
            transition_cursor, scene_cursor, event_cursor = cursors
            self._stream_events(
                run_id=events_match.group(1),
                operator_token=token,
                transition_cursor=transition_cursor,
                scene_cursor=scene_cursor,
                event_cursor=event_cursor,
            )
            return
        self._error(HTTPStatus.NOT_FOUND, "route.not_found", "route not found")

    def _do_post(self) -> None:
        parsed = self._validate_request_boundary()
        if parsed is None:
            return
        if parsed.query:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "request.invalid",
                "query parameters are not accepted",
            )
            return
        replay_match = _REPLAY_ACCESS_PATH.fullmatch(parsed.path)
        if replay_match is not None and isinstance(self.context.manager, SealedReplayManager):
            token = self._require_bootstrap_auth()
            if token is None or not self._require_rate_limit(token):
                return
            if not self._require_csrf(self.context.bootstrap_csrf_token):
                return
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                ReplayAccessRequest.model_validate(payload)
                response = self.context.manager.issue_read_access(replay_match.group(1))
            except ValidationError:
                self._error(HTTPStatus.BAD_REQUEST, "request.invalid", "replay access request is invalid")
                return
            except ControlManagerError as error:
                self._manager_error(error)
                return
            self._json(HTTPStatus.OK, response.model_dump(mode="json"))
            return
        if parsed.path == "/v1/runs":
            bootstrap_token = self._require_bootstrap_auth()
            if bootstrap_token is None or not self._require_rate_limit(
                bootstrap_token
            ):
                return
            if not self._require_csrf(self.context.bootstrap_csrf_token):
                return
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                request = StartRunRequest.model_validate(payload)
                response = self.context.manager.start(request)
            except ValidationError:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "request.invalid",
                    "start request is invalid",
                )
                return
            except ControlManagerError as error:
                self._manager_error(error)
                return
            self._json(HTTPStatus.ACCEPTED, response.model_dump(mode="json"))
            return

        control_match = _CONTROL_PATH.fullmatch(parsed.path)
        if control_match is not None:
            run_id, operation = control_match.groups()
            token = self._require_bearer()
            if token is None or not self._require_rate_limit(token):
                return
            csrf_values = self.headers.get_all("X-Aero-Bench-CSRF", [])
            csrf_token = csrf_values[0] if len(csrf_values) == 1 else ""
            try:
                self.context.manager.check_csrf(
                    run_id,
                    operator_token=token,
                    csrf_token=csrf_token,
                )
            except ControlManagerError as error:
                self._manager_error(error)
                return
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                request = RuntimeControlRequest.model_validate(payload)
                response = self.context.manager.control(
                    run_id,
                    operator_token=token,
                    operation=operation,
                    control_id=request.control_id,
                )
            except ValidationError:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "request.invalid",
                    "runtime control request is invalid",
                )
                return
            except ControlManagerError as error:
                self._manager_error(error)
                return
            self._json(HTTPStatus.OK, response.model_dump(mode="json"))
            return
        self._error(HTTPStatus.NOT_FOUND, "route.not_found", "route not found")

    def _do_options(self) -> None:
        parsed = self._validate_request_boundary()
        if parsed is None:
            return
        event_stream = _EVENTS_PATH.fullmatch(parsed.path) is not None
        supported_path = (
            parsed.path == "/v1/runs"
            or _CONTROL_PATH.fullmatch(parsed.path) is not None
            or _RUN_PATH.fullmatch(parsed.path) is not None
            or event_stream
            or _ASSET_PATH.fullmatch(parsed.path) is not None
            or _PUBLIC_TRACE_PATH.fullmatch(parsed.path) is not None
            or _PUBLIC_REPLAY_MANIFEST_PATH.fullmatch(parsed.path) is not None
            or parsed.path == "/v1/catalog"
            or (_REPLAY_ACCESS_PATH.fullmatch(parsed.path) is not None
                and isinstance(self.context.manager, SealedReplayManager))
        )
        if not supported_path or (parsed.query and not event_stream):
            self._error(HTTPStatus.NOT_FOUND, "route.not_found", "route not found")
            return
        if event_stream and self._parse_event_cursors(parsed.query) is None:
            return
        self._response_started = True
        self.send_response(HTTPStatus.NO_CONTENT)
        self._security_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Aero-Bench-CSRF",
        )
        self.send_header("Access-Control-Max-Age", "300")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _validate_request_boundary(self):
        hosts = self.headers.get_all("Host", [])
        host = hosts[0] if len(hosts) == 1 else None
        if host not in self.context.config.allowed_hosts:
            self._error(HTTPStatus.BAD_REQUEST, "host.rejected", "Host authority rejected")
            return None
        origins = self.headers.get_all("Origin", [])
        origin = origins[0] if len(origins) == 1 else None
        if origin not in self.context.config.allowed_origins:
            self._error(HTTPStatus.FORBIDDEN, "origin.rejected", "request origin rejected")
            return None
        if "%" in self.path or "#" in self.path:
            self._error(HTTPStatus.BAD_REQUEST, "request.invalid", "request target invalid")
            return None
        return urlsplit(self.path)

    def _require_rate_limit(self, token: str = "bootstrap") -> bool:
        client = self.client_address[0] if self.client_address else "unknown"
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        if self.context.limiter.allow(f"{client}:{token_digest}"):
            return True
        self._error(
            HTTPStatus.TOO_MANY_REQUESTS,
            "rate.exceeded",
            "control service rate limit exceeded",
        )
        return False

    def _require_bootstrap_auth(self) -> str | None:
        token = self._require_bearer()
        if token is None:
            return None
        if not _credential_matches(token, self.context.bootstrap_token):
            self._error(
                HTTPStatus.UNAUTHORIZED,
                "authentication.failed",
                "bootstrap authentication failed",
            )
            return None
        return token

    def _require_bearer(self) -> str | None:
        if not self._require_rate_limit("authentication"):
            return None
        authorizations = self.headers.get_all("Authorization", [])
        authorization = authorizations[0] if len(authorizations) == 1 else ""
        if not authorization.startswith("Bearer "):
            self._error(
                HTTPStatus.UNAUTHORIZED,
                "authentication.failed",
                "Bearer authentication is required",
            )
            return None
        token = authorization[7:]
        if _TOKEN_PATTERN.fullmatch(token) is None or token == "0" * 64:
            self._error(
                HTTPStatus.UNAUTHORIZED,
                "authentication.failed",
                "Bearer authentication failed",
            )
            return None
        return token

    def _require_csrf(self, expected: str) -> bool:
        csrf_values = self.headers.get_all("X-Aero-Bench-CSRF", [])
        candidate = csrf_values[0] if len(csrf_values) == 1 else ""
        if not _credential_matches(candidate, expected):
            self._error(HTTPStatus.FORBIDDEN, "csrf.failed", "CSRF validation failed")
            return False
        return True

    def _read_json_body(self) -> dict[str, object] | None:
        if self.headers.get_all("Transfer-Encoding", []):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "request.invalid",
                "transfer encoding is not accepted",
            )
            return None
        content_types = self.headers.get_all("Content-Type", [])
        content_type = (
            content_types[0].split(";", 1)[0].strip()
            if len(content_types) == 1
            else ""
        )
        if content_type != "application/json":
            self._error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "request.media_type",
                "application/json is required",
            )
            return None
        content_lengths = self.headers.get_all("Content-Length", [])
        content_length = content_lengths[0] if len(content_lengths) == 1 else None
        if content_length is None or not content_length.isascii() or not content_length.isdigit():
            self._error(
                HTTPStatus.LENGTH_REQUIRED,
                "request.length",
                "valid Content-Length is required",
            )
            return None
        size = int(content_length)
        if size <= 0 or size > self.context.config.max_request_body_bytes:
            self._error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "request.too_large",
                "request body exceeds its bound",
            )
            return None
        try:
            payload = self.rfile.read(size)
        except TimeoutError:
            self._error(
                HTTPStatus.REQUEST_TIMEOUT,
                "request.timeout",
                "request body timed out",
            )
            return None
        if len(payload) != size:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "request.invalid",
                "request body is incomplete",
            )
            return None
        try:
            return parse_json_object(payload)
        except Exception:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "request.invalid",
                "request body is invalid JSON",
            )
            return None

    def _parse_event_cursors(
        self,
        query: str,
    ) -> tuple[int, int, int] | None:
        try:
            parsed = parse_qs(query, strict_parsing=True, keep_blank_values=True)
        except ValueError:
            parsed = {}
        expected = {
            "after_event_sequence": -1,
            "after_scene_tick": 0,
            "after_transition": -1,
        }
        if set(parsed) != set(expected) or any(
            len(parsed[name]) != 1 for name in expected
        ):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "request.invalid",
                "event stream requires all three canonical cursors",
            )
            return None
        values: dict[str, int] = {}
        for name, minimum in expected.items():
            raw = parsed[name][0]
            try:
                value = int(raw)
            except ValueError:
                value = minimum - 1
            if str(value) != raw or value < minimum:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "request.invalid",
                    "event stream cursor is invalid",
                )
                return None
            values[name] = value
        return (
            values["after_transition"],
            values["after_scene_tick"],
            values["after_event_sequence"],
        )

    def _stream_events(
        self,
        *,
        run_id: str,
        operator_token: str,
        transition_cursor: int,
        scene_cursor: int,
        event_cursor: int,
    ) -> None:
        try:
            pending = self.context.manager.transitions_after(
                run_id,
                operator_token=operator_token,
                sequence=transition_cursor,
            )
        except ControlManagerError as error:
            self._manager_error(error)
            return
        self._response_started = True
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        known_public_event_ids: set[str] = set()
        delivered_event_cursor = event_cursor
        projection_event_cursor = -1
        last_output = time.monotonic()
        while True:
            wrote_frame = False
            for transition in pending:
                envelope = RunTransitionEvent(
                    schema_version="aero-bench.run-transition-event/v1",
                    run_id=run_id,
                    transition=transition,
                )
                self._write_sse(
                    event_type="run.transition",
                    event_id=f"transition.{transition.sequence}",
                    payload=envelope.model_dump(mode="json"),
                )
                transition_cursor = transition.sequence
                wrote_frame = True

            try:
                snapshot = self.context.manager.snapshot(
                    run_id,
                    operator_token=operator_token,
                )
            except ControlManagerError:
                return

            if snapshot.phase == "running":
                while True:
                    try:
                        batch = self.context.manager.runtime_projection(
                            run_id,
                            operator_token=operator_token,
                            after_scene_tick=scene_cursor,
                            after_event_sequence=projection_event_cursor,
                        )
                    except ControlManagerError:
                        break
                    projected_ids = known_public_event_ids | {
                        event.event_id for event in batch.events
                    }
                    for state in batch.scene_states:
                        envelope = SceneStateStreamEvent(
                            schema_version="aero-bench.scene-state-stream-event/v1",
                            run_id=run_id,
                            scene_state=state,
                        )
                        self._write_sse(
                            event_type="scene.state",
                            event_id=f"scene.{state.at.tick}",
                            payload=envelope.model_dump(mode="json"),
                        )
                        scene_cursor = state.at.tick
                        wrote_frame = True
                    for event in batch.events:
                        known_public_event_ids.add(event.event_id)
                        if event.sequence <= delivered_event_cursor:
                            continue
                        projected = project_public_run_event(
                            event,
                            public_event_ids=projected_ids,
                        )
                        envelope = PublicRunEventStreamEvent(
                            schema_version=(
                                "aero-bench.public-run-event-stream-event/v1"
                            ),
                            run_id=run_id,
                            event=projected,
                        )
                        self._write_sse(
                            event_type="run.event",
                            event_id=event.event_id,
                            payload=envelope.model_dump(mode="json"),
                        )
                        delivered_event_cursor = event.sequence
                        wrote_frame = True
                    projection_event_cursor = batch.next_event_sequence
                    if not batch.has_more:
                        break

            if snapshot.phase in {"blocked", "completed", "cancelled", "error"}:
                try:
                    trace = self.context.manager.public_trace(
                        run_id,
                        operator_token=operator_token,
                    )
                except ControlManagerError:
                    trace = None
                if trace is not None:
                    for state in trace.scene_states:
                        if state.at.tick <= scene_cursor:
                            continue
                        envelope = SceneStateStreamEvent(
                            schema_version="aero-bench.scene-state-stream-event/v1",
                            run_id=run_id,
                            scene_state=state,
                        )
                        self._write_sse(
                            event_type="scene.state",
                            event_id=f"scene.{state.at.tick}",
                            payload=envelope.model_dump(mode="json"),
                        )
                        scene_cursor = state.at.tick
                    caught_up_event_cursor = max(
                        delivered_event_cursor,
                        projection_event_cursor,
                    )
                    for event in trace.events:
                        if event.sequence <= caught_up_event_cursor:
                            continue
                        envelope = PublicRunEventStreamEvent(
                            schema_version=(
                                "aero-bench.public-run-event-stream-event/v1"
                            ),
                            run_id=run_id,
                            event=event,
                        )
                        self._write_sse(
                            event_type="run.event",
                            event_id=event.event_id,
                            payload=envelope.model_dump(mode="json"),
                        )
                        delivered_event_cursor = event.sequence
                return

            try:
                pending = self.context.manager.wait_for_transitions(
                    run_id,
                    operator_token=operator_token,
                    sequence=transition_cursor,
                    timeout_seconds=1.0,
                )
            except ControlManagerError:
                return
            if wrote_frame:
                last_output = time.monotonic()
            elif time.monotonic() - last_output >= 10.0:
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                last_output = time.monotonic()

    def _write_sse(
        self,
        *,
        event_type: str,
        event_id: str,
        payload: object,
    ) -> None:
        body = canonical_json_bytes(payload)
        frame = (
            f"event: {event_type}\n".encode("ascii")
            + f"id: {event_id}\n".encode("ascii")
            + b"data: "
            + body
            + b"\n\n"
        )
        self.wfile.write(frame)
        self.wfile.flush()

    def _manager_error(self, error: ControlManagerError) -> None:
        if error.code == "authentication.failed":
            status = HTTPStatus.UNAUTHORIZED
        elif error.code in {"csrf.failed"}:
            status = HTTPStatus.FORBIDDEN
        elif error.code in {"catalog.run_unknown", "asset.not_found"}:
            status = HTTPStatus.NOT_FOUND
        elif error.code in {
            "run.capacity",
            "run.already_created",
            "start.id_conflict",
            "control.id_conflict",
            "control.in_progress",
            "control.rejected",
            "run.public_trace_unavailable",
            "replay.read_only",
        }:
            status = HTTPStatus.CONFLICT
        elif error.code == "control.unavailable":
            status = HTTPStatus.SERVICE_UNAVAILABLE
        elif error.code in {"projection.unavailable", "projection.invalid"}:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        else:
            status = HTTPStatus.BAD_REQUEST
        self._error(status, error.code, error.detail)

    def _json(self, status: HTTPStatus, payload: object) -> None:
        body = canonical_json_bytes(payload) + b"\n"
        self._response_started = True
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, code: str, detail: str) -> None:
        self.close_connection = True
        response = ControlApiErrorResponse(
            schema_version="aero-bench.control-error-response/v1",
            error=ControlApiError(code=code, detail=detail),
        )
        self._json(status, response.model_dump(mode="json"))

    def _security_headers(self) -> None:
        origins = self.headers.get_all("Origin", [])
        origin = origins[0] if len(origins) == 1 else None
        if origin in self.context.config.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-site")


class ControlHttpServer:
    def __init__(
        self,
        *,
        manager: ControlRunManager | SealedReplayManager,
        config: ControlHttpConfig,
        bootstrap_token: str,
        bootstrap_csrf_token: str,
    ) -> None:
        if not isinstance(manager, (ControlRunManager, SealedReplayManager)):
            raise TypeError("ControlHttpServer.manager must be a live or sealed replay manager")
        if not isinstance(config, ControlHttpConfig):
            raise TypeError("ControlHttpServer.config must be ControlHttpConfig")
        if not _valid_distinct_credentials(bootstrap_token, bootstrap_csrf_token):
            raise ValueError("control bootstrap credentials are invalid")
        context = _ServerContext(
            manager=manager,
            config=config,
            bootstrap_token=bootstrap_token,
            bootstrap_csrf_token=bootstrap_csrf_token,
            limiter=_RateLimiter(requests_per_minute=config.requests_per_minute),
            request_slots=threading.BoundedSemaphore(config.max_concurrent_requests),
        )
        handler = type(
            "ConfiguredControlRequestHandler",
            (_ControlRequestHandler,),
            {"context": context},
        )
        server_class = ThreadingHTTPServer
        if ipaddress.ip_address(config.bind_host).version == 6:
            server_class = type(
                "IPv6ThreadingControlHttpServer",
                (ThreadingHTTPServer,),
                {"address_family": socket.AF_INET6},
            )
        try:
            self._server = server_class(
                (config.bind_host, config.port),
                handler,
            )
        except OSError as error:
            raise ControlHttpServerError("control HTTP endpoint could not bind") from error
        self._server.daemon_threads = True
        self._manager = manager

    def serve_forever(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.25)
        finally:
            self._server.server_close()
            self._manager.shutdown()

    def shutdown(self) -> None:
        self._server.shutdown()


def _credential_matches(candidate: str, expected: str) -> bool:
    if not isinstance(candidate, str) or _TOKEN_PATTERN.fullmatch(candidate) is None:
        return False
    candidate_digest = hashlib.sha256(candidate.encode("ascii")).digest()
    expected_digest = hashlib.sha256(expected.encode("ascii")).digest()
    return hmac.compare_digest(candidate_digest, expected_digest)


def _valid_distinct_credentials(first: str, second: str) -> bool:
    return (
        isinstance(first, str)
        and isinstance(second, str)
        and _TOKEN_PATTERN.fullmatch(first) is not None
        and _TOKEN_PATTERN.fullmatch(second) is not None
        and first != "0" * 64
        and second != "0" * 64
        and not hmac.compare_digest(first, second)
    )


__all__ = [
    "ControlHttpConfig",
    "ControlHttpServer",
    "ControlHttpServerError",
]
