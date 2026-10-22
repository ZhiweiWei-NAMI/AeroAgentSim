"""Host-only receiver admission; synthetic bytes are not browser/native acceptance."""

from __future__ import annotations

import base64
import copy
import math
import struct
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aerokernel import CommandRequest, Cut, Instant, Stamp

from aeroagentsim.observations.contracts import CaptureRequest, browser_encode
from aeroagentsim.observations.renderer import (
    DEPTH_SEMANTICS,
    MAX_DEPTH_PIXELS,
    BrowserRenderer,
    CaptureDepthError,
    StubRenderer,
)
from tests.observations.test_capture import ACTOR, png, receipts, setup_capture


@pytest.fixture
def request_depth() -> CaptureRequest:
    return CaptureRequest(
        "depth-1", ACTOR.run_id, ACTOR, Cut(7, Instant(2**53 + 1, 2)),
        {"revision": "explicit/v1", "frame": "enu", "eye": [1, 2, 3],
         "target": [1, 3, 3], "fov": 90, "near": 0.5, "far": 10, "depth": True},
        "assets/v1", Stamp("canonical", 2**53 + 1, 1, "canonical"), 2, 2, 10,
    )


def depth_bytes(values: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode("ascii")


def valid_reply(request: CaptureRequest) -> dict[str, Any]:
    binding = {"request_id": request.request_id,
               "source_cut": browser_encode(request.to_data()["source_cut"])}
    width, height = request.width, request.height
    focal = height / (2 * math.tan(math.radians(request.camera["fov"]) / 2))
    values = [1.0] * (width * height)
    values[-1] = math.nan
    return {
        "request": browser_encode(request.to_data()),
        "png_base64": base64.b64encode(png(width, height)).decode("ascii"),
        "depth": {**copy.deepcopy(binding), "encoding": "float32-le-metres",
                  "width": width, "height": height, "depth_convention": "optical_z",
                  "data_base64": depth_bytes(values), "near_m": 0.5, "far_m": 10,
                  "vertical_origin": "top-left", "invalid": "NaN",
                  "source": "same-camera-webgl-depth-buffer"},
        "calibration": {**copy.deepcopy(binding), "K": [focal, 0, width / 2, 0, focal, height / 2, 0, 0, 1],
                        "width": width, "height": height, "registered_rgb_depth": True,
                        "depth_convention": "optical_z", "depth_min_m": 0.5, "depth_max_m": 10,
                        "distortion": [], "depth_semantics": DEPTH_SEMANTICS},
        "camera_pose": {**copy.deepcopy(binding), "source": "applied_committed_capture_camera",
                        "T_world_enu_from_camera_optical": [
                            [1, 0, 0, 1], [0, 0, 1, 2], [0, -1, 0, 3], [0, 0, 0, 1]]},
    }


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Exercise BrowserRenderer.render, replacing only transport/lifecycle I/O."""
    monkeypatch.delenv("AEROAGENTSIM_CHROMIUM", raising=False)

    def make(reply: dict[str, Any]):
        renderer = BrowserRenderer("http://unused.invalid", node_modules=tmp_path)
        lifecycle = {"started": False, "closed": False}

        def start():
            lifecycle["started"] = True
            return SimpleNamespace(stdin=object(), stdout=object())

        monkeypatch.setattr(renderer, "_start", start)
        monkeypatch.setattr(renderer, "_send", lambda *args: None)
        monkeypatch.setattr(renderer, "_read_reply", lambda *args: reply)
        monkeypatch.setattr(renderer, "close", lambda: lifecycle.update(closed=True))
        return renderer, lifecycle

    return make


def test_valid_registered_depth_admitted(request_depth, bridge) -> None:
    reply = valid_reply(request_depth)
    reply["depth"]["data_base64"] = depth_bytes([0.5, 2, 10, math.nan])
    renderer, lifecycle = bridge(reply)
    frame = renderer.render(request_depth)
    assert frame.mode == "browser" and frame.png == png(2, 2)
    assert frame.depth is not None and frame.calibration is not None and frame.camera_pose is not None
    values = struct.unpack("<4f", base64.b64decode(frame.depth["data_base64"]))
    assert values[:3] == (0.5, 2, 10) and math.isnan(values[3])
    for metadata in (frame.depth, frame.calibration, frame.camera_pose):
        assert metadata["request_id"] == request_depth.request_id
        assert metadata["source_cut"] == request_depth.to_data()["source_cut"]
    assert lifecycle == {"started": True, "closed": False}


MISSING = object()
MALFORMED = [
    (("depth",), MISSING), (("depth",), None), (("depth",), []),
    (("calibration",), MISSING), (("calibration",), None), (("calibration",), []),
    (("camera_pose",), MISSING), (("camera_pose",), None), (("camera_pose",), []),
    (("depth", "encoding"), "float32-be-metres"),
    (("depth", "encoding"), "uint16-mm"),
    (("depth", "width"), 1), (("depth", "height"), 3),
    (("depth", "width"), 2.0), (("depth", "height"), True),
    (("depth", "depth_convention"), "euclidean_range"),
    (("depth", "vertical_origin"), "bottom-left"),
    (("depth", "invalid"), "0"), (("depth", "source"), "physical-ranging"),
    (("depth", "extra"), True),
    (("depth", "data_base64"), "!" * 24),
    (("depth", "data_base64"), "é" * 24),
    (("depth", "data_base64"), None),
    (("depth", "data_base64"), depth_bytes([1, 2, 3])),
    (("depth", "data_base64"), depth_bytes([1, 2, 3, 4, 5])),
    (("depth", "data_base64"), depth_bytes([1, 2, 3, 4]) + "\n"),
    (("depth", "data_base64"), depth_bytes([1, 2, 3, math.inf])),
    (("depth", "data_base64"), depth_bytes([1, 2, 3, -math.inf])),
    (("depth", "data_base64"), depth_bytes([1, 2, 3, 0])),
    (("depth", "data_base64"), depth_bytes([1, 2, 3, -1])),
    (("depth", "data_base64"), depth_bytes([1, 2, 3, 11])),
    (("depth", "near_m"), 0), (("depth", "near_m"), True),
    (("depth", "near_m"), 1), (("depth", "far_m"), math.inf),
    (("depth", "far_m"), 0.25), (("depth", "far_m"), 20),
    (("calibration", "width"), 3), (("calibration", "height"), "2"),
    (("calibration", "width"), True), (("calibration", "depth_min_m"), 0.25),
    (("calibration", "depth_max_m"), math.nan),
    (("calibration", "registered_rgb_depth"), 1),
    (("calibration", "registered_rgb_depth"), False),
    (("calibration", "depth_convention"), "range"),
    (("calibration", "distortion"), [0]),
    (("calibration", "depth_semantics"), "native weather sensor"),
    (("calibration", "K"), [[1, 0, 1], [0, 1, 1], [0, 0, 1]]),
    (("calibration", "K"), [1, 0]),
    (("calibration", "K", 0), -1), (("calibration", "K", 0), 2),
    (("calibration", "K", 0), True), (("calibration", "K", 2), 0),
    (("calibration", "K", 4), math.nan), (("calibration", "K", 8), 0),
    (("camera_pose", "source"), "native-pose"),
    (("camera_pose", "T_world_enu_from_camera_optical"), []),
    (("camera_pose", "T_world_enu_from_camera_optical", 0), [1, 0]),
    (("camera_pose", "T_world_enu_from_camera_optical", 0, 0), math.nan),
    (("camera_pose", "T_world_enu_from_camera_optical", 0, 0), True),
    (("camera_pose", "T_world_enu_from_camera_optical", 0, 0), 2),
    (("camera_pose", "T_world_enu_from_camera_optical", 0, 0), {"$integer": str(10**200)}),
    (("camera_pose", "T_world_enu_from_camera_optical"),
     [[-1, 0, 0, 1], [0, 0, 1, 2], [0, 1, 0, 3], [0, 0, 0, 1]]),
    (("camera_pose", "T_world_enu_from_camera_optical"),
     [[-1, 0, 0, 1], [0, 0, -1, 2], [0, -1, 0, 3], [0, 0, 0, 1]]),
    (("camera_pose", "T_world_enu_from_camera_optical", 0, 0), -1),
    (("camera_pose", "T_world_enu_from_camera_optical", 0, 3), 99),
    (("camera_pose", "T_world_enu_from_camera_optical", 3, 0), 1),
    (("camera_pose", "T_world_enu_from_camera_optical", 1), [0, 0, -1, 2]),
    (("request",), MISSING), (("request", "request_id"), "other"),
    (("request", "camera", "fov"), 80), (("request", "camera", "depth"), 1),
    (("request", "source_cut", "index"), 8),
    (("png_base64",), base64.b64encode(png(3, 2)).decode("ascii")),
    (("png_base64",), "not a PNG"),
]


@pytest.mark.parametrize("path,value", MALFORMED, ids=["/".join(map(str, p)) + f"-{i}" for i, (p, _) in enumerate(MALFORMED)])
def test_malformed_depth_never_returns_a_frame(request_depth, bridge, path, value) -> None:
    reply = valid_reply(request_depth)
    node = reply
    for key in path[:-1]:
        node = node[key]
    if value is MISSING:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    renderer, lifecycle = bridge(reply)
    with pytest.raises(CaptureDepthError) as error:
        renderer.render(request_depth)
    assert error.value.request_id == request_depth.request_id
    assert lifecycle["closed"]


@pytest.mark.parametrize("part", ["depth", "calibration", "camera_pose"])
@pytest.mark.parametrize("variant", ["missing-id", "wrong-id", "missing-cut", "wrong-index", "wrong-ns",
                                     "wrong-microstep", "float-index", "string-ns", "bool-microstep", "bad-tag", "extra-cut-key"])
def test_every_bundle_member_is_bound_to_exact_request_and_cut(request_depth, bridge, part, variant) -> None:
    reply = valid_reply(request_depth)
    metadata = reply[part]
    cut = metadata["source_cut"]
    if variant == "missing-id":
        del metadata["request_id"]
    elif variant == "wrong-id":
        metadata["request_id"] = "stale"
    elif variant == "missing-cut":
        del metadata["source_cut"]
    elif variant == "wrong-index":
        cut["index"] += 1
    elif variant == "wrong-ns":
        cut["instant"][0] = {"$integer": str(2**53)}
    elif variant == "wrong-microstep":
        cut["instant"][1] += 1
    elif variant == "float-index":
        cut["index"] = 7.0
    elif variant == "string-ns":
        cut["instant"][0] = str(2**53 + 1)
    elif variant == "bool-microstep":
        cut["instant"][1] = True
    elif variant == "bad-tag":
        cut["instant"][0] = {"$integer": "01"}
    else:
        cut["untrusted"] = True
    renderer, lifecycle = bridge(reply)
    with pytest.raises(CaptureDepthError):
        renderer.render(request_depth)
    assert lifecycle["closed"]


@pytest.mark.parametrize("depth_option", [None, False])
@pytest.mark.parametrize("extra_bundle", [False, True])
def test_rgb_only_keeps_existing_admission_and_ignores_unsolicited_depth(request_depth, bridge, depth_option, extra_bundle) -> None:
    camera = {key: value for key, value in request_depth.camera.items() if key != "depth"}
    if depth_option is not None:
        camera["depth"] = depth_option
    request = replace(request_depth, camera=camera)
    reply = {"request": browser_encode(request.to_data()), "png_base64": base64.b64encode(png(2, 2)).decode("ascii")}
    if extra_bundle:
        reply.update(depth="malformed unsolicited extension", calibration=None, camera_pose=[])
    renderer, lifecycle = bridge(reply)
    frame = renderer.render(request, label="RGB diagnostic")
    assert frame.png == png(2, 2) and frame.mode == "browser"
    assert frame.depth is frame.calibration is frame.camera_pose is None
    assert not lifecycle["closed"]


@pytest.mark.parametrize("variant", ["option", "budget", "overlay"])
def test_invalid_depth_request_fails_before_starting_browser(request_depth, bridge, variant) -> None:
    request = request_depth
    label = None
    if variant == "option":
        request = replace(request, camera={**request.camera, "depth": 1})
    elif variant == "budget":
        request = replace(request, width=8192, height=MAX_DEPTH_PIXELS // 8192 + 1)
    else:
        label = "not registered"
    renderer, lifecycle = bridge({})
    with pytest.raises(CaptureDepthError):
        renderer.render(request, label=label)
    assert not lifecycle["started"]


@pytest.mark.parametrize("revision", ["traffic-nadir/v1", "traffic-city-nadir/v1", "uav-gimbal-oblique/v1"])
@pytest.mark.parametrize("wrong_pose", [False, True])
def test_snapshot_derived_pose_is_checked(request_depth, bridge, revision, wrong_pose) -> None:
    camera = {key: value for key, value in request_depth.camera.items() if key not in {"eye", "target"}}
    camera.update(revision=revision, snapshot={
        "cut": request_depth.to_data()["source_cut"],
        "entities": [{"id": ACTOR.id, "generation": ACTOR.generation, "field": "pose", "position": [1, 2, 3]},
                     {"id": "target-subject", "generation": 0, "field": "pose", "position": [1, 5, 0]}],
    })
    if revision == "uav-gimbal-oblique/v1":
        camera.update(subject={"id": "target-subject", "generation": 0, "field": "pose"}, target_field="pose", pitch_deg=45)
    request = replace(request_depth, camera=camera)
    reply = valid_reply(request)
    if not wrong_pose:
        s = math.sqrt(0.5)
        reply["camera_pose"]["T_world_enu_from_camera_optical"] = (
            [[1, 0, 0, 1], [0, -s, s, 2], [0, -s, -s, 3], [0, 0, 0, 1]]
            if revision == "uav-gimbal-oblique/v1" else
            [[1, 0, 0, 1], [0, -1, 0, 2], [0, 0, -1, 3], [0, 0, 0, 1]])
    renderer, _ = bridge(reply)
    if wrong_pose:
        with pytest.raises(CaptureDepthError, match="axes"):
            renderer.render(request)
    else:
        assert renderer.render(request).camera_pose is not None


@pytest.mark.parametrize("missing", ["eye", "target"])
def test_explicit_pose_needs_both_constraints(request_depth, bridge, missing) -> None:
    camera = {key: value for key, value in request_depth.camera.items() if key != missing}
    request = replace(request_depth, camera=camera)
    renderer, _ = bridge(valid_reply(request))
    with pytest.raises(CaptureDepthError, match="vectors"):
        renderer.render(request)


def test_large_enu_coordinates_do_not_allow_relative_translation_drift(request_depth, bridge) -> None:
    request = replace(request_depth, camera={**request_depth.camera, "eye": [1e12, 2, 3], "target": [1e12, 3, 3]})
    reply = valid_reply(request)
    reply["camera_pose"]["T_world_enu_from_camera_optical"][0][3] = 1e12 + 999
    renderer, _ = bridge(reply)
    with pytest.raises(CaptureDepthError, match="translation"):
        renderer.render(request)


def test_non_square_rgb_with_default_clips_and_all_missing_depth(request_depth, bridge) -> None:
    camera = {key: value for key, value in request_depth.camera.items() if key not in {"near", "far"}}
    request = replace(request_depth, camera=camera, width=3)
    reply = valid_reply(request)
    reply["depth"].update(near_m=0.15, far_m=16000, data_base64=depth_bytes([math.nan] * 6))
    reply["calibration"].update(depth_min_m=0.15, depth_max_m=16000)
    renderer, _ = bridge(reply)
    assert renderer.render(request).depth is not None


def test_parallel_up_axis_matches_three_look_at_perturbation(request_depth, bridge) -> None:
    request = replace(request_depth, camera={**request_depth.camera, "target": [1, 2, 0]})
    reply = valid_reply(request)
    epsilon = 0.0001
    s = 1 / math.sqrt(1 + epsilon**2)
    reply["camera_pose"]["T_world_enu_from_camera_optical"] = [
        [1, 0, 0, 1], [0, -s, epsilon * s, 2], [0, -epsilon * s, -s, 3], [0, 0, 0, 1]]
    renderer, _ = bridge(reply)
    assert renderer.render(request).camera_pose is not None


@pytest.mark.parametrize("bits", [0x7FC00001, 0x7FFFFFFF, 0xFFC00001, 0x7F800001])
def test_all_ieee_nan_payloads_are_explicitly_missing(request_depth, bridge, bits) -> None:
    reply = valid_reply(request_depth)
    reply["depth"]["data_base64"] = base64.b64encode(struct.pack("<4I", bits, bits, bits, bits)).decode("ascii")
    renderer, _ = bridge(reply)
    assert renderer.render(request_depth).depth is not None


@pytest.mark.parametrize("part,field", [
    ("depth", field) for field in ["encoding", "width", "height", "depth_convention", "data_base64",
                                  "near_m", "far_m", "vertical_origin", "invalid", "source"]
] + [
    ("calibration", field) for field in ["K", "width", "height", "registered_rgb_depth", "depth_convention",
                                        "depth_min_m", "depth_max_m", "distortion", "depth_semantics"]
] + [("camera_pose", "source"), ("camera_pose", "T_world_enu_from_camera_optical")])
def test_no_required_bundle_field_is_optional(request_depth, bridge, part, field) -> None:
    reply = valid_reply(request_depth)
    del reply[part][field]
    renderer, _ = bridge(reply)
    with pytest.raises(CaptureDepthError, match="fields mismatch"):
        renderer.render(request_depth)


def test_depth_timeout_remains_timeout(request_depth, bridge, monkeypatch) -> None:
    renderer, lifecycle = bridge({})

    def timeout(*args):
        raise TimeoutError("viewer render deadline exceeded")

    monkeypatch.setattr(renderer, "_read_reply", timeout)
    with pytest.raises(TimeoutError):
        renderer.render(request_depth)
    assert lifecycle["closed"]


def test_render_world_pose_conversion(request_depth, bridge) -> None:
    request = replace(request_depth, camera={**request_depth.camera, "frame": "render-world",
                                           "eye": [1, 3, -2], "target": [1, 3, -3]})
    renderer, _ = bridge(valid_reply(request))
    assert renderer.render(request).camera_pose is not None


def test_png_only_stub_cannot_succeed_for_depth(request_depth) -> None:
    with pytest.raises(CaptureDepthError, match="stub"):
        StubRenderer({}).render(request_depth)


def test_depth_rejection_becomes_failed_capture_receipt_without_artifact(request_depth, bridge, tmp_path) -> None:
    reply: dict[str, Any] = {}
    renderer, lifecycle = bridge(reply)
    kernel, capture, request = setup_capture(tmp_path, renderer)
    try:
        request = replace(request, camera=request_depth.camera)
        reply.update(valid_reply(request))
        del reply["calibration"]
        command = kernel.submit(CommandRequest("capture.request", "capture", Instant(1), request.to_command_data()))
        kernel.run_until(1)
        assert kernel.view().action(command).status == "failed"
        terminal = [row for row in receipts(kernel) if row["status"] == "failed"][-1]
        assert terminal["result"]["status"] == "failed"
        assert terminal["result"]["request_id"] == request.request_id
        assert "calibration" in terminal["result"]["reason"]
        assert capture.store.list() == []
        assert request.request_id not in capture.seen
        assert capture.ready == []
        assert lifecycle["closed"]
        assert not list((tmp_path / "artifacts").glob("**/*.png"))
    finally:
        kernel.close()
