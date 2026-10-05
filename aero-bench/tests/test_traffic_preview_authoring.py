from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aero_bench.authoring.workspace import migrate_workspace_v2
from aero_bench.authoring.traffic_preview import (
    TRAFFIC_PREVIEW_DEMAND_SCHEMA,
    demand_from_validated_workspace,
    parse_workspace_preview_demand,
)


def workspace() -> dict[str, object]:
    return {
        "purpose": "scenario-authoring",
        "schema_version": "aero-bench.city-workspace/v3",
        "name": "Traffic preview",
        "scenePath": "/city-presentation/default-scene-v1.json",
        "seed": 73,
        "environment": {
            "cloudCover": 0,
            "precipitation": "none",
            "precipitationRateMmPerH": 0,
            "visibilityM": 10000,
            "windMps": 0,
            "windDirectionDeg": 0,
            "timeOfDay": "day",
            "reflectionsEnabled": True,
        },
        "fleet": [],
        "traffic": {"vehicles": 14, "pedestrians": 9, "bicycles": 3},
        "facilities": [],
        "airspace": [],
        "algorithms": {
            "mode": "centralized",
            "assignment": "greedy",
            "routing": "astar",
            "energy": "reserve_threshold",
            "parameters": {},
        },
        "deployment": {"executor": "docker_reference", "imageRef": ""},
        "events": [],
        "actionRules": [],
        "stateKeyframes": [],
        "labelRules": [],
        "orders": [],
        "orderGeneration": {
            "seed": 73, "maxOrders": 0, "startAtS": 0, "endAtS": 3600,
            "cargoMinKg": 0.1, "cargoMaxKg": 1, "deadlineLeadS": 600,
        },
        "performanceProfiles": [],
        "authoredLandscape": [],
    }


def workspace_v2() -> dict[str, object]:
    value = workspace()
    value["schema_version"] = "aero-bench.city-workspace/v2"
    for field in ("orders", "orderGeneration", "performanceProfiles", "authoredLandscape"):
        value.pop(field)
    return value


def test_current_workspace_materializes_exact_seed_counts_and_source_bytes() -> None:
    raw = json.dumps(workspace(), separators=(",", ":")).encode()
    demand = parse_workspace_preview_demand(raw)
    assert demand.seed == 73
    assert (demand.motor_vehicles, demand.pedestrians, demand.bicycles) == (14, 9, 3)
    assert demand.source_identity() == {
        "schema_version": TRAFFIC_PREVIEW_DEMAND_SCHEMA,
        "workspace_schema_version": "aero-bench.city-workspace/v3",
        "workspace_sha256": hashlib.sha256(raw).hexdigest(),
        "workspace_size_bytes": len(raw),
        "seed": 73,
        "traffic": {"vehicles": 14, "pedestrians": 9, "bicycles": 3},
    }


def test_explicit_zero_categories_are_preserved() -> None:
    value = workspace()
    value["traffic"] = {"vehicles": 0, "pedestrians": 0, "bicycles": 0}
    demand = parse_workspace_preview_demand(json.dumps(value).encode())
    assert demand.source_identity()["traffic"] == {
        "vehicles": 0,
        "pedestrians": 0,
        "bicycles": 0,
    }


def test_v2_requires_the_explicit_migration_path() -> None:
    legacy = workspace_v2()
    with pytest.raises(ValidationError, match="schema_version|Field required"):
        parse_workspace_preview_demand(json.dumps(legacy).encode())
    migrated = migrate_workspace_v2(legacy).snapshot()
    demand = parse_workspace_preview_demand(json.dumps(migrated).encode())
    assert demand.workspace_schema_version == "aero-bench.city-workspace/v3"
    assert demand.seed == 73


@pytest.mark.parametrize(
    "mutation",
    [
        {"seed": -1},
        {"seed": 0.5},
        {"traffic": {"vehicles": -1, "pedestrians": 0, "bicycles": 0}},
        {"traffic": {"vehicles": 1, "pedestrians": 0}},
    ],
)
def test_invalid_workspace_demand_is_rejected_by_the_workspace_contract(mutation: dict) -> None:
    value = workspace()
    value.update(mutation)
    with pytest.raises(ValidationError):
        parse_workspace_preview_demand(json.dumps(value).encode())


def test_extractor_is_schema_neutral_after_the_caller_validates_a_draft() -> None:
    draft = SimpleNamespace(
        schema_version="aero-bench.city-workspace/v3",
        seed=17,
        traffic=SimpleNamespace(vehicles=2, pedestrians=0, bicycles=1),
    )
    demand = demand_from_validated_workspace(draft, workspace_bytes=b"validated-v3")
    assert demand.workspace_schema_version == "aero-bench.city-workspace/v3"
    assert (demand.seed, demand.motor_vehicles, demand.pedestrians, demand.bicycles) == (17, 2, 0, 1)


def test_source_identity_requires_exact_bytes() -> None:
    draft = SimpleNamespace(
        schema_version="aero-bench.city-workspace/v2",
        seed=1,
        traffic=SimpleNamespace(vehicles=0, pedestrians=0, bicycles=0),
    )
    with pytest.raises(TypeError, match="exact imported bytes"):
        demand_from_validated_workspace(draft, workspace_bytes=bytearray(b"mutable"))  # type: ignore[arg-type]
