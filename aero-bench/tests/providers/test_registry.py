from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from aero_bench.config.models import ClockSpec
from aero_bench.world.resolved import ResolvedProvider, ResolvedScenario

import pytest
from pydantic import BaseModel, Field

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    FileRef,
    Identifier,
    ImplementationIdentity,
    ProviderRef,
    ResourceBudget,
    RuntimeImage,
    RuntimeSpec,
    SchemaBoundFile,
    StrictModel,
)
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.registry import (
    ProviderRegistration,
    ProviderRegistry,
    ProviderRegistryError,
    RuntimeEndpoint,
)


IMAGE = "registry.test/fake@sha256:" + "1" * 64
PROVIDER_PORT = 17432
RUN_ID = "a" * 64
CREDENTIAL = "f" * 64


class FakeImage(StrictModel):
    image: Annotated[str, Field(pattern=r"^[^@\s]+@sha256:[0-9a-f]{64}$")]


class FakeConfig(StrictModel):
    schema_version: Literal["aero-bench.fake/v1"]
    provider_id: Identifier
    mode: Identifier


class OtherConfig(StrictModel):
    schema_version: Literal["aero-bench.other/v1"]
    provider_id: Identifier
    mode: Identifier


class NonStrictConfig(BaseModel):
    provider_id: str
    mode: str


class IncompleteConfig(StrictModel):
    mode: Identifier


class DeploymentBoundConfig(StrictModel):
    provider_id: Identifier
    runtime_image: FakeImage


class FakeSession:
    def __init__(self, manifest: ProviderManifest) -> None:
        self.manifest = manifest

    async def prepare(self) -> None:
        return None

    async def reset(self, *, seed: int) -> object:
        raise AssertionError("not part of the registry test")

    async def step_stage(self, request: object) -> object:
        raise AssertionError("not part of the registry test")

    async def handle_command(self, request: object) -> object:
        raise AssertionError("not part of the registry test")

    async def finalize(self, request: object) -> object:
        raise AssertionError("not part of the registry test")

    async def snapshot_digest(self) -> str:
        raise AssertionError("not part of the registry test")

    async def shutdown(self) -> None:
        return None


def _write_json(root: Path, name: str, value: object) -> FileRef:
    path = root / name
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(encoded)
    return FileRef(path=name, sha256=hashlib.sha256(encoded).hexdigest())


def _bundle_and_provider(
    root: Path,
    *,
    provider_id: str = "provider",
    adapter: str = "fake.adapter",
    config_provider_id: str = "provider",
    ref_port: int = PROVIDER_PORT,
    ref_image: str = IMAGE,
    config_schema_version: str = "aero-bench.fake/v1",
) -> tuple[BundleReader, ProviderRef]:
    root.mkdir(parents=True, exist_ok=True)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "provider_id": {"type": "string"},
            "mode": {"type": "string"},
        },
        "required": [
            "schema_version",
            "provider_id",
            "mode",
        ],
        "additionalProperties": False,
    }
    schema_ref = _write_json(root, "provider.schema.json", schema)
    config_ref = _write_json(
        root,
        "provider.json",
        {
            "schema_version": config_schema_version,
            "provider_id": config_provider_id,
            "mode": "test",
        },
    )
    implementation = ImplementationIdentity(
        component_id=adapter,
        kind="mechanical_fixture",
        source_uri="https://github.com/example/aero-bench-fixture",
        source_revision="a" * 40,
        version="fixture-1",
    )
    provider = ProviderRef(
        provider_id=provider_id,
        adapter=adapter,
        port=ref_port,
        workload=RuntimeSpec(
            runtime=RuntimeImage(image=ref_image, command=("provider",)),
            resources=ResourceBudget(
                cpu_millicores=100,
                memory_mib=64,
                gpu_count=0,
            ),
            implementation=implementation,
        ),
        config=SchemaBoundFile(file=config_ref, schema_file=schema_ref),
        protocol_schema=FileRef(path="protocol.json", sha256="d" * 64),
        capabilities=("fake.capability",),
        artifact_requirements=(),
    )
    return BundleReader(root), provider



def _scenario_for(provider):
    return ResolvedScenario.model_construct(providers=(ResolvedProvider(
        provider_id=provider.provider_id,
        runtime_stage="motion",
        roles=("motion",),
        capability_ids=provider.capabilities,
    ),))


def _clock():
    return ClockSpec(authority="provider_barrier", step_ns=100,
                     max_steps=10, provider_timeout_ms=1000)


def _registration(builder) -> ProviderRegistration[FakeConfig]:
    return ProviderRegistration(
        adapter="fake.adapter",
        runtime_stage="motion",
        config_model=FakeConfig,
        session_builder=builder,
    )


