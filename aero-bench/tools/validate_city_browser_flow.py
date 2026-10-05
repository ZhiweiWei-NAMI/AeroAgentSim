#!/usr/bin/env python3
"""Run the real native-reference save/compile/execute/replay browser flow.

The authoring and Control services are private, fresh instances. The browser
starts the compiled run through the public UI. Random bootstrap credentials are
sent only over the Node child's stdin and are never logged or persisted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aero_bench.authoring.api import make_server  # noqa: E402
from aero_bench.authoring.draft_compiler import CityDraftCompiler  # noqa: E402
from aero_bench.authoring.native_registry import NativeSceneRegistry  # noqa: E402
from aero_bench.authoring.publication import ScenePackPublisher  # noqa: E402
from aero_bench.control.manager import ControlRunManager  # noqa: E402
from aero_bench.control.server import ControlHttpConfig, ControlHttpServer  # noqa: E402
from aero_bench.providers.rpc import parse_json_object  # noqa: E402
from aero_bench.runner.contracts import RunSummary  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.trace.contracts import (  # noqa: E402
    PublicReplayIndex,
    PublicReplayManifest,
    PublicTrace,
)


T = TypeVar("T")
SHA256 = frozenset("0123456789abcdef")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bounded_json(path: Path, *, maximum: int = 512 * 1024 * 1024) -> dict[str, Any]:
    size = path.stat().st_size
    if not 1 <= size <= maximum:
        raise ValueError(f"JSON evidence has an invalid byte size: {path.name}")
    return parse_json_object(path.read_bytes())


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _resolve(root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _wait_http(url: str, *, timeout_seconds: int, process: subprocess.Popen[bytes] | None = None) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"service process exited before readiness (code {process.returncode})")
        try:
            with opener.open(Request(url, headers={"Accept": "text/html,application/json"}), timeout=10) as response:
                if 200 <= response.status < 300:
                    response.read(1024)
                    return
                last_error = RuntimeError(f"HTTP {response.status}")
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = error
        time.sleep(1.0)
    raise TimeoutError(f"service did not become ready at {url}: {last_error}")


def _stop_process_group(process: subprocess.Popen[Any] | None, *, timeout_seconds: int = 20) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def _send(child: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if child.stdin is None:
        raise RuntimeError("browser IPC stdin is unavailable")
    child.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    child.stdin.flush()


def _receive(
    child: subprocess.Popen[str], *, timeout_seconds: int, forbidden: tuple[str, ...] = (),
) -> dict[str, Any]:
    if child.stdout is None:
        raise RuntimeError("browser IPC stdout is unavailable")
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    try:
        ready = selector.select(timeout_seconds)
    finally:
        selector.close()
    if not ready:
        raise TimeoutError("browser IPC response exceeded its declared timeout")
    line = child.stdout.readline()
    if line == "":
        raise RuntimeError(f"browser process closed IPC (code {child.poll()})")
    if any(secret in line for secret in forbidden):
        raise RuntimeError("browser IPC attempted to expose a credential")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise RuntimeError("browser IPC response is not JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise RuntimeError("browser IPC response has no message type")
    return value


def _model_bytes(path: Path, model: Any, *, exclude_none: bool = False) -> None:
    expected = canonical_json_bytes(model.model_dump(mode="json", exclude_none=exclude_none)) + b"\n"
    if path.read_bytes() != expected:
        raise ValueError(f"sealed document is not canonical: {path.name}")


def _validate_sealed_execution(
    *, execution_root: Path, run_id: str, browser: dict[str, Any],
    expected_native_mesh_status: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []

    def checked(name: str, action: Callable[[], T]) -> T:
        started = time.monotonic()
        try:
            result = action()
        except BaseException as error:
            checks.append({"name": name, "status": "failed",
                           "duration_ms": round((time.monotonic() - started) * 1000),
                           "error": str(error)})
            raise
        checks.append({"name": name, "status": "passed",
                       "duration_ms": round((time.monotonic() - started) * 1000)})
        return result

    run_root = execution_root / run_id

    def load_summary() -> RunSummary:
        path = run_root / "run-summary.json"
        summary = RunSummary.model_validate(_bounded_json(path))
        _model_bytes(path, summary)
        if summary.run_id != run_id or summary.status != "passed" or summary.failure_classes:
            raise ValueError("fresh run summary is not a clean pass")
        if summary.verification is None or summary.verification.status != "passed":
            raise ValueError("fresh run has no passed independent verification")
        if summary.public_trace is None or summary.seal is None:
            raise ValueError("fresh run has no sealed public trace identity")
        return summary

    summary = checked("parse the fresh canonical passed run summary", load_summary)

    def load_trace() -> tuple[PublicTrace, Path]:
        identity = summary.public_trace
        assert identity is not None
        path = run_root / identity.relative_path
        if _sha256_file(path) != identity.sha256:
            raise ValueError("public trace bytes differ from the run summary")
        trace = PublicTrace.model_validate(_bounded_json(path))
        _model_bytes(path, trace)
        if trace.run_id != run_id or trace.phase != "verified":
            raise ValueError("fresh public trace is not in verified phase")
        if trace.verifier_public is None or trace.verifier_public.status != "passed":
            raise ValueError("fresh public trace has no passed verifier verdict")
        if not trace.verifier_public.coverage_complete:
            raise ValueError("fresh public verifier coverage is incomplete")
        return trace, path

    trace, trace_path = checked("read and authenticate the fresh verified public trace", load_trace)

    def load_manifest() -> tuple[PublicReplayManifest, Path]:
        identity = summary.public_trace.replay  # type: ignore[union-attr]
        path = run_root / identity.relative_path
        if _sha256_file(path) != identity.sha256:
            raise ValueError("replay manifest bytes differ from the run summary")
        manifest = PublicReplayManifest.model_validate(_bounded_json(path))
        _model_bytes(path, manifest, exclude_none=True)
        if (manifest.run_id != run_id or manifest.trace_sha256 != summary.public_trace.sha256
                or manifest.event_chain_root != summary.seal.event_chain_root):  # type: ignore[union-attr]
            raise ValueError("replay manifest identity differs from the sealed run")
        if len(manifest.files) != identity.file_count:
            raise ValueError("replay manifest file count differs from the run summary")
        if sum(item.size_bytes for item in manifest.files) != identity.total_size_bytes:
            raise ValueError("replay manifest byte total differs from the run summary")
        return manifest, path

    manifest, manifest_path = checked("read and authenticate the fresh replay manifest", load_manifest)

    def verify_inventory() -> dict[str, Any]:
        replay_root = manifest_path.parent.resolve(strict=True)
        verified_bytes = 0
        for item in manifest.files:
            target = (replay_root / item.relative_path).resolve(strict=True)
            target.relative_to(replay_root)
            if target.is_symlink() or not target.is_file():
                raise ValueError(f"replay inventory entry is not a regular file: {item.relative_path}")
            if target.stat().st_size != item.size_bytes or _sha256_file(target) != item.sha256:
                raise ValueError(f"replay inventory entry failed byte verification: {item.relative_path}")
            verified_bytes += item.size_bytes
        if manifest.scene_state_history is None:
            raise ValueError("replay manifest has no sealed SceneState history")
        return {"file_count": len(manifest.files), "total_size_bytes": verified_bytes}

    inventory = checked("read and digest-check every sealed replay file", verify_inventory)

    requested = set(browser.get("replayAssetDigests", []))
    if any(not isinstance(item, str) or len(item) != 64 or not set(item) <= SHA256 for item in requested):
        raise ValueError("browser reported an invalid replay asset digest")
    replay_index: PublicReplayIndex | None = None
    missing_browser_digests: list[str] = []

    def validate_replay_mode() -> None:
        nonlocal replay_index, missing_browser_digests
        if manifest.replay_mode == "indexed":
            if manifest.replay_index is None:
                raise ValueError("indexed replay has no index descriptor")
            index_path = manifest_path.parent / manifest.replay_index.relative_path
            replay_index = PublicReplayIndex.model_validate(_bounded_json(index_path))
            _model_bytes(index_path, replay_index)
            expected = {manifest.replay_index.sha256, manifest.scene_state_history.sha256,
                        *(shard.sha256 for shard in replay_index.shards)}
            missing_browser_digests = sorted(expected - requested)
            if missing_browser_digests:
                raise ValueError("browser did not read every sealed replay index/history/shard byte set")
        elif manifest.replay_mode == "embedded":
            if manifest.replay_index is not None:
                raise ValueError("embedded replay unexpectedly declares an index")
        else:
            raise ValueError(f"unsupported replay mode {manifest.replay_mode}")

    checked("match browser replay reads to the declared replay mode", validate_replay_mode)

    def validate_browser_result() -> None:
        counts = browser.get("checkCounts")
        if not isinstance(counts, dict) or counts.get("failed") != 0 or not isinstance(counts.get("passed"), int):
            raise ValueError("browser checks did not all pass")
        if browser.get("runId") != run_id or browser.get("terminalPhase") != "completed":
            raise ValueError("browser did not observe the fresh run's completed terminal phase")
        if browser.get("replayPhase") != "verified" or browser.get("verdict") != "通过":
            raise ValueError("browser did not render the verified passed replay")
        if browser.get("coverage") != "覆盖完整":
            raise ValueError("browser did not render complete verifier coverage")
        if browser.get("nativeMeshStatus") != expected_native_mesh_status:
            raise ValueError("browser native-mesh state differs from the explicit harness expectation")
        if browser.get("replayTraceResponses", 0) < 1 or browser.get("replayManifestResponses", 0) < 1:
            raise ValueError("browser did not read the fresh sealed trace and replay manifest")

    checked("confirm the browser terminal and verified replay observations", validate_browser_result)

    evidence = {
        "run_summary": {
            "status": summary.status,
            "execution_scope": summary.execution_scope,
            "failure_classes": list(summary.failure_classes),
            "verification_status": summary.verification.status if summary.verification is not None else None,
        },
        "public_trace": {
            "relative_path": trace_path.relative_to(run_root).as_posix(),
            "sha256": summary.public_trace.sha256,  # type: ignore[union-attr]
            "phase": trace.phase,
            "tick": trace.time.tick,
            "scene_state_count": len(trace.scene_states),
            "event_count": len(trace.events),
            "verdict": trace.verifier_public.status if trace.verifier_public is not None else None,
            "coverage_complete": trace.verifier_public.coverage_complete if trace.verifier_public is not None else False,
        },
        "sealed_replay": {
            "relative_path": manifest_path.relative_to(run_root).as_posix(),
            "sha256": summary.public_trace.replay.sha256,  # type: ignore[union-attr]
            "mode": manifest.replay_mode,
            "file_count": inventory["file_count"],
            "total_size_bytes": inventory["total_size_bytes"],
            "scene_state_history_sha256": manifest.scene_state_history.sha256,
            "index_shard_count": 0 if replay_index is None else len(replay_index.shards),
            "indexed_scene_state_count": None if replay_index is None else replay_index.scene_state_count,
            "browser_asset_read_count": len(requested),
            "browser_missing_required_digests": missing_browser_digests,
            "all_files_digest_verified": True,
        },
    }
    return evidence, checks


def validate(args: argparse.Namespace) -> bool:
    root = Path(__file__).resolve().parents[1]
    frontend = root / "frontend"
    native_manifest = _resolve(root, args.native_scenes_manifest)
    runner_config = _resolve(root, args.runner_config)
    output = _resolve(root, args.output)
    node_script = frontend / "scripts" / "city-reference-e2e.mjs"
    if output.exists():
        raise FileExistsError(f"fresh I3 output already exists: {output}")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)

    report: dict[str, Any] = {
        "schema_version": "aero-bench.city-browser-reference-audit/v1",
        "started_at_utc": _utc_now(),
        "status": "running",
        "passed": False,
        "credentials_persisted": False,
        "fresh_compilation": False,
        "fresh_execution": False,
        "inputs": {
            "native_scene_registry": {"path": _relative(root, native_manifest),
                                      "sha256": _sha256_file(native_manifest)},
            "runner_config": {"path": _relative(root, runner_config),
                              "sha256": _sha256_file(runner_config)},
            "workspace_storage_key": args.workspace_storage_key,
            "expected_native_mesh_status": args.expected_native_mesh_status,
        },
    }
    authoring = None
    authoring_thread: threading.Thread | None = None
    publisher: ScenePackPublisher | None = None
    manager: ControlRunManager | None = None
    control = None
    control_thread: threading.Thread | None = None
    vite: subprocess.Popen[bytes] | None = None
    browser: subprocess.Popen[str] | None = None
    build_log = None
    vite_log = None
    browser_log = None
    bootstrap = ""
    csrf = ""
    checks: list[dict[str, Any]] = []
    try:
        registry = NativeSceneRegistry.from_manifest(native_manifest)
        compiler = CityDraftCompiler(output / "compilations", registry)
        publisher = ScenePackPublisher(output / "publication", repository_root=root)
        authoring = make_server("127.0.0.1", args.authoring_port, publisher, compiler=compiler)
        authoring_thread = threading.Thread(target=authoring.serve_forever,
                                            name="i3-authoring", daemon=True)
        authoring_thread.start()
        _wait_http(f"http://127.0.0.1:{args.authoring_port}/authoring/v1/native-scenes",
                   timeout_seconds=args.service_timeout_seconds)

        environment = os.environ.copy()
        environment["AERO_AUTHORING_API_TARGET"] = f"http://127.0.0.1:{args.authoring_port}"
        environment["AERO_CONTROL_API_TARGET"] = f"http://127.0.0.1:{args.control_port}"
        if args.frozen_frontend_dist is None:
            frozen_dist = output / "frontend-dist"
            build_log = (output / "frontend-build.log").open("wb")
            build = subprocess.run(
                [str(frontend / "node_modules" / ".bin" / "vite"), "build",
                 "--outDir", str(frozen_dist), "--emptyOutDir"],
                cwd=frontend, env=environment, stdout=build_log, stderr=subprocess.STDOUT,
                timeout=args.build_timeout_seconds, check=False,
            )
            build_log.flush()
            if build.returncode != 0:
                raise RuntimeError(f"private frontend build failed with code {build.returncode}")
            report["inputs"]["frozen_frontend"] = {
                "mode": "built", "path": _relative(root, frozen_dist),
                "city_studio_html_sha256": _sha256_file(frozen_dist / "city-studio.html"),
                "index_html_sha256": _sha256_file(frozen_dist / "index.html"),
            }
        else:
            frozen_dist = _resolve(root, args.frozen_frontend_dist)
            frozen_dist.relative_to(root.resolve())
            if not frozen_dist.is_dir():
                raise FileNotFoundError(f"frozen frontend dist is unavailable: {frozen_dist}")
            for entrypoint in ("city-studio.html", "index.html"):
                if not (frozen_dist / entrypoint).is_file():
                    raise FileNotFoundError(f"frozen frontend entrypoint is unavailable: {entrypoint}")
            (output / "frontend-build.log").write_text(
                f"Reused frozen frontend bundle: {_relative(root, frozen_dist)}\n",
                encoding="utf-8",
            )
            report["inputs"]["frozen_frontend"] = {
                "mode": "reused", "path": _relative(root, frozen_dist),
                "city_studio_html_sha256": _sha256_file(frozen_dist / "city-studio.html"),
                "index_html_sha256": _sha256_file(frozen_dist / "index.html"),
            }
        vite_log = (output / "vite.log").open("wb")
        vite = subprocess.Popen(
            [str(frontend / "node_modules" / ".bin" / "vite"), "preview",
             "--host", "127.0.0.1", "--port", str(args.frontend_port), "--strictPort",
             "--outDir", str(frozen_dist)],
            cwd=frontend, env=environment, stdout=vite_log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        frontend_origin = f"http://127.0.0.1:{args.frontend_port}"
        _wait_http(f"{frontend_origin}/city-studio.html?tab=compile",
                   timeout_seconds=args.service_timeout_seconds, process=vite)

        browser_log = (output / "browser-stderr.log").open("w", encoding="utf-8")
        browser = subprocess.Popen(
            ["node", str(node_script)], cwd=frontend,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=browser_log,
            text=True, bufsize=1, start_new_session=True,
        )
        _send(browser, {"type": "init", "origin": frontend_origin, "outputDir": str(output),
                        "studioTimeoutMs": args.studio_timeout_seconds * 1000,
                        "workspaceStorageKey": args.workspace_storage_key,
                        "expectedNativeMeshStatus": args.expected_native_mesh_status})
        compiled = _receive(browser, timeout_seconds=args.studio_timeout_seconds)
        if compiled.get("type") == "failed":
            raise RuntimeError(f"browser failed before compilation: {compiled.get('error')}")
        if compiled.get("type") != "compiled":
            raise RuntimeError(f"unexpected first browser event: {compiled.get('type')}")
        compilation_id = compiled.get("compilationId")
        run_id = compiled.get("runId")
        registration_id = compiled.get("registrationId")
        if not all(isinstance(value, str) for value in (compilation_id, run_id, registration_id)):
            raise ValueError("browser compilation event has invalid identities")
        report["fresh_compilation"] = True
        print(f"I3 compiled {compilation_id} to fresh Run {run_id}", flush=True)

        manager = ControlRunManager.from_compilation(
            compilation_root=output / "compilations", compilation_id=compilation_id,
            runner_config_path=runner_config, output_root=output / "execution",
        )
        if len(manager.catalog.runs) != 1 or manager.catalog.runs[0].run_id != run_id:
            raise ValueError("Control catalog identity differs from the browser compilation")
        bootstrap = secrets.token_hex(32)
        csrf = secrets.token_hex(32)
        control = ControlHttpServer(
            manager=manager,
            config=ControlHttpConfig(
                schema_version="aero-bench.control-http-config/v1",
                bind_host="127.0.0.1", port=args.control_port,
                allowed_origins=(frontend_origin,),
                allowed_hosts=(f"127.0.0.1:{args.control_port}",),
                requests_per_minute=10_000,
            ),
            bootstrap_token=bootstrap, bootstrap_csrf_token=csrf,
        )
        control_thread = threading.Thread(target=control.serve_forever,
                                          name="i3-control", daemon=True)
        control_thread.start()
        run_timeout_seconds = (manager._runtime_timeout_seconds
                               + manager._verifier_timeout_seconds + 1800)
        _send(browser, {
            "type": "control-ready",
            "baseUrl": f"http://127.0.0.1:{args.control_port}",
            "runId": run_id,
            "bootstrapToken": bootstrap,
            "csrfToken": csrf,
            "runTimeoutMs": run_timeout_seconds * 1000,
        })
        started = _receive(browser, timeout_seconds=args.service_timeout_seconds,
                           forbidden=(bootstrap, csrf))
        if started.get("type") == "failed":
            raise RuntimeError(f"browser flow failed before run start: {started.get('error')}")
        if started.get("type") != "started" or started.get("runId") != run_id:
            raise RuntimeError(f"unexpected run-start browser event: {started.get('type')}")
        report["fresh_execution"] = True
        print(f"I3 browser started fresh Run {run_id}", flush=True)
        final = _receive(browser, timeout_seconds=run_timeout_seconds + 300,
                         forbidden=(bootstrap, csrf))
        if final.get("type") == "failed":
            raise RuntimeError(f"browser flow failed: {final.get('error')}")
        if final.get("type") != "finished":
            raise RuntimeError(f"unexpected terminal browser event: {final.get('type')}")
        browser.wait(timeout=120)
        if browser.returncode != 0:
            raise RuntimeError(f"browser process exited with code {browser.returncode}")
        print(f"I3 browser observed terminal {final.get('terminalPhase')} and replay {final.get('replayPhase')}",
              flush=True)

        execution, checks = _validate_sealed_execution(
            execution_root=output / "execution", run_id=run_id, browser=final,
            expected_native_mesh_status=args.expected_native_mesh_status,
        )
        browser_counts = final["checkCounts"]
        report.update({
            "status": "passed", "passed": True, "completed_at_utc": _utc_now(),
            "compilation_id": compilation_id, "registration_id": registration_id,
            "run_id": run_id,
            "browser": {
                "check_counts": browser_counts,
                "render_backend": "hardware",
                "renderer": final.get("renderer"),
                "native_mesh_status": final.get("nativeMeshStatus"),
                "terminal_phase": final.get("terminalPhase"),
                "replay_phase": final.get("replayPhase"),
                "verdict": final.get("verdict"),
                "coverage": final.get("coverage"),
                "sealed_trace_response_count": final.get("replayTraceResponses"),
                "sealed_manifest_response_count": final.get("replayManifestResponses"),
                "sealed_asset_read_count": len(final.get("replayAssetDigests", [])),
                "screenshots": ["01-reference-loaded.png", "02-terminal-control.png",
                                "03-sealed-verified-replay.png"],
            },
            "execution": execution,
            "orchestrator_checks": checks,
            "check_counts": {
                "passed": browser_counts["passed"] + sum(item["status"] == "passed" for item in checks),
                "failed": browser_counts["failed"] + sum(item["status"] == "failed" for item in checks),
                "total": browser_counts["total"] + len(checks),
            },
            "artifacts": {
                "browser_observations": "browser-observations.json",
                "private_frontend_build": _relative(root, frozen_dist),
                "frontend_build_log": "frontend-build.log",
                "vite_log": "vite.log", "browser_stderr_log": "browser-stderr.log",
            },
        })
        return True
    except BaseException as error:
        failure = str(error)
        for credential in (bootstrap, csrf):
            if credential:
                failure = failure.replace(credential, "[redacted]")
        report.update({"status": "failed", "passed": False,
                       "completed_at_utc": _utc_now(), "failure": failure,
                       "orchestrator_checks": checks})
        return False
    finally:
        if browser is not None and browser.poll() is None:
            _stop_process_group(browser)
        if control is not None:
            control.shutdown()
        if control_thread is not None:
            control_thread.join(timeout=60)
        _stop_process_group(vite)
        if authoring is not None:
            authoring.shutdown()
            authoring.server_close()
        if authoring_thread is not None:
            authoring_thread.join(timeout=30)
        if publisher is not None:
            publisher.close()
        if browser_log is not None:
            browser_log.close()
        if vite_log is not None:
            vite_log.close()
        if build_log is not None:
            build_log.close()
        bootstrap = ""
        csrf = ""
        (output / "browser-flow-report.json").write_bytes(canonical_json_bytes(report) + b"\n")
        print(f"I3 browser reference audit {report['status'].upper()}: "
              f"{output / 'browser-flow-report.json'}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-scenes-manifest", type=Path,
                        default=Path("validation/backend-track-b-20260930/registered-reference-r2/native-scenes.json"))
    parser.add_argument("--runner-config", type=Path,
                        default=Path("validation/backend-track-b-20260930/reference-run-r5/runner.local.yaml"))
    parser.add_argument("--output", type=Path,
                        default=Path("validation/codex-takeover-20261001/I3"))
    parser.add_argument("--authoring-port", type=int, default=8140)
    parser.add_argument("--control-port", type=int, default=8141)
    parser.add_argument("--frontend-port", type=int, default=5392)
    parser.add_argument("--service-timeout-seconds", type=int, default=300)
    parser.add_argument("--studio-timeout-seconds", type=int, default=900)
    parser.add_argument("--build-timeout-seconds", type=int, default=1800)
    parser.add_argument("--frozen-frontend-dist", type=Path)
    parser.add_argument("--workspace-storage-key", required=True)
    parser.add_argument("--expected-native-mesh-status", required=True,
                        choices=("not_declared", "loaded_verified"))
    args = parser.parse_args()
    return 0 if validate(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
