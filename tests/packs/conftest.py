"""P5 isolated suite: caches/artifacts stay in packs, Docker is explicit opt-in."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

os.environ.setdefault(
    "HYPOTHESIS_STORAGE_DIRECTORY", str(Path(__file__).parent / ".hypothesis")
)

import pytest
import yaml

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "scenarios" / "packs"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "docker: opt-in real PX4/Gazebo integration"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("markexpr") == "docker":
        return
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(
                pytest.mark.skip(
                    reason="real PX4/Gazebo integration requires -m docker"
                )
            )


def document(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], yaml.safe_load((SCENARIOS / f"{name}.yaml").read_text())
    )


@pytest.fixture
def small() -> dict[str, Any]:
    """One scheduled order, one carrier, same real fields and engines as full demo."""
    d = document("logistics-small")
    c = d["engines"]["logistics"]["config"]
    c["orders"] = c["orders"][:1]
    c["fleet"] = c["fleet"][:1]
    c["arrivals"] = {"mode": "scheduled", "times_ns": [0]}
    d["entities"] = [
        e
        for e in d["entities"]
        if not e["id"].startswith(("order-", "parcel-", "carrier-"))
        or e["id"] in {"order-1", "parcel-1", "carrier-1"}
    ]
    d["engines"]["acceptance"]["config"]["machines"] = d["engines"]["acceptance"][
        "config"
    ]["machines"][:1]
    d["run"].update(until_ns=20_000_000_000, advance_ns=1_000_000_000)
    return d


def simulation(d: dict[str, Any]) -> Simulation:
    return Simulation(load_scenario(d, base=SCENARIOS))


def no_acceptance(d: dict[str, Any]) -> None:
    d["engines"].pop("acceptance")
    d["bindings"]["rules"] = [
        r for r in d["bindings"]["rules"] if r["writer"] != "acceptance"
    ]
    for entity in d["entities"]:
        entity["facts"].pop("packs.order.review", None)
