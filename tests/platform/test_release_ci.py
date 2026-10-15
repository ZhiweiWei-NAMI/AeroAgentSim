"""Publication waits for public runtime, replay and kernel verification."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_public_release_gates_are_unconditional_and_publish_waits() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())
    # PyYAML's YAML 1.1 parser reads the GitHub Actions `on` key as True.
    assert "workflow_call" in workflow[True]
    public = workflow["jobs"]["non-docker"]
    assert "if" not in public
    commands = "\n".join(step.get("run", "") for step in public["steps"])
    for suite in (
        "tests/behaviours",
        "tests/observations",
        "tests/platform",
    ):
        assert suite in commands
    assert "../aerokernel" not in commands
    kernel = next(
        step for step in public["steps"] if step.get("name") == "Kernel test suite"
    )
    assert kernel["working-directory"] == "platform/aerokernel"
    assert "python -m pytest" in kernel["run"]
    assert "./aerokernel[test]" in commands
    assert "/mnt/" not in commands
    source_case = "tests/assistant/test_blank_projects.py::test_blank_centre_charging_station_compiles_validates_and_applies[aerograph]"
    assert f"--deselect={source_case}" in commands
    source_commands = "\n".join(step.get("run", "") for step in workflow["jobs"]["aerograph"]["steps"])
    assert "tests/assistant/test_blank_projects.py" in source_commands
    publish = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())[
        "jobs"
    ]
    assert publish["tests"]["uses"] == "./.github/workflows/tests.yml"
    assert publish["deploy"]["needs"] == "tests"
