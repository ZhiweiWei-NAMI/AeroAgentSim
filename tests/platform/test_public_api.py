"""The package exports the shared runtime without legacy environment aliases."""

import importlib

import pytest


def test_public_runtime_exports() -> None:
    package = importlib.import_module("aeroagentsim")
    from aeroagentsim import RunSession, Simulation

    assert package.__all__ == ["RunSession", "Simulation", "__version__"]
    assert Simulation.__module__ == "aeroagentsim.platform.simulation"
    assert RunSession.__module__ == "aeroagentsim.platform.simulation"
    for name in ("Environment", "AirFogSimEnv", "AeroAgentSimEnv"):
        with pytest.raises(AttributeError, match=name):
            getattr(package, name)
