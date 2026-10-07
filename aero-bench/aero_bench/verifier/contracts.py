from __future__ import annotations

import math
from typing import Literal

from pydantic import field_validator, model_validator

from aero_bench.artifacts.contracts import EvidenceReference, SealManifest
from aero_bench.config.models import Identifier, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec


class MetricResult(StrictModel):
    metric_id: Identifier
    value: float
    unit: str | None = None
    evidence: tuple[EvidenceReference, ...]

    @field_validator("value", mode="before")
    @classmethod
    def value_is_finite_json_number(cls, value: object) -> float:
        """Accept only the numeric values representable by strict JSON."""

        if type(value) not in (int, float):
            raise ValueError("metric value must be a finite JSON number")
        try:
            numeric = float(value)
        except (OverflowError, ValueError):
            raise ValueError("metric value must be a finite JSON number") from None
        if not math.isfinite(numeric):
            raise ValueError("metric value must be a finite JSON number")
        return numeric


class GoalResult(StrictModel):
    goal_id: Identifier
    passed: bool
    metrics: tuple[MetricResult, ...]
    failure_class: Identifier | None = None

    @model_validator(mode="after")
    def result_has_measurement_or_failure(self) -> "GoalResult":
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("goal result metric ids must be unique")
        if self.passed and not self.metrics:
            raise ValueError("passed goal requires at least one measured metric")
        if self.passed and self.failure_class is not None:
            raise ValueError("passed goal cannot carry a failure_class")
        return self


class VerificationInput(StrictModel):
    run: ResolvedRunSpec
    seal: SealManifest

    @model_validator(mode="after")
    def seal_matches_run(self) -> "VerificationInput":
        if self.seal.run_id != self.run.run_id:
            raise ValueError("verification seal belongs to another ResolvedRun")
        if self.seal.execution_scope != self.run.execution_scope:
            raise ValueError(
                "verification seal execution_scope differs from ResolvedRun"
            )
        return self


class VerificationReport(StrictModel):
    schema_version: Literal["aero-bench.verification/v1"]
    run_id: str
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    status: Literal["passed", "failed", "invalid"]
    goals: tuple[GoalResult, ...]
    coverage_complete: bool

    @model_validator(mode="after")
    def status_matches_results(self) -> "VerificationReport":
        all_goals_passed = bool(self.goals) and all(goal.passed for goal in self.goals)
        if self.status in {"passed", "failed"} and self.execution_scope != (
            "formal_benchmark"
        ):
            raise ValueError(
                "benchmark pass/fail requires formal_benchmark execution_scope"
            )
        if self.status == "passed":
            if not self.coverage_complete:
                raise ValueError("passed verification requires complete coverage")
            if not all_goals_passed:
                raise ValueError("passed verification requires every goal to pass")
        elif self.status == "failed" and self.coverage_complete and all_goals_passed:
            raise ValueError(
                "failed verification requires an unpassed goal or incomplete coverage"
            )
        return self


def validate_report_against_run(
    report: VerificationReport,
    run: ResolvedRunSpec,
) -> None:
    if report.run_id != run.run_id:
        raise ValueError("verification report belongs to another ResolvedRun")
    if report.execution_scope != run.execution_scope:
        raise ValueError("verification report execution_scope differs from ResolvedRun")
    expected = {goal.goal_id: goal.metric_id for goal in run.task.goals}
    actual = {goal.goal_id: goal for goal in report.goals}
    if set(actual) != set(expected) or len(actual) != len(report.goals):
        raise ValueError("verification report does not cover every ResolvedRun goal")
    for goal_id, result in actual.items():
        if report.status == "invalid":
            if result.passed or result.metrics or result.failure_class is None:
                raise ValueError(
                    "invalid verification goal must contain only a failure class"
                )
            continue
        if tuple(metric.metric_id for metric in result.metrics) != (expected[goal_id],):
            raise ValueError(
                "verification goal does not contain its declared ResolvedRun metric"
            )
