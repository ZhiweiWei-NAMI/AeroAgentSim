from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.providers import rpc as _RPC
from aero_bench.providers.px4_gazebo.observations import InspectionRgbObservation
from aero_bench.runtime.contracts import ProviderEvent, SimulationTime
from aero_bench.runtime.harness import HarnessCoordinator


_SERVICE_PATH = Path(__file__).parents[2] / "containers" / "px4-gazebo" / "service.py"
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_px4_gazebo_camera_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules["rpc"] = _RPC
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)


def test_px4_service_accepts_explicit_null_optional_world_binding(tmp_path) -> None:
    import json
    from tools import build_urban_infrastructure_inspection_v1 as builder

    schemas = builder._schemas(tmp_path, agent_acceptance=True)
    package, _ = builder._task_package(
        tmp_path, builder._ref(tmp_path, schemas["observation"]), agent_acceptance=True
    )
    _, bindings = builder._goals()
    configs = builder._provider_configs(
        tmp_path, package, bindings, agent_acceptance=True
    )
    document = json.loads(configs["px4"].read_bytes())
    assert document["world_name"] is None
    parsed = _SERVICE._px4_provider_config(document)
    assert parsed["world_name"] is None
    assert parsed["airspace_transition"] is None


def test_urban_observations_require_complete_native_artifact_inventory() -> None:
    assert _SERVICE.PUBLIC_SENSOR_FRAME_ARTIFACT_SCHEMA == (
        "aero-bench.public-sensor-frame-artifact/v2"
    )
    assert _SERVICE.PUBLIC_SENSOR_FRAME_SCHEMA == "sensor.frame_ref.public.v2"
    assert _SERVICE.INSPECTION_OBSERVATION_METADATA_SCHEMA == (
        "aero-bench.inspection-observation-metadata/v1"
    )
    assert _SERVICE._expected_px4_artifact_types(
        inspections=(),
        camera_declarations=(),
        urban_camera_observations=({"observation_kind": "telemetry"},),
    ) == frozenset({"trajectory", "observation", "sensor-frame", "camera-frame-data"})


def test_public_rgb_frame_uses_v2_provider_interaction_contract() -> None:
    event = ProviderEvent(
        provider_id="flight",
        event_id="public.sensor-frame",
        time=SimulationTime(tick=1, sim_time_ns=2_000_000_000),
        payload_schema_id="sensor.frame_ref.public.v2",
    )

    HarnessCoordinator._validate_stage_events(
        type(
            "StageResult",
            (),
            {
                "target": event.time,
                "step_receipt": type("Receipt", (), {"events": (event,)})(),
            },
        )()
    )
    assert HarnessCoordinator._provider_interaction_type(event) == (
        "sensor.frame_ref.v2"
    )
    assert _SERVICE._expected_px4_artifact_types(
        inspections=({"work_order_id": "inspection.1"},),
        camera_declarations=(),
        urban_camera_observations=(),
    ) == frozenset({"trajectory", "observation", "sensor-frame", "camera-frame-data"})


@pytest.mark.parametrize("metadata", [None, [], [{"key": "frame_id", "value": ["camera_link"]}]])
def test_real_gazebo_rgb_frame_is_decoded_and_encoded_as_png(metadata) -> None:
    pixels = bytes((255, 0, 0, 0, 255, 0))
    header = {"stamp": {"sec": 12, "nsec": 34}}
    if metadata is not None:
        header["data"] = metadata
    frame = _SERVICE.RealPx4Stack._parse_camera_frame(
        {
            "width": 2,
            "height": 1,
            "pixelFormatType": "RGB_INT8",
            "step": 6,
            "header": header,
            "data": base64.b64encode(pixels).decode("ascii"),
        },
        vehicle_id="uav.1",
        camera_id="camera.1",
        expected_width=2,
        expected_height=1,
    )

    assert frame.vehicle_id == "uav.1"
    assert frame.camera_id == "camera.1"
    assert frame.width == 2
    assert frame.height == 1
    assert frame.pixel_format == "RGB_INT8"
    assert frame.engine_sim_time_ns == 12_000_000_034
    assert frame.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_agent_rgb_contract_binds_exact_png_content() -> None:
    png = _SERVICE._rgb_png(width=1, height=1, pixels=bytes((1, 2, 3)))
    encoded = base64.b64encode(png).decode("ascii")

    parsed = InspectionRgbObservation.model_validate(
        {
            "schema_version": "aero-bench.observation.rgb/v1",
            "target_id": "target.1",
            "camera_id": "camera.1",
            "distance_m": 10.0,
            "view_angle_deg": 2.0,
            "visual_kind": "gazebo_rgb",
            "visual_status": "captured",
            "frame_id": "frame.agent.1",
            "vehicle_id": "uav.1",
            "engine_sim_time_ns": 12,
            "logical_origin_engine_ns": 2,
            "capture_pose_source": "gazebo.pose.private_digest",
            "camera_pose_sha256": "a" * 64,
            "target_pose_sha256": "b" * 64,
            "image_sha256": hashlib.sha256(png).hexdigest(),
            "size_bytes": len(png),
            "selector": "frames/frame.agent.1",
            "width": 1,
            "height": 1,
            "media_type": "image/png",
            "source": "gazebo.camera",
            "image_base64": encoded,
        }
    )

    assert base64.b64decode(parsed.image_base64) == png


