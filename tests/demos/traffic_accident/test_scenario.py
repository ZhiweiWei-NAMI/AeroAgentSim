"""Loader/schema coverage without claiming the not-yet-integrated A executor."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

from aeroagentsim.packs.traffic_accident.profiles import validate_profile
from aeroagentsim.scenario import ScenarioError, load_scenario
from tests.demos.conftest import ROOT, SCENARIO, document, inputs, q6


def test_existing_loader_overlay_entities_and_ownership() -> None:
    d = document()
    d.pop("behaviours")
    scenario = load_scenario(d, base=SCENARIO)
    assert len(scenario.manifest.entities) == 219
    assert (
        sum(r.type_id == "aas:TrafficRoadVehicle" for r in scenario.manifest.entities)
        == 63
    )
    assert sum(r.type_id == "aas:TrafficUAV" for r in scenario.manifest.entities) == 8
    overlay = yaml.safe_load((SCENARIO / "registry.overlay.yaml").read_text())
    for key in ("types", "fields", "messages", "relations"):
        assert d["registry"][key] == overlay[key]
    assert all(f["metadata"]["reviewStatus"] == "proposed" for f in overlay["fields"])
    edge = next(e for e in d["entities"] if e["id"] == "edge.coordinator")
    assert not any("position" in field for field in edge["facts"])
    assert "traffic.edge.winner" not in edge["facts"]


def test_a_hook_is_explicitly_pending() -> None:
    with pytest.raises(ScenarioError, match="unknown keys.*behaviours"):
        load_scenario(SCENARIO / "scenario.yaml")


def test_behaviour_package_runtime5_structure_and_q6_ast() -> None:
    p = yaml.safe_load((SCENARIO / "behaviours.yaml").read_text())
    assert p["format"] == "aeroagentsim.behaviour-package/v1"
    assert {
        "id",
        "revision",
        "registry",
        "evaluator",
        "budgets",
        "predicates",
        "chains",
        "bindings",
        "conflicts",
        "injection_points",
    } <= p.keys()
    assert all(type(v) is int and v > 0 for v in p["budgets"].values())
    allowed_actions = {
        "set",
        "emit",
        "command",
        "cancel_command",
        "delay",
        "cancel_timer",
        "assert_relation",
        "close_relation",
        "create_entity",
        "remove_entity",
        "complete",
    }
    for chain in p["chains"].values():
        assert {
            "roles",
            "trigger",
            "preconditions",
            "initial",
            "terminal",
            "states",
            "transitions",
        } <= chain.keys()
        assert chain["initial"] in chain["states"] and set(chain["terminal"]) <= set(
            chain["states"]
        )
        assert len({t["id"] for t in chain["transitions"]}) == len(chain["transitions"])
        for t in chain["transitions"]:
            assert t["from"] in chain["states"] and t["to"] in chain["states"]
            assert {"id", "from", "on", "to", "actions", "priority"} <= t.keys()
            assert type(t["priority"]) is int
            assert len({a["id"] for a in t["actions"]}) == len(t["actions"])
            for a in t["actions"]:
                assert a["kind"] in allowed_actions
                if a["kind"] == "set":
                    assert not a["field"].startswith(
                        ("traffic.road.", "traffic.uav.", "he.aircraft.")
                    )
                if a["kind"] == "command":
                    assert a["deadline_ns"] > 0 and isinstance(a["payload"], dict)
    for b in p["bindings"]:
        assert b["chain"] in p["chains"]
        assert b["multiplicity"] in {
            "once_per_entity",
            "once_per_relation",
            "once_per_task_episode",
        }
        assert b["on_unbind"] in {"retain_until_terminal", "close_after_cleanup"}
    reference = document()["behaviours"][0]
    assert (
        hashlib.sha256((SCENARIO / reference["path"]).read_bytes()).hexdigest()
        == reference["sha256"]
    )
    module = q6()
    assert module.__file__ is not None
    assert (
        hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        == p["evaluator"]["source_sha256"]
    )
    for identity, definition in p["predicates"].items():
        module.validate_ast(definition["expression"], identity)


@pytest.mark.parametrize("profile", ["kinematic", "sumo", "px4"])
def test_replacement_configuration_capability_contract(profile: str) -> None:
    d = yaml.safe_load((SCENARIO / "profiles" / (profile + ".yaml")).read_text())
    required = validate_profile(d)
    assert bool(required) == (profile != "kinematic")
    if profile != "kinematic":
        assert d["readiness"] == "configuration_contract_only" and d["unverified"]


def test_importer_is_deterministic_and_missing_demand_fails(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "traffic_import", ROOT / "tools/demos/import_traffic_accident.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Bounded licence-clear authored synthetic input for importer error/transform
    # tests. This is explicitly a test fixture, never the shipped source scenario.
    import json

    source = tmp_path / "source"
    (source / "web/assets").mkdir(parents=True)
    scene: dict[str, Any] = {
        "routes": [
            {
                "id": "vehicle.001",
                "points": [[0, 0], [200, 0]],
                "speed": 6.5,
                "offset_m": 70.0,
            }
        ],
        "incident": {
            "points": [[0, 0], [200, 0]],
            "lane_id": "own",
            "adjacent_lane": {"id": "other", "points": [[0, 3.2], [200, 3.2]]},
            "distance_m": 102.0,
            "time_s": 8.0,
            "position": {"x": 102.0, "y": 0.0, "z": 0.0},
        },
        "bounds": [0, 200, 0, 10],
        "buildings": [],
        "roads": {"asphalt": []},
    }
    config = {
        "background_cars": 1,
        "background_uavs": 0,
        "tick_hz": 15,
        "uav_speed_mps": 8.0,
        "capture_dwell_s": 3.0,
        "region_radius_m": 400.0,
    }
    (source / "web/assets/scene.json").write_text(json.dumps(scene))
    (source / "config.json").write_text(json.dumps(config))
    (source / "runtime.py").write_text("# explicit authored test fixture\n")
    (source / "web/assets/NOTICE.txt").write_text("Test author-owned geometry\n")
    module.import_demo(source, tmp_path / "a")
    module.import_demo(source, tmp_path / "b")
    assert (tmp_path / "a/inputs.json").read_bytes() == (
        tmp_path / "b/inputs.json"
    ).read_bytes()
    config["background_cars"] = 2
    (source / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="explicit exported demand"):
        module.import_demo(source, tmp_path / "c")
    assert inputs()["frame"]["geodetic_anchor"] is None
