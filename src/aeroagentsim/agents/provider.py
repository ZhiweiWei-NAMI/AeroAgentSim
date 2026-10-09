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
from pathlib import Path
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
        """Resolve a provider from a profile name only; scenario data is untrusted.

        Scenarios may submit ``{"profile": <name>}`` and nothing else. URLs,
        model names and credential env selectors are operator-side trusted
        inputs: the ``default`` profile resolves AAS_LLM_BASE_URL,
        AAS_LLM_MODEL and AAS_LLM_API_KEY; the shipped ``live-llm`` profile
        resolves AEROAGENTSIM_LLM_BASE_URL, AEROAGENTSIM_LLM_MODEL and
        AEROAGENTSIM_LLM_API_KEY_ENV. Any other named profile is looked up in
        the operator JSON map AEROAGENTSIM_PROVIDER_PROFILES, optionally
        seeded from the AEROAGENTSIM_PROVIDER_CONFIG file (env wins).
        """
        cfg = {} if config is None else config
        if not isinstance(cfg, dict) or set(cfg) - {"profile"}:
            raise ValueError("provider config accepts profile only")
        profile = cfg.get("profile", "default")
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError("provider profile must be a nonempty string")
        if profile == "default":
            return cls(
                os.environ.get("AAS_LLM_BASE_URL", cls.DEFAULT_BASE_URL),
                os.environ.get("AAS_LLM_MODEL", cls.DEFAULT_MODEL),
                os.environ.get("AAS_LLM_API_KEY"),
            )
        if profile == "live-llm":
            names = ("AEROAGENTSIM_LLM_BASE_URL", "AEROAGENTSIM_LLM_MODEL")
            values = [os.environ.get(name) for name in names]
            if not all(values):
                missing = ", ".join(
                    name for name, value in zip(names, values) if not value
                )
                raise ValueError(f"live-llm provider profile requires: {missing}")
            key_env = os.environ.get("AEROAGENTSIM_LLM_API_KEY_ENV")
            return cls(
                values[0],  # type: ignore[arg-type]
                values[1],  # type: ignore[arg-type]
                os.environ.get(key_env) if key_env else None,
            )
        base_url, model, key_env = cls._operator_profile(profile)
        return cls(base_url, model, os.environ.get(key_env))

    @classmethod
    def _operator_profile(cls, profile: str) -> tuple[str, str, str]:
        sources: list[str] = []
        path = os.environ.get("AEROAGENTSIM_PROVIDER_CONFIG")
        if path is not None:
            try:
                sources.append(Path(path).read_text())
            except OSError as exc:
                raise ValueError("provider profile config file unreadable") from exc
        raw = os.environ.get("AEROAGENTSIM_PROVIDER_PROFILES")
        if raw is not None:
            sources.append(raw)
        profiles: dict[str, Any] = {}
        for source in sources:
            try:
                parsed = strict_json(source)
            except ValueError as exc:
                raise ValueError(
                    "operator provider profiles must be valid JSON"
                ) from exc
            if not isinstance(parsed, dict) or not all(
                isinstance(name, str) and isinstance(entry, dict)
                for name, entry in parsed.items()
            ):
                raise ValueError(
                    "operator provider profiles must map profile names to objects"
                )
            profiles.update(parsed)
        entry = profiles.get(profile)
        if entry is None:
            raise ValueError(f"unknown provider profile: {profile}")
        if set(entry) - {"base_url", "model", "api_key_env"} or not all(
            isinstance(value, str) and value
            for value in (entry.get("base_url"), entry.get("model"))
        ):
            raise ValueError(
                f"operator profile {profile!r} requires base_url and model strings"
            )
        key_env = entry.get("api_key_env", "AAS_LLM_API_KEY")
        if not isinstance(key_env, str) or not key_env:
            raise ValueError(
                f"operator profile {profile!r} api_key_env must be a nonempty string"
            )
        return entry["base_url"], entry["model"], key_env

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
                    # Never surface upstream body text: error bodies can echo
                    # credentials or other secrets from the gateway.
                    raise ProviderError(
                        "HTTP_STATUS", f"upstream returned HTTP {response.status}"
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
                ):
                    # Exception strings can echo untrusted payload content;
                    # only the typed code is reported.
                    raise ProviderError(
                        "PROTOCOL", "response is not a well-formed completion"
                    ) from None
                results.put({"message": message, "raw": raw, "usage": raw.get("usage")})
            except ProviderError as exc:
                results.put(exc)
            except TimeoutError:
                results.put(
                    ProviderError("WALL_TIMEOUT", "model wall deadline expired")
                )
            except http.client.HTTPException:
                # Protocol exception strings may include request URLs/headers
                # (credentials); report a fixed typed code.
                results.put(
                    ProviderError("PROTOCOL", "connection to model endpoint failed")
                )
            except OSError:
                # Same reason as above: never propagate the raw exception text.
                results.put(
                    ProviderError("TRANSPORT", "connection to model endpoint failed")
                )
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
