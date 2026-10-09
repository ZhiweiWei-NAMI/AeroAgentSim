"""Pinned evaluator policy/dwell counterexamples, independent of model success."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
import yaml

from tests.demos.conftest import SCENARIO, q6


def definitions() -> dict[str, Any]:
    return dict(
        yaml.safe_load((SCENARIO / "predicates.yaml").read_text())["predicates"]
    )


def evaluate(identity: str, fields: dict[str, Any]) -> Any:
    p = definitions()["traffic.p." + identity]
    return q6().evaluate(
        p["expression"],
        [{"t": 0, "fields": fields, "parameters": p["parameters"]}],
        "original",
    )


def test_accepting_model_cannot_interrupt_medical_route() -> None:
    task = {
        "traffic.task.kind": "medical_delivery",
        "traffic.task.phase": "executing",
        "traffic.task.interruptible": False,
    }
    assert evaluate("medical_locked", {"task": task}) is True
    fields: dict[str, Any] = {
        "task": task,
        "bid": {
            "traffic.bid.decision_status": "succeeded",
            "traffic.bid.recommendation": "accept",
        },
        "assessment": {
            "traffic.assessment.in_region": True,
            "traffic.assessment.feasible": True,
        },
        "capture_task": {"traffic.task.phase": "inactive"},
    }
    assert evaluate("bid_eligible", fields) is False
    fields["task"]["traffic.task.interruptible"] = True
    assert evaluate("bid_eligible", fields) is True


@pytest.mark.parametrize("slot", ["in_region", "feasible"])
def test_known_disqualification_and_missing_inputs(slot: str) -> None:
    f: dict[str, Any] = {
        "task": {"traffic.task.interruptible": True},
        "bid": {
            "traffic.bid.decision_status": "succeeded",
            "traffic.bid.recommendation": "accept",
        },
        "assessment": {
            "traffic.assessment.in_region": True,
            "traffic.assessment.feasible": True,
        },
        "capture_task": {"traffic.task.phase": "inactive"},
    }
    f["assessment"]["traffic.assessment." + slot] = False
    assert evaluate("bid_eligible", f) is False
    del f["assessment"]["traffic.assessment." + slot]
    assert evaluate("bid_eligible", f) is None


def test_explicit_scripted_fixtures_and_eligible_award_order() -> None:
    fixture = json.loads((SCENARIO / "fixtures/decisions.json").read_text())
    assert fixture["mode"] == "scripted"
    rows = fixture["responses"]
    assert rows["uav_alpha"][0]["accept"] is False
    assert rows["uav_bravo"][0]["accept"] is True
    base = {
        "bid": {
            "traffic.bid.decision_status": "succeeded",
            "traffic.bid.recommendation": "accept",
        },
        "assessment": {
            "traffic.assessment.in_region": True,
            "traffic.assessment.feasible": True,
        },
        "capture_task": {"traffic.task.phase": "inactive"},
    }
    candidates = []
    for identity, eta, interruptible in (
        ("uav.alpha", 1.0, False),
        ("uav.bravo", 20.0, True),
    ):
        fields: dict[str, Any] = {
            **copy.deepcopy(base),
            "task": {"traffic.task.interruptible": interruptible},
        }
        if evaluate("bid_eligible", fields) is True:
            candidates.append((eta, identity))
    assert min(candidates)[1] == rows["edge_award"][0]["winner_id"] == "uav.bravo"


def test_sampled_hold_requires_full_coverage_and_resets() -> None:
    p = definitions()["traffic.p.dwell_ready"]
    frames = [
        {
            "t": i * 100_000_000,
            "fields": {
                "pose": {"he.aircraft.position_enu_m": [10.0, 20.0, 70.0]},
                "velocity": {"traffic.uav.velocity_enu_mps": [0.0, 0.0, 0.0]},
                "task": {"traffic.task.target_enu_m": [10.0, 20.0, 70.0]},
            },
            "parameters": p["parameters"],
        }
        for i in range(31)
    ]
    engine = q6()
    assert engine.evaluate(p["expression"], frames[:10], "original") is None
    assert engine.evaluate(p["expression"], frames, "original") is True
    broken = copy.deepcopy(frames)
    del broken[15]["fields"]["pose"]["he.aircraft.position_enu_m"]
    assert engine.evaluate(p["expression"], broken, "original") is None
    assert (
        engine.evaluate(p["expression"], frames[:1] + frames[30:], "original") is None
    )
