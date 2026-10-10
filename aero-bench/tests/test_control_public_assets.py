"""Focused authorization and integrity checks for live public scene delivery."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.control.manager import ControlManagerError, ControlRunManager


def manager(tmp_path: Path, payload: bytes = b'{"version":0.6}', classification: str = "public"):
    path = tmp_path / "scene.json"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    asset = SimpleNamespace(classification=classification, file=SimpleNamespace(path="scene.json", sha256=digest), byte_size=len(payload), world=SimpleNamespace(media_type="application/json"))
    instance = object.__new__(ControlRunManager)
    instance._bundle_root = tmp_path
    instance._runs = {"run": SimpleNamespace(scenario=SimpleNamespace(assets=(asset,)))}
    import threading
    instance._lock = threading.RLock()
    instance._managed = {"run": SimpleNamespace(operator_token="a" * 64)}
    return instance, digest, path


def test_public_asset_requires_run_authentication(tmp_path: Path) -> None:
    instance, digest, _ = manager(tmp_path)
    with pytest.raises(ControlManagerError, match="authentication failed"):
        instance.public_asset("run", digest, operator_token="b" * 64)


def test_private_assets_are_never_delivered(tmp_path: Path) -> None:
    instance, digest, _ = manager(tmp_path, classification="private")
    with pytest.raises(ControlManagerError, match="public asset not found"):
        instance.public_asset("run", digest, operator_token="a" * 64)


def test_public_asset_checks_bytes_and_digest(tmp_path: Path) -> None:
    instance, digest, path = manager(tmp_path)
    assert instance.public_asset("run", digest, operator_token="a" * 64) == (path.read_bytes(), "application/json")
    path.write_bytes(b'{"version":0.7}')
    with pytest.raises(ControlManagerError, match="integrity validation failed"):
        instance.public_asset("run", digest, operator_token="a" * 64)


def test_public_asset_cannot_escape_bundle(tmp_path: Path) -> None:
    instance, digest, path = manager(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.write_bytes(path.read_bytes())
    try:
        path.unlink()
        path.symlink_to(outside)
        with pytest.raises(ControlManagerError, match="integrity validation failed"):
            instance.public_asset("run", digest, operator_token="a" * 64)
    finally:
        outside.unlink()


def test_public_asset_http_route_enforces_auth_and_returns_exact_bytes(tmp_path: Path) -> None:
    import http.client
    import socket
    import threading
    from aero_bench.control.server import ControlHttpConfig, ControlHttpServer

    instance, digest, path = manager(tmp_path)
    run_id = "d" * 64
    instance._runs[run_id] = instance._runs.pop("run")
    instance._managed[run_id] = instance._managed.pop("run")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = "http://127.0.0.1:4176"
    server = ControlHttpServer(
        manager=instance,
        config=ControlHttpConfig(schema_version="aero-bench.control-http-config/v1", bind_host="127.0.0.1", port=port, allowed_hosts=(f"127.0.0.1:{port}",), allowed_origins=(origin,)),
        bootstrap_token="e" * 64,
        bootstrap_csrf_token="f" * 64,
    )
    thread = threading.Thread(target=server._server.serve_forever, daemon=True)
    thread.start()
    try:
        for token, expected in (("b" * 64, 401), ("a" * 64, 200)):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", f"/v1/runs/{run_id}/assets/{digest}", headers={"Origin": origin, "Authorization": f"Bearer {token}"})
            response = connection.getresponse()
            body = response.read()
            assert response.status == expected
            assert response.getheader("Cache-Control") == "no-store"
            if expected == 200:
                assert body == path.read_bytes()
                assert response.getheader("Content-Type") == "application/json"
                assert response.getheader("Access-Control-Allow-Origin") == origin
            connection.close()
    finally:
        server._server.shutdown()
        server._server.server_close()
        thread.join(timeout=5)
