"""Synthetic delivery fixtures; these tests are not formal execution evidence."""
from __future__ import annotations

import hashlib
import http.client
from pathlib import Path
import socket
import threading
from types import SimpleNamespace

import pytest

import aero_bench.control.manager as manager_module
from aero_bench.control.manager import ControlManagerError, ControlRunManager
from aero_bench.control.server import ControlHttpConfig, ControlHttpServer
from aero_bench.runner.contracts import (
    PreflightIdentity,
    PublicReplayIdentity,
    PublicTraceIdentity,
    RunSummary,
    SealIdentity,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.contracts import (
    PublicReplayFile,
    PublicReplayIndex,
    PublicReplayManifest,
    PublicReplayShard,
)

RUN_ID = "d" * 64
SCENARIO_DIGEST = "3" * 64
EVENT_CHAIN_ROOT = "2" * 64
OPERATOR_TOKEN = "a" * 64


def _payload(document: object) -> bytes:
    return canonical_json_bytes(document) + b"\n"


def _indexed_manager(tmp_path: Path):
    replay_root = tmp_path / RUN_ID / "public" / "replay"
    (replay_root / "artifacts").mkdir(parents=True)
    payloads: dict[str, bytes] = {}

    def content_file(content: bytes) -> PublicReplayFile:
        digest = hashlib.sha256(content).hexdigest()
        path = f"artifacts/{digest}"
        (replay_root / path).write_bytes(content)
        payloads[digest] = content
        return PublicReplayFile(relative_path=path, sha256=digest, size_bytes=len(content))

    # Two index ranges exercise files that are not runtime-seal requirements.
    first_bytes = b"".join(_payload({"test_fixture_tick": tick}) for tick in range(1, 257))
    last_bytes = _payload({"test_fixture_tick": 257})
    first = content_file(first_bytes)
    last = content_file(last_bytes)
    history = content_file(first_bytes + last_bytes)
    index = PublicReplayIndex(
        schema_version="aero-bench.public-replay-index/v1",
        run_id=RUN_ID,
        scenario_digest=SCENARIO_DIGEST,
        event_chain_root=EVENT_CHAIN_ROOT,
        first_tick=1,
        last_tick=257,
        scene_state_count=257,
        shards=(
            PublicReplayShard(**first.model_dump(), first_tick=1, last_tick=256, scene_state_count=256),
            PublicReplayShard(**last.model_dump(), first_tick=257, last_tick=257, scene_state_count=1),
        ),
    )
    index_file = content_file(_payload(index.model_dump(mode="json")))
    trace_bytes = _payload({"delivery_test_fixture": True})
    trace_digest = hashlib.sha256(trace_bytes).hexdigest()
    (replay_root.parent / "public-trace.json").write_bytes(trace_bytes)
    trace_file = PublicReplayFile(
        relative_path="public-trace.json", sha256=trace_digest, size_bytes=len(trace_bytes)
    )
    files = tuple(sorted((first, last, history, index_file, trace_file), key=lambda item: item.relative_path))
    manifest = PublicReplayManifest(
        schema_version="aero-bench.public-replay-manifest/v1",
        run_id=RUN_ID,
        scenario_digest=SCENARIO_DIGEST,
        event_chain_root=EVENT_CHAIN_ROOT,
        trace_sha256=trace_digest,
        files=files,
        replay_mode="indexed",
        replay_index=index_file,
        scene_state_history=history,
    )
    manifest_bytes = _payload(manifest.model_dump(mode="json", exclude_none=True))
    (replay_root / "replay-manifest.json").write_bytes(manifest_bytes)
    summary = RunSummary(
        run_id=RUN_ID,
        executor_kind="docker_reference",
        execution_scope="formal_benchmark",
        preflight=PreflightIdentity(ready=True, blocker_codes=()),
        status="passed",
        seal=SealIdentity(
            schema_version="aero-bench.seal/v2",
            run_id=RUN_ID,
            attempt_id="delivery-test-fixture",
            execution_scope="formal_benchmark",
            manifest_digest="4" * 64,
            event_chain_root=EVENT_CHAIN_ROOT,
            artifacts=(),
        ),
        verification=None,
        public_trace=PublicTraceIdentity(
            schema_version="aero-bench.public-trace/v3",
            projector_version="aero-bench.public-projector/v2",
            relative_path="public/public-trace.json",
            sha256=trace_digest,
            event_chain_root=EVENT_CHAIN_ROOT,
            replay=PublicReplayIdentity(
                schema_version="aero-bench.public-replay-manifest/v1",
                relative_path="public/replay/replay-manifest.json",
                sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                file_count=len(files),
                total_size_bytes=sum(item.size_bytes for item in files),
            ),
        ),
        failure_classes=(),
    )
    instance = object.__new__(ControlRunManager)
    instance._output_root = tmp_path
    instance._bundle_root = tmp_path / "bundle"
    instance._runs = {RUN_ID: SimpleNamespace(
        artifact_requirements=(), scenario=SimpleNamespace(assets=()),
    )}
    instance._managed = {RUN_ID: SimpleNamespace(
        operator_token=OPERATOR_TOKEN,
        session=SimpleNamespace(phase="completed", summary=summary),
    )}
    instance._lock = threading.RLock()
    return instance, manifest, payloads, replay_root


def test_index_history_and_every_shard_are_delivered_from_manifest(tmp_path: Path) -> None:
    instance, _, payloads, _ = _indexed_manager(tmp_path)
    for digest, expected in payloads.items():
        assert instance.public_asset(RUN_ID, digest, operator_token=OPERATOR_TOKEN) == (
            expected, "application/octet-stream",
        )


def test_generated_replay_asset_requires_authentication_and_terminal_publication(tmp_path: Path) -> None:
    instance, manifest, _, _ = _indexed_manager(tmp_path)
    digest = manifest.replay_index.sha256
    with pytest.raises(ControlManagerError, match="authentication failed"):
        instance.public_asset(RUN_ID, digest, operator_token="b" * 64)
    instance._managed[RUN_ID].session.phase = "running"
    with pytest.raises(ControlManagerError) as error:
        instance.public_asset(RUN_ID, digest, operator_token=OPERATOR_TOKEN)
    assert error.value.code == "asset.not_found"


def test_terminal_source_asset_keeps_its_declared_media_type(tmp_path: Path) -> None:
    instance, manifest, payloads, _ = _indexed_manager(tmp_path)
    # A public source may share content with a published replay file. Its
    # original declared media type must remain stable after terminal replay.
    digest = manifest.replay_index.sha256
    instance._bundle_root.mkdir()
    (instance._bundle_root / "source.json").write_bytes(payloads[digest])
    instance._runs[RUN_ID].scenario.assets = (SimpleNamespace(
        classification="public", byte_size=len(payloads[digest]),
        file=SimpleNamespace(path="source.json", sha256=digest),
        world=SimpleNamespace(media_type="application/json"),
    ),)
    assert instance.public_asset(RUN_ID, digest, operator_token=OPERATOR_TOKEN) == (
        payloads[digest], "application/json",
    )


def test_unlisted_file_cannot_be_delivered_even_from_public_replay_directory(tmp_path: Path) -> None:
    instance, _, _, root = _indexed_manager(tmp_path)
    private_bytes = b"not in the public manifest"
    digest = hashlib.sha256(private_bytes).hexdigest()
    (root / "artifacts" / digest).write_bytes(private_bytes)
    with pytest.raises(ControlManagerError) as error:
        instance.public_asset(RUN_ID, digest, operator_token=OPERATOR_TOKEN)
    assert error.value.code == "asset.not_found"


@pytest.mark.parametrize("tamper", ["bytes", "size", "outside_symlink"])
def test_generated_replay_asset_integrity_and_root_are_checked(tmp_path: Path, tamper: str) -> None:
    instance, manifest, payloads, root = _indexed_manager(tmp_path)
    digest = manifest.replay_index.sha256
    path = root / "artifacts" / digest
    if tamper == "bytes":
        data = payloads[digest]
        path.write_bytes(b"!" + data[1:])
    elif tamper == "size":
        path.write_bytes(payloads[digest] + b" ")
    else:
        outside = tmp_path / "private-index.json"
        outside.write_bytes(payloads[digest])
        path.unlink()
        path.symlink_to(outside)
    with pytest.raises(ControlManagerError) as error:
        instance.public_asset(RUN_ID, digest, operator_token=OPERATOR_TOKEN)
    assert error.value.code == "asset.invalid"


def test_tampered_manifest_cannot_authorize_generated_files(tmp_path: Path) -> None:
    instance, manifest, _, root = _indexed_manager(tmp_path)
    (root / "replay-manifest.json").write_bytes(b"{}\n")
    with pytest.raises(ControlManagerError) as error:
        instance.public_asset(RUN_ID, manifest.replay_index.sha256, operator_token=OPERATOR_TOKEN)
    assert error.value.code == "projection.invalid"


def test_public_asset_bound_accepts_old_64_mib_overflow_without_weakening_digest_check(tmp_path: Path) -> None:
    data = b"x" * (64 * 1024 * 1024 + 1)
    path = tmp_path / "large-public-history.jsonl"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    assert ControlRunManager._read_public_asset_bytes(
        root=tmp_path, relative_path=path.name,
        expected_sha256=digest, expected_size_bytes=len(data),
    ) == data


def test_public_asset_limit_matches_viewer_and_rejects_oversize_before_opening(tmp_path: Path, monkeypatch) -> None:
    assert manager_module._MAX_PUBLIC_ASSET_BYTES == 512 * 1024 * 1024
    monkeypatch.setattr(manager_module.os, "open", lambda *args: pytest.fail("oversize asset must not be opened"))
    with pytest.raises(ControlManagerError) as error:
        ControlRunManager._read_public_asset_bytes(
            root=tmp_path, relative_path="nonexistent",
            expected_sha256="f" * 64,
            expected_size_bytes=manager_module._MAX_PUBLIC_ASSET_BYTES + 1,
        )
    assert error.value.code == "asset.too_large"


def test_sealed_document_routes_use_the_viewers_distinct_byte_bounds(tmp_path: Path, monkeypatch) -> None:
    instance, _, _, _ = _indexed_manager(tmp_path)
    original = instance._read_sealed_public_bytes
    observed = []

    def read(run_id, **kwargs):
        observed.append(kwargs["max_size_bytes"])
        return original(run_id, **kwargs)

    monkeypatch.setattr(instance, "_read_sealed_public_bytes", read)
    instance.public_trace_document(RUN_ID, operator_token=OPERATOR_TOKEN)
    instance.public_replay_manifest_document(RUN_ID, operator_token=OPERATOR_TOKEN)
    assert observed == [1024 * 1024 * 1024, 8 * 1024 * 1024]


@pytest.mark.parametrize("kind", ["trace", "manifest"])
def test_sealed_document_route_rejects_its_oversize_file_without_reading(tmp_path: Path, kind: str) -> None:
    instance, _, _, root = _indexed_manager(tmp_path)
    if kind == "trace":
        source = root.parent / "public-trace.json"
        limit = manager_module._MAX_PUBLIC_TRACE_BYTES
        request = instance.public_trace_document
    else:
        source = root / "replay-manifest.json"
        limit = manager_module._MAX_PUBLIC_REPLAY_MANIFEST_BYTES
        request = instance.public_replay_manifest_document
    with source.open("wb") as stream:
        stream.truncate(limit + 1)
    with pytest.raises(ControlManagerError) as error:
        request(RUN_ID, operator_token=OPERATOR_TOKEN)
    assert error.value.code == "projection.unavailable"


def test_indexed_replay_http_route_delivers_exact_authenticated_bytes(tmp_path: Path) -> None:
    instance, _, payloads, _ = _indexed_manager(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = "http://127.0.0.1:4179"
    server = ControlHttpServer(
        manager=instance,
        config=ControlHttpConfig(
            schema_version="aero-bench.control-http-config/v1",
            bind_host="127.0.0.1", port=port,
            allowed_hosts=(f"127.0.0.1:{port}",), allowed_origins=(origin,),
        ),
        bootstrap_token="e" * 64, bootstrap_csrf_token="f" * 64,
    )
    thread = threading.Thread(target=server._server.serve_forever, daemon=True)
    thread.start()
    try:
        for digest, expected in payloads.items():
            for token, status in (("b" * 64, 401), (OPERATOR_TOKEN, 200)):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request(
                    "GET", f"/v1/runs/{RUN_ID}/assets/{digest}",
                    headers={"Origin": origin, "Authorization": f"Bearer {token}"},
                )
                response = connection.getresponse()
                body = response.read()
                assert response.status == status
                assert response.getheader("Cache-Control") == "no-store"
                if status == 200:
                    assert body == expected
                    assert response.getheader("Access-Control-Allow-Origin") == origin
                connection.close()
    finally:
        server._server.shutdown()
        server._server.server_close()
        thread.join(timeout=5)
