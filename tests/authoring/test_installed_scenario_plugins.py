"""Exercise real wheels and entry-point metadata without checkout fallbacks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/plugins/aeroagentsim-example-scenario"
BUILTINS = (
    "air-taxi", "city-day", "crop-spraying", "flood-rescue", "food-delivery",
    "medical-delivery", "parcel-delivery", "powerline-inspection",
    "security-patrol", "traffic-accident",
)

# -I ignores PYTHONPATH and excludes cwd/user site. Explicitly admit only the
# target from PYTHONPATH, retaining the interpreter's installed dependencies.
# Also remove checkout paths added by legacy editable-install .pth files.
CHECK_INSTALLATION = r"""
import hashlib
import json
import os
import sys
from importlib.metadata import distribution, entry_points
from pathlib import Path

site, checkout, manifest = map(Path, sys.argv[1:])
site, checkout = site.resolve(), checkout.resolve()
forbidden = (checkout / "src", checkout / "examples")
sys.path[:] = [
    entry for entry in sys.path
    if entry and Path(entry).resolve() != checkout
    and not any(Path(entry).resolve().is_relative_to(root) for root in forbidden)
]
assert os.environ["PYTHONPATH"] == str(site)
sys.path.insert(0, str(site))
assert not Path.cwd().is_relative_to(checkout), Path.cwd()
assert all(
    not Path(entry).resolve().is_relative_to(root)
    for entry in sys.path for root in forbidden
), sys.path

import aeroagentsim
import aeroagentsim_example_scenario
from aeroagentsim.authoring.templates import TemplateRegistry, demo_source
from aeroagentsim.scenarios import TEMPLATES

for module in (aeroagentsim, aeroagentsim_example_scenario):
    assert Path(module.__file__).resolve().is_relative_to(site), module.__file__
for name in ("aeroagentsim", "aeroagentsim-example-scenario"):
    assert Path(distribution(name).locate_file("")).resolve() == site, name
entries = [entry for entry in entry_points(group="aeroagentsim.scenarios")
           if entry.name == "example-patrol"]
assert len(entries) == 1, entries
assert entries[0].value == "aeroagentsim_example_scenario:plugin"
assert entries[0].dist.metadata["Name"] == "aeroagentsim-example-scenario"

expected = json.loads(manifest.read_text(encoding="utf-8"))
assert set(TEMPLATES) == set(expected["builtins"]), TEMPLATES

def check_resources():
    for relative, digest in expected["resources"].items():
        path = site / relative
        assert path.is_file(), path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path

check_resources()
registry = TemplateRegistry()
identifiers = {row["id"] for row in registry.list()}
required = {*expected["builtins"], "example-patrol"}
assert required <= identifiers, (required - identifiers, identifiers)
for identifier in sorted(required):
    assert registry.plugin_of(identifier) == identifier, identifier
    directory = registry.directory_of(identifier)
    package = (site / "aeroagentsim_example_scenario/templates"
               if identifier == "example-patrol"
               else site / "aeroagentsim/template_data")
    assert directory == package / identifier, (identifier, directory)
    prepared = registry.apply(identifier)
    base = (site / "aeroagentsim/demo_data/traffic-accident"
            if identifier == "traffic-accident" else directory)
    assert prepared.base == base, (identifier, prepared.base)
    assert prepared.document["id"] == identifier, identifier
    assert prepared.knobs == {
        knob["id"]: knob["default"] for knob in registry.get(identifier)["knobs"]
    }, identifier
    assert (base / "scenario.yaml").is_file(), base
    assert (base / "registry.snapshot.json").is_file(), base
    if identifier != "example-patrol":
        assert (directory / "README.md").is_file(), directory
        assert (directory / registry.get(identifier)["thumbnail"]).is_file()
assert demo_source("traffic-accident") == site / "aeroagentsim/demo_data/traffic-accident"
# Exercise installed external and shared hooks, not just unchanged defaults.
changed = registry.apply(
    "example-patrol", {"patrol_interval_s": 60, "run_mode": "interactive"}
)
patrol = next(
    generator for generator in changed.document["engines"]["tasks"]["config"]["generators"]
    if generator["kind"] == "patrol"
)
assert patrol["arrivals"]["times_ns"] == [0, 60_000_000_000, 120_000_000_000]
operator = next(
    stream for stream in changed.document["ingress_streams"]
    if stream["id"] == "operator"
)
assert operator["initial_watermark_ns"] == 0
assert "timeout_s" not in operator
# Every input remains byte-identical after discovery and all hooks.
check_resources()
for name, module in tuple(sys.modules.items()):
    if name == "aeroagentsim" or name.startswith("aeroagentsim."):
        assert Path(module.__file__).resolve().is_relative_to(site), name
