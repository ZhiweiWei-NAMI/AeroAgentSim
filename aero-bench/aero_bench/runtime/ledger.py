from __future__ import annotations

import io
import json
import time as wall_clock
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated

from pydantic import Field, TypeAdapter, model_validator

from aero_bench.config.models import Identifier, NamedValue, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime, StateAttribute
from aero_bench.runtime.events import (
    RUN_EVENT_CHAIN_ROOT,
    AgentInteraction,
    AgentInteractionType,
    RunEvent,
    RunEventAudience,
    RunEventSegmentKind,
    RunEventSourceKind,
    agent_interaction_digest_value,
    run_event_digest_value,
    run_event_payload_digest_value,
)
from aero_bench.serialization import canonical_json_bytes


RESERVED_RUNTIME_EVENT_TYPES = frozenset(
    {
        "agent.turn-completion",
        "agent.turn-decision",
        "agent.decision-summary",
        "barrier.committed",
        "barrier.ready",
        "command.issued",
        "command.receipt",
        "command.validated",
        "control.pause",
        "control.resume",
        "control.step",
        "control.stop",
        "gateway.dispatch",
        "gateway.failure",
        "internal.command.failure",
        "internal.command.issued",
        "internal.command.receipt",
        "internal.command.validated",
        "observation.requested",
        "observation.validated",
        "process.stderr",
        "process.stdout",
        "provider.reset",
        "provider.step-receipt",
        "px4.telemetry",
        "run.aborted",
        "run.completed",
        "run.started",
        "runtime.identity",
        "scene.entity-state",
        "scene.state.committed",
        "sensor.frame-ref",
        "stage.barrier-closed",
        "validation.scope",
    }
)


class LedgerRecord(StrictModel):
    sequence: Annotated[int, Field(ge=0)]
    event: RunEvent
    previous_hash: Sha256
    event_hash: Sha256

    @model_validator(mode="after")
    def run_event_is_the_record(self) -> "LedgerRecord":
        if self.sequence != self.event.sequence:
            raise ValueError("ledger sequence differs from RunEvent sequence")
        if self.previous_hash != self.event.previous_event_digest:
            raise ValueError("ledger previous_hash differs from RunEvent chain binding")
        if self.event_hash != self.event.event_digest:
            raise ValueError("ledger event_hash differs from RunEvent digest")
        return self


