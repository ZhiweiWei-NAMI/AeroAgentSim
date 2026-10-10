from __future__ import annotations

import hashlib
from typing import Protocol

from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import (
    BusinessCommandEnvelope,
    BusinessCommandResult,
    BusinessHistoryRecord,
    BusinessQuery,
    BusinessQueryResult,
    InspectionBusinessState,
    InspectionActorGrant,
    InspectionCommand,
    InspectionTaskPackage,
    TASK_LOCAL_BUSINESS_PRINCIPAL,
    WorkOrderState,
    WorkOrderStatus,
    WorkOrderTransition,
    WorkOrderView,
)


def _time_before(left: object, right: object) -> bool:
    left_tick = getattr(left, "tick")
    right_tick = getattr(right, "tick")
    left_ns = getattr(left, "sim_time_ns")
    right_ns = getattr(right, "sim_time_ns")
    return left_tick < right_tick or left_ns < right_ns


def _next_status(status: WorkOrderStatus, event: str) -> WorkOrderStatus:
    if status is WorkOrderStatus.CREATED and event == "claim":
        return WorkOrderStatus.CLAIMED
    if status is WorkOrderStatus.CLAIMED:
        if event == "start":
            return WorkOrderStatus.IN_PROGRESS
        if event == "fail":
            return WorkOrderStatus.FAILED
        if event == "cancel":
            return WorkOrderStatus.CANCELLED
    if status is WorkOrderStatus.IN_PROGRESS:
        if event == "observation_ready":
            return WorkOrderStatus.OBSERVATION_READY
        if event == "fail":
            return WorkOrderStatus.FAILED
        if event == "cancel":
            return WorkOrderStatus.CANCELLED
    if status is WorkOrderStatus.OBSERVATION_READY:
        if event == "submit":
            return WorkOrderStatus.SUBMITTED
        if event == "fail":
            return WorkOrderStatus.FAILED
        if event == "cancel":
            return WorkOrderStatus.CANCELLED
    if status is WorkOrderStatus.SUBMITTED:
        if event == "complete":
            return WorkOrderStatus.COMPLETED
        if event == "fail":
            return WorkOrderStatus.FAILED
    raise ValueError(f"invalid work order transition: {status.value} -> {event}")


def _validate_actor(
    state: WorkOrderState,
    transition: WorkOrderTransition,
    actor_grants: tuple[InspectionActorGrant, ...],
    *,
    allow_task_local_business_principal: bool = False,
) -> None:
    expected_roles = {
        "claim": ("agent",),
        "start": ("agent",),
        "observation_ready": ("observation_provider",),
        "submit": ("agent",),
        "complete": ("business",),
        "fail": ("business",),
        "cancel": ("business",),
    }[transition.event]
    if transition.actor_role not in expected_roles:
        raise ValueError(
            f"{transition.event} cannot be issued by {transition.actor_role}"
        )
    if transition.actor_id == TASK_LOCAL_BUSINESS_PRINCIPAL:
        if not allow_task_local_business_principal:
            raise ValueError("task-local Business principal is not enabled")
        if (
            transition.actor_role != "business"
            or transition.event != "complete"
            or any(actor.role == "business" for actor in actor_grants)
            or any(
                actor.actor_id == TASK_LOCAL_BUSINESS_PRINCIPAL
                for actor in actor_grants
            )
        ):
            raise ValueError(
                "task-local Business principal may only complete a package without "
                "a Business actor"
            )
    else:
        grant = next(
            (item for item in actor_grants if item.actor_id == transition.actor_id),
            None,
        )
        if grant is None or grant.role != transition.actor_role:
            raise ValueError("actor id is not authorized for the declared actor role")
    if (
        transition.event in {"start", "submit"}
        and state.claimed_by != transition.actor_id
    ):
        raise ValueError("only the claimant may advance an agent work order")


