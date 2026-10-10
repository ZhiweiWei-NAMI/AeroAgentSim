from __future__ import annotations

import json

import pytest
import yaml

from aero_bench.config.resolver import resolve_suite, validate_resolved_run_bundle
from aero_bench.executor.planning import build_execution_plan
from aero_bench.providers.registry import ProviderRegistryError
from aero_bench.tasks.registry import builtin_task_package_resolvers
from aero_bench.world.resolved import ResolvedProvider, ResolvedScenario, scenario_assets_for_workload
from aero_bench.world.workload_scenario import validate_workload_scenario
from tests.support import build_bundle, digest, fixture_provider_registry


def _resolve(bundle, registry):
    return resolve_suite(str(bundle.suite), executor_kind="docker_reference",
                         task_package_resolvers=builtin_task_package_resolvers(),
                         provider_registry=registry)[0]


def test_registry_stage_is_bound_into_scenario_digest_and_run_id(tmp_path):
    bundle = build_bundle(tmp_path)
    first = _resolve(bundle, fixture_provider_registry())
    second = _resolve(bundle, fixture_provider_registry(stage_overrides={
        "fixture.business-artifact-writer": "network",
    }))
    assert first.scenario.scenario_digest != second.scenario.scenario_digest
    assert first.run_id != second.run_id
    assert first.environment == second.environment
    assert first.task == second.task and first.agents == second.agents
    assert {p.runtime_stage for p in first.scenario.providers if p.provider_id in
            {"business", "world", "observation"}} == {"business_environment"}


@pytest.mark.parametrize("override, message", [
    ({"fixture.flight-artifact-writer": "network"}, "exactly equal"),
    ({"fixture.traffic-provider": "business_environment"}, "exactly equal"),
    ({"fixture.business-artifact-writer": "motion"}, "exactly equal"),
    ({"fixture.network-artifact-writer": "business_environment"}, "network-stage"),
])
def test_stage_projection_preserves_exact_dynamic_owners_and_network_authority(
    tmp_path, override, message
):
    bundle = build_bundle(tmp_path)
    with pytest.raises(ValueError, match=message):
        _resolve(bundle, fixture_provider_registry(stage_overrides=override))


def test_resolver_refuses_unknown_adapter_instead_of_using_its_name(tmp_path):
    bundle = build_bundle(tmp_path)
    environment_path = bundle.root / "environments/environment.yaml"
    environment = yaml.safe_load(environment_path.read_text())
    environment["providers"][0]["adapter"] = "unregistered.business"
    environment["providers"][0]["workload"]["implementation"]["component_id"] = "unregistered.business"
    environment_path.write_text(yaml.safe_dump(environment, sort_keys=False))
    suite = yaml.safe_load(bundle.suite.read_text())
    suite["cases"][0]["environment"]["sha256"] = digest(environment_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False))
    with pytest.raises(ProviderRegistryError, match="no Provider adapter"):
        _resolve(bundle, fixture_provider_registry())


@pytest.mark.parametrize("boundary", [validate_resolved_run_bundle, build_execution_plan])
def test_materialization_and_full_bundle_validation_refuse_registry_drift(tmp_path, boundary):
    bundle = build_bundle(tmp_path)
    run = _resolve(bundle, fixture_provider_registry())
    arguments = dict(bundle_root=bundle.root,
                     task_package_resolvers=builtin_task_package_resolvers(),
                     provider_registry=fixture_provider_registry(stage_overrides={
                         "fixture.business-artifact-writer": "network"}))
    if boundary is build_execution_plan:
        arguments["executor_kind"] = "docker_reference"
    with pytest.raises(ProviderRegistryError, match="runtime stage drift"):
        boundary(run, **arguments)


@pytest.mark.parametrize("stage", [None, "compute", ["motion"]])
def test_resolved_provider_requires_a_strict_stage(stage):
    with pytest.raises(ValueError):
        ResolvedProvider(provider_id="provider", runtime_stage=stage,
                         roles=("sensor",), capability_ids=("camera.rgb",))
    with pytest.raises(ValueError, match="runtime_stage"):
        ResolvedProvider(provider_id="provider", roles=("sensor",),
                         capability_ids=("camera.rgb",))


def test_strict_python_and_standalone_reader_reject_old_version_and_missing_stage(tmp_path):
    bundle = build_bundle(tmp_path)
    run = _resolve(bundle, fixture_provider_registry())
    scenario = run.scenario.model_dump(mode="json")
    assert scenario["schema_version"] == "aero-bench.resolved-scenario/v4"
    reader_arguments = dict(expected_seed=run.seed, expected_digest=run.scenario.scenario_digest,
                            role="harness", workload_id="harness", projected_assets=[
                                a.model_dump(mode="json") for a in scenario_assets_for_workload(
                                    run.scenario, role="harness", workload_id="harness")])
    validate_workload_scenario(scenario, **reader_arguments)
    for change in ("old_version", "missing_stage"):
        raw = json.loads(json.dumps(scenario))
        if change == "old_version":
            raw["schema_version"] = "aero-bench.resolved-scenario/v3"
        else:
            raw["providers"][0].pop("runtime_stage")
        with pytest.raises(ValueError):
            ResolvedScenario.model_validate(raw)
        with pytest.raises(ValueError):
            validate_workload_scenario(raw, **reader_arguments)
