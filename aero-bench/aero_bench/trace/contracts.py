"""Strict Public Trace v3 contracts for exact sealed replay.

Public Trace v3 contains no latest-snapshot shortcut and no compatibility shape.
Every dynamic pose comes from the canonical sealed SceneState history, every
trajectory is an exact projection of those samples, and every published event
originates in a RunEvent explicitly authorized for public visibility.
"""

from __future__ import annotations

from aero_bench.runtime.events import PUBLIC_AGENT_INTERACTION_FIELDS

import hashlib
import math
import re
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import ExecutionScope, Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SceneState, SimulationTime, StateAttribute
from aero_bench.runtime.events import AgentInteractionType, RunEventSourceKind
from aero_bench.runtime.scene_history import scene_state_jsonl_bytes
from aero_bench.world import contracts as world_contracts
from aero_bench.world.resolved import (
    ResolvedCoordinate,
    ResolvedFrameAuthority,
    ResolvedLaunchSite,
    ResolvedMissionRequirement,
    ResolvedNetworkConfiguration,
    ResolvedPose,
    ResolvedRegion,
    ResolvedRoad,
    ResolvedSemanticTarget,
    ResolvedSensor,
)


PUBLIC_TRACE_SCHEMA_VERSION = "aero-bench.public-trace/v3"
PUBLIC_VERIFICATION_SCHEMA_VERSION = "aero-bench.verification-public/v3"
PUBLIC_PROJECTOR_VERSION = "aero-bench.public-projector/v2"

TracePhase: TypeAlias = Literal["sealed", "verified", "aborted"]
TerminalKind: TypeAlias = Literal["completed", "aborted"]
ProviderStateKind: TypeAlias = Literal["starting", "ready", "complete", "failed"]

_SELECTOR_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
_SELECTOR_PATTERN = re.compile(
    rf"{_SELECTOR_SEGMENT}(?:/{_SELECTOR_SEGMENT})*"
    rf"(?:#{_SELECTOR_SEGMENT}(?:/{_SELECTOR_SEGMENT})*)?\Z"
)
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_REPLAY_PATH = re.compile(r"^(assets|artifacts)/[0-9a-f]{64}$")
_REPLAY_FILE_PATH = re.compile(
    r"^(?:public-trace\.json|(?:assets|artifacts)/[0-9a-f]{64})$"
)
_UNIT_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.%^-]*"
_UNIT_PATTERN = re.compile(rf"{_UNIT_SEGMENT}(?:/{_UNIT_SEGMENT})?\Z")
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


def _finite(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _real_digest(value: str) -> str:
    if value == "0" * 64:
        raise ValueError("digest cannot be a placeholder")
    return value


def validate_public_selector(value: str) -> str:
    if (
        value.strip() != value
        or "\\" in value
        or "//" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        or _SELECTOR_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("selector is not a normalized relative public selector")
    return value


def _sorted_unique(label: str, values: tuple[str, ...]) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


def _artifact_reference_keys(
    references: tuple["ArtifactReference", ...],
) -> tuple[str, ...]:
    return tuple(
        f"{reference.artifact_id}:{reference.selector}:{reference.digest}"
        for reference in references
    )


class ArtifactReference(StrictModel):
    artifact_id: Identifier
    selector: Annotated[str, Field(max_length=512)]
    digest: Sha256
    visibility: Literal["public"]

    _selector = field_validator("selector")(validate_public_selector)
    _digest = field_validator("digest")(_real_digest)


class PublicScenarioAsset(StrictModel):
    asset_id: Identifier
    selector: Annotated[str, Field(max_length=512)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0)]
    media_type: Annotated[str, Field(min_length=3, max_length=128)] | None
    replay_path: Annotated[str, Field(pattern=_REPLAY_PATH.pattern)]
    license_id: Annotated[str, Field(min_length=1, max_length=256)] | None
    license_selector: Annotated[str, Field(max_length=512)] | None
    license_sha256: Sha256 | None
    license_size_bytes: Annotated[int, Field(gt=0)] | None
    license_replay_path: Annotated[str, Field(pattern=_REPLAY_PATH.pattern)] | None

    @field_validator("selector", "license_selector")
    @classmethod
    def selectors(cls, value: str | None) -> str | None:
        return None if value is None else validate_public_selector(value)

    @field_validator("sha256", "license_sha256")
    @classmethod
    def digests(cls, value: str | None) -> str | None:
        return None if value is None else _real_digest(value)

    @field_validator("media_type")
    @classmethod
    def normalized_media_type(cls, value: str | None) -> str | None:
        if value is not None and _MEDIA_TYPE.fullmatch(value) is None:
            raise ValueError("scenario asset media_type is invalid")
        return value

    @model_validator(mode="after")
    def license_is_complete(self) -> "PublicScenarioAsset":
        license_values = (
            self.license_id,
            self.license_selector,
            self.license_sha256,
            self.license_size_bytes,
            self.license_replay_path,
        )
        if any(value is not None for value in license_values) and any(
            value is None for value in license_values
        ):
            raise ValueError("scenario asset license fields must be all present or absent")
        if self.replay_path != f"assets/{self.sha256}":
            raise ValueError("scenario asset replay path must be content addressed")
        if self.license_sha256 is not None and (
            self.license_replay_path != f"assets/{self.license_sha256}"
        ):
            raise ValueError("scenario license replay path must be content addressed")
        return self


class PublicRuntimeArtifact(StrictModel):
    artifact_id: Identifier
    artifact_type: Identifier
    selector: Annotated[str, Field(max_length=512)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]
    replay_path: Annotated[str, Field(pattern=_REPLAY_PATH.pattern)]

    _selector = field_validator("selector")(validate_public_selector)
    _digest = field_validator("sha256")(_real_digest)

    @model_validator(mode="after")
    def content_addressed(self) -> "PublicRuntimeArtifact":
        if self.replay_path != f"artifacts/{self.sha256}":
            raise ValueError("runtime artifact replay path must be content addressed")
        return self


class PublicReplayFile(StrictModel):
    relative_path: Annotated[str, Field(pattern=_REPLAY_FILE_PATH.pattern)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]

    _digest = field_validator("sha256")(_real_digest)

    @model_validator(mode="after")
    def content_addressed(self) -> "PublicReplayFile":
        if self.relative_path != "public-trace.json" and not self.relative_path.endswith(
            self.sha256
        ):
            raise ValueError("replay content path must equal its SHA-256 digest")
        return self


class PublicReplayShard(StrictModel):
    relative_path: Annotated[str, Field(pattern=_REPLAY_PATH.pattern)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0)]
    first_tick: int = Field(ge=1)
    last_tick: int = Field(ge=1)
    scene_state_count: int = Field(gt=0, le=256)

    _digest = field_validator("sha256")(_real_digest)

    @model_validator(mode="after")
    def content_and_range(self) -> "PublicReplayShard":
        if self.relative_path != f"artifacts/{self.sha256}":
            raise ValueError("replay shard path must equal its SHA-256 digest")
        if self.last_tick < self.first_tick:
            raise ValueError("replay shard tick range is invalid")
        if self.scene_state_count != self.last_tick - self.first_tick + 1:
            raise ValueError("replay shard count does not match its tick range")
        return self


