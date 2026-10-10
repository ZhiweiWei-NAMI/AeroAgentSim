"""Launcher contracts without network access, Docker or existing servers."""

from __future__ import annotations

import errno
import signal
import stat
import sys
from pathlib import Path

import pytest

from tools import run_stack as stack


BASE = ["--control-suite", "suite.yaml", "--control-runner-config", "runner.yaml",
        "--authoring-output", "publication"]
COMPILED = ["--control-compilation-id", "c" * 64,
            "--control-compilation-root", "compilations", "--control-execution-output", "execution",
            "--control-runner-config", "runner.yaml", "--authoring-output", "publication"]


def configured(argv=BASE):
    args = stack.parse_args(argv)
    args.authoring_port, args.control_port, args.frontend_port = 20001, 20002, 20003
    return args


@pytest.mark.parametrize("argv", [
    ["--control-runner-config", "runner.yaml", "--authoring-output", "publication"],
    BASE + ["--control-compilation-id", "c" * 64],
    ["--control-compilation-id", "c" * 64, "--control-runner-config", "runner.yaml",
     "--authoring-output", "publication"],
    COMPILED + ["--control-sealed-run-id", "a" * 64],
    BASE + ["--control-compilation-root", "compilations"],
    BASE + ["--frontend-port", "0"],
    BASE + ["--traffic-preview-output", "preview"],
    BASE + ["--frontend-dist", "somewhere"],
])
def test_invalid_arguments(argv):
    with pytest.raises(SystemExit) as caught:
        stack.parse_args(argv)
    assert caught.value.code == 2


def test_frontend_dist_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(stack, "ROOT", tmp_path)
    dist = tmp_path / "frontend/dist"
    dist.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(dist, target_is_directory=True)
    for path in (dist, alias):
        with pytest.raises(SystemExit):
            stack.parse_args(BASE + ["--frontend", "preview", "--frontend-dist", str(path)])
        assert "frontend/dist is refused" in capsys.readouterr().err


def test_preview_requires_existing_directory(tmp_path):
    with pytest.raises(SystemExit):
        stack.parse_args(BASE + ["--frontend", "preview", "--frontend-dist", str(tmp_path / "missing")])


def test_port_in_use_refused_before_start(tmp_path, monkeypatch, capsys):
    reservations = []

    class Socket:
        def __init__(self, *_):
            self.closed = False
            reservations.append(self)

        def bind(self, address):
            if address[1] == 5403:
                raise OSError(errno.EADDRINUSE, "Address already in use")

        def getsockname(self):
            return "127.0.0.1", 21000 + len(reservations)

        def close(self):
            self.closed = True

    monkeypatch.setattr(stack.socket, "socket", Socket)
    monkeypatch.setattr(stack.subprocess, "Popen", lambda *_a, **_k: pytest.fail("child started"))
    run_dir = tmp_path / "run"
    assert stack.main(BASE + ["--frontend-port", "5403", "--run-dir", str(run_dir)]) == 1
    assert "frontend port 5403 is unavailable" in capsys.readouterr().err
    assert not run_dir.exists()
    assert all(reservation.closed for reservation in reservations)


