"""Non-Docker unit tests for the bounded container ownership guarantee.

The container integration tests must remove the exact container they named
even when ``docker run`` fails or times out after the daemon has already
created that container. These tests replace the Docker command boundary
with a recording fake, so no daemon is contacted, and prove that removal
and the residue assertion are invoked on every outcome.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.providers import container_lifecycle


class _RecordingDocker:
    """Stands in for container_lifecycle.docker and records every command."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.fail_on: str | None = None
        self.error: Exception | None = None

    def __call__(
        self, *arguments: str, timeout: float, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(arguments)
        if self.fail_on is not None and arguments[0] == self.fail_on:
            assert self.error is not None
            raise self.error
        return subprocess.CompletedProcess(
            ("docker", *arguments), returncode=0, stdout="", stderr=""
        )

    def verbs(self) -> list[str]:
        return [arguments[0] for arguments in self.commands]

    def run_names(self) -> list[str]:
        return [
            arguments[arguments.index("--name") + 1]
            for arguments in self.commands
            if arguments[0] == "run"
        ]

    def removed_names(self) -> list[str]:
        return [
            arguments[2] for arguments in self.commands if arguments[:2] == ("rm", "-f")
        ]

    def residue_filters(self) -> list[str]:
        return [
            arguments[3]
            for arguments in self.commands
            if arguments[:3] == ("ps", "-a", "--filter")
        ]


def _install(monkeypatch: pytest.MonkeyPatch) -> _RecordingDocker:
    recording = _RecordingDocker()
    monkeypatch.setattr(container_lifecycle, "docker", recording)
    return recording


def test_run_timeout_still_removes_exact_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _install(monkeypatch)
    recording.fail_on = "run"
    recording.error = subprocess.TimeoutExpired(("docker", "run"), timeout=120.0)
    with pytest.raises(subprocess.TimeoutExpired):
        with container_lifecycle.owned_container(
            "scene", "--read-only", run_timeout=120.0, command_timeout=60.0
        ):
            raise AssertionError("body must be unreachable when docker run fails")
    [run_name] = recording.run_names()
    assert run_name.startswith(container_lifecycle.CONTAINER_PREFIX)
    assert recording.removed_names() == [run_name]
    assert f"name=^{run_name}$" in recording.residue_filters()
    assert recording.verbs() == ["ps", "run", "rm", "ps"]


def test_body_failure_still_removes_exact_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _install(monkeypatch)
    with pytest.raises(AssertionError, match="scenario failure"):
        with container_lifecycle.owned_container(
            "traffic", "--read-only", run_timeout=120.0, command_timeout=60.0
        ) as container_name:
            raise AssertionError("scenario failure")
    assert recording.removed_names() == [container_name]
    assert f"name=^{container_name}$" in recording.residue_filters()
    assert recording.verbs() == ["ps", "run", "rm", "ps"]


def test_success_path_removes_container_and_proves_no_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _install(monkeypatch)
    with container_lifecycle.owned_container(
        "scene", "--read-only", run_timeout=120.0, command_timeout=60.0
    ) as container_name:
        assert container_name.startswith(container_lifecycle.CONTAINER_PREFIX)
    assert recording.removed_names() == [container_name]
    assert f"name=^{container_name}$" in recording.residue_filters()
    assert "name=^/aero-it-container-scene-" in recording.residue_filters()
    assert recording.verbs() == ["ps", "run", "rm", "ps"]
