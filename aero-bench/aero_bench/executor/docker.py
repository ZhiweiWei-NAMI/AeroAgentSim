from __future__ import annotations

import base64
import json
import hashlib
import os
import re
import secrets
import stat
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from uuid import uuid4

from aero_bench.artifacts.contracts import (
    ArtifactRecord,
    SealManifest,
    records_from_requirements,
    seal_manifest_size_upper_bound,
    seal_manifest,
)
from aero_bench.config.loader import BundleReader, sha256_file
from aero_bench.config.models import ArtifactRequirement, ResourceBudget, RuntimeImage
from aero_bench.config.resolver import ResolvedRunSpec, TaskPackageResolver
from aero_bench.executor.contracts import (
    AgentDriverWorkloadContract,
    ExecutionPlan,
    PreflightBlocker,
    PreflightReport,
    WorkloadPlan,
)
from aero_bench.executor.planning import build_execution_plan
from aero_bench.providers.registry import ProviderRegistry
from aero_bench.runtime.control import (
    RuntimeControlOperation,
    RuntimeControlReceipt,
    RuntimeProjectionBatch,
)
from aero_bench.runtime.events import ProcessStreamChunk
from aero_bench.runtime.ledger import EventLedger
from aero_bench.runtime.evidence import validate_authoritative_runtime_ledger
from aero_bench.serialization import canonical_json_bytes
from aero_bench.verifier.output import (
    ValidatedVerificationOutput,
    load_and_validate_verification_output,
)


class DockerExecutorError(RuntimeError):
    pass


class _DockerCommandTimeout(DockerExecutorError):
    """A redacted command timeout that still triggers runtime diagnostics."""


class _DockerResourceOwnershipError(DockerExecutorError):
    """Raised when Docker returned a resource owned by another run."""


