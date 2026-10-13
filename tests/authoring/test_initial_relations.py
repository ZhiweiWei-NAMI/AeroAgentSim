"""R4 concrete links remain valid and saveable on the real public templates."""

from __future__ import annotations

import copy
from fnmatch import fnmatchcase

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.relations import _literal_scope
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROAGENTSIM_ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.delenv("AEROAGENTSIM_TRAFFIC_ASSET_ROOT", raising=False)
    store = WorkspaceStore(tmp_path / "studio", None)
    app = FastAPI()
    app.include_router(create_router(store, extracts={}))
    with TestClient(app, base_url="http://localhost") as client:
        yield store, client


def scene(store, template="food-delivery", scope="unscoped", parent=False):
    draft = store.import_template(template, {}, registry=TemplateRegistry())
    document = copy.deepcopy(draft["scenario"])
    if template == "traffic-accident":
        relation, source, target = "traffic.coordinator-candidate", "edge.coordinator", "uav.bg.01"
        rule = next(item for item in document["bindings"]["relations"] if item["relation"] == relation)
    else:
        relation = "t1.serves"
        source = next(entity["id"] for entity in document["entities"] if entity["type"] == "lowalt:Restaurant")
        target = next(entity["id"] for entity in document["entities"] if entity["type"] == "lowalt:Customer")
        document["registry"].setdefault("relations", []).append({"id": relation, "source_type": "lowalt:Restaurant", "target_type": "lowalt:Customer",
            "targets_per_source": {"minimum": 0, "maximum": None}, "sources_per_target": {"minimum": 0, "maximum": None},
            "identity_policy": "edge_id", "metadata": {}})
        document["engines"]["behaviour"]["config"].setdefault("relation_produces", []).append(relation)
        rule = {"writer": "behaviour", "relation": relation, "type": "lowalt:Site" if parent else "lowalt:Restaurant"}
        document["bindings"].setdefault("relations", []).append(rule)
    if scope == "wildcard":
        rule["ids"] = "*"
    elif scope == "glob":
        rule["ids"] = source[:-1] + "?"
    elif scope == "class":
        rule["ids"] = source[:-1] + f"[{source[-1]}]"
    elif scope == "exact":
        rule["ids"] = _literal_scope(source)
    saved = store.save(draft["id"], {"scenario": document, "revision": draft["revision"]})
    return saved, {"relation": relation, "writer": "behaviour", "source": source, "target": target, "revision": saved["revision"]}


@pytest.mark.parametrize("template", ["traffic-accident", "food-delivery"])
def test_add_link_validate_save_version_reuses_existing_permission(studio, template):
    """T1-R4-002 / T3-R4-005 / T4-R4-004: real validation and version persistence."""
    store, client = studio
    draft, body = scene(store, template)
    endpoint = f"/v1/studio/workspaces/{draft['id']}"
    before = copy.deepcopy(draft["scenario"]["bindings"]["relations"])
    response = client.post(endpoint + "/relations", json=body)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated == client.get(endpoint).json()
    assert updated["revision"] == draft["revision"] + 1
    assert updated["scenario"]["bindings"]["relations"] == before
    link = updated["scenario"]["behaviours"][-1]["bootstrap_relations"]["assertions"][0]
    assert link["relation"] == body["relation"]
    assert link["source"]["$ref"]["id"] == body["source"]
    assert link["target"]["$ref"]["id"] == body["target"]
    validation = client.post(endpoint + "/validate", json={"revision": updated["revision"]})
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"], validation.json()
    version = client.post(endpoint + "/versions", json={"revision": updated["revision"]})
    assert version.status_code == 201, version.text
    assert version.json()["version"] == 1
    reopened = WorkspaceStore(store.root, None)
    assert reopened.summary(draft["id"])["latest_version"]["version"] == 1
    assert reopened.version(draft["id"], 1)["scenario"]["behaviours"][-1]["bootstrap_relations"]["assertions"][0] == link


@pytest.mark.parametrize("scope", ["wildcard", "glob", "class", "exact"])
def test_inherited_type_and_covering_glob_reuse_rule_verbatim(studio, scope):
    store, client = studio
    draft, body = scene(store, scope=scope, parent=True)
    response = client.post(f"/v1/studio/workspaces/{draft['id']}/relations", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["scenario"]["bindings"]["relations"] == draft["scenario"]["bindings"]["relations"]


def test_no_permission_creates_one_literal_scope_then_reuses_it_for_second_target(studio):
    store, client = studio
    draft, body = scene(store)
    document = copy.deepcopy(draft["scenario"])
    document["bindings"]["relations"] = []
    saved = store.save(draft["id"], {"scenario": document, "revision": draft["revision"]})
    endpoint = f"/v1/studio/workspaces/{draft['id']}/relations"
    response = client.post(endpoint, json={**body, "revision": saved["revision"]})
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["scenario"]["bindings"]["relations"] == [{"writer": body["writer"], "relation": body["relation"], "type": "lowalt:Restaurant", "ids": _literal_scope(body["source"])}]
    other = next(entity["id"] for entity in document["entities"] if entity["type"] == "lowalt:Customer" and entity["id"] != body["target"])
    second = client.post(endpoint, json={**body, "target": other, "revision": created["revision"]})
    assert second.status_code == 200, second.text
    assert second.json()["scenario"]["bindings"]["relations"] == created["scenario"]["bindings"]["relations"]


@pytest.mark.parametrize("problem", ["scope", "conflicting_writer", "overlapping_rules", "duplicate", "endpoint", "writer", "cardinality", "revision", "null_revision"])
def test_invalid_link_never_modifies_permissions_or_draft(studio, problem):
    store, client = studio
    draft, body = scene(store)
    document = copy.deepcopy(draft["scenario"])
    rule = document["bindings"]["relations"][0]
    if problem == "scope":
        rule["ids"] = "a-different-kitchen"
    elif problem == "conflicting_writer":
        rule["writer"] = "another-owner"
    elif problem == "overlapping_rules":
        document["bindings"]["relations"].append({**rule, "ids": body["source"]})
    elif problem == "cardinality":
        document["registry"]["relations"][-1]["targets_per_source"]["maximum"] = 0
    saved = store.save(draft["id"], {"scenario": document, "revision": draft["revision"]})
    body["revision"] = saved["revision"]
    if problem == "duplicate":
        accepted = client.post(f"/v1/studio/workspaces/{draft['id']}/relations", json=body)
        assert accepted.status_code == 200, accepted.text
        body["revision"] = accepted.json()["revision"]
    elif problem == "endpoint":
        body["source"] = body["target"]
    elif problem == "writer":
        body["writer"] = "not-configured"
    elif problem == "revision":
        body["revision"] -= 1
    elif problem == "null_revision":
        body["revision"] = None
    before = store.get(draft["id"])
    response = client.post(f"/v1/studio/workspaces/{draft['id']}/relations", json=body)
    assert response.status_code == (409 if problem == "revision" else 422), response.text
    assert store.get(draft["id"]) == before
    if problem in {"scope", "overlapping_rules", "conflicting_writer"}:
        assert body["relation"] in response.text


def test_literal_source_glob_metacharacters_are_not_wildcards():
    source = "kitchen[1]*?"
    pattern = _literal_scope(source)
    assert fnmatchcase(source, pattern)
    assert not fnmatchcase("kitchen1ZZ", pattern)
