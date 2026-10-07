from __future__ import annotations

from pathlib import Path

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentSpec,
    EnvironmentSpec,
    FeasibilityAssessment,
    NamedValue,
    ObservationGrant,
    TaskSpec,
)
from aero_bench.providers.ns3.protocol import MAILBOX_OBSERVATION_PREFIX
from aero_bench.tasks.inspection.bounds import (
    calculate_theoretical_bounds,
    resolve_inspection_bounds,
)
from aero_bench.tasks.inspection.contracts import (
    InspectionBoundResolution,
    InspectionTaskPackage,
)
from aero_bench.world.resolved import (
    ResolvedFlightObservationProjection,
    ResolvedInspectionObservationProjection,
    ResolvedScenario,
    ResolvedTaskScenarioProjection,
)


INSPECTION_PACKAGE_ID = "inspection.v1"


def _network_provider_ids(environment: EnvironmentSpec) -> frozenset[str]:
    provider_ids = frozenset(
        provider.provider_id
        for provider in environment.providers
        if provider.adapter == "ns3.rpc"
    )
    if len(provider_ids) > 1:
        raise ValueError("Inspection supports at most one ns-3 network Provider")
    return provider_ids


def _network_mailbox_grants(
    *,
    environment: EnvironmentSpec,
    agents: tuple[AgentSpec, ...],
) -> tuple[tuple[str, ObservationGrant], ...]:
    network_provider_ids = _network_provider_ids(environment)
    mailbox_grants: list[tuple[str, ObservationGrant]] = []
    mailbox_endpoints: set[str] = set()
    for agent in agents:
        for grant in agent.observations:
            reserved_name = grant.observation_id.startswith(
                MAILBOX_OBSERVATION_PREFIX
            )
            targets_network = grant.provider_id in network_provider_ids
            if reserved_name != targets_network:
                raise ValueError(
                    "network mailbox observations must target the sole ns-3 Provider"
                )
            if not targets_network:
                continue
            endpoint_id = grant.observation_id[len(MAILBOX_OBSERVATION_PREFIX) :]
            if not endpoint_id or endpoint_id in mailbox_endpoints:
                raise ValueError(
                    "network mailbox endpoint grants must be non-empty and unique"
                )
            mailbox_endpoints.add(endpoint_id)
            mailbox_grants.append((agent.agent_id, grant))
    return tuple(mailbox_grants)


def load_inspection_package(
    *,
    reader: BundleReader,
    task: TaskSpec,
) -> InspectionTaskPackage:
    if task.package.package_id != INSPECTION_PACKAGE_ID:
        raise ValueError("Inspection resolver received another task package")
    reader.validate_schema_bound_file(task.package.config)
    return InspectionTaskPackage.model_validate(
        reader.load_document(task.package.config.file)
    )


