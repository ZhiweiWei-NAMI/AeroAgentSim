"""Differential check of the authored gte/entered subset against native AeroGraph JS."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from aerokernel.values import thaw

from aeroagentsim.scenario import load_scenario


def test_native_expanded_positive_negative_missing_first_true(
    document: dict[str, Any],
) -> None:
    scenario = load_scenario(document)
    config = scenario.engines["threshold"]["config"]
    reference = config["native_reference"]
    assert (
        hashlib.sha256(Path(reference["path"]).read_bytes()).hexdigest()
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
    payload = {"data": data, "inputs": inputs}
    script = "const fs=require('fs'); const {AeroGraphExpandedRuntime:R}=require('/mnt/data2/weizhiwei/AeroGraph/semantic-directory/src/expanded_runtime.js'); const p=JSON.parse(fs.readFileSync(0,'utf8')); const r=new R(p.data); console.log(JSON.stringify(p.inputs.map(x=>r.evaluate('entered',x))));"
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    rows = json.loads(result.stdout)
    assert rows[1]["status"] == "known" and rows[1]["value"] is True, rows
    assert all(
        row["value"] is not True for index, row in enumerate(rows) if index != 1
    ), rows
