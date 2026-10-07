"""Resolution and readiness checks for the independent urban recovery demo."""

from __future__ import annotations

from collections.abc import Iterable

from aero_bench.config.loader import BundleReader, sha256_file
from aero_bench.config.models import (
    AgentSpec,
    EnvironmentSpec,
    FeasibilityAssessment,
    FileRef,
    NamedValue,
    TaskSpec,
)
from aero_bench.providers.ns3.config import Ns3Config, ns3_capabilities
from aero_bench.providers.ns3.protocol import MAILBOX_OBSERVATION_PREFIX
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.providers.sumo.config import SumoConfig
from aero_bench.tasks.urban_recovery_demo.contracts import (
    PACKAGE_ID,
    PHYSICS_STEP_NS,
    STEP_NS,
    DemoTaskPackage,
)
from aero_bench.tasks.urban_recovery_demo.goals import validate_urban_goals
from aero_bench.tasks.urban_recovery_demo.participant import UrbanParticipantConfig
from aero_bench.tasks.urban_recovery_demo.provenance import validate_urban_provenance
from aero_bench.world.contracts import WorldPackage
from aero_bench.world.resolved import (
    ResolvedScenario,
    ResolvedTaskScenarioProjection,
    ResolvedUrbanRecoveryObservationProjection,
)


URBAN_PROVIDER_ADAPTERS = {
    "flight": "px4.gazebo",
    "network": "ns3.rpc",
    "traffic": "sumo.traci",
}
URBAN_AGENT_IDS = (
    "groundstation.rule",
    "uav.policy.01",
    "uav.policy.02",
)
URBAN_REQUIRED_WORLD_LAYERS = frozenset({"osm_mesh", "osm_scene"})
URBAN_REQUIRED_CAPABILITIES = frozenset(
    {
        "flight.arm",
        "gazebo.frames",
        "gazebo.physics",
        "network.agent-mailbox",
        "network.delivery",
        "network.wifi-scene-mobility",
        "sumo.frames",
        "sumo.signals",
        "sumo.traffic",
    }
)
URBAN_REQUIRED_TOOLS = frozenset(
    {
        "flight.arm",
        "flight.disarm",
        "flight.goto",
        "flight.hold",
        "flight.land",
        "flight.takeoff",
        "network.send",
    }
)
URBAN_SHANGHAI_LATITUDE_DEG = 31.2304
URBAN_SHANGHAI_LONGITUDE_DEG = 121.4737


class UrbanRecoveryResolutionError(ValueError):
    """A declared urban recovery package cannot be resolved safely."""


def load_urban_recovery_package(
    *, reader: BundleReader, task: TaskSpec
) -> DemoTaskPackage:
    """Load the package config through its declared schema-bound file."""

    if task.package.package_id != PACKAGE_ID:
        raise UrbanRecoveryResolutionError(
            "urban recovery resolver received another task package"
        )
    reader.validate_schema_bound_file(task.package.config)
    try:
        package = DemoTaskPackage.model_validate(
            reader.load_document(task.package.config.file)
        )
    except (TypeError, ValueError) as error:
        raise UrbanRecoveryResolutionError(
            "urban recovery package config is invalid"
        ) from error
    return package


def _require_exact_ids(
    label: str, actual: Iterable[str], expected: Iterable[str]
) -> None:
    actual_set = set(actual)
    expected_set = set(expected)
    if actual_set != expected_set:
        raise UrbanRecoveryResolutionError(
            f"urban recovery {label} differ: "
            f"expected {sorted(expected_set)}, got {sorted(actual_set)}"
        )


def _provider_by_id(environment: EnvironmentSpec) -> dict[str, object]:
    return {provider.provider_id: provider for provider in environment.providers}