def resolve_inspection_business_endpoint(
    *,
    package: InspectionTaskPackage,
    task: TaskSpec,
    environment: EnvironmentSpec,
    agents: tuple[AgentSpec, ...],
) -> tuple[str, bool]:
    """Resolve the single authoritative Business endpoint and its ownership mode.

    An external Business actor must resolve to a physical Provider with the
    work-order capability. Without one, the explicit ``business.work-order``
    ToolGrant identifies the sole task-local endpoint; it may supply exactly
    the capability not implemented by physical Providers.
    """

    provider_by_id = {
        provider.provider_id: provider for provider in environment.providers
    }
    provider_ids = set(provider_by_id)
    physical_capability_ids = {
        capability
        for provider in environment.providers
        for capability in provider.capabilities
    }
    missing_physical_capabilities = (
        set(task.required_capabilities) - physical_capability_ids
    )
    business_actors = tuple(
        actor.actor_id for actor in package.actors if actor.role == "business"
    )
    business_grant_endpoint_ids = {
        grant.provider_id
        for agent in agents
        for grant in agent.tools
        if grant.tool_id.startswith("business.")
    }
    business_query_endpoint_ids = {
        grant.provider_id
        for agent in agents
        for grant in agent.queries
        if grant.query_type.startswith("business.")
    }
    work_order_grant_endpoint_ids = {
        grant.provider_id
        for agent in agents
        for grant in agent.tools
        if grant.tool_id == "business.work-order"
    }

    if business_actors:
        if len(business_actors) != 1:
            raise ValueError("Inspection package may declare at most one business actor")
        endpoint_id = business_actors[0]
        provider = provider_by_id.get(endpoint_id)
        if provider is None:
            raise ValueError(
                "Inspection external Business actor must name a physical Provider"
            )
        if "business.work-order" not in provider.capabilities:
            raise ValueError(
                "Inspection external Business Provider lacks business.work-order"
            )
        if (
            business_grant_endpoint_ids | business_query_endpoint_ids
        ) - {endpoint_id}:
            raise ValueError(
                "Inspection Business grants must target the external Business Provider"
            )
        if missing_physical_capabilities:
            raise ValueError(
                "Inspection external Business requires every task capability from "
                "physical Providers"
            )
        return endpoint_id, False

    if len(work_order_grant_endpoint_ids) != 1:
        raise ValueError(
            "Inspection task-local Business requires exactly one "
            "business.work-order ToolGrant endpoint"
        )
    endpoint_id = next(iter(work_order_grant_endpoint_ids))
    if endpoint_id in provider_ids:
        raise ValueError(
            "Inspection task-local Business endpoint must not be a physical Provider"
        )
    if business_grant_endpoint_ids != {endpoint_id} or (
        business_query_endpoint_ids and business_query_endpoint_ids != {endpoint_id}
    ):
        raise ValueError(
            "Inspection task-local Business grants must target its sole endpoint"
        )
    observation_grant_endpoint_ids = {
        grant.provider_id for agent in agents for grant in agent.observations
    }
    if endpoint_id in observation_grant_endpoint_ids:
        raise ValueError(
            "Inspection task-local Business endpoint cannot provide observations"
        )
    if missing_physical_capabilities != {"business.work-order"}:
        raise ValueError(
            "Inspection task-local Business may supply exactly the explicit "
            "business.work-order capability"
        )
    return endpoint_id, True


