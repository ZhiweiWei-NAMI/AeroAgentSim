"""Unit tests for the docker container lifecycle helper.

These tests never talk to a real docker daemon: ``subprocess.run``, the
``socket`` module and ``time`` used by the module under test are replaced
with recording fakes, and the ``docker`` CLI argv is asserted shape by
shape. Integration against the real docker CLI is owned by the parent
workflow, not by this test module.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal, cast

import pytest

_MODULE_NAME = "aeroagentsim_adapters_container_under_test"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "src" / "aeroagentsim" / "adapters" / "container.py"

_CONTAINER_ID = "abcdef1234567890"
_LOG_SENTINEL = "NATIVE-LOG px4 starting"


def _load_container_module() -> Any:
    """Import the container module directly, without installing the package."""
    if not _MODULE_PATH.is_file():  # pragma: no cover - guards wrong checkout
        raise AssertionError(f"container module missing at {_MODULE_PATH}")
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib guard
        raise AssertionError(f"cannot build import spec for {_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


container: Any = _load_container_module()

_PX4_IMAGE = "aeroagentsim/px4-gazebo:dev"
_SUMO_IMAGE = "aeroagentsim/sumo:dev"
_NS3_IMAGE = "aeroagentsim/ns3:dev"
_ALL_ALLOWED_IMAGES = (_PX4_IMAGE, _SUMO_IMAGE, _NS3_IMAGE)

_EXPECTED_RUN_HEAD = [
    "docker",
    "run",
    "--detach",
    "--label",
    "aeroagentsim.job=default",
    "--cpus",
    "8",
    "--publish",
    "127.0.0.1::9000",
]


def _ports_binding(host_port: str = "54321") -> dict[str, Any]:
    return {"9000/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]}


def _entry(
    *,
    running: bool = True,
    status: str = "running",
    exit_code: int = 0,
    health: str | None = None,
    ports: dict[str, Any] | None = None,
    labels: dict[str, str] | None = None,
    container_id: str = _CONTAINER_ID,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "Status": status,
        "Running": running,
        "ExitCode": exit_code,
    }
    if health is not None:
        state["Health"] = {"Status": health, "FailingStreak": 0}
    return {
        "Id": container_id,
        "State": state,
        "Config": {
            "Image": _PX4_IMAGE,
            "Labels": dict(labels)
            if labels is not None
            else {"aeroagentsim.job": "default"},
        },
        "NetworkSettings": {"Ports": _ports_binding() if ports is None else ports},
    }


_ENTRY = json.dumps([_entry()])


class _NoDataConnection:
    """Context manager that fails the test if any payload is sent or read."""

    def __init__(self, factory: _FakeSocketFactory) -> None:
        self._factory = factory

    def __enter__(self) -> _NoDataConnection:  # noqa: PYI034 - py310 test helper
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        self._factory.closed_connections += 1
        return False

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"TCP probe attempted socket I/O: {name!r}")


class _FakeSocketFactory:
    """Stand-in for the ``socket`` module with only ``create_connection``."""

    def __init__(self, accept: bool) -> None:
        self.accept = accept
        self.calls: list[tuple[str, int]] = []
        self.closed_connections = 0

    def create_connection(
        self, address: tuple[str, int], timeout: float | None = None
    ) -> _NoDataConnection:
        self.calls.append((address[0], address[1]))
        if not self.accept:
            raise OSError("connection refused by fake")
        return _NoDataConnection(self)


class _FakeTime:
    """Stand-in for the ``time`` module with virtual monotonic clocks."""

    def __init__(self) -> None:
        self._now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds + 0.01


class FakeDocker:
    """Recording ``docker`` CLI fake used in place of ``subprocess.run``.

    The instance itself is patched into the module as the ``subprocess``
    namespace, so only calls issued by the code under test are recorded.
    ``run`` asserts the exact keyword contract (no ``shell``, hard
    ``timeout``) and dispatches per docker subcommand.
    """

    CompletedProcess: type[subprocess.CompletedProcess[str]] = (
        subprocess.CompletedProcess
    )
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(
        self,
        *,
        run_returncode: int = 0,
        run_stdout: str = _CONTAINER_ID + "\n",
        run_stderr: str = "",
        run_missing_binary: bool = False,
        inspect_payloads: list[str] | None = None,
        inspect_exhaust_fail: bool = False,
        logs_returncode: int = 0,
        logs_stdout: str = _LOG_SENTINEL,
        logs_fail: bool = False,
        rm_returncode: int = 0,
        rm_stderr: str = "",
        rm_fail: bool = False,
        tcp_accept: bool = True,
    ) -> None:
        self.run_returncode = run_returncode
        self.run_stdout = run_stdout
        self.run_stderr = run_stderr
        self.run_missing_binary = run_missing_binary
        self.inspect_payloads = (
            [_ENTRY] if inspect_payloads is None else inspect_payloads
        )
        self.inspect_exhaust_fail = inspect_exhaust_fail
        self.logs_returncode = logs_returncode
        self.logs_stdout = logs_stdout
        self.logs_fail = logs_fail
        self.rm_returncode = rm_returncode
        self.rm_stderr = rm_stderr
        self.rm_fail = rm_fail
        self.commands: list[list[str]] = []
        self.timeouts: list[float | None] = []
        self.run_count = 0
        self.inspect_count = 0
        self.logs_count = 0
        self.rm_count = 0
        self.time = _FakeTime()
        self.sockets = _FakeSocketFactory(tcp_accept)

    def run(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        assert all(isinstance(part, str) for part in command)
        assert set(kwargs) == {"capture_output", "text", "timeout", "check"}, kwargs
        assert isinstance(kwargs["timeout"], float)
        self.commands.append(list(command))
        self.timeouts.append(cast("float | None", kwargs["timeout"]))
        verb = command[1] if len(command) > 1 else ""
        if verb == "run":
            return self._run_daemon(command)
        if verb == "exec":
            self.sockets.calls.append(("127.0.0.1", 54321))
            return self.CompletedProcess(
                command,
                0 if self.sockets.accept else 1,
                stdout="listening\n" if self.sockets.accept else "waiting\n",
                stderr="",
            )
        if verb == "inspect":
            return self._inspect(command)
        if verb == "logs":
            return self._logs(command)
        if verb == "rm":
            return self._rm(command)
        raise AssertionError(f"unexpected docker subcommand: {verb!r}")

    def _run_daemon(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.run_count += 1
        if self.run_missing_binary:
            raise FileNotFoundError(2, "No such file or directory", "docker")
        return self.CompletedProcess(
            command,
            self.run_returncode,
            stdout=self.run_stdout,
            stderr=self.run_stderr,
        )

    def _inspect(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.inspect_count += 1
        index = self.inspect_count - 1
        if index >= len(self.inspect_payloads):
            if self.inspect_exhaust_fail:
                return self.CompletedProcess(
                    command, 1, stdout="", stderr="Error: no such object"
                )
            index = len(self.inspect_payloads) - 1
        return self.CompletedProcess(
            command, 0, stdout=self.inspect_payloads[index], stderr=""
        )

    def _logs(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.logs_count += 1
        if self.logs_fail:
            raise self.TimeoutExpired(command, 10.0)
        return self.CompletedProcess(
            command, self.logs_returncode, stdout=self.logs_stdout, stderr=""
        )

    def _rm(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.rm_count += 1
        if self.rm_fail:
            raise self.TimeoutExpired(command, 30.0)
        return self.CompletedProcess(
            command, self.rm_returncode, stdout="", stderr=self.rm_stderr
        )


def _install_fakes(monkeypatch: pytest.MonkeyPatch, fake: FakeDocker) -> None:
    monkeypatch.setattr(container, "subprocess", fake)
    monkeypatch.setattr(container, "socket", fake.sockets, raising=False)
    monkeypatch.setattr(container, "time", fake.time)


@pytest.fixture
def make_docker(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Factory fixture that installs a fresh FakeDocker per test."""

    def _make(**kwargs: Any) -> FakeDocker:
        fake: FakeDocker = FakeDocker(**kwargs)
        _install_fakes(monkeypatch, fake)
        return fake

    return _make


