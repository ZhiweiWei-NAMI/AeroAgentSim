"""Host-only native contract fixtures; none establish native/graphics acceptance.

The in-memory transport records every request, and the real kernel supplies the
public command/event/receipt path and offline replay. No native server is needed.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from aerokernel import CommandRequest, Fact, Instant, KernelError, replay
from aerokernel.values import ResourceBudget, thaw

from aeroagentsim.adapters import lockstep_base
from aeroagentsim.adapters.lockstep_base import LEGACY_OPERATIONS, NATIVE_FEATURES, NATIVE_OPERATIONS
from aeroagentsim.adapters.px4_gazebo import PX4GazeboEngine
from aeroagentsim.adapters.runner import AdapterRun
from aeroagentsim.adapters.schemas import command_descriptors, event_descriptors, vehicle_estimate_schema
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2] / "scenarios" / "adapters"
STEP = 200_000_000
ORIGIN = 10_000_000_000
EPOCH = "native-epoch"
RUN_ID = "native-run"
CAMERA = {
    "sensor_id": "rgbd", "topic_prefix": "/native/cameras", "pose_flu": [0.0] * 6,
    "width": 1, "height": 1, "update_rate_hz": 20.0,
    "horizontal_fov_rad": 2 * math.atan(0.5), "near_m": 0.1, "far_m": 100.0,
}
PAYLOAD = {
    "carrier": "u1", "package_id": "parcel", "receiver_id": "receiver",
    "detach_topic": "/payload/detach", "state_topic": "/payload/state",
    "contact_topic": "/payload/contact", "attachment_offset_enu_m": [0, 0, -0.2],
    "package_size_m": [0.1] * 3, "package_mass_kg": 0.1,
    "receiver_pose": [0] * 6, "receiver_size": [1, 1, 0.1],
    "contact_duration_ns": STEP,
}


def hello(minor: int = 1) -> dict[str, Any]:
    caps = {
        "timing": "lockstep", "exact_stop": True, "hold": True,
        "physics_step_ns": 1, "max_frame_bytes": 8 * 1024 * 1024,
        "actions": ["arm", "takeoff", "goto_location", "hold", "land", "disarm"],
        "vehicles": ["x500"],
        "sensors": ["gazebo_pose", "mavsdk_telemetry", "gazebo_contacts"],
    }
    if minor == 1:
        caps.update(operations=sorted(LEGACY_OPERATIONS | NATIVE_OPERATIONS), features=sorted(NATIVE_FEATURES))
    return {"protocol": PX4GazeboEngine.protocol, "major": 1, "minor": minor, "capabilities": caps}


def unavailable_estimate(ns: int) -> dict[str, Any]:
    return {
        "status": "unavailable", "source": "PX4_MavlinkDirect", "run_id": RUN_ID,
        "uav_id": "u1", "clock_epoch": EPOCH, "source_sim_ns": None,
        "available_sim_ns": ns, "reason": "source clock unavailable",
    }


def estimate(ns: int, source: int) -> dict[str, Any]:
    raw = []
    for name, fields in (
        ("LOCAL_POSITION_NED", {"x": 2.0, "y": 1.0, "z": -3.0, "vx": 0.0, "vy": 0.0, "vz": 0.0}),
        ("ATTITUDE_QUATERNION", {"q1": 1.0, "q2": 0.0, "q3": 0.0, "q4": 0.0}),
    ):
        raw.append({
            "message_name": name, "fields": {"time_boot_ms": source // 1_000_000, **fields},
            "px4_instance": 0, "system_id": 1, "component_id": 1,
            "received_wall_time": 123, "available_native_ns": ORIGIN + ns,
            "clock_epoch": EPOCH, "run_id": RUN_ID, "source_ns": source,
        })
    return {
        "status": "available", "source": "PX4_MavlinkDirect", "run_id": RUN_ID,
        "uav_id": "u1", "clock_epoch": EPOCH,
        "position_enu_m": [1.0, 2.0, 3.0], "velocity_enu_m_s": [0.0] * 3,
        "attitude_body_frd_to_world_enu_xyzw": [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0],
        "source_sim_ns": source, "attitude_source_sim_ns": source,
        "available_sim_ns": ns, "source_clock": "px4_boot:0",
        "clock_map": {
            "source_anchor_ns": 0, "native_anchor_ns": ORIGIN,
            "method": "paused native anchor", "uncertainty_ns": 1_000_000,
            "source_resolution_ns": 1_000_000, "mapping_assumption": "lockstep",
            "clock_epoch": EPOCH, "source_clock": "px4_boot:0", "residual_ns": None,
        },
        "origin_version": f"{EPOCH}:u1", "local_ned_origin_world_enu_m": [0.0] * 3,
        "height_datum": "amsl", "origin_source": "GPS_GLOBAL_ORIGIN",
        "source_messages": raw, "horizontal_sigma_m": None,
    }


def sample(ns: int) -> dict[str, Any]:
    return {
        "vehicle": "u1", "sim_ns": ns, "position_enu": [1.0, 2.0, 3.0],
        "velocity_enu": [0.0] * 3, "attitude_quat": [0.0, 0.0, 0.0, 1.0],
        "battery": {"remaining_fraction": 0.9, "voltage_v": 16.0}, "armed": False,
        "flight_mode": "HOLD", "landed_state": "ON_GROUND",
        "freshness": {"mavsdk_source_sim_ns": None}, "vehicle_estimate": unavailable_estimate(ns),
    }


def joint(ns: int = 0, command_id: str | None = None, released: bool = False) -> dict[str, Any]:
    result = {
        "vehicle": "u1", "package_id": "parcel", "clock_epoch": EPOCH,
        "status": "detached" if released else "attached", "released": released,
        "source": "gazebo_detachable_joint", "source_clock": None if released else "episode_sim",
        "source_time_ns": None if released else 0, "observed_sim_ns": ns,
        "command_id": command_id,
    }
    if released:
        result["release_interval_ns"] = [STEP, ns]
    return result


def contact(command_id: str, start: int, end: int) -> dict[str, Any]:
    return {
        "command_id": command_id, "vehicle": "u1", "package_id": "parcel",
        "receiver_id": "receiver", "receipt_id": "parcel-contact-1",
        "source": "gazebo_contact_sensor", "source_topic": "/payload/contact",
        "clock_epoch": EPOCH, "source_clock": "gazebo_sim", "episode_origin_ns": ORIGIN,
        "contact_start_ns": ORIGIN + start, "contact_end_ns": ORIGIN + end,
        "contact_start_sim_ns": start, "contact_end_sim_ns": end, "contact_duration_ns": end - start,
    }


def scenario(*, operations=None, camera=False, payload=False, estimates=False, datum=False, step_ns=STEP):
    original = load_scenario(ROOT / "px4-flight.yaml")
    document = copy.deepcopy(original.document)
    document["registry"]["messages"] = []
    for descriptor in command_descriptors("px4_gazebo") + event_descriptors("px4_gazebo"):
        entry = {"id": descriptor.id, "kind": descriptor.kind, "schema": thaw(descriptor.schema)}
        if descriptor.result_schema is not None:
            entry["result_schema"] = thaw(descriptor.result_schema)
        if descriptor.cancel_support:
            entry["cancel_support"] = True
        document["registry"]["messages"].append(entry)
    config = document["engines"]["flight"]["config"]
    config["step_ns"] = step_ns
    if operations is not None:
        config["native_operations"] = operations
    if camera:
        config["reset"]["rgbd_camera"] = copy.deepcopy(CAMERA)
    if payload:
        config["reset"]["payload"] = copy.deepcopy(PAYLOAD)
    if datum:
        config["reset"]["altitude_datum"] = "amsl"
    if estimates:
        config["fields"]["estimate"] = "e1.px4.estimate"
        document["registry"]["fields"].append({
            "id": "e1.px4.estimate", "type": "e1:Aircraft", "schema": vehicle_estimate_schema(),
            "metadata": {"frame": "enu", "role": "observation"},
        })
        document["bindings"]["rules"][0]["fields"].append("e1.px4.estimate")
    return load_scenario(document, base=ROOT)


class Transport:
    def __init__(self, response=None, *, payload=False, hook=None):
        self.response = hello() if response is None else response
        self.payload = payload
        self.hook = hook
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.budget = ResourceBudget()
        self.tainted = False
        self.operations = LEGACY_OPERATIONS
        self.frontier = 0

    def admit_operations(self, operations):
        self.operations = operations

    def request(self, operation, payload):
        assert operation in self.operations
        assert not self.tainted
        self.requests.append((operation, copy.deepcopy(payload)))
        if operation == "advance":
            self.frontier = payload["to_sim_ns"]
        if self.hook is not None:
            result = self.hook(operation, payload, self)
            if result is not None:
                return result
        if operation == "hello":
            return copy.deepcopy(self.response)
        if operation == "reset":
            result = {
                "reached_sim_ns": 0, "physics_step_ns": 1, "warmup_sim_ns": ORIGIN,
                "vehicles": ["u1"], "telemetry": [sample(0)], "clock_epoch": EPOCH, "run_id": RUN_ID,
            }
            if self.payload:
                result.update(payload_events=[joint()], receiver_receipts=[])
            return result
        if operation == "advance":
            result = {"reached_sim_ns": self.frontier, "telemetry": [sample(self.frontier)], "contacts": [], "command_updates": []}
            if self.payload:
                result.update(payload_events=[], receiver_receipts=[])
            return result
        if operation == "command":
            return {"status": "accepted", "command_id": payload["command_id"], "sim_ns": self.frontier}
        if operation == "capture":
            return {"status": "pending", "reason": "native frame not yet available"}
        if operation == "close":
            return {"closed": True}
        raise AssertionError(f"unexpected RPC {operation}")

    def abort(self):
        self.tainted = True

    def disconnect(self):
        pass


def install(monkeypatch, transport):
    monkeypatch.setattr(lockstep_base, "JsonLinesClient", lambda *args, **kwargs: transport)


def make_engine(document) -> PX4GazeboEngine:
    return PX4GazeboEngine(EngineBuild("flight", document.engines["flight"]["config"], document.registry, document.manifest, document.manifest.entities, document.initial))


def submit(run, action: str, **payload) -> str:
    return run.kernel.submit(CommandRequest(f"adapters.px4_gazebo.{action}", "flight", Instant(run.kernel.view().instant.ns + 1), {"entity": "aircraft", **payload}))


@pytest.mark.parametrize("minor", [0, 1])
def test_legacy_scenario_admits_old_or_new_service_without_enabling_extras(monkeypatch, minor):
    transport = Transport(hello(minor))
    install(monkeypatch, transport)
    with AdapterRun(scenario()) as run:
        run.start()
        assert [operation for operation, _ in transport.requests] == ["hello", "reset"]
        assert run.engines[0].admitted_operations == LEGACY_OPERATIONS
        assert not any(command.endswith("_v1") for command in run.engines[0].partition.commands)
        assert not run.scenario.registry.message("adapters.px4_gazebo.arm").cancel_support


@pytest.mark.parametrize("requirements", [
    {"operations": ["cancel"]}, {"operations": ["payload"]}, {"operations": ["capture"]},
    {"payload": True}, {"camera": True}, {"estimates": True}, {"datum": True},
])
def test_old_service_rejects_every_explicit_or_inferred_native_requirement_before_reset(monkeypatch, requirements):
    transport = Transport(hello(0))
    install(monkeypatch, transport)
    engine = make_engine(scenario(**requirements))
    with pytest.raises(KernelError) as excinfo:
        engine.initialize(None)
    assert excinfo.value.code == "ADAPTER_CAPABILITY"
    assert [operation for operation, _ in transport.requests] == ["hello"]
    assert transport.tainted


@pytest.mark.parametrize("path,value", [
    (("major",), True), (("major",), 2), (("minor",), False), (("minor",), 2),
    (("protocol",), "wrong/v1"), (("capabilities",), []),
    (("capabilities", "operations"), "capture"), (("capabilities", "operations"), ["hello", []]),
    (("capabilities", "operations"), ["hello", "hello"]),
    (("capabilities", "operations"), sorted(LEGACY_OPERATIONS) + ["unknown"]),
    (("capabilities", "operations"), ["hello", "capture"]),
    (("capabilities", "features"), None), (("capabilities", "features"), [True]),
    (("capabilities", "features"), ["rgbd_camera", "rgbd_camera"]),
    (("capabilities", "features"), ["unknown"]), (("capabilities", "features"), []),
    (("capabilities", "actions"), [True]), (("capabilities", "vehicles"), [["x500"]]),
    (("capabilities", "sensors"), ["gazebo_pose"]), (("capabilities", "physics_step_ns"), True),
    (("capabilities", "max_frame_bytes"), False), (("capabilities", "hold"), 1),
])
def test_malformed_or_incomplete_native_catalogs_never_reach_reset(monkeypatch, path, value):
    response = hello()
    target = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    transport = Transport(response)
    install(monkeypatch, transport)
    engine = make_engine(scenario(operations=["cancel", "payload", "capture"]))
    with pytest.raises(KernelError):
        engine.initialize(None)
    assert [operation for operation, _ in transport.requests] == ["hello"]


@pytest.mark.parametrize("catalog", ["operations", "features"])
def test_minor_one_requires_both_catalogs_even_for_legacy_scenario(monkeypatch, catalog):
    response = hello()
    del response["capabilities"][catalog]
    transport = Transport(response)
    install(monkeypatch, transport)
    with pytest.raises(KernelError):
        make_engine(scenario()).initialize(None)
    assert [operation for operation, _ in transport.requests] == ["hello"]


@pytest.mark.parametrize("value", ["cancel", ["unknown"], ["cancel", "cancel"], [False], [None]])
def test_invalid_native_operation_config_has_no_transport_effect(monkeypatch, value):
    transport = Transport()
    install(monkeypatch, transport)
    with pytest.raises((KernelError, ValueError)):
        make_engine(scenario(operations=value))
    assert transport.requests == []


@pytest.mark.parametrize("operation", sorted((LEGACY_OPERATIONS | NATIVE_OPERATIONS) - {"hello"}))
def test_no_engine_operation_can_precede_hello_admission(operation):
    engine = make_engine(scenario(operations=["cancel", "payload", "capture"]))
    transport = Transport()
    engine.client = transport
    with pytest.raises(KernelError) as excinfo:
        engine.request(operation, {})
    assert excinfo.value.code == "ADAPTER_CAPABILITY"
    assert transport.requests == []


def test_native_commands_and_cancel_descriptors_are_opt_in(monkeypatch):
    transport = Transport()
    install(monkeypatch, transport)
    with AdapterRun(scenario(operations=["cancel", "capture", "payload"])) as run:
        run.start()
        engine = run.engines[0]
        assert engine.admitted_operations == LEGACY_OPERATIONS | NATIVE_OPERATIONS
        for command in ("arm_cancel_v1", "payload_release_v1", "payload_status_v1", "capture_poll_v1", "capture_read_v1"):
            assert f"adapters.px4_gazebo.{command}" in engine.partition.commands
        assert run.scenario.registry.message("adapters.px4_gazebo.arm_cancel_v1").cancel_support
    transport = Transport()
    install(monkeypatch, transport)
    with AdapterRun(scenario()) as run:
        run.start()
        for command in ("arm_cancel_v1", "payload_release_v1", "capture_poll_v1"):
            with pytest.raises(KernelError):
                submit(run, command)
        assert [op for op, _ in transport.requests] == ["hello", "reset"]


def test_source_clock_estimate_has_actual_mapped_acquisition_and_replays(monkeypatch):
    def hook(operation, payload, peer):
        if operation == "advance":
            current = sample(peer.frontier)
            current["vehicle_estimate"] = estimate(peer.frontier, peer.frontier - 20_000_000)
            return {"reached_sim_ns": peer.frontier, "telemetry": [current], "contacts": [], "command_updates": []}
        return None

    transport = Transport(hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(estimates=True)) as run:
        run.start()
        ref = run.scenario.manifest.entities[0]
        unavailable = run.kernel.view().field((ref, "e1.px4.estimate"), Instant(0))
        assert unavailable.value["status"] == "unavailable" and unavailable.value["source_sim_ns"] is None
        run.run_until(STEP)
        fact = run.kernel.view().field((ref, "e1.px4.estimate"), Instant(STEP))
        assert isinstance(fact, Fact)
        assert fact.acquired.numerator == STEP - 20_000_000
        assert fact.mapped_ns == STEP - 20_000_000 and fact.available.ns == STEP
        journal = run.kernel.journal.bytes
    assert replay(journal).view().field((ref, "e1.px4.estimate"), Instant(STEP)).value == fact.value


@pytest.mark.parametrize("bad", ["vehicle", "epoch", "future", "stale", "map", "raw_clock", "unknown"])
def test_malformed_source_estimates_fault_without_partial_publication(monkeypatch, bad):
    def hook(operation, payload, peer):
        if operation != "advance":
            return None
        current = sample(peer.frontier)
        value = estimate(peer.frontier, peer.frontier - 20_000_000)
        if bad == "vehicle":
            value["uav_id"] = "other"
        elif bad == "epoch":
            value["clock_epoch"] = "other"
        elif bad == "future":
            value["source_sim_ns"] = peer.frontier + 1
        elif bad == "stale":
            value["source_sim_ns"] = 0
            value["attitude_source_sim_ns"] = 0
            value["source_messages"] = estimate(peer.frontier, 0)["source_messages"]
        elif bad == "map":
            value["clock_map"]["source_clock"] = "other"
        elif bad == "raw_clock":
            value["source_messages"][0]["source_ns"] += 1
        else:
            value["undeclared"] = 1
        current["vehicle_estimate"] = value
        return {"reached_sim_ns": peer.frontier, "telemetry": [current], "contacts": [], "command_updates": []}

    transport = Transport(hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(estimates=True, step_ns=2 * STEP if bad == "stale" else STEP)) as run:
        run.start()
        ref = run.scenario.manifest.entities[0]
        before = run.kernel.view().field((ref, "e1.px4.position"), Instant(0))
        with pytest.raises(KernelError):
            run.run_until(2 * STEP if bad == "stale" else STEP)
        assert run.kernel.view().field((ref, "e1.px4.position"), Instant(2 * STEP)).version == before.version
        assert transport.tainted


def test_release_ack_waits_for_detach_and_source_contact_receipt_and_replays(monkeypatch):
    admitted = {}

    def hook(operation, payload, peer):
        if operation == "payload":
            admitted.update(payload)
            return {"status": "accepted", "command_id": payload["command_id"], "vehicle": "u1", "package_id": "parcel", "sim_ns": peer.frontier}
        if operation == "advance" and peer.frontier >= 2 * STEP:
            return {"reached_sim_ns": peer.frontier, "telemetry": [sample(peer.frontier)], "contacts": [], "command_updates": [],
                    "payload_events": [joint(peer.frontier, admitted["command_id"], True)] if peer.frontier == 2 * STEP else [],
                    "receiver_receipts": [contact(admitted["command_id"], 2 * STEP, 3 * STEP)] if peer.frontier == 3 * STEP else []}
        return None

    transport = Transport(payload=True, hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(payload=True)) as run:
        run.start()
        command_id = submit(run, "payload_release_v1", package_id="parcel")
        run.run_until(STEP)
        assert run.kernel.view().action(command_id).status == "accepted"
        run.run_until(2 * STEP)
        assert run.kernel.view().action(command_id).status == "executing"
        run.run_until(3 * STEP)
        action = run.kernel.view().action(command_id)
        assert action.status == "succeeded"
        assert action.history[-1]["result"]["receipt"]["contact_duration_ns"] == STEP
        schemas = {message.schema_id for message in run.kernel.view()._store.messages.values()}
        assert {"adapters.px4_gazebo.payload_release_ack_v1", "adapters.px4_gazebo.payload_state_v1", "adapters.px4_gazebo.receiver_receipt_v1"} <= schemas
        journal = run.kernel.journal.bytes
    assert replay(journal).view().action(command_id).status == "succeeded"


@pytest.mark.parametrize("bad", ["command", "package", "vehicle", "epoch", "future", "duration", "topic", "unknown", "predetach"])
def test_invalid_payload_contact_never_completes_or_partially_publishes(monkeypatch, bad):
    admitted = {}

    def hook(operation, payload, peer):
        if operation == "payload":
            admitted.update(payload)
            return {"status": "accepted", "command_id": payload["command_id"], "vehicle": "u1", "package_id": "parcel", "sim_ns": peer.frontier}
        if operation != "advance" or peer.frontier < 2 * STEP:
            return None
        result = {"reached_sim_ns": peer.frontier, "telemetry": [sample(peer.frontier)], "contacts": [], "command_updates": [], "payload_events": [], "receiver_receipts": []}
        if peer.frontier == 2 * STEP:
            result["payload_events"] = [joint(peer.frontier, admitted["command_id"], True)]
            return result
        receipt = contact(admitted["command_id"], 2 * STEP, 3 * STEP)
        if bad == "predetach":
            result["receiver_receipts"] = [contact(admitted["command_id"], STEP, 2 * STEP)]
            return result
        key, value = {
            "command": ("command_id", "other"), "package": ("package_id", "other"),
            "vehicle": ("vehicle", "other"), "epoch": ("clock_epoch", "other"),
            "future": ("contact_end_sim_ns", 4 * STEP), "duration": ("contact_duration_ns", STEP - 1),
            "topic": ("source_topic", "/other"), "unknown": ("undeclared", 0),
        }[bad]
        receipt[key] = value
        result["receiver_receipts"] = [receipt]
        return result

    transport = Transport(payload=True, hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(payload=True)) as run:
        run.start()
        command_id = submit(run, "payload_release_v1", package_id="parcel")
        run.run_until(2 * STEP)
        assert run.kernel.view().action(command_id).status == "executing"
        with pytest.raises(KernelError):
            run.run_until(3 * STEP)
        assert run.kernel.view().action(command_id).status == "executing"
        assert not any(message.schema_id.endswith("receiver_receipt_v1") for message in run.kernel.view()._store.messages.values())
        journal = run.kernel.journal.bytes
    assert replay(journal).view().action(command_id).status == "executing"


@pytest.mark.parametrize("detached", [False, True])
def test_failed_release_attempt_still_correlates_observed_status_without_delivery(monkeypatch, detached):
    attempted = {}

    def hook(operation, payload, peer):
        if operation != "payload":
            return None
        if payload["action"] == "release":
            attempted.update(payload)
            return {"status": "failed", "command_id": payload["command_id"], "vehicle": "u1", "package_id": "parcel", "sim_ns": peer.frontier, "reason": "detach publication failed"}
        return joint(peer.frontier, attempted["command_id"], detached)

    transport = Transport(payload=True, hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(payload=True)) as run:
        run.start()
        release_id = submit(run, "payload_release_v1", package_id="parcel")
        run.run_until(STEP)
        assert run.kernel.view().action(release_id).status == "rejected"
        status_id = submit(run, "payload_status_v1", package_id="parcel")
        run.run_until(2 * STEP)
        assert run.kernel.view().action(status_id).status == "succeeded"
        assert run.kernel.view().action(status_id).history[-1]["result"]["observation"]["released"] is detached
        assert run.kernel.view().action(release_id).status == "rejected"
        assert not any(message.schema_id.endswith("receiver_receipt_v1") for message in run.kernel.view()._store.messages.values())
        assert not transport.tainted


def capture_frame(frontier=STEP):
    # Synthetic bytes exercise recording/integrity only, never native acceptance.
    rgb = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==")
    depth = zlib.compress(struct.pack("<f", 2.0))
    observed = frontier - 20_000_000
    frame_id = f"{RUN_ID}:{EPOCH}:u1:rgbd:{ORIGIN + observed}"
    calibration = {
        "K": [1.0, 0.0, 0.5, 0.0, 1.0, 0.5, 0.0, 0.0, 1.0], "width": 1, "height": 1,
        "distortion": [], "distortion_model": "plumb_bob", "camera_info_source_ns": ORIGIN,
        "native_frame_id": "aas_u1::aas_rgbd_rgbd_link::rgbd", "source_topic": "/native/cameras/u1/rgbd/camera_info",
        "horizontal_fov_deg": math.degrees(2 * math.atan(0.5)), "vertical_fov_deg": math.degrees(2 * math.atan(0.5)),
        "fov_source": "actual Gazebo CameraInfo K and image dimensions", "registered_rgb_depth": True,
        "depth_convention": "optical_z", "depth_min_m": 0.1, "depth_max_m": 100.0,
        "invalid_depth_rule": "nonfinite or outside configured near/far range",
        "T_body_frd_from_camera_optical": [[0.0, 0.0, 1.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0, 0, 0, 1]],
        "extrinsic_source": "configured RGBD SDF pose relative to base_link FLU; converted to FRD/optical",
    }
    version_content = {key: value for key, value in calibration.items() if key != "camera_info_source_ns"}
    calibration["version"] = f"{EPOCH}:rgbd:" + hashlib.sha256(json.dumps(version_content, sort_keys=True, allow_nan=False).encode()).hexdigest()
    blobs = {kind: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "encoding": encoding} for kind, data, encoding in (("rgb", rgb, "png"), ("depth", depth, "zlib-float32-le-metres"))}
    return {
        "status": "succeeded", "blobs": blobs,
        "observation": {
            "run_id": RUN_ID, "uav_id": "u1", "sensor_id": "rgbd", "frame_id": frame_id,
            "source": "gazebo_transport", "source_clock": "gazebo_sim", "source_time": ORIGIN + observed,
            "clock_epoch": EPOCH, "observed_at": observed, "depth_observed_at": observed,
            "available_sim_ns": frontier, "received_wall_time": 123,
            "source_topic": "/native/cameras/u1/rgbd/image", "native_frame_id": "aas_u1::aas_rgbd_rgbd_link::rgbd",
            "coordinate_frame": "camera_optical", "calibration": calibration,
            "vehicle_estimate": unavailable_estimate(frontier), "localization_status": "unavailable",
            "localization_reason": "source clock unavailable", "rgb_sha256": blobs["rgb"]["sha256"],
            "sensing_model": "gazebo_geometric_rgbd", "physical_ranging": False,
        },
    }, {"rgb": rgb, "depth": depth}


def test_capture_public_commands_record_frame_chunk_and_replay_without_transport(monkeypatch):
    frame, blobs = capture_frame()

    def hook(operation, payload, peer):
        if operation != "capture":
            return None
        if payload["action"] == "poll":
            return copy.deepcopy(frame)
        data = blobs[payload["kind"]]
        return {"status": "succeeded", "vehicle": payload["vehicle"], "frame_id": payload["frame_id"], "kind": payload["kind"], "offset": 0, "total_bytes": len(data), "data_base64": base64.b64encode(data).decode(), "eof": True}

    transport = Transport(hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(camera=True)) as run:
        run.start()
        poll_id = submit(run, "capture_poll_v1", after_sim_ns=-1)
        run.run_until(STEP)
        poll = run.kernel.view().action(poll_id)
        assert poll.status == "succeeded"
        frame_id = poll.history[-1]["result"]["observation"]["frame_id"]
        read_id = submit(run, "capture_read_v1", frame_id=frame_id, kind="rgb", offset=0, max_bytes=262144)
        run.run_until(2 * STEP)
        assert run.kernel.view().action(read_id).status == "succeeded"
        events = [message for message in run.kernel.view()._store.messages.values() if message.schema_id.endswith(("capture_frame_v1", "capture_chunk_v1"))]
        assert len(events) == 2
        assert all(message.at.ns == STEP - 20_000_000 for message in events)
        journal = run.kernel.journal.bytes
    before = list(transport.requests)
    offline = replay(journal)
    assert offline.view().action(read_id).history[-1]["result"]["data_base64"] == base64.b64encode(blobs["rgb"]).decode()
    assert transport.requests == before


@pytest.mark.parametrize("response", [{"status": "pending", "reason": "awaiting frame"}, {"status": "unavailable", "reason": "camera offline"}])
def test_capture_unavailable_is_recorded_rejection_not_success(monkeypatch, response):
    transport = Transport(hook=lambda operation, payload, peer: response if operation == "capture" else None)
    install(monkeypatch, transport)
    with AdapterRun(scenario(camera=True)) as run:
        run.start()
        command_id = submit(run, "capture_poll_v1", after_sim_ns=-1)
        run.run_until(STEP)
        action = run.kernel.view().action(command_id)
        assert action.status == "rejected" and action.history[-1]["result"]["status"] == response["status"]
        assert not any(message.schema_id.endswith("capture_frame_v1") for message in run.kernel.view()._store.messages.values())


@pytest.mark.parametrize("bad", ["ack", "vehicle", "frame", "epoch", "source", "depth", "frontier", "calibration", "encoding", "physical", "alignment"])
def test_capture_malformed_or_ack_only_result_cannot_succeed(monkeypatch, bad):
    frame, _ = capture_frame()
    if bad == "ack":
        frame = {"status": "accepted"}
    elif bad == "calibration":
        frame["observation"]["calibration"]["undeclared"] = 1
    elif bad == "encoding":
        frame["blobs"]["depth"]["encoding"] = "png"
    elif bad == "alignment":
        # Internally consistent and valid for a 250ms telemetry bound, but not
        # the capture contract's stricter 40ms frame/estimate alignment.
        frame["observation"]["vehicle_estimate"] = estimate(STEP, 0)
        frame["observation"]["localization_status"] = "available"
        frame["observation"]["localization_reason"] = None
    else:
        key, value = {
            "vehicle": ("uav_id", "other"), "frame": ("frame_id", "other"), "epoch": ("clock_epoch", "other"),
            "source": ("source_time", ORIGIN + STEP + 1), "depth": ("depth_observed_at", 0),
            "frontier": ("available_sim_ns", STEP + 1), "physical": ("physical_ranging", True),
        }[bad]
        frame["observation"][key] = value
    transport = Transport(hook=lambda operation, payload, peer: copy.deepcopy(frame) if operation == "capture" else None)
    install(monkeypatch, transport)
    with AdapterRun(scenario(camera=True)) as run:
        run.start()
        command_id = submit(run, "capture_poll_v1", after_sim_ns=-1)
        with pytest.raises(KernelError):
            run.run_until(STEP)
        assert run.kernel.view().action(command_id).status != "succeeded"
        assert transport.tainted


def test_capture_read_requires_previously_recorded_frame_before_rpc(monkeypatch):
    transport = Transport()
    install(monkeypatch, transport)
    with AdapterRun(scenario(camera=True)) as run:
        run.start()
        command_id = submit(run, "capture_read_v1", frame_id="unknown", kind="rgb", offset=0, max_bytes=1)
        run.run_until(STEP)
        assert run.kernel.view().action(command_id).status == "rejected"
        assert not any(operation == "capture" for operation, _ in transport.requests)


def test_cancel_ack_only_admits_then_waits_for_observed_canceled_update(monkeypatch):
    admitted, canceled = {}, {}

    def hook(operation, payload, peer):
        if operation == "command":
            admitted.update(payload)
        if operation == "cancel":
            canceled.update(payload)
            return {"status": "accepted", "command_id": payload["command_id"], "sim_ns": peer.frontier}
        if operation == "advance" and canceled:
            return {"reached_sim_ns": peer.frontier, "telemetry": [sample(peer.frontier)], "contacts": [],
                    "command_updates": [{"command_id": admitted["command_id"], "vehicle": "u1", "action": "arm", "sim_ns": when, "status": status} for status, when in (("cancel_requested", 2 * STEP), ("running", 2 * STEP), ("canceled", peer.frontier))]}
        return None

    transport = Transport(hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(operations=["cancel"])) as run:
        run.start()
        command_id = submit(run, "arm_cancel_v1")
        run.run_until(STEP)
        assert run.kernel.cancel(command_id).queued
        run.run_until(2 * STEP)
        assert run.kernel.view().action(command_id).status == "canceling"
        run.run_until(3 * STEP)
        assert run.kernel.view().action(command_id).status == "canceled"
        journal = run.kernel.journal.bytes
    assert replay(journal).view().action(command_id).status == "canceled"


@pytest.mark.parametrize("reason", [42, {}, None, ""])
def test_malformed_accepted_cancel_ack_is_rejected_inside_guard(monkeypatch, reason):
    def hook(operation, payload, peer):
        if operation == "cancel":
            return {"status": "accepted", "command_id": payload["command_id"], "sim_ns": peer.frontier, "reason": reason}
        return None

    transport = Transport(hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(operations=["cancel"])) as run:
        run.start()
        command_id = submit(run, "arm_cancel_v1")
        run.run_until(STEP)
        assert run.kernel.cancel(command_id).queued
        with pytest.raises(KernelError):
            run.run_until(2 * STEP)
        assert run.kernel.view().action(command_id).status == "accepted"
        assert transport.tainted


@pytest.mark.parametrize("bad", ["reason", "time", "vehicle", "premature_success"])
def test_malformed_cancel_observation_cannot_finish_cleanup(monkeypatch, bad):
    admitted, canceled = {}, {}

    def hook(operation, payload, peer):
        if operation == "command":
            admitted.update(payload)
        if operation == "cancel":
            canceled.update(payload)
            return {"status": "accepted", "command_id": payload["command_id"], "sim_ns": peer.frontier}
        if operation == "advance" and canceled:
            update = {"command_id": admitted["command_id"], "vehicle": "u1", "action": "arm", "sim_ns": peer.frontier, "status": "canceled"}
            if bad == "reason":
                update["reason"] = True
            elif bad == "time":
                update["sim_ns"] = STEP
            elif bad == "vehicle":
                update["vehicle"] = "other"
            else:
                update["status"] = "succeeded"
            return {"reached_sim_ns": peer.frontier, "telemetry": [sample(peer.frontier)], "contacts": [], "command_updates": [update]}
        return None

    transport = Transport(hook=hook)
    install(monkeypatch, transport)
    with AdapterRun(scenario(operations=["cancel"])) as run:
        run.start()
        command_id = submit(run, "arm_cancel_v1")
        run.run_until(STEP)
        assert run.kernel.cancel(command_id).queued
        run.run_until(2 * STEP)
        with pytest.raises(KernelError):
            run.run_until(3 * STEP)
        assert run.kernel.view().action(command_id).status == "canceling"
        assert transport.tainted


def test_legacy_command_does_not_advertise_or_send_cancellation(monkeypatch):
    transport = Transport()
    install(monkeypatch, transport)
    with AdapterRun(scenario()) as run:
        run.start()
        command_id = submit(run, "arm")
        run.run_until(STEP)
        assert not run.kernel.cancel(command_id).queued
        assert not any(operation == "cancel" for operation, _ in transport.requests)
