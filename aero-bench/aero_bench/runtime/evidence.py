from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.loader import sha256_file
from aero_bench.config.models import NamedValue, ProviderRef, RuntimeSpec
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.stages import PROVIDER_STAGES, ordered_enabled_stages
from aero_bench.runtime.contracts import (
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    StageReceipt,
    stage_barrier_digest_value,
    stage_receipt_digest_value,
)
from aero_bench.runtime.events import ProcessStreamChunk, RunEvent
from aero_bench.runtime.ledger import EventLedger, LedgerRecord
from aero_bench.serialization import canonical_json_bytes


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALL_STAGES = PROVIDER_STAGES


def _parse_json_inventory(value: object, *, field: str) -> object:
    if not isinstance(value, str):
        raise ValueError(f"{field} inventory must be canonical JSON text")
    try:
        parsed = json.loads(value)
        canonical = canonical_json_bytes(parsed).decode("utf-8")
    except (ValueError, TypeError) as error:
        raise ValueError(f"{field} inventory is invalid") from error
    if canonical != value:
        raise ValueError(f"{field} inventory must be canonical JSON text")
    return parsed


def _parse_digest_inventory(value: object, *, field: str) -> tuple[str, ...]:
    parsed = _parse_json_inventory(value, field=field)
    if not isinstance(parsed, list) or any(not isinstance(v, str) for v in parsed):
        raise ValueError(f"{field} inventory is invalid")
    if len(parsed) != len(set(parsed)):
        raise ValueError(f"{field} inventory must not repeat a digest")
    for digest in parsed:
        _require_sha256(digest, field=field)
    return tuple(parsed)


def _parse_identifier_inventory(value: object, *, field: str) -> tuple[str, ...]:
    parsed = _parse_json_inventory(value, field=field)
    if not isinstance(parsed, list) or any(not isinstance(v, str) for v in parsed):
        raise ValueError(f"{field} inventory is invalid")
    if sorted(parsed) != parsed or len(parsed) != len(set(parsed)):
        raise ValueError(f"{field} inventory must be sorted and unique")
    return tuple(parsed)


def _parse_predecessor_barriers(value: object) -> tuple[StageBarrierDigest, ...]:
    parsed = _parse_json_inventory(value, field="predecessor barrier")
    if not isinstance(parsed, list):
        raise ValueError("predecessor barrier inventory is invalid")
    result: list[StageBarrierDigest] = []
    for item in parsed:
        if not isinstance(item, dict) or set(item) != {"stage", "barrier_digest"}:
            raise ValueError("predecessor barrier inventory is invalid")
        try:
            result.append(StageBarrierDigest.model_validate(item))
        except (TypeError, ValueError) as error:
            raise ValueError("predecessor barrier inventory is invalid") from error
    stages = tuple(item.stage for item in result)
    if len(stages) != len(set(stages)):
        raise ValueError("predecessor barrier stages must be unique")
    return tuple(result)


def _expected_stage_maps(
    run: ResolvedRunSpec,
) -> tuple[dict[str, str], dict[str, tuple[str, ...]], tuple[str, ...]]:
    expected_stage_by_provider = {
        provider.provider_id: provider.runtime_stage
        for provider in run.scenario.providers
    }
    stage_provider_ids: dict[str, tuple[str, ...]] = {
        stage: tuple(
            sorted(
                provider.provider_id
                for provider in run.scenario.providers
                if provider.runtime_stage == stage
            )
        )
        for stage in _ALL_STAGES
    }
    enabled_stages = ordered_enabled_stages(stage_provider_ids)
    return expected_stage_by_provider, stage_provider_ids, enabled_stages


def _reconstruct_stage_receipt(payload: dict[str, object]) -> StageReceipt:
    stage = payload["stage"]
    at = SimulationTime(
        tick=payload["target_tick"],
        sim_time_ns=payload["target_sim_time_ns"],
    )
    input_scene = None if stage == "motion" else payload["scene_state_digest"]
    fields = {
        "schema_version": "aero-bench.stage-receipt/v1",
        "run_id": payload["run_id"],
        "scenario_digest": payload["scenario_digest"],
        "at": at,
        "stage": stage,  # type: ignore[arg-type]
        "provider_id": payload["provider_id"],
        "state_digest": payload["state_digest"],
        "step_receipt_digest": payload["step_receipt_digest"],
        "contribution_digest": payload["contribution_digest"],
        "payload_digest": payload["contribution_payload_digest"],
        "input_scene_state_digest": input_scene,
        "predecessor_barriers": _parse_predecessor_barriers(
            payload["predecessor_barriers"]
        ),
    }
    candidate = StageReceipt.model_construct(**fields, receipt_digest="0" * 64)
    recomputed = stage_receipt_digest_value(candidate)  # type: ignore[arg-type]
    return StageReceipt(**fields, receipt_digest=recomputed)  # type: ignore[arg-type]


