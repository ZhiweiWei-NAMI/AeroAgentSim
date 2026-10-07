from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, StrictInt, StrictStr, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel


_HOST = re.compile(r"^[A-Za-z0-9_.:-]+$")
_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


class RunnerConfig(StrictModel):
    """Explicit infrastructure configuration for the Docker reference runner."""

    schema_version: Literal["aero-bench.runner-config/v1"]
    executor_kind: Literal["docker_reference"]
    output_root: StrictStr
    runtime_timeout_seconds: StrictInt = Field(gt=0)
    verifier_timeout_seconds: StrictInt = Field(gt=0)
    readiness_timeout_seconds: StrictInt = Field(gt=0)

    docker_binary: StrictStr = Field(min_length=1)
    input_mount_path: StrictStr = Field(min_length=1)
    artifact_mount_path: StrictStr = Field(min_length=1)
    seal_mount_path: StrictStr = Field(min_length=1)
    volume_keeper_mount_path: StrictStr = Field(min_length=1)
    input_volume_size_bytes: StrictInt = Field(gt=0)
    artifact_volume_size_bytes: StrictInt = Field(gt=0)
    scratch_size_bytes: StrictInt = Field(gt=0)
    pids_limit: StrictInt = Field(gt=0)
    workload_uid: StrictInt = Field(gt=0)
    workload_gid: StrictInt = Field(gt=0)
    provider_bind_host: StrictStr = Field(min_length=1)
    gateway_bind_host: StrictStr = Field(min_length=1)
    volume_keeper_image: StrictStr = Field(pattern=_IMAGE.pattern)
    volume_keeper_command: tuple[Annotated[StrictStr, Field(min_length=1)], ...] = (
        Field(min_length=1)
    )
    volume_keeper_cpu_millicores: StrictInt = Field(gt=0)
    volume_keeper_memory_mib: StrictInt = Field(ge=64)
    attempt_id: Annotated[Identifier, Field(max_length=128)] | None = None
    model_auth_file: StrictStr | None = None
    model_https_proxy: StrictStr | None = None

    @model_validator(mode="after")
    def validate_paths_and_hosts(self) -> "RunnerConfig":
        if self.model_auth_file is not None:
            auth = Path(self.model_auth_file)
            if not auth.is_absolute() or str(auth) != os.path.normpath(str(auth)):
                raise ValueError("model_auth_file must be an absolute normalized path")
        if self.model_https_proxy is not None:
            proxy = urlsplit(self.model_https_proxy)
            if proxy.scheme not in {"http", "https"} or not proxy.hostname or proxy.username or proxy.password or proxy.query or proxy.fragment:
                raise ValueError("model_https_proxy must be an explicit proxy URL without credentials")
        output = Path(self.output_root)
        if (
            not output.is_absolute()
            or str(output) != os.path.normpath(self.output_root)
            or ".." in output.parts
            or "\\" in self.output_root
        ):
            raise ValueError("output_root must be an absolute normalized path")

        paths = {
            "input_mount_path": self.input_mount_path,
            "artifact_mount_path": self.artifact_mount_path,
            "seal_mount_path": self.seal_mount_path,
            "volume_keeper_mount_path": self.volume_keeper_mount_path,
        }
        for name, value in paths.items():
            path = PurePosixPath(value)
            if not path.is_absolute() or ".." in path.parts or str(path) != value:
                raise ValueError(f"{name} must be an absolute normalized path")
        if len(set(paths.values())) != len(paths):
            raise ValueError("RunnerConfig mount paths must be distinct")

        for name, value in {
            "provider_bind_host": self.provider_bind_host,
            "gateway_bind_host": self.gateway_bind_host,
        }.items():
            if _HOST.fullmatch(value) is None:
                raise ValueError(f"{name} must be an explicit network host")
        if self.volume_keeper_image.rsplit(":", maxsplit=1)[-1] == "0" * 64:
            raise ValueError("volume_keeper_image must use a real digest")
        return self


