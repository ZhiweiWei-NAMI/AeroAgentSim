"""Export authoritative SUMO/TraCI trajectories for replay.

The exporter runs the declared scenario with the real SUMO executable and
serialises every observed vehicle, pedestrian, and traffic-light state.  It
never fabricates missing samples: an incomplete actor inventory or a failed
TraCI session is an error and leaves no output artifact behind.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

# Keep the direct entrypoint (`python tools/export_sumo_trajectories.py`) on
# the same repository import path as the module form.
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aero_bench.providers.sumo.scene import PERSON_COUNT, VEHICLE_COUNT
from aero_bench.serialization import canonical_json_bytes


SCHEMA_VERSION = "aero-bench.sumo-trajectory/v1"
DEFAULT_SEED = 1701
DEFAULT_BINARY = "sumo"
DEFAULT_PORT = 18913
DEFAULT_END_S = 600.0
DEFAULT_STEP_S = 0.5


class TrajectoryExportError(RuntimeError):
    """Raised when SUMO cannot produce a complete authoritative trace."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_float(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TrajectoryExportError(f"{label} is not numeric") from exc
    if not result == result or result in {float("inf"), float("-inf")}:
        raise TrajectoryExportError(f"{label} is not finite")
    return result


def _scenario_files(scenario_root: Path, config_path: Path) -> tuple[Path, Path, Path, float, float]:
    try:
        root = scenario_root.resolve(strict=True)
        config = config_path.resolve(strict=True)
    except OSError as exc:
        raise TrajectoryExportError("SUMO scenario or configuration is unavailable") from exc
    if not root.is_dir() or not config.is_file() or not config.is_relative_to(root):
        raise TrajectoryExportError("SUMO configuration must be inside the scenario directory")
    try:
        xml_root = ET.parse(config).getroot()
    except (OSError, ET.ParseError) as exc:
        raise TrajectoryExportError("SUMO configuration is invalid XML") from exc
    if xml_root.tag != "configuration":
        raise TrajectoryExportError("SUMO configuration root must be <configuration>")
    inputs = xml_root.find("input")
    if inputs is None:
        raise TrajectoryExportError("SUMO configuration has no input section")

    def input_file(tag: str) -> Path:
        element = inputs.find(tag)
        value = element.attrib.get("value") if element is not None else None
        if not value or Path(value).is_absolute() or "\\" in value:
            raise TrajectoryExportError(f"SUMO {tag} must be a relative file reference")
        candidate = (root / Path(value)).resolve(strict=True)
        if not candidate.is_file() or not candidate.is_relative_to(root):
            raise TrajectoryExportError(f"SUMO {tag} is outside the scenario directory")
        return candidate

    network = input_file("net-file")
    routes = input_file("route-files")
    additional_element = inputs.find("additional-files")
    additional = (
        input_file("additional-files")
        if additional_element is not None
        else None
    )
    if additional is None:
        # The formal scenario declares an additional file.  Requiring it here
        # keeps the exported digest inventory closed and avoids an implicit
        # alternate configuration.
        raise TrajectoryExportError("SUMO configuration must declare additional-files")

    time_section = xml_root.find("time")
    end_s = DEFAULT_END_S
    step_s = DEFAULT_STEP_S
    if time_section is not None:
        if time_section.find("end") is not None:
            end_s = _finite_float(time_section.find("end").attrib.get("value"), "time.end")
        if time_section.find("step-length") is not None:
            step_s = _finite_float(
                time_section.find("step-length").attrib.get("value"),
                "time.step-length",
            )
    if end_s <= 0.0 or step_s <= 0.0:
        raise TrajectoryExportError("SUMO time bounds must be positive")
    return network, routes, additional, end_s, step_s


def _canonical_line(value: dict[str, object]) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _port_is_available(port: int) -> bool:
    if not 1024 <= port <= 65535:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    return True


def _connect_traci(port: int, process: subprocess.Popen[Any], timeout_s: float) -> Any:
    try:
        import traci
    except ImportError as exc:
        raise TrajectoryExportError("the declared traci package is unavailable") from exc
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise TrajectoryExportError(
                f"SUMO exited before TraCI connection (status={process.returncode})"
            )
        try:
            return traci.connect(port=port, host="127.0.0.1")
        except Exception as exc:  # TraCI reports a transient connection error here.
            last_error = exc
            time.sleep(0.05)
    raise TrajectoryExportError("SUMO TraCI connection timed out") from last_error


