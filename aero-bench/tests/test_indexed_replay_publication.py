from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aero_bench.runner import execution
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.contracts import PublicReplayIndex, PublicReplayManifest, PublicRuntimeArtifact
from tests.runtime.test_scene_state import _assemble, _assembler
from tests.test_trace_projector import _project


def _publication(tmp_path: Path, count: int) -> dict:
    """Publication-only test shell; not a passing run or simulator substitute."""
    trace = _project(tmp_path)
    assembler = _assembler()
    states = []
    for tick in range(1, count + 1):
        states.append(_assemble(assembler, tick, states[-1] if states else None))
    history = b"".join(canonical_json_bytes(state.model_dump(mode="json")) + b"\n" for state in states)
    digest = hashlib.sha256(history).hexdigest()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "history.jsonl").write_bytes(history)
    artifact = PublicRuntimeArtifact(
        artifact_id="artifact.history", artifact_type="scene.state-history", selector="history.jsonl",
        sha256=digest, size_bytes=len(history), replay_path=f"artifacts/{digest}",
    )
    trace = trace.model_copy(update={
        "scene_states": tuple(states),
        "scenario": trace.scenario.model_copy(update={"assets": (), "replay_mode": "indexed"}),
        "runtime_artifacts": (artifact,),
        "scene_state_history_artifact": trace.scene_state_history_artifact.model_copy(update={
            "artifact_id": artifact.artifact_id, "selector": artifact.selector, "digest": digest,
        }),
    })
    payload = canonical_json_bytes(trace.model_dump(mode="json")) + b"\n"
    run_root = tmp_path / "run"
    run_root.mkdir()
    return dict(
        run=SimpleNamespace(run_id=trace.run_id, scenario=SimpleNamespace(assets=(), scenario_digest=trace.scenario_digest)),
        seal=SimpleNamespace(event_chain_root=trace.event_chain_root, artifacts=(SimpleNamespace(
            artifact_id=artifact.artifact_id, visibility="public", relative_path=artifact.selector,
            sha256=digest, size_bytes=len(history),
        ),)),
        trace=trace, trace_payload=payload, trace_digest=hashlib.sha256(payload).hexdigest(),
        bundle_root=tmp_path, seal_root=runtime_root, run_root=run_root,
    )


@pytest.mark.parametrize("count", [0, 1, 256, 257, 513])
def test_indexed_publication_binds_exact_256_record_shards(tmp_path, count) -> None:
    inputs = _publication(tmp_path, count)
    identity = execution._publish_public_replay(**inputs)
    root = inputs["run_root"] / "public/replay"
    manifest = PublicReplayManifest.model_validate_json((root / "replay-manifest.json").read_bytes())
    assert identity.sha256 == hashlib.sha256((root / "replay-manifest.json").read_bytes()).hexdigest()
    assert manifest.replay_mode == "indexed"
    assert manifest.replay_index is not None
    assert manifest.scene_state_history is not None
    index = PublicReplayIndex.model_validate_json((root / manifest.replay_index.relative_path).read_bytes())
    assert index.scene_state_count == count
    assert [shard.scene_state_count for shard in index.shards] == [min(256, count - start) for start in range(0, count, 256)]
    history = (root / manifest.scene_state_history.relative_path).read_bytes()
    assert b"".join((root / shard.relative_path).read_bytes() for shard in index.shards) == history
    if 0 < count <= 256:
        assert index.shards[0].relative_path == manifest.scene_state_history.relative_path
    assert not list(inputs["run_root"].glob(".public-*"))
    with pytest.raises(execution.RunnerError, match="not fresh"):
        execution._publish_public_replay(**inputs)
    assert (root / manifest.scene_state_history.relative_path).read_bytes() == history


def test_publication_never_removes_a_concurrent_publishers_directory(tmp_path, monkeypatch) -> None:
    inputs = _publication(tmp_path, 1)
    original = execution._rename_directory_noreplace

    def race(source, target):
        target.mkdir()
        (target / "existing-evidence").write_bytes(b"preserve")
        original(source, target)

    monkeypatch.setattr(execution, "_rename_directory_noreplace", race)
    with pytest.raises(execution.RunnerError, match="without overwrite"):
        execution._publish_public_replay(**inputs)
    assert (inputs["run_root"] / "public/existing-evidence").read_bytes() == b"preserve"
    assert not list(inputs["run_root"].glob(".public-*"))


def test_atomic_publication_refuses_even_an_empty_existing_directory(tmp_path) -> None:
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with pytest.raises(execution.RunnerError, match="without overwrite"):
        execution._rename_directory_noreplace(source, target)
    assert source.is_dir() and target.is_dir()


def test_exclusive_write_failure_preserves_a_file_it_did_not_create(tmp_path) -> None:
    path = tmp_path / "authority"
    path.write_bytes(b"old sealed bytes")
    with pytest.raises(execution.RunnerError):
        execution._write_exclusive(path, b"replacement")
    assert path.read_bytes() == b"old sealed bytes"


def test_content_addressed_deduplication_rejects_conflicts_and_symlinks(tmp_path) -> None:
    path = tmp_path / "asset"
    execution._write_content_addressed(path, b"verified")
    execution._write_content_addressed(path, b"verified")
    with pytest.raises(execution.RunnerError, match="conflicting"):
        execution._write_content_addressed(path, b"other")
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(execution.RunnerError, match="symbolic"):
        execution._write_content_addressed(link, b"verified")
    assert path.read_bytes() == b"verified"


def test_publication_fsyncs_children_before_rename_and_parent_after(tmp_path, monkeypatch) -> None:
    inputs = _publication(tmp_path, 1)
    actions = []
    sync, rename = execution._fsync_directory, execution._rename_directory_noreplace

    def record_sync(path):
        actions.append(path.name)
        sync(path)

    def record_rename(source, target):
        actions.append("rename")
        rename(source, target)

    monkeypatch.setattr(execution, "_fsync_directory", record_sync)
    monkeypatch.setattr(execution, "_rename_directory_noreplace", record_rename)
    execution._publish_public_replay(**inputs)
    assert actions[:3] == ["artifacts", "assets", "replay"]
    assert actions[3].startswith(".public-")
    assert actions[4:] == ["rename", "run"]


def test_publication_rejects_raw_trace_digest_mismatch(tmp_path) -> None:
    inputs = _publication(tmp_path, 1)
    inputs["trace_payload"] += b" "
    with pytest.raises(execution.RunnerError, match="trace bytes"):
        execution._publish_public_replay(**inputs)
    assert not (inputs["run_root"] / "public").exists()


def test_manifest_requires_explicit_indexed_declaration(tmp_path) -> None:
    inputs = _publication(tmp_path, 1)
    execution._publish_public_replay(**inputs)
    path = inputs["run_root"] / "public/replay/replay-manifest.json"
    document = json.loads(path.read_bytes())
    for field in ("replay_mode", "replay_index", "scene_state_history"):
        omitted = {key: value for key, value in document.items() if key != field}
        with pytest.raises(ValidationError):
            PublicReplayManifest.model_validate(omitted)