@pytest.fixture
def fake_docker(make_docker: Any) -> FakeDocker:
    """Pre-configured FakeDocker whose container starts up successfully."""
    fake: FakeDocker = make_docker()
    return fake


class TestConstruction:
    def test_all_allowlisted_images_accepted(self) -> None:
        for image in _ALL_ALLOWED_IMAGES:
            sim = container.DockerContainer(image)
            assert sim.image == image
            assert "new" in repr(sim)

    def test_container_names_are_unique(self) -> None:
        names = {container.DockerContainer(_PX4_IMAGE).name for _ in range(5)}
        assert len(names) == 5
        assert all(name.startswith("aeroagentsim-") for name in names)

    def test_disallowed_images_are_rejected(self) -> None:
        for image in ("ubuntu:latest", "aeroagentsim/px4-gazebo:unallowed", ""):
            with pytest.raises(container.ImageNotAllowedError, match="allowlisted"):
                container.DockerContainer(image)

    def test_invalid_startup_timeout_rejected(self) -> None:
        for timeout in (0.0, -1.0):
            with pytest.raises(ValueError, match="positive"):
                container.DockerContainer(_PX4_IMAGE, startup_timeout_s=timeout)

    def test_endpoint_and_id_require_successful_start(self) -> None:
        sim = container.DockerContainer(_SUMO_IMAGE)
        with pytest.raises(container.ContainerError, match="endpoint"):
            _ = sim.endpoint
        with pytest.raises(container.ContainerError, match="started"):
            _ = sim.container_id


