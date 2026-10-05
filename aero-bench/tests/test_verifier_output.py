from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aero_bench.artifacts import ArtifactRecord, SealManifest, seal_manifest
from aero_bench.config.models import ArtifactRequirement
from aero_bench.serialization import canonical_json_bytes
from aero_bench.verifier import (
    ValidatedVerificationOutput,
    VerificationOutputError,
    load_and_validate_verification_output,
)
from tests.support import build_bundle, promote_bundle_to_formal, resolve_bundle


EVENT_CHAIN_ROOT = "b" * 64


def _run(root: Path, *, formal: bool = True):
    bundle = build_bundle(root)
    if formal:
        promote_bundle_to_formal(bundle)
    return resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]


def _runtime_seal(
    root: Path, run, *, artifact_id: str = "runtime.evidence"
) -> SealManifest:
    artifact_root = root / "runtime"
    artifact_path = artifact_root / "evidence.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b'{"evidence":true}\n')
    artifact = ArtifactRecord(
        artifact_id=artifact_id,
        artifact_type="runtime.evidence",
        producer_id="harness",
        visibility="public",
        relative_path="evidence.json",
        sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        size_bytes=artifact_path.stat().st_size,
    )
    return seal_manifest(
        root=artifact_root,
        run_id=run.run_id,
        attempt_id="attempt.test",
        execution_scope=run.execution_scope,
        event_chain_root=EVENT_CHAIN_ROOT,
        artifacts=(artifact,),
    )


def _report_payload(
    run,
    *,
    status: str | None = None,
    goals: list[dict[str, object]] | None = None,
    coverage_complete: bool = True,
) -> dict[str, object]:
    goal = run.task.goals[0]
    resolved_status = status or (
        "passed" if run.execution_scope == "formal_benchmark" else "invalid"
    )
    if goals is None:
        if resolved_status == "invalid":
            goals = [
                {
                    "goal_id": goal.goal_id,
                    "passed": False,
                    "metrics": [],
                    "failure_class": "executor_validation",
                }
            ]
        else:
            goals = [
                {
                    "goal_id": goal.goal_id,
                    "passed": True,
                    "metrics": [
                        {
                            "metric_id": goal.metric_id,
                            "value": 1.0,
                            "unit": "ratio",
                            "evidence": [{"artifact_id": "runtime.evidence"}],
                        }
                    ],
                }
            ]
    return {
        "schema_version": "aero-bench.verification/v1",
        "run_id": run.run_id,
        "execution_scope": run.execution_scope,
        "status": resolved_status,
        "goals": goals,
        "coverage_complete": coverage_complete,
    }


def _output_seal(
    root: Path,
    run,
    raw: bytes,
    *,
    relative_path: str | None = None,
    artifact_id: str | None = None,
    producer_id: str | None = None,
    visibility: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    extra_output: bool = False,
) -> tuple[Path, SealManifest]:
    requirement = run.verification_outputs[0]
    output_root = root / "output"
    report_path = output_root / requirement.relative_path
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(raw)
    report_record = ArtifactRecord(
        artifact_id=requirement.artifact_id,
        artifact_type=requirement.artifact_type,
        producer_id=requirement.producer_id,
        visibility=requirement.visibility,
        relative_path=requirement.relative_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )
    records = [report_record]
    if extra_output:
        extra_requirement = run.verification_outputs[1]
        extra_path = output_root / extra_requirement.relative_path
        extra_path.parent.mkdir(parents=True, exist_ok=True)
        extra_payload = b"additional sealed output\n"
        extra_path.write_bytes(extra_payload)
        records.append(
            ArtifactRecord(
                artifact_id=extra_requirement.artifact_id,
                artifact_type=extra_requirement.artifact_type,
                producer_id=extra_requirement.producer_id,
                visibility=extra_requirement.visibility,
                relative_path=extra_requirement.relative_path,
                sha256=hashlib.sha256(extra_payload).hexdigest(),
                size_bytes=len(extra_payload),
            )
        )
    output_seal = seal_manifest(
        root=output_root,
        run_id=run.run_id,
        attempt_id="attempt.test",
        execution_scope=run.execution_scope,
        event_chain_root=EVENT_CHAIN_ROOT,
        artifacts=tuple(records),
    )
    (output_root / "verification-manifest.json").write_bytes(
        canonical_json_bytes(output_seal.model_dump(mode="json")) + b"\n"
    )
    overrides = {
        field: value
        for field, value in {
            "artifact_id": artifact_id,
            "producer_id": producer_id,
            "visibility": visibility,
            "relative_path": relative_path,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }.items()
        if value is not None
    }
    if overrides:
        forged_artifacts = tuple(
            artifact.model_copy(update=overrides)
            if artifact.artifact_id == requirement.artifact_id
            else artifact
            for artifact in output_seal.artifacts
        )
        output_seal = output_seal.model_copy(update={"artifacts": forged_artifacts})
    return output_root, output_seal


