from __future__ import annotations

import pytest
from pydantic import ValidationError

from aero_bench.verifier import (
    GoalResult,
    MetricResult,
    VerificationReport,
    validate_report_against_run,
)
from tests.support import build_bundle, promote_bundle_to_formal, resolve_bundle


RUN_ID = "a" * 64


def _goal(passed: bool) -> GoalResult:
    return GoalResult(
        goal_id="goal.1",
        passed=passed,
        metrics=(MetricResult(metric_id="metric.1", value=1.0, evidence=()),),
    )


def _report(
    status: str,
    *,
    coverage_complete: bool,
    goals: tuple[GoalResult, ...],
) -> VerificationReport:
    return VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=RUN_ID,
        execution_scope="formal_benchmark",
        status=status,
        goals=goals,
        coverage_complete=coverage_complete,
    )


def test_verification_report_accepts_consistent_statuses() -> None:
    assert (
        _report("passed", coverage_complete=True, goals=(_goal(True),)).status
        == "passed"
    )
    assert (
        _report("failed", coverage_complete=True, goals=(_goal(False),)).status
        == "failed"
    )
    assert (
        _report("failed", coverage_complete=False, goals=(_goal(True),)).status
        == "failed"
    )


@pytest.mark.parametrize(
    "value",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "1.0",
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_metric_result_rejects_non_json_or_nonfinite_values(value: object) -> None:
    with pytest.raises(ValidationError, match="finite JSON number"):
        MetricResult(metric_id="metric.1", value=value, evidence=())


def test_verification_report_rejects_contradictory_statuses() -> None:
    with pytest.raises(ValidationError, match="at least one measured metric"):
        GoalResult(goal_id="goal.empty", passed=True, metrics=())
    with pytest.raises(ValidationError, match="complete coverage"):
        _report("passed", coverage_complete=False, goals=(_goal(True),))
    with pytest.raises(ValidationError, match="every goal to pass"):
        _report("passed", coverage_complete=True, goals=(_goal(False),))
    with pytest.raises(ValidationError, match="every goal to pass"):
        _report("passed", coverage_complete=True, goals=())
    with pytest.raises(ValidationError, match="unpassed goal or incomplete coverage"):
        _report("failed", coverage_complete=True, goals=(_goal(True),))

    with pytest.raises(ValidationError, match="formal_benchmark"):
        VerificationReport(
            schema_version="aero-bench.verification/v1",
            run_id=RUN_ID,
            execution_scope="executor_validation",
            status="passed",
            goals=(_goal(True),),
            coverage_complete=True,
        )


def test_verification_report_must_cover_the_resolved_run_goals(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    promote_bundle_to_formal(bundle)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    declared = run.task.goals[0]
    report = VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=run.run_id,
        execution_scope="formal_benchmark",
        status="passed",
        goals=(
            GoalResult(
                goal_id=declared.goal_id,
                passed=True,
                metrics=(
                    MetricResult(
                        metric_id=declared.metric_id,
                        value=1.0,
                        evidence=(),
                    ),
                ),
            ),
        ),
        coverage_complete=True,
    )
    validate_report_against_run(report, run)

    incomplete = report.model_copy(update={"goals": ()})
    with pytest.raises(ValueError, match="cover every ResolvedRun goal"):
        validate_report_against_run(incomplete, run)
