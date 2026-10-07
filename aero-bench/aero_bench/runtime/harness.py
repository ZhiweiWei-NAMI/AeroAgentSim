from __future__ import annotations

import asyncio
import sys
import time as wall_clock
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from pydantic import TypeAdapter

from aero_bench.config.models import Identifier, NamedValue, ProviderRef
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.contracts import ProviderSession
from aero_bench.providers.stages import ordered_enabled_stages
from aero_bench.runtime.barrier import BarrierCommit, ProviderBarrier
from aero_bench.runtime.contracts import (
    AgentTurnCompletion,
    AgentTurnDecision,
    BusinessEnvironmentStageRequest,
    BusinessEnvironmentStageResult,
    FinalizedArtifact,
    MotionStageRequest,
    MotionStageResult,
    NetworkStageRequest,
    NetworkStageResult,
    ProviderEvent,
    ProviderFinalizationReceipt,
    ProviderFinalizationRequest,
    ProviderStage,
    ProviderStageRequest,
    ProviderStageResult,
    SceneState,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    StageReceipt,
    StateSample,
    StepReceipt,
)
from aero_bench.runtime.control import (
    RuntimeControlOperation,
    RuntimeControlReceipt,
    RuntimeControlStatus,
    RuntimeProjectionBatch,
)
from aero_bench.runtime.evidence import (
    harness_identity_payload,
    provider_identity_payload,
)
from aero_bench.runtime.events import (
    AgentInteractionType,
    ProcessStreamChunk,
    RunEventAudience,
    RunEventSourceKind,
)
from aero_bench.runtime.hooks import RuntimeHook
from aero_bench.runtime.scene_history import (
    IncrementalSceneStateHistory,
    scene_state_jsonl_bytes,
)
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.runtime.ledger import (
    RESERVED_RUNTIME_EVENT_TYPES,
    EventLedger,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.vocabulary import (
    PUBLIC_EVENT_EVENT_TYPE,
    PUBLIC_NETWORK_LINK_EVENT_TYPE,
    PUBLIC_PROVIDER_EVENT_TYPES,
    PUBLIC_SENSOR_FRAME_EVENT_TYPE,
    PUBLIC_STATUS_EVENT_TYPE,
    PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE,
)


class HarnessRuntimeError(RuntimeError):
    pass


class _HarnessStopRequested(HarnessRuntimeError):
    pass


AbortFailureClass = Literal[
    "runtime_failed",
    "prepare_failed",
    "signal",
    "cancelled",
]
_ABORT_FAILURE_CLASSES = frozenset(
    {"runtime_failed", "prepare_failed", "signal", "cancelled"}
)


def _startup_provider_progress(
    *,
    operation: str,
    provider_id: str,
    state: Literal["started", "complete", "failed"],
    started_at: float,
    error_type: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "kind": "harness-provider-startup-progress",
        "operation": operation,
        "provider_id": provider_id,
        "state": state,
        "elapsed_ms": max(
            0, round((wall_clock.monotonic() - started_at) * 1000)
        ),
    }
    if error_type is not None:
        payload["error_type"] = error_type
    sys.stderr.write(canonical_json_bytes(payload).decode("utf-8") + "\n")
    sys.stderr.flush()


@dataclass(frozen=True, slots=True)
class AbortReport:
    """Controlled outcome of a best-effort Provider abort."""

    failure_class: AbortFailureClass
    provider_failures: tuple[str, ...]


class HarnessCoordinator:
    """Coordinates real Provider sessions without implementing their domain behavior."""

    _MAX_PROCESS_STREAM_BYTES = 256 * 1024

    def __init__(
        self,
        *,
        run: ResolvedRunSpec,
        providers: dict[str, ProviderSession],
        ledger: EventLedger,
        scene_history_staging_path: Path | None = None,
    ):
        if not isinstance(run, ResolvedRunSpec):
            raise TypeError("HarnessCoordinator.run must be ResolvedRunSpec")
        declared = {provider.provider_id for provider in run.environment.providers}
        if ledger.run_id != run.run_id:
            raise ValueError("Harness EventLedger belongs to another ResolvedRun")
        if ledger.records:
            raise ValueError("Harness requires a fresh authoritative EventLedger")
        if set(providers) != declared:
            raise ValueError(
                "Provider sessions must exactly match ResolvedRun: "
                f"missing={sorted(declared - set(providers))}, "
                f"undeclared={sorted(set(providers) - declared)}"
            )
        for provider in run.environment.providers:
            manifest = providers[provider.provider_id].manifest
            if manifest.provider_id != provider.provider_id:
                raise ValueError("Provider session manifest has the wrong provider_id")
            if manifest.adapter != provider.adapter:
                raise ValueError("Provider session adapter does not match ResolvedRun")
            if manifest.implementation != provider.workload.implementation:
                raise ValueError(
                    "Provider session implementation does not match ResolvedRun"
                )
            if manifest.runtime_image != provider.workload.runtime.image:
                raise ValueError("Provider session image does not match ResolvedRun")
            if manifest.config_digest != provider.config.file.sha256:
                raise ValueError(
                    "Provider session config digest does not match ResolvedRun"
                )
            if manifest.capabilities != provider.capabilities:
                raise ValueError(
                    "Provider session capabilities do not match ResolvedRun"
                )
            if manifest.protocol_schema != provider.protocol_schema:
                raise ValueError(
                    "Provider session protocol schema does not match ResolvedRun"
                )
            if manifest.artifact_requirements != provider.artifact_requirements:
                raise ValueError("Provider session artifacts do not match ResolvedRun")

        self._run = run
        self._providers = dict(providers)
        self._provider_order = tuple(sorted(providers))
        self._ledger = ledger
        self._px4_provider_ids = frozenset(
            provider.provider_id
            for provider in run.environment.providers
            if provider.adapter == "px4.gazebo"
        )
        # runtime_stage is the single execution-stage authority. Group Providers
        # purely by the frozen ResolvedProvider.runtime_stage. Roles, capabilities,
        # and adapter naming never decide stage.
        scenario_provider_ids = {p.provider_id for p in run.scenario.providers}
        if scenario_provider_ids != declared:
            raise ValueError(
                "ResolvedRun scenario Provider set must equal environment Providers: "
                f"scenario={sorted(scenario_provider_ids)}, "
                f"environment={sorted(declared)}"
            )
        all_stage_ids: dict[ProviderStage, tuple[str, ...]] = {
            stage: tuple(
                sorted(
                    provider.provider_id
                    for provider in run.scenario.providers
                    if provider.runtime_stage == stage
                )
            )
            for stage in ("motion", "network", "business_environment")
        }
        # Keep the strong SceneStateAssembler equality: the motion Provider set
        # must exactly equal provider-owned dynamic entity owners.
        dynamic_owner_ids = tuple(
            sorted(
                {
                    entity.owner_id
                    for entity in run.scenario.entities
                    if entity.state == "dynamic" and entity.owner_kind == "provider"
                }
            )
        )
        if all_stage_ids["motion"] != dynamic_owner_ids:
            raise ValueError(
                "motion stage Provider IDs must exactly equal provider-owned "
                "dynamic entity owner IDs"
            )
        enabled_stages = ordered_enabled_stages(all_stage_ids)
        stage_provider_ids: dict[ProviderStage, tuple[str, ...]] = {
            stage: all_stage_ids[stage] for stage in enabled_stages
        }
        declared_stage_memberships = tuple(
            provider_id
            for stage_ids in stage_provider_ids.values()
            for provider_id in stage_ids
        )
        declared_stage_provider_ids = set(declared_stage_memberships)
        if len(declared_stage_memberships) != len(declared_stage_provider_ids):
            raise ValueError("ResolvedRun Provider cannot belong to multiple runtime stages")
        if undeclared_stage_provider_ids := declared_stage_provider_ids - declared:
            raise ValueError(
                "ResolvedRun stage membership names undeclared Providers: "
                f"{sorted(undeclared_stage_provider_ids)}"
            )
        if unassigned_provider_ids := declared - declared_stage_provider_ids:
            raise ValueError(
                "ResolvedRun Providers must each belong to an enabled runtime stage: "
                f"{sorted(unassigned_provider_ids)}"
            )
        self._barrier = ProviderBarrier(
            run_id=run.run_id,
            scenario_digest=run.scenario.scenario_digest,
            step_ns=run.environment.clock.step_ns,
            enabled_stages=tuple(enabled_stages),
            provider_ids_by_stage=stage_provider_ids,
        )
        self._scene_state_assembler = SceneStateAssembler(run.scenario)
        # Urban history is durably indexed in the private staging writer; only
        # Inspection retains its small legacy in-memory history.  The writer's
        # latest sample and byte index are the live urban source of truth.
        self._scene_state_history: list[SceneState] = []
        self._scene_state_history_writer: IncrementalSceneStateHistory | None = None
        self._scene_state_staging_discarded = False
        if scene_history_staging_path is not None:
            if run.task.package.package_id != "urban.uav-recovery-demo.v1":
                raise ValueError(
                    "SceneState history staging is only supported for the urban recovery package"
                )
            self._scene_state_history_writer = IncrementalSceneStateHistory(
                scene_history_staging_path,
                run_id=run.run_id,
                scenario_digest=run.scenario.scenario_digest,
            )
        self._active_agents = {agent.agent_id for agent in run.agents}
        self._turn_participants = set(self._active_agents)
        self._finished_agents: set[str] = set()
        self._pending_completions: dict[str, AgentTurnCompletion] = {}
        self._completion_results: dict[str, tuple[bytes, AgentTurnDecision]] = {}
        stream_roles: dict[str, Literal["agent", "agent_driver", "harness", "provider"]] = {
            "harness": "harness",
            **{provider_id: "provider" for provider_id in self._provider_order},
            **{agent.agent_id: "agent" for agent in run.agents},
            **{agent.driver.driver_id: "agent_driver" for agent in run.agents if agent.driver is not None},
        }
        if len(stream_roles) != 1 + len(self._provider_order) + len(run.agents) + sum(agent.driver is not None for agent in run.agents):
            raise ValueError("runtime workload IDs must be globally unique")
        self._process_stream_roles = stream_roles
        self._process_stream_progress: dict[
            tuple[str, str], tuple[int, int, int, bool]
        ] = {
            (workload_id, stream): (0, 0, 0, False)
            for workload_id in stream_roles
            for stream in ("stdout", "stderr")
        }
        self._turn_lock = asyncio.Lock()
        self._control_condition = asyncio.Condition()
        self._pause_requested = False
        self._stop_requested = False
        self._tick_in_progress = False
        self._step_in_progress = False
        self._step_budget = 0
        self._control_results: dict[
            str, tuple[RuntimeControlOperation, RuntimeControlReceipt]
        ] = {}
        self._control_inflight: dict[str, RuntimeControlOperation] = {}
        self._prepared = False
        self._shutdown = False
        self._failed: str | None = None
        self._abort_report: AbortReport | None = None
        self._provider_shutdown_failures: tuple[str, ...] = ()
        self._runtime_hook: RuntimeHook | None = None
        self._runtime_hook_time: SimulationTime | None = None

    @property
    def current(self) -> SimulationTime:
        return self._barrier.current

    @property
    def runtime_hook_time(self) -> SimulationTime:
        """Return the target only while executing the task-local RuntimeHook."""

        if self._runtime_hook_time is not None:
            return self._runtime_hook_time
        return self.current

    @property
    def active_agent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active_agents))

    @property
    def missing_agent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._turn_participants - self._pending_completions.keys()))

    @property
    def provider_sessions(self) -> Mapping[str, ProviderSession]:
        """Validated Provider sessions without exposing a mutable registry."""

        return MappingProxyType(self._providers)

    @property
    def scene_state_history(self) -> tuple[SceneState, ...]:
        """Return the immutable history, loading urban pages only at seal time."""

        writer = self._scene_state_history_writer
        if writer is None:
            return tuple(self._scene_state_history)
        aborted_before_first_motion = (
            writer.count == 0
            and self._ledger.latest_record is not None
            and self._ledger.latest_record.event.event_type == "run.aborted"
        )
        return writer.read_all(
            aborted_before_first_motion=aborted_before_first_motion,
        )

    def seal_scene_state_history(self) -> bytes:
        """Close and verify the incremental urban history before publication."""

        if self._scene_state_staging_discarded:
            raise HarnessRuntimeError(
                "SceneState history staging was already discarded"
            )
        writer = self._scene_state_history_writer
        if writer is None:
            aborted_before_first_motion = (
                not self._scene_state_history
                and self._ledger.latest_record is not None
                and self._ledger.latest_record.event.event_type == "run.aborted"
            )
            return scene_state_jsonl_bytes(
                tuple(self._scene_state_history),
                aborted_before_first_motion=aborted_before_first_motion,
            )
        aborted_before_first_motion = (
            writer.count == 0
            and self._ledger.latest_record is not None
            and self._ledger.latest_record.event.event_type == "run.aborted"
        )
        try:
            return writer.seal(
                aborted_before_first_motion=aborted_before_first_motion,
            )
        except ValueError as error:
            raise HarnessRuntimeError(
                "incremental SceneState history sealing failed"
            ) from error

    def discard_scene_state_staging(self) -> None:
        """Remove private staging bytes after their sealed artifact is written."""

        writer = self._scene_state_history_writer
        if writer is not None:
            writer.discard()
        self._scene_state_staging_discarded = True

    @property
    def latest_scene_state(self) -> SceneState | None:
        writer = self._scene_state_history_writer
        if writer is not None:
            return writer.latest
        if not self._scene_state_history:
            return None
        return self._scene_state_history[-1]

    @property
    def failed(self) -> bool:
        return self._failed is not None

    def install_runtime_hook(self, hook: RuntimeHook) -> None:
        if self._prepared or self._shutdown:
            raise HarnessRuntimeError(
                "runtime hook must be installed before Harness preparation"
            )
        if self._runtime_hook is not None:
            raise HarnessRuntimeError("Harness runtime hook is already installed")
        if not callable(
            getattr(hook, "on_validated_observation", None)
        ) or not callable(getattr(hook, "on_stage_barriers_closed", None)):
            raise TypeError("runtime hook does not implement the required callbacks")
        self._runtime_hook = hook

    def ingest_process_stream(self, chunk: ProcessStreamChunk) -> str:
        """Append one authenticated, bounded workload stream chunk as a RunEvent."""

        if not isinstance(chunk, ProcessStreamChunk):
            raise TypeError("process stream chunk is invalid")
        if not self._prepared or self._shutdown:
            raise HarnessRuntimeError("process stream ingestion is not open")
        if chunk.run_id != self._run.run_id:
            raise HarnessRuntimeError("process stream chunk belongs to another run")
        expected_role = self._process_stream_roles.get(chunk.workload_id)
        if expected_role is None or chunk.workload_role != expected_role:
            raise HarnessRuntimeError("process stream workload identity is invalid")
        expected_content_class = (
            "explicit_output" if expected_role == "agent" else "system_log"
        )
        if chunk.content_class != expected_content_class:
            raise HarnessRuntimeError("process stream content class is invalid")

        key = (chunk.workload_id, chunk.stream)
        next_sequence, next_offset, total_bytes, closed = (
            self._process_stream_progress.get(key, (0, 0, 0, False))
        )
        if closed:
            raise HarnessRuntimeError("process stream is already closed")
        if chunk.sequence != next_sequence or chunk.byte_offset != next_offset:
            raise HarnessRuntimeError("process stream chunk is not contiguous")
        updated_total = total_bytes + chunk.payload_size_bytes
        if updated_total > self._MAX_PROCESS_STREAM_BYTES:
            raise HarnessRuntimeError("process stream exceeds its run-scoped byte bound")

        correlation_id = f"process.{chunk.workload_id}.{chunk.stream}"
        source_kind: RunEventSourceKind = "executor" if expected_role == "agent_driver" else expected_role
        interaction_type: AgentInteractionType = (
            "process.stdout.v1"
            if chunk.stream == "stdout"
            else "process.stderr.v1"
        )
        stream_time = self.current
        latest_record = self._ledger.latest_record
        if latest_record is not None:
            ledger_time = latest_record.event.time
            if (
                stream_time.tick < ledger_time.tick
                or stream_time.sim_time_ns < ledger_time.sim_time_ns
            ):
                stream_time = ledger_time
        record = self._ledger.append_event(
            source=chunk.workload_id,
            source_kind=source_kind,
            workload_id=chunk.workload_id,
            event_type=f"process.{chunk.stream}",
            time=stream_time,
            wall_time_ns=chunk.captured_wall_time_ns,
            payload=(
                NamedValue(name="byte_offset", value=chunk.byte_offset),
                NamedValue(
                    name="captured_wall_time_ns",
                    value=chunk.captured_wall_time_ns,
                ),
                NamedValue(name="content_class", value=chunk.content_class),
                NamedValue(name="final", value=chunk.final),
                NamedValue(name="payload_base64", value=chunk.payload_base64),
                NamedValue(
                    name="payload_sha256", value=chunk.payload_sha256
                ),
                NamedValue(
                    name="payload_size_bytes", value=chunk.payload_size_bytes
                ),
                NamedValue(name="run_id", value=chunk.run_id),
                NamedValue(name="sequence", value=chunk.sequence),
                NamedValue(name="stream", value=chunk.stream),
                NamedValue(name="truncated", value=chunk.truncated),
                NamedValue(name="workload_id", value=chunk.workload_id),
                NamedValue(name="workload_role", value=chunk.workload_role),
            ),
            payload_schema_id=f"process.{chunk.stream}.v1",
            interaction_type=interaction_type,
            correlation_id=correlation_id,
            parent_event_id=self._ledger.latest_event_id(correlation_id),
            agent_id=(
                chunk.workload_id if expected_role == "agent" else None
            ),
            provider_id=(
                chunk.workload_id if expected_role == "provider" else None
            ),
        )
        self._process_stream_progress[key] = (
            next_sequence + 1,
            next_offset + chunk.payload_size_bytes,
            updated_total,
            chunk.final,
        )
        return record.event.event_id

    def _close_process_streams_for_terminal(self) -> None:
        if not self._prepared:
            return
        empty_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        for workload_id, stream in sorted(self._process_stream_progress):
            sequence, byte_offset, _, closed = self._process_stream_progress[
                (workload_id, stream)
            ]
            if closed:
                continue
            role = self._process_stream_roles[workload_id]
            self.ingest_process_stream(
                ProcessStreamChunk(
                    schema_version="aero-bench.process-stream-chunk/v1",
                    run_id=self._run.run_id,
                    workload_id=workload_id,
                    workload_role=role,
                    stream=stream,
                    content_class=(
                        "explicit_output" if role == "agent" else "system_log"
                    ),
                    sequence=sequence,
                    byte_offset=byte_offset,
                    captured_wall_time_ns=wall_clock.time_ns(),
                    payload_base64="",
                    payload_size_bytes=0,
                    payload_sha256=empty_digest,
                    final=True,
                    truncated=True,
                )
            )

    @property
    def process_streams_closed(self) -> bool:
        return all(
            progress[3] for progress in self._process_stream_progress.values()
        )

    async def control_runtime(
        self,
        operation: RuntimeControlOperation,
        *,
        control_id: str | None = None,
    ) -> RuntimeControlReceipt:
        if operation == "status":
            if control_id is not None:
                raise HarnessRuntimeError("runtime status cannot name a control_id")
            return RuntimeControlReceipt(
                schema_version="aero-bench.runtime-control-receipt/v1",
                operation=operation,
                control_id=None,
                status=self.runtime_control_status(),
                audit_event_id=None,
            )
        if operation not in {"pause", "resume", "step", "stop"}:
            raise HarnessRuntimeError("runtime control operation is invalid")
        try:
            validated_control_id = TypeAdapter(Identifier).validate_python(control_id)
        except (TypeError, ValueError) as error:
            raise HarnessRuntimeError(
                "state-changing runtime control requires a valid control_id"
            ) from error

        try:
            async with self._control_condition:
                previous = self._control_results.get(validated_control_id)
                if previous is not None:
                    previous_operation, previous_receipt = previous
                    if previous_operation != operation:
                        raise HarnessRuntimeError(
                            "control_id was reused for another operation"
                        )
                    return previous_receipt
                inflight = self._control_inflight.get(validated_control_id)
                if inflight is not None:
                    if inflight != operation:
                        raise HarnessRuntimeError(
                            "control_id was reused for another operation"
                        )
                    raise HarnessRuntimeError("runtime control request is in progress")
                if self._shutdown:
                    raise HarnessRuntimeError("runtime control is already terminal")
                if not self._prepared:
                    raise HarnessRuntimeError("runtime control is not ready")
                if operation == "step" and (
                    not self._pause_requested
                    or self._tick_in_progress
                    or self._step_budget
                ):
                    raise HarnessRuntimeError(
                        "single step requires an idle paused runtime"
                    )
                self._control_inflight[validated_control_id] = operation
                audit_event_id = self._append_control_event(
                    operation,
                    control_id=validated_control_id,
                )
                if operation == "pause":
                    self._pause_requested = True
                elif operation == "resume":
                    self._pause_requested = False
                    self._step_budget = 0
                elif operation == "step":
                    self._step_budget = 1
                else:
                    self._stop_requested = True
                    self._step_budget = 0
                self._control_condition.notify_all()

            if operation == "stop":
                await self.abort(failure_class="cancelled")
            receipt = RuntimeControlReceipt(
                schema_version="aero-bench.runtime-control-receipt/v1",
                operation=operation,
                control_id=validated_control_id,
                status=self.runtime_control_status(),
                audit_event_id=audit_event_id,
            )
            async with self._control_condition:
                self._control_inflight.pop(validated_control_id, None)
                self._control_results[validated_control_id] = (operation, receipt)
                self._control_condition.notify_all()
            return receipt
        except BaseException:
            async with self._control_condition:
                self._control_inflight.pop(validated_control_id, None)
                self._control_condition.notify_all()
            raise

    def runtime_control_status(self) -> RuntimeControlStatus:
        latest_record = self._ledger.latest_record
        terminal_type = latest_record.event.event_type if latest_record is not None else None
        if self._shutdown and terminal_type == "run.completed":
            phase = "completed"
        elif self._shutdown and terminal_type == "run.aborted":
            phase = "aborted"
        elif self._failed is not None:
            phase = "failed"
        elif self._stop_requested:
            phase = "stopping"
        elif not self._prepared:
            phase = "starting"
        elif self._step_in_progress:
            phase = "stepping"
        elif self._pause_requested and self._tick_in_progress:
            phase = "pausing"
        elif self._pause_requested:
            phase = "paused"
        else:
            phase = "running"
        return RuntimeControlStatus(
            schema_version="aero-bench.runtime-control-status/v1",
            run_id=self._run.run_id,
            phase=phase,
            current=self.current,
            tick_in_progress=self._tick_in_progress,
            step_budget=self._step_budget,
            event_chain_root=self._ledger.chain_root,
            latest_event_id=(
                latest_record.event.event_id if latest_record is not None else None
            ),
        )

    def runtime_projection(
        self,
        *,
        after_scene_tick: int,
        after_event_sequence: int,
        max_scene_states: int,
        max_events: int,
    ) -> RuntimeProjectionBatch:
        for label, value, minimum, maximum in (
            ("after_scene_tick", after_scene_tick, 0, 2**63 - 1),
            ("after_event_sequence", after_event_sequence, -1, 2**63 - 1),
            ("max_scene_states", max_scene_states, 1, 16),
            ("max_events", max_events, 1, 256),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise HarnessRuntimeError(f"runtime projection {label} is invalid")
        scene_count = (
            self._scene_state_history_writer.count
            if self._scene_state_history_writer is not None
            else len(self._scene_state_history)
        )
        if after_scene_tick > scene_count:
            raise HarnessRuntimeError("runtime projection SceneState cursor is ahead")
        latest_event_sequence = self._ledger.record_count - 1
        if after_event_sequence > latest_event_sequence:
            raise HarnessRuntimeError("runtime projection RunEvent cursor is ahead")

        try:
            if self._scene_state_history_writer is not None:
                states = self._scene_state_history_writer.read_page(
                    after_tick=after_scene_tick,
                    limit=max_scene_states,
                )
            else:
                states = tuple(
                    self._scene_state_history[
                        after_scene_tick : after_scene_tick + max_scene_states
                    ]
                )
        except ValueError as error:
            raise HarnessRuntimeError(
                "runtime projection SceneState page is unavailable"
            ) from error
        public_audience = RunEventAudience(scope="public", audience_id=None)
        available_events = tuple(
            record.event
            for record in self._ledger.iter_records()
            if record.event.sequence > after_event_sequence
            and public_audience in record.event.visibility
        )
        events = available_events[:max_events]
        events_have_more = len(events) < len(available_events)
        status = self.runtime_control_status()
        return RuntimeProjectionBatch(
            schema_version="aero-bench.runtime-projection-batch/v1",
            run_id=self._run.run_id,
            after_scene_tick=after_scene_tick,
            after_event_sequence=after_event_sequence,
            next_scene_tick=(
                states[-1].at.tick if states else after_scene_tick
            ),
            next_event_sequence=(
                events[-1].sequence if events_have_more else latest_event_sequence
            ),
            event_chain_root=self._ledger.chain_root,
            scene_states=states,
            events=events,
            runtime_terminal=status.phase in {"completed", "aborted", "failed"},
            has_more=(
                after_scene_tick + len(states) < scene_count
                or events_have_more
            ),
        )

    async def _await_control_permission(self) -> None:
        async with self._control_condition:
            while True:
                if self._stop_requested:
                    raise _HarnessStopRequested("runtime stop was requested")
                if not self._pause_requested:
                    self._tick_in_progress = True
                    return
                if self._step_budget == 1:
                    self._step_budget = 0
                    self._step_in_progress = True
                    self._tick_in_progress = True
                    return
                await self._control_condition.wait()

    async def _finish_controlled_tick(self) -> None:
        async with self._control_condition:
            self._tick_in_progress = False
            self._step_in_progress = False
            self._control_condition.notify_all()

    def _append_control_event(
        self,
        operation: RuntimeControlOperation,
        *,
        control_id: str,
    ) -> str:
        event_time = self.current
        latest_record = self._ledger.latest_record
        if latest_record is not None:
            ledger_time = latest_record.event.time
            if (
                event_time.tick < ledger_time.tick
                or event_time.sim_time_ns < ledger_time.sim_time_ns
            ):
                event_time = ledger_time
        record = self._ledger.append_event(
            source="executor",
            source_kind="executor",
            workload_id="executor",
            event_type=f"control.{operation}",
            time=event_time,
            payload=(
                NamedValue(name="control_id", value=control_id),
                NamedValue(name="operation", value=operation),
                NamedValue(name="run_id", value=self._run.run_id),
            ),
            payload_schema_id="runtime.control.v1",
            correlation_id="runtime.control",
            parent_event_id=self._ledger.latest_event_id("runtime.control"),
            visibility=(
                RunEventAudience(scope="operator", audience_id=None),
                RunEventAudience(scope="private", audience_id=None),
            ),
        )
        return record.event.event_id

    @property
    def shutdown_complete(self) -> bool:
        return self._shutdown

    @property
    def provider_shutdown_failures(self) -> tuple[str, ...]:
        """Provider IDs whose post-terminal cleanup RPC did not complete."""

        return self._provider_shutdown_failures

    async def prepare(self) -> tuple[StepReceipt, ...]:
        if self._prepared:
            raise HarnessRuntimeError("Harness is already prepared")
        if self._shutdown:
            raise HarnessRuntimeError("Harness is already shut down")
        zero = SimulationTime(tick=0, sim_time_ns=0)
        if self._run.execution_scope == "executor_validation":
            self._ledger.append_event(
                source="harness",
                event_type="validation.scope",
                time=zero,
                payload=(
                    NamedValue(name="execution_scope", value="executor_validation"),
                ),
            )
        self._ledger.append_event(
            source="harness",
            event_type="run.started",
            time=zero,
            payload=(
                NamedValue(name="execution_scope", value=self._run.execution_scope),
            ),
        )
        await self._await_provider_calls(
            tuple(
                self._providers[provider_id].prepare()
                for provider_id in self._provider_order
            ),
            operation="prepare",
        )
        self._ledger.append_event(
            source="harness",
            event_type="runtime.identity",
            time=zero,
            payload=harness_identity_payload(self._run),
        )
        for provider in sorted(
            self._run.environment.providers, key=lambda item: item.provider_id
        ):
            self._ledger.append_event(
                source=provider.provider_id,
                event_type="runtime.identity",
                time=zero,
                payload=provider_identity_payload(provider),
            )
        receipts = await self._await_provider_calls(
            tuple(
                self._providers[provider_id].reset(seed=self._run.seed)
                for provider_id in self._provider_order
            ),
            operation="reset",
        )
        typed_receipts = tuple(receipts)
        reset_time = SimulationTime(tick=0, sim_time_ns=0)
        for provider_id, receipt in zip(
            self._provider_order,
            typed_receipts,
            strict=True,
        ):
            if not isinstance(receipt, StepReceipt):
                raise HarnessRuntimeError("Provider reset did not return StepReceipt")
            if receipt.run_id != self._run.run_id or receipt.provider_id != provider_id:
                raise HarnessRuntimeError("Provider reset receipt identity is invalid")
            if receipt.reached != reset_time:
                raise HarnessRuntimeError(
                    "Provider reset receipt must establish time zero"
                )
            self._ledger.append_event(
                source=provider_id,
                event_type="provider.reset",
                time=reset_time,
                payload=(
                    NamedValue(name="state_digest", value=receipt.state_digest),
                ),
                provider_id=provider_id,
            )
        self._ledger.append_event(
            source="harness",
            event_type="barrier.ready",
            time=reset_time,
            payload=(NamedValue(name="seed", value=self._run.seed),),
        )
        self._prepared = True
        return typed_receipts

    async def submit_agent_turn(
        self, completion: AgentTurnCompletion
    ) -> AgentTurnDecision:
        """Accept one Agent completion and, when complete, advance one barrier.

        This is the only production trigger for a simulation tick.  The
        Provider barrier is called while ``_turn_lock`` is held, so concurrent
        Agent submissions cannot cause duplicate advances or shutdowns.
        """

        if not isinstance(completion, AgentTurnCompletion):
            raise TypeError("completion must be AgentTurnCompletion")
        fingerprint = canonical_json_bytes(completion.model_dump(mode="json"))
        async with self._turn_lock:
            self._require_healthy()
            previous = self._completion_results.get(completion.completion_id)
            if previous is not None:
                previous_fingerprint, previous_decision = previous
                if previous_fingerprint != fingerprint:
                    raise HarnessRuntimeError(
                        "completion_id was reused with different canonical content"
                    )
                return previous_decision

            self._require_turn_submission(completion)
            if (
                completion.disposition == "advance"
                and self.current.tick >= self._run.environment.clock.max_steps
            ):
                raise HarnessRuntimeError(
                    "Agent requested advance after ResolvedRun max_steps"
                )
            if (
                completion.disposition == "finished"
                and self._run.execution_scope == "formal_benchmark"
                and self.current.tick == 0
                and len(self._active_agents) == 1
            ):
                raise HarnessRuntimeError(
                    "formal benchmark cannot terminate before one Provider barrier"
                )

            completion_causal_event_ids = self._completion_causal_event_ids(
                completion
            )
            self._ledger.append_event(
                source=completion.agent_id,
                source_kind="agent",
                workload_id=completion.agent_id,
                event_type="agent.turn-completion",
                time=completion.at,
                payload=self._completion_payload(completion),
                correlation_id=completion.completion_id,
                parent_event_id=(
                    max(completion_causal_event_ids)
                    if completion_causal_event_ids
                    else None
                ),
                causal_event_ids=completion_causal_event_ids,
                agent_id=completion.agent_id,
            )
            self._pending_completions[completion.agent_id] = completion
            if completion.disposition == "finished":
                self._active_agents.remove(completion.agent_id)
                self._finished_agents.add(completion.agent_id)

            if self.missing_agent_ids:
                decision = self._decision(
                    status="waiting",
                    at=self.current,
                    missing_agent_ids=self.missing_agent_ids,
                )
                self._remember_completion(completion, fingerprint, decision)
                return decision

            completions = tuple(self._pending_completions.values())
            raw_completion_event_ids = tuple(
                self._ledger.latest_event_id(completion.completion_id)
                for completion in completions
            )
            if any(event_id is None for event_id in raw_completion_event_ids):
                raise HarnessRuntimeError("Agent turn completion audit event is missing")
            causal_completion_event_ids = tuple(
                sorted(
                    event_id
                    for event_id in raw_completion_event_ids
                    if event_id is not None
                )
            )
            if not self._active_agents:
                decision = self._decision(
                    status="terminated",
                    at=self.current,
                    missing_agent_ids=(),
                )
                self._ledger.append_event(
                    source="harness",
                    event_type="agent.turn-decision",
                    time=decision.at,
                    payload=self._decision_payload(decision),
                    correlation_id=f"turn.{decision.at.tick}",
                    parent_event_id=max(causal_completion_event_ids),
                    causal_event_ids=causal_completion_event_ids,
                )
                try:
                    await self._shutdown_locked()
                except BaseException as error:
                    self._mark_failed("Harness completion failed", error)
                    self._pending_completions.clear()
                    self._turn_participants.clear()
                    raise
                self._remember_completions(completions, decision)
                self._pending_completions.clear()
                self._turn_participants.clear()
                return decision

            try:
                commit = await self._advance_provider_barrier()
            except _HarnessStopRequested:
                self._pending_completions.clear()
                raise
            except Exception as error:
                self._mark_failed("Provider barrier advance failed", error)
                raise
            self._pending_completions.clear()
            self._turn_participants = set(self._active_agents)
            decision = self._decision(
                status="advanced",
                at=commit.time,
                missing_agent_ids=self.active_agent_ids,
            )
            barrier_event_id = self._ledger.latest_event_id(
                f"tick.{decision.at.tick}"
            )
            if barrier_event_id is None:
                raise HarnessRuntimeError("committed barrier RunEvent is missing")
            decision_causal_event_ids = tuple(
                sorted((*causal_completion_event_ids, barrier_event_id))
            )
            self._ledger.append_event(
                source="harness",
                event_type="agent.turn-decision",
                time=decision.at,
                payload=self._decision_payload(decision),
                correlation_id=f"turn.{decision.at.tick}",
                parent_event_id=barrier_event_id,
                causal_event_ids=decision_causal_event_ids,
            )
            self._remember_completions(completions, decision)
            return decision

    def _require_turn_submission(self, completion: AgentTurnCompletion) -> None:
        if not self._prepared:
            raise HarnessRuntimeError(
                "Harness must be prepared before accepting Agent completion"
            )
        if self._shutdown:
            raise HarnessRuntimeError("Harness is already shut down")
        if completion.run_id != self._run.run_id:
            raise HarnessRuntimeError("Agent completion belongs to another run")
        if completion.agent_id not in self._turn_participants:
            if completion.agent_id in self._finished_agents:
                raise HarnessRuntimeError("finished Agent cannot submit another turn")
            raise HarnessRuntimeError("Agent is not declared for this turn")
        if completion.agent_id in self._pending_completions:
            raise HarnessRuntimeError("Agent submitted more than one completion")
        if completion.at != self.current:
            raise HarnessRuntimeError(
                "Agent completion time must equal the authoritative Harness time"
            )
        self._require_completion_evidence(completion)

    def _require_completion_evidence(
        self, completion: AgentTurnCompletion
    ) -> None:
        for command_id in completion.command_ids:
            if not any(
                record.event.agent_id == completion.agent_id
                and record.event.command_id == command_id
                and record.event.time == completion.at
                and record.event.event_type == "command.validated"
                and record.event.interaction is not None
                and record.event.interaction.interaction_type
                == "agent.tool_result.v1"
                for record in self._ledger.iter_records()
            ):
                raise HarnessRuntimeError(
                    "Agent completion references an unvalidated command"
                )
        for observation_id in completion.observation_ids:
            if not any(
                record.event.agent_id == completion.agent_id
                and record.event.observation_id == observation_id
                and record.event.time == completion.at
                and record.event.interaction is not None
                and record.event.interaction.interaction_type
                == "agent.observation.v1"
                for record in self._ledger.iter_records()
            ):
                raise HarnessRuntimeError(
                    "Agent completion references an unavailable observation"
                )

    def _completion_causal_event_ids(
        self, completion: AgentTurnCompletion
    ) -> tuple[str, ...]:
        event_ids: set[str] = set()
        for command_id in completion.command_ids:
            event_ids.add(
                next(
                    record.event.event_id
                    for record in self._ledger.iter_records_reversed()
                    if record.event.agent_id == completion.agent_id
                    and record.event.command_id == command_id
                    and record.event.event_type == "command.validated"
                )
            )
        for observation_id in completion.observation_ids:
            event_ids.add(
                next(
                    record.event.event_id
                    for record in self._ledger.iter_records_reversed()
                    if record.event.agent_id == completion.agent_id
                    and record.event.observation_id == observation_id
                    and record.event.interaction is not None
                    and record.event.interaction.interaction_type
                    == "agent.observation.v1"
                )
            )
        return tuple(sorted(event_ids))

    def _require_healthy(self) -> None:
        if self._failed is not None:
            raise HarnessRuntimeError(f"Harness is permanently failed: {self._failed}")

    def _mark_failed(self, operation: str, error: BaseException) -> None:
        self._failed = f"{operation}: {type(error).__name__}: {error}"

    @staticmethod
    def _completion_payload(
        completion: AgentTurnCompletion,
    ) -> tuple[NamedValue, ...]:
        return (
            NamedValue(name="run_id", value=completion.run_id),
            NamedValue(name="agent_id", value=completion.agent_id),
            NamedValue(name="completion_id", value=completion.completion_id),
            NamedValue(name="disposition", value=completion.disposition),
            NamedValue(
                name="command_ids",
                value=canonical_json_bytes(list(completion.command_ids)).decode(
                    "utf-8"
                ),
            ),
            NamedValue(
                name="observation_ids",
                value=canonical_json_bytes(list(completion.observation_ids)).decode(
                    "utf-8"
                ),
            ),
        )

    @staticmethod
    def _decision_payload(decision: AgentTurnDecision) -> tuple[NamedValue, ...]:
        return (
            NamedValue(name="run_id", value=decision.run_id),
            NamedValue(name="status", value=decision.status),
            NamedValue(
                name="active_agent_ids",
                value=canonical_json_bytes(list(decision.active_agent_ids)).decode(
                    "utf-8"
                ),
            ),
            NamedValue(
                name="missing_agent_ids",
                value=canonical_json_bytes(list(decision.missing_agent_ids)).decode(
                    "utf-8"
                ),
            ),
        )

    def _decision(
        self,
        *,
        status: str,
        at: SimulationTime,
        missing_agent_ids: tuple[str, ...],
    ) -> AgentTurnDecision:
        return AgentTurnDecision(
            schema_version="aero-bench.agent-turn-decision/v1",
            run_id=self._run.run_id,
            status=status,
            at=at,
            active_agent_ids=self.active_agent_ids,
            missing_agent_ids=missing_agent_ids,
        )

    def _remember_completion(
        self,
        completion: AgentTurnCompletion,
        fingerprint: bytes,
        decision: AgentTurnDecision,
    ) -> None:
        self._completion_results[completion.completion_id] = (fingerprint, decision)

    def _remember_completions(
        self,
        completions: tuple[AgentTurnCompletion, ...],
        decision: AgentTurnDecision,
    ) -> None:
        for completion in completions:
            self._remember_completion(
                completion,
                canonical_json_bytes(completion.model_dump(mode="json")),
                decision,
            )

    async def _advance_provider_barrier(self) -> BarrierCommit:
        await self._await_control_permission()
        try:
            return await self._advance_provider_barrier_uncontrolled()
        finally:
            await self._finish_controlled_tick()

    async def _advance_provider_barrier_uncontrolled(self) -> BarrierCommit:
        next_tick = self.current.tick + 1
        if next_tick > self._run.environment.clock.max_steps:
            raise HarnessRuntimeError("ResolvedRun max_steps has been reached")
        target = SimulationTime(
            tick=next_tick,
            sim_time_ns=next_tick * self._run.environment.clock.step_ns,
        )
        self._barrier.begin_tick(target)

        motion_barrier, motion_results = await self._advance_stage(
            stage="motion",
            target=target,
            scene_state=None,
        )
        scene_state = self._scene_state_assembler.assemble(
            run_id=self._run.run_id,
            at=target,
            barrier=motion_barrier,
            contributions=tuple(result.contribution for result in motion_results),
            previous_scene_state=self.latest_scene_state,
        )
        provider_events: list[ProviderEvent] = list(
            self._record_closed_stage(
                barrier=motion_barrier,
                results=motion_results,
                scene_state_digest=scene_state.scene_state_digest,
            )
        )
        scene_correlation_id = f"tick.{target.tick}"
        scene_commit = self._ledger.append_event(
            source="harness",
            event_type="scene.state.committed",
            time=target,
            payload=self._scene_state_payload(
                scene_state=scene_state,
                motion_barrier=motion_barrier,
            ),
            correlation_id=scene_correlation_id,
            parent_event_id=self._ledger.latest_event_id(scene_correlation_id),
        )
        if self._scene_state_history_writer is not None:
            try:
                self._scene_state_history_writer.append(scene_state)
            except (TypeError, ValueError) as error:
                raise HarnessRuntimeError(
                    "incremental SceneState history append failed"
                ) from error
        else:
            self._scene_state_history.append(scene_state)
        for sample in scene_state.samples:
            entity_record = self._ledger.append_event(
                source="harness",
                event_type="scene.entity-state",
                time=target,
                payload=self._state_sample_event_payload(
                    sample=sample,
                    scene_state=scene_state,
                ),
                interaction_type="scene.entity_state.v1",
                correlation_id=f"tick.{target.tick}",
                parent_event_id=scene_commit.event.event_id,
                frame_id="ENU",
                provider_id=sample.provider_id,
                entity_id=sample.entity_id,
            )
            if sample.provider_id in self._px4_provider_ids:
                self._ledger.append_event(
                    source=sample.provider_id,
                    source_kind="provider",
                    workload_id=sample.provider_id,
                    event_type="px4.telemetry",
                    time=target,
                    payload=self._state_sample_event_payload(
                        sample=sample,
                        scene_state=scene_state,
                    ),
                    payload_schema_id="px4.telemetry.v1",
                    interaction_type="px4.telemetry.v1",
                    correlation_id=f"tick.{target.tick}",
                    parent_event_id=entity_record.event.event_id,
                    frame_id="ENU",
                    provider_id=sample.provider_id,
                    entity_id=sample.entity_id,
                )

        if "network" in self._barrier.enabled_stages:
            network_barrier, network_results = await self._advance_stage(
                stage="network",
                target=target,
                scene_state=scene_state,
            )
            provider_events.extend(
                self._record_closed_stage(
                    barrier=network_barrier,
                    results=network_results,
                    scene_state_digest=scene_state.scene_state_digest,
                )
            )

        if "business_environment" in self._barrier.enabled_stages:
            business_barrier, business_results = await self._advance_stage(
                stage="business_environment",
                target=target,
                scene_state=scene_state,
            )
            provider_events.extend(
                self._record_closed_stage(
                    barrier=business_barrier,
                    results=business_results,
                    scene_state_digest=scene_state.scene_state_digest,
                )
            )

        closed_stage_barriers = self._barrier.closed_stage_barriers
        await self._invoke_runtime_hook(
            events=tuple(provider_events),
            scene_state=scene_state,
            stage_barriers=closed_stage_barriers,
            target=target,
        )

        tick_correlation_id = f"tick.{target.tick}"
        self._ledger.append_event(
            source="harness",
            event_type="barrier.committed",
            time=target,
            payload=self._final_barrier_payload(
                scene_state=scene_state,
                stage_barriers=closed_stage_barriers,
            ),
            correlation_id=tick_correlation_id,
            parent_event_id=self._ledger.latest_event_id(tick_correlation_id),
        )
        return self._barrier.commit_tick()

    async def _invoke_runtime_hook(
        self,
        *,
        events: tuple[ProviderEvent, ...],
        scene_state: SceneState,
        stage_barriers: tuple[StageBarrier, ...],
        target: SimulationTime,
    ) -> None:
        hook = self._runtime_hook
        if hook is None:
            return
        if self._runtime_hook_time is not None:
            raise HarnessRuntimeError("runtime hook time is already active")
        self._runtime_hook_time = target
        try:
            await hook.on_stage_barriers_closed(
                events,
                scene_state=scene_state,
                stage_barriers=stage_barriers,
                target=target,
            )
        finally:
            self._runtime_hook_time = None

    async def _advance_stage(
        self,
        *,
        stage: ProviderStage,
        target: SimulationTime,
        scene_state: SceneState | None,
    ) -> tuple[StageBarrier, tuple[ProviderStageResult, ...]]:
        if stage == "motion":
            if scene_state is not None:
                raise HarnessRuntimeError("motion stage cannot receive a SceneState")
            provider_ids = self._barrier.begin_stage(stage)
        else:
            if scene_state is None:
                raise HarnessRuntimeError("non-motion stage requires a SceneState")
            provider_ids = self._barrier.begin_stage(
                stage,
                input_scene_state_digest=scene_state.scene_state_digest,
            )
        predecessor_barriers = self._barrier.active_predecessor_barriers
        requests = tuple(
            self._stage_request(
                stage=stage,
                provider_id=provider_id,
                target=target,
                scene_state=scene_state,
                predecessor_barriers=predecessor_barriers,
            )
            for provider_id in provider_ids
        )
        calls: list[object] = []
        for provider_id, request in zip(provider_ids, requests, strict=True):
            step_stage = getattr(self._providers[provider_id], "step_stage", None)
            if not callable(step_stage):
                raise HarnessRuntimeError(
                    f"Provider {provider_id} does not implement the staged boundary"
                )
            calls.append(step_stage(request))
        raw_results = await self._await_provider_calls(
            tuple(calls),
            operation=f"{stage}-step-{target.tick}",
        )

        results: list[ProviderStageResult] = []
        for provider_id, raw_result in zip(
            provider_ids,
            raw_results,
            strict=True,
        ):
            result = self._revalidate_stage_result(
                raw_result,
                expected_stage=stage,
            )
            if result.provider_id != provider_id:
                raise HarnessRuntimeError(
                    "Provider staged result identity does not match its call target"
                )
            self._validate_stage_events(result)
            try:
                self._barrier.submit(result)
            except (TypeError, ValueError, RuntimeError) as error:
                raise HarnessRuntimeError("Provider staged result is invalid") from error
            results.append(result)
        try:
            barrier = self._barrier.close_stage()
        except (TypeError, ValueError, RuntimeError) as error:
            raise HarnessRuntimeError("Provider stage barrier cannot close") from error
        return barrier, tuple(results)

    def _stage_request(
        self,
        *,
        stage: ProviderStage,
        provider_id: str,
        target: SimulationTime,
        scene_state: SceneState | None,
        predecessor_barriers: tuple[StageBarrierDigest, ...],
    ) -> ProviderStageRequest:
        if stage == "motion":
            if scene_state is not None or predecessor_barriers:
                raise HarnessRuntimeError("motion request has forbidden stage inputs")
            return MotionStageRequest(
                schema_version="aero-bench.provider-stage-request/v1",
                run_id=self._run.run_id,
                scenario_digest=self._run.scenario.scenario_digest,
                provider_id=provider_id,
                target=target,
                stage="motion",
            )
        if scene_state is None:
            raise HarnessRuntimeError("non-motion request lacks an assembled SceneState")
        if stage == "network":
            return NetworkStageRequest(
                schema_version="aero-bench.provider-stage-request/v1",
                run_id=self._run.run_id,
                scenario_digest=self._run.scenario.scenario_digest,
                provider_id=provider_id,
                target=target,
                stage="network",
                scene_state=scene_state,
                scene_state_digest=scene_state.scene_state_digest,
                predecessor_barriers=predecessor_barriers,
            )
        if stage == "business_environment":
            return BusinessEnvironmentStageRequest(
                schema_version="aero-bench.provider-stage-request/v1",
                run_id=self._run.run_id,
                scenario_digest=self._run.scenario.scenario_digest,
                provider_id=provider_id,
                target=target,
                stage="business_environment",
                scene_state=scene_state,
                scene_state_digest=scene_state.scene_state_digest,
                predecessor_barriers=predecessor_barriers,
            )
        raise HarnessRuntimeError(f"unsupported provider stage: {stage}")

    @staticmethod
    def _revalidate_stage_result(
        raw_result: object,
        *,
        expected_stage: ProviderStage,
    ) -> ProviderStageResult:
        if expected_stage == "motion":
            result_type = MotionStageResult
        elif expected_stage == "network":
            result_type = NetworkStageResult
        elif expected_stage == "business_environment":
            result_type = BusinessEnvironmentStageResult
        else:
            raise HarnessRuntimeError(f"unsupported provider stage: {expected_stage}")
        if not isinstance(raw_result, result_type):
            raise HarnessRuntimeError("Provider step did not return the expected stage result")
        try:
            result = result_type.model_validate(raw_result.model_dump(mode="json"))
        except (TypeError, ValueError) as error:
            raise HarnessRuntimeError(
                "Provider staged result failed strict revalidation"
            ) from error
        if result != raw_result:
            raise HarnessRuntimeError(
                "Provider staged result is not canonically serialized"
            )
        return result

    @staticmethod
    def _validate_stage_events(result: ProviderStageResult) -> None:
        public_schemas = {
            PUBLIC_EVENT_EVENT_TYPE: "public.event.v3",
            PUBLIC_NETWORK_LINK_EVENT_TYPE: "public.network.link.v3",
            PUBLIC_SENSOR_FRAME_EVENT_TYPE: "sensor.frame_ref.public.v2",
            PUBLIC_STATUS_EVENT_TYPE: "public.status.v3",
            PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE: "sumo.traffic_light.v1",
        }
        for event in result.step_receipt.events:
            if event.time != result.target:
                raise HarnessRuntimeError(
                    "Provider staged event does not occur at the stage target"
                )
            if event.event_id in RESERVED_RUNTIME_EVENT_TYPES:
                raise HarnessRuntimeError(
                    "Provider staged event uses a reserved event type"
                )
            if event.event_id.startswith("public."):
                expected_schema = public_schemas.get(event.event_id)
                if expected_schema is None:
                    raise HarnessRuntimeError(
                        "Provider staged event uses a legacy public event type"
                    )
                if event.payload_schema_id != expected_schema:
                    raise HarnessRuntimeError(
                        "Provider staged event uses a legacy public payload schema "
                        f"(provider_id={event.provider_id!r}, "
                        f"event_id={event.event_id!r}, expected={expected_schema!r}, "
                        f"actual={event.payload_schema_id!r})"
                    )

    @staticmethod
    def _provider_interaction_type(
        event: ProviderEvent,
    ) -> AgentInteractionType | None:
        if event.event_id == PUBLIC_SENSOR_FRAME_EVENT_TYPE:
            return "sensor.frame_ref.v2"
        if event.event_id in {PUBLIC_EVENT_EVENT_TYPE, PUBLIC_STATUS_EVENT_TYPE}:
            return "mission.event.v1"
        if event.event_id == PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE:
            return "sumo.traffic_light.v1"
        if event.event_id == PUBLIC_NETWORK_LINK_EVENT_TYPE:
            return "ns3.link_state.v1"
        if event.event_id.endswith(".applied.physical"):
            return "mavlink.command_ack.v1"
        if event.event_id.endswith(".physical"):
            return "physical.effect.v1"
        if event.event_id.startswith("command."):
            return "provider.command.v1"
        if (
            event.event_id.startswith("business.")
            or event.payload_schema_id.startswith("business.")
        ):
            return "mission.event.v1"
        if (
            event.event_id.startswith("network.")
            or event.payload_schema_id.startswith("ns3.")
        ):
            return "ns3.link_state.v1"
        return None

    @staticmethod
    def _provider_event_visibility(
        event: ProviderEvent,
    ) -> tuple[RunEventAudience, ...] | None:
        if event.event_id not in PUBLIC_PROVIDER_EVENT_TYPES:
            return None
        return (RunEventAudience(scope="public", audience_id=None),)

    def _record_closed_stage(
        self,
        *,
        barrier: StageBarrier,
        results: tuple[ProviderStageResult, ...],
        scene_state_digest: str,
    ) -> tuple[ProviderEvent, ...]:
        result_by_provider = {result.provider_id: result for result in results}
        if tuple(sorted(result_by_provider)) != barrier.provider_ids:
            raise HarnessRuntimeError("stage result inventory does not close over barrier")
        for receipt in barrier.receipts:
            result = result_by_provider[receipt.provider_id]
            if (
                receipt.contribution_digest != result.contribution.contribution_digest
                or receipt.payload_digest != result.contribution.payload_digest
                or receipt.state_digest != result.step_receipt.state_digest
                or receipt.step_receipt_digest != result.step_receipt_digest
            ):
                raise HarnessRuntimeError("stage receipt does not bind its result")
        if barrier.stage == "motion":
            if barrier.input_scene_state_digest is not None:
                raise HarnessRuntimeError("motion barrier consumed a SceneState")
        elif barrier.input_scene_state_digest != scene_state_digest:
            raise HarnessRuntimeError("non-motion barrier input SceneState is inconsistent")

        provider_events = tuple(
            sorted(
                (
                    event
                    for result in results
                    for event in result.step_receipt.events
                ),
                key=lambda event: (
                    event.time.tick,
                    event.time.sim_time_ns,
                    event.provider_id,
                    event.event_id,
                ),
            )
        )
        for event in provider_events:
            payload = {item.name: item.value for item in event.payload}
            command_id = (
                payload.get("command_id")
                if isinstance(payload.get("command_id"), str)
                else None
            )
            observation_id = (
                payload.get("observation_id")
                if isinstance(payload.get("observation_id"), str)
                else None
            )
            observation_correlation_id = None
            if observation_id is not None:
                observation_correlation_id = next(
                    (
                        record.event.correlation_id
                        for record in self._ledger.iter_records_reversed()
                        if record.event.observation_id == observation_id
                        and record.event.provider_id == event.provider_id
                    ),
                    None,
                )
            correlation_id = (
                command_id
                or observation_correlation_id
                or f"tick.{barrier.at.tick}"
            )
            parent_event_id = self._ledger.latest_event_id(correlation_id)
            tick_event_id = self._ledger.latest_event_id(
                f"tick.{barrier.at.tick}"
            )
            causal_event_ids = tuple(
                event_id
                for event_id in (parent_event_id, tick_event_id)
                if event_id is not None
            )
            if event.event_id.endswith(".applied.physical"):
                command_record = self._ledger.append_event(
                    source=event.provider_id,
                    source_kind="provider",
                    workload_id=event.provider_id,
                    event_type=f"{event.event_id}.mavlink-command",
                    time=event.time,
                    payload=event.payload,
                    payload_schema_id=event.payload_schema_id,
                    interaction_type="mavlink.command.v1",
                    correlation_id=correlation_id,
                    parent_event_id=parent_event_id,
                    causal_event_ids=causal_event_ids,
                    provider_id=event.provider_id,
                    command_id=command_id,
                )
                parent_event_id = command_record.event.event_id
            self._ledger.append_event(
                source=event.provider_id,
                source_kind="provider",
                workload_id=event.provider_id,
                event_type=event.event_id,
                time=event.time,
                payload=event.payload,
                payload_schema_id=event.payload_schema_id,
                interaction_type=self._provider_interaction_type(event),
                correlation_id=correlation_id,
                parent_event_id=parent_event_id,
                causal_event_ids=causal_event_ids,
                visibility=self._provider_event_visibility(event),
                frame_id=(
                    payload.get("frame_id")
                    if isinstance(payload.get("frame_id"), str)
                    else None
                ),
                provider_id=event.provider_id,
                entity_id=(
                    payload.get("entity_id")
                    if isinstance(payload.get("entity_id"), str)
                    else None
                ),
                command_id=command_id,
                observation_id=observation_id,
            )
        stage_receipt_event_ids: list[str] = []
        for receipt in barrier.receipts:
            correlation_id = f"tick.{barrier.at.tick}"
            record = self._ledger.append_event(
                source=receipt.provider_id,
                event_type="provider.step-receipt",
                time=barrier.at,
                payload=self._stage_receipt_payload(
                    barrier=barrier,
                    receipt=receipt,
                    scene_state_digest=scene_state_digest,
                ),
                correlation_id=correlation_id,
                parent_event_id=self._ledger.latest_event_id(correlation_id),
                provider_id=receipt.provider_id,
            )
            stage_receipt_event_ids.append(record.event.event_id)
        correlation_id = f"tick.{barrier.at.tick}"
        self._ledger.append_event(
            source="harness",
            event_type="stage.barrier-closed",
            time=barrier.at,
            payload=self._stage_barrier_payload(
                barrier=barrier,
                scene_state_digest=scene_state_digest,
            ),
            correlation_id=correlation_id,
            parent_event_id=self._ledger.latest_event_id(correlation_id),
            causal_event_ids=tuple(stage_receipt_event_ids),
        )
        return provider_events

    def _stage_receipt_payload(
        self,
        *,
        barrier: StageBarrier,
        receipt: StageReceipt,
        scene_state_digest: str,
    ) -> tuple[NamedValue, ...]:
        return self._sorted_named_values(
            {
                "barrier_digest": barrier.barrier_digest,
                "contribution_digest": receipt.contribution_digest,
                "contribution_payload_digest": receipt.payload_digest,
                "predecessor_barriers": self._predecessor_barriers_value(
                    barrier.predecessor_barriers
                ),
                "provider_id": receipt.provider_id,
                "run_id": self._run.run_id,
                "scenario_digest": self._run.scenario.scenario_digest,
                "scene_state_digest": scene_state_digest,
                "stage": barrier.stage,
                "stage_receipt_digest": receipt.receipt_digest,
                "state_digest": receipt.state_digest,
                "step_receipt_digest": receipt.step_receipt_digest,
                "target_sim_time_ns": barrier.at.sim_time_ns,
                "target_tick": barrier.at.tick,
            }
        )

    def _stage_barrier_payload(
        self,
        *,
        barrier: StageBarrier,
        scene_state_digest: str,
    ) -> tuple[NamedValue, ...]:
        return self._sorted_named_values(
            {
                "barrier_digest": barrier.barrier_digest,
                "contribution_digests": self._canonical_json_value(
                    [
                        receipt.contribution_digest
                        for receipt in barrier.receipts
                    ]
                ),
                "predecessor_barriers": self._predecessor_barriers_value(
                    barrier.predecessor_barriers
                ),
                "provider_ids": self._canonical_json_value(list(barrier.provider_ids)),
                "receipt_digests": self._canonical_json_value(
                    list(barrier.receipt_digests)
                ),
                "run_id": self._run.run_id,
                "scenario_digest": self._run.scenario.scenario_digest,
                "scene_state_digest": scene_state_digest,
                "stage": barrier.stage,
                "step_receipt_digests": self._canonical_json_value(
                    [receipt.step_receipt_digest for receipt in barrier.receipts]
                ),
                "target_sim_time_ns": barrier.at.sim_time_ns,
                "target_tick": barrier.at.tick,
            }
        )

    def _scene_state_payload(
        self,
        *,
        scene_state: SceneState,
        motion_barrier: StageBarrier,
    ) -> tuple[NamedValue, ...]:
        return self._sorted_named_values(
            {
                "barrier_digest": motion_barrier.barrier_digest,
                "contribution_digests": self._canonical_json_value(
                    list(scene_state.contribution_digests)
                ),
                "provider_ids": self._canonical_json_value(
                    list(motion_barrier.provider_ids)
                ),
                "receipt_digests": self._canonical_json_value(
                    list(motion_barrier.receipt_digests)
                ),
                "run_id": scene_state.run_id,
                "scenario_digest": scene_state.scenario_digest,
                "scene_state_digest": scene_state.scene_state_digest,
                "stage": "motion",
                "target_sim_time_ns": scene_state.at.sim_time_ns,
                "target_tick": scene_state.at.tick,
            }
        )

    def _state_sample_event_payload(
        self,
        *,
        sample: StateSample,
        scene_state: SceneState,
    ) -> tuple[NamedValue, ...]:
        return self._sorted_named_values(
            {
                "entity_id": sample.entity_id,
                "provider_id": sample.provider_id,
                "run_id": sample.run_id,
                "sample_digest": sample.sample_digest,
                "sample_kind": sample.sample_kind,
                "scenario_digest": sample.scenario_digest,
                "scene_state_digest": scene_state.scene_state_digest,
                "state_sample_json": self._canonical_json_value(
                    sample.model_dump(mode="json")
                ),
                "target_sim_time_ns": sample.at.sim_time_ns,
                "target_tick": sample.at.tick,
            }
        )

    def _final_barrier_payload(
        self,
        *,
        scene_state: SceneState,
        stage_barriers: tuple[StageBarrier, ...],
    ) -> tuple[NamedValue, ...]:
        return self._sorted_named_values(
            {
                "provider_result_count": sum(
                    len(barrier.receipts) for barrier in stage_barriers
                ),
                "run_id": self._run.run_id,
                "scenario_digest": self._run.scenario.scenario_digest,
                "scene_state_digest": scene_state.scene_state_digest,
                "stage": "tick",
                "stage_barriers": self._canonical_json_value(
                    [
                        {
                            "barrier_digest": barrier.barrier_digest,
                            "contribution_digests": [
                                receipt.contribution_digest
                                for receipt in barrier.receipts
                            ],
                            "predecessor_barriers": [
                                predecessor.model_dump(mode="json")
                                for predecessor in barrier.predecessor_barriers
                            ],
                            "provider_ids": list(barrier.provider_ids),
                            "receipt_digests": list(barrier.receipt_digests),
                            "stage": barrier.stage,
                            "step_receipt_digests": [
                                receipt.step_receipt_digest
                                for receipt in barrier.receipts
                            ],
                        }
                        for barrier in stage_barriers
                    ]
                ),
                "target_sim_time_ns": scene_state.at.sim_time_ns,
                "target_tick": scene_state.at.tick,
            }
        )

    @staticmethod
    def _canonical_json_value(value: object) -> str:
        return canonical_json_bytes(value).decode("utf-8")

    @staticmethod
    def _predecessor_barriers_value(
        predecessor_barriers: tuple[StageBarrierDigest, ...],
    ) -> str:
        return HarnessCoordinator._canonical_json_value(
            [
                item.model_dump(mode="json")
                for item in predecessor_barriers
            ]
        )

    @staticmethod
    def _sorted_named_values(
        values: Mapping[str, str | int | float | bool | None],
    ) -> tuple[NamedValue, ...]:
        return tuple(
            NamedValue(name=name, value=values[name]) for name in sorted(values)
        )

    async def shutdown(self) -> None:
        async with self._turn_lock:
            self._require_healthy()
            if self._shutdown:
                raise HarnessRuntimeError("Harness is already shut down")
            try:
                await self._shutdown_locked()
            except BaseException as error:
                self._mark_failed("Harness completion failed", error)
                raise

    async def abort(self, *, failure_class: AbortFailureClass) -> AbortReport:
        """Stop Providers and close a failed run without claiming completion.

        Abort is deliberately independent from ``shutdown``: a partial prepare,
        a permanently failed barrier, or a signal must end in ``run.aborted``.
        Provider exception text is never copied into the authoritative ledger.
        """

        if failure_class not in _ABORT_FAILURE_CLASSES:
            raise ValueError("unsupported Harness abort failure_class")
        async with self._turn_lock:
            if self._abort_report is not None:
                return self._abort_report
            if self._shutdown:
                raise HarnessRuntimeError(
                    "cannot abort a Harness that has already completed"
                )
            failures = await self._close_provider_calls()
            self._close_process_streams_for_terminal()
            latest_record = self._ledger.latest_record
            abort_time = latest_record.event.time if latest_record is not None else self.current
            abort_parent_event_id = (
                latest_record.event.event_id if latest_record is not None else None
            )
            self._ledger.append_event(
                source="harness",
                event_type="run.aborted",
                time=abort_time,
                payload=(
                    NamedValue(name="failure_class", value=failure_class),
                    NamedValue(
                        name="provider_failure_count",
                        value=len(failures),
                    ),
                    NamedValue(
                        name="provider_failure_ids",
                        value=canonical_json_bytes(list(failures)).decode("utf-8"),
                    ),
                ),
                correlation_id="run.terminal",
                parent_event_id=abort_parent_event_id,
            )
            self._shutdown = True
            self._failed = self._failed or f"aborted:{failure_class}"
            self._abort_report = AbortReport(
                failure_class=failure_class,
                provider_failures=failures,
            )
            return self._abort_report

    async def _shutdown_locked(self) -> None:
        self._require_healthy()
        if self._shutdown:
            raise HarnessRuntimeError("Harness is already shut down")

        self._close_process_streams_for_terminal()
        if not self.process_streams_closed:
            raise HarnessRuntimeError("process stream segments did not close")
        latest_record = self._ledger.latest_record
        if latest_record is None:
            raise HarnessRuntimeError("authoritative ledger is empty before completion")
        terminal_parent_event_id = latest_record.event.event_id
        terminal_causal_event_ids = {terminal_parent_event_id}
        terminal_decision = next(
            (
                record.event.event_id
                for record in self._ledger.iter_records_reversed()
                if record.event.event_type == "agent.turn-decision"
            ),
            None,
        )
        if terminal_decision is not None:
            terminal_causal_event_ids.add(terminal_decision)
        terminal_event = self._ledger.new_event(
            source="harness",
            event_type="run.completed",
            time=self.current,
            payload=(
                NamedValue(name="execution_scope", value=self._run.execution_scope),
            ),
            correlation_id="run.terminal",
            parent_event_id=terminal_parent_event_id,
            causal_event_ids=tuple(sorted(terminal_causal_event_ids)),
        )
        finalization = ProviderFinalizationRequest(
            schema_version="aero-bench.provider-finalization-request/v1",
            run_id=self._run.run_id,
            terminal_event="run.completed",
            terminal_time=self.current,
            event_chain_root=self._ledger.prospective_chain_root(terminal_event),
        )
        baseline_length = self._ledger.record_count
        baseline_root = self._ledger.chain_root
        raw_receipts = await self._await_provider_calls(
            tuple(
                self._providers[provider_id].finalize(finalization)
                for provider_id in self._provider_order
            ),
            operation="finalize",
        )
        self._validate_finalization_receipts(
            raw_receipts,
            request=finalization,
        )
        if (
            self._ledger.record_count != baseline_length
            or self._ledger.chain_root != baseline_root
        ):
            raise HarnessRuntimeError(
                "authoritative ledger changed during Provider finalization"
            )

        terminal_record = self._ledger.append(terminal_event)
        if terminal_record.event_hash != finalization.event_chain_root:
            raise HarnessRuntimeError(
                "terminal event root differs from finalization root"
            )
        self._shutdown = True
        # Finalization and the exact terminal event are the benchmark commit
        # point. Provider shutdown is post-terminal resource cleanup: it cannot
        # rewrite a completed run as failed, and no cleanup event may be
        # appended after the terminal record. The coordinator still exposes an
        # explicit failure inventory for executor residue enforcement.
        self._provider_shutdown_failures = await self._close_provider_calls()

    def _validate_finalization_receipts(
        self,
        raw_receipts: tuple[object, ...],
        *,
        request: ProviderFinalizationRequest,
    ) -> tuple[ProviderFinalizationReceipt, ...]:
        receipts: list[ProviderFinalizationReceipt] = []
        requirements_by_provider = {
            provider.provider_id: {
                requirement.artifact_id: requirement
                for requirement in provider.artifact_requirements
            }
            for provider in self._run.environment.providers
        }
        for provider_id, raw_receipt in zip(
            self._provider_order,
            raw_receipts,
            strict=True,
        ):
            if not isinstance(raw_receipt, ProviderFinalizationReceipt):
                raise HarnessRuntimeError(
                    "Provider finalization returned an invalid receipt"
                )
            receipt = raw_receipt
            if (
                receipt.run_id != self._run.run_id
                or receipt.provider_id != provider_id
                or receipt.event_chain_root != request.event_chain_root
            ):
                raise HarnessRuntimeError(
                    "Provider finalization receipt identity is invalid"
                )
            expected = requirements_by_provider[provider_id]
            actual: dict[str, FinalizedArtifact] = {
                artifact.artifact_id: artifact for artifact in receipt.artifacts
            }
            if set(actual) != set(expected):
                raise HarnessRuntimeError(
                    "Provider finalization artifacts do not match ResolvedRun"
                )
            for artifact_id, artifact in actual.items():
                if artifact.size_bytes > expected[artifact_id].max_size_bytes:
                    raise HarnessRuntimeError(
                        "Provider finalized artifact exceeds max_size_bytes"
                    )
            receipts.append(receipt)
        return tuple(receipts)

    async def _close_provider_calls(self) -> tuple[str, ...]:
        """Best-effort close all sessions and return the failed Provider IDs."""

        provider_ids = tuple(reversed(self._provider_order))
        calls = tuple(
            self._providers[provider_id].shutdown() for provider_id in provider_ids
        )
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*calls, return_exceptions=True),
                timeout=self._run.environment.clock.provider_timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            return tuple(sorted(provider_ids))
        except BaseException:
            return tuple(sorted(provider_ids))
        return tuple(
            sorted(
                provider_id
                for provider_id, result in zip(provider_ids, results, strict=True)
                if isinstance(result, BaseException)
            )
        )

    async def _await_provider_calls(
        self,
        calls: tuple[object, ...],
        *,
        operation: str,
    ) -> tuple[object, ...]:
        scheduled_calls = calls
        if operation in {"prepare", "reset"}:
            if len(calls) != len(self._provider_order):
                raise HarnessRuntimeError(
                    f"Provider {operation} call inventory differs from provider order"
                )

            async def startup_call(provider_id: str, call: object) -> object:
                started_at = wall_clock.monotonic()
                _startup_provider_progress(
                    operation=operation,
                    provider_id=provider_id,
                    state="started",
                    started_at=started_at,
                )
                try:
                    result = await cast(Awaitable[object], call)
                except BaseException as error:
                    _startup_provider_progress(
                        operation=operation,
                        provider_id=provider_id,
                        state="failed",
                        started_at=started_at,
                        error_type=type(error).__name__,
                    )
                    raise
                _startup_provider_progress(
                    operation=operation,
                    provider_id=provider_id,
                    state="complete",
                    started_at=started_at,
                )
                return result

            scheduled_calls = tuple(
                startup_call(provider_id, call)
                for provider_id, call in zip(
                    self._provider_order, calls, strict=True
                )
            )
        tasks = tuple(asyncio.create_task(call) for call in scheduled_calls)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=self._run.environment.clock.provider_timeout_ms / 1000,
            )
        except asyncio.TimeoutError as error:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise HarnessRuntimeError(
                f"Provider {operation} exceeded the configured timeout"
            ) from error
        except BaseException as error:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise HarnessRuntimeError(
                f"Provider {operation} failed: {error}"
            ) from error
        return tuple(results)
