"""K7 run-level provenance: lean default, strict validation, full explicit audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel.journal import iter_records, replay

from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.scenario import ScenarioError, load_scenario
from aeroagentsim.services.cli import main

SCENARIO = Path("scenarios/predicates-demo.yaml")


def document() -> dict[str, Any]:
    return copy.deepcopy(load_scenario(SCENARIO).document)


def header(path: Path) -> dict[str, Any]:
    return next(iter_records(path / "journal.jsonl"))


def test_scenario_provenance_defaults_to_lean_and_kernel_header_pins_it(
    tmp_path: Path,
) -> None:
    with RunSession(load_scenario(document()), tmp_path / "lean") as session:
        assert session.simulation.kernel.provenance == "lean"
    lean = header(tmp_path / "lean")
    assert lean["provenance"] == "lean"
    assert (lean["major"], lean["minor"], lean["semantic_version"]) == (2, 1, 4)


def test_scenario_provenance_full_restores_audit_semantics(tmp_path: Path) -> None:
    doc = document()
    doc["provenance"] = "full"
    with RunSession(load_scenario(doc), tmp_path / "full") as session:
        assert session.simulation.kernel.provenance == "full"
    full = header(tmp_path / "full")
    assert "provenance" not in full  # Historical full headers stay unchanged.
    # This offline scenario retains full 2.0 with its historical policy version 2.
    assert (full["major"], full["minor"], full["semantic_version"]) == (2, 0, 2)


@pytest.mark.parametrize("level", ["lean", "full"])
def test_simulation_override_wins_and_replays(level: str, tmp_path: Path) -> None:
    sim = Simulation(load_scenario(document()), provenance=level)
    try:
        sim.start()
        sim.run_until(1_000_000_000)
        assert sim.kernel.provenance == level
        recovered = replay(sim.kernel.journal.bytes)
        assert recovered.provenance == level
        assert not recovered.incomplete
    finally:
        sim.close()


def test_run_session_override_pinned_in_effective_run_manifest(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(document())
    original = copy.deepcopy(scenario.document)
    with RunSession(scenario, tmp_path / "run", provenance="full") as session:
        assert session.simulation.kernel.provenance == "full"
    assert scenario.document == original
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert manifest["provenance"] == "full"
    assert "provenance" not in header(tmp_path / "run")
    assert (
        json.loads((tmp_path / "run" / "scenario.json").read_text())["provenance"]
        == "full"
    )


def test_invalid_provenance_is_rejected_without_coercion() -> None:
    for bad in ("audit", "LEAN", "", None, True, 1):
        doc = document()
        doc["provenance"] = bad
        with pytest.raises(ScenarioError, match="provenance"):
            load_scenario(doc)
    with pytest.raises(ScenarioError, match="provenance"):
        Simulation(load_scenario(document()), provenance="audit")


def test_cli_run_provenance_flag_overrides_scenario(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from unittest.mock import patch

    with patch(
        "sys.argv",
        [
            "aeroagentsim",
            "run",
            str(SCENARIO),
            "--out",
            str(tmp_path),
            "--provenance",
            "full",
        ],
    ):
        main()
    directory = Path(json.loads(capsys.readouterr().out)["run"])
    assert "provenance" not in header(directory)
    assert json.loads((directory / "manifest.json").read_text())["provenance"] == "full"
