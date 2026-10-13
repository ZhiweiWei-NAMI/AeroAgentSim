"""Copied fixture references compare by real bytes, independently of storage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.assistant.api import create_router
from aeroagentsim.assistant.compiler import validate_compiled
from aeroagentsim.assistant.sessions import SessionStore
from aeroagentsim.assistant.tools import ToolRunner
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.scenario.paths import fixture_digests


@pytest.mark.parametrize("reference", ["fixtures/decisions.json", "${RESEARCH_FIXTURE}/decisions.json"])
def test_fixture_digests_resolve_real_references_and_ignore_annotations(tmp_path, monkeypatch, reference):
    directory = tmp_path / "fixtures"
    directory.mkdir()
    source = directory / "decisions.json"
    source.write_bytes(b'{"mode":"scripted"}')
    monkeypatch.setenv("RESEARCH_FIXTURE", str(directory))
    document = {"engines": {"decisions": {"config": {"fixture_path": reference, "fixture_sha256": "stale"}}}}
    expected = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    assert fixture_digests(document, tmp_path) == {reference: expected}
    source.write_bytes(b'{"mode":"scripted","responses":{}}')
    assert fixture_digests(document, tmp_path)[reference] != expected
    source.unlink()
    with pytest.raises(FileNotFoundError):
        fixture_digests(document, tmp_path)


def test_t2_r4_002_preview_and_applied_workspace_expose_copied_fixture_digests(tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces", None)
    templates = TemplateRegistry()
    draft = store.import_template("traffic-accident", {}, registry=templates)
    sessions = SessionStore(tmp_path / "recordings")
    identifier = sessions.create(draft["id"])["id"]
    tools = ToolRunner(store, templates, sessions, tmp_path / "recordings")
    proposal = tools.draft_plan(identifier, base={"workspace": draft["id"]}, title="Wind", summary="Wind at 5 m/s",
                                operations=[{"id": "wind", "op": "set_wind", "params": {"speed_mps": 5}}],
                                assumptions=[], open_questions=[], note="Wind")
    version = proposal["version"]
    validation = validate_compiled(tools.context(identifier), sessions.plan_directory(identifier, version))
    assert validation["valid"], validation
    sessions.validated(identifier, version, True, [])
    app = FastAPI()
    app.include_router(create_router(store, run_root=tmp_path / "recordings", templates=templates,
                                     sessions=sessions, harness=SimpleNamespace(tools=tools)))
    endpoint = f"/v1/assistant/sessions/{identifier}/plans/{version}/scenario"
    with TestClient(app) as client:
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        preview = response.json()
        applied = client.post(f"/v1/assistant/sessions/{identifier}/apply", json={"plan_version": version})
        assert applied.status_code == 200, applied.text
        saved = store.summary(applied.json()["workspace_id"])
        plan_path = preview["scenario"]["engines"]["decisions"]["config"]["fixture_path"]
        saved_path = saved["scenario"]["engines"]["decisions"]["config"]["fixture_path"]
        assert plan_path != saved_path
        digest = "sha256:" + hashlib.sha256(Path(plan_path).read_bytes()).hexdigest()
        assert preview["fixture_digests"] == {plan_path: digest}
        assert saved["fixture_digests"] == {saved_path: digest}
        assert saved["dirty"] is False and saved["latest_version"]["version"] == 1

        # Digest reads must reflect the actual file even without a draft revision.
        Path(saved_path).write_bytes(Path(saved_path).read_bytes() + b"\n")
        edited = store.summary(saved["id"])
        assert edited["scenario"] == saved["scenario"]
        assert edited["fixture_digests"][saved_path] != digest
        assert client.get(endpoint).json()["fixture_digests"] == {plan_path: digest}

        Path(saved_path).unlink()
        with pytest.raises(FileNotFoundError):
            store.summary(saved["id"])
        Path(plan_path).unlink()
        missing = client.get(endpoint)
        assert missing.status_code == 404 and "decisions.json" in missing.json()["detail"]
