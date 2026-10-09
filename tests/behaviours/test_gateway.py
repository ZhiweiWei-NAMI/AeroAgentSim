"""The service checks real compiled injection declarations before admission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.worker import validate_injection


def request() -> dict[str, Any]:
    return {
        "schema": "aas.runtime.inject_event",
        "engine": "behaviour",
        "target": "behaviour",
        "at_ns": 5,
        "stream_id": "operator",
        "source_stamp": {
            "clock_id": "canonical",
            "mapping_id": "canonical",
            "numerator": 5,
            "denominator": 1,
        },
        "payload": {"injection_point": "interrupt", "payload": {"reason": "test"}},
    }


@pytest.mark.parametrize(
    "field,replacement,message",
    [
        ("stream_id", "camera", "stream"),
        ("target", "physics", "target"),
        ("engine", "physics", "target"),
        ("point", "unknown", "injection_point"),
        ("payload", {}, "VALUE_RECORD"),
    ],
)
def test_rejects_wrong_manifest_binding(
    tmp_path: Path, field: str, replacement: Any, message: str
) -> None:
    session = RunSession(
        load_scenario("scenarios/behaviours/minimal.yaml"), tmp_path / "run"
    )
    body = request()
    if field == "point":
        body["payload"]["injection_point"] = replacement
    elif field == "payload":
        body["payload"]["payload"] = replacement
    else:
        body[field] = replacement
    with pytest.raises(ValueError, match=message):
        validate_injection(session, body)
    assert not session.started
    session.close()


def test_valid_injection_does_not_execute_or_mutate_run(tmp_path: Path) -> None:
    session = RunSession(
        load_scenario("scenarios/behaviours/minimal.yaml"), tmp_path / "run"
    )
    before = session.simulation.kernel.records
    validate_injection(session, request())
    assert session.simulation.kernel.records == before
    assert not session.started
    session.close()


def test_http_injection_uses_existing_named_ingress_route(tmp_path: Path) -> None:
    import time

    import yaml
    from fastapi.testclient import TestClient

    from aeroagentsim.scenario.loader import UniqueLoader
    from aeroagentsim.services.app import create_app

    base = Path("scenarios/behaviours").resolve()
    doc = yaml.load((base / "minimal.yaml").read_text(), Loader=UniqueLoader)
    doc["bindings"]["commands"] = []
    doc["ingress_streams"][0]["initial_watermark_ns"] = 0
    doc["ingress_streams"][0]["timeout_s"] = 5
    doc["registry"]["snapshot"] = str(base / "registry.snapshot.json")
    with TestClient(create_app(tmp_path / "runs")) as client:
        created = client.post("/v1/runs", json=doc)
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        bad = request()
        bad["payload"]["injection_point"] = "absent"
        rejected = client.post(f"/v1/runs/{run_id}/ingress", json=bad)
        assert rejected.status_code == 422
        accepted = client.post(f"/v1/runs/{run_id}/ingress", json=request())
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["stream_id"] == "operator"
        closed = client.post(
            f"/v1/runs/{run_id}/watermark",
            json={"stream_id": "operator", "watermark_ns": 20},
        )
        assert closed.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            runs = client.get("/v1/runs").json()
            result = next(item for item in runs if item["id"] == run_id)
            if result["status"] in {"completed", "faulted"}:
                break
            time.sleep(0.02)
        assert result["status"] == "completed", result
