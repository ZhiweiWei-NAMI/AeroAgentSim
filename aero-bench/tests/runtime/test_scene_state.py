from __future__ import annotations

from typing import Any

import pytest

from aero_bench.runtime.contracts import (
    SceneState,
    SimulationTime,
    StageBarrier,
    stage_barrier_digest_value,
)
from aero_bench.runtime.scene_state import SceneStateAssembler
from tests.world.support import deterministic_inspection_scenario


_RUN_ID = "a" * 64


def _assembler() -> SceneStateAssembler:
    source = deterministic_inspection_scenario()
    static_entity = next(entity for entity in source.entities if entity.state == "static")
    # The assembly contract is independent of the scenario's other projections.
    # Keep this fixture limited to one static entity so the barrier can be built
    # without introducing a provider test double.
    scenario = source.model_copy(update={"entities": (static_entity,)})
    return SceneStateAssembler(scenario)


def _barrier(
    assembler: SceneStateAssembler,
    at: SimulationTime,
) -> StageBarrier:
    scenario_digest = assembler._scenario.scenario_digest
    fields: dict[str, Any] = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": _RUN_ID,
        "scenario_digest": scenario_digest,
        "at": at,
        "stage": "motion",
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
        "provider_ids": (),
        "receipts": (),
        "receipt_digests": (),
    }
    candidate = StageBarrier.model_construct(**fields, barrier_digest="0" * 64)
    return StageBarrier(
        **fields,
        barrier_digest=stage_barrier_digest_value(candidate),
    )


def _assemble(
    assembler: SceneStateAssembler,
    tick: int,
    previous: SceneState | None = None,
) -> SceneState:
    at = SimulationTime(tick=tick, sim_time_ns=tick * 500_000_000)
    return assembler.assemble(
        run_id=_RUN_ID,
        at=at,
        barrier=_barrier(assembler, at),
        contributions=(),
        previous_scene_state=previous,
    )


def test_assembler_reuses_its_immutable_predecessor_without_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembler = _assembler()
    first = _assemble(assembler, 1)
    original_model_validate = SceneState.model_validate
    validation_calls: list[object] = []

    def record_validation(cls: type[SceneState], value: object, *args: object, **kwargs: object) -> SceneState:
        validation_calls.append(value)
        return original_model_validate(value, *args, **kwargs)

    monkeypatch.setattr(SceneState, "model_validate", classmethod(record_validation))

    second = _assemble(assembler, 2, previous=first)

    assert second.previous_scene_state_digest == first.scene_state_digest
    assert validation_calls == []


def test_assembler_strictly_revalidates_an_independently_supplied_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembler = _assembler()
    first = _assemble(assembler, 1)
    second = _assemble(assembler, 2, previous=first)
    original_model_validate = SceneState.model_validate
    validation_calls: list[object] = []

    def record_validation(cls: type[SceneState], value: object, *args: object, **kwargs: object) -> SceneState:
        validation_calls.append(value)
        return original_model_validate(value, *args, **kwargs)

    monkeypatch.setattr(SceneState, "model_validate", classmethod(record_validation))
    independently_supplied = original_model_validate(
        second.model_dump(mode="json")
    )
    validation_calls.clear()

    validated = assembler._validate_previous(
        run_id=_RUN_ID,
        at=SimulationTime(tick=3, sim_time_ns=1_500_000_000),
        previous_scene_state=independently_supplied,
    )

    assert validated == second
    assert validated is not independently_supplied
    assert len(validation_calls) == 1
