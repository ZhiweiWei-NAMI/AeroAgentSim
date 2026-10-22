"""Host-only pure-part regressions; these fixtures are NOT native acceptance."""

import asyncio
import base64
import hashlib
import json
import struct
import xml.etree.ElementTree as ET
from copy import deepcopy
from types import SimpleNamespace

import pytest

from service.commands import CANCEL_TIMEOUT_NS, Commands, request_state
from service.config import camera_config, payload_config, prepare
from service.configure_rgbd import optical_to_body_frd, sensor_frame
from service.estimate_streams import EstimateStreams, geodetic_to_enu
from service.payload_adapter import PayloadAdapter
from service.payload_receiver import ContactReceiver
from service.rpc import FEATURES, HELLO_MINOR, OPERATIONS
from service.runtime import Runtime
from service.sensor_streams import SensorStreams, dimensions, png_rgb
from service.transport import Gazebo

STEP = 4_000_000
SECOND = 1_000_000_000


def camera():
    return dict(sensor_id="rgbd", topic_prefix="/aas/sensors", pose_flu=[0, 0, 0, 0, 0, 0],
                width=2, height=2, update_rate_hz=30, horizontal_fov_rad=1.2,
                near_m=0.1, far_m=100)


def physical_payload():
    return dict(carrier="v0", package_id="package", receiver_id="receiver",
                detach_topic="/aas/package/detach", state_topic="/aas/package/state",
                contact_topic="/aas/receiver/contact", attachment_offset_enu_m=[0, 0, -0.2],
                package_size_m=[0.1, 0.2, 0.3], package_mass_kg=0.5,
                receiver_pose=[1, 2, 0, 0, 0, 0], receiver_size=[2, 2, 0.1],
                contact_duration_ns=3 * STEP)


@pytest.mark.parametrize("key,value", [
    ("extra", 1), ("sensor_id", "../camera"), ("sensor_id", ""),
    ("topic_prefix", "relative/topic"), ("topic_prefix", "/aas//camera"),
    ("topic_prefix", "/aas/../camera"), ("topic_prefix", "/aas/trailing/"),
    ("width", True), ("width", 2.0), ("width", 0), ("height", -1),
    ("height", 2049), ("width", float("nan")),
    ("pose_flu", [0, 0, 0]), ("pose_flu", [0, True, 0, 0, 0, 0]),
    ("pose_flu", [0, 0, 0, 0, 0, float("inf")]),
    ("update_rate_hz", True), ("update_rate_hz", 0), ("update_rate_hz", 241),
    ("horizontal_fov_rad", 0), ("horizontal_fov_rad", 3.141592653589793),
    ("near_m", 0), ("near_m", 100), ("far_m", 0.05), ("far_m", 10001),
    ("far_m", float("nan")), ("far_m", 10**1000),
    ("depth_width", 3),  # Separate inconsistent RGB/depth dimensions are not admitted.
])
def test_camera_configuration_is_closed_and_bounded(key, value):
    config = camera()
    config[key] = value
    with pytest.raises(ValueError):
        camera_config(config)


def test_camera_geometry_validation_does_not_mutate_input():
    config = camera()
    original = deepcopy(config)
    result = camera_config(config)
    assert config == original
    assert result["width"] == result["height"] == 2
    oversized = {**config, "width": 2048, "height": 2048}
    with pytest.raises(ValueError, match="pixel"):
        camera_config(oversized)
    with pytest.raises(ValueError):
        camera_config({k: v for k, v in config.items() if k != "near_m"})
    with pytest.raises(ValueError):
        camera_config(None)
    assert optical_to_body_frd([0, 0, 0, 0, 0, 0]) == [
        [0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1]
    ]


@pytest.mark.parametrize("key,value", [
    ("extra", 1), ("carrier", "missing"), ("carrier", True),
    ("package_id", "../package"), ("package_id", "receiver"),
    ("package_id", "v0"), ("receiver_id", "aas_v0"), ("child_link", "bad::link"),
    ("detach_topic", "/stats"), ("state_topic", "/world/demo/state"),
    ("contact_topic", "/aas/package/state"), ("contact_topic", "contact"),
    ("package_mass_kg", True), ("package_mass_kg", 0), ("package_mass_kg", -1),
    ("package_mass_kg", float("inf")), ("package_mass_kg", 1001),
    ("package_size_m", [0, 1, 1]), ("package_size_m", [1, 2]),
    ("receiver_size", [1, False, 1]), ("receiver_size", [101, 1, 1]),
    ("attachment_offset_enu_m", [0, 0, float("nan")]),
    ("receiver_pose", [0, 0, 0, 0, 0, 7]),
    ("package_orientation_rpy", [0, 0, float("inf")]),
    ("contact_duration_ns", True), ("contact_duration_ns", 0),
    ("contact_duration_ns", STEP + 1), ("contact_duration_ns", 60 * SECOND + STEP),
])
def test_payload_configuration_is_closed_and_bounded(key, value):
    config = physical_payload()
    config[key] = value
    with pytest.raises(ValueError):
        payload_config(config, {"v0"}, STEP)


