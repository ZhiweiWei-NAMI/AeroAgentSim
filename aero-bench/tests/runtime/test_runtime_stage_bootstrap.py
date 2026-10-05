from __future__ import annotations

import json

import pytest

from aero_bench.executor.contracts import HarnessWorkloadContract
from aero_bench.providers.registry import ProviderRegistration, ProviderRegistry
from aero_bench.runtime.bootstrap import HarnessBootstrapError, bootstrap_harness
from aero_bench.tasks.registry import builtin_task_package_resolvers
from tests.runtime.test_harness_bootstrap import _ready_fixture
from tests.support import FixtureProviderConfig


@pytest.mark.parametrize("drift", ["stage", "missing_registration"])
def test_every_registry_binding_is_checked_before_any_session_builder(tmp_path, drift):
    environment, _ = _ready_fixture(tmp_path)
    run = HarnessWorkloadContract.model_validate_json(
        open(environment["AERO_BENCH_CONTRACT"], "rb").read()
    ).run
    stages = {p.provider_id: p.runtime_stage for p in run.scenario.providers}
    builder_calls = []

    def spy_builder(*args):
        builder_calls.append(args)
        raise AssertionError("stage drift must fail before any session builder")

    registrations = []
    for provider in run.environment.providers:
        if provider.provider_id == "world" and drift == "missing_registration":
            continue
        stage = ("network" if provider.provider_id == "world" and drift == "stage"
                 else stages[provider.provider_id])
        registrations.append(ProviderRegistration(
            adapter=provider.adapter, runtime_stage=stage,
            config_model=FixtureProviderConfig, session_builder=spy_builder))
    registry = ProviderRegistry(registrations)
    with pytest.raises(HarnessBootstrapError, match="runtime stage drift"):
        bootstrap_harness(environment, registry=registry,
                          task_package_resolvers=builtin_task_package_resolvers())
    assert builder_calls == []


def test_registry_refuses_missing_extra_or_duplicate_serialized_provider_ids(tmp_path):
    environment, _ = _ready_fixture(tmp_path)
    run = HarnessWorkloadContract.model_validate_json(
        open(environment["AERO_BENCH_CONTRACT"], "rb").read()
    ).run
    from tests.support import fixture_provider_registry
    registry = fixture_provider_registry()
    for providers in (run.scenario.providers[:-1],
                      (*run.scenario.providers, run.scenario.providers[0])):
        with pytest.raises(ValueError, match="Provider"):
            registry.verify_runtime_stage_binding(environment_providers=run.environment.providers,
                                                  resolved_providers=providers)
