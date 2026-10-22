"""Persistent headless viewer bridge with an explicitly selected fixture mode."""

from __future__ import annotations

import base64
import json
import math
import os
import select
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from .contracts import CaptureRequest, browser_decode, browser_encode
from .png import validate_png


@dataclass(frozen=True)
class RenderedFrame:
    png: bytes
    mode: str
    depth: dict[str, Any] | None = None
    calibration: dict[str, Any] | None = None
    camera_pose: dict[str, Any] | None = None


class CaptureDepthError(ValueError):
    """Requested registered depth was not admitted; never a partial success."""

    def __init__(self, request_id: str, reason: str) -> None:
        self.request_id = request_id
        super().__init__(f"capture depth {request_id}: {reason}")


MAX_DEPTH_PIXELS = 4_194_304
DEPTH_SEMANTICS = "rendered geometry (optical z); not physical ranging or weather sensing"


def _depth_requested(request: CaptureRequest) -> bool:
    if "depth" in request.camera and type(request.camera["depth"]) is not bool:
        raise CaptureDepthError(request.request_id, "camera.depth must be boolean")
    return request.camera.get("depth") is True


def _finite(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _same_metadata(actual: Any, expected: Any) -> bool:
    """JSON number roundtrips may drop .0, but booleans are never numbers."""
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and actual.keys() == expected.keys()
                and all(_same_metadata(actual[key], value) for key, value in expected.items()))
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_same_metadata(a, b) for a, b in zip(actual, expected)))
    if type(expected) in (int, float):
        return _finite(actual) and actual == expected
    return type(actual) is type(expected) and actual == expected


