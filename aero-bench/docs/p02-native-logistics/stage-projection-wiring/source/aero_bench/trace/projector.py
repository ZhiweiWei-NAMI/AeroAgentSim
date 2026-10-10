"""Deterministic sealed-evidence to Public Trace v3 projection."""

from __future__ import annotations

from aero_bench.runtime.events import PUBLIC_AGENT_INTERACTION_FIELDS

import json
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from aero_bench.artifacts.contracts import ArtifactRecord, EvidenceReference, SealManifest
from aero_bench.config.models import Identifier
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.runtime.contracts import (
    SceneState,
    SimulationTime,
    StateAttribute,
    StateAttributeValue,
)
from aero_bench.runtime.events import RunEvent, RunEventAudience
from aero_bench.runtime.ledger import EventLedger, LedgerRecord
from aero_bench.runtime.scene_history import validate_scene_state_history
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace.contracts import (
    PUBLIC_PROJECTOR_VERSION,
    PUBLIC_TRACE_SCHEMA_VERSION,
    PUBLIC_VERIFICATION_SCHEMA_VERSION,
    ArtifactReference,
    ProviderStateKind,
    PublicBuilding,
    PublicEntityDefinition,
    PublicGoalResult,
    PublicMetricResult,
    PublicMissionEvent,
    PublicMissionStatus,
    PublicNetworkEvent,
    PublicNetworkFrame,
    PublicNetworkLinkGeometry,
    PublicNetworkNodeState,
    PublicProviderStatus,
    PublicRunEvent,
    PublicRuntimeArtifact,
    PublicScenario,
    PublicScenarioAsset,
    PublicSensorFrameReference,
    PublicSumoBinding,
    PublicTerminal,
    PublicTrace,
    PublicTrajectory,
    PublicTrajectorySample,
    PublicTrafficLightFrame,
    PublicTrafficLightState,
    PublicVerificationReport,
    validate_public_selector,
)
from aero_bench.trace.vocabulary import (
    PUBLIC_EVENT_EVENT_TYPE,
    PUBLIC_NETWORK_LINK_EVENT_TYPE,
    PUBLIC_PROVIDER_EVENT_TYPES,
    PUBLIC_SENSOR_FRAME_EVENT_TYPE,
    PUBLIC_STATUS_EVENT_TYPE,
    PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE,
    PUBLIC_PARCEL_EVENT_TYPE,
    PUBLIC_PARCEL_PAYLOAD_SCHEMA_ID,
)
from aero_bench.verifier.contracts import (
    GoalResult,
    VerificationReport,
    validate_report_against_run,
)


_PUBLIC_EVENT_SCHEMA = "public.event.v3"
_PUBLIC_NETWORK_LINK_SCHEMA = "public.network.link.v3"
_PUBLIC_STATUS_SCHEMA = "public.status.v3"
_PUBLIC_SENSOR_FRAME_SCHEMA = "sensor.frame_ref.public.v2"
_PUBLIC_TRAFFIC_LIGHT_SCHEMA = "sumo.traffic_light.v1"
# Canonical NativeParcelProjection model version; the wire payload_schema_id
# is PUBLIC_PARCEL_PAYLOAD_SCHEMA_ID (trace vocabulary, Identifier form).
NATIVE_PARCEL_PROJECTION_MODEL_VERSION = "aero-bench.native-parcel-projection/v1"
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
# Policy budget for embedding history, below the viewer's total trace limit.
# Use the resolved artifact capacity, not a partial runtime sample, so catalog,
# terminal projection and sealed replay agree before and after execution.
_MAX_EMBEDDED_SCENE_HISTORY_BYTES = 256 * 1024 * 1024
_SAFE_PUBLIC_INTERACTION_PAYLOADS = {
    **PUBLIC_AGENT_INTERACTION_FIELDS,
    "sumo.traffic_light.v1": frozenset(
        {"snapshot_digest", "simulation_time_ns", "traffic_lights_json"}
    ),
    "logistics.parcel_projection.v1": frozenset({
        "order_id","parcel_id","carrier_entity_id","custody_holder_id",
        "custody_holder_kind","destination_id","parcel_state","authority",
        "source_scene_state_digest","source_stage_barrier_digest","source_observation_digest",
        "scenario_digest","frame_digest","x_m","y_m","z_m","qw","qx","qy","qz",
    }),
}
_FORBIDDEN_PUBLIC_INTERACTIONS = frozenset(
    {"process.stdout.v1", "process.stderr.v1"}
)
_FORBIDDEN_PUBLIC_PAYLOAD_NAMES = frozenset(
    {
        "authentication_id",
        "captured_bytes_b64",
        "chain_of_thought",
        "envelope_json",
        "hidden_reasoning",
        "payload_base64",
        "payload_json",
        "reasoning_trace",
        "token",
    }
)


class PublicProjectorError(ValueError):
    """Sealed evidence cannot be projected without inventing or leaking truth."""


