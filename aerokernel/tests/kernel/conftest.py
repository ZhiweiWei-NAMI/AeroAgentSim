"""Keep host performance checks opt-in under either repository pytest config."""

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "perf: opt-in host performance checks (-m perf)")


def pytest_collection_modifyitems(config, items):
    if config.option.markexpr.strip() == "perf":
        return
    for item in items:
        if "perf" in item.keywords:
            item.add_marker(
                pytest.mark.skip(reason="opt-in host benchmark: use -m perf")
            )
