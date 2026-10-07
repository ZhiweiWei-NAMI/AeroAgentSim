"""Supported urban goal semantics, shared by packaging and offline verification."""
from __future__ import annotations

from aero_bench.config.models import GoalSpec, TaskSpec

HORIZON_METRIC = "urban.recovery.exact_horizon"
MISSION_METRIC = "urban.recovery.mission_completed"
URBAN_GOALS = (
    ("goal.urban.exact-horizon", HORIZON_METRIC),
    ("goal.urban.mission-completed", MISSION_METRIC),
)


def urban_goals(verifier_id: str) -> tuple[GoalSpec, ...]:
    return tuple(
        GoalSpec(
            goal_id=goal_id, verifier_id=verifier_id, metric_id=metric_id,
            operator="eq", threshold=1.0, evidence="event_log", parameters=(),
        )
        for goal_id, metric_id in URBAN_GOALS
    )


def validate_urban_goals(task: TaskSpec) -> None:
    if tuple(task.goals) != urban_goals(task.verifier.verifier_id):
        raise ValueError(
            "urban goals must declare the exact supported horizon and mission metrics, "
            "verifier, equality thresholds, evidence kind, and empty parameters"
        )
