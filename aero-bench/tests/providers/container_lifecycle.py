"""Bounded Docker lifecycle helpers for provider container integration tests.

Every Docker invocation carries an explicit subprocess timeout, so a stalled
daemon surfaces as a failed command instead of hanging the test process, and
the owning ``owned_container`` context manager's finally still removes the
container. Commands are argument lists without shell pipelines. Each test
addresses the exact container it creates by unique name and closes with a
residue assertion proving the daemon holds no such object — including when
``docker run`` itself fails or times out after the daemon has created the
named container.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager


CONTAINER_PREFIX = "aero-it-container-"


class DockerCommandError(AssertionError):
    """A bounded Docker command failed or its timeout expired."""


def docker(
    *arguments: str, timeout: float, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("docker", *arguments), capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode != 0:
        raise DockerCommandError(
            f"docker {' '.join(arguments)} failed "
            f"(rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def fresh_container_name(label: str) -> str:
    return f"{CONTAINER_PREFIX}{label}-{uuid.uuid4().hex[:12]}"


def remove_leftover_containers(label: str, *, timeout: float = 60.0) -> None:
    """Reap containers abandoned by a previously killed run of this test.

    A hard kill of the test process cannot run try/finally, so each test
    start reaps its own label namespace before creating a new container;
    an orphan from a crashed run cannot survive into the next run.
    """

    listing = docker(
        "ps",
        "-a",
        "--filter",
        f"name=^/{CONTAINER_PREFIX}{label}-",
        "--format",
        "{{.ID}} {{.Names}}",
        timeout=timeout,
    ).stdout.splitlines()
    for line in listing:
        container_id = line.split(maxsplit=1)[0]
        docker("rm", "-f", container_id, timeout=timeout)


@contextmanager
def owned_container(
    label: str,
    *run_arguments: str,
    run_timeout: float,
    command_timeout: float = 60.0,
) -> Iterator[str]:
    """Yield the name of a detached container this helper always removes.

    ``docker run`` executes inside the try, so a command failure or
    subprocess timeout after the daemon has already created the named
    container still reaches the finally, which removes that exact container
    and proves no residue remains. The same guarantee covers failures raised
    by the test body between creation and cleanup.
    """

    name = fresh_container_name(label)
    remove_leftover_containers(label, timeout=command_timeout)
    try:
        docker(
            "run",
            "--detach",
            "--name",
            name,
            *run_arguments,
            timeout=run_timeout,
        )
        yield name
    finally:
        remove_container(name, timeout=command_timeout)
        assert_no_residue(name, timeout=command_timeout)


def remove_container(name: str, *, timeout: float = 60.0) -> None:
    docker("rm", "-f", name, timeout=timeout, check=False)


def assert_no_residue(name: str, *, timeout: float = 30.0) -> None:
    listing = docker(
        "ps",
        "-a",
        "--filter",
        f"name=^{name}$",
        "--format",
        "{{.ID}}",
        timeout=timeout,
    ).stdout.strip()
    assert listing == "", f"orphan Docker container left behind: {name}"


def wait_for_exit(
    name: str, *, deadline_seconds: float = 60.0, timeout: float = 30.0
) -> int:
    """Poll the container state until it exits; never blocks on docker wait."""

    deadline = time.monotonic() + deadline_seconds
    while True:
        state = docker(
            "inspect",
            "--format",
            "{{.State.Running}} {{.State.ExitCode}}",
            name,
            timeout=timeout,
        ).stdout.strip()
        running, _, exit_code = state.partition(" ")
        if running == "false":
            return int(exit_code)
        if time.monotonic() >= deadline:
            raise DockerCommandError(
                f"container {name} did not exit within {deadline_seconds:.0f}s"
            )
        time.sleep(0.2)
