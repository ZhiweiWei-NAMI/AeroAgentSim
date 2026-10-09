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
        "tests/platform/test_realtime.py",
    ):
        assert suite in commands
    assert "--ignore=tests/behaviours/test_compat.py" in commands
    assert "# RELEASE: kernel path" in commands and "kernel=./aerokernel" in commands
    kernel = next(
        step for step in public["steps"] if step.get("name") == "Kernel test suite"
    )
    assert 'cd "$kernel"' in kernel["run"] and "python -m pytest" in kernel["run"]
    assert "$kernel[test]" in commands
    assert "/mnt/data2" not in commands
    publish = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())[
        "jobs"
    ]
    assert publish["tests"]["uses"] == "./.github/workflows/tests.yml"
    assert publish["deploy"]["needs"] == "tests"
