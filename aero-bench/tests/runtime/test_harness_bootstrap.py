from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from collections.abc import Callable

import pytest

from aero_bench.executor.contracts import HarnessWorkloadContract
from aero_bench.providers import ProviderManifest
from aero_bench.runtime.bootstrap import (
    HarnessBootstrapError,
    bootstrap_harness,
)
from aero_bench.runtime.contracts import SimulationTime, StepReceipt
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.registry import builtin_task_package_resolvers
from tests.support import build_bundle, fake_finalization_receipt, resolve_bundle
from tests.support import fixture_provider_registry


class _Session:
    def __init__(self, manifest: ProviderManifest, run_id: str) -> None:
        self.manifest = manifest
        self._run_id = run_id

    async def prepare(self) -> None:
        return None

    async def reset(self, *, seed: int) -> StepReceipt:
        return StepReceipt(
            run_id=self._run_id,
            provider_id=self.manifest.provider_id,
            reached=SimulationTime(tick=0, sim_time_ns=0),
            state_digest=hashlib.sha256(str(seed).encode()).hexdigest(),
        )

    async def step_to(self, request) -> StepReceipt:
        return StepReceipt(
            run_id=self._run_id,
            provider_id=self.manifest.provider_id,
            reached=request.target,
            state_digest=hashlib.sha256(
                f"{self.manifest.provider_id}:{request.target.tick}".encode()
            ).hexdigest(),
        )

    async def handle_command(self, request):
        return ()

    async def finalize(self, request):
        return fake_finalization_receipt(self.manifest, request)

    async def snapshot_digest(self) -> str:
        return hashlib.sha256(b"snapshot").hexdigest()

    async def shutdown(self) -> None:
        return None


class _Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object, str]] = []

    def verify_runtime_stage_binding(self, **bindings):
        fixture_provider_registry().verify_runtime_stage_binding(**bindings)

    def build_session(
        self, *, provider, bundle, runtime_endpoint, run_id, credential, scenario, clock
    ):
        self.calls.append(
            (provider.provider_id, bundle, runtime_endpoint, run_id, credential)
        )
        return _Session(
            ProviderManifest(
                provider_id=provider.provider_id,
                adapter=provider.adapter,
                implementation=provider.workload.implementation,
                runtime_image=provider.workload.runtime.image,
                config_digest=provider.config.file.sha256,
                capabilities=provider.capabilities,
                protocol_schema=provider.protocol_schema,
                artifact_requirements=provider.artifact_requirements,
            ),
            run_id,
        )


def test_provider_contracts_import_without_bootstrap_cycle() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from aero_bench.providers.contracts import ProviderManifest",
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _environment(
    root: Path, *, contract_path: Path, artifact_root: Path
) -> dict[str, str]:
    run = resolve_bundle(root / "suite.yaml", executor_kind="docker_reference")[0]
    endpoints = {
        provider.provider_id: {
            "host": f"{provider.provider_id}.svc",
            "port": provider.port,
        }
        for provider in run.environment.providers
    }
    return {
        "AERO_BENCH_CONTRACT": str(contract_path),
        "AERO_BENCH_BUNDLE_DIR": str(root),
        "AERO_BENCH_ARTIFACT_DIR": str(artifact_root),
        "AERO_BENCH_RUN_ID": run.run_id,
        "AERO_BENCH_SEED": str(run.seed),
        "AERO_BENCH_ROLE": "harness",
        "AERO_BENCH_WORKLOAD_ID": "harness",
        "AERO_BENCH_PROVIDER_ENDPOINTS": canonical_json_bytes(endpoints).decode(),
        "AERO_BENCH_PROVIDER_CREDENTIALS": canonical_json_bytes(
            {
                provider.provider_id: f"{index + 1:064x}"
                for index, provider in enumerate(run.environment.providers)
            }
        ).decode(),
    }


def _write_contract(root: Path, destination: Path) -> None:
    run = resolve_bundle(root / "suite.yaml", executor_kind="docker_reference")[0]
    contract = HarnessWorkloadContract.from_run(run)
    destination.write_bytes(
        canonical_json_bytes(contract.model_dump(mode="json")) + b"\n"
    )


def _ready_fixture(tmp_path: Path) -> tuple[dict[str, str], _Registry]:
    bundle = build_bundle(tmp_path / "bundle")
    contract_path = tmp_path / "contract.json"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    _write_contract(bundle.root, contract_path)
    return _environment(
        bundle.root,
        contract_path=contract_path,
        artifact_root=artifact_root,
    ), _Registry()


