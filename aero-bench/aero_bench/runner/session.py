from __future__ import annotations

import threading
import time
import sys
import traceback
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.executor import Executor
from aero_bench.executor.contracts import ExecutionPlan, PreflightReport
from aero_bench.runtime.control import (
    RuntimeControlOperation,
    RuntimeControlReceipt,
    RuntimeControlStatus,
    RuntimeProjectionBatch,
)
from aero_bench.runtime.evidence import load_sealed_event_ledger
from aero_bench.runner.contracts import (
    PreflightIdentity,
    PublicTraceIdentity,
    RunSummary,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.verifier.output import ValidatedVerificationOutput


RunExecutionPhase: TypeAlias = Literal[
    "resolved",
    "materializing",
    "preflight",
    "blocked",
    "starting",
    "running",
    "sealing",
    "verifying",
    "projecting",
    "completed",
    "cancelled",
    "error",
]

_TERMINAL_PHASES = frozenset({"blocked", "completed", "cancelled", "error"})
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "resolved": frozenset({"materializing", "error"}),
    "materializing": frozenset({"preflight", "error"}),
    "preflight": frozenset({"blocked", "starting", "error"}),
    "starting": frozenset({"running", "error"}),
    "running": frozenset({"sealing", "error"}),
    "sealing": frozenset({"verifying", "projecting", "error"}),
    "verifying": frozenset({"projecting", "error"}),
    "projecting": frozenset({"completed", "cancelled", "error"}),
}


class RunExecutionTransition(StrictModel):
    schema_version: Literal["aero-bench.run-execution-transition/v1"]
    run_id: Sha256
    sequence: Annotated[int, Field(ge=0)]
    event_type: Identifier
    phase: RunExecutionPhase
    wall_time_ns: Annotated[int, Field(ge=0)]
    failure_class: Identifier | None
    runtime_control: RuntimeControlReceipt | None

    @model_validator(mode="after")
    def event_matches_payload(self) -> "RunExecutionTransition":
        is_control = self.event_type.startswith("runtime.control.")
        if is_control != (self.runtime_control is not None):
            raise ValueError("runtime control transition payload is inconsistent")
        if self.runtime_control is not None and (
            self.runtime_control.status.run_id != self.run_id
        ):
            raise ValueError("runtime control transition belongs to another run")
        return self


class RunExecutionSnapshot(StrictModel):
    schema_version: Literal["aero-bench.run-execution-snapshot/v1"]
    run_id: Sha256
    phase: RunExecutionPhase
    transition_sequence: Annotated[int, Field(ge=0)]
    preflight: PreflightIdentity | None
    runtime_control: RuntimeControlStatus | None
    summary: RunSummary | None
    failure_classes: tuple[Identifier, ...]

    @model_validator(mode="after")
    def terminal_snapshot_has_summary(self) -> "RunExecutionSnapshot":
        if (self.phase in _TERMINAL_PHASES) != (self.summary is not None):
            raise ValueError("terminal session snapshot and summary disagree")
        if self.summary is not None:
            if self.summary.run_id != self.run_id:
                raise ValueError("session summary belongs to another run")
            expected_phase = {
                "blocked": "blocked",
                "cancelled": "cancelled",
                "error": "error",
                "passed": "completed",
                "failed": "completed",
                "invalid": "completed",
            }[self.summary.status]
            if self.phase != expected_phase:
                raise ValueError("terminal session phase and summary status disagree")
        return self