class TestRunCommand:
    def test_run_command_shape(self, make_docker: Any) -> None:
        fake = make_docker()
        with container.DockerContainer(_PX4_IMAGE) as sim:
            assert sim.endpoint == ("127.0.0.1", 54321)
        run_cmd = fake.commands[0]
        assert run_cmd[: len(_EXPECTED_RUN_HEAD)] == _EXPECTED_RUN_HEAD
        name_flag_at = run_cmd.index("--name")
        assert run_cmd[name_flag_at + 2] == _PX4_IMAGE
        name = run_cmd[name_flag_at + 1]
        assert name.startswith("aeroagentsim-")
        assert len(name) > len("aeroagentsim-")
        assert fake.timeouts[0] == 60.0

    def test_environment_and_mounts_rendered_sorted(self) -> None:
        command = container._build_run_command(
            _NS3_IMAGE,
            "aeroagentsim-x",
            {"B_VAR": "2", "A_VAR": "1"},
            {"/srv/b": "/data/b", "/srv/a": "/data/a"},
        )
        assert command == [
            "docker",
            "run",
            "--detach",
            "--label",
            "aeroagentsim.job=default",
            "--cpus",
            "8",
            "--publish",
            "127.0.0.1::9000",
            "--name",
            "aeroagentsim-x",
            "--env",
            "A_VAR=1",
            "--env",
            "B_VAR=2",
            "--volume",
            "/srv/a:/data/a",
            "--volume",
            "/srv/b:/data/b",
            _NS3_IMAGE,
        ]

    def test_run_failure_raises_startup_error(self, make_docker: Any) -> None:
        fake = make_docker(run_returncode=1, run_stderr="image not found")
        sim = container.DockerContainer(_SUMO_IMAGE)
        with pytest.raises(container.ContainerStartupError, match="image not found"):
            sim.start()
        assert len(fake.commands) == 1
        assert fake.commands[0][1] == "run"
        assert fake.inspect_count == 0
        assert fake.rm_count == 0

    def test_empty_run_stdout_rejected(self, make_docker: Any) -> None:
        fake = make_docker(run_stdout="\n")
        sim = container.DockerContainer(_NS3_IMAGE)
        with pytest.raises(container.ContainerStartupError, match="empty container id"):
            sim.start()
        assert fake.rm_count == 0

    def test_missing_docker_binary_surfaces(self, make_docker: Any) -> None:
        fake = make_docker(run_missing_binary=True)
        sim = container.DockerContainer(_PX4_IMAGE)
        with pytest.raises(container.ContainerError, match="not available"):
            sim.start()
        assert fake.rm_count == 0
        assert fake.inspect_count == 0


