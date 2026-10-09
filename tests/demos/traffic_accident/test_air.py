"""Existing kinematic ownership and shared-model feasibility over measured poses."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from aerokernel import CommandRequest, Instant, Journal
from aerokernel.state import Fact
from aerokernel.values import thaw

from aeroagentsim.models import motion_model
from aeroagentsim.packs.common import finite, vec
from aeroagentsim.packs.traffic_accident.traffic_assessment import metrics
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from tests.demos.conftest import SCENARIO, domain_document, inputs, ref


def test_kinematic_air_trace_assessment_and_deviation(tmp_path: Path) -> None:
    d = domain_document(air=("uav.alpha", "uav.bravo"), assessment=True)
    initial = inputs()["air_actors"][0]
    target = [float(x) for x in initial["points_enu_m"][1]]
    d["bindings"]["commands"] = [
        {
            "schema": "traffic.air.move_to",
            "target": "air_motion",
            "at_ns": 0,
            "payload": {"entity": ref("uav.alpha", "aas:TrafficUAV"), "target": target},
        }
    ]
    traces = []
    for iteration in range(2):
        sim = Simulation(
            load_scenario(d, base=SCENARIO),
            journal=Journal(tmp_path / f"air-{iteration}.jsonl"),
        )
        try:
            sim.start()
            view = sim.run_until(1_000_000_005)
            refs = {r.id: r for r in sim.scenario.manifest.entities}
            fact = view.field(
                (refs["uav.alpha"], "he.aircraft.position_enu_m"), view.instant
            )
            assert isinstance(fact, Fact)
            position = list(vec(thaw(fact.value)))
            moved = math.dist(position, initial["points_enu_m"][0])
            assert moved == pytest.approx(2.00000002)
            traces.append(position)
            for suffix in ("alpha", "bravo"):
                record = refs["assessment.01." + suffix]
                budget = view.field(
                    (record, "traffic.assessment.budget_j"), view.instant
                )
                eligible = view.field(
                    (record, "traffic.assessment.feasible"), view.instant
                )
                evidence = view.field(
                    (record, "traffic.assessment.evidence"), view.instant
                )
                assert (
                    isinstance(budget, Fact)
                    and finite(thaw(budget.value), "budget") > 0
                )
                assert isinstance(eligible, Fact) and thaw(eligible.value) is True
                assert isinstance(evidence, Fact)
                evidence_data = thaw(evidence.value)
            assert isinstance(evidence_data, dict)
            sources = evidence_data["sources"]
            assert isinstance(sources, list) and len(sources) == 3
            legacy = json.loads((SCENARIO / "fixtures/legacy-trace.json").read_text())
            last = next(s for s in legacy["samples"] if s["time_s"] == 1.0)
            deviation = math.dist(position, last["air"]["uav.alpha"]["position_enu_m"])
            assert deviation == pytest.approx(5.0, abs=0.01)
        finally:
            sim.close()
    assert traces[0] == traces[1]
    assert (tmp_path / "air-0.jsonl").read_bytes() == (
        tmp_path / "air-1.jsonl"
    ).read_bytes()


def test_insufficient_energy_is_known_false() -> None:
    d = domain_document(air=("uav.bravo",), assessment=True)
    next(e for e in d["entities"] if e["id"] == "uav.bravo")["facts"][
        "traffic.uav.energy_j"
    ] = 500.0
    sim = Simulation(load_scenario(d, base=SCENARIO))
    try:
        sim.start()
        sim.run_until(66_666_667)
        record = next(
            r for r in sim.scenario.manifest.entities if r.id == "assessment.01.bravo"
        )
        view = sim.kernel.view()
        fact = view.field((record, "traffic.assessment.feasible"), view.instant)
        assert isinstance(fact, Fact) and thaw(fact.value) is False
    finally:
        sim.close()


def test_assessment_vs_recorded_model_input_distance_eta(tmp_path: Path) -> None:
    """Same actual historical pose; old rounded ETA vs the new trajectory model."""
    d = domain_document(air=("uav.alpha", "uav.bravo"), assessment=True)
    scenario = load_scenario(d, base=SCENARIO)
    model = motion_model(
        d["engines"]["air_motion"]["config"],
        scenario.registry,
        scenario.manifest.entities,
    )
    recorded = json.loads((SCENARIO / "fixtures/legacy-assessment.json").read_text())
    measurements = []
    for row in recorded["rows"]:
        anchor = vec(row["incident_enu_m"])
        target = (anchor[0], anchor[1], 70.0)
        segment, in_region, _available = metrics(
            vec(row["position_enu_m"]),
            target,
            anchor,
            400.0,
            100_000.0,
            10_000.0,
            model,
        )
        distance_error = segment.distance_m - row["distance_3d_m"]
        eta_delta = segment.elapsed_s - row["eta_s"]
        assert abs(distance_error) < 0.05 and in_region
        # Source ETA is rounded to 0.1 s and new ETA is rounded UP to a
        # publication grid. Account explicitly for both quantizations.
        assert 2.0 - 0.05 <= eta_delta <= 2.0 + 0.05 + model.step_ns / 1e9
        measurements.append(
            {
                "actor": row["actor"],
                "distance_error_m": distance_error,
                "eta_delta_s": eta_delta,
            }
        )
    (tmp_path / "assessment-deviations.json").write_text(
        json.dumps(measurements, indent=2) + "\n"
    )


def test_assessment_link_command_actual_subject_and_receipt() -> None:
    d = domain_document(air=("uav.bravo",), assessment=True)
    actor = ref("uav.bravo", "aas:TrafficUAV")
    bid = ref("bid.test", "aas:TrafficBid")
    d["entities"].append(
        {
            "id": "bid.test",
            "type": "aas:TrafficBid",
            "facts": {"traffic.bid.actor": actor},
        }
    )
    d["engines"]["bid_inputs"] = {
        "plugin": "environment",
        "config": {
            "produces": ["traffic.bid.actor"],
            "lifecycle": True,
            "profiles": {
                "bid.test": {"traffic.bid.actor": {"mode": "constant", "value": actor}}
            },
        },
    }
    d["bindings"]["rules"].append(
        {
            "writer": "bid_inputs",
            "type": "aas:TrafficBid",
            "fields": ["traffic.bid.actor"],
        }
    )
    d["bindings"]["lifecycle"].append(
        {"controller": "bid_inputs", "type": "aas:TrafficBid"}
    )
    d["bindings"]["relations"] = [
        {
            "writer": "traffic_assessment",
            "type": "aas:TrafficAssessment",
            "relation": "traffic.assessment-bid",
        }
    ]
    sim = Simulation(load_scenario(d, base=SCENARIO))
    try:
        sim.start()
        command = sim.kernel.submit(
            CommandRequest(
                "traffic.assessment.link_bid",
                "traffic_assessment",
                Instant(1),
                {"actor": actor, "bid": bid},
            )
        )
        view = sim.run_until(1)
        assert sim.kernel.action(command).status == "succeeded"
        edges = view.relations("traffic.assessment-bid", view.instant)
        assert len(edges) == 1 and edges[0].target.id == "bid.test"
    finally:
        sim.close()