def _object_state(domain: Any, object_id: str, *, kind: str) -> dict[str, object]:
    required = ("getPosition", "getSpeed", "getAngle", "getRoadID", "getLaneID")
    if any(not callable(getattr(domain, method, None)) for method in required):
        raise TrajectoryExportError(f"SUMO {kind} domain does not expose the required state API")
    position = domain.getPosition(object_id)
    if not isinstance(position, (tuple, list)) or len(position) < 2:
        raise TrajectoryExportError(f"SUMO {kind} {object_id} returned an invalid position")
    east_m = _finite_float(position[0], f"{kind}.{object_id}.east_m")
    north_m = _finite_float(position[1], f"{kind}.{object_id}.north_m")
    speed_mps = _finite_float(domain.getSpeed(object_id), f"{kind}.{object_id}.speed_mps")
    angle_deg = _finite_float(domain.getAngle(object_id), f"{kind}.{object_id}.angle_deg")
    if speed_mps < 0.0 or not 0.0 <= angle_deg < 360.0:
        raise TrajectoryExportError(f"SUMO {kind} {object_id} returned an out-of-domain state")
    state = {
        "object_id": object_id,
        "kind": kind,
        "position_enu_m": {"east_m": east_m, "north_m": north_m, "up_m": 0.0},
        "speed_mps": speed_mps,
        "angle_deg": angle_deg,
        "road_id": str(domain.getRoadID(object_id)),
        "lane_id": str(domain.getLaneID(object_id)),
        "lifecycle": "active",
        "telemetry_source": "sumo-traci",
    }
    if kind == "vehicle":
        route_method = getattr(domain, "getRouteID", None)
        if not callable(route_method):
            raise TrajectoryExportError("SUMO vehicle domain does not expose getRouteID")
        state["route_id"] = str(route_method(object_id))
    return state


def _traffic_light_state(domain: Any, signal_id: str) -> dict[str, object]:
    required = ("getRedYellowGreenState", "getPhase", "getNextSwitch", "getProgram")
    if any(not callable(getattr(domain, method, None)) for method in required):
        raise TrajectoryExportError("SUMO traffic-light domain does not expose the required state API")
    state = str(domain.getRedYellowGreenState(signal_id))
    phase_index = int(domain.getPhase(signal_id))
    next_switch_s = _finite_float(domain.getNextSwitch(signal_id), f"traffic_light.{signal_id}.next_switch_s")
    if not state or phase_index < 0:
        raise TrajectoryExportError(f"SUMO traffic-light {signal_id} returned an invalid state")
    return {
        "signal_id": signal_id,
        "state": state,
        "phase_index": phase_index,
        "next_switch_s": next_switch_s,
        "program_id": str(domain.getProgram(signal_id)),
        "telemetry_source": "sumo-traci",
    }


def _frame(connection: Any, tick: int, step_s: float, previous: dict[tuple[str, str], dict[str, object]]) -> dict[str, object]:
    simulation_time_s = _finite_float(connection.simulation.getTime(), "simulation.time")
    expected_ns = int(round(simulation_time_s * 1_000_000_000))
    vehicles = {str(value) for value in connection.vehicle.getIDList()}
    pedestrians = {str(value) for value in connection.person.getIDList()}
    current: dict[tuple[str, str], dict[str, object]] = {}
    vehicle_states = []
    person_states = []
    for object_id in sorted(vehicles):
        state = _object_state(connection.vehicle, object_id, kind="vehicle")
        current[("vehicle", object_id)] = state
        vehicle_states.append(state)
    for object_id in sorted(pedestrians):
        state = _object_state(connection.person, object_id, kind="person")
        current[("person", object_id)] = state
        person_states.append(state)
    # Once SUMO reports an actor as arrived, retaining its last measured pose
    # with an explicit terminal lifecycle keeps replay continuity without
    # inventing a position between ticks.
    arrived = getattr(connection.simulation, "getArrivedIDList", lambda: ())()
    arrived_people = getattr(connection.simulation, "getArrivedPersonIDList", lambda: ())()
    for object_id in sorted(str(value) for value in arrived):
        key = ("vehicle", object_id)
        if key in previous and key not in current:
            state = {**previous[key], "speed_mps": 0.0, "lifecycle": "arrived"}
            current[key] = state
            vehicle_states.append(state)
    for object_id in sorted(str(value) for value in arrived_people):
        key = ("person", object_id)
        if key in previous and key not in current:
            state = {**previous[key], "speed_mps": 0.0, "lifecycle": "arrived"}
            current[key] = state
            person_states.append(state)
    trafficlight = getattr(connection, "trafficlight", None)
    if trafficlight is None or not callable(getattr(trafficlight, "getIDList", None)):
        raise TrajectoryExportError("SUMO scenario has no traffic-light state domain")
    signal_ids = sorted(str(value) for value in trafficlight.getIDList())
    if not signal_ids:
        raise TrajectoryExportError("SUMO scenario declares no traffic lights")
    return {
        "schema_version": SCHEMA_VERSION,
        "record_type": "frame",
        "tick": tick,
        "sim_time_ns": expected_ns,
        "step_length_ns": int(round(step_s * 1_000_000_000)),
        "vehicles": sorted(vehicle_states, key=lambda value: str(value["object_id"])),
        "pedestrians": sorted(person_states, key=lambda value: str(value["object_id"])),
        "traffic_lights": [_traffic_light_state(trafficlight, signal_id) for signal_id in signal_ids],
    }


