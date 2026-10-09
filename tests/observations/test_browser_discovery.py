"""Browser selection follows public configuration rather than a machine-specific cache."""

from pathlib import Path

import pytest

from aeroagentsim.observations.renderer import BrowserRenderer


def test_browser_selection_operator_override_and_playwright_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules = tmp_path / "node_modules"
    monkeypatch.delenv("AEROAGENTSIM_CHROMIUM", raising=False)
    default = BrowserRenderer("http://127.0.0.1/", node_modules=modules)
    assert default.browser_executable is None  # Playwright selects its installed revision.

    operator = tmp_path / "operator-chromium"
    operator.touch()
    monkeypatch.setenv("AEROAGENTSIM_CHROMIUM", str(operator))
    configured = BrowserRenderer("http://127.0.0.1/", node_modules=modules)
    assert configured.browser_executable == operator
    explicit = tmp_path / "authored-chromium"
    explicit.touch()
    authored = BrowserRenderer(
        "http://127.0.0.1/", node_modules=modules, browser_executable=explicit
    )
    assert authored.browser_executable == explicit

    monkeypatch.setenv("AEROAGENTSIM_CHROMIUM", str(tmp_path / "missing-chromium"))
    with pytest.raises(FileNotFoundError, match="Chromium executable"):
        BrowserRenderer("http://127.0.0.1/", node_modules=modules)
