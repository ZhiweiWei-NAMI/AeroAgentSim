"""Bounded, source-stamped Gazebo geometric RGB-D capture and chunk RPCs.

Only poll/read_chunk results are public state exchange. The internal buffers
exist solely to assemble synchronized frames and serve bounded blob reads.
No native dependency is imported until start(), and no method steps physics.
"""

import base64
import hashlib
import json
import math
import struct
import sys
import threading
import time
import zlib
from array import array
from collections import deque

from .configure_rgbd import optical_to_body_frd, sensor_frame, sensor_topic

MAX_PIXELS = 1_048_576
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_BUFFER_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 4
MAX_CHUNK_BYTES = 262_144
ESTIMATE_ALIGNMENT_NS = 40_000_000


def stamp(header):
    if not header.HasField("stamp"):
        raise ValueError("sensor has no Gazebo acquisition timestamp")
    sec, nsec = header.stamp.sec, header.stamp.nsec
    if (type(sec) is not int or type(nsec) is not int or sec < 0
            or not 0 <= nsec < 1_000_000_000):
        raise ValueError("invalid Gazebo acquisition timestamp")
    ns = sec * 1_000_000_000 + nsec
    if ns > 2**63 - 1:
        raise ValueError("Gazebo acquisition timestamp exceeds int64")
    return ns


def dimensions(width, height):
    if (type(width) is not int or type(height) is not int
            or not 1 <= width <= 2048 or not 1 <= height <= 2048
            or width * height > MAX_PIXELS):
        raise ValueError("sensor dimensions exceed the configured pixel bounds")
    return width, height


def image_layout(raw, width, height, stride, channels):
    dimensions(width, height)
    if (type(stride) is not int or stride < width * channels
            or height * stride > MAX_INPUT_BYTES or len(raw) != height * stride):
        raise ValueError("native image stride/byte count mismatch or oversized input")


def png_rgb(raw, width, height, stride, bgr=False):
    """Encode validated RGB/BGR rows, ignoring only declared row padding."""
    image_layout(raw, width, height, stride, 3)
    if type(bgr) is not bool:
        raise ValueError("BGR conversion flag must be bool")
    rows = []
    for row in range(height):
        pixels = bytes(raw[row * stride:row * stride + width * 3])
        if bgr:
            pixels = b"".join(pixels[i:i + 3][::-1] for i in range(0, len(pixels), 3))
        rows.append(b"\0" + pixels)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 3))
            + chunk(b"IEND", b""))


def normalized_calibration(value):
    """Keep producer hashes stable across strict wire signed-zero normalization."""
    if type(value) is float and value == 0:
        return 0.0
    if isinstance(value, list):
        return [normalized_calibration(item) for item in value]
    if isinstance(value, dict):
        return {key: normalized_calibration(item) for key, item in value.items()}
    return value


def native_frame(header, expected):
    if len(header.data) > 16:
        raise ValueError("oversized sensor header")
    identities = [entry for entry in header.data if entry.key == "frame_id"]
    if (len(identities) != 1 or len(identities[0].value) != 1
            or identities[0].value[0] != expected):
        raise ValueError("missing or mismatched configured native sensor frame_id")
    return expected