class TestReadiness:
    def test_passive_listener_readiness_via_context_manager(
        self, fake_docker: FakeDocker
    ) -> None:
        with container.DockerContainer(_PX4_IMAGE, startup_timeout_s=5.0) as sim:
            endpoint = sim.endpoint
            container_id = sim.container_id
        assert isinstance(endpoint, tuple)
        assert isinstance(endpoint[0], str) and endpoint[0] == "127.0.0.1"
        assert isinstance(endpoint[1], int) and endpoint[1] == 54321
        assert container_id == _CONTAINER_ID
        assert fake_docker.sockets.calls == [("127.0.0.1", 54321)]
        assert fake_docker.inspect_count == 2  # readiness poll + close ownership
        assert fake_docker.time.sleeps == []

    def test_passive_probe_opens_no_socket(self, fake_docker: FakeDocker) -> None:
        with container.DockerContainer(_PX4_IMAGE):
            pass
        assert fake_docker.sockets.closed_connections == 0

    def test_docker_health_wins_without_tcp(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(health="healthy")])
        fake = make_docker(inspect_payloads=[payload], tcp_accept=False)
        with container.DockerContainer(_PX4_IMAGE) as sim:
            assert sim.endpoint == ("127.0.0.1", 54321)
        assert fake.sockets.calls == []

    def test_health_starting_then_healthy(self, make_docker: Any) -> None:
        starting = json.dumps([_entry(health="starting")])
        healthy = json.dumps([_entry(health="healthy")])
        fake = make_docker(inspect_payloads=[starting, healthy], tcp_accept=False)
        with container.DockerContainer(_PX4_IMAGE, startup_timeout_s=10.0) as sim:
            assert sim.endpoint == ("127.0.0.1", 54321)
        assert fake.inspect_count == 3  # starting + healthy + close ownership

    def test_unhealthy_health_raises_and_cleans_up(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(health="unhealthy")])
        fake = make_docker(inspect_payloads=[payload])
        sim = container.DockerContainer(_PX4_IMAGE)
        with pytest.raises(
            container.ContainerStartupError, match="unhealthy"
        ) as excinfo:
            sim.start()
        assert _LOG_SENTINEL in str(excinfo.value)
        assert fake.rm_count == 1

    def test_exited_container_raises_and_cleans_up(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(running=False, status="exited", exit_code=7)])
        fake = make_docker(inspect_payloads=[payload])
        sim = container.DockerContainer(_SUMO_IMAGE)
        with pytest.raises(container.ContainerStartupError) as excinfo:
            sim.start()
        message = str(excinfo.value)
        assert "exited" in message and "7" in message
        assert _LOG_SENTINEL in message
        assert fake.rm_count == 1

    def test_readiness_timeout_cleans_up(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(ports={})])
        fake = make_docker(inspect_payloads=[payload])
        sim = container.DockerContainer(_PX4_IMAGE, startup_timeout_s=0.2)
        with pytest.raises(
            container.ContainerStartupError, match="did not become ready"
        ):
            sim.start()
        assert fake.inspect_count == 4  # three readiness polls + close ownership
        assert fake.time.sleeps == [0.1, 0.1]
        assert fake.rm_count == 1
        assert fake.logs_count == 1

    def test_missing_port_binding_is_reported(self, make_docker: Any) -> None:
        payload = json.dumps([_entry()])
        fake = make_docker(inspect_payloads=[payload], tcp_accept=False)
        sim = container.DockerContainer(_PX4_IMAGE, startup_timeout_s=0.2)
        with pytest.raises(container.ContainerStartupError, match="was not accepted"):
            sim.start()
        assert fake.sockets.calls
        assert set(fake.sockets.calls) == {("127.0.0.1", 54321)}
        assert fake.rm_count == 1

    def test_never_published_binding_message(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(ports={})])
        fake = make_docker(inspect_payloads=[payload])
        sim = container.DockerContainer(_NS3_IMAGE, startup_timeout_s=0.2)
        with pytest.raises(
            container.ContainerStartupError, match="no published host binding"
        ):
            sim.start()
        assert fake.rm_count == 1

    def test_healthy_container_without_binding_raises(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(health="healthy", ports={})])
        fake = make_docker(inspect_payloads=[payload])
        sim = container.DockerContainer(_PX4_IMAGE)
        with pytest.raises(
            container.ContainerStartupError, match="no published host binding"
        ):
            sim.start()
        assert fake.rm_count == 1

    def test_invalid_inspect_json_is_wrapped(self, make_docker: Any) -> None:
        fake = make_docker(inspect_payloads=["this is not json"])
        sim = container.DockerContainer(_PX4_IMAGE)
        with pytest.raises(container.ContainerStartupError, match="invalid JSON"):
            sim.start()
        assert fake.rm_count == 0  # the ownership re-inspect fails the same way

    def test_inspect_failure_during_wait_cleans_up(self, make_docker: Any) -> None:
        fake = make_docker(inspect_exhaust_fail=True, inspect_payloads=[])
        sim = container.DockerContainer(_PX4_IMAGE)
        with pytest.raises(
            container.ContainerStartupError, match="no such object"
        ) as excinfo:
            sim.start()
        assert "cleanup failed" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, container.ContainerError)
        assert fake.rm_count == 0  # rm is never reached; the re-inspect fails first


