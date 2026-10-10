from __future__ import annotations

from tests.support import fixture_provider_registry

import base64
import json
import subprocess

import pytest
import yaml

from aero_bench.executor import (
    DockerExecutor,
    ExecutionPlan,
    KubernetesExecutor,
    KubernetesMaterializer,
)
from aero_bench.tasks.registry import builtin_task_package_resolvers
from tests.support import (
    build_bundle,
    digest,
    promote_bundle_to_formal,
    resolve_bundle,
)


def docker_executor(binary: str) -> DockerExecutor:
    return DockerExecutor(
        docker_binary=binary,
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        volume_keeper_mount_path="/run/aero-held",
        input_volume_size_bytes=33_554_432,
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
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    )


def shared_readiness_healthcheck(*, interpreter: str = "python") -> dict[str, object]:
    return {
        "Test": [
            "CMD",
            interpreter,
            "/opt/aero-bench/readiness_probe.py",
            "--timeout-seconds",
            "2",
        ],
        "Interval": 10_000_000_000,
        "Timeout": 3_000_000_000,
        "StartPeriod": 30_000_000_000,
        "Retries": 3,
    }


def kubernetes_materializer() -> KubernetesMaterializer:
    return KubernetesMaterializer(
        namespace="benchmark-ns",
        storage_class_name="benchmark-rwo",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        scratch_mount_path="/tmp",
        config_map_max_bytes=900_000,
        artifact_pvc_size="64Mi",
        seal_pvc_size="64Mi",
        scratch_size="32Mi",
        workload_uid=65532,
        workload_gid=65532,
        provider_bind_host="0.0.0.0",
        termination_grace_period_seconds=10,
        verifier_timeout_seconds=60,
        dns_namespace="kube-system",
        dns_pod_labels={"k8s-app": "kube-dns"},
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    )


def test_kubernetes_materializer_stages_verifier_and_sets_resources(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="kubernetes_cluster")[0]

    materialization = kubernetes_materializer().materialize(
        run,
        bundle_root=bundle.root,
    )
    runtime_roles = {
        resource["metadata"]["labels"]["aero-bench/role"]
        for resource in materialization.runtime_resources
        if resource["kind"] == "Job"
    }
    verifier_job = next(
        resource
        for resource in materialization.verification_resources
        if resource["kind"] == "Job"
    )
    service = next(
        resource
        for resource in materialization.runtime_resources
        if resource["kind"] == "Service"
    )
    harness_job = next(
        resource
        for resource in materialization.runtime_resources
        if resource["kind"] == "Job"
        and resource["metadata"]["labels"]["aero-bench/role"] == "harness"
    )
    provider_job = next(
        resource
        for resource in materialization.runtime_resources
        if resource["kind"] == "Job"
        and resource["metadata"]["labels"]["aero-bench/role"] == "provider"
    )

    assert runtime_roles == {"harness", "provider", "agent"}
    assert verifier_job["metadata"]["labels"]["aero-bench/phase"] == "verification"
    assert service["spec"]["ports"][0]["port"] == 17432
    resources = harness_job["spec"]["template"]["spec"]["containers"][0]["resources"]
    pod_spec = harness_job["spec"]["template"]["spec"]
    security_context = harness_job["spec"]["template"]["spec"]["containers"][0][
        "securityContext"
    ]
    assert resources["requests"]["cpu"] == "500m"
    assert resources["limits"]["memory"] == "256Mi"
    assert pod_spec["automountServiceAccountToken"] is False
    assert "hostNetwork" not in pod_spec
    assert security_context["runAsNonRoot"] is True
    assert security_context["readOnlyRootFilesystem"] is True
    assert security_context["capabilities"] == {"drop": ["ALL"]}
    assert security_context["seccompProfile"] == {"type": "RuntimeDefault"}
    provider_environment = {
        item["name"]: item["value"]
        for item in provider_job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert provider_environment["AERO_BENCH_PROVIDER_BIND_HOST"] == "0.0.0.0"
    declared = next(
        item
        for item in run.environment.providers
        if item.provider_id == provider_environment["AERO_BENCH_WORKLOAD_ID"]
    )
    assert provider_environment["AERO_BENCH_PROVIDER_PORT"] == str(declared.port)
    assert harness_job["spec"]["activeDeadlineSeconds"] == 3_000
    assert verifier_job["spec"]["activeDeadlineSeconds"] == 60
    assert any(
        resource["kind"] == "ConfigMap"
        for resource in materialization.runtime_resources
    )
    assert any(
        resource["kind"] == "PersistentVolumeClaim"
        for resource in materialization.verification_resources
    )
    verifier_mounts = verifier_job["spec"]["template"]["spec"]["containers"][0][
        "volumeMounts"
    ]
    assert any(
        mount["name"] == "sealed-runtime" and mount["readOnly"]
        for mount in verifier_mounts
    )
    runtime_config_bytes = b"".join(
        base64.b64decode(value)
        for resource in materialization.runtime_resources
        if resource["kind"] == "ConfigMap"
        for value in resource["binaryData"].values()
    )
    verifier_config_bytes = b"".join(
        base64.b64decode(value)
        for resource in materialization.verification_resources
        if resource["kind"] == "ConfigMap"
        for value in resource["binaryData"].values()
    )
    assert b'"defects":["defect-1"]' not in runtime_config_bytes
    assert b'"defects":["defect-1"]' in verifier_config_bytes
    assert any(
        resource["kind"] == "Service"
        and resource["metadata"]["name"].endswith("-provider")
        for resource in materialization.runtime_resources
    )
    dns_policy = next(
        resource
        for resource in materialization.runtime_resources
        if resource["kind"] == "NetworkPolicy"
        and resource["metadata"]["name"].endswith("-dns")
    )
    dns_rule = dns_policy["spec"]["egress"][0]
    assert dns_rule["to"] == [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
            },
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }
    ]
    assert dns_policy["spec"]["podSelector"]["matchExpressions"][0]["values"] == [
        "agent",
        "harness",
        "provider",
    ]