class InspectionTaskPackageResolver:
    @property
    def package_id(self) -> str:
        return INSPECTION_PACKAGE_ID

    def scenario_projection(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
    ) -> ResolvedTaskScenarioProjection:
        """Compile provider-safe task semantics and explicit logical endpoints."""

        package = load_inspection_package(reader=reader, task=task)
        if (
            package.task_id != task.task_id
            or package.verifier_id != task.verifier.verifier_id
        ):
            raise ValueError("Inspection package identity differs from TaskSpec")
        provider_ids = {provider.provider_id for provider in environment.providers}
        network_provider_ids = _network_provider_ids(environment)
        mailbox_grants = _network_mailbox_grants(
            environment=environment,
            agents=agents,
        )
        for _agent_id, grant in mailbox_grants:
            reader.validate_schema(grant.schema_file)
        mailbox_agent_ids = {agent_id for agent_id, _grant in mailbox_grants}
        network_tool_agent_ids: set[str] = set()
        for agent in agents:
            for grant in agent.tools:
                if grant.provider_id not in network_provider_ids:
                    continue
                if grant.tool_id != "network.send":
                    raise ValueError("Inspection ns-3 Provider owns only network.send")
                network_tool_agent_ids.add(agent.agent_id)
        if network_tool_agent_ids - mailbox_agent_ids:
            raise ValueError(
                "every network.send Agent requires an authorized mailbox endpoint"
            )
        business_endpoint_id, task_local_business = (
            resolve_inspection_business_endpoint(
                package=package,
                task=task,
                environment=environment,
                agents=agents,
            )
        )
        logical_endpoint_ids = (business_endpoint_id,) if task_local_business else ()
        observation_actor_ids = {
            actor.actor_id
            for actor in package.actors
            if actor.role == "observation_provider"
        }
        agent_actor_ids = {
            actor.actor_id for actor in package.actors if actor.role == "agent"
        }
        if agent_actor_ids != {agent.agent_id for agent in agents}:
            raise ValueError("Inspection package agent actors differ from AgentSpec")

        tool_grant_endpoints = {
            grant.provider_id for agent in agents for grant in agent.tools
        }
        observation_grant_endpoints = {
            grant.provider_id for agent in agents for grant in agent.observations
        }
        query_grant_endpoints = {
            grant.provider_id for agent in agents for grant in agent.queries
        }
        allowed_endpoint_ids = provider_ids | set(logical_endpoint_ids)
        unknown_endpoints = (
            tool_grant_endpoints | query_grant_endpoints | observation_grant_endpoints
        ) - allowed_endpoint_ids
        if unknown_endpoints:
            raise ValueError(
                "Inspection grants name undeclared physical or task-local endpoints: "
                f"{sorted(unknown_endpoints)}"
            )
        if logical_endpoint_ids and (
            business_endpoint_id not in (tool_grant_endpoints | query_grant_endpoints)
            or business_endpoint_id in observation_grant_endpoints
        ):
            raise ValueError(
                "Inspection task-local Business must be used by tools or queries only"
            )
        trigger_provider_ids = {
            observation.trigger.provider_id for observation in package.observations
        }
        if trigger_provider_ids != observation_actor_ids:
            raise ValueError(
                "Inspection observation actors differ from physical trigger providers"
            )

        grants = {
            (grant.observation_id, grant.provider_id, grant.schema_file)
            for agent in agents
            for grant in agent.observations
        }
        task_assets = {asset.asset_id: asset for asset in task.assets}
        projections: list[ResolvedInspectionObservationProjection] = []
        for observation in package.observations:
            trigger = observation.trigger
            if (
                trigger.provider_id not in provider_ids
                or (
                    observation.observation_id,
                    trigger.provider_id,
                    observation.metadata_schema,
                )
                not in grants
            ):
                raise ValueError(
                    "Inspection observation is not granted to its physical provider"
                )
            orders = tuple(
                order
                for order in package.work_orders
                if order.target_id == observation.target_id
                and order.required_observation_id == observation.observation_id
            )
            if len(orders) != 1:
                raise ValueError(
                    "Inspection observation must bind exactly one work order"
                )
            package_asset = next(
                (
                    asset
                    for asset in package.assets
                    if asset.asset_id == observation.simulation_asset_id
                ),
                None,
            )
            task_asset = task_assets.get(observation.simulation_asset_id)
            if (
                package_asset is None
                or package_asset.visibility != "world"
                or task_asset is None
                or task_asset.file != package_asset.file
                or task_asset.classification != "private"
                or {
                    workload_id
                    for audience in task_asset.audiences
                    if audience.role == "provider"
                    for workload_id in audience.workload_ids
                }
                != {trigger.provider_id}
            ):
                raise ValueError(
                    "Inspection simulation asset is not private to its observation provider"
                )
            projections.append(
                ResolvedInspectionObservationProjection(
                    projection_kind="inspection",
                    observation_id=observation.observation_id,
                    work_order_id=orders[0].work_order_id,
                    target_id=observation.target_id,
                    simulation_asset_id=observation.simulation_asset_id,
                    sensor_id=trigger.camera_id,
                    media_type=observation.media_type,
                    min_distance_m=trigger.min_distance_m,
                    max_distance_m=trigger.max_distance_m,
                    min_view_angle_deg=trigger.min_view_angle_deg,
                    max_view_angle_deg=trigger.max_view_angle_deg,
                    earliest_time_ns=trigger.earliest_time_ns,
                    latest_time_ns=trigger.latest_time_ns,
                )
            )
        package_observations = {
            (
                observation.observation_id,
                observation.trigger.provider_id,
                observation.metadata_schema,
            )
            for observation in package.observations
        }
        mailbox_observations = {
            (grant.observation_id, grant.provider_id, grant.schema_file)
            for _agent_id, grant in mailbox_grants
        }
        px4_providers = {
            provider.provider_id: set(provider.capabilities)
            for provider in environment.providers
            if provider.adapter == "px4.gazebo"
        }
        flight_projections: list[ResolvedFlightObservationProjection] = []
        declared_flight_kinds: dict[str, set[str]] = {}
        for observation_id, provider_id, schema_file in sorted(
            grants - package_observations - mailbox_observations,
            key=lambda item: item[0],
        ):
            prefix, separator, remainder = observation_id.partition(".")
            observation_kind, nested_separator, vehicle_id = remainder.partition(".")
            if (
                prefix != "flight"
                or not separator
                or not nested_separator
                or observation_kind not in {"telemetry", "gnss"}
                or not vehicle_id
                or provider_id not in px4_providers
                or f"flight.{observation_kind}"
                not in px4_providers[provider_id]
            ):
                raise ValueError(
                    "Inspection extra observation grant is not a declared PX4 flight stream"
                )
            reader.validate_schema(schema_file)
            flight_projections.append(
                ResolvedFlightObservationProjection(
                    projection_kind="flight",
                    observation_id=observation_id,
                    observation_kind=observation_kind,
                    vehicle_id=vehicle_id,
                )
            )
            declared_flight_kinds.setdefault(vehicle_id, set()).add(observation_kind)
        if any(
            kinds != {"telemetry", "gnss"}
            for kinds in declared_flight_kinds.values()
        ):
            raise ValueError(
                "Inspection must grant telemetry and GNSS together for each flight vehicle"
            )
        return ResolvedTaskScenarioProjection(
            logical_endpoint_ids=logical_endpoint_ids,
            logical_capability_ids=(
                ("business.work-order",) if task_local_business else ()
            ),
            observations=tuple(
                sorted(projections, key=lambda item: item.observation_id)
            ),
            flight_observations=tuple(
                sorted(flight_projections, key=lambda item: item.observation_id)
            ),
        )

    def resolve(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
        scenario: ResolvedScenario,
    ) -> FeasibilityAssessment:
        _, _, feasibility = resolve_inspection_run_context(
            reader=reader,
            task=task,
            environment=environment,
            agents=agents,
            scenario=scenario,
        )
        return feasibility

    @staticmethod
    def _validate_run_contract(
        reader: BundleReader,
        package: InspectionTaskPackage,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
        scenario: ResolvedScenario,
    ) -> None:
        if not isinstance(scenario, ResolvedScenario):
            raise TypeError("Inspection resolution requires a ResolvedScenario")
        if package.task_id != task.task_id:
            raise ValueError("Inspection package task_id does not match TaskSpec")
        if package.verifier_id != task.verifier.verifier_id:
            raise ValueError("Inspection package verifier_id does not match TaskSpec")

        package_agent_ids = {
            actor.actor_id for actor in package.actors if actor.role == "agent"
        }
        resolved_agent_ids = {agent.agent_id for agent in agents}
        if package_agent_ids != resolved_agent_ids:
            raise ValueError("Inspection actor grants do not match resolved agents")
        provider_ids = {provider.provider_id for provider in environment.providers}
        scenario_providers = {
            provider.provider_id: provider.capability_ids
            for provider in scenario.providers
        }
        environment_providers = {
            provider.provider_id: tuple(sorted(provider.capabilities))
            for provider in environment.providers
        }
        if scenario_providers != environment_providers:
            raise ValueError(
                "Inspection ResolvedScenario providers do not match the environment"
            )
        observation_actor_ids = {
            actor.actor_id
            for actor in package.actors
            if actor.role == "observation_provider"
        }
        if not observation_actor_ids <= provider_ids:
            raise ValueError(
                "Inspection observation actors are not declared physical Providers"
            )
        business_endpoint_id, task_local_business = (
            resolve_inspection_business_endpoint(
                package=package,
                task=task,
                environment=environment,
                agents=agents,
            )
        )
        expected_logical_endpoint_ids = (
            (business_endpoint_id,) if task_local_business else ()
        )
        if scenario.task.logical_endpoint_ids != expected_logical_endpoint_ids:
            raise ValueError(
                "Inspection ResolvedScenario logical endpoints do not match "
                "the resolved Business ownership mode"
            )

        mailbox_grants = _network_mailbox_grants(
            environment=environment,
            agents=agents,
        )
        network_provider_ids = _network_provider_ids(environment)
        if mailbox_grants:
            if (
                scenario.network is None
                or scenario.network.provider_id not in network_provider_ids
            ):
                raise ValueError(
                    "Inspection mailbox grants require resolved ns-3 network authority"
                )
            network_endpoint_ids = {
                binding.endpoint_id for binding in scenario.network.node_bindings
            }
            resolved_observations = {
                (binding.agent_id, binding.observation_id): binding
                for binding in scenario.task.observations
            }
            for agent_id, grant in mailbox_grants:
                reader.validate_schema(grant.schema_file)
                endpoint_id = grant.observation_id[
                    len(MAILBOX_OBSERVATION_PREFIX) :
                ]
                binding = resolved_observations.get(
                    (agent_id, grant.observation_id)
                )
                if (
                    endpoint_id not in network_endpoint_ids
                    or binding is None
                    or binding.endpoint_id != grant.provider_id
                    or binding.schema_file != grant.schema_file
                    or binding.inspection is not None
                    or binding.flight is not None
                ):
                    raise ValueError(
                        "Inspection mailbox grant differs from resolved network authority"
                    )

        scenario_sensors = {sensor.sensor_id: sensor for sensor in scenario.sensors}
        scenario_targets = {
            target.target_id: target for target in scenario.semantic_targets
        }
        if any(
            order.target_id not in scenario_targets for order in package.work_orders
        ):
            raise ValueError(
                "Inspection work orders must reference ResolvedScenario targets"
            )

        for observation in package.observations:
            reader.validate_schema(observation.metadata_schema)
            sensor = scenario_sensors.get(observation.trigger.camera_id)
            target = scenario_targets.get(observation.target_id)
            if (
                sensor is None
                or target is None
                or sensor.provider_id != observation.trigger.provider_id
                or target.required_sensor_id != sensor.sensor_id
                or observation.trigger.target_id != target.target_id
            ):
                raise ValueError(
                    "Inspection observations must match ResolvedScenario sensor and target bindings"
                )
        package_observations = {
            (
                observation.observation_id,
                observation.trigger.provider_id,
                observation.metadata_schema,
            )
            for observation in package.observations
        }
        granted_observations = {
            (grant.observation_id, grant.provider_id, grant.schema_file)
            for agent in agents
            for grant in agent.observations
        }
        mailbox_observations = {
            (grant.observation_id, grant.provider_id, grant.schema_file)
            for _agent_id, grant in mailbox_grants
        }
        flight_observations = {
            (binding.observation_id, binding.endpoint_id, binding.schema_file)
            for binding in scenario.task.observations
            if binding.flight is not None
        }
        if granted_observations != (
            package_observations | mailbox_observations | flight_observations
        ):
            raise ValueError(
                "Inspection Observation contracts do not match task and mailbox grants"
            )

        task_assets = {asset.asset_id: asset for asset in task.assets}
        for package_asset in package.assets:
            asset = task_assets.get(package_asset.asset_id)
            if asset is None or asset.file != package_asset.file:
                raise ValueError("Inspection package asset is not pinned by TaskSpec")
            if package_asset.visibility == "world":
                provider_audiences = {
                    workload_id
                    for audience in asset.audiences
                    if audience.role == "provider"
                    for workload_id in audience.workload_ids
                }
                required_providers = {
                    observation.trigger.provider_id
                    for observation in package.observations
                    if observation.simulation_asset_id == package_asset.asset_id
                }
                if (
                    asset.classification != "private"
                    or any(audience.role != "provider" for audience in asset.audiences)
                    or provider_audiences != required_providers
                ):
                    raise ValueError(
                        "Inspection world asset is not private to its Observation Provider"
                    )
            elif (
                asset.classification != "private"
                or len(asset.audiences) != 1
                or asset.audiences[0].role != "verifier"
                or asset.audiences[0].workload_ids != (package.verifier_id,)
            ):
                raise ValueError("Inspection truth asset is not verifier-private")

        verifier_requirements = {
            item.artifact_id: item for item in task.verifier.artifact_requirements
        }
        if {item.artifact_id for item in package.artifact_requirements} != set(
            verifier_requirements
        ):
            raise ValueError(
                "Inspection artifact requirements do not match VerifierSpec identities"
            )
        package_assets = {asset.asset_id: asset for asset in package.assets}
        for requirement in package.artifact_requirements:
            core_requirement = verifier_requirements.get(requirement.artifact_id)
            if core_requirement is None:
                raise ValueError(
                    "Inspection evidence artifact is absent from VerifierSpec: "
                    f"{requirement.artifact_id}"
                )
            if requirement.model_dump(mode="json", exclude={"evidence_kind"}) != (
                core_requirement.model_dump(mode="json")
            ):
                raise ValueError(
                    "Inspection evidence artifact contract does not match "
                    f"VerifierSpec: {requirement.artifact_id}"
                )
            if requirement.producer_id == "bundle":
                source_asset = package_assets.get(
                    core_requirement.source_asset_id or ""
                )
                if source_asset is None or source_asset.visibility != "verifier":
                    raise ValueError(
                        "Inspection bundle evidence must come from verifier-private asset"
                    )

        task_goals = {
            (
                goal.goal_id,
                goal.metric_id,
                goal.operator,
                goal.threshold,
                goal.evidence,
                goal.parameters,
            )
            for goal in task.goals
        }
        package_goals = {
            (
                goal.goal_id,
                goal.metric_id,
                goal.operator,
                goal.threshold,
                goal.evidence,
                goal.parameters,
            )
            for goal in package.goals
        }
        if any(goal.parameters for goal in package.goals):
            raise ValueError(
                "Inspection metrics do not declare parameter schemas; goal parameters "
                "must be empty"
            )
        if package_goals != task_goals:
            raise ValueError("Inspection goals do not match TaskSpec goals")


