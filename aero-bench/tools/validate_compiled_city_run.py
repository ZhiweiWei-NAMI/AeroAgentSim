#!/usr/bin/env python3
"""Execute one accepted compilation over authenticated Control and audit its result.

This invokes the declared native workloads. It is not a unit test or a mock
executor. Credentials remain in process memory and never enter the audit/log.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aero_bench.control.client import ControlApiClient  # noqa: E402
from aero_bench.control.manager import ControlRunManager  # noqa: E402
from aero_bench.control.server import ControlHttpConfig, ControlHttpServer  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402


def validate(*, compilation_root: Path, compilation_id: str,
             runner_config: Path, output: Path, port: int) -> bool:
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    manager = ControlRunManager.from_compilation(
        compilation_root=compilation_root, compilation_id=compilation_id,
        runner_config_path=runner_config, output_root=output / "execution",
    )
    if len(manager.catalog.runs) != 1:
        raise ValueError("compiled-run audit requires one resolved run")
    origin = "http://127.0.0.1:5310"
    base_url = f"http://127.0.0.1:{port}"
    bootstrap = secrets.token_hex(32)
    csrf = secrets.token_hex(32)
    server = ControlHttpServer(
        manager=manager,
        config=ControlHttpConfig(
            schema_version="aero-bench.control-http-config/v1", bind_host="127.0.0.1", port=port,
            allowed_origins=(origin,), allowed_hosts=(f"127.0.0.1:{port}",),
        ),
        bootstrap_token=bootstrap, bootstrap_csrf_token=csrf,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = ControlApiClient(base_url=base_url, origin=origin, bearer_token=bootstrap, csrf_token=csrf)
        catalog = client.catalog()
        run_id = catalog.runs[0].run_id
        started = client.start(run_id=run_id, start_id="compiled-native-audit")
        token = started.credentials.operator_token
        client = ControlApiClient(base_url=base_url, origin=origin, bearer_token=token,
                                  csrf_token=started.credentials.csrf_token)
        print(f"Started compiled native run {run_id}", flush=True)
        phases = []
        sequence = -1
        deadline = time.monotonic() + manager._runtime_timeout_seconds + manager._verifier_timeout_seconds + 1800
        while True:
            transitions = manager.wait_for_transitions(
                run_id, operator_token=token, sequence=sequence, timeout_seconds=300,
            )
            for transition in transitions:
                sequence = transition.sequence
                if not phases or phases[-1] != transition.phase:
                    phases.append(transition.phase)
                    print(f"Control phase {transition.phase}", flush=True)
            snapshot = manager.snapshot(run_id, operator_token=token)
            if snapshot.summary is not None:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("compiled-run audit exceeded the declared execution window")
        status = client.status(run_id)
        if status.snapshot.summary is None:
            raise ValueError("terminal Control response has no run summary")
        trace = manager.public_trace(run_id, operator_token=token)
        asset_check = None
        if trace is not None:
            asset = trace.scenario.assets[0]
            request = Request(f"{base_url}/v1/runs/{run_id}/assets/{asset.sha256}",
                              headers={"Authorization": f"Bearer {token}", "Origin": origin})
            with build_opener(ProxyHandler({})).open(request, timeout=120) as response:
                raw = response.read()
            asset_check = {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                           "passed": hashlib.sha256(raw).hexdigest() == asset.sha256 and len(raw) == asset.size_bytes}
        passed = (status.snapshot.summary.status == "passed"
                  and not status.management_failure_classes
                  and trace is not None and trace.phase == "verified"
                  and trace.verifier_public is not None and trace.verifier_public.status == "passed"
                  and asset_check is not None and asset_check["passed"])
        report = {
            "schema_version": "aero-bench.compiled-native-control-audit/v1",
            "compilation_id": compilation_id, "run_id": run_id, "passed": passed,
            "browser_validated": False, "http_catalog_start_status_validated": True,
            "credentials_persisted": False, "phases": phases,
            "summary": status.snapshot.summary.model_dump(mode="json"),
            "management_failure_classes": status.management_failure_classes,
            "public_asset_http_check": asset_check,
            "public_trace": None if trace is None else {
                "phase": trace.phase, "tick": trace.time.tick,
                "sim_time_ns": trace.time.sim_time_ns, "scene_state_count": len(trace.scene_states),
                "event_count": len(trace.events),
                "verdict": None if trace.verifier_public is None else trace.verifier_public.status,
            },
        }
        (output / "control-run-audit.json").write_bytes(canonical_json_bytes(report) + b"\n")
        print(f"Compiled native Control audit {'PASSED' if passed else 'FAILED'}; {output / 'control-run-audit.json'}", flush=True)
        return passed
    finally:
        server.shutdown()
        thread.join(timeout=30)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compilation-root", type=Path, required=True)
    parser.add_argument("--compilation-id", required=True)
    parser.add_argument("--runner-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    return 0 if validate(compilation_root=args.compilation_root, compilation_id=args.compilation_id,
                         runner_config=args.runner_config, output=args.output, port=args.port) else 1


if __name__ == "__main__":
    raise SystemExit(main())