def _validate_provider_contracts(
    *, reader: BundleReader, package: DemoTaskPackage, environment: EnvironmentSpec
) -> None:
    providers = _provider_by_id(environment)
    _require_exact_ids(
        "physical providers",
        providers,
        (package.flight_provider_id, package.network_provider_id, package.traffic_provider_id),
    )
    for provider_id, adapter in URBAN_PROVIDER_ADAPTERS.items():
        declared_id = {
            "flight": package.flight_provider_id,
            "network": package.network_provider_id,
            "traffic": package.traffic_provider_id,
        }[provider_id]
        provider = providers.get(declared_id)
        if provider is None:
            raise UrbanRecoveryResolutionError(
                f"urban recovery provider is absent: {declared_id}"
            )
        if provider.adapter != adapter:
            raise UrbanRecoveryResolutionError(
                f"urban recovery provider {declared_id} must use adapter {adapter}"
            )
        reader.validate_schema_bound_file(provider.config)
        reader.validate_schema(provider.protocol_schema)

    flight = providers[package.flight_provider_id]
    network = providers[package.network_provider_id]
    traffic = providers[package.traffic_provider_id]
    trajectory_artifacts = tuple(
        requirement
        for requirement in flight.artifact_requirements
        if requirement.artifact_type == "trajectory"
    )
    if (
        len(trajectory_artifacts) != 1
        or trajectory_artifacts[0].visibility != "public"
        or trajectory_artifacts[0].producer_id != package.flight_provider_id
    ):
        raise UrbanRecoveryResolutionError(
            "urban recovery PX4 Provider must publish one public trajectory artifact"
        )
    scene_history_artifacts = tuple(
        requirement
        for requirement in environment.harness_artifact_requirements
        if requirement.artifact_type == "scene.state-history"
    )
    if (
        len(scene_history_artifacts) != 1
        or scene_history_artifacts[0].visibility != "public"
        or scene_history_artifacts[0].producer_id != "harness"
    ):
        raise UrbanRecoveryResolutionError(
            "urban recovery Harness must publish one public SceneState history artifact"
        )
    try:
        flight_config = Px4GazeboConfig.model_validate(
            reader.load_document(flight.config.file)
        )
        network_config = Ns3Config.model_validate(
            reader.load_document(network.config.file)
        )
        traffic_config = SumoConfig.model_validate(
            reader.load_document(traffic.config.file)
        )
    except (TypeError, ValueError) as error:
        raise UrbanRecoveryResolutionError(
            "urban recovery provider config does not match its adapter"
        ) from error

    if flight_config.provider_id != flight.provider_id:
        raise UrbanRecoveryResolutionError("PX4 config provider_id differs from ProviderRef")
    if flight_config.physics_step_ns != PHYSICS_STEP_NS:
        raise UrbanRecoveryResolutionError(
            "PX4 physics_step_ns must equal the urban recovery physics step"
        )
    if len(flight_config.vehicles) != 2:
        raise UrbanRecoveryResolutionError(
            "urban recovery PX4 config must declare exactly two vehicles"
        )
    if network_config.provider_id != network.provider_id:
        raise UrbanRecoveryResolutionError("ns-3 config provider_id differs from ProviderRef")
    expected_network_capabilities = set(
        ns3_capabilities(network_config.supported_wifi_standards)
    )
    if not expected_network_capabilities <= set(network.capabilities):
        raise UrbanRecoveryResolutionError(
            "ns-3 ProviderRef omits capabilities required by its config"
        )
    if traffic_config.provider_id != traffic.provider_id:
        raise UrbanRecoveryResolutionError("SUMO config provider_id differs from ProviderRef")
    if traffic_config.step_length_ns != STEP_NS:
        raise UrbanRecoveryResolutionError(
            "SUMO step_length_ns must equal the urban recovery logical step"
        )


