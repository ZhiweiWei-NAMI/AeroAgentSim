"""Real Gateway wire/gate regression with a stub policy, not simulator evidence."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from aero_bench.agent.runtime import AgentContext
from aero_bench.providers.rpc import JsonLineRpcServer
from aero_bench.runtime.contracts import AgentTurnDecision, SimulationTime
from aero_bench.tasks.urban_recovery_demo.contracts import STEP_NS
from aero_bench.tasks.urban_recovery_demo.participant import UrbanParticipant, UrbanParticipantError
from tests.support import build_bundle, resolve_bundle
from tests.test_gateway_service import (
    SECOND_TOKEN, TOKEN, _SequenceCoordinator, _completion, _fixture,
)


def test_urban_run_waits_for_gateway_commit_before_next_observation(tmp_path):
    source = build_bundle(tmp_path / "source")
    base = resolve_bundle(source.suite, executor_kind="docker_reference")[0]
    # Unit Gateway roster, not a canonical two-Agent simulator scenario.
    run = base.model_copy(update={"agents": (
        base.agents[0], base.agents[0].model_copy(update={"agent_id": "agent.two"}),
    )})
    zero = SimulationTime(tick=0, sim_time_ns=0)
    one = SimulationTime(tick=1, sim_time_ns=STEP_NS)
    coordinator = _SequenceCoordinator(tuple(
        AgentTurnDecision(
            schema_version="aero-bench.agent-turn-decision/v1", run_id=run.run_id,
            status=status, at=at, active_agent_ids=("reference.agent", "agent.two"),
            missing_agent_ids=missing,
        )
        for status, at, missing in (("waiting", zero, ("agent.two",)), ("advanced", one, ()))
    ))
    _, service, dispatcher, _, _ = _fixture(
        tmp_path / "service", run_override=run, coordinator=coordinator,
        credential_tokens={"reference.agent": TOKEN, "agent.two": SECOND_TOKEN},
    )

    async def scenario():
        server = await JsonLineRpcServer(service).start(host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        context = AgentContext(
            contract=SimpleNamespace(run_id=run.run_id, agent=run.agents[0]),
            token=TOKEN, gateway_host="127.0.0.1", gateway_port=port,
            bundle_root=tmp_path, artifact_root=tmp_path,
        )
        participant = object.__new__(UrbanParticipant)
        participant.context = context
        participant.config = SimpleNamespace(final_tick=1)
        submitted = threading.Event()

        def step(gateway, at):
            if at == zero:
                decision = gateway.complete_turn(
                    completion_id="unit.early", at=at, disposition="advance",
                )
                assert decision.status == "waiting"
                submitted.set()
            else:
                gateway.observe(observation_id="inspection.camera", at=at)

        participant.step = step
        task = asyncio.create_task(asyncio.to_thread(participant.run, timeout_seconds=2))
        try:
            assert await asyncio.to_thread(submitted.wait, 2)
            await asyncio.sleep(0.05)
            assert not task.done()
            assert dispatcher.observations == []
            decision = await service("turn.complete", {
                "token": SECOND_TOKEN,
                "completion": _completion(
                    run, agent_id="agent.two", completion_id="unit.late",
                    disposition="advance",
                ),
            })
            assert decision["status"] == "advanced"
            await asyncio.wait_for(task, timeout=3)
            assert len(dispatcher.observations) == 1
            assert dispatcher.observations[0][2] == one
            assert len(coordinator.completions) == 2
        finally:
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("run_id,tick", [("wrong-run", 1), ("unit-run", 2)])
def test_barrier_wait_rejects_wrong_identity_or_skipped_time(run_id, tick):
    participant = object.__new__(UrbanParticipant)
    participant.context = SimpleNamespace(run_id="unit-run")
    gateway = SimpleNamespace(probe=lambda: {
        "schema_version": "aero-bench.gateway-probe/v1", "run_id": run_id,
        "status": "ready", "current": {"tick": tick, "sim_time_ns": tick * STEP_NS},
    })
    with pytest.raises(UrbanParticipantError):
        participant._wait_for_barrier(
            gateway, SimulationTime(tick=1, sim_time_ns=STEP_NS), timeout_seconds=1,
        )