def export_trajectories(
    scenario_root: Path,
    config_path: Path,
    output_path: Path,
    *,
    seed: int = DEFAULT_SEED,
    binary: str = DEFAULT_BINARY,
    port: int = DEFAULT_PORT,
    startup_timeout_s: float = 30.0,
    max_steps: int | None = None,
) -> Path:
    """Run SUMO and write a canonical JSONL trajectory artifact."""

    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise TrajectoryExportError("seed must be a non-negative integer")
    executable = shutil.which(binary)
    if executable is None:
        raise TrajectoryExportError(f"SUMO executable is unavailable: {binary}")
    scenario_root = scenario_root.resolve()
    network, routes, additional, end_s, step_s = _scenario_files(scenario_root, config_path)
    if max_steps is None:
        max_steps = int(round(end_s / step_s)) + 1
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
        raise TrajectoryExportError("max_steps must be a positive integer")
    if not _port_is_available(port):
        raise TrajectoryExportError(f"TraCI port is unavailable: {port}")
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    process: subprocess.Popen[Any] | None = None
    connection: Any | None = None
    seen: set[tuple[str, str]] = set()
    frame_count = 0
    try:
        command = (
            executable,
            "--configuration-file",
            str(config_path.resolve()),
            "--remote-port",
            str(port),
            "--seed",
            str(seed),
            "--step-length",
            f"{step_s:g}",
            "--no-step-log",
            "--duration-log.disable",
            "--no-warnings",
        )
        process = subprocess.Popen(
            command,
            cwd=scenario_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        connection = _connect_traci(port, process, startup_timeout_s)
        version = connection.getVersion()
        if not isinstance(version, tuple) or len(version) < 2:
            raise TrajectoryExportError("SUMO TraCI returned no version identity")
        version_text = str(version[1])
        temp_file = tempfile.NamedTemporaryFile(
            mode="wb", prefix=".sumo-trajectory-", suffix=".jsonl", dir=output_path.parent, delete=False
        )
        temp_path = Path(temp_file.name)
        with temp_file:
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "record_type": "manifest",
                "source": "sumo-traci",
                "sumo_version": version_text,
                "seed": seed,
                "step_length_ns": int(round(step_s * 1_000_000_000)),
                "end_time_ns": int(round(end_s * 1_000_000_000)),
                "scenario_root": scenario_root.name,
                "scenario_files": [
                    {"path": path.relative_to(scenario_root).as_posix(), "sha256": _sha256(path)}
                    for path in (config_path.resolve(), network, routes, additional)
                ],
                "expected_inventory": {
                    "vehicles": VEHICLE_COUNT,
                    "pedestrians": PERSON_COUNT,
                },
            }
            temp_file.write(_canonical_line(manifest))
            previous: dict[tuple[str, str], dict[str, object]] = {}
            # SUMO starts at t=0.  Capture that authoritative state before the
            # first step and then every subsequent provider tick.
            for tick in range(max_steps):
                record = _frame(connection, tick, step_s, previous)
                temp_file.write(_canonical_line(record))
                frame_count += 1
                for state in (*record["vehicles"], *record["pedestrians"]):
                    seen.add((str(state["kind"]), str(state["object_id"])))
                previous = {
                    (str(state["kind"]), str(state["object_id"])): state
                    for state in (*record["vehicles"], *record["pedestrians"])
                }
                if tick + 1 >= max_steps:
                    break
                connection.simulationStep()
                if _finite_float(connection.simulation.getTime(), "simulation.time") > end_s + step_s:
                    break
            expected = {
                *(('vehicle', f"veh.{index:02d}") for index in range(1, VEHICLE_COUNT + 1)),
                *(('person', f"ped.{index:02d}") for index in range(1, PERSON_COUNT + 1)),
            }
            if not expected.issubset(seen):
                missing = sorted(expected - seen)
                raise TrajectoryExportError(f"SUMO trajectory omitted declared actors: {missing}")
            if frame_count < 2:
                raise TrajectoryExportError("SUMO trajectory contains fewer than two frames")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, output_path)
        temp_path = None
        return output_path
    except (OSError, subprocess.SubprocessError) as exc:
        raise TrajectoryExportError("SUMO trajectory export failed") from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stderr is not None:
                process.stderr.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export real SUMO/TraCI trajectories as canonical JSONL")
    parser.add_argument("--scenario-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--binary", default=DEFAULT_BINARY)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--startup-timeout-s", type=float, default=30.0)
    parser.add_argument("--max-steps", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        path = export_trajectories(
            arguments.scenario_root,
            arguments.config,
            arguments.output,
            seed=arguments.seed,
            binary=arguments.binary,
            port=arguments.port,
            startup_timeout_s=arguments.startup_timeout_s,
            max_steps=arguments.max_steps,
        )
    except TrajectoryExportError as exc:
        print(f"trajectory export failed: {exc}")
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