def _validate_agent_grants(
    *, package: DemoTaskPackage, agents: tuple[AgentSpec, ...], environment: EnvironmentSpec
) -> None:
    _require_exact_ids("Agent identities", (agent.agent_id for agent in agents), URBAN_AGENT_IDS)
    provider_ids = {provider.provider_id for provider in environment.providers}
    if package.flight_provider_id not in provider_ids or package.network_provider_id not in provider_ids:
        raise UrbanRecoveryResolutionError("urban recovery Agent grants require physical flight and network Providers")

    roles = {role.agent_id: role for role in package.roles}
    for agent in agents:
        role = roles.get(agent.agent_id)
        if role is None:
            raise UrbanRecoveryResolutionError(
                f"package does not declare Agent role {agent.agent_id}"
            )
        if role.mailbox_observation_id != f"{MAILBOX_OBSERVATION_PREFIX}{role.endpoint_id}":
            raise UrbanRecoveryResolutionError("Agent mailbox identity is not endpoint-bound")
        expected_tools = {"network.send": package.network_provider_id}
        expected_observations = {role.mailbox_observation_id: package.network_provider_id}
        if role.role == "uav":
            expected_tools.update({
                tool_id: package.flight_provider_id
                for tool_id in URBAN_REQUIRED_TOOLS if tool_id != "network.send"
            })
            expected_observations.update({
                role.telemetry_observation_id: package.flight_provider_id,
                role.safety_observation_id: package.flight_provider_id,
            })
        actual_tools = {grant.tool_id: grant.provider_id for grant in agent.tools}
        actual_observations = {grant.observation_id: grant.provider_id for grant in agent.observations}
        if actual_tools != expected_tools or len(agent.tools) != len(expected_tools):
            raise UrbanRecoveryResolutionError(
                f"Agent {agent.agent_id} tool grants differ from its exact authorized role"
            )
        if actual_observations != expected_observations or len(agent.observations) != len(expected_observations):
            raise UrbanRecoveryResolutionError(
                f"Agent {agent.agent_id} observation grants differ from its exact authorized role"
            )
        if agent.artifact_requirements:
            raise UrbanRecoveryResolutionError("urban Agents must report through the Gateway, not artifact grants")


def _validate_participant_inputs(
    *, reader: BundleReader, task: TaskSpec, package: DemoTaskPackage, agents: tuple[AgentSpec, ...]
) -> None:
    """Bind policy bytes and least-privilege mounts to the resolved Run-ID."""
    assets = {asset.asset_id: asset for asset in task.assets}
    roles = {role.agent_id: role for role in package.roles}
    ground = next(role for role in package.roles if role.role == "groundstation")
    agent_asset_ids = {
        asset.asset_id for asset in task.assets
        if any(audience.role == "agent" for audience in asset.audiences)
    }
    expected_asset_ids = {f"asset.participant.{agent.agent_id}" for agent in agents}
    if agent_asset_ids != expected_asset_ids:
        raise UrbanRecoveryResolutionError("urban Agent inputs must contain exactly their declared public policy assets")
    for agent in agents:
        role = roles[agent.agent_id]
        asset_id = f"asset.participant.{agent.agent_id}"
        asset = assets[asset_id]
        audiences = {audience.role: tuple(audience.workload_ids) for audience in asset.audiences}
        if asset.classification != "public" or audiences != {
            "agent": (agent.agent_id,), "verifier": (package.verifier_id,),
        }:
            raise UrbanRecoveryResolutionError("participant config audience differs from its own Agent and verifier")
        if agent.workload.runtime.command != ("run", "--config-asset", asset_id):
            raise UrbanRecoveryResolutionError("participant command must use its authorized config asset")
        for grant in agent.tools:
            schema = reader.validate_schema(grant.request_schema)
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                raise UrbanRecoveryResolutionError("participant command schema must be a closed object")
            if grant.tool_id == "network.send":
                destinations = sorted(
                    item.endpoint_id for item in package.roles if item.role == "uav"
                ) if role.role == "groundstation" else [ground.endpoint_id]
                source = properties.get("source", {})
                destination = properties.get("destination", {})
                if (
                    not isinstance(source, dict) or source.get("const") != role.endpoint_id
                    or not isinstance(destination, dict) or destination.get("enum") != destinations
                    or not {"source", "destination"} <= set(required)
                ):
                    raise UrbanRecoveryResolutionError("network command schema does not restrict owned endpoints")
            else:
                vehicle = properties.get("vehicle_id", {})
                if not isinstance(vehicle, dict) or vehicle.get("const") != role.vehicle_id or "vehicle_id" not in required:
                    raise UrbanRecoveryResolutionError("flight command schema does not restrict the owned UAV")
        try:
            config = UrbanParticipantConfig.model_validate(reader.load_document(asset.file))
        except (OSError, TypeError, ValueError) as error:
            raise UrbanRecoveryResolutionError("participant config bytes or schema failed validation") from error
        for name in ("role", "endpoint_id", "vehicle_id", "telemetry_observation_id", "safety_observation_id", "mailbox_observation_id"):
            if getattr(config, name) != getattr(role, name):
                raise UrbanRecoveryResolutionError(f"participant config differs from its declared role: {name}")
        for name in ("execution_profile", "recovery_variant", "final_tick"):
            if getattr(config, name) != getattr(package, name):
                raise UrbanRecoveryResolutionError(f"participant config differs from package execution: {name}")
        for name in ("heartbeat_interval_ns", "injection_start_ns", "response_timeout_ns", "maximum_retries", "vehicle_radius_m", "obstacle_margin_m", "cruise_agl_m"):
            if getattr(config, name) != getattr(package.recovery, name):
                raise UrbanRecoveryResolutionError(f"participant config differs from package recovery policy: {name}")
        if (
            config.groundstation_endpoint_id != ground.endpoint_id
            or config.origin_latitude_deg != URBAN_SHANGHAI_LATITUDE_DEG
            or config.origin_longitude_deg != URBAN_SHANGHAI_LONGITUDE_DEG
            or config.origin_ellipsoid_height_m != 50.0
            or config.origin_amsl_m != 20.0
        ):
            raise UrbanRecoveryResolutionError("participant config differs from Shanghai origin or groundstation binding")
        if role.role == "groundstation" and (
            config.vehicle_endpoint_ids != {item.vehicle_id: item.endpoint_id for item in package.roles if item.role == "uav"}
            or config.vehicle_agent_ids != {item.vehicle_id: item.agent_id for item in package.roles if item.role == "uav"}
        ):
            raise UrbanRecoveryResolutionError("groundstation config differs from package vehicle/Agent/endpoint bindings")


