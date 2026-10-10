from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from aero_bench.config.models import (
    ArtifactRequirement,
    ClockSpec,
    FileRef,
    ImplementationIdentity,
)
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.sumo import (
    PROTOCOL_VERSION,
    SoftwareIdentity,
    SumoConfig,
    SumoProvider,
    SumoProviderError,
)
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.runtime.contracts import (
    CommandRequest,
    NamedValue,
    ProviderEvent,
    ProviderFinalizationRequest,
    SimulationTime,
    StepReceipt,
)
from aero_bench.world.resolved import ResolvedScenario
from tests.world.support import compile_scenario_fixture


RUN_ID = "a" * 64
SESSION_TOKEN = "c" * 64


def artifact_requirement() -> ArtifactRequirement:
    return ArtifactRequirement(
        artifact_id="artifact.sumo.traffic",
        artifact_type="sumo.traffic.evidence",
        producer_id="traffic",
        visibility="private",
        relative_path="traffic/evidence.jsonl",
        max_size_bytes=65_536,
        source_asset_id=None,
    )


def sumo_manifest() -> ProviderManifest:
    return ProviderManifest(
        provider_id="traffic",
        adapter="sumo.traci",
        implementation=ImplementationIdentity(
            component_id="sumo.traci",
            kind="mechanical_fixture",
            source_uri="https://github.com/moby/moby",
            source_revision="4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
            version="test-fixture-1",
        ),
        runtime_image="registry.test/sumo@sha256:" + "1" * 64,
        config_digest="f" * 64,
        capabilities=("traffic.state",),
        protocol_schema=FileRef(path="sumo.json", sha256="f" * 64),
        artifact_requirements=(artifact_requirement(),),
    )


@lru_cache(maxsize=1)
def sumo_scenario() -> ResolvedScenario:
    """Real compiled scenario that assigns SUMO object bindings to ``traffic``."""
    with TemporaryDirectory(prefix="aero-sumo-provider-") as temporary:
        return compile_scenario_fixture(Path(temporary))


def sumo_clock() -> ClockSpec:
    return ClockSpec(
        authority="provider_barrier",
        step_ns=100,
        max_steps=10,
        provider_timeout_ms=1000,
    )


def sumo_provider(*, port: int = 17434) -> SumoProvider:
    return SumoProvider(
        config=sumo_config(),
        manifest=sumo_manifest(),
        runtime_endpoint=RuntimeEndpoint(host="runtime-sumo", port=port),
        run_id=RUN_ID,
        session_token=SESSION_TOKEN,
        scenario=sumo_scenario(),
        clock=sumo_clock(),
    )


def sumo_config() -> SumoConfig:
    return SumoConfig(
        schema_version="aero-bench.sumo/v2",
        provider_id="traffic",
        sumo=SoftwareIdentity(version="1.20.0", commit="b" * 40),
        sumo_binary="sumo",
        traci_port=17534,
        step_length_ns=100,
        sumo_args=("--no-step-log", "true"),
        required_commands=("sumo",),
        command_timeout_ms=1000,
    )


def sumo_state_receipt(target: SimulationTime, digest: str) -> dict[str, object]:
    return StepReceipt(
        run_id=RUN_ID,
        provider_id="traffic",
        reached=target,
        state_digest=digest,
        events=(
            ProviderEvent(
                provider_id="traffic",
                event_id=f"state.{target.tick}",
                time=target,
                payload_schema_id="sumo.state.v3",
                payload=(
                    NamedValue(name="snapshot_digest", value=digest),
                    NamedValue(
                        name="evidence_path",
                        value=artifact_requirement().relative_path,
                    ),
                    NamedValue(name="evidence_sha256", value="9" * 64),
                    NamedValue(name="simulation_time_ns", value=target.sim_time_ns),
                ),
            ),
        ),
    ).model_dump(mode="json")


def test_sumo_provider_rejects_commands_it_does_not_own() -> None:
    asyncio.run(_test_sumo_provider_rejects_commands_it_does_not_own())


async def _test_sumo_provider_rejects_commands_it_does_not_own() -> None:
    provider = sumo_provider()
    with pytest.raises(RuntimeError, match="does not own"):
        await provider.handle_command(
            CommandRequest(
                run_id=RUN_ID,
                command_id="command.1",
                agent_id="agent.1",
                tool_id="traffic.command",
                issued_at=SimulationTime(tick=1, sim_time_ns=100),
            )
        )


