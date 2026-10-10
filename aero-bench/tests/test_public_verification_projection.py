"""Public verdict summaries preserve outcomes without exposing private inputs.

These mechanical report fixtures are not formal benchmark evidence.
"""

from pathlib import Path

import pytest

from aero_bench.artifacts import EvidenceReference
from aero_bench.trace.projector import (
    PublicProjectorError,
    SealedPublicArtifacts,
    _project_report,
)
from aero_bench.verifier.contracts import GoalResult, MetricResult, VerificationReport
from tests.test_trace_projector import (
    EVENT_LOG_ARTIFACT_ID,
    HISTORY_ARTIFACT_ID,
    RUN_ID,
    _ledger,
    _run,
    _seal,
)


def _resources(tmp_path: Path) -> SealedPublicArtifacts:
    return SealedPublicArtifacts(_seal(tmp_path, _ledger(_run())))


def _report(
    evidence: tuple[EvidenceReference, ...], *, passed: bool = False
) -> VerificationReport:
    return VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=RUN_ID,
        execution_scope="formal_benchmark",
        status="passed" if passed else "failed",
        coverage_complete=True,
        goals=(
            GoalResult(
                goal_id="goal.fixture",
                passed=passed,
                failure_class=None if passed else "requirement.fixture.not_met",
                metrics=(
                    MetricResult(
                        metric_id="metric.fixture",
                        value=1.25,
                        unit="m",
                        evidence=evidence,
                    ),
                ),
            ),
        ),
    )


def _public_reference(*, selector: str | None = "states/1") -> EvidenceReference:
    return EvidenceReference(artifact_id=HISTORY_ARTIFACT_ID, selector=selector)


@pytest.mark.parametrize("passed", [False, True])
def test_public_summary_preserves_report_and_filters_only_declared_private_inputs(
    tmp_path: Path, passed: bool
) -> None:
    resources = _resources(tmp_path)
    report = _report(
        (
            EvidenceReference(
                artifact_id=EVENT_LOG_ARTIFACT_ID,
                selector="private/provider/state/123",
            ),
            _public_reference(),
        ),
        passed=passed,
    )
    original = report.model_dump_json()

    public = _project_report(report, resources=resources)

    assert public.run_id == report.run_id
    assert public.status == report.status
    assert public.coverage_complete == report.coverage_complete
    assert public.goals[0].passed == passed
    assert public.goals[0].failure_class == report.goals[0].failure_class
    metric = public.goals[0].metrics[0]
    assert (metric.metric_id, metric.value, metric.unit) == (
        "metric.fixture", 1.25, "m"
    )
    assert len(metric.evidence) == 1
    assert metric.evidence[0].artifact_id == HISTORY_ARTIFACT_ID
    assert metric.evidence[0].digest == resources.resolve_by_id(
        HISTORY_ARTIFACT_ID, context="test"
    ).sha256
    assert metric.evidence[0].selector == "states/1"
    assert metric.evidence[0].visibility == "public"
    assert EVENT_LOG_ARTIFACT_ID not in public.model_dump_json()
    assert "private/provider/state/123" not in public.model_dump_json()
    assert report.model_dump_json() == original


@pytest.mark.parametrize(
    "evidence",
    [(), (EvidenceReference(artifact_id=EVENT_LOG_ARTIFACT_ID, selector=None),)],
)
def test_public_summary_rejects_metrics_without_public_support(
    tmp_path: Path, evidence: tuple[EvidenceReference, ...]
) -> None:
    with pytest.raises(PublicProjectorError, match="no sealed public evidence"):
        _project_report(_report(evidence), resources=_resources(tmp_path))


def test_public_summary_rejects_unknown_artifacts_even_with_public_support(
    tmp_path: Path,
) -> None:
    report = _report(
        (_public_reference(), EvidenceReference(artifact_id="artifact.unknown"))
    )
    with pytest.raises(PublicProjectorError, match="unsealed artifact artifact.unknown"):
        _project_report(report, resources=_resources(tmp_path))


def test_public_summary_still_rejects_duplicate_public_references(tmp_path: Path) -> None:
    with pytest.raises(PublicProjectorError, match="duplicate evidence"):
        _project_report(
            _report((_public_reference(), _public_reference())),
            resources=_resources(tmp_path),
        )


def test_public_summary_still_requires_a_public_selector(tmp_path: Path) -> None:
    with pytest.raises(PublicProjectorError, match="omits its public selector"):
        _project_report(
            _report((_public_reference(selector=None),)),
            resources=_resources(tmp_path),
        )


def test_other_public_references_still_reject_private_artifacts(tmp_path: Path) -> None:
    with pytest.raises(PublicProjectorError, match="private artifact"):
        _resources(tmp_path).reference(
            artifact_id=EVENT_LOG_ARTIFACT_ID,
            selector="events/1",
            context="sensor frame",
        )
