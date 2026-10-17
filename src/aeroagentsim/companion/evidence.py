"""Bounded real image evidence and shared perception tool implementations."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .geometry import compare_observations, locate_target, verify_target
from .history import LocalBGE, ObservationStore
from .vision import PersonTracker, RtdetrDetector


def package_evidence(
    store: ObservationStore,
    artifact_id: str,
    bbox_xyxy: list[float] | None,
    budget_bytes: int,
) -> dict[str, Any]:
    from PIL import Image

    original, metadata = store.read(artifact_id)
    with Image.open(io.BytesIO(original)) as image:
        rgb = image.convert("RGB")
    crop = bbox_xyxy or [0, 0, rgb.width, rgb.height]
    x1, y1, x2, y2 = map(int, crop)
    if not 0 <= x1 < x2 <= rgb.width or not 0 <= y1 < y2 <= rgb.height:
        return {"status": "unavailable", "reason": "crop lies outside original RGB"}
    cropped = rgb.crop((x1, y1, x2, y2))
    for scale in (1.0, 0.75, 0.5):
        width, height = (
            max(1, int(cropped.width * scale)),
            max(1, int(cropped.height * scale)),
        )
        resized = cropped.resize((width, height), Image.Resampling.LANCZOS)
        for quality in (90, 80, 70, 60, 50):
            encoded = io.BytesIO()
            resized.save(encoded, format="JPEG", quality=quality, optimize=True)
            content = encoded.getvalue()
            if len(content) <= budget_bytes:
                manifest = {
                    "source_artifact_id": artifact_id,
                    "source_frame_id": metadata.get("frame_id"),
                    "crop_xyxy": [x1, y1, x2, y2],
                    "width": width,
                    "height": height,
                    "quality": quality,
                    "scale": scale,
                    "budget_bytes": budget_bytes,
                }
                saved = store.save_artifact(content, ".jpg", manifest)
                return {"status": "succeeded", **saved, "manifest": manifest}
    return {
        "status": "unavailable",
        "reason": "cannot fit byte budget within minimum JPEG quality 50 and half-size",
        "budget_bytes": budget_bytes,
    }


class PerceptionTools:
    def __init__(
        self,
        root: str | Path,
        run_id: str,
        uav_id: str,
        model_path: str | Path | None = None,
        embedding_path: str | Path | None = None,
    ):
        self.store = ObservationStore(
            root, run_id, uav_id, LocalBGE(embedding_path) if embedding_path else None
        )
        self.model_path = model_path
        self._detector: RtdetrDetector | None = None
        self._tracker: PersonTracker | None = None

    @property
    def detector(self) -> RtdetrDetector:
        if self.model_path is None:
            raise FileNotFoundError(
                "RT-DETRv2 model is not installed; detection unavailable"
            )
        if self._detector is None:
            self._detector = RtdetrDetector(self.model_path)
        return self._detector

    def detect(self, request: dict[str, Any]) -> dict[str, Any]:
        observation = self.store.observation(request["frame_id"])
        result = self.detector.detect(
            self.store.artifact_path(observation["rgb_artifact_id"]),
            observation["frame_id"],
            self.store.root / "observations",
        )
        result["observed_at"] = observation["observed_at"]
        result["analyzed_at"] = request["analyzed_at"]
        self.store.add_analysis(observation["frame_id"], result, request["analyzed_at"])
        return result

    def locate(self, request: dict[str, Any]) -> dict[str, Any]:
        observation = self.store.observation(request["frame_id"])
        if not observation.get("depth_artifact_id"):
            return {
                "status": "unavailable",
                "reason": "frame has no depth",
                "frame_id": request["frame_id"],
            }
        content, _ = self.store.read(observation["depth_artifact_id"])
        detection_id = request.get(
            "detection_id", request.get("detection", {}).get("detection_id")
        )
        matches = [
            d
            for d in observation.get("last_analysis", {}).get("detections", [])
            if d["detection_id"] == detection_id
        ]
        if not matches:
            return {
                "status": "unavailable",
                "reason": "detection is not present in this frame's recorded inference",
            }
        result = locate_target(
            observation, matches[0], np.load(io.BytesIO(content), allow_pickle=False)
        )
        self.store.save_location(observation["frame_id"], result)
        return result

    def track(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._tracker is None:
            self._tracker = PersonTracker(self.store.uav_id)
        return self._tracker.update(
            request["detections"], request["observed_at"], request["locations"]
        )

    def retrieve(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("reanalyze_frame_id"):
            return self.store.reanalyze(
                request["reanalyze_frame_id"], self.detector, request["analyzed_at"]
            )
        return {
            "status": "succeeded",
            "records": self.store.retrieve(**request),
            "semantic_backend": "local BGE"
            if self.store.embedder
            else "unavailable; literal/FTS/time/space only",
        }

    def read(self, request: dict[str, Any]) -> dict[str, Any]:
        content, metadata = self.store.read(request["artifact_id"])
        return {
            "status": "succeeded",
            "artifact_id": request["artifact_id"],
            "bytes": len(content),
            "path": str(self.store.artifact_path(request["artifact_id"])),
            "metadata": metadata,
        }

    def verify(self, request: dict[str, Any]) -> dict[str, Any]:
        first = self.store.observation(request["first_frame_id"])
        second = self.store.observation(request["second_frame_id"])
        for name, observation in (("first", first), ("second", second)):
            detection_id = request[name + "_detection_id"]
            observation["location"] = observation.get("locations", {}).get(
                detection_id, {}
            )
        return verify_target(first, second)

    def compare(self, request: dict[str, Any]) -> dict[str, Any]:
        first = self.store.observation(request["first_frame_id"])
        second = self.store.observation(request["second_frame_id"])
        for observation in (first, second):
            observation["detections"] = observation.get("last_analysis", {}).get(
                "detections", []
            )
        return compare_observations(first, second)

    def handlers(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {
            "detect-persons": self.detect,
            "track-persons": self.track,
            "locate-target": self.locate,
            "verify-target": self.verify,
            "compare-observations": self.compare,
            "retrieve-sightings": self.retrieve,
            "read-evidence": self.read,
            "package-evidence": lambda r: package_evidence(
                self.store, r["artifact_id"], r.get("bbox_xyxy"), r["budget_bytes"]
            ),
        }
