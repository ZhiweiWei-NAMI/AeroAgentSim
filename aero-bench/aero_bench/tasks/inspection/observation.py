from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from aero_bench.config.models import Identifier, StrictModel
from aero_bench.tasks.inspection.contracts import (
    AuthoritativeViewContext,
    ObservationContract,
    ObservationReceipt,
    ObservationRequest,
    ObservationTrigger,
    PublicObservation,
)


def _time_in_window(time_ns: int, trigger: ObservationTrigger) -> bool:
    return trigger.earliest_time_ns <= time_ns <= trigger.latest_time_ns


class ObservationEligibility(StrictModel):
    """Immutable result of checking a provider-supplied view against a trigger."""

    observation_id: Identifier
    eligible: bool
    failed_conditions: tuple[Identifier, ...]


def evaluate_trigger(
    trigger: ObservationTrigger,
    context: AuthoritativeViewContext,
) -> ObservationEligibility:
    """Evaluate only declared geometry/time conditions from authoritative context."""

    failed: list[str] = []
    if context.observation_id != trigger.observation_id:
        failed.append("observation")
    if context.source_provider_id != trigger.provider_id:
        failed.append("provider")
    if context.camera_id != trigger.camera_id:
        failed.append("camera")
    if context.target_id != trigger.target_id:
        failed.append("target")
    if context.distance_m < trigger.min_distance_m:
        failed.append("distance_below_minimum")
    if context.distance_m > trigger.max_distance_m:
        failed.append("distance_above_maximum")
    if context.view_angle_deg < trigger.min_view_angle_deg:
        failed.append("view_angle_below_minimum")
    if context.view_angle_deg > trigger.max_view_angle_deg:
        failed.append("view_angle_above_maximum")
    if not _time_in_window(context.time.sim_time_ns, trigger):
        failed.append("time_window")
    return ObservationEligibility(
        observation_id=trigger.observation_id,
        eligible=not failed,
        failed_conditions=tuple(failed),
    )


def public_observation(
    *,
    request: ObservationRequest,
    receipt: ObservationReceipt,
    contract: ObservationContract,
    context: AuthoritativeViewContext,
) -> PublicObservation:
    """Build the sole observation shape allowed to cross into an agent."""

    if receipt.run_id != request.run_id:
        raise ValueError("observation receipt belongs to another run")
    if (
        receipt.agent_id != request.agent_id
        or receipt.work_order_id != request.work_order_id
    ):
        raise ValueError("observation receipt identity does not match request")
    if receipt.observation_id != request.observation_id:
        raise ValueError("observation receipt id does not match request")
    if (
        receipt.time.tick < request.requested_at.tick
        or receipt.time.sim_time_ns < request.requested_at.sim_time_ns
    ):
        raise ValueError("observation receipt time moved before request")
    if (
        contract.observation_id != request.observation_id
        or contract.observation_id != context.observation_id
        or contract.target_id != context.target_id
        or context.time != receipt.time
    ):
        raise ValueError("observation context does not match the contract")
    if not evaluate_trigger(contract.trigger, context).eligible:
        raise ValueError("observation context does not satisfy the trigger")
    if receipt.status != "captured" or receipt.payload_digest is None:
        raise ValueError("a rejected observation cannot be exposed to an agent")
    return PublicObservation(
        run_id=receipt.run_id,
        agent_id=receipt.agent_id,
        observation_id=receipt.observation_id,
        time=receipt.time,
        schema_file=contract.metadata_schema,
        payload_digest=receipt.payload_digest,
    )


class ObservationProviderPort(Protocol):
    async def capture(
        self,
        request: ObservationRequest,
        context: AuthoritativeViewContext,
    ) -> ObservationReceipt: ...


class ExternalVisualRequest(StrictModel):
    """Reserved agent fetch against an external renderer.

    Inspection does not capture Gazebo images. The observation data layer
    exposes view geometry; pixels, if needed later, come from this port.
    The renderer is outside the Provider barrier and is not PX4/Gazebo.
    """

    schema_version: Literal["aero-bench.external-visual-request/v1"] = (
        "aero-bench.external-visual-request/v1"
    )
    observation_id: Identifier
    renderer_id: Identifier
    fetch_uri: str = Field(min_length=1, max_length=2048)
    view: AuthoritativeViewContext


class ExternalVisualPort(Protocol):
    async def fetch(self, request: ExternalVisualRequest) -> bytes: ...


__all__ = [
    "ExternalVisualPort",
    "ExternalVisualRequest",
    "ObservationEligibility",
    "ObservationProviderPort",
    "evaluate_trigger",
    "public_observation",
]
