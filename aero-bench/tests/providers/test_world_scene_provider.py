from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from aero_bench.config.models import (
    ArtifactRequirement,
    ClockSpec,
    FileRef,
    ImplementationIdentity,
    NamedValue,
)
from aero_bench.providers.contracts import ProviderManifest
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import JsonLineRpcServer
from aero_bench.providers.world_scene import (
    PROTOCOL_VERSION,
    WorldSceneProvider,
    WorldSceneProviderError,
    WorldSceneProviderNotReady,
)
from aero_bench.providers.world_scene.config import SoftwareIdentity, WorldSceneConfig
from aero_bench.runtime.contracts import (
    CommandRequest,
    ProviderEvent,
    ProviderFinalizationRequest,
    SimulationTime,
    StepReceipt,
    StepRequest,
)
from aero_bench.world.resolved import ResolvedScenario


_CONTAINER_ROOT = Path(__file__).parents[2] / "containers" / "world-scene"
_service_spec = importlib.util.spec_from_file_location(
    "aero_bench_world_scene_provider_test_service", _CONTAINER_ROOT / "service.py"
)
assert _service_spec is not None and _service_spec.loader is not None
_SERVICE = importlib.util.module_from_spec(_service_spec)
sys.modules[_service_spec.name] = _SERVICE
_service_spec.loader.exec_module(_SERVICE)
_previous_service = sys.modules.get("service")
sys.modules["service"] = _SERVICE
try:
    _selfcheck_spec = importlib.util.spec_from_file_location(
        "aero_bench_world_scene_provider_test_selfcheck",
        _CONTAINER_ROOT / "selfcheck.py",
    )
    assert _selfcheck_spec is not None and _selfcheck_spec.loader is not None
    _SELFCHECK = importlib.util.module_from_spec(_selfcheck_spec)
    sys.modules[_selfcheck_spec.name] = _SELFCHECK
    _selfcheck_spec.loader.exec_module(_SELFCHECK)
finally:
    if _previous_service is None:
        sys.modules.pop("service", None)
    else:
        sys.modules["service"] = _previous_service


RUN_ID = _SELFCHECK.RUN_ID
SEED = _SELFCHECK.SEED
IMAGE = _SELFCHECK.RUNTIME_IMAGE
PROVIDER_ID = _SELFCHECK.PROVIDER_ID
SESSION_TOKEN = _SELFCHECK.SESSION_TOKEN
STEP_NS = _SELFCHECK.STEP_NS
SCENE_VERSION = _SELFCHECK.SCENE_VERSION
SOURCE_REVISION = _SELFCHECK.SOURCE_REVISION


def resolved_scenario() -> ResolvedScenario:
    return ResolvedScenario.model_validate_json(
        json.dumps(_SELFCHECK._resolved_scenario(), allow_nan=False)
    )


def clock() -> ClockSpec:
    return ClockSpec(
        authority="provider_barrier",
        step_ns=STEP_NS,
        max_steps=_SELFCHECK.MAX_STEPS,
        provider_timeout_ms=5_000,
    )


def config(**overrides: object) -> WorldSceneConfig:
    values: dict[str, object] = {
        "schema_version": "aero-bench.world-scene/v2",
        "provider_id": PROVIDER_ID,
        "scene": SoftwareIdentity(version=SCENE_VERSION, commit=SOURCE_REVISION),
    }
    values.update(overrides)
    return WorldSceneConfig.model_validate(values)


def artifact_requirement() -> ArtifactRequirement:
    return ArtifactRequirement.model_validate(_SELFCHECK._artifact_requirement())


def manifest(**overrides: object) -> ProviderManifest:
    values: dict[str, object] = {
        "provider_id": PROVIDER_ID,
        "adapter": "world.scene.rpc",
        "implementation": ImplementationIdentity(
            component_id="world.scene.rpc",
            kind="production",
            source_uri="https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
            source_revision=SOURCE_REVISION,
            version=SCENE_VERSION,
        ),
        "runtime_image": IMAGE,
        "config_digest": "f" * 64,
        "capabilities": (_SELFCHECK.PROJECTION_CAPABILITY,),
        "protocol_schema": FileRef(
            path="world-scene.protocol.json", sha256="e" * 64
        ),
        "artifact_requirements": (artifact_requirement(),),
    }
    values.update(overrides)
    return ProviderManifest.model_validate(values)


def readiness_payload(**overrides: object) -> dict[str, object]:
    scenario = resolved_scenario()
    values: dict[str, object] = {
        "status": "ready",
        "provider_id": PROVIDER_ID,
        "protocol_version": PROTOCOL_VERSION,
        "runtime_image": IMAGE,
        "scenario_schema_version": scenario.schema_version,
        "scenario_digest": scenario.scenario_digest,
        "world_schema_version": scenario.world_schema_version,
        "world_id": scenario.world_id,
        "world_digest": scenario.world_digest,
        "source_asset_digest": scenario.source_asset_digest,
        "scenario_asset_digest": scenario.scenario_asset_digest,
        "static_authority": "scenario.compiler",
        "scene_version": SCENE_VERSION,
        "scene_commit": SOURCE_REVISION,
    }
    values.update(overrides)
    return values