def _reconstruct_stage_barrier(
    closure_payload: dict[str, object],
    receipts: tuple[StageReceipt, ...],
) -> StageBarrier:
    stage = closure_payload["stage"]
    at = SimulationTime(
        tick=closure_payload["target_tick"],
        sim_time_ns=closure_payload["target_sim_time_ns"],
    )
    input_scene = (
        None if stage == "motion" else closure_payload["scene_state_digest"]
    )
    fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": closure_payload["run_id"],
        "scenario_digest": closure_payload["scenario_digest"],
        "at": at,
        "stage": stage,  # type: ignore[arg-type]
        "input_scene_state_digest": input_scene,
        "predecessor_barriers": _parse_predecessor_barriers(
            closure_payload["predecessor_barriers"]
        ),
        "provider_ids": _parse_identifier_inventory(
            closure_payload["provider_ids"], field="closure provider_ids"
        ),
        "receipts": receipts,
        "receipt_digests": tuple(receipt.receipt_digest for receipt in receipts),
    }
    candidate = StageBarrier.model_construct(**fields, barrier_digest="0" * 64)
    recomputed = stage_barrier_digest_value(candidate)  # type: ignore[arg-type]
    return StageBarrier(**fields, barrier_digest=recomputed)  # type: ignore[arg-type]


def _payload(event: RunEvent) -> dict[str, object]:
    return {item.name: item.value for item in event.payload}


def _implementation_values(runtime: RuntimeSpec) -> dict[str, object]:
    identity = runtime.implementation
    return {
        "component_id": identity.component_id,
        "implementation_kind": identity.kind,
        "runtime_image": runtime.runtime.image,
        "source_revision": identity.source_revision,
        "source_uri": identity.source_uri,
        "version": identity.version,
    }


def harness_identity_payload(run: ResolvedRunSpec) -> tuple[NamedValue, ...]:
    return tuple(
        NamedValue(name=name, value=value)
        for name, value in sorted(
            _implementation_values(run.environment.harness).items()
        )
    )


def provider_identity_payload(provider: ProviderRef) -> tuple[NamedValue, ...]:
    values = {
        **_implementation_values(provider.workload),
        "adapter": provider.adapter,
        "capabilities_digest": hashlib.sha256(
            canonical_json_bytes(list(provider.capabilities))
        ).hexdigest(),
        "config_digest": provider.config.file.sha256,
    }
    return tuple(
        NamedValue(name=name, value=value) for name, value in sorted(values.items())
    )


def _records_of_type(
    ledger: EventLedger,
    event_type: str,
) -> tuple[LedgerRecord, ...]:
    return tuple(
        record for record in ledger.records if record.event.event_type == event_type
    )