def _validate_transition_fields(transition: WorkOrderTransition) -> None:
    if transition.event in {"claim", "start", "complete"}:
        if any(
            value is not None
            for value in (
                transition.observation_id,
                transition.report_payload_digest,
                transition.reason,
            )
        ):
            raise ValueError(f"{transition.event} cannot carry transition payload")
        return
    if transition.event == "observation_ready":
        if transition.observation_id is None:
            raise ValueError("observation_ready requires observation_id")
        if (
            transition.report_payload_digest is not None
            or transition.reason is not None
        ):
            raise ValueError("observation_ready cannot carry report or failure payload")
        return
    if transition.event == "submit":
        if (
            transition.observation_id is None
            or transition.report_payload_digest is None
        ):
            raise ValueError("submit requires observation and report payload digest")
        if transition.reason is not None:
            raise ValueError("submit cannot carry failure reason")
        return
    if (
        transition.observation_id is not None
        or transition.report_payload_digest is not None
    ):
        raise ValueError(
            f"{transition.event} cannot carry observation or report payload"
        )
    if transition.reason is None:
        raise ValueError(f"{transition.event} requires failure reason")


def _updated_state(state: WorkOrderState, **updates: object) -> WorkOrderState:
    values = state.model_dump()
    values.update(updates)
    return WorkOrderState.model_validate(values)


def _history_record_hash(
    *,
    sequence: int,
    command: BusinessCommandEnvelope,
    transition: WorkOrderTransition,
    previous_hash: str,
) -> str:
    body = {
        "sequence": sequence,
        "command": command.model_dump(mode="json"),
        "transition": transition.model_dump(mode="json"),
        "previous_hash": previous_hash,
    }
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _validate_history_chain(
    history: tuple[BusinessHistoryRecord, ...],
    *,
    run_id: str,
) -> None:
    previous_hash = "0" * 64
    for sequence, record in enumerate(history):
        if record.sequence != sequence:
            raise ValueError("business history sequence is not contiguous")
        if record.command.run_id != run_id or record.transition.run_id != run_id:
            raise ValueError("business history belongs to another run")
        if record.previous_hash != previous_hash:
            raise ValueError("business history hash chain is broken")
        expected_hash = _history_record_hash(
            sequence=record.sequence,
            command=record.command,
            transition=record.transition,
            previous_hash=record.previous_hash,
        )
        if record.record_hash != expected_hash:
            raise ValueError("business history record hash is invalid")
        previous_hash = record.record_hash


def _make_history_record(
    *,
    history: tuple[BusinessHistoryRecord, ...],
    envelope: BusinessCommandEnvelope,
    transition: WorkOrderTransition,
) -> BusinessHistoryRecord:
    previous_hash = history[-1].record_hash if history else "0" * 64
    sequence = len(history)
    return BusinessHistoryRecord(
        sequence=sequence,
        command=envelope,
        transition=transition,
        previous_hash=previous_hash,
        record_hash=_history_record_hash(
            sequence=sequence,
            command=envelope,
            transition=transition,
            previous_hash=previous_hash,
        ),
    )


