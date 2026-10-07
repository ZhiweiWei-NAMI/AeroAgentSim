from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageResult,
    MotionStageResult,
    NetworkStageResult,
    ProviderStage,
    ProviderStageResult,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    StageReceipt,
    stage_barrier_digest_value,
    stage_receipt_digest_value,
)


_STAGE_ORDER: tuple[ProviderStage, ...] = (
    "motion",
    "network",
    "business_environment",
)
_STAGE_ORDER_INDEX = {stage: index for index, stage in enumerate(_STAGE_ORDER)}
_STAGE_RESULT_TYPES = (
    MotionStageResult,
    NetworkStageResult,
    BusinessEnvironmentStageResult,
)


@dataclass(frozen=True, slots=True)
class BarrierCommit:
    time: SimulationTime
    stage_barriers: tuple[StageBarrier, ...]


class ProviderBarrier:
    """Close ordered stage barriers inside one tick before advancing time."""

    def __init__(
        self,
        *,
        run_id: str,
        scenario_digest: str,
        step_ns: int,
        enabled_stages: tuple[ProviderStage, ...],
        provider_ids_by_stage: Mapping[ProviderStage, tuple[str, ...]],
    ):
        if step_ns <= 0:
            raise ValueError("step_ns must be positive")
        if not enabled_stages:
            raise ValueError("provider barrier requires an enabled motion stage")
        if enabled_stages[0] != "motion":
            raise ValueError("provider barrier must begin with the motion stage")
        if len(enabled_stages) != len(set(enabled_stages)):
            raise ValueError("enabled stages must be unique")
        if tuple(sorted(enabled_stages, key=_STAGE_ORDER_INDEX.__getitem__)) != enabled_stages:
            raise ValueError("enabled stages must follow the staged execution order")
        if set(provider_ids_by_stage) != set(enabled_stages):
            raise ValueError("stage provider declarations must close over enabled stages")

        stage_provider_ids: dict[ProviderStage, tuple[str, ...]] = {}
        for stage in enabled_stages:
            provider_ids = tuple(provider_ids_by_stage[stage])
            if provider_ids != tuple(sorted(provider_ids)):
                raise ValueError("stage provider IDs must be sorted")
            if len(provider_ids) != len(set(provider_ids)):
                raise ValueError("stage provider IDs must be unique")
            if stage != "motion" and not provider_ids:
                raise ValueError("optional enabled stages require at least one provider")
            stage_provider_ids[stage] = provider_ids

        self._run_id = run_id
        self._scenario_digest = scenario_digest
        self._step_ns = step_ns
        self._enabled_stages = enabled_stages
        self._provider_ids_by_stage = stage_provider_ids
        self._current = SimulationTime(tick=0, sim_time_ns=0)
        self._target: SimulationTime | None = None
        self._next_stage_index = 0
        self._active_stage: ProviderStage | None = None
        self._active_input_scene_state_digest: str | None = None
        self._active_predecessor_barriers: tuple[StageBarrierDigest, ...] = ()
        self._stage_results: dict[str, ProviderStageResult] = {}
        self._closed_stage_barriers: list[StageBarrier] = []

    @property
    def current(self) -> SimulationTime:
        return self._current

    @property
    def enabled_stages(self) -> tuple[ProviderStage, ...]:
        return self._enabled_stages

    @property
    def pending_provider_ids(self) -> frozenset[str]:
        if self._active_stage is None:
            return frozenset()
        return frozenset(
            self._provider_ids_by_stage[self._active_stage]
        ) - self._stage_results.keys()

    @property
    def active_stage(self) -> ProviderStage | None:
        return self._active_stage

    @property
    def active_predecessor_barriers(self) -> tuple[StageBarrierDigest, ...]:
        if self._active_stage is None:
            raise RuntimeError("provider barrier has no active stage")
        return self._active_predecessor_barriers

    @property
    def closed_stage_barriers(self) -> tuple[StageBarrier, ...]:
        return tuple(self._closed_stage_barriers)

    def begin_tick(self, target: SimulationTime) -> None:
        if self._target is not None:
            raise RuntimeError("provider barrier already has an active tick")
        if target.tick != self._current.tick + 1:
            raise ValueError("provider barrier target tick must advance by one")
        if target.sim_time_ns != target.tick * self._step_ns:
            raise ValueError(
                "provider barrier target time is inconsistent with step_ns"
            )
        self._target = target
        self._next_stage_index = 0
        self._active_stage = None
        self._active_input_scene_state_digest = None
        self._active_predecessor_barriers = ()
        self._stage_results = {}
        self._closed_stage_barriers = []

    def begin_stage(
        self,
        stage: ProviderStage,
        *,
        input_scene_state_digest: str | None = None,
    ) -> tuple[str, ...]:
        if self._target is None:
            raise RuntimeError("provider barrier has no active tick")
        if self._active_stage is not None:
            raise RuntimeError("provider barrier already has an active stage")
        if self._next_stage_index >= len(self._enabled_stages):
            raise RuntimeError("all enabled stages are already closed")
        expected_stage = self._enabled_stages[self._next_stage_index]
        if stage != expected_stage:
            raise ValueError(
                f"provider barrier expected stage {expected_stage}, received {stage}"
            )
        if stage == "motion":
            if input_scene_state_digest is not None:
                raise ValueError("motion stage cannot consume a SceneState digest")
        elif input_scene_state_digest is None:
            raise ValueError("non-motion stages require a SceneState digest")

        self._active_stage = stage
        self._active_input_scene_state_digest = input_scene_state_digest
        self._active_predecessor_barriers = tuple(
            StageBarrierDigest(
                stage=barrier.stage,
                barrier_digest=barrier.barrier_digest,
            )
            for barrier in self._closed_stage_barriers
        )
        self._stage_results = {}
        return self._provider_ids_by_stage[stage]

    def submit(self, result: ProviderStageResult) -> None:
        if self._target is None or self._active_stage is None:
            raise RuntimeError("provider result arrived without an active stage")
        if not isinstance(result, _STAGE_RESULT_TYPES):
            raise TypeError("provider stage result has an unsupported contract type")
        try:
            canonical = type(result).model_validate(result.model_dump(mode="json"))
        except (TypeError, ValueError) as error:
            raise ValueError("provider stage result fails strict revalidation") from error
        if canonical != result:
            raise ValueError("provider stage result is not canonically serialized")
        if (
            result.run_id != self._run_id
            or result.scenario_digest != self._scenario_digest
            or result.target != self._target
            or result.stage != self._active_stage
        ):
            raise ValueError("provider stage result binding is inconsistent")
        expected_provider_ids = self._provider_ids_by_stage[self._active_stage]
        if result.provider_id not in expected_provider_ids:
            raise ValueError("provider result came from an undeclared stage provider")
        if result.provider_id in self._stage_results:
            raise ValueError("provider submitted multiple results for one stage")
        if result.predecessor_barriers != self._active_predecessor_barriers:
            raise ValueError("provider result predecessor inventory is inconsistent")
        result_input_scene_state_digest = getattr(
            result,
            "input_scene_state_digest",
            None,
        )
        if result_input_scene_state_digest != self._active_input_scene_state_digest:
            raise ValueError("provider result SceneState input digest is inconsistent")
        for event in result.step_receipt.events:
            if (
                event.time.tick < self._current.tick
                or event.time.sim_time_ns < self._current.sim_time_ns
            ):
                raise ValueError("provider event moved backwards across a barrier")
        self._stage_results[result.provider_id] = result

    def close_stage(self) -> StageBarrier:
        if self._target is None or self._active_stage is None:
            raise RuntimeError("provider barrier has no active stage to close")
        missing = self.pending_provider_ids
        if missing:
            raise RuntimeError(f"provider results are missing: {sorted(missing)}")

        stage = self._active_stage
        provider_ids = self._provider_ids_by_stage[stage]
        receipts = tuple(
            self._stage_receipt_from_result(
                self._stage_results[provider_id],
                input_scene_state_digest=self._active_input_scene_state_digest,
                predecessor_barriers=self._active_predecessor_barriers,
            )
            for provider_id in provider_ids
        )
        fields = {
            "schema_version": "aero-bench.stage-barrier/v1",
            "run_id": self._run_id,
            "scenario_digest": self._scenario_digest,
            "at": self._target,
            "stage": stage,
            "input_scene_state_digest": self._active_input_scene_state_digest,
            "predecessor_barriers": self._active_predecessor_barriers,
            "provider_ids": provider_ids,
            "receipts": receipts,
            "receipt_digests": tuple(receipt.receipt_digest for receipt in receipts),
        }
        candidate = StageBarrier.model_construct(
            **fields,
            barrier_digest="0" * 64,
        )
        barrier = StageBarrier(
            **fields,
            barrier_digest=stage_barrier_digest_value(candidate),
        )
        self._closed_stage_barriers.append(barrier)
        self._next_stage_index += 1
        self._active_stage = None
        self._active_input_scene_state_digest = None
        self._active_predecessor_barriers = ()
        self._stage_results = {}
        return barrier

    def commit_tick(self) -> BarrierCommit:
        if self._target is None:
            raise RuntimeError("provider barrier has no active tick")
        if self._active_stage is not None:
            raise RuntimeError("provider barrier cannot commit an active stage")
        if self._next_stage_index != len(self._enabled_stages):
            expected = self._enabled_stages[self._next_stage_index]
            raise RuntimeError(f"provider barrier has not closed stage {expected}")
        commit = BarrierCommit(
            time=self._target,
            stage_barriers=tuple(self._closed_stage_barriers),
        )
        self._current = self._target
        self._target = None
        self._next_stage_index = 0
        self._closed_stage_barriers = []
        return commit

    def _stage_receipt_from_result(
        self,
        result: ProviderStageResult,
        *,
        input_scene_state_digest: str | None,
        predecessor_barriers: tuple[StageBarrierDigest, ...],
    ) -> StageReceipt:
        fields = {
            "schema_version": "aero-bench.stage-receipt/v1",
            "run_id": result.run_id,
            "scenario_digest": result.scenario_digest,
            "at": result.target,
            "stage": result.stage,
            "provider_id": result.provider_id,
            "state_digest": result.step_receipt.state_digest,
            "step_receipt_digest": result.step_receipt_digest,
            "contribution_digest": result.contribution.contribution_digest,
            "payload_digest": result.contribution.payload_digest,
            "input_scene_state_digest": input_scene_state_digest,
            "predecessor_barriers": predecessor_barriers,
        }
        candidate = StageReceipt.model_construct(
            **fields,
            receipt_digest="0" * 64,
        )
        return StageReceipt(
            **fields,
            receipt_digest=stage_receipt_digest_value(candidate),
        )
