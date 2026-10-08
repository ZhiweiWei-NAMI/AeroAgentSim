"""E1 Docker container lifecycle helper.

This module owns the lifecycle of exactly one simulation container for the
platform adapter milestone E1: it runs an allowlisted image detached with a
fixed CPU budget and a dynamic host port forwarding to container port 9000,
waits for readiness, exposes the discovered ``host:port`` endpoint, and
removes the container again on exit or on startup failure.

All container access goes through the ``docker`` CLI invoked with argument
lists (never a shell) and per-call timeouts. Readiness uses the Docker
health status when the image defines a healthcheck, and otherwise inspects the native TCP listener through /proc without opening an RPC owner
session. Cleanup removes only the
container id this instance created, and only after a fresh ``docker
inspect`` re-confirmed the ``aeroagentsim.job=e1`` label on it.

Errors are strict: startup failures raise :class:`ContainerStartupError`
including the container log tail, and cleanup failures are never silently
swallowed. Actual integration runs against the real docker CLI are owned
by the parent workflow; unit tests cover this module with subprocess and
socket mocks only and never start a real container.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from collections.abc import Mapping
from types import TracebackType
from typing import NoReturn, TypedDict, cast

__all__ = [
    "ContainerError",
    "ContainerStartupError",
    "DockerContainer",
    "ImageNotAllowedError",
]

#: Images this helper is allowed to run; anything else is rejected up front.
_ALLOWED_IMAGES: frozenset[str] = frozenset(
    {
        "aeroagentsim/px4-gazebo:dev-p2b",
        "aeroagentsim/sumo:dev-p3a-5",
        "aeroagentsim/ns3:dev-p4b",
    }
)

_DOCKER_BINARY = "docker"
_JOB_LABEL_KEY = "aeroagentsim.job"
_JOB_LABEL_VALUE = "e1"
_CPUS = "8"
_CONTAINER_PORT = 9000
_PUBLISH_TARGET = f"127.0.0.1::{_CONTAINER_PORT}"
_RUN_TIMEOUT_S = 60.0
_INSPECT_TIMEOUT_S = 10.0
_LOGS_TIMEOUT_S = 10.0
_REMOVE_TIMEOUT_S = 30.0
_POLL_INTERVAL_S = 0.1
_PROBE_TIMEOUT_S = 0.5
_LOG_TAIL_LINES = 100


class ContainerError(RuntimeError):
    """Base class for all docker container lifecycle failures."""


class ContainerStartupError(ContainerError):
    """Raised when a container fails to start or to become ready in time."""


class ImageNotAllowedError(ContainerError):
    """Raised when an image outside the E1 allowlist is requested."""


class _PortBinding(TypedDict, total=False):
    HostIp: str
    HostPort: str


class _HealthState(TypedDict, total=False):
    Status: str


class _InspectState(TypedDict, total=False):
    Status: str
    Running: bool
    ExitCode: int
    Health: _HealthState | None


class _InspectConfig(TypedDict, total=False):
    Image: str
    Labels: dict[str, str]


class _InspectNetworkSettings(TypedDict, total=False):
    Ports: dict[str, list[_PortBinding] | None]


class _InspectEntry(TypedDict, total=False):
    Id: str
    State: _InspectState
    Config: _InspectConfig
    NetworkSettings: _InspectNetworkSettings


def _execute(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run a docker CLI command with a hard timeout and no shell."""
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise ContainerError(
            f"docker CLI {_DOCKER_BINARY!r} is not available: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ContainerError(
            f"docker command timed out after {timeout:g}s: {' '.join(command)}"
        ) from exc


def _build_run_command(
    image: str,
    name: str,
    environment: Mapping[str, str],
    mounts: Mapping[str, str],
) -> list[str]:
    """Build the ``docker run`` argv for one detached E1 container."""
    command = [
        _DOCKER_BINARY,
        "run",
        "--detach",
        "--label",
        f"{_JOB_LABEL_KEY}={_JOB_LABEL_VALUE}",
        "--cpus",
        _CPUS,
        "--publish",
        _PUBLISH_TARGET,
        "--name",
        name,
    ]
    for key in sorted(environment):
        command += ["--env", f"{key}={environment[key]}"]
    for host_path in sorted(mounts):
        command += ["--volume", f"{host_path}:{mounts[host_path]}"]
    command.append(image)
    return command


def _parse_inspect(stdout: str, container_id: str) -> _InspectEntry:
    """Parse the single-entry JSON document emitted by ``docker inspect``."""
    try:
        payload: object = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ContainerError(
            f"docker inspect returned invalid JSON for container {container_id}: {exc}"
        ) from exc
    if not isinstance(payload, list) or len(payload) != 1:
        raise ContainerError(
            f"docker inspect returned an unexpected payload for container {container_id}"
        )
    entry = payload[0]
    if not isinstance(entry, dict):
        raise ContainerError(
            f"docker inspect entry for container {container_id} is not an object"
        )
    return cast(_InspectEntry, entry)