def test_registry_reloads_pinned_config_and_injects_runtime_endpoint(
    tmp_path: Path,
) -> None:
    reader, provider = _bundle_and_provider(tmp_path)
    captured: dict[str, object] = {}

    def builder(
        config: FakeConfig,
        manifest: ProviderManifest,
        endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario,
        clock,
    ) -> FakeSession:
        captured.update(
            config=config,
            manifest=manifest,
            endpoint=endpoint,
            run_id=run_id,
            credential=credential,
        )
        return FakeSession(manifest)

    runtime_endpoint = RuntimeEndpoint(host="runtime-provider", port=PROVIDER_PORT)
    registry = ProviderRegistry((_registration(builder),))

    session = registry.build_session(
        provider=provider,
        bundle=reader,
        runtime_endpoint=runtime_endpoint,
        run_id=RUN_ID,
        credential=CREDENTIAL,
        scenario=_scenario_for(provider),
        clock=_clock(),
    )

    assert registry.adapters == ("fake.adapter",)
    assert isinstance(session, FakeSession)
    assert captured["endpoint"] == runtime_endpoint
    assert captured["run_id"] == RUN_ID
    assert captured["credential"] == CREDENTIAL
    config = captured["config"]
    assert isinstance(config, FakeConfig)
    assert "endpoint" not in FakeConfig.model_fields
    assert "runtime_image" not in FakeConfig.model_fields
    assert "protocol_version" not in FakeConfig.model_fields
    assert config.provider_id == provider.provider_id
    manifest = captured["manifest"]
    assert isinstance(manifest, ProviderManifest)
    assert manifest.provider_id == provider.provider_id
    assert manifest.adapter == provider.adapter
    assert manifest.implementation == provider.workload.implementation
    assert manifest.runtime_image == provider.workload.runtime.image
    assert manifest.config_digest == provider.config.file.sha256
    assert manifest.protocol_schema == provider.protocol_schema
    assert manifest.capabilities == provider.capabilities
    assert manifest.artifact_requirements == provider.artifact_requirements


@pytest.mark.parametrize("run_id", ("", "a" * 63, "g" * 64, "0" * 64))
def test_registry_rejects_unbound_or_invalid_run_id(
    tmp_path: Path,
    run_id: str,
) -> None:
    reader, provider = _bundle_and_provider(tmp_path)
    called = False

    def builder(
        config: FakeConfig,
        manifest: ProviderManifest,
        endpoint: RuntimeEndpoint,
        bound_run_id: str,
        credential: str,
        scenario,
        clock,
    ) -> FakeSession:
        nonlocal called
        called = True
        return FakeSession(manifest)

    registry = ProviderRegistry((_registration(builder),))
    with pytest.raises(ProviderRegistryError, match="run_id"):
        registry.build_session(
            provider=provider,
            bundle=reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT
            ),
            run_id=run_id,
            credential=CREDENTIAL,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )
    assert called is False


@pytest.mark.parametrize("credential", ("", "a" * 63, "g" * 64, "0" * 64))
def test_registry_rejects_unbound_or_invalid_credential(
    tmp_path: Path,
    credential: str,
) -> None:
    reader, provider = _bundle_and_provider(tmp_path)
    called = False

    def builder(
        config: FakeConfig,
        manifest: ProviderManifest,
        endpoint: RuntimeEndpoint,
        run_id: str,
        bound_credential: str,
    ) -> FakeSession:
        nonlocal called
        called = True
        return FakeSession(manifest)

    registry = ProviderRegistry((_registration(builder),))
    with pytest.raises(ProviderRegistryError, match="credential"):
        registry.build_session(
            provider=provider,
            bundle=reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT
            ),
            run_id=RUN_ID,
            credential=credential,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )
    assert called is False


def test_registry_rejects_unknown_adapter_and_duplicate_registration(
    tmp_path: Path,
) -> None:
    reader, provider = _bundle_and_provider(tmp_path)

    def builder(
        config: FakeConfig,
        manifest: ProviderManifest,
        endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario,
        clock,
    ) -> FakeSession:
        return FakeSession(manifest)

    registration = _registration(builder)
    with pytest.raises(ProviderRegistryError, match="already registered"):
        ProviderRegistry((registration, registration))

    unknown_reader, unknown_provider = _bundle_and_provider(
        tmp_path / "unknown",
        adapter="unknown.adapter",
    )
    registry = ProviderRegistry((registration,))
    with pytest.raises(ProviderRegistryError, match="no Provider adapter"):
        registry.build_session(
            provider=unknown_provider,
            bundle=unknown_reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT
            ),
            run_id=RUN_ID,
            credential=CREDENTIAL,
            scenario=_scenario_for(unknown_provider),
            clock=_clock(),
        )