class TestCleanupAndOwnership:
    def test_exit_removes_exactly_own_container(self, fake_docker: FakeDocker) -> None:
        with container.DockerContainer(_PX4_IMAGE):
            pass
        assert fake_docker.rm_count == 1
        assert fake_docker.commands[-1] == ["docker", "rm", "--force", _CONTAINER_ID]
        assert fake_docker.timeouts[-1] == 30.0
        inspect_cmd = fake_docker.commands[-2]
        assert inspect_cmd[:3] == ["docker", "inspect", "--type"]
        assert inspect_cmd[-1] == _CONTAINER_ID

    def test_close_is_idempotent(self, fake_docker: FakeDocker) -> None:
        sim = container.DockerContainer(_PX4_IMAGE)
        with sim:
            pass
        sim.close()
        sim.close()
        assert fake_docker.rm_count == 1

    def test_close_before_start_is_noop(self, fake_docker: FakeDocker) -> None:
        sim = container.DockerContainer(_PX4_IMAGE)
        sim.close()
        assert fake_docker.commands == []
        with pytest.raises(container.ContainerError, match="closed"):
            sim.start()

    def test_start_twice_rejected(self, fake_docker: FakeDocker) -> None:
        sim = container.DockerContainer(_PX4_IMAGE)
        sim.start()
        with pytest.raises(container.ContainerError, match="already started"):
            sim.start()
        sim.close()

    def test_close_refuses_foreign_label(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(labels={"aeroagentsim.job": "wrong"})])
        fake = make_docker(inspect_payloads=[payload])
        sim = container.DockerContainer(_PX4_IMAGE)
        sim.start()
        with pytest.raises(container.ContainerError, match="refusing to remove"):
            sim.close()
        assert fake.rm_count == 0

    def test_close_refuses_id_mismatch(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(container_id="ffffffffffffffff")])
        fake = make_docker(inspect_payloads=[payload])
        sim = container.DockerContainer(_PX4_IMAGE)
        sim.start()
        with pytest.raises(container.ContainerError, match="refusing to remove"):
            sim.close()
        assert fake.rm_count == 0

    def test_failed_removal_raises(self, make_docker: Any) -> None:
        fake = make_docker(rm_returncode=1, rm_stderr="device is busy")
        sim = container.DockerContainer(_PX4_IMAGE)
        sim.start()
        with pytest.raises(container.ContainerError, match="device is busy"):
            sim.close()
        assert fake.rm_count == 1

    def test_removal_timeout_raises(self, make_docker: Any) -> None:
        make_docker(rm_fail=True)
        sim = container.DockerContainer(_PX4_IMAGE)
        sim.start()
        with pytest.raises(container.ContainerError, match="timed out"):
            sim.close()

    def test_body_exception_propagates_after_cleanup(
        self, fake_docker: FakeDocker
    ) -> None:
        with (
            pytest.raises(RuntimeError, match="boom"),
            container.DockerContainer(_PX4_IMAGE),
        ):
            raise RuntimeError("boom")
        assert fake_docker.rm_count == 1

    def test_cleanup_failure_does_not_suppress_body_exception(
        self, make_docker: Any
    ) -> None:
        make_docker(rm_returncode=1, rm_stderr="still running")
        with (
            pytest.raises(container.ContainerError) as excinfo,
            container.DockerContainer(_PX4_IMAGE),
        ):
            raise RuntimeError("boom")
        assert isinstance(excinfo.value.__context__, RuntimeError)
        assert excinfo.value.__context__.args == ("boom",)

    def test_startup_cleanup_failure_is_surfaced(self, make_docker: Any) -> None:
        fake = make_docker(
            inspect_exhaust_fail=True,
            inspect_payloads=[],
            rm_returncode=1,
            rm_stderr="remove failed",
        )
        sim = container.DockerContainer(_PX4_IMAGE)
        with pytest.raises(container.ContainerStartupError) as excinfo:
            sim.start()
        message = str(excinfo.value)
        assert "cleanup failed" in message
        assert "no such object" in message
        assert isinstance(excinfo.value.__cause__, container.ContainerError)
        assert fake.rm_count == 0  # rm is never reached; the re-inspect fails first

    def test_log_fetch_failure_still_raises_with_note(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(health="unhealthy")])
        fake = make_docker(inspect_payloads=[payload], logs_fail=True)
        sim = container.DockerContainer(_PX4_IMAGE)
        with pytest.raises(
            container.ContainerStartupError, match="fetching container logs failed"
        ):
            sim.start()
        assert fake.logs_count == 1
        assert fake.rm_count == 1

    def test_log_tail_command_shape(self, make_docker: Any) -> None:
        payload = json.dumps([_entry(health="unhealthy")])
        fake = make_docker(inspect_payloads=[payload])
        sim = container.DockerContainer(_PX4_IMAGE)
        with pytest.raises(container.ContainerStartupError):
            sim.start()
        logs_cmd = next(c for c in fake.commands if c[1] == "logs")
        assert logs_cmd == ["docker", "logs", "--tail", "100", _CONTAINER_ID]
        assert fake.timeouts[fake.commands.index(logs_cmd)] == 10.0
