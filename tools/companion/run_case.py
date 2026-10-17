#!/usr/bin/env python3
"""Run one rescue case in a fresh Compose project; keep every failed attempt."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/rescue-companion/execution-package/04_scenario_cases.yaml"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def cases() -> list[dict]:
    return yaml.safe_load(SPEC.read_text())["cases"]


def run_c00(run: Path, command: list[str], env: dict[str, str], timeout: float) -> None:
    """Original backend smokes, then one private companion flight client.

    This exercises concrete flight/radio paths, not the whole C00 requirement.
    Baseline services are separate from the later shared-world coordinator.
    """
    steps: list[dict] = []
    containers: list[str] = []
    deadline = time.monotonic() + timeout

    def execute(name: str, argv: list[str], *, input_text: str | None = None) -> str:
        entry = {"step": name, "command": argv}
        steps.append(entry)
        write_json(run / "c00-steps.json", steps)
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=max(0.1, deadline - time.monotonic()),
            check=False,
        )
        (run / (name + ".log")).write_text(completed.stdout + completed.stderr)
        entry["exit_code"] = completed.returncode
        write_json(run / "c00-steps.json", steps)
        if completed.returncode:
            raise RuntimeError(
                f"C00 {name} failed ({completed.returncode}); see {name}.log"
            )
        return completed.stdout.strip()

    try:
        for backend in ("px4-gazebo", "ns3"):
            name = command[-1] + "-baseline-" + backend
            execute(
                "start-" + backend,
                [
                    "docker",
                    "run",
                    "-d",
                    "--pull",
                    "never",
                    "--name",
                    name,
                    "-p",
                    "127.0.0.1::9000",
                    f"aeroagentsim/{backend}:standalone",
                ],
            )
            containers.append(name)
            address = execute("port-" + backend, ["docker", "port", name, "9000/tcp"])
            port = address.splitlines()[0].rsplit(":", 1)[1]
            # Reuse the deployment's read-only listener check. Opening a
            # separate probe socket would briefly claim the native world owner.
            service = "simulator" if backend == "px4-gazebo" else "radio"
            deployment = yaml.safe_load(
                (ROOT / "deploy/rescue/compose.yaml").read_text()
            )
            listener_command = deployment["services"][service]["healthcheck"]["test"][
                1:
            ]
            ready_until = min(deadline, time.monotonic() + 30)
            while True:
                ready = subprocess.run(
                    ["docker", "exec", name, *listener_command],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if ready.returncode == 0:
                    break
                if time.monotonic() >= ready_until:
                    (run / ("listener-" + backend + ".log")).write_text(
                        ready.stdout + ready.stderr
                    )
                    raise RuntimeError(f"{backend} did not open its RPC listener")
                time.sleep(0.2)
            argv = [
                sys.executable,
                str(ROOT / "containers" / backend / "smoke.py"),
                "--host",
                "127.0.0.1",
                "--port",
                port,
            ]
            if backend == "px4-gazebo":
                argv += [
                    "--vehicles",
                    "1",
                    "--runs",
                    "1",
                    "--step-ms",
                    "20",
                    "--seed",
                    env["SEED"],
                    "--output",
                    str(run / "baseline-px4.json"),
                ]
            else:
                argv += ["--output", str(run / "baseline-ns3")]
            execute("smoke-" + backend, argv)
    finally:
        for name in containers:
            with (run / (name + ".log")).open("w") as log:
                subprocess.run(
                    ["docker", "logs", name],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                    check=False,
                )
            subprocess.run(
                ["docker", "stop", "--time", "10", name],
                capture_output=True,
                timeout=60,
                check=False,
            )
    # Use explicit diagnostic tasks, not C01's protected survey commitments.
    scene = yaml.safe_load(
        (ROOT / "scenarios/rescue/rooftop-flood-companion.yaml").read_text()
    )
    for uid, vehicle in scene["vehicles"].items():
        vehicle["initial_task"] = {
            "task_id": "c00-flight-" + uid,
            "task_revision": 1,
            "kind": "flight_diagnostic",
        }
    scene_path = run / "c00-scenario.yaml"
    scene_path.write_text(yaml.safe_dump(scene, sort_keys=False))
    override = run / "c00-compose.yaml"
    override.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "coordinator": {
                        "command": [
                            "--scenario",
                            "/case/scene.yaml",
                            "--run-id",
                            env["RUN_ID"],
                            "--data",
                            "/data",
                            "--socket-root",
                            "/run/aas",
                            "--seed",
                            env["SEED"],
                            "--until-ns",
                            "900000000000",
                        ],
                        "volumes": [str(scene_path) + ":/case/scene.yaml:ro"],
                    }
                }
            },
            sort_keys=False,
        )
    )
    private_command = command + ["-f", str(override)]
    # No normal UAV or centre runtime runs alongside this diagnostic client.
    execute(
        "start-private-flight",
        private_command
        + [
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "simulator",
            "radio",
            "coordinator",
        ],
    )
    script = r"""
