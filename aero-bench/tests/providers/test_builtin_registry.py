from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tempfile import TemporaryDirectory
from tests.world.support import compile_scenario_fixture, deterministic_inspection_scenario

import pytest

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    ClockSpec,
    ArtifactRequirement,
    FileRef,
    ImplementationIdentity,
    ProviderRef,
    ResourceBudget,
    RuntimeImage,
    RuntimeSpec,
    SchemaBoundFile,
)
from aero_bench.providers import builtin_provider_registry
from aero_bench.providers.inspection_business import InspectionBusinessProvider
from aero_bench.providers.ns3 import NETWORK_MODEL, Ns3Provider, ns3_capabilities
from aero_bench.providers.registry import ProviderRegistryError, RuntimeEndpoint
from aero_bench.providers.sumo import SumoProvider
from aero_bench.world.resolved import ResolvedProvider, ResolvedScenario
from tests.test_inspection_business import _package


def _write_json(root: Path, name: str, value: object) -> FileRef:
    path = root / name
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(encoded)
    return FileRef(path=name, sha256=hashlib.sha256(encoded).hexdigest())


def _provider_bundle(
    root: Path,
    *,
    adapter: str,
    provider_id: str,
    port: int,
) -> tuple[BundleReader, ProviderRef]:
    root.mkdir(parents=True, exist_ok=True)
    image = f"registry.test/{provider_id}@sha256:" + "1" * 64
    schema_ref = _write_json(root, f"{provider_id}.schema.json", {"type": "object"})
    if adapter == "ns3.rpc":
        raw_config: dict[str, object] = {
            "schema_version": "aero-bench.ns3/v3",
            "provider_id": provider_id,
            "ns3": {"version": "3.48", "commit": "b" * 40},
            "network_model": NETWORK_MODEL,
            "supported_wifi_standards": ["802.11ax"],
            "required_commands": ["ns3"],
            "command_timeout_ms": 1000,
        }
    elif adapter == "inspection.business":
        raw_config = {
            "schema_version": "aero-bench.inspection-business/v1",
            "provider_id": provider_id,
            "task_package": _package().model_dump(mode="json"),
        }
    else:
        scenario = {"path": "scenario.sumocfg", "sha256": "c" * 64}
        raw_config = {
            "schema_version": "aero-bench.sumo/v2",
            "provider_id": provider_id,
            "sumo": {"version": "1.27.1", "commit": "b" * 40},
            "sumo_binary": "sumo",
            "traci_port": port + 1000,
            "step_length_ns": 100,
            "sumo_args": ["--no-step-log", "true"],
            "required_commands": ["sumo"],
            "command_timeout_ms": 1000,
        }
    config_ref = _write_json(root, f"{provider_id}.json", raw_config)
    implementation = ImplementationIdentity(
        component_id=adapter,
        kind="mechanical_fixture",
        source_uri="https://github.com/aero-bench/provider-fixture",
        source_revision="a" * 40,
        version="fixture-1",
    )
    provider = ProviderRef(
        provider_id=provider_id,
        adapter=adapter,
        port=port,
        workload=RuntimeSpec(
            runtime=RuntimeImage(image=image, command=("provider",)),
            resources=ResourceBudget(cpu_millicores=100, memory_mib=64, gpu_count=0),
            implementation=implementation,
        ),
        config=SchemaBoundFile(file=config_ref, schema_file=schema_ref),
        protocol_schema=FileRef(path=f"{provider_id}.protocol.json", sha256="d" * 64),
        capabilities=(
            ns3_capabilities(("802.11ax",))
            if adapter == "ns3.rpc"
            else (f"{provider_id}.state",)
        ),
        artifact_requirements=(
            (ArtifactRequirement(
                artifact_id="artifact.sumo.traffic", artifact_type="sumo.traffic.evidence",
                producer_id=provider_id, visibility="private", relative_path="traffic/evidence.jsonl",
                max_size_bytes=65_536, source_asset_id=None,
            ),) if adapter == "sumo.traci" else ()
        ),
    )
    return BundleReader(root), provider


