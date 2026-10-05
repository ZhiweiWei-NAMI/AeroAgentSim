"""Unit-only inputs for the report gate; these are not simulator evidence."""
import json

import pytest

from tools.summarize_inspection_flight import inspect


def _inputs():
    configs = {}
    events = []
    states = [{"at": {"tick": tick}, "samples": []} for tick in range(1, 9)]
    for vehicle in ("01", "02"):
        agent = f"uav.policy.{vehicle}"
        entity = f"uav.{vehicle}"
        configs[agent] = {
            "mission_mode": "inspection", "final_tick": 8, "vehicle_id": entity,
            "patrol_enu_m": {"x": 10, "y": 0}, "launch_enu_m": {"x": 0, "y": 0},
            "cruise_agl_m": 20,
        }
        actual = {
            "mission_phase": "done", "active_flight_command_id": None,
            "failed_command_count": 0, "mission_failure_count": 0,
            "flight_progression_stopped": False, "airspace_state": "outside",
        }
        events.append({"agent_id": agent, "interaction_type": "agent.decision_summary.v1",
                       "public_payload": [{"name": "decision_summary", "value": "actual_state=" + json.dumps(actual)}]})
        for state in states:
            tick = state["at"]["tick"]
            landed = tick == 8
            state["samples"].append({
                "entity_id": entity, "at": {"tick": tick, "sim_time_ns": tick * 1_000_000_000},
                "pose": {"position": {"enu": {"east_m": 0 if landed else 10, "north_m": 0}, "agl_m": 0 if landed else 20}},
                "attributes": [{"name": name, "value": value} for name, value in {
                    "landed": landed, "in_air": not landed, "ground_contact": landed, "collision_contact": False,
                }.items()],
                "armed": not landed, "mode": "LAND" if landed else "HOLD",
                "sample_digest": str(tick) * 64, "battery": {"remaining_fraction": 0.7},
            })
    return {"execution_scope": "executor_validation", "run_id": "a" * 64,
            "scene_states": states, "events": events, "time": {"tick": 8, "sim_time_ns": 8_000_000_000},
            "terminal": {"kind": "completed"}}, configs


def test_summary_requires_both_policy_and_recorded_physical_completion():
    trace, configs = _inputs()
    result = inspect(trace, configs)
    assert result["inspection_flight_complete"] is True
    assert result["formal_benchmark_pass"] is False
    assert [v["recorded_inspection_dwell_seconds"] for v in result["vehicles"]] == [6, 6]
    trace["scene_states"][-1]["samples"][1]["armed"] = True
    assert inspect(trace, configs)["inspection_flight_complete"] is False


def test_summary_does_not_add_discontinuous_dwell_or_invent_agent_state():
    trace, configs = _inputs()
    trace["scene_states"][3]["samples"][0]["pose"]["position"]["enu"]["east_m"] = 0
    assert inspect(trace, configs)["inspection_flight_complete"] is False
    trace, configs = _inputs()
    trace["events"] = []
    assert inspect(trace, configs)["inspection_flight_complete"] is False


def test_summary_rejects_missing_ticks_and_mismatched_horizon():
    trace, configs = _inputs()
    trace["scene_states"].pop(2)
    with pytest.raises(ValueError, match="coverage"):
        inspect(trace, configs)
    trace, configs = _inputs()
    configs["uav.policy.01"]["final_tick"] = 9
    with pytest.raises(ValueError, match="horizon"):
        inspect(trace, configs)