def _load(root: Path, *, formal: bool = True, raw: bytes | None = None, **seal_kwargs):
    run = _run(root / "bundle", formal=formal)
    if seal_kwargs.pop("extra_output", False):
        extra = ArtifactRequirement(
            artifact_id="verification.trace",
            artifact_type="public.trace",
            producer_id=run.task.verifier.verifier_id,
            visibility="public",
            relative_path="verifier/public-trace.json",
            max_size_bytes=1024,
            source_asset_id=None,
        )
        run = run.model_copy(
            update={"verification_outputs": (*run.verification_outputs, extra)}
        )
        seal_kwargs["extra_output"] = True
    runtime_seal = _runtime_seal(root, run)
    report = _report_payload(run)
    report_bytes = canonical_json_bytes(report) + b"\n" if raw is None else raw
    output_root, output_seal = _output_seal(
        root,
        run,
        report_bytes,
        **seal_kwargs,
    )
    return run, runtime_seal, output_root, output_seal


@pytest.mark.parametrize("formal", [True, False])
def test_loads_legal_report_for_formal_or_executor_validation(
    tmp_path: Path, formal: bool
) -> None:
    run, runtime_seal, output_root, output_seal = _load(tmp_path, formal=formal)

    result = load_and_validate_verification_output(
        run=run,
        runtime_seal=runtime_seal,
        output_seal=output_seal,
        output_root=output_root,
    )

    assert result.seal == output_seal
    assert isinstance(result, ValidatedVerificationOutput)
    assert result.report.status == ("passed" if formal else "invalid")


def test_validated_output_rejects_mismatched_seal_bindings(
    tmp_path: Path,
) -> None:
    run, runtime_seal, output_root, output_seal = _load(tmp_path, formal=False)
    result = load_and_validate_verification_output(
        run=run,
        runtime_seal=runtime_seal,
        output_seal=output_seal,
        output_root=output_root,
    )

    with pytest.raises(ValueError, match="run_id"):
        ValidatedVerificationOutput(
            seal=result.seal,
            report=result.report.model_copy(update={"run_id": "c" * 64}),
        )
    with pytest.raises(ValueError, match="execution_scope"):
        formal_report = result.report.model_copy(
            update={"execution_scope": "formal_benchmark"}
        )
        ValidatedVerificationOutput(seal=result.seal, report=formal_report)


def test_accepts_report_alongside_another_declared_output(tmp_path: Path) -> None:
    run, runtime_seal, output_root, output_seal = _load(tmp_path, extra_output=True)

    result = load_and_validate_verification_output(
        run=run,
        runtime_seal=runtime_seal,
        output_seal=output_seal,
        output_root=output_root,
    )

    assert result.report.status == "passed"
    assert len(output_seal.artifacts) == 2


def test_rejects_seal_bindings_that_do_not_match_run_or_runtime(tmp_path: Path) -> None:
    run, runtime_seal, output_root, output_seal = _load(tmp_path)
    forged_runtime = runtime_seal.model_copy(update={"run_id": "c" * 64})

    with pytest.raises(VerificationOutputError, match="runtime seal run_id"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=forged_runtime,
            output_seal=output_seal,
            output_root=output_root,
        )

    forged_output = output_seal.model_copy(update={"event_chain_root": "d" * 64})
    with pytest.raises(VerificationOutputError, match="event_chain_root"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=forged_output,
            output_root=output_root,
        )


