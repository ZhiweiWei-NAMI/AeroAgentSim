"""Removal previews references and replaces a validated draft atomically."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.removal import RemovalPlan, references
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceConflict, WorkspaceStore
from aeroagentsim.packs.lowalt.template import expand
from aeroagentsim.scenario import load_scenario


def placed(store: WorkspaceStore) -> dict[str, Any]:
    draft = store.create("Remove facility")
    for identity in ("facility/a-1", "keeper"):
        draft = store.place(draft["id"], {
            "id": identity, "type": "aas:StudioFacility", "kind": "facility",
            "position": [1, 2, 0], "engine": "workflow",
        })
    return draft


def test_remove_cleans_bindings_and_workflow_machine_then_validates(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = placed(store)
    before = (store.directory(draft["id"]) / "draft.json").read_bytes()
    preview = store.remove(draft["id"], {"id": "facility/a-1", "revision": draft["revision"], "dry_run": True})
    assert {row["kind"] for row in preview["dependencies"]} == {"binding", "engine"}
    assert any(row["path"] == "/engines/operations/config/machines/0" for row in preview["dependencies"])
    assert all("删除" in row["detail"] for row in preview["dependencies"])
    assert (store.directory(draft["id"]) / "draft.json").read_bytes() == before
    removed = store.remove(draft["id"], {"id": "facility/a-1", "revision": draft["revision"]})
    assert removed["revision"] == draft["revision"] + 1
    assert [entity["id"] for entity in removed["scenario"]["entities"]] == ["keeper"]
    assert not references(removed["scenario"], {"facility/a-1"})
    assert [machine["entity"] for machine in removed["scenario"]["engines"]["operations"]["config"]["machines"]] == ["keeper"]
    assert "validation" not in removed
    assert store.validate(draft["id"])["valid"] is True


def test_literal_states_named_like_the_deleted_entity_are_preserved(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("State identity")
    for identity in ("placed", "keeper"):
        draft = store.place(draft["id"], {
            "id": identity, "type": "aas:StudioFacility", "kind": "facility",
            "position": [1, 2, 0], "engine": "workflow",
        })
    removed = store.remove(draft["id"], {"id": "placed", "revision": draft["revision"]})
    assert removed["scenario"]["entities"][0]["facts"]["aas.studio.state"] == "placed"
    machine = removed["scenario"]["engines"]["operations"]["config"]["machines"][0]
    assert machine["entity"] == "keeper" and "placed" in machine["states"]


def test_typed_fact_reference_is_removed_from_the_actual_bound_scenario(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = placed(store)
    field = "aas.test.partner"
    draft["scenario"]["registry"]["fields"].append({
        "id": field, "type": "aas:StudioFacility",
        "schema": {"type": "ref", "target_type": "aas:StudioFacility"}, "metadata": {"role": "state"},
    })
    keeper = next(entity for entity in draft["scenario"]["entities"] if entity["id"] == "keeper")
    keeper["facts"][field] = {"$ref": {
        "id": "facility/a-1", "run_id": draft["id"], "epoch": "0", "generation": 0, "type_id": "aas:StudioFacility",
    }}
    draft["scenario"]["engines"]["operations"]["config"]["produces"].append(field)
    draft["scenario"]["bindings"]["exact"].append({"entity": "keeper", "field": field, "writer": "operations"})
    draft = store._write(draft)
    assert store.validate(draft["id"])["valid"]
    preview = store.remove(draft["id"], {"id": "facility/a-1", "dry_run": True})
    assert any(row["path"] == "/entities/1/facts/aas.test.partner" and row["kind"] == "relation" for row in preview["dependencies"])
    removed = store.remove(draft["id"], {"id": "facility/a-1", "revision": draft["revision"]})
    assert field not in removed["scenario"]["entities"][0]["facts"]
    assert store.validate(draft["id"])["valid"]


def test_entity_named_like_its_engine_does_not_remove_other_writer_bindings(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.create("Separate identity namespaces")
    for identity in ("operations", "keeper"):
        draft = store.place(draft["id"], {
            "id": identity, "type": "aas:StudioFacility", "kind": "facility", "position": [0, 0, 0], "engine": "workflow",
        })
    removed = store.remove(draft["id"], {"id": "operations", "revision": draft["revision"]})
    assert "operations" in removed["scenario"]["engines"]
    assert {row["entity"] for row in removed["scenario"]["bindings"]["exact"]} == {"keeper"}
    assert all(row["writer"] == "operations" for row in removed["scenario"]["bindings"]["exact"])


def test_dependency_plan_covers_all_reference_scopes_and_keeps_unrelated_data(tmp_path: Path) -> None:
    ref = {"$ref": {"id": "victim", "run_id": "draft", "epoch": "0", "generation": 0, "type_id": "Type"}}
    survivor = {"$ref": {**ref["$ref"], "id": "keeper"}}
    document = {
        "entities": [
            {"id": "victim", "type": "Type", "facts": {}},
            {"id": "keeper", "type": "Type", "label": "victim", "facts": {"partner/a~b": ref, "partners": [ref, survivor], "label": "victim-extra"}},
        ],
        "bindings": {"exact": [{"entity": "victim"}, {"entity": "keeper", "writer": "weather"}], "lifecycle": [{"ids": "victim"}, {"ids": "*"}], "commands": [{"payload": {"actor": ref}}], "cohorts": [["motion", "weather"]]},
        "relations": [{"source": "victim", "target": "keeper"}, {"source": "keeper", "target": "keeper"}],
        "engines": {
            "workflow": {"plugin": "workflow", "config": {"machines": [{"entity": "victim"}, {"entity": "keeper"}]}},
            "weather": {"plugin": "environment", "config": {"profiles": {"victim": {"wind": {"value": [1, 0, 0]}}}}},
            "motion": {"plugin": "kinematic", "config": {"environment_owner": "weather"}},
            "behaviour": {"plugin": "behaviour", "config": {"packages": [], "capabilities": {"move": {"target": "motion"}}}},
        },
        "behaviours": [{
            "predicates": {}, "chains": {"move-chain": {"action": {"capability": "move"}}, "keep": {"state": "waiting"}},
            "bindings": [{"chain": "keep", "match": {"actor": {"entity": "victim"}}}, {"chain": "move-chain"}],
            "bootstrap_relations": {"assertions": [{"source": ref, "target": survivor}]},
        }],
        "presentation": [{"entity": "victim"}, {"typeId": "Type", "visual": {"asset": "victim-extra"}}],
        "generators": {"requests": {"facts": {"recipient": ref}}, "unrelated": {"count": 2}},
        "tasks": [{"id": "order", "assignee": "victim"}],
    }
    original = copy.deepcopy(document)
    cleaned, dependencies = RemovalPlan(document, "victim", tmp_path).build()
    assert document == original
    assert {row["kind"] for row in dependencies} == {"binding", "relation", "behaviour", "presentation", "engine", "generator", "other"}
    assert any(row["path"] == "/entities/1/facts/partner~1a~0b" for row in dependencies)
    assert not references(cleaned, {"victim", "weather", "motion", "move", "move-chain"})
    assert cleaned["entities"][0]["label"] == "victim"
    assert cleaned["entities"][0]["facts"]["partners"] == [survivor]
    assert cleaned["bindings"]["lifecycle"] == [{"ids": "*"}]
    assert cleaned["generators"] == {"unrelated": {"count": 2}}
    assert "keep" in cleaned["behaviours"][0]["chains"]


def test_file_behaviour_and_imports_are_cleaned_inline_without_writing_sources(tmp_path: Path) -> None:
    child = {"predicates": {}, "chains": {}, "bindings": [{"match": {"actor": {"entity": "victim"}}}]}
    parent = {"predicates": {}, "chains": {}, "bindings": [], "imports": [{"path": "child.yaml"}]}
    (tmp_path / "child.yaml").write_text(yaml.safe_dump(child))
    (tmp_path / "parent.yaml").write_text(yaml.safe_dump(parent))
    before = {name: (tmp_path / name).read_bytes() for name in ("child.yaml", "parent.yaml")}
    document = {"entities": [{"id": "victim", "facts": {}}], "bindings": {}, "engines": {}, "behaviours": [{"path": "parent.yaml"}]}
    cleaned, dependencies = RemovalPlan(document, "victim", tmp_path).build()
    assert cleaned["behaviours"][0]["imports"][0]["bindings"] == []
    assert dependencies and dependencies[0]["kind"] == "behaviour"
    assert dependencies[0]["path"] == "/behaviours/0"
    assert "child.yaml" in dependencies[0]["detail"]
    assert {name: (tmp_path / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("dry_run", [False, True])
def test_revision_mismatch_preserves_content_and_returns_409(tmp_path: Path, dry_run: bool) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = placed(store)
    with pytest.raises(WorkspaceConflict):
        store.remove(draft["id"], {"id": "keeper", "revision": draft["revision"] - 1, "dry_run": dry_run})
    app = FastAPI()
    app.include_router(create_router(store, extracts={}))
    with TestClient(app) as client:
        response = client.post(f"/v1/studio/workspaces/{draft['id']}/remove", json={"id": "keeper", "revision": draft["revision"] - 1, "dry_run": dry_run})
        assert response.status_code == 409
    assert store.get(draft["id"]) == draft


def test_failed_validation_never_changes_revision_or_source_files(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = placed(store)
    # This unrelated authored error must not be erased to manufacture success.
    draft["scenario"]["run"]["seed"] = None
    draft = store._write(draft)
    before = {str(path): path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="run.seed"):
        store.remove(draft["id"], {"id": "facility/a-1", "revision": draft["revision"]})
    assert store.get(draft["id"]) == draft
    assert {str(path): path.read_bytes() for path in store.root.rglob("*") if path.is_file()} == before


def test_remove_rest_preview_commit_and_missing_instance(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = placed(store)
    app = FastAPI()
    app.include_router(create_router(store, extracts={}))
    url = f"/v1/studio/workspaces/{draft['id']}/remove"
    with TestClient(app) as client:
        preview = client.post(url, json={"id": "keeper", "revision": draft["revision"], "dry_run": True})
        assert preview.status_code == 200 and preview.json()["dependencies"]
        removed = client.post(url, json={"id": "keeper", "revision": draft["revision"]})
        assert removed.status_code == 200 and removed.json()["id"] == draft["id"]
        assert client.post(url, json={"id": "keeper"}).status_code == 404
        assert client.post(url, json={"id": "facility/a-1", "dry_run": "true"}).status_code == 422


def test_generated_member_removal_preserves_siblings_and_prevents_regeneration(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspaces", None)
    draft = store.import_template("parcel-delivery", {"fleet_size": 3}, registry=TemplateRegistry())
    expand(draft["scenario"])
    draft = store._write(draft)
    before = {entity["id"]: copy.deepcopy(entity) for entity in draft["scenario"]["entities"] if entity["id"].startswith("drone-")}
    preview = store.remove(draft["id"], {"id": "drone-2", "revision": draft["revision"], "dry_run": True})
    assert any(row["path"] == "/generators/fleet" and row["kind"] == "generator" for row in preview["dependencies"])
    removed = store.remove(draft["id"], {"id": "drone-2", "revision": draft["revision"]})
    assert "generators" not in removed["scenario"]
    loaded = load_scenario(removed["scenario"], base=store.directory(draft["id"]))
    remaining = {entity["id"]: entity for entity in loaded.document["entities"] if entity["id"].startswith("drone-")}
    assert remaining == {identity: entity for identity, entity in before.items() if identity != "drone-2"}