print("installed templates applied: " + ", ".join(sorted(required)))
"""


def _run(
    command: list[str], cwd: Path, env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180,
    )


def _log(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + "\n" + result.stderr


def _wheel(source: Path, destination: Path, env: dict[str, str]) -> Path:
    destination.mkdir()
    command = [
        sys.executable, "-m", "pip", "--disable-pip-version-check", "--no-input",
        "wheel", "--no-deps", "--wheel-dir", str(destination), ".",
    ]
    isolated = _run(command, source, env)
    if isolated.returncode:
        # Fall back only for dependency acquisition failures, not errors in our
        # backend hooks: a local success must not hide an isolated regression.
        output = _log(isolated).lower()
        acquisition_errors = (
            "install build dependencies did not run successfully",
            "install backend dependencies did not run successfully",
            "installing build dependencies did not run successfully",
            "installing backend dependencies did not run successfully",
            "installing build dependencies: finished with status 'error'",
            "installing backend dependencies: finished with status 'error'",
            "installing build dependencies ... error",
            "installing backend dependencies ... error",
        )
        # An isolated backend's own `import pip` error is a backend regression,
        # not evidence that the outer interpreter cannot invoke pip at all.
        pip_missing = (
            f"{sys.executable}: No module named pip"
            in isolated.stderr.splitlines()
        )
        if not pip_missing and not any(
            message in output for message in acquisition_errors
        ):
            pytest.fail(
                "Isolated scenario-plugin wheel build failed:\n" + _log(isolated)
            )
        local = _run([*command, "--no-build-isolation"], source, env)
        if local.returncode:
            logs = (
                _log(isolated) + "\n--- no-build-isolation retry ---\n" + _log(local)
            )
            # Confirm tool absence independently instead of treating arbitrary
            # backend exception text as evidence that an offline skip is safe.
            tools = _run(
                [
                    sys.executable, "-c",
                    "import importlib.util, json; print(json.dumps([name for name "
                    "in ('pip', 'setuptools', 'wheel') "
                    "if importlib.util.find_spec(name) is None]))",
                ],
                source,
                env,
            )
            assert tools.returncode == 0, _log(tools)
            missing = json.loads(tools.stdout)
            tool_errors = {
                "pip": ("No module named pip", "No module named 'pip'"),
                "setuptools": (
                    "No module named 'setuptools'",
                    "Cannot import 'setuptools.build_meta'",
                ),
                "wheel": (
                    "No module named 'wheel'", "invalid command 'bdist_wheel'",
                ),
            }
            unavailable = [
                name for name in missing
                if any(message in _log(local) for message in tool_errors[name])
            ]
            if unavailable:
                pytest.skip(
                    "Cannot build scenario-plugin wheels offline: missing local "
                    + ", ".join(unavailable)
                    + " after build-dependency acquisition and the "
                    "--no-build-isolation fallback failed.\n" + logs
                )
            pytest.fail("Scenario-plugin wheel build failed:\n" + logs)
    wheels = list(destination.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def _resources(source: Path, prefix: str) -> dict[str, str]:
    return {
        f"{prefix}/{path.relative_to(source).as_posix()}": hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(source.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo", ".pyd"}
    }


def _assert_wheel_resources(wheel: Path, expected: dict[str, str]) -> None:
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert expected.keys() <= names, (wheel, expected.keys() - names)
        for name, digest in expected.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest, name


@pytest.mark.slow
def test_installed_scenario_plugins(tmp_path: Path) -> None:
    assert not tmp_path.resolve().is_relative_to(ROOT), (
        "The installed-wheel gate needs a temporary directory outside the checkout"
    )
    environment = dict(os.environ)
    for key in (
        "PYTHONPATH", "PYTHONHOME", "AEROAGENTSIM_AEROGRAPH_ROOT",
        "PIP_TARGET", "PIP_PREFIX", "PIP_USER",
    ):
        environment.pop(key, None)
    environment.update(PIP_RETRIES="0", PIP_TIMEOUT="15")

    # Stage clean build inputs: do not reuse build/ or *.egg-info from an earlier
    # build, nor leave generated packaging files in the working checkout.
    project = tmp_path / "project"
    project.mkdir()
    ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.pyo", "*.pyd", "*.egg-info", "build", "dist",
    )
    for directory in ("src", "scenarios"):
        shutil.copytree(ROOT / directory, project / directory, ignore=ignore)
    for filename in (
        "pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE",
    ):
        shutil.copy2(ROOT / filename, project / filename)
    example = tmp_path / "example"
    shutil.copytree(EXAMPLE, example, ignore=ignore)

    platform_resources = {
        **_resources(ROOT / "scenarios/templates", "aeroagentsim/template_data"),
        **_resources(
            ROOT / "scenarios/demos/traffic-accident",
            "aeroagentsim/demo_data/traffic-accident",
        ),
    }
    example_resources = _resources(
        EXAMPLE / "src/aeroagentsim_example_scenario/templates",
        "aeroagentsim_example_scenario/templates",
    )
    platform_wheel = _wheel(project, tmp_path / "platform-wheels", environment)
    example_wheel = _wheel(example, tmp_path / "example-wheels", environment)
    _assert_wheel_resources(platform_wheel, platform_resources)
    _assert_wheel_resources(example_wheel, example_resources)

    site = tmp_path / "site"
    outside = tmp_path / "outside"
    outside.mkdir()
    installed = _run(
        [
            sys.executable, "-m", "pip", "--disable-pip-version-check", "--no-input",
            "install", "--no-index", "--no-deps", "--no-compile", "--target", str(site),
            str(platform_wheel), str(example_wheel),
        ],
        outside,
        environment,
    )
    assert installed.returncode == 0, _log(installed)
    manifest = tmp_path / "resources.json"
    manifest.write_text(
        json.dumps({
            "builtins": BUILTINS,
            "resources": {**platform_resources, **example_resources},
        }),
        encoding="utf-8",
    )
    checked = _run(
        [
            sys.executable, "-I", "-c", CHECK_INSTALLATION,
            str(site), str(ROOT), str(manifest),
        ],
        outside,
        dict(environment, PYTHONPATH=str(site)),
    )
    assert checked.returncode == 0, _log(checked)
    assert "installed templates applied:" in checked.stdout, _log(checked)