@pytest.mark.parametrize(
    "seal_kwargs,pattern",
    [
        ({"artifact_id": "other.report"}, "artifact_id"),
        ({"producer_id": "other.verifier"}, "producer_id"),
        ({"visibility": "private"}, "visibility"),
        ({"relative_path": "other/report.json"}, "relative_path"),
        ({"size_bytes": 1}, "size"),
        ({"sha256": "e" * 64}, "digest"),
    ],
)
def test_rejects_output_record_tampering(
    tmp_path: Path, seal_kwargs: dict[str, object], pattern: str
) -> None:
    run, runtime_seal, output_root, output_seal = _load(tmp_path, **seal_kwargs)

    with pytest.raises(VerificationOutputError, match=pattern):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )


def test_rejects_content_tampering_after_output_seal(tmp_path: Path) -> None:
    run, runtime_seal, output_root, output_seal = _load(tmp_path)
    report_path = output_root / run.verification_outputs[0].relative_path
    report_path.write_bytes(report_path.read_bytes() + b"tampered")

    with pytest.raises(VerificationOutputError, match="size|digest"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )


def test_rejects_missing_or_additional_report_declarations(tmp_path: Path) -> None:
    run, runtime_seal, output_root, output_seal = _load(tmp_path)
    other = ArtifactRequirement(
        artifact_id="other.output",
        artifact_type="other.output",
        producer_id=run.task.verifier.verifier_id,
        visibility="public",
        relative_path="verifier/other.json",
        max_size_bytes=1024,
        source_asset_id=None,
    )

    with pytest.raises(VerificationOutputError, match="inventory"):
        load_and_validate_verification_output(
            run=run.model_copy(
                update={"verification_outputs": (*run.verification_outputs, other)}
            ),
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )

    with pytest.raises(VerificationOutputError, match="exactly one"):
        load_and_validate_verification_output(
            run=run.model_copy(update={"verification_outputs": (other,)}),
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )


@pytest.mark.parametrize(
    "kind,pattern",
    [
        ("duplicate", "duplicate"),
        ("spacing", "canonical"),
        ("nan", "strict JSON"),
        ("utf8", "UTF-8"),
    ],
)
def test_rejects_non_strict_or_noncanonical_json(
    tmp_path: Path, kind: str, pattern: str
) -> None:
    run = _run(tmp_path / "bundle")
    runtime_seal = _runtime_seal(tmp_path, run)
    canonical = canonical_json_bytes(_report_payload(run)) + b"\n"
    raw = {
        "duplicate": b'{"coverage_complete":true,"coverage_complete":true}\n',
        "spacing": b" " + canonical,
        "nan": canonical.replace(b"1.0", b"NaN", 1),
        "utf8": b"\xff\n",
    }[kind]
    output_root, output_seal = _output_seal(tmp_path, run, raw)

    with pytest.raises(VerificationOutputError, match=pattern):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1.0"])
def test_rejects_non_json_metric_value_at_output_boundary(
    tmp_path: Path, value: str
) -> None:
    run = _run(tmp_path / "bundle")
    runtime_seal = _runtime_seal(tmp_path, run)
    report = _report_payload(run)
    report_goal = report["goals"][0]
    assert isinstance(report_goal, dict)
    report_metric = report_goal["metrics"][0]
    assert isinstance(report_metric, dict)
    report_metric["value"] = value
    raw = canonical_json_bytes(report) + b"\n"
    output_root, output_seal = _output_seal(tmp_path, run, raw)

    with pytest.raises(VerificationOutputError, match="contract is invalid"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )


