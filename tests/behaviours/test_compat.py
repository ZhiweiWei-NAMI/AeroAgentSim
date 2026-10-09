"""Legacy compat executors: class identity, lazy alias modules, and pinned runs."""

from __future__ import annotations

import copy
import importlib
import json
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from aeroagentsim.behaviours.compat import (
    LegacyThresholdExecutor,
    LegacyWorkflowExecutor,
    compile_threshold,
    compile_threshold_digest,
    compile_workflow,
    compile_workflow_digest,
)
from aeroagentsim.engines.behaviour import (
    build_legacy_threshold,
    build_legacy_workflow,
)
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario


@pytest.fixture(scope="module")
def document() -> Iterator[dict[str, Any]]:
    """The pinned p1-slice document, exactly as the platform review suites use it."""
    scenario = load_scenario(Path("scenarios/p1-slice.yaml"))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "snapshot.json"
        scenario.compiled.write_snapshot(path)
        doc = copy.deepcopy(scenario.document)
        doc["registry"].pop("compile")
        doc["registry"]["snapshot"] = str(path)
        doc["registry"]["digest"] = scenario.compiled.digest
        yield doc


def test_alias_modules_expose_lazy_factories() -> None:
    workflow = importlib.import_module("aeroagentsim.engines.workflow")
    threshold = importlib.import_module("aeroagentsim.engines.threshold")
    importlib.import_module("aeroagentsim.behaviours.compat")
    assert workflow.Workflow is build_legacy_workflow
    assert threshold.Threshold is build_legacy_threshold
    assert workflow.build is build_legacy_workflow
    assert threshold.build is build_legacy_threshold


def test_alias_modules_do_not_import_compat_at_module_import() -> None:
    compat = sys.modules.get("aeroagentsim.behaviours.compat")
    sys.modules.pop("aeroagentsim.engines.workflow", None)
    if compat is not None:
        sys.modules.pop("aeroagentsim.behaviours.compat", None)
    from aeroagentsim.engines import workflow

    # The alias module is loaded; the compat module stays lazy until invoked.
    assert "aeroagentsim.behaviours.compat" not in sys.modules
    assert callable(workflow.build)
    # Restore the evicted module object so class identity stays stable.
    if compat is not None:
        sys.modules["aeroagentsim.behaviours.compat"] = compat


def test_compile_workflow_describes_machines_verbatim() -> None:
    config = {
        "produces": ["aas.p1.order_state"],
        "consumes": ["aas.p1.energy_j"],
        "emits": [],
        "subscribes": [],
        "targets": [],
        "lifecycle": True,
        "machines": [
            {
                "entity": "order-1",
                "field": "aas.p1.order_state",
                "initial": "submitted",
                "states": {
                    "submitted": {
                        "on_enter": [
                            {
                                "kind": "set",
                                "entity": "uav-1",
                                "field": "aas.p1.x",
                                "value": 0,
                            }
                        ],
                        "transitions": [
                            {"to": "executing", "receipt": "accepted"},
                            {
                                "to": "done",
                                "predicate": {
                                    "entity": "uav-1",
                                    "field": "aas.p1.energy_j",
                                    "op": "gte",
                                    "value": 10.0,
                                },
                            },
                        ],
                    },
                    "executing": {},
                    "done": {},
                },
            }
        ],
    }
    ir = compile_workflow(config)
    assert ir["kind"] == "legacy_workflow/v1"
    assert ir["machines"][0]["initial"] == "submitted"
    transitions = ir["machines"][0]["states"][0]["transitions"]
    assert transitions[0] == {
        "to": "executing",
        "trigger": "receipt",
        "comparator": None,
    }
    assert transitions[1]["trigger"] == "predicate"
    assert transitions[1]["comparator"] == "gte"


def test_compile_threshold_describes_comparator_verbatim() -> None:
    config = {
        "context": "sample",
        "event": "aas.p1.entered",
        "topic": "records",
        "parameters": {"limit": 5.0},
        "native_reference": {"path": "x", "sha256": "y"},
        "ast": {
            "op": "gte",
            "args": [
                {
                    "op": "field",
                    "role": "subject",
                    "field": "aas.p1.energy_j",
                    "path": [0],
                },
                {"op": "parameter", "parameter": "limit"},
            ],
        },
    }
    ir = compile_threshold(config)
    assert ir["kind"] == "legacy_threshold/v1"
    assert ir["comparator"] == "gte"
    assert ir["op"] == "gte"
    assert ir["selector"] == "aas.p1.energy_j"
    assert ir["path"] == [0]
    assert ir["parameter"] == "limit"