@pytest.mark.parametrize("argv", [BASE, COMPILED])
def test_child_commands_and_environments(argv, tmp_path):
    options = argv + ["--native-scenes-manifest", "native.json", "--compilation-output", "drafts",
                      "--traffic-preview-profiles-manifest", "profiles.json",
                      "--traffic-preview-output", "traffic"]
    args = configured(options)
    token, csrf = "a" * 64, "b" * 64
    ambient = {"PATH": "/bin", "HTTP_PROXY": "http://proxy.invalid", "http_proxy": "http://proxy.invalid",
               "HTTPS_PROXY": "http://proxy.invalid", "ALL_PROXY": "http://proxy.invalid",
               "NO_PROXY": "localhost", "AERO_PREVIEW_TLS_KEY": "unused", "AERO_PREVIEW_TLS_CERT": "unused"}
    authoring, control, viewer = stack.build_services(args, tmp_path, token, csrf, ambient)
    assert authoring.argv == [sys.executable, "-m", "aero_bench.authoring.api", "--host", "127.0.0.1",
                              "--port", "20001", "--output", "publication", "--compilation-output", "drafts",
                              "--native-scenes-manifest", "native.json",
                              "--traffic-preview-profiles-manifest", "profiles.json",
                              "--traffic-preview-output", "traffic"]
    expected = [sys.executable, "-m", "aero_bench.control.cli", "serve", "--runner-config", "runner.yaml",
                "--bind-host", "127.0.0.1", "--port", "20002", "--allowed-origin", "http://127.0.0.1:20003",
                "--allowed-host", "127.0.0.1:20002", "--bootstrap-token-env", stack.TOKEN_ENV,
                "--bootstrap-csrf-env", stack.CSRF_ENV]
    if argv == BASE:
        expected += ["--suite", "suite.yaml"]
    else:
        expected += ["--compilation-id", "c" * 64, "--compilation-root", "compilations",
                     "--execution-output", "execution"]
    assert control.argv == expected
    assert viewer.argv == [str(stack.ROOT / "frontend/node_modules/.bin/vite"), "--host", "127.0.0.1",
                           "--port", "20003", "--strictPort"]
    assert viewer.cwd == stack.ROOT / "frontend"
    assert viewer.url == "http://127.0.0.1:20003/workspace-traces/catalog.json"
    assert authoring.cwd == control.cwd == Path.cwd()
    assert control.env[stack.TOKEN_ENV] == token
    assert control.env[stack.CSRF_ENV] == csrf
    assert control.headers == {"Origin": "http://127.0.0.1:20003", "Authorization": f"Bearer {token}"}
    assert viewer.env["AERO_CONTROL_API_TARGET"] == "http://127.0.0.1:20002"
    assert viewer.env["AERO_AUTHORING_API_TARGET"] == "http://127.0.0.1:20001"
    assert "AERO_PREVIEW_TLS_KEY" not in viewer.env
    for service in (authoring, control, viewer):
        assert not any(key.lower().endswith("_proxy") for key in service.env)
        assert token not in " ".join(service.argv) and csrf not in " ".join(service.argv)
        if service is not control:
            assert token not in service.env.values() and csrf not in service.env.values()


def test_replay_ids_and_preview_command(tmp_path):
    run_ids = ["a" * 64, "b" * 64]
    args = configured(BASE + ["--control-sealed-run-id", run_ids[0], "--control-sealed-run-id", run_ids[1],
                              "--frontend", "preview", "--frontend-dist", str(tmp_path)])
    authoring, control, viewer = stack.build_services(args, tmp_path, "c" * 64, "d" * 64, {})
    assert control.argv[-4:] == ["--sealed-run-id", run_ids[0], "--sealed-run-id", run_ids[1]]
    assert viewer.argv[1:4] == ["preview", "--outDir", str(tmp_path)]
    assert authoring.argv[-2:] == ["--compilation-output", str(tmp_path / "compilations")]


def test_credentials_permissions_exclusive_creation_and_shutdown(tmp_path):
    path = tmp_path / "credentials.json"
    stack.create_credentials(path, "a" * 64, "b" * 64)
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with pytest.raises(FileExistsError):
            stack.create_credentials(path, "c" * 64, "d" * 64)
    finally:
        stack.shutdown([], path)
    assert not path.exists()


