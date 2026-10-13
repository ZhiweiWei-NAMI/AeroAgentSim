"""Round-2 backend contract regressions; no server, model or external simulator."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.inputs import configured_extracts, extract_catalogue
from aeroagentsim.authoring.scene import compile_scene, extract_bounds
from aeroagentsim.authoring.templates import ENERGY, TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore
from aeroagentsim.scenario.loader import UniqueLoader


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("AEROAGENTSIM_OSM_EXTRACTS", raising=False)
    store = WorkspaceStore(tmp_path / "studio", None)
    app = FastAPI()
    app.include_router(create_router(store))
    with TestClient(app) as client:
        yield store, client


def placement(value):
    return {"id": "qa-point", "type": "aas:StudioFacility", "kind": "entity", "position": [0, 0, 0], "engine": "kinematic", "facts": {ENERGY: value}}


@pytest.mark.parametrize("value", [1, 1.0, 100000])
def test_number_facts_accept_integral_json_numbers(studio, value):
    store, client = studio
    draft = store.create("Explicit energy")
    response = client.post(f"/v1/studio/workspaces/{draft['id']}/place", json=placement(value))
    assert response.status_code == 200, response.text
    fact = response.json()["scenario"]["entities"][0]["facts"][ENERGY]
    assert fact == value and type(fact) is float
    assert store.get(draft["id"])["scenario"]["entities"][0]["facts"][ENERGY] == value
    assert store.validate(draft["id"])["valid"]


@pytest.mark.parametrize("value", [None, False, True, "1"])
def test_invalid_numeric_fact_is_never_replaced_by_default(studio, value):
    store, client = studio
    draft = store.create("Invalid energy")
    response = client.post(f"/v1/studio/workspaces/{draft['id']}/place", json=placement(value))
    assert response.status_code == 422 and "strict number" in response.text
    assert store.get(draft["id"]) == draft


def test_draft_save_returns_same_version_summary_as_get_and_refresh(studio):
    store, client = studio
    draft = store.create("Versioned draft")
    assert store.validate(draft["id"])["valid"]
    version = store.save_version(draft["id"], {"revision": draft["revision"]})
    assert version["version"] == 1
    endpoint = f"/v1/studio/workspaces/{draft['id']}"
    response = client.post(endpoint, json={"name": "Edited draft", "revision": draft["revision"]})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["latest_version"]["version"] == 1 and saved["dirty"] is True
    assert saved["revision"] == draft["revision"] + 1
    assert saved == client.get(endpoint).json()
    assert WorkspaceStore(store.root, None).summary(draft["id"]) == saved
    assert client.post(endpoint, json={"name": "Stale", "revision": draft["revision"]}).status_code == 409


def test_blank_duration_is_600_seconds_and_existing_templates_keep_their_durations(studio):
    store, _ = studio
    assert store.create("Blank")["scenario"]["run"]["until_ns"] == 600_000_000_000
    registry = TemplateRegistry()
    for template_id in ("food-delivery", "traffic-accident", "parcel-delivery"):
        source = registry.apply(template_id)
        expected = source.document["run"]["until_ns"]
        assert store.import_template(template_id, {}, registry=registry)["scenario"]["run"]["until_ns"] == expected


def test_huangpu_extract_is_default_with_measured_counts_and_bounds(studio):
    _, client = studio
    response = client.get("/v1/studio/catalog")
    assert response.status_code == 200, response.text
    catalog = response.json()
    assert catalog["default_extract"] == "huangpu-lite"
    rows = catalog["extracts"]
    assert rows[0]["id"] == "huangpu-lite" and rows[0]["default"] is True
    assert {row["id"] for row in rows} == {"huangpu-lite", "wujiaochang"}
    assert rows[0]["label"] == "Huangpu lite" and rows[0]["label_zh"] == "黄浦轻量地图"
    for row in rows:
        source = configured_extracts()[row["id"]]
        scene = compile_scene(source, extract_bounds(source), alt=0, level_height_m=3)
        assert row["bounds"] == scene["bounds"]
        assert row["buildings"] == sum(f["geometry"]["type"] in {"Polygon", "MultiPolygon"} for f in scene["geojson"]["features"])
        assert row["roads"] == len(scene["roads"])
    # The checked-in lite source really has 32 buildings and no OSM highways.
    assert rows[0]["buildings"] == 32 and rows[0]["roads"] == 0
    assert rows[1]["buildings"] == 3 and rows[1]["roads"] == 1
    source_dir = Path(__file__).parents[2] / "scenarios/demos/traffic-accident/inputs/lite-city/osm-source"
    raw_ways = {str(e["id"]) for p in source_dir.glob("*.json") for e in json.loads(p.read_text())["elements"] if e["type"] == "way"}
    xml_ways = {w.attrib["id"] for w in ET.parse(configured_extracts()["huangpu-lite"]).getroot().findall("way")}
    assert xml_ways == raw_ways


def test_explicit_extract_configuration_is_measured_and_not_substituted(tmp_path, monkeypatch):
    source = Path(__file__).parent / "fixtures/wujiaochang.osm.xml"
    monkeypatch.setenv("AEROAGENTSIM_OSM_EXTRACTS", json.dumps({"research": str(source)}))
    catalog = extract_catalogue(configured_extracts())
    assert len(catalog) == 1 and catalog[0]["id"] == "research" and catalog[0]["default"] is True
    assert catalog[0]["bounds"] == extract_bounds(source) and catalog[0]["buildings"] == 3 and catalog[0]["roads"] == 1


def test_scoped_relation_catalog_exposes_real_writers_and_rule_semantics(studio):
    store, client = studio
    draft = store.create("Relation ownership")
    source = Path(__file__).parents[2] / "scenarios/behaviours"
    document = yaml.load((source / "minimal.yaml").read_text(), Loader=UniqueLoader)
    (store.directory(draft["id"]) / "minimal.registry.snapshot.json").write_bytes((source / "registry.snapshot.json").read_bytes())
    document["registry"]["snapshot"] = "minimal.registry.snapshot.json"
    document["behaviours"][0]["registry"]["snapshot"] = "minimal.registry.snapshot.json"
    document["registry"]["relations"] = [{"id": "example.serves", "source_type": "example:Task", "target_type": "example:Task",
                                          "targets_per_source": {"minimum": 0, "maximum": 1}, "sources_per_target": {"minimum": 0, "maximum": 1},
                                          "identity_policy": "edge_id", "metadata": {}}]
    document["engines"]["behaviour"]["config"]["relation_produces"] = ["example.serves"]
    # Reproduce the form's invalid writer without binding it or starting a run.
    document["bindings"]["relations"] = [{"writer": "operations", "relation": "example.serves", "type": "example:Task", "ids": "task-1"}]
    saved = store.save(draft["id"], {"scenario": document})
    response = client.get("/v1/studio/types", params={"workspace": draft["id"]})
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["relations"] if row["id"] == "example.serves")
    assert row["valid_writers"] == ["behaviour"] and "operations" not in row["valid_writers"]
    assert row["writer_options"] == [{"writer": "behaviour", "engine_id": "behaviour", "plugin": "behaviour"}]
    assert row["binding_kind"] == "writer_rule" and store.get(draft["id"]) == saved
    document = copy.deepcopy(saved["scenario"])
    document["bindings"]["relations"][0]["writer"] = row["valid_writers"][0]
    accepted = client.post(f"/v1/studio/workspaces/{draft['id']}", json={"scenario": document, "revision": saved["revision"]})
    assert accepted.status_code == 200, accepted.text
    validation = store.validate(draft["id"])
    assert validation["valid"], validation
    saved = store.get(draft["id"])
    document = copy.deepcopy(saved["scenario"])
    document["engines"]["behaviour"]["config"].pop("relation_produces")
    document["bindings"]["relations"] = []
    store.save(draft["id"], {"scenario": document, "revision": saved["revision"]})
    row = next(r for r in client.get("/v1/studio/types", params={"workspace": draft["id"]}).json()["relations"] if r["id"] == "example.serves")
    assert row["valid_writers"] == [] and row["writer_options"] == []