def test_reset_validates_nested_configuration_and_keeps_source_sdf_private(tmp_path, monkeypatch):
    from service import config as module

    native = tmp_path / "native"
    (native / "share/gz").mkdir(parents=True)
    (native / "share/gz/server.config").write_text("<server_config/>")
    world = tmp_path / "source.sdf"
    source = ('<sdf version="1.9"><world name="demo"><physics><max_step_size>0.004</max_step_size>'
              '</physics><spherical_coordinates><world_frame_orientation>ENU</world_frame_orientation>'
              '<latitude_deg>47</latitude_deg><longitude_deg>8</longitude_deg><elevation>100</elevation>'
              '</spherical_coordinates></world></sdf>')
    world.write_text(source)
    monkeypatch.setattr(module, "ROOT", native)
    monkeypatch.setattr(module, "catalog", lambda: {"x500": 4001})
    output = tmp_path / "out"
    output.mkdir()
    reset = dict(seed=1, world=str(world), warmup=SECOND,
                 vehicles=[dict(id="v0", model="x500", spawn=[0, 0, 1, 0, 0, 0])],
                 rgbd_camera=camera(), payload=physical_payload(), altitude_datum="amsl")
    for changes in ({"unknown": 1}, {"altitude_datum": "ellipsoid"},
                    {"rgbd_camera": {**camera(), "gain": 1}},
                    {"payload": {**physical_payload(), "carrier": "unknown"}},
                    {"payload": {**physical_payload(), "child_link": "aas_v0"}}):
        with pytest.raises(ValueError):
            prepare({**reset, **changes}, output)
        assert not list(output.iterdir())
    camera_only = {k: v for k, v in reset.items() if k != "payload"}
    camera_only["vehicles"] = [dict(id="rgbd_rgbd_link", model="x500", spawn=[0, 0, 1, 0, 0, 0])]
    with pytest.raises(ValueError, match="alias"):
        prepare(camera_only, output)
    assert not list(output.iterdir())
    result = prepare(reset, output)
    assert result.payload["child_link"] == "parcel"
    assert result.rgbd_camera["width"] == 2
    assert world.read_text() == source
    wrapper = output / "rgbd_v0/model.sdf"
    assert wrapper.is_file()
    model = ET.parse(wrapper).getroot().find("model")
    assert model.find("link/sensor/camera/image/width").text == "2"
    assert model.find("plugin").get("name") == "gz::sim::systems::DetachableJoint"


@pytest.mark.parametrize("features", [("rgbd_camera",), ("payload",), ("rgbd_camera", "payload")])
@pytest.mark.parametrize("existing", ["none", "partial", "complete"])
def test_optional_world_plugins_merge_baseline_systems(tmp_path, monkeypatch, features, existing):
    from service import config as module

    native = tmp_path / "native"
    (native / "share/gz").mkdir(parents=True)
    (native / "share/gz/server.config").write_text("<server_config/>")
    monkeypatch.setattr(module, "ROOT", native)
    monkeypatch.setattr(module, "catalog", lambda: {"x500": 4001})
    root = ET.fromstring(
        '<sdf version="1.9"><world name="demo"><physics><max_step_size>0.004</max_step_size>'
        '</physics><spherical_coordinates><world_frame_orientation>ENU</world_frame_orientation>'
        '<latitude_deg>47</latitude_deg><longitude_deg>8</longitude_deg><elevation>100</elevation>'
        '</spherical_coordinates></world></sdf>'
    )
    world = root.find("world")
    systems = {
        "Physics": "gz-sim-physics-system", "UserCommands": "gz-sim-user-commands-system",
        "SceneBroadcaster": "gz-sim-scene-broadcaster-system",
    }
    optional = {"rgbd_camera": ("Sensors", "gz-sim-sensors-system"),
                "payload": ("Contact", "gz-sim-contact-system")}
    if existing != "none":
        plugin = ET.SubElement(world, "plugin", name="gz::sim::systems::Physics",
                               filename=systems["Physics"])
        ET.SubElement(plugin, "engine").text = "custom-physics-engine"
        ET.SubElement(world, "plugin", name="custom::System", filename="custom-system")
    if existing == "complete":
        for name, filename in {**systems, **dict(optional.values())}.items():
            if name == "Physics":
                continue
            plugin = ET.SubElement(world, "plugin", name="gz::sim::systems::" + name,
                                   filename=filename)
            ET.SubElement(plugin, "keep_setting").text = "preserved"
            if name == "Sensors":
                ET.SubElement(plugin, "render_engine").text = "ogre2"
    source = tmp_path / "source.sdf"
    ET.ElementTree(root).write(source, encoding="unicode")
    original = source.read_text()
    output = tmp_path / "out"
    output.mkdir()
    reset = dict(seed=1, world=str(source), warmup=SECOND,
                 vehicles=[dict(id="v0", model="x500", spawn=[0, 0, 1, 0, 0, 0])])
    values = {"rgbd_camera": camera(), "payload": physical_payload()}
    result = prepare({**reset, **{key: values[key] for key in features}}, output)
    generated = ET.parse(result.sdf).getroot().find("world")
    plugins = generated.findall("plugin")
    expected = {**systems, **dict(optional[key] for key in features)}
    for name, filename in expected.items():
        matches = [p for p in plugins if p.get("name") == "gz::sim::systems::" + name]
        assert len(matches) == 1
        assert matches[0].get("filename") == filename
        if existing == "complete" and name != "Physics":
            assert matches[0].findtext("keep_setting") == "preserved"
    scene = next(p for p in plugins if p.get("name") == "gz::sim::systems::SceneBroadcaster")
    assert scene.findtext("dynamic_pose_hertz") == "1000"
    if existing != "none":
        physics = next(p for p in plugins if p.get("name") == "gz::sim::systems::Physics")
        assert physics.findtext("engine") == "custom-physics-engine"
        assert len([p for p in plugins if p.get("name") == "custom::System"]) == 1
    assert source.read_text() == original


