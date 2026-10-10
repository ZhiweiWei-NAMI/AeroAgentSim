"""Scene-derived roofs, cruise paths and reproducible public placements."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from aeroagentsim.algorithms.builtins import CorridorAstar
from aeroagentsim.algorithms.protocols import Snapshot, Zone
from aeroagentsim.algorithms.routes import cruise_plan
from aeroagentsim.packs.geometry import segment_hits_box
from aeroagentsim.scenario import load_scenario
from tools.templates.place import SCENE, TEMPLATES, inside, place_document, placements

ROOT = Path(__file__).resolve().parents[2]


def test_huangpu_roofs_field_labels_and_reproducibility() -> None:
    scene = json.loads(SCENE.read_text())
    layout = placements(scene)
    assert layout["cruise"] > max(b["height_m"] for b in scene["buildings"])
    for identity in TEMPLATES:
        path = ROOT / "scenarios/templates" / identity / "scenario.yaml"
        document = yaml.safe_load(path.read_text())
        assert document == place_document(document, layout)
        scenario = load_scenario(path)
        for entity in scenario.document["entities"]:
            # Runtime-generated internal behaviour instances have no authored
            # display contract; every template-authored entity does.
            if entity["id"].startswith("behaviour:"):
                continue
            assert entity["label"]
            assert entity["business"]
            facts = entity["facts"]
            if "lowalt.vehicle_class" in facts:
                assert entity["vehicle_class"] == facts["lowalt.vehicle_class"]
            if "lowalt.site_position" not in facts:
                continue
            point = facts["lowalt.site_position"]
            roofs = [b for b in scene["buildings"] if inside(point, b["footprint"])]
            if roofs:
                assert point[2] == max(b["height_m"] for b in roofs)
            elif entity["type"] in {
                "lowalt:ChargingPad",
                "lowalt:Vertiport",
                "lowalt:Field",
            }:
                assert point[2] == 0
    lo, hi = layout["field_lower"], layout["field_upper"]
    for b in scene["buildings"]:
        assert max(p[1] for p in b["footprint"]) < lo[1]
    assert hi[0] > lo[0] and hi[1] > lo[1]


def test_cruise_vertical_roof_departure_and_no_fly_avoidance() -> None:
    # A 105 m roof; a restricted incident still reaches above 125 m cruise.
    roof = Zone("roof", "restricted", (-10, -10, 0), (10, 10, 105), True)
    incident = Zone("incident", "no_fly", (35, -5, 0), (55, 5, 180), True)
    state = Snapshot(0, (), (), (roof, incident), (), (0, 0, 0))
    start, goal = (0.0, 0.0, 105.0), (90.0, 0.0, 12.0)
    route = cruise_plan(CorridorAstar(), state, start, goal, 125.0)
    assert route[0] == (0.0, 0.0, 125.0)
    assert route[-2] == (90.0, 0.0, 125.0)
    assert route[-1] == goal
    for a, b in zip((start,) + route, route):
        assert not segment_hits_box(a, b, incident.lower, incident.upper)
        if a[:2] != b[:2]:
            assert min(a[2], b[2]) >= 125.0
