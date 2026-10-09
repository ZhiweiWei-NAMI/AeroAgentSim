"""Explorer endpoint joining the native catalog with real workspace state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim.authoring.explorer import explorer_payload
from aeroagentsim.authoring.inputs import configured_ontology
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.services.app import create_app

try:
    ONTOLOGY = configured_ontology()
except ValueError as error:  # unconfigured: AEROAGENTSIM_AEROGRAPH_ROOT unset/invalid
    pytest.skip(str(error), allow_module_level=True)


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "drafts", ONTOLOGY)


def placed(store: WorkspaceStore) -> dict[str, Any]:
    workspace = store.create("explorer")
    for index in range(2):
        workspace = store.place(
            workspace["id"],
            {
                "id": f"uav-{index}",
                "type": "oo:UAV",
                "kind": "entity",
                "position": [0.0, index * 12.0, 10.0],
                "engine": "kinematic",
            },
        )
    return store.place(
        workspace["id"],
        {
            "id": "facility",
            "type": "aas:StudioFacility",
            "kind": "facility",
            "position": [10.0, 10.0, 0.0],
            "engine": "workflow",
        },
    )


def test_explorer_joins_native_catalog_with_real_writers_and_counts(
    store: WorkspaceStore,
) -> None:
    workspace = placed(store)
    payload = explorer_payload(store, workspace["id"])

    assert payload["workspace"] == {"id": workspace["id"], "name": "explorer"}

    # Native source rows: real persisted types, fields and directed relations.
    types = {row["id"]: row for row in payload["types"]}
    assert "oo:UAV" in types and types["oo:UAV"]["origin"] == "native"
    assert "aas:StudioFacility" in types
    assert types["aas:StudioFacility"]["origin"] == "workspace"
    assert payload["relations"], "persisted relation rows must be preserved"
    row = payload["relations"][0]
    assert {"id", "sourceClass", "targetClass"} <= set(row)
    assert any(field["writers"] == [] for field in payload["fields"]), (
        "undeclared native fields must not gain a writer"
    )

    # Counts come from the actual draft through real is_a ancestry closure.
    uav = types["oo:UAV"]
    assert uav["count"] == 2
    facility = types["aas:StudioFacility"]
    assert facility["count"] == 1
    # Counts reach ancestors through the real is_a closure only.
    assert types["oo:Aircraft"]["count"] == 2
    assert types["oo:ModelObject"]["count"] == 3
    assert types["oo:Sensor"]["count"] == 0
    assert all(
        row["count"] is None or isinstance(row["count"], int)
        for row in payload["types"]
    )

    # Writers resolve from the current workspace bindings and engine declares.
    fields = {row["id"]: row for row in payload["fields"]}
    position = fields["aas.studio.position_enu_m"]
    assert {writer["engine_id"] for writer in position["writers"]} == {
        "motion",
        "operations",
    }
    motion = next(w for w in position["writers"] if w["engine_id"] == "motion")
    assert motion["plugin"] == "kinematic"
    assert motion["entities"] == ["uav-0", "uav-1"]
    operations = next(w for w in position["writers"] if w["engine_id"] == "operations")
    assert operations["entities"] == ["facility"]
    state = fields["aas.studio.state"]
    assert [w["engine_id"] for w in state["writers"]] == ["operations"]
    assert fields["aas.studio.energy_j"]["writers"][0]["entities"] == [
        "uav-0",
        "uav-1",
    ]

    # A native field on an ancestor resolves the rule writer for a child type.
    draft = store.get(workspace["id"])
    draft["scenario"]["bindings"].setdefault("rules", []).append(
        {
            "writer": "motion",
            "type": "oo:Aircraft",
            "fields": ["he.aircraft.position_enu_m"],
            "ids": "*",
        }
    )
    store._write(draft)
    inherited = {
        row["id"]: row for row in explorer_payload(store, workspace["id"])["fields"]
    }
    assert inherited["he.aircraft.position_enu_m"]["writers"][0]["entities"] == [
        "uav-0",
        "uav-1",
    ]

    # Entities come from the scenario with real identities.
    assert {entity["id"] for entity in payload["entities"]} == {
        "uav-0",
        "uav-1",
        "facility",
    }

    # Native predicate/event definitions come from the persisted source index.
    predicates = {row["id"]: row for row in payload["predicates"]}
    assert any(
        row["definition"]["kind"] == "predicate" and row["definition"]["expression"]
        for row in predicates.values()
    )
    assert payload["events"], "persisted native events must be listed"

    # The workspace list reflects the actual drafts.
    assert {"id": workspace["id"], "name": "explorer"} in payload["workspaces"]


def test_explorer_without_workspace_has_null_counts(store: WorkspaceStore) -> None:
    payload = explorer_payload(store, None)
    assert payload["workspace"] is None
    assert payload["entities"] == []
    assert all(row["count"] is None for row in payload["types"])
    assert all(row["writers"] == [] for row in payload["fields"])


def test_explorer_route_selects_latest_draft_and_rejects_bad_ids(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path / "runs", scenario_root=tmp_path, studio_root=tmp_path / "studio"
    )
    with TestClient(app) as client:
        first = client.post("/v1/studio/workspaces", json={"name": "older"}).json()
        second = client.post("/v1/studio/workspaces", json={"name": "newer"}).json()
        # Filesystem timestamp precision must not override recorded update order.
        for draft in (first, second):
            path = tmp_path / "studio" / draft["id"] / "draft.json"
            os.utime(path, ns=(1_700_000_000_000_000_000,) * 2)
        default = client.get("/v1/studio/explorer")
        assert default.status_code == 200
        assert default.json()["workspace"] == {
            "id": second["id"],
            "name": "newer",
        }
        selected = client.get(
            "/v1/studio/explorer", params={"workspace_id": first["id"]}
        )
        assert selected.status_code == 200
        assert selected.json()["workspace"]["id"] == first["id"]
        assert (
            client.get(
                "/v1/studio/explorer", params={"workspace_id": "../escape"}
            ).status_code
            == 422
        )
