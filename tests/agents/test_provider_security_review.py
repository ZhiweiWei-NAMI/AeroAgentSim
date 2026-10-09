"""Provider error regression through real HTTP, persisted WAL and the commits API."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from aerokernel.compact import expand_record
from aerokernel.journal import iter_records
from fastapi.testclient import TestClient

from aeroagentsim.agents.langgraph import record_descriptor
from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.app import create_app


def test_provider_error_secrets_never_reach_wal_or_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "dummy-provider-key-for-release-regression"
    received: list[str | None] = []

    class ErrorHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            authorization = self.headers.get("Authorization")
            received.append(authorization)
            body = json.dumps({"error": "rejected " + str(authorization)}).encode()
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ErrorHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setenv("AAS_LLM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("AAS_LLM_MODEL", "test-model")
    monkeypatch.setenv("AAS_LLM_API_KEY", secret)
    descriptor = record_descriptor()
    descriptor["id"] = "aas.agent.record"
    document = {
        "format": "aeroagentsim.scenario/v1",
        "id": "provider-error-review",
        "registry": {
            "snapshot": str(
                Path("scenarios/realtime-registry.snapshot.json").resolve()
            ),
            "messages": [descriptor],
        },
        "entities": [],
        "bindings": {"lifecycle": []},
        "engines": {
            "agent": {
                "plugin": "decision",
                "config": {
                    "instruction": "Test one real HTTP failure",
                    "provider": {"profile": "default"},
                    "grants": {
                        "fields": [],
                        "relations": [],
                        "events": [],
                        "commands": [],
                    },
                    "points": [{"timer_ns": 1}],
                    "budget": {
                        "wall_timeout_s": 2,
                        "max_rounds": 1,
                        "max_calls": 1,
                        "max_tokens": 1,
                        "max_retries": 0,
                        "max_prompt_bytes": 10000,
                    },
                },
            }
        },
        "run": {"pacing": "fast", "seed": 1, "until_ns": 2, "advance_ns": 2},
        "outputs": {"durability": "flush"},
    }
    root, directory = tmp_path / "runs", tmp_path / "runs" / "error-run"
    try:
        with RunSession(load_scenario(document), directory) as session:
            session.run()
        assert received == ["Bearer " + secret]
        decoded = [
            expand_record(row) for row in iter_records(directory / "journal.jsonl")
        ]
        assert secret not in json.dumps(decoded)
        with TestClient(create_app(root), base_url="http://localhost") as client:
            response = client.get("/v1/runs/error-run/commits?limit=4096")
            assert response.status_code == 200, response.text
            assert (
                secret not in response.text and "rejected Bearer" not in response.text
            )
            failures = [
                message["payload"]
                for commit in response.json()["commits"]
                for message in commit["messages"]
                if message["schemaId"] == "aas.agent.record"
                and message["payload"]["phase"] == "failure"
            ]
            assert len(failures) == 1 and failures[0]["code"] == "HTTP_STATUS"
            assert "401" in json.loads(failures[0]["data_json"])["message"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