def test_compile_threshold_requires_declared_keys() -> None:
    with pytest.raises(ValueError, match="threshold.config: missing required key"):
        compile_threshold({"ast": {"op": "gte", "args": []}})


def test_compile_workflow_requires_declared_keys() -> None:
    with pytest.raises(ValueError, match="workflow.config: missing required key"):
        compile_workflow({"produces": []})


def test_workflow_ir_json_round_trip() -> None:
    config = {
        "produces": ["f"],
        "consumes": [],
        "emits": [],
        "subscribes": [],
        "targets": [],
        "lifecycle": False,
        "machines": [],
    }
    assert json.loads(json.dumps(compile_workflow(config))) == compile_workflow(config)


def test_threshold_ir_json_round_trip() -> None:
    config = {
        "context": "c",
        "event": "e",
        "topic": "t",
        "parameters": {},
        "native_reference": {"path": "p", "sha256": "s"},
        "ast": {
            "op": "exists",
            "args": [{"op": "relation", "role": "subject", "relation": "r"}],
        },
    }
    ir = compile_threshold(config)
    assert json.loads(json.dumps(ir)) == ir
    assert ir["comparator"] == "exists"


def test_ir_digests_are_stable_and_content_bound() -> None:
    config = {
        "produces": ["f"],
        "consumes": [],
        "emits": [],
        "subscribes": [],
        "targets": [],
        "lifecycle": False,
        "machines": [
            {
                "entity": "order-1",
                "field": "f",
                "initial": "s0",
                "states": {
                    "s0": {
                        "transitions": [
                            {
                                "to": "s1",
                                "predicate": {
                                    "entity": "e",
                                    "field": "g",
                                    "op": "lte",
                                    "value": 1,
                                },
                            }
                        ]
                    },
                    "s1": {},
                },
            }
        ],
    }
    first = compile_workflow(config)
    assert (
        first["digest"]
        == compile_workflow_digest(config)
        == compile_workflow(config)["digest"]
    )
    changed = cast(dict[str, Any], copy.deepcopy(config))
    predicate = cast(
        dict[str, Any],
        changed["machines"][0]["states"]["s0"]["transitions"][0]["predicate"],
    )
    predicate["op"] = "gte"
    assert compile_workflow_digest(changed) != first["digest"]
    threshold_config = {
        "context": "c",
        "event": "e",
        "topic": "t",
        "parameters": {"k": 1},
        "native_reference": {"path": "p", "sha256": "s"},
        "ast": {
            "op": "gte",
            "args": [
                {"op": "field", "role": "subject", "field": "x"},
                {"op": "parameter", "parameter": "k"},
            ],
        },
    }
    threshold_ir = compile_threshold(threshold_config)
    assert (
        threshold_ir["digest"]
        == compile_threshold_digest(threshold_config)
        == compile_threshold(threshold_config)["digest"]
    )
    assert compile_threshold_digest(threshold_config) != compile_workflow_digest(config)


def test_pinned_replay_uses_moved_threshold_executor(
    document: dict[str, Any],
) -> None:
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        simulation.run_until(500_000_000)
        engine = simulation.kernel._engines["threshold"]
        assert isinstance(engine, LegacyThresholdExecutor)
    finally:
        simulation.close()


def test_pinned_replay_uses_moved_workflow_executor(
    document: dict[str, Any],
) -> None:
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        simulation.run_until(500_000_000)
        engines = [
            engine
            for engine in simulation.kernel._engines.values()
            if isinstance(engine, LegacyWorkflowExecutor)
        ]
        assert engines
    finally:
        simulation.close()


def test_pinned_document_is_unchanged_by_compilers(document: dict[str, Any]) -> None:
    before = copy.deepcopy(document)
    threshold_cfg = document["engines"]["threshold"]["config"]
    workflow_cfgs = [
        engine["config"]
        for engine in document["engines"].values()
        if engine["plugin"] == "workflow"
    ]
    assert compile_threshold(threshold_cfg)["comparator"] == threshold_cfg["ast"]["op"]
    for cfg in workflow_cfgs:
        assert compile_workflow(cfg)["kind"] == "legacy_workflow/v1"
    assert document == before