def test_docker_preflight_never_substitutes_an_unavailable_binary(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    executor = docker_executor("docker-not-installed")
    report = executor.preflight(executor.materialize(run, bundle_root=bundle.root))

    assert not report.ready
    assert report.blockers[0].code == "docker.unavailable"


def test_formal_docker_preflight_rejects_unattested_image_labels(
    monkeypatch, tmp_path
) -> None:
    bundle = build_bundle(tmp_path)
    promote_bundle_to_formal(bundle)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    executor = docker_executor("sh")
    plan = executor.materialize(run, bundle_root=bundle.root)

    def fake_run(arguments, *, check=True, timeout_seconds=None):
        if arguments[0] == "version":
            output = "26.0.0\n"
        elif arguments[0] == "info":
            output = json.dumps(["name=seccomp,profile=default"])
        elif arguments[:2] == ("image", "inspect"):
            if arguments[3] == "{{json .RepoDigests}}":
                output = json.dumps([arguments[-1]])
            elif arguments[3] == "{{json .Config.Healthcheck}}":
                output = json.dumps(shared_readiness_healthcheck(interpreter="python3"))
            else:
                output = "{}"
        else:
            raise AssertionError(f"unexpected Docker command: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(executor, "_run", fake_run)
    report = executor.preflight(plan)

    assert not report.ready
    assert report.execution_scope == "formal_benchmark"
    assert {blocker.code for blocker in report.blockers} == {
        "docker.image.identity-mismatch"
    }


@pytest.mark.parametrize(
    ("healthcheck", "expected_code"),
    [
        (None, "docker.image.healthcheck-missing"),
        ({"Test": ["NONE"]}, "docker.image.healthcheck-disabled"),
        (
            {
                "Test": ["CMD-SHELL", "true"],
                "Interval": 10_000_000_000,
                "Timeout": 3_000_000_000,
                "StartPeriod": 30_000_000_000,
                "Retries": 3,
            },
            "docker.image.healthcheck-not-shared",
        ),
        (
            {
                "Test": ["CMD", "python", "/opt/aero-bench/readiness_probe.py"],
                "Interval": 10_000_000_000,
                "Timeout": 3_000_000_000,
                "StartPeriod": 30_000_000_000,
                "Retries": 0,
            },
            "docker.image.healthcheck-invalid",
        ),
    ],
)
def test_formal_preflight_requires_provider_and_harness_healthchecks(
    monkeypatch, tmp_path, healthcheck, expected_code
) -> None:
    bundle = build_bundle(tmp_path)
    promote_bundle_to_formal(bundle)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ex = docker_executor("sh")
    plan = ex.materialize(run, bundle_root=bundle.root)
    inspected_health_images: list[str] = []

    def fake_run(arguments, *, check=True, timeout_seconds=None):
        if arguments[0] == "version":
            output = "26.0.0\n"
        elif arguments[0] == "info":
            output = json.dumps(["name=seccomp,profile=default"])
        elif arguments[:2] == ("image", "inspect"):
            if arguments[3] == "{{json .RepoDigests}}":
                output = json.dumps([arguments[-1]])
            elif arguments[3] == "{{json .Config.Healthcheck}}":
                inspected_health_images.append(arguments[-1])
                output = json.dumps(healthcheck)
            else:
                output = "{}"
        else:
            raise AssertionError(f"unexpected Docker command: {arguments}")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(ex, "_run", fake_run)
    report = ex.preflight(plan)

    assert expected_code in {blocker.code for blocker in report.blockers}
    expected_health_images = {
        workload.workload.runtime.image
        for workload in plan.runtime_workloads
        if workload.role in {"provider", "harness"}
    }
    assert set(inspected_health_images) == expected_health_images


def test_infeasible_task_package_blocks_every_executor(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    package_path = bundle.root / "tasks/inspection-package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["bounds"]["mission"]["deadline_s"] = 1.0
    package_path.write_text(
        json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    task = yaml.safe_load(bundle.task.read_text(encoding="utf-8"))
    task["package"]["config"]["file"]["sha256"] = digest(package_path)
    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    provider_config_path = bundle.root / "configs/provider.json"
    provider_config = json.loads(provider_config_path.read_text(encoding="utf-8"))
    provider_config["bounds"]["mission.deadline_s"] = 1.0
    provider_config_path.write_text(
        json.dumps(provider_config, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    environment_path = bundle.root / "environments/environment.yaml"
    environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
    for provider in environment["providers"]:
        provider["config"]["file"]["sha256"] = digest(provider_config_path)
    environment_path.write_text(
        yaml.safe_dump(environment, sort_keys=False), encoding="utf-8"
    )
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["task"]["sha256"] = digest(bundle.task)
    suite["cases"][0]["environment"]["sha256"] = digest(environment_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")

    docker_run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    docker = docker_executor("docker-not-installed")
    report = docker.preflight(docker.materialize(docker_run, bundle_root=bundle.root))
    kubernetes_run = resolve_bundle(
        bundle.suite,
        executor_kind="kubernetes_cluster",
    )[0]

    assert not docker_run.feasibility.feasible
    assert "mission_deadline" in docker_run.feasibility.failed_conditions
    assert "feasibility.impossible" in {blocker.code for blocker in report.blockers}
    with pytest.raises(ValueError, match="infeasible"):
        kubernetes_materializer().materialize(
            kubernetes_run,
            bundle_root=bundle.root,
        )


def test_execution_plan_materializes_only_role_authorized_inputs(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    plan = docker_executor("docker-not-installed").materialize(
        run,
        bundle_root=bundle.root,
    )
    by_role = {workload.role: workload for workload in plan.runtime_workloads}
    agent_paths = {item.source.path for item in by_role["agent"].bundle_inputs}
    provider_paths = {item.source.path for item in by_role["provider"].bundle_inputs}
    harness_paths = {item.source.path for item in by_role["harness"].bundle_inputs}
    verifier_paths = {item.source.path for item in plan.verifier_workload.bundle_inputs}

    assert "private/truth.json" not in agent_paths
    assert "private/truth.json" not in provider_paths
    assert "private/truth.json" not in harness_paths
    assert "private/truth.json" in verifier_paths
    assert "configs/provider.json" in provider_paths
    assert "configs/provider.json" in harness_paths
    assert "configs/provider.json" not in agent_paths
    assert "configs/verifier.json" in verifier_paths
    assert "configs/verifier.json" not in harness_paths


@pytest.mark.parametrize("executor_kind", ["docker_reference", "kubernetes_cluster"])
def test_world_package_is_verifier_control_input_not_payload(tmp_path, executor_kind) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind=executor_kind)[0]
    executor = docker_executor("docker-not-installed") if executor_kind == "docker_reference" else KubernetesExecutor(
        kubectl_binary="kubectl",
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    )
    plan = executor.materialize(run, bundle_root=bundle.root)
    authority = run.scenario.source_world_package
    inputs = [item for item in plan.verifier_workload.bundle_inputs if item.source.path == authority.path]
    assert len(inputs) == 1
    assert inputs[0].source == authority
    assert inputs[0].destination == "bundle/" + authority.path
    assert inputs[0].size_bytes == (bundle.root / authority.path).stat().st_size
    assert inputs[0].source.sha256 == digest(bundle.root / authority.path)
    assert all(asset.file.path != authority.path for asset in run.scenario.assets)
    assert all(asset.file.path != authority.path for asset in run.task.assets)
    for workload in plan.runtime_workloads:
        assert all(item.source.path != authority.path for item in workload.bundle_inputs)


def test_world_package_declared_as_payload_asset_keeps_control_separation(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    task = yaml.safe_load(bundle.task.read_text())
    task["assets"].append({
        "asset_id": "world.authority-as-payload",
        "file": run.scenario.source_world_package.model_dump(mode="json"),
        "classification": "private",
        "audiences": [{"role": "verifier", "workload_ids": [run.task.verifier.verifier_id]}],
    })
    bundle.task.write_text(yaml.safe_dump(task, sort_keys=False))
    suite = yaml.safe_load(bundle.suite.read_text())
    suite["cases"][0]["task"]["sha256"] = digest(bundle.task)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False))
    with pytest.raises(ValueError, match="payload/license files must be disjoint from control files"):
        resolve_bundle(bundle.suite, executor_kind="docker_reference")


def test_docker_preflight_rejects_a_forged_execution_plan(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    executor = docker_executor("docker-not-installed")
    plan = executor.materialize(run, bundle_root=bundle.root)
    raw = plan.model_dump(mode="json")
    agent = next(
        workload for workload in raw["runtime_workloads"] if workload["role"] == "agent"
    )
    truth = next(
        asset for asset in run.task.assets if asset.asset_id == "inspection.truth"
    )
    agent["bundle_inputs"].append(
        {
            "source": truth.file.model_dump(mode="json"),
            "destination": f"bundle/{truth.file.path}",
            "size_bytes": (bundle.root / truth.file.path).stat().st_size,
        }
    )
    forged = ExecutionPlan.model_validate(raw)

    report = executor.preflight(forged)

    assert not report.ready
    assert report.blockers[0].code == "execution-plan.invalid"
    assert "unique materialization" in report.blockers[0].detail


def test_materializer_revalidates_pinned_task_package_at_execution_boundary(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    package_path = bundle.root / "tasks/inspection-package.json"
    package_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        docker_executor("docker-not-installed").materialize(
            run,
            bundle_root=bundle.root,
        )


def test_materializers_refuse_another_executor_profile(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    docker_run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    kubernetes_run = resolve_bundle(bundle.suite, executor_kind="kubernetes_cluster")[0]

    with pytest.raises(ValueError, match="executor_kind"):
        kubernetes_materializer().materialize(
            docker_run,
            bundle_root=bundle.root,
        )
    docker_executor_instance = docker_executor("docker-not-installed")
    kubernetes_plan = KubernetesExecutor(
        kubectl_binary="kubectl",
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    ).materialize(
        kubernetes_run,
        bundle_root=bundle.root,
    )
    report = docker_executor_instance.preflight(kubernetes_plan)

    assert not report.ready
    assert report.blockers[0].code == "executor.mismatch"


def test_kubernetes_executor_cannot_report_ready_while_cluster_path_is_deferred(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="kubernetes_cluster")[0]
    executor = KubernetesExecutor(
        kubectl_binary="echo",
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=fixture_provider_registry(),
    )
    report = executor.preflight(executor.materialize(run, bundle_root=bundle.root))

    assert not report.ready
    assert "kubernetes.execution.deferred" in {
        blocker.code for blocker in report.blockers
    }
    assert "kubectl.invalid-client" in {blocker.code for blocker in report.blockers}