def _validate_osm2world_mesh_manifest(
    *, reader: BundleReader, world: WorldPackage
) -> None:
    """Require a release-time official OSM2World mesh-pack manifest."""
    mesh_layers = [layer for layer in world.layers if layer.kind == "osm_mesh"]
    scene_layers = [layer for layer in world.layers if layer.kind == "osm_scene"]
    if len(mesh_layers) != 1 or len(scene_layers) != 1 or world.base_layers:
        raise UrbanRecoveryResolutionError(
            "urban recovery must expose only its OSM scene and official mesh layers"
        )
    assets = {asset.artifact.artifact_id: asset for asset in world.assets}
    mesh_asset = assets.get(mesh_layers[0].asset_id)
    scene_asset = assets.get(scene_layers[0].asset_id)
    if mesh_asset is None or scene_asset is None:
        raise UrbanRecoveryResolutionError("urban OSM layers reference missing assets")
    try:
        document = reader.load_document(
            FileRef(
                path=mesh_asset.artifact.selector.split("#", 1)[0],
                sha256=mesh_asset.artifact.sha256,
            )
        )
    except (OSError, TypeError, ValueError) as error:
        raise UrbanRecoveryResolutionError(
            "urban OSM2World mesh-pack manifest cannot be verified"
        ) from error
    if not isinstance(document, dict):
        raise UrbanRecoveryResolutionError("urban OSM2World mesh-pack manifest is not an object")
    required_keys = {
        "schema_version",
        "source",
        "generator",
        "projection",
        "extent",
        "original_mesh_count",
        "batches",
        "objects",
        "textures",
    }
    if set(document) != required_keys or document["schema_version"] != (
        "aero-bench.osm2world-mesh-pack/v1"
    ):
        raise UrbanRecoveryResolutionError(
            "urban production geometry must use the official OSM2World mesh-pack schema"
        )
    source = document["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"sha256", "size_bytes"}
        or source.get("sha256") != scene_asset.artifact.sha256
        or source.get("size_bytes") != scene_asset.byte_size
    ):
        raise UrbanRecoveryResolutionError(
            "OSM2World mesh-pack source is not bound to the authoritative OSM scene"
        )
    generator = document["generator"]
    if (
        not isinstance(generator, dict)
        or set(generator) != {"runtime_sha256", "patch_sha256", "config_sha256", "revision"}
        or any(
            not isinstance(generator.get(name), str)
            or len(generator[name]) != 64
            or generator[name] == "0" * 64
            for name in ("runtime_sha256", "patch_sha256", "config_sha256")
        )
        or not isinstance(generator.get("revision"), str)
        or len(generator["revision"]) != 40
        or any(character not in "0123456789abcdef" for character in generator["revision"])
    ):
        raise UrbanRecoveryResolutionError(
            "OSM2World mesh-pack generator identity is incomplete or unpinned"
        )
    projection = document["projection"]
    origin = projection.get("origin") if isinstance(projection, dict) else None
    if (
        not isinstance(projection, dict)
        or set(projection) != {"name", "axes", "origin"}
        or projection.get("name") != "MetricMapProjection"
        or projection.get("axes") != "east-up-south"
        or not isinstance(origin, dict)
        or set(origin) != {"latitude_deg", "longitude_deg"}
        or origin.get("latitude_deg") != URBAN_SHANGHAI_LATITUDE_DEG
        or origin.get("longitude_deg") != URBAN_SHANGHAI_LONGITUDE_DEG
    ):
        raise UrbanRecoveryResolutionError(
            "OSM2World mesh-pack projection is not the declared Shanghai ENU projection"
        )
    if (
        not isinstance(document["batches"], list)
        or not document["batches"]
        or not isinstance(document["objects"], list)
        or not document["objects"]
        or not isinstance(document["textures"], dict)
    ):
        raise UrbanRecoveryResolutionError(
            "OSM2World mesh-pack geometry inventory is empty or malformed"
        )


