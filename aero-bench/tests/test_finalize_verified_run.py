"""Synthetic recovery fixtures; these are not formal execution evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from aero_bench.config.loader import sha256_file
from aero_bench.config.models import FileRef
from aero_bench.control import sealed_replay
from aero_bench.runner import execution
from aero_bench.runner.contracts import RunSummary
from aero_bench.serialization import canonical_json_bytes
from tests.support import (
    build_bundle, fixture_provider_registry, promote_bundle_to_formal, resolve_bundle,
)
from tests.test_control_sealed_replay import _SealedFixtureExecutor
from tests.test_runner import _runner_config, _write_runner_config
from tools import finalize_verified_run as recovery


def _bytes(value) -> bytes:
    return canonical_json_bytes(value.model_dump(mode="json")) + b"\n"


def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {str(path.relative_to(root)): (sha256_file(path), path.stat().st_ino)
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def publication(tmp_path, monkeypatch):
    bundle = build_bundle(tmp_path / "bundle")
    promote_bundle_to_formal(bundle)
    suite = yaml.safe_load(bundle.suite.read_text())
    suite["cases"][0]["seeds"] = [7]
    suite["cases"][0]["axes"][0]["values"] = [100_000_000]
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False))
    config_path = tmp_path / "source-runner.json"
    _write_runner_config(config_path, _runner_config(tmp_path / "execution"))
    monkeypatch.setattr(execution, "builtin_provider_registry", fixture_provider_registry)
    monkeypatch.setattr(sealed_replay, "builtin_provider_registry", fixture_provider_registry)
    monkeypatch.setattr(execution, "build_docker_executor", lambda *a, **kw: _SealedFixtureExecutor())
    runner = execution.run_suite(bundle.suite, config_path)
    completed = runner.runs[0]
    source = tmp_path / "execution" / completed.run_id
    retained_public = tmp_path / "fixture-public"
    shutil.move(source / "public", retained_public)
    original = RunSummary.model_validate({
        **completed.model_dump(mode="json"), "status": "error",
        "failure_classes": ["public_trace_projection_failed"], "public_trace": None,
    })
    (source / "run-summary.json").write_bytes(_bytes(original))
    (source / "failure-diagnostic.public_trace_projection.json").write_bytes(
        canonical_json_bytes({"stage": "public_trace_projection", "error": "fixture failure"}) + b"\n")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    compiled = SimpleNamespace(suite=FileRef(path="suite.yaml", sha256=sha256_file(bundle.suite)))
    monkeypatch.setattr(recovery, "load_published_compilation", lambda *args: (compiled, bundle.root, (run,)))
    monkeypatch.setattr(recovery, "_source_revision", lambda: {"git_head": "fixture", "files": []})
    calls = []

    def project(**kwargs):
        calls.append(kwargs)
        assert kwargs["validated"].report.status == "passed"
        shutil.copytree(retained_public, kwargs["run_root"] / "public")
        return completed.public_trace

    def forbidden(*args, **kwargs):
        pytest.fail("publication recovery attempted to construct an executor")

    monkeypatch.setattr(recovery, "_project_and_write_public_trace", project)
    monkeypatch.setattr(execution, "build_docker_executor", forbidden)
    return SimpleNamespace(
        source=source, bundle=bundle, config=config_path, original=original, calls=calls,
        output=tmp_path / "publication", run=run,
    )


def _recover(fixture, **overrides):
    return recovery.finalize_verified_run(
        compilation_root=fixture.bundle.root.parent, compilation_id="a" * 64,
        runner_config_path=fixture.config, source_run_root=fixture.source,
        output_root=overrides.pop("output_root", fixture.output), progress=lambda _: None,
        **overrides,
    )


def test_recovery_preserves_source_and_publishes_existing_read_only_delivery(publication):
    before = _snapshot(publication.source)
    result = _recover(publication)
    assert _snapshot(publication.source) == before
    assert result.passed_count == result.execution_complete_count == 1
    destination = publication.output / publication.run.run_id
    assert (destination / "original-run-summary.json").read_bytes() == _bytes(publication.original)
    receipt = json.loads((destination / "finalization-recovery.json").read_bytes())
    assert receipt["original_failure_classes"] == ["public_trace_projection_failed"]
    assert receipt["original_summary_sha256"] == hashlib.sha256(_bytes(publication.original)).hexdigest()
    assert receipt["simulation_rerun"] is receipt["verifier_rerun"] is False
    assert receipt["runtime_seal"] == publication.original.seal.model_dump(mode="json")
    for folder in ("runtime-seal", "verification"):
        for relative, (digest, inode) in _snapshot(publication.source / folder).items():
            copied = destination / folder / relative
            assert sha256_file(copied) == digest
            assert copied.stat().st_ino != inode
    assert publication.calls[0]["seal_root"] == destination / "runtime-seal"
    manager = sealed_replay.SealedReplayManager.from_files(
        suite_path=publication.bundle.suite,
        runner_config_path=publication.output / "replay-runner.json",
        run_ids=(publication.run.run_id,),
    )
    access = manager.issue_read_access(publication.run.run_id)
    assert access.read_only is True
    trace_bytes = manager.public_trace_document(
        publication.run.run_id, operator_token=access.credentials.operator_token,
    )
    assert json.loads(trace_bytes)["run_id"] == publication.run.run_id


@pytest.mark.parametrize("changes", [
    {"status": "passed"},
    {"failure_classes": ["public_trace_projection_failed", "runtime_failed"]},
    {"failure_classes": ["runtime_failed"]},
])
def test_recovery_rejects_other_failure_receipts_before_projection(publication, changes):
    document = publication.original.model_dump(mode="json")
    document.update(changes)
    (publication.source / "run-summary.json").write_bytes(canonical_json_bytes(document) + b"\n")
    with pytest.raises(ValueError):
        _recover(publication)
    assert not publication.calls and not publication.output.exists()


@pytest.mark.parametrize("folder", ["runtime-seal", "verification"])
def test_recovery_rejects_tampered_original_sealed_bytes(publication, folder):
    path = next(path for path in (publication.source / folder).rglob("*")
                if path.is_file() and not path.name.endswith("manifest.json"))
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError):
        _recover(publication)
    assert not publication.calls and not publication.output.exists()


def test_recovery_never_overwrites_existing_publication(publication):
    publication.output.mkdir()
    marker = publication.output / "user-data"
    marker.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="fresh"):
        _recover(publication)
    assert marker.read_bytes() == b"preserve" and not publication.calls


def test_recovery_rejects_output_redirected_into_source(publication):
    before = _snapshot(publication.source)
    alias = publication.output.parent / "source-alias"
    alias.symlink_to(publication.source, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links"):
        _recover(publication, output_root=alias / "publication")
    assert _snapshot(publication.source) == before
    assert not publication.calls
    assert not (publication.source / "publication").exists()


def test_projection_failure_cannot_publish_a_success_summary(publication, monkeypatch):
    before = _snapshot(publication.source)

    def fail(**kwargs):
        raise ValueError("actual projection failed")

    monkeypatch.setattr(recovery, "_project_and_write_public_trace", fail)
    with pytest.raises(ValueError, match="actual projection failed"):
        _recover(publication)
    assert _snapshot(publication.source) == before
    assert not (publication.output / "runner-summary.json").exists()
    assert not (publication.output / publication.run.run_id / "finalization-recovery.json").exists()