def advance_work_order(
    state: WorkOrderState,
    transition: WorkOrderTransition,
    *,
    actor_grants: tuple[InspectionActorGrant, ...],
    allow_task_local_business_principal: bool = False,
) -> WorkOrderState:
    """Apply exactly one legal business transition.

    This function is intentionally independent of tool receipts. A receipt can
    document a protocol action, while only this state machine records the
    business state reached by a valid command.
    """

    if state.run_id != transition.run_id:
        raise ValueError("work order transition belongs to another run")
    if state.work_order_id != transition.work_order_id:
        raise ValueError("work order transition targets another work order")
    if _time_before(transition.time, state.last_time):
        raise ValueError("work order transition time moved backwards")
    _validate_transition_fields(transition)
    next_status = _next_status(state.status, transition.event)
    _validate_actor(
        state,
        transition,
        actor_grants,
        allow_task_local_business_principal=allow_task_local_business_principal,
    )

    if transition.event == "claim":
        if state.claimed_by is not None:
            raise ValueError("work order is already claimed")
        return _updated_state(
            state,
            status=next_status,
            version=state.version + 1,
            last_time=transition.time,
            claimed_by=transition.actor_id,
        )

    if transition.event == "observation_ready":
        if transition.observation_id != state.required_observation_id:
            raise ValueError("observation does not satisfy the work order contract")
        return _updated_state(
            state,
            status=next_status,
            version=state.version + 1,
            last_time=transition.time,
            observation_id=transition.observation_id,
        )

    if transition.event == "submit":
        if transition.observation_id != state.required_observation_id:
            raise ValueError("submitted observation does not satisfy the work order")
        if transition.report_payload_digest is None:
            raise ValueError("submission requires report payload digest")
        if state.observation_id != transition.observation_id:
            raise ValueError("submission requires a completed observation")
        return _updated_state(
            state,
            status=next_status,
            version=state.version + 1,
            last_time=transition.time,
            report_payload_digest=transition.report_payload_digest,
        )

    if transition.event in {"fail", "cancel"}:
        if transition.reason is None:
            raise ValueError(f"{transition.event} requires a reason")
        return _updated_state(
            state,
            status=next_status,
            version=state.version + 1,
            last_time=transition.time,
            failure_reason=transition.reason,
        )

    return _updated_state(
        state,
        status=next_status,
        version=state.version + 1,
        last_time=transition.time,
    )


def _transition_for_command(
    *, run_id: str, command: InspectionCommand
) -> WorkOrderTransition:
    return WorkOrderTransition(
        run_id=run_id,
        work_order_id=command.work_order_id,
        event=command.kind,
        actor_id=command.actor_id,
        actor_role=command.actor_role,
        time=command.issued_at,
        observation_id=getattr(command, "observation_id", None),
        report_payload_digest=getattr(command, "report_payload_digest", None),
        reason=getattr(command, "reason", None),
    )


def apply_business_command(
    state: InspectionBusinessState,
    envelope: BusinessCommandEnvelope,
    *,
    actor_grants: tuple[InspectionActorGrant, ...],
    history: tuple[BusinessHistoryRecord, ...],
    allow_task_local_business_principal: bool = False,
) -> BusinessCommandResult:
    if state.run_id != envelope.run_id:
        raise ValueError("business command belongs to another run")
    _validate_history_chain(history, run_id=state.run_id)
    command = envelope.command
    if command.command_id in state.processed_command_ids:
        raise ValueError("command_id was already processed")
    if _time_before(command.issued_at, state.current_time):
        raise ValueError("business command time moved backwards")

    current_index = next(
        (
            index
            for index, work_order in enumerate(state.work_orders)
            if work_order.work_order_id == command.work_order_id
        ),
        None,
    )
    if current_index is None:
        raise ValueError(f"unknown work order: {command.work_order_id}")

    transition = _transition_for_command(run_id=state.run_id, command=command)
    next_work_order = advance_work_order(
        state.work_orders[current_index],
        transition,
        actor_grants=actor_grants,
        allow_task_local_business_principal=allow_task_local_business_principal,
    )
    next_work_orders = (
        *state.work_orders[:current_index],
        next_work_order,
        *state.work_orders[current_index + 1 :],
    )
    next_state = InspectionBusinessState(
        run_id=state.run_id,
        current_time=command.issued_at,
        work_orders=next_work_orders,
        version=state.version + 1,
        processed_command_ids=(*state.processed_command_ids, command.command_id),
    )
    history_record = _make_history_record(
        history=history,
        envelope=envelope,
        transition=transition,
    )
    return BusinessCommandResult(
        run_id=state.run_id,
        command_id=command.command_id,
        transition=transition,
        state=next_state,
        history_record=history_record,
    )


