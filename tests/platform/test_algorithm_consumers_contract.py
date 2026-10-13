"""Algorithm scopes follow real readers, not copied configuration dictionaries."""

from __future__ import annotations

import ast
from importlib import metadata
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

from aeroagentsim.algorithms.consumers import READERS, slot_consumers
from aeroagentsim.services.algorithm_catalog import discover_algorithm_catalog


def test_consumers_agree_with_runtime_load_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "entry_points", lambda **_: [])
    consumers = slot_consumers()
    root = Path(__file__).resolve().parents[2] / "src"
    for plugin, (engine, factory, _) in READERS.items():
        module = factory.split(":")[0]
        source = root.joinpath(*module.split(".")).with_suffix(".py")
        calls = [node for node in ast.walk(ast.parse(source.read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "load_algorithm"]
        slots = {node.args[0].value for node in calls if isinstance(node.args[0], ast.Constant)}
        advertised = {slot for slot, rows in consumers.items() if {"engine": engine, "plugin": plugin} in rows}
        assert slots == advertised
    catalog = discover_algorithm_catalog()
    for slot, rows in catalog.items():
        assert all(row["consumers"] == consumers[slot] for row in rows)
    assert catalog["allocation"][0]["consumers"] == [{"engine": "tasks", "plugin": "lowalt_tasks"}]
    assert catalog["charging"][0]["consumers"] == [{"engine": "energy", "plugin": "lowalt_energy"}]


def test_engine_override_does_not_inherit_unverified_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    override = EntryPoint(name="lowalt_tasks", value="third_party.tasks:build", group="aeroagentsim.engines")
    monkeypatch.setattr(metadata, "entry_points", lambda **kw: [override] if kw["group"] == override.group else [])
    catalog = discover_algorithm_catalog()
    assert all(row["consumers"] == [] for slot in ("allocation", "path_planning") for row in catalog[slot])
    assert catalog["conflict_resolution"][0]["consumers"] == [{"engine": "airspace", "plugin": "lowalt_airspace"}]


def test_entry_point_factory_is_not_constructed_and_keeps_slot_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    class Plugin:
        label = "Research allocation"
        description = "A separately installed allocation implementation."

        def __init__(self) -> None:
            raise AssertionError("Discovery constructed an algorithm")

    class Entry:
        name = "allocation.research"

        def load(self) -> type[Plugin]:
            return Plugin

    monkeypatch.setattr(metadata, "entry_points", lambda **kw: [Entry()] if kw["group"] == "aeroagentsim.algorithms" else [])
    row = next(row for row in discover_algorithm_catalog()["allocation"] if row["id"] == "research")
    assert row["consumers"] == [{"engine": "tasks", "plugin": "lowalt_tasks"}]
