from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from aero_bench.config.loader import load_suite, sha256_file
from aero_bench.config.resolver import ResolvedRunSpec, resolve_suite
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.executor import Executor
from aero_bench.runner.execution import build_docker_executor, load_runner_config
from aero_bench.runtime.control import RuntimeControlOperation, RuntimeProjectionBatch
from aero_bench.runner.session import (
    RunExecutionSession,
    RunExecutionSnapshot,
    RunExecutionTransition,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.providers.rpc import parse_json_object
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.trace.contracts import PublicReplayManifest, PublicTrace
from aero_bench.trace.projector import PublicProjectorError, project_public_scenario

from aero_bench.control.contracts import (
    CONTROL_MUTATIONS,
    CatalogRun,
    ControlCatalog,
    RunAccessCredentials,
    RunStatusResponse,
    RuntimeControlResponse,
    StartRunRequest,
    StartRunResponse,
)


_TERMINAL_PHASES = frozenset({"blocked", "completed", "cancelled", "error"})
_MAX_PUBLIC_ASSET_BYTES = 512 * 1024 * 1024
_MAX_PUBLIC_TRACE_BYTES = 1024 * 1024 * 1024
_MAX_PUBLIC_REPLAY_MANIFEST_BYTES = 8 * 1024 * 1024


class ControlManagerError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(slots=True)
class _ManagedRun:
    start_id: str
    session: RunExecutionSession
    operator_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    shutdown_thread: threading.Thread | None = field(default=None, repr=False)
    shutdown_stop_requested: bool = field(default=False, repr=False)
    control_results: dict[str, tuple[str, RuntimeControlResponse]] = field(
        default_factory=dict,
        repr=False,
    )
    controls_inflight: dict[str, str] = field(default_factory=dict, repr=False)
    management_failure_classes: tuple[str, ...] = ()


class ControlRunManager:
    """Own configured immutable runs and execute them through runner sessions."""

    def __init__(
        self,
        *,
        runs: tuple[ResolvedRunSpec, ...],
        suite_sha256: str,
        bundle_root: Path,
        output_root: Path,
        executor: Executor,
        runtime_timeout_seconds: int,
        verifier_timeout_seconds: int,
    ) -> None:
        if not runs:
            raise ValueError("control manager requires at least one resolved run")
        if not bundle_root.is_absolute() or not output_root.is_absolute():
            raise ValueError("control manager roots must be absolute")
        suite_ids = {run.suite_id for run in runs}
        if len(suite_ids) != 1:
            raise ValueError("control manager runs must belong to one suite")
        run_ids = [run.run_id for run in runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("control manager resolved run IDs must be unique")
        try:
            output_root.mkdir(mode=0o700)
        except FileExistsError:
            raise ControlManagerError(
                "output.not_fresh",
                "control output root must be fresh",
            ) from None
        except OSError:
            raise ControlManagerError(
                "output.unavailable",
                "control output root could not be created",
            ) from None

        self._runs = {run.run_id: run for run in runs}
        self._bundle_root = bundle_root
        self._output_root = output_root
        self._executor = executor
        self._runtime_timeout_seconds = runtime_timeout_seconds
        self._verifier_timeout_seconds = verifier_timeout_seconds
        self._lock = threading.RLock()
        self._managed: dict[str, _ManagedRun] = {}
        self._starts: dict[str, str] = {}
        self._shutdown_requested = False
        self._catalog = ControlCatalog(
            schema_version="aero-bench.control-catalog/v1",
            suite_id=next(iter(suite_ids)),
            suite_sha256=suite_sha256,
            runs=tuple(
                sorted(
                    (
                        CatalogRun(
                            run_id=run.run_id,
                            suite_id=run.suite_id,
                            case_id=run.case_id,
                            execution_scope=run.execution_scope,
                            world_id=run.scenario.world_id,
                            scenario_digest=run.scenario.scenario_digest,
                            task_id=run.task.task_id,
                            seed=run.seed,
                            launch_site_id=run.launch_site_id,
                            matrix_axis_ids=tuple(
                                sorted(override.axis_id for override in run.overrides)
                            ),
                        )
                        for run in runs
                    ),
                    key=lambda item: item.run_id,
                )
            ),
        )

    @classmethod
    def from_files(
        cls,
        *,
        suite_path: str | Path,
        runner_config_path: str | Path,
    ) -> "ControlRunManager":
        loaded_suite = load_suite(suite_path)
        config = load_runner_config(runner_config_path)
        provider_registry = builtin_provider_registry()
        runs = resolve_suite(
            str(loaded_suite.suite_path),
            executor_kind=config.executor_kind,
            task_package_resolvers=builtin_task_package_resolvers(),
            provider_registry=provider_registry,
        )
        executor = build_docker_executor(config, provider_registry=provider_registry)
        return cls(
            runs=runs,
            suite_sha256=sha256_file(loaded_suite.suite_path),
            bundle_root=loaded_suite.root,
            output_root=Path(config.output_root),
            executor=executor,
            runtime_timeout_seconds=config.runtime_timeout_seconds,
            verifier_timeout_seconds=config.verifier_timeout_seconds,
        )

    @classmethod
    def from_compilation(
        cls, *, compilation_root: Path, compilation_id: str,
        runner_config_path: str | Path, output_root: Path,
    ) -> "ControlRunManager":
        """Re-resolve one accepted publication; preserve the existing auth plane."""
        from aero_bench.authoring.draft_compiler import load_published_compilation

        result, bundle, runs = load_published_compilation(compilation_root, compilation_id)
        config = load_runner_config(runner_config_path)
        if {run.executor_kind for run in runs} != {config.executor_kind}:
            raise ControlManagerError(
                "compilation.executor_mismatch",
                "runner configuration differs from the compiled executor profile",
            )
        if result.suite is None:
            raise ValueError("accepted compilation has no suite")
        executor = build_docker_executor(config, provider_registry=builtin_provider_registry())
        return cls(
            runs=runs, suite_sha256=result.suite.sha256, bundle_root=bundle,
            output_root=output_root.resolve(), executor=executor,
            runtime_timeout_seconds=config.runtime_timeout_seconds,
            verifier_timeout_seconds=config.verifier_timeout_seconds,
        )

    @property
    def catalog(self) -> ControlCatalog:
        return self._catalog

    def start(self, request: StartRunRequest) -> StartRunResponse:
        if not isinstance(request, StartRunRequest):
            raise TypeError("control start request is invalid")
        with self._lock:
            if self._shutdown_requested:
                raise ControlManagerError(
                    "service.shutting_down",
                    "control manager is shutting down",
                )
            prior_run_id = self._starts.get(request.start_id)
            if prior_run_id is not None:
                if prior_run_id != request.run_id:
                    raise ControlManagerError(
                        "start.id_conflict",
                        "start_id was already used for another run",
                    )
                return self._start_response(self._managed[prior_run_id])
            run = self._runs.get(request.run_id)
            if run is None:
                raise ControlManagerError(
                    "catalog.run_unknown",
                    "run_id is not present in the configured catalog",
                )
            if request.run_id in self._managed:
                raise ControlManagerError(
                    "run.already_created",
                    "configured run was already created",
                )
            if any(
                managed.session.phase not in _TERMINAL_PHASES
                for managed in self._managed.values()
            ):
                raise ControlManagerError(
                    "run.capacity",
                    "another configured run is still active",
                )

            operator_token = secrets.token_hex(32)
            csrf_token = secrets.token_hex(32)
            while csrf_token == operator_token:
                csrf_token = secrets.token_hex(32)
            session = RunExecutionSession(
                executor=self._executor,
                run=run,
                bundle_root=self._bundle_root,
                output_root=self._output_root,
                runtime_timeout_seconds=self._runtime_timeout_seconds,
                verifier_timeout_seconds=self._verifier_timeout_seconds,
            )
            managed = _ManagedRun(
                start_id=request.start_id,
                session=session,
                operator_token=operator_token,
                csrf_token=csrf_token,
            )
            thread = threading.Thread(
                target=self._execute,
                args=(managed,),
                name=f"aero-run-{run.run_id[:12]}",
                daemon=False,
            )
            managed.thread = thread
            self._managed[run.run_id] = managed
            self._starts[request.start_id] = run.run_id
            thread.start()
            return self._start_response(managed)

    def status(self, run_id: str, *, operator_token: str) -> RunStatusResponse:
        managed = self._authorize_run(run_id, operator_token)
        if managed.session.phase == "running":
            try:
                managed.session.control_runtime("status")
            except Exception as error:
                if managed.session.phase == "running":
                    raise ControlManagerError(
                        "control.unavailable",
                        "runtime status is temporarily unavailable",
                    ) from error
        return RunStatusResponse(
            schema_version="aero-bench.run-status-response/v1",
            snapshot=managed.session.snapshot(),
            management_failure_classes=managed.management_failure_classes,
        )

    def control(
        self,
        run_id: str,
        *,
        operator_token: str,
        operation: str,
        control_id: str,
    ) -> RuntimeControlResponse:
        if operation not in CONTROL_MUTATIONS:
            raise ControlManagerError(
                "control.unsupported",
                "runtime control operation is unsupported",
            )
        managed = self._authorize_run(run_id, operator_token)
        with self._lock:
            prior = managed.control_results.get(control_id)
            if prior is not None:
                prior_operation, response = prior
                if prior_operation != operation:
                    raise ControlManagerError(
                        "control.id_conflict",
                        "control_id was already used for another operation",
                    )
                return response
            inflight = managed.controls_inflight.get(control_id)
            if inflight is not None:
                if inflight != operation:
                    raise ControlManagerError(
                        "control.id_conflict",
                        "control_id was already used for another operation",
                    )
                raise ControlManagerError(
                    "control.in_progress",
                    "runtime control operation is still in progress",
                )
            managed.controls_inflight[control_id] = operation
        try:
            receipt = managed.session.control_runtime(
                cast(RuntimeControlOperation, operation),
                control_id=control_id,
            )
            response = RuntimeControlResponse(
                schema_version="aero-bench.runtime-control-response/v1",
                receipt=receipt,
                snapshot=managed.session.snapshot(),
            )
        except Exception as error:
            with self._lock:
                managed.controls_inflight.pop(control_id, None)
            raise ControlManagerError(
                "control.rejected",
                "runtime control operation was rejected",
            ) from error
        with self._lock:
            managed.control_results[control_id] = (operation, response)
            managed.controls_inflight.pop(control_id, None)
        return response

    def check_csrf(
        self,
        run_id: str,
        *,
        operator_token: str,
        csrf_token: str,
    ) -> None:
        managed = self._authorize_run(run_id, operator_token)
        if not self._credential_matches(csrf_token, managed.csrf_token):
            raise ControlManagerError("csrf.failed", "CSRF validation failed")

    def runtime_projection(
        self,
        run_id: str,
        *,
        operator_token: str,
        after_scene_tick: int,
        after_event_sequence: int,
    ) -> RuntimeProjectionBatch:
        managed = self._authorize_run(run_id, operator_token)
        try:
            return managed.session.runtime_projection(
                after_scene_tick=after_scene_tick,
                after_event_sequence=after_event_sequence,
            )
        except Exception as error:
            if managed.session.phase == "running":
                raise ControlManagerError(
                    "projection.unavailable",
                    "runtime public projection is temporarily unavailable",
                ) from error
            raise ControlManagerError(
                "projection.not_running",
                "runtime public projection is no longer live",
            ) from error

    def public_trace(
        self,
        run_id: str,
        *,
        operator_token: str,
    ) -> PublicTrace | None:
        managed = self._authorize_run(run_id, operator_token)
        summary = managed.session.summary
        if summary is None or summary.public_trace is None:
            return None
        content = self._read_sealed_public_bytes(
            run_id,
            relative_path=summary.public_trace.relative_path,
            expected_sha256=summary.public_trace.sha256,
            max_size_bytes=_MAX_PUBLIC_TRACE_BYTES,
        )
        try:
            document = parse_json_object(content)
            trace = PublicTrace.model_validate(document)
        except Exception as error:
            raise ControlManagerError(
                "projection.invalid",
                "sealed public projection is invalid",
            ) from error
        if (
            content
            != canonical_json_bytes(trace.model_dump(mode="json")) + b"\n"
            or trace.run_id != run_id
            or trace.event_chain_root != summary.public_trace.event_chain_root
        ):
            raise ControlManagerError(
                "projection.invalid",
                "sealed public projection identity is invalid",
            )
        return trace

    def public_trace_document(
        self,
        run_id: str,
        *,
        operator_token: str,
    ) -> bytes:
        """Return the exact sealed ``public-trace.json`` bytes for a terminal run.

        Only runs whose session has reached a terminal phase and whose summary
        records a sealed public trace are served; every other state is an
        explicit conflict so the frontend never treats a receipt or an
        in-progress run as replayable evidence.
        """
        if run_id not in self._runs:
            raise ControlManagerError(
                "catalog.run_unknown",
                "run_id is not present in the configured catalog",
            )
        managed = self._authorize_run(run_id, operator_token)
        summary = managed.session.summary
        if (
            managed.session.phase not in _TERMINAL_PHASES
            or summary is None
            or summary.public_trace is None
        ):
            raise ControlManagerError(
                "run.public_trace_unavailable",
                "run has not produced a sealed public trace",
            )
        return self._read_sealed_public_bytes(
            run_id,
            relative_path=summary.public_trace.relative_path,
            expected_sha256=summary.public_trace.sha256,
            max_size_bytes=_MAX_PUBLIC_TRACE_BYTES,
        )

    def public_replay_manifest_document(
        self,
        run_id: str,
        *,
        operator_token: str,
    ) -> bytes:
        """Return the exact sealed ``replay-manifest.json`` bytes for a terminal run.

        The returned bytes are re-parsed and validated against
        ``PublicReplayManifest`` (the same contract that backs
        ``schemas/generated/public-replay-manifest.schema.json``) and are
        cross-checked against the sealed trace/seal identities recorded in the
        run summary, mirroring the identity checks already applied to
        ``public_trace``.
        """
        if run_id not in self._runs:
            raise ControlManagerError(
                "catalog.run_unknown",
                "run_id is not present in the configured catalog",
            )
        managed = self._authorize_run(run_id, operator_token)
        summary = managed.session.summary
        if (
            managed.session.phase not in _TERMINAL_PHASES
            or summary is None
            or summary.public_trace is None
        ):
            raise ControlManagerError(
                "run.public_trace_unavailable",
                "run has not produced a sealed public replay manifest",
            )
        identity = summary.public_trace.replay
        content = self._read_sealed_public_bytes(
            run_id,
            relative_path=identity.relative_path,
            expected_sha256=identity.sha256,
            max_size_bytes=_MAX_PUBLIC_REPLAY_MANIFEST_BYTES,
        )
        try:
            document = parse_json_object(content)
            manifest = PublicReplayManifest.model_validate(document)
        except Exception as error:
            raise ControlManagerError(
                "projection.invalid",
                "sealed public replay manifest is invalid",
            ) from error
        if (
            content
            != canonical_json_bytes(manifest.model_dump(mode="json", exclude_none=True)) + b"\n"
            or manifest.run_id != run_id
            or manifest.trace_sha256 != summary.public_trace.sha256
            or (
                summary.seal is not None
                and manifest.event_chain_root != summary.seal.event_chain_root
            )
        ):
            raise ControlManagerError(
                "projection.invalid",
                "sealed public replay manifest identity is invalid",
            )
        return content

    def _read_sealed_public_bytes(
        self,
        run_id: str,
        *,
        relative_path: str,
        expected_sha256: str,
        max_size_bytes: int = _MAX_PUBLIC_ASSET_BYTES,
    ) -> bytes:
        source = self._output_root / run_id / relative_path
        descriptor = -1
        try:
            descriptor = os.open(
                source,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                metadata = os.fstat(stream.fileno())
                size_bytes = metadata.st_size
                if not stat.S_ISREG(metadata.st_mode) or not 1 <= size_bytes <= max_size_bytes:
                    raise OSError("sealed public file is invalid")
                content = stream.read(size_bytes + 1)
            if len(content) != size_bytes:
                raise OSError("sealed public file changed while being read")
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise ControlManagerError(
                "projection.unavailable",
                "sealed public file is unavailable",
            ) from error
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ControlManagerError(
                "projection.invalid",
                "sealed public file digest is invalid",
            )
        return content

    def transitions_after(
        self,
        run_id: str,
        *,
        operator_token: str,
        sequence: int,
    ) -> tuple[RunExecutionTransition, ...]:
        managed = self._authorize_run(run_id, operator_token)
        return managed.session.transitions_after(sequence)

    def wait_for_transitions(
        self,
        run_id: str,
        *,
        operator_token: str,
        sequence: int,
        timeout_seconds: float,
    ) -> tuple[RunExecutionTransition, ...]:
        managed = self._authorize_run(run_id, operator_token)
        return managed.session.wait_for_transitions(
            sequence,
            timeout_seconds=timeout_seconds,
        )

    def snapshot(
        self,
        run_id: str,
        *,
        operator_token: str,
    ) -> RunExecutionSnapshot:
        managed = self._authorize_run(run_id, operator_token)
        return managed.session.snapshot()

    def shutdown(self, *, join_timeout_seconds: float = 30.0) -> None:
        if join_timeout_seconds < 0:
            raise ValueError("shutdown join timeout cannot be negative")
        with self._lock:
            self._shutdown_requested = True
            managed_runs = tuple(self._managed.values())
            shutdown_threads: list[threading.Thread] = []
            for managed in managed_runs:
                execution_thread = managed.thread
                if (
                    execution_thread is not None
                    and execution_thread.is_alive()
                    and managed.shutdown_thread is None
                ):
                    shutdown_thread = threading.Thread(
                        target=self._stop_when_running,
                        args=(managed,),
                        name=f"aero-stop-{managed.start_id}",
                        daemon=True,
                    )
                    managed.shutdown_thread = shutdown_thread
                    shutdown_threads.append(shutdown_thread)
        for shutdown_thread in shutdown_threads:
            shutdown_thread.start()
        for managed in managed_runs:
            thread = managed.thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=join_timeout_seconds)

    def _stop_when_running(self, managed: _ManagedRun) -> None:
        """Persist a service-shutdown stop across pre-runtime phase transitions."""
        transition_sequence = -1
        while True:
            phase = managed.session.phase
            if phase in _TERMINAL_PHASES:
                return
            if phase == "running":
                with self._lock:
                    if managed.shutdown_stop_requested:
                        return
                    managed.shutdown_stop_requested = True
                control_id = "service-shutdown-" + secrets.token_hex(8)
                try:
                    managed.session.control_runtime(
                        "stop",
                        control_id=control_id,
                    )
                except Exception:
                    if managed.session.phase == "running":
                        self._record_management_failure(
                            managed,
                            "shutdown_stop_failed",
                        )
                    return
                else:
                    return
            transitions = managed.session.wait_for_transitions(
                transition_sequence,
                timeout_seconds=0.25,
            )
            if transitions:
                transition_sequence = transitions[-1].sequence

    def _execute(self, managed: _ManagedRun) -> None:
        summary = managed.session.execute()
        run_root = self._output_root / summary.run_id
        target = run_root / "run-summary.json"
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(
                    canonical_json_bytes(summary.model_dump(mode="json")) + b"\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            self._record_management_failure(managed, "summary_write_failed")

    def _record_management_failure(
        self,
        managed: _ManagedRun,
        failure_class: str,
    ) -> None:
        with self._lock:
            managed.management_failure_classes = tuple(
                sorted({*managed.management_failure_classes, failure_class})
            )

    def _start_response(self, managed: _ManagedRun) -> StartRunResponse:
        run_id = managed.session.run.run_id
        try:
            scenario = project_public_scenario(managed.session.run)
        except PublicProjectorError as error:
            raise ControlManagerError(
                "scenario.unavailable",
                "resolved scenario cannot be projected for the operator console",
            ) from error
        return StartRunResponse(
            schema_version="aero-bench.start-run-response/v1",
            run_id=run_id,
            credentials=RunAccessCredentials(
                schema_version="aero-bench.run-access-credentials/v1",
                run_id=run_id,
                operator_token=managed.operator_token,
                csrf_token=managed.csrf_token,
            ),
            snapshot=managed.session.snapshot(),
            scenario=scenario,
        )

    def public_asset(self, run_id: str, digest: str, *, operator_token: str) -> tuple[bytes, str]:
        managed = self._authorize_run(run_id, operator_token)
        run = self._runs[run_id]

        # Runtime public artifacts are copied into the run directory only after
        # the executor has closed and sealed the authoritative output set.  Do
        # not expose a live writer or infer a digest from an in-progress file.
        summary = getattr(getattr(managed, "session", None), "summary", None)
        if summary is not None and summary.seal is not None:
            sealed_ids = {
                artifact.artifact_id: artifact for artifact in summary.seal.artifacts
            }
            requirement_by_id = {
                requirement.artifact_id: requirement
                for requirement in run.artifact_requirements
                if requirement.visibility == "public"
            }
            runtime_match = next(
                (
                    requirement
                    for artifact_id, requirement in requirement_by_id.items()
                    if artifact_id in sealed_ids
                    and sealed_ids[artifact_id].sha256 == digest
                ),
                None,
            )
            if runtime_match is not None:
                sealed = sealed_ids[runtime_match.artifact_id]
                data = self._read_public_asset_bytes(
                    root=self._output_root / run_id,
                    relative_path=runtime_match.relative_path,
                    expected_sha256=digest,
                    expected_size_bytes=sealed.size_bytes,
                )
                return data, "application/octet-stream"

        asset = next((asset for asset in run.scenario.assets
                      if asset.classification == "public" and asset.file.sha256 == digest), None)
        if asset is not None:
            data = self._read_public_asset_bytes(
                root=self._bundle_root,
                relative_path=asset.file.path,
                expected_sha256=digest,
                expected_size_bytes=asset.byte_size,
            )
            return data, "application/octet-stream" if asset.world is None else asset.world.media_type

        # Indexed replay adds derived index/shard files after the runtime seal.
        # Their authority is the digest-bound terminal replay manifest, not a
        # guessed file path or the runtime artifact requirement inventory.
        if (
            summary is not None
            and summary.public_trace is not None
            and managed.session.phase in _TERMINAL_PHASES
        ):
            manifest = PublicReplayManifest.model_validate(
                parse_json_object(
                    self.public_replay_manifest_document(
                        run_id, operator_token=operator_token
                    )
                )
            )
            replay_file = next(
                (
                    item for item in manifest.files
                    if item.relative_path != "public-trace.json"
                    and item.sha256 == digest
                ),
                None,
            )
            if replay_file is not None:
                data = self._read_public_asset_bytes(
                    root=self._output_root / run_id / "public" / "replay",
                    relative_path=replay_file.relative_path,
                    expected_sha256=digest,
                    expected_size_bytes=replay_file.size_bytes,
                )
                return data, "application/octet-stream"

        raise ControlManagerError("asset.not_found", "declared public asset not found")

    @staticmethod
    def _read_public_asset_bytes(
        *,
        root: Path,
        relative_path: str,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> bytes:
        # Match the viewer's existing 512 MiB per-asset bound. City histories
        # exceed the old 64 MiB cap without changing their sealed identity.
        if expected_size_bytes > _MAX_PUBLIC_ASSET_BYTES:
            raise ControlManagerError("asset.too_large", "public asset exceeds delivery limit")
        try:
            path = (root / relative_path).resolve(strict=True)
            if not path.is_relative_to(root.resolve(strict=True)):
                raise ValueError("asset outside declared public root")
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size_bytes:
                    raise ValueError("asset size or file type mismatch")
                data = stream.read(expected_size_bytes + 1)
            if len(data) != expected_size_bytes or hashlib.sha256(data).hexdigest() != expected_sha256:
                raise ValueError("asset digest mismatch")
        except (OSError, ValueError) as error:
            raise ControlManagerError("asset.invalid", "public asset integrity validation failed") from error
        return data

    def _authorize_run(self, run_id: str, operator_token: str) -> _ManagedRun:
        with self._lock:
            managed = self._managed.get(run_id)
        if managed is None or not self._credential_matches(
            operator_token,
            managed.operator_token,
        ):
            raise ControlManagerError(
                "authentication.failed",
                "run authentication failed",
            )
        return managed

    @staticmethod
    def _credential_matches(candidate: str, expected: str) -> bool:
        if (
            not isinstance(candidate, str)
            or len(candidate) != 64
            or any(character not in "0123456789abcdef" for character in candidate)
        ):
            return False
        candidate_digest = hashlib.sha256(candidate.encode("ascii")).digest()
        expected_digest = hashlib.sha256(expected.encode("ascii")).digest()
        return hmac.compare_digest(candidate_digest, expected_digest)


__all__ = ["ControlManagerError", "ControlRunManager"]