def resolve_inspection_run_context(
    *,
    reader: BundleReader,
    task: TaskSpec,
    environment: EnvironmentSpec,
    agents: tuple[AgentSpec, ...],
    scenario: ResolvedScenario,
) -> tuple[
    InspectionTaskPackage,
    InspectionBoundResolution,
    FeasibilityAssessment,
]:
    """Resolve the pinned Inspection package and its physical bounds."""

    package = load_inspection_package(reader=reader, task=task)
    InspectionTaskPackageResolver._validate_run_contract(
        reader,
        package,
        task,
        environment,
        agents,
        scenario,
    )
    providers = {provider.provider_id: provider for provider in environment.providers}
    required_provider_ids = {source.provider_id for source in package.bound_sources} | {
        observation.trigger.provider_id for observation in package.observations
    }
    for provider_id in sorted(required_provider_ids):
        provider = providers.get(provider_id)
        if provider is None:
            raise ValueError(
                "Inspection physical-bound provider is absent from the environment"
            )
        reader.validate_schema_bound_file(provider.config)
    resolved_bounds = resolve_inspection_bounds(
        package,
        environment=environment,
        reader=reader,
    )
    bounds = calculate_theoretical_bounds(
        resolved_bounds.bounds,
        network_delivery_required=package.network_delivery_required,
    )
    feasibility = FeasibilityAssessment(
        package_id=INSPECTION_PACKAGE_ID,
        feasible=bounds.success_upper_bound > 0.0,
        success_upper_bound=bounds.success_upper_bound,
        failed_conditions=bounds.failed_conditions,
        bounds=(
            NamedValue(
                name="mission.minimum-time-s",
                value=bounds.mission_minimum_time_s,
            ),
            NamedValue(
                name="upload.minimum-time-s",
                value=bounds.upload_minimum_time_s,
            ),
            NamedValue(
                name="imaging.projected-defect-pixels",
                value=bounds.projected_defect_pixels,
            ),
            NamedValue(
                name="recall.upper-bound",
                value=bounds.recall_upper_bound,
            ),
            NamedValue(
                name="detection-f1.upper-bound",
                value=bounds.detection_f1_upper_bound,
            ),
        ),
    )
    return package, resolved_bounds, feasibility


def inspection_package_from_bundle(
    *,
    bundle_root: str | Path,
    task: TaskSpec,
) -> InspectionTaskPackage:
    return load_inspection_package(reader=BundleReader(Path(bundle_root)), task=task)


__all__ = [
    "INSPECTION_PACKAGE_ID",
    "InspectionTaskPackageResolver",
    "inspection_package_from_bundle",
    "load_inspection_package",
    "resolve_inspection_business_endpoint",
    "resolve_inspection_run_context",
]
