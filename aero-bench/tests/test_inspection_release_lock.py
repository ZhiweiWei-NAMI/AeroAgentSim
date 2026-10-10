from __future__ import annotations

import pytest

from tools.build_inspection_release_lock import _runtime_specs


def _workload(component_id: str) -> dict[str, object]:
    return {
        "runtime": {"image": "registry.test/image@sha256:" + "a" * 64},
        "implementation": {"component_id": component_id},
    }


def _resolved_run() -> dict[str, object]:
    return {
        "environment": {"harness": _workload("aero-bench.harness"), "providers": []},
        "task": {"verifier": {"workload": _workload("inspection.verifier")}},
        "agents": [
            {
                "agent_id": "participant.agent",
                "workload": _workload("participant.agent"),
                "driver": {
                    "driver_id": "astra.driver",
                    "workload": _workload("astra.driver"),
                },
            }
        ],
    }


def test_runtime_specs_include_nested_agent_driver_workload() -> None:
    assert _runtime_specs(_resolved_run()) == [
        ("harness", _workload("aero-bench.harness")),
        ("verifier", _workload("inspection.verifier")),
        ("agent:participant.agent", _workload("participant.agent")),
        ("agent_driver:astra.driver", _workload("astra.driver")),
    ]


def test_runtime_specs_reject_malformed_nested_driver() -> None:
    run = _resolved_run()
    run["agents"][0]["driver"] = {"driver_id": "astra.driver"}
    with pytest.raises(ValueError, match="driver workload"):
        _runtime_specs(run)
