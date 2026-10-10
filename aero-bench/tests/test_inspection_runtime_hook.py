from __future__ import annotations

import asyncio
import hashlib

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import NamedValue
from aero_bench.providers.contracts import ProviderCommandResult
from aero_bench.providers.inspection_business.provider import (
    BUSINESS_HISTORY_EVENT_SCHEMA,
    BUSINESS_HISTORY_SCHEMA,
)
from aero_bench.runtime.contracts import (
    CommandReceipt,
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    StageReceipt,
    scene_state_digest_value,
    stage_barrier_digest_value,
    stage_receipt_digest_value,
)
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.runtime.hooks import ValidatedObservation
from aero_bench.runtime.ledger import EventLedger
from aero_bench.tasks.inspection import (
    BusinessCommandEnvelope,
    ClaimWorkOrderCommand,
    CompleteWorkOrderCommand,
    InspectionBusinessService,
    ObservationReadyCommand,
    StartWorkOrderCommand,
    SubmitInspectionCommand,
    WorkOrderStatus,
    resolve_inspection_run_context,
)
from aero_bench.tasks.inspection.runtime_hook import InspectionRuntimeHookFactory
from tests.support import build_bundle, resolve_bundle


class _BusinessSession:
    def __init__(self, package, *, run_id: str):
        self.service = InspectionBusinessService(package, run_id=run_id)
        self.commands = []

    async def query_business(self, query):
        return self.service.query(query)

    async def handle_command(self, request):
        values = {item.name: item.value for item in request.arguments}
        if request.tool_id == "business.observation_ready":
            command = ObservationReadyCommand(
                command_id=request.command_id,
                work_order_id=values["work_order_id"],
                actor_id=values["actor_id"],
                issued_at=request.issued_at,
                observation_id=values["observation_id"],
            )
        elif request.tool_id == "business.complete":
            command = CompleteWorkOrderCommand(
                command_id=request.command_id,
                work_order_id=values["work_order_id"],
                actor_id=values["actor_id"],
                issued_at=request.issued_at,
            )
        else:  # pragma: no cover - the authority prevents this
            raise AssertionError(request.tool_id)
        result = self.service.apply(
            BusinessCommandEnvelope(run_id=request.run_id, command=command)
        )
        self.commands.append(request)
        receipts = tuple(
            CommandReceipt(
                run_id=request.run_id,
                command_id=request.command_id,
                provider_id="business",
                phase=phase,
                time=request.issued_at,
            )
            for phase in ("received", "accepted", "applied", "completed")
        )
        events = [
            ProviderEvent(
                provider_id="business",
                event_id="business.transition",
                time=request.issued_at,
                payload_schema_id=BUSINESS_HISTORY_EVENT_SCHEMA,
                payload=(
                    NamedValue(
                        name="business_history_record_hash",
                        value=result.history_record.record_hash,
                    ),
                    NamedValue(
                        name="business_history_sequence",
                        value=result.history_record.sequence,
                    ),
                    NamedValue(name="command_id", value=request.command_id),
                    NamedValue(name="schema_id", value=BUSINESS_HISTORY_SCHEMA),
                ),
            )
        ]
        if request.tool_id == "business.complete":
            events.extend(
                (
                    ProviderEvent(
                        provider_id="business",
                        event_id="public.status",
                        time=request.issued_at,
                        payload_schema_id="public.status.v2",
                        payload=(
                            NamedValue(
                                name="status",
                                value=(
                                    '{"business_state":"completed",'
                                    '"network_state":"delivered",'
                                    '"observation_state":"ready",'
                                    '"task_progress_ratio":1.0}'
                                ),
                            ),
                        ),
                    ),
                    ProviderEvent(
                        provider_id="business",
                        event_id="public.event",
                        time=request.issued_at,
                        payload_schema_id="public.event.v2",
                        payload=(NamedValue(name="severity", value="info"),),
                    ),
                )
            )
        return ProviderCommandResult(receipts=receipts, events=tuple(events))


class _NetworkSession:
    pass


def _delivery_event(
    *,
    run_id: str,
    provider_id: str,
    artifact_id: str,
    agent_id: str,
    work_order_id: str,
    payload_digest: str,
    message_id: str,
    delivered_at: SimulationTime,
    committed_at: SimulationTime | None = None,
) -> ProviderEvent:
    values = {
        "schema_id": "inspection.network-delivery.v1",
        "run_id": run_id,
        "provider_id": provider_id,
        "command_id": f"command.{message_id}",
        "agent_id": agent_id,
        "source_artifact_id": artifact_id,
        "work_order_id": work_order_id,
        "message_id": message_id,
        "payload_digest": payload_digest,
        "sent_tick": 0,
        "sent_time_ns": 0,
        "delivered_tick": delivered_at.tick,
        "delivered_time_ns": delivered_at.sim_time_ns,
    }
    return ProviderEvent(
        provider_id=provider_id,
        event_id="network.delivery",
        time=delivered_at if committed_at is None else committed_at,
        payload_schema_id="inspection.network-delivery.v1",
        payload=tuple(
            NamedValue(name=name, value=value) for name, value in values.items()
        ),
    )