class RunExecutionSession:
    """One reusable, stateful execution of the canonical runner pipeline."""

    def __init__(
        self,
        *,
        executor: Executor,
        run: ResolvedRunSpec,
        bundle_root: Path,
        output_root: Path,
        runtime_timeout_seconds: int,
        verifier_timeout_seconds: int,
    ) -> None:
        if not isinstance(executor, Executor):
            raise TypeError("RunExecutionSession executor does not implement Executor")
        if not isinstance(run, ResolvedRunSpec):
            raise TypeError("RunExecutionSession run must be ResolvedRunSpec")
        if not bundle_root.is_absolute() or not output_root.is_absolute():
            raise ValueError("RunExecutionSession roots must be absolute")
        if runtime_timeout_seconds <= 0 or verifier_timeout_seconds <= 0:
            raise ValueError("RunExecutionSession timeouts must be positive")
        self.executor = executor
        self.run = run
        self.bundle_root = bundle_root
        self.output_root = output_root
        self.runtime_timeout_seconds = runtime_timeout_seconds
        self.verifier_timeout_seconds = verifier_timeout_seconds
        self.run_root = output_root / run.run_id

        self._condition = threading.Condition(threading.RLock())
        self._phase: RunExecutionPhase = "resolved"
        self._transitions: list[RunExecutionTransition] = []
        self._preflight: PreflightIdentity | None = None
        self._plan: ExecutionPlan | None = None
        self._handle: object | None = None
        self._runtime_control: RuntimeControlStatus | None = None
        self._control_results: dict[
            str, tuple[RuntimeControlOperation, RuntimeControlReceipt]
        ] = {}
        self._controls_inflight: dict[str, RuntimeControlOperation] = {}
        self._summary: RunSummary | None = None
        self._executing = False
        self._emit(event_type="session.resolved", phase="resolved")

    @property
    def phase(self) -> RunExecutionPhase:
        with self._condition:
            return self._phase

    @property
    def summary(self) -> RunSummary | None:
        with self._condition:
            return self._summary

    @property
    def plan(self) -> ExecutionPlan | None:
        with self._condition:
            return self._plan

    @property
    def handle(self) -> object | None:
        with self._condition:
            return self._handle

    def snapshot(self) -> RunExecutionSnapshot:
        with self._condition:
            return RunExecutionSnapshot(
                schema_version="aero-bench.run-execution-snapshot/v1",
                run_id=self.run.run_id,
                phase=self._phase,
                transition_sequence=self._transitions[-1].sequence,
                preflight=self._preflight,
                runtime_control=self._runtime_control,
                summary=self._summary,
                failure_classes=(
                    self._summary.failure_classes if self._summary is not None else ()
                ),
            )

    def transitions_after(self, sequence: int) -> tuple[RunExecutionTransition, ...]:
        if sequence < -1:
            raise ValueError("transition sequence cursor is invalid")
        with self._condition:
            return tuple(
                transition
                for transition in self._transitions
                if transition.sequence > sequence
            )

    def wait_for_transitions(
        self,
        sequence: int,
        *,
        timeout_seconds: float,
    ) -> tuple[RunExecutionTransition, ...]:
        if timeout_seconds <= 0:
            raise ValueError("transition wait timeout must be positive")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while self._transitions[-1].sequence <= sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ()
                self._condition.wait(timeout=remaining)
            return tuple(
                transition
                for transition in self._transitions
                if transition.sequence > sequence
            )

    def control_runtime(
        self,
        operation: RuntimeControlOperation,
        *,
        control_id: str | None = None,
    ) -> RuntimeControlReceipt:
        with self._condition:
            if operation != "status":
                if control_id is None:
                    raise ValueError("state-changing control requires control_id")
                prior = self._control_results.get(control_id)
                if prior is not None:
                    prior_operation, prior_receipt = prior
                    if prior_operation != operation:
                        raise RuntimeError(
                            "control_id was reused for another operation"
                        )
                    return prior_receipt
                inflight = self._controls_inflight.get(control_id)
                if inflight is not None:
                    if inflight != operation:
                        raise RuntimeError(
                            "control_id was reused for another operation"
                        )
                    raise RuntimeError("runtime control request is in progress")
            elif control_id is not None:
                raise ValueError("runtime status query cannot use control_id")
            if self._phase != "running":
                raise RuntimeError("runtime controls require a running session")
            plan = self._plan
            handle = self._handle
            if control_id is not None:
                self._controls_inflight[control_id] = operation
        if plan is None or handle is None:
            with self._condition:
                if control_id is not None:
                    self._controls_inflight.pop(control_id, None)
            raise RuntimeError("runtime control target is unavailable")
        control = getattr(self.executor, "control_runtime", None)
        if not callable(control):
            with self._condition:
                if control_id is not None:
                    self._controls_inflight.pop(control_id, None)
            raise RuntimeError("executor does not implement runtime control")
        try:
            receipt = control(
                plan,
                handle,
                operation=operation,
                control_id=control_id,
            )
            if not isinstance(receipt, RuntimeControlReceipt):
                raise RuntimeError(
                    "executor returned an invalid runtime control receipt"
                )
        except BaseException:
            with self._condition:
                if control_id is not None:
                    self._controls_inflight.pop(control_id, None)
            raise
        with self._condition:
            self._runtime_control = receipt.status
            if control_id is not None:
                self._control_results[control_id] = (operation, receipt)
                self._controls_inflight.pop(control_id, None)
                self._emit(
                    event_type=f"runtime.control.{operation}",
                    phase=self._phase,
                    runtime_control=receipt,
                )
        return receipt

    def runtime_projection(
        self,
        *,
        after_scene_tick: int,
        after_event_sequence: int,
        max_scene_states: int = 1,
        max_events: int = 128,
    ) -> RuntimeProjectionBatch:
        with self._condition:
            if self._phase != "running":
                raise RuntimeError("runtime projection requires a running session")
            plan = self._plan
            handle = self._handle
        if plan is None or handle is None:
            raise RuntimeError("runtime projection target is unavailable")
        reader = getattr(self.executor, "read_runtime_projection", None)
        if not callable(reader):
            raise RuntimeError("executor does not implement runtime projection")
        batch = reader(
            plan,
            handle,
            after_scene_tick=after_scene_tick,
            after_event_sequence=after_event_sequence,
            max_scene_states=max_scene_states,
            max_events=max_events,
        )
        if not isinstance(batch, RuntimeProjectionBatch):
            raise RuntimeError("executor returned an invalid runtime projection")
        return batch

    def execute(self) -> RunSummary:
        with self._condition:
            if self._executing or self._phase != "resolved":
                raise RuntimeError("RunExecutionSession can execute only once")
            self._executing = True
        try:
            return self._execute_once()
        except Exception as error:
            self._write_failure_diagnostic(stage="session", error=error)
            with self._condition:
                if self._summary is not None:
                    return self._summary
                handle = self._handle
                preflight = self._preflight or PreflightIdentity(
                    ready=False,
                    blocker_codes=(),
                )
            cleanup_failed = False
            try:
                if handle is not None:
                    self.executor.cleanup(handle)
                else:
                    self.executor.cleanup_run(self.run.run_id)
            except Exception:
                cleanup_failed = True
            with self._condition:
                self._handle = None
            return self._finish_error(
                preflight=preflight,
                failure_classes=(
                    ("session_failed", "cleanup_failed")
                    if cleanup_failed
                    else ("session_failed",)
                ),
            )
        except BaseException:
            try:
                self.executor.cleanup_run(self.run.run_id)
            except Exception:
                pass
            raise

    def _execute_once(self) -> RunSummary:
        from aero_bench.runner.execution import (
            RunnerError,
            _project_and_write_public_trace,
            _seal_identity,
            _verification_identity,
        )

        self._transition("materializing")
        try:
            self.run_root.mkdir(mode=0o700)
        except FileExistsError:
            return self._finish_error(
                preflight=PreflightIdentity(ready=False, blocker_codes=()),
                failure_classes=("output_not_fresh",),
            )
        except OSError:
            return self._finish_error(
                preflight=PreflightIdentity(ready=False, blocker_codes=()),
                failure_classes=("output_unavailable",),
            )

        try:
            plan = self.executor.materialize(self.run, bundle_root=self.bundle_root)
            if not isinstance(plan, ExecutionPlan):
                raise RunnerError("executor returned an invalid execution plan")
            with self._condition:
                self._plan = plan
            self._transition("preflight")
            report = self.executor.preflight(plan)
            if not isinstance(report, PreflightReport):
                raise RunnerError("executor returned an invalid preflight report")
        except Exception as error:
            self._write_failure_diagnostic(stage="preflight", error=error)
            return self._finish_error(
                preflight=PreflightIdentity(ready=False, blocker_codes=()),
                failure_classes=("preflight_failed",),
            )

        preflight = PreflightIdentity(
            ready=report.ready,
            blocker_codes=tuple(sorted(blocker.code for blocker in report.blockers)),
        )
        with self._condition:
            self._preflight = preflight
        if not report.ready:
            summary = RunSummary(
                run_id=self.run.run_id,
                executor_kind="docker_reference",
                execution_scope=self.run.execution_scope,
                preflight=preflight,
                status="blocked",
                seal=None,
                verification=None,
                public_trace=None,
                failure_classes=("preflight_blocked",),
            )
            return self._finish(summary, phase="blocked")

        handle: object | None = None
        seal: SealManifest | None = None
        validated: ValidatedVerificationOutput | None = None
        failure_classes: list[str] = []
        stage = "start_runtime"
        self._transition("starting")
        try:
            handle = self.executor.start_runtime(plan)
            with self._condition:
                self._handle = handle
            self._transition("running")
            stage = "wait_runtime"
            self.executor.wait_runtime(
                handle,
                timeout_seconds=self.runtime_timeout_seconds,
            )
            self._transition("sealing")
            stage = "collect_and_seal"
            seal = self.executor.collect_and_seal(
                plan,
                handle,
                destination_root=self.run_root / "runtime-seal",
            )
            if not isinstance(seal, SealManifest):
                raise RunnerError("executor returned an invalid runtime seal")
            runtime_ledger = load_sealed_event_ledger(
                run=self.run,
                seal=seal,
                seal_root=self.run_root / "runtime-seal",
            )
            if runtime_ledger.records[-1].event.event_type == "run.aborted":
                return self._finish_cancelled(
                    preflight=preflight,
                    handle=handle,
                    seal=seal,
                )

            self._transition("verifying")
            stage = "start_verifier"
            handle = self.executor.start_verifier(plan, seal)
            with self._condition:
                self._handle = handle
            stage = "wait_verifier"
            self.executor.wait_verifier(
                handle,
                timeout_seconds=self.verifier_timeout_seconds,
            )
            stage = "collect_verification_outputs"
            validated = self.executor.collect_verification_outputs(
                plan,
                handle,
                destination_root=self.run_root / "verification",
            )
            if not isinstance(validated, ValidatedVerificationOutput):
                raise RunnerError(
                    "executor returned invalid validated verification output"
                )
        except Exception as error:
            self._write_failure_diagnostic(stage=stage, error=error)
            failure_classes.append(f"{stage}_failed")
            if handle is not None:
                try:
                    self.executor.collect_failure_outputs(
                        plan,
                        handle,
                        destination_root=self.run_root / "failure-evidence",
                    )
                except Exception as evidence_error:
                    failure_classes.append("failure_evidence_collection_failed")
                    (self.run_root / "failure-evidence-error.json").write_bytes(
                        canonical_json_bytes(
                            {"error_type": type(evidence_error).__name__}
                        )
                        + b"\n"
                    )
            if stage == "start_runtime":
                try:
                    self.executor.cleanup_run(self.run.run_id)
                except Exception:
                    failure_classes.append("cleanup_failed")
        except BaseException:
            try:
                self.executor.cleanup_run(self.run.run_id)
            except Exception:
                pass
            raise

        if handle is not None:
            try:
                self.executor.cleanup(handle)
            except Exception:
                failure_classes.append("cleanup_failed")
        with self._condition:
            self._handle = None

        public_trace_identity = None
        if seal is not None:
            self._transition("projecting")
            try:
                public_trace_identity = _project_and_write_public_trace(
                    run=self.run,
                    seal=seal,
                    validated=validated,
                    bundle_root=self.bundle_root,
                    run_root=self.run_root,
                )
            except Exception as error:
                self._write_failure_diagnostic(
                    stage="public_trace_projection", error=error
                )
                failure_classes.append("public_trace_projection_failed")

        if failure_classes:
            return self._finish_error(
                preflight=preflight,
                failure_classes=tuple(failure_classes),
                seal=seal,
                validated=validated,
                public_trace=public_trace_identity,
            )

        assert seal is not None and validated is not None
        verification = _verification_identity(validated)
        assert verification is not None
        summary = RunSummary(
            run_id=self.run.run_id,
            executor_kind="docker_reference",
            execution_scope=self.run.execution_scope,
            preflight=preflight,
            status=verification.status,
            seal=_seal_identity(seal),
            verification=verification,
            public_trace=public_trace_identity,
            failure_classes=(),
        )
        return self._finish(summary, phase="completed")

    def _write_failure_diagnostic(self, *, stage: str, error: BaseException) -> None:
        """Persist the bounded root cause of a failed runner stage.

        RunSummary intentionally keeps stable failure classes for machine
        consumers.  The sidecar gives operators the concrete exception and
        traceback without changing the authoritative sealed artifact set.
        It is best-effort because a failure must never be replaced by a
        diagnostic-writing error.
        """

        detail = "".join(traceback.format_exception(error))
        if len(detail) > 16_384:
            detail = detail[-16_384:]
        payload = {
            "schema_version": "aero-bench.runner-failure/v1",
            "run_id": self.run.run_id,
            "stage": stage,
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": detail,
        }
        try:
            self.run_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            stage_path = self.run_root / f"failure-diagnostic.{stage}.json"
            raw = canonical_json_bytes(payload) + b"\n"
            with stage_path.open("xb") as stage_file:
                stage_file.write(raw)
            path = self.run_root / "failure-diagnostic.json"
            path.write_bytes(raw)
            print(
                f"aero-bench run {self.run.run_id} failed at {stage}: "
                f"{type(error).__name__}: {error}; details={stage_path}",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            return

    def _finish_cancelled(
        self,
        *,
        preflight: PreflightIdentity,
        handle: object,
        seal: SealManifest,
    ) -> RunSummary:
        from aero_bench.runner.execution import (
            _project_and_write_public_trace,
            _seal_identity,
        )

        failure_classes: list[str] = ["controlled_stop"]
        try:
            self.executor.cleanup(handle)
        except Exception:
            failure_classes.append("cleanup_failed")
        with self._condition:
            self._handle = None
        self._transition("projecting")
        public_trace = None
        try:
            public_trace = _project_and_write_public_trace(
                run=self.run,
                seal=seal,
                validated=None,
                bundle_root=self.bundle_root,
                run_root=self.run_root,
            )
        except Exception:
            failure_classes.append("public_trace_projection_failed")
        summary = RunSummary(
            run_id=self.run.run_id,
            executor_kind="docker_reference",
            execution_scope=self.run.execution_scope,
            preflight=preflight,
            status="cancelled",
            seal=_seal_identity(seal),
            verification=None,
            public_trace=public_trace,
            failure_classes=tuple(failure_classes),
        )
        return self._finish(summary, phase="cancelled")

    def _finish_error(
        self,
        *,
        preflight: PreflightIdentity,
        failure_classes: tuple[str, ...],
        seal: SealManifest | None = None,
        validated: ValidatedVerificationOutput | None = None,
        public_trace: PublicTraceIdentity | None = None,
    ) -> RunSummary:
        from aero_bench.runner.execution import _seal_identity, _verification_identity

        summary = RunSummary(
            run_id=self.run.run_id,
            executor_kind="docker_reference",
            execution_scope=self.run.execution_scope,
            preflight=preflight,
            status="error",
            seal=_seal_identity(seal),
            verification=_verification_identity(validated),
            public_trace=public_trace,
            failure_classes=failure_classes,
        )
        return self._finish(summary, phase="error")

    def _finish(
        self,
        summary: RunSummary,
        *,
        phase: Literal["blocked", "completed", "cancelled", "error"],
    ) -> RunSummary:
        with self._condition:
            allowed = _ALLOWED_TRANSITIONS.get(self._phase, frozenset())
            if phase not in allowed:
                raise RuntimeError(
                    f"invalid RunExecutionSession transition {self._phase} -> {phase}"
                )
            self._summary = summary
            self._preflight = summary.preflight
            self._emit(
                event_type=f"session.{phase}",
                phase=phase,
                failure_class=(
                    summary.failure_classes[0] if summary.failure_classes else None
                ),
            )
        return summary

    def _transition(self, phase: RunExecutionPhase) -> None:
        with self._condition:
            allowed = _ALLOWED_TRANSITIONS.get(self._phase, frozenset())
            if phase not in allowed:
                raise RuntimeError(
                    f"invalid RunExecutionSession transition {self._phase} -> {phase}"
                )
        self._emit(event_type=f"session.{phase}", phase=phase)

    def _emit(
        self,
        *,
        event_type: str,
        phase: RunExecutionPhase,
        failure_class: str | None = None,
        runtime_control: RuntimeControlReceipt | None = None,
    ) -> None:
        with self._condition:
            sequence = len(self._transitions)
            transition = RunExecutionTransition(
                schema_version="aero-bench.run-execution-transition/v1",
                run_id=self.run.run_id,
                sequence=sequence,
                event_type=event_type,
                phase=phase,
                wall_time_ns=time.time_ns(),
                failure_class=failure_class,
                runtime_control=runtime_control,
            )
            self._phase = phase
            self._transitions.append(transition)
            self._condition.notify_all()


__all__ = [
    "RunExecutionPhase",
    "RunExecutionSession",
    "RunExecutionSnapshot",
    "RunExecutionTransition",
]