class EventLedger:
    _TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.aborted"})
    _HARNESS_ONLY_EVENT_TYPES = _TERMINAL_EVENT_TYPES | frozenset(
        {"scene.entity-state", "scene.state.committed", "stage.barrier-closed"}
    )

    def __init__(
        self,
        *,
        run_id: str,
        wall_time_ns: Callable[[], int] = wall_clock.time_ns,
    ) -> None:
        try:
            validated_run_id = TypeAdapter(Sha256).validate_python(run_id)
        except (TypeError, ValueError) as error:
            raise ValueError("EventLedger run_id must be a SHA-256 digest") from error
        if validated_run_id == RUN_EVENT_CHAIN_ROOT:
            raise ValueError("EventLedger run_id cannot be a placeholder digest")
        if not callable(wall_time_ns):
            raise TypeError("EventLedger wall_time_ns must be callable")
        self._run_id = validated_run_id
        self._wall_time_ns = wall_time_ns
        self._records: list[LedgerRecord] = []
        # Event identifiers and correlation bindings are append-only runtime
        # indexes.  The ledger is validated on every append, so rebuilding an
        # ID set from the complete history would make a long deterministic run
        # quadratic in its event count.
        self._event_ids: set[str] = set()
        self._events_by_id: dict[str, RunEvent] = {}
        self._latest_correlation_event_ids: dict[str, str] = {}

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def records(self) -> tuple[LedgerRecord, ...]:
        return tuple(self._records)

    @property
    def record_count(self) -> int:
        """Return the number of records without materializing a snapshot."""

        return len(self._records)

    @property
    def latest_record(self) -> LedgerRecord | None:
        """Return the current tail without copying the record history."""

        return self._records[-1] if self._records else None

    def iter_records(self, start_sequence: int = 0) -> Iterator[LedgerRecord]:
        """Iterate over an append-only record suffix without a tuple copy."""

        if (
            not isinstance(start_sequence, int)
            or isinstance(start_sequence, bool)
            or start_sequence < 0
        ):
            raise ValueError("ledger record start sequence must be a nonnegative integer")

        def records_from_start() -> Iterator[LedgerRecord]:
            for sequence in range(start_sequence, len(self._records)):
                yield self._records[sequence]

        return records_from_start()

    def iter_records_reversed(self) -> Iterator[LedgerRecord]:
        """Iterate over records newest-first without a tuple copy."""

        return reversed(self._records)

    @property
    def chain_root(self) -> str:
        return self._records[-1].event_hash if self._records else RUN_EVENT_CHAIN_ROOT

    def latest_event_id(self, correlation_id: str) -> str | None:
        return self._latest_correlation_event_ids.get(correlation_id)

    def event_by_id(self, event_id: str) -> RunEvent | None:
        """Return an authoritative event by identifier without scanning history."""

        return self._events_by_id.get(event_id)

    @classmethod
    def from_records(cls, records: tuple[LedgerRecord, ...]) -> "EventLedger":
        if not records:
            raise ValueError("authoritative event ledger cannot be empty")
        ledger = cls(run_id=records[0].event.run_id)
        ledger._records.extend(records)
        ledger._rebuild_indexes()
        ledger.verify()
        return ledger

    @classmethod
    def read_jsonl(cls, source: Path) -> "EventLedger":
        try:
            content = source.resolve(strict=True).read_bytes()
        except (OSError, RuntimeError) as error:
            raise ValueError("authoritative event ledger is unavailable") from error
        if not content or not content.endswith(b"\n"):
            raise ValueError(
                "authoritative event ledger must be non-empty canonical JSONL"
            )
        raw_lines = content[:-1].split(b"\n")
        if not raw_lines or any(not line for line in raw_lines):
            raise ValueError("authoritative event ledger contains an empty record")

        records: list[LedgerRecord] = []
        for raw_line in raw_lines:
            try:
                decoded = raw_line.decode("utf-8")
                raw = json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError(
                    "authoritative event ledger contains invalid JSON"
                ) from error
            record = LedgerRecord.model_validate(raw)
            if raw_line != canonical_json_bytes(record.model_dump(mode="json")):
                raise ValueError(
                    "authoritative event ledger record is not canonical JSON"
                )
            records.append(record)
        return cls.from_records(tuple(records))

    def new_event(
        self,
        *,
        source: str,
        event_type: str,
        time: SimulationTime,
        payload: tuple[NamedValue | StateAttribute, ...] = (),
        source_kind: RunEventSourceKind | None = None,
        workload_id: str | None = None,
        payload_schema_id: str | None = None,
        segment_kind: RunEventSegmentKind = "runtime",
        interaction_type: AgentInteractionType | None = None,
        correlation_id: str | None = None,
        parent_event_id: str | None = None,
        causal_event_ids: tuple[str, ...] = (),
        visibility: tuple[RunEventAudience, ...] | None = None,
        frame_id: str | None = None,
        agent_id: str | None = None,
        provider_id: str | None = None,
        vehicle_id: str | None = None,
        entity_id: str | None = None,
        command_id: str | None = None,
        query_id: str | None = None,
        observation_id: str | None = None,
        wall_time_ns: int | None = None,
    ) -> RunEvent:
        self._require_open()
        sequence = len(self._records)
        event_id = f"event.{sequence:016d}"
        previous_event_digest = self.chain_root
        canonical_payload = self._state_attributes(payload)
        payload_values = {
            attribute.name: attribute.value for attribute in canonical_payload
        }
        if len(payload_values) != len(canonical_payload):
            raise ValueError("RunEvent payload names must be unique")

        source_kind = source_kind or self._infer_source_kind(source)
        workload_id = workload_id or source
        payload_schema_id = payload_schema_id or (
            event_type if event_type.endswith(".v1") else f"{event_type}.v1"
        )
        agent_id = agent_id or self._payload_identifier(payload_values, "agent_id")
        provider_id = provider_id or self._payload_identifier(
            payload_values, "provider_id"
        )
        vehicle_id = vehicle_id or self._payload_identifier(
            payload_values, "vehicle_id"
        )
        entity_id = entity_id or self._payload_identifier(payload_values, "entity_id")
        command_id = command_id or self._payload_identifier(
            payload_values, "command_id"
        )
        query_id = query_id or self._payload_identifier(payload_values, "query_id")
        observation_id = observation_id or self._payload_identifier(
            payload_values, "observation_id"
        )
        correlation_id = (
            correlation_id or command_id or query_id or observation_id or event_id
        )
        causal = set(causal_event_ids)
        if parent_event_id is not None:
            causal.add(parent_event_id)
        canonical_causal_event_ids = tuple(sorted(causal))
        canonical_visibility = tuple(
            sorted(
                visibility
                or (RunEventAudience(scope="private", audience_id=None),),
                key=lambda item: (item.scope, item.audience_id or ""),
            )
        )
        event_time = SimulationTime.model_validate(time)
        captured_wall_time_ns = self._capture_wall_time(wall_time_ns)
        payload_digest = run_event_payload_digest_value(canonical_payload)

        interaction: AgentInteraction | None = None
        if interaction_type is not None:
            interaction_fields = {
                "schema_version": "aero-bench.agent-interaction/v1",
                "segment_kind": segment_kind,
                "interaction_type": interaction_type,
                "run_id": self._run_id,
                "sequence": sequence,
                "event_id": event_id,
                "time": event_time,
                "wall_time_ns": captured_wall_time_ns,
                "source_kind": source_kind,
                "source": source,
                "workload_id": workload_id,
                "correlation_id": correlation_id,
                "parent_event_id": parent_event_id,
                "causal_event_ids": canonical_causal_event_ids,
                "visibility": canonical_visibility,
                "frame_id": frame_id,
                "agent_id": agent_id,
                "provider_id": provider_id,
                "vehicle_id": vehicle_id,
                "entity_id": entity_id,
                "command_id": command_id,
                "query_id": query_id,
                "observation_id": observation_id,
                "payload_schema_id": payload_schema_id,
                "payload": canonical_payload,
                "payload_digest": payload_digest,
            }
            candidate = AgentInteraction.model_construct(
                **interaction_fields,
                interaction_digest=RUN_EVENT_CHAIN_ROOT,
            )
            interaction = AgentInteraction(
                **interaction_fields,
                interaction_digest=agent_interaction_digest_value(candidate),
            )

        event_fields = {
            "schema_version": "aero-bench.run-event/v1",
            "segment_kind": segment_kind,
            "run_id": self._run_id,
            "sequence": sequence,
            "event_id": event_id,
            "source_kind": source_kind,
            "source": source,
            "workload_id": workload_id,
            "event_type": event_type,
            "time": event_time,
            "wall_time_ns": captured_wall_time_ns,
            "correlation_id": correlation_id,
            "parent_event_id": parent_event_id,
            "causal_event_ids": canonical_causal_event_ids,
            "visibility": canonical_visibility,
            "frame_id": frame_id,
            "agent_id": agent_id,
            "provider_id": provider_id,
            "vehicle_id": vehicle_id,
            "entity_id": entity_id,
            "command_id": command_id,
            "query_id": query_id,
            "observation_id": observation_id,
            "payload_schema_id": payload_schema_id,
            "payload": canonical_payload,
            "payload_digest": payload_digest,
            "interaction": interaction,
            "previous_event_digest": previous_event_digest,
        }
        event_candidate = RunEvent.model_construct(
            **event_fields,
            event_digest=RUN_EVENT_CHAIN_ROOT,
        )
        return RunEvent(
            **event_fields,
            event_digest=run_event_digest_value(event_candidate),
        )

    def append_event(self, **fields: object) -> LedgerRecord:
        return self.append(self.new_event(**fields))  # type: ignore[arg-type]

    def prospective_chain_root(self, event: RunEvent) -> str:
        self._require_open()
        self._validate_next_event(event)
        self._validate_event_authority(event)
        return event.event_digest

    def append(self, event: RunEvent) -> LedgerRecord:
        self._require_open()
        self._validate_next_event(event)
        self._validate_event_authority(event)
        record = LedgerRecord(
            sequence=event.sequence,
            event=event,
            previous_hash=event.previous_event_digest,
            event_hash=event.event_digest,
        )
        self._records.append(record)
        self._index_record(record)
        return record

    def _validate_next_event(self, event: RunEvent) -> None:
        if not isinstance(event, RunEvent):
            raise TypeError("EventLedger accepts RunEvent values only")
        if event.run_id != self._run_id:
            raise ValueError("RunEvent belongs to another run")
        if event.segment_kind != "runtime":
            raise ValueError("authoritative runtime ledger accepts runtime events only")
        if event.sequence != len(self._records):
            raise ValueError("RunEvent sequence is not the next global sequence")
        if event.previous_event_digest != self.chain_root:
            raise ValueError("RunEvent previous digest differs from the ledger root")
        if any(
            event_id not in self._event_ids for event_id in event.causal_event_ids
        ):
            raise ValueError("RunEvent causal graph references an unknown prior event")
        if self._records:
            previous = self._records[-1].event
            if self._time_before(event.time, previous.time):
                raise ValueError("RunEvent simulation time moved backwards")
            if event.wall_time_ns < previous.wall_time_ns:
                raise ValueError("RunEvent wall time moved backwards")

    def _require_open(self) -> None:
        if (
            self._records
            and self._records[-1].event.event_type in self._TERMINAL_EVENT_TYPES
        ):
            raise RuntimeError("authoritative event ledger is already terminal")

    def _index_record(self, record: LedgerRecord) -> None:
        event = record.event
        self._event_ids.add(event.event_id)
        self._events_by_id[event.event_id] = event
        self._latest_correlation_event_ids[event.correlation_id] = event.event_id

    def _rebuild_indexes(self) -> None:
        self._event_ids = set()
        self._events_by_id = {}
        self._latest_correlation_event_ids = {}
        for record in self._records:
            self._index_record(record)

    @classmethod
    def _validate_event_authority(cls, event: RunEvent) -> None:
        if event.event_type in cls._TERMINAL_EVENT_TYPES and event.source != "harness":
            raise ValueError(
                "authoritative terminal event must originate from the Harness"
            )
        if (
            event.event_type
            in cls._HARNESS_ONLY_EVENT_TYPES - cls._TERMINAL_EVENT_TYPES
            and event.source != "harness"
        ):
            raise ValueError(
                "authoritative staged event must originate from the Harness"
            )

    def _capture_wall_time(self, supplied: int | None) -> int:
        value = self._wall_time_ns() if supplied is None else supplied
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("RunEvent wall_time_ns must be a nonnegative integer")
        if self._records:
            value = max(value, self._records[-1].event.wall_time_ns)
        return value

    @staticmethod
    def _state_attributes(
        payload: tuple[NamedValue | StateAttribute, ...],
    ) -> tuple[StateAttribute, ...]:
        attributes: list[StateAttribute] = []
        for item in payload:
            if isinstance(item, StateAttribute):
                attribute = item
            elif isinstance(item, NamedValue):
                value = item.value
                if value is None:
                    value_type = "null"
                elif isinstance(value, bool):
                    value_type = "bool"
                elif isinstance(value, int):
                    value_type = "int"
                elif isinstance(value, float):
                    value_type = "float"
                else:
                    value_type = "str"
                attribute = StateAttribute(
                    name=item.name,
                    value_type=value_type,
                    value=value,
                )
            else:
                raise TypeError("RunEvent payload values must be typed attributes")
            attributes.append(attribute)
        return tuple(sorted(attributes, key=lambda item: item.name))

    @staticmethod
    def _payload_identifier(
        payload: dict[str, object], name: str
    ) -> str | None:
        value = payload.get(name)
        if not isinstance(value, str):
            return None
        try:
            return TypeAdapter(Identifier).validate_python(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _infer_source_kind(source: str) -> RunEventSourceKind:
        if source == "harness":
            return "harness"
        if source == "gateway":
            return "gateway"
        if source == "executor":
            return "executor"
        if source.startswith("verifier"):
            return "verifier"
        if source.startswith("agent"):
            return "agent"
        return "provider"

    def verify(self) -> None:
        previous_hash = RUN_EVENT_CHAIN_ROOT
        previous_time = SimulationTime(tick=0, sim_time_ns=0)
        previous_wall_time_ns = 0
        known_event_ids: set[str] = set()
        for index, record in enumerate(self._records):
            event = record.event
            self._validate_event_authority(event)
            if (
                index > 0
                and self._records[index - 1].event.event_type
                in self._TERMINAL_EVENT_TYPES
            ):
                raise ValueError(
                    "authoritative event ledger contains a post-terminal event"
                )
            if record.sequence != index or event.sequence != index:
                raise ValueError("event sequence is not contiguous")
            if event.run_id != self._run_id:
                raise ValueError("event ledger contains another run identity")
            if self._time_before(event.time, previous_time):
                raise ValueError("event simulation time moved backwards")
            if event.wall_time_ns < previous_wall_time_ns:
                raise ValueError("event wall time moved backwards")
            if (
                record.previous_hash != previous_hash
                or event.previous_event_digest != previous_hash
            ):
                raise ValueError("event hash chain is broken")
            if record.event_hash != event.event_digest:
                raise ValueError("ledger record hash differs from RunEvent digest")
            if event.event_digest != run_event_digest_value(event):
                raise ValueError("RunEvent content hash is invalid")
            if any(
                event_id not in known_event_ids
                for event_id in event.causal_event_ids
            ):
                raise ValueError(
                    "RunEvent causal graph references an unknown prior event"
                )
            known_event_ids.add(event.event_id)
            previous_hash = event.event_digest
            previous_time = event.time
            previous_wall_time_ns = event.wall_time_ns

    @staticmethod
    def _time_before(left: SimulationTime, right: SimulationTime) -> bool:
        return (
            left.tick < right.tick
            or left.sim_time_ns < right.sim_time_ns
        )

    def write_jsonl(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(ledger_jsonl_bytes(tuple(self._records)))


class VerifierEventLedger(EventLedger):
    """Append-only verifier segment continuing an immutable runtime ledger.

    The runtime records are retained only as a read-only chain prefix. ``records``
    exposes the verifier suffix, so serializing this object cannot rewrite or
    duplicate the sealed runtime event log.
    """

    def __init__(
        self,
        *,
        runtime_records: tuple[LedgerRecord, ...],
        wall_time_ns: Callable[[], int] = wall_clock.time_ns,
    ) -> None:
        runtime = EventLedger.from_records(runtime_records)
        if runtime.records[-1].event.event_type not in self._TERMINAL_EVENT_TYPES:
            raise ValueError("verifier segment requires a terminal runtime ledger")
        super().__init__(run_id=runtime.run_id, wall_time_ns=wall_time_ns)
        self._records.extend(runtime.records)
        self._rebuild_indexes()
        self._runtime_record_count = len(runtime.records)
        self._runtime_event_chain_root = runtime.chain_root

    @property
    def records(self) -> tuple[LedgerRecord, ...]:
        return tuple(self._records[self._runtime_record_count :])

    @property
    def runtime_event_chain_root(self) -> str:
        return self._runtime_event_chain_root

    @property
    def first_verifier_sequence(self) -> int:
        return self._runtime_record_count

    def new_event(self, **fields: object) -> RunEvent:
        source = fields.get("source")
        if not isinstance(source, str):
            raise ValueError("verifier RunEvent source must identify the Verifier")
        supplied_source_kind = fields.get("source_kind")
        if supplied_source_kind not in (None, "verifier"):
            raise ValueError("verifier RunEvent source_kind must be verifier")
        supplied_segment_kind = fields.get("segment_kind")
        if supplied_segment_kind not in (None, "verifier"):
            raise ValueError("verifier RunEvent segment_kind must be verifier")
        fields["source_kind"] = "verifier"
        fields["segment_kind"] = "verifier"
        return super().new_event(**fields)  # type: ignore[arg-type]

    def _require_open(self) -> None:
        return

    def _validate_next_event(self, event: RunEvent) -> None:
        if not isinstance(event, RunEvent):
            raise TypeError("VerifierEventLedger accepts RunEvent values only")
        if event.run_id != self._run_id:
            raise ValueError("verifier RunEvent belongs to another run")
        if event.segment_kind != "verifier" or event.source_kind != "verifier":
            raise ValueError("verifier event segment accepts verifier events only")
        if event.sequence != len(self._records):
            raise ValueError("verifier RunEvent sequence is not globally contiguous")
        if event.previous_event_digest != self.chain_root:
            raise ValueError("verifier RunEvent does not continue the prior chain root")
        if any(
            event_id not in self._event_ids for event_id in event.causal_event_ids
        ):
            raise ValueError(
                "verifier RunEvent causal graph references an unknown prior event"
            )
        previous = self._records[-1].event
        if self._time_before(event.time, previous.time):
            raise ValueError("verifier RunEvent simulation time moved backwards")
        if event.wall_time_ns < previous.wall_time_ns:
            raise ValueError("verifier RunEvent wall time moved backwards")

    def verify(self) -> None:
        runtime_records = tuple(self._records[: self._runtime_record_count])
        runtime = EventLedger.from_records(runtime_records)
        if runtime.chain_root != self._runtime_event_chain_root:
            raise ValueError("verifier segment runtime prefix changed")
        previous_digest = runtime.chain_root
        previous_time = runtime.records[-1].event.time
        previous_wall_time_ns = runtime.records[-1].event.wall_time_ns
        known_event_ids = {
            record.event.event_id for record in runtime.records
        }
        for offset, record in enumerate(self.records):
            event = record.event
            expected_sequence = self._runtime_record_count + offset
            if record.sequence != expected_sequence or event.sequence != expected_sequence:
                raise ValueError("verifier event sequence is not globally contiguous")
            if event.run_id != self._run_id:
                raise ValueError("verifier segment contains another run identity")
            if event.segment_kind != "verifier" or event.source_kind != "verifier":
                raise ValueError("verifier segment contains a non-verifier event")
            if self._time_before(event.time, previous_time):
                raise ValueError("verifier event simulation time moved backwards")
            if event.wall_time_ns < previous_wall_time_ns:
                raise ValueError("verifier event wall time moved backwards")
            if (
                record.previous_hash != previous_digest
                or event.previous_event_digest != previous_digest
            ):
                raise ValueError("verifier event hash chain is broken")
            if record.event_hash != event.event_digest:
                raise ValueError("verifier record hash differs from RunEvent digest")
            if event.event_digest != run_event_digest_value(event):
                raise ValueError("verifier RunEvent content hash is invalid")
            if any(
                event_id not in known_event_ids
                for event_id in event.causal_event_ids
            ):
                raise ValueError(
                    "verifier RunEvent causal graph references an unknown prior event"
                )
            known_event_ids.add(event.event_id)
            previous_digest = event.event_digest
            previous_time = event.time
            previous_wall_time_ns = event.wall_time_ns

    def write_jsonl(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(
                verifier_event_segment_jsonl_bytes(
                    runtime_records=tuple(
                        self._records[: self._runtime_record_count]
                    ),
                    verifier_records=self.records,
                )
            )


def verifier_event_segment_jsonl_bytes(
    *,
    runtime_records: tuple[LedgerRecord, ...],
    verifier_records: tuple[LedgerRecord, ...],
) -> bytes:
    if not verifier_records:
        raise ValueError("verifier event segment cannot be empty")
    if not any(
        record.event.interaction is not None
        and record.event.interaction.interaction_type == "verifier.evidence.v1"
        for record in verifier_records
    ):
        raise ValueError("verifier event segment contains no verifier evidence")
    segment = VerifierEventLedger(runtime_records=runtime_records)
    for record in verifier_records:
        appended = segment.append(record.event)
        if appended != record:
            raise ValueError("verifier ledger record differs from its RunEvent")
    segment.verify()
    return b"".join(
        canonical_json_bytes(record.model_dump(mode="json")) + b"\n"
        for record in segment.records
    )


def verifier_event_segment_from_jsonl_bytes(
    *,
    runtime_records: tuple[LedgerRecord, ...],
    content: bytes,
) -> VerifierEventLedger:
    if not content or not content.endswith(b"\n"):
        raise ValueError("verifier event segment must be non-empty canonical JSONL")
    raw_lines = content[:-1].split(b"\n")
    if not raw_lines or any(not line for line in raw_lines):
        raise ValueError("verifier event segment contains an empty record")
    records: list[LedgerRecord] = []
    for raw_line in raw_lines:
        try:
            decoded = raw_line.decode("utf-8")
            raw = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("verifier event segment contains invalid JSON") from error
        record = LedgerRecord.model_validate(raw)
        if raw_line != canonical_json_bytes(record.model_dump(mode="json")):
            raise ValueError("verifier event record is not canonical JSON")
        records.append(record)
    segment = VerifierEventLedger(runtime_records=runtime_records)
    for record in records:
        appended = segment.append(record.event)
        if appended != record:
            raise ValueError("verifier ledger record differs from its RunEvent")
    segment.verify()
    verifier_event_segment_jsonl_bytes(
        runtime_records=runtime_records,
        verifier_records=segment.records,
    )
    return segment


def read_verifier_event_segment_jsonl(
    *,
    runtime_records: tuple[LedgerRecord, ...],
    source: Path,
) -> VerifierEventLedger:
    try:
        content = source.resolve(strict=True).read_bytes()
    except (OSError, RuntimeError) as error:
        raise ValueError("verifier event segment is unavailable") from error
    return verifier_event_segment_from_jsonl_bytes(
        runtime_records=runtime_records,
        content=content,
    )


def ledger_jsonl_bytes(records: tuple[LedgerRecord, ...]) -> bytes:
    ledger = EventLedger.from_records(records)
    # join materializes every encoded record before allocating the final bytes.
    # Keep only one record's encoding alongside the output buffer at seal time.
    with io.BytesIO() as stream:
        for record in ledger.iter_records():
            stream.write(canonical_json_bytes(record.model_dump(mode="json")))
            stream.write(b"\n")
        return stream.getvalue()


__all__ = [
    "RESERVED_RUNTIME_EVENT_TYPES",
    "EventLedger",
    "LedgerRecord",
    "VerifierEventLedger",
    "ledger_jsonl_bytes",
    "read_verifier_event_segment_jsonl",
    "verifier_event_segment_from_jsonl_bytes",
    "verifier_event_segment_jsonl_bytes",
]
