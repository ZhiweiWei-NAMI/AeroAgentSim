from __future__ import annotations

from tests.support import fixture_provider_registry

import hashlib
import json
import os
import re
import subprocess
from types import SimpleNamespace

import pytest

import aero_bench.executor.docker as docker_module
from aero_bench.artifacts import (
    ArtifactRecord,
    seal_manifest,
    seal_manifest_size_upper_bound,
)
from aero_bench.config.models import ArtifactRequirement, NamedValue
from aero_bench.executor import (
    DockerExecutionHandle,
    DockerExecutor,
    DockerExecutorError,
)
from aero_bench.runtime import EventLedger, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from tests.support import build_bundle, promote_bundle_to_formal, resolve_bundle


IMAGE = "registry.invalid/keeper@sha256:" + "6" * 64


def executor(
    *,
    input_volume_size_bytes: int = 1_048_576,
    artifact_volume_size_bytes: int = 4_194_304,
    provider_bind_host: str = "0.0.0.0",
    gateway_bind_host: str = "0.0.0.0",
    readiness_timeout_seconds: int = 30,
) -> DockerExecutor:
    return DockerExecutor(
        docker_binary="docker",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        volume_keeper_mount_path="/run/aero-held",
        input_volume_size_bytes=input_volume_size_bytes,
        artifact_volume_size_bytes=artifact_volume_size_bytes,
        scratch_size_bytes=1_048_576,
        pids_limit=64,
        workload_uid=65532,
        workload_gid=65532,
        provider_bind_host=provider_bind_host,
        gateway_bind_host=gateway_bind_host,
        readiness_timeout_seconds=readiness_timeout_seconds,
        volume_keeper_image=IMAGE,
        volume_keeper_command=("/bin/sleep", "infinity"),
        volume_keeper_cpu_millicores=50,
        volume_keeper_memory_mib=64,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    )


def handle(run_id: str = "a" * 64) -> DockerExecutionHandle:
    return DockerExecutionHandle(
        run_id=run_id,
        attempt_id="attempt.test",
        agent_network="audit-agent",
        provider_network="audit-provider",
        egress_network="audit-egress",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
    )


