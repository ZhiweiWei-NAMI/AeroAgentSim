from __future__ import annotations

from aero_bench.config.models import Sha256, StrictModel
from aero_bench.world.resolved import ResolvedScenario


class WorldScenePreflightError(RuntimeError):
    pass


class WorldSceneBindings(StrictModel):
    """Fail-closed ownership report for a compiler-resolved static scene."""

    scenario_digest: Sha256
    static_entity_ids: tuple[str, ...]
    dynamic_entity_ids: tuple[str, ...]
    ownership_violation_ids: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.ownership_violation_ids


def check_world_bindings(scenario: ResolvedScenario) -> WorldSceneBindings:
    """Verify that runtime providers own dynamics and never static declarations."""

    if not isinstance(scenario, ResolvedScenario):
        raise TypeError("world-scene preflight requires a ResolvedScenario")
    static_ids: list[str] = []
    dynamic_ids: list[str] = []
    violations: list[str] = []
    for entity in scenario.entities:
        if entity.state == "static":
            static_ids.append(entity.entity_id)
            if (
                entity.owner_kind != "scenario"
                or entity.owner_id != "scenario.compiler"
                or entity.source_provider_id is not None
                or entity.authority_kind != "scenario_static"
            ):
                violations.append(entity.entity_id)
            continue
        dynamic_ids.append(entity.entity_id)
        if (
            entity.owner_kind != "provider"
            or entity.source_provider_id != entity.owner_id
            or entity.authority_kind == "scenario_static"
        ):
            violations.append(entity.entity_id)
    return WorldSceneBindings(
        scenario_digest=scenario.scenario_digest,
        static_entity_ids=tuple(static_ids),
        dynamic_entity_ids=tuple(dynamic_ids),
        ownership_violation_ids=tuple(violations),
    )


def require_world_bindings(scenario: ResolvedScenario) -> WorldSceneBindings:
    report = check_world_bindings(scenario)
    if not report.ready:
        raise WorldScenePreflightError(
            "world-scene projection contains noncanonical entity ownership: "
            f"{list(report.ownership_violation_ids)}"
        )
    return report


__all__ = [
    "WorldSceneBindings",
    "WorldScenePreflightError",
    "check_world_bindings",
    "require_world_bindings",
]