def replay_business_history(
    package: InspectionTaskPackage,
    *,
    run_id: str,
    history: tuple[BusinessHistoryRecord, ...],
    allow_task_local_business_principal: bool = False,
) -> InspectionBusinessState:
    """Replay authoritative commands and compare each deterministic transition."""

    _validate_history_chain(history, run_id=run_id)
    state = package.initial_business_state(run_id=run_id)
    prefix: tuple[BusinessHistoryRecord, ...] = ()
    for record in history:
        result = apply_business_command(
            state,
            record.command,
            actor_grants=package.actors,
            history=prefix,
            allow_task_local_business_principal=allow_task_local_business_principal,
        )
        if result.history_record != record:
            raise ValueError("business history transition does not replay")
        state = result.state
        prefix = (*prefix, record)
    return state


def query_business_state(
    state: InspectionBusinessState,
    query: BusinessQuery,
) -> BusinessQueryResult:
    if state.run_id != query.run_id:
        raise ValueError("business query belongs to another run")
    if _time_before(query.issued_at, state.current_time):
        raise ValueError("business query time moved backwards")
    if query.kind == "work_order":
        selected = tuple(
            item
            for item in state.work_orders
            if item.work_order_id == query.work_order_id
        )
        if not selected:
            raise ValueError(f"unknown work order: {query.work_order_id}")
    else:
        selected = state.work_orders
    return BusinessQueryResult(
        run_id=state.run_id,
        query_id=query.query_id,
        observed_at=query.issued_at,
        work_orders=tuple(
            WorkOrderView(
                work_order_id=item.work_order_id,
                target_id=item.target_id,
                status=item.status,
                version=item.version,
                last_time=item.last_time,
                claimed_by=item.claimed_by,
                observation_id=item.observation_id,
                report_payload_digest=item.report_payload_digest,
            )
            for item in selected
        ),
    )


class WorkOrderStateMachine:
    """Single-work-order deterministic state machine for provider integration."""

    def __init__(
        self,
        initial: WorkOrderState,
        *,
        actor_grants: tuple[InspectionActorGrant, ...],
        allow_task_local_business_principal: bool = False,
    ) -> None:
        self._state = initial
        self._actor_grants = actor_grants
        self._allow_task_local_business_principal = (
            allow_task_local_business_principal
        )

    @property
    def state(self) -> WorkOrderState:
        return self._state

    def apply(self, transition: WorkOrderTransition) -> WorkOrderState:
        self._state = advance_work_order(
            self._state,
            transition,
            actor_grants=self._actor_grants,
            allow_task_local_business_principal=(
                self._allow_task_local_business_principal
            ),
        )
        return self._state


class InspectionBusinessService:
    """Task-scoped command/query service; it is not a general workflow engine."""

    def __init__(
        self,
        package: InspectionTaskPackage,
        *,
        run_id: str,
        allow_task_local_business_principal: bool = False,
    ) -> None:
        self._state = package.initial_business_state(run_id=run_id)
        self._actor_grants = package.actors
        self._allow_task_local_business_principal = (
            allow_task_local_business_principal
        )
        self._history: tuple[BusinessHistoryRecord, ...] = ()

    @property
    def state(self) -> InspectionBusinessState:
        return self._state

    @property
    def history(self) -> tuple[BusinessHistoryRecord, ...]:
        return self._history

    def apply(self, envelope: BusinessCommandEnvelope) -> BusinessCommandResult:
        result = apply_business_command(
            self._state,
            envelope,
            actor_grants=self._actor_grants,
            history=self._history,
            allow_task_local_business_principal=(
                self._allow_task_local_business_principal
            ),
        )
        self._state = result.state
        self._history = (*self._history, result.history_record)
        return result

    def query(self, request: BusinessQuery) -> BusinessQueryResult:
        return query_business_state(self._state, request)


class BusinessCommandQueryPort(Protocol):
    def apply(self, envelope: BusinessCommandEnvelope) -> BusinessCommandResult: ...

    def query(self, request: BusinessQuery) -> BusinessQueryResult: ...


__all__ = [
    "BusinessCommandQueryPort",
    "InspectionBusinessService",
    "WorkOrderStateMachine",
    "advance_work_order",
    "apply_business_command",
    "query_business_state",
    "replay_business_history",
]
