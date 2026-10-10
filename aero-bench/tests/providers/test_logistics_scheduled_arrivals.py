"""Synthetic module/RPC fixtures, never formal execution evidence."""

import asyncio
from copy import deepcopy
import json

import pytest

from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.logistics_business.scheduled_arrivals import (
    SCHEDULED_ARRIVAL_CAPABILITY,
    SCHEDULED_ARRIVAL_EVENT_SCHEMA,
)
from aero_bench.tasks.logistics_arrivals.verifier import verify_native_arrivals
from tests.providers import test_logistics_business_service as fixture


def scheduled_config():
    source = fixture._config_document()["task_package"]
    domain = {
        key: source[key]
        for key in ("task_id", "verifier_id", "scene", "facilities", "fleet")
    }
    domain.update(
        schema_version="aero-bench.logistics-arrivals-domain/v1",
        orders=[],
        actor_grants=[{"actor_id": "logistics.business", "role": "business"}],
    )
    orders = []
    for tick in (1, 2):
        order = {
            **source["orders"][0],
            "order_id": f"scheduled.order.{tick}",
            "release_time_s": float(tick),
        }
        orders.append(
            {
                "event_id": f"editor.order.{tick}",
                "at_tick": tick,
                "actor_id": "logistics.business",
                "order": order,
            }
        )
    return {
        "schema_version": "aero-bench.logistics-business/v3",
        "provider_id": fixture.PROVIDER_ID,
        "task_package": domain,
        "principal_bindings": [],
        "observation": None,
        "scheduled_orders": orders,
    }


async def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fixture,
        "CAPABILITIES",
        tuple(sorted((*fixture.CAPABILITIES, SCHEDULED_ARRIVAL_CAPABILITY))),
    )
    service = await fixture._prepared_service_with_config(tmp_path, scheduled_config())
    return service


async def completed(tmp_path, monkeypatch):
    service = await prepared(tmp_path, monkeypatch)
    await fixture._advance_to(service, to_tick=2)
    artifact = json.loads(service._artifact_content(event_chain_root="8" * 64))
    return service, artifact


def verify(service, artifact):
    return verify_native_arrivals(
        config=service._config,
        artifact=artifact,
        run_id=fixture.RUN_ID,
        runtime_image=fixture.IMAGE,
        config_digest=service._workload_identity.config_digest,
        seed=fixture.SEED,
        scenario_digest=fixture._resolved_scenario().scenario_digest,
        step_ns=1_000_000_000,
        final_tick=2,
    )


def test_scheduled_creation_is_consumed_once_at_native_barriers_and_replayed(
    tmp_path, monkeypatch
):
    async def run():
        service = await prepared(tmp_path, monkeypatch)
        assert not service._arrival.registered_order_ids()
        previous = None
        for tick in (1, 2):
            request = fixture._stage_request(
                tick=tick,
                sim_time_ns=tick * 1_000_000_000,
                previous_scene_state=previous,
            )
            response = await service.handle(
                "step_stage", fixture._stage_payload(request)
            )
            events = response["result"]["step_receipt"]["events"]
            assert (
                len(
                    [
                        event
                        for event in events
                        if event["payload_schema_id"] == SCHEDULED_ARRIVAL_EVENT_SCHEMA
                    ]
                )
                == 1
            )
            assert service._arrival.registered_order_ids() == tuple(
                f"scheduled.order.{index}" for index in range(1, tick + 1)
            )
            with pytest.raises(fixture._SERVICE.LogisticsBusinessServiceError):
                await service.handle("step_stage", fixture._stage_payload(request))
            previous = request.scene_state
        artifact = json.loads(service._artifact_content(event_chain_root="8" * 64))
        assert len(verify(service, artifact)) == 2
        assert [event["kind"] for event in artifact["arrival"]] == [
            "created",
            "released",
            "created",
            "released",
        ]
        assert artifact["history"] == []
        assert all(
            order["status"] == "created" for order in artifact["ledger"]["orders"]
        )
        await service.handle(
            "reset",
            fixture._authorized(
                {
                    "provider_id": fixture.PROVIDER_ID,
                    "run_id": fixture.RUN_ID,
                    "seed": fixture.SEED,
                }
            ),
        )
        assert service._scheduled_applications == []
        assert service._arrival.registered_order_ids() == ()

    asyncio.run(run())