class SensorStreams:
    def __init__(
        self, gazebo, telemetry, vehicles, run_id, clock_epoch, episode_origin_ns=0, *, camera
    ):
        self.gazebo, self.telemetry = gazebo, telemetry
        self.camera = dict(camera)
        self.camera["pose_flu"] = tuple(camera["pose_flu"])
        dimensions(camera["width"], camera["height"])
        self.vehicles = {vehicle.id: vehicle for vehicle in vehicles}
        self.run_id, self.epoch = run_id, clock_epoch
        self.origin_ns = 0
        self.rgb = {vehicle: deque(maxlen=MAX_RECORDS) for vehicle in self.vehicles}
        self.depth = {vehicle: deque(maxlen=MAX_RECORDS) for vehicle in self.vehicles}
        self.info = {vehicle: deque(maxlen=MAX_RECORDS) for vehicle in self.vehicles}
        self.completed, self.errors, self.failures, self.last_source = {}, {}, {}, {}
        self.lock = threading.Lock()
        self.last_native_ns = None
        self.sequence = 0
        self.active = True
        # Two nominal camera periods, with an explicit ten-second hard ceiling.
        self.max_frame_age_ns = max(
            250_000_000, int(min(10_000_000_000, 2_000_000_000 / camera["update_rate_hz"]))
        )
        self.set_episode_origin(episode_origin_ns)

    async def start(self):
        from gz.msgs10.camera_info_pb2 import CameraInfo
        from gz.msgs10.image_pb2 import Image

        for vehicle in self.vehicles:
            root = sensor_topic(self.camera, vehicle)
            await self.gazebo.subscribe(
                Image, root + "/image", lambda msg, v=vehicle: self.on_image(v, "rgb", msg)
            )
            await self.gazebo.subscribe(
                Image, root + "/depth_image",
                lambda msg, v=vehicle: self.on_image(v, "depth", msg),
            )
            await self.gazebo.subscribe(
                CameraInfo, root + "/camera_info", lambda msg, v=vehicle: self.on_info(v, msg)
            )

    def set_episode_origin(self, ns):
        if type(ns) is not int or not 0 <= ns <= 2**63 - 1:
            raise ValueError("episode origin must be nonnegative int64 nanoseconds")
        with self.lock:
            if ns < self.origin_ns:
                raise ValueError("a regressed episode clock requires a new sensor epoch")
            self.origin_ns = ns
            for records in (*self.rgb.values(), *self.depth.values(), *self.info.values()):
                records.clear()
            self.completed.clear()
            self.errors.clear()

    def _owner(self, vehicle):
        if not isinstance(vehicle, str) or vehicle not in self.vehicles:
            raise ValueError("unknown sensor owner")
        return self.vehicles[vehicle]

    def _header(self, vehicle, message):
        owner = self._owner(vehicle)
        ns = stamp(message.header)
        with self.lock:
            if ns < self.origin_ns:
                return None, None  # Discard late warmup frames before interpreting geometry.
        identity = native_frame(message.header, sensor_frame(self.camera, owner))
        return ns, identity

    def _invalidate_locked(self, vehicle, reason):
        self.failures.setdefault(vehicle, reason)
        for buffers in (self.rgb, self.depth, self.info):
            buffers[vehicle].clear()
        for key in [key for key in self.completed if key[0] == vehicle]:
            del self.completed[key]

    def _frontier_locked(self):
        now = self.gazebo.sim_ns
        if type(now) is not int or not 0 <= now <= 2**63 - 1:
            return None, "native Gazebo frontier unavailable"
        if now < self.origin_ns or (self.last_native_ns is not None and now < self.last_native_ns):
            reason = "native Gazebo source clock regressed; new sensor epoch required"
            for vehicle in self.vehicles:
                self._invalidate_locked(vehicle, reason)
            return None, reason
        self.last_native_ns = now
        return now, None

    def _stream_error_locked(self, vehicle, now):
        if vehicle in self.failures:
            return self.failures[vehicle]
        errors = [f"{kind}: {reason}" for (owner, kind), reason in self.errors.items()
                  if owner == vehicle]
        if errors:
            return "; ".join(errors)
        if any(ns > now for (owner, _), ns in self.last_source.items() if owner == vehicle):
            return "sensor acquisition timestamp is in the future"
        return None

    def _error(self, vehicle, kind, error):
        with self.lock:
            if self.active:
                self.errors[(vehicle, kind)] = str(error)[:512]

    def _store(self, vehicle, kind, record):
        with self.lock:
            if not self.active or vehicle in self.failures or record["source_ns"] < self.origin_ns:
                return
            key = vehicle, kind
            previous = self.last_source.get(key)
            if previous is not None and record["source_ns"] < previous:
                self._invalidate_locked(vehicle, "sensor source timestamp reordered or clock reset; new epoch required")
                return
            records = {"rgb": self.rgb, "depth": self.depth, "camera_info": self.info}[kind][vehicle]
            if records and record["source_ns"] == records[-1]["source_ns"]:
                if record["fingerprint"] != records[-1]["fingerprint"]:
                    self._invalidate_locked(vehicle, "conflicting sensor content at one acquisition timestamp")
                    return
                self.errors.pop(key, None)
                return
            self.sequence += 1
            record["sequence"] = self.sequence
            records.append(record)
            self.last_source[key] = record["source_ns"]
            self.errors.pop(key, None)
            self._trim_locked()

    def _trim_locked(self):
        def used_bytes():
            return (sum(len(record["content"]) for buffers in (self.rgb, self.depth)
                        for records in buffers.values() for record in records)
                    + sum(len(content) for _, blobs in self.completed.values()
                          for content in blobs.values()))

        # Counts shared immutable bytes conservatively twice when also retained
        # for a chunk read. Both stream queues and completed reads share this cap.
        while used_bytes() > MAX_BUFFER_BYTES:
            if self.completed:
                del self.completed[next(iter(self.completed))]
                continue
            candidates = [records for buffers in (self.rgb, self.depth)
                          for records in buffers.values() if records]
            if not candidates:
                break
            min(candidates, key=lambda records: records[0]["sequence"]).popleft()

    def on_info(self, vehicle, message):
        self._owner(vehicle)
        if not self.active:
            return
        try:
            ns, identity = self._header(vehicle, message)
            with self.lock:
                if ns is None or ns < self.origin_ns:
                    return
            width, height = dimensions(message.width, message.height)
            if (width, height) != (self.camera["width"], self.camera["height"]):
                raise ValueError("CameraInfo dimensions differ from configured RGBD dimensions")
            k, distortion = list(message.intrinsics.k), list(message.distortion.k)
            if (len(k) != 9 or len(distortion) not in (0, 4, 5, 8, 12, 14)
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           for value in k + distortion)):
                raise ValueError("CameraInfo has invalid finite calibration arrays")
            if (k[0] <= 0 or k[4] <= 0 or not 0 <= k[2] < width or not 0 <= k[5] < height
                    or any(abs(k[i]) > 1e-9 for i in (1, 3, 6, 7)) or abs(k[8] - 1) > 1e-9):
                raise ValueError("CameraInfo is not a usable pinhole calibration")
            if type(message.distortion.model) is not int:
                raise ValueError("CameraInfo distortion model must be an enum integer")
            model = {0: "plumb_bob", 1: "rational_polynomial", 2: "equidistant"}.get(
                message.distortion.model
            )
            if model is None:
                raise ValueError("unknown CameraInfo distortion model")
            info = {
                "K": k, "width": width, "height": height, "distortion": distortion,
                "distortion_model": model, "camera_info_source_ns": ns,
                "native_frame_id": identity,
                "source_topic": sensor_topic(self.camera, vehicle) + "/camera_info",
            }
            self._store(vehicle, "camera_info", {
                "source_ns": ns, "calibration": info,
                "fingerprint": json.dumps({key: value for key, value in info.items()
                                           if key != "camera_info_source_ns"}, sort_keys=True),
            })
        except Exception as error:
            self._error(vehicle, "camera_info", error)

    def on_image(self, vehicle, kind, message):
        self._owner(vehicle)
        if kind not in ("rgb", "depth"):
            raise ValueError("unknown sensor image kind")
        if not self.active:
            return
        try:
            ns, identity = self._header(vehicle, message)
            with self.lock:
                if ns is None or ns < self.origin_ns:
                    return
            width, height, stride = message.width, message.height, message.step
            dimensions(width, height)
            if (width, height) != (self.camera["width"], self.camera["height"]):
                raise ValueError("image dimensions differ from configured RGBD dimensions")
            image_layout(message.data, width, height, stride, 3 if kind == "rgb" else 4)
            if type(message.pixel_format_type) is not int:
                raise ValueError("image pixel format must be an enum integer")
            raw = bytes(message.data)
            if kind == "rgb":
                if message.pixel_format_type not in (3, 8):
                    raise ValueError("RGB stream is not RGB_INT8/BGR_INT8")
                content = png_rgb(raw, width, height, stride, message.pixel_format_type == 8)
                encoding = "png"
            else:
                if message.pixel_format_type != 13:
                    raise ValueError("depth stream is not R_FLOAT32")
                packed = b"".join(raw[row * stride:row * stride + width * 4]
                                  for row in range(height))
                if sys.byteorder != "little":
                    values = array("f")
                    values.frombytes(packed)
                    values.byteswap()
                    packed = values.tobytes()
                content = zlib.compress(packed, 3)
                encoding = "zlib-float32-le-metres"
            self._store(vehicle, kind, {
                "source_ns": ns, "width": width, "height": height,
                "content": content, "encoding": encoding, "native_frame_id": identity,
                "received_wall_time": time.time_ns(),
                "fingerprint": hashlib.sha256(content).hexdigest(),
            })
        except Exception as error:
            self._error(vehicle, kind, error)

    @staticmethod
    def _result(status, reason):
        return {"status": status, "reason": str(reason)}

    def poll(self, vehicle, after_sim_ns=-1):
        self._owner(vehicle)
        if type(after_sim_ns) is not int or not -1 <= after_sim_ns <= 2**63 - 1:
            raise ValueError("after_sim_ns must be an integer >= -1")
        with self.lock:
            if not self.active:
                return self._result("unavailable", "sensor streams stopped")
            now_native, error = self._frontier_locked()
            if error is not None:
                return self._result("unavailable", error)
            error = self._stream_error_locked(vehicle, now_native)
            if error is not None:
                return self._result("unavailable", error)
            origin = self.origin_ns
            rgb, depth, infos = (list(buffers[vehicle])
                                 for buffers in (self.rgb, self.depth, self.info))
        rgb = [record for record in rgb
               if origin <= record["source_ns"] <= now_native
               and record["source_ns"] - origin > after_sim_ns]
        if not rgb or not depth or not infos:
            return self._result("pending", "awaiting episode-native RGB/depth/CameraInfo")
        # Do not quietly fall back to an older pair if the latest RGB is incomplete.
        image = rgb[-1]
        matches = [record for record in depth if record["source_ns"] == image["source_ns"]]
        if not matches:
            return self._result("pending", "awaiting depth at the exact RGB acquisition timestamp")
        range_image = matches[-1]
        infos = [record for record in infos if origin <= record["source_ns"] <= image["source_ns"]]
        if not infos:
            return self._result("pending", "awaiting causal episode CameraInfo for RGBD")
        info = infos[-1]["calibration"]
        if (image["width"], image["height"]) != (range_image["width"], range_image["height"]) or (
                image["width"], image["height"]) != (info["width"], info["height"]):
            return self._result("unavailable", "RGB/depth/CameraInfo dimensions differ")
        if image["native_frame_id"] != range_image["native_frame_id"] or (
                image["native_frame_id"] != info["native_frame_id"]):
            return self._result("unavailable", "RGB/depth/CameraInfo native frame identities differ")
        if now_native - image["source_ns"] > self.max_frame_age_ns:
            return self._result("unavailable", "latest native RGBD acquisition is stale")
        now, observed_at = now_native - origin, image["source_ns"] - origin
        estimate = self.telemetry.estimates.sample(
            vehicle, now, observed_at, max_age_ns=ESTIMATE_ALIGNMENT_NS
        )
        k = info["K"]
        calibration = {
            **info,
            "horizontal_fov_deg": math.degrees(
                math.atan(k[2] / k[0]) + math.atan((info["width"] - k[2]) / k[0])
            ),
            "vertical_fov_deg": math.degrees(
                math.atan(k[5] / k[4]) + math.atan((info["height"] - k[5]) / k[4])
            ),
            "fov_source": "actual Gazebo CameraInfo K and image dimensions",
            "registered_rgb_depth": True,
            "version": "",  # Set from the calibration content, not a side cache.
            "depth_convention": "optical_z",
            "depth_min_m": self.camera["near_m"], "depth_max_m": self.camera["far_m"],
            "invalid_depth_rule": "nonfinite or outside configured near/far range",
            "T_body_frd_from_camera_optical": optical_to_body_frd(self.camera["pose_flu"]),
            "extrinsic_source": "configured RGBD SDF pose relative to base_link FLU; converted to FRD/optical",
        }
        calibration = normalized_calibration(calibration)
        version_content = {key: value for key, value in calibration.items()
                           if key not in ("version", "camera_info_source_ns")}
        calibration["version"] = f"{self.epoch}:{self.camera['sensor_id']}:" + hashlib.sha256(
            json.dumps(version_content, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        frame_id = f"{self.run_id}:{self.epoch}:{vehicle}:rgbd:{image['source_ns']}"
        metadata = {
            "run_id": self.run_id, "uav_id": vehicle, "sensor_id": self.camera["sensor_id"],
            "frame_id": frame_id, "source": "gazebo_transport", "source_clock": "gazebo_sim",
            "source_time": image["source_ns"], "clock_epoch": self.epoch,
            "observed_at": observed_at, "depth_observed_at": range_image["source_ns"] - origin,
            "available_sim_ns": now, "received_wall_time": image["received_wall_time"],
            "source_topic": sensor_topic(self.camera, vehicle) + "/image",
            "native_frame_id": image["native_frame_id"], "coordinate_frame": "camera_optical",
            "calibration": calibration, "vehicle_estimate": estimate,
            "localization_status": estimate["status"], "localization_reason": estimate.get("reason"),
            "rgb_sha256": image["fingerprint"],
            "sensing_model": "gazebo_geometric_rgbd", "physical_ranging": False,
        }
        blobs = {"rgb": image["content"], "depth": range_image["content"]}
        with self.lock:
            current, error = self._frontier_locked()
            if error is not None:
                return self._result("unavailable", error)
            if not self.active or self.origin_ns != origin or current != now_native:
                return self._result("unavailable", "capture frontier changed while assembling frame")
            error = self._stream_error_locked(vehicle, current)
            if error is not None:
                return self._result("unavailable", error)
            self.completed[(vehicle, frame_id)] = (metadata, blobs)
            owned = [key for key in self.completed if key[0] == vehicle]
            for key in owned[:-MAX_RECORDS]:
                del self.completed[key]
            self._trim_locked()
            if (vehicle, frame_id) not in self.completed:
                return self._result("unavailable", "capture exceeds the shared frame buffer budget")
        return {
            "status": "succeeded", "observation": metadata,
            "blobs": {kind: {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                             "encoding": "png" if kind == "rgb" else "zlib-float32-le-metres"}
                      for kind, content in blobs.items()},
        }

    def read_chunk(self, vehicle, frame_id, kind, offset, max_bytes=MAX_CHUNK_BYTES):
        self._owner(vehicle)
        if (not isinstance(frame_id, str) or not frame_id or len(frame_id) > 512
                or kind not in ("rgb", "depth") or type(offset) is not int or offset < 0
                or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_CHUNK_BYTES):
            raise ValueError("invalid sensor blob request")
        with self.lock:
            if not self.active:
                return self._result("unavailable", "sensor streams stopped")
            now, error = self._frontier_locked()
            if error is not None:
                return self._result("unavailable", error)
            error = self._stream_error_locked(vehicle, now)
            if error is not None:
                return self._result("unavailable", error)
            entry = self.completed.get((vehicle, frame_id))
            if entry is None:
                return self._result("unavailable", "frame absent or expired from this UAV sensor buffer")
            metadata, blobs = entry
            if (metadata["run_id"], metadata["clock_epoch"], metadata["uav_id"]) != (
                    self.run_id, self.epoch, vehicle):
                return self._result("unavailable", "frame identity does not match the active sensor epoch")
            if not self.origin_ns <= metadata["source_time"] <= now:
                return self._result("unavailable", "frame acquisition is outside the active episode frontier")
            content = blobs[kind]
            if offset > len(content):
                raise ValueError("sensor blob offset exceeds total bytes")
            chunk = content[offset:offset + max_bytes]
        return {
            "status": "succeeded", "vehicle": vehicle, "frame_id": frame_id, "kind": kind,
            "offset": offset, "total_bytes": len(content),
            "data_base64": base64.b64encode(chunk).decode("ascii"),
            "eof": offset + len(chunk) >= len(content),
        }

    async def stop(self):
        with self.lock:
            self.active = False
            for records in (*self.rgb.values(), *self.depth.values(), *self.info.values()):
                records.clear()
            self.completed.clear()
            self.errors.clear()
            self.last_source.clear()
            self.failures.clear()
