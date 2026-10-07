"""Launcher contracts without external network access, Docker or existing servers."""

from __future__ import annotations

import errno
import json
import runpy
import signal
import socket
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
    BASE + ["--reuse-credentials"],
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

        def setsockopt(self, level, option, value):
            assert (level, option, value) == (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        def listen(self, backlog):
            assert backlog == 1

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


def test_port_reservation_accepts_time_wait_after_a_closed_connection():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                connection.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b""
                client.shutdown(socket.SHUT_WR)
                assert connection.recv(1) == b""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as plain:
        with pytest.raises(OSError) as caught:
            plain.bind(("127.0.0.1", port))
        assert caught.value.errno == errno.EADDRINUSE
    args = stack.parse_args(BASE + ["--authoring-port", str(port)])
    stack.choose_ports(args)
    assert args.authoring_port == port
    assert len({args.authoring_port, args.control_port, args.frontend_port}) == 3


def test_reusable_port_reservation_still_rejects_an_active_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        args = stack.parse_args(BASE + ["--authoring-port", str(port)])
        with pytest.raises(ValueError, match="authoring port .* is unavailable"):
            stack.choose_ports(args)


def test_reusable_port_reservations_still_require_distinct_service_ports():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temporary:
        temporary.bind(("127.0.0.1", 0))
        port = temporary.getsockname()[1]
    args = stack.parse_args(BASE + ["--authoring-port", str(port), "--control-port", str(port)])
    with pytest.raises(ValueError, match="control port .* is unavailable"):
        stack.choose_ports(args)


@pytest.mark.parametrize("argv", [BASE, COMPILED])
def test_child_commands_and_environments(argv, tmp_path):
    options = argv + ["--sources-manifest", "sources.json", "--native-scenes-manifest", "native.json", "--compilation-output", "drafts",
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
                              "--sources-manifest", "sources.json", "--native-scenes-manifest", "native.json",
                              "--traffic-preview-profiles-manifest", "profiles.json",
                              "--traffic-preview-output", "traffic"]
    expected = [sys.executable, "-m", "aero_bench.control.cli", "serve", "--runner-config", "runner.yaml",
                "--bind-host", "127.0.0.1", "--port", "20002", "--allowed-origin", "http://127.0.0.1:20003",
                "--allowed-origin", "http://localhost:20003",
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


def test_reused_credentials_remain_private_and_unchanged_after_restarts(tmp_path, monkeypatch, capsys):
    path = tmp_path / "credentials.json"
    token, csrf = "a" * 64, "b" * 64
    stack.create_credentials(path, token, csrf)
    original = path.read_bytes()
    run_dir = tmp_path / "services"
    run_dir.mkdir()
    log = run_dir / "fake.log"
    log.write_text("retained earlier log\n")
    monkeypatch.setattr(stack, "choose_ports", lambda args: None)
    children = []

    def build(_args, directory, actual_token, actual_csrf, _env):
        assert (actual_token, actual_csrf) == (token, csrf)
        service = stack.Service("fake", [sys.executable, "-c", "print('new launch', flush=True)"], {},
                                tmp_path, "http://unused/", {}, directory / "fake.log")
        children.append(service)
        return [service]

    def ready(service, _services):
        service.process.wait(timeout=5)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    monkeypatch.setattr(stack, "build_services", build)
    monkeypatch.setattr(stack, "wait_ready", ready)
    options = BASE + ["--run-dir", str(run_dir), "--credentials-file", str(path), "--reuse-credentials"]
    for _ in range(2):
        assert stack.main(options) == 130
        assert path.read_bytes() == original
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert log.read_text() == "retained earlier log\nnew launch\nnew launch\n"
    assert all(service.process.poll() is not None for service in children)
    output = capsys.readouterr()
    assert token not in output.out + output.err and csrf not in output.out + output.err


@pytest.mark.parametrize("kind", ["missing", "public", "symlink", "invalid", "same-token"])
def test_invalid_reused_credentials_never_start_services(tmp_path, monkeypatch, kind, capsys):
    path = tmp_path / "credentials.json"
    if kind != "missing":
        stack.create_credentials(path, "a" * 64, "b" * 64)
    if kind == "public":
        path.chmod(0o644)
    elif kind == "symlink":
        target = tmp_path / "target.json"
        path.rename(target)
        path.symlink_to(target)
    elif kind == "invalid":
        path.write_text('{"bootstrap_token":null,"bootstrap_csrf":"invalid"}')
    elif kind == "same-token":
        path.write_text(json.dumps({"bootstrap_token": "a" * 64, "bootstrap_csrf": "a" * 64}))
    existed = path.exists()
    original = path.read_bytes() if existed else None
    monkeypatch.setattr(stack, "choose_ports", lambda args: None)
    monkeypatch.setattr(stack, "build_services", lambda *_: pytest.fail("services built"))
    assert stack.main(BASE + ["--run-dir", str(tmp_path / "services"),
                              "--credentials-file", str(path), "--reuse-credentials"]) == 1
    assert path.exists() is existed
    if existed:
        assert path.read_bytes() == original
    assert "a" * 64 not in capsys.readouterr().err


@pytest.mark.parametrize("check", [False, True])
def test_root_entry_uses_canonical_sealed_stack_from_any_directory(tmp_path, monkeypatch, check):
    entry = stack.ROOT.parent / "run-aero-bench"
    evidence = json.loads((stack.ROOT / "docs/p02-native-logistics/platform-0.1-evidence.json").read_text())
    calls = []

    def run(options):
        calls.append((Path.cwd(), stack.parse_args(options)))
        return 7

    monkeypatch.setattr(stack, "main", run)
    monkeypatch.chdir(tmp_path)
    namespace = runpy.run_path(str(entry))
    assert namespace["main"](["--check"] if check else []) == 7
    directory, args = calls[0]
    platform = Path("validation/platform-0.1")
    assert directory == stack.ROOT
    assert args.control_suite == str(platform / "final-compilations" / evidence["compilation_id"] / "bundle/suite.yaml")
    assert args.control_sealed_run_id == [evidence["run_id"]]
    assert args.control_compilation_id is None
    assert args.control_runner_config == str(platform / "replay-runner.json")
    assert args.authoring_output == str(platform / "authoring")
    assert args.compilation_output == str(platform / "authoring/compilations")
    assert args.sources_manifest == str(platform / "inputs/authoring-sources.json")
    assert args.native_scenes_manifest == str(platform / "final-registry/native-scenes.json")
    assert args.credentials_file == Path("credentials/platform-0.1.json")
    assert args.reuse_credentials and args.run_dir == platform / "services"
    assert (args.frontend_port, args.control_port, args.authoring_port) == (5416, 8769, 8771)
    assert args.check is check


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
    assert len(requests) == 6
    output = capsys.readouterr().out
    assert "PASS Control wrong origin rejected" in output
    assert ("PASS" if expected else "FAIL") + " Control localhost origin" in output
    assert ("PASS" if expected else "FAIL") + " authenticated Control catalog" in output
    assert "c" * 64 not in output
