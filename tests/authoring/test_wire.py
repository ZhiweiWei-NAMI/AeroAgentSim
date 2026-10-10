"""Studio saves restore declared fact kinds lost by browser JSON serialization."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim.authoring.catalog import SnapshotCatalog
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.app import create_app


def browser_document(document: dict[str, Any]) -> dict[str, Any]:
    """Model JSON.parse/stringify erasing integral floats in these safe-number inputs."""

    def number(token: str) -> int | float:
        value = float(token)
        return int(value) if value.is_integer() else value

    result: dict[str, Any] = json.loads(
        json.dumps(document, allow_nan=False), parse_float=number
    )
    return result


def assert_same_kinds(actual: Any, expected: Any) -> None:
    # Plain equality misses both 1 == 1.0 and False == 0.
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key, value in expected.items():
            assert_same_kinds(actual[key], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for item, value in zip(actual, expected, strict=True):
            assert_same_kinds(item, value)
    else:
        assert actual == expected


@pytest.fixture
def parcel_workspace(tmp_path: Path) -> tuple[WorkspaceStore, dict[str, Any]]:
    store = WorkspaceStore(tmp_path / "drafts", None)
    draft = store.import_template(
        "parcel-delivery", {"fleet_size": 2}, registry=TemplateRegistry()
    )
    return store, draft


def test_parcel_browser_api_save_restores_generated_float_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    app = create_app(tmp_path / "runs", studio_root=tmp_path / "studio")
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/v1/templates/parcel-delivery/workspace", json={"knobs": {"fleet_size": 2}}
        )
        assert response.status_code == 201, response.text
        identifier = response.json()["workspace_id"]
        endpoint = f"/v1/studio/workspaces/{identifier}"
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        authored = response.json()["scenario"]
        fleet = authored["generators"]["fleet"]
        # No explicit UAV imports its field schemas on the generator's behalf.
        assert not any(entity["type"] == fleet["type"] for entity in authored["entities"])
        assert isinstance(app.state.studio.catalog_for(identifier), SnapshotCatalog)

        incoming = browser_document(authored)
        wire_facts = incoming["generators"]["fleet"]["facts"]
        assert type(wire_facts["lowalt.capacity_j"]) is int
        assert all(type(value) is int for value in wire_facts["lowalt.velocity"])
        response = client.post(endpoint, json={"scenario": incoming})
        assert response.status_code == 200, response.text
        saved = response.json()["scenario"]
        assert_same_kinds(saved["generators"]["fleet"]["facts"], fleet["facts"])
        for key in ("count", "prefix", "type", "label", "business", "vehicle_class"):
            assert_same_kinds(saved["generators"]["fleet"][key], fleet[key])
        assert type(saved["run"]["seed"]) is int

        # Verify persisted content, not just the response or a draft file's existence.
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        assert_same_kinds(response.json()["scenario"], saved)
        loaded = load_scenario(saved, base=app.state.studio.directory(identifier))
        generated = [
            ref.id for ref in loaded.manifest.entities if ref.type_id == fleet["type"]
        ]
        assert generated == ["drone-1", "drone-2"]
        for entity_id in generated:
            assert_same_kinds(
                {field: loaded.initial[entity_id][field] for field in fleet["facts"]},
                fleet["facts"],
            )
        response = client.post(endpoint + "/validate", json={})
        assert response.status_code == 200, response.text
        assert response.json()["valid"] is True, response.json()
        assert response.json()["errors"] == []


def test_explicit_entities_keep_float_and_non_float_fact_kinds_after_save(
    parcel_workspace: tuple[WorkspaceStore, dict[str, Any]],
) -> None:
    store, draft = parcel_workspace
    authored = draft["scenario"]
    incoming = browser_document(authored)
    before = copy.deepcopy(incoming)
    by_id = {entity["id"]: entity["facts"] for entity in incoming["entities"]}
    assert type(by_id["pad-1"]["lowalt.pad_power_w"]) is int
    assert type(by_id["vertiport-1"]["lowalt.site_position"][2]) is int
    assert type(by_id["task-1"]["lowalt.task_payload_kg"]) is int

    saved = store.save(draft["id"], {"scenario": incoming})["scenario"]
    # Normalization must not mutate the caller's draft.
    assert_same_kinds(incoming, before)
    assert_same_kinds(saved["entities"], authored["entities"])
    facts = {entity["id"]: entity["facts"] for entity in saved["entities"]}
    assert type(facts["pad-1"]["lowalt.pad_power_w"]) is float
    assert type(facts["vertiport-1"]["lowalt.site_capacity"]) is int
    assert facts["vertiport-1"]["lowalt.site_open"] is True
    assert facts["central-no-fly"]["lowalt.zone_dynamic"] is False
    assert type(facts["central-no-fly"]["lowalt.zone_end_ns"]) is int
    assert facts["task-1"]["lowalt.task_kind"] == "parcel"
    assert facts["task-1"]["lowalt.task_vehicle"] == ""
    assert type(facts["task-1"]["lowalt.task_priority"]) is int
    assert type(facts["task-1"]["lowalt.task_created_ns"]) is int

    restarted = WorkspaceStore(store.root, None)
    assert_same_kinds(restarted.get(draft["id"])["scenario"], saved)
    result = restarted.validate(draft["id"])
    assert result["valid"] is True, result


@pytest.mark.parametrize("subject", ["entity", "generator"])
@pytest.mark.parametrize("invalid", [None, False, "0"], ids=["null", "boolean", "string"])
def test_save_does_not_coerce_invalid_float_facts(
    parcel_workspace: tuple[WorkspaceStore, dict[str, Any]],
    subject: str,
    invalid: Any,
) -> None:
    store, draft = parcel_workspace
    document = browser_document(draft["scenario"])
    if subject == "entity":
        facts = next(
            entity["facts"]
            for entity in document["entities"]
            if entity["id"] == "task-1"
        )
        field = "lowalt.task_payload_kg"
    else:
        facts = document["generators"]["fleet"]["facts"]
        field = "lowalt.soc"
    facts[field] = invalid
    saved = store.save(draft["id"], {"scenario": document})["scenario"]
    if subject == "entity":
        restored = next(
            entity["facts"] for entity in saved["entities"] if entity["id"] == "task-1"
        )
    else:
        restored = saved["generators"]["fleet"]["facts"]
    assert_same_kinds(restored[field], invalid)
    assert_same_kinds(facts[field], invalid)
    result = store.validate(draft["id"])
    assert result["valid"] is False
    assert any(field in error for error in result["errors"]), result


@pytest.mark.parametrize("invalid", [False, "1"], ids=["boolean", "numeric-string"])
def test_task_generator_payload_config_rejects_non_numeric_kinds(
    parcel_workspace: tuple[WorkspaceStore, dict[str, Any]], invalid: Any
) -> None:
    store, draft = parcel_workspace
    document = browser_document(draft["scenario"])
    group = document["engines"]["tasks"]["config"]["generators"][0]
    group["payload_kg"] = invalid
    saved = store.save(draft["id"], {"scenario": document})["scenario"]
    saved_group = saved["engines"]["tasks"]["config"]["generators"][0]
    assert_same_kinds(saved_group["payload_kg"], invalid)
    assert_same_kinds(group["payload_kg"], invalid)
    result = store.validate(draft["id"])
    assert result["valid"] is False
    assert any("task payload" in error for error in result["errors"]), result
