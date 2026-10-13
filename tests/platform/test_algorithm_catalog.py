"""GET /v1/algorithms catalog: builtins plus a mock installed entry point."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Iterator
from importlib.metadata import EntryPoint
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aeroagentsim.algorithms import BUILTINS
from aeroagentsim.services.algorithm_catalog import (
    BUILTIN_COPY,
    discover_algorithm_catalog,
    mount_algorithm_catalog,
)


class GreedyAllocation:
    """Mock installed plugin exercising the real allocation protocol."""

    description = "Give every open task to the first available vehicle."
    label_zh = "贪心分配"
    description_zh = "将每个待处理任务交给第一架空闲飞行器。"

    def allocate(self, snapshot: Any) -> tuple[Any, ...]:  # pragma: no cover
        return ()


@pytest.fixture()
def client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    """Test app with a mock installed entry point in the real registry group."""

    def fake_entry_points(*, group: str) -> list[EntryPoint]:
        if group == "aeroagentsim.engines":
            return []
        assert group == "aeroagentsim.algorithms"
        return [
            EntryPoint(
                name="allocation.greedy",
                value="tests.platform.test_algorithm_catalog:GreedyAllocation",
                group="aeroagentsim.algorithms",
            )
        ]

    monkeypatch.setattr(
        importlib.metadata, "entry_points", fake_entry_points, raising=True
    )
    app = FastAPI()
    mount_algorithm_catalog(app)
    with TestClient(app) as test_client:
        yield test_client, {}


def test_catalog_lists_builtin_and_installed_entries(
    client: tuple[TestClient, dict[str, Any]],
) -> None:
    test_client, _ = client
    response = test_client.get("/v1/algorithms")
    assert response.status_code == 200
    payload = response.json()
    slots = payload
    assert set(slots) == {
        "allocation",
        "path_planning",
        "conflict_resolution",
        "charging",
    }

    allocation_ids = [a["id"] for a in slots["allocation"]]
    # All builtin names from the real registry, plus the mock installed plugin.
    assert set(allocation_ids) == {"nearest", "auction", "hungarian", "llm", "greedy"}
    for slot, registered in BUILTINS.items():
        assert set(registered).issubset({row["id"] for row in slots[slot]})

    greedy = next(a for a in slots["allocation"] if a["id"] == "greedy")
    assert greedy["label"] == "Greedy"
    assert (
        greedy["description"] == "Give every open task to the first available vehicle."
    )

    nearest = next(a for a in slots["allocation"] if a["id"] == "nearest")
    assert nearest["label"] == "Nearest available drone"
    assert nearest["description"]  # friendly one-line builtin description

    # Every algorithm entry carries exactly the documented shape.
    consumers = {
        "allocation": [{"engine": "tasks", "plugin": "lowalt_tasks"}],
        "path_planning": [{"engine": "tasks", "plugin": "lowalt_tasks"}],
        "conflict_resolution": [{"engine": "airspace", "plugin": "lowalt_airspace"}],
        "charging": [{"engine": "energy", "plugin": "lowalt_energy"}],
    }
    for slot, algorithms in payload.items():
        for algorithm in algorithms:
            assert set(algorithm) == {
                "id", "label", "description", "label_zh", "description_zh", "consumers"
            }
            assert algorithm["consumers"] == consumers[slot]
            assert algorithm["label"] and algorithm["description"]
            for field in ("label_zh", "description_zh"):
                assert any("\u4e00" <= char <= "\u9fff" for char in algorithm[field])
                assert algorithm[field] != algorithm[field.removesuffix("_zh")]
    assert greedy["label_zh"] == "贪心分配"
    assert greedy["description_zh"] == GreedyAllocation.description_zh


@pytest.mark.parametrize("installed", [False, True])
def test_all_builtin_algorithms_translate_with_or_without_entry_points(
    installed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = []
    for slot, algorithms in BUILTINS.items():
        for identity, class_name in algorithms.items():
            module = (
                "routes" if identity in {"coverage", "survey", "patrol"}
                else "llm" if identity == "llm" else "builtins"
            )
            entries.append(
                EntryPoint(
                    name=f"{slot}.{identity}",
                    value=f"aeroagentsim.algorithms.{module}:{class_name}",
                    group="aeroagentsim.algorithms",
                )
            )
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda **_: entries if installed else []
    )
    catalog = discover_algorithm_catalog()
    assert sum(len(rows) for rows in catalog.values()) == 12
    for slot, rows in catalog.items():
        assert {row["id"] for row in rows} == set(BUILTINS[slot])
        for row in rows:
            assert row["label"] == BUILTIN_COPY[row["id"]][0]
            for field in ("label_zh", "description_zh"):
                assert row[field].strip()
                assert any("\u4e00" <= char <= "\u9fff" for char in row[field])
            if installed:
                entry = next(e for e in entries if e.name == f"{slot}.{row['id']}")
                factory = entry.load()
                assert row["label"] == factory.label
                assert row["label_zh"] == factory.label_zh
                assert row["description_zh"] == factory.description_zh
    hungarian = next(row for row in catalog["allocation"] if row["id"] == "hungarian")
    corridor = next(row for row in catalog["path_planning"] if row["id"] == "corridor_astar")
    assert corridor["label"] == "Corridor A*"
    assert corridor["label_zh"] == "安全走廊航线"
    assert "完成时间" in hungarian["description_zh"]
    if not installed:
        assert "estimated completion time" in hungarian["description"]


def test_override_plugin_does_not_inherit_a_builtin_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UntitledPlugin:
        description = "This plugin is not the nearest algorithm."
        label_zh = None
        description_zh = "  "

        def __init__(self) -> None:
            raise AssertionError("Catalog discovery must not instantiate plugins")

    class Entry:
        name = "allocation.nearest"

        def load(self) -> type[UntitledPlugin]:
            return UntitledPlugin

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_: [Entry()])
    nearest = next(
        row for row in discover_algorithm_catalog()["allocation"]
        if row["id"] == "nearest"
    )
    assert nearest["description"] == UntitledPlugin.description
    assert "label_zh" not in nearest and "description_zh" not in nearest
