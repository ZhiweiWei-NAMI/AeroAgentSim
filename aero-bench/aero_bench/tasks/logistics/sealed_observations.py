"""Offline reconstruction of logistics observations and airspace segments.

The sealed loader checks native PX4 source snapshots, re-derives every pad
observation, replays the Business journal, and compares private runtime records.
It does not evaluate orders, dwell, battery transfer, or a task success verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from aero_bench.artifacts.contracts import ArtifactRecord, SealManifest
from aero_bench.config.loader import BundleReader
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.px4_gazebo.sealed_motion import load_sealed_px4_motion
from aero_bench.providers.rpc import parse_json_object
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.ledger import LedgerRecord
from aero_bench.runtime.sealed_motion import SealedMotionFrame
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.observation_ingress import (
    ObservationJournal,
    append_observations_batch,
    build_observation_batch,
    derive_physical_observations,
    empty_observation_journal,
)
from aero_bench.tasks.logistics.runtime_airspace import (
    AirspaceSegmentRecord,
    ClosedMotionAirspaceTracker,
)
from aero_bench.tasks.logistics.runtime_bindings import (
    validate_logistics_runtime_bindings,
)
from aero_bench.tasks.logistics.runtime_hook import LogisticsRuntimeHookFactory
from aero_bench.world.resolved import ResolvedScenario


@dataclass(frozen=True, slots=True)
class ReplayedObservationTick:
    at: SimulationTime
    journal_digest: str
    sequence_count: int
    segments: tuple[AirspaceSegmentRecord, ...]
    commit_sequence: int


@dataclass(frozen=True, slots=True)
class ReplayedLogisticsObservations:
    journal: ObservationJournal
    ticks: tuple[ReplayedObservationTick, ...]


@dataclass(frozen=True, slots=True)
class SealedLogisticsObservations:
    seal: SealManifest
    business_artifact: ArtifactRecord
    replay: ReplayedLogisticsObservations


def replay_logistics_motion(
    *,
    run_id: str,
    config: LogisticsBusinessConfig,
    scenario: ResolvedScenario,
    step_ns: int,
    frames: tuple[SealedMotionFrame, ...],
) -> ReplayedLogisticsObservations:
    """Pure replay from explicit inputs; this function issues no provenance."""
    config = type(config).model_validate(config.model_dump(mode="json"))
    declared = config.observation
    if declared is None:
        raise ValueError(
            "sealed logistics observations require declared observation ingress"
        )
    tracker = ClosedMotionAirspaceTracker(
        run_id=run_id,
        scenario_digest=scenario.scenario_digest,
        step_ns=step_ns,
        package=config.task_package,
        bindings=declared.bindings,
    )
    journal = empty_observation_journal(run_id=run_id, provider_id=config.provider_id)
    ticks = []
    for frame in frames:
        state = frame.scene_state
        observations = derive_physical_observations(
            scene_state=state,
            events=frame.events,
            stage_barriers=frame.stage_barriers,
            package=config.task_package,
            bindings=declared.bindings,
            scenario=scenario,
            spec=declared.spec,
            pose_references=declared.pose_references,
            tolerances=declared.tolerances,
            expected_run_id=run_id,
            target=state.at,
        )
        batch = build_observation_batch(
            observations=observations,
            run_id=run_id,
            scenario_digest=scenario.scenario_digest,
            at=state.at,
            source_scene_state_digest=state.scene_state_digest,
            source_stage_barrier_digest=state.stage_barrier.barrier_digest,
        )
        airspace = tracker.prepare(batch)
        outcome = append_observations_batch(
            journal=journal, batch=batch, provider_id=config.provider_id
        )
        if outcome.replayed:
            raise ValueError("sealed observation history repeats a closed motion tick")
        journal = outcome.journal
        tracker.commit(airspace)
        ticks.append(
            ReplayedObservationTick(
                state.at,
                journal.canonical_digest(),
                journal.last_sequence,
                airspace.segments,
                frame.commit_sequence,
            )
        )
    return ReplayedLogisticsObservations(journal, tuple(ticks))


def validate_logistics_observation_records(
    *,
    replay: ReplayedLogisticsObservations,
    records: tuple[LedgerRecord, ...],
    business_document: dict[str, object],
    artifact: ArtifactRecord,
    config: LogisticsBusinessConfig,
    event_chain_root: str,
) -> None:
    """Compare journal and hook records; a seal/source check remains caller-required."""
    if (
        artifact.artifact_type != "logistics.business.state"
        or artifact.producer_id != config.provider_id
    ):
        raise ValueError("logistics observation artifact has another type or producer")
    if (
        business_document.get("schema_version")
        != "aero-bench.logistics-business-state/v1"
        or business_document.get("source_artifact_id") != artifact.artifact_id
        or business_document.get("event_chain_root") != event_chain_root
    ):
        raise ValueError(
            "Business observation artifact differs from its declared identity or ledger root"
        )
    journal = ObservationJournal.model_validate(
        business_document.get("observation_journal")
    )
    if (
        journal != replay.journal
        or business_document.get("observation_journal_digest")
        != replay.journal.canonical_digest()
    ):
        raise ValueError(
            "sealed Business journal differs from closed native motion reconstruction"
        )
    if business_document.get("facilities") != config.task_package.facilities.model_dump(
        mode="json"
    ):
        raise ValueError(
            "sealed Business facilities differ from their pinned declaration"
        )
    if business_document.get("principal_bindings") != [
        item.model_dump(mode="json") for item in config.principal_bindings
    ]:
        raise ValueError(
            "sealed Business principals differ from their pinned declaration"
        )
    relevant_by_time: dict[SimulationTime, list[LedgerRecord]] = {}
    closures_by_time: dict[SimulationTime, list[LedgerRecord]] = {}
    for record in records:
        if record.event.event_type in {
            "logistics.observation.ingested",
            "logistics.airspace.segment.v1",
        }:
            relevant_by_time.setdefault(record.event.time, []).append(record)
        elif record.event.event_type == "stage.barrier-closed":
            closures_by_time.setdefault(record.event.time, []).append(record)
    by_time = {tick.at: tick for tick in replay.ticks}
    if set(relevant_by_time) - set(by_time):
        raise ValueError("logistics hook record has no matching closed motion tick")
    for tick in replay.ticks:
        current = relevant_by_time.get(tick.at, [])
        closures = closures_by_time.get(tick.at, [])
        if not closures:
            raise ValueError("logistics hook records require closed Provider stages")
        last_closure = max(record.sequence for record in closures)
        if any(
            not last_closure < record.sequence < tick.commit_sequence
            for record in current
        ):
            raise ValueError(
                "logistics hook evidence is outside the closed-stage, pre-commit window"
            )
        acks = tuple(
            record
            for record in current
            if record.event.event_type == "logistics.observation.ingested"
        )
        if not acks:
            raise ValueError(
                "sealed logistics journal lacks its runtime acknowledgement"
            )
        for index, ack in enumerate(acks):
            event = ack.event
            expected = {
                "run_id": replay.journal.run_id,
                "provider_id": config.provider_id,
                "journal_digest": tick.journal_digest,
                "sequence_count": tick.sequence_count,
                "replayed": index > 0,
            }
            if (
                event.source_kind != "provider"
                or event.source != config.provider_id
                or event.provider_id != config.provider_id
                or event.payload_schema_id != "logistics.observation.ingested.v1"
                or {item.scope for item in event.visibility} != {"private"}
                or canonical_json_bytes(
                    {item.name: item.value for item in event.payload}
                )
                != canonical_json_bytes(expected)
            ):
                raise ValueError(
                    "sealed logistics acknowledgement differs from reconstructed journal"
                )
        segments = tuple(
            record
            for record in current
            if record.event.event_type == "logistics.airspace.segment.v1"
        )
        if len(segments) != len(tick.segments):
            raise ValueError(
                "sealed airspace segment inventory differs from native motion reconstruction"
            )
        for record, segment in zip(segments, tick.segments, strict=True):
            event = record.event
            expected = {
                "segment_digest": segment.canonical_digest(),
                "segment_json": canonical_json_bytes(
                    segment.model_dump(mode="json")
                ).decode(),
            }
            if (
                event.source_kind != "harness"
                or event.source != "harness"
                or event.payload_schema_id != "logistics.airspace.segment.v1"
                or {item.scope for item in event.visibility} != {"private", "verifier"}
                or record.sequence <= acks[0].sequence
                or canonical_json_bytes(
                    {item.name: item.value for item in event.payload}
                )
                != canonical_json_bytes(expected)
            ):
                raise ValueError(
                    "sealed airspace segment differs from reconstructed source witnesses"
                )


def load_sealed_logistics_observations(
    *,
    run: ResolvedRunSpec,
    reader: BundleReader,
    seal: SealManifest,
    seal_root: Path,
    business_provider_id: str = "logistics.business",
    manifest_name: str | None = "seal-manifest.json",
) -> SealedLogisticsObservations:
    """Verify sealed sources and replay observations, without a logistics task pass."""
    provider = next(
        (
            item
            for item in run.environment.providers
            if item.provider_id == business_provider_id
        ),
        None,
    )
    from aero_bench.providers.logistics_business.native_parcel import (
        NATIVE_PARCEL_ADAPTER, NativeParcelBusinessConfig,
    )
    if provider is None or provider.adapter not in {"logistics.business",NATIVE_PARCEL_ADAPTER}:
        raise ValueError(
            "sealed logistics observations require the real declared Business adapter"
        )
    reader.validate_schema_bound_file(provider.config)
    config_type = (NativeParcelBusinessConfig if provider.adapter == NATIVE_PARCEL_ADAPTER
                   else LogisticsBusinessConfig)
    config = config_type.model_validate(
        reader.load_document(provider.config.file)
    )
    if config.provider_id != business_provider_id or config.observation is None:
        raise ValueError(
            "sealed logistics observations require the exact declared Business observation config"
        )
    # Use the same digest-verified Task/config proof as the runtime factory.
    # No Provider session is created, and no factory is registered here.
    LogisticsRuntimeHookFactory(
        config=config, business_provider_id=business_provider_id
    )._prove_declared_config_matches_resolved_run(run, reader)
    validate_logistics_runtime_bindings(
        bindings_document=config.observation.bindings.model_dump(mode="json"),
        reader=reader,
        environment=run.environment,
        agents=run.agents,
        package=config.task_package,
        scenario=run.scenario,
    )
    sources = tuple(
        load_sealed_px4_motion(
            run=run,
            reader=reader,
            seal=seal,
            seal_root=seal_root,
            provider_id=provider_id,
            manifest_name=manifest_name,
        )
        for provider_id in sorted(
            {binding.provider_id for binding in config.observation.bindings.aircraft}
        )
    )
    if not sources:
        raise ValueError(
            "sealed logistics observations require the declared native fleet"
        )
    motion = sources[0].motion
    replay = replay_logistics_motion(
        run_id=run.run_id,
        config=config,
        scenario=run.scenario,
        step_ns=run.environment.clock.step_ns,
        frames=motion.frames,
    )
    artifacts = [
        item
        for item in seal.artifacts
        if item.producer_id == business_provider_id
        and item.artifact_type == "logistics.business.state"
    ]
    if len(artifacts) != 1 or artifacts[0].visibility != "private":
        raise ValueError(
            "sealed logistics observations require one declared private Business state artifact"
        )
    artifact = artifacts[0]
    raw = (seal_root / artifact.relative_path).read_bytes()
    if (
        len(raw) != artifact.size_bytes
        or hashlib.sha256(raw).hexdigest() != artifact.sha256
    ):
        raise ValueError("Business state changed after seal verification")
    document = parse_json_object(raw)
    if canonical_json_bytes(document) != raw:
        raise ValueError(
            "sealed Business state must use its canonical artifact encoding"
        )
    validate_logistics_observation_records(
        replay=replay,
        records=motion.ledger.records,
        business_document=document,
        artifact=artifact,
        config=config,
        event_chain_root=seal.event_chain_root,
    )
    return SealedLogisticsObservations(seal, artifact, replay)
