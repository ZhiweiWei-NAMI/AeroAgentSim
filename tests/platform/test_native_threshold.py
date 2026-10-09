"""Differential check of the authored gte/entered subset against native AeroGraph JS."""

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any, cast

import pytest
from aerokernel import Interval
from aerokernel.sdk import EngineContext
from aerokernel.values import thaw

from aeroagentsim.platform import Simulation
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.paths import source_path
from aeroagentsim.services.projector import project
from tests.platform.test_sample_missing import RetractionWriter


def test_native_expanded_positive_negative_missing_first_true(
    document: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(document)
    config = scenario.engines["threshold"]["config"]
    reference = config["native_reference"]
    assert (
        hashlib.sha256(source_path(reference["path"]).read_bytes()).hexdigest()
        == reference["sha256"]
    )
    descriptor = scenario.registry.field(config["field"])
    contract = {
        "id": "aas.p1.reached_x",
        "roles": [{"id": "subject", "typeIds": ["oo:UAV"]}],
        "parameters": [
            {
                "id": "thresholdXM",
                "valueSchema": {"type": "number"},
                "unit": {"symbol": "m"},
            }
        ],
        "expression": config["ast"],
    }
    data = {
        "fields": [thaw(descriptor.metadata["raw"])],
        "entities": [
            {"id": t.id, "parent": t.parents[0] if t.parents else None}
            for t in scenario.registry.types
        ],
        "relations": [],
        "contracts": [contract],
        "events": [{"id": "entered", "transitionOf": contract["id"]}],
    }

    def frame(x: float | None, t: int) -> dict[str, Any]:
        states = {} if x is None else {"subject": {config["field"]: [x, 0.0, 0.0]}}
        return {
            "t": t,
            "clock": "canonical",
            "instances": {
                "subject": {
                    "instanceId": "uav-1",
                    "entityTypeId": "oo:UAV",
                    "runId": "p1",
                    "epoch": "0",
                    "generation": 0,
                    "source": "motion",
                    "clock": "canonical",
                    "observedAt": t,
                }
            },
            "states": states,
            "parameters": config["parameters"],
        }

    inputs = [frame(0.0, 0), frame(10.0, 1), frame(None, 2)]
    inputs[1]["history"] = [inputs[0]]
    inputs.append(frame(10.0, 3))

    # Run precisely those four inputs through the actual Python plugin.
    class SharedFrames(RetractionWriter):
        def step(self, ctx: EngineContext) -> None:
            ref = self.build.entities[0]
            if ctx.now.ns == 2_000_000_000:
                ctx.retract(
                    ref, config["field"], Interval(ctx.now, None), "shared unknown"
                )
            else:
                ctx.set(ref, config["field"], [10.0, 0.0, 0.0])

    entity = document["entities"][0]
    entity["facts"] = {config["field"]: [0.0, 0.0, 0.0]}
    document["entities"] = [entity]
    document["engines"] = {
        "motion": {"plugin": "shared-frames", "config": {}},
        "threshold": document["engines"]["threshold"],
    }
    sample = document["bindings"]["samples"][0]
    sample.update(
        upstream=["motion"],
        bindings={entity["id"]: entity["id"]},
        sources={entity["id"]: "motion"},
        clocks={entity["id"]: ["canonical", "canonical"]},
    )
    document["bindings"] = {
        "rules": [
            {"writer": "motion", "type": entity["type"], "fields": [config["field"]]}
        ],
        "lifecycle": [{"controller": "motion", "type": entity["type"]}],
        "samples": [sample],
    }
    original = EngineCatalog.build

    def factory(catalog: EngineCatalog, plugin: str, build: EngineBuild) -> Any:
        return (
            SharedFrames(build)
            if plugin == "shared-frames"
            else original(catalog, plugin, build)
        )

    monkeypatch.setattr(EngineCatalog, "build", factory)
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(3_000_000_000)
        frames = view.sample_frames(sample["context"])
        assert [
            cast(dict[str, Any], f.frame.result)["states"][entity["id"]]["status"]
            for f in frames
        ] == [
            "known",
            "known",
            "required_input",
            "known",
        ]
        python_entered = {
            int(m["at"]["ns"]) // 1_000_000_000
            for r in simulation.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"] == config["event"]
        }
    finally:
        simulation.close()
    for index, item in enumerate(inputs):
        if index:
            item["history"] = [inputs[index - 1]]
    payload = {"data": data, "inputs": inputs}
    script = "const fs=require('fs'); const {AeroGraphExpandedRuntime:R}=require(process.env.AEROAGENTSIM_AEROGRAPH_ROOT+'/semantic-directory/src/expanded_runtime.js'); const p=JSON.parse(fs.readFileSync(0,'utf8')); const r=new R(p.data); console.log(JSON.stringify(p.inputs.map(x=>r.evaluate('entered',x))));"
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    rows = json.loads(result.stdout)
    assert [row["value"] is True for row in rows] == [
        i in python_entered for i in range(len(inputs))
    ]
    assert rows[1]["status"] == "known" and rows[1]["value"] is True, rows
    assert all(
        row["value"] is not True for index, row in enumerate(rows) if index != 1
    ), rows