def test_failed_credentials_write_removes_partial_file(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"

    def fail(*_args):
        raise OSError("write failed")

    monkeypatch.setattr(stack.json, "dump", fail)
    with pytest.raises(OSError, match="write failed"):
        stack.create_credentials(path, "a" * 64, "b" * 64)
    assert not path.exists()


@pytest.mark.parametrize("runtime_configured", [False, True])
def test_default_credentials_location_and_cleanup(tmp_path, monkeypatch, runtime_configured):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    if runtime_configured:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    else:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(stack, "choose_ports", lambda args: None)
    private_dir = tmp_path / "private-credentials"

    def mkdtemp(*, prefix):
        assert prefix == "aero-stack-credentials-"
        private_dir.mkdir(mode=0o700)
        return str(private_dir)

    monkeypatch.setattr(stack.tempfile, "mkdtemp", mkdtemp)
    paths = []
    original = stack.create_credentials

    def create(path, token, csrf):
        paths.append(path)
        original(path, token, csrf)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        if not runtime_configured:
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def build(*_args):
        raise RuntimeError("stop after credential creation")

    monkeypatch.setattr(stack, "create_credentials", create)
    monkeypatch.setattr(stack, "build_services", build)
    assert stack.main(BASE + ["--run-dir", str(tmp_path / "run")]) == 1
    assert paths[0].parent == (runtime if runtime_configured else private_dir)
    assert not paths[0].exists()
    assert not private_dir.exists()


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_shutdown_removes_credentials(tmp_path, monkeypatch, capsys, signum):
    credentials = tmp_path / "credentials.json"
    monkeypatch.setattr(stack, "choose_ports", lambda args: None)

    def build(_args, directory, _token, _csrf, _env):
        assert stat.S_IMODE(credentials.stat().st_mode) == 0o600
        return [stack.Service("fake", [sys.executable, "-c", "import time; time.sleep(60)"], {},
                              tmp_path, "http://unused/", {}, directory / "fake.log")]

    children = []

    def ready(service, _services):
        children.append(service)
        handler = signal.getsignal(signum)
        handler(signum, None)

    monkeypatch.setattr(stack, "build_services", build)
    monkeypatch.setattr(stack, "wait_ready", ready)
    assert stack.main(BASE + ["--run-dir", str(tmp_path / "run"),
                              "--credentials-file", str(credentials)]) == 130
    assert children[0].process.poll() is not None
    assert not credentials.exists()
    assert "Stopping stack: received signal" in capsys.readouterr().out


def test_every_service_starts_before_the_first_readiness_wait(tmp_path, monkeypatch, capsys):
    credentials = tmp_path / "credentials.json"
    monkeypatch.setattr(stack, "choose_ports", lambda args: None)
    services = []

    def build(_args, directory, _token, _csrf, _env):
        services.extend(stack.Service(name, [sys.executable, "-c", "import time; time.sleep(60)"], {},
                                      tmp_path, "http://unused/", {}, directory / f"{name}.log")
                        for name in ("first", "second"))
        return services

    started_at_first_wait = []

    def ready(service, _services):
        started_at_first_wait.append([other.process is not None for other in services])
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    monkeypatch.setattr(stack, "build_services", build)
    monkeypatch.setattr(stack, "wait_ready", ready)
    assert stack.main(BASE + ["--run-dir", str(tmp_path / "run"),
                              "--credentials-file", str(credentials)]) == 130
    assert started_at_first_wait == [[True, True]]
    assert all(service.process.poll() is not None for service in services)
    assert not credentials.exists()


def test_readiness_failure_cleans_up_fake_child(tmp_path, monkeypatch, capsys):
    credentials = tmp_path / "credentials.json"
    run_dir = tmp_path / "run"
    children = []
    monkeypatch.setattr(stack, "choose_ports", lambda args: None)

    def build(_args, directory, _token, _csrf, _environment):
        code = "for i in range(45): print(f'line-{i:02d}', flush=True)\nraise SystemExit(7)"
        service = stack.Service("fake authoring", [sys.executable, "-c", code], {}, tmp_path,
                                "http://unused/ready", {}, directory / "fake.log")
        children.append(service)
        return children

    monkeypatch.setattr(stack, "build_services", build)
    monkeypatch.setattr(stack, "request", lambda *_: (503, "", b""))
    assert stack.main(BASE + ["--run-dir", str(run_dir), "--credentials-file", str(credentials)]) == 1
    output = capsys.readouterr()
    assert "fake authoring failed: process exited with code 7" in output.err
    assert "line-04\n" not in output.err
    assert all(f"line-{i:02d}\n" in output.err for i in range(5, 45))
    assert "GET http://unused/ready (timeout 900s)" in output.out
    assert "Ready. Viewer" not in output.out
    assert children[0].process.poll() == 7
    assert not credentials.exists()


def test_missing_child_command_names_service_and_removes_credentials(tmp_path, monkeypatch, capsys):
    credentials = tmp_path / "credentials.json"
    monkeypatch.setattr(stack, "choose_ports", lambda args: None)

    def build(_args, directory, _token, _csrf, _env):
        return [stack.Service("viewer", [str(tmp_path / "missing-vite")], {}, tmp_path,
                              "http://unused/", {}, directory / "viewer.log")]

    monkeypatch.setattr(stack, "build_services", build)
    assert stack.main(BASE + ["--run-dir", str(tmp_path / "run"),
                              "--credentials-file", str(credentials)]) == 1
    error = capsys.readouterr().err
    assert "viewer failed: could not start:" in error
    assert "Last 40 log lines" in error
    assert not credentials.exists()


def test_readiness_timeout_names_service_and_url(tmp_path, monkeypatch):
    log = tmp_path / "viewer.log"
    log.write_text("viewer diagnostic\n")
    service = stack.Service("viewer", [], {}, tmp_path, "http://unused/", {}, log)
    monkeypatch.setattr(stack, "request", lambda *_: (503, "", b""))
    monkeypatch.setattr(stack.time, "sleep", lambda *_: None)
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(stack.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="viewer failed: readiness timed out at http://unused/") as caught:
        stack.wait_ready(service, [service], timeout=1)
    assert "viewer diagnostic" in str(caught.value)


def test_shutdown_kills_group_even_after_leader_exits(monkeypatch):
    calls = []

    class Process:
        pid = 123456

        def poll(self):
            return 0

        def wait(self):
            calls.append("wait")

    monkeypatch.setattr(stack.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(stack, "STOP_TIMEOUT", 0)
    stack.stop_process_group(Process())
    assert calls == [(123456, signal.SIGTERM), (123456, signal.SIGKILL), "wait"]


@pytest.mark.parametrize("runs, expected", [([], False), (["a" * 64], False), (["a" * 64, "b" * 64], True)])
def test_smoke_checks_validate_catalog_and_wrong_origin(runs, expected, monkeypatch, capsys):
    import json

    args = configured(BASE + ["--control-sealed-run-id", "a" * 64, "--control-sealed-run-id", "b" * 64])
    requests = []

    def request(url, headers):
        requests.append((url, headers))
        if url.endswith("/v1/catalog"):
            assert headers["Authorization"] == "Bearer " + "c" * 64
            if headers["Origin"] == "http://127.0.0.1:1":
                return 403, "application/json", b'{"error":{"code":"origin.rejected"}}'
            return 200, "application/json", json.dumps({"runs": [{"run_id": run} for run in runs]}).encode()
        if url.endswith("native-scenes"):
            return 200, "application/json", b"{}"
        if url.endswith("catalog.json"):
            return 200, "application/json", b"{}"
        return 200, "text/html", b"<html></html>"

    monkeypatch.setattr(stack, "request", request)
    assert stack.smoke_checks(args, "c" * 64) is expected
    assert len(requests) == 5
    output = capsys.readouterr().out
    assert "PASS Control wrong origin rejected" in output
    assert ("PASS" if expected else "FAIL") + " authenticated Control catalog" in output
    assert "c" * 64 not in output
