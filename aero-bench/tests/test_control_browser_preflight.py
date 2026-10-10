"""Browser CORS preflight coverage for authenticated Control reads."""

from __future__ import annotations

import http.client
import json
import socket
import threading

import pytest

from aero_bench.control.manager import ControlRunManager
from aero_bench.control.server import ControlHttpConfig, ControlHttpServer


RUN_ID = "d" * 64
ASSET_DIGEST = "a" * 64
ORIGIN = "http://127.0.0.1:5392"


@pytest.fixture
def control_server():
    manager = object.__new__(ControlRunManager)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = ControlHttpServer(
        manager=manager,
        config=ControlHttpConfig(
            schema_version="aero-bench.control-http-config/v1",
            bind_host="127.0.0.1",
            port=port,
            allowed_hosts=(f"127.0.0.1:{port}",),
            allowed_origins=(ORIGIN,),
        ),
        bootstrap_token="e" * 64,
        bootstrap_csrf_token="f" * 64,
    )
    thread = threading.Thread(target=server._server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server._server.shutdown()
        server._server.server_close()
        thread.join(timeout=5)


def _options(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(
        "OPTIONS",
        path,
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    response = connection.getresponse()
    body = response.read()
    headers = {name: value for name, value in response.getheaders()}
    connection.close()
    return response.status, headers, body


def test_event_stream_preflight_accepts_only_canonical_cursor_query(control_server: int) -> None:
    canonical = (
        f"/v1/runs/{RUN_ID}/events?after_transition=-1"
        "&after_scene_tick=0&after_event_sequence=-1"
    )
    status, headers, body = _options(control_server, canonical)
    assert status == 204
    assert body == b""
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    assert headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
    assert "Authorization" in headers["Access-Control-Allow-Headers"]

    status, _, body = _options(
        control_server,
        f"/v1/runs/{RUN_ID}/events?after_transition=-1&after_scene_tick=0",
    )
    assert status == 400
    assert json.loads(body)["error"]["code"] == "request.invalid"


@pytest.mark.parametrize(
    "path",
    (
        f"/v1/runs/{RUN_ID}",
        f"/v1/runs/{RUN_ID}/assets/{ASSET_DIGEST}",
        f"/v1/runs/{RUN_ID}/public/trace",
        f"/v1/runs/{RUN_ID}/public/replay-manifest",
    ),
)
def test_authenticated_control_read_preflights_are_declared(control_server: int, path: str) -> None:
    status, headers, body = _options(control_server, path)
    assert status == 204
    assert body == b""
    assert headers["Access-Control-Allow-Origin"] == ORIGIN


def test_non_event_query_is_not_silently_accepted(control_server: int) -> None:
    status, _, body = _options(control_server, f"/v1/runs/{RUN_ID}/public/trace?live=true")
    assert status == 404
    assert json.loads(body)["error"]["code"] == "route.not_found"
