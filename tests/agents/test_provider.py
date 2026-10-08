"""Fake HTTP server tests for the stdlib OpenAI provider: success, malformed, timeout."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from aeroagentsim.agents.provider import OpenAIProvider, Provider, ProviderError

MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
TOOLS: list[dict[str, Any]] = []


class FakeHandler(BaseHTTPRequestHandler):
    behaviors: ClassVar[dict[str, str]] = {}
    lock = threading.Lock()

    def log_message(self, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        behavior = "/" + self.path.strip("/").split("/")[0]
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if behavior == "/slow":
            self.server.deadline_hit.set()  # type: ignore[attr-defined]
            self.server.serve_block.wait(timeout=10)  # type: ignore[attr-defined]
            body = b'{"choices": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass  # Expected: caller closes a timed-out fake request.
            return
        with self.lock:
            mode = self.server.behaviors.get(behavior, "success")  # type: ignore[attr-defined]
        if mode == "malformed":
            body = b"{not-json"
        elif mode == "missing":
            body = json.dumps(
                {"object": "chat.completion", "usage": {"total_tokens": 3}}
            ).encode()
        elif mode == "status":
            body = b'{"error": "nope"}'
        else:
            body = json.dumps(
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 2,
                        "total_tokens": 3,
                    },
                }
            ).encode()
        self.send_response(200 if mode != "status" else 502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeServer(ThreadingHTTPServer):
    daemon_threads = True
    behaviors: dict[str, str]
    deadline_hit: threading.Event
    serve_block: threading.Event


@pytest.fixture(scope="module")
def server() -> Any:
    httpd = FakeServer(("127.0.0.1", 0), FakeHandler)
    httpd.behaviors = {
        "/bad-json": "malformed",
        "/no-choices": "missing",
        "/bad-status": "status",
    }
    httpd.deadline_hit = threading.Event()
    httpd.serve_block = threading.Event()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.serve_block.set()
    httpd.shutdown()
    httpd.server_close()


def provider(port: int, path: str = "/ok") -> OpenAIProvider:
    return OpenAIProvider(f"http://127.0.0.1:{port}{path}", "glm-5.3-flashx")


def test_success_shape_and_usage(server: Any) -> None:
    result = provider(server.server_address[1]).complete(
        MESSAGES, TOOLS, timeout_s=10.0, max_tokens=64
    )
    assert result["message"]["role"] == "assistant"
    assert result["message"]["content"] == "ok"
    assert result["raw"]["object"] == "chat.completion"
    assert result["usage"]["completion_tokens"] == 2


def test_from_config_defaults_and_env(
    monkeypatch: pytest.MonkeyPatch, server: Any
) -> None:
    monkeypatch.delenv("AAS_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AAS_LLM_MODEL", raising=False)
    monkeypatch.delenv("AAS_LLM_API_KEY", raising=False)
    base = OpenAIProvider.from_config({})
    assert (base.base_url, base.model, base.api_key) == (
        "http://127.0.0.1:8788/v1",
        "glm-5.3-flashx",
        None,
    )
    monkeypatch.setenv(
        "AAS_LLM_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}"
    )
    monkeypatch.setenv("AAS_LLM_MODEL", "env-model")
    monkeypatch.setenv("AAS_LLM_API_KEY", "sk-env")
    env_provider = OpenAIProvider.from_config()
    assert env_provider.model == "env-model" and env_provider.api_key == "sk-env"
    monkeypatch.setenv("TEST_GATEWAY_KEY", "k")
    config_provider = OpenAIProvider.from_config(
        {
            "model": "cfg-model",
            "base_url": "http://example.internal/v1",
            "api_key_env": "TEST_GATEWAY_KEY",
        }
    )
    assert (
        config_provider.model == "cfg-model"
        and config_provider.host == "example.internal"
    )
    assert config_provider.api_key == "k"


def test_malformed_bodies_map_to_protocol(server: Any) -> None:
    port = server.server_address[1]
    with pytest.raises(ProviderError) as json_error:
        provider(port, "/bad-json").complete(
            MESSAGES, TOOLS, timeout_s=10.0, max_tokens=16
        )
    assert json_error.value.code == "PROTOCOL"
    with pytest.raises(ProviderError) as shape_error:
        provider(port, "/no-choices").complete(
            MESSAGES, TOOLS, timeout_s=10.0, max_tokens=16
        )
    assert shape_error.value.code == "PROTOCOL"


def test_http_status_is_distinct(server: Any) -> None:
    with pytest.raises(ProviderError) as status_error:
        provider(server.server_address[1], "/bad-status").complete(
            MESSAGES, TOOLS, timeout_s=10.0, max_tokens=16
        )
    assert status_error.value.code == "HTTP_STATUS"


def test_wall_timeout_on_stalled_server(server: Any) -> None:
    server.deadline_hit.clear()
    server.serve_block.clear()
    port = server.server_address[1]
    started = time.monotonic()
    with pytest.raises(ProviderError) as timeout_error:
        provider(port, "/slow").complete(MESSAGES, TOOLS, timeout_s=0.3, max_tokens=16)
    assert timeout_error.value.code == "WALL_TIMEOUT"
    assert time.monotonic() - started < 1.0
    assert server.deadline_hit.is_set()
    server.serve_block.set()


def test_transport_error_on_refused_connection() -> None:
    with socket_closed_port() as port, pytest.raises(ProviderError) as transport_error:
        provider(port).complete(MESSAGES, TOOLS, timeout_s=5.0, max_tokens=16)
    assert transport_error.value.code == "TRANSPORT"


@contextmanager
def socket_closed_port() -> Any:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        yield sock.getsockname()[1]


def test_provider_protocol_and_deadline_bounds_request() -> None:
    assert isinstance(OpenAIProvider.from_config(), Provider)
    with pytest.raises(ProviderError) as tokens_error:
        OpenAIProvider("http://127.0.0.1:9/v1", "m").complete(
            MESSAGES, TOOLS, timeout_s=5.0, max_tokens=0
        )
    assert tokens_error.value.code == "PROTOCOL"


def test_no_env_leakage_between_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAS_LLM_API_KEY", "sk-leak")
    assert OpenAIProvider.from_config().api_key == "sk-leak"
    monkeypatch.delenv("AAS_LLM_API_KEY")
    assert OpenAIProvider.from_config().api_key is None
    assert not [
        name
        for name in os.environ
        if name.startswith("AAS_LLM_")
        and name == "AAS_LLM_API_KEY"
        and os.environ[name] == "sk-leak"
    ]
