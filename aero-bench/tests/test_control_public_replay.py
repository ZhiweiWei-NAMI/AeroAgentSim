"""Focused checks for the sealed public trace / replay-manifest HTTP routes.

These routes let the browser replay a finished run: they must serve the exact
sealed bytes recorded in the run summary (never a live or reconstructed
projection), only once the run has reached a terminal phase, and must never be
able to reach anything outside the two fixed public paths.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aero_bench.control.manager import ControlManagerError, ControlRunManager
from aero_bench.runner.contracts import (
    PreflightIdentity,
    PublicReplayIdentity,
    PublicTraceIdentity,
    RunSummary,
    SealIdentity,
)
from aero_bench.trace.contracts import PublicReplayManifest, PublicReplayFile
from aero_bench.serialization import canonical_json_bytes

RUN_ID = "d" * 64
EVENT_CHAIN_ROOT = "2" * 64
SCENARIO_DIGEST = "3" * 64


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _seal() -> SealIdentity:
    return SealIdentity(
        schema_version="aero-bench.seal/v2",
        run_id=RUN_ID,
        attempt_id="attempt-1",
        execution_scope="formal_benchmark",
        manifest_digest="4" * 64,
        event_chain_root=EVENT_CHAIN_ROOT,
        artifacts=(),
    )


def _summary(*, trace_sha256: str, manifest_sha256: str) -> RunSummary:
    return RunSummary(
        run_id=RUN_ID,
        executor_kind="docker_reference",
        execution_scope="formal_benchmark",
        preflight=PreflightIdentity(ready=True, blocker_codes=()),
        status="passed",
        seal=_seal(),
        verification=None,
        public_trace=PublicTraceIdentity(
            schema_version="aero-bench.public-trace/v3",
            projector_version="aero-bench.public-projector/v2",
            relative_path="public/public-trace.json",
            sha256=trace_sha256,
            event_chain_root=EVENT_CHAIN_ROOT,
            replay=PublicReplayIdentity(
                schema_version="aero-bench.public-replay-manifest/v1",
                relative_path="public/replay/replay-manifest.json",
                sha256=manifest_sha256,
                file_count=1,
                total_size_bytes=1,
            ),
        ),
        failure_classes=(),
    )


def _manifest_bytes(trace_sha256: str, trace_size: int) -> bytes:
    manifest = PublicReplayManifest(
        schema_version="aero-bench.public-replay-manifest/v1",
        run_id=RUN_ID,
        scenario_digest=SCENARIO_DIGEST,
        event_chain_root=EVENT_CHAIN_ROOT,
        trace_sha256=trace_sha256,
        files=(
            PublicReplayFile(
                relative_path="public-trace.json",
                sha256=trace_sha256,
                size_bytes=trace_size,
            ),
        ),
    )
    return canonical_json_bytes(manifest.model_dump(mode="json", exclude_none=True)) + b"\n"


def _build_manager(tmp_path: Path, *, phase: str = "completed", known_run: bool = True):
    trace_content = b'{"ok":true}'
    trace_sha256 = hashlib.sha256(trace_content).hexdigest()
    manifest_content = _manifest_bytes(trace_sha256, len(trace_content))
    manifest_sha256 = hashlib.sha256(manifest_content).hexdigest()

    run_root = tmp_path / RUN_ID
    _write(run_root / "public" / "public-trace.json", trace_content)
    _write(run_root / "public" / "replay" / "replay-manifest.json", manifest_content)

    summary = _summary(trace_sha256=trace_sha256, manifest_sha256=manifest_sha256)
    session = SimpleNamespace(phase=phase, summary=summary if phase == "completed" else None)
    managed = SimpleNamespace(session=session, operator_token="a" * 64)

    instance = object.__new__(ControlRunManager)
    instance._output_root = tmp_path
    instance._runs = {RUN_ID: SimpleNamespace()} if known_run else {}
    instance._managed = {RUN_ID: managed}
    instance._lock = threading.RLock()
    return instance, trace_content, manifest_content


def test_public_trace_document_serves_exact_bytes_for_terminal_run(tmp_path: Path) -> None:
    instance, trace_content, _ = _build_manager(tmp_path)
    data = instance.public_trace_document(RUN_ID, operator_token="a" * 64)
    assert data == trace_content
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(trace_content).hexdigest()


def test_public_replay_manifest_document_serves_exact_bytes_for_terminal_run(tmp_path: Path) -> None:
    instance, _, manifest_content = _build_manager(tmp_path)
    data = instance.public_replay_manifest_document(RUN_ID, operator_token="a" * 64)
    assert data == manifest_content


def test_public_trace_document_rejects_non_terminal_run(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path, phase="running")
    with pytest.raises(ControlManagerError, match="run has not produced a sealed public trace"):
        instance.public_trace_document(RUN_ID, operator_token="a" * 64)


def test_public_replay_manifest_document_rejects_non_terminal_run(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path, phase="running")
    with pytest.raises(
        ControlManagerError, match="run has not produced a sealed public replay manifest"
    ):
        instance.public_replay_manifest_document(RUN_ID, operator_token="a" * 64)


def test_public_trace_document_rejects_blocked_run_without_trace(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path, phase="blocked")
    with pytest.raises(ControlManagerError, match="run has not produced a sealed public trace"):
        instance.public_trace_document(RUN_ID, operator_token="a" * 64)


def test_public_trace_document_requires_run_authentication(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path)
    with pytest.raises(ControlManagerError, match="authentication failed"):
        instance.public_trace_document(RUN_ID, operator_token="b" * 64)


def test_public_replay_manifest_document_requires_run_authentication(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path)
    with pytest.raises(ControlManagerError, match="authentication failed"):
        instance.public_replay_manifest_document(RUN_ID, operator_token="b" * 64)


def test_public_trace_document_unknown_run_is_explicit(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path, known_run=False)
    with pytest.raises(ControlManagerError, match="not present in the configured catalog") as excinfo:
        instance.public_trace_document(RUN_ID, operator_token="a" * 64)
    assert excinfo.value.code == "catalog.run_unknown"


def test_public_replay_manifest_document_unknown_run_is_explicit(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path, known_run=False)
    with pytest.raises(ControlManagerError, match="not present in the configured catalog") as excinfo:
        instance.public_replay_manifest_document(RUN_ID, operator_token="a" * 64)
    assert excinfo.value.code == "catalog.run_unknown"


def test_public_trace_document_detects_tampered_bytes(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path)
    (tmp_path / RUN_ID / "public" / "public-trace.json").write_bytes(b'{"ok":false}')
    with pytest.raises(ControlManagerError, match="digest is invalid"):
        instance.public_trace_document(RUN_ID, operator_token="a" * 64)


def test_public_replay_manifest_document_detects_tampered_bytes(tmp_path: Path) -> None:
    instance, _, _ = _build_manager(tmp_path)
    (tmp_path / RUN_ID / "public" / "replay" / "replay-manifest.json").write_bytes(b"{}")
    with pytest.raises(ControlManagerError, match="digest is invalid"):
        instance.public_replay_manifest_document(RUN_ID, operator_token="a" * 64)


def test_public_trace_identity_relative_path_cannot_escape_the_public_tree() -> None:
    with pytest.raises(ValidationError):
        PublicTraceIdentity(
            schema_version="aero-bench.public-trace/v3",
            projector_version="aero-bench.public-projector/v2",
            relative_path="../private/run-summary.json",
            sha256="1" * 64,
            event_chain_root=EVENT_CHAIN_ROOT,
            replay=PublicReplayIdentity(
                schema_version="aero-bench.public-replay-manifest/v1",
                relative_path="public/replay/replay-manifest.json",
                sha256="5" * 64,
                file_count=1,
                total_size_bytes=1,
            ),
        )


def test_public_replay_identity_relative_path_cannot_escape_the_public_tree() -> None:
    with pytest.raises(ValidationError):
        PublicReplayIdentity(
            schema_version="aero-bench.public-replay-manifest/v1",
            relative_path="../private/seal.json",
            sha256="5" * 64,
            file_count=1,
            total_size_bytes=1,
        )


def test_public_trace_and_replay_manifest_http_routes(tmp_path: Path) -> None:
    import http.client
    import socket
    from aero_bench.control.server import ControlHttpConfig, ControlHttpServer

    instance, trace_content, manifest_content = _build_manager(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = "http://127.0.0.1:4177"
    server = ControlHttpServer(
        manager=instance,
        config=ControlHttpConfig(
            schema_version="aero-bench.control-http-config/v1",
            bind_host="127.0.0.1",
            port=port,
            allowed_hosts=(f"127.0.0.1:{port}",),
            allowed_origins=(origin,),
        ),
        bootstrap_token="e" * 64,
        bootstrap_csrf_token="f" * 64,
    )
    thread = threading.Thread(target=server._server.serve_forever, daemon=True)
    thread.start()
    try:
        cases = (
            (f"/v1/runs/{RUN_ID}/public/trace", "b" * 64, 401, None),
            (f"/v1/runs/{RUN_ID}/public/trace", "a" * 64, 200, trace_content),
            (f"/v1/runs/{RUN_ID}/public/replay-manifest", "b" * 64, 401, None),
            (f"/v1/runs/{RUN_ID}/public/replay-manifest", "a" * 64, 200, manifest_content),
            (f"/v1/runs/{'f' * 64}/public/trace", "a" * 64, 404, None),
            (f"/v1/runs/{'f' * 64}/public/replay-manifest", "a" * 64, 404, None),
        )
        for path, token, expected_status, expected_body in cases:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request(
                "GET",
                path,
                headers={"Origin": origin, "Authorization": f"Bearer {token}"},
            )
            response = connection.getresponse()
            body = response.read()
            assert response.status == expected_status, (path, token, body)
            assert response.getheader("Cache-Control") == "no-store"
            if expected_body is not None:
                assert body == expected_body
                assert response.getheader("Content-Type") == "application/json"
                assert response.getheader("Access-Control-Allow-Origin") == origin
            connection.close()
    finally:
        server._server.shutdown()
        server._server.server_close()
        thread.join(timeout=5)


def test_public_trace_http_route_rejects_non_terminal_run_with_409(tmp_path: Path) -> None:
    import http.client
    import socket
    from aero_bench.control.server import ControlHttpConfig, ControlHttpServer

    instance, _, _ = _build_manager(tmp_path, phase="running")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = "http://127.0.0.1:4178"
    server = ControlHttpServer(
        manager=instance,
        config=ControlHttpConfig(
            schema_version="aero-bench.control-http-config/v1",
            bind_host="127.0.0.1",
            port=port,
            allowed_hosts=(f"127.0.0.1:{port}",),
            allowed_origins=(origin,),
        ),
        bootstrap_token="e" * 64,
        bootstrap_csrf_token="f" * 64,
    )
    thread = threading.Thread(target=server._server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "GET",
            f"/v1/runs/{RUN_ID}/public/trace",
            headers={"Origin": origin, "Authorization": "Bearer " + "a" * 64},
        )
        response = connection.getresponse()
        body = response.read()
        assert response.status == 409
        import json

        assert json.loads(body)["error"]["code"] == "run.public_trace_unavailable"
        connection.close()
    finally:
        server._server.shutdown()
        server._server.server_close()
        thread.join(timeout=5)
