from __future__ import annotations

from tests.support import fixture_provider_registry

import subprocess
from pathlib import Path
from typing import get_type_hints

import pytest

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.executor import DockerExecutor, Executor, KubernetesExecutor
from aero_bench.executor.contracts import ExecutionPlan, PreflightReport
from aero_bench.verifier.output import ValidatedVerificationOutput


def _docker_executor() -> DockerExecutor:
    return DockerExecutor(
        docker_binary="docker",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        volume_keeper_mount_path="/run/aero-held",
        input_volume_size_bytes=1_048_576,
        artifact_volume_size_bytes=4_194_304,
        scratch_size_bytes=1_048_576,
        pids_limit=64,
        workload_uid=65532,
        workload_gid=65532,
        provider_bind_host="0.0.0.0",
        gateway_bind_host="0.0.0.0",
        readiness_timeout_seconds=30,
        volume_keeper_image="registry.invalid/keeper@sha256:" + "6" * 64,
        volume_keeper_command=("/bin/sleep", "infinity"),
        volume_keeper_cpu_millicores=50,
        volume_keeper_memory_mib=64,
        task_package_resolvers=(object(),),
        provider_registry=fixture_provider_registry(),
    )


def _kubernetes_executor() -> KubernetesExecutor:
    return KubernetesExecutor(
        kubectl_binary="kubectl",
        task_package_resolvers=(object(),),
        provider_registry=fixture_provider_registry(),
    )


def test_executor_implementations_satisfy_complete_runtime_protocol() -> None:
    assert isinstance(_docker_executor(), Executor)
    assert isinstance(_kubernetes_executor(), Executor)

    expected = {
        "materialize",
        "preflight",
        "start_runtime",
        "wait_runtime",
        "collect_failure_outputs",
        "collect_and_seal",
        "start_verifier",
        "wait_verifier",
        "collect_verification_outputs",
        "cleanup",
        "cleanup_run",
    }
    assert expected <= set(Executor.__dict__)
    assert expected <= set(vars(DockerExecutor))
    assert expected <= set(vars(KubernetesExecutor))


def test_executor_protocol_uses_strict_lifecycle_types() -> None:
    materialize_hints = get_type_hints(Executor.materialize)
    assert materialize_hints["bundle_root"] == str | Path
    assert materialize_hints["return"] is ExecutionPlan
    assert get_type_hints(Executor.preflight)["return"] is PreflightReport
    assert get_type_hints(Executor.collect_and_seal)["destination_root"] is Path
    assert get_type_hints(Executor.collect_and_seal)["return"] is SealManifest
    assert get_type_hints(Executor.collect_failure_outputs)["destination_root"] is Path
    assert get_type_hints(Executor.collect_failure_outputs)["return"] is type(None)
    assert (
        get_type_hints(Executor.collect_verification_outputs)["destination_root"]
        is Path
    )
    assert (
        get_type_hints(Executor.collect_verification_outputs)["return"]
        is ValidatedVerificationOutput
    )
    for method_name in ("wait_runtime", "wait_verifier"):
        hints = get_type_hints(getattr(Executor, method_name))
        assert hints["timeout_seconds"] is int
        assert hints["return"] is type(None)
    cleanup_run_hints = get_type_hints(Executor.cleanup_run)
    assert cleanup_run_hints["run_id"] is str
    assert cleanup_run_hints["return"] is type(None)


@pytest.mark.parametrize(
    "method_name,args,kwargs",
    [
        ("start_runtime", (object(),), {}),
        ("wait_runtime", (object(),), {"timeout_seconds": 1}),
        (
            "collect_and_seal",
            (object(), object()),
            {"destination_root": Path("/tmp/seal")},
        ),
        ("start_verifier", (object(), object()), {}),
        ("wait_verifier", (object(),), {"timeout_seconds": 1}),
        (
            "collect_verification_outputs",
            (object(), object()),
            {"destination_root": Path("/tmp/verification")},
        ),
        ("cleanup", (object(),), {}),
        ("cleanup_run", ("a" * 64,), {}),
    ],
)
def test_kubernetes_runtime_lifecycle_is_fail_closed_without_kubectl_mutation(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("post-preflight Kubernetes lifecycle called kubectl")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    executor = _kubernetes_executor()

    with pytest.raises(RuntimeError, match="^kubernetes execution deferred:"):
        getattr(executor, method_name)(*args, **kwargs)