@dataclass(slots=True)
class DockerExecutionHandle:
    run_id: str
    agent_network: str
    provider_network: str
    egress_network: str
    input_mount_path: str
    artifact_mount_path: str
    seal_mount_path: str
    runtime_containers: dict[str, str] = field(default_factory=dict)
    input_volumes: dict[str, str] = field(default_factory=dict)
    artifact_volumes: dict[str, str] = field(default_factory=dict)
    volume_keeper_containers: list[str] = field(default_factory=list)
    verifier_container: str | None = None
    seal_volume: str | None = None
    seal_root: Path | None = None
    pre_verification_seal: SealManifest | None = None
    owner_token: str = field(default_factory=lambda: uuid4().hex)
    created_networks: list[str] = field(default_factory=list)
    created_volumes: list[str] = field(default_factory=list)
    staging_containers: list[str] = field(default_factory=list)
    cleanup_errors: tuple[str, ...] = ()
    agent_credentials: dict[str, str] = field(default_factory=dict, repr=False)
    session_credentials: dict[str, str] = field(default_factory=dict, repr=False)
    attempt_id: str = field(default_factory=lambda: f"attempt.{uuid4().hex}")
    provider_credentials: dict[str, str] = field(default_factory=dict, repr=False)
    runtime_control_token: str = field(
        default_factory=lambda: secrets.token_hex(32), repr=False
    )
    stream_ingest_token: str = field(
        default_factory=lambda: secrets.token_hex(32), repr=False
    )
    stream_ingest_host: str | None = None
    stream_pump_processes: list[subprocess.Popen[bytes]] = field(
        default_factory=list, repr=False
    )
    stream_pump_threads: list[threading.Thread] = field(
        default_factory=list, repr=False
    )
    stream_pump_errors: list[str] = field(default_factory=list, repr=False)
    controlled_stop_audit_event_id: str | None = None
    controlled_stop_errors: tuple[str, ...] = ()
    fail_fast_runtime: bool = False
    stream_pump_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class DockerExecutor:
    """Single-host executor with role-isolated networks and evidence volumes."""

    _RESOURCE_INSPECT_TIMEOUT_SECONDS = 10
    _CONTROL_COMMAND_TIMEOUT_SECONDS = 30
    _READINESS_POLL_INTERVAL_SECONDS = 0.25
    _SHARED_READINESS_PROBE_PATH = "/opt/aero-bench/readiness_probe.py"
    _SHARED_READINESS_PROBE_INTERPRETERS = frozenset({"python", "python3"})
    _SHARED_READINESS_PROBE_TIMEOUT_SECONDS = "2"

    def __init__(
        self,
        *,
        docker_binary: str,
        input_mount_path: str,
        artifact_mount_path: str,
        seal_mount_path: str,
        volume_keeper_mount_path: str,
        input_volume_size_bytes: int,
        artifact_volume_size_bytes: int,
        scratch_size_bytes: int,
        pids_limit: int,
        workload_uid: int,
        workload_gid: int,
        provider_bind_host: str,
        gateway_bind_host: str,
        readiness_timeout_seconds: int,
        volume_keeper_image: str,
        volume_keeper_command: tuple[str, ...],
        volume_keeper_cpu_millicores: int,
        volume_keeper_memory_mib: int,
        task_package_resolvers: tuple[TaskPackageResolver, ...],
        provider_registry: ProviderRegistry,
        attempt_id: str | None = None,
        model_auth_file: str | None = None,
        model_https_proxy: str | None = None,
    ):
        if not docker_binary:
            raise ValueError("docker_binary must be explicit")
        self._validate_bind_host(provider_bind_host, "provider_bind_host")
        self._validate_bind_host(gateway_bind_host, "gateway_bind_host")
        paths = {
            "input_mount_path": input_mount_path,
            "artifact_mount_path": artifact_mount_path,
            "seal_mount_path": seal_mount_path,
            "volume_keeper_mount_path": volume_keeper_mount_path,
        }
        for name, value in paths.items():
            path = PurePosixPath(value)
            if not path.is_absolute() or ".." in path.parts or str(path) != value:
                raise ValueError(
                    f"{name} must be an absolute normalized container path"
                )
        if len(set(paths.values())) != len(paths):
            raise ValueError("input, artifact, and seal mount paths must be distinct")
        for name, value in {
            "input_volume_size_bytes": input_volume_size_bytes,
            "artifact_volume_size_bytes": artifact_volume_size_bytes,
            "scratch_size_bytes": scratch_size_bytes,
            "pids_limit": pids_limit,
            "workload_uid": workload_uid,
            "workload_gid": workload_gid,
        }.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        self._docker_binary = docker_binary
        self._input_mount_path = input_mount_path
        self._artifact_mount_path = artifact_mount_path
        self._seal_mount_path = seal_mount_path
        self._volume_keeper_mount_path = volume_keeper_mount_path
        self._input_volume_size_bytes = input_volume_size_bytes
        self._artifact_volume_size_bytes = artifact_volume_size_bytes
        self._scratch_size_bytes = scratch_size_bytes
        self._pids_limit = pids_limit
        self._workload_uid = workload_uid
        self._workload_gid = workload_gid
        self._provider_bind_host = provider_bind_host
        self._gateway_bind_host = gateway_bind_host
        if readiness_timeout_seconds <= 0:
            raise ValueError("readiness_timeout_seconds must be positive")
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._volume_keeper_image = RuntimeImage(
            image=volume_keeper_image,
            command=volume_keeper_command,
        )
        self._volume_keeper_resources = ResourceBudget(
            cpu_millicores=volume_keeper_cpu_millicores,
            memory_mib=volume_keeper_memory_mib,
            gpu_count=0,
        )
        if not task_package_resolvers:
            raise ValueError("task_package_resolvers must be explicit and non-empty")
        self._task_package_resolvers = tuple(task_package_resolvers)
        self._provider_registry = provider_registry
        self._handles: dict[str, DockerExecutionHandle] = {}
        if attempt_id is not None and (
            len(attempt_id) > 128
            or re.fullmatch(r"[a-z][a-z0-9_.-]*", attempt_id) is None
        ):
            raise ValueError("attempt_id must be a declared identifier")
        self._attempt_id = attempt_id
        self._model_auth_file = model_auth_file
        self._model_https_proxy = model_https_proxy
        self._captured_model_auth: bytes | None = None

    def _model_auth_bytes(self) -> bytes:
        """Read executor-owned authentication without publishing it into the run."""
        if self._model_auth_file is None:
            raise DockerExecutorError(
                "model driver requires an explicit model_auth_file"
            )
        if self._captured_model_auth is not None:
            return self._captured_model_auth
        path = Path(self._model_auth_file)
        if not path.is_absolute() or path.resolve() != path:
            raise DockerExecutorError("model authentication input is unavailable")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source:
                metadata = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or not 1 <= metadata.st_size <= 1_048_576
                ):
                    raise ValueError("invalid authentication file")
                payload = source.read(1_048_577)
                if len(payload) != metadata.st_size:
                    raise ValueError("authentication file changed during capture")
            document = json.loads(payload)
            tokens = document["tokens"]
            if (
                not isinstance(tokens["access_token"], str)
                or not tokens["access_token"]
            ):
                raise ValueError("missing access token")
            if not isinstance(tokens["account_id"], str) or not tokens["account_id"]:
                raise ValueError("missing account identity")
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise DockerExecutorError(
                "model authentication input is invalid"
            ) from error
        self._captured_model_auth = payload
        return payload

    def materialize(
        self, run: ResolvedRunSpec, *, bundle_root: str | Path
    ) -> ExecutionPlan:
        return build_execution_plan(
            run,
            executor_kind="docker_reference",
            bundle_root=bundle_root,
            task_package_resolvers=self._task_package_resolvers,
            provider_registry=self._provider_registry,
        )

    def preflight(self, plan: ExecutionPlan) -> PreflightReport:
        blockers: list[PreflightBlocker] = []
        if plan.executor_kind != "docker_reference":
            blockers.append(
                PreflightBlocker(
                    code="executor.mismatch",
                    detail="DockerExecutor accepts only docker_reference plans",
                )
            )
            return PreflightReport(
                run_id=plan.run.run_id,
                executor_kind="docker_reference",
                execution_scope=plan.run.execution_scope,
                ready=False,
                blockers=tuple(blockers),
            )
        try:
            self._require_canonical_plan(plan)
        except (OSError, ValueError) as error:
            blockers.append(
                PreflightBlocker(
                    code="execution-plan.invalid",
                    detail=str(error),
                )
            )
            return PreflightReport(
                run_id=plan.run.run_id,
                executor_kind="docker_reference",
                execution_scope=plan.run.execution_scope,
                ready=False,
                blockers=tuple(blockers),
            )
        if not plan.run.feasibility.feasible:
            blockers.append(
                PreflightBlocker(
                    code="feasibility.impossible",
                    detail=(
                        "task package theoretical success upper bound is zero: "
                        f"{plan.run.feasibility.failed_conditions}"
                    ),
                )
            )
        blockers.extend(self._artifact_capacity_blockers(plan))
        if any(item.role == "agent_driver" for item in plan.runtime_workloads):
            try:
                self._model_auth_bytes()
            except DockerExecutorError:
                blockers.append(
                    PreflightBlocker(
                        code="model.authentication.unavailable",
                        detail="the declared model driver requires valid executor-managed Codex ChatGPT authentication",
                    )
                )
        binary = shutil.which(self._docker_binary)
        if binary is None:
            blockers.append(
                PreflightBlocker(
                    code="docker.unavailable", detail="docker binary was not found"
                )
            )
            return PreflightReport(
                run_id=plan.run.run_id,
                executor_kind="docker_reference",
                execution_scope=plan.run.execution_scope,
                ready=False,
                blockers=tuple(blockers),
            )

        version = self._run(
            ("version", "--format", "{{.Server.Version}}"),
            check=False,
            timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
        )
        if version.returncode != 0 or not version.stdout.strip():
            blockers.append(
                PreflightBlocker(
                    code="docker.daemon.unavailable",
                    detail=version.stderr.strip()
                    or "Docker daemon did not report a server version",
                )
            )
        security = self._run(
            ("info", "--format", "{{json .SecurityOptions}}"),
            check=False,
            timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
        )
        try:
            security_options = (
                json.loads(security.stdout) if security.returncode == 0 else []
            )
        except json.JSONDecodeError:
            security_options = []
        if not any(
            isinstance(option, str) and option.startswith("name=seccomp")
            for option in security_options
        ):
            blockers.append(
                PreflightBlocker(
                    code="docker.seccomp.unavailable",
                    detail="Docker daemon does not report its built-in seccomp isolation",
                )
            )
        try:
            reader = BundleReader(Path(plan.bundle_root))
            for workload in (*plan.runtime_workloads, plan.verifier_workload):
                total_size = len(workload.contract.content_utf8.encode("utf-8"))
                for item in workload.bundle_inputs:
                    actual_size = reader.resolve_file(item.source).stat().st_size
                    if actual_size != item.size_bytes:
                        raise ValueError(
                            f"bundle input size differs from plan: {item.source.path}"
                        )
                    total_size += actual_size
                if total_size > self._input_volume_size_bytes:
                    blockers.append(
                        PreflightBlocker(
                            code="docker.input.too-large",
                            detail=(
                                f"{workload.workload_id}: {total_size} bytes exceeds explicit "
                                f"input limit {self._input_volume_size_bytes}"
                            ),
                        )
                    )
        except (OSError, ValueError) as error:
            blockers.append(PreflightBlocker(code="bundle.invalid", detail=str(error)))

        workloads = (*plan.runtime_workloads, plan.verifier_workload)
        images = [
            *(
                (workload.workload_id, workload.workload.runtime)
                for workload in workloads
            ),
            ("executor.volume-keeper", self._volume_keeper_image),
        ]
        available_images: set[str] = set()
        for workload_id, runtime_image in images:
            image = runtime_image.image
            if runtime_image.is_local_id:
                if plan.run.execution_scope != "executor_validation":
                    blockers.append(
                        PreflightBlocker(
                            code="docker.image.reference-forbidden",
                            detail=(
                                f"{workload_id}: local Docker image IDs are allowed "
                                "only for executor_validation"
                            ),
                        )
                    )
                    continue
                inspect = self._run(
                    ("image", "inspect", "--format", "{{json .Id}}", image),
                    check=False,
                    timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
                )
                if inspect.returncode != 0:
                    blockers.append(
                        PreflightBlocker(
                            code="docker.image.unavailable",
                            detail=(
                                f"{workload_id}: "
                                f"{inspect.stderr.strip() or 'local image ID is unavailable'}"
                            ),
                        )
                    )
                    continue
                try:
                    inspected_id = json.loads(inspect.stdout)
                except json.JSONDecodeError:
                    inspected_id = None
                if inspected_id != image:
                    blockers.append(
                        PreflightBlocker(
                            code="docker.image.id-mismatch",
                            detail=(
                                f"{workload_id}: inspected Docker image Id does not "
                                "exactly match the configured local image ID"
                            ),
                        )
                    )
                    continue
                available_images.add(image)
                continue

            inspect = self._run(
                ("image", "inspect", "--format", "{{json .RepoDigests}}", image),
                check=False,
                timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
            )
            if inspect.returncode != 0:
                blockers.append(
                    PreflightBlocker(
                        code="docker.image.unavailable",
                        detail=(
                            f"{workload_id}: "
                            f"{inspect.stderr.strip() or 'digest-pinned image is not local'}"
                        ),
                    )
                )
                continue
            try:
                repo_digests = json.loads(inspect.stdout)
            except json.JSONDecodeError:
                repo_digests = None
            if not isinstance(repo_digests, list) or image not in repo_digests:
                blockers.append(
                    PreflightBlocker(
                        code="docker.image.digest-unverified",
                        detail=(
                            f"{workload_id}: local image metadata does not retain the "
                            "exact configured repository digest"
                        ),
                    )
                )
                continue
            available_images.add(image)

        if plan.run.execution_scope == "formal_benchmark":
            for workload in workloads:
                if workload.role not in {"provider", "harness"}:
                    continue
                image = workload.workload.runtime.image
                if image not in available_images:
                    continue
                try:
                    healthcheck = self._run(
                        (
                            "image",
                            "inspect",
                            "--format",
                            "{{json .Config.Healthcheck}}",
                            image,
                        ),
                        check=False,
                        timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
                    )
                except DockerExecutorError:
                    blockers.append(
                        PreflightBlocker(
                            code="docker.image.healthcheck-unavailable",
                            detail=(
                                f"{workload.workload_id}: "
                                "image healthcheck metadata is unavailable"
                            ),
                        )
                    )
                    continue
                blocker = self._healthcheck_blocker(
                    workload.workload_id,
                    healthcheck,
                )
                if blocker is not None:
                    blockers.append(blocker)

        identity_attestation_required = (
            plan.run.execution_scope == "formal_benchmark"
            or any(workload.workload.runtime.is_local_id for workload in workloads)
        )
        if identity_attestation_required:
            for workload in workloads:
                image = workload.workload.runtime.image
                if image not in available_images:
                    continue
                labels_result = self._run(
                    (
                        "image",
                        "inspect",
                        "--format",
                        "{{json .Config.Labels}}",
                        image,
                    ),
                    check=False,
                    timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
                )
                try:
                    labels = (
                        json.loads(labels_result.stdout)
                        if labels_result.returncode == 0
                        else None
                    )
                except json.JSONDecodeError:
                    labels = None
                identity = workload.workload.implementation
                expected_labels = {
                    "io.aero-bench.component": identity.component_id,
                    "io.aero-bench.implementation-kind": "production",
                    "org.opencontainers.image.revision": identity.source_revision,
                    "org.opencontainers.image.source": identity.source_uri,
                    "org.opencontainers.image.version": identity.version,
                }
                if not isinstance(labels, dict) or any(
                    labels.get(key) != value for key, value in expected_labels.items()
                ):
                    blockers.append(
                        PreflightBlocker(
                            code="docker.image.identity-mismatch",
                            detail=(
                                f"{workload.workload_id}: immutable image labels do not "
                                "match the resolved production implementation identity"
                            ),
                        )
                    )
        return PreflightReport(
            run_id=plan.run.run_id,
            executor_kind="docker_reference",
            execution_scope=plan.run.execution_scope,
            ready=not blockers,
            blockers=tuple(blockers),
        )

    def _artifact_capacity_blockers(
        self, plan: ExecutionPlan
    ) -> tuple[PreflightBlocker, ...]:
        requirements_by_producer: dict[str, list[ArtifactRequirement]] = {}
        for requirement in plan.run.artifact_requirements:
            if requirement.producer_id != "bundle":
                requirements_by_producer.setdefault(requirement.producer_id, []).append(
                    requirement
                )
        blockers: list[PreflightBlocker] = []
        for workload in plan.runtime_workloads:
            requirements = requirements_by_producer.get(workload.workload_id, ())
            declared_bytes = sum(
                requirement.max_size_bytes for requirement in requirements
            )
            if declared_bytes > self._artifact_volume_size_bytes:
                blockers.append(
                    PreflightBlocker(
                        code="docker.artifact-volume-too-small",
                        detail=(
                            f"{workload.workload_id}: declared artifact maximum "
                            f"{declared_bytes} bytes exceeds explicit artifact volume "
                            f"limit {self._artifact_volume_size_bytes}"
                        ),
                    )
                )
        verifier_bytes = sum(
            requirement.max_size_bytes for requirement in plan.run.verification_outputs
        )
        if verifier_bytes > self._artifact_volume_size_bytes:
            blockers.append(
                PreflightBlocker(
                    code="docker.artifact-volume-too-small",
                    detail=(
                        "verifier: declared artifact maximum "
                        f"{verifier_bytes} bytes exceeds explicit artifact volume "
                        f"limit {self._artifact_volume_size_bytes}"
                    ),
                )
            )
        sealed_artifact_bytes = sum(
            requirement.max_size_bytes for requirement in plan.run.artifact_requirements
        )
        seal_manifest_bytes = seal_manifest_size_upper_bound(
            run_id=plan.run.run_id,
            execution_scope=plan.run.execution_scope,
            requirements=plan.run.artifact_requirements,
        )
        sealed_input_bytes = sealed_artifact_bytes + seal_manifest_bytes
        if sealed_input_bytes > self._input_volume_size_bytes:
            blockers.append(
                PreflightBlocker(
                    code="docker.seal-input-volume-too-small",
                    detail=(
                        "verifier: declared sealed snapshot maximum "
                        f"{sealed_input_bytes} bytes (artifacts "
                        f"{sealed_artifact_bytes} + manifest {seal_manifest_bytes}) "
                        "exceeds explicit input volume "
                        f"limit {self._input_volume_size_bytes}"
                    ),
                )
            )
        return tuple(blockers)

    @staticmethod
    def _healthcheck_blocker(
        workload_id: str,
        result: subprocess.CompletedProcess[str],
    ) -> PreflightBlocker | None:
        if result.returncode != 0:
            return PreflightBlocker(
                code="docker.image.healthcheck-unavailable",
                detail=f"{workload_id}: image healthcheck metadata is unavailable",
            )
        try:
            healthcheck = json.loads(result.stdout)
        except json.JSONDecodeError:
            healthcheck = object()
        if healthcheck is None:
            return PreflightBlocker(
                code="docker.image.healthcheck-missing",
                detail=f"{workload_id}: image does not declare a Docker healthcheck",
            )
        if not isinstance(healthcheck, dict):
            return PreflightBlocker(
                code="docker.image.healthcheck-invalid",
                detail=f"{workload_id}: image Docker healthcheck metadata is invalid",
            )
        test = healthcheck.get("Test")
        if test == ["NONE"]:
            return PreflightBlocker(
                code="docker.image.healthcheck-disabled",
                detail=f"{workload_id}: image Docker healthcheck is disabled",
            )
        if not isinstance(test, list) or not test:
            return PreflightBlocker(
                code="docker.image.healthcheck-missing",
                detail=f"{workload_id}: image does not declare a Docker healthcheck",
            )
        if any(not isinstance(item, str) or not item for item in test):
            return PreflightBlocker(
                code="docker.image.healthcheck-invalid",
                detail=f"{workload_id}: image Docker healthcheck metadata is invalid",
            )
        for field_name in (
            "Interval",
            "Timeout",
            "StartPeriod",
            "Retries",
        ):
            value = healthcheck.get(field_name)
            minimum = 0 if field_name == "StartPeriod" else 1
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                return PreflightBlocker(
                    code="docker.image.healthcheck-invalid",
                    detail=(
                        f"{workload_id}: image Docker healthcheck metadata is invalid"
                    ),
                )
        if (
            len(test) != 5
            or test[0] != "CMD"
            or test[1] not in DockerExecutor._SHARED_READINESS_PROBE_INTERPRETERS
            or test[2] != DockerExecutor._SHARED_READINESS_PROBE_PATH
            or test[3:]
            != [
                "--timeout-seconds",
                DockerExecutor._SHARED_READINESS_PROBE_TIMEOUT_SECONDS,
            ]
        ):
            return PreflightBlocker(
                code="docker.image.healthcheck-not-shared",
                detail=(
                    f"{workload_id}: image Docker healthcheck must use the repository "
                    "shared readiness probe"
                ),
            )
        return None

    @staticmethod
    def _validate_bind_host(value: str, name: str) -> None:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is None
        ):
            raise ValueError(f"{name} must be an explicit network host")

    @staticmethod
    def _new_run_credentials(workload_ids: list[str], *, kind: str) -> dict[str, str]:
        if len(workload_ids) != len(set(workload_ids)):
            raise DockerExecutorError(f"declared {kind} workload IDs must be unique")

        credentials: dict[str, str] = {}
        for workload_id in sorted(workload_ids):
            for _ in range(128):
                token = secrets.token_hex(32)
                if (
                    re.fullmatch(r"[0-9a-f]{64}", token) is not None
                    and token != "0" * 64
                    and token not in credentials.values()
                ):
                    credentials[workload_id] = token
                    break
            else:
                raise DockerExecutorError(
                    f"could not allocate unique credentials for declared {kind} "
                    "workloads"
                )
        return credentials

    @classmethod
    def _new_agent_credentials(cls, plan: ExecutionPlan) -> dict[str, str]:
        return cls._new_run_credentials(
            [
                workload.workload_id
                for workload in plan.runtime_workloads
                if workload.role == "agent"
            ],
            kind="agent",
        )

    @classmethod
    def _new_provider_credentials(cls, plan: ExecutionPlan) -> dict[str, str]:
        return cls._new_run_credentials(
            [
                workload.workload_id
                for workload in plan.runtime_workloads
                if workload.role == "provider"
            ],
            kind="provider",
        )

    def start_runtime(self, plan: ExecutionPlan) -> DockerExecutionHandle:
        self._require_ready(plan)
        if plan.run.run_id in self._handles:
            raise DockerExecutorError("a runtime handle already exists for this run")
        prefix = f"aero-{plan.run.run_id[:12]}"
        handle = DockerExecutionHandle(
            run_id=plan.run.run_id,
            agent_network=f"{prefix}-agent",
            provider_network=f"{prefix}-provider",
            egress_network=f"{prefix}-egress",
            input_mount_path=self._input_mount_path,
            artifact_mount_path=self._artifact_mount_path,
            seal_mount_path=self._seal_mount_path,
        )
        if self._attempt_id is not None:
            handle.attempt_id = self._attempt_id
        self._handles[plan.run.run_id] = handle
        try:
            handle.agent_credentials = self._new_agent_credentials(plan)
            handle.provider_credentials = self._new_provider_credentials(plan)
            handle.session_credentials = self._new_run_credentials(
                [
                    agent.agent_id
                    for agent in plan.run.agents
                    if agent.driver is not None
                ],
                kind="agent-session",
            )
            self._create_network(handle, handle.agent_network, internal=True)
            self._create_network(handle, handle.provider_network, internal=True)
            self._create_network(handle, handle.egress_network, internal=False)

            for workload in plan.runtime_workloads:
                suffix = self._resource_suffix(workload.workload_id)
                input_volume = f"{prefix}-{suffix}-input"
                artifact_volume = f"{prefix}-{suffix}-artifacts"
                self._create_volume(handle, input_volume)
                handle.input_volumes[workload.workload_id] = input_volume
                self._create_tmpfs_volume(
                    artifact_volume,
                    self._artifact_volume_size_bytes,
                    handle=handle,
                )
                handle.artifact_volumes[workload.workload_id] = artifact_volume
                keeper_name = f"{prefix}-{suffix}-keeper"
                self._start_volume_keeper(
                    handle,
                    volume=artifact_volume,
                    container_name=keeper_name,
                )
                self._populate_workload_inputs(
                    plan,
                    workload,
                    input_volume,
                    prefix,
                    handle=handle,
                )

                container_name = self._container_name(prefix, workload)
                initial_network = (
                    handle.agent_network
                    if workload.role in {"agent", "agent_driver"}
                    else handle.provider_network
                )
                self._create_container(
                    handle,
                    workload,
                    self._create_arguments(
                        plan,
                        handle,
                        workload,
                        container_name,
                        network=initial_network,
                    ),
                )

            harness_name = handle.runtime_containers["harness"]
            self._run(
                ("network", "connect", handle.agent_network, harness_name),
                timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
            self._run(
                ("network", "connect", handle.egress_network, harness_name),
                timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
            for workload in plan.runtime_workloads:
                if workload.role == "agent_driver":
                    self._run(
                        (
                            "network",
                            "connect",
                            handle.egress_network,
                            handle.runtime_containers[workload.workload_id],
                        ),
                        timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
                    )

            # Unscored urban runs still use real services: wait for dependencies
            # before starting their consumers, and retain recorded process output.
            ordered_startup = plan.run.execution_scope == "formal_benchmark" or (
                plan.run.task.package.package_id == "urban.uav-recovery-demo.v1"
                and all(
                    item.workload.implementation.kind == "production"
                    for item in plan.runtime_workloads
                )
            )
            # A deterministic formal Agent can crash before closing its turn,
            # just as a model driver can. Observe either failure immediately.
            handle.fail_fast_runtime = ordered_startup
            if ordered_startup:
                for role in ("provider", "harness", "agent", "agent_driver"):
                    workloads = tuple(
                        sorted(
                            (
                                workload
                                for workload in plan.runtime_workloads
                                if workload.role == role
                            ),
                            key=lambda item: item.workload_id,
                        )
                    )
                    if not workloads:
                        continue
                    for workload in workloads:
                        self._run(
                            (
                                "start",
                                handle.runtime_containers[workload.workload_id],
                            ),
                            timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
                        )
                    if role in {"provider", "harness"}:
                        try:
                            self._wait_for_health(
                                tuple(
                                    handle.runtime_containers[workload.workload_id]
                                    for workload in workloads
                                ),
                                timeout_seconds=self._readiness_timeout_seconds,
                            )
                        except DockerExecutorError as error:
                            # Consumers can fail because of a still-running Provider.
                            # Capture its logs before startup cleanup removes them.
                            diagnostic = self._runtime_failure_diagnostics(
                                {}, handle=handle, include_all=True
                            )
                            raise DockerExecutorError(
                                f"{error}; startup diagnostics: {diagnostic}"
                            ) from error
                    if role == "harness":
                        self._start_runtime_stream_pumps(
                            plan, handle, workload_role="harness"
                        )
                        self._start_runtime_stream_pumps(
                            plan, handle, workload_role="provider"
                        )
                    elif role in {"agent", "agent_driver"}:
                        self._start_runtime_stream_pumps(
                            plan, handle, workload_role=role
                        )
            else:
                ordered = sorted(
                    plan.runtime_workloads,
                    key=lambda item: {
                        "provider": 0,
                        "harness": 1,
                        "agent": 2,
                        "agent_driver": 3,
                    }[item.role],
                )
                for workload in ordered:
                    self._run(
                        ("start", handle.runtime_containers[workload.workload_id]),
                        timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
                    )
            handle.agent_credentials.clear()
            handle.provider_credentials.clear()
            handle.session_credentials.clear()
            return handle
        except BaseException as error:
            self._raise_after_cleanup(handle, error)

    def _runtime_gateway_host(self, handle: DockerExecutionHandle) -> str:
        if handle.stream_ingest_host is not None:
            return handle.stream_ingest_host
        try:
            harness_name = handle.runtime_containers["harness"]
        except KeyError as error:
            raise DockerExecutorError(
                "Harness runtime container is unavailable"
            ) from error
        result = self._run(
            (
                "inspect",
                "--format",
                '{{(index .NetworkSettings.Networks "'
                + handle.provider_network
                + '").IPAddress}}',
                harness_name,
            ),
            timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
        )
        host = result.stdout.strip()
        if not host or any(character.isspace() for character in host):
            raise DockerExecutorError("Harness runtime address is unavailable")
        handle.stream_ingest_host = host
        return host

    def _start_runtime_stream_pumps(
        self,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        *,
        workload_role: str,
    ) -> None:
        if workload_role not in {"agent", "agent_driver", "harness", "provider"}:
            raise ValueError("runtime stream pump role is invalid")
        self._runtime_gateway_host(handle)

        for workload in sorted(
            (item for item in plan.runtime_workloads if item.role == workload_role),
            key=lambda item: item.workload_id,
        ):
            container_name = handle.runtime_containers[workload.workload_id]
            try:
                process = subprocess.Popen(
                    [
                        self._docker_binary,
                        "logs",
                        "--follow",
                        container_name,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except OSError as error:
                raise DockerExecutorError(
                    f"could not start stream pump for {workload.workload_id}"
                ) from error
            handle.stream_pump_processes.append(process)
            for stream, pipe in (
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                if pipe is None:
                    raise DockerExecutorError("Docker stream pump pipe is unavailable")
                thread = threading.Thread(
                    target=self._pump_runtime_stream,
                    kwargs={
                        "plan": plan,
                        "handle": handle,
                        "workload_id": workload.workload_id,
                        "workload_role": workload_role,
                        "stream": stream,
                        "pipe": pipe,
                    },
                    name=f"aero-stream-{workload.workload_id}-{stream}",
                    daemon=True,
                )
                handle.stream_pump_threads.append(thread)
                thread.start()

    def _pump_runtime_stream(
        self,
        *,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        workload_id: str,
        workload_role: str,
        stream: str,
        pipe: object,
    ) -> None:
        sequence = 0
        byte_offset = 0
        total_bytes = 0
        max_bytes = 256 * 1024
        try:
            while True:
                payload = pipe.read(65_536)  # type: ignore[attr-defined]
                if not isinstance(payload, bytes):
                    raise DockerExecutorError("Docker stream pump returned non-bytes")
                if not payload:
                    self._send_process_stream_chunk(
                        plan=plan,
                        handle=handle,
                        workload_id=workload_id,
                        workload_role=workload_role,
                        stream=stream,
                        sequence=sequence,
                        byte_offset=byte_offset,
                        payload=b"",
                        final=True,
                        truncated=False,
                    )
                    return
                remaining = max_bytes - total_bytes
                if remaining <= 0:
                    self._send_process_stream_chunk(
                        plan=plan,
                        handle=handle,
                        workload_id=workload_id,
                        workload_role=workload_role,
                        stream=stream,
                        sequence=sequence,
                        byte_offset=byte_offset,
                        payload=b"",
                        final=True,
                        truncated=True,
                    )
                    while pipe.read(65_536):  # type: ignore[attr-defined]
                        pass
                    return
                chunk_payload = payload[:remaining]
                accepted = self._send_process_stream_chunk(
                    plan=plan,
                    handle=handle,
                    workload_id=workload_id,
                    workload_role=workload_role,
                    stream=stream,
                    sequence=sequence,
                    byte_offset=byte_offset,
                    payload=chunk_payload,
                    final=False,
                    truncated=False,
                )
                if not accepted:
                    return
                sequence += 1
                byte_offset += len(chunk_payload)
                total_bytes += len(chunk_payload)
                if len(chunk_payload) != len(payload):
                    self._send_process_stream_chunk(
                        plan=plan,
                        handle=handle,
                        workload_id=workload_id,
                        workload_role=workload_role,
                        stream=stream,
                        sequence=sequence,
                        byte_offset=byte_offset,
                        payload=b"",
                        final=True,
                        truncated=True,
                    )
                    while pipe.read(65_536):  # type: ignore[attr-defined]
                        pass
                    return
        except Exception as error:
            with handle.stream_pump_lock:
                handle.stream_pump_errors.append(
                    f"{workload_id}:{stream}:{type(error).__name__}"
                )
        finally:
            try:
                pipe.close()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _send_process_stream_chunk(
        self,
        *,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        workload_id: str,
        workload_role: str,
        stream: str,
        sequence: int,
        byte_offset: int,
        payload: bytes,
        final: bool,
        truncated: bool,
    ) -> bool:
        host = handle.stream_ingest_host
        if host is None or not handle.stream_ingest_token:
            raise DockerExecutorError("stream ingestion authority is unavailable")
        chunk = ProcessStreamChunk(
            schema_version="aero-bench.process-stream-chunk/v1",
            run_id=plan.run.run_id,
            workload_id=workload_id,
            workload_role=workload_role,
            stream=stream,
            content_class=(
                "explicit_output" if workload_role == "agent" else "system_log"
            ),
            sequence=sequence,
            byte_offset=byte_offset,
            captured_wall_time_ns=time.time_ns(),
            payload_base64=base64.b64encode(payload).decode("ascii"),
            payload_size_bytes=len(payload),
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            final=final,
            truncated=truncated,
        )
        request = (
            canonical_json_bytes(
                {
                    "operation": "process.stream.ingest",
                    "token": handle.stream_ingest_token,
                    "chunk": chunk.model_dump(mode="json"),
                }
            )
            + b"\n"
        )
        response_bytes = b""
        try:
            with socket.create_connection(
                (host, plan.run.environment.gateway.port), timeout=5.0
            ) as connection:
                connection.settimeout(5.0)
                connection.sendall(request)
                while b"\n" not in response_bytes:
                    received = connection.recv(65_536)
                    if not received:
                        raise DockerExecutorError(
                            "Harness closed the stream ingestion connection"
                        )
                    response_bytes += received
                    if len(response_bytes) > 1_048_576:
                        raise DockerExecutorError(
                            "stream ingestion response exceeded its byte bound"
                        )
        except OSError as error:
            raise DockerExecutorError("stream ingestion transport failed") from error
        frame, remainder = response_bytes.split(b"\n", 1)
        if remainder:
            raise DockerExecutorError("stream ingestion returned multiple frames")
        try:
            response = json.loads(frame.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DockerExecutorError("stream ingestion response is invalid") from error
        if not isinstance(response, dict):
            raise DockerExecutorError("stream ingestion response is not an object")
        error = response.get("error")
        if isinstance(error, dict):
            if error.get("code") in {"stream.rejected", "gateway.closed"}:
                return False
            raise DockerExecutorError("stream ingestion request was rejected")
        expected = {
            "schema_version": "aero-bench.process-stream-ingest-response/v1",
            "run_id": plan.run.run_id,
            "workload_id": workload_id,
            "stream": stream,
            "sequence": sequence,
            "event_id": response.get("event_id"),
            "closed": final,
        }
        if response != expected or not isinstance(response.get("event_id"), str):
            raise DockerExecutorError("stream ingestion acknowledgement is invalid")
        return True

    def control_runtime(
        self,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        *,
        operation: RuntimeControlOperation,
        control_id: str | None = None,
    ) -> RuntimeControlReceipt:
        receipt = self._request_runtime_control(
            plan, handle, operation=operation, control_id=control_id
        )
        if operation == "stop":
            handle.controlled_stop_audit_event_id = receipt.audit_event_id
            handle.controlled_stop_errors = self._stop_controlled_runtime_containers(
                handle
            )
        return receipt

    def _request_runtime_control(
        self,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        *,
        operation: RuntimeControlOperation,
        control_id: str | None = None,
    ) -> RuntimeControlReceipt:
        self._require_handle(plan, handle)
        if operation not in {"status", "pause", "resume", "step", "stop"}:
            raise ValueError("runtime control operation is invalid")
        if (operation == "status") != (control_id is None):
            raise ValueError("runtime control identity does not match its operation")
        if not handle.runtime_control_token:
            raise DockerExecutorError("runtime control authority is unavailable")
        host = self._runtime_gateway_host(handle)
        request = (
            canonical_json_bytes(
                {
                    "operation": "runtime.control",
                    "token": handle.runtime_control_token,
                    "action": operation,
                    "control_id": control_id,
                }
            )
            + b"\n"
        )
        response_bytes = b""
        timeout_seconds = (
            (2 * plan.run.environment.clock.provider_timeout_ms) / 1000 + 10.0
            if operation == "stop"
            else 5.0
        )
        try:
            with socket.create_connection(
                (host, plan.run.environment.gateway.port),
                timeout=timeout_seconds,
            ) as connection:
                connection.settimeout(timeout_seconds)
                connection.sendall(request)
                while b"\n" not in response_bytes:
                    received = connection.recv(65_536)
                    if not received:
                        raise DockerExecutorError(
                            "Harness closed the runtime control connection"
                        )
                    response_bytes += received
                    if len(response_bytes) > 1_048_576:
                        raise DockerExecutorError(
                            "runtime control response exceeded its byte bound"
                        )
        except OSError as error:
            raise DockerExecutorError("runtime control transport failed") from error
        frame, remainder = response_bytes.split(b"\n", 1)
        if remainder:
            raise DockerExecutorError("runtime control returned multiple frames")
        try:
            response = json.loads(frame.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DockerExecutorError("runtime control response is invalid") from error
        if not isinstance(response, dict):
            raise DockerExecutorError("runtime control response is not an object")
        if isinstance(response.get("error"), dict):
            raise DockerExecutorError("runtime control request was rejected")
        try:
            receipt = RuntimeControlReceipt.model_validate(response)
        except (TypeError, ValueError) as error:
            raise DockerExecutorError("runtime control receipt is invalid") from error
        if (
            receipt.operation != operation
            or receipt.control_id != control_id
            or receipt.status.run_id != plan.run.run_id
        ):
            raise DockerExecutorError("runtime control receipt identity is invalid")
        if operation == "stop":
            if receipt.status.phase != "aborted" or receipt.audit_event_id is None:
                raise DockerExecutorError(
                    "runtime stop did not produce an audited aborted terminal"
                )
        return receipt

    def read_runtime_projection(
        self,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        *,
        after_scene_tick: int,
        after_event_sequence: int,
        max_scene_states: int = 1,
        max_events: int = 128,
    ) -> RuntimeProjectionBatch:
        self._require_handle(plan, handle)
        if not handle.runtime_control_token:
            raise DockerExecutorError("runtime projection authority is unavailable")
        host = self._runtime_gateway_host(handle)
        request = (
            canonical_json_bytes(
                {
                    "operation": "runtime.projection",
                    "token": handle.runtime_control_token,
                    "after_scene_tick": after_scene_tick,
                    "after_event_sequence": after_event_sequence,
                    "max_scene_states": max_scene_states,
                    "max_events": max_events,
                }
            )
            + b"\n"
        )
        response_bytes = b""
        try:
            with socket.create_connection(
                (host, plan.run.environment.gateway.port),
                timeout=5.0,
            ) as connection:
                connection.settimeout(5.0)
                connection.sendall(request)
                connection.shutdown(socket.SHUT_WR)
                while True:
                    received = connection.recv(65_536)
                    if not received:
                        break
                    response_bytes += received
                    if len(response_bytes) > 8_388_608:
                        raise DockerExecutorError(
                            "runtime projection response exceeded its byte bound"
                        )
        except OSError as error:
            raise DockerExecutorError("runtime projection transport failed") from error
        if response_bytes.count(b"\n") != 1 or not response_bytes.endswith(b"\n"):
            raise DockerExecutorError(
                "runtime projection must return exactly one canonical frame"
            )
        frame = response_bytes[:-1]
        try:
            response = json.loads(frame.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DockerExecutorError(
                "runtime projection response is invalid"
            ) from error
        if not isinstance(response, dict) or frame != canonical_json_bytes(response):
            raise DockerExecutorError("runtime projection response is not canonical")
        if isinstance(response.get("error"), dict):
            raise DockerExecutorError("runtime projection request was rejected")
        try:
            batch = RuntimeProjectionBatch.model_validate(response)
        except (TypeError, ValueError) as error:
            raise DockerExecutorError("runtime projection batch is invalid") from error
        if (
            batch.run_id != plan.run.run_id
            or batch.after_scene_tick != after_scene_tick
            or batch.after_event_sequence != after_event_sequence
        ):
            raise DockerExecutorError("runtime projection cursor is invalid")
        return batch

    def _stop_controlled_runtime_containers(
        self,
        handle: DockerExecutionHandle,
    ) -> tuple[str, ...]:
        errors: list[str] = []
        for workload_id, container in sorted(handle.runtime_containers.items()):
            try:
                result = self._run(
                    ("stop", "--time", "10", container),
                    check=False,
                    timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
                )
            except Exception as error:
                errors.append(f"{workload_id}:{type(error).__name__}")
                continue
            if result.returncode != 0 and not self._is_missing_resource(result.stderr):
                errors.append(f"{workload_id}:stop-failed")
        return tuple(errors)

    def _join_runtime_stream_pumps(self, handle: DockerExecutionHandle) -> None:
        for process in handle.stream_pump_processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for thread in handle.stream_pump_threads:
            thread.join(timeout=10)
            if thread.is_alive():
                with handle.stream_pump_lock:
                    handle.stream_pump_errors.append(f"{thread.name}:did-not-stop")

    def _stop_runtime_stream_pumps(self, handle: DockerExecutionHandle) -> None:
        for process in handle.stream_pump_processes:
            if process.poll() is None:
                process.terminate()
        self._join_runtime_stream_pumps(handle)

    def wait_runtime(
        self, handle: DockerExecutionHandle, *, timeout_seconds: int
    ) -> None:
        self._wait_for_containers(
            tuple(handle.runtime_containers.values()),
            timeout_seconds=timeout_seconds,
            stage="runtime",
            controlled_stop_handle=handle,
        )
        self._join_runtime_stream_pumps(handle)

    def collect_failure_outputs(
        self,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        *,
        destination_root: Path,
    ) -> None:
        """Retain bounded declared partial evidence without claiming a valid seal."""
        self._require_handle(plan, handle)
        if destination_root.exists():
            raise ValueError("failure evidence destination must be fresh")
        self._create_private_directory(destination_root)
        errors: list[dict[str, str]] = []
        copied: list[dict[str, object]] = []
        # Abruptly killing a healthy Harness loses its in-memory ledger and
        # SceneState staging. Close it through the audited abort path first,
        # then wait for its failure artifacts before stopping owned containers.
        if "harness" in handle.runtime_containers:
            try:
                status = self._request_runtime_control(
                    plan, handle, operation="status"
                ).status
                if status.phase not in {"completed", "aborted"}:
                    receipt = self._request_runtime_control(
                        plan, handle, operation="stop",
                        control_id="failure-diagnostic.stop",
                    )
                    handle.controlled_stop_audit_event_id = receipt.audit_event_id
                self._run(
                    ("wait", handle.runtime_containers["harness"]),
                    timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
                )
            except Exception as error:
                errors.append(
                    {"workload_id": "harness", "error": f"abort_{type(error).__name__}"}
                )
        for workload_id, container_name in handle.runtime_containers.items():
            try:
                stopped = self._run(
                    ("stop", "--time", "10", container_name),
                    check=False,
                    timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
                )
                if stopped.returncode != 0:
                    errors.append({"workload_id": workload_id, "error": "stop_failed"})
            except Exception as error:
                errors.append(
                    {
                        "workload_id": workload_id,
                        "error": f"stop_{type(error).__name__}",
                    }
                )
        try:
            self._join_runtime_stream_pumps(handle)
        except Exception as error:
            errors.append({"error": f"stream_join_{type(error).__name__}"})
        driver_ids = {
            a.driver.driver_id for a in plan.run.agents if a.driver is not None
        }
        containers = sorted(
            handle.runtime_containers.items(),
            key=lambda item: (item[0] not in driver_ids, item[0]),
        )
        for workload_id, container_name in containers:
            requirements = tuple(
                a
                for a in plan.run.artifact_requirements
                if a.producer_id == workload_id
            )
            try:
                self._require_stopped((container_name,), allow_failed=True)
                self._copy_failure_artifacts(
                    container_name, requirements, destination_root, copied, errors
                )
            except Exception as error:
                errors.append(
                    {
                        "workload_id": workload_id,
                        "error": f"copy_{type(error).__name__}",
                    }
                )
        copied_ids = {item["artifact_id"] for item in copied}
        missing = sorted(
            a.artifact_id
            for a in plan.run.artifact_requirements
            if a.producer_id in handle.runtime_containers
            and a.artifact_id not in copied_ids
        )
        index = {
            "schema_version": "aero-bench.failed-attempt-evidence/v1",
            "run_id": handle.run_id,
            "attempt_id": handle.attempt_id,
            "authoritative_seal": False,
            "artifacts": copied,
            "missing_artifact_ids": missing,
            "collection_errors": errors,
        }
        index_path = destination_root / "failure-evidence.json"
        with index_path.open("xb") as stream:
            stream.write(canonical_json_bytes(index) + b"\n")
        self._make_private_file(index_path)

    def _copy_failure_artifacts(
        self,
        container_name: str,
        requirements: tuple[ArtifactRequirement, ...],
        destination_root: Path,
        copied: list[dict[str, object]],
        errors: list[dict[str, str]],
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="aero-failed-output-") as temporary:
            root = Path(temporary) / "output"
            root.mkdir(mode=0o700)
            result = self._run(
                ("cp", f"{container_name}:{self._artifact_mount_path}/.", str(root)),
                check=False,
                timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                raise DockerExecutorError("failed workload output copy failed")
            for requirement in requirements:
                source = root / requirement.relative_path
                if not source.exists() and not source.is_symlink():
                    continue
                if (
                    source.is_symlink()
                    or not source.is_file()
                    or source.resolve(strict=True) != source
                    or source.stat().st_size > requirement.max_size_bytes
                ):
                    errors.append(
                        {
                            "artifact_id": requirement.artifact_id,
                            "error": "unsafe_or_oversized_output",
                        }
                    )
                    continue
                destination = destination_root / requirement.relative_path
                self._ensure_private_directory(destination.parent)
                shutil.copyfile(source, destination)
                self._make_private_file(destination)
                copied.append(
                    {
                        "artifact_id": requirement.artifact_id,
                        "relative_path": requirement.relative_path,
                        "sha256": sha256_file(destination),
                        "size_bytes": destination.stat().st_size,
                    }
                )

    def collect_and_seal(
        self,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        *,
        destination_root: Path,
    ) -> SealManifest:
        self._require_handle(plan, handle)
        self._require_stopped(tuple(handle.runtime_containers.values()))
        requirements = plan.run.artifact_requirements
        self._validate_requirement_contract(
            requirements, manifest_name="seal-manifest.json"
        )
        if destination_root.exists():
            raise ValueError("seal destination must not already exist")
        self._create_private_directory(destination_root)
        try:
            assets_by_id = {asset.asset_id: asset for asset in plan.run.task.assets}
            reader = BundleReader(Path(plan.bundle_root))
            runtime_requirements: dict[str, list[ArtifactRequirement]] = {
                producer_id: [] for producer_id in handle.runtime_containers
            }
            bundle_requirements: list[ArtifactRequirement] = []
            for requirement in requirements:
                if requirement.producer_id == "bundle":
                    bundle_requirements.append(requirement)
                else:
                    runtime_requirements[requirement.producer_id].append(requirement)
            for producer_id, container_name in handle.runtime_containers.items():
                self._copy_declared_outputs(
                    container_name=container_name,
                    requirements=tuple(runtime_requirements[producer_id]),
                    destination_root=destination_root,
                )
            for requirement in bundle_requirements:
                destination = destination_root / requirement.relative_path
                self._ensure_private_directory(destination.parent)
                asset = assets_by_id[requirement.source_asset_id or ""]
                source = reader.resolve_file(asset.file)
                self._validate_source_asset(source, requirement)
                shutil.copyfile(source, destination)
                self._make_private_file(destination)
            artifacts = records_from_requirements(
                root=destination_root,
                requirements=requirements,
            )
            event_chain_root = self._validate_runtime_event_chain(
                destination_root, artifacts, run=plan.run
            )
            seal = seal_manifest(
                root=destination_root,
                run_id=handle.run_id,
                attempt_id=handle.attempt_id,
                execution_scope=plan.run.execution_scope,
                event_chain_root=event_chain_root,
                artifacts=artifacts,
            )
            self._write_manifest(destination_root / "seal-manifest.json", seal)
        except Exception:
            shutil.rmtree(destination_root, ignore_errors=True)
            raise
        handle.seal_root = destination_root.resolve(strict=True)
        handle.pre_verification_seal = seal
        return seal

    def start_verifier(
        self, plan: ExecutionPlan, seal: SealManifest
    ) -> DockerExecutionHandle:
        handle = self._handles.get(plan.run.run_id)
        if handle is None:
            raise DockerExecutorError("runtime handle does not exist for this run")
        self._require_handle(plan, handle)
        if handle.verifier_container is not None:
            raise DockerExecutorError("verifier has already been started")
        if handle.seal_root is None or handle.pre_verification_seal != seal:
            raise ValueError(
                "verifier can only consume the seal produced by this runtime handle"
            )
        self._reject_manifest_path(seal.artifacts, "seal-manifest.json")

        verified = seal_manifest(
            root=handle.seal_root,
            run_id=seal.run_id,
            attempt_id=seal.attempt_id,
            execution_scope=seal.execution_scope,
            event_chain_root=seal.event_chain_root,
            artifacts=seal.artifacts,
            manifest_name="seal-manifest.json",
        )
        if verified != seal:
            raise ValueError("sealed artifact snapshot no longer matches its manifest")

        verifier = plan.verifier_workload
        prefix = f"aero-{plan.run.run_id[:12]}"
        suffix = self._resource_suffix(verifier.workload_id)
        input_volume = f"{prefix}-{suffix}-input"
        output_volume = f"{prefix}-{suffix}-artifacts"
        seal_volume = f"{prefix}-sealed"
        container_name = self._container_name(prefix, verifier)
        staging_before = tuple(handle.staging_containers)
        keepers_before = tuple(handle.volume_keeper_containers)
        volumes_before = tuple(handle.created_volumes)
        try:
            self._create_volume(handle, input_volume)
            handle.input_volumes[verifier.workload_id] = input_volume
            self._create_tmpfs_volume(
                output_volume,
                self._artifact_volume_size_bytes,
                handle=handle,
            )
            handle.artifact_volumes[verifier.workload_id] = output_volume
            self._create_volume(handle, seal_volume)
            handle.seal_volume = seal_volume
            self._start_volume_keeper(
                handle,
                volume=output_volume,
                container_name=f"{prefix}-{suffix}-keeper",
            )
            self._populate_workload_inputs(
                plan,
                verifier,
                input_volume,
                prefix,
                handle=handle,
            )
            self._populate_seal(
                verifier,
                seal_volume,
                handle.seal_root,
                seal,
                prefix,
                handle=handle,
            )

            self._create_container(
                handle,
                verifier,
                self._create_arguments(
                    plan,
                    handle,
                    verifier,
                    container_name,
                    network="none",
                    seal_volume=seal_volume,
                ),
            )
            self._run(
                ("start", container_name),
                timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
            return handle
        except Exception as error:
            self._raise_after_verifier_cleanup(
                handle,
                verifier.workload_id,
                error,
                staging_before=staging_before,
                keepers_before=keepers_before,
                volumes_before=volumes_before,
            )

    def wait_verifier(
        self, handle: DockerExecutionHandle, *, timeout_seconds: int
    ) -> None:
        if handle.verifier_container is None:
            raise DockerExecutorError("verifier container has not been started")
        self._wait_for_containers(
            (handle.verifier_container,),
            timeout_seconds=timeout_seconds,
            stage="verifier",
            controlled_stop_handle=handle,
        )

    def collect_verification_outputs(
        self,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        *,
        destination_root: Path,
    ) -> ValidatedVerificationOutput:
        self._require_handle(plan, handle)
        if handle.verifier_container is None or handle.pre_verification_seal is None:
            raise DockerExecutorError("verifier has not consumed a sealed runtime")
        self._require_stopped((handle.verifier_container,))
        requirements = plan.run.verification_outputs
        self._validate_requirement_contract(
            requirements, manifest_name="verification-manifest.json"
        )
        if destination_root.exists():
            raise ValueError("verification destination must not already exist")
        self._create_private_directory(destination_root)
        try:
            self._copy_declared_outputs(
                container_name=handle.verifier_container,
                requirements=requirements,
                destination_root=destination_root,
            )
            artifacts = records_from_requirements(
                root=destination_root,
                requirements=requirements,
            )
            verification_seal = seal_manifest(
                root=destination_root,
                run_id=handle.run_id,
                attempt_id=handle.attempt_id,
                execution_scope=plan.run.execution_scope,
                event_chain_root=handle.pre_verification_seal.event_chain_root,
                artifacts=artifacts,
            )
            self._write_manifest(
                destination_root / "verification-manifest.json",
                verification_seal,
            )
            return load_and_validate_verification_output(
                run=plan.run,
                runtime_seal=handle.pre_verification_seal,
                output_seal=verification_seal,
                output_root=destination_root,
                runtime_root=handle.seal_root,
            )
        except Exception:
            shutil.rmtree(destination_root, ignore_errors=True)
            raise

    def cleanup(self, handle: DockerExecutionHandle) -> None:
        self._stop_runtime_stream_pumps(handle)
        containers = [
            *handle.staging_containers,
            *handle.runtime_containers.values(),
            handle.verifier_container,
            *handle.volume_keeper_containers,
        ]
        volumes = [
            *handle.created_volumes,
            *handle.input_volumes.values(),
            *handle.artifact_volumes.values(),
            handle.seal_volume,
        ]
        networks = handle.created_networks
        try:
            errors = self._cleanup_resources(
                handle,
                containers=containers,
                networks=networks,
                volumes=volumes,
            )
            if errors:
                self._record_cleanup_errors(handle, errors)
                self._handles[handle.run_id] = handle
                raise DockerExecutorError(
                    "Docker cleanup failed; handle retained for retry: "
                    + "; ".join(errors)
                )
            handle.cleanup_errors = ()
            self._handles.pop(handle.run_id, None)
        finally:
            handle.agent_credentials.clear()
            handle.provider_credentials.clear()
            handle.session_credentials.clear()
            self._captured_model_auth = None
            handle.stream_ingest_token = ""
            handle.runtime_control_token = ""

    def cleanup_run(self, run_id: str) -> None:
        handle = self._handles.get(run_id)
        if handle is not None:
            self.cleanup(handle)

    def _create_arguments(
        self,
        plan: ExecutionPlan,
        handle: DockerExecutionHandle,
        workload: WorkloadPlan,
        container_name: str,
        *,
        network: str,
        seal_volume: str | None = None,
    ) -> tuple[str, ...]:
        resources = workload.workload.resources
        memory_bytes = resources.memory_mib * 1024 * 1024
        input_volume = handle.input_volumes[workload.workload_id]
        artifact_volume = handle.artifact_volumes[workload.workload_id]
        arguments = [
            "create",
            "--name",
            container_name,
            "--label",
            f"aero-bench/run={handle.run_id}",
            "--label",
            f"aero-bench/owner={handle.owner_token}",
            "--label",
            f"aero-bench/role={workload.role}",
            "--label",
            f"aero-bench/phase={workload.phase}",
            "--network",
            network,
            "--pull",
            "never",
            "--read-only",
            "--user",
            f"{self._workload_uid}:{self._workload_gid}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self._pids_limit),
            "--cpus",
            f"{resources.cpu_millicores / 1000:.3f}",
            "--memory",
            str(memory_bytes),
            "--memory-swap",
            str(memory_bytes),
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={self._scratch_size_bytes}",
            "--mount",
            f"type=volume,src={input_volume},dst={handle.input_mount_path},readonly",
            "--mount",
            f"type=volume,src={artifact_volume},dst={handle.artifact_mount_path}",
            "--env",
            f"AERO_BENCH_RUN_ID={handle.run_id}",
            "--env",
            f"AERO_BENCH_ATTEMPT_ID={handle.attempt_id}",
            "--env",
            f"AERO_BENCH_SEED={plan.run.seed}",
            "--env",
            f"AERO_BENCH_ROLE={workload.role}",
            "--env",
            f"AERO_BENCH_WORKLOAD_ID={workload.workload_id}",
            "--env",
            f"AERO_BENCH_INPUT_DIR={handle.input_mount_path}",
            "--env",
            f"AERO_BENCH_CONTRACT={handle.input_mount_path}/{workload.contract.destination}",
            "--env",
            f"AERO_BENCH_BUNDLE_DIR={handle.input_mount_path}/bundle",
            "--env",
            f"AERO_BENCH_ARTIFACT_DIR={handle.artifact_mount_path}",
        ]
        harness_name = self._container_name(
            f"aero-{plan.run.run_id[:12]}",
            next(item for item in plan.runtime_workloads if item.role == "harness"),
        )
        if workload.role in {"agent", "provider"}:
            arguments.extend(("--env", f"AERO_BENCH_HARNESS_HOST={harness_name}"))
        if workload.role == "provider":
            provider = next(
                item
                for item in plan.run.environment.providers
                if item.provider_id == workload.workload_id
            )
            try:
                provider_token = handle.provider_credentials[workload.workload_id]
            except KeyError as error:
                raise DockerExecutorError(
                    "provider credential is missing for the declared workload"
                ) from error
            arguments.extend(
                (
                    "--env",
                    f"AERO_BENCH_PROVIDER_TOKEN={provider_token}",
                    "--env",
                    f"AERO_BENCH_PROVIDER_BIND_HOST={self._provider_bind_host}",
                    "--env",
                    f"AERO_BENCH_PROVIDER_PORT={provider.port}",
                )
            )
        if workload.role == "agent":
            declared_agent = next(
                agent
                for agent in plan.run.agents
                if agent.agent_id == workload.workload_id
            )
            try:
                agent_token = handle.agent_credentials[workload.workload_id]
            except KeyError as error:
                raise DockerExecutorError(
                    "agent credential is missing for the declared workload"
                ) from error
            arguments.extend(
                (
                    "--env",
                    f"AERO_BENCH_AGENT_TOKEN={agent_token}",
                    "--env",
                    f"AERO_BENCH_GATEWAY_HOST={harness_name}",
                    "--env",
                    f"AERO_BENCH_GATEWAY_PORT={plan.run.environment.gateway.port}",
                )
            )
            if declared_agent.driver is not None:
                arguments.extend(
                    (
                        "--env",
                        f"AERO_BENCH_SESSION_TOKEN={handle.session_credentials[declared_agent.agent_id]}",
                        "--env",
                        f"AERO_BENCH_SESSION_PORT={declared_agent.driver.bridge_port}",
                    )
                )
        if workload.role == "agent_driver":
            contract = AgentDriverWorkloadContract.model_validate_json(
                workload.contract.content_utf8
            )
            agent = contract.context.agent
            if agent.driver is None:
                raise DockerExecutorError(
                    "driver workload lacks its declared agent binding"
                )
            agent_workload = next(
                item
                for item in plan.runtime_workloads
                if item.workload_id == agent.agent_id
            )
            agent_host = self._container_name(
                f"aero-{plan.run.run_id[:12]}", agent_workload
            )
            arguments.extend(
                (
                    "--env",
                    f"AERO_BENCH_SESSION_TOKEN={handle.session_credentials[agent.agent_id]}",
                    "--env",
                    f"AERO_BENCH_SESSION_HOST={agent_host}",
                    "--env",
                    f"AERO_BENCH_SESSION_PORT={agent.driver.bridge_port}",
                    "--env",
                    f"AERO_BENCH_CODEX_AUTH_FILE={handle.input_mount_path}/runtime-secrets/codex-auth.json",
                    "--env",
                    "NO_PROXY=127.0.0.1,localhost," + agent_host,
                    "--env",
                    "no_proxy=127.0.0.1,localhost," + agent_host,
                )
            )
            if self._model_https_proxy is not None:
                arguments.extend(("--env", f"HTTPS_PROXY={self._model_https_proxy}"))
        if workload.role == "harness":
            provider_by_id = {
                provider.provider_id: provider
                for provider in plan.run.environment.providers
            }
            endpoints = {
                item.workload_id: {
                    "host": self._container_name(f"aero-{plan.run.run_id[:12]}", item),
                    "port": provider_by_id[item.workload_id].port,
                }
                for item in plan.runtime_workloads
                if item.role == "provider"
            }
            arguments.extend(
                (
                    "--env",
                    "AERO_BENCH_AGENT_CREDENTIALS="
                    + canonical_json_bytes(handle.agent_credentials).decode("utf-8"),
                    "--env",
                    "AERO_BENCH_PROVIDER_CREDENTIALS="
                    + canonical_json_bytes(handle.provider_credentials).decode("utf-8"),
                    "--env",
                    f"AERO_BENCH_GATEWAY_BIND_HOST={self._gateway_bind_host}",
                    "--env",
                    f"AERO_BENCH_GATEWAY_PORT={plan.run.environment.gateway.port}",
                    "--env",
                    f"AERO_BENCH_STREAM_INGEST_TOKEN={handle.stream_ingest_token}",
                    "--env",
                    f"AERO_BENCH_RUNTIME_CONTROL_TOKEN={handle.runtime_control_token}",
                    "--env",
                    "AERO_BENCH_PROVIDER_ENDPOINTS="
                    + json.dumps(endpoints, sort_keys=True, separators=(",", ":")),
                )
            )
        if seal_volume is not None:
            arguments.extend(
                (
                    "--mount",
                    f"type=volume,src={seal_volume},dst={handle.seal_mount_path},readonly",
                    "--env",
                    f"AERO_BENCH_SEAL_DIR={handle.seal_mount_path}",
                )
            )
        if resources.gpu_count:
            arguments.extend(("--gpus", str(resources.gpu_count)))
        arguments.append(workload.workload.runtime.image)
        arguments.extend(workload.workload.runtime.command)
        return tuple(arguments)

    def _populate_workload_inputs(
        self,
        plan: ExecutionPlan,
        workload: WorkloadPlan,
        volume: str,
        prefix: str,
        *,
        handle: DockerExecutionHandle,
    ) -> None:
        reader = BundleReader(Path(plan.bundle_root))
        with tempfile.TemporaryDirectory(prefix="aero-input-") as temporary:
            root = Path(temporary)
            contract_path = root / workload.contract.destination
            contract_path.parent.mkdir(parents=True, exist_ok=True)
            contract_path.write_text(workload.contract.content_utf8, encoding="utf-8")
            if sha256_file(contract_path) != workload.contract.sha256:
                raise DockerExecutorError(
                    "workload contract changed during input materialization"
                )
            total_size = contract_path.stat().st_size
            if workload.role == "agent_driver":
                auth_bytes = self._model_auth_bytes()
                auth_path = root / "runtime-secrets" / "codex-auth.json"
                auth_path.parent.mkdir(mode=0o700)
                auth_path.write_bytes(auth_bytes)
                total_size += len(auth_bytes)
            for item in workload.bundle_inputs:
                source = reader.resolve_file(item.source)
                destination = root / item.destination
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                if sha256_file(destination) != item.source.sha256:
                    raise DockerExecutorError(
                        "bundle input changed during materialization"
                    )
                materialized_size = destination.stat().st_size
                if materialized_size != item.size_bytes:
                    raise DockerExecutorError(
                        "bundle input size changed during materialization"
                    )
                total_size += materialized_size
            for item in workload.derived_inputs:
                destination = root / item.destination
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(item.content_utf8, encoding="utf-8")
                if sha256_file(destination) != item.sha256:
                    raise DockerExecutorError(
                        "derived input changed during materialization"
                    )
                total_size += destination.stat().st_size
            if total_size > self._input_volume_size_bytes:
                raise DockerExecutorError(
                    f"{workload.workload_id} input exceeds explicit input-volume limit"
                )
            self._make_read_only_tree(root)
            self._copy_tree_into_volume(
                workload,
                volume=volume,
                source_root=root,
                seed_name=f"{prefix}-{self._resource_suffix(workload.workload_id)}-input-seed",
                destination=self._input_mount_path,
                handle=handle,
            )

    def _populate_seal(
        self,
        verifier: WorkloadPlan,
        volume: str,
        seal_root: Path,
        seal: SealManifest,
        prefix: str,
        *,
        handle: DockerExecutionHandle,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="aero-seal-") as temporary:
            root = Path(temporary)
            total_size = 0
            for artifact in seal.artifacts:
                source = seal_root / artifact.relative_path
                destination = root / artifact.relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                total_size += destination.stat().st_size
            manifest_path = root / "seal-manifest.json"
            manifest_path.write_bytes(
                canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
            )
            total_size += manifest_path.stat().st_size
            if total_size > self._input_volume_size_bytes:
                raise DockerExecutorError(
                    "sealed snapshot exceeds explicit input-volume limit"
                )
            self._make_read_only_tree(root)
            self._copy_tree_into_volume(
                verifier,
                volume=volume,
                source_root=root,
                seed_name=f"{prefix}-seal-seed",
                destination=self._seal_mount_path,
                handle=handle,
            )

    def _copy_tree_into_volume(
        self,
        workload: WorkloadPlan,
        *,
        volume: str,
        source_root: Path,
        seed_name: str,
        destination: str,
        handle: DockerExecutionHandle,
    ) -> None:
        self._require_absent("container", seed_name)
        arguments = (
            "create",
            "--name",
            seed_name,
            "--network",
            "none",
            "--label",
            f"aero-bench/run={handle.run_id}",
            "--label",
            f"aero-bench/owner={handle.owner_token}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pull",
            "never",
            "--mount",
            f"type=volume,src={volume},dst={destination}",
            workload.workload.runtime.image,
            *workload.workload.runtime.command,
        )
        self._register(handle.staging_containers, seed_name)
        self._run_create(
            handle,
            "container",
            seed_name,
            arguments,
        )
        self._require_owner("container", seed_name, handle.owner_token)
        stage_error: Exception | None = None
        try:
            self._run(("cp", f"{source_root}/.", f"{seed_name}:{destination}/"))
        except Exception as error:
            stage_error = error
        finally:
            cleanup_error = self._remove_container(handle, seed_name)
        if stage_error is not None and cleanup_error is not None:
            raise DockerExecutorError(
                f"input staging failed: {stage_error}; staging cleanup failed: "
                f"{cleanup_error}"
            ) from stage_error
        if stage_error is not None:
            raise stage_error
        if cleanup_error is not None:
            raise DockerExecutorError(f"staging cleanup failed: {cleanup_error}")

    def _create_tmpfs_volume(
        self,
        name: str,
        size_bytes: int,
        *,
        handle: DockerExecutionHandle,
    ) -> None:
        self._require_absent("volume", name)
        arguments = ["volume", "create", "--driver", "local"]
        arguments.extend(
            (
                "--label",
                f"aero-bench/run={handle.run_id}",
                "--label",
                f"aero-bench/owner={handle.owner_token}",
            )
        )
        arguments.extend(
            (
                "--opt",
                "type=tmpfs",
                "--opt",
                "device=tmpfs",
                "--opt",
                (
                    "o="
                    f"uid={self._workload_uid},gid={self._workload_gid},mode=0750,"
                    f"size={size_bytes}"
                ),
                name,
            )
        )
        self._register(handle.created_volumes, name)
        self._run_create(
            handle,
            "volume",
            name,
            tuple(arguments),
        )
        self._require_owner("volume", name, handle.owner_token)

    def _create_volume(
        self,
        handle: DockerExecutionHandle,
        name: str,
    ) -> None:
        self._require_absent("volume", name)
        self._register(handle.created_volumes, name)
        self._run_create(
            handle,
            "volume",
            name,
            (
                "volume",
                "create",
                "--label",
                f"aero-bench/run={handle.run_id}",
                "--label",
                f"aero-bench/owner={handle.owner_token}",
                name,
            ),
        )
        self._require_owner("volume", name, handle.owner_token)

    def _create_network(
        self,
        handle: DockerExecutionHandle,
        name: str,
        *,
        internal: bool,
    ) -> None:
        self._require_absent("network", name)
        arguments = [
            "network",
            "create",
            "--label",
            f"aero-bench/run={handle.run_id}",
            "--label",
            f"aero-bench/owner={handle.owner_token}",
        ]
        if internal:
            arguments.append("--internal")
        arguments.append(name)
        self._register(handle.created_networks, name)
        self._run_create(
            handle,
            "network",
            name,
            tuple(arguments),
        )
        self._require_owner("network", name, handle.owner_token)

    def _create_container(
        self,
        handle: DockerExecutionHandle,
        workload: WorkloadPlan,
        arguments: tuple[str, ...],
    ) -> None:
        try:
            name = arguments[arguments.index("--name") + 1]
        except (ValueError, IndexError) as error:
            raise DockerExecutorError(
                "Docker container create arguments must include --name"
            ) from error
        self._require_absent("container", name)
        if workload.role == "verifier":
            handle.verifier_container = name
        else:
            handle.runtime_containers[workload.workload_id] = name
        self._run_create(handle, "container", name, arguments)
        self._require_owner("container", name, handle.owner_token)

    def _require_absent(self, resource_kind: str, name: str) -> None:
        result = self._run(
            (resource_kind, "inspect", name),
            check=False,
            timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            raise DockerExecutorError(
                f"Docker {resource_kind} already exists; refusing to reuse {name}"
            )
        if not self._is_missing_resource(result.stderr):
            raise DockerExecutorError(
                f"could not verify that Docker {resource_kind} is absent: "
                f"{name}: {result.stderr.strip() or 'inspect failed'}"
            )

    def _require_owner(
        self,
        resource_kind: str,
        name: str,
        owner_token: str,
    ) -> None:
        state = self._owner_state(resource_kind, name, owner_token)
        if state == "missing":
            raise DockerExecutorError(
                f"Docker {resource_kind} disappeared before ownership verification: "
                f"{name}"
            )

    def _owner_state(
        self,
        resource_kind: str,
        name: str,
        owner_token: str,
    ) -> str:
        labels_template = (
            "{{json .Config.Labels}}"
            if resource_kind == "container"
            else "{{json .Labels}}"
        )
        result = self._run(
            (
                resource_kind,
                "inspect",
                "--format",
                labels_template,
                name,
            ),
            check=False,
            timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            if self._is_missing_resource(result.stderr):
                return "missing"
            raise DockerExecutorError(
                f"could not verify Docker {resource_kind} ownership for {name}: "
                f"{result.stderr.strip() or 'inspect failed'}"
            )
        try:
            labels = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise DockerExecutorError(
                f"Docker {resource_kind} ownership response was invalid for {name}"
            ) from error
        if (
            not isinstance(labels, dict)
            or labels.get("aero-bench/owner") != owner_token
        ):
            raise _DockerResourceOwnershipError(
                f"Docker {resource_kind} {name} is not owned by this execution handle"
            )
        return "owned"

    @staticmethod
    def _is_missing_resource(stderr: str) -> bool:
        message = stderr.lower()
        return any(
            marker in message for marker in ("no such", "not found", "does not exist")
        )

    @staticmethod
    def _register(resources: list[str], name: str) -> None:
        if name not in resources:
            resources.append(name)

    @staticmethod
    def _unregister(resources: list[str], name: str) -> None:
        try:
            resources.remove(name)
        except ValueError:
            pass

    def _start_volume_keeper(
        self,
        handle: DockerExecutionHandle,
        *,
        volume: str,
        container_name: str,
    ) -> None:
        self._require_absent("container", container_name)
        resources = self._volume_keeper_resources
        memory_bytes = resources.memory_mib * 1024 * 1024
        arguments = (
            "run",
            "--detach",
            "--name",
            container_name,
            "--label",
            f"aero-bench/run={handle.run_id}",
            "--label",
            f"aero-bench/owner={handle.owner_token}",
            "--label",
            "aero-bench/role=executor-volume-keeper",
            "--network",
            "none",
            "--pull",
            "never",
            "--read-only",
            "--user",
            f"{self._workload_uid}:{self._workload_gid}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self._pids_limit),
            "--cpus",
            f"{resources.cpu_millicores / 1000:.3f}",
            "--memory",
            str(memory_bytes),
            "--memory-swap",
            str(memory_bytes),
            "--mount",
            (
                f"type=volume,src={volume},dst={self._volume_keeper_mount_path},"
                "readonly"
            ),
            "--entrypoint",
            self._volume_keeper_image.command[0],
            self._volume_keeper_image.image,
            *self._volume_keeper_image.command[1:],
        )
        self._register(handle.volume_keeper_containers, container_name)
        self._run_create(
            handle,
            "container",
            container_name,
            arguments,
        )
        self._require_owner("container", container_name, handle.owner_token)
        running = self._run(
            ("container", "inspect", "--format", "{{.State.Running}}", container_name),
            timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
        ).stdout.strip()
        if running != "true":
            raise DockerExecutorError("artifact volume keeper did not remain running")

    def _run_create(
        self,
        handle: DockerExecutionHandle,
        resource_kind: str,
        name: str,
        arguments: tuple[str, ...],
    ) -> subprocess.CompletedProcess[str]:
        """Run a create command and reconcile a possibly completed daemon call.

        Docker can finish a create on the daemon after the client-side timeout.
        The caller has already registered ``name`` before entering this method;
        on any Docker command error, inspect the exact name and retain the
        registration only when the owner label proves this handle owns it.
        Cleanup then removes the owned resource through the same ownership
        barrier, while an absent resource is forgotten.
        """
        try:
            return self._run(
                arguments,
                timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
        except (DockerExecutorError, subprocess.TimeoutExpired) as error:
            try:
                self._reconcile_create_candidate(handle, resource_kind, name)
            except DockerExecutorError as reconcile_error:
                raise DockerExecutorError(
                    f"Docker {resource_kind} create outcome was unknown for {name}; "
                    f"ownership reconciliation failed: {reconcile_error}"
                ) from error
            raise

    def _reconcile_create_candidate(
        self,
        handle: DockerExecutionHandle,
        resource_kind: str,
        name: str,
    ) -> None:
        state = self._owner_state(resource_kind, name, handle.owner_token)
        if state == "missing":
            if resource_kind == "container":
                self._forget_container(handle, name)
            elif resource_kind == "network":
                self._forget_network(handle, name)
            elif resource_kind == "volume":
                self._forget_volume(handle, name)
            else:
                raise ValueError(f"unsupported Docker resource kind: {resource_kind}")

    def _raise_after_cleanup(
        self,
        handle: DockerExecutionHandle,
        original_error: BaseException,
    ) -> None:
        try:
            self.cleanup(handle)
        except DockerExecutorError as cleanup_error:
            raise DockerExecutorError(
                f"Docker startup failed: {original_error}; "
                f"cleanup failed and handle was retained: {cleanup_error}"
            ) from original_error
        finally:
            handle.agent_credentials.clear()
            handle.provider_credentials.clear()
        raise original_error

    def _raise_after_verifier_cleanup(
        self,
        handle: DockerExecutionHandle,
        verifier_id: str,
        original_error: Exception,
        *,
        staging_before: tuple[str, ...],
        keepers_before: tuple[str, ...],
        volumes_before: tuple[str, ...],
    ) -> None:
        try:
            self._cleanup_verifier_resources(
                handle,
                verifier_id,
                staging_before=staging_before,
                keepers_before=keepers_before,
                volumes_before=volumes_before,
            )
        except DockerExecutorError as cleanup_error:
            raise DockerExecutorError(
                f"Verifier startup failed: {original_error}; cleanup failed and "
                f"handle was retained: {cleanup_error}"
            ) from original_error
        raise original_error

    def _cleanup_verifier_resources(
        self,
        handle: DockerExecutionHandle,
        verifier_id: str,
        *,
        staging_before: tuple[str, ...],
        keepers_before: tuple[str, ...],
        volumes_before: tuple[str, ...],
    ) -> None:
        staging = [
            name for name in handle.staging_containers if name not in staging_before
        ]
        keepers = [
            name
            for name in handle.volume_keeper_containers
            if name not in keepers_before
        ]
        containers = [
            *staging,
            handle.verifier_container,
            *keepers,
        ]
        volumes = [
            name for name in handle.created_volumes if name not in volumes_before
        ]
        volumes.extend(
            [
                handle.input_volumes.get(verifier_id),
                handle.artifact_volumes.get(verifier_id),
                handle.seal_volume,
            ]
        )
        errors = self._cleanup_resources(
            handle,
            containers=containers,
            networks=(),
            volumes=volumes,
        )
        if errors:
            self._record_cleanup_errors(handle, errors)
            raise DockerExecutorError(
                "Verifier resource cleanup failed: " + "; ".join(errors)
            )

    def _cleanup_resources(
        self,
        handle: DockerExecutionHandle,
        *,
        containers: list[str | None] | tuple[str | None, ...],
        networks: list[str] | tuple[str, ...],
        volumes: list[str | None] | tuple[str | None, ...],
    ) -> tuple[str, ...]:
        errors: list[str] = []
        for name in self._unique(item for item in containers if item is not None):
            error = self._remove_container(handle, name)
            if error is not None:
                errors.append(error)
        for name in self._unique(networks):
            result = self._remove_resource(handle, "network", name)
            if result is None:
                self._forget_network(handle, name)
            else:
                errors.append(result)
        for name in self._unique(item for item in volumes if item is not None):
            result = self._remove_resource(handle, "volume", name)
            if result is None:
                self._forget_volume(handle, name)
            else:
                errors.append(result)
        return tuple(errors)

    def _remove_container(
        self,
        handle: DockerExecutionHandle,
        name: str,
    ) -> str | None:
        error = self._remove_resource(handle, "container", name)
        if error is None:
            self._forget_container(handle, name)
        return error

    def _remove_resource(
        self,
        handle: DockerExecutionHandle,
        resource_kind: str,
        name: str,
    ) -> str | None:
        try:
            owner_state = self._owner_state(
                resource_kind,
                name,
                handle.owner_token,
            )
            if owner_state == "missing":
                return None
            result = self._run(
                (resource_kind, "rm", "--force", name)
                if resource_kind == "container"
                else (resource_kind, "rm", name),
                check=False,
                timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
        except Exception as error:
            return f"{resource_kind} {name}: {type(error).__name__}: {error}"
        if result.returncode == 0 or self._is_missing_resource(result.stderr):
            return None
        return (
            f"{resource_kind} {name}: "
            f"{result.stderr.strip() or 'remove command failed'}"
        )

    @staticmethod
    def _unique(values):
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return tuple(result)

    @staticmethod
    def _forget_container(handle: DockerExecutionHandle, name: str) -> None:
        for workload_id, container_name in tuple(handle.runtime_containers.items()):
            if container_name == name:
                del handle.runtime_containers[workload_id]
        if handle.verifier_container == name:
            handle.verifier_container = None
        DockerExecutor._unregister(handle.volume_keeper_containers, name)
        DockerExecutor._unregister(handle.staging_containers, name)

    @staticmethod
    def _forget_network(handle: DockerExecutionHandle, name: str) -> None:
        DockerExecutor._unregister(handle.created_networks, name)

    @staticmethod
    def _forget_volume(handle: DockerExecutionHandle, name: str) -> None:
        DockerExecutor._unregister(handle.created_volumes, name)
        for mapping in (handle.input_volumes, handle.artifact_volumes):
            for workload_id, volume_name in tuple(mapping.items()):
                if volume_name == name:
                    del mapping[workload_id]
        if handle.seal_volume == name:
            handle.seal_volume = None

    @staticmethod
    def _record_cleanup_errors(
        handle: DockerExecutionHandle,
        errors: tuple[str, ...],
    ) -> None:
        handle.cleanup_errors = tuple(errors)

    def _runtime_failure_diagnostics(
        self,
        failures: dict[str, int | None],
        *,
        handle: DockerExecutionHandle | None,
        include_all: bool = False,
    ) -> str:
        """Capture bounded daemon state and logs before failed containers stop.

        A non-zero ``docker wait`` result is otherwise only an opaque exit-code
        map.  The bounded tail is diagnostic evidence for the caller and does
        not alter the authoritative artifact path or cleanup semantics.
        """

        workload_by_container = (
            {}
            if handle is None
            else {
                name: workload for workload, name in handle.runtime_containers.items()
            }
        )
        targets = (
            {container: None for container in handle.runtime_containers.values()}
            if include_all and handle is not None
            else failures
        )
        tokens = (
            ()
            if handle is None
            else (
                *handle.agent_credentials.values(),
                *handle.provider_credentials.values(),
                *handle.session_credentials.values(),
                handle.runtime_control_token,
                handle.stream_ingest_token,
            )
        )
        details: list[str] = []
        for container, exit_code in targets.items():
            label = workload_by_container.get(container, container)
            state = "unavailable"
            logs = ""
            try:
                inspected = self._run(
                    (
                        "inspect",
                        "--format",
                        "{{.State.Status}}|{{.State.Error}}|{{.State.OOMKilled}}|"
                        "{{.State.ExitCode}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                        container,
                    ),
                    check=False,
                    timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
                )
                if inspected.returncode == 0:
                    state = inspected.stdout.strip()
            except Exception:
                pass
            try:
                logged = self._run(
                    ("logs", "--tail", "80", container),
                    check=False,
                    timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
                )
                logs = (logged.stdout + logged.stderr).strip()
            except Exception:
                pass
            for token in tokens:
                if token:
                    state = state.replace(token, "<redacted>")
                    logs = logs.replace(token, "<redacted>")
            state = " ".join(state[-4_096:].split())
            logs = " ".join(logs[-4_096:].split())
            detail = (
                f"{label}=state:{state}"
                if exit_code is None
                else f"{label}=exit:{exit_code},state:{state}"
            )
            if logs:
                detail += f",logs:{logs}"
            details.append(detail)
        if handle is not None:
            with handle.stream_pump_lock:
                if handle.stream_pump_errors:
                    details.append(
                        "stream_pumps=" + ",".join(handle.stream_pump_errors)
                    )
        return "; ".join(details)

    def _poll_runtime_exit_codes(
        self,
        containers: tuple[str, ...],
        *,
        timeout_seconds: int,
        handle: DockerExecutionHandle,
    ) -> tuple[int, ...]:
        # docker wait waits for every workload, hiding Agent crashes while the
        # Harness is still waiting for those Agents to close their turns.
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _DockerCommandTimeout("runtime exit polling timed out")
            result = self._run(
                (
                    "inspect",
                    "--format",
                    '{"container":{{json .Name}},"state":{{json .State}}}',
                    *containers,
                ),
                timeout_seconds=min(self._RESOURCE_INSPECT_TIMEOUT_SECONDS, remaining),
            )
            try:
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                states = {
                    row["container"].removeprefix("/"): row["state"] for row in rows
                }
                if len(rows) != len(containers) or set(states) != set(containers):
                    raise ValueError("container inventory differs")
                stopped = {}
                for container, state in states.items():
                    if state["Status"] in {"exited", "dead"}:
                        code = state["ExitCode"]
                        if type(code) is not int or code < 0:
                            raise ValueError("invalid exit code")
                        stopped[container] = code
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise DockerExecutorError(
                    "runtime container state was invalid"
                ) from error
            failures = {name: code for name, code in stopped.items() if code != 0}
            if failures and handle.controlled_stop_audit_event_id is None:
                diagnostic = self._runtime_failure_diagnostics(
                    failures, handle=handle, include_all=True
                )
                raise DockerExecutorError(
                    f"runtime workloads failed: {failures}; diagnostics: {diagnostic}"
                )
            if len(stopped) == len(containers):
                return tuple(stopped[container] for container in containers)
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    def _wait_for_containers(
        self,
        containers: tuple[str, ...],
        *,
        timeout_seconds: int,
        stage: str,
        controlled_stop_handle: DockerExecutionHandle | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not containers:
            raise DockerExecutorError(f"{stage} has no containers")
        try:
            if (
                stage == "runtime"
                and controlled_stop_handle is not None
                and controlled_stop_handle.fail_fast_runtime
            ):
                exit_codes = self._poll_runtime_exit_codes(
                    containers,
                    timeout_seconds=timeout_seconds,
                    handle=controlled_stop_handle,
                )
            else:
                result = self._run(
                    ("wait", *containers), timeout_seconds=timeout_seconds
                )
                exit_codes = tuple(
                    int(line) for line in result.stdout.splitlines() if line.strip()
                )
        except (subprocess.TimeoutExpired, _DockerCommandTimeout) as error:
            diagnostic = self._runtime_failure_diagnostics(
                {},
                handle=controlled_stop_handle,
                include_all=True,
            )
            stop_errors: list[str] = []
            for container in containers:
                try:
                    result = self._run(
                        ("stop", "--time", "10", container),
                        check=False,
                        timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
                    )
                except Exception as stop_error:
                    stop_errors.append(
                        f"{container}: {type(stop_error).__name__}: {stop_error}"
                    )
                    continue
                if result.returncode != 0 and not self._is_missing_resource(
                    result.stderr
                ):
                    stop_errors.append(
                        f"{container}: "
                        f"{result.stderr.strip() or 'stop command failed'}"
                    )
            suffix = (
                f"; stop cleanup failed: {'; '.join(stop_errors)}"
                if stop_errors
                else ""
            )
            diagnostic_suffix = f"; diagnostics: {diagnostic}" if diagnostic else ""
            raise DockerExecutorError(
                f"{stage} exceeded its explicit {timeout_seconds}-second timeout"
                f"{suffix}{diagnostic_suffix}"
            ) from error
        if len(exit_codes) != len(containers):
            raise DockerExecutorError(f"docker did not report every {stage} exit code")
        failures = {
            container: code
            for container, code in zip(containers, exit_codes, strict=True)
            if code != 0
        }
        if failures and not (
            stage == "runtime"
            and controlled_stop_handle is not None
            and controlled_stop_handle.controlled_stop_audit_event_id is not None
        ):
            diagnostic = self._runtime_failure_diagnostics(
                failures,
                handle=controlled_stop_handle,
            )
            suffix = f"; diagnostics: {diagnostic}" if diagnostic else ""
            raise DockerExecutorError(f"{stage} workloads failed: {failures}{suffix}")

    def _wait_for_health(
        self,
        containers: tuple[str, ...],
        *,
        timeout_seconds: int,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not containers:
            raise DockerExecutorError("readiness has no containers")

        deadline = time.monotonic() + timeout_seconds
        pending = set(containers)
        while pending:
            for container in tuple(sorted(pending)):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DockerExecutorError(
                        "container readiness exceeded its explicit "
                        f"{timeout_seconds}-second timeout; pending={sorted(pending)}"
                    )
                result = self._run(
                    (
                        "inspect",
                        "--format",
                        "{{json .State}}",
                        container,
                    ),
                    check=False,
                    timeout_seconds=min(
                        self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
                        remaining,
                    ),
                )
                if result.returncode != 0:
                    raise DockerExecutorError(
                        f"container readiness inspect failed: {container}"
                    )
                try:
                    state = json.loads(result.stdout)
                except json.JSONDecodeError:
                    raise DockerExecutorError(
                        f"container readiness state was invalid: {container}"
                    ) from None
                if not isinstance(state, dict):
                    raise DockerExecutorError(
                        f"container readiness state was invalid: {container}"
                    )
                container_status = state.get("Status")
                if container_status != "running":
                    raise DockerExecutorError(
                        self._readiness_failure_detail(
                            container,
                            state,
                            "exited"
                            if container_status in {"exited", "dead"}
                            else "stopped",
                        )
                    )
                health = state.get("Health")
                if not isinstance(health, dict):
                    raise DockerExecutorError(
                        f"container has no Docker health status: {container}"
                    )
                health_status = health.get("Status")
                if health_status == "healthy":
                    pending.remove(container)
                elif health_status == "unhealthy":
                    raise DockerExecutorError(
                        self._readiness_failure_detail(
                            container, state, "became unhealthy"
                        )
                    )
                elif health_status != "starting":
                    raise DockerExecutorError(
                        f"container reported invalid Docker health status: {container}"
                    )
            if pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DockerExecutorError(
                        "container readiness exceeded its explicit "
                        f"{timeout_seconds}-second timeout; pending={sorted(pending)}"
                    )
                # Poll cadence only limits inspect traffic; Docker health is authoritative.
                time.sleep(min(self._READINESS_POLL_INTERVAL_SECONDS, remaining))

    def _readiness_failure_detail(
        self,
        container: str,
        state: Mapping[str, object],
        verb: str,
    ) -> str:
        status = state.get("Status")
        exit_code = state.get("ExitCode")
        detail = (
            f"container {verb} before becoming healthy: {container} "
            f"(status={status}, exit_code={exit_code})"
        )
        excerpt = self._container_log_excerpt(container)
        if excerpt:
            detail += f"; logs={excerpt}"
        return detail

    def _container_log_excerpt(self, container: str) -> str:
        result = self._run(
            ("logs", "--tail", "80", container),
            check=False,
            timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
        )
        text = ((result.stdout or "") + (result.stderr or "")).strip()
        if not text:
            return ""
        if len(text) > 4000:
            text = text[-4000:]
        return self._redact_text(text, ("logs", container))

    def _require_stopped(
        self, containers: tuple[str, ...], *, allow_failed: bool = False
    ) -> None:
        for container in containers:
            result = self._run(
                (
                    "inspect",
                    "--format",
                    "{{.State.Running}} {{.State.ExitCode}}",
                    container,
                ),
                timeout_seconds=self._RESOURCE_INSPECT_TIMEOUT_SECONDS,
            )
            running, exit_code = result.stdout.strip().split()
            if running not in {"true", "false"} or not exit_code.isdecimal():
                raise DockerExecutorError("container stop state is invalid")
            if running == "true":
                raise DockerExecutorError(f"container is still running: {container}")
            if not allow_failed and int(exit_code) != 0:
                raise DockerExecutorError(
                    f"container exited unsuccessfully: {container} ({exit_code})"
                )

    def _copy_declared_outputs(
        self,
        *,
        container_name: str,
        requirements: tuple[ArtifactRequirement, ...],
        destination_root: Path,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="aero-output-snapshot-") as temporary:
            snapshot_root = Path(temporary) / "output"
            snapshot_root.mkdir(mode=0o700)
            self._run(
                (
                    "cp",
                    f"{container_name}:{self._artifact_mount_path}/.",
                    str(snapshot_root),
                ),
                timeout_seconds=self._CONTROL_COMMAND_TIMEOUT_SECONDS,
            )
            self._validate_output_snapshot(
                snapshot_root,
                requirements=requirements,
            )
            for requirement in requirements:
                source = snapshot_root / requirement.relative_path
                destination = destination_root / requirement.relative_path
                self._ensure_private_directory(destination.parent)
                shutil.copyfile(source, destination)
                self._make_private_file(destination)

    @staticmethod
    def _validate_output_snapshot(
        root: Path,
        *,
        requirements: tuple[ArtifactRequirement, ...],
    ) -> None:
        expected = {requirement.relative_path for requirement in requirements}
        max_sizes = {
            requirement.relative_path: requirement.max_size_bytes
            for requirement in requirements
        }
        actual_paths: set[str] = set()
        for candidate in root.rglob("*"):
            relative_path = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                raise ValueError(
                    f"producer artifact output contains a symbolic link: {relative_path}"
                )
            if candidate.is_dir():
                if not any(
                    expected_path.startswith(relative_path + "/")
                    for expected_path in expected
                ):
                    raise ValueError(
                        "producer artifact output contains an undeclared directory: "
                        f"{relative_path}"
                    )
                continue
            if not candidate.is_file():
                raise ValueError(
                    f"producer artifact output contains a special file: {relative_path}"
                )
            if relative_path not in max_sizes:
                actual_paths.add(relative_path)
                continue
            size_bytes = candidate.stat().st_size
            if size_bytes > max_sizes[relative_path]:
                raise ValueError(
                    "producer artifact output exceeds its declared max_size_bytes: "
                    f"{relative_path} ({size_bytes} > {max_sizes[relative_path]})"
                )
            actual_paths.add(relative_path)
        if actual_paths != expected:
            raise ValueError(
                "producer artifact output does not match its complete declared inventory: "
                f"missing={sorted(expected - actual_paths)}, "
                f"undeclared={sorted(actual_paths - expected)}"
            )

    @staticmethod
    def _validate_requirement_contract(
        requirements: tuple[ArtifactRequirement, ...],
        *,
        manifest_name: str,
    ) -> None:
        paths = [requirement.relative_path for requirement in requirements]
        artifact_ids = [requirement.artifact_id for requirement in requirements]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact requirements repeat an artifact_id")
        if len(paths) != len(set(paths)):
            raise ValueError("artifact requirements repeat a relative_path")
        if any(
            PurePosixPath(requirement.relative_path).parts[:1] == (manifest_name,)
            for requirement in requirements
        ):
            raise ValueError(
                f"artifact relative_path is reserved for the {manifest_name}"
            )

    @staticmethod
    def _validate_source_asset(
        source: Path,
        requirement: ArtifactRequirement,
    ) -> None:
        if source.is_symlink() or not source.is_file():
            raise ValueError(
                f"bundle source asset is not a regular file: {requirement.artifact_id}"
            )
        size_bytes = source.stat().st_size
        if size_bytes > requirement.max_size_bytes:
            raise ValueError(
                "bundle source asset exceeds its declared max_size_bytes: "
                f"{requirement.artifact_id} ({size_bytes} > "
                f"{requirement.max_size_bytes})"
            )

    @staticmethod
    def _validate_runtime_event_chain(
        root: Path,
        artifacts: tuple[ArtifactRecord, ...],
        *,
        run: ResolvedRunSpec,
    ) -> str:
        event_logs = [
            artifact for artifact in artifacts if artifact.artifact_type == "event.log"
        ]
        if len(event_logs) != 1 or event_logs[0].producer_id != "harness":
            raise ValueError(
                "runtime seal requires exactly one harness-produced event.log"
            )
        ledger = EventLedger.read_jsonl(root / event_logs[0].relative_path)
        validate_authoritative_runtime_ledger(run, ledger)
        return ledger.chain_root

    @staticmethod
    def _reject_manifest_path(
        artifacts: tuple[ArtifactRecord, ...],
        manifest_name: str,
    ) -> None:
        if any(
            PurePosixPath(artifact.relative_path).parts[:1] == (manifest_name,)
            for artifact in artifacts
        ):
            raise ValueError(
                f"artifact relative_path is reserved for the {manifest_name}"
            )

    @staticmethod
    def _create_private_directory(path: Path) -> None:
        path.mkdir(parents=True, mode=0o700)
        os.chmod(path, 0o700)

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)

    @staticmethod
    def _make_private_file(path: Path) -> None:
        if path.is_symlink():
            raise ValueError(f"artifact copy produced a symbolic link: {path}")
        if path.is_file():
            os.chmod(path, 0o600)

    def _require_ready(self, plan: ExecutionPlan) -> None:
        report = self.preflight(plan)
        if not report.ready:
            raise DockerExecutorError(
                f"Docker executor preflight failed: {report.blockers}"
            )

    def _require_canonical_plan(self, plan: ExecutionPlan) -> None:
        canonical = build_execution_plan(
            plan.run,
            executor_kind="docker_reference",
            bundle_root=plan.bundle_root,
            task_package_resolvers=self._task_package_resolvers,
            provider_registry=self._provider_registry,
        )
        if canonical != plan:
            raise ValueError(
                "ExecutionPlan is not the unique materialization of its ResolvedRun"
            )

    def _require_handle(
        self, plan: ExecutionPlan, handle: DockerExecutionHandle
    ) -> None:
        self._require_canonical_plan(plan)
        if plan.run.run_id != handle.run_id:
            raise ValueError("execution handle belongs to another run")

    def _run(
        self,
        arguments: tuple[str, ...],
        *,
        check: bool = True,
        timeout_seconds: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        binary = shutil.which(self._docker_binary)
        if binary is None:
            raise DockerExecutorError("docker binary was not found")
        command = (binary, *arguments)
        try:
            result = subprocess.run(
                command,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            parts = ["docker command timed out"]
            command_text = self._command_text(error.cmd)
            if command_text:
                parts.append("command=" + self._redact_text(command_text, arguments))
            stdout = self._output_text(error.stdout)
            if stdout:
                parts.append("stdout=" + self._redact_text(stdout, arguments))
            stderr = self._output_text(error.stderr)
            if stderr:
                parts.append("stderr=" + self._redact_text(stderr, arguments))
            raise _DockerCommandTimeout("; ".join(parts)) from None
        if check and result.returncode != 0:
            detail = result.stderr.strip()
            if not detail:
                detail = "docker command failed: " + " ".join(
                    self._redact_arguments(arguments)
                )
            else:
                detail = self._redact_text(detail, arguments)
            raise DockerExecutorError(detail)
        return result

    @staticmethod
    def _redact_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            "AERO_BENCH_AGENT_TOKEN=<redacted>"
            if argument.startswith("AERO_BENCH_AGENT_TOKEN=")
            else "AERO_BENCH_AGENT_CREDENTIALS=<redacted>"
            if argument.startswith("AERO_BENCH_AGENT_CREDENTIALS=")
            else "AERO_BENCH_PROVIDER_TOKEN=<redacted>"
            if argument.startswith("AERO_BENCH_PROVIDER_TOKEN=")
            else "AERO_BENCH_PROVIDER_CREDENTIALS=<redacted>"
            if argument.startswith("AERO_BENCH_PROVIDER_CREDENTIALS=")
            else argument.split("=", 1)[0] + "=<redacted>"
            if argument.startswith(
                (
                    "AERO_BENCH_SESSION_TOKEN=",
                    "AERO_BENCH_RUNTIME_CONTROL_TOKEN=",
                    "AERO_BENCH_STREAM_INGEST_TOKEN=",
                )
            )
            else argument
            for argument in arguments
        )

    @staticmethod
    def _command_text(command: object) -> str:
        if command is None:
            return ""
        if isinstance(command, (tuple, list)):
            return " ".join(str(item) for item in command)
        return str(command)

    @staticmethod
    def _output_text(output: object) -> str:
        if output is None:
            return ""
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")
        return str(output)

    @classmethod
    def _credential_values(cls, arguments: tuple[str, ...]) -> tuple[str, ...]:
        values: list[str] = []
        for argument in arguments:
            for prefix in (
                "AERO_BENCH_AGENT_TOKEN=",
                "AERO_BENCH_AGENT_CREDENTIALS=",
                "AERO_BENCH_PROVIDER_TOKEN=",
                "AERO_BENCH_PROVIDER_CREDENTIALS=",
                "AERO_BENCH_SESSION_TOKEN=",
                "AERO_BENCH_RUNTIME_CONTROL_TOKEN=",
                "AERO_BENCH_STREAM_INGEST_TOKEN=",
            ):
                if not argument.startswith(prefix):
                    continue
                value = argument[len(prefix) :]
                if value:
                    values.append(value)
                if prefix in {
                    "AERO_BENCH_AGENT_CREDENTIALS=",
                    "AERO_BENCH_PROVIDER_CREDENTIALS=",
                }:
                    try:
                        payload = json.loads(value)
                    except (TypeError, json.JSONDecodeError):
                        payload = None
                    values.extend(cls._nested_credential_tokens(payload))
        return tuple(values)

    @classmethod
    def _nested_credential_tokens(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return (value,) if re.fullmatch(r"[0-9a-f]{64}", value) is not None else ()
        if isinstance(value, dict):
            return tuple(
                token
                for item in value.values()
                for token in cls._nested_credential_tokens(item)
            )
        if isinstance(value, (list, tuple)):
            return tuple(
                token for item in value for token in cls._nested_credential_tokens(item)
            )
        return ()

    @classmethod
    def _redact_text(cls, detail: str, arguments: tuple[str, ...]) -> str:
        for value in sorted(
            set(cls._credential_values(arguments)), key=len, reverse=True
        ):
            detail = detail.replace(value, "<redacted>")
        return detail

    @staticmethod
    def _make_read_only_tree(root: Path) -> None:
        for path in root.rglob("*"):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(root, 0o555)

    @staticmethod
    def _write_manifest(path: Path, seal: SealManifest) -> None:
        with path.open("xb") as stream:
            stream.write(canonical_json_bytes(seal.model_dump(mode="json")) + b"\n")
        os.chmod(path, 0o600)

    @staticmethod
    def _resource_suffix(workload_id: str) -> str:
        normalized = "".join(
            character if character.isalnum() or character == "-" else "-"
            for character in workload_id
        ).strip("-")
        digest = hashlib.sha256(workload_id.encode("utf-8")).hexdigest()[:8]
        return f"{normalized[:30].rstrip('-')}-{digest}"

    @classmethod
    def _container_name(cls, prefix: str, workload: WorkloadPlan) -> str:
        return f"{prefix}-{cls._resource_suffix(workload.workload_id)}"[:63].rstrip("-")
