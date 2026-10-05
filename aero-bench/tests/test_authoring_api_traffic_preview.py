from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import threading
import time

from aero_bench.authoring.api import make_server
from aero_bench.authoring.traffic_preview_contracts import (
    TrafficPreviewJob,
    TrafficPreviewProfileCatalog,
)
from aero_bench.authoring.traffic_preview_jobs import TrafficPreviewJobManager
from aero_bench.serialization import canonical_json_bytes
from tests.test_traffic_preview_jobs import passing_runner, registry, request


def _exchange(
    connection: http.client.HTTPConnection,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    raw = response.read()
    return response.status, dict(response.getheaders()), raw


def test_unconfigured_traffic_preview_routes_report_named_absence() -> None:
    with make_server("127.0.0.1", 0, None) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            status, _, raw = _exchange(
                connection, "GET", "/authoring/v1/traffic-preview-profiles",
            )
            assert status == 503
            assert json.loads(raw)["error"]["code"] == "traffic_preview_unconfigured"
            status, _, raw = _exchange(
                connection, "POST", "/authoring/v1/traffic-previews",
                canonical_json_bytes({}), {"Content-Type": "application/json"},
            )
            assert status == 503
            assert json.loads(raw)["error"]["code"] == "traffic_preview_unconfigured"
            connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_http_catalog_job_poll_and_content_addressed_trace(tmp_path: Path) -> None:
    profiles = registry(tmp_path / "repo")
    preview_request = request(profiles, vehicles=3, pedestrians=1, bicycles=1)
    request_bytes = canonical_json_bytes(preview_request.model_dump(mode="json"))
    with TrafficPreviewJobManager(
        tmp_path / "jobs", profiles, runner=passing_runner,
    ) as manager, make_server(
        "127.0.0.1", 0, None, traffic_preview_manager=manager,
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            status, _, raw = _exchange(
                connection, "GET", "/authoring/v1/traffic-preview-profiles",
            )
            assert status == 200
            catalog = TrafficPreviewProfileCatalog.model_validate(json.loads(raw))
            assert catalog == profiles.catalog()

            status, _, raw = _exchange(
                connection, "POST", "/authoring/v1/traffic-previews", request_bytes,
                {"Content-Type": "application/json"},
            )
            assert status == 202
            submitted = TrafficPreviewJob.model_validate(json.loads(raw))
            result = submitted
            for _ in range(200):
                status, _, raw = _exchange(
                    connection, "GET",
                    f"/authoring/v1/traffic-previews/{submitted.job_id}",
                )
                assert status == 200
                result = TrafficPreviewJob.model_validate(json.loads(raw))
                if result.state in {"ready", "failed"}:
                    break
                time.sleep(0.01)
            assert result.state == "ready"
            assert result.trace is not None and result.canonical_audit is not None
            assert result.profile_sha256 == catalog.profiles[0].profile_sha256
            workspace_bytes = canonical_json_bytes(preview_request.draft.snapshot())
            assert result.workspace_sha256 == hashlib.sha256(workspace_bytes).hexdigest()
            assert result.workspace_size_bytes == len(workspace_bytes)

            status, headers, trace_raw = _exchange(connection, "GET", result.trace.url)
            assert status == 200
            assert headers["ETag"] == f'"{result.trace.sha256}"'
            assert hashlib.sha256(trace_raw).hexdigest() == result.trace.sha256
            assert len(trace_raw) == result.trace.size_bytes
            trace = json.loads(trace_raw)
            assert trace["demand_authoring"]["workspace_sha256"] == result.workspace_sha256
            assert trace["demand_authoring"]["traffic"] == {
                "vehicles": 3, "pedestrians": 1, "bicycles": 1,
            }

            status, _, raw = _exchange(
                connection, "GET",
                f"/authoring/v1/traffic-previews/{result.job_id}/assets/{'f' * 64}",
            )
            assert status == 404
            assert json.loads(raw)["error"]["code"] == "unknown_traffic_preview_asset"
            connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_http_post_rejects_duplicate_keys_extra_fields_and_stale_profile(
    tmp_path: Path,
) -> None:
    profiles = registry(tmp_path / "repo")
    valid = request(profiles).model_dump(mode="json")
    with TrafficPreviewJobManager(
        tmp_path / "jobs", profiles, runner=passing_runner,
    ) as manager, make_server(
        "127.0.0.1", 0, None, traffic_preview_manager=manager,
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            status, _, raw = _exchange(
                connection, "POST", "/authoring/v1/traffic-previews",
                b'{"schema_version":"a","schema_version":"b"}',
                {"Content-Type": "application/json"},
            )
            assert status == 400
            assert json.loads(raw)["error"]["code"] == "invalid_traffic_preview_request"

            extra = {**valid, "filesystem_path": "/tmp/untrusted"}
            status, _, raw = _exchange(
                connection, "POST", "/authoring/v1/traffic-previews",
                canonical_json_bytes(extra), {"Content-Type": "application/json"},
            )
            assert status == 400
            assert json.loads(raw)["error"]["code"] == "invalid_traffic_preview_request"

            stale = {**valid, "profile_sha256": "f" * 64}
            status, _, raw = _exchange(
                connection, "POST", "/authoring/v1/traffic-previews",
                canonical_json_bytes(stale), {"Content-Type": "application/json"},
            )
            assert status == 409
            assert json.loads(raw)["error"]["code"] == "traffic_preview_profile_conflict"
            connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