def _validate_world_authority(
    *, reader: BundleReader, package: DemoTaskPackage, task: TaskSpec, scenario: ResolvedScenario
) -> None:
    try:
        world = WorldPackage.model_validate(
            reader.load_document(scenario.source_world_package)
        )
    except (TypeError, ValueError) as error:
        raise UrbanRecoveryResolutionError(
            "urban recovery source WorldPackage is invalid"
        ) from error
    if world.world_digest != scenario.world_digest or world.asset_digest != scenario.source_asset_digest:
        raise UrbanRecoveryResolutionError(
            "urban recovery WorldPackage digest differs from ResolvedScenario"
        )
    bounds = scenario.frame_authority.spatial_extent
    if (
        bounds.min_east_m != -500.0
        or bounds.max_east_m != 500.0
        or bounds.min_north_m != -500.0
        or bounds.max_north_m != 500.0
    ):
        raise UrbanRecoveryResolutionError(
            "urban recovery scene must cover exactly 1 km x 1 km in ENU"
        )
    layer_kinds = {layer.kind for layer in world.layers}
    if layer_kinds != URBAN_REQUIRED_WORLD_LAYERS:
        raise UrbanRecoveryResolutionError(
            "urban recovery production scene requires exactly osm_scene and osm_mesh layers"
        )
    _validate_osm2world_mesh_manifest(reader=reader, world=world)
    if package.execution_profile == "formal":
        try:
            validate_urban_provenance(reader=reader, world=world, task=task)
        except (OSError, TypeError, ValueError) as error:
            raise UrbanRecoveryResolutionError("urban source provenance bytes or bindings are incomplete") from error
    osm_sources = [asset for asset in world.assets if asset.asset_role == "osm_source"]
    if len(osm_sources) != 1:
        raise UrbanRecoveryResolutionError(
            "urban recovery WorldPackage must declare exactly one raw OSM source asset"
        )
    source_asset = osm_sources[0]
    source_path = source_asset.artifact.selector.split("#", 1)[0]
    try:
        source_file = reader.resolve_file(
            FileRef(path=source_path, sha256=source_asset.artifact.sha256)
        )
    except (OSError, TypeError, ValueError) as error:
        raise UrbanRecoveryResolutionError(
            "urban recovery raw OSM source asset cannot be verified"
        ) from error
    if sha256_file(source_file) != package.scene_source_sha256:
        raise UrbanRecoveryResolutionError(
            "urban recovery scene_source_sha256 is not bound to the raw OSM source asset"
        )
    if source_asset.media_type != "application/json":
        raise UrbanRecoveryResolutionError(
            "urban recovery raw OSM source must use application/json"
        )
    if world.frame.origin.latitude_deg != URBAN_SHANGHAI_LATITUDE_DEG or world.frame.origin.longitude_deg != URBAN_SHANGHAI_LONGITUDE_DEG:
        raise UrbanRecoveryResolutionError(
            "urban recovery scene origin must be the declared Shanghai reference"
        )
    if package.scene_source_sha256 == "0" * 64:
        raise UrbanRecoveryResolutionError("urban recovery scene source digest is a placeholder")


