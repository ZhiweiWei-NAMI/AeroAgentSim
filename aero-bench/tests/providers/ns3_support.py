"""Strict, mechanical scenario inputs for ns-3 client protocol tests."""

from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from aero_bench.config.models import ObservationGrant, ToolGrant
from aero_bench.providers.ns3 import ns3_capabilities
from aero_bench.runtime.contracts import NetworkStageRequest, SimulationTime, StageBarrierDigest
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.world.contracts import NetworkLinkBinding, NetworkNodeBinding
from aero_bench.world.resolved import ResolvedScenario, compile_resolved_scenario
from tests.test_runner import _stage_barrier
from tests.world.support import (
    materialize_world_package, provider_capabilities, scenario_task_inputs,
)


RUN_ID = "a" * 64
AGENT_ID = "fixture.agent"


@lru_cache(maxsize=1)
def network_scenario() -> ResolvedScenario:
    def network_world(kwargs, _root):
        kwargs["provider_requirements"] = tuple(sorted((
            item.model_copy(update={
                "provider_id": "network",
                "required_capability_ids": ns3_capabilities(("802.11ax",)),
            }) if item.provider_id == "radio" else item
            for item in kwargs["provider_requirements"]
        ), key=lambda item: item.provider_id))
        kwargs["assets"] = tuple(asset.model_copy(update={
            "audiences": tuple(audience.model_copy(update={"audience_id": "network"})
                               if audience.audience_id == "radio" else audience
                               for audience in asset.audiences),
        }) for asset in kwargs["assets"])
        network = kwargs["network"]
        radio = network.radio_profiles[0].model_copy(update={
            "provider_id": "network", "frequency_ghz": 5.805,
            "channel_width_mhz": 20.0,
        })
        static = next(entity for entity in kwargs["entities"] if entity.state == "static")
        kwargs["network"] = network.model_copy(update={
            "provider_id": "network", "radio_profiles": (radio,),
            "node_bindings": (
                NetworkNodeBinding(node_id="node.edge", entity_id=static.entity_id,
                                   endpoint_id="edge.1", radio_profile_id=radio.radio_profile_id),
                NetworkNodeBinding(node_id="node.uav", entity_id="uav.alpha",
                                   endpoint_id="uav.1", radio_profile_id=radio.radio_profile_id),
            ),
            "links": (NetworkLinkBinding(
                link_id="link.1", source_node_id="node.uav", destination_node_id="node.edge",
                data_rate_bps=1_000_000, propagation_delay_ns=10,
            ),),
        })

    with TemporaryDirectory(prefix="aero-ns3-client-fixture-") as temporary:
        reader, world, _ = materialize_world_package(Path(temporary), mutate_kwargs=network_world)
        task, agents, projection = scenario_task_inputs()
        schema = task.package.config.schema_file
        agent = agents[0].model_copy(update={
            "tools": (ToolGrant(
                tool_id="network.send", provider_id="network", request_schema=schema,
                response_schema=schema, timeout_ms=1000, idempotent=False,
            ),),
            "observations": (ObservationGrant(
                observation_id="network.mailbox.uav.1", provider_id="network",
                schema_file=schema, timeout_ms=1000,
            ),),
        })
        capabilities = provider_capabilities()
        del capabilities["radio"]
        capabilities["network"] = ns3_capabilities(("802.11ax",))
        return compile_resolved_scenario(
            reader, world, "launch.alpha", 7, capabilities,
            {"flight": "motion", "traffic": "motion", "network": "network",
             "perception": "business_environment"},
            task, (agent,), projection,
        )


def network_request(tick: int = 1) -> NetworkStageRequest:
    scenario = network_scenario()
    run = SimpleNamespace(run_id=RUN_ID, scenario=scenario)
    target = SimulationTime(tick=tick, sim_time_ns=tick * 100)
    barrier, contributions = _stage_barrier(
        run, at=target, stage="motion",
        provider_ids=tuple(p.provider_id for p in scenario.providers if p.runtime_stage == "motion"),
    )
    scene = SceneStateAssembler(scenario).assemble(
        run_id=RUN_ID, at=target, barrier=barrier, contributions=contributions,
        previous_scene_state=None if tick == 1 else network_request(tick - 1).scene_state,
    )
    return NetworkStageRequest(
        schema_version="aero-bench.provider-stage-request/v1",
        run_id=RUN_ID, scenario_digest=scenario.scenario_digest,
        provider_id="network", target=target, stage="network",
        scene_state=scene, scene_state_digest=scene.scene_state_digest,
        predecessor_barriers=(StageBarrierDigest(stage="motion", barrier_digest=barrier.barrier_digest),),
    )
