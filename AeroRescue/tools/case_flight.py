"""Concrete retained-evidence rescue case actions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLATFORM_ROOT = Path(os.environ.get("AEROAGENTSIM_ROOT", str(ROOT.parent))).resolve()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run_flight_case(
    run: Path,
    command: list[str],
    env: dict[str, str],
    timeout: float,
    case_id: str = "C00",
    variant: str | None = None,
) -> None:
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
        write_json(run / (case_id.lower() + "-steps.json"), steps)
        try:
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
        except subprocess.TimeoutExpired as error:
            partial = b"".join(
                value if isinstance(value, bytes) else value.encode()
                for value in (error.stdout, error.stderr)
                if value
            )
            (run / (name + ".log")).write_bytes(
                partial if isinstance(partial, bytes) else partial.encode()
            )
            entry["error"] = "wall-time deadline exceeded"
            write_json(run / (case_id.lower() + "-steps.json"), steps)
            raise
        (run / (name + ".log")).write_text(completed.stdout + completed.stderr)
        entry["exit_code"] = completed.returncode
        write_json(run / (case_id.lower() + "-steps.json"), steps)
        if completed.returncode:
            raise RuntimeError(
                f"{case_id} {name} failed ({completed.returncode}); see {name}.log"
            )
        return completed.stdout.strip()

    if case_id == "C00":
        try:
            for backend in ("px4-gazebo", "ns3"):
                name = "aas-" + env["RUN_ID"].lower() + "-baseline-" + backend
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
                address = execute(
                    "port-" + backend, ["docker", "port", name, "9000/tcp"]
                )
                port = address.splitlines()[0].rsplit(":", 1)[1]
                # Reuse the deployment's read-only listener check. Opening a
                # separate probe socket would briefly claim the native world owner.
                service = "simulator" if backend == "px4-gazebo" else "radio"
                deployment = yaml.safe_load(
                    (ROOT / "deploy/compose.yaml").read_text()
                )
                listener_command = deployment["services"][service]["healthcheck"][
                    "test"
                ][1:]
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
                    str(PLATFORM_ROOT / "containers" / backend / "smoke.py"),
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
                for operation in (
                    ["docker", "logs", name],
                    ["docker", "stop", "--time", "10", name],
                ):
                    with (run / (name + ".log")).open("a") as log:
                        try:
                            subprocess.run(
                                operation,
                                stdout=log,
                                stderr=subprocess.STDOUT,
                                timeout=60,
                                check=False,
                            )
                        except (OSError, subprocess.TimeoutExpired) as error:
                            log.write(f"\nCleanup error: {error}\n")
    # Use explicit diagnostic tasks, not C01's protected survey commitments.
    scene = yaml.safe_load(
        (ROOT / "scenarios/rooftop-flood-companion.yaml").read_text()
    )
    for uid, vehicle in scene["vehicles"].items():
        vehicle["initial_task"] = {
            "task_id": case_id.lower() + "-flight-" + uid,
            "task_revision": 1,
            "kind": "flight_diagnostic",
        }
    if case_id == "C05":
        scene.pop("payload", None)  # Required unloaded cancellation condition.
    scene_path = run / (case_id.lower() + "-scenario.yaml")
    scene_path.write_text(yaml.safe_dump(scene, sort_keys=False))
    override = run / (case_id.lower() + "-compose.yaml")
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
from aerorescue.companion.flight import FlightClient
client = FlightClient(os.environ['COMPANION_SOCKET'], os.environ['RUN_ID'])
log = Path('/data/logs/flight-case-client.jsonl')
log.parent.mkdir(parents=True, exist_ok=True)
end = time.monotonic() + 1200

def request(op, **params):
    try:
        response = client.request(op, **params)
    except RuntimeError as error:
        with log.open('a') as stream:
            stream.write(json.dumps({'op': op, 'params': params, 'error': str(error)}) + '\n')
        raise
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
accepted = request('task_accept', message_id=notice['message_id'], **task)
if accepted.get('status') == 'pending':
    acceptance_id = accepted['request_id']
    accepted = until(lambda: request('result', pending_request_id=acceptance_id), lambda r: r.get('status') != 'pending')
if accepted.get('status') != 'accepted':
    raise RuntimeError('Task was not accepted: ' + json.dumps(accepted))
state = until(lambda: request('state'), lambda r: r.get('vehicle_estimate', {}).get('status') == 'available')
origin = state['vehicle_estimate']['position_enu_m']

terminal = {'succeeded', 'failed', 'canceled', 'rejected', 'reconcile_required'}
def finish(operation, expected='succeeded'):
    result = until(lambda: request('poll', operation_id=operation['operation_id']), lambda r: r.get('status') in terminal)
    if result['status'] != expected:
        raise RuntimeError('Flight action ended unexpectedly: ' + json.dumps(result))
    return result

def submit(action, params=None, revision=None):
    return request('submit', action=action, params=params or {}, task_id=task['task_id'], task_revision=task['task_revision'] if revision is None else revision)

if CASE_ID == 'C05' and VARIANT == 'queued_not_dispatched':
    operation = submit('arm')
    request('cancel', operation_id=operation['operation_id'])
    canceled = finish(operation, 'canceled')
    if canceled.get('dispatched'):
        raise RuntimeError('Coordinator dispatched before cancellation; this attempt did not exercise queued cancellation')
    watch_from = request('state')['sim_ns']
    until(lambda: request('state'), lambda r: r['sim_ns'] >= watch_from + 5_000_000_000)
    after = request('poll', operation_id=operation['operation_id'])
    if after.get('dispatched') or after['status'] != 'canceled':
        raise RuntimeError('Queued cancellation later dispatched or changed state')
else:
    finish(submit('arm'))
    finish(submit('takeoff', {'altitude_m': 10}))
    distance = 80 if CASE_ID == 'C05' else 5
    operation = submit('goto', {'position_enu_m': [origin[0] + distance, origin[1], origin[2] + 10], 'yaw_deg': 0})
    if CASE_ID == 'C05':
        import math
        def moving(state):
            estimate = state.get('vehicle_estimate', {})
            velocity = estimate.get('velocity_enu_m_s', [])
            return len(velocity) == 3 and estimate.get('status') == 'available' and 2.75 <= math.hypot(*velocity[:2]) <= 3.25
        initial = until(lambda: request('state'), moving)
        request('cancel', operation_id=operation['operation_id'])
        canceled = finish(operation, 'canceled')
        # Old-revision action injection is explicit, never a live model response.
        try:
            submit('goto', {'position_enu_m': [origin[0] + 80, origin[1], origin[2] + 10], 'yaw_deg': 0}, task['task_revision'] - 1)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('Stale-revision action was accepted')
        watch_from = request('state')['sim_ns']
        until(lambda: request('state'), lambda r: r['sim_ns'] >= watch_from + 5_000_000_000)
        if request('poll', operation_id=operation['operation_id'])['status'] != 'canceled':
            raise RuntimeError('Canceled operation revived during the observation window')
    else:
        finish(operation)
    finish(submit('hold'))
    finish(submit('land'))
print('Flight requests completed; source telemetry and native events are retained for review.')
"""
    script = (
        "CASE_ID = " + repr(case_id) + "\nVARIANT = " + repr(variant) + "\n" + script
    )
    (run / "flight-case-client.py").write_text(script)
    execute(
        "private-flight",
        private_command
        + ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "uav-u1", "-"],
        input_text=script,
    )