def state_receipt(target: SimulationTime, digest: str = "c" * 64) -> dict[str, object]:
    scenario = resolved_scenario()
    return StepReceipt(
        run_id=RUN_ID,
        provider_id=PROVIDER_ID,
        reached=target,
        state_digest=digest,
        events=(
            ProviderEvent(
                provider_id=PROVIDER_ID,
                event_id=f"state.{target.tick}",
                time=target,
                payload_schema_id="world.scene.projection.v2",
                payload=(
                    NamedValue(name="scenario_digest", value=scenario.scenario_digest),
                    NamedValue(name="static_authority", value="scenario.compiler"),
                ),
            ),
        ),
    ).model_dump(mode="json")


class ScriptedService:
    """Scripted responses transported through the real JSON-line server."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.responses: dict[str, dict[str, object]] = {}

    async def __call__(
        self, operation: str, payload: dict[str, object]
    ) -> dict[str, object]:
        self.requests.append((operation, payload))
        if operation in self.responses:
            return self.responses[operation]
        if operation == "prepare":
            return readiness_payload()
        if operation == "reset":
            return {
                "receipt": state_receipt(SimulationTime(tick=0, sim_time_ns=0))
            }
        if operation == "step_to":
            target = payload["request"]["target"]
            return {
                "receipt": state_receipt(
                    SimulationTime(
                        tick=target["tick"], sim_time_ns=target["sim_time_ns"]
                    )
                )
            }
        if operation == "snapshot":
            return {"snapshot_digest": "c" * 64}
        if operation == "finalize":
            request = payload["request"]
            return {
                "receipt": {
                    "schema_version": "aero-bench.provider-finalization-receipt/v1",
                    "run_id": RUN_ID,
                    "provider_id": PROVIDER_ID,
                    "event_chain_root": request["event_chain_root"],
                    "artifacts": [
                        {
                            "artifact_id": artifact_requirement().artifact_id,
                            "sha256": "e" * 64,
                            "size_bytes": 128,
                        }
                    ],
                }
            }
        if operation == "shutdown":
            return {"status": "stopped"}
        raise AssertionError(f"unexpected operation: {operation}")


def run(test_case) -> None:
    async def runner() -> None:
        scripted = ScriptedService()
        server = JsonLineRpcServer(scripted)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        session = WorldSceneProvider(
            config=config(),
            manifest=manifest(),
            runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=port),
            run_id=RUN_ID,
            session_token=SESSION_TOKEN,
            scenario=resolved_scenario(),
            clock=clock(),
        )
        try:
            await test_case(session, scripted)
        finally:
            await session.shutdown()
            await server.graceful_close()

    asyncio.run(runner())


def test_prepare_sends_only_contract_bound_projection_identity() -> None:
    async def case(session: WorldSceneProvider, scripted: ScriptedService) -> None:
        await session.prepare()
        operation, payload = scripted.requests[0]
        assert operation == "prepare"
        assert payload == {
            "provider_id": PROVIDER_ID,
            "run_id": RUN_ID,
            "runtime_image": IMAGE,
            "config_digest": "f" * 64,
            "artifact_requirements": [artifact_requirement().model_dump(mode="json")],
            "scenario_digest": resolved_scenario().scenario_digest,
            "session_token": SESSION_TOKEN,
        }

    run(case)


def test_every_frame_presents_token_and_uses_contract_clock() -> None:
    async def case(session: WorldSceneProvider, scripted: ScriptedService) -> None:
        await session.prepare()
        await session.reset(seed=SEED)
        await session.step_to(
            StepRequest(
                run_id=RUN_ID,
                target=SimulationTime(tick=1, sim_time_ns=STEP_NS),
            )
        )
        await session.snapshot_digest()
        await session.shutdown()
        assert [operation for operation, _ in scripted.requests] == [
            "prepare",
            "reset",
            "step_to",
            "snapshot",
            "shutdown",
        ]
        assert all(
            payload["session_token"] == SESSION_TOKEN
            for _, payload in scripted.requests
        )

    run(case)


def test_reset_and_step_reject_non_scenario_time() -> None:
    async def case(session: WorldSceneProvider, scripted: ScriptedService) -> None:
        await session.prepare()
        with pytest.raises(WorldSceneProviderError, match="seed differs"):
            await session.reset(seed=SEED + 1)
        await session.reset(seed=SEED)
        with pytest.raises(WorldSceneProviderError, match="next ResolvedRun clock barrier"):
            await session.step_to(
                StepRequest(
                    run_id=RUN_ID,
                    target=SimulationTime(tick=1, sim_time_ns=STEP_NS + 1),
                )
            )
        with pytest.raises(WorldSceneProviderError, match="another run"):
            await session.step_to(
                StepRequest(
                    run_id="b" * 64,
                    target=SimulationTime(tick=1, sim_time_ns=STEP_NS),
                )
            )

    run(case)


@pytest.mark.parametrize(
    ("override", "message"),
    (
        ({"protocol_version": "v0"}, "protocol version"),
        ({"world_digest": "9" * 64}, "compiler-owned scenario projection"),
        ({"static_authority": "provider"}, "readiness response is invalid"),
        ({"scene_version": "0.0.0"}, "service identity"),
    ),
)
def test_prepare_rejects_projection_or_service_identity_mismatch(
    override: dict[str, object], message: str
) -> None:
    async def case(session: WorldSceneProvider, scripted: ScriptedService) -> None:
        scripted.responses["prepare"] = readiness_payload(**override)
        with pytest.raises(WorldSceneProviderError, match=message):
            await session.prepare()

    run(case)


def test_receipt_requires_one_compiler_projection_event() -> None:
    async def case(session: WorldSceneProvider, scripted: ScriptedService) -> None:
        scripted.responses["reset"] = {
            "receipt": StepReceipt(
                run_id=RUN_ID,
                provider_id=PROVIDER_ID,
                reached=SimulationTime(tick=0, sim_time_ns=0),
                state_digest="c" * 64,
            ).model_dump(mode="json")
        }
        await session.prepare()
        with pytest.raises(WorldSceneProviderError, match="exactly one compiler projection"):
            await session.reset(seed=SEED)

    run(case)


def test_finalization_binds_exact_terminal_root_and_artifact() -> None:
    async def case(session: WorldSceneProvider, scripted: ScriptedService) -> None:
        await session.prepare()
        await session.reset(seed=SEED)
        request = ProviderFinalizationRequest(
            schema_version="aero-bench.provider-finalization-request/v1",
            run_id=RUN_ID,
            terminal_event="run.completed",
            terminal_time=SimulationTime(tick=0, sim_time_ns=0),
            event_chain_root="d" * 64,
        )
        receipt = await session.finalize(request)
        assert receipt.event_chain_root == request.event_chain_root
        operation, payload = scripted.requests[-1]
        assert operation == "finalize"
        assert payload["request"] == request.model_dump(mode="json")
        assert payload["session_token"] == SESSION_TOKEN

        changed = receipt.model_copy(update={"event_chain_root": "e" * 64})
        scripted.responses["finalize"] = {
            "receipt": changed.model_dump(mode="json")
        }
        with pytest.raises(WorldSceneProviderError, match="receipt identity"):
            await session.finalize(request)

    run(case)


def test_projection_provider_owns_no_tools_or_commands() -> None:
    async def case(session: WorldSceneProvider, scripted: ScriptedService) -> None:
        await session.prepare()
        request = CommandRequest(
            run_id=RUN_ID,
            command_id="command.one",
            agent_id="agent.one",
            tool_id="move.to",
            issued_at=SimulationTime(tick=0, sim_time_ns=0),
        )
        with pytest.raises(WorldSceneProviderError, match="does not own tool"):
            await session.handle_command(request)

    run(case)


def test_provider_requires_prepared_state() -> None:
    async def case(session: WorldSceneProvider, scripted: ScriptedService) -> None:
        with pytest.raises(WorldSceneProviderNotReady):
            await session.reset(seed=SEED)
        with pytest.raises(WorldSceneProviderNotReady):
            await session.snapshot_digest()

    run(case)


def _constructor(**overrides: object) -> WorldSceneProvider:
    values: dict[str, object] = {
        "config": config(),
        "manifest": manifest(),
        "runtime_endpoint": RuntimeEndpoint(host="runtime-scene", port=17436),
        "run_id": RUN_ID,
        "session_token": SESSION_TOKEN,
        "scenario": resolved_scenario(),
        "clock": clock(),
    }
    values.update(overrides)
    return WorldSceneProvider(**values)


def test_constructor_rejects_config_manifest_identity_mismatch() -> None:
    with pytest.raises(ValueError, match="IDs differ"):
        _constructor(config=config(provider_id="other.scene"))


def test_constructor_rejects_non_compiler_static_ownership() -> None:
    scenario = resolved_scenario()
    mutated = scenario.model_copy(
        update={
            "entities": tuple(
                entity.model_copy(
                    update={
                        "owner_kind": "provider",
                        "owner_id": PROVIDER_ID,
                        "source_provider_id": PROVIDER_ID,
                    }
                )
                if entity.state == "static"
                else entity
                for entity in scenario.entities
            )
        }
    )
    with pytest.raises(ValueError, match="scenario-compiler owned"):
        _constructor(scenario=mutated)


def test_constructor_rejects_absent_projection_workload() -> None:
    scenario = resolved_scenario()
    mutated = scenario.model_copy(
        update={
            "providers": tuple(
                provider
                for provider in scenario.providers
                if provider.provider_id != PROVIDER_ID
            )
        }
    )
    with pytest.raises(ValueError, match="absent from ResolvedScenario"):
        _constructor(scenario=mutated)


@pytest.mark.parametrize(
    "token", ("0" * 64, "g" * 64, "abc", "", "A" * 64, SESSION_TOKEN[:-1] + "G")
)
def test_constructor_rejects_invalid_session_token(token: str) -> None:
    with pytest.raises(ValueError, match="session_token"):
        _constructor(session_token=token)