@pytest.mark.parametrize("method,payload", [
    ("payload", {"action": "typo"}), ("payload", {"action": True}), ("payload", {"action": {}}), ("payload", {}),
    ("payload", {"action": "poll", "vehicle": "v0", "package_id": "package"}),
    ("payload", {"action": "status", "vehicle": "v0", "package_id": "package", "extra": 1}),
    ("payload", {"action": "release", "vehicle": "v0", "package_id": "package"}),
    ("payload", {"action": "status", "vehicle": "../v0", "package_id": "package"}),
    ("capture", {"action": "typo"}), ("capture", {"action": True}), ("capture", {"action": {}}), ("capture", {"vehicle": "v0"}),
    ("capture", {"action": "poll", "vehicle": "v0", "after_sim_ns": True}),
    ("capture", {"action": "poll", "vehicle": "v0", "extra": 0}),
    ("capture", {"action": "read", "vehicle": "v0", "frame_id": "f", "kind": "other"}),
    ("capture", {"action": "read", "vehicle": "v0", "frame_id": "f", "kind": "rgb", "offset": True}),
    ("capture", {"action": "read", "vehicle": "v0", "frame_id": "f", "kind": "rgb", "max_bytes": 262145}),
])
def test_actions_and_arguments_reject_even_if_subsystem_disabled(method, payload):
    runtime = Runtime()
    runtime.config = SimpleNamespace(vehicles=[SimpleNamespace(id="v0")])
    with pytest.raises(ValueError):
        getattr(runtime, method)(payload)


def test_hello_advertises_additive_operations_explicitly():
    assert HELLO_MINOR == 1
    assert {"cancel", "payload", "capture"} <= set(OPERATIONS)
    assert {"observed_cancel_hold", "payload_contact_receipts", "rgbd_camera", "source_clock_estimates"} <= set(FEATURES)


def commands_fixture(*, action="hold", params=None):
    async def hold():
        return None

    telemetry = SimpleNamespace(
        sessions={"v0": SimpleNamespace(action=SimpleNamespace(hold=hold))},
        versions={"v0": {"flight_mode": 1, "velocity_enu": 1, "armed": 1}},
        cache={"v0": {}},
    )
    commands = Commands(SimpleNamespace(origin_wgs84=(47, 8, 0)), telemetry, {"v0": 0})
    commands.accept(dict(vehicle="v0", command_id="c1", action=action,
                         params={} if params is None else params), 0)
    return commands, telemetry


def hold_sample(ns, *, mode="HOLD", speed=0):
    return dict(vehicle="v0", sim_ns=ns, flight_mode=mode, velocity_enu=[speed, 0, 0],
                freshness={"mavsdk_receipt_age_wall_ns": {"flight_mode": 0, "velocity_enu": 0}})


def estimate_sample(ns, source_ns):
    value = hold_sample(ns)
    value.update(armed=True, landed_state="IN_AIR", relative_altitude_m=3)
    value["freshness"]["mavsdk_receipt_age_wall_ns"].update(
        armed=0, landed_state=0, global_position=0,
    )
    value["vehicle_estimate"] = dict(
        status="available", run_id="run", uav_id="v0", clock_epoch="epoch",
        origin_version="origin1", source_clock="px4_boot:0", clock_map={},
        available_sim_ns=ns, source_sim_ns=source_ns, attitude_source_sim_ns=source_ns,
        position_enu_m=[0, 0, 3], velocity_enu_m_s=[0, 0, 0],
    )
    return value


@pytest.mark.parametrize("action,params,cancel", [
    ("hold", {}, False), ("takeoff", {"altitude_m": 3}, False),
    ("goto_location", {"position_enu": [0, 0, 3], "yaw_deg": 0}, False),
    ("goto_location", {"position_enu": [10, 0, 3], "yaw_deg": 0}, True),
])
def test_estimate_completion_at_4ms_frontiers_with_20ms_sources(action, params, cancel):
    async def scenario():
        commands, telemetry = commands_fixture(action=action, params=params)
        commands.estimate_completion = True
        telemetry.versions["v0"].update(landed_state=1, global_position=1)
        ack = asyncio.get_running_loop().create_future()
        ack.set_result(None)
        record = commands.records["c1"]
        record.update(status="running", task=ack, dispatched=True)
        if cancel:
            commands.cancel({"vehicle": "v0", "command_id": "c1"}, 0)
            await commands.dispatch(0)
            await record["task"]
        commands.observe([estimate_sample(STEP, 0)], STEP)  # ACK frontier, not evidence
        telemetry.versions["v0"] = {key: 2 for key in telemetry.versions["v0"]}
        interval = 5 * STEP
        for ns in range(2 * STEP, SECOND + interval + STEP, STEP):
            source_ns = ns // interval * interval
            commands.observe([estimate_sample(ns, source_ns)], ns)
            if source_ns < interval:
                assert record["observed_since"] is None  # source must postdate ACK
            else:
                assert record["observed_since"] == interval
            expected = ("canceled" if cancel else "succeeded") if ns == SECOND + interval else (
                "cancel_requested" if cancel else "running"
            )
            assert record["status"] == expected
        assert not commands.blocked
        assert SECOND + interval < CANCEL_TIMEOUT_NS
        await commands.stop()

    asyncio.run(scenario())


