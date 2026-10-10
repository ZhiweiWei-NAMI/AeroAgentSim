from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import subprocess
import time
from pathlib import Path

import aero_frame_math as frame_math
from mavsdk import System


PROTOCOL_VERSION = "aero-bench.px4-gazebo-rpc/v4"
PX4_COMMIT = "381149fb012762f5e38c4a7fdc1b905b28038970"
PX4_VERSION = "v1.17.0-alpha1-1551-g381149fb01"
GAZEBO_VERSION = "8.11.0"
GAZEBO_COMMIT = "1be3cc376fec778cc725b4eeea463245affa56d3"
MAVSDK_COMMIT = "9e3ca17faa84aa868caea10a3bbdab7e53810ced"
MAVSDK_VERSION = "3.17.2"
PHYSICS_STEP_NS = 4_000_000
PHYSICS_STEPS = 2_500
MAXIMUM_AGENT_DECISION_WALL_TIME_S = 60
HEARTBEAT_TIMEOUT_FIXED_MARGIN_S = 10
INCOMING_HEARTBEAT_TIMEOUT_S = (
    MAXIMUM_AGENT_DECISION_WALL_TIME_S + HEARTBEAT_TIMEOUT_FIXED_MARGIN_S
)
WORLD = "default"


def _start(
    command: tuple[str, ...], log_path: Path, *, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    log = log_path.open("wb")
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    log.close()
    return process


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _wait_for_service(
    service: str, process: subprocess.Popen[bytes], timeout_s: float
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"process exited before Gazebo service {service} became ready"
            )
        result = subprocess.run(
            ("gz", "service", "--info", "--service", service),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if (
            result.returncode == 0
            and "Service providers" in result.stdout + result.stderr
        ):
            return
        time.sleep(1)
    raise RuntimeError(f"Gazebo service {service} did not become ready")


def _wait_for_log(
    path: Path, text: str, process: subprocess.Popen[bytes], timeout_s: float
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"process exited before emitting {text!r}: {path.read_text(errors='replace')[-2000:]}"
            )
        if path.exists() and text in path.read_text(errors="replace"):
            return
        time.sleep(0.25)
    tail = path.read_text(errors="replace")[-2000:] if path.exists() else ""
    raise RuntimeError(f"timed out waiting for {text!r}: {tail}")


def _step_world() -> None:
    result = subprocess.run(
        (
            "gz",
            "service",
            "--service",
            f"/world/{WORLD}/control",
            "--reqtype",
            "gz.msgs.WorldControl",
            "--reptype",
            "gz.msgs.Boolean",
            "--timeout",
            "30000",
            "--req",
            f"pause: true, multi_step: {PHYSICS_STEPS}",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=35,
    )
    if "data: true" not in result.stdout:
        raise RuntimeError(
            f"Gazebo rejected physics step: {result.stdout}{result.stderr}"
        )


def _sim_time_ns() -> int:
    result = subprocess.run(
        (
            "gz",
            "topic",
            "--echo",
            "--topic",
            f"/world/{WORLD}/stats",
            "--num",
            "1",
            "--json-output",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    payload = json.loads(result.stdout)
    if "simTime" not in payload:
        raise RuntimeError(f"Gazebo stats omitted simTime: {payload}")
    sim_time = payload["simTime"]
    return int(sim_time.get("sec", 0)) * 1_000_000_000 + int(sim_time.get("nsec", 0))


def _wait_for_sim_time(target_ns: int, timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        reached_ns = _sim_time_ns()
        if reached_ns == target_ns:
            return reached_ns
        if reached_ns > target_ns:
            raise RuntimeError(
                f"Gazebo overshot {target_ns} ns and reached {reached_ns} ns"
            )
        time.sleep(0.25)
    raise RuntimeError(f"Gazebo did not reach {target_ns} ns before timeout")


async def _mavsdk_evidence() -> dict[str, object]:
    vehicle = System(mavsdk_server_address="127.0.0.1", port=50051)
    await vehicle.connect()
    connection_deadline = asyncio.get_running_loop().time() + 20
    async for state in vehicle.core.connection_state():
        if state.is_connected:
            break
        if asyncio.get_running_loop().time() >= connection_deadline:
            raise RuntimeError("MAVSDK did not discover the PX4 SITL system")

    position = await asyncio.wait_for(
        vehicle.telemetry.position().__anext__(), timeout=10
    )
    health = await asyncio.wait_for(vehicle.telemetry.health().__anext__(), timeout=10)
    values = (
        position.latitude_deg,
        position.longitude_deg,
        position.absolute_altitude_m,
        position.relative_altitude_m,
    )
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError("MAVSDK returned non-finite authoritative telemetry")
    return {
        "absolute_altitude_m": position.absolute_altitude_m,
        "global_position_ok": health.is_global_position_ok,
        "home_position_ok": health.is_home_position_ok,
        "latitude_deg": position.latitude_deg,
        "longitude_deg": position.longitude_deg,
        "relative_altitude_m": position.relative_altitude_m,
    }


def _check_runtime_contract() -> None:
    identity_path = Path("/opt/aero-bench/identity.json")
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("PX4 identity metadata is unavailable") from exc
    if (
        not isinstance(identity, dict)
        or identity.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise RuntimeError("PX4 identity protocol does not match staged RPC v4")
    transform = frame_math.EnuTransform.from_origin(
        longitude_deg=121.5,
        latitude_deg=31.2,
        altitude_m=20.0,
    )
    point = frame_math.Vector3(x=10.0, y=-5.0, z=3.0)
    recovered = transform.enu_from_ecef(transform.ecef_from_enu(point))
    if any(
        not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-8)
        for actual, expected in (
            (recovered.x, point.x),
            (recovered.y, point.y),
            (recovered.z, point.z),
        )
    ):
        raise RuntimeError("shared frame_math ENU transform selfcheck failed")


def main() -> int:
    _check_runtime_contract()
    root = Path("/tmp/aero-px4-selfcheck")
    home = root / "home"
    px4_work = root / "px4"
    home.mkdir(parents=True, exist_ok=False)
    px4_work.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "HEADLESS": "1",
            "GZ_PARTITION": "aero-px4-selfcheck",
            "HOME": str(home),
            "PX4_GZ_MODEL_POSE": "0,0,0,0,0,0",
            "PX4_GZ_NO_FOLLOW": "1",
            "PX4_GZ_STANDALONE": "1",
            "PX4_GZ_WORLD": WORLD,
            "PX4_SIM_MODEL": "gz_x500",
        }
    )
    os.environ.update(env)
    gazebo: subprocess.Popen[bytes] | None = None
    px4: subprocess.Popen[bytes] | None = None
    mavsdk: subprocess.Popen[bytes] | None = None
    try:
        gazebo_log = root / "gazebo.log"
        gazebo = _start(
            (
                "gz",
                "sim",
                "--verbose=1",
                "-s",
                f"/opt/px4-gazebo/share/gz/worlds/{WORLD}.sdf",
            ),
            gazebo_log,
            env=env,
        )
        _wait_for_service(f"/world/{WORLD}/scene/info", gazebo, 90)

        px4_log = root / "px4.log"
        px4 = _start(
            (
                "/opt/px4-gazebo/bin/px4",
                "-i",
                "0",
                "-w",
                str(px4_work),
                "/opt/px4-gazebo/etc",
            ),
            px4_log,
            env=env,
        )
        _wait_for_log(px4_log, "Spawning Gazebo model", px4, 30)

        mavsdk_log = root / "mavsdk.log"
        command_audit_journal = root / "mavsdk-command-audit.jsonl"
        mavsdk = _start(
            (
                "/opt/mavsdk/mavsdk_server",
                "-p",
                "50051",
                "--incoming-heartbeat-timeout-s",
                str(INCOMING_HEARTBEAT_TIMEOUT_S),
                "--command-audit-journal-path",
                str(command_audit_journal),
                "udpin://0.0.0.0:14540",
            ),
            mavsdk_log,
            env=env,
        )
        _step_world()
        if not command_audit_journal.is_file():
            raise RuntimeError(
                "mavsdk_server did not create the required command audit journal"
            )
        _wait_for_log(px4_log, "Startup script returned successfully", px4, 30)
        telemetry = asyncio.run(_mavsdk_evidence())
        expected_ns = PHYSICS_STEP_NS * PHYSICS_STEPS
        reached_ns = _wait_for_sim_time(expected_ns, 60)

        print(
            json.dumps(
                {
                    "gazebo_commit": GAZEBO_COMMIT,
                    "gazebo_version": GAZEBO_VERSION,
                    "mavsdk_commit": MAVSDK_COMMIT,
                    "mavsdk_version": MAVSDK_VERSION,
                    "physics_steps": PHYSICS_STEPS,
                    "px4_commit": PX4_COMMIT,
                    "px4_version": PX4_VERSION,
                    "sim_time_ns": reached_ns,
                    "status": "real-px4-gazebo-mavsdk-ok",
                    "telemetry": telemetry,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    finally:
        _stop(mavsdk)
        _stop(px4)
        _stop(gazebo)


if __name__ == "__main__":
    raise SystemExit(main())