def _validate_scenario_bindings(
    *, package: DemoTaskPackage, scenario: ResolvedScenario
) -> None:
    provider_ids = {provider.provider_id for provider in scenario.providers}
    _require_exact_ids(
        "resolved Providers",
        provider_ids,
        (package.flight_provider_id, package.network_provider_id, package.traffic_provider_id),
    )
    if scenario.sumo is None or scenario.sumo.provider_id != package.traffic_provider_id:
        raise UrbanRecoveryResolutionError("urban recovery requires resolved SUMO authority")
    if scenario.network is None or scenario.network.provider_id != package.network_provider_id:
        raise UrbanRecoveryResolutionError("urban recovery requires resolved ns-3 authority")

    roles_by_vehicle = {
        role.vehicle_id: role for role in package.roles if role.role == "uav"
    }
    entity_map = {entity.entity_id: entity for entity in scenario.entities}
    for vehicle_id, role in roles_by_vehicle.items():
        entity = entity_map.get(vehicle_id)
        if entity is None or entity.kind != "uav" or entity.state != "dynamic" or entity.owner_id != package.flight_provider_id:
            raise UrbanRecoveryResolutionError(
                f"resolved scenario does not authorize UAV entity {vehicle_id}"
            )
    if not any(region.region_id == package.recovery.incident_region_id and region.kind == "no_fly" for region in scenario.regions):
        raise UrbanRecoveryResolutionError("recovery policy must reference a resolved no-fly region")

    endpoint_ids = {binding.endpoint_id for binding in scenario.network.node_bindings}
    role_endpoints = {role.endpoint_id for role in package.roles}
    if not role_endpoints <= endpoint_ids:
        raise UrbanRecoveryResolutionError("all Agent role endpoints require resolved ns-3 node bindings")


def _validate_task_requirements(
    *, task: TaskSpec, agents: tuple[AgentSpec, ...]
) -> None:
    try:
        validate_urban_goals(task)
    except ValueError as error:
        raise UrbanRecoveryResolutionError(str(error)) from error
    missing_capabilities = URBAN_REQUIRED_CAPABILITIES - set(task.required_capabilities)
    if missing_capabilities:
        raise UrbanRecoveryResolutionError(
            "urban recovery TaskSpec omits required capabilities: "
            f"{sorted(missing_capabilities)}"
        )
    missing_tools = URBAN_REQUIRED_TOOLS - set(task.required_tools)
    if missing_tools:
        raise UrbanRecoveryResolutionError(
            "urban recovery TaskSpec omits required tools: "
            f"{sorted(missing_tools)}"
        )
    granted_tools = {grant.tool_id for agent in agents for grant in agent.tools}
    missing_granted_tools = URBAN_REQUIRED_TOOLS - granted_tools
    if missing_granted_tools:
        raise UrbanRecoveryResolutionError(
            "urban recovery Agents omit required tools: "
            f"{sorted(missing_granted_tools)}"
        )


def _validate_package_identity(
    *, package: DemoTaskPackage, task: TaskSpec, scenario: ResolvedScenario
) -> None:
    if package.task_id != task.task_id or package.verifier_id != task.verifier.verifier_id:
        raise UrbanRecoveryResolutionError(
            "urban recovery package identity differs from TaskSpec"
        )
    if package.package_id != PACKAGE_ID or task.package.package_id != PACKAGE_ID:
        raise UrbanRecoveryResolutionError("urban recovery package_id is not authoritative")
    if scenario.task.package_id != PACKAGE_ID or scenario.task.task_id != task.task_id:
        raise UrbanRecoveryResolutionError(
            "ResolvedScenario task binding differs from urban recovery package"
        )
    if scenario.task.logical_endpoint_ids:
        raise UrbanRecoveryResolutionError(
            "urban recovery roles must use physical Provider endpoints only"
        )