def _stage_boundary(
    *,
    run,
    network_provider_id: str,
    target: SimulationTime,
    previous_scene_state: SceneState | None = None,
) -> tuple[SceneState, tuple[StageBarrier, StageBarrier]]:
    motion_fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": run.run_id,
        "scenario_digest": run.scenario.scenario_digest,
        "at": target,
        "stage": "motion",
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
        "provider_ids": (),
        "receipts": (),
        "receipt_digests": (),
    }
    unsigned_motion = StageBarrier.model_construct(
        **motion_fields,
        barrier_digest="0" * 64,
    )
    motion = StageBarrier(
        **motion_fields,
        barrier_digest=stage_barrier_digest_value(unsigned_motion),
    )
    static_entity = next(
        entity for entity in run.scenario.entities if entity.state == "static"
    )
    static_scenario = run.scenario.model_copy(
        update={"entities": (static_entity,)}
    )
    scene_state = SceneStateAssembler(static_scenario).assemble(
        run_id=run.run_id,
        at=target,
        barrier=motion,
        contributions=(),
        previous_scene_state=previous_scene_state,
    )
    predecessor = (
        StageBarrierDigest(
            stage="motion",
            barrier_digest=motion.barrier_digest,
        ),
    )
    receipt_fields = {
        "schema_version": "aero-bench.stage-receipt/v1",
        "run_id": run.run_id,
        "scenario_digest": run.scenario.scenario_digest,
        "at": target,
        "stage": "network",
        "provider_id": network_provider_id,
        "state_digest": hashlib.sha256(b"network-state").hexdigest(),
        "step_receipt_digest": hashlib.sha256(b"network-step").hexdigest(),
        "contribution_digest": hashlib.sha256(
            b"network-contribution"
        ).hexdigest(),
        "payload_digest": hashlib.sha256(b"network-payload").hexdigest(),
        "input_scene_state_digest": scene_state.scene_state_digest,
        "predecessor_barriers": predecessor,
    }
    unsigned_receipt = StageReceipt.model_construct(
        **receipt_fields,
        receipt_digest="0" * 64,
    )
    receipt = StageReceipt(
        **receipt_fields,
        receipt_digest=stage_receipt_digest_value(unsigned_receipt),
    )
    network_fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": run.run_id,
        "scenario_digest": run.scenario.scenario_digest,
        "at": target,
        "stage": "network",
        "input_scene_state_digest": scene_state.scene_state_digest,
        "predecessor_barriers": predecessor,
        "provider_ids": (network_provider_id,),
        "receipts": (receipt,),
        "receipt_digests": (receipt.receipt_digest,),
    }
    unsigned_network = StageBarrier.model_construct(
        **network_fields,
        barrier_digest="0" * 64,
    )
    network = StageBarrier(
        **network_fields,
        barrier_digest=stage_barrier_digest_value(unsigned_network),
    )
    assert scene_state.scene_state_digest == scene_state_digest_value(scene_state)
    return scene_state, (motion, network)


