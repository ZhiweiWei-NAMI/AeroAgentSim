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
    uncertainty_ns: int = 0

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
            },
        )
        return observation
    observation["localization_reason"] = "no PX4 pose within 40 ms"
    return observation


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
