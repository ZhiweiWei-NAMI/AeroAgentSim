#!/usr/bin/env python3
"""Launch the local authoring, Control and viewer services as one stack."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
TOKEN_ENV = "AERO_STACK_BOOTSTRAP_TOKEN"
CSRF_ENV = "AERO_STACK_BOOTSTRAP_CSRF"
READINESS_TIMEOUT = 900
STOP_TIMEOUT = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--control-suite")
    modes.add_argument("--control-compilation-id")
    parser.add_argument("--control-runner-config", required=True)
    parser.add_argument("--control-sealed-run-id", action="append", default=[])
    parser.add_argument("--control-compilation-root")
    parser.add_argument("--control-execution-output")
    parser.add_argument("--authoring-output", required=True)
    for option in ("sources-manifest", "native-scenes-manifest", "compilation-output",
                   "traffic-preview-profiles-manifest", "traffic-preview-output"):
        parser.add_argument(f"--{option}")
    for service in ("authoring", "control", "frontend"):
        parser.add_argument(f"--{service}-port", type=int)
    parser.add_argument("--frontend", choices=("dev", "preview"), default="dev")
    parser.add_argument("--frontend-dist", type=Path)
    parser.add_argument("--credentials-file", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    compiled = args.control_compilation_id is not None
    if compiled and (not args.control_compilation_root or not args.control_execution_output):
        parser.error("compiled mode requires --control-compilation-root and --control-execution-output")
    if not compiled and (args.control_compilation_root or args.control_execution_output):
        parser.error("compilation options require --control-compilation-id")
    if compiled and args.control_sealed_run_id:
        parser.error("--control-sealed-run-id requires --control-suite")
    if args.frontend == "preview":
        if args.frontend_dist is None or not args.frontend_dist.is_dir():
            parser.error("preview requires an existing --frontend-dist directory")
        if args.frontend_dist.resolve() in {
            (ROOT / "frontend/dist").resolve(), (Path.cwd() / "frontend/dist").resolve(),
        }:
            parser.error("frontend/dist is refused; supply a separate build directory")
        args.frontend_dist = args.frontend_dist.resolve()
    elif args.frontend_dist is not None:
        parser.error("--frontend-dist requires --frontend preview")
    if bool(args.traffic_preview_profiles_manifest) != bool(args.traffic_preview_output):
        parser.error("traffic preview profiles and output must be supplied together")
    for service in ("authoring", "control", "frontend"):
        port = getattr(args, f"{service}_port")
        if port is not None and not 1024 <= port <= 65535:
            parser.error(f"--{service}-port must be between 1024 and 65535")
    return args


def choose_ports(args: argparse.Namespace) -> None:
    """Check all ports before any child starts; reserve distinct selections."""
    reservations: list[socket.socket] = []
    try:
        for service in ("authoring", "control", "frontend"):
            port = getattr(args, f"{service}_port")
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservations.append(reservation)
            try:
                reservation.bind(("127.0.0.1", port or 0))
            except OSError as error:
                raise ValueError(f"{service} port {port} is unavailable: {error}") from error
            setattr(args, f"{service}_port", reservation.getsockname()[1])
    finally:
        for reservation in reservations:
            reservation.close()


@dataclass
class Service:
    name: str
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    url: str
    headers: dict[str, str]
    log: Path
    process: subprocess.Popen | None = None


def build_services(args: argparse.Namespace, run_dir: Path, token: str, csrf: str,
                   environment: dict[str, str]) -> list[Service]:
    env = {key: value for key, value in environment.items()
           if not key.lower().endswith("_proxy") and key not in {TOKEN_ENV, CSRF_ENV}}
    origin = f"http://127.0.0.1:{args.frontend_port}"
    control_url = f"http://127.0.0.1:{args.control_port}"
    authoring_url = f"http://127.0.0.1:{args.authoring_port}"
    authoring = [sys.executable, "-m", "aero_bench.authoring.api", "--host", "127.0.0.1",
                 "--port", str(args.authoring_port), "--output", args.authoring_output]
    # The authoring CLI needs a compiler even for the native-scenes catalog.
    compilation_output = args.compilation_output or str(run_dir / "compilations")
    authoring += ["--compilation-output", compilation_output]
    for option in ("sources-manifest", "native-scenes-manifest", "traffic-preview-profiles-manifest",
                   "traffic-preview-output"):
        value = getattr(args, option.replace("-", "_"))
        if value is not None:
            authoring += [f"--{option}", value]
    control = [sys.executable, "-m", "aero_bench.control.cli", "serve",
               "--runner-config", args.control_runner_config,
               "--bind-host", "127.0.0.1", "--port", str(args.control_port),
               "--allowed-origin", origin, "--allowed-host", f"127.0.0.1:{args.control_port}",
               "--bootstrap-token-env", TOKEN_ENV, "--bootstrap-csrf-env", CSRF_ENV]
    if args.control_suite is not None:
        control += ["--suite", args.control_suite]
        for run_id in args.control_sealed_run_id:
            control += ["--sealed-run-id", run_id]
    else:
        control += ["--compilation-id", args.control_compilation_id,
                    "--compilation-root", args.control_compilation_root,
                    "--execution-output", args.control_execution_output]
    frontend_root = ROOT / "frontend"
    frontend = [str(frontend_root / "node_modules/.bin/vite")]
    if args.frontend == "preview":
        frontend += ["preview", "--outDir", str(args.frontend_dist)]
    frontend += ["--host", "127.0.0.1", "--port", str(args.frontend_port), "--strictPort"]
    frontend_env = dict(env, AERO_CONTROL_API_TARGET=control_url,
                        AERO_AUTHORING_API_TARGET=authoring_url)
    # This launcher advertises HTTP origins, including in preview mode.
    frontend_env.pop("AERO_PREVIEW_TLS_KEY", None)
    frontend_env.pop("AERO_PREVIEW_TLS_CERT", None)
    return [
        Service("authoring", authoring, dict(env), Path.cwd(),
                authoring_url + "/authoring/v1/sources", {}, run_dir / "authoring.log"),
        Service("Control", control, dict(env, **{TOKEN_ENV: token, CSRF_ENV: csrf}), Path.cwd(),
                control_url + "/v1/catalog", {"Origin": origin, "Authorization": f"Bearer {token}"},
                run_dir / "control.log"),
        # The catalog answers once the viewer's startup scan of workspace traces completes.
        Service("viewer", frontend, frontend_env, frontend_root, origin + "/workspace-traces/catalog.json", {},
                run_dir / "viewer.log"),
    ]


def create_credentials(path: Path, token: str, csrf: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"bootstrap_token": token, "bootstrap_csrf": csrf}, stream)
            stream.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def request(url: str, headers: dict[str, str]) -> tuple[int, str, bytes]:
    opener = build_opener(ProxyHandler({}))
    try:
        response = opener.open(Request(url, headers=headers), timeout=2)
    except HTTPError as error:
        response = error
    with response:
        return response.code, response.headers.get("Content-Type", ""), response.read()


def failure(service: Service, reason: str) -> RuntimeError:
    with service.log.open(encoding="utf-8", errors="replace") as stream:
        tail = "".join(deque(stream, maxlen=40))
    return RuntimeError(f"{service.name} failed: {reason}\nLast 40 log lines ({service.log}):\n{tail}")


def ensure_alive(services: list[Service]) -> None:
    for service in services:
        if service.process is not None and service.process.poll() is not None:
            raise failure(service, f"process exited with code {service.process.returncode}")


def wait_ready(service: Service, services: list[Service], timeout: float = READINESS_TIMEOUT) -> None:
    print(f"Readiness {service.name}: GET {service.url} (timeout {timeout:g}s)", flush=True)
    deadline = time.monotonic() + timeout
    last_error = "no successful response"
    while time.monotonic() < deadline:
        ensure_alive(services)
        try:
            status, _, _ = request(service.url, service.headers)
            if status == 200:
                ensure_alive(services)
                return
            last_error = f"HTTP {status}"
        except (URLError, TimeoutError, OSError) as error:
            last_error = str(error)
        time.sleep(0.25)
    raise failure(service, f"readiness timed out at {service.url} after {timeout:g}s: {last_error}")


def stop_process_group(process: subprocess.Popen) -> None:
    # A child can exit while descendants remain in its process group.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            process.wait()
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def shutdown(services: list[Service], credentials_file: Path | None) -> None:
    try:
        for service in reversed(services):
            if service.process is not None:
                stop_process_group(service.process)
    finally:
        if credentials_file is not None:
            credentials_file.unlink(missing_ok=True)


def smoke_checks(args: argparse.Namespace, token: str) -> bool:
    origin = f"http://127.0.0.1:{args.frontend_port}"
    catalog_url = f"http://127.0.0.1:{args.control_port}/v1/catalog"
    headers = {"Origin": origin, "Authorization": f"Bearer {token}"}

    def html() -> bool:
        status, media, body = request(origin + "/", {})
        return status == 200 and "text/html" in media and b"<html" in body.lower()

    def native_scenes() -> bool:
        status, media, body = request(origin + "/authoring/v1/native-scenes", {})
        return status == 200 and "application/json" in media and isinstance(json.loads(body), dict)

    def workspace() -> bool:
        return request(origin + "/workspace-traces/catalog.json", {})[0] == 200

    def catalog() -> bool:
        status, _, body = request(catalog_url, headers)
        if status != 200:
            return False
        runs = json.loads(body)["runs"]
        return bool(runs) and set(args.control_sealed_run_id) <= {run["run_id"] for run in runs}

    def rejected_origin() -> bool:
        status, _, body = request(catalog_url, dict(headers, Origin="http://127.0.0.1:1"))
        return status == 403 and json.loads(body)["error"]["code"] == "origin.rejected"

    passed = True
    for name, check in (("viewer HTML", html), ("authoring native-scenes proxy", native_scenes),
                        ("workspace-traces catalog", workspace), ("authenticated Control catalog", catalog),
                        ("Control wrong origin rejected", rejected_origin)):
        try:
            result = check()
        except (URLError, OSError, ValueError, KeyError, TypeError):
            result = False
        print(f"{'PASS' if result else 'FAIL'} {name}", flush=True)
        passed = passed and result
    return passed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    services: list[Service] = []
    credentials_file: Path | None = None
    private_dir: Path | None = None
    previous_handlers = {}

    def interrupted(signum: int, _frame: object) -> None:
        raise InterruptedError(f"received signal {signum}")

    try:
        choose_ports(args)
        run_dir = (args.run_dir.resolve() if args.run_dir else
                   Path(tempfile.mkdtemp(prefix="aero-stack-")))
        if args.run_dir is not None:
            run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        print(f"Run directory: {run_dir}", flush=True)
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupted)
        path = args.credentials_file
        if path is None:
            runtime = os.environ.get("XDG_RUNTIME_DIR")
            if runtime:
                path = Path(runtime) / f"aero-stack-{secrets.token_hex(8)}.json"
            else:
                private_dir = Path(tempfile.mkdtemp(prefix="aero-stack-credentials-"))
                path = private_dir / "credentials.json"
        token, csrf = secrets.token_hex(32), secrets.token_hex(32)
        create_credentials(path, token, csrf)
        credentials_file = path.resolve()
        services = build_services(args, run_dir, token, csrf, dict(os.environ))
        for service in services:
            with service.log.open("wb") as log:
                try:
                    service.process = subprocess.Popen(
                        service.argv, cwd=service.cwd, env=service.env,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as error:
                    raise failure(service, f"could not start: {error}") from error
            (run_dir / f"{service.name.lower()}.pid").write_text(
                f"{service.process.pid}\n", encoding="ascii",
            )
        # All services start before any readiness wait, so the viewer's trace scan overlaps Control startup.
        for service in services:
            wait_ready(service, services)
        ensure_alive(services)
        origin = f"http://127.0.0.1:{args.frontend_port}"
        print(f"Ready. Viewer: {origin}/\nReplay: {origin}/?view=replay\n"
              f"Studio: {origin}/city-studio.html\n"
              f"Control base URL: http://127.0.0.1:{args.control_port}\n"
              f"Credentials file: {credentials_file}", flush=True)
        if args.check:
            result = smoke_checks(args, token)
            ensure_alive(services)
            return 0 if result else 1
        while True:
            ensure_alive(services)
            time.sleep(0.5)
    except InterruptedError as error:
        print(f"Stopping stack: {error}", flush=True)
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1
    finally:
        # Further termination signals must not interrupt cleanup.
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        try:
            shutdown(services, credentials_file)
            if private_dir is not None:
                private_dir.rmdir()
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