def test_rejects_wrong_goal_coverage(tmp_path: Path) -> None:
    root = tmp_path
    run = _run(root / "bundle")
    runtime_seal = _runtime_seal(root, run)
    raw = (
        canonical_json_bytes(
            _report_payload(
                run,
                status="failed",
                goals=[],
                coverage_complete=False,
            )
        )
        + b"\n"
    )
    output_root, output_seal = _output_seal(root, run, raw)

    with pytest.raises(VerificationOutputError, match="cover every"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )


def test_rejects_unknown_evidence_and_unsafe_selector(tmp_path: Path) -> None:
    run = _run(tmp_path / "bundle")
    runtime_seal = _runtime_seal(tmp_path, run)
    goal = run.task.goals[0]
    bad_goal = {
        "goal_id": goal.goal_id,
        "passed": True,
        "metrics": [
            {
                "metric_id": goal.metric_id,
                "value": 1.0,
                "evidence": [
                    {"artifact_id": "not.in.runtime.seal", "selector": "../x"}
                ],
            }
        ],
    }
    raw = canonical_json_bytes(_report_payload(run, goals=[bad_goal])) + b"\n"
    output_root, output_seal = _output_seal(tmp_path, run, raw)

    with pytest.raises(VerificationOutputError, match="absent from the runtime"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )

    safe_goal = json.loads(json.dumps(bad_goal))
    safe_goal["metrics"][0]["evidence"][0] = {
        "artifact_id": "runtime.evidence",
        "selector": "../x",
    }
    safe_raw = canonical_json_bytes(_report_payload(run, goals=[safe_goal])) + b"\n"
    safe_root, safe_seal = _output_seal(tmp_path / "safe", run, safe_raw)
    with pytest.raises(VerificationOutputError, match="escape|normalized"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=safe_seal,
            output_root=safe_root,
        )


def test_rejects_executor_validation_pass_status(tmp_path: Path) -> None:
    run = _run(tmp_path / "bundle", formal=False)
    runtime_seal = _runtime_seal(tmp_path, run)
    raw = (
        canonical_json_bytes(
            _report_payload(run, status="passed", coverage_complete=True)
        )
        + b"\n"
    )
    output_root, output_seal = _output_seal(tmp_path, run, raw)

    with pytest.raises(
        VerificationOutputError, match="formal_benchmark|executor_validation"
    ):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )


def test_accepts_a_normalized_selector_and_sealed_manifest_file(tmp_path: Path) -> None:
    run = _run(tmp_path / "bundle")
    runtime_seal = _runtime_seal(tmp_path, run)
    goal = run.task.goals[0]
    goal_payload = {
        "goal_id": goal.goal_id,
        "passed": True,
        "metrics": [
            {
                "metric_id": goal.metric_id,
                "value": 1.0,
                "unit": "ratio",
                "evidence": [
                    {"artifact_id": "runtime.evidence", "selector": "/evidence"}
                ],
            }
        ],
    }
    raw = canonical_json_bytes(_report_payload(run, goals=[goal_payload])) + b"\n"
    output_root, output_seal = _output_seal(tmp_path, run, raw)
    (output_root / "verification-manifest.json").write_bytes(
        canonical_json_bytes(output_seal.model_dump(mode="json")) + b"\n"
    )

    result = load_and_validate_verification_output(
        run=run,
        runtime_seal=runtime_seal,
        output_seal=output_seal,
        output_root=output_root,
    )

    assert result.report.goals[0].metrics[0].evidence[0].selector == "/evidence"


def test_rejects_output_root_symlink_and_extra_files(tmp_path: Path) -> None:
    run, runtime_seal, output_root, output_seal = _load(tmp_path)
    (output_root / "extra.json").write_text("extra", encoding="utf-8")

    with pytest.raises(VerificationOutputError, match="inventory"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=output_root,
        )

    symlink = tmp_path / "output-link"
    symlink.symlink_to(output_root, target_is_directory=True)
    with pytest.raises(VerificationOutputError, match="symbolic link"):
        load_and_validate_verification_output(
            run=run,
            runtime_seal=runtime_seal,
            output_seal=output_seal,
            output_root=symlink,
        )
