from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import FileRef
from aero_bench.config.resolver import ResolvedRunSpec, TaskPackageResolver
from aero_bench.executor.contracts import (
    ExecutionPlan,
    PreflightBlocker,
    PreflightReport,
    ValidatedVerificationOutput,
    WorkloadPlan,
)
from aero_bench.executor.planning import build_execution_plan
from aero_bench.providers.registry import ProviderRegistry


_QUANTITY = re.compile(r"^[1-9][0-9]*(?:Ki|Mi|Gi|Ti)$")


@dataclass(frozen=True, slots=True)
class KubernetesMaterialization:
    runtime_resources: tuple[dict[str, object], ...]
    verification_resources: tuple[dict[str, object], ...]


class KubernetesMaterializer:
    """Pure ResolvedRun conversion to standard namespaced Kubernetes resources."""

    def __init__(
        self,
        *,
        namespace: str,
        storage_class_name: str,
        input_mount_path: str,
        artifact_mount_path: str,
        seal_mount_path: str,
        scratch_mount_path: str,
        config_map_max_bytes: int,
        artifact_pvc_size: str,
        seal_pvc_size: str,
        scratch_size: str,
        workload_uid: int,
        workload_gid: int,
        provider_bind_host: str,
        termination_grace_period_seconds: int,
        verifier_timeout_seconds: int,
        dns_namespace: str,
        dns_pod_labels: dict[str, str],
        task_package_resolvers: tuple[TaskPackageResolver, ...],
        provider_registry: ProviderRegistry,
    ):
        if not namespace or not storage_class_name or not dns_namespace:
            raise ValueError(
                "namespace, storage_class_name, and dns_namespace must be explicit"
            )
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", provider_bind_host) is None:
            raise ValueError("provider_bind_host must be an explicit network host")
        if not dns_pod_labels or any(
            not key or not value for key, value in dns_pod_labels.items()
        ):
            raise ValueError("dns_pod_labels must be explicit non-empty labels")
        paths = {
            input_mount_path,
            artifact_mount_path,
            seal_mount_path,
            scratch_mount_path,
        }
        if len(paths) != 4:
            raise ValueError("Kubernetes mount paths must be distinct")
        for value in paths:
            path = PurePosixPath(value)
            if not path.is_absolute() or ".." in path.parts or str(path) != value:
                raise ValueError(
                    "Kubernetes mount paths must be normalized and absolute"
                )
        for name, quantity in {
            "artifact_pvc_size": artifact_pvc_size,
            "seal_pvc_size": seal_pvc_size,
            "scratch_size": scratch_size,
        }.items():
            if _QUANTITY.fullmatch(quantity) is None:
                raise ValueError(
                    f"{name} must be an explicit binary Kubernetes quantity"
                )
        for name, value in {
            "config_map_max_bytes": config_map_max_bytes,
            "workload_uid": workload_uid,
            "workload_gid": workload_gid,
            "termination_grace_period_seconds": termination_grace_period_seconds,
            "verifier_timeout_seconds": verifier_timeout_seconds,
        }.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        self._namespace = namespace
        self._storage_class_name = storage_class_name
        self._input_mount_path = input_mount_path
        self._artifact_mount_path = artifact_mount_path
        self._seal_mount_path = seal_mount_path
        self._scratch_mount_path = scratch_mount_path
        self._config_map_max_bytes = config_map_max_bytes
        self._artifact_pvc_size = artifact_pvc_size
        self._seal_pvc_size = seal_pvc_size
        self._scratch_size = scratch_size
        self._workload_uid = workload_uid
        self._workload_gid = workload_gid
        self._provider_bind_host = provider_bind_host
        self._termination_grace_period_seconds = termination_grace_period_seconds
        self._verifier_timeout_seconds = verifier_timeout_seconds
        self._dns_namespace = dns_namespace
        self._dns_pod_labels = dict(dns_pod_labels)
        if not task_package_resolvers:
            raise ValueError("task_package_resolvers must be explicit and non-empty")
        self._task_package_resolvers = tuple(task_package_resolvers)
        self._provider_registry = provider_registry

    def materialize(
        self,
        run: ResolvedRunSpec,
        *,
        bundle_root: str | Path,
    ) -> KubernetesMaterialization:
        plan = build_execution_plan(
            run,
            executor_kind="kubernetes_cluster",
            bundle_root=bundle_root,
            task_package_resolvers=self._task_package_resolvers,
            provider_registry=self._provider_registry,
        )
        if not plan.run.feasibility.feasible:
            raise ValueError(
                "infeasible ResolvedRun cannot materialize Kubernetes resources"
            )
        prefix = f"aero-{run.run_id[:12]}"
        runtime_resources: list[dict[str, object]] = []
        for workload in plan.runtime_workloads:
            runtime_resources.extend(
                (
                    self._input_config_map(prefix, plan, workload),
                    self._pvc(
                        self._artifact_claim_name(prefix, workload),
                        self._artifact_pvc_size,
                        purpose="runtime-artifact",
                    ),
                    self._job(prefix, plan, workload),
                )
            )
        runtime_resources.extend(
            (
                self._gateway_service(prefix, run.environment.gateway.port),
                *(
                    self._provider_service(prefix, provider.provider_id, provider.port)
                    for provider in run.environment.providers
                ),
                *self._network_policies(run, prefix),
            )
        )

        verifier = plan.verifier_workload
        verification_resources = (
            self._input_config_map(prefix, plan, verifier),
            self._pvc(
                self._artifact_claim_name(prefix, verifier),
                self._artifact_pvc_size,
                purpose="verification-output",
            ),
            self._pvc(
                self._seal_claim_name(prefix),
                self._seal_pvc_size,
                purpose="sealed-runtime-input",
            ),
            self._job(prefix, plan, verifier),
        )
        return KubernetesMaterialization(
            runtime_resources=tuple(runtime_resources),
            verification_resources=verification_resources,
        )

    def _input_config_map(
        self,
        prefix: str,
        plan: ExecutionPlan,
        workload: WorkloadPlan,
    ) -> dict[str, object]:
        binary_data: dict[str, str] = {}
        total_bytes = 0
        entries = self._input_entries(plan, workload)
        for key, _, content in entries:
            total_bytes += len(content)
            binary_data[key] = base64.b64encode(content).decode("ascii")
        if total_bytes > self._config_map_max_bytes:
            raise ValueError(
                f"{workload.workload_id} inputs exceed explicit ConfigMap limit; "
                "provide a cluster asset transport before materialization"
            )
        name = self._input_config_map_name(prefix, workload)
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": self._labels(prefix, workload),
                "annotations": {"aero-bench/run-id": plan.run.run_id},
            },
            "immutable": True,
            "binaryData": binary_data,
        }

    def _input_entries(
        self,
        plan: ExecutionPlan,
        workload: WorkloadPlan,
    ) -> tuple[tuple[str, str, bytes], ...]:
        reader = BundleReader(Path(plan.bundle_root))
        raw_entries = [
            (
                workload.contract.destination,
                workload.contract.content_utf8.encode("utf-8"),
            ),
            *(
                (
                    item.destination,
                    self._read_pinned(
                        reader,
                        item.source,
                        expected_size=item.size_bytes,
                    ),
                )
                for item in workload.bundle_inputs
            ),
            *(
                (item.destination, item.content_utf8.encode("utf-8"))
                for item in workload.derived_inputs
            ),
        ]
        return tuple(
            (
                f"input-{index:04d}-{hashlib.sha256(content).hexdigest()[:8]}",
                destination,
                content,
            )
            for index, (destination, content) in enumerate(raw_entries)
        )

    @staticmethod
    def _read_pinned(
        reader: BundleReader,
        reference: FileRef,
        *,
        expected_size: int,
    ) -> bytes:
        path = reader.resolve_file(reference)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != reference.sha256:
            raise ValueError(f"bundle input changed while reading: {reference.path}")
        if len(content) != expected_size:
            raise ValueError(
                f"bundle input size changed while reading: {reference.path}"
            )
        return content

    def _job(
        self,
        prefix: str,
        plan: ExecutionPlan,
        workload: WorkloadPlan,
    ) -> dict[str, object]:
        name = self._workload_name(prefix, workload.workload_id)
        labels = self._labels(prefix, workload)
        resources: dict[str, object] = {
            "requests": {
                "cpu": f"{workload.workload.resources.cpu_millicores}m",
                "memory": f"{workload.workload.resources.memory_mib}Mi",
            },
            "limits": {
                "cpu": f"{workload.workload.resources.cpu_millicores}m",
                "memory": f"{workload.workload.resources.memory_mib}Mi",
            },
        }
        if workload.workload.resources.gpu_count:
            for resource_kind in ("requests", "limits"):
                resources[resource_kind]["nvidia.com/gpu"] = (
                    workload.workload.resources.gpu_count
                )

        input_items = [
            {"key": key, "path": destination, "mode": 0o444}
            for key, destination, _ in self._input_entries(plan, workload)
        ]
        volumes: list[dict[str, object]] = [
            {
                "name": "inputs",
                "configMap": {
                    "name": self._input_config_map_name(prefix, workload),
                    "items": input_items,
                    "defaultMode": 0o444,
                },
            },
            {
                "name": "artifacts",
                "persistentVolumeClaim": {
                    "claimName": self._artifact_claim_name(prefix, workload)
                },
            },
            {
                "name": "scratch",
                "emptyDir": {"sizeLimit": self._scratch_size},
            },
        ]
        volume_mounts: list[dict[str, object]] = [
            {"name": "inputs", "mountPath": self._input_mount_path, "readOnly": True},
            {"name": "artifacts", "mountPath": self._artifact_mount_path},
            {"name": "scratch", "mountPath": self._scratch_mount_path},
        ]
        environment = self._environment(plan, prefix, workload)
        if workload.role == "verifier":
            volumes.append(
                {
                    "name": "sealed-runtime",
                    "persistentVolumeClaim": {
                        "claimName": self._seal_claim_name(prefix),
                        "readOnly": True,
                    },
                }
            )
            volume_mounts.append(
                {
                    "name": "sealed-runtime",
                    "mountPath": self._seal_mount_path,
                    "readOnly": True,
                }
            )
            environment.append(
                {"name": "AERO_BENCH_SEAL_DIR", "value": self._seal_mount_path}
            )

        container_security = {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": self._workload_uid,
            "runAsGroup": self._workload_gid,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        }
        if workload.role == "verifier":
            active_deadline_seconds = self._verifier_timeout_seconds
        else:
            active_deadline_seconds = max(
                1,
                math.ceil(
                    plan.run.environment.clock.max_steps
                    * plan.run.environment.clock.provider_timeout_ms
                    / 1000
                ),
            )
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": labels,
                "annotations": {"aero-bench/run-id": plan.run.run_id},
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": active_deadline_seconds,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "restartPolicy": "Never",
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "terminationGracePeriodSeconds": self._termination_grace_period_seconds,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": self._workload_uid,
                            "runAsGroup": self._workload_gid,
                            "fsGroup": self._workload_gid,
                            "fsGroupChangePolicy": "OnRootMismatch",
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": self._container_name(workload.workload_id),
                                "image": workload.workload.runtime.image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": list(workload.workload.runtime.command),
                                "env": environment,
                                "resources": resources,
                                "securityContext": container_security,
                                "volumeMounts": volume_mounts,
                            }
                        ],
                        "volumes": volumes,
                    },
                },
            },
        }

    def _environment(
        self,
        plan: ExecutionPlan,
        prefix: str,
        workload: WorkloadPlan,
    ) -> list[dict[str, str]]:
        environment = [
            {"name": "AERO_BENCH_RUN_ID", "value": plan.run.run_id},
            {"name": "AERO_BENCH_SEED", "value": str(plan.run.seed)},
            {"name": "AERO_BENCH_ROLE", "value": workload.role},
            {"name": "AERO_BENCH_WORKLOAD_ID", "value": workload.workload_id},
            {"name": "AERO_BENCH_INPUT_DIR", "value": self._input_mount_path},
            {
                "name": "AERO_BENCH_CONTRACT",
                "value": f"{self._input_mount_path}/{workload.contract.destination}",
            },
            {
                "name": "AERO_BENCH_BUNDLE_DIR",
                "value": f"{self._input_mount_path}/bundle",
            },
            {"name": "AERO_BENCH_ARTIFACT_DIR", "value": self._artifact_mount_path},
        ]
        if workload.role in {"agent", "provider"}:
            environment.append(
                {"name": "AERO_BENCH_HARNESS_HOST", "value": f"{prefix}-gateway"}
            )
        if workload.role == "provider":
            provider = next(
                item
                for item in plan.run.environment.providers
                if item.provider_id == workload.workload_id
            )
            environment.extend(
                (
                    {
                        "name": "AERO_BENCH_PROVIDER_BIND_HOST",
                        "value": self._provider_bind_host,
                    },
                    {
                        "name": "AERO_BENCH_PROVIDER_PORT",
                        "value": str(provider.port),
                    },
                )
            )
        if workload.role in {"agent", "harness"}:
            environment.append(
                {
                    "name": "AERO_BENCH_GATEWAY_PORT",
                    "value": str(plan.run.environment.gateway.port),
                }
            )
        if workload.role == "harness":
            endpoints = {
                provider.provider_id: {
                    "host": self._provider_service_name(prefix, provider.provider_id),
                    "port": provider.port,
                }
                for provider in plan.run.environment.providers
            }
            environment.append(
                {
                    "name": "AERO_BENCH_PROVIDER_ENDPOINTS",
                    "value": json.dumps(
                        endpoints, sort_keys=True, separators=(",", ":")
                    ),
                }
            )
        return environment

    def _pvc(self, name: str, size: str, *, purpose: str) -> dict[str, object]:
        return {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": name,
                "namespace": self._namespace,
                "labels": {"aero-bench/purpose": purpose},
            },
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "storageClassName": self._storage_class_name,
                "resources": {"requests": {"storage": size}},
            },
        }

    def _gateway_service(self, prefix: str, port: int) -> dict[str, object]:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": f"{prefix}-gateway", "namespace": self._namespace},
            "spec": {
                "selector": {
                    "aero-bench/run": prefix,
                    "aero-bench/role": "harness",
                },
                "ports": [{"name": "gateway", "port": port, "targetPort": port}],
            },
        }

    def _provider_service(
        self, prefix: str, provider_id: str, port: int
    ) -> dict[str, object]:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": self._provider_service_name(prefix, provider_id),
                "namespace": self._namespace,
            },
            "spec": {
                "selector": {
                    "aero-bench/run": prefix,
                    "aero-bench/workload": self._resource_suffix(provider_id),
                },
                "ports": [{"name": "provider", "port": port, "targetPort": port}],
            },
        }

    def _network_policies(
        self,
        run: ResolvedRunSpec,
        prefix: str,
    ) -> tuple[dict[str, object], ...]:
        labels = {"aero-bench/run": prefix}
        agent_labels = {**labels, "aero-bench/role": "agent"}
        harness_labels = {**labels, "aero-bench/role": "harness"}
        provider_labels = {**labels, "aero-bench/role": "provider"}
        provider_ports = [
            {"protocol": "TCP", "port": provider.port}
            for provider in run.environment.providers
        ]
        return (
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": f"{prefix}-default-deny",
                    "namespace": self._namespace,
                },
                "spec": {
                    "podSelector": {"matchLabels": labels},
                    "policyTypes": ["Ingress", "Egress"],
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": f"{prefix}-dns", "namespace": self._namespace},
                "spec": {
                    "podSelector": {
                        "matchExpressions": [
                            {
                                "key": "aero-bench/role",
                                "operator": "In",
                                "values": ["agent", "harness", "provider"],
                            }
                        ]
                    },
                    "policyTypes": ["Egress"],
                    "egress": [
                        {
                            "to": [
                                {
                                    "namespaceSelector": {
                                        "matchLabels": {
                                            "kubernetes.io/metadata.name": self._dns_namespace
                                        }
                                    },
                                    "podSelector": {
                                        "matchLabels": self._dns_pod_labels
                                    },
                                }
                            ],
                            "ports": [
                                {"protocol": "UDP", "port": 53},
                                {"protocol": "TCP", "port": 53},
                            ],
                        }
                    ],
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": f"{prefix}-agent-gateway",
                    "namespace": self._namespace,
                },
                "spec": {
                    "podSelector": {"matchLabels": agent_labels},
                    "policyTypes": ["Egress"],
                    "egress": [
                        {
                            "to": [{"podSelector": {"matchLabels": harness_labels}}],
                            "ports": [
                                {
                                    "protocol": "TCP",
                                    "port": run.environment.gateway.port,
                                }
                            ],
                        }
                    ],
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": f"{prefix}-gateway-ingress",
                    "namespace": self._namespace,
                },
                "spec": {
                    "podSelector": {"matchLabels": harness_labels},
                    "policyTypes": ["Ingress"],
                    "ingress": [
                        {
                            "from": [{"podSelector": {"matchLabels": agent_labels}}],
                            "ports": [
                                {
                                    "protocol": "TCP",
                                    "port": run.environment.gateway.port,
                                }
                            ],
                        }
                    ],
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": f"{prefix}-harness-provider",
                    "namespace": self._namespace,
                },
                "spec": {
                    "podSelector": {"matchLabels": harness_labels},
                    "policyTypes": ["Egress"],
                    "egress": [
                        {
                            "to": [{"podSelector": {"matchLabels": provider_labels}}],
                            "ports": provider_ports,
                        }
                    ],
                },
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {
                    "name": f"{prefix}-provider-ingress",
                    "namespace": self._namespace,
                },
                "spec": {
                    "podSelector": {"matchLabels": provider_labels},
                    "policyTypes": ["Ingress"],
                    "ingress": [
                        {
                            "from": [{"podSelector": {"matchLabels": harness_labels}}],
                            "ports": provider_ports,
                        }
                    ],
                },
            },
        )

    def _labels(self, prefix: str, workload: WorkloadPlan) -> dict[str, str]:
        return {
            "aero-bench/run": prefix,
            "aero-bench/role": workload.role,
            "aero-bench/phase": workload.phase,
            "aero-bench/workload": self._resource_suffix(workload.workload_id),
        }

    @classmethod
    def _workload_name(cls, prefix: str, workload_id: str) -> str:
        return f"{prefix}-{cls._resource_suffix(workload_id)}"[:63].rstrip("-")

    @classmethod
    def _input_config_map_name(cls, prefix: str, workload: WorkloadPlan) -> str:
        return f"{cls._workload_name(prefix, workload.workload_id)[:57]}-input"[
            :63
        ].rstrip("-")

    @classmethod
    def _artifact_claim_name(cls, prefix: str, workload: WorkloadPlan) -> str:
        return f"{cls._workload_name(prefix, workload.workload_id)[:53]}-artifacts"[
            :63
        ].rstrip("-")

    @staticmethod
    def _seal_claim_name(prefix: str) -> str:
        return f"{prefix}-sealed"

    @classmethod
    def _provider_service_name(cls, prefix: str, provider_id: str) -> str:
        return f"{prefix}-{cls._resource_suffix(provider_id)[:39]}-provider"[
            :63
        ].rstrip("-")

    @staticmethod
    def _resource_suffix(workload_id: str) -> str:
        normalized = "".join(
            character if character.isalnum() or character == "-" else "-"
            for character in workload_id
        ).strip("-")
        if not normalized:
            raise ValueError("workload_id cannot materialize to an empty name")
        suffix = hashlib.sha256(workload_id.encode("utf-8")).hexdigest()[:8]
        return f"{normalized[:30].rstrip('-')}-{suffix}"

    @classmethod
    def _container_name(cls, workload_id: str) -> str:
        return cls._resource_suffix(workload_id)