def test_sumo_provider_finalizes_against_exact_terminal_root() -> None:
    class ScriptedTransport:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict[str, object]]] = []
            self.size_bytes = 128
            self.closed = False

        async def request(self, operation, payload):
            document = dict(payload)
            self.requests.append((operation, document))
            if operation == "prepare":
                return {
                    "status": "ready",
                    "provider_id": "traffic",
                    "protocol_version": PROTOCOL_VERSION,
                    "runtime_image": sumo_manifest().runtime_image,
                    "scenario_digest": sumo_scenario().scenario_digest,
                    "sumo_version": sumo_config().sumo.version,
                    "sumo_commit": sumo_config().sumo.commit,
                }
            if operation == "reset":
                return {
                    "receipt": sumo_state_receipt(
                        SimulationTime(tick=0, sim_time_ns=0), "e" * 64
                    )
                }
            if operation == "finalize":
                finalization = document["request"]
                return {
                    "receipt": {
                        "schema_version": (
                            "aero-bench.provider-finalization-receipt/v1"
                        ),
                        "run_id": RUN_ID,
                        "provider_id": "traffic",
                        "event_chain_root": finalization["event_chain_root"],
                        "artifacts": [
                            {
                                "artifact_id": artifact_requirement().artifact_id,
                                "sha256": "f" * 64,
                                "size_bytes": self.size_bytes,
                            }
                        ],
                    }
                }
            if operation == "shutdown":
                return {"status": "stopped"}
            raise AssertionError(f"unexpected operation: {operation}")

        async def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        provider = sumo_provider()
        transport = ScriptedTransport()
        provider._transport = transport
        await provider.prepare()
        await provider.reset(seed=7)
        request = ProviderFinalizationRequest(
            schema_version="aero-bench.provider-finalization-request/v1",
            run_id=RUN_ID,
            terminal_event="run.completed",
            terminal_time=SimulationTime(tick=0, sim_time_ns=0),
            event_chain_root="d" * 64,
        )
        receipt = await provider.finalize(request)
        assert receipt.event_chain_root == request.event_chain_root
        operation, frame = transport.requests[-1]
        assert operation == "finalize"
        assert frame["request"] == request.model_dump(mode="json")
        assert frame["session_token"] == SESSION_TOKEN

        transport.size_bytes = artifact_requirement().max_size_bytes + 1
        with pytest.raises(SumoProviderError, match="receipt identity"):
            await provider.finalize(request)
        await provider.shutdown()
        assert transport.closed

    asyncio.run(scenario())


def test_sumo_config_rejects_process_identity_overrides() -> None:
    raw = sumo_config().model_dump(mode="json")
    raw["sumo_args"] = ["--seed=9"]
    with pytest.raises(ValueError, match="cannot override"):
        SumoConfig.model_validate(raw)


def test_sumo_provider_rejects_traci_port_collision_with_runtime_endpoint() -> None:
    with pytest.raises(ValueError, match="TraCI ports must differ"):
        sumo_provider(port=17534)


@pytest.mark.parametrize(
    "field",
    (
        "runtime_image",
        "endpoint",
        "protocol_version",
        "port",
        "protocol",
        "deployment",
    ),
)
def test_sumo_config_rejects_deployment_fields(field: str) -> None:
    raw = sumo_config().model_dump(mode="json")
    raw[field] = {
        "runtime_image": {"image": "registry.test/sumo@sha256:" + "1" * 64},
        "endpoint": {"host": "sumo-provider", "port": 17434},
        "protocol_version": PROTOCOL_VERSION,
        "port": 17434,
        "protocol": "tcp",
        "deployment": {"kind": "docker"},
    }[field]
    with pytest.raises(ValueError):
        SumoConfig.model_validate(raw)


@pytest.mark.parametrize("field", ("scenario_config", "scenario_files"))
def test_sumo_config_rejects_v1_scenario_inventory_fields(field: str) -> None:
    # SUMO scenario files are bound through ResolvedScenario.sumo assets in v2.
    raw = sumo_config().model_dump(mode="json")
    scenario = {"path": "scenario.sumocfg", "sha256": "c" * 64}
    raw[field] = scenario if field == "scenario_config" else [scenario]
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        SumoConfig.model_validate(raw)
