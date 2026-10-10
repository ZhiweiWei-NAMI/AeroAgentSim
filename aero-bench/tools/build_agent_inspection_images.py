#!/usr/bin/env python3
"""Build and push the digest-locked images for the formal Agent inspection run."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aero_bench.serialization import canonical_json_bytes  # noqa: E402


SOURCE_URI = "https://github.com/ZhiweiWei-NAMI/AERO_BENCH"
IMAGE_LOCK_SCHEMA = "aero-bench.runtime-image-lock/v1"
CODEX_CLI_VERSION = "0.153.4"
CODEX_BINARY_SHA256 = (
    "56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da"
)
CODEX_CODE_MODE_HOST_SHA256 = (
    "3e85d67471825f73d02ff5f7e047ca1f6ca8caa3f59e4c6e8d9ca6ca7302cb45"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Component:
    key: str
    directory: str
    component_id: str
    version: str


COMPONENTS = (
    Component("harness", "harness", "aero-bench.harness", "0.4.0-harness.1"),
    Component("flight", "px4-gazebo", "px4.gazebo", "0.4.0-px4-gazebo.1"),
    Component("network", "ns3", "ns3.rpc", "0.4.0-ns3.1"),
    Component("traffic", "sumo", "sumo.traci", "0.4.0-sumo.2"),
    Component(
        "business",
        "inspection-business",
        "inspection.business",
        "0.4.0-inspection-business.1",
    ),
    Component(
        "verifier",
        "inspection-verifier",
        "inspection.verifier",
        "0.4.0-inspection-verifier.4",
    ),
    Component(
        "agent", "agent-bridge", "participant.agent", "0.4.0-agent-bridge.1"
    ),
    Component(
        "agent_driver", "codex-driver", "astra.driver", "0.4.0-astra-driver.1"
    ),
)
IMAGE_LOCK_KEYS = frozenset(component.key for component in COMPONENTS)
REFERENCE_COMPONENTS = (
    *COMPONENTS[:6],
    Component(
        "agent", "inspection-reference", "participant.agent",
        "0.4.0-inspection-reference.1",
    ),
)
PARTICIPANT_PROFILES = ("model_inspection", "reference_inspection")
ARRIVALS_PROFILE = "logistics_arrivals"
VERIFIER_COMPONENTS = {
    "inspection": COMPONENTS[5],
    "inspection_sumo": Component(
        "verifier", "sumo/restriction-verifier", "inspection.verifier",
        "0.4.0-inspection-sumo-verifier.1",
    ),
    "logistics_arrivals": Component(
        "verifier", "logistics-business/arrivals-verifier", "logistics.arrivals.verifier",
        "0.4.0-logistics-arrivals-verifier.1",
    ),
}
ARRIVALS_COMPONENTS = (
    COMPONENTS[0],
    Component("business", "logistics-business", "logistics.business", "0.4.0-logistics-business.2"),
    VERIFIER_COMPONENTS["logistics_arrivals"],
    Component("agent", "logistics-business/clock-agent", "participant.agent", "0.4.0-logistics-clock.1"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object, *, immutable: bool = False) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")
    if immutable:
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)


def _freeze_source_snapshot(source: Path) -> None:
    """Keep captured build inputs readable but not editable."""
    for path in source.rglob("*"):
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    source.chmod(stat.S_IMODE(source.stat().st_mode) & ~0o222)


def _capture_build_context(source: Path, destination: Path) -> None:
    """Archive the original source modes before the diagnostic copy is frozen."""

    def metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.uid = member.gid = 0
        member.uname = member.gname = ""
        return member

    with destination.open("xb") as stream:
        with tarfile.open(
            fileobj=stream, mode="w:gz", format=tarfile.GNU_FORMAT
        ) as archive:
            for path in sorted(source.rglob("*")):
                archive.add(
                    path,
                    arcname=path.relative_to(source).as_posix(),
                    recursive=False,
                    filter=metadata,
                )
    destination.chmod(stat.S_IMODE(destination.stat().st_mode) & ~0o222)


def _source_inventory(source: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for base in ("aero_bench", "containers"):
        for path in sorted((source / base).rglob("*")):
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or path.suffix in {".pyc", ".pyo"}
            ):
                continue
            records.append(
                {
                    "path": path.relative_to(source).as_posix(),
                    "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return records


def _build_context_inventory(source: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(source).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(source.rglob("*"))
        if path.is_file()
    ]


def _source_revision(records: list[dict[str, object]]) -> str:
    return hashlib.sha256(canonical_json_bytes(records)).hexdigest()


def _copy_managed_source(source: Path) -> None:
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for directory in ("aero_bench", "containers"):
        shutil.copytree(ROOT / directory, source / directory, ignore=ignored)
    dockerignore = ROOT / ".dockerignore"
    if dockerignore.is_file():
        shutil.copyfile(dockerignore, source / ".dockerignore")


def _stage_codex_binaries(source: Path, codex_binary: Path) -> dict[str, object]:
    codex_binary = codex_binary.expanduser().resolve(strict=True)
    code_mode_host = codex_binary.with_name("codex-code-mode-host")
    if not codex_binary.is_file() or not os.access(codex_binary, os.X_OK):
        raise ValueError("--codex-binary must name an executable regular file")
    if not code_mode_host.is_file() or not os.access(code_mode_host, os.X_OK):
        raise ValueError("codex-code-mode-host must be executable and adjacent to Codex")

    completed = subprocess.run(
        [str(codex_binary), "--version"],
        check=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
    )
    expected_version_output = f"codex-cli {CODEX_CLI_VERSION}"
    if completed.stdout.strip() != expected_version_output:
        raise ValueError(
            f"Codex version must be {expected_version_output!r}, got "
            f"{completed.stdout.strip()!r}"
        )

    inputs = (
        ("codex", codex_binary, CODEX_BINARY_SHA256),
        ("codex-code-mode-host", code_mode_host, CODEX_CODE_MODE_HOST_SHA256),
    )
    vendor = source / "containers/codex-driver/vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict[str, object]] = {}
    for name, source_path, expected_digest in inputs:
        actual_digest = _sha256(source_path)
        if actual_digest != expected_digest:
            raise ValueError(
                f"{name} SHA-256 differs from the pinned Codex {CODEX_CLI_VERSION} "
                f"binary: expected {expected_digest}, got {actual_digest}"
            )
        destination = vendor / name
        if destination.exists():
            raise ValueError(f"source snapshot already contains {destination.relative_to(source)}")
        shutil.copyfile(source_path, destination)
        destination.chmod(0o555)
        records[name] = {
            "path": destination.relative_to(source).as_posix(),
            "sha256": actual_digest,
            "size_bytes": destination.stat().st_size,
        }
    return {"cli_version": CODEX_CLI_VERSION, "binaries": records}


def _inspect(reference: str, docker_binary: str) -> dict[str, Any]:
    completed = subprocess.run(
        [docker_binary, "image", "inspect", reference],
        check=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError(f"Docker returned an invalid image inspection for {reference}")
    return value[0]


def _expected_labels(component: Component, revision: str) -> dict[str, str]:
    return {
        "io.aero-bench.component": component.component_id,
        "io.aero-bench.implementation-kind": "production",
        "org.opencontainers.image.revision": revision,
        "org.opencontainers.image.source": SOURCE_URI,
        "org.opencontainers.image.version": component.version,
    }


def _validate_identity(
    inspected: dict[str, Any], component: Component, revision: str
) -> str:
    image_id = inspected.get("Id")
    if (
        not isinstance(image_id, str)
        or not image_id.startswith("sha256:")
        or _SHA256.fullmatch(image_id.removeprefix("sha256:")) is None
    ):
        raise ValueError(f"{component.key} Docker image ID is invalid")
    config = inspected.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        raise ValueError(f"{component.key} image has no OCI labels")
    expected = _expected_labels(component, revision)
    drift = {
        name: {"expected": expected_value, "actual": labels.get(name)}
        for name, expected_value in expected.items()
        if labels.get(name) != expected_value
    }
    if drift:
        raise ValueError(f"{component.key} image identity differs from build inputs: {drift}")
    return image_id


def _exact_repository_digest(inspected: dict[str, Any], repository: str) -> str:
    values = inspected.get("RepoDigests")
    prefix = repository + "@sha256:"
    matches = (
        []
        if not isinstance(values, list)
        else sorted(
            {
                value
                for value in values
                if isinstance(value, str)
                and value.startswith(prefix)
                and _SHA256.fullmatch(value[len(prefix) :]) is not None
            }
        )
    )
    if len(matches) != 1:
        raise ValueError(
            f"Docker did not report exactly one digest for pushed repository {repository}"
        )
    return matches[0]


def _run_logged(
    command: list[str], log_path: Path, *, build_context: Path | None = None
) -> None:
    try:
        with log_path.open("xb") as log:
            if build_context is None:
                subprocess.run(
                    command, check=True, stdout=log, stderr=subprocess.STDOUT
                )
            else:
                with build_context.open("rb") as stream:
                    subprocess.run(
                        command,
                        check=True,
                        stdin=stream,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        env={**os.environ, "DOCKER_BUILDKIT": "1"},
                    )
    finally:
        if log_path.exists():
            log_path.chmod(stat.S_IMODE(log_path.stat().st_mode) & ~0o222)


def _registry_prefix(value: str) -> str:
    result = value.rstrip("/")
    if (
        not result
        or "://" in result
        or "@" in result
        or any(character.isspace() for character in result)
    ):
        raise ValueError("--registry must be a Docker repository prefix")
    return result


def _validate_digest_bindings(revision: str, images: dict[str, str]) -> None:
    if _SHA256.fullmatch(revision) is None:
        raise ValueError("runtime source revision must be a content SHA-256")
    for key, reference in images.items():
        repository, separator, digest = reference.rpartition("@sha256:")
        if (
            not repository
            or separator != "@sha256:"
            or _SHA256.fullmatch(digest) is None
            or repository.startswith("registry.invalid/")
        ):
            raise ValueError(f"{key} image must be a non-placeholder repository digest")


def _image_lock_payload(
    *, revision: str, images: dict[str, str], codex: dict[str, object]
) -> dict[str, object]:
    if set(images) != IMAGE_LOCK_KEYS:
        raise ValueError("formal image lock must bind exactly all runtime image keys")
    _validate_digest_bindings(revision, images)
    return {
        "schema_version": IMAGE_LOCK_SCHEMA,
        "runtime_source_revision": revision,
        "images": dict(sorted(images.items())),
        "codex_binaries": codex,
    }


def _reference_image_lock_payload(
    *, revision: str, images: dict[str, str]
) -> dict[str, object]:
    required = frozenset(component.key for component in REFERENCE_COMPONENTS)
    if set(images) != required:
        raise ValueError("reference image lock must bind exactly its seven runtime roles")
    _validate_digest_bindings(revision, images)
    return {
        "schema_version": "aero-bench.inspection-reference-image-lock/v1",
        "runtime_source_revision": revision,
        "participant_profile": "reference_inspection",
        "images": dict(sorted(images.items())),
        "agent": {
            "source_uri": SOURCE_URI,
            "source_revision": revision,
            "version": REFERENCE_COMPONENTS[-1].version,
        },
    }


def build_images(
    output: Path,
    *,
    codex_binary: Path | None = None,
    participant_profile: str = "model_inspection",
    verifier_profile: str = "inspection",
    registry: str = "localhost:5000/aero-bench",
    docker_binary: str = "docker",
) -> Path:
    if participant_profile not in (*PARTICIPANT_PROFILES, ARRIVALS_PROFILE):
        raise ValueError("participant profile must be explicitly supported")
    if verifier_profile not in VERIFIER_COMPONENTS:
        raise ValueError("verifier profile must be explicitly supported")
    arrivals = participant_profile == "logistics_arrivals"
    if arrivals != (verifier_profile == "logistics_arrivals"):
        raise ValueError("logistics_arrivals requires its separate arrivals verifier profile")
    reference = participant_profile != "model_inspection"
    if not reference and codex_binary is None:
        raise ValueError("model_inspection requires --codex-binary")
    if reference and codex_binary is not None:
        raise ValueError("reference_inspection must not stage a model CLI")
    components = ARRIVALS_COMPONENTS if arrivals else REFERENCE_COMPONENTS if reference else COMPONENTS
    components = tuple(
        VERIFIER_COMPONENTS[verifier_profile] if item.key == "verifier" else item
        for item in components
    )
    registry = _registry_prefix(registry)
    output = output.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    source = output / "source"
    source.mkdir(mode=0o700)
    context = output / "build-context.tar.gz"
    built: list[dict[str, object]] = []
    result: dict[str, object] = {
        "status": "incomplete",
        "registry": registry,
        "built": built,
        "pushed": False,
        "simulator_executed": False,
        "participant_profile": participant_profile,
        "verifier_profile": verifier_profile,
    }
    try:
        _copy_managed_source(source)
        managed_files = _source_inventory(source)
        revision = _source_revision(managed_files)
        result["source_revision"] = revision
        codex = None if reference else _stage_codex_binaries(source, codex_binary)
        for component in components:
            dockerfile = source / f"containers/{component.directory}/Dockerfile"
            if not dockerfile.is_file():
                raise FileNotFoundError(f"missing formal image Dockerfile: {dockerfile}")
        all_files = _build_context_inventory(source)
        dockerignore_record = next(
            (record for record in all_files if record["path"] == ".dockerignore"),
            None,
        )
        try:
            _capture_build_context(source, context)
        finally:
            _freeze_source_snapshot(source)
        _write_json(
            output / "build-inputs.json",
            {
                "schema_version": "aero-bench.formal-image-build-inputs/v1",
                "source_revision_kind": "content_closure_sha256",
                "source_revision": revision,
                "managed_source_files": managed_files,
                "files": all_files,
                "dockerignore": dockerignore_record,
                "codex_binaries": codex,
                "build_context": {
                    "path": context.name,
                    "sha256": _sha256(context),
                    "permissions": "original_source_modes_before_write_protection",
                },
            },
            immutable=True,
        )
        print(f"Build output: {output}\nSource revision: {revision}", flush=True)

        images: dict[str, str] = {}
        for component in components:
            repository = f"{registry}/{component.key}"
            tag = f"{repository}:source-{revision}"
            build_command = [
                docker_binary,
                "build",
                "--progress=plain",
                "--tag",
                tag,
                "--build-arg",
                f"AERO_BENCH_REVISION={revision}",
                "--file",
                f"containers/{component.directory}/Dockerfile",
                "-",
            ]
            print(f"Building {component.key}", flush=True)
            _run_logged(
                build_command,
                output / f"{component.key}.build.log",
                build_context=context,
            )
            before_push = _inspect(tag, docker_binary)
            image_id = _validate_identity(before_push, component, revision)

            print(f"Pushing {component.key}", flush=True)
            _run_logged(
                [docker_binary, "push", tag], output / f"{component.key}.push.log"
            )
            after_push = _inspect(tag, docker_binary)
            if _validate_identity(after_push, component, revision) != image_id:
                raise ValueError(f"{component.key} image ID changed while pushing")
            repository_digest = _exact_repository_digest(after_push, repository)
            images[component.key] = repository_digest
            built.append(
                {
                    "component": component.key,
                    "component_id": component.component_id,
                    "version": component.version,
                    "tag": tag,
                    "image_id": image_id,
                    "repository_digest": repository_digest,
                }
            )
            print(f"Pushed {component.key}: {repository_digest}", flush=True)

        lock = output / "images-lock.json"
        _write_json(
            lock,
            (
                _arrivals_image_lock_payload(revision=revision, images=images)
                if arrivals else _reference_image_lock_payload(revision=revision, images=images)
                if reference
                else _image_lock_payload(revision=revision, images=images, codex=codex)
            ),
            immutable=True,
        )
        result.update(
            {
                "status": "formal_images_pushed",
                "pushed": True,
                "images_lock": str(lock),
                "exact_repo_digests": dict(sorted(images.items())),
            }
        )
        return lock
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        _write_json(output / "build-result.json", result, immutable=True)


def _arrivals_image_lock_payload(*, revision: str, images: dict[str, str]) -> dict[str, object]:
    if set(images) != {item.key for item in ARRIVALS_COMPONENTS}:
        raise ValueError("arrivals lock requires exactly its four native workload images")
    _validate_digest_bindings(revision, images)
    return {"schema_version": "aero-bench.logistics-arrivals-image-lock/v1",
            "runtime_source_revision": revision, "participant_profile": "logistics_arrivals",
            "images": dict(sorted(images.items()))}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", required=True, type=Path, help="fresh build directory")
    result.add_argument(
        "--codex-binary",
        type=Path,
        help="native Codex 0.153.4 binary; codex-code-mode-host must be adjacent",
    )
    result.add_argument(
        "--participant-profile", choices=(*PARTICIPANT_PROFILES, ARRIVALS_PROFILE),
        default="model_inspection",
    )
    result.add_argument(
        "--verifier-profile", choices=tuple(VERIFIER_COMPONENTS), default="inspection",
        help="explicit inspection verifier, optionally with the sealed SUMO restriction gate",
    )
    result.add_argument(
        "--registry",
        default="localhost:5000/aero-bench",
        help="local Docker repository prefix",
    )
    result.add_argument("--docker-binary", default="docker")
    return result


def main() -> None:
    args = parser().parse_args()
    print(
        build_images(
            args.output,
            codex_binary=args.codex_binary,
            participant_profile=args.participant_profile,
            verifier_profile=args.verifier_profile,
            registry=args.registry,
            docker_binary=args.docker_binary,
        )
    )


if __name__ == "__main__":
    main()
