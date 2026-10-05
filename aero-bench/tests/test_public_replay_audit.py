"""Public export checks with mechanical unit fixtures, not formal run evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aero_bench.runner.execution import _project_and_write_public_trace
from aero_bench.trace import projector
from tests.test_trace_projector import _ledger, _run, _seal
from tests.world.support import materialized_inspection_scenario
from tools.audit_public_replay import PublicReplayIntegrityError, audit_public_replay


@pytest.fixture(params=["embedded", "indexed"])
def publication(tmp_path: Path, request, monkeypatch) -> Path:
    bundle = tmp_path / "bundle"
    run = _run(scenario=materialized_inspection_scenario(bundle))
    if request.param == "indexed":
        original = projector.project_public_scenario
        monkeypatch.setattr(
            projector,
            "project_public_scenario",
            lambda run: original(run).model_copy(update={"replay_mode": "indexed"}),
        )
    root = tmp_path / "run"
    seal = _seal(tmp_path, _ledger(run), seal_root=root / "runtime-seal")
    _project_and_write_public_trace(
        run=run, seal=seal, validated=None, bundle_root=bundle, run_root=root
    )
    return root / "public/replay"


def test_audit_checks_closed_public_bytes_without_minting_verifier_success(publication):
    result = audit_public_replay(publication)
    assert result["integrity_status"] == "checked"
    assert result["trace_phase"] == "aborted"
    assert result["execution_scope"] == "executor_validation"
    assert result["public_verdict"] is None
    assert result["benchmark_verifier_executed"] is False


def test_audit_rejects_changed_published_bytes(publication):
    manifest = json.loads((publication / "replay-manifest.json").read_bytes())
    target = next(
        item
        for item in manifest["files"]
        if item["relative_path"] != "public-trace.json"
    )
    (publication / target["relative_path"]).write_bytes(b"altered")
    with pytest.raises(PublicReplayIntegrityError, match="bytes differ"):
        audit_public_replay(publication)


def test_audit_rejects_an_unlisted_file(publication):
    (publication / "unlisted-private-evidence.txt").write_bytes(b"unit fixture")
    with pytest.raises(PublicReplayIntegrityError, match="undeclared files"):
        audit_public_replay(publication)


def test_audit_rejects_manifest_identity_rebinding(publication):
    path = publication / "replay-manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["run_id"] = "a" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PublicReplayIntegrityError, match="identity differs"):
        audit_public_replay(publication)


def test_audit_rejects_replay_symlink(publication, tmp_path):
    manifest = json.loads((publication / "replay-manifest.json").read_bytes())
    target = next(
        item
        for item in manifest["files"]
        if item["relative_path"] != "public-trace.json"
    )
    source = publication / target["relative_path"]
    elsewhere = tmp_path / "unit-other-file"
    elsewhere.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(elsewhere)
    with pytest.raises(PublicReplayIntegrityError, match="symlink"):
        audit_public_replay(publication)


def test_audit_rejects_a_missing_declared_file(publication):
    manifest = json.loads((publication / "replay-manifest.json").read_bytes())
    target = next(
        item
        for item in manifest["files"]
        if item["relative_path"] != "public-trace.json"
    )
    (publication / target["relative_path"]).unlink()
    with pytest.raises(PublicReplayIntegrityError, match="unavailable"):
        audit_public_replay(publication)


def test_audit_rejects_a_rehashed_trace_that_omits_required_query_id(publication):
    trace_path = publication / "public-trace.json"
    trace = json.loads(trace_path.read_bytes())
    assert trace["events"][0]["query_id"] is None
    del trace["events"][0]["query_id"]
    payload = json.dumps(trace).encode()
    trace_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest_path = publication / "replay-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["trace_sha256"] = digest
    for item in manifest["files"]:
        if item["relative_path"] == "public-trace.json":
            item.update(sha256=digest, size_bytes=len(payload))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PublicReplayIntegrityError, match="events/0/query_id"):
        audit_public_replay(publication)
