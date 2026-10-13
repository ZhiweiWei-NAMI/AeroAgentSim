"""Connection masking, persistence and operator profile registration, without I/O."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from aeroagentsim.agents.provider import OpenAIProvider, ProviderError
from aeroagentsim.assistant.connection import ConnectionStore


class Provider:
    model = "scripted"

    def __init__(self, *args: Any) -> None:
        self.args = args
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], tools: list[Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"message": {"role": "assistant", "content": "OK"}, "usage": None}


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AEROAGENTSIM_ASSISTANT_BASE_URL", "AEROAGENTSIM_ASSISTANT_MODEL", "AEROAGENTSIM_ASSISTANT_API_KEY_ENV", "AEROAGENTSIM_ASSISTANT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AEROAGENTSIM_PROVIDER_PROFILES", '{"other":{"base_url":"http://localhost:8000/v1","model":"other"}}')


def test_key_is_separate_masked_and_profile_is_resolvable(tmp_path: Path, environment: None) -> None:
    connection = ConnectionStore(tmp_path, provider_factory=Provider)
    connection.set("http://localhost:9876/v1/", "small", "secret-key")
    public = connection.test()
    assert public["source"] == "user" and public["has_key"]
    assert public["status"] == "ok" and public["tested_at"]
    assert "secret-key" not in json.dumps(public)
    assert "secret-key" not in connection.path.read_text()
    assert stat.S_IMODE(connection.key_path.stat().st_mode) == 0o600
    resolved = OpenAIProvider.from_config({"profile": "assistant"})
    assert resolved.api_key == "secret-key" and resolved.model == "small"
    restarted = ConnectionStore(tmp_path, provider_factory=Provider)
    assert restarted.get() == public
    assert restarted.provider().args == ("http://localhost:9876/v1", "small", "secret-key")
    restarted.set("http://localhost/v1", "new", clear_key=True)
    assert not restarted.get()["has_key"] and not restarted.key_path.exists()
    assert OpenAIProvider.from_config({"profile": "assistant"}).api_key is None
    assert OpenAIProvider.from_config({"profile": "other"}).model == "other"


def test_operator_key_selector_and_tiny_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environment: None) -> None:
    monkeypatch.setenv("AEROAGENTSIM_ASSISTANT_BASE_URL", "http://localhost:9876/v1")
    monkeypatch.setenv("AEROAGENTSIM_ASSISTANT_MODEL", "operator-model")
    monkeypatch.setenv("AEROAGENTSIM_ASSISTANT_API_KEY_ENV", "TEST_OPERATOR_KEY")
    monkeypatch.setenv("TEST_OPERATOR_KEY", "operator-secret")
    provider = Provider()
    connection = ConnectionStore(tmp_path, provider_factory=lambda *args: provider)
    assert connection.get()["source"] == "operator"
    assert OpenAIProvider.from_config({"profile": "assistant"}).api_key == "operator-secret"
    assert connection.test()["status"] == "ok"
    assert provider.calls == [{"timeout_s": 20, "max_tokens": 8}]


@pytest.mark.parametrize("url", ["ftp://localhost", "http://user:password@localhost/v1", "http://localhost/v1?key=secret", "http://localhost/v1#fragment", "relative"])
def test_same_url_boundary_as_provider(tmp_path: Path, environment: None, url: str) -> None:
    connection = ConnectionStore(tmp_path, provider_factory=Provider)
    with pytest.raises(ValueError):
        connection.set(url, "small")
    assert not connection.get()["configured"]


def test_provider_error_does_not_echo_upstream_text(tmp_path: Path, environment: None) -> None:
    class Refusing(Provider):
        def complete(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise ProviderError("HTTP_STATUS", "upstream returned HTTP 401 SECRET_BODY")

    connection = ConnectionStore(tmp_path, provider_factory=Refusing)
    connection.set("http://localhost/v1", "small", "SECRET_KEY")
    result = connection.test()
    assert result["status"] == "error" and "401" in result["detail"]
    assert "SECRET_BODY" not in json.dumps(result) and "SECRET_KEY" not in json.dumps(result)
