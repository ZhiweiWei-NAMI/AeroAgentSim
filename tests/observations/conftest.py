"""Local markers do not require modifying shared project configuration."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "browser: real Chromium renderer bridge gate")
    config.addinivalue_line("markers", "media: real ffmpeg encode/decode gate")
    config.addinivalue_line("markers", "docker: Docker runtime gates")
