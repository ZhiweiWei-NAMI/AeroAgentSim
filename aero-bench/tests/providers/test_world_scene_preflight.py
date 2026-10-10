from __future__ import annotations

import json

import pytest

from aero_bench.providers.world_scene import (
    WorldScenePreflightError,
    check_world_bindings,
    require_world_bindings,
)
from aero_bench.world.resolved import ResolvedScenario
from tests.providers.test_world_scene_service import _SELFCHECK


def scenario() -> ResolvedScenario:
    return ResolvedScenario.model_validate_json(
        json.dumps(_SELFCHECK._resolved_scenario(), allow_nan=False)
    )


def test_binding_report_separates_compiler_static_and_provider_dynamic_entities() -> None:
    report = check_world_bindings(scenario())
    assert report.static_entity_ids == ("static.selfcheck",)
    assert report.dynamic_entity_ids == ("uav.selfcheck",)
    assert report.ownership_violation_ids == ()
    assert report.ready


def test_static_entity_must_remain_scenario_compiler_owned() -> None:
    resolved = scenario()
    mutated = resolved.model_copy(
        update={
            "entities": tuple(
                entity.model_copy(
                    update={
                        "owner_kind": "provider",
                        "owner_id": "flight",
                        "source_provider_id": "flight",
                    }
                )
                if entity.state == "static"
                else entity
                for entity in resolved.entities
            )
        }
    )
    report = check_world_bindings(mutated)
    assert report.ownership_violation_ids == ("static.selfcheck",)
    assert not report.ready


def test_dynamic_entity_must_remain_provider_owned() -> None:
    resolved = scenario()
    mutated = resolved.model_copy(
        update={
            "entities": tuple(
                entity.model_copy(
                    update={
                        "owner_kind": "scenario",
                        "owner_id": "scenario.compiler",
                        "source_provider_id": None,
                    }
                )
                if entity.entity_id == "uav.selfcheck"
                else entity
                for entity in resolved.entities
            )
        }
    )
    report = check_world_bindings(mutated)
    assert report.ownership_violation_ids == ("uav.selfcheck",)
    assert not report.ready


def test_require_world_bindings_fails_closed_on_any_ownership_violation() -> None:
    resolved = scenario()
    static = next(entity for entity in resolved.entities if entity.state == "static")
    mutated = resolved.model_copy(
        update={
            "entities": tuple(
                static.model_copy(update={"authority_kind": "gazebo_physics"})
                if entity.entity_id == static.entity_id
                else entity
                for entity in resolved.entities
            )
        }
    )
    with pytest.raises(WorldScenePreflightError, match="noncanonical entity ownership"):
        require_world_bindings(mutated)
    assert require_world_bindings(resolved).ready


def test_preflight_requires_resolved_scenario() -> None:
    with pytest.raises(TypeError, match="ResolvedScenario"):
        check_world_bindings({})  # type: ignore[arg-type]