def test_repeated_estimate_preserves_dwell_without_crediting_poll_time():
    async def scenario():
        commands, telemetry = commands_fixture()
        commands.estimate_completion = True
        ack = asyncio.get_running_loop().create_future()
        ack.set_result(None)
        record = commands.records["c1"]
        record.update(status="running", task=ack, dispatched=True)
        commands.observe([estimate_sample(STEP, STEP)], STEP)
        telemetry.versions["v0"].update(flight_mode=2, velocity_enu=2)
        commands.observe([estimate_sample(2 * STEP, 2 * STEP)], 2 * STEP)
        # A repeated source at the maximum valid age must not complete a dwell.
        ns = SECOND + 2 * STEP
        commands.observe([estimate_sample(ns, 2 * STEP)], ns)
        assert record["status"] == "running" and record["observed_since"] == 2 * STEP
        commands.observe([estimate_sample(ns + STEP, 2 * STEP)], ns + STEP)
        assert record["observed_since"] is None  # the same source is now stale
        await commands.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", ["position_reorder", "attitude_reorder", "predicate", "identity"])
def test_estimate_dwell_still_resets_on_invalid_evidence_or_identity_change(interruption):
    async def scenario():
        commands, telemetry = commands_fixture()
        commands.estimate_completion = True
        ack = asyncio.get_running_loop().create_future()
        ack.set_result(None)
        record = commands.records["c1"]
        record.update(status="running", task=ack, dispatched=True)
        commands.observe([estimate_sample(STEP, STEP)], STEP)
        telemetry.versions["v0"].update(flight_mode=2, velocity_enu=2)
        commands.observe([estimate_sample(2 * STEP, 2 * STEP)], 2 * STEP)
        commands.observe([estimate_sample(3 * STEP, 3 * STEP)], 3 * STEP)
        sample = estimate_sample(4 * STEP, 4 * STEP)
        estimate = sample["vehicle_estimate"]
        if interruption == "position_reorder":
            estimate["source_sim_ns"] = 2 * STEP
        elif interruption == "attitude_reorder":
            estimate["attitude_source_sim_ns"] = 2 * STEP
        elif interruption == "predicate":
            estimate["velocity_enu_m_s"] = [2, 0, 0]
        else:
            estimate["origin_version"] = "origin2"
        commands.observe([sample], 4 * STEP)
        assert record["status"] == "running"
        assert record["observed_since"] == (4 * STEP if interruption == "identity" else None)
        await commands.stop()

    asyncio.run(scenario())


def test_queued_cancellation_confirms_no_dispatch_and_terminal_race():
    async def scenario():
        commands, _ = commands_fixture()
        result = commands.cancel({"vehicle": "v0", "command_id": "c1"}, 0)
        assert result["status"] == "accepted"
        assert commands.records["c1"]["status"] == "canceled"
        assert commands.records["c1"]["task"] is None
        assert commands.cancel({"vehicle": "v0", "command_id": "c1"}, 0)["status"] == "rejected"
        await commands.stop()

    asyncio.run(scenario())


def test_running_cancel_waits_for_original_termination_hold_ack_and_observation():
    async def scenario():
        commands, telemetry = commands_fixture()
        record = commands.records["c1"]
        original = asyncio.get_running_loop().create_future()
        record.update(status="running", task=original, dispatched=True)
        assert commands.cancel({"vehicle": "v0", "command_id": "c1"}, 0)["status"] == "accepted"
        assert record["status"] == "cancel_requested"
        assert commands.cancel({"vehicle": "v0", "command_id": "c1"}, STEP)["sim_ns"] == 0
        await commands.dispatch(STEP)
        assert record["task"] is original and not original.cancelled()
        assert not record["cancel_hold_started"]
        original.set_result(None)
        await commands.dispatch(2 * STEP)
        assert record["cancel_hold_started"]
        await record["task"]  # successful HOLD ACK still cannot complete cancellation
        commands.observe([hold_sample(3 * STEP)], 3 * STEP)
        assert record["status"] == "cancel_requested"
        telemetry.versions["v0"].update(flight_mode=2, velocity_enu=2)
        commands.observe([hold_sample(4 * STEP)], 4 * STEP)
        assert record["observed_since"] == 4 * STEP
        commands.observe([hold_sample(SECOND + 4 * STEP, speed=2)], SECOND + 4 * STEP)
        assert record["status"] == "cancel_requested"
        assert record["observed_since"] is None
        commands.observe([hold_sample(2 * SECOND)], 2 * SECOND)
        commands.observe([hold_sample(3 * SECOND)], 3 * SECOND)
        assert record["status"] == "canceled"
        assert commands.drain()[-1]["status"] == "canceled"
        await commands.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "transport", "canceled", "deadline"])
def test_cancel_ambiguity_quarantines_until_reset(failure):
    async def scenario():
        commands, _ = commands_fixture()
        original = asyncio.get_running_loop().create_future()
        record = commands.records["c1"]
        record.update(status="running", task=original, dispatched=True)
        commands.cancel({"vehicle": "v0", "command_id": "c1"}, 0)
        if failure == "timeout":
            original.set_exception(TimeoutError("SDK timeout"))
        elif failure == "transport":
            original.set_exception(ConnectionError("ambiguous link"))
        elif failure == "canceled":
            original.cancel()
        now = CANCEL_TIMEOUT_NS + STEP if failure == "deadline" else STEP
        await commands.dispatch(now)
        assert record["status"] == "failed"
        assert "v0" in commands.blocked
        assert commands.accept(dict(vehicle="v0", command_id="c2", action="hold", params={}), now)["status"] == "rejected"
        await commands.stop()
        fresh, _ = commands_fixture()
        assert not fresh.blocked
        await fresh.stop()

    asyncio.run(scenario())


