"""Isolate deprecated v1 suites from the default kernel installation."""

from pathlib import Path

import pytest

from aeroagentsim import _require_legacy


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "legacy: requires aeroagentsim[legacy] (v1 SimPy)"
    )


def pytest_ignore_collect(collection_path, config):
    if collection_path.name == "test_compatibility.py":
        return None
    if collection_path.suffix == ".py" and collection_path.name.startswith("test_"):
        try:
            _require_legacy()
        except ImportError:
            return True
    return None


def pytest_collection_modifyitems(items):
    suite = Path(__file__).parent
    for item in items:
        if (
            Path(item.path).parent == suite
            and item.path.name != "test_compatibility.py"
        ):
            item.add_marker(pytest.mark.legacy)