def _inspect(container_id: str) -> _InspectEntry:
    """Fetch and parse the inspect document for one container id."""
    command = [_DOCKER_BINARY, "inspect", "--type", "container", container_id]
    completed = _execute(command, timeout=_INSPECT_TIMEOUT_S)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ContainerError(
            f"docker inspect failed for container {container_id} "
            f"(exit {completed.returncode}): {detail}"
        )
    return _parse_inspect(completed.stdout, container_id)


def _host_binding(entry: _InspectEntry) -> tuple[str, int] | None:
    """Return the mapped ``(host, port)`` for container port 9000, if any."""
    network = entry.get("NetworkSettings")
    if network is None:
        return None
    ports = network.get("Ports")
    if not ports:
        return None
    bindings = ports.get(f"{_CONTAINER_PORT}/tcp")
    if not bindings:
        return None
    binding = bindings[0]
    host_ip = binding.get("HostIp", "")
    host_port = binding.get("HostPort", "")
    if not host_ip or not host_port:
        return None
    return host_ip, int(host_port)


def _listener_ready(container_id: str) -> bool:
    """Inspect the native TCP listener without occupying the single-owner RPC.

    Connecting a host probe races the next real hello while the previous owner
    is closing. A read-only /proc probe in our container avoids that race.
    """
    script = (
        "from pathlib import Path; import sys; "
        "rows=Path('/proc/net/tcp').read_text().splitlines()[1:]; "
        "ready=any(r.split()[1].split(':')[1]=='2328' and r.split()[3]=='0A' for r in rows); "
        "print('listening' if ready else 'waiting'); sys.exit(0 if ready else 1)"
    )
    result = _execute(
        [_DOCKER_BINARY, "exec", container_id, "python3", "-c", script],
        timeout=_PROBE_TIMEOUT_S + 5,
    )
    if result.returncode == 0 and result.stdout.strip() == "listening":
        return True
    if result.returncode == 1 and result.stdout.strip() == "waiting":
        return False
    raise ContainerError(
        f"native listener probe failed: {result.stderr or result.stdout}"
    )


def _await_ready(container_id: str, timeout_s: float) -> tuple[str, int]:
    """Poll inspect until the container reports a usable endpoint.

    Docker health status wins when the image defines a healthcheck;
    otherwise the container must expose a native listening socket.
    """
    deadline = time.monotonic() + timeout_s
    detail = "no readiness observation recorded"
    while True:
        entry = _inspect(container_id)
        state = entry.get("State")
        if state is None:
            raise ContainerError(
                f"docker inspect output for container {container_id} lacks State"
            )
        if state.get("Running") is not True:
            raise ContainerStartupError(
                f"container {container_id} is not running "
                f"(status={state.get('Status')!r}, exit code={state.get('ExitCode')})"
            )
        binding = _host_binding(entry)
        health = state.get("Health")
        if health is None:
            if binding is not None and _listener_ready(container_id):
                return binding
            if binding is None:
                detail = (
                    f"container port {_CONTAINER_PORT}/tcp has no published host "
                    "binding yet"
                )
            else:
                detail = f"TCP probe on {binding[0]}:{binding[1]} was not accepted"
        else:
            status = health.get("Status", "unknown")
            if status == "healthy":
                if binding is None:
                    raise ContainerStartupError(
                        f"container {container_id} is healthy but port "
                        f"{_CONTAINER_PORT}/tcp has no published host binding"
                    )
                return binding
            if status == "unhealthy":
                raise ContainerStartupError(
                    f"container {container_id} reports docker health status unhealthy"
                )
            detail = f"docker health status is {status!r}"
        if time.monotonic() >= deadline:
            raise ContainerStartupError(
                f"container {container_id} did not become ready within "
                f"{timeout_s:g}s (last observation: {detail})"
            )
        time.sleep(_POLL_INTERVAL_S)


def _verify_ownership(entry: _InspectEntry, container_id: str) -> None:
    """Confirm the inspect entry really describes our own labeled container."""
    observed_id = entry.get("Id", "")
    if not observed_id.startswith(container_id):
        raise ContainerError(
            f"refusing to remove container {container_id}: inspect returned id "
            f"{observed_id!r}"
        )
    config = entry.get("Config")
    labels = config.get("Labels") if config is not None else None
    observed_label = labels.get(_JOB_LABEL_KEY) if labels else None
    if observed_label != _JOB_LABEL_VALUE:
        raise ContainerError(
            f"refusing to remove container {container_id}: label "
            f"{_JOB_LABEL_KEY}={_JOB_LABEL_VALUE!r} is missing or mismatched "
            f"(observed labels: {labels!r})"
        )