def test_runtime_progresses_cancellation_at_each_paused_batch_frontier():
    async def scenario():
        runtime = Runtime()
        runtime.config = SimpleNamespace(physics_step_ns=STEP, vehicles=[])
        runtime.processes = SimpleNamespace(check=lambda: None)
        dispatch_times = []
        original_terminated = [False]
        hold_started = [False]
        payload_frontiers = []

        def drain_payload(ns):
            payload_frontiers.append(ns)
            return ([{"observed_sim_ns": ns}], [])

        runtime.payload_adapter = SimpleNamespace(drain=drain_payload)

        async def dispatch(ns):
            dispatch_times.append(ns)
            if original_terminated[0]:
                hold_started[0] = True

        async def pulse(*args):
            return None

        async def step(iterations, quantum):
            runtime.gazebo.sim_ns += iterations * quantum
            original_terminated[0] = True  # native response settles during the first batch

        runtime.commands = SimpleNamespace(dispatch=dispatch, observe=lambda *a: None, drain=lambda: [])
        runtime.telemetry = SimpleNamespace(check=lambda: None, samples=lambda *a: [],
            heartbeat=SimpleNamespace(pulse=pulse), pulse_estimates=pulse)
        runtime.gazebo = SimpleNamespace(sim_ns=0, step=step, profile={}, drain_contacts=lambda: [])
        result = await runtime.advance({"to_sim_ns": 375 * STEP})
        assert dispatch_times == [0, 125 * STEP, 250 * STEP]
        assert hold_started[0]
        assert payload_frontiers == [125 * STEP, 250 * STEP, 375 * STEP]
        assert [event["observed_sim_ns"] for event in result["payload_events"]] == payload_frontiers

    asyncio.run(scenario())


def test_normal_ambiguous_request_is_also_quarantined():
    async def scenario():
        commands, _ = commands_fixture()
        future = asyncio.get_running_loop().create_future()
        future.set_exception(ConnectionError("no terminal native response"))
        commands.records["c1"].update(status="running", task=future, dispatched=True)
        commands.observe([hold_sample(STEP)], STEP)
        assert "v0" in commands.blocked
        assert request_state(future)[0] == "ambiguous"
        await commands.stop()

    asyncio.run(scenario())


def test_observed_completion_wins_cancel_race_but_a_spoofed_action_error_does_not():
    async def scenario():
        commands, telemetry = commands_fixture()
        ack = asyncio.get_running_loop().create_future()
        ack.set_result(None)
        record = commands.records["c1"]
        record.update(status="running", task=ack, dispatched=True)
        commands.observe([hold_sample(STEP)], STEP)
        telemetry.versions["v0"].update(flight_mode=2, velocity_enu=2)
        commands.observe([hold_sample(2 * STEP)], 2 * STEP)
        commands.observe([hold_sample(SECOND + 2 * STEP)], SECOND + 2 * STEP)
        assert record["status"] == "succeeded"
        assert commands.cancel({"vehicle": "v0", "command_id": "c1"}, SECOND + 2 * STEP)["status"] == "rejected"
        await commands.stop()
        commands, _ = commands_fixture()
        failure = asyncio.get_running_loop().create_future()
        failure.set_exception(type("ActionError", (Exception,), {})("not a real terminal MAVSDK result"))
        commands.records["c1"].update(status="running", task=failure, dispatched=True)
        commands.cancel({"vehicle": "v0", "command_id": "c1"}, 0)
        await commands.dispatch(STEP)
        assert "v0" in commands.blocked
        assert not commands.records["c1"]["cancel_hold_started"]
        await commands.stop()

    asyncio.run(scenario())