def _scenario_for(provider: ProviderRef) -> ResolvedScenario:
    role = {
        "ns3.rpc": "wireless_network",
        "sumo.traci": "traffic",
        "inspection.business": "sensor",
    }.get(provider.adapter, "sensor")
    if provider.adapter == "inspection.business":
        base = deterministic_inspection_scenario()
    else:
        with TemporaryDirectory(prefix="aero-registry-world-") as temporary:
            base = compile_scenario_fixture(Path(temporary))
    projection = ResolvedProvider(
        provider_id=provider.provider_id,
        runtime_stage={"ns3.rpc": "network", "sumo.traci": "motion",
                       "inspection.business": "business_environment",
                       "unknown.provider": "business_environment"}[provider.adapter],
        roles=(role,), capability_ids=provider.capabilities,
    )
    updates = {"providers": (projection,)}
    if provider.adapter == "ns3.rpc":
        updates["network"] = base.network.model_copy(update={"provider_id": provider.provider_id})
    return base.model_copy(update=updates)



def _clock() -> ClockSpec:
    return ClockSpec(
        authority="provider_barrier",
        step_ns=100,
        max_steps=10,
        provider_timeout_ms=1_000,
    )


@pytest.mark.parametrize(
    ("adapter", "provider_id", "port", "provider_type"),
    (
        ("ns3.rpc", "network", 17433, Ns3Provider),
        ("sumo.traci", "traffic", 17434, SumoProvider),
        ("inspection.business", "business", 17435, InspectionBusinessProvider),
    ),
)
def test_builtin_registry_uses_materializer_endpoint_without_rewriting_config(
    tmp_path: Path,
    adapter: str,
    provider_id: str,
    port: int,
    provider_type: type[object],
) -> None:
    bundle, provider = _provider_bundle(
        tmp_path, adapter=adapter, provider_id=provider_id, port=port
    )
    config_digest = provider.config.file.sha256
    runtime_endpoint = RuntimeEndpoint(host=f"runtime-{provider_id}", port=port)

    session = builtin_provider_registry().build_session(
        provider=provider,
        bundle=bundle,
        runtime_endpoint=runtime_endpoint,
        run_id="e" * 64,
        credential="f" * 64,
        scenario=_scenario_for(provider),
        clock=_clock(),
    )

    assert isinstance(session, provider_type)
    assert session.manifest.config_digest == config_digest
    assert session.manifest.runtime_image == provider.workload.runtime.image
    assert session._runtime_endpoint == runtime_endpoint
    assert session._session_token == "f" * 64
    assert "endpoint" not in type(session._config).model_fields
    assert "runtime_image" not in type(session._config).model_fields
    assert "protocol_version" not in type(session._config).model_fields


def test_builtin_registry_rejects_invalid_provider_credentials(tmp_path: Path) -> None:
    bundle, provider = _provider_bundle(
        tmp_path, adapter="inspection.business", provider_id="business", port=17435
    )
    for credential in ("nothex", "F" * 64, "0" * 64, "f" * 63):
        with pytest.raises(ProviderRegistryError, match="provider credential"):
            builtin_provider_registry().build_session(
                provider=provider,
                bundle=bundle,
                runtime_endpoint=RuntimeEndpoint(host="runtime-business", port=17435),
                run_id="e" * 64,
                credential=credential,
                scenario=_scenario_for(provider),
                clock=_clock(),
            )


def test_builtin_registry_fails_closed_for_unknown_adapter(tmp_path: Path) -> None:
    bundle, provider = _provider_bundle(
        tmp_path, adapter="unknown.provider", provider_id="unknown", port=17435
    )
    with pytest.raises(ProviderRegistryError, match="no Provider adapter"):
        builtin_provider_registry().build_session(
            provider=provider,
            bundle=bundle,
            runtime_endpoint=RuntimeEndpoint(host="runtime-unknown", port=17435),
            run_id="e" * 64,
            credential="f" * 64,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )
