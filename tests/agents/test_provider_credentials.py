"""Credential validation and worker failure containment without network I/O."""

from __future__ import annotations

import http.client
import json
import threading
import traceback
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from aeroagentsim.agents import provider as provider_module
from aeroagentsim.agents.provider import OpenAIProvider, ProviderError

SECRET = "dummy-provider-secret-containment"
KEY_ERROR = "API key must be nonempty, control-free and latin-1 encodable"
SOURCES = ("direct", "default-env", "live-env", "profile-env", "file")
INVALID_KEYS = [
    "",
    "   ",
    *(SECRET + chr(code) + "tail" for code in range(32)),
    *(SECRET + chr(code) + "tail" for code in range(127, 160)),
    SECRET + "\r\ntail",
    SECRET + "\u0100",
]


@pytest.fixture(autouse=True)
def quiet_workers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> Iterator[None]:
    workers: list[threading.Thread] = []
    failures: list[Any] = []

    def make_thread(*args: Any, **kwargs: Any) -> threading.Thread:
        worker = threading.Thread(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(
        provider_module,
        "threading",
        SimpleNamespace(Thread=make_thread, Event=threading.Event),
    )
    monkeypatch.setattr(threading, "excepthook", failures.append)
    yield
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert not failures
    captured = capsys.readouterr()
    assert captured.err == ""
    assert SECRET not in captured.out
    assert SECRET not in caplog.text


def make_provider(
    source: str,
    api_key: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> OpenAIProvider:
    environment = {
        "AAS_LLM_BASE_URL": "http://example.invalid/v1",
        "AAS_LLM_MODEL": "test-model",
    }
    config: dict[str, Any] = {}
    if source == "direct":
        return OpenAIProvider("http://example.invalid/v1", "test-model", api_key)
    if source == "default-env":
        if api_key is not None:
            environment["AAS_LLM_API_KEY"] = api_key
    elif source == "live-env":
        environment.update(
            AEROAGENTSIM_LLM_BASE_URL="http://example.invalid/v1",
            AEROAGENTSIM_LLM_MODEL="test-model",
            AEROAGENTSIM_LLM_API_KEY_ENV="TEST_PROVIDER_KEY",
        )
        if api_key is not None:
            environment["TEST_PROVIDER_KEY"] = api_key
        config = {"profile": "live-llm"}
    else:
        entry = {"base_url": "http://example.invalid/v1", "model": "test-model"}
        if source == "file":
            key_file = tmp_path / "provider-key.txt"
            assert api_key is not None
            key_file.write_text(api_key, encoding="utf-8")
            entry["api_key_file"] = str(key_file)
        else:
            assert source == "profile-env"
            entry["api_key_env"] = "TEST_PROVIDER_KEY"
            if api_key is not None:
                environment["TEST_PROVIDER_KEY"] = api_key
        environment["AEROAGENTSIM_PROVIDER_PROFILES"] = json.dumps({"test": entry})
        config = {"profile": "test"}
    # A local map also permits NUL/surrogates without OS environment rejection.
    monkeypatch.setattr(provider_module, "os", SimpleNamespace(environ=environment))
    return OpenAIProvider.from_config(config)


def assert_safe_error(error: Exception, message: str) -> None:
    assert str(error) == message
    assert SECRET not in repr(error)
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert SECRET not in rendered


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(
    "api_key", INVALID_KEYS, ids=[f"invalid-{index}" for index in range(len(INVALID_KEYS))]
)
def test_invalid_credentials_fail_before_transport(
    source: str,
    api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def forbidden_transport(*args: Any, **kwargs: Any) -> None:
        pytest.fail("invalid credentials must not construct a transport")

    monkeypatch.setattr(provider_module.http.client, "HTTPConnection", forbidden_transport)
    monkeypatch.setattr(provider_module.http.client, "HTTPSConnection", forbidden_transport)
    with pytest.raises(ValueError) as caught:
        make_provider(source, api_key, monkeypatch, tmp_path)
    assert_safe_error(caught.value, KEY_ERROR)


@pytest.mark.parametrize("source", SOURCES[:-1])
def test_surrogate_credentials_have_fixed_errors(
    source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValueError) as caught:
        make_provider(source, SECRET + "\udcff", monkeypatch, tmp_path)
    assert_safe_error(caught.value, KEY_ERROR)


@pytest.mark.parametrize(
    "api_key", [SECRET.encode(), 123, False], ids=["bytes", "integer", "boolean"]
)
def test_nonstring_direct_credentials_have_fixed_errors(api_key: Any) -> None:
    with pytest.raises(ValueError) as caught:
        OpenAIProvider("http://example.invalid/v1", "test-model", api_key)
    assert_safe_error(caught.value, KEY_ERROR)


@pytest.mark.parametrize("source", SOURCES[:-1])
def test_absent_credentials_still_allow_unauthenticated_endpoints(
    source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert make_provider(source, None, monkeypatch, tmp_path).api_key is None


@pytest.mark.parametrize("source", SOURCES)
def test_latin1_credentials_are_preserved(
    source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api_key = SECRET + "\u00e9\u00ff"
    assert make_provider(source, api_key, monkeypatch, tmp_path).api_key == api_key


@pytest.mark.parametrize("source", SOURCES[:-1])
def test_environment_and_direct_credentials_are_not_trimmed(
    source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api_key = " " + SECRET + " "
    assert make_provider(source, api_key, monkeypatch, tmp_path).api_key == api_key


def test_file_keeps_existing_surrounding_whitespace_normalization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resolved = make_provider("file", " \t" + SECRET + "\r\n", monkeypatch, tmp_path)
    assert resolved.api_key == SECRET


class StubConnection:
    """Single exchange recorder; no sockets or servers."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.connects = 0
        self.closed = False
        self.sock = None

    def connect(self) -> None:
        self.connects += 1

    def request(
        self, method: str, path: str, body: bytes, headers: dict[str, str]
    ) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> Any:
        body = b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'
        return SimpleNamespace(status=200, read=lambda limit: body[:limit])

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    "failure",
    [
        ValueError(SECRET),
        TypeError(SECRET),
        OverflowError(SECRET),
        UnicodeEncodeError("latin-1", SECRET + "\u0100", 0, 1, SECRET),
        http.client.InvalidURL(SECRET),
        http.client.HTTPException(SECRET),
        http.client.RemoteDisconnected(SECRET),
        OSError(SECRET),
        TimeoutError(SECRET),
    ],
)
def test_worker_request_failures_are_typed_without_retry(
    failure: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = StubConnection()

    def fail_request(
        method: str, path: str, body: bytes, headers: dict[str, str]
    ) -> None:
        connection.requests.append((method, path, body, headers))
        raise failure

    monkeypatch.setattr(connection, "request", fail_request)
    monkeypatch.setattr(
        provider_module.http.client, "HTTPConnection", lambda *a, **kw: connection
    )
    provider = OpenAIProvider("http://example.invalid/v1", "test-model", SECRET)
    with pytest.raises(ProviderError) as caught:
        provider.complete([], [], timeout_s=1.0, max_tokens=16)
    if isinstance(failure, TimeoutError):
        code, message = "WALL_TIMEOUT", "model wall deadline expired"
    elif isinstance(failure, http.client.HTTPException):
        code, message = "PROTOCOL", "connection to model endpoint failed"
    elif isinstance(failure, OSError):
        code, message = "TRANSPORT", "connection to model endpoint failed"
    else:
        code, message = "PROTOCOL", "model request construction failed"
    assert caught.value.code == code
    assert_safe_error(caught.value, f"{code}: {message}")
    assert connection.connects == 1
    assert len(connection.requests) == 1


@pytest.mark.parametrize(
    "bad_header", [SECRET + "\r\ninjected", SECRET + "\u0100"], ids=["crlf", "unicode"]
)
def test_stdlib_header_validation_is_contained(
    bad_header: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class HeaderConnection(http.client.HTTPConnection):
        def connect(self) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> None:
            # Exercise real stdlib header checking/encoding without sending bytes.
            self.putrequest("POST", "/v1/chat/completions")
            self.putheader("x-test", bad_header)

    connection = HeaderConnection("example.invalid")
    monkeypatch.setattr(
        provider_module.http.client, "HTTPConnection", lambda *a, **kw: connection
    )
    provider = OpenAIProvider("http://example.invalid/v1", "test-model", SECRET)
    with pytest.raises(ProviderError) as caught:
        provider.complete([], [], timeout_s=1.0, max_tokens=16)
    assert caught.value.code == "PROTOCOL"
    assert_safe_error(caught.value, "PROTOCOL: model request construction failed")


@pytest.mark.parametrize("host", ["opencode.ai", "example.invalid"])
@pytest.mark.parametrize("api_key", [None, SECRET], ids=["unauthenticated", "authenticated"])
def test_transport_policy_is_credential_free_and_preserves_wire_behavior(
    host: str, api_key: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = StubConnection()
    monkeypatch.setattr(
        provider_module.http.client, "HTTPSConnection", lambda *a, **kw: connection
    )
    provider = OpenAIProvider(f"https://{host}/v1", "test-model", api_key)
    tools = [{"type": "function", "function": {"name": "test", "strict": True}}]
    original = deepcopy(tools)
    result = provider.complete([], tools, timeout_s=1.0, max_tokens=16)
    assert result["message"]["content"] == "ok"
    assert tools == original
    sent = json.loads(connection.requests[0][2])
    if host == "opencode.ai":
        assert provider.transport_transformations == (
            "opencode.ai:strip-function.strict:v1",
        )
        assert "strict" not in sent["tools"][0]["function"]
    else:
        assert provider.transport_transformations == ()
        assert sent["tools"] == original
    assert SECRET not in json.dumps(provider.transport_transformations)
    if api_key is None:
        assert "Authorization" not in connection.requests[0][3]
    else:
        assert connection.requests[0][3]["Authorization"] == "Bearer " + api_key
    assert len(connection.requests) == 1
