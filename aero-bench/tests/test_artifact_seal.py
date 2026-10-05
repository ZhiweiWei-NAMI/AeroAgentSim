from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from aero_bench.artifacts import (
    ArtifactRecord,
    records_from_requirements,
    seal_manifest,
    seal_manifest_size_upper_bound,
)
from aero_bench.config.models import ArtifactRequirement
from aero_bench.serialization import canonical_json_bytes


RUN_ID = "a" * 64
EVENT_CHAIN_ROOT = "b" * 64


def _requirement(
    *,
    artifact_id: str = "trace",
    relative_path: str = "trace.json",
    max_size_bytes: int = 64,
) -> ArtifactRequirement:
    return ArtifactRequirement(
        artifact_id=artifact_id,
        artifact_type="public.trace",
        producer_id="harness",
        visibility="public",
        relative_path=relative_path,
        max_size_bytes=max_size_bytes,
        source_asset_id=None,
    )


def test_seal_manifest_hashes_real_artifact_files(tmp_path) -> None:
    trace = tmp_path / "public" / "trace.json"
    trace.parent.mkdir()
    trace.write_text('{"tick": 1}\n', encoding="utf-8")
    artifact = ArtifactRecord(
        artifact_id="trace",
        artifact_type="public.trace",
        producer_id="harness",
        visibility="public",
        relative_path="public/trace.json",
        sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
        size_bytes=trace.stat().st_size,
    )

    seal = seal_manifest(
        root=tmp_path,
        run_id=RUN_ID,
        attempt_id="attempt.test",
        execution_scope="executor_validation",
        event_chain_root=EVENT_CHAIN_ROOT,
        artifacts=(artifact,),
    )

    assert seal.run_id == RUN_ID
    assert seal.artifacts == (artifact,)
    assert len(seal.manifest_digest) == 64


def test_seal_rejects_changed_artifact_content(tmp_path) -> None:
    trace = tmp_path / "trace.json"
    trace.write_text("before\n", encoding="utf-8")
    artifact = ArtifactRecord(
        artifact_id="trace",
        artifact_type="public.trace",
        producer_id="harness",
        visibility="public",
        relative_path="trace.json",
        sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
        size_bytes=trace.stat().st_size,
    )
    trace.write_text("after\n", encoding="utf-8")

    with pytest.raises(ValueError, match="digest mismatch"):
        seal_manifest(
            root=tmp_path,
            run_id=RUN_ID,
            attempt_id="attempt.test",
            execution_scope="executor_validation",
            event_chain_root=EVENT_CHAIN_ROOT,
            artifacts=(artifact,),
        )


def test_seal_rejects_an_undeclared_file(tmp_path) -> None:
    declared = tmp_path / "declared.json"
    declared.write_bytes(b"declared\n")
    (tmp_path / "undeclared.json").write_bytes(b"undeclared\n")
    artifact = ArtifactRecord(
        artifact_id="declared",
        artifact_type="public.trace",
        producer_id="harness",
        visibility="public",
        relative_path="declared.json",
        sha256=hashlib.sha256(declared.read_bytes()).hexdigest(),
        size_bytes=declared.stat().st_size,
    )

    with pytest.raises(ValueError, match="undeclared=.*undeclared.json"):
        seal_manifest(
            root=tmp_path,
            run_id=RUN_ID,
            attempt_id="attempt.test",
            execution_scope="executor_validation",
            event_chain_root=EVENT_CHAIN_ROOT,
            artifacts=(artifact,),
        )


def test_seal_model_rejects_a_forged_manifest_digest(tmp_path) -> None:
    trace = tmp_path / "trace.json"
    trace.write_text("sealed\n", encoding="utf-8")
    artifact = ArtifactRecord(
        artifact_id="trace",
        artifact_type="public.trace",
        producer_id="harness",
        visibility="public",
        relative_path="trace.json",
        sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
        size_bytes=trace.stat().st_size,
    )
    seal = seal_manifest(
        root=tmp_path,
        run_id=RUN_ID,
        attempt_id="attempt.test",
        execution_scope="executor_validation",
        event_chain_root=EVENT_CHAIN_ROOT,
        artifacts=(artifact,),
    )
    forged = seal.model_dump(mode="json")
    forged["manifest_digest"] = "c" * 64

    with pytest.raises(ValidationError, match="manifest_digest"):
        type(seal).model_validate(forged)


def test_records_from_requirements_hashes_and_binds_declared_metadata(tmp_path) -> None:
    trace = tmp_path / "trace.json"
    trace.write_bytes(b"trace")

    records = records_from_requirements(
        root=tmp_path,
        requirements=(_requirement(),),
    )

    assert records == (
        ArtifactRecord(
            artifact_id="trace",
            artifact_type="public.trace",
            producer_id="harness",
            visibility="public",
            relative_path="trace.json",
            sha256=hashlib.sha256(b"trace").hexdigest(),
            size_bytes=5,
        ),
    )


def test_records_from_requirements_enforces_max_size(tmp_path) -> None:
    (tmp_path / "trace.json").write_bytes(b"too large")

    with pytest.raises(ValueError, match="max_size_bytes"):
        records_from_requirements(
            root=tmp_path,
            requirements=(_requirement(max_size_bytes=3),),
        )


def test_seal_manifest_size_upper_bound_includes_stored_newline(tmp_path) -> None:
    requirement = _requirement(max_size_bytes=64)
    (tmp_path / requirement.relative_path).write_bytes(b"x" * 64)
    records = records_from_requirements(root=tmp_path, requirements=(requirement,))
    seal = seal_manifest(
        root=tmp_path,
        run_id=RUN_ID,
        attempt_id="a" * 128,
        execution_scope="formal_benchmark",
        event_chain_root=EVENT_CHAIN_ROOT,
        artifacts=records,
    )
    stored_manifest = canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"

    assert len(stored_manifest) == seal_manifest_size_upper_bound(
        run_id=RUN_ID,
        execution_scope="formal_benchmark",
        requirements=(requirement,),
    )


def test_artifact_requirement_rejects_the_root_directory() -> None:
    with pytest.raises(ValueError, match="normalized and relative"):
        _requirement(relative_path=".")


def test_seal_allows_multiple_artifacts_of_one_type_from_one_producer(tmp_path) -> None:
    first = tmp_path / "frame-1.json"
    second = tmp_path / "frame-2.json"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    artifacts = (
        ArtifactRecord(
            artifact_id="frame.one",
            artifact_type="frame",
            producer_id="provider",
            visibility="public",
            relative_path=first.name,
            sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
            size_bytes=first.stat().st_size,
        ),
        ArtifactRecord(
            artifact_id="frame.two",
            artifact_type="frame",
            producer_id="provider",
            visibility="public",
            relative_path=second.name,
            sha256=hashlib.sha256(second.read_bytes()).hexdigest(),
            size_bytes=second.stat().st_size,
        ),
    )

    seal = seal_manifest(
        root=tmp_path,
        run_id=RUN_ID,
        attempt_id="attempt.test",
        execution_scope="executor_validation",
        event_chain_root=EVENT_CHAIN_ROOT,
        artifacts=artifacts,
    )

    assert {item.artifact_id for item in seal.artifacts} == {
        "frame.one",
        "frame.two",
    }