class PublicReplayIndex(StrictModel):
    schema_version: Literal["aero-bench.public-replay-index/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    event_chain_root: Sha256
    first_tick: int = Field(ge=0)
    last_tick: int = Field(ge=0)
    scene_state_count: int = Field(ge=0)
    shards: tuple[PublicReplayShard, ...]

    @field_validator("run_id", "scenario_digest", "event_chain_root")
    @classmethod
    def real_digests(cls, value: str) -> str:
        return _real_digest(value)

    @model_validator(mode="after")
    def closed_ranges(self) -> "PublicReplayIndex":
        paths = tuple(shard.relative_path for shard in self.shards)
        if len(paths) != len(set(paths)):
            raise ValueError("replay shard paths must be unique")
        if self.scene_state_count == 0:
            if self.first_tick != 0 or self.last_tick != 0 or self.shards:
                raise ValueError("empty replay index has a non-empty coverage")
            return self
        if (
            self.first_tick != 1
            or self.last_tick != self.scene_state_count
            or not self.shards
        ):
            raise ValueError("replay index coverage must start at tick one")
        expected_tick = 1
        for offset, shard in enumerate(self.shards):
            if shard.first_tick != expected_tick:
                raise ValueError("replay shards must cover contiguous ticks")
            if offset < len(self.shards) - 1 and shard.scene_state_count != 256:
                raise ValueError("non-final replay shards must contain 256 records")
            expected_tick = shard.last_tick + 1
        if expected_tick != self.last_tick + 1:
            raise ValueError("replay shards do not cover the complete history")
        return self


class PublicReplayManifest(StrictModel):
    schema_version: Literal["aero-bench.public-replay-manifest/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    event_chain_root: Sha256
    trace_sha256: Sha256
    files: tuple[PublicReplayFile, ...] = Field(min_length=1)
    replay_mode: Literal["embedded", "indexed"] = "embedded"
    replay_index: PublicReplayFile | None = None
    scene_state_history: PublicReplayFile | None = None

    @field_validator("run_id", "scenario_digest", "event_chain_root", "trace_sha256")
    @classmethod
    def real_digests(cls, value: str) -> str:
        return _real_digest(value)

    @model_validator(mode="after")
    def closed_inventory(self) -> "PublicReplayManifest":
        paths = tuple(item.relative_path for item in self.files)
        _sorted_unique("public replay file", paths)
        trace_files = tuple(
            item for item in self.files if item.relative_path == "public-trace.json"
        )
        if len(trace_files) != 1 or trace_files[0].sha256 != self.trace_sha256:
            raise ValueError("public replay manifest does not bind its trace")
        if self.replay_mode == "indexed":
            if self.replay_index is None or self.scene_state_history is None:
                raise ValueError("indexed replay must declare its index and SceneState history")
        elif self.replay_index is not None:
            raise ValueError("replay index requires an explicit indexed replay declaration")
        for label, reference in (
            ("replay index", self.replay_index),
            ("SceneState history", self.scene_state_history),
        ):
            if reference is None:
                continue
            if reference.relative_path != f"artifacts/{reference.sha256}":
                raise ValueError(f"{label} must be a content-addressed artifact")
            matching = tuple(item for item in self.files if item.relative_path == reference.relative_path)
            if len(matching) != 1 or matching[0] != reference:
                raise ValueError(f"public replay manifest does not bind its {label}")
        return self


class PublicBuilding(StrictModel):
    building_id: Identifier
    entity_id: Identifier
    render_asset_id: Identifier
    anchor_east_m: float
    anchor_north_m: float
    base_vertices: tuple[ResolvedCoordinate, ...] = Field(min_length=3)
    top_vertices: tuple[ResolvedCoordinate, ...] = Field(min_length=3)

    @field_validator("anchor_east_m", "anchor_north_m")
    @classmethod
    def finite_anchor(cls, value: float) -> float:
        return _finite(value, "building anchor")


class PublicEntityDefinition(StrictModel):
    entity_id: Identifier
    kind: world_contracts.EntityKind
    owner_kind: Literal["scenario", "provider"]
    owner_id: Identifier
    authority_kind: world_contracts.AuthorityKind
    state: Literal["static", "dynamic"]
    model_asset_id: Identifier | None
    initial_pose: ResolvedPose
    selected_launch_override: bool


class PublicSumoBinding(StrictModel):
    provider_id: Identifier
    object_bindings: tuple[world_contracts.SumoEntityBinding, ...]

    @model_validator(mode="after")
    def canonical_bindings(self) -> "PublicSumoBinding":
        _sorted_unique(
            "SUMO object binding IDs",
            tuple(binding.sumo_object_id for binding in self.object_bindings),
        )
        return self


class PublicScenario(StrictModel):
    schema_version: Literal["aero-bench.public-scenario/v1"]
    world_schema_version: Literal["aero-bench.world/v2"]
    world_id: Identifier
    world_digest: Sha256
    scenario_asset_digest: Sha256
    scenario_digest: Sha256
    selected_launch_site_id: Identifier | None
    seed: Annotated[int, Field(ge=0)]
    frame_authority: ResolvedFrameAuthority
    assets: tuple[PublicScenarioAsset, ...]
    base_layers: tuple[world_contracts.ViewerLayerSource, ...]
    layers: tuple[world_contracts.PublicLayer, ...]
    buildings: tuple[PublicBuilding, ...]
    roads: tuple[ResolvedRoad, ...]
    regions: tuple[ResolvedRegion, ...]
    launch_sites: tuple[ResolvedLaunchSite, ...]
    entities: tuple[PublicEntityDefinition, ...] = Field(min_length=1)
    replay_mode: Literal["embedded", "indexed"] = "embedded"
    sensors: tuple[ResolvedSensor, ...]
    semantic_targets: tuple[ResolvedSemanticTarget, ...]
    weather: tuple[world_contracts.WeatherSample, ...] = Field(min_length=1)
    sumo: PublicSumoBinding | None
    network: ResolvedNetworkConfiguration | None
    mission_requirements: tuple[ResolvedMissionRequirement, ...]
    expected_public_assets: tuple[world_contracts.ExpectedPublicAsset, ...]

    @field_validator("world_digest", "scenario_asset_digest", "scenario_digest")
    @classmethod
    def real_digests(cls, value: str) -> str:
        return _real_digest(value)

    @model_validator(mode="after")
    def references_are_closed(self) -> "PublicScenario":
        for label, values in (
            ("asset", tuple(asset.asset_id for asset in self.assets)),
            ("base layer", tuple(layer.layer_id for layer in self.base_layers)),
            ("layer", tuple(layer.layer_id for layer in self.layers)),
            ("building", tuple(item.building_id for item in self.buildings)),
            ("road", tuple(item.road_id for item in self.roads)),
            ("region", tuple(item.region_id for item in self.regions)),
            ("launch site", tuple(item.launch_site_id for item in self.launch_sites)),
            ("entity", tuple(item.entity_id for item in self.entities)),
            ("sensor", tuple(item.sensor_id for item in self.sensors)),
            ("target", tuple(item.target_id for item in self.semantic_targets)),
            ("weather", tuple(item.sample_id for item in self.weather)),
            (
                "mission requirement",
                tuple(item.requirement_id for item in self.mission_requirements),
            ),
            (
                "expected public asset",
                tuple(item.asset_id for item in self.expected_public_assets),
            ),
        ):
            _sorted_unique(label, values)
        asset_ids = {asset.asset_id for asset in self.assets}
        for layer in (*self.base_layers, *self.layers):
            if layer.asset_id not in asset_ids:
                raise ValueError("public layer references a non-public asset")
        if self.frame_authority.geoid_correction_asset_id not in asset_ids:
            raise ValueError("public frame authority geoid asset is unavailable")
        if self.frame_authority.terrain_height_asset_id not in asset_ids:
            raise ValueError("public frame authority terrain asset is unavailable")
        entity_ids = {entity.entity_id for entity in self.entities}
        for entity in self.entities:
            if entity.model_asset_id is not None and entity.model_asset_id not in asset_ids:
                raise ValueError("public entity model is unavailable")
        for building in self.buildings:
            if (
                building.entity_id not in entity_ids
                or building.render_asset_id not in asset_ids
            ):
                raise ValueError("public building references unavailable content")
        launch_ids = {site.launch_site_id for site in self.launch_sites}
        if self.selected_launch_site_id is None:
            if (
                self.launch_sites
                or any(entity.state != "static" or entity.selected_launch_override for entity in self.entities)
                or self.sensors or self.semantic_targets or self.mission_requirements
                or self.sumo is not None or self.network is not None
            ):
                raise ValueError("no-launch public scenario must be static and non-physical")
        elif self.selected_launch_site_id not in launch_ids:
            raise ValueError("selected public launch site is unavailable")
        expected_public_asset_ids = {
            asset.asset_id for asset in self.expected_public_assets
        }
        for requirement in self.mission_requirements:
            if (
                requirement.expected_public_asset_id is not None
                and requirement.expected_public_asset_id
                not in expected_public_asset_ids
            ):
                raise ValueError(
                    "mission requirement references an unavailable public output"
                )
        return self


class PublicTrajectorySample(StrictModel):
    at: SimulationTime
    pose: ResolvedPose
    sample_digest: Sha256

    _digest = field_validator("sample_digest")(_real_digest)


class PublicTrajectory(StrictModel):
    entity_id: Identifier
    samples: tuple[PublicTrajectorySample, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def contiguous_samples(self) -> "PublicTrajectory":
        for expected_tick, sample in enumerate(self.samples, start=1):
            if sample.at.tick != expected_tick:
                raise ValueError("trajectory samples must cover every tick from one")
            if expected_tick > 1 and (
                sample.at.sim_time_ns <= self.samples[expected_tick - 2].at.sim_time_ns
            ):
                raise ValueError("trajectory simulation time must strictly advance")
        return self


class PublicNetworkNodeState(StrictModel):
    node_id: Identifier
    entity_id: Identifier
    endpoint_id: Identifier
    pose: ResolvedPose


class PublicNetworkLinkGeometry(StrictModel):
    link_id: Identifier
    source_node_id: Identifier
    destination_node_id: Identifier
    source_pose: ResolvedPose
    destination_pose: ResolvedPose


class PublicNetworkFrame(StrictModel):
    at: SimulationTime
    scene_state_digest: Sha256
    nodes: tuple[PublicNetworkNodeState, ...]
    links: tuple[PublicNetworkLinkGeometry, ...]

    _digest = field_validator("scene_state_digest")(_real_digest)

    @model_validator(mode="after")
    def canonical_network_frame(self) -> "PublicNetworkFrame":
        _sorted_unique("network frame node", tuple(item.node_id for item in self.nodes))
        _sorted_unique("network frame link", tuple(item.link_id for item in self.links))
        return self


class PublicTrafficLightState(StrictModel):
    """One controller state read from SUMO TraCI, never locally interpolated."""

    # SUMO's external IDs can be numeric; they are not AERO entity identifiers.
    signal_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    phase_index: int = Field(ge=0)
    next_switch_s: float
    program_id: str = Field(min_length=1)
    telemetry_source: Literal["sumo-traci"]

    @field_validator("next_switch_s")
    @classmethod
    def finite_next_switch(cls, value: float) -> float:
        return _finite(value, "traffic-light next switch")


class PublicTrafficLightFrame(StrictModel):
    """Authoritative traffic-light snapshot projected from a SUMO provider event."""

    event_id: Identifier
    sequence: int = Field(ge=0)
    at: SimulationTime
    provider_id: Identifier
    snapshot_digest: Sha256
    states: tuple[PublicTrafficLightState, ...]

    _digest = field_validator("snapshot_digest")(_real_digest)

    @model_validator(mode="after")
    def canonical_states(self) -> "PublicTrafficLightFrame":
        _sorted_unique("traffic-light state", tuple(item.signal_id for item in self.states))
        return self


class PublicNetworkEvent(StrictModel):
    event_id: Identifier
    sequence: Annotated[int, Field(ge=0)]
    at: SimulationTime
    provider_id: Identifier
    link_id: Identifier
    source_entity_id: Identifier
    target_entity_id: Identifier
    source_pose: ResolvedPose
    target_pose: ResolvedPose
    state: Identifier
    properties: tuple[StateAttribute, ...]
    evidence: tuple[ArtifactReference, ...]

    @model_validator(mode="after")
    def canonical_fields(self) -> "PublicNetworkEvent":
        _sorted_unique(
            "network event property",
            tuple(item.name for item in self.properties),
        )
        _sorted_unique(
            "network event evidence",
            _artifact_reference_keys(self.evidence),
        )
        return self


class PublicMissionStatus(StrictModel):
    event_id: Identifier
    sequence: Annotated[int, Field(ge=0)]
    at: SimulationTime
    provider_id: Identifier
    business_state: Identifier | None
    observation_state: Identifier | None
    network_state: Identifier | None
    task_progress_ratio: Annotated[float, Field(ge=0.0, le=1.0)] | None

    @field_validator("task_progress_ratio")
    @classmethod
    def finite_ratio(cls, value: float | None) -> float | None:
        return None if value is None else _finite(value, "task progress ratio")


class PublicMissionEvent(StrictModel):
    event_id: Identifier
    sequence: Annotated[int, Field(ge=0)]
    at: SimulationTime
    provider_id: Identifier
    entity_id: Identifier | None
    state: Identifier
    evidence: tuple[ArtifactReference, ...]

    @model_validator(mode="after")
    def canonical_evidence(self) -> "PublicMissionEvent":
        _sorted_unique(
            "mission event evidence",
            _artifact_reference_keys(self.evidence),
        )
        return self


class PublicSensorFrameReference(StrictModel):
    event_id: Identifier
    sequence: Annotated[int, Field(ge=0)]
    at: SimulationTime
    provider_id: Identifier
    observation_id: Identifier
    frame_id: Identifier
    payload_digest: Sha256
    artifact: ArtifactReference

    _digest = field_validator("payload_digest")(_real_digest)


class PublicRunEvent(StrictModel):
    run_id: Sha256
    event_id: Identifier
    event_digest: Sha256
    sequence: Annotated[int, Field(ge=0)]
    source_kind: RunEventSourceKind
    source: Identifier
    event_type: Identifier
    at: SimulationTime
    correlation_id: Identifier
    parent_event_id: Identifier | None
    causal_event_ids: tuple[Identifier, ...]
    agent_id: Identifier | None
    provider_id: Identifier | None
    vehicle_id: Identifier | None
    entity_id: Identifier | None
    command_id: Identifier | None
    query_id: Identifier | None
    observation_id: Identifier | None
    payload_schema_id: Identifier
    payload_digest: Sha256
    interaction_type: AgentInteractionType | None
    public_payload: tuple[StateAttribute, ...]

    @field_validator("run_id", "event_digest", "payload_digest")
    @classmethod
    def real_event_digests(cls, value: str) -> str:
        return _real_digest(value)

    @model_validator(mode="after")
    def safe_canonical_payload(self) -> "PublicRunEvent":
        if self.event_id != f"event.{self.sequence:016d}":
            raise ValueError("public event ID differs from its global sequence")
        _sorted_unique("public event causal ID", self.causal_event_ids)
        _sorted_unique(
            "public event payload",
            tuple(item.name for item in self.public_payload),
        )
        if {item.name for item in self.public_payload}.intersection(
            _FORBIDDEN_PUBLIC_PAYLOAD_NAMES
        ):
            raise ValueError("public event payload contains a private field")
        if self.parent_event_id is not None and self.parent_event_id not in set(
            self.causal_event_ids
        ):
            raise ValueError("public parent event must be present in causal IDs")
        if self.interaction_type in {"process.stdout.v1", "process.stderr.v1"}:
            raise ValueError("process interactions cannot enter Public Trace")
        provider_types = {
            "public.event",
            "public.network-link",
            "public.sensor-frame",
            "public.status",
            "public.traffic-light",
        }
        if self.source_kind == "provider":
            if self.provider_id != self.source:
                raise ValueError("public Provider identity is inconsistent")
            if self.event_type == "public.agent-interaction":
                if self.interaction_type == "agent.observation.v1":
                    if self.public_payload:
                        raise ValueError(
                            "public Provider observation mirror must have an empty payload"
                        )
                    return self
                if self.interaction_type != "agent.query_result.v1" or not {
                    item.name for item in self.public_payload
                }.issubset(PUBLIC_AGENT_INTERACTION_FIELDS[self.interaction_type]):
                    raise ValueError(
                        "public Provider Agent interaction exceeds its safe contract"
                    )
                return self
            if self.event_type not in provider_types:
                raise ValueError("Provider event is outside the public vocabulary")
            expected_interaction = {
                "public.event": "mission.event.v1",
                "public.network-link": "ns3.link_state.v1",
                "public.sensor-frame": "sensor.frame_ref.v2",
                "public.status": "mission.event.v1",
                "public.traffic-light": "sumo.traffic_light.v1",
            }[self.event_type]
            if self.interaction_type != expected_interaction:
                raise ValueError("public Provider event has the wrong interaction")
        elif self.interaction_type not in PUBLIC_AGENT_INTERACTION_FIELDS:
            raise ValueError("public non-Provider event has no safe contract")
        elif not {item.name for item in self.public_payload}.issubset(PUBLIC_AGENT_INTERACTION_FIELDS[self.interaction_type]):
            raise ValueError("public interaction payload exceeds its disclosure contract")
        return self


class PublicProviderStatus(StrictModel):
    provider_id: Identifier
    state: ProviderStateKind
    version: Annotated[str, Field(min_length=1, max_length=128)]
    implementation_kind: Literal["mechanical_fixture", "production"] | None


class PublicMetricResult(StrictModel):
    metric_id: Identifier
    value: float
    unit: Annotated[str, Field(min_length=1, max_length=32)] | None
    evidence: tuple[ArtifactReference, ...] = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        return _finite(value, "verification metric")

    @field_validator("unit")
    @classmethod
    def unit_grammar(cls, value: str | None) -> str | None:
        if value is not None and _UNIT_PATTERN.fullmatch(value) is None:
            raise ValueError("verification metric unit is invalid")
        return value

    @model_validator(mode="after")
    def canonical_evidence(self) -> "PublicMetricResult":
        _sorted_unique(
            "verification metric evidence",
            _artifact_reference_keys(self.evidence),
        )
        return self


class PublicGoalResult(StrictModel):
    goal_id: Identifier
    passed: bool
    metrics: tuple[PublicMetricResult, ...]
    failure_class: Identifier | None

    @model_validator(mode="after")
    def result_is_consistent(self) -> "PublicGoalResult":
        _sorted_unique("goal metric", tuple(metric.metric_id for metric in self.metrics))
        if self.passed and (not self.metrics or self.failure_class is not None):
            raise ValueError("passed goal requires metrics and no failure class")
        return self


class PublicVerificationReport(StrictModel):
    schema_version: Literal["aero-bench.verification-public/v3"]
    run_id: Sha256
    status: Literal["passed", "failed", "invalid"]
    goals: tuple[PublicGoalResult, ...]
    coverage_complete: bool

    @model_validator(mode="after")
    def result_is_consistent(self) -> "PublicVerificationReport":
        _sorted_unique("verification goal", tuple(goal.goal_id for goal in self.goals))
        metric_ids = tuple(
            metric.metric_id for goal in self.goals for metric in goal.metrics
        )
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("verification metric IDs must be globally unique")
        all_passed = bool(self.goals) and all(goal.passed for goal in self.goals)
        if self.status == "passed" and (not self.coverage_complete or not all_passed):
            raise ValueError("passed verification requires complete passing coverage")
        return self


class PublicTerminal(StrictModel):
    kind: TerminalKind
    event_id: Identifier
    at: SimulationTime
    failure_class: Identifier | None
    provider_failure_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def terminal_fields_match(self) -> "PublicTerminal":
        _sorted_unique("terminal Provider failure", self.provider_failure_ids)
        if self.kind == "completed" and (
            self.failure_class is not None or self.provider_failure_ids
        ):
            raise ValueError("completed terminal cannot carry abort fields")
        if self.kind == "aborted" and self.failure_class is None:
            raise ValueError("aborted terminal requires a failure class")
        return self


class PublicTrace(StrictModel):
    schema_version: Literal["aero-bench.public-trace/v3"]
    projector_version: Literal["aero-bench.public-projector/v2"]
    run_id: Sha256
    suite_id: Identifier
    case_id: Identifier
    execution_scope: ExecutionScope
    phase: TracePhase
    time: SimulationTime
    event_chain_root: Sha256
    scenario_digest: Sha256
    scenario: PublicScenario
    scene_state_history_artifact: ArtifactReference
    runtime_artifacts: tuple[PublicRuntimeArtifact, ...]
    scene_states: tuple[SceneState, ...]
    trajectories: tuple[PublicTrajectory, ...]
    network_frames: tuple[PublicNetworkFrame, ...]
    traffic_light_frames: tuple[PublicTrafficLightFrame, ...] = ()
    network_events: tuple[PublicNetworkEvent, ...]
    mission_status_history: tuple[PublicMissionStatus, ...]
    mission_status: PublicMissionStatus | None
    mission_events: tuple[PublicMissionEvent, ...]
    sensor_frames: tuple[PublicSensorFrameReference, ...]
    events: tuple[PublicRunEvent, ...]
    provider_status: tuple[PublicProviderStatus, ...]
    terminal: PublicTerminal
    verifier_public: PublicVerificationReport | None

    @field_validator("run_id", "event_chain_root", "scenario_digest")
    @classmethod
    def real_digests(cls, value: str) -> str:
        return _real_digest(value)

    @model_validator(mode="after")
    def exact_replay_is_closed(self) -> "PublicTrace":
        if self.scenario.scenario_digest != self.scenario_digest:
            raise ValueError("public scenario digest differs from trace identity")
        if self.terminal.at != self.time:
            raise ValueError("trace time must equal terminal time")
        if self.phase == "aborted":
            if self.terminal.kind != "aborted" or self.verifier_public is not None:
                raise ValueError("aborted trace terminal or verifier state is invalid")
        elif self.terminal.kind != "completed":
            raise ValueError("non-aborted trace requires a completed terminal")
        if (self.phase == "verified") != (self.verifier_public is not None):
            raise ValueError("verified trace and verifier report disagree")
        if self.verifier_public is not None:
            if (
                self.execution_scope != "formal_benchmark"
                or self.verifier_public.run_id != self.run_id
            ):
                raise ValueError("public verifier report is not bound to the run")

        runtime_artifact_ids = tuple(
            artifact.artifact_id for artifact in self.runtime_artifacts
        )
        _sorted_unique("runtime artifact", runtime_artifact_ids)
        runtime_artifact_selectors = tuple(
            artifact.selector for artifact in self.runtime_artifacts
        )
        if len(runtime_artifact_selectors) != len(set(runtime_artifact_selectors)):
            raise ValueError("runtime artifact selectors must be unique")
        history_artifacts = tuple(
            artifact
            for artifact in self.runtime_artifacts
            if artifact.artifact_type == "scene.state-history"
        )
        history_artifact = next(
            (
                artifact
                for artifact in self.runtime_artifacts
                if artifact.artifact_id
                == self.scene_state_history_artifact.artifact_id
            ),
            None,
        )
        if (
            len(history_artifacts) != 1
            or history_artifact is None
            or history_artifacts[0] != history_artifact
            or history_artifact.sha256 != self.scene_state_history_artifact.digest
            or history_artifact.selector != self.scene_state_history_artifact.selector
        ):
            raise ValueError("SceneState history reference is absent from replay artifacts")

        aborted_before_motion = self.terminal.kind == "aborted" and not self.scene_states
        # scene_state_jsonl_bytes validates the complete history before encoding it.
        history_payload = scene_state_jsonl_bytes(
            self.scene_states,
            aborted_before_first_motion=aborted_before_motion,
        )
        if (
            hashlib.sha256(history_payload).hexdigest()
            != self.scene_state_history_artifact.digest
            or len(history_payload) != history_artifact.size_bytes
        ):
            raise ValueError("embedded SceneStates differ from their sealed artifact")
        scenario_entity_ids = tuple(
            entity.entity_id for entity in self.scenario.entities
        )
        for state in self.scene_states:
            if state.run_id != self.run_id or state.scenario_digest != self.scenario_digest:
                raise ValueError("SceneState belongs to another run or scenario")
            if state.declared_entity_ids != scenario_entity_ids:
                raise ValueError(
                    "SceneState entity inventory differs from the public scenario"
                )
        if self.scene_states:
            if self.scene_states[-1].at != self.time:
                raise ValueError("final SceneState must equal terminal simulation time")
        elif self.time != SimulationTime(tick=0, sim_time_ns=0):
            raise ValueError("an empty SceneState replay must terminate at time zero")

        expected_trajectories: dict[str, list[PublicTrajectorySample]] = {}
        for state in self.scene_states:
            for sample in state.samples:
                if sample.sample_kind == "dynamic":
                    expected_trajectories.setdefault(sample.entity_id, []).append(
                        PublicTrajectorySample(
                            at=state.at,
                            pose=sample.pose,
                            sample_digest=sample.sample_digest,
                        )
                    )
        actual_trajectories = {trajectory.entity_id: trajectory for trajectory in self.trajectories}
        if tuple(actual_trajectories) != tuple(sorted(actual_trajectories)):
            raise ValueError("trajectory IDs must be sorted and unique")
        if set(actual_trajectories) != set(expected_trajectories):
            raise ValueError("trajectories do not close over dynamic SceneState entities")
        for entity_id, expected_samples in expected_trajectories.items():
            if actual_trajectories[entity_id].samples != tuple(expected_samples):
                raise ValueError("trajectory differs from canonical SceneState samples")

        if len(self.network_frames) != (
            len(self.scene_states) if self.scenario.network is not None else 0
        ):
            raise ValueError("network geometry does not cover the SceneState replay")
        if self.scenario.network is not None:
            for state, frame in zip(
                self.scene_states,
                self.network_frames,
                strict=True,
            ):
                by_entity = {sample.entity_id: sample for sample in state.samples}
                expected_nodes = tuple(
                    PublicNetworkNodeState(
                        node_id=binding.node_id,
                        entity_id=binding.entity_id,
                        endpoint_id=binding.endpoint_id,
                        pose=by_entity[binding.entity_id].pose,
                    )
                    for binding in self.scenario.network.node_bindings
                )
                node_poses = {node.node_id: node.pose for node in expected_nodes}
                expected_links = tuple(
                    PublicNetworkLinkGeometry(
                        link_id=link.link_id,
                        source_node_id=link.source_node_id,
                        destination_node_id=link.destination_node_id,
                        source_pose=node_poses[link.source_node_id],
                        destination_pose=node_poses[link.destination_node_id],
                    )
                    for link in self.scenario.network.links
                )
                if (
                    frame.at != state.at
                    or frame.scene_state_digest != state.scene_state_digest
                    or frame.nodes != expected_nodes
                    or frame.links != expected_links
                ):
                    raise ValueError(
                        "network geometry differs from its canonical SceneState"
                    )

        for label, sequences in (
            ("network event", tuple(item.sequence for item in self.network_events)),
            ("mission status", tuple(item.sequence for item in self.mission_status_history)),
            ("mission event", tuple(item.sequence for item in self.mission_events)),
            ("sensor frame", tuple(item.sequence for item in self.sensor_frames)),
            ("traffic-light frame", tuple(item.sequence for item in self.traffic_light_frames)),
            ("public event", tuple(item.sequence for item in self.events)),
        ):
            if sequences != tuple(sorted(set(sequences))):
                raise ValueError(f"{label} sequences must be sorted and unique")
        runtime_by_id = {
            artifact.artifact_id: artifact for artifact in self.runtime_artifacts
        }

        def validate_references(
            references: tuple[ArtifactReference, ...],
        ) -> None:
            for reference in references:
                artifact = runtime_by_id.get(reference.artifact_id)
                if artifact is None or artifact.sha256 != reference.digest:
                    raise ValueError(
                        "public evidence reference is absent from runtime artifacts"
                    )

        public_events_by_id = {event.event_id: event for event in self.events}
        if len(public_events_by_id) != len(self.events):
            raise ValueError("public event IDs must be unique")

        def bind_projection(
            *,
            event_id: str,
            sequence: int,
            at: SimulationTime,
            event_type: str,
        ) -> PublicRunEvent:
            event = public_events_by_id.get(event_id)
            if (
                event is None
                or event.sequence != sequence
                or event.at != at
                or event.event_type != event_type
            ):
                raise ValueError(
                    "specialized public projection differs from its RunEvent"
                )
            return event

        state_by_tick = {state.at.tick: state for state in self.scene_states}
        for network_event in self.network_events:
            event = bind_projection(
                event_id=network_event.event_id,
                sequence=network_event.sequence,
                at=network_event.at,
                event_type="public.network-link",
            )
            if (
                self.scenario.network is None
                or event.source != network_event.provider_id
                or network_event.provider_id != self.scenario.network.provider_id
            ):
                raise ValueError("network event has no public network authority")
            state = state_by_tick.get(network_event.at.tick)
            if state is None or state.at != network_event.at:
                raise ValueError("network event has no exact SceneState")
            by_entity = {sample.entity_id: sample for sample in state.samples}
            source = by_entity.get(network_event.source_entity_id)
            target = by_entity.get(network_event.target_entity_id)
            if (
                source is None
                or target is None
                or source.pose != network_event.source_pose
                or target.pose != network_event.target_pose
            ):
                raise ValueError("network event geometry differs from SceneState")
            validate_references(network_event.evidence)
        for status in self.mission_status_history:
            event = bind_projection(
                event_id=status.event_id,
                sequence=status.sequence,
                at=status.at,
                event_type="public.status",
            )
            if event.source != status.provider_id:
                raise ValueError("mission status Provider differs from its RunEvent")
        for frame in self.traffic_light_frames:
            event = bind_projection(
                event_id=frame.event_id,
                sequence=frame.sequence,
                at=frame.at,
                event_type="public.traffic-light",
            )
            if (
                self.scenario.sumo is None
                or event.source != frame.provider_id
                or frame.provider_id != self.scenario.sumo.provider_id
            ):
                raise ValueError("traffic-light frame has no SUMO authority")
        for mission_event in self.mission_events:
            event = bind_projection(
                event_id=mission_event.event_id,
                sequence=mission_event.sequence,
                at=mission_event.at,
                event_type="public.event",
            )
            if event.source != mission_event.provider_id:
                raise ValueError("mission event Provider differs from its RunEvent")
            validate_references(mission_event.evidence)
        for frame in self.sensor_frames:
            event = bind_projection(
                event_id=frame.event_id,
                sequence=frame.sequence,
                at=frame.at,
                event_type="public.sensor-frame",
            )
            if (
                event.source != frame.provider_id
                or event.observation_id != frame.observation_id
            ):
                raise ValueError("sensor frame identity differs from its RunEvent")
            validate_references((frame.artifact,))
        if self.verifier_public is not None:
            for goal in self.verifier_public.goals:
                for metric in goal.metrics:
                    validate_references(metric.evidence)

        specialized_event_ids = {
            "public.event": tuple(item.event_id for item in self.mission_events),
            "public.network-link": tuple(
                item.event_id for item in self.network_events
            ),
            "public.sensor-frame": tuple(item.event_id for item in self.sensor_frames),
            "public.status": tuple(
                item.event_id for item in self.mission_status_history
            ),
            "public.traffic-light": tuple(
                item.event_id for item in self.traffic_light_frames
            ),
        }
        for event_type, projected_ids in specialized_event_ids.items():
            expected_ids = tuple(
                event.event_id
                for event in self.events
                if event.event_type == event_type
            )
            if projected_ids != expected_ids:
                raise ValueError(
                    "specialized public projection does not close over its RunEvents"
                )

        expected_status = (
            self.mission_status_history[-1] if self.mission_status_history else None
        )
        if self.mission_status != expected_status:
            raise ValueError("latest mission status differs from mission status history")
        _sorted_unique(
            "Provider status",
            tuple(status.provider_id for status in self.provider_status),
        )
        public_event_ids = {event.event_id for event in self.events}
        for event in self.events:
            if event.run_id != self.run_id:
                raise ValueError("public event belongs to another run")
            if event.parent_event_id is not None and event.parent_event_id not in public_event_ids:
                raise ValueError("public event parent is absent from public projection")
            if set(event.causal_event_ids) - public_event_ids:
                raise ValueError("public event causal reference is absent")
            if any(
                public_events_by_id[event_id].sequence >= event.sequence
                for event_id in event.causal_event_ids
            ):
                raise ValueError("public event causality must point backward")
        return self


__all__ = [
    "ArtifactReference",
    "PUBLIC_PROJECTOR_VERSION",
    "PUBLIC_TRACE_SCHEMA_VERSION",
    "PUBLIC_VERIFICATION_SCHEMA_VERSION",
    "ProviderStateKind",
    "PublicBuilding",
    "PublicEntityDefinition",
    "PublicGoalResult",
    "PublicMetricResult",
    "PublicMissionEvent",
    "PublicMissionStatus",
    "PublicNetworkEvent",
    "PublicNetworkFrame",
    "PublicNetworkLinkGeometry",
    "PublicNetworkNodeState",
    "PublicTrafficLightFrame",
    "PublicTrafficLightState",
    "PublicProviderStatus",
    "PublicReplayFile",
    "PublicReplayIndex",
    "PublicReplayManifest",
    "PublicReplayShard",
    "PublicRunEvent",
    "PublicRuntimeArtifact",
    "PublicScenario",
    "PublicScenarioAsset",
    "PublicSensorFrameReference",
    "PublicSumoBinding",
    "PublicTerminal",
    "PublicTrace",
    "PublicTrajectory",
    "PublicTrajectorySample",
    "PublicVerificationReport",
    "TerminalKind",
    "TracePhase",
    "validate_public_selector",
]