import json, os, time
from pathlib import Path
from aeroagentsim.companion.flight import FlightClient
client = FlightClient(os.environ['COMPANION_SOCKET'], os.environ['RUN_ID'])
log = Path('/data/logs/c00-flight-client.jsonl')
log.parent.mkdir(parents=True, exist_ok=True)
end = time.monotonic() + 1200

def request(op, **params):
    response = client.request(op, **params)
    with log.open('a') as stream:
        stream.write(json.dumps({'op': op, 'params': params, 'response': response}) + '\n')
    return response

def until(call, predicate):
    while time.monotonic() < end:
        try:
            value = call()
        except (ConnectionError, FileNotFoundError):
            time.sleep(.1)
            continue
        if predicate(value):
            return value
        time.sleep(.02)
    raise TimeoutError('C00 private flight reached its wall-time deadline')

notice = None
while notice is None and time.monotonic() < end:
    inbox = until(lambda: request('radio_inbox'), lambda r: r.get('status') == 'available')
    notice = next((m for m in inbox['messages'] if m.get('kind') == 'notice'), None)
    time.sleep(.02)
if notice is None:
    raise RuntimeError('No initial task actually received by radio')
task = {'task_id': notice['task_id'], 'task_revision': notice['task_revision']}
request('task_accept', message_id=notice['message_id'], **task)
state = until(lambda: request('state'), lambda r: r.get('vehicle_estimate', {}).get('status') == 'available')
origin = state['vehicle_estimate']['position_enu_m']
for action, params in [('arm', {}), ('takeoff', {'altitude_m': 10}),
    ('goto', {'position_enu_m': [origin[0] + 5, origin[1], origin[2] + 10], 'yaw_deg': 0}),
    ('hold', {}), ('land', {})]:
    submitted = request('submit', action=action, params=params, **task)
    completed = until(lambda: request('poll', operation_id=submitted['operation_id']),
                      lambda r: r.get('status') in {'succeeded', 'failed', 'canceled', 'rejected'})
    if completed['status'] != 'succeeded':
        raise RuntimeError('Flight action did not succeed: ' + json.dumps(completed))
    request('state')
