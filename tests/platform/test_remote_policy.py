"""Listener policy is explicit; API token and Host/Origin boundaries still apply."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from aeroagentsim.services import cli
from aeroagentsim.services.app import create_app
from aeroagentsim.services.lifecycle import WorkerLimits
from aeroagentsim.services.security import validate_serving_host


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "127.0.0.1", "127.1.2.3", "::1"])
def test_loopback_serving_still_needs_no_token(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    assert validate_serving_host(host) is None


@pytest.mark.parametrize("host", [
    "0.0.0.0", "::", "192.0.2.10", "2001:db8::1", "sim.example",
    "localhost.evil.invalid", "127.0.0.1.evil.invalid", "127.1", "",
])
@pytest.mark.parametrize("token", [None, "", "   "])
def test_non_loopback_requires_real_token_or_explicit_override(
    host: str, token: str | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if token is None:
        monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AEROAGENTSIM_API_TOKEN", token)
    with pytest.raises(ValueError, match="requires AEROAGENTSIM_API_TOKEN"):
        validate_serving_host(host)
    assert "NO AUTHENTICATION" in (validate_serving_host(host, unsafe_local_lab=True) or "")


@pytest.mark.parametrize("token", [None, "test-api-token"])
def test_local_api_unauthenticated_and_authenticated_behaviour_is_preserved(
    token: str | None, tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_CONSOLE_URL", raising=False)
    if token is None:
        monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AEROAGENTSIM_API_TOKEN", token)
    document["run"]["until_ns"] = 0
    with TestClient(create_app(tmp_path / "runs"), base_url="http://localhost") as client:
        listing = client.get("/v1/runs")
        assert listing.status_code == (200 if token is None else 401)
        if token is not None:
            denied = client.post("/v1/runs", json=document)
            assert denied.status_code == 401 and denied.headers["www-authenticate"] == "Bearer"
            assert client.get("/v1/runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
            client.headers["Authorization"] = "Bearer " + token
        assert client.get("/v1/runs").json() == []
        created = client.post("/v1/runs", json=document)
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        assert client.get(f"/v1/runs/{run_id}/header").status_code == 200
        assert client.post(f"/v1/runs/{run_id}/stop", json={}, headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert client.post(f"/v1/runs/{run_id}/stop", content="{}").status_code == 415


def test_remote_authenticated_api_keeps_origin_json_and_preflight_checks(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AEROAGENTSIM_API_TOKEN", "remote-test-token")
    monkeypatch.setenv("AEROAGENTSIM_CONSOLE_URL", "https://sim.example")
    assert validate_serving_host("0.0.0.0") is None
    document["run"]["until_ns"] = 0
    with TestClient(create_app(tmp_path / "runs"), base_url="https://sim.example") as client:
        assert client.get("/v1/runs").status_code == 401
        assert client.post("/v1/runs", json=document).status_code == 401
        preflight = client.options("/v1/runs", headers={
            "Origin": "https://sim.example", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, Authorization",
        })
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://sim.example"
        client.headers["Authorization"] = "Bearer remote-test-token"
        assert client.post("/v1/runs", json=document, headers={"Host": "evil.invalid"}).status_code == 403
        assert client.post("/v1/runs", json=document, headers={"Origin": "https://evil.invalid"}).status_code == 403
        response = client.post("/v1/runs", json=document, headers={"Origin": "https://sim.example"})
        assert response.status_code == 201, response.text
        assert client.get(f"/v1/runs/{response.json()['id']}/header").status_code == 200


def test_serve_rejects_remote_before_storage_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    calls: list[Any] = []
    monkeypatch.setattr("aeroagentsim.services.app.create_app", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    root = tmp_path / "not-created"
    monkeypatch.setattr(sys, "argv", ["aeroagentsim", "serve", "--host", "0.0.0.0", "--out", str(root)])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 2
    assert "--unsafe-local-lab" in capsys.readouterr().err
    assert calls == [] and not root.exists()


@pytest.mark.parametrize("unsafe,token", [(False, "test-api-token"), (True, None), (True, "test-api-token")])
def test_cli_allows_authenticated_remote_or_warned_unsafe_override(
    unsafe: bool, token: str | None, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli.os, "environ", cli.os.environ.copy())
    monkeypatch.delenv("AEROAGENTSIM_CONSOLE_URL", raising=False)
    if token is None:
        monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AEROAGENTSIM_API_TOKEN", token)
    application = object()
    created: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def create(*args: Any, **kwargs: Any) -> object:
        created.append(kwargs)
        return application

    monkeypatch.setattr("aeroagentsim.services.app.create_app", create)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    argv = ["aeroagentsim", "serve", "--host", "192.0.2.10", "--out", str(tmp_path),
            "--startup-timeout", "7", "--input-timeout", "3", "--shutdown-grace", "4",
            "--terminate-grace", "1", "--kill-grace", "2", "--cleanup-timeout", "0.5"]
    monkeypatch.setattr(sys, "argv", argv + (["--unsafe-local-lab"] if unsafe else []))
    cli.main()
    assert calls == [{"host": "192.0.2.10", "port": 8002, "timeout_graceful_shutdown": 5}]
    assert created[0]["worker_limits"] == WorkerLimits(7, 3, 4, 1, 2, 0.5)
    assert cli.os.environ["AEROAGENTSIM_CONSOLE_URL"] == "http://192.0.2.10:8002"
    warning = capsys.readouterr().err
    assert ("WARNING / 警告" in warning) is unsafe
    if unsafe:
        assert "--unsafe-local-lab" in warning and "NO AUTHENTICATION" in warning
    assert "test-api-token" not in warning


def test_unsafe_remote_lab_has_no_auth_but_still_checks_origin(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    monkeypatch.setenv("AEROAGENTSIM_CONSOLE_URL", "http://lab.example:8002")
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    application = create_app(tmp_path / "runs")
    cli.serve_app(application, host="0.0.0.0", port=8002, unsafe_local_lab=True)
    assert calls and "WARNING / 警告" in capsys.readouterr().err
    document["run"]["until_ns"] = 0
    with TestClient(application, base_url="http://lab.example:8002") as client:
        assert client.get("/v1/runs").status_code == 200
        assert client.post("/v1/runs", json=document, headers={"Origin": "http://evil.invalid"}).status_code == 403
        response = client.post("/v1/runs", json=document, headers={"Origin": "http://lab.example:8002"})
        assert response.status_code == 201, response.text


def test_programmatic_serve_helper_cannot_bypass_remote_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match="Non-loopback serve"):
        cli.serve_app(object(), host="::", port=8002)
    assert calls == []


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_invalid_operator_limits_fail_before_application_creation(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr("aeroagentsim.services.app.create_app", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["aeroagentsim", "serve", "--out", str(tmp_path), "--startup-timeout", value])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 2 and calls == []


def test_ipv6_loopback_cli_origin_is_bracketed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.os, "environ", cli.os.environ.copy())
    monkeypatch.delenv("AEROAGENTSIM_CONSOLE_URL", raising=False)
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    monkeypatch.setattr("aeroagentsim.services.app.create_app", lambda *args, **kwargs: object())
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["aeroagentsim", "serve", "--host", "::1", "--out", str(tmp_path)])
    cli.main()
    assert cli.os.environ["AEROAGENTSIM_CONSOLE_URL"] == "http://[::1]:8002"
