"""Builtin-registry registration tests for the ``logistics.business`` adapter.

These tests resolve the adapter and materialize the real
:class:`LogisticsBusinessProvider` through the production ``builtin_provider_registry()``
path — never through a direct class constructor as a substitute for the
registry contract. The fixtures are synthetic single-package documents; this
file never starts a precedence/formal run.

The registration may only supply the two non-physical business capabilities
(``logistics.orders.authority`` and ``logistics.facilities.state``). Declaring the
pending capabilities (``logistics.airspace.events``,
``logistics.delivery.observation``) or any flight capability in a ProviderRef
must fail at materialization on the real registry path, and the logistics
config must never be accepted by another adapter (inspection.business is not
aliased as logistics).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    ArtifactRequirement,
    ClockSpec,
    FileRef,
    ImplementationIdentity,
    ProviderRef,
    ResourceBudget,
    RuntimeImage,
    RuntimeSpec,
    SchemaBoundFile,
)
from aero_bench.providers import builtin_provider_registry
from aero_bench.providers.logistics_business import (
    LogisticsBusinessConfig,
    LogisticsBusinessProvider,
)
from aero_bench.providers.logistics_business.adapter import (
    LOGISTICS_BUSINESS_ADAPTER,
    SUPPORTED_CAPABILITIES,
)
from aero_bench.providers.registry import ProviderRegistryError, RuntimeEndpoint
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_BUSINESS_CAPABILITIES,
    LOGISTICS_FLIGHT_CAPABILITIES,
)
from aero_bench.world.resolved import ResolvedScenario
from containers.selfcheck_scenario import ScenarioProvider, build_resolved_scenario
from tests.providers.test_logistics_business_service import (
    CAPABILITIES,
    IMAGE,
    PROVIDER_ID,
    RUN_ID,
    SEED,
    SESSION_TOKEN,
    _config_document,
    _requirement,
)
from tests.tasks.test_logistics_package import _package_document

LOGISTICS_BUSINESS_PORT = 18436


def _clock() -> ClockSpec:
    return ClockSpec(
        authority="provider_barrier",
        step_ns=1_000_000_000,
        max_steps=2,
        provider_timeout_ms=10_000,
    )


def _write_json(root: Path, name: str, value: object) -> FileRef:
    root.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json_bytes(value) + b"\n"
    path = root / name
    path.write_bytes(encoded)
    return FileRef(path=name, sha256=hashlib.sha256(encoded).hexdigest())


def _scenario_for(capabilities: tuple[str, ...]) -> ResolvedScenario:
    raw = build_resolved_scenario(
        seed=SEED,
        providers=(
            ScenarioProvider(PROVIDER_ID, ("mission",), capabilities, "business_environment"),
            ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
        ),
        dynamic_provider_id="flight",
        verifier_id="logistics.verifier",
        world_id="world.city-demo",
    )
    return ResolvedScenario.model_validate(raw)


def _provider_bundle(
    tmp_path: Path,
    *,
    adapter: str = LOGISTICS_BUSINESS_ADAPTER,
    provider_id: str = PROVIDER_ID,
    port: int = LOGISTICS_BUSINESS_PORT,
    capabilities: tuple[str, ...] = CAPABILITIES,
    config_document: dict[str, object] | None = None,
    config_provider_id: str | None = None,
) -> tuple[BundleReader, ProviderRef]:
    schema_ref = _write_json(tmp_path, f"{provider_id}.schema.json", {"type": "object"})
    document = (
        dict(config_document) if config_document is not None else _config_document()
    )
    if config_provider_id is not None:
        document["provider_id"] = config_provider_id
    config_ref = _write_json(tmp_path, f"{provider_id}.json", document)
    requirement = ArtifactRequirement.model_validate(
        {**_requirement(), "producer_id": provider_id}
    )
    implementation = ImplementationIdentity(
        component_id=adapter,
        kind="mechanical_fixture",
        source_uri="https://github.com/moby/moby",
        source_revision="4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
        version="test-fixture-1",
    )
    provider = ProviderRef(
        provider_id=provider_id,
        adapter=adapter,
        port=port,
        workload=RuntimeSpec(
            runtime=RuntimeImage(image=IMAGE, command=("provider", "serve")),
            resources=ResourceBudget(cpu_millicores=100, memory_mib=64, gpu_count=0),
            implementation=implementation,
        ),
        config=SchemaBoundFile(file=config_ref, schema_file=schema_ref),
        protocol_schema=FileRef(path=f"{provider_id}.protocol.json", sha256="d" * 64),
        capabilities=capabilities,
        artifact_requirements=(requirement,),
    )
    return BundleReader(tmp_path), provider


def test_logistics_business_is_a_registered_builtin_adapter() -> None:
    adapters = builtin_provider_registry().adapters
    assert LOGISTICS_BUSINESS_ADAPTER in adapters
    assert LOGISTICS_BUSINESS_ADAPTER == "logistics.business"
    # The adapter never supplies pending or flight capabilities.
    assert LOGISTICS_BUSINESS_CAPABILITIES - SUPPORTED_CAPABILITIES == {
        "logistics.airspace.events",
        "logistics.delivery.observation",
    }
    assert SUPPORTED_CAPABILITIES.isdisjoint(LOGISTICS_FLIGHT_CAPABILITIES)
    assert SUPPORTED_CAPABILITIES == {
        "logistics.facilities.state", "logistics.orders.authority", "logistics.orders.scheduled-arrivals",
    }


def test_logistics_business_resolves_through_real_registry(tmp_path: Path) -> None:
    bundle, provider = _provider_bundle(tmp_path)
    config_digest = provider.config.file.sha256
    requirement = ArtifactRequirement.model_validate(_requirement())
    runtime_endpoint = RuntimeEndpoint(
        host=f"runtime-{PROVIDER_ID}", port=LOGISTICS_BUSINESS_PORT
    )

    session = builtin_provider_registry().build_session(
        provider=provider,
        bundle=bundle,
        runtime_endpoint=runtime_endpoint,
        run_id=RUN_ID,
        credential=SESSION_TOKEN,
        scenario=_scenario_for(CAPABILITIES),
        clock=_clock(),
    )

    assert isinstance(session, LogisticsBusinessProvider)
    assert session.manifest.provider_id == PROVIDER_ID
    assert session.manifest.adapter == LOGISTICS_BUSINESS_ADAPTER
    assert session.manifest.runtime_image == IMAGE
    assert session.manifest.config_digest == config_digest
    assert session.manifest.capabilities == CAPABILITIES
    assert session.manifest.artifact_requirements == (requirement,)
    # The materializer-owned endpoint and executor-issued session token are
    # passed straight through to the real session; they never enter the config.
    assert session._runtime_endpoint == runtime_endpoint
    assert session._session_token == SESSION_TOKEN
    assert isinstance(session._config, LogisticsBusinessConfig)
    assert "endpoint" not in type(session._config).model_fields
    assert "runtime_image" not in type(session._config).model_fields
    assert "protocol_version" not in type(session._config).model_fields
    assert "capabilities" not in type(session._config).model_fields


def test_logistics_business_accepts_a_supported_capability_subset(
    tmp_path: Path,
) -> None:
    subset = ("logistics.orders.authority",)
    bundle, provider = _provider_bundle(tmp_path, capabilities=subset)
    session = builtin_provider_registry().build_session(
        provider=provider,
        bundle=bundle,
        runtime_endpoint=RuntimeEndpoint(
            host=f"runtime-{PROVIDER_ID}", port=LOGISTICS_BUSINESS_PORT
        ),
        run_id=RUN_ID,
        credential=SESSION_TOKEN,
        scenario=_scenario_for(subset),
        clock=_clock(),
    )
    assert isinstance(session, LogisticsBusinessProvider)
    assert session.manifest.capabilities == subset


@pytest.mark.parametrize(
    "pending_capability",
    (
        "logistics.airspace.events",
        "logistics.delivery.observation",
        "flight.command",
        "observation.capture",
    ),
)
def test_logistics_business_rejects_pending_capability_claim(
    tmp_path: Path,
    pending_capability: str,
) -> None:
    capabilities = tuple(sorted((*CAPABILITIES, pending_capability)))
    root = tmp_path / pending_capability.replace(".", "_")
    bundle, provider = _provider_bundle(
        root, capabilities=capabilities, port=LOGISTICS_BUSINESS_PORT + 1
    )

    with pytest.raises(
        ProviderRegistryError, match="Provider session builder failed"
    ) as exc:
        builtin_provider_registry().build_session(
            provider=provider,
            bundle=bundle,
            runtime_endpoint=RuntimeEndpoint(
                host=f"runtime-{PROVIDER_ID}", port=LOGISTICS_BUSINESS_PORT + 1
            ),
            run_id=RUN_ID,
            credential=SESSION_TOKEN,
            scenario=_scenario_for(capabilities),
            clock=_clock(),
        )
    assert isinstance(exc.value.__cause__, ProviderRegistryError)
    assert "does not support claimed capability ids" in str(exc.value.__cause__)
    assert pending_capability in str(exc.value.__cause__)


def test_logistics_config_is_not_aliased_as_inspection_business(
    tmp_path: Path,
) -> None:
    """The logistics config must not be accepted by the inspection adapter."""

    bundle, provider = _provider_bundle(tmp_path, adapter="inspection.business")

    with pytest.raises(ProviderRegistryError, match="does not match adapter"):
        builtin_provider_registry().build_session(
            provider=provider,
            bundle=bundle,
            runtime_endpoint=RuntimeEndpoint(
                host=f"runtime-{PROVIDER_ID}", port=LOGISTICS_BUSINESS_PORT
            ),
            run_id=RUN_ID,
            credential=SESSION_TOKEN,
            scenario=_scenario_for(CAPABILITIES),
            clock=_clock(),
        )


def test_logistics_business_rejects_config_provider_id_mismatch(
    tmp_path: Path,
) -> None:
    """A ProviderRef naming another provider id cannot receive this config."""

    wrong_provider_id = "other.logistics"
    bundle, provider = _provider_bundle(
        tmp_path, provider_id=wrong_provider_id, config_document=_config_document()
    )
    raw = build_resolved_scenario(
        seed=SEED,
        providers=(
            ScenarioProvider(wrong_provider_id, ("mission",), CAPABILITIES, "business_environment"),
            ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
        ),
        dynamic_provider_id="flight",
        verifier_id="logistics.verifier",
        world_id="world.city-demo",
    )
    scenario = ResolvedScenario.model_validate(raw)

    with pytest.raises(
        ProviderRegistryError, match="provider_id does not match ProviderRef"
    ):
        builtin_provider_registry().build_session(
            provider=provider,
            bundle=bundle,
            runtime_endpoint=RuntimeEndpoint(
                host=f"runtime-{wrong_provider_id}", port=LOGISTICS_BUSINESS_PORT
            ),
            run_id=RUN_ID,
            credential=SESSION_TOKEN,
            scenario=scenario,
            clock=_clock(),
        )


@pytest.mark.parametrize(
    "malformed",
    (
        {"unexpected": True},
        {"schema_version": "aero-bench.other/v1"},
        # A principal binding whose role drifts from the canonical grant.
        {
            "principal_bindings": [
                {
                    "principal_id": "agent.provider",
                    "actor_id": "fleet-alpha:1",
                    "role": "dispatcher",
                }
            ]
        },
    ),
)
def test_logistics_business_rejects_malformed_config(
    tmp_path: Path,
    malformed: dict[str, object],
) -> None:
    document = {**_config_document(), **malformed}
    bundle, provider = _provider_bundle(tmp_path, config_document=document)

    with pytest.raises(
        ProviderRegistryError, match="does not match adapter logistics.business"
    ):
        builtin_provider_registry().build_session(
            provider=provider,
            bundle=bundle,
            runtime_endpoint=RuntimeEndpoint(
                host=f"runtime-{PROVIDER_ID}", port=LOGISTICS_BUSINESS_PORT
            ),
            run_id=RUN_ID,
            credential=SESSION_TOKEN,
            scenario=_scenario_for(CAPABILITIES),
            clock=_clock(),
        )


def test_logistics_business_adapter_rejects_wrong_manifest_identity(
    tmp_path: Path,
) -> None:
    """Direct adapter unit check: the builder is bound to this adapter/provider."""

    from aero_bench.providers.contracts import ProviderManifest

    config = LogisticsBusinessConfig.model_validate(_config_document())
    manifest = ProviderManifest(
        provider_id=PROVIDER_ID,
        adapter="inspection.business",
        implementation=ImplementationIdentity(
            component_id="inspection.business",
            kind="mechanical_fixture",
            source_uri="https://github.com/moby/moby",
            source_revision="4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
            version="test-fixture-1",
        ),
        runtime_image=IMAGE,
        config_digest="b" * 64,
        capabilities=CAPABILITIES,
        protocol_schema=FileRef(path="protocol.json", sha256="f" * 64),
        artifact_requirements=(ArtifactRequirement.model_validate(_requirement()),),
    )
    from aero_bench.providers.logistics_business.adapter import (
        build_logistics_business_session,
    )

    with pytest.raises(ProviderRegistryError, match="requires the 'logistics.business'"):
        build_logistics_business_session(
            config=config,
            manifest=manifest,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-logistics.business", port=LOGISTICS_BUSINESS_PORT
            ),
            run_id=RUN_ID,
            credential=SESSION_TOKEN,
            scenario=_scenario_for(CAPABILITIES),
            clock=_clock(),
        )


def test_logistics_package_document_is_deterministic() -> None:
    """The registration relies on a deterministic strict package on the wire."""

    from aero_bench.tasks.logistics.contracts import lower_logistics_task_package

    lowered = lower_logistics_task_package(_package_document())
    assert lowered.canonical_digest() == lowered.canonical_digest()
    assert lowered.canonical_digest() != "0" * 64


__all__ = ["_clock", "_package_document", "_provider_bundle", "_scenario_for"]
