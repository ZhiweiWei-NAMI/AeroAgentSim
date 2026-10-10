from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from aero_bench.config.loader import sha256_file
from aero_bench.config.models import (
    ArtifactRequirement,
    Identifier,
    Sha256,
    StrictModel,
)
from aero_bench.serialization import canonical_json_bytes


def _relative_artifact_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or value.strip() != value
        or str(path) != value
        or "\\" in value
    ):
        raise ValueError("artifact path must be normalized and relative")
    return value


class ArtifactRecord(StrictModel):
    artifact_id: Identifier
    artifact_type: Identifier
    producer_id: Identifier
    visibility: Literal["public", "private"]
    relative_path: Annotated[str, Field(min_length=1)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]

    _path_validator = field_validator("relative_path")(_relative_artifact_path)

    @field_validator("sha256")
    @classmethod
    def non_placeholder_digest(cls, value: str) -> str:
        if value == "0" * 64:
            raise ValueError("artifact sha256 cannot be a placeholder")
        return value


class SealManifest(StrictModel):
    schema_version: Literal["aero-bench.seal/v2"]
    run_id: Sha256
    attempt_id: Annotated[Identifier, Field(max_length=128)]
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    artifacts: tuple[ArtifactRecord, ...] = Field(min_length=1)
    event_chain_root: Sha256
    manifest_digest: Sha256

    @model_validator(mode="after")
    def unique_artifacts(self) -> "SealManifest":
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        relative_paths = [artifact.relative_path for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact_id values must be unique")
        if len(relative_paths) != len(set(relative_paths)):
            raise ValueError("artifact relative_path values must be unique")
        if self.event_chain_root == "0" * 64:
            raise ValueError("event_chain_root cannot be a placeholder")
        body = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "execution_scope": self.execution_scope,
            "artifacts": [
                artifact.model_dump(mode="json") for artifact in self.artifacts
            ],
            "event_chain_root": self.event_chain_root,
        }
        actual_manifest_digest = hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.manifest_digest != actual_manifest_digest:
            raise ValueError("manifest_digest does not match the sealed manifest")
        return self


class EvidenceReference(StrictModel):
    artifact_id: Identifier
    selector: str | None = None


def records_from_requirements(
    *,
    root: Path,
    requirements: tuple[ArtifactRequirement, ...],
) -> tuple[ArtifactRecord, ...]:
    """Hash an exact declared artifact inventory after it has been collected."""

    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("artifact root is unavailable") from error
    if not resolved_root.is_dir():
        raise ValueError("artifact root is not a directory")

    expected_paths = {requirement.relative_path for requirement in requirements}
    artifact_ids = [requirement.artifact_id for requirement in requirements]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("artifact requirements repeat an artifact_id")
    if len(expected_paths) != len(requirements):
        raise ValueError("artifact requirements repeat a relative_path")

    actual_paths: set[str] = set()
    for candidate in resolved_root.rglob("*"):
        relative_path = candidate.relative_to(resolved_root).as_posix()
        if candidate.is_symlink():
            raise ValueError(f"artifact root contains a symbolic link: {relative_path}")
        if candidate.is_dir():
            if not any(
                expected_path.startswith(relative_path + "/")
                for expected_path in expected_paths
            ):
                raise ValueError(
                    f"artifact root contains an undeclared directory: {relative_path}"
                )
            continue
        if not candidate.is_file():
            raise ValueError(f"artifact root contains a special file: {relative_path}")
        actual_paths.add(relative_path)
    if actual_paths != expected_paths:
        raise ValueError(
            "artifact root file inventory does not match declared requirements: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"undeclared={sorted(actual_paths - expected_paths)}"
        )

    records: list[ArtifactRecord] = []
    for requirement in requirements:
        candidate = resolved_root / requirement.relative_path
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError(
                f"declared artifact is unavailable: {requirement.artifact_id}"
            ) from error
        if not resolved_candidate.is_relative_to(resolved_root):
            raise ValueError(
                f"declared artifact escapes root: {requirement.artifact_id}"
            )
        if resolved_candidate.is_symlink() or not resolved_candidate.is_file():
            raise ValueError(
                f"declared artifact is not a regular file: {requirement.artifact_id}"
            )
        size_bytes = resolved_candidate.stat().st_size
        if size_bytes > requirement.max_size_bytes:
            raise ValueError(
                "declared artifact exceeds max_size_bytes: "
                f"{requirement.artifact_id} ({size_bytes} > "
                f"{requirement.max_size_bytes})"
            )
        records.append(
            ArtifactRecord(
                artifact_id=requirement.artifact_id,
                artifact_type=requirement.artifact_type,
                producer_id=requirement.producer_id,
                visibility=requirement.visibility,
                relative_path=requirement.relative_path,
                sha256=sha256_file(resolved_candidate),
                size_bytes=size_bytes,
            )
        )
    return tuple(records)