class SealedPublicArtifacts:
    def __init__(self, seal: SealManifest) -> None:
        records: dict[str, ArtifactRecord] = {}
        private_ids: set[str] = set()
        for artifact in seal.artifacts:
            if artifact.artifact_id in records or artifact.artifact_id in private_ids:
                raise PublicProjectorError(
                    f"runtime seal repeats artifact {artifact.artifact_id}"
                )
            if artifact.visibility == "public":
                records[artifact.artifact_id] = artifact
            else:
                private_ids.add(artifact.artifact_id)
        self._records = records
        self._private_ids = private_ids

    @property
    def records(self) -> tuple[ArtifactRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def is_public(self, artifact_id: str, *, context: str) -> bool:
        """Classify sealed evidence without treating unknown IDs as private."""

        if artifact_id in self._private_ids:
            return False
        self.resolve_by_id(artifact_id, context=context)
        return True

    def resolve_by_id(self, artifact_id: str, *, context: str) -> ArtifactRecord:
        record = self._records.get(artifact_id)
        if record is None:
            if artifact_id in self._private_ids:
                raise PublicProjectorError(
                    f"{context} references private artifact {artifact_id}"
                )
            raise PublicProjectorError(
                f"{context} references unsealed artifact {artifact_id}"
            )
        return record

    def reference(
        self,
        *,
        artifact_id: str,
        selector: str,
        context: str,
    ) -> ArtifactReference:
        record = self.resolve_by_id(artifact_id, context=context)
        try:
            return ArtifactReference(
                artifact_id=record.artifact_id,
                selector=selector,
                digest=record.sha256,
                visibility="public",
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise PublicProjectorError(
                f"{context} has an invalid public artifact reference"
            ) from error

    def resolve_reference(
        self,
        reference: ArtifactReference,
        *,
        context: str,
    ) -> ArtifactRecord:
        record = self.resolve_by_id(reference.artifact_id, context=context)
        if record.sha256 != reference.digest:
            raise PublicProjectorError(
                f"{context} artifact digest differs from the runtime seal"
            )
        try:
            validate_public_selector(reference.selector)
        except ValueError as error:
            raise PublicProjectorError(
                f"{context} artifact selector is invalid"
            ) from error
        return record


@dataclass(frozen=True, slots=True)
class _ProviderLifecycle:
    version: str
    implementation_kind: Literal["mechanical_fixture", "production"]
    state: ProviderStateKind
    reset_at_zero: bool


def project_public_trace(
    *,
    run: ResolvedRunSpec,
    ledger: EventLedger,
    seal: SealManifest,
    scene_states: tuple[SceneState, ...],
    report: VerificationReport | None = None,
) -> PublicTrace:
    """Build Public Trace v3 from immutable authoritative inputs only."""

    try:
        ledger.verify()
    except ValueError as error:
        raise PublicProjectorError("authoritative RunEvent ledger is invalid") from error
    if not ledger.records:
        raise PublicProjectorError("authoritative RunEvent ledger is empty")
    if (
        run.run_id != seal.run_id
        or run.execution_scope != seal.execution_scope
        or ledger.chain_root != seal.event_chain_root
    ):
        raise PublicProjectorError("runtime seal identity differs from the run ledger")
    if report is not None:
        if run.execution_scope != "formal_benchmark":
            raise PublicProjectorError(
                "executor validation cannot publish benchmark verification"
            )
        try:
            validate_report_against_run(report, run)
        except ValueError as error:
            raise PublicProjectorError(
                "verification report is not bound to ResolvedRun"
            ) from error

    terminal = _terminal(ledger)
    if report is not None and terminal.kind != "completed":
        raise PublicProjectorError("an aborted run cannot publish verification")
    aborted_before_motion = terminal.kind == "aborted" and not scene_states
    try:
        scene_states = validate_scene_state_history(
            scene_states,
            aborted_before_first_motion=aborted_before_motion,
        )
    except (TypeError, ValueError) as error:
        raise PublicProjectorError("sealed SceneState history is invalid") from error
    _bind_scene_history(run, ledger, scene_states, terminal)

    resources = SealedPublicArtifacts(seal)
    scene_artifact = _scene_history_artifact(seal)
    scene_history_reference = resources.reference(
        artifact_id=scene_artifact.artifact_id,
        selector=scene_artifact.relative_path,
        context="SceneState history",
    )
    scenario = project_public_scenario(run)
    runtime_artifacts = tuple(
        PublicRuntimeArtifact(
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            selector=artifact.relative_path,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            replay_path=f"artifacts/{artifact.sha256}",
        )
        for artifact in resources.records
    )
    public_records = tuple(
        record for record in ledger.records if _has_public_visibility(record.event)
    )
    for record in public_records:
        _validate_public_event(record.event)
    trajectories = _project_trajectories(scene_states)
    network_frames = _project_network_frames(run, scene_states)
    traffic_light_frames = _project_traffic_light_frames(
        run, public_records,
    )
    provider_status = _provider_status(run, ledger, terminal)

    public_event_ids = {record.event.event_id for record in public_records}
    events = tuple(
        project_public_run_event(
            record.event,
            public_event_ids=public_event_ids,
        )
        for record in public_records
    )
    network_events = tuple(
        _project_network_event(
            run,
            record,
            scene_states=scene_states,
            resources=resources,
        )
        for record in public_records
        if record.event.event_type == PUBLIC_NETWORK_LINK_EVENT_TYPE
    )
    mission_status_history = tuple(
        _project_mission_status(run, record)
        for record in public_records
        if record.event.event_type == PUBLIC_STATUS_EVENT_TYPE
    )
    mission_events = tuple(
        _project_mission_event(run, record, resources=resources)
        for record in public_records
        if record.event.event_type == PUBLIC_EVENT_EVENT_TYPE
    )
    sensor_frames = tuple(
        _project_sensor_frame(run, record, resources=resources)
        for record in public_records
        if record.event.event_type == PUBLIC_SENSOR_FRAME_EVENT_TYPE
    )
    verifier_public = (
        None if report is None else _project_report(report, resources=resources)
    )
    phase = (
        "aborted"
        if terminal.kind == "aborted"
        else "verified"
        if verifier_public is not None
        else "sealed"
    )
    try:
        return PublicTrace(
            schema_version=PUBLIC_TRACE_SCHEMA_VERSION,
            projector_version=PUBLIC_PROJECTOR_VERSION,
            run_id=run.run_id,
            suite_id=run.suite_id,
            case_id=run.case_id,
            execution_scope=run.execution_scope,
            phase=phase,
            time=terminal.at,
            event_chain_root=seal.event_chain_root,
            scenario_digest=run.scenario.scenario_digest,
            scenario=scenario,
            scene_state_history_artifact=scene_history_reference,
            runtime_artifacts=runtime_artifacts,
            scene_states=scene_states,
            trajectories=trajectories,
            network_frames=network_frames,
            traffic_light_frames=traffic_light_frames,
            network_events=network_events,
            mission_status_history=mission_status_history,
            mission_status=(
                mission_status_history[-1] if mission_status_history else None
            ),
            mission_events=mission_events,
            sensor_frames=sensor_frames,
            events=events,
            provider_status=provider_status,
            terminal=terminal,
            verifier_public=verifier_public,
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise PublicProjectorError(
            "projected document violates Public Trace v3"
        ) from error


def _scene_history_artifact(seal: SealManifest) -> ArtifactRecord:
    records = tuple(
        artifact
        for artifact in seal.artifacts
        if artifact.artifact_type == "scene.state-history"
    )
    if len(records) != 1:
        raise PublicProjectorError(
            "runtime seal must contain one SceneState history artifact"
        )
    record = records[0]
    if record.producer_id != "harness" or record.visibility != "public":
        raise PublicProjectorError(
            "SceneState history must be a public Harness artifact"
        )
    return record


def _bind_scene_history(
    run: ResolvedRunSpec,
    ledger: EventLedger,
    states: tuple[SceneState, ...],
    terminal: PublicTerminal,
) -> None:
    commits = tuple(
        record
        for record in ledger.records
        if record.event.event_type == "scene.state.committed"
    )
    if len(commits) != len(states):
        raise PublicProjectorError(
            "SceneState history differs from the committed SceneState inventory"
        )
    for record, state in zip(commits, states, strict=True):
        payload = _payload(record)
        if (
            state.run_id != run.run_id
            or state.scenario_digest != run.scenario.scenario_digest
            or record.event.time != state.at
            or payload.get("scene_state_digest") != state.scene_state_digest
        ):
            raise PublicProjectorError(
                "SceneState history is not bound to its RunEvent commit"
            )
    if states:
        if states[-1].at != terminal.at:
            raise PublicProjectorError(
                "terminal time differs from the final committed SceneState"
            )
    elif terminal.at != SimulationTime(tick=0, sim_time_ns=0):
        raise PublicProjectorError(
            "run without SceneStates did not abort at the initial time"
        )


def project_public_scenario(run: ResolvedRunSpec) -> PublicScenario:
    resolved = run.scenario
    public_assets = tuple(
        asset for asset in resolved.assets if asset.classification == "public"
    )
    projected_assets = tuple(
        PublicScenarioAsset(
            asset_id=asset.asset_id,
            selector=asset.file.path,
            sha256=asset.file.sha256,
            size_bytes=asset.byte_size,
            media_type=(None if asset.world is None else asset.world.media_type),
            replay_path=f"assets/{asset.file.sha256}",
            license_id=(
                None if asset.world is None else asset.world.license.license_id
            ),
            license_selector=(
                None if asset.world is None else asset.world.license.file.path
            ),
            license_sha256=(
                None if asset.world is None else asset.world.license.file.sha256
            ),
            license_size_bytes=(
                None if asset.world is None else asset.world.license.byte_size
            ),
            license_replay_path=(
                None
                if asset.world is None
                else f"assets/{asset.world.license.file.sha256}"
            ),
        )
        for asset in public_assets
    )
    public_asset_ids = {asset.asset_id for asset in projected_assets}
    buildings = tuple(
        PublicBuilding(
            building_id=building.building_id,
            entity_id=building.entity_id,
            render_asset_id=building.render_asset_id,
            anchor_east_m=building.anchor_east_m,
            anchor_north_m=building.anchor_north_m,
            base_vertices=building.base_vertices,
            top_vertices=building.top_vertices,
        )
        for building in resolved.buildings
    )
    entities = tuple(
        PublicEntityDefinition(
            entity_id=entity.entity_id,
            kind=entity.kind,
            owner_kind=entity.owner_kind,
            owner_id=entity.owner_id,
            authority_kind=entity.authority_kind,
            state=entity.state,
            model_asset_id=(
                entity.model_asset_id
                if entity.model_asset_id in public_asset_ids
                else None
            ),
            initial_pose=entity.initial_pose,
            selected_launch_override=entity.selected_launch_override,
        )
        for entity in resolved.entities
    )
    sumo = (
        None
        if resolved.sumo is None
        else PublicSumoBinding(
            provider_id=resolved.sumo.provider_id,
            object_bindings=resolved.sumo.object_bindings,
        )
    )
    try:
        return PublicScenario(
            schema_version="aero-bench.public-scenario/v1",
            world_schema_version=resolved.world_schema_version,
            world_id=resolved.world_id,
            world_digest=resolved.world_digest,
            scenario_asset_digest=resolved.scenario_asset_digest,
            scenario_digest=resolved.scenario_digest,
            selected_launch_site_id=resolved.selected_launch_site_id,
            seed=resolved.seed,
            frame_authority=resolved.frame_authority,
            assets=projected_assets,
            base_layers=resolved.base_layers,
            layers=resolved.layers,
            buildings=buildings,
            roads=resolved.roads,
            regions=resolved.regions,
            launch_sites=resolved.launch_sites,
            entities=entities,
            replay_mode=_public_replay_mode(run),
            sensors=resolved.sensors,
            semantic_targets=resolved.semantic_targets,
            weather=resolved.weather,
            sumo=sumo,
            network=resolved.network,
            mission_requirements=resolved.mission_requirements,
            expected_public_assets=resolved.expected_public_assets,
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise PublicProjectorError(
            "ResolvedScenario cannot be safely projected"
        ) from error


def _public_replay_mode(run: ResolvedRunSpec) -> Literal["embedded", "indexed"]:
    package_id = getattr(getattr(run.task, "package", None), "package_id", None)
    if package_id == "urban.uav-recovery-demo.v1":
        return "indexed"
    native_inspection = package_id == "inspection.v1" and any(
        provider.workload.implementation.component_id == "px4.gazebo"
        for provider in run.environment.providers
    )
    if not native_inspection:
        return "embedded"
    histories = tuple(
        item for item in run.artifact_requirements
        if item.artifact_type == "scene.state-history"
    )
    if len(histories) != 1:
        raise PublicProjectorError("native Inspection requires one SceneState history capacity")
    return (
        "indexed"
        if histories[0].max_size_bytes > _MAX_EMBEDDED_SCENE_HISTORY_BYTES
        else "embedded"
    )


def _project_trajectories(
    states: tuple[SceneState, ...],
) -> tuple[PublicTrajectory, ...]:
    samples: dict[str, list[PublicTrajectorySample]] = {}
    for state in states:
        for sample in state.samples:
            if sample.sample_kind != "dynamic":
                continue
            samples.setdefault(sample.entity_id, []).append(
                PublicTrajectorySample(
                    at=state.at,
                    pose=sample.pose,
                    sample_digest=sample.sample_digest,
                )
            )
    return tuple(
        PublicTrajectory(entity_id=entity_id, samples=tuple(samples[entity_id]))
        for entity_id in sorted(samples)
    )


def _project_network_frames(
    run: ResolvedRunSpec,
    states: tuple[SceneState, ...],
) -> tuple[PublicNetworkFrame, ...]:
    network = run.scenario.network
    if network is None:
        return ()
    frames: list[PublicNetworkFrame] = []
    for state in states:
        by_entity = {sample.entity_id: sample for sample in state.samples}
        nodes: list[PublicNetworkNodeState] = []
        node_poses = {}
        for binding in network.node_bindings:
            sample = by_entity.get(binding.entity_id)
            if sample is None:
                raise PublicProjectorError(
                    f"network node {binding.node_id} has no SceneState entity"
                )
            node_poses[binding.node_id] = sample.pose
            nodes.append(
                PublicNetworkNodeState(
                    node_id=binding.node_id,
                    entity_id=binding.entity_id,
                    endpoint_id=binding.endpoint_id,
                    pose=sample.pose,
                )
            )
        links = tuple(
            PublicNetworkLinkGeometry(
                link_id=link.link_id,
                source_node_id=link.source_node_id,
                destination_node_id=link.destination_node_id,
                source_pose=node_poses[link.source_node_id],
                destination_pose=node_poses[link.destination_node_id],
            )
            for link in network.links
        )
        frames.append(
            PublicNetworkFrame(
                at=state.at,
                scene_state_digest=state.scene_state_digest,
                nodes=tuple(nodes),
                links=links,
            )
        )
    return tuple(frames)


def _project_traffic_light_frames(
    run: ResolvedRunSpec,
    records: tuple[LedgerRecord, ...],
) -> tuple[PublicTrafficLightFrame, ...]:
    """Project the SUMO TraCI controller snapshot without deriving a local clock."""

    frames: list[PublicTrafficLightFrame] = []
    sumo = run.scenario.sumo
    for record in records:
        if record.event.event_type != PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE:
            continue
        if sumo is None or record.event.source != sumo.provider_id:
            raise PublicProjectorError("public traffic-light event has no SUMO authority")
        if record.event.payload_schema_id != _PUBLIC_TRAFFIC_LIGHT_SCHEMA:
            raise PublicProjectorError("public traffic-light event uses the wrong schema")
        payload = _payload(record)
        if set(payload) != {"snapshot_digest", "simulation_time_ns", "traffic_lights_json"}:
            raise PublicProjectorError("public traffic-light payload inventory is invalid")
        snapshot_digest = payload["snapshot_digest"]
        simulation_time_ns = payload["simulation_time_ns"]
        if (
            not isinstance(snapshot_digest, str)
            or not isinstance(simulation_time_ns, int)
            or isinstance(simulation_time_ns, bool)
            or simulation_time_ns != record.event.time.sim_time_ns
        ):
            raise PublicProjectorError("public traffic-light payload binding is invalid")
        document = _canonical_json(
            payload["traffic_lights_json"],
            "public traffic-light state document",
        )
        if not isinstance(document, list):
            raise PublicProjectorError("public traffic-light state document is not a list")
        try:
            states = tuple(
                PublicTrafficLightState.model_validate(item) for item in document
            )
            frame = PublicTrafficLightFrame(
                event_id=record.event.event_id,
                sequence=record.event.sequence,
                at=record.event.time,
                provider_id=record.event.source,
                snapshot_digest=snapshot_digest,
                states=states,
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise PublicProjectorError("public traffic-light state is invalid") from error
        frames.append(frame)
    sequences = tuple(frame.sequence for frame in frames)
    if sequences != tuple(sorted(set(sequences))):
        raise PublicProjectorError("public traffic-light frame sequence is not canonical")
    return tuple(frames)


def _terminal(ledger: EventLedger) -> PublicTerminal:
    records = tuple(
        record
        for record in ledger.records
        if record.event.event_type in {"run.completed", "run.aborted"}
    )
    if len(records) != 1 or records[0] != ledger.records[-1]:
        raise PublicProjectorError("RunEvent ledger has no unique terminal event")
    record = records[0]
    if record.event.source != "harness":
        raise PublicProjectorError("terminal event was not emitted by Harness")
    if record.event.event_type == "run.completed":
        return PublicTerminal(
            kind="completed",
            event_id=record.event.event_id,
            at=record.event.time,
            failure_class=None,
            provider_failure_ids=(),
        )
    payload = _payload(record)
    failure_class = payload.get("failure_class")
    failure_ids = _canonical_json(payload.get("provider_failure_ids"), "abort failures")
    if (
        not isinstance(failure_class, str)
        or not isinstance(failure_ids, list)
        or any(not isinstance(item, str) for item in failure_ids)
        or payload.get("provider_failure_count") != len(failure_ids)
    ):
        raise PublicProjectorError("run.aborted payload is invalid")
    return PublicTerminal(
        kind="aborted",
        event_id=record.event.event_id,
        at=record.event.time,
        failure_class=failure_class,
        provider_failure_ids=tuple(failure_ids),
    )


def _provider_status(
    run: ResolvedRunSpec,
    ledger: EventLedger,
    terminal: PublicTerminal,
) -> tuple[PublicProviderStatus, ...]:
    declared = {
        provider.provider_id: provider for provider in run.environment.providers
    }
    lifecycles: dict[str, _ProviderLifecycle] = {}
    for record in ledger.records:
        event = record.event
        if event.event_type == "runtime.identity" and event.source in declared:
            if event.source in lifecycles:
                raise PublicProjectorError("Provider runtime identity is duplicated")
            implementation = declared[event.source].workload.implementation
            payload = _payload(record)
            if (
                payload.get("version") != implementation.version
                or payload.get("implementation_kind") != implementation.kind
            ):
                raise PublicProjectorError(
                    "Provider runtime identity differs from ResolvedRun"
                )
            lifecycles[event.source] = _ProviderLifecycle(
                version=implementation.version,
                implementation_kind=implementation.kind,
                state="starting",
                reset_at_zero=False,
            )
        elif event.event_type == "provider.reset" and event.source in declared:
            lifecycle = lifecycles.get(event.source)
            if lifecycle is None:
                raise PublicProjectorError(
                    "Provider reset precedes its runtime identity"
                )
            lifecycles[event.source] = _ProviderLifecycle(
                version=lifecycle.version,
                implementation_kind=lifecycle.implementation_kind,
                state="ready",
                reset_at_zero=(
                    lifecycle.reset_at_zero
                    or event.time == SimulationTime(tick=0, sim_time_ns=0)
                ),
            )
    if terminal.kind == "completed":
        if set(lifecycles) != set(declared) or any(
            not lifecycle.reset_at_zero for lifecycle in lifecycles.values()
        ):
            raise PublicProjectorError(
                "completed run lacks a complete Provider prepare lifecycle"
            )
    failure_ids = set(terminal.provider_failure_ids)
    statuses: list[PublicProviderStatus] = []
    for provider_id in sorted(declared):
        lifecycle = lifecycles.get(provider_id)
        implementation = declared[provider_id].workload.implementation
        if terminal.kind == "completed":
            state: ProviderStateKind = "complete"
        elif provider_id in failure_ids:
            state = "failed"
        elif lifecycle is None:
            state = "starting"
        else:
            state = lifecycle.state
        statuses.append(
            PublicProviderStatus(
                provider_id=provider_id,
                state=state,
                version=implementation.version,
                implementation_kind=(
                    None if lifecycle is None else lifecycle.implementation_kind
                ),
            )
        )
    return tuple(statuses)


def _validate_public_event(event: RunEvent) -> None:
    if not _has_public_visibility(event):
        raise PublicProjectorError("RunEvent is not explicitly public")
    payload_names = {item.name for item in event.payload}
    if payload_names.intersection(_FORBIDDEN_PUBLIC_PAYLOAD_NAMES):
        raise PublicProjectorError(
            "public RunEvent contains a field reserved for private evidence"
        )
    interaction_type = (
        None if event.interaction is None else event.interaction.interaction_type
    )
    if interaction_type in _FORBIDDEN_PUBLIC_INTERACTIONS:
        raise PublicProjectorError("process streams cannot enter Public Trace")
    if event.source_kind == "provider":
        if event.provider_id != event.source:
            raise PublicProjectorError("public Provider identity is inconsistent")
        if event.event_type == "public.agent-interaction":
            if interaction_type == "agent.observation.v1":
                if event.payload:
                    raise PublicProjectorError(
                        "public Provider observation mirror must have an empty payload"
                    )
                return
            allowed = _SAFE_PUBLIC_INTERACTION_PAYLOADS.get(interaction_type)
            if interaction_type != "agent.query_result.v1" or allowed is None or not {
                item.name for item in event.payload
            }.issubset(allowed):
                raise PublicProjectorError(
                    "public Provider Agent interaction exceeds its safe contract"
                )
            return
        if event.event_type not in PUBLIC_PROVIDER_EVENT_TYPES:
            raise PublicProjectorError(
                "Provider RunEvent is not in the closed public vocabulary"
            )
        expected_interactions = {
            PUBLIC_EVENT_EVENT_TYPE: "mission.event.v1",
            PUBLIC_NETWORK_LINK_EVENT_TYPE: "ns3.link_state.v1",
            PUBLIC_SENSOR_FRAME_EVENT_TYPE: "sensor.frame_ref.v2",
            PUBLIC_STATUS_EVENT_TYPE: "mission.event.v1",
            PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE: "sumo.traffic_light.v1",
            PUBLIC_PARCEL_EVENT_TYPE: "logistics.parcel_projection.v1",
        }
        if interaction_type != expected_interactions[event.event_type]:
            raise PublicProjectorError(
                "public Provider event has the wrong typed interaction"
            )
        if event.event_type == PUBLIC_PARCEL_EVENT_TYPE:
            from aero_bench.tasks.logistics.native_parcel_rpc import NativeParcelProjection
            # The wire payload_schema_id is an Identifier (no slash); the
            # validated projection rebinds the model's own canonical version.
            if event.payload_schema_id != PUBLIC_PARCEL_PAYLOAD_SCHEMA_ID:
                raise PublicProjectorError("parcel projection schema differs")
            fields = {item.name:item.value for item in event.payload}
            if set(fields) != _SAFE_PUBLIC_INTERACTION_PAYLOADS[interaction_type]:
                raise PublicProjectorError("parcel projection fields differ from the typed public contract")
            fields.pop("frame_digest")
            fields["state"] = fields.pop("parcel_state")
            pose = {name:fields.pop(name) for name in ("x_m","y_m","z_m")}
            pose["orientation"] = {name:fields.pop(name) for name in ("qw","qx","qy","qz")}
            try:
                parcel = NativeParcelProjection.model_validate({
                    **fields,"schema_version":NATIVE_PARCEL_PROJECTION_MODEL_VERSION,
                    "run_id":event.run_id,"at":event.time,"pose":pose})
            except ValueError as exc:
                raise PublicProjectorError("invalid typed parcel projection") from exc
            if event.entity_id != parcel.parcel_id:
                raise PublicProjectorError("parcel projection names another entity")
        return
    if interaction_type not in _SAFE_PUBLIC_INTERACTION_PAYLOADS:
        raise PublicProjectorError(
            "non-Provider public RunEvent has no safe projection contract"
        )


def project_public_run_event(
    event: RunEvent,
    *,
    public_event_ids: set[str],
) -> PublicRunEvent:
    if not isinstance(event, RunEvent):
        raise TypeError("public RunEvent projection requires RunEvent")
    _validate_public_event(event)
    interaction_type = (
        None if event.interaction is None else event.interaction.interaction_type
    )
    if interaction_type in _FORBIDDEN_PUBLIC_INTERACTIONS:
        raise PublicProjectorError("process streams cannot enter Public Trace")
    allowed_names = _SAFE_PUBLIC_INTERACTION_PAYLOADS.get(interaction_type, frozenset())
    public_payload = tuple(
        item for item in event.payload if item.name in allowed_names
    )
    parent_event_id = (
        event.parent_event_id
        if event.parent_event_id in public_event_ids
        else None
    )
    causal_event_ids = tuple(
        item for item in event.causal_event_ids if item in public_event_ids
    )
    if parent_event_id is not None and parent_event_id not in causal_event_ids:
        causal_event_ids = tuple(sorted((*causal_event_ids, parent_event_id)))
    return PublicRunEvent(
        run_id=event.run_id,
        event_id=event.event_id,
        event_digest=event.event_digest,
        sequence=event.sequence,
        source_kind=event.source_kind,
        source=event.source,
        event_type=event.event_type,
        at=event.time,
        correlation_id=event.correlation_id,
        parent_event_id=parent_event_id,
        causal_event_ids=causal_event_ids,
        agent_id=event.agent_id,
        provider_id=event.provider_id,
        vehicle_id=event.vehicle_id,
        entity_id=event.entity_id,
        command_id=event.command_id,
        query_id=event.query_id,
        observation_id=event.observation_id,
        payload_schema_id=event.payload_schema_id,
        payload_digest=event.payload_digest,
        interaction_type=interaction_type,
        public_payload=public_payload,
    )


def _project_network_event(
    run: ResolvedRunSpec,
    record: LedgerRecord,
    *,
    scene_states: tuple[SceneState, ...],
    resources: SealedPublicArtifacts,
) -> PublicNetworkEvent:
    event = record.event
    network = run.scenario.network
    if network is None or event.source != network.provider_id:
        raise PublicProjectorError("public network event has no network authority")
    if event.payload_schema_id != _PUBLIC_NETWORK_LINK_SCHEMA:
        raise PublicProjectorError("public network event is not a v3 document")
    document = _single_document(record, name="network_link")
    required = {
        "link_id",
        "source_entity_id",
        "target_entity_id",
        "state",
        "public_properties",
        "provenance",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise PublicProjectorError("public network link document is invalid")
    state = _state_at(scene_states, event.time)
    by_entity = {sample.entity_id: sample for sample in state.samples}
    source_id = _required_identifier(document, "source_entity_id")
    target_id = _required_identifier(document, "target_entity_id")
    network_entity_ids = {
        binding.entity_id for binding in network.node_bindings
    }
    if source_id not in network_entity_ids or target_id not in network_entity_ids:
        raise PublicProjectorError(
            "public network event endpoint has no network-node authority"
        )
    try:
        source_pose = by_entity[source_id].pose
        target_pose = by_entity[target_id].pose
    except KeyError as error:
        raise PublicProjectorError(
            "public network event endpoint is absent from SceneState"
        ) from error
    properties_raw = document["public_properties"]
    provenance_raw = document["provenance"]
    if not isinstance(properties_raw, list) or not isinstance(provenance_raw, list):
        raise PublicProjectorError("public network link evidence is invalid")
    properties: list[StateAttribute] = []
    evidence: list[ArtifactReference] = []
    for index, property_document in enumerate(properties_raw):
        if (
            not isinstance(property_document, dict)
            or set(property_document) != {"name", "value", "provenance"}
            or not isinstance(property_document["provenance"], list)
        ):
            raise PublicProjectorError("public network property is invalid")
        properties.append(
            _state_attribute(
                name=property_document["name"],
                value=property_document["value"],
            )
        )
        evidence.extend(
            _references(
                property_document["provenance"],
                resources=resources,
                context=f"network property {index}",
            )
        )
    evidence.extend(
        _references(
            provenance_raw,
            resources=resources,
            context="network event provenance",
        )
    )
    unique_evidence = {
        (item.artifact_id, item.selector, item.digest): item for item in evidence
    }
    return PublicNetworkEvent(
        event_id=event.event_id,
        sequence=event.sequence,
        at=event.time,
        provider_id=event.source,
        link_id=_required_identifier(document, "link_id"),
        source_entity_id=source_id,
        target_entity_id=target_id,
        source_pose=source_pose,
        target_pose=target_pose,
        state=_required_identifier(document, "state"),
        properties=tuple(sorted(properties, key=lambda item: item.name)),
        evidence=tuple(unique_evidence[key] for key in sorted(unique_evidence)),
    )


def _project_mission_status(
    run: ResolvedRunSpec,
    record: LedgerRecord,
) -> PublicMissionStatus:
    event = record.event
    if event.source not in _business_provider_ids(run):
        raise PublicProjectorError("public mission status has no Business authority")
    if event.payload_schema_id != _PUBLIC_STATUS_SCHEMA:
        raise PublicProjectorError("public mission status is not a v3 document")
    document = _single_document(record, name="status")
    expected = {
        "business_state",
        "observation_state",
        "network_state",
        "task_progress_ratio",
    }
    if not isinstance(document, dict) or set(document) != expected:
        raise PublicProjectorError("public mission status document is invalid")
    return PublicMissionStatus(
        event_id=event.event_id,
        sequence=event.sequence,
        at=event.time,
        provider_id=event.source,
        business_state=_optional_identifier(document["business_state"]),
        observation_state=_optional_identifier(document["observation_state"]),
        network_state=_optional_identifier(document["network_state"]),
        task_progress_ratio=cast(float | None, document["task_progress_ratio"]),
    )


def _project_mission_event(
    run: ResolvedRunSpec,
    record: LedgerRecord,
    *,
    resources: SealedPublicArtifacts,
) -> PublicMissionEvent:
    event = record.event
    declared_provider_ids = {
        provider.provider_id for provider in run.environment.providers
    }
    if event.source not in declared_provider_ids:
        raise PublicProjectorError("public mission event has no Provider authority")
    if event.payload_schema_id != _PUBLIC_EVENT_SCHEMA:
        raise PublicProjectorError("public mission event is not a v3 document")
    payload = _payload(record)
    allowed = {"severity", "message", "entity_id", "state", "evidence"}
    if set(payload) - allowed or not {"severity", "state", "evidence"} <= set(
        payload
    ):
        raise PublicProjectorError("public mission event payload is invalid")
    if payload["severity"] not in {"info", "warning", "error", "critical"}:
        raise PublicProjectorError("public mission event severity is invalid")
    evidence_document = _canonical_json(payload["evidence"], "mission evidence")
    if not isinstance(evidence_document, list):
        raise PublicProjectorError("public mission event evidence is invalid")
    return PublicMissionEvent(
        event_id=event.event_id,
        sequence=event.sequence,
        at=event.time,
        provider_id=event.source,
        entity_id=(
            None
            if "entity_id" not in payload
            else _optional_identifier(payload["entity_id"])
        ),
        state=_required_identifier(payload, "state"),
        evidence=_references(
            evidence_document,
            resources=resources,
            context="mission event",
        ),
    )


def _project_sensor_frame(
    run: ResolvedRunSpec,
    record: LedgerRecord,
    *,
    resources: SealedPublicArtifacts,
) -> PublicSensorFrameReference:
    event = record.event
    declared_provider_ids = {
        provider.provider_id for provider in run.environment.providers
    }
    if event.source not in declared_provider_ids or event.provider_id != event.source:
        raise PublicProjectorError("public sensor frame has no Provider authority")
    if event.payload_schema_id != _PUBLIC_SENSOR_FRAME_SCHEMA:
        raise PublicProjectorError("public sensor frame is not a v1 document")
    if event.interaction is None or (
        event.interaction.interaction_type != "sensor.frame_ref.v2"
    ):
        raise PublicProjectorError("public sensor frame lacks its typed interaction")
    payload = _payload(record)
    expected = {
        "artifact_id",
        "frame_id",
        "observation_id",
        "payload_digest",
        "selector",
    }
    task_package = getattr(getattr(run, "task", None), "package", None)
    urban_recovery = getattr(task_package, "package_id", None) == (
        "urban.uav-recovery-demo.v1"
    )
    provider = next(
        item for item in run.environment.providers if item.provider_id == event.source
    )
    native_inspection = (
        getattr(task_package, "package_id", None) == "inspection.v1"
        and provider.workload.implementation.kind == "production"
    )
    if native_inspection:
        expected |= {
            "camera_id", "camera_pose_sha256", "capture_pose_source",
            "engine_sim_time_ns", "height", "image_sha256",
            "logical_origin_engine_ns", "media_type", "size_bytes", "source",
            "target_pose_sha256", "vehicle_id", "width",
        }
    if urban_recovery:
        expected |= {
            "camera_id",
            "engine_sim_time_ns",
            "height",
            "image_sha256",
            "intrinsics_sha256",
            "logical_origin_engine_ns",
            "media_type",
            "pose_sha256",
            "size_bytes",
            "source",
            "vehicle_id",
            "width",
        }
    if set(payload) != expected:
        raise PublicProjectorError("public sensor frame payload is invalid")
    frame_id = _required_identifier(payload, "frame_id")
    observation_id = _required_identifier(payload, "observation_id")
    if event.frame_id != frame_id or event.observation_id != observation_id:
        raise PublicProjectorError("public sensor frame identity is inconsistent")
    if native_inspection:
        _validate_native_inspection_frame(run, event, payload, resources)
    if urban_recovery:
        _required_identifier(payload, "camera_id")
        _required_identifier(payload, "vehicle_id")
        if (
            payload.get("width") != 640
            or payload.get("height") != 480
            or payload.get("media_type") != "image/png"
            or payload.get("source") != "gazebo.camera"
            or not isinstance(payload.get("engine_sim_time_ns"), int)
            or payload.get("engine_sim_time_ns", 0) < 1
            or not isinstance(payload.get("logical_origin_engine_ns"), int)
            or payload.get("logical_origin_engine_ns", -1) < 0
            or not isinstance(payload.get("size_bytes"), int)
            or not 0 < payload.get("size_bytes", 0) <= 2 * 1024 * 1024
            or payload.get("engine_sim_time_ns")
            - payload.get("logical_origin_engine_ns", 0)
            != event.time.sim_time_ns
        ):
            raise PublicProjectorError("urban sensor frame metadata is invalid")
        for digest_name in (
            "image_sha256",
            "pose_sha256",
            "intrinsics_sha256",
        ):
            _required_digest(payload, digest_name)
    return PublicSensorFrameReference(
        event_id=event.event_id,
        sequence=event.sequence,
        at=event.time,
        provider_id=event.source,
        observation_id=observation_id,
        frame_id=frame_id,
        payload_digest=_required_digest(payload, "payload_digest"),
        artifact=resources.reference(
            artifact_id=_required_identifier(payload, "artifact_id"),
            selector=cast(str, payload["selector"]),
            context="sensor frame",
        ),
    )


def _validate_native_inspection_frame(
    run: ResolvedRunSpec,
    event: RunEvent,
    payload: dict[str, object],
    resources: SealedPublicArtifacts,
) -> None:
    # Keep this domain import out of the Agent/Gateway/runtime module bootstrap.
    from aero_bench.tasks.inspection.contracts import PublicSensorFrameRecord

    archive = resources.resolve_by_id(
        _required_identifier(payload, "artifact_id"), context="inspection camera frame"
    )
    indices = tuple(
        artifact for artifact in resources.records
        if artifact.artifact_type == "sensor-frame" and artifact.producer_id == event.source
    )
    if (
        archive.artifact_type != "camera-frame-data"
        or archive.producer_id != event.source
        or len(indices) != 1
    ):
        raise PublicProjectorError("inspection camera frame lacks its public archive/index")
    for name in (
        "payload_digest", "image_sha256", "camera_pose_sha256", "target_pose_sha256"
    ):
        _required_digest(payload, name)
    try:
        # Inspection capture references are published at the next Provider barrier.
        capture_time = SimulationTime(
            tick=event.time.tick - 1,
            sim_time_ns=event.time.sim_time_ns - run.environment.clock.step_ns,
        )
        PublicSensorFrameRecord.model_validate(
            {
                "schema_version": "aero-bench.public-sensor-frame-artifact/v2",
                "source_artifact_id": indices[0].artifact_id,
                "run_id": run.run_id,
                "time": capture_time,
                **{name: value for name, value in payload.items() if name != "artifact_id"},
            },
            strict=True,
        )
    except (TypeError, ValueError) as error:
        raise PublicProjectorError("inspection camera frame metadata/clock is invalid") from error


def _project_report(
    report: VerificationReport,
    *,
    resources: SealedPublicArtifacts,
) -> PublicVerificationReport:
    goals = tuple(
        _project_goal(goal, resources=resources)
        for goal in sorted(report.goals, key=lambda item: item.goal_id)
    )
    return PublicVerificationReport(
        schema_version=PUBLIC_VERIFICATION_SCHEMA_VERSION,
        run_id=report.run_id,
        status=report.status,
        goals=goals,
        coverage_complete=report.coverage_complete,
    )


def _project_goal(
    goal: GoalResult,
    *,
    resources: SealedPublicArtifacts,
) -> PublicGoalResult:
    metrics = tuple(
        PublicMetricResult(
            metric_id=metric.metric_id,
            value=metric.value,
            unit=metric.unit,
            evidence=_public_metric_evidence(
                metric.evidence,
                resources=resources,
                context=f"verification metric {metric.metric_id}",
            ),
        )
        for metric in sorted(goal.metrics, key=lambda item: item.metric_id)
    )
    return PublicGoalResult(
        goal_id=goal.goal_id,
        passed=goal.passed,
        metrics=metrics,
        failure_class=goal.failure_class,
    )


def _public_metric_evidence(
    evidence: tuple[EvidenceReference, ...],
    *,
    resources: SealedPublicArtifacts,
    context: str,
) -> tuple[ArtifactReference, ...]:
    # The full verifier report retains private inputs; viewers receive only
    # sealed public support without changing measurements or goal outcomes.
    references = [
        _evidence_reference(reference, resources=resources, context=context)
        for reference in evidence
        if resources.is_public(reference.artifact_id, context=context)
    ]
    if not references:
        raise PublicProjectorError(f"{context} has no sealed public evidence")
    return _canonical_references(references, context=context)


def _canonical_references(
    references: list[ArtifactReference],
    *,
    context: str,
) -> tuple[ArtifactReference, ...]:
    by_key: dict[tuple[str, str, str], ArtifactReference] = {}
    for reference in references:
        key = (
            str(reference.artifact_id),
            reference.selector,
            str(reference.digest),
        )
        if key in by_key:
            raise PublicProjectorError(f"{context} contains duplicate evidence")
        by_key[key] = reference
    return tuple(by_key[key] for key in sorted(by_key))


def _evidence_reference(
    reference: EvidenceReference,
    *,
    resources: SealedPublicArtifacts,
    context: str,
) -> ArtifactReference:
    if reference.selector is None:
        raise PublicProjectorError(f"{context} omits its public selector")
    return resources.reference(
        artifact_id=reference.artifact_id,
        selector=reference.selector,
        context=context,
    )


def _references(
    values: list[object],
    *,
    resources: SealedPublicArtifacts,
    context: str,
) -> tuple[ArtifactReference, ...]:
    references: list[ArtifactReference] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != {"artifact_id", "selector"}:
            raise PublicProjectorError(f"{context} reference {index} is invalid")
        references.append(
            resources.reference(
                artifact_id=_required_identifier(value, "artifact_id"),
                selector=cast(str, value["selector"]),
                context=f"{context} reference {index}",
            )
        )
    return _canonical_references(references, context=context)


def _single_document(record: LedgerRecord, *, name: str) -> object:
    payload = _payload(record)
    if set(payload) != {name}:
        raise PublicProjectorError(
            f"{record.event.event_type} must contain one '{name}' document"
        )
    return _canonical_json(payload[name], f"{record.event.event_type} document")


def _canonical_json(value: object, context: str) -> object:
    if not isinstance(value, str):
        raise PublicProjectorError(f"{context} must be canonical JSON text")
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise PublicProjectorError(f"{context} is invalid JSON") from error
    if canonical_json_bytes(parsed).decode("utf-8") != value:
        raise PublicProjectorError(f"{context} is not canonical JSON")
    return parsed


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> object:
    raise ValueError(f"non-finite JSON value: {value}")


def _payload(record: LedgerRecord) -> dict[str, StateAttributeValue]:
    return {item.name: item.value for item in record.event.payload}


def _has_public_visibility(event: RunEvent) -> bool:
    return RunEventAudience(scope="public", audience_id=None) in event.visibility


def _business_provider_ids(run: ResolvedRunSpec) -> frozenset[str]:
    return frozenset(
        provider.provider_id
        for provider in run.environment.providers
        if provider.adapter.endswith("inspection_business")
        or any(
            capability.startswith(("business.", "work_order."))
            for capability in provider.capabilities
        )
    )


def _state_at(
    states: tuple[SceneState, ...],
    at: SimulationTime,
) -> SceneState:
    if at.tick < 1 or at.tick > len(states):
        raise PublicProjectorError("public event has no exact SceneState tick")
    state = states[at.tick - 1]
    if state.at != at:
        raise PublicProjectorError("public event time differs from SceneState time")
    return state


def _required_identifier(values: dict[str, object], name: str) -> str:
    value = values.get(name)
    try:
        return _IDENTIFIER_ADAPTER.validate_python(value, strict=True)
    except (TypeError, ValidationError, ValueError) as error:
        raise PublicProjectorError(f"field {name} must be an identifier") from error


def _optional_identifier(value: object) -> str | None:
    if value is None:
        return None
    try:
        return _IDENTIFIER_ADAPTER.validate_python(value, strict=True)
    except (TypeError, ValidationError, ValueError) as error:
        raise PublicProjectorError("optional identifier is invalid") from error


def _required_digest(values: dict[str, object], name: str) -> str:
    value = values.get(name)
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value == "0" * 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PublicProjectorError(f"field {name} must be a real SHA-256 digest")
    return value


def _state_attribute(*, name: object, value: object) -> StateAttribute:
    if not isinstance(name, str):
        raise PublicProjectorError("public property name is invalid")
    if value is None:
        value_type = "null"
    elif isinstance(value, bool):
        value_type = "bool"
    elif isinstance(value, int):
        value_type = "int"
    elif isinstance(value, float):
        value_type = "float"
    elif isinstance(value, str):
        value_type = "str"
    else:
        raise PublicProjectorError("public property value is not scalar")
    try:
        return StateAttribute(name=name, value_type=value_type, value=value)
    except (TypeError, ValidationError, ValueError) as error:
        raise PublicProjectorError("public property is invalid") from error


__all__ = [
    "PUBLIC_PROJECTOR_VERSION",
    "PublicProjectorError",
    "SealedPublicArtifacts",
    "project_public_run_event",
    "project_public_scenario",
    "project_public_trace",
]