@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "duplicate",
        "early",
        "order",
        "actor",
        "image",
        "seed",
        "clock",
        "baseline",
        "stage",
    ],
)
def test_independent_native_replay_rejects_tampered_schedule_evidence(
    tmp_path, monkeypatch, tamper
):
    service, artifact = asyncio.run(completed(tmp_path, monkeypatch))
    bad = deepcopy(artifact)
    evidence = bad["scheduled_evidence"]
    if tamper == "missing":
        evidence["applied"].pop()
    elif tamper == "duplicate":
        evidence["applied"].append(evidence["applied"][0])
    elif tamper == "early":
        evidence["applied"][0]["applied_at"]["tick"] = 0
    elif tamper == "order":
        evidence["applied"][0]["requested"]["order"]["cargo_mass_kg"] = 2.0
    elif tamper == "actor":
        evidence["applied"][0]["arrival"]["actor_id"] = "unauthorized.business"
    elif tamper == "image":
        evidence["runtime_image"] = "different@sha256:" + "2" * 64
    elif tamper == "seed":
        evidence["seed"] += 1
    elif tamper == "clock":
        evidence["current_time"]["sim_time_ns"] += 1
    elif tamper == "baseline":
        bad["initial_requests"] = [scheduled_config()["scheduled_orders"][0]["order"]]
    elif tamper == "stage":
        evidence["accepted_stage_inputs"].pop()
    with pytest.raises((ValueError, KeyError)):
        verify(service, bad)


@pytest.mark.parametrize(
    "tamper", ["actor", "order_id", "event_id", "tick", "physical", "v2"]
)
def test_schedule_config_is_strict_and_declares_canonical_authority(tamper):
    value = scheduled_config()
    if tamper == "actor":
        value["scheduled_orders"][0]["actor_id"] = "undeclared.business"
    elif tamper == "order_id":
        value["scheduled_orders"][1]["order"]["order_id"] = value["scheduled_orders"][
            0
        ]["order"]["order_id"]
    elif tamper == "event_id":
        value["scheduled_orders"][1]["event_id"] = value["scheduled_orders"][0][
            "event_id"
        ]
    elif tamper == "tick":
        value["scheduled_orders"][0]["at_tick"] = 0
    elif tamper == "physical":
        value["task_package"]["actor_grants"][0]["role"] = "aircraft_agent"
    elif tamper == "v2":
        value["schema_version"] = "aero-bench.logistics-business/v2"
    with pytest.raises(ValueError):
        LogisticsBusinessConfig.model_validate(value)


def test_skipping_a_due_creation_fails_before_native_mutation(tmp_path, monkeypatch):
    async def run():
        service = await prepared(tmp_path, monkeypatch)
        digest = service._state_digest()
        previous = fixture._stage_request(tick=1, sim_time_ns=1_000_000_000).scene_state
        with pytest.raises(
            fixture._SERVICE.LogisticsBusinessServiceError, match="skipped"
        ):
            await service.handle(
                "step_stage",
                fixture._stage_payload(
                    fixture._stage_request(
                        tick=2, sim_time_ns=2_000_000_000, previous_scene_state=previous
                    )
                ),
            )
        assert service._state_digest() == digest

    asyncio.run(run())


def test_scheduled_arrivals_cross_the_real_rpc_client_and_finalize(
    tmp_path, monkeypatch
):
    from aero_bench.providers.logistics_business.provider import (
        LogisticsBusinessProvider,
    )
    from aero_bench.providers.registry import RuntimeEndpoint
    from aero_bench.providers.rpc import JsonLineRpcServer
    from aero_bench.runtime.contracts import ProviderFinalizationRequest, SimulationTime
    from tests.providers.test_logistics_business_provider import _manifest

    async def run():
        monkeypatch.setattr(
            fixture,
            "CAPABILITIES",
            tuple(sorted((*fixture.CAPABILITIES, SCHEDULED_ARRIVAL_CAPABILITY))),
        )
        config = scheduled_config()
        service = fixture._make_service_with_config(tmp_path, config)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        client = LogisticsBusinessProvider(
            config=LogisticsBusinessConfig.model_validate(config),
            manifest=_manifest(service._workload_identity.config_digest).model_copy(
                update={"capabilities": fixture.CAPABILITIES}
            ),
            runtime_endpoint=RuntimeEndpoint(
                host="127.0.0.1", port=handle.sockets[0].getsockname()[1]
            ),
            run_id=fixture.RUN_ID,
            session_token=fixture.SESSION_TOKEN,
            scenario=fixture._resolved_scenario(),
        )
        try:
            await client.prepare()
            await client.reset(seed=fixture.SEED)
            previous = None
            for tick in (1, 2):
                request = fixture._stage_request(
                    tick=tick,
                    sim_time_ns=tick * 1_000_000_000,
                    previous_scene_state=previous,
                )
                result = await client.step_stage(request)
                assert len(result.step_receipt.events) == 2
                previous = request.scene_state
            assert client._applied_schedule_ids == {"editor.order.1", "editor.order.2"}
            assert len(client._orders_pool) == 2
            receipt = await client.finalize(
                ProviderFinalizationRequest(
                    schema_version="aero-bench.provider-finalization-request/v1",
                    run_id=fixture.RUN_ID,
                    terminal_event="run.completed",
                    terminal_time=SimulationTime(tick=2, sim_time_ns=2_000_000_000),
                    event_chain_root="8" * 64,
                )
            )
            raw = (tmp_path / "artifacts/logistics/state.json").read_bytes()
            assert receipt.artifacts[0].size_bytes == len(raw)
            assert len(verify(service, json.loads(raw))) == 2
        finally:
            await client.shutdown()
            handle.close()
            await handle.wait_closed()

    asyncio.run(run())