def _feasibility_assessment(
    *, package: DemoTaskPackage, environment: EnvironmentSpec
) -> FeasibilityAssessment:
    if environment.clock.authority != "provider_barrier":
        raise UrbanRecoveryResolutionError(
            "urban recovery requires provider_barrier clock authority"
        )
    if (
        environment.clock.step_ns != package.step_ns
        or environment.clock.max_steps != package.final_tick
    ):
        raise UrbanRecoveryResolutionError(
            "urban recovery clock differs from its declared horizon"
        )
    return FeasibilityAssessment(
        package_id=PACKAGE_ID,
        feasible=True,
        success_upper_bound=1.0,
        failed_conditions=(),
        bounds=(
            NamedValue(name="duration.ns", value=package.duration_ns),
            NamedValue(name="execution.profile", value=package.execution_profile),
            NamedValue(name="recovery.variant", value=package.recovery_variant),
            NamedValue(name="logical.step.ns", value=STEP_NS),
            NamedValue(name="physics.step.ns", value=PHYSICS_STEP_NS),
            NamedValue(name="logical.final-tick", value=package.final_tick),
            NamedValue(name="uav.count", value=2),
        ),
    )


class UrbanRecoveryTaskPackageResolver:
    """Resolve one strict, independent urban recovery task package."""

    @property
    def package_id(self) -> str:
        return PACKAGE_ID

    def scenario_projection(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
    ) -> ResolvedTaskScenarioProjection:
        package = load_urban_recovery_package(reader=reader, task=task)
        if package.task_id != task.task_id or package.verifier_id != task.verifier.verifier_id:
            raise UrbanRecoveryResolutionError("urban recovery package identity differs from TaskSpec")
        _validate_task_requirements(task=task, agents=agents)
        _validate_provider_contracts(reader=reader, package=package, environment=environment)
        _validate_agent_grants(package=package, agents=agents, environment=environment)
        _validate_participant_inputs(reader=reader, task=task, package=package, agents=agents)
        # Urban semantics are projected as typed ownership records. The common
        # scenario compiler never receives package-private policy or truth.
        urban_observations = tuple(
            sorted(
                (
                    ResolvedUrbanRecoveryObservationProjection(
                        projection_kind="urban_recovery",
                        observation_id=observation_id,
                        observation_kind=observation_kind,
                        vehicle_id=(
                            role.vehicle_id
                            if observation_kind in {"telemetry", "safety"}
                            else None
                        ),
                        camera_id=None,
                    )
                    for role in package.roles
                    for observation_kind, observation_id in (
                        ("telemetry", role.telemetry_observation_id),
                        ("safety", role.safety_observation_id),
                        ("mailbox", role.mailbox_observation_id),
                    )
                    if observation_id is not None
                ),
                key=lambda projection: projection.observation_id,
            )
        )
        return ResolvedTaskScenarioProjection(
            logical_endpoint_ids=(),
            logical_capability_ids=(),
            observations=(),
            urban_observations=urban_observations,
        )

    def resolve_runtime(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
        scenario: ResolvedScenario,
    ) -> FeasibilityAssessment:
        """Recompute runtime feasibility from inputs authorized to the Harness.

        Full source resolution additionally verifies participant policy bytes and
        source-world/provenance assets. Those role-private inputs are deliberately
        absent from the Harness bundle and remain validated by the executor before
        it creates the canonical plan.
        """

        package = load_urban_recovery_package(reader=reader, task=task)
        _validate_package_identity(package=package, task=task, scenario=scenario)
        _validate_task_requirements(task=task, agents=agents)
        _validate_provider_contracts(
            reader=reader, package=package, environment=environment
        )
        _validate_agent_grants(
            package=package, agents=agents, environment=environment
        )
        _validate_scenario_bindings(package=package, scenario=scenario)
        return _feasibility_assessment(package=package, environment=environment)

    def resolve(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
        scenario: ResolvedScenario,
    ) -> FeasibilityAssessment:
        package = load_urban_recovery_package(reader=reader, task=task)
        _validate_package_identity(package=package, task=task, scenario=scenario)
        _validate_task_requirements(task=task, agents=agents)
        _validate_provider_contracts(reader=reader, package=package, environment=environment)
        _validate_agent_grants(package=package, agents=agents, environment=environment)
        _validate_participant_inputs(reader=reader, task=task, package=package, agents=agents)
        _validate_world_authority(reader=reader, package=package, task=task, scenario=scenario)
        _validate_scenario_bindings(package=package, scenario=scenario)
        return _feasibility_assessment(package=package, environment=environment)


__all__ = [
    "PACKAGE_ID",
    "UrbanRecoveryResolutionError",
    "UrbanRecoveryTaskPackageResolver",
    "load_urban_recovery_package",
]