def test_real_gazebo_rgb_frame_rejects_wrong_sensor_contract() -> None:
    with pytest.raises(_SERVICE.Px4ServiceError, match="dimensions"):
        _SERVICE.RealPx4Stack._parse_camera_frame(
            {
                "width": 1,
                "height": 1,
                "pixelFormatType": "RGB_INT8",
                "step": 3,
                "data": base64.b64encode(bytes((0, 0, 0))).decode("ascii"),
            },
            vehicle_id="uav.1",
            camera_id="camera.1",
            expected_width=2,
            expected_height=1,
        )


def test_urban_camera_frame_archive_is_content_addressed_and_closed(
    tmp_path: Path,
) -> None:
    image = b"\x89PNG\r\n\x1a\nreal-frame"
    frame_id = "frame.uav.policy.01.observation.camera.0000000000000001"
    archive = tmp_path / "camera-frames.bin"
    archive.write_bytes(_SERVICE._camera_frame_archive_record(frame_id, image))
    expected = (
        {
            "frame_id": frame_id,
            "size_bytes": len(image),
            "image_sha256": _SERVICE._digest_bytes(image),
        },
    )

    _SERVICE._validate_camera_frame_archive(archive, expected)

    archive.write_bytes(archive.read_bytes()[:-1])
    with pytest.raises(_SERVICE.Px4ServiceError, match="truncated PNG"):
        _SERVICE._validate_camera_frame_archive(archive, expected)


def test_urban_camera_frame_archive_rejects_unexpected_frame(tmp_path: Path) -> None:
    image = b"frame"
    archive = tmp_path / "camera-frames.bin"
    archive.write_bytes(
        _SERVICE._camera_frame_archive_record("frame.unexpected", image)
    )
    with pytest.raises(_SERVICE.Px4ServiceError, match="inventory"):
        _SERVICE._validate_camera_frame_archive(archive, ())

    assert "capture_camera_frame" not in _SERVICE.Px4RuntimeStack.__dict__
    assert "camera_frame" in _SERVICE.Px4RuntimeStack.__dict__
    assert "camera_frame" in _SERVICE.RealPx4Stack.__dict__
    assert "_parse_camera_frame" in _SERVICE.RealPx4Stack.__dict__


@pytest.mark.parametrize('metadata', [
    {}, [{'key': 'frame_id', 'value': 'camera_link'}],
    [{'key': 'frame_id', 'value': [3]}],
    [{'key': 'frame_id', 'value': [], 'unexpected': True}],
])
def test_gazebo_camera_rejects_invalid_header_metadata(metadata):
    with pytest.raises(_SERVICE.Px4ServiceError, match='camera.header.data'):
        _SERVICE.RealPx4Stack._parse_camera_frame(
            {
                'width': 1, 'height': 1, 'pixelFormatType': 'RGB_INT8', 'step': 3,
                'header': {'stamp': {'sec': 12, 'nsec': 0}, 'data': metadata},
                'data': base64.b64encode(bytes((1, 2, 3))).decode('ascii'),
            },
            vehicle_id='uav.1', camera_id='camera.1', expected_width=1, expected_height=1,
        )


def test_camera_accepts_native_gz_topic_json_with_omitted_zero_nsec():
    # Captured from the locked Gazebo CLI protocol probe, not flight evidence.
    frame = _SERVICE.RealPx4Stack._parse_camera_frame(
        {
            'header': {'stamp': {'sec': '12'}, 'data': [
                {'key': 'frame_id', 'value': ['camera_link']},
            ]},
            'width': 2, 'height': 1, 'step': 6,
            'data': 'YWJjZGVm', 'pixelFormatType': 'RGB_INT8',
        },
        vehicle_id='uav.1', camera_id='camera.1', expected_width=2, expected_height=1,
    )
    assert frame.engine_sim_time_ns == 12_000_000_000


