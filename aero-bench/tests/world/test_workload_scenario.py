"""Focused wire-level tests for the dependency-free workload scenario validator."""

from __future__ import annotations

from tests.world.support import provider_stages

import hashlib
import json
import math
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from aero_bench.config.models import (
    AssetAudience,
    AssetRef,
    FileRef,
    ObservationGrant,
    ToolGrant,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.resolved import (
    ResolvedInspectionObservationProjection,
    ResolvedScenario,
    ResolvedTaskScenarioProjection,
    compile_resolved_scenario,
)
from aero_bench.world.workload_scenario import (
    ValidatedWorkloadScenario,
    WorkloadScenarioError,
    validate_workload_scenario,
)
from tests.world.support import (
    materialize_world_package,
    provider_capabilities,
    scenario_task_inputs,
)


_SEED = 20260901


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _file(path: str) -> FileRef:
    return FileRef(path=path, sha256=_sha(path))


def _compile_real_scenario(root: Path) -> ResolvedScenario:
    reader, world_reference, world = materialize_world_package(root)
    simulation_bytes = b"real compiler task asset\n"
    simulation_path = "task/simulation.bin"
    destination = root / simulation_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(simulation_bytes)

    base_task, base_agents, _ = scenario_task_inputs()
    task = base_task.model_copy(
        update={
            "required_capabilities": ("business.work-order",),
            "required_tools": ("business.claim",),
            "assets": (
                AssetRef(
                    asset_id="simulation.asset",
                    file=FileRef(
                        path=simulation_path,
                        sha256=hashlib.sha256(simulation_bytes).hexdigest(),
                    ),
                    classification="private",
                    audiences=(
                        AssetAudience(
                            role="provider",
                            workload_ids=("perception",),
                        ),
                    ),
                ),
            ),
        }
    )
    agent = base_agents[0].model_copy(
        update={
            "tools": (
                ToolGrant(
                    tool_id="business.claim",
                    provider_id="business.local",
                    request_schema=_file("schemas/business-claim-request.json"),
                    response_schema=_file("schemas/business-claim-response.json"),
                    timeout_ms=1000,
                    idempotent=False,
                ),
            ),
            "observations": (
                ObservationGrant(
                    observation_id="observation.capture",
                    provider_id="perception",
                    schema_file=_file("schemas/observation.json"),
                    timeout_ms=1000,
                ),
            ),
        }
    )
    target = world.semantic_targets[0]
    task_projection = ResolvedTaskScenarioProjection(
        logical_endpoint_ids=("business.local",),
        logical_capability_ids=("business.work-order",),
        observations=(
            ResolvedInspectionObservationProjection(
                projection_kind="inspection",
                observation_id="observation.capture",
                work_order_id="work.one",
                target_id=target.target_id,
                simulation_asset_id="simulation.asset",
                sensor_id=target.required_sensor_id,
                media_type="image/png",
                min_distance_m=target.min_distance_m,
                max_distance_m=target.max_distance_m,
                min_view_angle_deg=0.0,
                max_view_angle_deg=target.max_view_angle_deg,
                earliest_time_ns=0,
                latest_time_ns=10_000,
            ),
        ),
    )
    return compile_resolved_scenario(
        reader,
        world_reference,
        "launch.alpha",
        _SEED,
        provider_capabilities(),
        provider_stages((provider_capabilities()).keys()),
        task,
        (agent,),
        task_projection,
    )


@lru_cache(maxsize=1)
def _serialized_fixture() -> str:
    with TemporaryDirectory(prefix="workload-scenario-test-") as temporary:
        scenario = _compile_real_scenario(Path(temporary))
        return scenario.model_dump_json()


def _serialized_scenario() -> dict[str, Any]:
    return json.loads(_serialized_fixture())


def _seal(document: dict[str, Any]) -> None:
    document["scenario_asset_digest"] = hashlib.sha256(
        canonical_json_bytes(document["assets"])
    ).hexdigest()
    body = dict(document)
    body.pop("scenario_digest", None)
    document["scenario_digest"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _record(
    document: dict[str, Any], collection: str, field: str, value: str
) -> dict[str, Any]:
    return next(item for item in document[collection] if item[field] == value)


def _projection(
    scenario: dict[str, Any], role: str, workload_id: str
) -> list[dict[str, Any]]:
    return [
        asset
        for asset in scenario["assets"]
        if any(
            audience == {"role": role, "workload_id": workload_id}
            for audience in asset["audiences"]
        )
    ]


def _validate(
    scenario: dict[str, Any],
    *,
    role: str = "provider",
    workload_id: str = "flight",
    projected_assets: list[dict[str, Any]] | None = None,
) -> ValidatedWorkloadScenario:
    capabilities = provider_capabilities().get(workload_id) if role == "provider" else None
    return validate_workload_scenario(
        scenario,
        expected_seed=_SEED,
        expected_digest=scenario["scenario_digest"],
        role=role,  # type: ignore[arg-type]
        workload_id=workload_id,
        projected_assets=(
            _projection(scenario, role, workload_id)
            if projected_assets is None
            else projected_assets
        ),
        provider_capabilities=capabilities,
    )


def test_real_compiler_serialization_and_all_role_projections_validate() -> None:
    scenario = _serialized_scenario()

    flight = _validate(scenario)
    assert flight.scenario is scenario
    uav_model_id = _record(scenario, "entities", "entity_id", "uav.alpha")[
        "model_asset_id"
    ]
    assert flight.asset(uav_model_id)["source_kind"] == "world"

    perception = _validate(
        scenario,
        role="provider",
        workload_id="perception",
    )
    assert perception.asset("simulation.asset")["source_kind"] == "task"
    _validate(scenario, role="harness", workload_id="harness")
    _validate(scenario, role="agent", workload_id="fixture.agent")
    _validate(scenario, role="verifier", workload_id="fixture.verifier")


def _extra_frame_field(document: dict[str, Any]) -> None:
    document["frame_authority"]["origin"]["enu"]["legacy_x"] = 0.0


def _wrong_base_asset(document: dict[str, Any]) -> None:
    document["base_layers"][0]["asset_id"] = "uav.model"


def _bad_building_reference(document: dict[str, Any]) -> None:
    document["buildings"][0]["entity_id"] = "uav.alpha"


def _zero_road_width(document: dict[str, Any]) -> None:
    document["roads"][0]["width_m"] = 0.0


def _integer_road_width(document: dict[str, Any]) -> None:
    document["roads"][0]["width_m"] = 4


def _bad_region_attenuation(document: dict[str, Any]) -> None:
    region = document["regions"][0]
    if region["kind"] == "communications_shadow":
        region["kind"] = "no_fly"
    else:
        region["communications_shadow_attenuation_db"] = 1.0


def _bad_launch_override(document: dict[str, Any]) -> None:
    selected = next(
        entity for entity in document["entities"] if entity["selected_launch_override"]
    )
    selected["selected_launch_override"] = False


def _bad_entity_owner(document: dict[str, Any]) -> None:
    entity = _record(document, "entities", "entity_id", "uav.alpha")
    entity["source_provider_id"] = "traffic"


def _bad_asset_selector(document: dict[str, Any]) -> None:
    asset = next(asset for asset in document["assets"] if asset["world"] is not None)
    asset["world"]["selector_fragment"] = "other"


def _bad_sensor_range(document: dict[str, Any]) -> None:
    document["sensors"][0]["horizontal_fov_deg"] = 180.0


def _bad_target_reference(document: dict[str, Any]) -> None:
    document["semantic_targets"][0]["required_sensor_id"] = "camera.absent"


def _bad_weather_consistency(document: dict[str, Any]) -> None:
    document["weather"][0]["precipitation_rate_mm_per_h"] = 1.0


def _bad_scene_asset(document: dict[str, Any]) -> None:
    binding = next(
        item for item in document["engine_frame_bindings"] if item["engine"] == "gazebo"
    )
    binding["scene_asset_id"] = "uav.model"


def _bad_sumo_kind(document: dict[str, Any]) -> None:
    binding = document["sumo"]["object_bindings"][0]
    binding["kind"] = "person" if binding["kind"] == "vehicle" else "vehicle"


def _bad_network_node(document: dict[str, Any]) -> None:
    document["network"]["node_bindings"][0]["radio_profile_id"] = "radio.absent"


def _bad_network_link(document: dict[str, Any]) -> None:
    source = document["network"]["node_bindings"][0]["node_id"]
    document["network"]["links"] = [
        {
            "link_id": "link.forged",
            "source_node_id": source,
            "destination_node_id": "node.absent",
            "data_rate_bps": 1,
            "propagation_delay_ns": 0,
        }
    ]


def _mission_cycle(document: dict[str, Any]) -> None:
    takeoff = next(
        requirement
        for requirement in document["mission_requirements"]
        if requirement["kind"] == "takeoff"
    )
    land = next(
        requirement
        for requirement in document["mission_requirements"]
        if requirement["kind"] == "land"
    )
    takeoff["dependencies"] = [land["requirement_id"]]


def _bad_public_asset_producer(document: dict[str, Any]) -> None:
    observation = next(
        requirement
        for requirement in document["mission_requirements"]
        if requirement["kind"] == "observation"
    )
    document["expected_public_assets"][0]["producer_requirement_id"] = observation[
        "requirement_id"
    ]


def _bad_tool_type(document: dict[str, Any]) -> None:
    document["task"]["tools"][0]["idempotent"] = 0


def _bad_observation_asset(document: dict[str, Any]) -> None:
    document["task"]["observations"][0]["inspection"][
        "simulation_asset_id"
    ] = "uav.model"


def _bad_goal_shape(document: dict[str, Any]) -> None:
    document["task"]["goals"][0]["threshold"] = "1.0"


def _unused_logical_endpoint(document: dict[str, Any]) -> None:
    document["task"]["logical_endpoint_ids"].append("unused.endpoint")


def _unsorted_network_records(document: dict[str, Any]) -> None:
    document["entities"].reverse()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_extra_frame_field, id="nested-exact-fields"),
        pytest.param(_wrong_base_asset, id="base-layer-role-reference"),
        pytest.param(_bad_building_reference, id="building-entity-reference"),
        pytest.param(_zero_road_width, id="road-range"),
        pytest.param(_integer_road_width, id="road-exact-number-type"),
        pytest.param(_bad_region_attenuation, id="region-kind-fields"),
        pytest.param(_bad_launch_override, id="launch-override-closure"),
        pytest.param(_bad_entity_owner, id="entity-ownership"),
        pytest.param(_bad_asset_selector, id="asset-selector-binding"),
        pytest.param(_bad_sensor_range, id="sensor-range"),
        pytest.param(_bad_target_reference, id="target-sensor-reference"),
        pytest.param(_bad_weather_consistency, id="weather-consistency"),
        pytest.param(_bad_scene_asset, id="gazebo-scene-role"),
        pytest.param(_bad_sumo_kind, id="sumo-entity-kind"),
        pytest.param(_bad_network_node, id="network-node-reference"),
        pytest.param(_bad_network_link, id="network-link-reference"),
        pytest.param(_mission_cycle, id="mission-dag"),
        pytest.param(_bad_public_asset_producer, id="public-asset-closure"),
        pytest.param(_bad_tool_type, id="tool-exact-type"),
        pytest.param(_bad_observation_asset, id="observation-authority"),
        pytest.param(_bad_goal_shape, id="goal-exact-type"),
        pytest.param(_unused_logical_endpoint, id="logical-endpoint-closure"),
        pytest.param(_unsorted_network_records, id="canonical-record-order"),
    ],
)
def test_digest_resealed_deep_tampering_is_rejected(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    scenario = _serialized_scenario()
    mutate(scenario)
    _seal(scenario)

    with pytest.raises(WorkloadScenarioError):
        _validate(scenario)


def test_projected_assets_must_be_the_exact_role_authorized_records() -> None:
    scenario = _serialized_scenario()
    projected = _projection(scenario, "provider", "flight")

    with pytest.raises(WorkloadScenarioError, match="exact authorized projection"):
        _validate(scenario, projected_assets=projected[:-1])

    unauthorized = _record(scenario, "assets", "asset_id", "simulation.asset")
    with pytest.raises(WorkloadScenarioError, match="exact authorized projection"):
        _validate(scenario, projected_assets=sorted([*projected, unauthorized], key=lambda asset: asset["asset_id"]))


def test_nonfinite_numbers_and_unsorted_asset_catalog_fail_closed() -> None:
    scenario = _serialized_scenario()
    scenario["weather"][0]["temperature_c"] = math.inf
    # A non-finite JSON value cannot possess a valid canonical scenario digest.
    with pytest.raises(WorkloadScenarioError):
        _validate(scenario)

    scenario = _serialized_scenario()
    scenario["assets"].reverse()
    _seal(scenario)
    with pytest.raises(WorkloadScenarioError, match="sorted and unique"):
        _validate(scenario)