class DockerContainer:
    """Context-manager lifecycle helper for one allowlisted E1 container.

    ``__enter__`` runs the image detached, discovers the mapped host port,
    and waits for readiness (docker health when available, otherwise a
    passive native listener probe). ``__exit__`` force-removes the container and
    never suppresses the body exception; a cleanup failure surfaces as
    :class:`ContainerError`. A startup failure cleans the container up and
    raises :class:`ContainerStartupError` with the container log tail
    attached.

    Example:
        with DockerContainer("aeroagentsim/px4-gazebo:dev-p2b") as sim:
            host, port = sim.endpoint
    """

    def __init__(
        self,
        image: str,
        *,
        startup_timeout_s: float = 30.0,
        environment: dict[str, str] | None = None,
        mounts: dict[str, str] | None = None,
    ) -> None:
        if image not in _ALLOWED_IMAGES:
            raise ImageNotAllowedError(
                f"image {image!r} is not allowlisted; expected one of "
                f"{sorted(_ALLOWED_IMAGES)}"
            )
        if startup_timeout_s <= 0:
            raise ValueError("startup_timeout_s must be positive")
        self._image = image
        self._startup_timeout_s = float(startup_timeout_s)
        self._environment: dict[str, str] = (
            dict(environment) if environment is not None else {}
        )
        self._mounts: dict[str, str] = dict(mounts) if mounts is not None else {}
        self._name = f"aeroagentsim-e1-{uuid.uuid4().hex[:12]}"
        self._id: str | None = None
        self._endpoint: tuple[str, int] | None = None
        self._closed = False

    def __repr__(self) -> str:
        if self._closed:
            state = "closed"
        elif self._endpoint is not None:
            state = "ready"
        elif self._id is not None:
            state = "starting"
        else:
            state = "new"
        return f"<DockerContainer {self._name!r} {self._image!r} {state}>"

    @property
    def image(self) -> str:
        """Allowlisted image this container runs."""
        return self._image

    @property
    def name(self) -> str:
        """Unique docker container name chosen for this instance."""
        return self._name

    @property
    def container_id(self) -> str:
        """Docker container id, available only after a successful start."""
        if self._id is None:
            raise ContainerError(f"container {self._name} has not been started")
        return self._id

    @property
    def endpoint(self) -> tuple[str, int]:
        """Mapped ``(host, port)`` endpoint, available only once ready."""
        if self._endpoint is None:
            raise ContainerError(
                f"container {self._name} has no endpoint before a successful start"
            )
        return self._endpoint

    def __enter__(self) -> DockerContainer:  # noqa: PYI034 - Python 3.10 runtime
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def start(self) -> None:
        """Run the container detached and wait until it is ready.

        On any readiness failure the container is removed again before
        :class:`ContainerStartupError` is raised with the log tail attached.
        """
        if self._closed:
            raise ContainerError(f"container {self._name} is already closed")
        if self._id is not None:
            raise ContainerError(f"container {self._name} is already started")
        self._id = self._run_daemon()
        try:
            self._endpoint = _await_ready(self._id, self._startup_timeout_s)
        except Exception as exc:  # noqa: BLE001 - clean up then report original fault
            self._fail_startup(exc)

    def close(self) -> None:
        """Force-remove the container; calling this repeatedly is a no-op.

        Removal happens only after a fresh inspect confirmed the container
        id and the ``aeroagentsim.job=e1`` label. Failures raise
        :class:`ContainerError` and leave the container un-removed, so a
        retry can surface the same failure again instead of hiding it.
        """
        if self._closed:
            return
        container_id = self._id
        if container_id is None:
            self._closed = True
            return
        entry = _inspect(container_id)
        _verify_ownership(entry, container_id)
        command = [_DOCKER_BINARY, "rm", "--force", container_id]
        completed = _execute(command, timeout=_REMOVE_TIMEOUT_S)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ContainerError(
                f"failed to remove container {container_id} "
                f"(exit {completed.returncode}): {detail}"
            )
        self._closed = True

    def _run_daemon(self) -> str:
        command = _build_run_command(
            self._image, self._name, self._environment, self._mounts
        )
        completed = _execute(command, timeout=_RUN_TIMEOUT_S)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ContainerStartupError(
                f"docker run failed for image {self._image!r} "
                f"(exit {completed.returncode}): {detail}"
            )
        container_id = completed.stdout.strip()
        if not container_id:
            raise ContainerStartupError(
                f"docker run returned an empty container id for {self._name}"
            )
        return container_id

    def _tail_logs(self) -> str:
        container_id = self._id
        if container_id is None:
            return "<container id unknown; no logs fetched>"
        command = [
            _DOCKER_BINARY,
            "logs",
            "--tail",
            str(_LOG_TAIL_LINES),
            container_id,
        ]
        completed = _execute(command, timeout=_LOGS_TIMEOUT_S)
        output = (completed.stdout + completed.stderr).strip()
        return output or "<no container logs>"

    def _fail_startup(self, exc: Exception) -> NoReturn:
        """Attach diagnostics, clean up, and re-raise without swallowing."""
        try:
            logs = self._tail_logs()
        except Exception as log_exc:  # noqa: BLE001 - retain diagnostic failure
            logs = f"<fetching container logs failed: {log_exc!r}>"
        container_id = self._id or "<unknown>"
        message = (
            f"container {self._name} ({container_id}) failed to become ready: {exc}"
        )
        message += f"\n--- container log tail ({_LOG_TAIL_LINES} lines) ---\n{logs}"
        try:
            self.close()
        except Exception as cleanup_exc:
            message += f"\nadditionally, cleanup failed: {cleanup_exc!r}"
            raise ContainerStartupError(message) from cleanup_exc
        raise ContainerStartupError(message) from exc
