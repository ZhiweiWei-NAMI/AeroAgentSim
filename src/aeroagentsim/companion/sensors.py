"""Source-time joining and local ingestion of native, registered RGB-D frames."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ClockMap:
    source_clock: str
    clock_epoch: str
    source_anchor_ns: int
    sim_anchor_ns: int
    method: str
    uncertainty_ns: int | None = None

    def to_sim(self, source_ns: int, epoch: str) -> int:
        if epoch != self.clock_epoch:
            raise ValueError("source sample belongs to a previous clock epoch")
        return self.sim_anchor_ns + source_ns - self.source_anchor_ns


def join_observation(
    metadata: dict[str, Any],
    poses: list[dict[str, Any]],
    available_sim_ns: int,
    max_delta_ns: int = 40_000_000,
) -> dict[str, Any]:
    observation = dict(metadata)
    observation["available_sim_ns"] = available_sim_ns
    observation["localization_status"] = "unavailable"
    timestamp = metadata.get("observed_at")
    depth_ns = metadata.get("depth_observed_at")
    if timestamp is None or depth_ns is None:
        observation["localization_reason"] = (
            "sensor source clock has no run-time mapping"
        )
        return observation
    if timestamp > available_sim_ns or depth_ns > available_sim_ns:
        observation["localization_reason"] = (
            "sensor sample is not available at this simulation boundary"
        )
        return observation
    if abs(timestamp - depth_ns) > max_delta_ns:
        observation["localization_reason"] = "RGB/depth source times exceed 40 ms"
        return observation
    usable = [
        p
        for p in poses
        if p.get("status") == "available"
        and p.get("source_sim_ns") is not None
        and p.get("clock_epoch") == metadata["clock_epoch"]
        and p.get("available_sim_ns", available_sim_ns) <= available_sim_ns
    ]
    usable.sort(key=lambda p: p["source_sim_ns"])
    if not usable:
        observation["localization_reason"] = (
            "PX4 source-time pose unavailable for this epoch"
        )
        return observation
    closest = min(usable, key=lambda p: abs(p["source_sim_ns"] - timestamp))
    if abs(closest["source_sim_ns"] - timestamp) <= max_delta_ns:
        observation.update(
            vehicle_estimate=closest,
            localization_status="available",
            pose_alignment={
                "method": "nearest",
                "sample_times_ns": [closest["source_sim_ns"]],
                "delta_ns": abs(closest["source_sim_ns"] - timestamp),
                "clock_mapping_uncertainty_ns": closest.get("clock_map", {}).get("uncertainty_ns"),
                "bound_status": "mapped source delta only; clock residual not established",
            },
        )
        return observation
    observation["localization_reason"] = "no PX4 pose within 40 ms"
    return observation


def observed_surface_extent(
    observation: dict[str, Any], depth: np.ndarray
) -> dict[str, Any]:
    """Sparse actual depth surfaces, with a candidate envelope for history search.

    The convex envelope is not an assertion that occluded gaps were observed.
    Coverage accounting must use the sampled surfaces, not fill this polygon.
    """
    from .geometry import backproject, quaternion_matrix

    if observation.get("localization_status") != "available":
        return {"footprint_status": "unavailable: synchronized estimate absent"}
    calibration, estimate = observation["calibration"], observation["vehicle_estimate"]
    extrinsic = np.asarray(calibration["T_body_frd_from_camera_optical"]).reshape(4, 4)
    rotation = quaternion_matrix(estimate["attitude_body_frd_to_world_enu_xyzw"])
    origin = np.asarray(estimate["position_enu_m"])
    minimum, maximum = calibration.get("depth_min_m", .2), calibration.get("depth_max_m", 60.)
    samples = []
    for y in np.linspace(0, depth.shape[0] - 1, 13, dtype=int):
        for x in np.linspace(0, depth.shape[1] - 1, 17, dtype=int):
            value = float(depth[y, x])
            if np.isfinite(value) and minimum <= value <= maximum:
                camera = backproject([int(x), int(y)], value, calibration)
                world = rotation @ (extrinsic @ np.append(camera, 1))[:3] + origin
                samples.append(world.tolist())
    points = sorted(set((p[0], p[1]) for p in samples))
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lower, upper = [], []
    for sequence, half in ((points, lower), (reversed(points), upper)):
        for point in sequence:
            while len(half) >= 2 and cross(half[-2], half[-1], point) <= 0:
                half.pop()
            half.append(point)
    hull = lower[:-1] + upper[:-1] if len(points) >= 3 else []
    return {
        "visible_surface_samples_enu_m": samples,
        "visible_surface_sampling": "17x13 actual valid depth pixels; no occluded-area fill",
        "footprint_enu": [list(p) for p in hull],
        "footprint_status": "candidate_extent" if hull else "unavailable: insufficient depth",
        "footprint_method": "convex envelope of sparse actual depth surfaces; spatial candidate filter only",
    }


def ingest_frame(
    store: Any,
    metadata: dict[str, Any],
    rgb_png: bytes,
    depth: np.ndarray,
    poses: list[dict[str, Any]],
    available_sim_ns: int,
) -> dict[str, Any]:
    from PIL import Image

    with Image.open(io.BytesIO(rgb_png)) as image:
        image.verify()
    if depth.dtype != np.float32 or depth.ndim != 2:
        raise ValueError("native depth must be a two-dimensional float32 metre array")
    observation = join_observation(metadata, poses, available_sim_ns)
    observation.update(observed_surface_extent(observation, depth))
    calibration = metadata.get("calibration", {})
    invalid = (~np.isfinite(depth) | (depth < calibration.get("depth_min_m", .2))
               | (depth > calibration.get("depth_max_m", 60.)))
    mask_stream = io.BytesIO()
    np.save(mask_stream, invalid, allow_pickle=False)
    mask = store.save_artifact(mask_stream.getvalue(), ".npy", {
        "frame_id": observation["frame_id"], "kind": "invalid_depth_mask",
        "rule": "nonfinite or outside native camera near/far range", "true_means": "invalid",
    })
    observation["depth_invalid_mask_artifact_id"] = mask["artifact_id"]
    observation["depth_valid_fraction"] = float((~invalid).mean())
    stream = io.BytesIO()
    np.save(stream, depth, allow_pickle=False)
    return dict(store.record(observation, rgb_png, stream.getvalue()))


class CaptureDownload:
    """File-backed capture chunks; each next request yields to the coordinator."""

    def __init__(self, root: Any, result: dict[str, Any]):
        import hashlib
        import json
        from pathlib import Path

        self.metadata, self.blobs = result["observation"], result["blobs"]
        key = hashlib.sha256(self.metadata["frame_id"].encode()).hexdigest()
        self.directory = Path(root) / "observations" / "downloads" / key
        self.directory.mkdir(parents=True, exist_ok=True)
        manifest = self.directory / "capture.json"
        if not manifest.exists():
            manifest.write_text(json.dumps(result))

    def next_request(self) -> dict[str, Any] | None:
        for kind, descriptor in self.blobs.items():
            path = self.directory / (kind + ".part")
            offset = path.stat().st_size if path.exists() else 0
            if offset < descriptor["bytes"]:
                return {
                    "op": "capture_read",
                    "frame_id": self.metadata["frame_id"],
                    "kind": kind,
                    "offset": offset,
                    "max_bytes": 262144,
                }
        return None

    def accept(self, result: dict[str, Any]) -> None:
        import base64

        if (
            result.get("status") != "succeeded"
            or result.get("frame_id") != self.metadata["frame_id"]
        ):
            raise ValueError("sensor chunk did not belong to this capture")
        kind = result["kind"]
        if kind not in self.blobs:
            raise ValueError("unknown sensor blob")
        path = self.directory / (kind + ".part")
        offset = path.stat().st_size if path.exists() else 0
        if (
            result["offset"] != offset
            or result["total_bytes"] != self.blobs[kind]["bytes"]
        ):
            raise ValueError("sensor chunk sequence or total length changed")
        chunk = base64.b64decode(result["data_base64"], validate=True)
        if not chunk or offset + len(chunk) > result["total_bytes"]:
            raise ValueError("empty or oversized sensor chunk")
        with path.open("ab") as handle:
            handle.write(chunk)

    def finish(self, store: Any) -> dict[str, Any]:
        import hashlib
        import zlib

        if self.next_request() is not None:
            raise ValueError("capture still has missing chunks")
        content = {}
        for kind, descriptor in self.blobs.items():
            content[kind] = (self.directory / (kind + ".part")).read_bytes()
            if hashlib.sha256(content[kind]).hexdigest() != descriptor["sha256"]:
                raise ValueError("sensor blob hash mismatch")
        calibration = self.metadata["calibration"]
        expected = calibration["width"] * calibration["height"] * 4
        decoder = zlib.decompressobj()
        packed = decoder.decompress(content["depth"], expected + 1)
        if len(packed) != expected or not decoder.eof:
            raise ValueError("depth byte count differs from actual camera dimensions")
        depth = (
            np.frombuffer(packed, dtype="<f4")
            .reshape(calibration["height"], calibration["width"])
            .copy()
        )
        return ingest_frame(
            store,
            self.metadata,
            content["rgb"],
            depth,
            [self.metadata["vehicle_estimate"]],
            self.metadata["available_sim_ns"],
        )


def download_capture(
    store: Any, request_callable: Any, result: dict[str, Any]
) -> dict[str, Any]:
    """Download an already-ready frame from an independent companion worker.

    request_callable resolves one queued management response. The caller must
    never run this loop on the sole coordinator/world-control thread.
    """
    download = CaptureDownload(store.root, result)
    while (request := download.next_request()) is not None:
        download.accept(request_callable(**request))
    return download.finish(store)
