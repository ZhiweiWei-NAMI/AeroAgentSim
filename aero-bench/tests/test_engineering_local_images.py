from __future__ import annotations

from tests.support import fixture_provider_registry

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from aero_bench.config.models import RuntimeImage
from aero_bench.executor import DockerExecutor
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.tasks.registry import builtin_task_package_resolvers
from tests.support import build_bundle, digest, promote_bundle_to_formal, resolve_bundle


KEEPER_IMAGE = "registry.invalid/keeper@sha256:" + "f" * 64


def _executor() -> DockerExecutor:
    return DockerExecutor(
        docker_binary="sh",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        volume_keeper_mount_path="/run/aero-held",
        input_volume_size_bytes=32 * 1024 * 1024,
        artifact_volume_size_bytes=4_194_304,
        scratch_size_bytes=1_048_576,
        pids_limit=64,
        workload_uid=65532,
        workload_gid=65532,
        provider_bind_host="0.0.0.0",
        gateway_bind_host="0.0.0.0",
        readiness_timeout_seconds=30,
        volume_keeper_image=KEEPER_IMAGE,
        volume_keeper_command=("/bin/sleep", "infinity"),
        volume_keeper_cpu_millicores=50,
        volume_keeper_memory_mib=64,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    )


def _engineering_bundle(root: Path):
    bundle = build_bundle(root)
    promote_bundle_to_formal(bundle)

    environment_path = bundle.root / "environments/environment.yaml"
    agent_path = bundle.root / "agents/reference.yaml"
    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    runtimes = [
        environment["harness"]["runtime"],
        *(provider["workload"]["runtime"] for provider in environment["providers"]),
        agent["workload"]["runtime"],
        task["verifier"]["workload"]["runtime"],
    ]
    for index, runtime in enumerate(runtimes, start=1):
        runtime["image"] = "sha256:" + f"{index:064x}"

    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    environment_path.write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False), encoding="utf-8")

    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["execution_scope"] = "executor_validation"
    case = suite["cases"][0]
    case["task"]["sha256"] = digest(bundle.task)
    case["environment"]["sha256"] = digest(environment_path)
    case["agents"][0]["sha256"] = digest(agent_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    return bundle


def _completed(
    arguments: tuple[str, ...],
    *,
    stdout: str = "",
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(arguments, returncode, stdout, stderr)


def test_local_image_id_is_explicit_and_scope_bound(tmp_path: Path) -> None:
    reference = RuntimeImage(image="sha256:" + "1" * 64, command=("run",))
    assert reference.is_local_id
    assert reference.immutable_digest == "1" * 64

    bundle = _engineering_bundle(tmp_path / "bundle")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    assert run.execution_scope == "executor_validation"
    assert all(
        workload.runtime.is_local_id
        for workload in (
            run.environment.harness,
            *(provider.workload for provider in run.environment.providers),
            *(agent.workload for agent in run.agents),
            run.task.verifier.workload,
        )
    )
    provider = run.environment.providers[0]
    manifest = ProviderManifest(
        provider_id=provider.provider_id,
        adapter=provider.adapter,
        implementation=provider.workload.implementation,
        runtime_image=provider.workload.runtime.image,
        config_digest=provider.config.file.sha256,
        capabilities=provider.capabilities,
        protocol_schema=provider.protocol_schema,
        artifact_requirements=provider.artifact_requirements,
    )
    assert manifest.runtime_image == provider.workload.runtime.image

    with pytest.raises(ValueError, match="docker_reference executor"):
        resolve_bundle(bundle.suite, executor_kind="kubernetes_cluster")

    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["execution_scope"] = "formal_benchmark"
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="allowed only for executor_validation"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_engineering_preflight_binds_exact_ids_and_production_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _engineering_bundle(tmp_path / "bundle")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    executor = _executor()
    plan = executor.materialize(run, bundle_root=bundle.root)
    workloads = (*plan.runtime_workloads, plan.verifier_workload)
    identities = {
        workload.workload.runtime.image: workload.workload.implementation
        for workload in workloads
    }
    calls: list[tuple[str, ...]] = []

    def fake_run(arguments, *, check=True, timeout_seconds=None):
        calls.append(arguments)
        if arguments[0] == "version":
            return _completed(arguments, stdout="26.0.0\n")
        if arguments[0] == "info":
            return _completed(
                arguments, stdout=json.dumps(["name=seccomp,profile=default"])
            )
        if arguments[:2] != ("image", "inspect"):
            raise AssertionError(f"unexpected Docker command: {arguments}")
        template = arguments[3]
        image = arguments[-1]
        if template == "{{json .Id}}":
            assert image in identities
            return _completed(arguments, stdout=json.dumps(image))
        if template == "{{json .RepoDigests}}":
            assert image == KEEPER_IMAGE
            return _completed(arguments, stdout=json.dumps([image]))
        if template == "{{json .Config.Labels}}":
            identity = identities[image]
            return _completed(
                arguments,
                stdout=json.dumps(
                    {
                        "io.aero-bench.component": identity.component_id,
                        "io.aero-bench.implementation-kind": "production",
                        "org.opencontainers.image.revision": identity.source_revision,
                        "org.opencontainers.image.source": identity.source_uri,
                        "org.opencontainers.image.version": identity.version,
                    }
                ),
            )
        raise AssertionError(f"unexpected image inspection: {arguments}")

    monkeypatch.setattr(executor, "_run", fake_run)
    report = executor.preflight(plan)

    assert report.ready
    assert report.blockers == ()
    id_inspections = {
        arguments[-1]
        for arguments in calls
        if arguments[:4] == ("image", "inspect", "--format", "{{json .Id}}")
    }
    assert id_inspections == set(identities)


def test_engineering_preflight_rejects_inspected_id_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _engineering_bundle(tmp_path / "bundle")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    executor = _executor()
    plan = executor.materialize(run, bundle_root=bundle.root)
    mismatched_image = plan.runtime_workloads[0].workload.runtime.image

    def fake_run(arguments, *, check=True, timeout_seconds=None):
        if arguments[0] == "version":
            return _completed(arguments, stdout="26.0.0\n")
        if arguments[0] == "info":
            return _completed(
                arguments, stdout=json.dumps(["name=seccomp,profile=default"])
            )
        if arguments[:2] != ("image", "inspect"):
            raise AssertionError(f"unexpected Docker command: {arguments}")
        template = arguments[3]
        image = arguments[-1]
        if template == "{{json .Id}}":
            inspected_id = "sha256:" + "e" * 64 if image == mismatched_image else image
            return _completed(arguments, stdout=json.dumps(inspected_id))
        if template == "{{json .RepoDigests}}":
            return _completed(arguments, stdout=json.dumps([image]))
        if template == "{{json .Config.Labels}}":
            workload = next(
                item
                for item in (*plan.runtime_workloads, plan.verifier_workload)
                if item.workload.runtime.image == image
            )
            identity = workload.workload.implementation
            return _completed(
                arguments,
                stdout=json.dumps(
                    {
                        "io.aero-bench.component": identity.component_id,
                        "io.aero-bench.implementation-kind": "production",
                        "org.opencontainers.image.revision": identity.source_revision,
                        "org.opencontainers.image.source": identity.source_uri,
                        "org.opencontainers.image.version": identity.version,
                    }
                ),
            )
        raise AssertionError(f"unexpected image inspection: {arguments}")

    monkeypatch.setattr(executor, "_run", fake_run)
    report = executor.preflight(plan)

    assert not report.ready
    assert {blocker.code for blocker in report.blockers} == {
        "docker.image.id-mismatch"
    }