def seal_manifest_size_upper_bound(
    *,
    run_id: str,
    execution_scope: Literal["executor_validation", "formal_benchmark"],
    requirements: tuple[ArtifactRequirement, ...],
) -> int:
    """Return the exact maximum stored manifest size for declared artifacts."""

    artifacts = [
        {
            "artifact_id": requirement.artifact_id,
            "artifact_type": requirement.artifact_type,
            "producer_id": requirement.producer_id,
            "visibility": requirement.visibility,
            "relative_path": requirement.relative_path,
            "sha256": "f" * 64,
            "size_bytes": requirement.max_size_bytes,
        }
        for requirement in requirements
    ]
    artifacts.sort(
        key=lambda artifact: (
            artifact["producer_id"],
            artifact["artifact_type"],
            artifact["artifact_id"],
        )
    )
    manifest = {
        "schema_version": "aero-bench.seal/v2",
        "run_id": run_id,
        "attempt_id": "a" * 128,
        "execution_scope": execution_scope,
        "artifacts": artifacts,
        "event_chain_root": "f" * 64,
        "manifest_digest": "f" * 64,
    }
    return len(canonical_json_bytes(manifest)) + 1


def seal_manifest(
    *,
    root: Path,
    run_id: str,
    attempt_id: str,
    execution_scope: Literal["executor_validation", "formal_benchmark"],
    event_chain_root: str,
    artifacts: tuple[ArtifactRecord, ...],
    manifest_name: str | None = None,
) -> SealManifest:
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError("seal root must be a directory")
    if manifest_name is not None and (
        PurePosixPath(manifest_name).name != manifest_name or not manifest_name
    ):
        raise ValueError("manifest_name must be a single normalized filename")

    expected_paths = {artifact.relative_path for artifact in artifacts}
    if manifest_name is not None:
        expected_paths.add(manifest_name)
    actual_paths: set[str] = set()
    for candidate in resolved_root.rglob("*"):
        relative_path = candidate.relative_to(resolved_root).as_posix()
        if candidate.is_symlink():
            raise ValueError(f"seal root contains a symbolic link: {relative_path}")
        if candidate.is_dir():
            if not any(
                expected_path.startswith(relative_path + "/")
                for expected_path in expected_paths
            ):
                raise ValueError(
                    f"seal root contains an undeclared directory: {relative_path}"
                )
            continue
        if not candidate.is_file():
            raise ValueError(f"seal root contains a special file: {relative_path}")
        actual_paths.add(relative_path)

    if actual_paths != expected_paths:
        raise ValueError(
            "seal root file inventory does not match declared artifacts: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"undeclared={sorted(actual_paths - expected_paths)}"
        )

    verified: list[ArtifactRecord] = []
    for artifact in artifacts:
        candidate = (resolved_root / artifact.relative_path).resolve(strict=True)
        if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
            raise ValueError(f"artifact is outside seal root: {artifact.relative_path}")
        actual_digest = sha256_file(candidate)
        if actual_digest != artifact.sha256:
            raise ValueError(f"artifact digest mismatch: {artifact.relative_path}")
        if candidate.stat().st_size != artifact.size_bytes:
            raise ValueError(f"artifact size mismatch: {artifact.relative_path}")
        verified.append(artifact)
    verified.sort(
        key=lambda artifact: (
            artifact.producer_id,
            artifact.artifact_type,
            artifact.artifact_id,
        )
    )
    body = {
        "schema_version": "aero-bench.seal/v2",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "execution_scope": execution_scope,
        "artifacts": [artifact.model_dump(mode="json") for artifact in verified],
        "event_chain_root": event_chain_root,
    }
    seal = SealManifest(
        **body,
        manifest_digest=hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )
    if manifest_name is not None:
        expected_manifest = canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
        if (resolved_root / manifest_name).read_bytes() != expected_manifest:
            raise ValueError("stored seal manifest does not match sealed artifacts")
    return seal
