"""Bounded, source-stamped native RGB-D buffers; never advances the world."""

import base64
import hashlib
import math
import struct
import threading
import time
import zlib
from collections import deque

from .configure_rgbd import optical_to_body_frd, sensor_topic


def stamp(header):
    if not header.HasField("stamp"):
        raise ValueError("image has no Gazebo acquisition timestamp")
    return header.stamp.sec * 1_000_000_000 + header.stamp.nsec


def png_rgb(raw, width, height, stride, bgr=False):
    rows = []
    for row in range(height):
        pixels = raw[row * stride : row * stride + width * 3]
        if bgr:
            pixels = b"".join(pixels[i : i + 3][::-1] for i in range(0, len(pixels), 3))
        rows.append(b"\0" + pixels)

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 3))
        + chunk(b"IEND", b"")
    )


class SensorStreams:
    def __init__(
        self, gazebo, telemetry, vehicles, run_id, clock_epoch, episode_origin_ns=0, *, camera
    ):
        self.gazebo, self.telemetry = gazebo, telemetry
        self.camera = camera
        self.vehicles = {v.id: v for v in vehicles}
        self.run_id, self.epoch = run_id, clock_epoch
        self.origin_ns = episode_origin_ns
        self.rgb = {v.id: deque(maxlen=4) for v in vehicles}
        self.depth = {v.id: deque(maxlen=4) for v in vehicles}
        self.info, self.completed, self.errors = {}, {}, {}
        self.error_history = deque(maxlen=256)
        self.lock = threading.Lock()
        self.active = True

    async def start(self):
        from gz.msgs10.camera_info_pb2 import CameraInfo
        from gz.msgs10.image_pb2 import Image

        for vehicle in self.vehicles:
            root = sensor_topic(self.camera, vehicle)
            await self.gazebo.subscribe(
                Image,
                root + "/image",
                lambda msg, v=vehicle: self.on_image(v, "rgb", msg),
            )
            await self.gazebo.subscribe(
                Image,
                root + "/depth_image",
                lambda msg, v=vehicle: self.on_image(v, "depth", msg),
            )
            await self.gazebo.subscribe(
                CameraInfo,
                root + "/camera_info",
                lambda msg, v=vehicle: self.on_info(v, msg),
            )

    def set_episode_origin(self, ns):
        self.origin_ns = ns
        self.completed.clear()

    def on_info(self, vehicle, message):
        if not self.active:
            return
        try:
            info = {
                "K": list(message.intrinsics.k),
                "width": message.width,
                "height": message.height,
                "distortion": list(message.distortion.k),
                "distortion_model": {
                    0: "plumb_bob",
                    1: "rational_polynomial",
                    2: "equidistant",
                }.get(message.distortion.model),
                "camera_info_source_ns": stamp(message.header),
            }
            with self.lock:
                self.info[vehicle] = info
                self.errors.pop((vehicle, "camera_info"), None)
        except Exception as error:
            with self.lock:
                self.errors[(vehicle, "camera_info")] = str(error)
                self.error_history.append({
                    "uav_id": vehicle, "stream": "camera_info", "reason": str(error),
                    "received_wall_time": time.time_ns(),
                })

    def on_image(self, vehicle, kind, message):
        if not self.active:
            return
        try:
            ns = stamp(message.header)
            width, height, stride = message.width, message.height, message.step
            raw = bytes(message.data)
            if len(raw) != height * stride:
                raise ValueError("native image stride/byte count mismatch")
            if kind == "rgb":
                if message.pixel_format_type not in (3, 8):
                    raise ValueError("RGB stream is not RGB_INT8/BGR_INT8")
                content = png_rgb(
                    raw, width, height, stride, message.pixel_format_type == 8
                )
                encoding = "png"
            else:
                if message.pixel_format_type != 13:
                    raise ValueError("depth stream is not R_FLOAT32")
                packed = b"".join(
                    raw[row * stride : row * stride + width * 4]
                    for row in range(height)
                )
                content = zlib.compress(packed, 3)
                encoding = "zlib-float32-le-metres"
            record = {
                "source_ns": ns,
                "width": width,
                "height": height,
                "content": content,
                "encoding": encoding,
                "received_wall_time": time.time_ns(),
            }
            with self.lock:
                (self.rgb if kind == "rgb" else self.depth)[vehicle].append(record)
                self.errors.pop((vehicle, kind), None)
        except Exception as error:
            with self.lock:
                self.errors[(vehicle, kind)] = str(error)
                self.error_history.append(
                    {
                        "uav_id": vehicle,
                        "stream": kind,
                        "reason": str(error),
                        "received_wall_time": time.time_ns(),
                    }
                )

    def poll(self, vehicle, after_sim_ns):
        if vehicle not in self.vehicles:
            raise ValueError("unknown sensor owner")
        with self.lock:
            errors = {
                kind: reason
                for (owner, kind), reason in self.errors.items()
                if owner == vehicle
            }
            if errors:
                return {"status": "unavailable", "reason": errors}
            rgb = [
                r
                for r in self.rgb[vehicle]
                if r["source_ns"] - self.origin_ns > after_sim_ns
            ]
            depth, info = list(self.depth[vehicle]), self.info.get(vehicle)
        if not rgb or not depth or not info:
            return {
                "status": "pending",
                "reason": "awaiting native RGB/depth/CameraInfo",
                "after_sim_ns": after_sim_ns,
            }
        image = rgb[-1]
        range_image = min(depth, key=lambda d: abs(d["source_ns"] - image["source_ns"]))
        if abs(range_image["source_ns"] - image["source_ns"]) > self.camera["max_alignment_ns"]:
            return {"status": "pending", "reason": "awaiting depth synchronized to RGB"}
        if (image["width"], image["height"]) != (
            range_image["width"],
            range_image["height"],
        ) or (image["width"], image["height"]) != (info["width"], info["height"]):
            return {
                "status": "unavailable",
                "reason": "RGB/depth/CameraInfo dimensions differ",
            }
        observed_at = image["source_ns"] - self.origin_ns
        now = self.gazebo.sim_ns - self.origin_ns
        estimate = self.telemetry.estimates.sample(
            vehicle, now, observed_at, max_age_ns=self.camera["max_alignment_ns"]
        )
        frame_id = f"{self.run_id}:{self.epoch}:{vehicle}:rgbd:{image['source_ns']}"
        k = info["K"]
        if len(k) != 9 or k[0] <= 0 or k[4] <= 0:
            return {
                "status": "unavailable",
                "reason": "actual CameraInfo has no usable focal lengths",
            }
        calibration = {
            **info,
            "orientation": self.camera.get("orientation", "configured"),
            "horizontal_fov_deg": math.degrees(
                math.atan(k[2] / k[0]) + math.atan((info["width"] - k[2]) / k[0])
            ),
            "vertical_fov_deg": math.degrees(
                math.atan(k[5] / k[4]) + math.atan((info["height"] - k[5]) / k[4])
            ),
            "fov_source": "actual Gazebo CameraInfo K and image dimensions",
            "registered_rgb_depth": True,
            "version": f"{self.epoch}:{self.camera['sensor_id']}",
            "depth_convention": "optical_z",
            "depth_min_m": self.camera["near_m"],
            "depth_max_m": self.camera["far_m"],
            "invalid_depth_rule": "nonfinite or outside configured near/far range",
            "T_body_frd_from_camera_optical": optical_to_body_frd(self.camera["pose_flu"]),
            "extrinsic_source": "configured RGBD SDF pose relative to base_link FLU; converted to FRD/optical",

        }
        metadata = {
            "run_id": self.run_id,
            "uav_id": vehicle,
            "sensor_id": self.camera["sensor_id"],
            "frame_id": frame_id,
            "source": "gazebo_transport",
            "source_clock": "gazebo_sim",
            "source_time": image["source_ns"],
            "clock_epoch": self.epoch,
            "observed_at": observed_at,
            "depth_observed_at": range_image["source_ns"] - self.origin_ns,
            "available_sim_ns": now,
            "received_wall_time": image["received_wall_time"],
            "coordinate_frame": "camera_optical",
            "calibration": calibration,
            "vehicle_estimate": estimate,
            "localization_status": estimate["status"],
            "localization_reason": estimate.get("reason"),
            "rgb_sha256": hashlib.sha256(image["content"]).hexdigest(),
        }
        blobs = {"rgb": image["content"], "depth": range_image["content"]}
        self.completed[(vehicle, frame_id)] = (metadata, blobs)
        # Bound memory independently of the consumer persistence.
        owned = [key for key in self.completed if key[0] == vehicle]
        for key in owned[:-4]:
            del self.completed[key]
        return {
            "status": "succeeded",
            "observation": metadata,
            "blobs": {
                kind: {
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "encoding": "png" if kind == "rgb" else "zlib-float32-le-metres",
                }
                for kind, content in blobs.items()
            },
        }

    def read_chunk(self, vehicle, frame_id, kind, offset, max_bytes=262144):
        if kind not in ("rgb", "depth") or offset < 0 or not 1 <= max_bytes <= 262144:
            raise ValueError("invalid sensor blob request")
        entry = self.completed.get((vehicle, frame_id))
        if entry is None:
            return {
                "status": "unavailable",
                "reason": "frame absent or expired from this UAV sensor buffer",
            }
        content = entry[1][kind]
        chunk = content[offset : offset + max_bytes]
        return {
            "status": "succeeded",
            "frame_id": frame_id,
            "kind": kind,
            "offset": offset,
            "total_bytes": len(content),
            "data_base64": base64.b64encode(chunk).decode(),
            "eof": offset + len(chunk) >= len(content),
        }

    async def stop(self):
        self.active = False
        self.completed.clear()