def test_inspection_runtime_hook_advances_only_from_validated_real_facts(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    reader = BundleReader(bundle.root)
    package, _, _ = resolve_inspection_run_context(
        reader=reader,
        task=run.task,
        environment=run.environment,
        agents=run.agents,
        scenario=run.scenario,
    )
    agent_id = next(actor.actor_id for actor in package.actors if actor.role == "agent")
    business_id = next(
        actor.actor_id for actor in package.actors if actor.role == "business"
    )
    observation = package.observations[0]
    observation_provider_id = observation.trigger.provider_id
    work_order = next(
        item
        for item in package.work_orders
        if item.required_observation_id == observation.observation_id
    )
    delivery_requirement = next(
        item
        for item in package.artifact_requirements
        if item.evidence_kind == "delivery"
    )
    assert business_id == "business"

    business = _BusinessSession(package, run_id=run.run_id)
    now = [SimulationTime(tick=0, sim_time_ns=0)]
    ledger = EventLedger(run_id=run.run_id)
    ledger.append_event(
        source="harness", event_type="run.started", time=now[0]
    )
    hook = InspectionRuntimeHookFactory().create(
        run=run,
        reader=reader,
        providers={
            business_id: business,
            delivery_requirement.producer_id: _NetworkSession(),
        },
        ledger=ledger,
        runtime_hook_time=lambda: now[0],
    )

    def apply(command) -> None:
        business.service.apply(
            BusinessCommandEnvelope(run_id=run.run_id, command=command)
        )

    apply(
        ClaimWorkOrderCommand(
            command_id="setup.claim",
            work_order_id=work_order.work_order_id,
            actor_id=agent_id,
            issued_at=now[0],
        )
    )
    apply(
        StartWorkOrderCommand(
            command_id="setup.start",
            work_order_id=work_order.work_order_id,
            actor_id=agent_id,
            issued_at=now[0],
        )
    )

    async def scenario() -> None:
        validated = ValidatedObservation(
            run_id=run.run_id,
            agent_id=agent_id,
            provider_id=observation_provider_id,
            observation_id=observation.observation_id,
            time=now[0],
            request_digest="1" * 64,
            payload_digest="2" * 64,
        )
        await hook.on_validated_observation(validated)
        await hook.on_validated_observation(validated)
        assert business.service.state.work_orders[0].status is (
            WorkOrderStatus.OBSERVATION_READY
        )
        assert len(business.commands) == 1

        now[0] = SimulationTime(tick=1, sim_time_ns=100)
        scene_state, stage_barriers = _stage_boundary(
            run=run,
            network_provider_id=delivery_requirement.producer_id,
            target=now[0],
        )
        report_digest = hashlib.sha256(b"report").hexdigest()
        early = _delivery_event(
            run_id=run.run_id,
            provider_id=delivery_requirement.producer_id,
            artifact_id=delivery_requirement.artifact_id,
            agent_id=agent_id,
            work_order_id=work_order.work_order_id,
            payload_digest=report_digest,
            message_id="message.early",
            delivered_at=now[0],
        )
        await hook.on_stage_barriers_closed(
            (early,),
            scene_state=scene_state,
            stage_barriers=stage_barriers,
            target=now[0],
        )
        assert business.service.state.work_orders[0].status is (
            WorkOrderStatus.OBSERVATION_READY
        )

        apply(
            SubmitInspectionCommand(
                command_id="setup.submit",
                work_order_id=work_order.work_order_id,
                actor_id=agent_id,
                issued_at=now[0],
                observation_id=observation.observation_id,
                report_payload_digest=report_digest,
            )
        )
        wrong = _delivery_event(
            run_id=run.run_id,
            provider_id=delivery_requirement.producer_id,
            artifact_id=delivery_requirement.artifact_id,
            agent_id=agent_id,
            work_order_id=work_order.work_order_id,
            payload_digest="3" * 64,
            message_id="message.wrong",
            delivered_at=now[0],
        )
        await hook.on_stage_barriers_closed(
            (wrong,),
            scene_state=scene_state,
            stage_barriers=stage_barriers,
            target=now[0],
        )
        assert business.service.state.work_orders[0].status is WorkOrderStatus.SUBMITTED

        now[0] = SimulationTime(tick=2, sim_time_ns=200)
        scene_state, stage_barriers = _stage_boundary(
            run=run,
            network_provider_id=delivery_requirement.producer_id,
            target=now[0],
            previous_scene_state=scene_state,
        )
        matching = _delivery_event(
            run_id=run.run_id,
            provider_id=delivery_requirement.producer_id,
            artifact_id=delivery_requirement.artifact_id,
            agent_id=agent_id,
            work_order_id=work_order.work_order_id,
            payload_digest=report_digest,
            message_id="message.matching",
            delivered_at=SimulationTime(tick=2, sim_time_ns=150),
            committed_at=now[0],
        )
        await hook.on_stage_barriers_closed(
            (matching,),
            scene_state=scene_state,
            stage_barriers=stage_barriers,
            target=now[0],
        )
        await hook.on_stage_barriers_closed(
            (matching,),
            scene_state=scene_state,
            stage_barriers=stage_barriers,
            target=now[0],
        )
        assert business.service.state.work_orders[0].status is WorkOrderStatus.COMPLETED
        assert business.service.state.work_orders[0].last_time == now[0]
        assert len(business.commands) == 2

    asyncio.run(scenario())

    public_events = [
        record.event.event_type
        for record in ledger.records
        if record.event.event_type.startswith("public.")
    ]
    assert public_events == ["public.status", "public.event"]

    internal_events = tuple(
        record.event
        for record in ledger.records
        if record.event.event_type.startswith("internal.command.")
    )
    assert [event.event_type for event in internal_events].count(
        "internal.command.issued"
    ) == 2
    internal_history = [
        record
        for record in business.service.history
        if record.transition.event in {"observation_ready", "complete"}
    ]
    assert [record.transition.actor_role for record in internal_history] == [
        "observation_provider",
        "business",
    ]
    for event in internal_events:
        payload = {item.name: item.value for item in event.payload}
        assert payload["principal_type"] == "provider_actor"
        assert "agent_id" not in payload
