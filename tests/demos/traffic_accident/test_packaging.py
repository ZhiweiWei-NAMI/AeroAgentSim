"""The installed demo resolves its own data outside the repository layout."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_built_package_contains_demo_resources(tmp_path: Path) -> None:
    built = tmp_path / "installed"
    subprocess.run(
        [sys.executable, "setup.py", "build_py", "--build-lib", str(built)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    check = """
import json
from pathlib import Path
from aeroagentsim.authoring.templates import demo_source
from aeroagentsim.scenario import load_scenario
source = demo_source('traffic-accident')
assert 'demo_data' in source.parts, source
assert (source / 'inputs/lite-city/scene.json').is_file()
assert (source / 'fixtures/decisions.json').is_file()
assert (source / 'profiles/kinematic.yaml').is_file()
assert (source / 'prompts/manifest.json').is_file()
scenario = load_scenario(source / 'scenario.yaml')
assert len(scenario.manifest.entities) > 0
print(source)
"""
    environment = dict(os.environ, PYTHONPATH=str(built))
    environment.pop("AEROAGENTSIM_AEROGRAPH_ROOT", None)
    result = subprocess.run(
        [sys.executable, "-c", check],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert str(built) in result.stdout