def _require_sha256(value: object, *, field: str) -> None:
    if (
        not isinstance(value, str)
        or _SHA256.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise ValueError(f"{field} must be a non-placeholder SHA-256 digest")


def _validate_executor_validation_ledger(ledger: EventLedger) -> None:
    scope_records = _records_of_type(ledger, "validation.scope")
    if len(scope_records) != 1:
        raise ValueError(
            "executor-validation ledger requires exactly one validation.scope event"
        )
    scope = scope_records[0].event
    if (
        scope.source != "harness"
        or scope.time != SimulationTime(tick=0, sim_time_ns=0)
        or _payload(scope) != {"execution_scope": "executor_validation"}
    ):
        raise ValueError("validation.scope event does not identify executor_validation")


def _validate_process_streams(run: ResolvedRunSpec, ledger: EventLedger) -> None:
    expected_roles = {
        "harness": "harness",
        **{provider.provider_id: "provider" for provider in run.environment.providers},
        **{agent.agent_id: "agent" for agent in run.agents},
        **{
            agent.driver.driver_id: "agent_driver"
            for agent in run.agents
            if agent.driver is not None
        },
    }
    expected_keys = {
        (workload_id, stream)
        for workload_id in expected_roles
        for stream in ("stdout", "stderr")
    }
    records_by_key: dict[tuple[str, str], list[LedgerRecord]] = {
        key: [] for key in expected_keys
    }
    for record in ledger.records:
        event = record.event
        if event.event_type not in {"process.stdout", "process.stderr"}:
            continue
        payload = _payload(event)
        workload_id = payload.get("workload_id")
        stream = payload.get("stream")
        key = (workload_id, stream)
        if key not in records_by_key:
            raise ValueError("process stream RunEvent names an undeclared workload")
        records_by_key[key].append(record)

    for (workload_id, stream), records in records_by_key.items():
        if not records:
            raise ValueError("formal ledger omits a declared process stream")
        next_sequence = 0
        next_offset = 0
        total_bytes = 0
        previous_event_id: str | None = None
        for index, record in enumerate(records):
            event = record.event
            payload = _payload(event)
            expected_fields = {
                "byte_offset",
                "captured_wall_time_ns",
                "content_class",
                "final",
                "payload_base64",
                "payload_sha256",
                "payload_size_bytes",
                "run_id",
                "sequence",
                "stream",
                "truncated",
                "workload_id",
                "workload_role",
            }
            if set(payload) != expected_fields:
                raise ValueError("process stream RunEvent payload is invalid")
            try:
                chunk = ProcessStreamChunk(
                    schema_version="aero-bench.process-stream-chunk/v1",
                    **payload,
                )
            except (TypeError, ValueError) as error:
                raise ValueError("process stream chunk contract is invalid") from error
            interaction = event.interaction
            expected_workload_role = expected_roles[workload_id]
            expected_source_kind = (
                "executor"
                if expected_workload_role == "agent_driver"
                else expected_workload_role
            )
            expected_interaction_type = f"process.{stream}.v1"
            if (
                chunk.run_id != run.run_id
                or chunk.workload_role != expected_workload_role
                or chunk.sequence != next_sequence
                or chunk.byte_offset != next_offset
                or event.source != workload_id
                or event.source_kind != expected_source_kind
                or event.workload_id != workload_id
                or event.event_type != f"process.{stream}"
                or event.payload_schema_id != expected_interaction_type
                or event.correlation_id != f"process.{workload_id}.{stream}"
                or event.parent_event_id != previous_event_id
                or event.wall_time_ns < chunk.captured_wall_time_ns
                or interaction is None
                or interaction.interaction_type != expected_interaction_type
                or tuple(
                    (audience.scope, audience.audience_id)
                    for audience in event.visibility
                )
                != (("private", None),)
                or (chunk.final and index != len(records) - 1)
                or (not chunk.final and index == len(records) - 1)
            ):
                raise ValueError("process stream RunEvent continuity is invalid")
            if expected_source_kind == "agent" and event.agent_id != workload_id:
                raise ValueError("Agent process stream lacks Agent identity")
            if expected_source_kind == "provider" and event.provider_id != workload_id:
                raise ValueError("Provider process stream lacks Provider identity")
            next_sequence += 1
            next_offset += chunk.payload_size_bytes
            total_bytes += chunk.payload_size_bytes
            previous_event_id = event.event_id
        if total_bytes > 256 * 1024:
            raise ValueError("process stream exceeds its run-scoped byte bound")


def _validate_formal_ledger(run: ResolvedRunSpec, ledger: EventLedger) -> None:
    if _records_of_type(ledger, "validation.scope"):
        raise ValueError("formal benchmark ledger cannot contain validation.scope")
    first_event = ledger.records[0].event
    if (
        first_event.run_id != run.run_id
        or first_event.sequence != 0
        or first_event.source != "harness"
        or first_event.source_kind != "harness"
        or first_event.event_type != "run.started"
        or first_event.time != SimulationTime(tick=0, sim_time_ns=0)
        or _payload(first_event) != {"execution_scope": "formal_benchmark"}
    ):
        raise ValueError("formal benchmark ledger must begin with run.started")
    completed = _records_of_type(ledger, "run.completed")
    aborted = _records_of_type(ledger, "run.aborted")
    terminal_records = (*completed, *aborted)
    if len(terminal_records) != 1 or terminal_records[0] != ledger.records[-1]:
        raise ValueError(
            "formal benchmark ledger must end with one completed or aborted terminal"
        )
    terminal = terminal_records[0]

    identities = _records_of_type(ledger, "runtime.identity")
    expected_identity_payloads = {
        "harness": {item.name: item.value for item in harness_identity_payload(run)},
        **{
            provider.provider_id: {
                item.name: item.value for item in provider_identity_payload(provider)
            }
            for provider in run.environment.providers
        },
    }
    actual_identity_payloads: dict[str, dict[str, object]] = {}
    for record in identities:
        event = record.event
        if event.time != SimulationTime(tick=0, sim_time_ns=0):
            raise ValueError("runtime identity must be recorded at time zero")
        if event.source in actual_identity_payloads:
            raise ValueError("runtime identity is duplicated")
        actual_identity_payloads[event.source] = _payload(event)
    if actual_identity_payloads != expected_identity_payloads:
        raise ValueError("runtime identities do not match ResolvedRun implementations")

    provider_ids = {provider.provider_id for provider in run.environment.providers}
    # Independent, immutable-scenario stage authority for offline verification.
    expected_stage_by_provider, stage_provider_ids, enabled_stages = _expected_stage_maps(
        run
    )
    if set(expected_stage_by_provider) != provider_ids:
        raise ValueError(
            "resolved Provider runtime stages do not cover the declared Providers"
        )
    reset_records = _records_of_type(ledger, "provider.reset")
    if {record.event.source for record in reset_records} != provider_ids or len(
        reset_records
    ) != len(provider_ids):
        raise ValueError("formal ledger must contain one reset receipt per provider")
    for record in reset_records:
        event = record.event
        if event.time != SimulationTime(tick=0, sim_time_ns=0):
            raise ValueError("provider reset receipt must establish time zero")
        payload = _payload(event)
        if set(payload) != {"state_digest"}:
            raise ValueError("provider reset receipt payload is invalid")
        _require_sha256(payload["state_digest"], field="provider reset state_digest")

    ready = _records_of_type(ledger, "barrier.ready")
    if len(ready) != 1:
        raise ValueError("formal ledger requires exactly one barrier.ready event")
    if (
        ready[0].event.source != "harness"
        or ready[0].event.time != SimulationTime(tick=0, sim_time_ns=0)
        or _payload(ready[0].event) != {"seed": run.seed}
    ):
        raise ValueError("barrier.ready does not match the ResolvedRun seed")
    identity_sequences = {record.sequence for record in identities}
    reset_sequences = {record.sequence for record in reset_records}
    if (
        not identity_sequences
        or not reset_sequences
        or min(identity_sequences) <= ledger.records[0].sequence
        or max(identity_sequences) >= min(reset_sequences)
        or max(reset_sequences) >= ready[0].sequence
    ):
        raise ValueError(
            "runtime identities, reset receipts, and barrier.ready are out of order"
        )

    commits = _records_of_type(ledger, "barrier.committed")
    if completed and not commits:
        raise ValueError(
            "completed formal ledger contains no committed Provider barrier"
        )
    if len(commits) > run.environment.clock.max_steps:
        raise ValueError("formal ledger exceeds ResolvedRun max_steps")
    expected_ticks = tuple(range(1, len(commits) + 1))
    if tuple(record.event.time.tick for record in commits) != expected_ticks:
        raise ValueError("formal barrier commit ticks are not contiguous")

    scene_commits = _records_of_type(ledger, "scene.state.committed")
    if len(scene_commits) != len(commits):
        raise ValueError("formal ledger requires one SceneState commit per tick")
    receipts = _records_of_type(ledger, "provider.step-receipt")
    stage_closures = _records_of_type(ledger, "stage.barrier-closed")
    receipt_sequences: set[int] = set()
    closure_sequences: set[int] = set()
    previous_commit_sequence = ready[0].sequence
    for tick, scene_commit, commit in zip(
        expected_ticks, scene_commits, commits, strict=True
    ):
        expected_time = SimulationTime(
            tick=tick,
            sim_time_ns=tick * run.environment.clock.step_ns,
        )
        if scene_commit.event.time != expected_time:
            raise ValueError("SceneState commit time does not match its tick")
        scene_payload = _payload(scene_commit.event)
        expected_scene_fields = {
            "barrier_digest",
            "contribution_digests",
            "provider_ids",
            "receipt_digests",
            "run_id",
            "scenario_digest",
            "scene_state_digest",
            "stage",
            "target_sim_time_ns",
            "target_tick",
        }
        if (
            scene_commit.event.source != "harness"
            or set(scene_payload) != expected_scene_fields
            or scene_payload["run_id"] != run.run_id
            or scene_payload["scenario_digest"] != run.scenario.scenario_digest
            or scene_payload["stage"] != "motion"
            or scene_payload["target_tick"] != tick
            or scene_payload["target_sim_time_ns"] != expected_time.sim_time_ns
        ):
            raise ValueError("SceneState commit does not match its ResolvedRun tick")
        _require_sha256(scene_payload["scene_state_digest"], field="SceneState digest")
        if not previous_commit_sequence < scene_commit.sequence < commit.sequence:
            raise ValueError("SceneState commit is outside its tick transaction")

        tick_receipts = tuple(
            record for record in receipts if record.event.time == expected_time
        )
        if {record.event.source for record in tick_receipts} != provider_ids or len(
            tick_receipts
        ) != len(provider_ids):
            raise ValueError("staged tick lacks one receipt per Provider")
        receipt_digests: set[str] = set()
        # Stage-bound per-Provider inventory: provider_id -> reconstructed receipt,
        # and the per-stage grouping used to bind each closure to its own stage.
        receipt_by_provider: dict[str, StageReceipt] = {}
        receipt_records_by_provider: dict[str, LedgerRecord] = {}
        for receipt in tick_receipts:
            if not previous_commit_sequence < receipt.sequence < commit.sequence:
                raise ValueError("Provider receipt is outside its staged transaction")
            payload = _payload(receipt.event)
            expected_receipt_fields = {
                "barrier_digest",
                "contribution_digest",
                "contribution_payload_digest",
                "predecessor_barriers",
                "provider_id",
                "run_id",
                "scenario_digest",
                "scene_state_digest",
                "stage",
                "stage_receipt_digest",
                "state_digest",
                "step_receipt_digest",
                "target_sim_time_ns",
                "target_tick",
            }
            if (
                set(payload) != expected_receipt_fields
                or payload["provider_id"] != receipt.event.source
                or receipt.event.provider_id != receipt.event.source
                or receipt.event.source_kind != "provider"
                or receipt.event.workload_id != receipt.event.source
                or payload["run_id"] != run.run_id
                or payload["scenario_digest"] != run.scenario.scenario_digest
                or payload["scene_state_digest"] != scene_payload["scene_state_digest"]
                or payload["target_tick"] != tick
                or payload["target_sim_time_ns"] != expected_time.sim_time_ns
                or payload["stage"] not in _ALL_STAGES
            ):
                raise ValueError("Provider staged receipt payload is invalid")
            for field in (
                "barrier_digest",
                "contribution_digest",
                "contribution_payload_digest",
                "stage_receipt_digest",
                "state_digest",
                "step_receipt_digest",
            ):
                _require_sha256(payload[field], field=f"Provider receipt {field}")
            provider_id = str(payload["provider_id"])
            # (1) receipt source, payload provider_id and the immutable-scenario
            # expected serialized stage must agree. This is the semantic stage
            # binding that rejects a re-attributed receipt even when its hashes
            # are internally self-consistent.
            expected_stage = expected_stage_by_provider.get(provider_id)
            if expected_stage is None or payload["stage"] != expected_stage:
                raise ValueError(
                    "Provider staged receipt stage does not match its resolved stage"
                )
            # (5) rebuild the StageReceipt from ledger fields and verify its digest.
            try:
                reconstructed = _reconstruct_stage_receipt(payload)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "Provider staged receipt digest is not self-consistent"
                ) from error
            if reconstructed.receipt_digest != payload["stage_receipt_digest"]:
                raise ValueError(
                    "Provider staged receipt digest is not self-consistent"
                )
            receipt_by_provider[provider_id] = reconstructed
            receipt_records_by_provider[provider_id] = receipt
            receipt_digests.add(str(payload["stage_receipt_digest"]))
            receipt_sequences.add(receipt.sequence)

        tick_closures = tuple(
            record for record in stage_closures if record.event.time == expected_time
        )
        # (2) exactly one closure per enabled stage, in barrier order, with no
        # closure for a disabled stage.
        if len(tick_closures) != len(enabled_stages):
            raise ValueError("staged tick does not close exactly the enabled stages")
        closure_stages = tuple(
            _payload(closure.event).get("stage") for closure in tick_closures
        )
        if closure_stages != enabled_stages:
            raise ValueError("stage closures do not follow the runtime barrier order")
        closed_receipt_digests: set[str] = set()
        closure_summaries: list[dict[str, object]] = []
        motion_closure_payload: dict[str, object] | None = None
        expected_predecessors: list[StageBarrierDigest] = []
        previous_closure_sequence = previous_commit_sequence
        for closure in tick_closures:
            if not previous_commit_sequence < closure.sequence < commit.sequence:
                raise ValueError("stage closure is outside its staged transaction")
            payload = _payload(closure.event)
            expected_closure_fields = {
                "barrier_digest",
                "contribution_digests",
                "predecessor_barriers",
                "provider_ids",
                "receipt_digests",
                "run_id",
                "scenario_digest",
                "scene_state_digest",
                "stage",
                "step_receipt_digests",
                "target_sim_time_ns",
                "target_tick",
            }
            if (
                closure.event.source != "harness"
                or set(payload) != expected_closure_fields
                or payload["run_id"] != run.run_id
                or payload["scenario_digest"] != run.scenario.scenario_digest
                or payload["scene_state_digest"] != scene_payload["scene_state_digest"]
                or payload["target_tick"] != tick
                or payload["target_sim_time_ns"] != expected_time.sim_time_ns
                or payload["stage"] not in _ALL_STAGES
            ):
                raise ValueError("stage barrier closure payload is invalid")
            _require_sha256(payload["barrier_digest"], field="stage barrier digest")
            stage = str(payload["stage"])
            if _parse_predecessor_barriers(payload["predecessor_barriers"]) != tuple(
                expected_predecessors
            ):
                raise ValueError("stage closure predecessors do not bind prior closures")
            # (3) the closure's provider_ids exactly match that stage's Providers.
            closure_provider_ids = _parse_identifier_inventory(
                payload["provider_ids"], field="closure provider_ids"
            )
            if closure_provider_ids != stage_provider_ids[stage]:
                raise ValueError(
                    "stage closure provider_ids do not match its resolved stage set"
                )
            ordered_stage_receipts_list: list[StageReceipt] = []
            for provider_id in closure_provider_ids:
                reconstructed = receipt_by_provider.get(provider_id)
                if reconstructed is None:
                    raise ValueError(
                        "stage closure references a Provider without a staged receipt"
                    )
                ordered_stage_receipts_list.append(reconstructed)
                receipt_record = receipt_records_by_provider[provider_id]
                if (
                    not previous_closure_sequence < receipt_record.sequence < closure.sequence
                    or _payload(receipt_record.event)["barrier_digest"]
                    != payload["barrier_digest"]
                    or stage != "motion" and receipt_record.sequence <= scene_commit.sequence
                ):
                    raise ValueError("Provider receipt does not bind its stage closure")
            ordered_stage_receipts = tuple(ordered_stage_receipts_list)
            # (4) the per-stage receipt / step / contribution inventories align
            # Provider-by-Provider (in provider_ids order), not merely as an
            # unordered per-tick digest union.
            closure_receipt_digests = _parse_digest_inventory(
                payload["receipt_digests"], field="closure receipt_digests"
            )
            expected_receipt_digests = tuple(
                receipt.receipt_digest for receipt in ordered_stage_receipts
            )
            if closure_receipt_digests != expected_receipt_digests:
                raise ValueError(
                    "stage closure receipt_digests do not bind its stage receipts"
                )
            closure_contribution_digests = _parse_digest_inventory(
                payload["contribution_digests"], field="closure contribution_digests"
            )
            expected_contribution_digests = tuple(
                receipt.contribution_digest for receipt in ordered_stage_receipts
            )
            if closure_contribution_digests != expected_contribution_digests:
                raise ValueError(
                    "stage closure contribution_digests do not bind its stage receipts"
                )
            closure_step_receipt_digests = _parse_digest_inventory(
                payload["step_receipt_digests"], field="closure step_receipt_digests"
            )
            expected_step_receipt_digests = tuple(
                receipt.step_receipt_digest for receipt in ordered_stage_receipts
            )
            if closure_step_receipt_digests != expected_step_receipt_digests:
                raise ValueError(
                    "stage closure step_receipt_digests do not bind its stage receipts"
                )
            # (5) rebuild the StageBarrier and verify its digest.
            try:
                reconstructed_barrier = _reconstruct_stage_barrier(
                    payload, ordered_stage_receipts
                )
            except (TypeError, ValueError) as error:
                raise ValueError("stage barrier digest is not self-consistent") from error
            if reconstructed_barrier.barrier_digest != payload["barrier_digest"]:
                raise ValueError("stage barrier digest is not self-consistent")
            expected_predecessors.append(StageBarrierDigest(
                stage=stage, barrier_digest=reconstructed_barrier.barrier_digest
            ))
            previous_closure_sequence = closure.sequence
            for digest in closure_receipt_digests:
                if digest in closed_receipt_digests:
                    raise ValueError("stage closures repeat a receipt digest")
                closed_receipt_digests.add(digest)
            closure_sequences.add(closure.sequence)
            if stage == "motion":
                motion_closure_payload = payload
                if closure.sequence >= scene_commit.sequence:
                    raise ValueError("SceneState commit must follow the motion closure")
            elif closure.sequence <= scene_commit.sequence:
                raise ValueError("non-motion closures must follow the SceneState commit")
            closure_summaries.append(
                {
                    "barrier_digest": payload["barrier_digest"],
                    "contribution_digests": list(closure_contribution_digests),
                    "predecessor_barriers": [
                        item.model_dump(mode="json")
                        for item in _parse_predecessor_barriers(
                            payload["predecessor_barriers"]
                        )
                    ],
                    "provider_ids": list(closure_provider_ids),
                    "receipt_digests": list(closure_receipt_digests),
                    "stage": stage,
                    "step_receipt_digests": list(closure_step_receipt_digests),
                }
            )
        if closed_receipt_digests != receipt_digests:
            raise ValueError("stage closures do not bind every Provider receipt")

        # (5) scene.state.committed binds this tick's motion closure inventory.
        if motion_closure_payload is None:
            raise ValueError("staged tick lacks a motion stage closure")
        if (
            scene_payload["barrier_digest"] != motion_closure_payload["barrier_digest"]
            or scene_payload["receipt_digests"]
            != motion_closure_payload["receipt_digests"]
            or _parse_identifier_inventory(
                scene_payload["provider_ids"], field="SceneState provider_ids"
            ) != stage_provider_ids["motion"]
        ):
            raise ValueError("SceneState commit does not bind the motion closure")
        scene_contribution = _parse_digest_inventory(
            scene_payload["contribution_digests"],
            field="SceneState contribution_digests",
        )
        motion_contribution = _parse_digest_inventory(
            motion_closure_payload["contribution_digests"],
            field="motion closure contribution_digests",
        )
        if tuple(sorted(scene_contribution)) != tuple(sorted(motion_contribution)):
            raise ValueError("SceneState commit does not bind the motion closure")

        commit_payload = _payload(commit.event)
        expected_commit_fields = {
            "provider_result_count",
            "run_id",
            "scenario_digest",
            "scene_state_digest",
            "stage",
            "stage_barriers",
            "target_sim_time_ns",
            "target_tick",
        }
        if (
            commit.event.source != "harness"
            or commit.event.time != expected_time
            or set(commit_payload) != expected_commit_fields
            or commit_payload["run_id"] != run.run_id
            or commit_payload["scenario_digest"] != run.scenario.scenario_digest
            or commit_payload["scene_state_digest"]
            != scene_payload["scene_state_digest"]
            or commit_payload["stage"] != "tick"
            or commit_payload["provider_result_count"] != len(provider_ids)
            or commit_payload["target_tick"] != tick
            or commit_payload["target_sim_time_ns"] != expected_time.sim_time_ns
        ):
            raise ValueError("barrier commit does not match its staged tick")
        # (5) barrier.committed.stage_barriers binds the actual closed summaries.
        commit_barriers = _parse_json_inventory(
            commit_payload["stage_barriers"], field="barrier commit stage_barriers"
        )
        if commit_barriers != closure_summaries:
            raise ValueError(
                "barrier commit stage_barriers do not match the closed stages"
            )
        previous_commit_sequence = commit.sequence
    if receipt_sequences != {record.sequence for record in receipts}:
        raise ValueError("formal ledger contains an uncommitted Provider receipt")
    if closure_sequences != {record.sequence for record in stage_closures}:
        raise ValueError("formal ledger contains an uncommitted stage closure")
    if completed:
        if (
            terminal.event.source != "harness"
            or terminal.event.time != commits[-1].event.time
            or _payload(terminal.event) != {"execution_scope": "formal_benchmark"}
        ):
            raise ValueError("run.completed does not match the final committed barrier")
    else:
        abort_payload = _payload(terminal.event)
        if (
            terminal.event.source != "harness"
            or terminal.event.time
            != (
                commits[-1].event.time
                if commits
                else SimulationTime(tick=0, sim_time_ns=0)
            )
            or set(abort_payload)
            != {
                "failure_class",
                "provider_failure_count",
                "provider_failure_ids",
            }
            or abort_payload["failure_class"]
            not in {"runtime_failed", "prepare_failed", "signal", "cancelled"}
            or not isinstance(abort_payload["provider_failure_count"], int)
            or isinstance(abort_payload["provider_failure_count"], bool)
            or abort_payload["provider_failure_count"] < 0
        ):
            raise ValueError("run.aborted terminal payload is invalid")
        try:
            failure_ids = json.loads(str(abort_payload["provider_failure_ids"]))
        except json.JSONDecodeError as error:
            raise ValueError(
                "run.aborted Provider failure inventory is invalid"
            ) from error
        if (
            not isinstance(failure_ids, list)
            or failure_ids != sorted(failure_ids)
            or len(failure_ids) != len(set(failure_ids))
            or any(not isinstance(value, str) for value in failure_ids)
            or set(failure_ids) - provider_ids
            or len(failure_ids) != abort_payload["provider_failure_count"]
        ):
            raise ValueError("run.aborted Provider failure inventory is invalid")
    _validate_process_streams(run, ledger)


