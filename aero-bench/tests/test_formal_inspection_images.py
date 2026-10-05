from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from aero_bench.serialization import canonical_json_bytes
from tools import build_agent_inspection_images as builder


def _inspection(
    component: builder.Component,
    revision: str,
    *,
    repository: str = "localhost:5000/aero-bench/test",
) -> dict[str, object]:
    return {
        "Id": "sha256:" + "a" * 64,
        "RepoDigests": [repository + "@sha256:" + "b" * 64],
        "Config": {"Labels": builder._expected_labels(component, revision)},
    }


def test_source_revision_matches_formal_managed_content_closure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "aero_bench").mkdir(parents=True)
    (source / "containers/example/__pycache__").mkdir(parents=True)
    (source / "aero_bench/runtime.py").write_bytes(b"runtime\n")
    (source / "containers/example/Dockerfile").write_bytes(b"FROM scratch\n")
    (source / "containers/example/__pycache__/ignored.pyc").write_bytes(b"ignored")
    (source / ".dockerignore").write_bytes(b"__pycache__\n")

    records = builder._source_inventory(source)

    assert [record["path"] for record in records] == [
        "aero_bench/runtime.py",
        "containers/example/Dockerfile",
    ]
    assert builder._source_revision(records) == hashlib.sha256(
        canonical_json_bytes(records)
    ).hexdigest()
    assert [record["path"] for record in builder._build_context_inventory(source)] == [
        ".dockerignore",
        "aero_bench/runtime.py",
        "containers/example/Dockerfile",
        "containers/example/__pycache__/ignored.pyc",
    ]


def test_codex_binaries_are_versioned_hashed_and_staged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host = tmp_path / "host"
    host.mkdir()
    codex = host / "codex"
    code_mode_host = host / "codex-code-mode-host"
    codex.write_bytes(b"codex-native")
    code_mode_host.write_bytes(b"code-mode-native")
    codex.chmod(0o755)
    code_mode_host.chmod(0o755)
    monkeypatch.setattr(builder, "CODEX_BINARY_SHA256", hashlib.sha256(codex.read_bytes()).hexdigest())
    monkeypatch.setattr(
        builder,
        "CODEX_CODE_MODE_HOST_SHA256",
        hashlib.sha256(code_mode_host.read_bytes()).hexdigest(),
    )

    def fake_run(arguments, **kwargs):
        assert arguments == [str(codex), "--version"]
        return subprocess.CompletedProcess(arguments, 0, "codex-cli 0.153.4\n", "")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    source = tmp_path / "snapshot"
    source.mkdir()

    record = builder._stage_codex_binaries(source, codex)

    assert record["cli_version"] == "0.153.4"
    assert set(record["binaries"]) == {"codex", "codex-code-mode-host"}
    vendor = source / "containers/codex-driver/vendor"
    assert (vendor / "codex").read_bytes() == b"codex-native"
    assert (vendor / "codex-code-mode-host").read_bytes() == b"code-mode-native"
    assert (vendor / "codex").stat().st_mode & 0o777 == 0o555


def test_build_context_preserves_modes_before_snapshot_is_frozen(tmp_path: Path) -> None:
    source = tmp_path / "source"
    binary = source / "containers/codex-driver/vendor/codex"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native")
    binary.chmod(0o555)
    context = tmp_path / "context.tar.gz"

    builder._capture_build_context(source, context)
    builder._freeze_source_snapshot(source)

    with tarfile.open(context, "r:gz") as archive:
        member = archive.getmember("containers/codex-driver/vendor/codex")
    assert member.mode & 0o777 == 0o555
    assert binary.stat().st_mode & 0o222 == 0
    assert source.stat().st_mode & 0o222 == 0


def test_image_identity_requires_every_formal_runtime_label() -> None:
    component = builder.COMPONENTS[0]
    revision = "c" * 64
    inspected = _inspection(component, revision)
    assert builder._validate_identity(inspected, component, revision) == "sha256:" + "a" * 64

    inspected["Config"]["Labels"]["org.opencontainers.image.version"] = "wrong"
    with pytest.raises(ValueError, match="identity differs"):
        builder._validate_identity(inspected, component, revision)


