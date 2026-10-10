"""Goal semantics tests; no fixture here represents a verified formal run."""
from types import SimpleNamespace

import pytest

from aero_bench.config.models import GoalSpec
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.urban_recovery_demo import verifier as urban
from aero_bench.tasks.urban_recovery_demo.goals import urban_goals, validate_urban_goals
from aero_bench.tasks.urban_recovery_demo.integration import (
    UrbanRecoveryResolutionError, _validate_task_requirements,
)
from tests.test_urban_recovery_inputs import _inputs
from tests.test_urban_recovery_verifier import _config_document, _patch_sparse_pipeline, _report_run


@pytest.mark.parametrize("field,value", [
    ("metric_id", "urban.unsupported.score"), ("goal_id", "goal.unsupported"),
    ("verifier_id", "foreign.verifier"), ("operator", "ge"), ("threshold", 0.0),
    ("evidence", "artifact"), ("parameters", [{"name": "bypass", "value": True}]),
])
def test_verifier_cannot_assign_a_boolean_to_arbitrary_goal_semantics(tmp_path, monkeypatch, field, value):
    run = _patch_sparse_pipeline(monkeypatch)
    document = run.task.goals[0].model_dump(mode="json")
    document[field] = value
    run.task.goals = (GoalSpec.model_validate(document), run.task.goals[1])
    report = urban.verify_urban_recovery_sealed(
        bundle_root=tmp_path, sealed_root=tmp_path, run=run, seal=None,
        config=urban.UrbanRecoveryVerifierConfig.model_validate(_config_document()),
    )
    assert report.status == "invalid" and not report.coverage_complete
    assert all(goal.metrics == () and goal.failure_class == "urban.authority.goals_invalid" for goal in report.goals)


@pytest.mark.parametrize("goals", [lambda items: items[:1], lambda items: items[::-1], lambda items: (*items, items[0])])
def test_goal_inventory_must_include_both_distinct_measurements(goals):
    task = SimpleNamespace(goals=goals(urban_goals("urban.recovery.verifier")), verifier=SimpleNamespace(verifier_id="urban.recovery.verifier"))
    with pytest.raises(ValueError, match="exact supported"):
        validate_urban_goals(task)


def test_builder_and_resolver_require_both_horizon_and_mission_goals(tmp_path):
    _, task, agents = _inputs(tmp_path)
    validate_urban_goals(task)
    _validate_task_requirements(task=task, agents=agents)
    incomplete = task.model_copy(update={"goals": task.goals[:1]})
    with pytest.raises(UrbanRecoveryResolutionError, match="exact supported"):
        _validate_task_requirements(task=incomplete, agents=agents)


def test_early_mission_failure_does_not_claim_later_authority_was_validated(tmp_path, monkeypatch):
    run = _patch_sparse_pipeline(monkeypatch)
    horizon = SimulationTime(tick=3000, sim_time_ns=600_000_000_000)
    monkeypatch.setattr(urban, "_load_ledger", lambda *args: SimpleNamespace(records=(SimpleNamespace(event=SimpleNamespace(time=horizon)),)))
    monkeypatch.setattr(urban, "_validate_physics", lambda *args: None)

    def failed_command(*args):
        raise urban._MissionFailure("urban.mission.flight_command_failed", "fixture failure")

    def must_not_run(*args):
        pytest.fail("later authority is not reached after a physical command failure")

    monkeypatch.setattr(urban, "_validate_physical_commands", failed_command)
    monkeypatch.setattr(urban, "_validate_airspace", must_not_run)
    report = urban.verify_urban_recovery_sealed(
        bundle_root=tmp_path, sealed_root=tmp_path, run=run, seal=None,
        config=urban.UrbanRecoveryVerifierConfig.model_validate(_config_document()),
    )
    assert report.status == "failed" and report.coverage_complete is False
    assert [(goal.passed, goal.metrics[0].value) for goal in report.goals] == [(True, 1.0), (False, 0.0)]
    assert report.goals[0].failure_class is None
    assert report.goals[1].failure_class == "urban.mission.flight_command_failed"


def test_report_cannot_claim_pass_with_empty_evidence_or_incomplete_coverage():
    from aero_bench.artifacts.contracts import EvidenceReference

    for evidence, coverage in (((), True), ((EvidenceReference(artifact_id="artifact.event-log", selector="runtime-chain-root"),), False)):
        with pytest.raises(ValueError, match="complete authority"):
            urban._measured_report(
                _report_run(), horizon=SimulationTime(tick=3000, sim_time_ns=600_000_000_000),
                mission_passed=True, failure_class=None, evidence=evidence, coverage_complete=coverage,
            )