def _shared_camera_stack(tmp_path: Path, camera_count: int = 1):
    stack = _SERVICE.RealPx4Stack(tmp_dir=tmp_path)
    cameras = tuple(
        {
            "camera_id": f"camera.{index}",
            "camera_vehicle_id": f"uav.{index}",
            "camera_mount": {key: 0.0 for key in (
                "x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad",
            )},
            "resolution_width_px": 1,
            "resolution_height_px": 1,
            "target_id": f"target.{target}",
        }
        for index in range(camera_count)
        for target in range(3)
    )
    stack._config = SimpleNamespace(
        camera_declarations=(), inspections=cameras, command_timeout_ms=1000,
        world_name="world",
        vehicles=tuple({
            "vehicle_id": f"uav.{index}", "gazebo_model_name": f"model.{index}",
        } for index in range(camera_count)),
    )
    stack._time_origin_ns = 12_000_000_000
    return stack


class _CameraMessage:
    def __init__(self, payload=None):
        self.payload = payload

    def CopyFrom(self, other):
        import copy
        self.payload = copy.deepcopy(other.payload)


def _camera_payload(stamp=12_500_000_000):
    return {
        "header": {"stamp": {"sec": str(stamp // 1_000_000_000),
                              "nsec": stamp % 1_000_000_000}},
        "width": 1, "height": 1, "step": 3,
        "data": "AQID", "pixelFormatType": "RGB_INT8",
    }


def _native_camera_stack(tmp_path, monkeypatch, camera_count=1):
    stack = _shared_camera_stack(tmp_path, camera_count)
    stack._config.command_timeout_ms = 30
    events = []

    class Publisher:
        connected = True
        accepted = True
        on_publish = None

        def valid(self):
            return True

        def has_connections(self):
            return self.connected

        def publish(self, message):
            assert message.data is True
            events.append(("trigger",))
            if self.on_publish is not None:
                self.on_publish()
            return self.accepted

    class Node:
        reject_number = None

        def __init__(self):
            self.callbacks = {}
            self.publisher = Publisher()

        def subscribe(self, image_type, topic, callback):
            assert image_type is _CameraMessage
            events.append(("subscribe", topic))
            if len(self.callbacks) + 1 == self.reject_number:
                return False
            self.callbacks[topic] = callback
            return True

        def unsubscribe(self, topic):
            events.append(("unsubscribe", topic))
            self.callbacks.pop(topic)
            return True

        def advertise(self, topic, boolean_type):
            assert topic == _SERVICE.CAMERA_TRIGGER_TOPIC
            events.append(("advertise", topic))
            return self.publisher

        def emit(self, stamp=12_500_000_000):
            for callback in tuple(self.callbacks.values()):
                callback(_CameraMessage(_camera_payload(stamp)))

    node = Node()
    node.publisher.on_publish = node.emit
    monkeypatch.setattr(stack, "_require_gazebo_transport",
                        lambda config: (node, None, SimpleNamespace))
    monkeypatch.setattr(stack, "_camera_image_type", lambda: _CameraMessage)
    monkeypatch.setattr(stack, "_camera_message_payload", lambda message: message.payload)

    async def forbidden(*args, **kwargs):
        pytest.fail("camera capture must not advance the world or invoke a CLI")
    monkeypatch.setattr(stack, "_advance_world_control", forbidden)
    monkeypatch.setattr(stack, "_gazebo_world_control", forbidden)
    monkeypatch.setattr(stack, "_gz_cli", forbidden)
    return stack, node, events


@pytest.mark.parametrize("camera_count", [1, 2])
def test_shared_camera_native_single_global_trigger(tmp_path, monkeypatch, camera_count):
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch, camera_count)

    async def run():
        await stack._start_camera_subscribers(stack._config)
        await stack._capture_camera_frames(500_000_000)
        assert set(stack._camera_frames) == {f"camera.{i}" for i in range(camera_count)}
        for frame in stack._camera_frames.values():
            assert frame.engine_sim_time_ns == 12_500_000_000
        assert len([event for event in events if event[0] == "trigger"]) == 1
        assert all(event[0] != "trigger" for event in events[:camera_count])
        await stack._stop_camera_subscribers()
        assert not node.callbacks
        assert not stack._camera_frames
    asyncio.run(run())


def test_camera_conflict_fails_before_subscription(tmp_path, monkeypatch):
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch)
    stack._config.inspections[1]["resolution_width_px"] = 2
    with pytest.raises(_SERVICE.Px4ServiceError, match="conflict"):
        asyncio.run(stack._start_camera_subscribers(stack._config))
    assert not events


def test_camera_start_failure_rolls_back_registered_topics(tmp_path, monkeypatch):
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch, 2)
    node.reject_number = 2
    with pytest.raises(_SERVICE.Px4ServiceError, match="subscription rejected"):
        asyncio.run(stack._start_camera_subscribers(stack._config))
    assert not node.callbacks
    assert not stack._camera_stream_active
    assert not stack._camera_callbacks


