"""Draft round trips and compiler scope never fabricate run validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.authoring.api import create_router
from aeroagentsim.authoring.workspace import WorkspaceStore


def package() -> dict[str, Any]:
    return {
        "format": "aeroagentsim.behaviour-package/v1",
        "id": "example",
        "revision": 1,
        "predicates": {},
        "chains": {},
        "bindings": [],
        "budgets": {"max_transitions_per_instance_per_ns": 64, "max_instances": 10},
        "future_extension": {
            "integer": 9007199254740993,
            "nullable": None,
            "false": False,
        },
    }


@pytest.fixture
def store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "drafts", tmp_path / "read-only-ontology")


def test_unknown_content_semantic_roundtrip_and_separate_layout(
    store: WorkspaceStore,
) -> None:
    draft = store.create("test")
    identifier = draft["id"]
    authored = package()
    result = store.edit_behaviour(
        identifier, {"package": authored, "layout": {"states": {"waiting": [10, 30]}}}
    )
    assert result["scenario"]["behaviours"] == [authored]
    first = store.export_behaviour(identifier, 0)
    store.edit_behaviour(identifier, {"index": 0, "yaml": first["yaml"]})
    second = store.export_behaviour(identifier, 0)
    assert first["semantic_digest"] == second["semantic_digest"]
    assert second["package"] == authored
    assert second["layout"] == first["layout"]
    assert "behaviour_layout" not in result["scenario"]
    before = copy.deepcopy(result["scenario"])
    store.edit_behaviour(identifier, {"index": 0, "layout": {"state": [40, 80]}})
    assert store.get(identifier)["scenario"] == before


def test_duplicate_yaml_and_escape_preserve_previous_draft(
    store: WorkspaceStore, tmp_path: Path
) -> None:
    identifier = store.create("test")["id"]
    store.edit_behaviour(identifier, {"package": package()})
    before = store.get(identifier)
    with pytest.raises(ValueError, match="duplicate"):
        store.edit_behaviour(identifier, {"index": 0, "yaml": "id: a\nid: b\n"})
    assert store.get(identifier) == before
    store.edit_behaviour(
        identifier,
        {"index": 0, "package": {"path": "../outside.yaml", "sha256": "0" * 64}},
    )
    with pytest.raises(ValueError, match="inside workspace"):
        store.export_behaviour(identifier, 0)


def test_real_console_demo_compiles_and_reports_an_authored_error(
    tmp_path: Path,
) -> None:
    from aeroagentsim.authoring.inputs import configured_ontology

    store = WorkspaceStore(tmp_path / "drafts", configured_ontology())
    identifier = store.create("Traffic accident (demo)")["id"]
    draft = store.import_demo(
        identifier, "traffic-accident", console=True, primitive=True
    )
    scenario = draft["scenario"]
    scenario["engines"]["capture"]["config"]["renderer"] = {
        "mode": "stub",
        "fixtures": {},
    }

    def browser_numbers(value: Any) -> Any:
        if type(value) is float and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [browser_numbers(item) for item in value]
        if isinstance(value, dict):
            return {
                key: item if key == "behaviours" else browser_numbers(item)
                for key, item in value.items()
            }
        return value

    scenario = browser_numbers(scenario)
    store.save(identifier, {"scenario": scenario})
    saved = store.get(identifier)["scenario"]
    bridge = saved["engines"]["capture_bridge"]["config"]
    request_schema = next(
        item["schema"]
        for item in saved["registry"]["messages"]
        if item["id"] == bridge["request_schema"]
    )
    from aerokernel.registry import MemoryRegistry

    MemoryRegistry(()).validate(
        request_schema["members"]["timeout_s"], bridge["timeout_s"]
    )
    assert store.validate_behaviour(identifier, 0)["valid"] is True
    from aeroagentsim.authoring.demo import live_decisions

    live = copy.deepcopy(scenario)
    live_decisions(
        live,
        {
            "base_url": "http://127.0.0.1:9/v1",
            "model": "explicit-no-calls-test",
            "api_key_env": "AAS_TEST_NO_KEY",
        },
    )
    store.save(identifier, {"scenario": live})
    checked = store.validate_behaviour(identifier, 0)
    assert checked["valid"] is True, checked
    store.save(identifier, {"scenario": scenario})
    scenario["behaviours"][0]["chains"]["traffic.incident_report"]["trigger"] = {
        "predicate": "missing",
        "edge": "entered",
    }
    store.save(identifier, {"scenario": scenario})
    result = store.validate_behaviour(identifier, 0)
    assert result["valid"] is False
    assert result["scope"] == "bound_scenario"
    assert result["errors"][0]["path"].startswith(
        "$.chains.traffic.incident_report.trigger"
    )


def test_rest_draft_export_and_read_actual_wal_identity(
    store: WorkspaceStore, tmp_path: Path
) -> None:
    app = FastAPI()
    run_root = tmp_path / "runs"
    app.include_router(create_router(store, extracts={}, run_root=run_root))
    identifier = store.create("test")["id"]
    with TestClient(app) as client:
        base = f"/v1/studio/workspaces/{identifier}"
        assert (
            client.post(base + "/behaviours", json={"package": package()}).status_code
            == 200
        )
        assert client.get(base + "/behaviours/0/export").json()["package"] == package()
        assert (
            client.post(
                base + "/behaviours/0/validate", json={"ignored": True}
            ).status_code
            == 422
        )
        run_id = "run-" + "a" * 32
        directory = run_root / run_id
        directory.mkdir(parents=True)
        (directory / "scenario.json").write_text(
            json.dumps({"id": "authored", "engines": {}})
        )
        (directory / "journal.jsonl").write_text(
            json.dumps(
                {
                    "type": "header",
                    "index": 0,
                    "run_id": "kernel-namespace",
                    "epoch": "recorded-epoch",
                    "resolved_bindings": [],
                }
            )
            + "\n"
        )
        identity = client.get(f"/v1/studio/runs/{run_id}/configuration").json()
        assert identity["kernel_run_id"] == "kernel-namespace"
        assert identity["epoch"] == "recorded-epoch"
        (directory / "journal.jsonl").write_text('{"index":0}')
        assert client.get(f"/v1/studio/runs/{run_id}/configuration").status_code == 422


def test_import_real_demo_and_pin_nested_references(store: WorkspaceStore) -> None:
    identifier = store.create("actual shipped source")["id"]
    draft = store.import_demo(identifier, "traffic-accident")
    assert "validation" not in draft
    assert draft["scenario"]["id"] == "traffic-accident"
    spec = draft["scenario"]["behaviours"][0]
    authored = store.export_behaviour(identifier, 0)["package"]
    assert authored["id"]
    store._check_behaviour_refs([spec], identifier)
    package_path = store.directory(identifier) / spec["path"]
    package_path.write_text(package_path.read_text() + "\n# changed bytes\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        store._check_behaviour_refs([spec], identifier)
    with pytest.raises(ValueError, match="inside workspace"):
        store._check_behaviour_refs(
            [{"imports": [{"path": "../../outside.yaml", "sha256": "0" * 64}]}],
            identifier,
        )


def test_pinned_package_inline_edit_rebases_actual_dependency_sources(
    store: WorkspaceStore,
) -> None:
    import hashlib

    import yaml

    identifier = store.create("pinned source relocation")["id"]
    root = store.directory(identifier)
    (root / "packages").mkdir()
    child = package()
    child["id"] = "imported"
    child_bytes = yaml.safe_dump(child).encode()
    (root / "packages/child.yaml").write_bytes(child_bytes)
    parent = package()
    parent["imports"] = [
        {"path": "child.yaml", "sha256": hashlib.sha256(child_bytes).hexdigest()}
    ]
    parent_bytes = yaml.safe_dump(parent).encode()
    (root / "packages/parent.yaml").write_bytes(parent_bytes)
    store.edit_behaviour(
        identifier,
        {
            "package": {
                "path": "packages/parent.yaml",
                "sha256": hashlib.sha256(parent_bytes).hexdigest(),
            }
        },
    )
    exported = store.export_behaviour(identifier, 0)
    assert exported["package"] == parent
    assert exported["inline_package"]["imports"][0]["path"] == "packages/child.yaml"
    assert exported["inline_package"]["future_extension"] == parent["future_extension"]
    store._check_behaviour_refs([exported["inline_package"]], identifier)