print('Private flight sequence returned; independent estimated telemetry still requires review.')
"""
    (run / "c00-flight-client.py").write_text(script)
    execute(
        "private-flight",
        private_command
        + ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "uav-u1", "-"],
        input_text=script,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[row["id"] for row in cases()])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--agent", choices=("rule", "model"), default="rule")
    parser.add_argument("--attempt", default="a1")
    parser.add_argument("--variant")
    parser.add_argument("--run-root", type=Path, default=ROOT / "runs/acceptance")
    parser.add_argument("--wall-timeout", type=float, default=7200)
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the supplied case specification; do not run",
    )
    args = parser.parse_args()
    if args.list:
        print(json.dumps(cases(), ensure_ascii=False, indent=2))
        return 0
    if not args.case:
        parser.error("--case is required unless --list is used")
    case = next(row for row in cases() if row["id"] == args.case)
    if args.seed not in case["seeds"] or args.agent not in case["agent_modes"]:
        parser.error("seed or agent mode is not declared for this case")
    variants = case.get("variants", [])
    if (variants and args.variant not in variants) or (not variants and args.variant):
        parser.error(
            f"--variant must be one of {variants}"
            if variants
            else "case has no variants"
        )
    if not re.fullmatch(r"a?[1-9][0-9]*", args.attempt):
        parser.error("--attempt must be a positive integer, optionally prefixed with a")
    if not math.isfinite(args.wall_timeout) or args.wall_timeout <= 0:
        parser.error("--wall-timeout must be positive")
    attempt = int(args.attempt.removeprefix("a"))
    prefix = f"{args.case}-{args.seed}-{args.agent}"
    base_id = f"{prefix}-a{attempt}"
    run_id = base_id + (f"-{args.variant}" if args.variant else "")
    root = args.run_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Reserve the attempt across variants, not merely the final directory name.
    reservations = root / ".attempts"
    reservations.mkdir(exist_ok=True)
    previous = [
        int(p.name.rsplit("-a", 1)[1]) for p in reservations.glob(prefix + "-a*")
    ]
    if previous and attempt <= max(previous):
        parser.error(
            f"attempt must exceed the retained attempt a{max(previous)} for {prefix}"
        )
    run = root / run_id
    if run.exists():
        parser.error(f"refusing to overwrite {run}")
    try:
        (reservations / base_id).mkdir()
        run.mkdir()
    except FileExistsError:
        parser.error("this run or attempt already exists; choose a new attempt")
    for identity in ("u1", "u2", "u3", "u4", "centre", "coordinator", "simulator"):
        (run / "data" / identity).mkdir(parents=True, mode=0o700)
    for identity in ("u1", "u2", "u3", "u4", "centre"):
        (run / "data/sockets" / identity).mkdir(parents=True, mode=0o700)
    compose = ROOT / "deploy/rescue/compose.yaml"
    env = os.environ.copy()
    env.update(
        RUN_ID=run_id,
        AGENT_MODE=args.agent,
        CASE_ID=args.case,
        CASE_VARIANT=args.variant or "",
        SEED=str(args.seed),
        RUN_ROOT=str(run / "data"),
        AAS_UID=str(os.getuid()),
        AAS_GID=str(os.getgid()),
    )
    command = ["docker", "compose", "-f", str(compose), "-p", "aas-" + run_id.lower()]
    manifest = {
        "run_id": run_id,
        "case_id": args.case,
        "seed": args.seed,
        "agent_mode": args.agent,
        "attempt": attempt,
        "variant_id": args.variant,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "execution_status": "PREPARED",
        "native_started": False,
        "native_launch_attempted": False,
        "case_specification": case,
        "command": command,
        "note": "Execution status is not a functional acceptance result.",
    }
    write_json(run / "run-manifest.json", manifest)
    shutil.copyfile(SPEC, run / "case-specification.yaml")
    started = time.monotonic()
    try:
        if not shutil.which("docker"):
            raise RuntimeError(
                "Docker executable is unavailable; native cases were not run"
            )
        if not compose.is_file():
            raise RuntimeError("Rescue Compose deployment is unavailable")
        with (run / "preflight.log").open("w") as log:
            for check in (["docker", "info"], ["docker", "compose", "version"]):
                result = subprocess.run(
                    check,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    timeout=30,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(f"{check!r} failed; see preflight.log")
        # Cases require their own real stimuli. Running C01 repeatedly does not
        # exercise cancellation, restart, packet loss or the image benchmark.
        if args.case not in {"C00", "C01"}:
            raise RuntimeError(
                f"{args.case} has no native case driver yet; its specification is retained"
            )
        manifest["native_launch_attempted"] = True
        manifest["execution_status"] = "RUNNING"
        write_json(run / "run-manifest.json", manifest)
        if args.case == "C00":
            run_c00(run, command, env, args.wall_timeout)
            manifest.update(execution_status="FINISHED", exit_code=0)
        else:
            with (run / "compose.log").open("w") as log:
                completed = subprocess.run(
                    command
                    + [
                        "up",
                        "--no-build",
                        "--pull",
                        "never",
                        "--abort-on-container-exit",
                        "--exit-code-from",
                        "coordinator",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=args.wall_timeout,
                    check=False,
                )
            manifest.update(
                execution_status="FINISHED" if completed.returncode == 0 else "FAIL",
                exit_code=completed.returncode,
            )
    except (
        RuntimeError,
        OSError,
        subprocess.TimeoutExpired,
        KeyboardInterrupt,
    ) as error:
        manifest.update(
            execution_status="FAIL"
            if manifest["native_launch_attempted"]
            else "BLOCKED",
            reason=str(error) or "Interrupted by operator",
        )
    finally:
        if manifest["native_launch_attempted"]:
            # Stop only this unique project; preserve containers, bind data and logs.
            with (run / "stop.log").open("w") as log:
                try:
                    stopped = subprocess.run(
                        command + ["stop", "--timeout", "10"],
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=60,
                        check=False,
                    )
                    manifest["stop_exit_code"] = stopped.returncode
                except (OSError, subprocess.TimeoutExpired) as error:
                    manifest["stop_error"] = str(error)
        manifest["native_started"] = any(
            path.stat().st_size
            for filename in ("native-flight-events.jsonl", "estimated-telemetry.jsonl")
            for path in run.rglob(filename)
        )
        baseline = run / "baseline-px4.json"
        if baseline.is_file():
            manifest["native_started"] = manifest["native_started"] or any(
                row.get("trajectory")
                for row in json.loads(baseline.read_text()).get("runs", [])
            )
        manifest.update(
            finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            wall_elapsed_s=time.monotonic() - started,
        )
        write_json(run / "run-manifest.json", manifest)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "run": str(run),
                **{
                    key: manifest[key] for key in ("execution_status", "native_started")
                },
                "reason": manifest.get("reason"),
            },
            indent=2,
        )
    )
    return 0 if manifest["execution_status"] == "FINISHED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
