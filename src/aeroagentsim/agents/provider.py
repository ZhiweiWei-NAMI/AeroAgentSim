"""Bounded stdlib OpenAI-compatible provider with no stateful automatic retry."""

from __future__ import annotations

import http.client
import json
import math
import os
import queue
import socket
import threading
import time
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from .tools import strict_json


class ProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@runtime_checkable
class Provider(Protocol):
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]: ...


class OpenAIProvider:
    """A daemon I/O worker isolates DNS, headers and slow-body deadline overruns.

    The calling engine receives a bounded typed timeout. The socket is shut down
    on expiry; a late reply cannot mutate simulation state or become a success.
    """

    MAX_RESPONSE_BYTES = 4 * 1024 * 1024
    DEFAULT_BASE_URL = "http://127.0.0.1:8788/v1"
    DEFAULT_MODEL = "glm-5.3-flashx"

    def __init__(self, base_url: str, model: str, api_key: str | None = None) -> None:
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.query
            or parts.fragment
            or parts.username
        ):
            raise ValueError(
                "base_url must be an absolute HTTP(S) endpoint without credentials/query/fragment"
            )
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be nonempty")
        self.base_url, self.model, self.api_key = base_url.rstrip("/"), model, api_key
        self.host, self.scheme = parts.hostname, parts.scheme
        self.port = parts.port
        self.path = parts.path.rstrip("/") + "/chat/completions"

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> OpenAIProvider:
        cfg = {} if config is None else config
        if set(cfg) - {"base_url", "model", "api_key_env"}:
            raise ValueError("provider config accepts base_url/model/api_key_env")
        base_url = cfg.get(
            "base_url", os.environ.get("AAS_LLM_BASE_URL", cls.DEFAULT_BASE_URL)
        )
        model = cfg.get("model", os.environ.get("AAS_LLM_MODEL", cls.DEFAULT_MODEL))
        key_env = cfg.get("api_key_env", "AAS_LLM_API_KEY")
        if not all(
            isinstance(value, str) and value for value in (base_url, model, key_env)
        ):
            raise ValueError("explicit provider settings must be nonempty strings")
        return cls(base_url, model, os.environ.get(key_env))

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_s: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        if (
            type(timeout_s) not in (int, float)
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise ProviderError("WALL_TIMEOUT", "positive finite wall timeout required")
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ProviderError("PROTOCOL", "positive max_tokens required")
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "stream": False,
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        factory = (
            http.client.HTTPSConnection
            if self.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = factory(self.host, self.port, timeout=timeout_s)
        deadline = time.monotonic() + timeout_s
        results: queue.Queue[dict[str, Any] | ProviderError] = queue.Queue(maxsize=1)
        expired = threading.Event()

        def exchange() -> None:
            try:
                connection.connect()
                if expired.is_set():
                    return
                headers = {"Content-Type": "application/json"}
                if self.api_key is not None:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                connection.request("POST", self.path, payload, headers)
                response = connection.getresponse()
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
                if len(body) > self.MAX_RESPONSE_BYTES:
                    raise ProviderError("PROTOCOL", "response exceeds byte budget")
                if not 200 <= response.status < 300:
                    raise ProviderError(
                        "HTTP_STATUS", f"HTTP {response.status}: {body[:200]!r}"
                    )
                try:
                    raw = strict_json(body.decode())
                    message = raw["choices"][0]["message"]
                    if (
                        not isinstance(message, dict)
                        or message.get("role") != "assistant"
                    ):
                        raise TypeError("expected assistant message")
                except (
                    ValueError,
                    UnicodeError,
                    KeyError,
                    IndexError,
                    TypeError,
                ) as exc:
                    raise ProviderError("PROTOCOL", str(exc)) from exc
                results.put({"message": message, "raw": raw, "usage": raw.get("usage")})
            except ProviderError as exc:
                results.put(exc)
            except TimeoutError:
                results.put(
                    ProviderError("WALL_TIMEOUT", "model wall deadline expired")
                )
            except http.client.HTTPException as exc:
                results.put(ProviderError("PROTOCOL", str(exc)))
            except OSError as exc:
                results.put(ProviderError("TRANSPORT", str(exc)))
            finally:
                connection.close()

        worker = threading.Thread(
            target=exchange, daemon=True, name="aas-model-request"
        )
        worker.start()
        try:
            result = results.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty as exc:
            expired.set()
            transport = connection.sock
            if transport is not None:
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # Socket may already be closed by the worker; timeout remains authoritative.
            raise ProviderError("WALL_TIMEOUT", "model wall deadline expired") from exc
        if isinstance(result, ProviderError):
            raise result
        return result