def test_camera_generation_rejects_callback_after_restart(tmp_path, monkeypatch):
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch)

    async def run():
        await stack._start_camera_subscribers(stack._config)
        old_callback = next(iter(node.callbacks.values()))
        await stack._stop_camera_subscribers()
        await stack._start_camera_subscribers(stack._config)
        old_callback(_CameraMessage(_camera_payload()))
        assert stack._camera_stream_versions == {"camera.0": 0}
        await stack._capture_camera_frames(500_000_000)
        assert stack._camera_stream_versions == {"camera.0": 1}
        await stack._stop_camera_subscribers()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["transport", "future", "conflict", "cancel"])
def test_camera_failure_invalidates_cache_and_partial_batch(tmp_path, monkeypatch, failure):
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch, 2)

    async def run():
        await stack._start_camera_subscribers(stack._config)
        stack._camera_frames = {"camera.0": object(), "camera.1": object()}
        if failure == "transport":
            node.publisher.accepted = False
        elif failure == "future":
            def mixed():
                callbacks = list(node.callbacks.values())
                callbacks[0](_CameraMessage(_camera_payload()))
                callbacks[1](_CameraMessage(_camera_payload(13_000_000_000)))
            node.publisher.on_publish = mixed
        elif failure == "conflict":
            stack._config.inspections[1]["resolution_width_px"] = 2
        else:
            node.publisher.on_publish = None
        task = asyncio.create_task(stack._capture_camera_frames(500_000_000))
        if failure == "cancel":
            while not any(event[0] == "trigger" for event in events):
                await asyncio.sleep(0)
            task.cancel()
        expected = asyncio.CancelledError if failure == "cancel" else _SERVICE.Px4ServiceError
        with pytest.raises(expected):
            await task
        assert not stack._camera_frames
        with pytest.raises(_SERVICE.Px4ServiceError):
            stack.camera_frame("camera.0")
        if failure != "conflict":
            node.emit()
            with pytest.raises(_SERVICE.Px4ServiceError, match="requires reset"):
                await stack._capture_camera_frames(500_000_000)
        await stack._stop_camera_subscribers()
    asyncio.run(run())


def test_camera_old_frame_waits_for_new_exact_frame(tmp_path, monkeypatch):
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch)

    async def run():
        await stack._start_camera_subscribers(stack._config)
        node.emit(12_000_000_000)
        def publish():
            node.emit(12_000_000_000)
            asyncio.get_running_loop().call_later(0.001, node.emit)
        node.publisher.on_publish = publish
        await stack._capture_camera_frames(500_000_000)
        assert stack.camera_frame("camera.0").engine_sim_time_ns == 12_500_000_000
        await stack._stop_camera_subscribers()
    asyncio.run(run())


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("writable", [False, True])
def test_camera_timeout_preserves_actual_phase(tmp_path, monkeypatch, connected, writable):
    import json
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch)
    node.publisher.connected = connected
    node.publisher.on_publish = None
    if not writable:
        def fail_log(*args, **kwargs):
            raise OSError("diagnostic storage failed")
        monkeypatch.setattr(stack, "_record_gz_diagnostic", fail_log)

    async def run():
        await stack._start_camera_subscribers(stack._config)
        with pytest.raises(_SERVICE.Px4ServiceError, match="exact barrier time") as caught:
            await stack._capture_camera_frames(500_000_000)
        phase = "image-delivery" if connected else "trigger-connection"
        assert phase in str(caught.value)
        assert "12500000000" in str(caught.value)
        assert len([e for e in events if e[0] == "trigger"]) == int(connected)
        if writable:
            record = json.loads((tmp_path / "logs" / "gz-command-diagnostics.jsonl").read_text())
            assert record["phase"] == phase
            assert record["trigger_has_connections"] is connected
        await stack._stop_camera_subscribers()
    asyncio.run(run())


def test_camera_callback_owns_copy_of_native_message(tmp_path, monkeypatch):
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch)

    async def run():
        await stack._start_camera_subscribers(stack._config)
        def publish():
            message = _CameraMessage(_camera_payload())
            next(iter(node.callbacks.values()))(message)
            message.payload["width"] = 99
        node.publisher.on_publish = publish
        await stack._capture_camera_frames(500_000_000)
        assert stack.camera_frame("camera.0").width == 1
        await stack._stop_camera_subscribers()
    asyncio.run(run())


def test_camera_stop_during_connection_wait_does_not_deadlock(tmp_path, monkeypatch):
    stack, node, events = _native_camera_stack(tmp_path, monkeypatch)
    node.publisher.connected = False

    async def run():
        await stack._start_camera_subscribers(stack._config)
        task = asyncio.create_task(stack._capture_camera_frames(500_000_000))
        await asyncio.sleep(0)
        await asyncio.wait_for(stack._stop_camera_subscribers(), 0.1)
        with pytest.raises(_SERVICE.Px4ServiceError, match="inactive"):
            await task
        assert not node.callbacks
    asyncio.run(run())
