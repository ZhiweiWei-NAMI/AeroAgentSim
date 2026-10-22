"""Installed plugin inventory exposed through the service layer, CLI and HTTP API."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import aeroagentsim.plugin_api as plugin_api
import aeroagentsim.services.plugins as plugin_inventory_service
from aeroagentsim.plugin_api import ScenarioPlugin, TemplateSource
from aeroagentsim.services.app import create_app
from aeroagentsim.services.cli import main

BUILTIN_IDS = {
    "air-taxi",
    "city-day",
    "crop-spraying",
    "flood-rescue",
    "food-delivery",
    "medical-delivery",
    "parcel-delivery",
    "powerline-inspection",
    "security-patrol",
    "traffic-accident",
}


@pytest.fixture(autouse=True)
def _isolate_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    def query(*, group: str) -> tuple[Any, ...]:
        return ()

    monkeypatch.setattr(plugin_api, "entry_points", query)
    monkeypatch.setattr(plugin_inventory_service, "entry_points", query)


def test_builtin_scenarios_cover_every_shipped_template() -> None:
    inventory = plugin_inventory_service.plugin_inventory()
    scenarios = {row["name"]: row for row in inventory["scenarios"]}
    # Hook providers; templates may live in any number of scenario plugins.
    assert {"lowalt", "traffic", "rescue"} <= set(scenarios)
    for row in scenarios.values():
        assert row["origin"] == "builtin"
        assert row["distribution"] is None
    assert {
        template_id
        for row in scenarios.values()
        for template_id in row["templates"]
    } == BUILTIN_IDS


def test_installed_scenario_entry_point_reports_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "external-template"
    source.mkdir()
    (source / "template.yaml").write_text("id: external-template\n", encoding="utf-8")
    plugin = ScenarioPlugin(
        name="external",
        templates=(TemplateSource(directory=source),),
        apply_hooks={"external.mode": lambda document, base, value: None},
    )
    entry = SimpleNamespace(
        name="external",
        dist=SimpleNamespace(name="acme-plugins"),
        load=lambda: plugin,
    )

    def query(*, group: str) -> tuple[Any, ...]:
        return (entry,) if group == "aeroagentsim.scenarios" else ()

    monkeypatch.setattr(plugin_api, "entry_points", query)
    monkeypatch.setattr(plugin_inventory_service, "entry_points", query)
    scenarios = {
        row["name"]: row
        for row in plugin_inventory_service.plugin_inventory()["scenarios"]
    }
    assert scenarios["external"] == {
        "name": "external",
        "origin": "entry-point",
        "distribution": "acme-plugins",
        "templates": ["external-template"],
        "apply_hooks": ["external.mode"],
    }


def test_plugins_http_api_returns_the_inventory(tmp_path: Path) -> None:
    with TestClient(
        create_app(tmp_path / "runs"), base_url="http://localhost"
    ) as client:
        response = client.get("/v1/plugins")
    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == 1
    assert set(payload) == {
        "api_version",
        "scenarios",
        "engines",
        "algorithms",
        "models",
    }


def test_plugins_cli_prints_valid_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["aeroagentsim", "plugins"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["api_version"] == 1
    assert set(payload) == {
        "api_version",
        "scenarios",
        "engines",
        "algorithms",
        "models",
    }
