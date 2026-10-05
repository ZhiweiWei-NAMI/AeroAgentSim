from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aero_bench.artifacts.contracts import ArtifactRecord, seal_manifest
from aero_bench.config.models import NamedValue
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes

IMAGE = "aero-bench/px4-gazebo:runtime"
RUN_ID = "b" * 64
TOKEN = "c" * 64
SEED = 19
PHYSICS_NS = 4_000_000
STEP_NS = 2_000_000_000
PORT = 17432
PROTOCOL = "aero-bench.px4-gazebo-rpc/v1"
TRAJECTORY_PATH = "private/flight/trajectory.json"
OBSERVATION_PATH = "private/flight/observation.json"
SENSOR_FRAME_PATH = "public/flight/sensor-frames.json"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical(value))


def ref(root: Path, relative: str) -> dict[str, str]:
    data = (root / relative).read_bytes()
    return {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def rpc(
    connection: socket.socket, operation: str, payload: dict[str, object]
) -> dict[str, object]:
    connection.sendall(canonical({"operation": operation, **payload}) + b"\n")
    response = b""
    while not response.endswith(b"\n"):
        chunk = connection.recv(65536)
        if not chunk:
            raise RuntimeError(f"service closed during {operation}")
        response += chunk
    decoded = json.loads(response)
    if "error" in decoded:
        raise RuntimeError(f"{operation}: {decoded['error']}")
    summary: dict[str, object] = {"stage": operation}
    if operation == "observe":
        observation = decoded["observation"]
        payload_map = {
            item["name"]: item["value"] for item in observation["payload"]
        }
        summary.update(
            {
                "observation_id": observation["observation_id"],
                "payload_digest": observation["payload_digest"],
                "distance_m": payload_map["distance_m"],
                "view_angle_deg": payload_map["view_angle_deg"],
                "visual_kind": payload_map["visual_kind"],
                "visual_status": payload_map["visual_status"],
            }
        )
    elif operation == "step_to":
        summary["reached"] = decoded["receipt"]["reached"]
        summary["events"] = [
            event["event_id"] for event in decoded["receipt"]["events"]
        ]
    elif operation == "command":
        summary["phases"] = [item["phase"] for item in decoded["receipts"]]
    elif operation == "finalize":
        summary["artifacts"] = decoded["receipt"]["artifacts"]
    else:
        summary["response"] = decoded
    print(json.dumps(summary, sort_keys=True), flush=True)
    return decoded


def image_identity() -> tuple[str, str]:
    raw = subprocess.run(
        ("docker", "image", "inspect", IMAGE),
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    ).stdout
    image = json.loads(raw)[0]
    image_sha = image["Id"].removeprefix("sha256:")
    revision = image["Config"]["Labels"]["org.opencontainers.image.revision"]
    return image_sha, revision


def main() -> None:
    image_sha, revision = image_identity()
    runtime_image = f"local.invalid/aero-bench/px4-gazebo@sha256:{image_sha}"
    root = Path(tempfile.mkdtemp(prefix="aero-px4-live-debug-"))
    bundle = root / "bundle"
    artifacts = root / "artifacts"
    runtime_tmp = root / "runtime-tmp"
    bundle.mkdir(mode=0o755)
    artifacts.mkdir(mode=0o777)
    runtime_tmp.mkdir(mode=0o777)
    (artifacts / "private").mkdir(mode=0o777)
    (artifacts / "private").chmod(0o777)
    artifacts.chmod(0o777)
    runtime_tmp.chmod(0o777)
    target_model_path = bundle / "inspection-target.sdf"
    shutil.copyfile(
        REPO_ROOT / "releases/inspection-v1/assets/inspection-target.sdf",
        target_model_path,
    )
    target_model_ref = ref(bundle, "inspection-target.sdf")
    container = f"aero-px4-live-{os.getpid()}"
    host_port = free_port()

    vehicle = {
        "vehicle_id": "uav.1",
        "system_id": 1,
        "mavsdk_udp_port": 14540,
        "px4_mavlink_udp_port": 14580,
        "mavsdk_grpc_port": 15040,
        "sys_autostart": 4001,
        "model": "gz_x500_mono_cam",
        "gazebo_model_name": "x500_mono_cam_0",
        "gazebo_resource": "x500_mono_cam",
        "initial_pose": {
            "x_m": 0.0,
            "y_m": 0.0,
            "z_m": 0.1,
            "roll_rad": 0.0,
            "pitch_rad": 0.0,
            "yaw_rad": 0.0,
        },
    }
    inspection = {
        "work_order_id": "work-order.1",
        "target_id": "target.1",
        "target_position": {"x_m": 2.0, "y_m": 0.03, "z_m": 0.6},
        "target_model_asset_id": "asset.inspection-target",
        "observation_id": "observation.1",
        "camera_id": "camera.mono.1",
        "camera_vehicle_id": "uav.1",
        "camera_mount": {
            "x_m": 0.12,
            "y_m": 0.03,
            "z_m": 0.242,
            "roll_rad": 0.0,
            "pitch_rad": 0.0,
            "yaw_rad": 0.0,
        },
        "metadata_schema": None,
        "media_type": "application/json",
        "min_distance_m": 0.5,
        "max_distance_m": 5.0,
        "min_view_angle_deg": 0.0,
        "max_view_angle_deg": 30.0,
        "earliest_time_ns": 0,
        "latest_time_ns": 10_000_000_000,
    }
    config = {
        "schema_version": "aero-bench.px4-gazebo/v1",
        "provider_id": "flight",
        "px4": {
            "version": "v1.17.0-alpha1-1551-g381149fb01",
            "commit": "381149fb012762f5e38c4a7fdc1b905b28038970",
        },
        "gazebo": {
            "version": "8.11.0",
            "commit": "1be3cc376fec778cc725b4eeea463245affa56d3",
        },
        "mavsdk": {
            "version": "3.17.2",
            "commit": "9e3ca17faa84aa868caea10a3bbdab7e53810ced",
        },
        "world_name": "default",
        "world_sdf": "default.sdf",
        "physics_step_ns": PHYSICS_NS,
        "step_length_ns": STEP_NS,
        "px4_executable": "px4",
        "gazebo_executable": "gz",
        "mavsdk_server_executable": "mavsdk-server",
        "vehicles": [vehicle],
        "required_commands": ["px4", "gz", "mavsdk-server"],
        "command_timeout_ms": 120000,
        "maximum_agent_decision_wall_time_ms": 30000,
        "heartbeat_timeout_fixed_margin_ms": 10000,
        "inspection": inspection,
    }
    observation_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "target_id",
            "camera_id",
            "distance_m",
            "view_angle_deg",
            "visual_kind",
            "visual_status",
        ],
        "properties": {
            "schema_version": {"const": "aero-bench.observation.geometry.v1"},
            "target_id": {"type": "string"},
            "camera_id": {"type": "string"},
            "distance_m": {"type": "number", "exclusiveMinimum": 0},
            "view_angle_deg": {"type": "number", "minimum": 0},
            "visual_kind": {"const": "external_renderer"},
            "visual_status": {"const": "reserved"},
        },
    }
    write_json(bundle / "observation.schema.json", observation_schema)
    inspection["metadata_schema"] = ref(bundle, "observation.schema.json")
    write_json(bundle / "provider.config.json", config)
    write_json(
        bundle / "provider.schema.json",
        {"$schema": "https://json-schema.org/draft/2020-12/schema"},
    )
    config_ref = ref(bundle, "provider.config.json")
    schema_ref = ref(bundle, "provider.schema.json")
    requirements = [
        {
            "artifact_id": "artifact.flight.trajectory",
            "artifact_type": "trajectory",
            "producer_id": "flight",
            "visibility": "private",
            "relative_path": TRAJECTORY_PATH,
            "max_size_bytes": 16_777_216,
            "source_asset_id": None,
        },
        {
            "artifact_id": "artifact.flight.observation",
            "artifact_type": "observation",
            "producer_id": "flight",
            "visibility": "private",
            "relative_path": OBSERVATION_PATH,
            "max_size_bytes": 8_388_608,
            "source_asset_id": None,
        },
        {
            "artifact_id": "artifact.flight.sensor-frames",
            "artifact_type": "sensor-frame",
            "producer_id": "flight",
            "visibility": "public",
            "relative_path": SENSOR_FRAME_PATH,
            "max_size_bytes": 8_388_608,
            "source_asset_id": None,
        },
    ]
    contract = {
        "schema_version": "aero-bench.workload-contract/v1",
        "role": "provider",
        "run_id": RUN_ID,
        "seed": SEED,
        "workload_id": "flight",
        "clock": {
            "authority": "provider_barrier",
            "step_ns": STEP_NS,
            "max_steps": 3,
            "provider_timeout_ms": 120000,
        },
        "provider": {
            "provider_id": "flight",
            "adapter": "px4.gazebo",
            "port": PORT,
            "workload": {
                "runtime": {"image": runtime_image, "command": ["provider", "serve"]},
                "resources": {
                    "cpu_millicores": 2000,
                    "memory_mib": 4096,
                    "gpu_count": 0,
                },
                "implementation": {
                    "component_id": "px4.gazebo",
                    "kind": "production",
                    "source_uri": "https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
                    "source_revision": revision,
                    "version": "0.2.0-px4-gazebo.1",
                },
            },
            "config": {"file": config_ref, "schema_file": schema_ref},
            "protocol_schema": schema_ref,
            "capabilities": ["flight.command", "observation.capture"],
            "artifact_requirements": requirements,
        },
        "assets": [
            {
                "asset_id": "asset.inspection-target",
                "file": target_model_ref,
                "classification": "private",
                "audiences": [
                    {"role": "provider", "workload_ids": ["flight"]},
                ],
            }
        ],
    }
    contract_path = root / "contract.json"
    write_json(contract_path, contract)

    subprocess.run(("docker", "rm", "-f", container), capture_output=True)
    command = (
        "docker",
        "run",
        "--detach",
        "--name",
        container,
        "--read-only",
        "--user",
        "65532:65532",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--shm-size",
        "1g",
        "--mount",
        f"type=bind,src={runtime_tmp},dst=/tmp",
        "--publish",
        f"127.0.0.1:{host_port}:{PORT}",
        "--mount",
        f"type=bind,src={bundle},dst=/input/bundle,readonly",
        "--mount",
        f"type=bind,src={artifacts},dst=/input/artifacts",
        "--mount",
        f"type=bind,src={contract_path},dst=/input/contract.json,readonly",
        "--env",
        "AERO_BENCH_ROLE=provider",
        "--env",
        "AERO_BENCH_PROVIDER_BIND_HOST=0.0.0.0",
        "--env",
        f"AERO_BENCH_PROVIDER_PORT={PORT}",
        "--env",
        f"AERO_BENCH_RUN_ID={RUN_ID}",
        "--env",
        f"AERO_BENCH_SEED={SEED}",
        "--env",
        "AERO_BENCH_WORKLOAD_ID=flight",
        "--env",
        "AERO_BENCH_CONTRACT=/input/contract.json",
        "--env",
        "AERO_BENCH_BUNDLE_DIR=/input/bundle",
        "--env",
        "AERO_BENCH_ARTIFACT_DIR=/input/artifacts",
        "--env",
        f"AERO_BENCH_PROVIDER_TOKEN={TOKEN}",
        "--env",
        "TMPDIR=/tmp",
        IMAGE,
    )
    started = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if started.returncode:
        raise RuntimeError(started.stderr or started.stdout)
    print(
        json.dumps(
            {
                "stage": "container_started",
                "container_id": started.stdout.strip(),
                "host_port": host_port,
                "image_sha256": image_sha,
                "revision": revision,
            }
        ),
        flush=True,
    )

    connection: socket.socket | None = None
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                probe = socket.create_connection(("127.0.0.1", host_port), timeout=2)
                response = rpc(probe, "probe", {})
                probe.close()
                if response.get("status") == "accepting":
                    break
            except (OSError, RuntimeError):
                time.sleep(0.25)
        else:
            raise RuntimeError("provider listener did not become ready")

        connection = socket.create_connection(("127.0.0.1", host_port), timeout=5)
        connection.settimeout(300)
        prepare = {
            "provider_id": "flight",
            "run_id": RUN_ID,
            "protocol_version": PROTOCOL,
            "session_token": TOKEN,
            "runtime_image": runtime_image,
            "config_digest": config_ref["sha256"],
            "artifact_requirements": requirements,
            "endpoint": {"host": "px4-provider", "port": PORT},
            **{
                key: config[key]
                for key in (
                    "px4",
                    "gazebo",
                    "mavsdk",
                    "world_name",
                    "world_sdf",
                    "physics_step_ns",
                    "step_length_ns",
                    "px4_executable",
                    "gazebo_executable",
                    "mavsdk_server_executable",
                    "vehicles",
                    "required_commands",
                    "command_timeout_ms",
                    "maximum_agent_decision_wall_time_ms",
                    "heartbeat_timeout_fixed_margin_ms",
                    "inspection",
                )
            },
        }
        rpc(connection, "prepare", prepare)
        rpc(
            connection,
            "reset",
            {
                "provider_id": "flight",
                "run_id": RUN_ID,
                "seed": SEED,
                "session_token": TOKEN,
            },
        )
        rpc(
            connection,
            "step_to",
            {
                "provider_id": "flight",
                "run_id": RUN_ID,
                "session_token": TOKEN,
                "request": {
                    "run_id": RUN_ID,
                    "target": {"tick": 1, "sim_time_ns": STEP_NS},
                },
            },
        )
        observation = rpc(
            connection,
            "observe",
            {
                "provider_id": "flight",
                "run_id": RUN_ID,
                "session_token": TOKEN,
                "agent_id": "agent.1",
                "observation_id": "observation.1",
                "requested_at": {"tick": 1, "sim_time_ns": STEP_NS},
            },
        )["observation"]
        payload = {item["name"]: item["value"] for item in observation["payload"]}
        if hashlib.sha256(canonical(payload)).hexdigest() != observation["payload_digest"]:
            raise RuntimeError("agent-visible observation digest mismatch")
        if (
            payload.get("schema_version") != "aero-bench.observation.geometry.v1"
            or payload.get("visual_kind") != "external_renderer"
            or payload.get("visual_status") != "reserved"
            or "captured_bytes_b64" in payload
        ):
            raise RuntimeError("observation payload is not geometry-only")
        paused_seconds = 12
        print(
            json.dumps(
                {
                    "stage": "long_paused_barrier",
                    "paused_seconds": paused_seconds,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        time.sleep(paused_seconds)
        command_response = rpc(
            connection,
            "command",
            {
                "provider_id": "flight",
                "run_id": RUN_ID,
                "session_token": TOKEN,
                "request": {
                    "run_id": RUN_ID,
                    "command_id": "command.hold",
                    "agent_id": "agent.1",
                    "tool_id": "flight.hold",
                    "issued_at": {"tick": 1, "sim_time_ns": STEP_NS},
                    "arguments": [{"name": "vehicle_id", "value": "uav.1"}],
                },
            },
        )
        phases = [item["phase"] for item in command_response["receipts"]]
        if phases != ["received", "accepted"]:
            raise RuntimeError(f"invalid deferred command phases: {phases}")
        second_step = rpc(
            connection,
            "step_to",
            {
                "provider_id": "flight",
                "run_id": RUN_ID,
                "session_token": TOKEN,
                "request": {
                    "run_id": RUN_ID,
                    "target": {"tick": 2, "sim_time_ns": 2 * STEP_NS},
                },
            },
        )
        sensor_frame_events = [
            event
            for event in second_step["receipt"]["events"]
            if event["event_id"] == "public.sensor-frame"
        ]
        if len(sensor_frame_events) != 1:
            raise RuntimeError("captured frame did not emit one public sensor reference")
        sensor_frame_payload = {
            item["name"]: item["value"]
            for item in sensor_frame_events[0]["payload"]
        }
        if sensor_frame_payload != {
            "artifact_id": "artifact.flight.sensor-frames",
            "frame_id": "frame.agent.1.observation.1.0000000000000001",
            "observation_id": "observation.1",
            "payload_digest": observation["payload_digest"],
            "selector": "frames/frame.agent.1.observation.1.0000000000000001",
        }:
            raise RuntimeError("public sensor reference does not bind the captured frame")
        command_events = [
            event
            for event in second_step["receipt"]["events"]
            if event["payload_schema_id"] == "px4.command.v1"
        ]
        deferred_phases = [
            next(
                item["value"]
                for item in event["payload"]
                if item["name"] == "final_phase"
            )
            for event in command_events
        ]
        if not deferred_phases or deferred_phases[-1] != "completed":
            raise RuntimeError(
                f"deferred physical command did not complete: {deferred_phases}"
            )
        snapshot = rpc(
            connection,
            "snapshot",
            {"provider_id": "flight", "run_id": RUN_ID, "session_token": TOKEN},
        )
        ledger = EventLedger(run_id=RUN_ID)
        ledger.append_event(
            source="harness",
            source_kind="harness",
            workload_id="harness",
            event_type="validation.scope",
            time=SimulationTime(tick=0, sim_time_ns=0),
            payload=(
                NamedValue(
                    name="execution_scope", value="executor_validation"
                ),
            ),
        )
        ledger.append_event(
            source="flight",
            source_kind="provider",
            workload_id="flight",
            event_type="px4.lifecycle-validated",
            time=SimulationTime(tick=2, sim_time_ns=2 * STEP_NS),
            payload=(
                NamedValue(name="command_phase", value="completed"),
                NamedValue(name="paused_seconds", value=paused_seconds),
                NamedValue(
                    name="trajectory_sha256",
                    value=hashlib.sha256(
                        (artifacts / TRAJECTORY_PATH).read_bytes()
                    ).hexdigest(),
                ),
                NamedValue(
                    name="observation_sha256",
                    value=hashlib.sha256(
                        (artifacts / OBSERVATION_PATH).read_bytes()
                    ).hexdigest(),
                ),
                NamedValue(name="payload_digest", value=observation["payload_digest"]),
                NamedValue(
                    name="snapshot_digest", value=snapshot["snapshot_digest"]
                ),
            ),
            interaction_type="physical.effect.v1",
            correlation_id="px4.lifecycle-validation",
            provider_id="flight",
            command_id="px4.lifecycle-validation",
        )
        terminal_event = ledger.new_event(
            source="harness",
            source_kind="harness",
            workload_id="harness",
            event_type="run.completed",
            time=SimulationTime(tick=2, sim_time_ns=2 * STEP_NS),
            payload=(
                NamedValue(name="execution_scope", value="executor_validation"),
            ),
        )
        terminal_chain_root = ledger.prospective_chain_root(terminal_event)
        final = rpc(
            connection,
            "finalize",
            {
                "provider_id": "flight",
                "run_id": RUN_ID,
                "session_token": TOKEN,
                "request": {
                    "schema_version": "aero-bench.provider-finalization-request/v1",
                    "run_id": RUN_ID,
                    "terminal_event": "run.completed",
                    "terminal_time": {"tick": 2, "sim_time_ns": 2 * STEP_NS},
                    "event_chain_root": terminal_chain_root,
                },
            },
        )
        received = {
            item["artifact_id"]: item for item in final["receipt"]["artifacts"]
        }
        expected_artifacts = {
            requirement["artifact_id"]: artifacts / requirement["relative_path"]
            for requirement in requirements
        }
        if set(received) != set(expected_artifacts):
            raise RuntimeError("finalization artifact inventory mismatch")
        for artifact_id, path in expected_artifacts.items():
            data = path.read_bytes()
            if received[artifact_id] != {
                "artifact_id": artifact_id,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }:
                raise RuntimeError(f"finalization receipt mismatch for {artifact_id}")
        trajectory = json.loads(expected_artifacts["artifact.flight.trajectory"].read_bytes())
        observations = json.loads(expected_artifacts["artifact.flight.observation"].read_bytes())
        sensor_frames = json.loads(
            expected_artifacts["artifact.flight.sensor-frames"].read_bytes()
        )
        if len(trajectory) != 3 or len(observations) != 1 or len(sensor_frames) != 1:
            raise RuntimeError(
                "canonical evidence counts are wrong: "
                f"{len(trajectory)=}, {len(observations)=}, {len(sensor_frames)=}"
            )
        if (
            sensor_frames[0]["payload_digest"] != observation["payload_digest"]
            or "captured_bytes_b64" in sensor_frames[0]
        ):
            raise RuntimeError("public sensor-frame artifact differs from geometry observation")
        if canonical(trajectory) != expected_artifacts[
            "artifact.flight.trajectory"
        ].read_bytes() or canonical(observations) != expected_artifacts[
            "artifact.flight.observation"
        ].read_bytes() or canonical(sensor_frames) != expected_artifacts[
            "artifact.flight.sensor-frames"
        ].read_bytes():
            raise RuntimeError("inspection evidence is not canonical direct JSON")

        ledger.append(terminal_event)
        if ledger.chain_root != terminal_chain_root:
            raise RuntimeError("prospective terminal event-chain root changed")
        event_log_path = artifacts / "private/harness/event.log"
        ledger.write_jsonl(event_log_path)
        records = [
            ArtifactRecord(
                artifact_id=requirement["artifact_id"],
                artifact_type=requirement["artifact_type"],
                producer_id=requirement["producer_id"],
                visibility=requirement["visibility"],
                relative_path=requirement["relative_path"],
                sha256=received[requirement["artifact_id"]]["sha256"],
                size_bytes=received[requirement["artifact_id"]]["size_bytes"],
            )
            for requirement in requirements
        ]
        event_log_bytes = event_log_path.read_bytes()
        records.append(
            ArtifactRecord(
                artifact_id="artifact.harness.event-log",
                artifact_type="event.log",
                producer_id="harness",
                visibility="private",
                relative_path="private/harness/event.log",
                sha256=hashlib.sha256(event_log_bytes).hexdigest(),
                size_bytes=len(event_log_bytes),
            )
        )
        seal = seal_manifest(
            root=artifacts,
            run_id=RUN_ID,
            attempt_id="attempt." + hashlib.sha256(str(root.resolve()).encode()).hexdigest(),
            execution_scope="executor_validation",
            event_chain_root=ledger.chain_root,
            artifacts=tuple(records),
        )
        seal_path = root / "seal.json"
        seal_path.write_bytes(
            canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
        )
        rpc(
            connection,
            "shutdown",
            {"provider_id": "flight", "run_id": RUN_ID, "session_token": TOKEN},
        )
        connection.close()
        connection = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = subprocess.run(
                (
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.Status}} {{.State.ExitCode}}",
                    container,
                ),
                capture_output=True,
                text=True,
            )
            if state.returncode == 0 and state.stdout.strip() == "exited 0":
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("container did not exit cleanly")
        verifier = subprocess.run(
            (
                sys.executable,
                str(REPO_ROOT / "tools/verify_px4_lifecycle.py"),
                str(artifacts),
                str(seal_path),
            ),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )
        print(
            json.dumps(
                {
                    "stage": "independent_verifier",
                    "report": json.loads(verifier.stdout),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "stage": "lifecycle_complete",
                    "command_phases": deferred_phases,
                    "trajectory_records": len(trajectory),
                    "observation_records": len(observations),
                    "payload_digest": observation["payload_digest"],
                    "visual_kind": payload["visual_kind"],
                    "target_model_sha256": target_model_ref["sha256"],
                    "container_exit": 0,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    finally:
        if connection is not None:
            connection.close()
        logs = subprocess.run(
            ("docker", "logs", container),
            capture_output=True,
            text=True,
            timeout=20,
        )
        print("--- container stdout ---")
        print(logs.stdout[-12000:])
        print("--- container stderr ---")
        print(logs.stderr[-12000:])
        debug = Path("/tmp/px4-debug-logs")
        shutil.rmtree(debug, ignore_errors=True)
        subprocess.run(
            ("docker", "cp", f"{container}:/tmp/logs", str(debug)),
            capture_output=True,
            timeout=30,
        )
        print(f"stack logs copied to {debug}")
        subprocess.run(
            ("docker", "rm", "-f", container), capture_output=True, timeout=20
        )
        print(f"debug runtime retained at {root}")


if __name__ == "__main__":
    main()
