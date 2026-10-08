"""Docker is opt-in; all artifact/cache writes remain inside the E1 scratch root."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "docker: real E1 native Docker integration")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("markexpr") == "docker":
        return
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="opt in with -m docker"))
