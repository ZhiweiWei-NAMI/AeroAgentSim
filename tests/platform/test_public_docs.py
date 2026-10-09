"""Public entry-point instructions refer to shipped files and supported extras."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_public_instructions_are_portable_and_links_exist() -> None:
    paths = [
        ROOT / name
        for name in ("README.md", "README_CN.md", "INSTALL.md", "CONTRIBUTING.md")
    ]
    paths += sorted((ROOT / "docs/getting-started").glob("*.md"))
    paths += [
        ROOT / "scenarios/demos/traffic-accident" / name
        for name in ("README.md", "INTEGRATION.md")
    ]
    project = (ROOT / "pyproject.toml").read_text()
    optional = project.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    extras = set(re.findall(r"^([a-z][a-z-]*)\s*=", optional, re.MULTILINE))
    for path in paths:
        text = path.read_text()
        assert "/mnt/" not in text and "/home/" not in text, path
        for requested in re.findall(r"\.\[([a-z,-]+)\]", text):
            assert set(requested.split(",")) <= extras, (path, requested)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(("https:", "http:", "mailto:", "#")):
                continue
            local = target.split("#", 1)[0]
            assert (path.parent / local).exists(), (path, target)
    scenario = (ROOT / "scenarios/demos/traffic-accident/scenario.yaml").read_text()
    assert "browser_executable:" not in scenario