def validate_authoritative_runtime_ledger(
    run: ResolvedRunSpec,
    ledger: EventLedger,
) -> None:
    ledger.verify()
    if run.execution_scope == "executor_validation":
        _validate_executor_validation_ledger(ledger)
        return
    _validate_formal_ledger(run, ledger)


def load_sealed_event_ledger(
    *,
    run: ResolvedRunSpec,
    seal: SealManifest,
    seal_root: Path,
) -> EventLedger:
    """Load and independently validate the declared sealed event ledger."""

    if seal.run_id != run.run_id:
        raise ValueError("runtime seal run_id does not match ResolvedRun")
    if seal.execution_scope != run.execution_scope:
        raise ValueError("runtime seal execution_scope does not match ResolvedRun")

    requirements = tuple(
        requirement
        for requirement in run.artifact_requirements
        if requirement.artifact_type == "event.log"
    )
    records = tuple(
        artifact for artifact in seal.artifacts if artifact.artifact_type == "event.log"
    )
    if len(requirements) != 1 or len(records) != 1:
        raise ValueError("runtime seal requires one declared event.log")
    requirement = requirements[0]
    record = records[0]
    if (
        record.artifact_id != requirement.artifact_id
        or record.producer_id != "harness"
        or record.producer_id != requirement.producer_id
        or record.visibility != "private"
        or record.visibility != requirement.visibility
        or record.relative_path != requirement.relative_path
    ):
        raise ValueError("sealed event.log does not match its artifact requirement")
    if record.size_bytes > requirement.max_size_bytes:
        raise ValueError("sealed event.log exceeds its declared maximum size")

    try:
        root_mode = seal_root.lstat().st_mode
        resolved_root = seal_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("runtime seal root is unavailable") from error
    if stat.S_ISLNK(root_mode) or not resolved_root.is_dir():
        raise ValueError("runtime seal root must be a non-link directory")

    candidate = resolved_root
    for part in Path(record.relative_path).parts:
        candidate /= part
        try:
            candidate_mode = candidate.lstat().st_mode
        except OSError as error:
            raise ValueError("sealed event.log is unavailable") from error
        if stat.S_ISLNK(candidate_mode):
            raise ValueError("sealed event.log path contains a symbolic link")
    try:
        resolved_candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("sealed event.log is unavailable") from error
    if not resolved_candidate.is_relative_to(resolved_root) or not stat.S_ISREG(
        resolved_candidate.stat().st_mode
    ):
        raise ValueError("sealed event.log must be a regular file inside the seal")
    if resolved_candidate.stat().st_size != record.size_bytes:
        raise ValueError("sealed event.log size does not match the seal")
    if sha256_file(resolved_candidate) != record.sha256:
        raise ValueError("sealed event.log digest does not match the seal")

    ledger = EventLedger.read_jsonl(resolved_candidate)
    validate_authoritative_runtime_ledger(run, ledger)
    if ledger.chain_root != seal.event_chain_root:
        raise ValueError("sealed event.log chain root does not match the seal")
    return ledger


__all__ = [
    "harness_identity_payload",
    "load_sealed_event_ledger",
    "provider_identity_payload",
    "validate_authoritative_runtime_ledger",
]