def test_unknown_or_reordered_estimate_interrupts_observed_hold_duration():
    async def scenario():
        commands, telemetry = commands_fixture()
        commands.estimate_completion = True
        ack = asyncio.get_running_loop().create_future()
        ack.set_result(None)
        record = commands.records["c1"]
        record.update(status="running", task=ack, dispatched=True)

        def sample(ns, *, known=True, source_ns=None):
            value = hold_sample(ns)
            value["vehicle_estimate"] = dict(status="available" if known else "unavailable",
                run_id="run", uav_id="v0", clock_epoch="epoch", origin_version="origin1",
                source_clock="px4_boot:0", clock_map={}, available_sim_ns=ns,
                source_sim_ns=ns if source_ns is None else source_ns, attitude_source_sim_ns=ns,
                position_enu_m=[0, 0, 0], velocity_enu_m_s=[0, 0, 0])
            return value

        commands.observe([sample(STEP)], STEP)  # establish conservative ACK frontier
        telemetry.versions["v0"].update(flight_mode=2, velocity_enu=2)
        commands.observe([sample(2 * STEP)], 2 * STEP)
        assert record["observed_since"] == 2 * STEP
        commands.observe([sample(SECOND // 2, known=False)], SECOND // 2)
        assert record["observed_since"] is None
        commands.observe([sample(SECOND)], SECOND)
        commands.observe([sample(SECOND + STEP, source_ns=SECOND - STEP)], SECOND + STEP)
        assert record["status"] == "running" and record["observed_since"] is None
        commands.observe([sample(2 * SECOND)], 2 * SECOND)
        commands.observe([sample(3 * SECOND)], 3 * SECOND)
        assert record["status"] == "succeeded"
        await commands.stop()

    asyncio.run(scenario())


def test_cancel_ids_and_unknown_command_are_not_coerced():
    commands, _ = commands_fixture()
    for payload in ({"vehicle": True, "command_id": "c1"},
                    {"vehicle": "v0", "command_id": "../c1"},
                    {"vehicle": "v0", "command_id": "c1", "extra": 1}):
        with pytest.raises(ValueError):
            commands.cancel(payload, 0)
    assert commands.cancel({"vehicle": "v0", "command_id": "missing"}, 0)["status"] == "rejected"


def receiver(tmp_path):
    return ContactReceiver(tmp_path, "package", "receiver::surface::collision", "/aas/contact", "epoch",
                           receiver_id="receiver", contact_duration_ns=3 * STEP, max_gap_ns=STEP)


def contact_pair():
    return [("receiver::surface::collision", "package::parcel::parcel_collision")]


def test_contact_duration_requires_release_scoped_identity_and_continuous_observations(tmp_path):
    contact = receiver(tmp_path)
    for ns in range(0, 5 * STEP, STEP):
        assert contact.consume(ns, contact_pair()) is None  # no release, no completion
    contact.arm(5 * STEP)
    assert contact.consume(5 * STEP, [("other::collision", "package::parcel")]) is None
    assert contact.consume(6 * STEP, contact_pair()) is None
    assert contact.consume(8 * STEP, contact_pair()) is None  # gap resets dwell
    assert contact.consume(9 * STEP, []) is None  # contact loss resets dwell
    for ns in (10 * STEP, 11 * STEP, 12 * STEP):
        assert contact.consume(ns, contact_pair()) is None
    receipt = contact.consume(13 * STEP, contact_pair())
    assert receipt["contact_start_ns"] == 10 * STEP
    assert receipt["contact_duration_ns"] == 3 * STEP
    assert contact.pop_receipts() == [receipt]
    assert contact.consume(14 * STEP, contact_pair()) is None
    assert contact.pop_receipts() == []


def test_reordered_contact_stamp_does_not_complete_duration(tmp_path):
    contact = receiver(tmp_path)
    contact.arm(0)
    contact.consume(2 * STEP, contact_pair())
    with pytest.raises(ValueError, match="regressed"):
        contact.consume(STEP, contact_pair())
    assert contact.first_ns is None
    assert not contact.completed


def test_payload_publication_and_detach_ack_never_complete_delivery(tmp_path):
    config = payload_config(physical_payload(), {"v0"}, STEP)
    now = [0]
    errors = []
    gazebo = SimpleNamespace(fail=errors.append)
    adapter = PayloadAdapter(config, gazebo, tmp_path, "epoch", lambda: now[0], physics_step_ns=STEP)
    adapter.set_episode_origin(10 * SECOND)
    published = []
    adapter.publisher = SimpleNamespace(publish=lambda msg: published.append(msg) or True)
    adapter.empty_type = lambda: object()
    assert adapter.release("v0", "package", 0, "release1")["status"] == "unavailable"
    adapter.state.update(status="attached", reason="fixture")
    ack = adapter.release("v0", "package", 0, "release1")
    assert ack["status"] == "accepted" and len(published) == 1
    assert not adapter.receiver.completed
    assert adapter.release("v0", "package", 0, "release2")["status"] == "rejected"
    now[0] = STEP
    adapter.on_state(SimpleNamespace(data="detached"))
    events, receipts = adapter.drain(STEP)
    assert events[-1]["released"] is True
    assert events[-1]["source_time_ns"] is None  # unstamped native joint transition
    assert receipts == [] and not adapter.receiver.completed
    for offset in range(4):
        ns = 10 * SECOND + (offset + 2) * STEP
        message = SimpleNamespace(
            HasField=lambda field: field == "header",
            header=SimpleNamespace(HasField=lambda field: field == "stamp",
                                   stamp=SimpleNamespace(sec=ns // SECOND, nsec=ns % SECOND)),
            contact=[SimpleNamespace(collision1=SimpleNamespace(name=adapter.receiver.receiver_collision),
                                     collision2=SimpleNamespace(name=contact_pair()[0][1]))],
        )
        adapter.on_contacts(message)
    _, receipts = adapter.drain(5 * STEP)
    assert not errors
    assert len(receipts) == 1 and receipts[0]["command_id"] == "release1"
    assert receipts[0]["contact_duration_ns"] == 3 * STEP


def sensor_fixture():
    camera_configured = camera_config(camera())
    owner = SimpleNamespace(id="v0", model_name="aas_v0")
    gazebo = SimpleNamespace(sim_ns=STEP)
    estimates = SimpleNamespace(sample=lambda *a, **k: {"status": "unavailable", "reason": "no source clock"})
    streams = SensorStreams(gazebo, SimpleNamespace(estimates=estimates), [owner], "run", "epoch", camera=camera_configured)
    return streams, gazebo, owner


def sensor_header(ns, frame):
    return SimpleNamespace(HasField=lambda field: field == "stamp",
                           stamp=SimpleNamespace(sec=ns // SECOND, nsec=ns % SECOND),
                           data=[SimpleNamespace(key="frame_id", value=[frame])])


def feed_sensor(streams, owner, ns=STEP, *, depth_ns=None, width=2):
    frame = sensor_frame(streams.camera, owner)
    header = sensor_header(ns, frame)
    streams.on_info("v0", SimpleNamespace(header=header, width=2, height=2,
        intrinsics=SimpleNamespace(k=[2, 0, 1, 0, 2, 1, 0, 0, 1]),
        distortion=SimpleNamespace(k=[], model=0)))
    streams.on_image("v0", "rgb", SimpleNamespace(header=header, width=width, height=2,
        step=width * 3, pixel_format_type=3, data=b"\x11\x22\x33" * width * 2))
    streams.on_image("v0", "depth", SimpleNamespace(header=sensor_header(ns if depth_ns is None else depth_ns, frame),
        width=2, height=2, step=8, pixel_format_type=13, data=struct.pack("<4f", 1, 2, 3, 4)))


def test_capture_requires_matching_geometry_stamps_and_owner_and_records_geometric_model():
    streams, gazebo, owner = sensor_fixture()
    assert streams.poll("v0")["status"] == "pending"
    feed_sensor(streams, owner, width=1)
    assert streams.poll("v0")["status"] == "unavailable"
    feed_sensor(streams, owner)
    result = streams.poll("v0")
    assert result["status"] == "succeeded"
    assert result["observation"]["physical_ranging"] is False
    assert result["observation"]["localization_status"] == "unavailable"
    calibration = result["observation"]["calibration"]
    assert "-0.0" not in json.dumps(calibration)
    content = {key: value for key, value in calibration.items() if key not in ("version", "camera_info_source_ns")}
    assert calibration["version"] == "epoch:rgbd:" + hashlib.sha256(
        json.dumps(content, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    frame = result["observation"]["frame_id"]
    chunk = streams.read_chunk("v0", frame, "rgb", 0, 262144)
    assert base64.b64decode(chunk["data_base64"]).startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        streams.poll("other")
    feed_sensor(streams, owner, 2 * STEP, depth_ns=3 * STEP)
    gazebo.sim_ns = 3 * STEP
    assert streams.poll("v0")["status"] == "pending"
    feed_sensor(streams, owner, STEP)  # source regression is sticky until a new epoch
    assert streams.poll("v0")["status"] == "unavailable"


def test_capture_chunk_is_unavailable_after_native_clock_reset():
    streams, gazebo, owner = sensor_fixture()
    feed_sensor(streams, owner)
    result = streams.poll("v0")
    frame = result["observation"]["frame_id"]
    assert streams.read_chunk("v0", frame, "rgb", 0)["status"] == "succeeded"
    gazebo.sim_ns = 0
    assert streams.read_chunk("v0", frame, "rgb", 0)["status"] == "unavailable"
    gazebo.sim_ns = STEP
    assert streams.poll("v0")["status"] == "unavailable"


def estimate_message(name, fields, *, system=1):
    return SimpleNamespace(message_name=name, fields_json=json.dumps(fields),
                           system_id=system, component_id=1)


def test_source_clock_estimate_is_unavailable_until_mapping_and_never_uses_truth():
    async def scenario():
        now = [SECOND]
        owner = SimpleNamespace(id="v0", index=0)
        streams = EstimateStreams([owner])
        streams.configure("run", "epoch", (47, 8, 100), lambda: now[0],
                          episode_origin_ns=SECOND, altitude_datum="amsl",
                          native_paused_callable=lambda: True)
        assert streams.sample("v0", 0)["status"] == "unavailable"

        async def send(message):
            fields = json.loads(message.fields_json)
            if message.message_name == "TIMESYNC":
                streams.receive("v0", estimate_message("TIMESYNC", {
                    "ts1": fields["ts1"], "tc1": now[0] + 5 * SECOND,
                }))

        def message_type(name, source_sys, source_comp, target_sys, target_comp, fields):
            return SimpleNamespace(message_name=name, fields_json=fields)

        sessions = {"v0": SimpleNamespace(mavlink_direct=SimpleNamespace(send_message=send))}
        streams.receive("v0", estimate_message("GPS_GLOBAL_ORIGIN", {
            "latitude": 470_000_000, "longitude": 80_000_000, "altitude": 100_000,
        }))
        position = dict(time_boot_ms=6000, x=1, y=2, z=-3, vx=4, vy=5, vz=-6)
        attitude = dict(time_boot_ms=6000, q1=1, q2=0, q3=0, q4=0)
        streams.receive("v0", estimate_message("LOCAL_POSITION_NED", position))
        streams.receive("v0", estimate_message("ATTITUDE_QUATERNION", attitude))
        assert streams.sample("v0", 0)["status"] == "unavailable"  # receipt time is not a map
        await streams.request(sessions, message_type)
        sample = streams.sample("v0", 0)
        assert sample["status"] == "available"
        assert sample["source_sim_ns"] == 0
        assert sample["position_enu_m"] == pytest.approx([2, 1, 3])
        assert sample["velocity_enu_m_s"] == [5, 4, 6]
        assert sample["clock_map"]["source_clock"] == "px4_boot:0"
        assert sample["horizontal_sigma_m"] is None
        streams.receive("v0", estimate_message("LOCAL_POSITION_NED", {**position, "time_boot_ms": 6001}))
        streams.receive("v0", estimate_message("ATTITUDE_QUATERNION", {**attitude, "time_boot_ms": 6001}))
        assert streams.sample("v0", 0)["status"] == "unavailable"  # never fall back past a future newest source
        now[0] += 2_000_000
        assert streams.sample("v0", 2_000_000)["status"] == "available"
        now[0] = 2 * SECOND
        unavailable = streams.sample("v0", SECOND)
        assert unavailable["status"] == "unavailable" and "position_enu_m" not in unavailable
        assert "stale" in unavailable["reason"]
        now[0] = 7 * SECOND
        assert "map is stale" in streams.sample("v0", 6 * SECOND)["reason"]
        # Source-clock reorder invalidates the whole epoch rather than returning cached truth.
        streams.receive("v0", estimate_message("LOCAL_POSITION_NED", {**position, "time_boot_ms": 5999}))
        assert streams.sample("v0", SECOND)["status"] == "unavailable"
        streams.receive("v0", estimate_message("LOCAL_POSITION_NED", {**position, "time_boot_ms": 7000}))
        assert streams.sample("v0", SECOND)["status"] == "unavailable"

    asyncio.run(scenario())


def test_estimate_wrong_owner_future_source_and_clock_reset_stay_unknown():
    async def scenario():
        now = [SECOND]
        streams = EstimateStreams([SimpleNamespace(id="v0", index=0)])
        streams.configure("run", "epoch", (47, 8, 100), lambda: now[0],
                          episode_origin_ns=SECOND, altitude_datum="amsl", native_paused_callable=lambda: True)
        assert streams.receive("v0", estimate_message("LOCAL_POSITION_NED", {}, system=2)) is False
        assert streams.sample("v0", 0)["status"] == "unavailable"
        now[0] = 0
        assert streams.sample("v0", 0)["status"] == "unavailable"
        assert "regressed" in streams.failure["v0"]
        streams.configure("run2", "epoch2", (47, 8, 100), lambda: now[0], altitude_datum=None)
        assert not streams.failure
        assert streams.sample("v0", 0)["status"] == "unavailable"

    asyncio.run(scenario())
    assert geodetic_to_enu((47, 8, 100), (47, 8, 100)) == pytest.approx([0, 0, 0])


def test_failed_release_publication_retains_attempt_but_never_arms_delivery(tmp_path):
    config = payload_config(physical_payload(), {"v0"}, STEP)
    errors = []
    adapter = PayloadAdapter(config, SimpleNamespace(fail=errors.append), tmp_path,
                             "epoch", lambda: STEP, physics_step_ns=STEP)
    adapter.set_episode_origin(0)
    adapter.state.update(status="attached")
    adapter.publisher = SimpleNamespace(publish=lambda message: False)
    adapter.empty_type = object
    ack = adapter.release("v0", "package", 0, "release1")
    assert ack["status"] == "failed"
    assert adapter.status("v0", "package")["command_id"] == "release1"
    assert adapter.release("v0", "package", STEP, "release2")["status"] == "rejected"
    adapter.on_state(SimpleNamespace(data="detached"))  # honest observed state is not release-command success
    events, receipts = adapter.drain(STEP)
    assert not errors and events[-1]["command_id"] == "release1"
    assert receipts == [] and adapter.receiver.release_ns is None
    scoped = [(adapter.receiver.receiver_collision, "package::parcel::parcel_collision")]
    for ns in range(5):
        assert adapter.receiver.consume((ns + 1) * STEP, scoped) is None
    assert not adapter.receiver.completed


def test_payload_contact_before_confirmed_detachment_does_not_count(tmp_path):
    config = payload_config(physical_payload(), {"v0"}, STEP)
    adapter = PayloadAdapter(config, SimpleNamespace(fail=lambda error: None), tmp_path,
                             "epoch", lambda: 0, physics_step_ns=STEP)
    adapter.set_episode_origin(0)
    adapter.state.update(status="attached")
    adapter.publisher = SimpleNamespace(publish=lambda message: True)
    adapter.empty_type = object
    adapter.release("v0", "package", 0, "release1")
    adapter.on_state(SimpleNamespace(data="detached"))
    scoped = [(adapter.receiver.receiver_collision, "package::parcel::parcel_collision")]
    # Detach notification without a confirmed frontier is not timed release evidence.
    for ns in range(4):
        assert adapter.receiver.consume(ns * STEP, scoped) is None
    adapter.drain(4 * STEP)
    assert adapter.receiver.release_ns == 4 * STEP
    for ns in (3 * STEP, 4 * STEP, 5 * STEP, 6 * STEP):
        assert adapter.receiver.consume(ns, scoped) is None
    assert adapter.receiver.consume(7 * STEP, scoped)["contact_start_ns"] == 4 * STEP


def test_ambiguous_native_model_link_pose_names_never_overwrite_physical_pose():
    gazebo = Gazebo("partition", "world", ["aas_v0"])
    pose = SimpleNamespace(name="aas_v0", position=SimpleNamespace(x=0, y=0, z=1),
        orientation=SimpleNamespace(x=0, y=0, z=0, w=1),
        HasField=lambda field: field in ("position", "orientation"))
    gazebo.on_pose(SimpleNamespace(HasField=lambda field: field == "header",
        header=sensor_header(STEP, "unused"), pose=[pose, pose]))
    with pytest.raises(RuntimeError, match="duplicate"):
        gazebo.check()
    assert gazebo.poses == {}


def test_image_layout_rejects_boolean_dimensions_and_inconsistent_bytes():
    for width, height in ((True, 2), (2, False), (2048, 2048), (2.0, 2)):
        with pytest.raises(ValueError):
            dimensions(width, height)
    with pytest.raises(ValueError):
        png_rgb(b"short", 2, 2, 6)