def _validate_depth(
    request: CaptureRequest, reply: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Admit the bounded v1 rendered-geometry bundle, not sensor accuracy.

    The PNG has already been validated against the same request dimensions.
    NaN float32 samples are the sole missing-value sentinel; infinity is invalid.
    See docs/reference/capture-depth.md for frames, pixel centres and limitations.
    """
    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise CaptureDepthError(request.request_id, reason)

    def record(name: str, fields: set[str]) -> dict[str, Any]:
        value = browser_decode(reply.get(name))
        require(isinstance(value, dict), f"{name} must be present as a record")
        require(set(value) == fields | {"request_id", "source_cut"}, f"{name} fields mismatch")
        require(value["request_id"] == request.request_id, f"{name} request_id mismatch")
        cut = value["source_cut"]
        require(isinstance(cut, dict) and set(cut) == {"index", "instant"}, f"{name} source_cut malformed")
        require(type(cut["index"]) is int and isinstance(cut["instant"], list)
                and len(cut["instant"]) == 2 and all(type(v) is int for v in cut["instant"]),
                f"{name} source_cut must contain exact integers")
        require(cut == request.to_data()["source_cut"], f"{name} source_cut mismatch")
        return value

    width, height = request.width, request.height
    require(width * height <= MAX_DEPTH_PIXELS, "depth pixel budget exceeded")
    depth = record("depth", {"encoding", "width", "height", "depth_convention", "data_base64",
                             "near_m", "far_m", "vertical_origin", "invalid", "source"})
    calibration = record("calibration", {"K", "width", "height", "registered_rgb_depth",
                                         "depth_convention", "depth_min_m", "depth_max_m",
                                         "distortion", "depth_semantics"})
    pose = record("camera_pose", {"T_world_enu_from_camera_optical", "source"})
    for item, name in ((depth, "depth"), (calibration, "calibration")):
        require(type(item["width"]) is int and type(item["height"]) is int
                and (item["width"], item["height"]) == (width, height), f"{name} dimensions mismatch")
        require(item["depth_convention"] == "optical_z", f"{name} must use optical_z")
    require(depth["encoding"] == "float32-le-metres", "unsupported depth encoding")
    require(depth["vertical_origin"] == "top-left" and depth["invalid"] == "NaN",
            "unsupported depth origin or missing sentinel")
    require(depth["source"] == "same-camera-webgl-depth-buffer", "depth source mismatch")
    near, far = depth["near_m"], depth["far_m"]
    require(_finite(near) and _finite(far) and 0 < near < far, "invalid depth clip planes")
    require(_same_metadata(near, request.camera.get("near", 0.15))
            and _same_metadata(far, request.camera.get("far", 16000)), "depth clip planes mismatch request")
    require(_finite(calibration["depth_min_m"]) and _finite(calibration["depth_max_m"])
            and calibration["depth_min_m"] == near and calibration["depth_max_m"] == far,
            "calibration clip planes mismatch")
    require(calibration["registered_rgb_depth"] is True and calibration["distortion"] == []
            and calibration["depth_semantics"] == DEPTH_SEMANTICS, "invalid calibration semantics")
    fov = request.camera.get("fov")
    require(_finite(fov) and 0 < fov < 180, "invalid requested vertical FOV")
    tangent = math.tan(math.radians(fov) / 2)
    require(_finite(tangent) and tangent > 0, "invalid requested vertical FOV")
    focal = height / (2 * tangent)
    require(_finite(focal) and focal > 0, "invalid requested focal length")
    expected_k = [focal, 0, width / 2, 0, focal, height / 2, 0, 0, 1]
    k = calibration["K"]
    require(isinstance(k, list) and len(k) == 9 and all(_finite(v) for v in k), "malformed calibration K")
    require(all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-8) for a, b in zip(k, expected_k)),
            "calibration K mismatch requested camera")
    require(pose["source"] == "applied_committed_capture_camera", "camera pose source mismatch")
    matrix = pose["T_world_enu_from_camera_optical"]
    require(isinstance(matrix, list) and len(matrix) == 4
            and all(isinstance(row, list) and len(row) == 4 and all(_finite(v) for v in row) for row in matrix),
            "camera pose must be a finite row-major 4x4 matrix")
    require(matrix[3] == [0, 0, 0, 1], "camera pose must be affine")
    rotation = [row[:3] for row in matrix[:3]]
    require(all(abs(v) <= 1 + 1e-6 for row in rotation for v in row), "camera pose rotation entries out of range")
    for i in range(3):
        for j in range(3):
            dot = sum(rotation[i][axis] * rotation[j][axis] for axis in range(3))
            require(math.isclose(dot, float(i == j), abs_tol=1e-6), "camera pose rotation is not orthonormal")
    a, b, c = rotation
    determinant = (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                   + a[2] * (b[0] * c[1] - b[1] * c[0]))
    require(math.isclose(determinant, 1, abs_tol=1e-6), "camera pose rotation is not right-handed")

    def vector(value: Any) -> list[float]:
        require(isinstance(value, list) and len(value) == 3 and all(_finite(v) for v in value),
                "camera requires finite eye/target/snapshot vectors")
        return [float(v) for v in value]

    def normalized(value: list[float]) -> list[float]:
        length = math.hypot(*value)
        require(math.isfinite(length) and length > 0, "invalid camera axis")
        return [v / length for v in value]

    def cross(a: list[float], b: list[float]) -> list[float]:
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

    camera = request.camera
    nadir = (camera.get("preset") == "actor-nadir"
             or camera.get("revision") in {"traffic-nadir/v1", "traffic-city-nadir/v1"})
    oblique = camera.get("revision") == "uav-gimbal-oblique/v1"
    if nadir or oblique:
        require(not nadir or camera.get("revision") == "traffic-nadir/v1"
                or (camera.get("revision") == "traffic-city-nadir/v1" and camera.get("frame") == "enu"),
                "unsupported nadir camera revision/frame")
        snapshot = camera.get("snapshot")
        require(isinstance(snapshot, dict) and _same_metadata(snapshot.get("cut"), request.to_data()["source_cut"])
                and isinstance(snapshot.get("entities"), list), "camera snapshot cut/poses missing or mismatched")

        def position(identity: str, generation: int, field: str | None = None) -> list[float]:
            rows = [row for row in snapshot["entities"] if isinstance(row, dict)
                    and row.get("id") == identity and str(row.get("generation")) == str(generation)
                    and (field is None or row.get("field") == field)]
            require(bool(rows), "camera snapshot position missing")
            require(all(isinstance(row.get("field"), str) for row in rows), "camera snapshot position field missing")
            values = [vector(row.get("position")) for row in rows]
            require(all(value == values[0] for value in values), "camera snapshot position ambiguous")
            return values[0]

        eye = position(request.actor.id, request.actor.generation)
        if oblique:
            require(camera.get("frame") == "enu", "oblique camera requires ENU")
            subject = camera.get("subject")
            require(isinstance(subject, dict) and isinstance(subject.get("id"), str)
                    and type(subject.get("generation")) is int and isinstance(subject.get("field"), str)
                    and subject["field"] == camera.get("target_field"), "camera subject descriptor malformed")
            subject_position = position(subject["id"], subject["generation"], subject["field"])
            if "position" in subject:
                require(vector(subject["position"]) == subject_position, "camera subject differs from snapshot")
            pitch = camera.get("pitch_deg")
            require(_finite(pitch) and 35 <= pitch <= 50 and fov < 150, "invalid oblique pitch/FOV")
            sx, sy = subject_position[0] - eye[0], subject_position[1] - eye[1]
            height_m = eye[2] - subject_position[2]
            require(math.isfinite(height_m) and height_m > 0, "oblique camera must be above subject")
            require(abs(math.degrees(math.atan2(height_m, math.hypot(sx, sy))) - pitch) < fov / 2,
                    "camera subject lies outside sensor field")
            if sx or sy:
                bearing = math.atan2(sy, sx)
            else:
                azimuth = camera.get("coincident_azimuth_deg")
                require(_finite(azimuth), "overhead camera requires explicit azimuth")
                bearing = math.radians(azimuth)
            require(math.isfinite(bearing), "invalid camera bearing")
            radius = height_m / math.tan(math.radians(pitch))
            target = [eye[0] + radius * math.cos(bearing), eye[1] + radius * math.sin(bearing), subject_position[2]]
            for name, resolved in (("eye", eye), ("target", target)):
                if name in camera:
                    require(all(math.isclose(a, b, rel_tol=0, abs_tol=1e-8) for a, b in zip(vector(camera[name]), resolved)),
                            f"oblique {name} disagrees with snapshot")
        else:
            require(eye[2] > 0, "nadir camera must be above ground")
            target = [eye[0], eye[1], 0.0]
    else:
        eye, target = vector(camera.get("eye")), vector(camera.get("target"))
        frame = camera.get("frame", "render-world")
        require(frame in {"enu", "render-world"}, "unsupported camera frame")
        if frame == "render-world":
            eye, target = [eye[0], -eye[2], eye[1]], [target[0], -target[2], target[1]]
    require(all(math.isclose(matrix[i][3], eye[i], rel_tol=0, abs_tol=1e-6) for i in range(3)),
            "camera pose translation mismatch request")
    # Reproduce Three.js lookAt's camera-up convention, including its deterministic
    # tiny perturbation when up and the optical axis are exactly parallel.
    back = normalized([eye[i] - target[i] for i in range(3)])
    up = [0.0, 1.0, 0.0] if nadir else [0.0, 0.0, 1.0]
    right = cross(up, back)
    if math.hypot(*right) == 0:
        back[0 if nadir else 1] += 0.0001 if nadir else -0.0001
        back = normalized(back)
        right = cross(up, back)
    right = normalized(right)
    camera_up = cross(back, right)
    axes = [right, [-v for v in camera_up], [-v for v in back]]
    require(all(math.isclose(matrix[row][column], axes[column][row], abs_tol=1e-6)
                for row in range(3) for column in range(3)), "camera pose axes mismatch request")
    encoded = depth["data_base64"]
    byte_count = width * height * 4
    require(isinstance(encoded, str) and len(encoded) == 4 * ((byte_count + 2) // 3),
            "depth base64 length mismatch")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise CaptureDepthError(request.request_id, "invalid depth base64") from exc
    require(len(data) == byte_count and base64.b64encode(data).decode("ascii") == encoded,
            "depth decoded byte count or base64 padding mismatch")
    for (value,) in struct.iter_unpack("<f", data):
        require(math.isnan(value) or (math.isfinite(value) and near <= value <= far),
                "depth sample must be within clip planes or NaN (missing)")
    return depth, calibration, pose


class Renderer(Protocol):
    def render(
        self, request: CaptureRequest, *, label: str | None = None
    ) -> RenderedFrame: ...
    def close(self) -> None: ...


class StubRenderer:
    """Explicit fixture bytes; missing fixtures fail rather than switch providers."""

    def __init__(self, fixtures: dict[str, Path]) -> None:
        self.fixtures = fixtures

    def render(
        self, request: CaptureRequest, *, label: str | None = None
    ) -> RenderedFrame:
        if _depth_requested(request):
            raise CaptureDepthError(request.request_id, "PNG-only stub has no registered depth")
        data = self.fixtures[request.request_id].read_bytes()
        validate_png(data, expected=(request.width, request.height))
        return RenderedFrame(data, "stub")

    def close(self) -> None:
        pass


def browser_executable_path(configured: Path | None = None) -> Path | None:
    """Prefer an authored executable, then the operator override, else Playwright."""
    if configured is None:
        value = os.environ.get("AEROAGENTSIM_CHROMIUM")
        if value is None:
            return None
        if not value:
            raise ValueError("AEROAGENTSIM_CHROMIUM: executable path must be nonempty")
        configured = Path(value)
    if not configured.is_file():
        raise FileNotFoundError(f"Chromium executable does not exist: {configured}")
    return configured


class BrowserRenderer:
    """One Node/Playwright browser process per bridge, reused across requests.

    The built viewer implements window.aeroCapture.render as documented in
    docs/guides/visualization.md. Failure kills the bridge; it never selects stub.
    """

    def __init__(
        self,
        viewer_url: str,
        *,
        node_modules: Path,
        timeout_s: float = 10,
        executable: str = "node",
        browser_executable: Path | None = None,
        software_gl: bool = True,
        service_run_id: str | None = None,
    ) -> None:
        if not isinstance(viewer_url, str) or not viewer_url:
            raise ValueError("renderer requires an explicit viewer URL")
        if type(software_gl) is not bool:
            raise TypeError("renderer.software_gl must be an explicit boolean")
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (float, int))
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 300
        ):
            raise ValueError("renderer timeout must be finite and in (0,300]")
        self.viewer_url, self.node_modules = viewer_url, node_modules.resolve()
        self.timeout_s = timeout_s
        self.executable = executable
        self.browser_executable = browser_executable_path(browser_executable)
        self.software_gl = software_gl
        self.service_run_id = service_run_id
        self.process: subprocess.Popen[bytes] | None = None
        self.buffer = bytearray()
        self.prepared: bytes | None = None

    def __enter__(self) -> BrowserRenderer:  # noqa: PYI034 - Python 3.10 runtime
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _start(self) -> subprocess.Popen[bytes]:
        if self.process is None:
            read_fd, write_fd = os.pipe()
            with os.fdopen(read_fd, "rb") as ready_channel:
                try:
                    self.process = subprocess.Popen(
                        [
                            self.executable,
                            str(Path(__file__).with_name("viewer_bridge.mjs")),
                            str(self.node_modules),
                            self.viewer_url,
                            str(self.browser_executable)
                            if self.browser_executable is not None
                            else "",
                            "software" if self.software_gl else "hardware",
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        pass_fds=(write_fd,),
                        env={**os.environ, "AEROAGENTSIM_RENDERER_READY_FD": str(write_fd)},
                        start_new_session=True,
                    )
                finally:
                    os.close(write_fd)
                try:
                    ready = self._read_reply(time.monotonic() + 30, "viewer startup deadline exceeded", stream=ready_channel)
                    if ready != {"contract": "aeroagentsim.viewer-ready/v1"}:
                        raise RuntimeError("viewer startup failed: " + str(ready))
                except (ValueError, TypeError, RuntimeError, TimeoutError, OSError):
                    self.close()
                    raise
        return self.process

    def _read_reply(self, deadline: float, timeout: str, *, stream: BinaryIO | None = None) -> dict[str, Any]:
        process = self.process
        assert process is not None
        channel = process.stdout if stream is None else stream
        assert channel is not None
        buffer = self.buffer if stream is None else bytearray()
        while b"\n" not in buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([channel], [], [], left)[0]:
                raise TimeoutError(timeout)
            chunk = os.read(channel.fileno(), 65536)
            if not chunk:
                raise RuntimeError("renderer bridge exited before supplying a reply")
            buffer.extend(chunk)
            if len(buffer) > 96 * 1024 * 1024:
                raise ValueError("renderer bridge response exceeds limit")
        line, _, remainder = buffer.partition(b"\n")
        if stream is None:
            self.buffer = bytearray(remainder)
        reply = json.loads(line)
        if not isinstance(reply, dict):
            raise TypeError("renderer bridge reply must be an object")
        return reply

    def render(
        self, request: CaptureRequest, *, label: str | None = None,
        scene: dict[str, Any] | None = None,
    ) -> RenderedFrame:
        wants_depth = _depth_requested(request)
        if wants_depth and (request.width * request.height > MAX_DEPTH_PIXELS or label):
            raise CaptureDepthError(request.request_id, "depth pixel budget exceeded or diagnostic overlay requested")
        process = self._start()
        assert process.stdin is not None and process.stdout is not None
        try:
            if scene is not None:
                preparation = {
                    "service_run_id": self.service_run_id,
                    "asset_digest": request.asset_digest,
                    "header": scene["header"],
                    "asset_manifest": scene["asset_manifest"],
                    "oblique": request.camera.get("revision") == "uav-gimbal-oblique/v1",
                }
                encoded = json.dumps(browser_encode({"operation": "prepare", "preparation": preparation})).encode() + b"\n"
                if encoded != self.prepared:
                    deadline = time.monotonic() + 30
                    self._send(process, encoded, deadline)
                    ready = self._read_reply(deadline, "viewer asset initialization deadline exceeded")
                    expected = {"contract": "aeroagentsim.viewer-assets-ready/v1", "service_run_id": self.service_run_id, "asset_digest": request.asset_digest}
                    if ready != expected:
                        raise RuntimeError("viewer asset initialization failed: " + str(ready))
                    self.prepared = encoded
            deadline = time.monotonic() + min(self.timeout_s, request.timeout_s)
            payload = (
                json.dumps(
                    {
                        "request": browser_encode(request.to_data()),
                        "label": label,
                        "service_run_id": self.service_run_id,
                        **({"scene": scene} if scene is not None else {}),
                    }
                ).encode()
                + b"\n"
            )
            self._send(process, payload, deadline)
            reply = self._read_reply(deadline, "viewer render deadline exceeded")
            if "error" in reply:
                raise RuntimeError("viewer render failed: " + str(reply["error"]))
            applied = browser_decode(reply["request"])
            matches = (_same_metadata(applied, request.to_data()) if wants_depth
                       else applied == request.to_data())
            if not matches:
                raise ValueError(
                    "viewer returned wrong actor, source cut, camera or assets"
                )
            png = base64.b64decode(reply["png_base64"], validate=True)
            validate_png(png, expected=(request.width, request.height))
            bundle = _validate_depth(request, reply) if wants_depth else (None, None, None)
            if time.monotonic() > deadline:
                raise TimeoutError("viewer render deadline exceeded")
            return RenderedFrame(png, "browser", *bundle)
        except (ValueError, TypeError, RuntimeError, TimeoutError, OSError, KeyError, ArithmeticError) as exc:
            self.close()
            if wants_depth and isinstance(exc, (ValueError, TypeError, RuntimeError, KeyError, ArithmeticError)) and not isinstance(exc, CaptureDepthError):
                raise CaptureDepthError(request.request_id, str(exc)) from exc
            raise

    def _send(self, process: subprocess.Popen[bytes], payload: bytes, deadline: float) -> None:
        assert process.stdin is not None
        os.set_blocking(process.stdin.fileno(), False)
        offset = 0
        while offset < len(payload):
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [process.stdin], [], left)[1]:
                raise TimeoutError("viewer request delivery deadline exceeded")
            try:
                offset += os.write(process.stdin.fileno(), payload[offset:])
            except BrokenPipeError:
                raise RuntimeError("renderer bridge exited: " + self._bridge_error(deadline)) from None

    def _bridge_error(self, deadline: float) -> str:
        """The bridge reports why it stopped on stdout before exiting."""
        process = self.process
        if process is None or process.stdout is None:
            return "no bridge process"
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([process.stdout], [], [], left)[0]:
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            self.buffer.extend(chunk)
        line = bytes(self.buffer).partition(b"\n")[0]
        try:
            return str(json.loads(line)["error"])
        except (ValueError, KeyError, TypeError):
            return "no error reported"

    def close(self) -> None:
        process, self.process = self.process, None
        self.buffer.clear()
        self.prepared = None
        if process is None:
            return
        # EOF lets the bridge leave readline and await browser.close(). Sending
        # SIGTERM to Playwright first keeps Node's input loop alive until the
        # forced-kill deadline, delaying every completed or stopped photo run.
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            import signal

            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # A hung render cannot finish the normal shutdown. Its entire
                # owned process group must exit before cleanup can succeed.
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