def completed(
    arguments: tuple[str, ...],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=arguments,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _runtime_seal(tmp_path, run):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    evidence = runtime_root / "event.log"
    payload = b"runtime evidence\n"
    evidence.write_bytes(payload)
    artifact = ArtifactRecord(
        artifact_id="runtime.evidence",
        artifact_type="event.log",
        producer_id="harness",
        visibility="public",
        relative_path="event.log",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    return seal_manifest(
        root=runtime_root,
        run_id=run.run_id,
        attempt_id="attempt.test",
        execution_scope=run.execution_scope,
        event_chain_root="b" * 64,
        artifacts=(artifact,),
    )


def _verification_report(run, *, goals=None):
    goal = run.task.goals[0]
    return {
        "schema_version": "aero-bench.verification/v1",
        "run_id": run.run_id,
        "execution_scope": run.execution_scope,
        "status": "invalid",
        "goals": goals
        if goals is not None
        else [
            {
                "goal_id": goal.goal_id,
                "passed": False,
                "failure_class": "executor.validation",
                "metrics": [],
            }
        ],
        "coverage_complete": True,
    }


def _collection_setup(tmp_path, *, extra_output=False):
    bundle = build_bundle(tmp_path / "bundle")
    base_run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    run = base_run
    if extra_output:
        extra = ArtifactRequirement(
            artifact_id="verification.trace",
            artifact_type="public.trace",
            producer_id=run.task.verifier.verifier_id,
            visibility="public",
            relative_path="verifier/public-trace.json",
            max_size_bytes=1024,
            source_asset_id=None,
        )
        verifier = run.task.verifier.model_copy(
            update={"output_artifacts": (*run.task.verifier.output_artifacts, extra)}
        )
        task = run.task.model_copy(update={"verifier": verifier})
        payload = run.model_dump(mode="json", exclude={"run_id"})
        payload["task"] = task.model_dump(mode="json")
        payload["verification_outputs"] = [
            item.model_dump(mode="json") for item in (*run.verification_outputs, extra)
        ]
        run = run.model_copy(
            update={
                "task": task,
                "verification_outputs": (*run.verification_outputs, extra),
                "run_id": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
            }
        )
    ex = executor()
    plan = ex.materialize(base_run, bundle_root=bundle.root)
    if extra_output:
        plan = plan.model_copy(update={"run": run})
    run_handle = handle(run.run_id)
    run_handle.verifier_container = "verifier-container"
    run_handle.pre_verification_seal = _runtime_seal(tmp_path, run)
    return ex, plan, run_handle


def fake_docker(
    owner_token: str,
    calls: list[tuple[tuple[str, ...], dict[str, object]]],
    *,
    remove_error: bool = False,
):
    def run(
        arguments: tuple[str, ...],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((arguments, {"check": check, "timeout_seconds": timeout_seconds}))
        if len(arguments) >= 2 and arguments[1] == "inspect":
            if (
                "{{json .Config.Labels}}" in arguments
                or "{{json .Labels}}" in arguments
            ):
                return completed(
                    arguments,
                    stdout=json.dumps({"aero-bench/owner": owner_token}),
                )
            if "{{.State.Running}}" in arguments:
                return completed(arguments, stdout="true\n")
            resource = arguments[0]
            return completed(
                arguments,
                returncode=1,
                stderr=f"Error: No such {resource}: {arguments[-1]}",
            )
        if len(arguments) >= 2 and arguments[1] == "rm":
            if remove_error:
                return completed(arguments, returncode=1, stderr="daemon unavailable")
            return completed(arguments)
        if arguments[0] in {"create", "run", "volume", "network"}:
            if arguments[0] in {"volume", "network"} and len(arguments) > 1:
                if arguments[1] == "create":
                    return completed(arguments)
            return completed(arguments, stdout="created\n")
        return completed(arguments)

    return run


def test_collect_verification_outputs_returns_seal_and_validated_report(
    monkeypatch, tmp_path
) -> None:
    ex, plan, run_handle = _collection_setup(tmp_path)
    monkeypatch.setattr(ex, "_require_stopped", lambda _containers: None)

    def copy_outputs(*, destination_root, **_kwargs):
        report_path = destination_root / plan.run.verification_outputs[0].relative_path
        report_path.parent.mkdir(parents=True)
        report_path.write_bytes(
            canonical_json_bytes(_verification_report(plan.run)) + b"\n"
        )

    monkeypatch.setattr(ex, "_copy_declared_outputs", copy_outputs)
    output_root = tmp_path / "verification"

    result = ex.collect_verification_outputs(
        plan,
        run_handle,
        destination_root=output_root,
    )

    assert result.seal.artifacts[0].artifact_type == "verification.report"
    assert result.report.status == "invalid"
    assert (output_root / "verification-manifest.json").is_file()


def test_collect_verification_outputs_rejects_semantically_invalid_report_and_cleans(
    monkeypatch, tmp_path
) -> None:
    ex, plan, run_handle = _collection_setup(tmp_path)
    monkeypatch.setattr(ex, "_require_stopped", lambda _containers: None)

    def copy_outputs(*, destination_root, **_kwargs):
        report_path = destination_root / plan.run.verification_outputs[0].relative_path
        report_path.parent.mkdir(parents=True)
        invalid_report = _verification_report(plan.run, goals=[])
        report_path.write_bytes(canonical_json_bytes(invalid_report) + b"\n")

    monkeypatch.setattr(ex, "_copy_declared_outputs", copy_outputs)
    output_root = tmp_path / "verification"

    with pytest.raises(ValueError, match="does not cover every"):
        ex.collect_verification_outputs(
            plan,
            run_handle,
            destination_root=output_root,
        )

    assert not output_root.exists()


def test_collect_verification_outputs_accepts_extra_declared_output(
    monkeypatch, tmp_path
) -> None:
    ex, plan, run_handle = _collection_setup(tmp_path, extra_output=True)
    monkeypatch.setattr(ex, "_require_canonical_plan", lambda _plan: None)
    monkeypatch.setattr(ex, "_require_stopped", lambda _containers: None)

    def copy_outputs(*, destination_root, **_kwargs):
        report_path = destination_root / plan.run.verification_outputs[0].relative_path
        report_path.parent.mkdir(parents=True)
        report_path.write_bytes(
            canonical_json_bytes(_verification_report(plan.run)) + b"\n"
        )
        (destination_root / "verifier/public-trace.json").write_bytes(
            b'{"trace":true}\n'
        )

    monkeypatch.setattr(ex, "_copy_declared_outputs", copy_outputs)
    output_root = tmp_path / "verification"

    result = ex.collect_verification_outputs(
        plan,
        run_handle,
        destination_root=output_root,
    )

    assert result.report.status == "invalid"
    assert {artifact.artifact_id for artifact in result.seal.artifacts} == {
        "artifact.verification.report",
        "verification.trace",
    }


def test_create_volume_rejects_existing_name_before_create(monkeypatch) -> None:
    ex = executor()
    run_handle = handle()
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def existing(arguments, *, check=True, timeout_seconds=None):
        calls.append((arguments, {"check": check, "timeout_seconds": timeout_seconds}))
        if arguments[:2] == ("volume", "inspect"):
            return completed(arguments)
        raise AssertionError(f"unexpected Docker command: {arguments}")

    monkeypatch.setattr(ex, "_run", existing)
    with pytest.raises(DockerExecutorError, match="refusing to reuse"):
        ex._create_volume(run_handle, "audit-volume")

    assert run_handle.created_volumes == []
    assert len(calls) == 1


def test_create_volume_registers_immediately_and_uses_owner_label(monkeypatch) -> None:
    ex = executor()
    run_handle = handle()
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(ex, "_run", fake_docker(run_handle.owner_token, calls))

    ex._create_volume(run_handle, "audit-volume")

    assert run_handle.created_volumes == ["audit-volume"]
    create = next(
        arguments for arguments, _ in calls if arguments[:2] == ("volume", "create")
    )
    assert "--label" in create
    assert f"aero-bench/owner={run_handle.owner_token}" in create


def test_timeout_after_daemon_network_create_is_reconciled_and_cleaned(
    monkeypatch,
) -> None:
    ex = executor()
    run_handle = handle()
    resources: dict[tuple[str, str], dict[str, str]] = {}
    calls: list[tuple[str, ...]] = []
    network_name = "audit-network"

    monkeypatch.setattr(
        docker_module.shutil, "which", lambda _binary: "/usr/bin/docker"
    )

    def docker_run(command, **_kwargs):
        arguments = tuple(command[1:])
        calls.append(arguments)
        if arguments[:2] == ("network", "inspect"):
            name = arguments[-1]
            labels = resources.get(("network", name))
            if labels is None:
                return completed(
                    arguments,
                    returncode=1,
                    stderr=f"Error: No such network: {name}",
                )
            return completed(arguments, stdout=json.dumps(labels))
        if arguments[:2] == ("network", "create"):
            resources[("network", network_name)] = {
                "aero-bench/run": run_handle.run_id,
                "aero-bench/owner": run_handle.owner_token,
            }
            raise subprocess.TimeoutExpired(command, 1)
        if arguments[:2] == ("network", "rm"):
            resources.pop(("network", arguments[-1]), None)
            return completed(arguments)
        raise AssertionError(f"unexpected Docker command: {arguments}")

    monkeypatch.setattr(subprocess, "run", docker_run)

    with pytest.raises(DockerExecutorError, match="docker command timed out"):
        ex._create_network(run_handle, network_name, internal=True)

    assert run_handle.created_networks == [network_name]
    assert resources[("network", network_name)]["aero-bench/owner"] == (
        run_handle.owner_token
    )

    ex.cleanup(run_handle)

    assert resources == {}
    assert run_handle.created_networks == []
    assert ("network", "rm", network_name) in calls


def test_verifier_staging_failure_reclaims_only_new_resources(
    monkeypatch, tmp_path
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    sealed_root = tmp_path / "sealed"
    sealed_root.mkdir()
    payload = b"sealed evidence\n"
    (sealed_root / "event.log").write_bytes(payload)
    artifact = ArtifactRecord(
        artifact_id="artifact.event",
        artifact_type="event.log",
        producer_id="harness",
        visibility="private",
        relative_path="event.log",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    seal = seal_manifest(
        root=sealed_root,
        run_id=run.run_id,
        attempt_id="attempt.test",
        execution_scope=run.execution_scope,
        event_chain_root="b" * 64,
        artifacts=(artifact,),
    )
    ex._write_manifest(sealed_root / "seal-manifest.json", seal)
    run_handle = handle(run.run_id)
    run_handle.seal_root = sealed_root
    run_handle.pre_verification_seal = seal
    ex._handles[run.run_id] = run_handle
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(ex, "_run", fake_docker(run_handle.owner_token, calls))

    def fail_staging(*args, **kwargs):
        raise RuntimeError("injected verifier staging failure")

    monkeypatch.setattr(ex, "_populate_workload_inputs", fail_staging)
    with pytest.raises(RuntimeError, match="injected verifier staging failure"):
        ex.start_verifier(plan, seal)

    assert run_handle.input_volumes == {}
    assert run_handle.artifact_volumes == {}
    assert run_handle.seal_volume is None
    assert run_handle.created_volumes == []
    assert run_handle.volume_keeper_containers == []
    assert run_handle.verifier_container is None
    remove_calls = [
        (arguments, options)
        for arguments, options in calls
        if len(arguments) >= 2 and arguments[1] == "rm"
    ]
    assert len(remove_calls) == 4
    assert all(
        options["timeout_seconds"] == ex._CONTROL_COMMAND_TIMEOUT_SECONDS
        for _, options in remove_calls
    )


def test_verifier_cleanup_includes_registered_unmapped_volume(monkeypatch) -> None:
    ex = executor()
    run_handle = handle()
    run_handle.created_volumes.append("registered-before-map")
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(ex, "_run", fake_docker(run_handle.owner_token, calls))

    ex._cleanup_verifier_resources(
        run_handle,
        "verifier",
        staging_before=(),
        keepers_before=(),
        volumes_before=(),
    )

    remove_calls = [
        arguments for arguments, _ in calls if arguments[:2] == ("volume", "rm")
    ]
    assert remove_calls == [("volume", "rm", "registered-before-map")]
    assert run_handle.created_volumes == []


def test_cleanup_reports_failure_and_retains_handle_for_retry(monkeypatch) -> None:
    ex = executor()
    run_handle = handle()
    run_handle.agent_credentials = {"reference.agent": "d" * 64}
    run_handle.created_volumes.append("audit-volume")
    run_handle.input_volumes["workload"] = "audit-volume"
    ex._handles[run_handle.run_id] = run_handle
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        ex,
        "_run",
        fake_docker(run_handle.owner_token, calls, remove_error=True),
    )

    with pytest.raises(DockerExecutorError, match="handle retained for retry"):
        ex.cleanup(run_handle)

    assert ex._handles[run_handle.run_id] is run_handle
    assert run_handle.created_volumes == ["audit-volume"]
    assert run_handle.cleanup_errors
    assert run_handle.agent_credentials == {}
    remove = next(
        options for arguments, options in calls if arguments[:2] == ("volume", "rm")
    )
    assert remove["timeout_seconds"] == ex._CONTROL_COMMAND_TIMEOUT_SECONDS

    calls.clear()
    monkeypatch.setattr(ex, "_run", fake_docker(run_handle.owner_token, calls))
    ex.cleanup(run_handle)
    assert run_handle.run_id not in ex._handles
    assert run_handle.created_volumes == []
    assert run_handle.cleanup_errors == ()


def test_cleanup_exception_clears_credentials(monkeypatch) -> None:
    ex = executor()
    run_handle = handle()
    run_handle.agent_credentials = {"reference.agent": "f" * 64}
    ex._handles[run_handle.run_id] = run_handle

    def fail_cleanup(*_args, **_kwargs):
        raise RuntimeError("injected cleanup exception")

    monkeypatch.setattr(ex, "_cleanup_resources", fail_cleanup)
    with pytest.raises(RuntimeError, match="injected cleanup exception"):
        ex.cleanup(run_handle)

    assert run_handle.agent_credentials == {}


def test_cleanup_refuses_to_delete_a_replaced_resource(monkeypatch) -> None:
    ex = executor()
    run_handle = handle()
    run_handle.created_volumes.append("audit-volume")
    ex._handles[run_handle.run_id] = run_handle
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def replaced(arguments, *, check=True, timeout_seconds=None):
        calls.append((arguments, {"check": check, "timeout_seconds": timeout_seconds}))
        if arguments[:2] == ("volume", "inspect"):
            return completed(
                arguments,
                stdout=json.dumps({"aero-bench/owner": "another-owner"}),
            )
        raise AssertionError(f"unexpected Docker command: {arguments}")

    monkeypatch.setattr(ex, "_run", replaced)
    with pytest.raises(DockerExecutorError, match="handle retained for retry"):
        ex.cleanup(run_handle)

    assert run_handle.created_volumes == ["audit-volume"]
    assert ex._handles[run_handle.run_id] is run_handle
    assert not any(arguments[:2] == ("volume", "rm") for arguments, _ in calls)


def test_timeout_stop_is_bounded_and_reports_stop_failure(monkeypatch) -> None:
    ex = executor()
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def timed_run(arguments, *, check=True, timeout_seconds=None):
        calls.append((arguments, {"check": check, "timeout_seconds": timeout_seconds}))
        if arguments[0] == "wait":
            raise subprocess.TimeoutExpired(arguments, 1)
        return completed(arguments)

    monkeypatch.setattr(ex, "_run", timed_run)
    with pytest.raises(DockerExecutorError, match="exceeded its explicit"):
        ex._wait_for_containers(
            ("audit-container",), timeout_seconds=1, stage="runtime"
        )

    stop = next(options for arguments, options in calls if arguments[0] == "stop")
    assert stop["timeout_seconds"] == ex._CONTROL_COMMAND_TIMEOUT_SECONDS


def test_seed_create_is_digest_local_and_pull_free(monkeypatch, tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    run_handle = handle(run.run_id)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(ex, "_run", fake_docker(run_handle.owner_token, calls))

    ex._copy_tree_into_volume(
        plan.runtime_workloads[0],
        volume="audit-volume",
        source_root=tmp_path,
        seed_name="audit-seed",
        destination="/run/aero-input",
        handle=run_handle,
    )

    create = next(arguments for arguments, _ in calls if arguments[0] == "create")
    assert create[create.index("--pull") + 1] == "never"
    assert run_handle.staging_containers == []


def test_provider_container_receives_materialized_bind_endpoint(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    provider = next(item for item in plan.runtime_workloads if item.role == "provider")
    run_handle = handle(run.run_id)
    run_handle.input_volumes[provider.workload_id] = "provider-input"
    run_handle.artifact_volumes[provider.workload_id] = "provider-artifacts"
    run_handle.provider_credentials[provider.workload_id] = "e" * 64

    arguments = ex._create_arguments(
        plan,
        run_handle,
        provider,
        "provider-container",
        network="provider-network",
    )

    environment = {
        arguments[index + 1].split("=", maxsplit=1)[0]: arguments[index + 1].split(
            "=", maxsplit=1
        )[1]
        for index, value in enumerate(arguments)
        if value == "--env"
    }
    assert environment["AERO_BENCH_PROVIDER_BIND_HOST"] == "0.0.0.0"
    assert environment["AERO_BENCH_PROVIDER_TOKEN"] == "e" * 64
    declared = next(
        item
        for item in run.environment.providers
        if item.provider_id == provider.workload_id
    )
    assert environment["AERO_BENCH_PROVIDER_PORT"] == str(declared.port)


def test_start_runtime_allocates_isolated_agent_credentials_and_gateway_env(
    monkeypatch, tmp_path
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    created_arguments: dict[str, tuple[str, ...]] = {}

    monkeypatch.setattr(ex, "_require_ready", lambda _plan: None)

    def create_network(run_handle, name, *, internal):
        assert isinstance(internal, bool)
        run_handle.created_networks.append(name)

    def create_volume(run_handle, name):
        run_handle.created_volumes.append(name)

    def create_tmpfs_volume(name, _size_bytes, *, handle):
        handle.created_volumes.append(name)

    def start_keeper(run_handle, *, volume, container_name):
        assert volume
        run_handle.volume_keeper_containers.append(container_name)

    def create_container(run_handle, workload, arguments):
        created_arguments[workload.workload_id] = arguments
        name = arguments[arguments.index("--name") + 1]
        run_handle.runtime_containers[workload.workload_id] = name

    monkeypatch.setattr(ex, "_create_network", create_network)
    monkeypatch.setattr(ex, "_create_volume", create_volume)
    monkeypatch.setattr(ex, "_create_tmpfs_volume", create_tmpfs_volume)
    monkeypatch.setattr(ex, "_start_volume_keeper", start_keeper)
    monkeypatch.setattr(ex, "_populate_workload_inputs", lambda *args, **kwargs: None)
    monkeypatch.setattr(ex, "_create_container", create_container)
    monkeypatch.setattr(
        ex,
        "_run",
        lambda arguments, **_kwargs: completed(arguments),
    )
    monkeypatch.setattr(
        ex,
        "_wait_for_health",
        lambda *_args, **_kwargs: pytest.fail(
            "executor_validation must not require Docker healthchecks"
        ),
    )

    run_handle = ex.start_runtime(plan)
    expected_agents = {
        item.workload_id for item in plan.runtime_workloads if item.role == "agent"
    }

    def environment(arguments):
        return {
            arguments[index + 1].split("=", maxsplit=1)[0]: arguments[index + 1].split(
                "=", maxsplit=1
            )[1]
            for index, value in enumerate(arguments)
            if value == "--env"
        }

    harness_environment = environment(created_arguments["harness"])
    credentials = json.loads(harness_environment["AERO_BENCH_AGENT_CREDENTIALS"])
    assert set(credentials) == expected_agents
    assert len(set(credentials.values())) == len(expected_agents)
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", token) and token != "0" * 64
        for token in credentials.values()
    )
    assert harness_environment["AERO_BENCH_AGENT_CREDENTIALS"] == (
        canonical_json_bytes(credentials).decode("utf-8")
    )
    assert "AERO_BENCH_AGENT_TOKEN" not in harness_environment
    assert harness_environment["AERO_BENCH_GATEWAY_BIND_HOST"] == "0.0.0.0"

    agent = next(item for item in plan.runtime_workloads if item.role == "agent")
    agent_environment = environment(created_arguments[agent.workload_id])
    assert agent_environment["AERO_BENCH_AGENT_TOKEN"] == credentials[agent.workload_id]
    assert "AERO_BENCH_AGENT_CREDENTIALS" not in agent_environment
    assert (
        agent_environment["AERO_BENCH_GATEWAY_HOST"]
        == created_arguments["harness"][
            created_arguments["harness"].index("--name") + 1
        ]
    )

    provider = next(item for item in plan.runtime_workloads if item.role == "provider")
    provider_environment = environment(created_arguments[provider.workload_id])
    assert "AERO_BENCH_AGENT_TOKEN" not in provider_environment
    assert "AERO_BENCH_AGENT_CREDENTIALS" not in provider_environment
    assert "AERO_BENCH_PROVIDER_TOKEN" in provider_environment
    assert "AERO_BENCH_PROVIDER_CREDENTIALS" not in provider_environment
    assert "AERO_BENCH_PROVIDER_TOKEN" not in harness_environment
    provider_credentials = json.loads(
        harness_environment["AERO_BENCH_PROVIDER_CREDENTIALS"]
    )
    assert (
        provider_environment["AERO_BENCH_PROVIDER_TOKEN"]
        == provider_credentials[provider.workload_id]
    )
    assert run_handle.agent_credentials == {}
    assert run_handle.provider_credentials == {}

    monkeypatch.setattr(ex, "_cleanup_resources", lambda *args, **kwargs: ())
    ex.cleanup(run_handle)
    assert run_handle.agent_credentials == {}
    assert run_handle.provider_credentials == {}


def test_docker_command_errors_redact_provider_credentials(monkeypatch) -> None:
    ex = executor()
    token = "e" * 64
    other = "d" * 64
    credentials = canonical_json_bytes({"network": token, "traffic": other}).decode(
        "utf-8"
    )

    def failed_run(arguments, **_kwargs):
        return completed(arguments, returncode=1)

    monkeypatch.setattr(subprocess, "run", failed_run)
    with pytest.raises(DockerExecutorError) as error:
        ex._run(
            (
                "create",
                "--env",
                f"AERO_BENCH_PROVIDER_TOKEN={token}",
                "--env",
                f"AERO_BENCH_PROVIDER_CREDENTIALS={credentials}",
            )
        )

    message = str(error.value)
    assert token not in message
    assert other not in message
    assert credentials not in message
    assert "<redacted>" in message


@pytest.mark.parametrize("urban_engineering", [False, True])
def test_formal_start_runtime_waits_for_provider_and_harness_health(
    monkeypatch, tmp_path, urban_engineering
) -> None:
    bundle = build_bundle(tmp_path)
    promote_bundle_to_formal(bundle)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    if urban_engineering:
        # Unit-only startup routing; this is not a resolved urban execution.
        package = run.task.package.model_copy(update={"package_id": "urban.uav-recovery-demo.v1"})
        task = run.task.model_copy(update={"package": package})
        run = run.model_copy(update={"execution_scope": "executor_validation", "task": task})
        plan = plan.model_copy(update={"run": run})
    events: list[tuple[str, tuple[str, ...]]] = []
    created: dict[str, str] = {}
    stream_roles: list[str] = []

    monkeypatch.setattr(ex, "_require_ready", lambda _plan: None)
    monkeypatch.setattr(ex, "_create_network", lambda *args, **kwargs: None)
    monkeypatch.setattr(ex, "_create_volume", lambda *args, **kwargs: None)
    monkeypatch.setattr(ex, "_create_tmpfs_volume", lambda *args, **kwargs: None)
    monkeypatch.setattr(ex, "_start_volume_keeper", lambda *args, **kwargs: None)
    monkeypatch.setattr(ex, "_populate_workload_inputs", lambda *args, **kwargs: None)

    def create_container(run_handle, workload, arguments):
        name = arguments[arguments.index("--name") + 1]
        created[workload.workload_id] = name
        run_handle.runtime_containers[workload.workload_id] = name

    monkeypatch.setattr(ex, "_create_container", create_container)

    def run_command(arguments, **_kwargs):
        if arguments[0] == "start":
            workload_id = next(
                workload_id
                for workload_id, name in created.items()
                if name == arguments[1]
            )
            events.append(("start", (workload_id,)))
        return completed(arguments)

    monkeypatch.setattr(ex, "_run", run_command)

    def wait_for_health(containers, *, timeout_seconds):
        assert timeout_seconds == 30
        workload_ids = tuple(
            workload_id
            for container in containers
            for workload_id, name in created.items()
            if name == container
        )
        events.append(("healthy", workload_ids))

    monkeypatch.setattr(ex, "_wait_for_health", wait_for_health)
    monkeypatch.setattr(
        ex, "_start_runtime_stream_pumps",
        lambda _plan, _handle, *, workload_role: stream_roles.append(workload_role),
    )
    run_handle = ex.start_runtime(plan)
    assert stream_roles == ["harness", "provider", "agent"]
    assert run_handle.fail_fast_runtime is True

    provider_ids = tuple(
        sorted(
            workload.workload_id
            for workload in plan.runtime_workloads
            if workload.role == "provider"
        )
    )
    agent_ids = tuple(
        sorted(
            workload.workload_id
            for workload in plan.runtime_workloads
            if workload.role == "agent"
        )
    )
    assert events == [
        *[("start", (provider_id,)) for provider_id in provider_ids],
        ("healthy", provider_ids),
        ("start", ("harness",)),
        ("healthy", ("harness",)),
        *[("start", (agent_id,)) for agent_id in agent_ids],
    ]
    assert run_handle.agent_credentials == {}
    monkeypatch.setattr(ex, "_cleanup_resources", lambda *args, **kwargs: ())
    ex.cleanup(run_handle)


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (
            {"Status": "running", "Health": {"Status": "unhealthy"}},
            "unhealthy",
        ),
        (
            {"Status": "exited", "Health": {"Status": "starting"}},
            "exited",
        ),
    ],
)
def test_wait_for_health_fails_closed_for_unhealthy_or_early_exit(
    monkeypatch, state, message
) -> None:
    ex = executor()
    monkeypatch.setattr(
        ex,
        "_run",
        lambda arguments, **_kwargs: completed(
            arguments,
            stdout=json.dumps(state),
        ),
    )

    with pytest.raises(DockerExecutorError, match=message):
        ex._wait_for_health(("provider-container",), timeout_seconds=1)


def test_wait_for_health_includes_status_and_logs_on_early_exit(monkeypatch) -> None:
    ex = executor()
    calls: list[tuple[str, ...]] = []

    def fake_run(arguments, **_kwargs):
        calls.append(arguments)
        if arguments and arguments[0] == "logs":
            return completed(arguments, stdout="ERROR: Harness bootstrap failed\n")
        return completed(
            arguments,
            stdout=json.dumps({"Status": "exited", "ExitCode": 1, "Health": {"Status": "starting"}}),
        )

    monkeypatch.setattr(ex, "_run", fake_run)

    with pytest.raises(
        DockerExecutorError,
        match=r"exited before becoming healthy: provider-container \(status=exited, exit_code=1\); logs=ERROR: Harness bootstrap failed",
    ):
        ex._wait_for_health(("provider-container",), timeout_seconds=1)
    assert any(call[:1] == ("logs",) for call in calls)


def test_wait_for_health_fails_closed_at_explicit_deadline(monkeypatch) -> None:
    ex = executor()
    times = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(docker_module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(docker_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        ex,
        "_run",
        lambda arguments, **_kwargs: completed(
            arguments,
            stdout=json.dumps({"Status": "running", "Health": {"Status": "starting"}}),
        ),
    )

    with pytest.raises(DockerExecutorError, match="explicit 1-second timeout"):
        ex._wait_for_health(("provider-container",), timeout_seconds=1)


def test_start_runtime_failure_clears_credential_maps(monkeypatch, tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    captured: dict[str, DockerExecutionHandle] = {}

    monkeypatch.setattr(ex, "_require_ready", lambda _plan: None)

    def fail_create_network(run_handle, _name, *, internal):
        assert isinstance(internal, bool)
        captured["handle"] = run_handle
        raise RuntimeError("injected setup failure")

    monkeypatch.setattr(ex, "_create_network", fail_create_network)

    with pytest.raises(RuntimeError, match="injected setup failure"):
        ex.start_runtime(plan)

    assert captured["handle"].agent_credentials == {}
    assert captured["handle"].provider_credentials == {}
    ex.cleanup_run(run.run_id)


def test_start_runtime_cleanup_failure_retains_handle_for_cleanup_run_retry(
    monkeypatch, tmp_path
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    captured: dict[str, DockerExecutionHandle] = {}
    cleanup_attempts = 0

    monkeypatch.setattr(ex, "_require_ready", lambda _plan: None)

    def fail_create_network(run_handle, _name, *, internal):
        assert isinstance(internal, bool)
        captured["handle"] = run_handle
        raise RuntimeError("injected setup failure")

    monkeypatch.setattr(ex, "_create_network", fail_create_network)

    def cleanup_resources(*_args, **_kwargs):
        nonlocal cleanup_attempts
        cleanup_attempts += 1
        return ("cleanup failed",) if cleanup_attempts == 1 else ()

    monkeypatch.setattr(ex, "_cleanup_resources", cleanup_resources)
    with pytest.raises(DockerExecutorError, match="handle was retained"):
        ex.start_runtime(plan)

    run_handle = captured["handle"]
    assert ex._handles[run.run_id] is run_handle
    assert run_handle.agent_credentials == {}
    assert run_handle.provider_credentials == {}
    ex.cleanup_run(run.run_id)
    assert run.run_id not in ex._handles
    assert cleanup_attempts == 2


def test_execution_handle_repr_excludes_credential_maps() -> None:
    run_handle = handle()
    agent_token = "e" * 64
    provider_token = "f" * 64
    run_handle.agent_credentials = {"reference.agent": agent_token}
    run_handle.provider_credentials = {"business": provider_token}

    representation = repr(run_handle)

    assert agent_token not in representation
    assert provider_token not in representation
    assert run_handle.run_id in representation


def test_start_runtime_base_exception_cleans_resources_and_reraises(
    monkeypatch, tmp_path
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor()
    plan = ex.materialize(run, bundle_root=bundle.root)
    captured: dict[str, DockerExecutionHandle] = {}
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    monkeypatch.setattr(ex, "_require_ready", lambda _plan: None)

    def create_network(run_handle, name, *, internal):
        assert isinstance(internal, bool)
        run_handle.created_networks.append(name)

    def create_volume(run_handle, name):
        run_handle.created_volumes.append(name)

    def create_tmpfs_volume(name, _size_bytes, *, handle):
        handle.created_volumes.append(name)

    def start_keeper(run_handle, *, volume, container_name):
        assert volume
        run_handle.volume_keeper_containers.append(container_name)

    def interrupt_first_container(run_handle, workload, arguments):
        captured["handle"] = run_handle
        name = arguments[arguments.index("--name") + 1]
        run_handle.runtime_containers[workload.workload_id] = name
        raise KeyboardInterrupt("injected startup interrupt")

    monkeypatch.setattr(ex, "_create_network", create_network)
    monkeypatch.setattr(ex, "_create_volume", create_volume)
    monkeypatch.setattr(ex, "_create_tmpfs_volume", create_tmpfs_volume)
    monkeypatch.setattr(ex, "_start_volume_keeper", start_keeper)
    monkeypatch.setattr(ex, "_populate_workload_inputs", lambda *args, **kwargs: None)
    monkeypatch.setattr(ex, "_create_container", interrupt_first_container)

    def run_docker(arguments, **kwargs):
        return fake_docker(captured["handle"].owner_token, calls)(arguments, **kwargs)

    monkeypatch.setattr(ex, "_run", run_docker)

    with pytest.raises(KeyboardInterrupt, match="injected startup interrupt"):
        ex.start_runtime(plan)

    run_handle = captured["handle"]
    assert run.run_id not in ex._handles
    assert run_handle.agent_credentials == {}
    assert run_handle.provider_credentials == {}
    assert run_handle.runtime_containers == {}
    assert run_handle.volume_keeper_containers == []
    assert run_handle.created_networks == []
    assert run_handle.created_volumes == []
    assert run_handle.input_volumes == {}
    assert run_handle.artifact_volumes == {}
    assert any(arguments[:2] == ("container", "rm") for arguments, _ in calls)
    assert any(arguments[:2] == ("network", "rm") for arguments, _ in calls)
    assert any(arguments[:2] == ("volume", "rm") for arguments, _ in calls)


@pytest.mark.parametrize("field", ["provider_bind_host", "gateway_bind_host"])
def test_executor_rejects_invalid_bind_host(field) -> None:
    with pytest.raises(ValueError, match=field):
        executor(**{field: "gateway host/with whitespace"})


def test_executor_requires_positive_readiness_timeout() -> None:
    with pytest.raises(ValueError, match="readiness_timeout_seconds"):
        executor(readiness_timeout_seconds=0)


def test_docker_command_errors_redact_agent_credentials(monkeypatch) -> None:
    ex = executor()
    token = "a" * 64

    def failed_run(arguments, **_kwargs):
        return completed(arguments, returncode=1)

    monkeypatch.setattr(subprocess, "run", failed_run)
    with pytest.raises(DockerExecutorError) as error:
        ex._run(
            (
                "create",
                "--env",
                f"AERO_BENCH_AGENT_TOKEN={token}",
            )
        )

    assert token not in str(error.value)
    assert "<redacted>" in str(error.value)


def test_docker_command_errors_redact_nested_credential_json() -> None:
    ex = executor()
    first = "a" * 64
    second = "b" * 64
    credentials = canonical_json_bytes(
        {"agent-a": {"token": first}, "agent-b": [second]}
    ).decode("utf-8")
    arguments = (
        "create",
        "--env",
        f"AERO_BENCH_AGENT_CREDENTIALS={credentials}",
    )

    detail = ex._redact_text(
        f"command={arguments!r}; credentials={credentials}; {first}; {second}",
        arguments,
    )

    assert first not in detail
    assert second not in detail
    assert credentials not in detail


def test_docker_command_timeout_redacts_exception_command_and_output(
    monkeypatch,
) -> None:
    ex = executor()
    token = "e" * 64
    provider_token = "f" * 64
    credentials = canonical_json_bytes({"agent-a": token}).decode("utf-8")
    provider_credentials = canonical_json_bytes({"business": provider_token}).decode(
        "utf-8"
    )

    def timed_out(command, **_kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=1,
            output=f"stdout {token} {provider_token}".encode("utf-8"),
            stderr=(f"stderr {credentials} {provider_credentials}".encode("utf-8")),
        )

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(DockerExecutorError) as error:
        ex._run(
            (
                "create",
                "--env",
                f"AERO_BENCH_AGENT_TOKEN={token}",
                "--env",
                f"AERO_BENCH_AGENT_CREDENTIALS={credentials}",
                "--env",
                f"AERO_BENCH_PROVIDER_TOKEN={provider_token}",
                "--env",
                f"AERO_BENCH_PROVIDER_CREDENTIALS={provider_credentials}",
            ),
            timeout_seconds=1,
        )

    message = str(error.value)
    assert token not in message
    assert provider_token not in message
    assert credentials not in message
    assert provider_credentials not in message
    assert "docker command timed out" in message
    assert "command=" in message
    assert "stdout=" in message
    assert "stderr=" in message


def test_preflight_accounts_for_each_workload_artifact_maximum(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = executor(artifact_volume_size_bytes=1_500_000)
    report = ex.preflight(ex.materialize(run, bundle_root=bundle.root))

    capacity_blockers = [
        blocker
        for blocker in report.blockers
        if blocker.code == "docker.artifact-volume-too-small"
    ]
    assert {
        blocker.detail.split(":", maxsplit=1)[0] for blocker in capacity_blockers
    } == {"harness", "observation", "reference.agent"}
    expected_maximums = {
        "harness": "3145728 bytes",
        "observation": "3145728 bytes",
        "reference.agent": "2097152 bytes",
    }
    assert all(
        expected_maximums[blocker.detail.split(":", maxsplit=1)[0]]
        in blocker.detail
        for blocker in capacity_blockers
    )


def test_preflight_accounts_for_complete_verifier_seal_input() -> None:
    requirements = tuple(
        ArtifactRequirement(
            artifact_id=f"artifact.{index}",
            artifact_type="evidence",
            producer_id="harness",
            visibility="private",
            relative_path=f"evidence/{index}.json",
            max_size_bytes=size_bytes,
            source_asset_id=None,
        )
        for index, size_bytes in enumerate((600_000, 500_000), start=1)
    )
    declared_seal_bytes = sum(
        requirement.max_size_bytes for requirement in requirements
    )
    manifest_bytes = seal_manifest_size_upper_bound(
        run_id="a" * 64,
        execution_scope="formal_benchmark",
        requirements=requirements,
    )
    declared_snapshot_bytes = declared_seal_bytes + manifest_bytes
    ex = executor(input_volume_size_bytes=declared_snapshot_bytes - 1)
    plan = SimpleNamespace(
        run=SimpleNamespace(
            run_id="a" * 64,
            execution_scope="formal_benchmark",
            artifact_requirements=requirements,
            verification_outputs=(),
        ),
        runtime_workloads=(),
    )

    capacity_blockers = [
        blocker
        for blocker in ex._artifact_capacity_blockers(plan)
        if blocker.code == "docker.seal-input-volume-too-small"
    ]
    assert len(capacity_blockers) == 1
    assert (
        capacity_blockers[0].detail
        == "verifier: declared sealed snapshot maximum "
        f"{declared_snapshot_bytes} bytes (artifacts {declared_seal_bytes} + "
        f"manifest {manifest_bytes}) exceeds explicit input volume limit "
        f"{declared_snapshot_bytes - 1}"
    )


def test_manifest_paths_are_reserved() -> None:
    with pytest.raises(ValueError, match="reserved"):
        DockerExecutor._validate_requirement_contract(
            (
                ArtifactRequirement(
                    artifact_id="artifact.event-log",
                    artifact_type="event.log",
                    producer_id="harness",
                    visibility="private",
                    relative_path="seal-manifest.json",
                    max_size_bytes=1_048_576,
                    source_asset_id=None,
                ),
            ),
            manifest_name="seal-manifest.json",
        )


def test_producer_output_snapshot_rejects_undeclared_files(tmp_path) -> None:
    (tmp_path / "declared.json").write_bytes(b"declared")
    (tmp_path / "undeclared.json").write_bytes(b"undeclared")

    with pytest.raises(ValueError, match="undeclared=.*undeclared.json"):
        DockerExecutor._validate_output_snapshot(
            tmp_path,
            requirements=(
                ArtifactRequirement(
                    artifact_id="artifact.declared",
                    artifact_type="declared",
                    producer_id="harness",
                    visibility="private",
                    relative_path="declared.json",
                    max_size_bytes=1_048_576,
                    source_asset_id=None,
                ),
            ),
        )


def test_seal_directories_and_files_are_private(tmp_path) -> None:
    ex = executor()
    root = tmp_path / "seal"
    ex._create_private_directory(root)
    nested = root / "private"
    ex._ensure_private_directory(nested)
    artifact = nested / "truth.json"
    artifact.write_text("private", encoding="utf-8")
    ex._make_private_file(artifact)

    assert os.stat(root).st_mode & 0o777 == 0o700
    assert os.stat(nested).st_mode & 0o777 == 0o700
    assert os.stat(artifact).st_mode & 0o777 == 0o600


def test_runtime_seal_root_is_replayed_from_event_log(tmp_path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    ledger.append_event(
        source="harness",
        event_type="validation.scope",
        time=SimulationTime(tick=0, sim_time_ns=0),
        payload=(NamedValue(name="execution_scope", value="executor_validation"),),
    )
    ledger.append_event(
        source="harness",
        event_type="run.completed",
        time=SimulationTime(tick=1, sim_time_ns=100),
    )
    event_log = tmp_path / "event.log"
    ledger.write_jsonl(event_log)
    artifact = ArtifactRecord(
        artifact_id="artifact.event-log",
        artifact_type="event.log",
        producer_id="harness",
        visibility="private",
        relative_path="event.log",
        sha256=hashlib.sha256(event_log.read_bytes()).hexdigest(),
        size_bytes=event_log.stat().st_size,
    )

    assert (
        DockerExecutor._validate_runtime_event_chain(
            tmp_path,
            (artifact,),
            run=run,
        )
        == ledger.chain_root
    )
    event_log.write_bytes(b"not-canonical-ledger\n")
    with pytest.raises(ValueError, match="invalid JSON"):
        DockerExecutor._validate_runtime_event_chain(
            tmp_path,
            (artifact,),
            run=run,
        )