def _bootstrap(environment: dict[str, str], registry: _Registry):
    return bootstrap_harness(
        environment,
        registry=registry,
        task_package_resolvers=builtin_task_package_resolvers(),
    )


def _rewrite_run_contract(
    environment: dict[str, str], mutate: Callable[[dict[str, object]], None]
) -> None:
    contract_path = Path(environment["AERO_BENCH_CONTRACT"])
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    run = contract["run"]
    mutate(run)
    payload = {key: value for key, value in run.items() if key != "run_id"}
    run["run_id"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    contract_path.write_bytes(canonical_json_bytes(contract) + b"\n")
    environment["AERO_BENCH_RUN_ID"] = run["run_id"]


def test_bootstrap_requires_explicit_task_package_resolvers(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)

    with pytest.raises(HarnessBootstrapError, match="non-empty"):
        bootstrap_harness(
            environment,
            registry=registry,
            task_package_resolvers=(),
        )

    assert registry.calls == []


def test_bootstrap_builds_all_sessions_without_advancing_harness(
    tmp_path: Path,
) -> None:
    environment, registry = _ready_fixture(tmp_path)

    result = _bootstrap(environment, registry)

    assert result.run.run_id == environment["AERO_BENCH_RUN_ID"]
    assert result.artifact_root == Path(environment["AERO_BENCH_ARTIFACT_DIR"])
    assert result.ledger.records == ()
    assert result.coordinator.current == SimulationTime(tick=0, sim_time_ns=0)
    assert [call[0] for call in registry.calls] == [
        "business",
        "flight",
        "network",
        "observation",
        "traffic",
        "world",
    ]
    credentials = json.loads(environment["AERO_BENCH_PROVIDER_CREDENTIALS"])
    assert {call[0]: call[4] for call in registry.calls} == credentials


def test_bootstrap_rejects_missing_provider_credentials(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    del environment["AERO_BENCH_PROVIDER_CREDENTIALS"]

    with pytest.raises(HarnessBootstrapError, match="missing required environment"):
        _bootstrap(environment, registry)

    assert registry.calls == []


def _rewrite_credentials(
    environment: dict[str, str],
    credentials: dict[str, object],
    *,
    canonical: bool = True,
) -> None:
    if canonical:
        environment["AERO_BENCH_PROVIDER_CREDENTIALS"] = canonical_json_bytes(
            credentials
        ).decode()
    else:
        environment["AERO_BENCH_PROVIDER_CREDENTIALS"] = json.dumps(
            credentials, indent=2
        )


def test_bootstrap_rejects_invalid_provider_credential_maps(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    base = json.loads(environment["AERO_BENCH_PROVIDER_CREDENTIALS"])
    expected_providers = set(base)

    cases: list[dict[str, object]] = [
        {**base, "business": "0" * 64},
        {**base, "business": "F" * 64},
        {**base, "business": "f" * 63},
        {key: base["business"] for key in expected_providers},
        {key: base[key] for key in expected_providers if key != "flight"},
        {**base, "unknown": "f" * 64},
    ]
    for index, credentials in enumerate(cases):
        environment, registry = _ready_fixture(tmp_path / f"case-{index}")
        _rewrite_credentials(environment, credentials)
        with pytest.raises(HarnessBootstrapError, match="credential"):
            _bootstrap(environment, registry)
        assert registry.calls == []

    environment, registry = _ready_fixture(tmp_path / "noncanonical")
    _rewrite_credentials(environment, base, canonical=False)
    with pytest.raises(HarnessBootstrapError, match="canonical"):
        _bootstrap(environment, registry)
    assert registry.calls == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AERO_BENCH_RUN_ID", "f" * 64),
        ("AERO_BENCH_SEED", "08"),
        ("AERO_BENCH_ROLE", "provider"),
        ("AERO_BENCH_WORKLOAD_ID", "flight"),
    ],
)
def test_bootstrap_rejects_process_identity_mismatch(
    tmp_path: Path, key: str, value: str
) -> None:
    environment, registry = _ready_fixture(tmp_path)
    environment[key] = value

    with pytest.raises(HarnessBootstrapError):
        _bootstrap(environment, registry)


def test_bootstrap_rejects_missing_provider_endpoint(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    endpoints = json.loads(environment["AERO_BENCH_PROVIDER_ENDPOINTS"])
    endpoints.pop("flight")
    environment["AERO_BENCH_PROVIDER_ENDPOINTS"] = canonical_json_bytes(
        endpoints
    ).decode()

    with pytest.raises(HarnessBootstrapError, match="missing"):
        _bootstrap(environment, registry)


def test_bootstrap_rejects_unknown_provider_endpoint(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    endpoints = json.loads(environment["AERO_BENCH_PROVIDER_ENDPOINTS"])
    endpoints["unknown"] = {"host": "unknown.svc", "port": 18000}
    environment["AERO_BENCH_PROVIDER_ENDPOINTS"] = canonical_json_bytes(
        endpoints
    ).decode()

    with pytest.raises(HarnessBootstrapError, match="unknown"):
        _bootstrap(environment, registry)


def test_bootstrap_rejects_duplicate_endpoint_and_port_mismatch(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    endpoints = json.loads(environment["AERO_BENCH_PROVIDER_ENDPOINTS"])
    endpoints["network"] = endpoints["flight"]
    environment["AERO_BENCH_PROVIDER_ENDPOINTS"] = canonical_json_bytes(
        endpoints
    ).decode()
    with pytest.raises(HarnessBootstrapError, match="duplicate endpoints"):
        _bootstrap(environment, registry)

    environment, registry = _ready_fixture(tmp_path / "port")
    endpoints = json.loads(environment["AERO_BENCH_PROVIDER_ENDPOINTS"])
    endpoints["flight"]["port"] += 1
    environment["AERO_BENCH_PROVIDER_ENDPOINTS"] = canonical_json_bytes(
        endpoints
    ).decode()
    with pytest.raises(HarnessBootstrapError, match="port"):
        _bootstrap(environment, registry)


def test_bootstrap_rejects_duplicate_endpoint_json_key(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    environment["AERO_BENCH_PROVIDER_ENDPOINTS"] = (
        '{"business":{"host":"business.svc","port":17603},'
        '"business":{"host":"other.svc","port":17604},'
        '"flight":{"host":"flight.svc","port":17601},'
        '"network":{"host":"network.svc","port":17602},'
        '"observation":{"host":"observation.svc","port":17604}}'
    )

    with pytest.raises(HarnessBootstrapError, match="strict JSON"):
        _bootstrap(environment, registry)


def test_bootstrap_rejects_noncanonical_contract(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    contract_path = Path(environment["AERO_BENCH_CONTRACT"])
    value = json.loads(contract_path.read_text(encoding="utf-8"))
    contract_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(HarnessBootstrapError, match="canonical JSON"):
        _bootstrap(environment, registry)


def test_bootstrap_rejects_nonempty_or_symlink_artifact_root(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    artifact_root = Path(environment["AERO_BENCH_ARTIFACT_DIR"])
    (artifact_root / "stale").write_text("stale", encoding="utf-8")
    with pytest.raises(HarnessBootstrapError, match="empty"):
        _bootstrap(environment, registry)

    environment, registry = _ready_fixture(tmp_path / "symlink")
    artifact_root = Path(environment["AERO_BENCH_ARTIFACT_DIR"])
    target = artifact_root.parent / "artifact-target"
    target.mkdir()
    artifact_root.rmdir()
    artifact_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(HarnessBootstrapError, match="symbolic link"):
        _bootstrap(environment, registry)


def test_bootstrap_rejects_path_traversal(tmp_path: Path) -> None:
    environment, registry = _ready_fixture(tmp_path)
    environment["AERO_BENCH_BUNDLE_DIR"] = str(
        Path(environment["AERO_BENCH_BUNDLE_DIR"]) / ".." / "bundle"
    )

    with pytest.raises(HarnessBootstrapError, match="traversal"):
        _bootstrap(environment, registry)


@pytest.mark.parametrize("tamper", ["task_package", "feasibility"])
def test_bootstrap_revalidates_every_resolved_run_input(
    tmp_path: Path, tamper: str
) -> None:
    environment, registry = _ready_fixture(tmp_path)
    unrelated = Path(environment["AERO_BENCH_BUNDLE_DIR"]) / "configs/provider.json"
    unrelated_ref = {
        "path": "configs/provider.json",
        "sha256": hashlib.sha256(unrelated.read_bytes()).hexdigest(),
    }

    def mutate(run: dict[str, object]) -> None:
        if tamper == "task_package":
            run["task"]["package"]["config"]["file"] = unrelated_ref
        else:
            run["feasibility"]["bounds"][0]["value"] += 1

    _rewrite_run_contract(environment, mutate)

    with pytest.raises(HarnessBootstrapError, match="revalidation|HarnessWorkloadContract"):
        _bootstrap(environment, registry)

    assert registry.calls == []
