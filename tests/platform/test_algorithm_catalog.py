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
from aeroagentsim.services.algorithm_catalog import mount_algorithm_catalog


class GreedyAllocation:
    """Mock installed plugin exercising the real allocation protocol."""

    description = "Give every open task to the first available vehicle."

    def allocate(self, snapshot: Any) -> tuple[Any, ...]:  # pragma: no cover
        return ()


@pytest.fixture()
def client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    """Test app with a mock installed entry point in the real registry group."""

    def fake_entry_points(*, group: str) -> list[EntryPoint]:
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
    for slot in payload.values():
        for algorithm in slot:
            assert set(algorithm) == {"id", "label", "description"}
            assert algorithm["label"] and algorithm["description"]
