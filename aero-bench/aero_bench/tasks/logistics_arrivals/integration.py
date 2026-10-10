"""Fail-closed resolution for a distinct Business-only arrivals profile."""

from aero_bench.config.models import FeasibilityAssessment, NamedValue
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.logistics_business.scheduled_arrivals import (
    SCHEDULED_ARRIVAL_CAPABILITY,
)
from aero_bench.tasks.logistics_arrivals.contracts import (
    PACKAGE_ID,
    METRIC_ID,
    LogisticsArrivalsPackage,
    LogisticsArrivalsVerifierConfig,
)
from aero_bench.tasks.logistics_arrivals.domain import LogisticsArrivalsDomain
from aero_bench.world.resolved import ResolvedTaskScenarioProjection


def load_context(*, reader, task, environment, agents):
    if task.package.package_id != PACKAGE_ID:
        raise ValueError("arrivals resolver received another task profile")
    package = LogisticsArrivalsPackage.model_validate(
        reader.validate_schema_bound_file(task.package.config)
    )
    if (package.task_id, package.verifier_id) != (
        task.task_id,
        task.verifier.verifier_id,
    ):
        raise ValueError("arrivals package identity differs from TaskSpec")
    if len(environment.providers) != 1:
        raise ValueError("non-physical arrivals requires exactly one Business Provider")
    provider = environment.providers[0]
    capabilities = {
        "logistics.facilities.state",
        "logistics.orders.authority",
        SCHEDULED_ARRIVAL_CAPABILITY,
    }
    if (
        provider.provider_id != package.provider_id
        or provider.adapter != "logistics.business"
        or set(provider.capabilities) != capabilities
        or set(task.required_capabilities) != capabilities
        or package.business_config != provider.config
    ):
        raise ValueError(
            "arrivals requires its exact pinned Business capability/config binding"
        )
    config = LogisticsBusinessConfig.model_validate(
        reader.validate_schema_bound_file(provider.config)
    )
    if (
        config.provider_id != provider.provider_id
        or config.task_package.task_id != task.task_id
        or config.task_package.verifier_id != package.verifier_id
        or config.observation is not None
        or not isinstance(config.task_package, LogisticsArrivalsDomain)
        or config.task_package.orders
        or not config.scheduled_orders
        or config.principal_bindings
    ):
        raise ValueError(
            "arrivals profile requires scheduled creation only, no baseline/physical/command ingress"
        )
    if (
        len(agents) != 1
        or agents[0].tools
        or agents[0].queries
        or agents[0].observations
        or agents[0].driver
    ):
        raise ValueError(
            "arrivals requires one clock participant with no domain/private grants"
        )
    if task.required_tools:
        raise ValueError("scheduled arrivals cannot be injected through Agent tools")
    if len(task.goals) != 1 or any(
        (goal.metric_id, goal.operator, goal.threshold, goal.evidence, goal.parameters)
        != (METRIC_ID, "eq", 1.0, "authoritative_state", ())
        for goal in task.goals
    ):
        raise ValueError(
            "arrivals goal must check the complete scheduled arrival authority"
        )
    for item in config.scheduled_orders:
        if (
            item.at_tick > environment.clock.max_steps
            or item.order.release_time_s * 1_000_000_000
            < item.at_tick * environment.clock.step_ns
        ):
            raise ValueError("scheduled creation/release violates the declared clock")
        if (
            item.order.release_time_s * 1_000_000_000
            > environment.clock.max_steps * environment.clock.step_ns
        ):
            raise ValueError(
                "arrival release must be observable within the run horizon"
            )
    if task.verifier.artifact_requirements != (
        *environment.harness_artifact_requirements,
        *provider.artifact_requirements,
    ):
        raise ValueError(
            "arrivals verifier must consume the complete declared native inventory"
        )
    return package, provider, config


class LogisticsArrivalsTaskPackageResolver:
    package_id = PACKAGE_ID

    def scenario_projection(self, **kwargs):
        load_context(**kwargs)
        return ResolvedTaskScenarioProjection(
            logical_endpoint_ids=(), logical_capability_ids=(), observations=()
        )

    def resolve(self, **kwargs):
        # The host validates actual Verifier inputs before materialization.
        LogisticsArrivalsVerifierConfig.model_validate(
            kwargs["reader"].validate_schema_bound_file(kwargs["task"].verifier.config)
        )
        return self.resolve_runtime(**kwargs)

    def resolve_runtime(self, *, scenario, **kwargs):
        # Harness revalidation consumes only its declared runtime inputs.
        # The independent Verifier separately validates its private rules.
        _, provider, config = load_context(**kwargs)
        if (
            scenario.world_id != config.task_package.scene.scene_id
            or scenario.source_asset_digest != config.task_package.scene.scene_source_sha256
            or abs(scenario.frame_authority.origin.wgs84.latitude_deg - config.task_package.scene.origin_latitude_deg) > 1e-10
            or abs(scenario.frame_authority.origin.wgs84.longitude_deg - config.task_package.scene.origin_longitude_deg) > 1e-10
            or abs(scenario.frame_authority.origin.wgs84.ellipsoid_height_m - config.task_package.scene.origin_altitude_m) > 1e-6
            or scenario.selected_launch_site_id is not None
            or scenario.launch_sites
            or any(entity.state != "static" for entity in scenario.entities)
            or scenario.sensors
            or scenario.mission_requirements
            or scenario.sumo
            or scenario.network
            or any(
                item.runtime_stage != "business_environment"
                for item in scenario.providers
            )
        ):
            raise ValueError(
                "non-physical arrivals cannot declare launch, motion, sensors or physical missions"
            )
        return FeasibilityAssessment(
            package_id=PACKAGE_ID,
            feasible=True,
            success_upper_bound=1.0,
            failed_conditions=(),
            bounds=(
                NamedValue(
                    name="scheduled_creation_count", value=len(config.scheduled_orders)
                ),
                NamedValue(name="physical_delivery_supported", value=False),
            ),
        )
