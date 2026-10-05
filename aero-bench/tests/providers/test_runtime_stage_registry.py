from __future__ import annotations

import pytest

from aero_bench.providers.registry import (
    ProviderRegistration, ProviderRegistry, ProviderRegistryError,
    builtin_provider_registry,
)
from tests.support import FixtureProviderConfig


def _builder(*args):
    raise AssertionError("registry projection must not create a session")


def test_builtin_stages_are_explicit_and_exact():
    assert builtin_provider_registry().runtime_stages() == {
        "inspection.business": "business_environment",
        "logistics.business": "business_environment",
        "ns3.rpc": "network",
        "px4.gazebo": "motion",
        "sumo.traci": "motion",
    }


@pytest.mark.parametrize("stage", [None, "compute", ["motion"], ("motion",), 1, True])
def test_registration_rejects_non_scalar_or_unknown_stages(stage):
    with pytest.raises(ProviderRegistryError, match="runtime_stage"):
        ProviderRegistration(adapter="contract.provider", runtime_stage=stage,
                             config_model=FixtureProviderConfig, session_builder=_builder)


def test_registration_requires_stage_and_rejects_duplicate_adapter():
    with pytest.raises(TypeError, match="runtime_stage"):
        ProviderRegistration(adapter="contract.provider", config_model=FixtureProviderConfig,
                             session_builder=_builder)
    registration = ProviderRegistration(
        adapter="contract.provider", runtime_stage="network",
        config_model=FixtureProviderConfig, session_builder=_builder)
    registry = ProviderRegistry((registration,))
    with pytest.raises(ProviderRegistryError, match="already registered"):
        registry.register(registration)
    with pytest.raises(ProviderRegistryError, match="no Provider adapter"):
        registry.runtime_stage_for("unknown.business")


def test_adapter_names_do_not_infer_stage_and_multiple_providers_may_share_stage():
    registry = ProviderRegistry(tuple(ProviderRegistration(
        adapter=adapter, runtime_stage="network", config_model=FixtureProviderConfig,
        session_builder=_builder,
    ) for adapter in ("contract.motion", "contract.business", "contract.sensor")))
    assert registry.runtime_stages() == {
        "contract.business": "network", "contract.motion": "network",
        "contract.sensor": "network",
    }