class PreflightIdentity(StrictModel):
    ready: bool
    blocker_codes: tuple[Identifier, ...]


class ArtifactIdentity(StrictModel):
    artifact_id: Identifier
    sha256: Sha256
    size_bytes: StrictInt = Field(ge=0)


class SealIdentity(StrictModel):
    schema_version: Literal["aero-bench.seal/v2"]
    run_id: Sha256
    attempt_id: Annotated[Identifier, Field(max_length=128)]
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    manifest_digest: Sha256
    event_chain_root: Sha256
    artifacts: tuple[ArtifactIdentity, ...]


class VerificationIdentity(StrictModel):
    schema_version: Literal["aero-bench.verification/v1"]
    run_id: Sha256
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    status: Literal["passed", "failed", "invalid"]
    output_manifest_digest: Sha256
    goal_ids: tuple[Identifier, ...]


class PublicReplayIdentity(StrictModel):
    schema_version: Literal["aero-bench.public-replay-manifest/v1"]
    relative_path: Literal["public/replay/replay-manifest.json"]
    sha256: Sha256
    file_count: StrictInt = Field(gt=0)
    total_size_bytes: StrictInt = Field(gt=0)


class PublicTraceIdentity(StrictModel):
    schema_version: Literal["aero-bench.public-trace/v3"]
    projector_version: Literal["aero-bench.public-projector/v2"]
    relative_path: Literal["public/public-trace.json"]
    sha256: Sha256
    event_chain_root: Sha256
    replay: PublicReplayIdentity


class RunSummary(StrictModel):
    run_id: Sha256
    executor_kind: Literal["docker_reference"]
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    preflight: PreflightIdentity
    status: Literal[
        "passed", "failed", "invalid", "blocked", "cancelled", "error"
    ]
    seal: SealIdentity | None
    verification: VerificationIdentity | None
    public_trace: PublicTraceIdentity | None
    failure_classes: tuple[Identifier, ...]

    @model_validator(mode="after")
    def benchmark_status_requires_formal_scope(self) -> "RunSummary":
        if self.execution_scope == "executor_validation" and self.status in {
            "passed",
            "failed",
        }:
            raise ValueError(
                "executor_validation cannot produce a benchmark pass or failure"
            )
        if self.public_trace is not None:
            if self.seal is None:
                raise ValueError("public trace identity requires a runtime seal")
            if self.public_trace.event_chain_root != self.seal.event_chain_root:
                raise ValueError("public trace event root does not match runtime seal")
        if self.execution_complete and self.public_trace is None:
            raise ValueError("a completed execution requires a public trace identity")
        return self

    @property
    def execution_complete(self) -> bool:
        return self.status in {"passed", "failed"} or (
            self.execution_scope == "executor_validation" and self.status == "invalid"
        )


class RunnerSummary(StrictModel):
    schema_version: Literal["aero-bench.runner-summary/v1"]
    executor_kind: Literal["docker_reference"]
    suite_sha256: Sha256
    runner_config_sha256: Sha256
    run_count: StrictInt = Field(ge=0)
    execution_complete_count: StrictInt = Field(ge=0)
    passed_count: StrictInt = Field(ge=0)
    runs: tuple[RunSummary, ...]

    @model_validator(mode="after")
    def counts_match_runs(self) -> "RunnerSummary":
        if self.run_count != len(self.runs):
            raise ValueError("runner summary run_count does not match runs")
        if self.execution_complete_count != sum(
            run.execution_complete for run in self.runs
        ):
            raise ValueError(
                "runner summary execution_complete_count does not match runs"
            )
        if self.passed_count != sum(run.status == "passed" for run in self.runs):
            raise ValueError("runner summary passed_count does not match runs")
        return self


__all__ = [
    "ArtifactIdentity",
    "PreflightIdentity",
    "PublicReplayIdentity",
    "PublicTraceIdentity",
    "RunSummary",
    "RunnerConfig",
    "RunnerSummary",
    "SealIdentity",
    "VerificationIdentity",
]