def test_exact_repo_digest_and_lock_cover_exact_formal_roles() -> None:
    component = builder.COMPONENTS[0]
    revision = "d" * 64
    repository = "localhost:5000/aero-bench/harness"
    inspected = _inspection(component, revision, repository=repository)
    digest = builder._exact_repository_digest(inspected, repository)
    images = {
        component.key: f"localhost:5000/aero-bench/{component.key}@sha256:"
        + f"{index:064x}"
        for index, component in enumerate(builder.COMPONENTS, start=1)
    }

    lock = builder._image_lock_payload(
        revision=revision,
        images=images,
        codex={"cli_version": "0.153.4", "binaries": {}},
    )

    assert digest == repository + "@sha256:" + "b" * 64
    assert lock["schema_version"] == "aero-bench.runtime-image-lock/v1"
    assert set(lock["images"]) == {
        "harness",
        "flight",
        "network",
        "traffic",
        "business",
        "verifier",
        "agent",
        "agent_driver",
    }
    assert all("@sha256:" in value for value in lock["images"].values())

    images["agent_driver"] = "registry.invalid/aero-bench/agent_driver@sha256:" + "f" * 64
    with pytest.raises(ValueError, match="non-placeholder repository digest"):
        builder._image_lock_payload(
            revision=revision,
            images=images,
            codex={"cli_version": "0.153.4", "binaries": {}},
        )


def test_build_result_json_is_canonical_and_immutable(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    builder._write_json(output, {"z": 1, "a": 2}, immutable=True)
    assert output.read_bytes() == canonical_json_bytes({"z": 1, "a": 2}) + b"\n"
    assert output.stat().st_mode & 0o222 == 0


def test_participant_profiles_do_not_silently_substitute_model_or_reference(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires --codex-binary"):
        builder.build_images(tmp_path / "no-model-binary")
    with pytest.raises(ValueError, match="must not stage a model CLI"):
        builder.build_images(
            tmp_path / "wrong-reference", participant_profile="reference_inspection",
            codex_binary=tmp_path / "unused",
        )
    with pytest.raises(ValueError, match="explicitly supported"):
        builder.build_images(tmp_path / "unknown-profile", participant_profile="unknown")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("participant_profile", builder.PARTICIPANT_PROFILES)
def test_build_orchestration_pushes_all_images_and_writes_digest_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, participant_profile: str
) -> None:
    commands: list[list[str]] = []
    reference = participant_profile == "reference_inspection"
    components = builder.REFERENCE_COMPONENTS if reference else builder.COMPONENTS

    def fake_copy(source: Path) -> None:
        (source / "aero_bench").mkdir()
        (source / "aero_bench/runtime.py").write_bytes(b"runtime\n")
        for component in components:
            directory = source / "containers" / component.directory
            directory.mkdir(parents=True)
            (directory / "Dockerfile").write_bytes(b"FROM scratch\n")

    def fake_stage(source: Path, _codex_binary: Path) -> dict[str, object]:
        assert not reference, "reference profile must not stage a model CLI"
        vendor = source / "containers/codex-driver/vendor"
        vendor.mkdir()
        (vendor / "codex").write_bytes(b"codex")
        (vendor / "codex-code-mode-host").write_bytes(b"host")
        return {"cli_version": "0.153.4", "binaries": {}}

    def fake_run_logged(
        command: list[str], log_path: Path, *, build_context: Path | None = None
    ) -> None:
        commands.append(command)
        log_path.write_text("unit-only\n", encoding="utf-8")
        if command[1] == "build":
            assert build_context is not None
        else:
            assert build_context is None

    def fake_inspect(reference: str, docker_binary: str) -> dict[str, object]:
        assert docker_binary == "never-docker"
        repository, revision = reference.rsplit(":source-", 1)
        key = repository.rsplit("/", 1)[1]
        component = next(item for item in components if item.key == key)
        return _inspection(component, revision, repository=repository)

    monkeypatch.setattr(builder, "_copy_managed_source", fake_copy)
    monkeypatch.setattr(builder, "_stage_codex_binaries", fake_stage)
    monkeypatch.setattr(builder, "_run_logged", fake_run_logged)
    monkeypatch.setattr(builder, "_inspect", fake_inspect)
    output = tmp_path / "formal-images"

    lock_path = builder.build_images(
        output,
        codex_binary=None if reference else tmp_path / "unused-codex",
        participant_profile=participant_profile,
        docker_binary="never-docker",
    )

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    result = json.loads((output / "build-result.json").read_text(encoding="utf-8"))
    assert len(commands) == 2 * len(components)
    assert [command[1] for command in commands] == [
        operation for _ in components for operation in ("build", "push")
    ]
    assert set(lock["images"]) == {component.key for component in components}
    assert result["participant_profile"] == participant_profile
    if reference:
        assert lock["participant_profile"] == participant_profile
        assert "codex_binaries" not in lock
        assert lock["agent"]["version"] == builder.REFERENCE_COMPONENTS[-1].version
        assert not (output / "source/containers/codex-driver").exists()
    else:
        assert lock["codex_binaries"]["cli_version"] == builder.CODEX_CLI_VERSION
    assert result["status"] == "formal_images_pushed"
    assert result["pushed"] is True
    assert lock_path.stat().st_mode & 0o222 == 0
    assert (output / "source").stat().st_mode & 0o222 == 0
