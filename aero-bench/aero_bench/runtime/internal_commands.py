from __future__ import annotations

import asyncio
import hashlib
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from aero_bench.config.models import Identifier, NamedValue, StrictModel
from aero_bench.providers.contracts import ProviderCommandResult, ProviderSession
from aero_bench.runtime.contracts import (
    CommandReceipt,
    CommandRequest,
    ProviderEvent,
    SimulationTime,
)
from aero_bench.runtime.ledger import (
    RESERVED_RUNTIME_EVENT_TYPES,
    EventLedger,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.vocabulary import PUBLIC_PROVIDER_EVENT_TYPES


class InternalCommandError(RuntimeError):
    pass


class InternalCommandAuthority(StrictModel):
    """Package-scoped authority for one Provider actor and one Provider tool."""

    authority_id: Identifier
    package_id: Identifier
    actor_id: Identifier
    actor_role: Literal["observation_provider", "business"]
    provider_id: Identifier
    tool_id: Identifier


@dataclass(slots=True)
class _Dispatch:
    authority_id: str
    request_digest: str
    result: ProviderCommandResult | None = None


class InternalCommandDispatcher:
    """Dispatch authenticated Provider-actor commands without fabricating Agent grants."""

    def __init__(
        self,
        *,
        run_id: str,
        package_id: str,
        authorities: tuple[InternalCommandAuthority, ...],
        providers: Mapping[str, ProviderSession],
        authoritative_time: Callable[[], SimulationTime],
        ledger: EventLedger,
        timeout_ms: int,
    ):
        if not callable(authoritative_time):
            raise TypeError("authoritative_time must be callable")
        if not isinstance(ledger, EventLedger):
            raise TypeError("ledger must be EventLedger")
        if ledger.run_id != run_id:
            raise ValueError("internal command ledger belongs to another run")
        if timeout_ms <= 0:
            raise ValueError("internal command timeout must be positive")
        by_id = {authority.authority_id: authority for authority in authorities}
        if len(by_id) != len(authorities):
            raise ValueError("internal command authority IDs must be unique")
        if any(authority.package_id != package_id for authority in authorities):
            raise ValueError("internal command authority belongs to another package")
        missing = {
            authority.provider_id
            for authority in authorities
            if authority.provider_id not in providers
        }
        if missing:
            raise ValueError(
                f"internal command authority names unavailable Providers: {sorted(missing)}"
            )
        self._run_id = run_id
        self._package_id = package_id
        self._authorities = by_id
        self._providers = dict(providers)
        self._authoritative_time = authoritative_time
        self._ledger = ledger
        self._timeout_seconds = timeout_ms / 1000
        self._dispatches: dict[str, _Dispatch] = {}
        self._lock = asyncio.Lock()

    async def invoke(
        self, *, authority_id: str, request: CommandRequest
    ) -> ProviderCommandResult:
        async with self._lock:
            return await self._invoke_locked(
                authority_id=authority_id,
                request=request,
            )

    async def _invoke_locked(
        self, *, authority_id: str, request: CommandRequest
    ) -> ProviderCommandResult:
        authority = self._authorities.get(authority_id)
        if authority is None:
            raise InternalCommandError("internal command authority is not registered")
        if not isinstance(request, CommandRequest):
            raise InternalCommandError("internal command request is invalid")
        if request.run_id != self._run_id:
            raise InternalCommandError("internal command belongs to another run")
        if request.agent_id != authority.actor_id:
            raise InternalCommandError(
                "internal command actor differs from its authority"
            )
        if request.tool_id != authority.tool_id:
            raise InternalCommandError(
                "internal command tool differs from its authority"
            )

        request_digest = hashlib.sha256(
            canonical_json_bytes(request.model_dump(mode="json"))
        ).hexdigest()
        previous = self._dispatches.get(request.command_id)
        if previous is not None:
            if (
                previous.authority_id != authority_id
                or previous.request_digest != request_digest
            ):
                raise InternalCommandError(
                    "internal command_id was reused with different authority or content"
                )
            if previous.result is None:
                raise InternalCommandError(
                    "internal command was dispatched without a validated result; "
                    "use a new command_id"
                )
            return previous.result

        current_time = self._current_time()
        if request.issued_at != current_time:
            raise InternalCommandError(
                "internal command time must equal authoritative simulation time"
            )
        dispatch = _Dispatch(
            authority_id=authority_id,
            request_digest=request_digest,
        )
        self._dispatches[request.command_id] = dispatch
        self._append_audit(
            event_type="internal.command.issued",
            time=current_time,
            authority=authority,
            request=request,
            request_digest=request_digest,
        )

        try:
            raw_result = await asyncio.wait_for(
                self._providers[authority.provider_id].handle_command(request),
                timeout=self._timeout_seconds,
            )
            if self._current_time() != current_time:
                raise InternalCommandError(
                    "authoritative simulation time advanced during internal command"
                )
            result = self._validate_result(
                raw_result,
                authority=authority,
                request=request,
                expected_time=current_time,
            )
        except asyncio.TimeoutError as error:
            self._log_failure(request, error)
            self._append_failure(
                authority=authority,
                request=request,
                request_digest=request_digest,
                expected_time=current_time,
                failure_class="provider_timeout",
            )
            raise InternalCommandError("internal Provider command timed out") from error
        except Exception as error:
            self._log_failure(request, error)
            self._append_failure(
                authority=authority,
                request=request,
                request_digest=request_digest,
                expected_time=current_time,
                failure_class="dispatch_or_validation_failed",
            )
            if isinstance(error, InternalCommandError):
                raise
            raise InternalCommandError("internal Provider command failed") from error

        for receipt in result.receipts:
            self._append_audit(
                source=receipt.provider_id,
                event_type="internal.command.receipt",
                time=receipt.time,
                authority=authority,
                request=request,
                request_digest=request_digest,
                extra={"phase": receipt.phase},
            )
        for event in result.events:
            payload = {item.name: item.value for item in event.payload}
            command_id = (
                payload.get("command_id")
                if isinstance(payload.get("command_id"), str)
                else request.command_id
            )
            parent_event_id = self._ledger.latest_event_id(command_id)
            if event.event_id.endswith(".applied.physical"):
                command_record = self._ledger.append_event(
                    source=event.provider_id,
                    source_kind="provider",
                    workload_id=event.provider_id,
                    event_type=f"{event.event_id}.mavlink-command",
                    time=event.time,
                    payload=event.payload,
                    payload_schema_id=event.payload_schema_id,
                    interaction_type="mavlink.command.v1",
                    correlation_id=command_id,
                    parent_event_id=parent_event_id,
                    provider_id=event.provider_id,
                    command_id=command_id,
                )
                parent_event_id = command_record.event.event_id
            self._ledger.append_event(
                source=event.provider_id,
                source_kind="provider",
                workload_id=event.provider_id,
                event_type=event.event_id,
                time=event.time,
                payload=event.payload,
                payload_schema_id=event.payload_schema_id,
                interaction_type=(
                    "mavlink.command_ack.v1"
                    if event.event_id.endswith(".applied.physical")
                    else "physical.effect.v1"
                    if event.event_id.endswith(".physical")
                    else "provider.command.v1"
                ),
                correlation_id=command_id,
                parent_event_id=parent_event_id,
                provider_id=event.provider_id,
                command_id=command_id,
            )
        self._append_audit(
            event_type="internal.command.validated",
            time=current_time,
            authority=authority,
            request=request,
            request_digest=request_digest,
            extra={
                "outcome": result.receipts[-1].phase,
                "result_digest": hashlib.sha256(
                    canonical_json_bytes(result.model_dump(mode="json"))
                ).hexdigest(),
            },
        )
        dispatch.result = result
        return result

    @staticmethod
    def _log_failure(request: CommandRequest, error: BaseException) -> None:
        print(
            "internal Provider command failed "
            f"command_id={request.command_id!r} tool_id={request.tool_id!r} "
            f"exception={type(error).__name__}: {error}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exception(error, file=sys.stderr)

    def _validate_result(
        self,
        raw_result: object,
        *,
        authority: InternalCommandAuthority,
        request: CommandRequest,
        expected_time: SimulationTime,
    ) -> ProviderCommandResult:
        if not isinstance(raw_result, ProviderCommandResult):
            raise InternalCommandError("Provider returned an invalid internal result")
        try:
            result = ProviderCommandResult.model_validate(
                raw_result.model_dump(mode="json")
            )
        except (TypeError, ValueError) as error:
            raise InternalCommandError(
                "Provider result failed strict revalidation"
            ) from error
        if result != raw_result:
            raise InternalCommandError("Provider result is not canonical")
        phases = tuple(receipt.phase for receipt in result.receipts)
        valid_phases = {
            ("received", "accepted", "applied", "completed"),
            ("received", "failed"),
            ("received", "accepted", "failed"),
            ("received", "accepted", "applied", "failed"),
        }
        if phases not in valid_phases:
            raise InternalCommandError("internal command receipt phases are invalid")
        for receipt in result.receipts:
            self._validate_receipt(
                receipt,
                authority=authority,
                request=request,
                expected_time=expected_time,
            )
        if result.events and "applied" not in phases:
            raise InternalCommandError(
                "internal command events require an applied receipt"
            )
        fingerprints: set[bytes] = set()
        for event in result.events:
            self._validate_event(
                event,
                authority=authority,
                request=request,
                expected_time=expected_time,
            )
            fingerprint = canonical_json_bytes(event.model_dump(mode="json"))
            if fingerprint in fingerprints:
                raise InternalCommandError(
                    "Provider returned a duplicate internal event"
                )
            fingerprints.add(fingerprint)
        return result

    def _validate_receipt(
        self,
        receipt: CommandReceipt,
        *,
        authority: InternalCommandAuthority,
        request: CommandRequest,
        expected_time: SimulationTime,
    ) -> None:
        if (
            receipt.run_id != request.run_id
            or receipt.command_id != request.command_id
            or receipt.provider_id != authority.provider_id
            or receipt.time != expected_time
        ):
            raise InternalCommandError("internal command receipt identity is invalid")

    def _validate_event(
        self,
        event: ProviderEvent,
        *,
        authority: InternalCommandAuthority,
        request: CommandRequest,
        expected_time: SimulationTime,
    ) -> None:
        if event.event_id in RESERVED_RUNTIME_EVENT_TYPES:
            raise InternalCommandError("Provider used a reserved internal event type")
        if event.provider_id != authority.provider_id or event.time != expected_time:
            raise InternalCommandError("internal command event identity is invalid")
        if event.event_id in PUBLIC_PROVIDER_EVENT_TYPES:
            return
        payload = {item.name: item.value for item in event.payload}
        if not isinstance(payload.get("schema_id"), str):
            raise InternalCommandError("internal command event has no schema identity")
        if payload.get("command_id") != request.command_id:
            raise InternalCommandError(
                "internal command event belongs to another command"
            )
        if "run_id" in payload and payload["run_id"] != request.run_id:
            raise InternalCommandError("internal command event belongs to another run")
        if "provider_id" in payload and payload["provider_id"] != authority.provider_id:
            raise InternalCommandError("internal command event names another Provider")

    def _append_failure(
        self,
        *,
        authority: InternalCommandAuthority,
        request: CommandRequest,
        request_digest: str,
        expected_time: SimulationTime,
        failure_class: str,
    ) -> None:
        self._append_audit(
            event_type="internal.command.failure",
            time=self._failure_time(expected_time),
            authority=authority,
            request=request,
            request_digest=request_digest,
            extra={"failure_class": failure_class},
        )

    def _append_audit(
        self,
        *,
        event_type: str,
        time: SimulationTime,
        authority: InternalCommandAuthority,
        request: CommandRequest,
        request_digest: str,
        extra: dict[str, object] | None = None,
        source: str = "runtime-hook",
    ) -> None:
        payload: dict[str, object] = {
            "run_id": self._run_id,
            "package_id": self._package_id,
            "authority_id": authority.authority_id,
            "principal_type": "provider_actor",
            "actor_id": authority.actor_id,
            "actor_role": authority.actor_role,
            "command_id": request.command_id,
            "tool_id": request.tool_id,
            "provider_id": authority.provider_id,
            "request_digest": request_digest,
            "arguments_json": canonical_json_bytes(
                [item.model_dump(mode="json") for item in request.arguments]
            ).decode("utf-8"),
        }
        if extra:
            payload.update(extra)
        parent_event_id = self._ledger.latest_event_id(request.command_id)
        interaction_type = (
            "gateway.dispatch.v1"
            if event_type == "internal.command.issued"
            else "provider.command.v1"
        )
        source_kind = (
            "provider" if source == authority.provider_id else "harness"
        )
        workload_id = authority.provider_id if source_kind == "provider" else "harness"
        self._ledger.append_event(
            source=source,
            source_kind=source_kind,
            workload_id=workload_id,
            event_type=event_type,
            time=time,
            payload=tuple(
                NamedValue(name=name, value=value)
                for name, value in sorted(payload.items())
            ),
            interaction_type=interaction_type,
            correlation_id=request.command_id,
            parent_event_id=parent_event_id,
            provider_id=authority.provider_id,
            command_id=request.command_id,
        )

    def _current_time(self) -> SimulationTime:
        try:
            current = SimulationTime.model_validate(self._authoritative_time())
        except Exception as error:
            raise InternalCommandError(
                "authoritative simulation time is unavailable"
            ) from error
        if self._ledger.records and self._time_before(
            current, self._ledger.records[-1].event.time
        ):
            raise InternalCommandError("authoritative simulation time moved backwards")
        return current

    def _failure_time(self, expected: SimulationTime) -> SimulationTime:
        try:
            current = SimulationTime.model_validate(self._authoritative_time())
        except Exception:
            current = expected
        if self._time_before(current, expected):
            current = expected
        if self._ledger.records and self._time_before(
            current, self._ledger.records[-1].event.time
        ):
            current = self._ledger.records[-1].event.time
        return current

    @staticmethod
    def _time_before(left: SimulationTime, right: SimulationTime) -> bool:
        return left.tick < right.tick or left.sim_time_ns < right.sim_time_ns


__all__ = [
    "InternalCommandAuthority",
    "InternalCommandDispatcher",
    "InternalCommandError",
]
