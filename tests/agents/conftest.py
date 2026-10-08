"""Keep the optional live gateway gate scoped to the owned agent tests."""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "llm: real local GLM gateway integration (no mocks)"
    )