def test_registry_rejects_config_provider_id_mismatch(tmp_path: Path) -> None:
    reader, provider = _bundle_and_provider(
        tmp_path,
        config_provider_id="other",
    )

    registry = ProviderRegistry(
        (
            _registration(
                lambda config, manifest, endpoint, run_id, credential, scenario, clock: FakeSession(
                    manifest
                ),
            ),
        )
    )
    with pytest.raises(ProviderRegistryError, match="provider_id"):
        registry.build_session(
            provider=provider,
            bundle=reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT
            ),
            run_id=RUN_ID,
            credential=CREDENTIAL,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )


def test_registry_rejects_runtime_port_and_config_digest_mismatch(
    tmp_path: Path,
) -> None:
    reader, provider = _bundle_and_provider(tmp_path)
    registry = ProviderRegistry(
        (
            _registration(
                lambda config, manifest, endpoint, run_id, credential, scenario, clock: FakeSession(
                    manifest
                )
            ),
        )
    )
    with pytest.raises(ProviderRegistryError, match="runtime endpoint port"):
        registry.build_session(
            provider=provider,
            bundle=reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT + 1
            ),
            run_id=RUN_ID,
            credential=CREDENTIAL,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )

    (tmp_path / "provider.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="sha256 mismatch"):
        registry.build_session(
            provider=provider,
            bundle=reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT
            ),
            run_id=RUN_ID,
            credential=CREDENTIAL,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )


def test_registry_rejects_config_model_mismatch_and_non_strict_registration(
    tmp_path: Path,
) -> None:
    reader, provider = _bundle_and_provider(tmp_path)

    def builder(config, manifest, endpoint, run_id, credential, scenario, clock):
        return FakeSession(manifest)

    with pytest.raises(ValueError, match="subclass StrictModel"):
        ProviderRegistration(
            adapter="fake.adapter",
            runtime_stage="motion",
            config_model=NonStrictConfig,
            session_builder=builder,
        )
    with pytest.raises(ProviderRegistryError, match="must declare provider_id"):
        ProviderRegistration(
            adapter="fake.adapter",
            runtime_stage="motion",
            config_model=IncompleteConfig,
            session_builder=builder,
        )
    with pytest.raises(
        ProviderRegistryError,
        match="must not declare runtime_image, endpoint, port, protocol, or deployment",
    ):
        ProviderRegistration(
            adapter="fake.adapter",
            runtime_stage="motion",
            config_model=DeploymentBoundConfig,
            session_builder=builder,
        )

    registry = ProviderRegistry(
        (
            ProviderRegistration(
                adapter="fake.adapter",
                runtime_stage="motion",
                config_model=OtherConfig,
                session_builder=builder,
            ),
        )
    )
    with pytest.raises(ProviderRegistryError, match="does not match adapter"):
        registry.build_session(
            provider=provider,
            bundle=reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT
            ),
            run_id=RUN_ID,
            credential=CREDENTIAL,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )


def test_registry_rejects_session_manifest_mismatch(tmp_path: Path) -> None:
    reader, provider = _bundle_and_provider(tmp_path)

    def builder(
        config: FakeConfig,
        manifest: ProviderManifest,
        endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario,
        clock,
    ) -> FakeSession:
        return FakeSession(manifest.model_copy(update={"provider_id": "other"}))

    registry = ProviderRegistry((_registration(builder),))
    with pytest.raises(ProviderRegistryError, match="manifest does not match"):
        registry.build_session(
            provider=provider,
            bundle=reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT
            ),
            run_id=RUN_ID,
            credential=CREDENTIAL,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )


def test_registry_rejects_session_without_finalizer(tmp_path: Path) -> None:
    reader, provider = _bundle_and_provider(tmp_path)

    def builder(
        config: FakeConfig,
        manifest: ProviderManifest,
        endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario,
        clock,
    ) -> FakeSession:
        session = FakeSession(manifest)
        session.finalize = None  # type: ignore[method-assign,assignment]
        return session

    registry = ProviderRegistry((_registration(builder),))
    with pytest.raises(
        ProviderRegistryError, match="does not implement ProviderSession"
    ):
        registry.build_session(
            provider=provider,
            bundle=reader,
            runtime_endpoint=RuntimeEndpoint(
                host="runtime-provider", port=PROVIDER_PORT
            ),
            run_id=RUN_ID,
            credential=CREDENTIAL,
            scenario=_scenario_for(provider),
            clock=_clock(),
        )
