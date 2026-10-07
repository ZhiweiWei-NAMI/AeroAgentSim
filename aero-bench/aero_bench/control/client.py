from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from typing import NoReturn, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import BaseModel, ValidationError

from aero_bench.control.contracts import (
    CONTROL_MUTATIONS,
    ControlApiErrorResponse,
    ControlCatalog,
    ControlStreamEvent,
    PublicRunEventStreamEvent,
    RunStatusResponse,
    RunTransitionEvent,
    RuntimeControlRequest,
    RuntimeControlResponse,
    SceneStateStreamEvent,
    StartRunRequest,
    StartRunResponse,
)
from aero_bench.providers.rpc import parse_json_object
from aero_bench.serialization import canonical_json_bytes


_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ControlApiClientError(RuntimeError):
    def __init__(self, code: str, detail: str, *, http_status: int | None = None):
        self.code = code
        self.detail = detail
        self.http_status = http_status
        super().__init__(detail)


class ControlApiClient:
    """Strict client for the local run-control API."""

    def __init__(
        self,
        *,
        base_url: str,
        origin: str,
        bearer_token: str,
        csrf_token: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.hostname is None
            or parsed.port is None
        ):
            raise ValueError("control base URL must be an explicit local HTTP origin")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if parsed.hostname != "localhost":
                raise ValueError("control base URL must use a loopback host") from None
        else:
            if not address.is_loopback:
                raise ValueError("control base URL must use a loopback host")
        parsed_origin = urlsplit(origin)
        if (
            parsed_origin.scheme not in {"http", "https"}
            or not parsed_origin.netloc
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.path
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise ValueError("control client origin is invalid")
        if timeout_seconds <= 0:
            raise ValueError("control client timeout must be positive")
        if not _valid_token(bearer_token):
            raise ValueError("control client Bearer token is invalid")
        if csrf_token is not None and (
            not _valid_token(csrf_token) or csrf_token == bearer_token
        ):
            raise ValueError("control client CSRF token is invalid")
        self._base_url = base_url.rstrip("/")
        self._origin = origin
        self._bearer_token = bearer_token
        self._csrf_token = csrf_token
        self._timeout_seconds = timeout_seconds
        # The validated endpoint is loopback. Do not send local credentials to
        # an ambient HTTP proxy or depend on a caller's NO_PROXY settings.
        self._opener = build_opener(ProxyHandler({}))

    def catalog(self) -> ControlCatalog:
        payload = self._request("GET", "/v1/catalog")
        return self._validate(ControlCatalog, payload, "control catalog")

    def start(self, *, run_id: str, start_id: str) -> StartRunResponse:
        request = StartRunRequest(
            schema_version="aero-bench.start-run-request/v1",
            start_id=start_id,
            run_id=run_id,
        )
        payload = self._request(
            "POST",
            "/v1/runs",
            body=request.model_dump(mode="json"),
            require_csrf=True,
        )
        return self._validate(StartRunResponse, payload, "start response")

    def status(self, run_id: str) -> RunStatusResponse:
        self._validate_run_id(run_id)
        payload = self._request("GET", f"/v1/runs/{run_id}")
        return self._validate(RunStatusResponse, payload, "run status response")

    def control(
        self,
        run_id: str,
        *,
        operation: str,
        control_id: str,
    ) -> RuntimeControlResponse:
        self._validate_run_id(run_id)
        if operation not in CONTROL_MUTATIONS:
            raise ValueError("control operation is unsupported")
        request = RuntimeControlRequest(
            schema_version="aero-bench.runtime-control-request/v1",
            control_id=control_id,
        )
        payload = self._request(
            "POST",
            f"/v1/runs/{run_id}/controls/{operation}",
            body=request.model_dump(mode="json"),
            require_csrf=True,
        )
        response = self._validate(
            RuntimeControlResponse,
            payload,
            "runtime control response",
        )
        if (
            response.receipt.operation != operation
            or response.receipt.control_id != control_id
            or response.snapshot.run_id != run_id
        ):
            raise ControlApiClientError(
                "response.identity",
                "runtime control response identity is invalid",
            )
        return response

    def events(
        self,
        run_id: str,
        *,
        after_transition: int = -1,
        after_scene_tick: int = 0,
        after_event_sequence: int = -1,
    ) -> Iterator[ControlStreamEvent]:
        self._validate_run_id(run_id)
        for label, value, minimum in (
            ("after_transition", after_transition, -1),
            ("after_scene_tick", after_scene_tick, 0),
            ("after_event_sequence", after_event_sequence, -1),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
            ):
                raise ValueError(f"{label} cursor is invalid")
        query = urlencode(
            {
                "after_transition": str(after_transition),
                "after_scene_tick": str(after_scene_tick),
                "after_event_sequence": str(after_event_sequence),
            }
        )
        request = self._build_request(
            "GET",
            f"/v1/runs/{run_id}/events?{query}",
            body=None,
            require_csrf=False,
        )
        try:
            response = self._opener.open(request, timeout=self._timeout_seconds)
        except HTTPError as error:
            self._raise_http_error(error)
        except URLError as error:
            raise ControlApiClientError(
                "transport.failed",
                "control API transport failed",
            ) from error
        with response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("text/event-stream"):
                raise ControlApiClientError(
                    "response.invalid",
                    "control event stream has an invalid media type",
                )
            event_type: str | None = None
            event_id: str | None = None
            data: bytes | None = None
            while True:
                line = response.readline(16_777_217)
                if not line:
                    return
                if len(line) > 16_777_216:
                    raise ControlApiClientError(
                        "response.too_large",
                        "control event stream line exceeds its bound",
                    )
                if line in {b"\n", b"\r\n"}:
                    if event_type is None and data is None:
                        continue
                    if event_type is None or event_id is None or data is None:
                        raise ControlApiClientError(
                            "response.invalid",
                            "control event stream frame is invalid",
                        )
                    try:
                        payload = parse_json_object(data)
                        if data != canonical_json_bytes(payload):
                            raise ValueError("event data is not canonical JSON")
                        if event_type == "run.transition":
                            event: ControlStreamEvent = (
                                RunTransitionEvent.model_validate(payload)
                            )
                            expected_id = (
                                f"transition.{event.transition.sequence}"
                            )
                        elif event_type == "scene.state":
                            event = SceneStateStreamEvent.model_validate(payload)
                            expected_id = f"scene.{event.scene_state.at.tick}"
                        elif event_type == "run.event":
                            event = PublicRunEventStreamEvent.model_validate(payload)
                            expected_id = event.event.event_id
                        else:
                            raise ValueError("unsupported stream event type")
                    except Exception as error:
                        raise ControlApiClientError(
                            "response.invalid",
                            "control stream event is invalid",
                        ) from error
                    if event_id != expected_id or event.run_id != run_id:
                        raise ControlApiClientError(
                            "response.identity",
                            "control stream event identity is invalid",
                        )
                    yield event
                    event_type = None
                    event_id = None
                    data = None
                    continue
                line = line.rstrip(b"\r\n")
                if line.startswith(b":"):
                    continue
                if line.startswith(b"event: ") and event_type is None:
                    try:
                        event_type = line[7:].decode("ascii")
                    except UnicodeDecodeError as error:
                        raise ControlApiClientError(
                            "response.invalid",
                            "control event type is invalid",
                        ) from error
                elif line.startswith(b"id: ") and event_id is None:
                    try:
                        event_id = line[4:].decode("ascii")
                    except UnicodeDecodeError as error:
                        raise ControlApiClientError(
                            "response.invalid",
                            "control event ID is invalid",
                        ) from error
                    if (
                        not event_id
                        or len(event_id) > 96
                        or any(
                            character not in "abcdefghijklmnopqrstuvwxyz0123456789.-"
                            for character in event_id
                        )
                    ):
                        raise ControlApiClientError(
                            "response.invalid",
                            "control event ID is invalid",
                        )
                elif line.startswith(b"data: ") and data is None:
                    data = line[6:]
                else:
                    raise ControlApiClientError(
                        "response.invalid",
                        "control event stream field is invalid",
                    )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        require_csrf: bool = False,
    ) -> dict[str, object]:
        request = self._build_request(
            method,
            path,
            body=body,
            require_csrf=require_csrf,
        )
        try:
            response = self._opener.open(request, timeout=self._timeout_seconds)
        except HTTPError as error:
            self._raise_http_error(error)
        except URLError as error:
            raise ControlApiClientError(
                "transport.failed",
                "control API transport failed",
            ) from error
        with response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("application/json"):
                raise ControlApiClientError(
                    "response.invalid",
                    "control API response has an invalid media type",
                )
            payload = response.read(1_048_577)
            if len(payload) > 1_048_576:
                raise ControlApiClientError(
                    "response.too_large",
                    "control API response exceeds its bound",
                )
            try:
                return parse_json_object(payload)
            except Exception as error:
                raise ControlApiClientError(
                    "response.invalid",
                    "control API response is invalid JSON",
                ) from error

    def _build_request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None,
        require_csrf: bool,
    ) -> Request:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._bearer_token}",
            "Origin": self._origin,
            "User-Agent": "aero-bench-control-cli/1",
        }
        payload = None
        if body is not None:
            payload = canonical_json_bytes(body)
            headers["Content-Type"] = "application/json"
        if require_csrf:
            if self._csrf_token is None:
                raise ValueError("control mutation requires a CSRF token")
            headers["X-Aero-Bench-CSRF"] = self._csrf_token
        return Request(
            self._base_url + path,
            data=payload,
            headers=headers,
            method=method,
        )

    @staticmethod
    def _validate(
        model: type[_ModelT],
        payload: object,
        label: str,
    ) -> _ModelT:
        try:
            return model.model_validate(payload)
        except (AttributeError, ValidationError, ValueError) as error:
            raise ControlApiClientError(
                "response.invalid",
                f"{label} is invalid",
            ) from error

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if (
            not isinstance(run_id, str)
            or len(run_id) != 64
            or any(character not in "0123456789abcdef" for character in run_id)
        ):
            raise ValueError("run_id is invalid")

    @staticmethod
    def _raise_http_error(error: HTTPError) -> NoReturn:
        payload = error.read(1_048_577)
        try:
            parsed = parse_json_object(payload)
            response = ControlApiErrorResponse.model_validate(parsed)
        except Exception as parse_error:
            raise ControlApiClientError(
                "response.http_error",
                "control API returned an invalid error response",
                http_status=error.code,
            ) from parse_error
        raise ControlApiClientError(
            response.error.code,
            response.error.detail,
            http_status=error.code,
        )


def _valid_token(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value != "0" * 64
        and all(character in "0123456789abcdef" for character in value)
    )


__all__ = ["ControlApiClient", "ControlApiClientError"]