class KubernetesExecutor:
    """Deferred executor that blocks until a real Kubernetes API context exists."""

    def __init__(
        self,
        *,
        kubectl_binary: str,
        task_package_resolvers: tuple[TaskPackageResolver, ...],
        provider_registry: ProviderRegistry,
    ):
        if not kubectl_binary:
            raise ValueError("kubectl_binary must be explicit")
        self._kubectl_binary = kubectl_binary
        if not task_package_resolvers:
            raise ValueError("task_package_resolvers must be explicit and non-empty")
        self._task_package_resolvers = tuple(task_package_resolvers)
        self._provider_registry = provider_registry

    def materialize(
        self, run: ResolvedRunSpec, *, bundle_root: str | Path
    ) -> ExecutionPlan:
        return build_execution_plan(
            run,
            executor_kind="kubernetes_cluster",
            bundle_root=bundle_root,
            task_package_resolvers=self._task_package_resolvers,
            provider_registry=self._provider_registry,
        )

    def preflight(self, plan: ExecutionPlan) -> PreflightReport:
        blockers: list[PreflightBlocker] = [
            PreflightBlocker(
                code="kubernetes.execution.deferred",
                detail=(
                    "cluster execution remains disabled until namespace RBAC, CNI "
                    "NetworkPolicy enforcement, storage, GPU, registry access, and "
                    "sealed-artifact transport are validated against a real cluster"
                ),
            )
        ]
        if any(item.role == "agent_driver" for item in plan.runtime_workloads):
            blockers.append(PreflightBlocker(code="kubernetes.agent-driver.deferred", detail="model driver credentials and inference egress are not materialized by the deferred cluster executor"))
        if plan.executor_kind != "kubernetes_cluster":
            blockers.append(
                PreflightBlocker(
                    code="executor.mismatch",
                    detail="KubernetesExecutor accepts only kubernetes_cluster plans",
                )
            )
        else:
            try:
                canonical = build_execution_plan(
                    plan.run,
                    executor_kind="kubernetes_cluster",
                    bundle_root=plan.bundle_root,
                    task_package_resolvers=self._task_package_resolvers,
                    provider_registry=self._provider_registry,
                )
                if canonical != plan:
                    raise ValueError(
                        "ExecutionPlan is not the unique materialization of its "
                        "ResolvedRun"
                    )
            except (OSError, ValueError) as error:
                blockers.append(
                    PreflightBlocker(
                        code="execution-plan.invalid",
                        detail=str(error),
                    )
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
        binary = shutil.which(self._kubectl_binary)
        if binary is None:
            blockers.append(
                PreflightBlocker(
                    code="kubectl.unavailable", detail="kubectl binary was not found"
                )
            )
        else:
            try:
                version = subprocess.run(
                    (binary, "version", "--client=true", "--output=json"),
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
                try:
                    version_payload = (
                        json.loads(version.stdout) if version.returncode == 0 else None
                    )
                except json.JSONDecodeError:
                    version_payload = None
                if not isinstance(version_payload, dict) or not isinstance(
                    version_payload.get("clientVersion"), dict
                ):
                    blockers.append(
                        PreflightBlocker(
                            code="kubectl.invalid-client",
                            detail=version.stderr.strip()
                            or "kubectl did not return a valid client version payload",
                        )
                    )
                result = subprocess.run(
                    (binary, "config", "current-context"),
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                blockers.append(
                    PreflightBlocker(
                        code="kubectl.timeout",
                        detail="kubectl did not respond within the explicit timeout",
                    )
                )
                result = None
            if result is not None and (
                result.returncode != 0 or not result.stdout.strip()
            ):
                blockers.append(
                    PreflightBlocker(
                        code="kubernetes.context.unavailable",
                        detail=result.stderr.strip()
                        or "current Kubernetes context is not set",
                    )
                )
        return PreflightReport(
            run_id=plan.run.run_id,
            executor_kind="kubernetes_cluster",
            execution_scope=plan.run.execution_scope,
            ready=not blockers,
            blockers=tuple(blockers),
        )

    @staticmethod
    def _deferred() -> NoReturn:
        raise RuntimeError(
            "kubernetes execution deferred: cluster submission is not enabled"
        )

    def start_runtime(self, plan: ExecutionPlan) -> object:
        self._deferred()

    def wait_runtime(self, handle: object, *, timeout_seconds: int) -> None:
        self._deferred()

    def collect_failure_outputs(
        self, plan: ExecutionPlan, handle: object, *, destination_root: Path
    ) -> None:
        self._deferred()

    def collect_and_seal(
        self,
        plan: ExecutionPlan,
        handle: object,
        *,
        destination_root: Path,
    ) -> SealManifest:
        self._deferred()

    def start_verifier(self, plan: ExecutionPlan, seal: SealManifest) -> object:
        self._deferred()

    def wait_verifier(self, handle: object, *, timeout_seconds: int) -> None:
        self._deferred()

    def collect_verification_outputs(
        self,
        plan: ExecutionPlan,
        handle: object,
        *,
        destination_root: Path,
    ) -> ValidatedVerificationOutput:
        self._deferred()

    def cleanup(self, handle: object) -> None:
        self._deferred()

    def cleanup_run(self, run_id: str) -> None:
        self._deferred()
