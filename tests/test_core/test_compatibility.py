"""Public compatibility behavior in both minimal and legacy installations."""

import importlib
import subprocess
import sys
import warnings
from importlib.metadata import PackageNotFoundError

import pytest

import aeroagentsim


def test_new_api_stays_import_light():
    script = """
import sys
from aeroagentsim import Simulation, RunSession
from aeroagentsim.services.cli import main
assert Simulation.__name__ == 'Simulation'
assert RunSession.__name__ == 'RunSession'
for name in sys.modules:
    assert not name.startswith(('airfogsim', 'aero_bench', 'simpy',
        'aeroagentsim.core', 'aeroagentsim.visualization', 'aeroagentsim.manager'))
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_missing_legacy_dependencies_is_actionable(monkeypatch):
    original = aeroagentsim.version

    def missing(name):
        if name == "simpy":
            raise PackageNotFoundError(name)
        return original(name)

    monkeypatch.setattr(aeroagentsim, "version", missing)
    with pytest.raises(ImportError, match=r"aeroagentsim\[legacy\].*MIGRATION-v1"):
        _ = aeroagentsim.Environment


def test_legacy_environment_identity_and_warning():
    try:
        aeroagentsim._require_legacy()
    except ImportError:
        pytest.skip("requires aeroagentsim[legacy]")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        from aeroagentsim import Environment
        from airfogsim import Environment as alias
    assert Environment is alias
    assert (
        Environment
        is importlib.import_module("aeroagentsim.core.environment").Environment
    )
    assert any("MIGRATION-v1.md" in str(w.message) for w in caught)
