"""Targeted CLI coverage for the template registry surface."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from aeroagentsim.authoring.templates import TemplateRegistry, demo_source

REPO = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def generic_template(tmp_path: Path) -> Path:
    """A pinned, no-engine scenario registry derived from the demo snapshot."""
    root = tmp_path / "templates"
    source = root / "experiment"
    source.mkdir(parents=True)
    shutil.copyfile(
        demo_source("traffic-accident") / "registry.snapshot.json",
        source / "registry.snapshot.json",
    )
    document = {
        "format": "aeroagentsim.scenario/v1",
        "id": "experiment",
        "registry": {"snapshot": "registry.snapshot.json"},
        "entities": [],
        "bindings": {"exact": [], "lifecycle": []},
        "engines": {},
        "algorithms": {"allocation": "nearest"},
        "run": {
            "pacing": "fast",
            "seed": 1,
            "until_ns": 1_000_000_000,
            "advance_ns": 1_000_000_000,
        },
        "outputs": {"durability": "flush"},
    }
    (source / "scenario.yaml").write_text(yaml.safe_dump(document))
    metadata = {
        "format": "aeroagentsim.template/v1",
        "id": "experiment",
        "title": "Experiment",
        "summary": "Compare pinned allocation settings.",
        "category": "logistics",
        "difficulty": "beginner",
        "duration_s": 1,
        "scenario": "scenario.yaml",
        "observe": ["Inspect committed task counts.", "Compare run settings."],
        "try": ["Change the random seed.", "Switch the allocation algorithm."],
        "knobs": [
            {
                "id": "seed",
                "label": "Seed",
                "type": "integer",
                "default": 1,
                "min": 0,
                "max": 10,
                "pointer": "/run/seed",
            }
        ],
        "kpis": ["tasks_created", "tasks_completed"],
    }
    (source / "template.yaml").write_text(yaml.safe_dump(metadata))
    return root


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(PYTHON),
            "-m",
            "aeroagentsim.services.cli",
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={
            **os.environ,
            "PYTHONPATH": "aerokernel:src",
        },
        check=False,
    )


def test_templates_list_and_run_headless_measured(tmp_path: Path) -> None:
    root = generic_template(tmp_path)
    out = tmp_path / "runs"
    listed = run_cli("templates", "--templates-root", str(root))
    assert listed.returncode == 0, listed.stderr
    entries = json.loads(listed.stdout)
    assert [row["id"] for row in entries] == ["experiment"]

    done = run_cli(
        "run-template",
        "experiment",
        "--set",
        "seed=2",
        "--headless",
        "--out",
        str(out),
        "--templates-root",
        str(root),
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["status"] == "completed"
    assert Path(result["run"]).is_dir()
    assert result["kpis"]["tasks_created"] == 0
    metadata = json.loads((Path(result["run"]) / "manifest.json").read_text())
    assert metadata["template"]["knobs"] == {"seed": 2}
    scenario = yaml.safe_load((Path(result["run"]) / "scenario.json").read_text())
    assert scenario["run"]["seed"] == 2


def test_run_template_rejects_unknown_and_typed_knob(tmp_path: Path) -> None:
    root = generic_template(tmp_path)
    out = tmp_path / "runs"
    missing = run_cli(
        "run-template",
        "no-such",
        "--headless",
        "--out",
        str(out),
        "--templates-root",
        str(root),
    )
    assert missing.returncode != 0
    assert "no-such" in missing.stderr

    wrong = run_cli(
        "run-template",
        "experiment",
        "--set",
        "seed=true",
        "--headless",
        "--out",
        str(out),
        "--templates-root",
        str(root),
    )
    assert wrong.returncode != 0
    assert "seed" in wrong.stderr
    # The typed boolean parsed by UniqueLoader must be rejected by the registry.
    assert "boolean" in wrong.stderr or "integer" in wrong.stderr


def test_registry_pinned_snapshot_matches_demo_source() -> None:
    registry = TemplateRegistry()
    names = {row["id"] for row in registry.list()}
    assert "traffic-accident" in names
    prepared = registry.apply("traffic-accident", {"decision_mode": "scripted"})
    assert prepared.metadata["id"] == "traffic-accident"
    assert demo_source("traffic-accident") == prepared.base
