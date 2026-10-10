"""Synthetic seal tests; these fixtures do not establish native execution."""

from __future__ import annotations

import hashlib

import pytest

from aero_bench.artifacts.contracts import ArtifactRecord, seal_manifest
from aero_bench.runtime.sealed_motion import load_sealed_motion_evidence
from tests.support import as_formal_run, build_bundle, resolve_bundle
from tests.test_runner import _make_runtime_seal


@pytest.fixture
def sealed(tmp_path):
    bundle = build_bundle(tmp_path / "bundle")
    run = as_formal_run(resolve_bundle(bundle.suite, executor_kind="docker_reference")[0])
    root = tmp_path / "artifacts"
    manifest = _make_runtime_seal(root, run, attempt_id="motion-closure")
    records = list(manifest.artifacts)
    existing = {item.artifact_id for item in records}
    for requirement in run.artifact_requirements:
        if requirement.artifact_id in existing:
            continue
        path = root / requirement.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"{}"
        path.write_bytes(payload)
        records.append(ArtifactRecord(
            artifact_id=requirement.artifact_id, artifact_type=requirement.artifact_type,
            producer_id=requirement.producer_id, visibility=requirement.visibility,
            relative_path=requirement.relative_path, sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        ))
    manifest = seal_manifest(
        root=root, run_id=run.run_id, attempt_id=manifest.attempt_id,
        execution_scope=run.execution_scope, event_chain_root=manifest.event_chain_root,
        artifacts=tuple(records),
    )
    return run, root, manifest


def load(sealed):
    run, root, manifest = sealed
    return load_sealed_motion_evidence(run=run, seal=manifest, seal_root=root, manifest_name=None)


def test_motion_loader_reconstructs_all_declared_stages(sealed):
    evidence = load(sealed)
    assert len(evidence.frames) == 1
    frame = evidence.frames[0]
    assert tuple(item.stage for item in frame.stage_barriers) == ("motion", "network", "business_environment")
    assert frame.scene_state.stage_barrier == frame.stage_barriers[0]
    assert frame.events == ()  # This fixture has no native PX4 source events.
    assert evidence.seal == sealed[2]
    assert evidence.ledger.chain_root == evidence.seal.event_chain_root


def test_external_manifest_layout_is_explicit(sealed):
    run, root, manifest = sealed
    with pytest.raises(ValueError, match="inventory"):
        load_sealed_motion_evidence(run=run, seal=manifest, seal_root=root)


def test_motion_loader_checks_all_artifact_bytes(sealed):
    _, root, manifest = sealed
    artifact = next(item for item in manifest.artifacts if item.artifact_type != "event.log")
    path = root / artifact.relative_path
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="digest mismatch"):
        load(sealed)


def test_resealed_invalid_scene_history_is_rejected(sealed):
    run, root, manifest = sealed
    history = next(item for item in manifest.artifacts if item.artifact_type == "scene.state-history")
    path = root / history.relative_path
    path.chmod(0o600)
    path.write_bytes(b"{}\n")
    changed = history.model_copy(update={"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                         "size_bytes": path.stat().st_size})
    resealed = seal_manifest(
        root=root, run_id=run.run_id, attempt_id=manifest.attempt_id,
        execution_scope=run.execution_scope, event_chain_root=manifest.event_chain_root,
        artifacts=tuple(changed if item == history else item for item in manifest.artifacts),
    )
    with pytest.raises(ValueError):
        load((run, root, resealed))


def test_linked_artifact_root_is_rejected(sealed, tmp_path):
    run, root, manifest = sealed
    linked = tmp_path / "link"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        load((run, linked, manifest))
