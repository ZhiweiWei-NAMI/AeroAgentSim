"""Real RGB inference and anonymous world-coordinate track association."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np


class RtdetrDetector:
    def __init__(
        self,
        model_path: str | Path,
        score_threshold: float = 0.5,
        person_label: int = 0,
        providers: list[str] | None = None,
        tile_size: int | None = None,
    ):
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        import onnxruntime as ort

        ort.disable_telemetry_events()
        self.path = Path(model_path)
        self.threshold, self.person_label = score_threshold, person_label
        self.tile_size = tile_size
        if tile_size is not None and tile_size <= 0:
            raise ValueError("tile_size must be positive")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(self.path),
            sess_options=options,
            providers=providers or ["CPUExecutionProvider"],
        )
        self.providers = self.session.get_providers()
        self.model_sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        shape = self.session.get_inputs()[0].shape
        self.height, self.width = int(shape[-2]), int(shape[-1])

    def detect(
        self, rgb_path: str | Path, frame_id: str, output_dir: str | Path | None = None
    ) -> dict[str, Any]:
        from PIL import Image, ImageDraw

        started = time.perf_counter_ns()
        with Image.open(rgb_path) as source:
            rgb = source.convert("RGB")
        views = [(0, 0, rgb.width, rgb.height)]
        if self.tile_size is not None:
            side = self.tile_size
            stride = max(1, round(side * 0.75))

            def starts(length: int) -> list[int]:
                end = max(0, length - side)
                return sorted(set([*range(0, end + 1, stride), end]))

            views += [
                (x, y, min(x + side, rgb.width), min(y + side, rgb.height))
                for y in starts(rgb.height)
                for x in starts(rgb.width)
                if (x, y, min(x + side, rgb.width), min(y + side, rgb.height)) != views[0]
            ]
        detections = []
        truncated_tile_detections = 0
        for view_index, (left, top, right, bottom) in enumerate(views):
            view = rgb.crop((left, top, right, bottom))
            resized = view.resize((self.width, self.height), Image.Resampling.BILINEAR)
            tensor = np.asarray(resized, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
            labels, boxes, scores = self.session.run(
                ["labels", "boxes", "scores"],
                {"images": tensor, "orig_target_sizes": np.asarray([[view.width, view.height]], dtype=np.int64)},
            )
            for index, (label, box, score) in enumerate(zip(labels[0], boxes[0], scores[0])):
                if int(label) != self.person_label or float(score) < self.threshold:
                    continue
                x1, y1, x2, y2 = map(float, box)
                bbox = [max(0.0, x1) + left, max(0.0, y1) + top,
                        min(float(view.width), x2) + left, min(float(view.height), y2) + top]
                if bbox[0] >= bbox[2] or bbox[1] >= bbox[3] or not np.isfinite(bbox + [float(score)]).all():
                    continue
                # Internal crop edges can turn one person into a high-score
                # partial box. Full-frame and overlapping views remain available
                # for detection; real image boundaries are not excluded.
                if view_index and (
                    (left > 0 and bbox[0] <= left + 1)
                    or (top > 0 and bbox[1] <= top + 1)
                    or (right < rgb.width and bbox[2] >= right - 1)
                    or (bottom < rgb.height and bbox[3] >= bottom - 1)
                ):
                    truncated_tile_detections += 1
                    continue
                detections.append({
                    "detection_id": f"{frame_id}:person:{view_index * 100000 + index}",
                    "frame_id": frame_id, "class": "person", "label": int(label),
                    "score": float(score), "bbox_xyxy": bbox,
                })
        if len(views) > 1:
            kept = []
            for candidate in sorted(detections, key=lambda d: -d["score"]):
                box = np.asarray(candidate["bbox_xyxy"])
                duplicate = False
                for previous in kept:
                    other = np.asarray(previous["bbox_xyxy"])
                    intersection = np.maximum(0, np.minimum(box[2:], other[2:]) - np.maximum(box[:2], other[:2])).prod()
                    union = (box[2:] - box[:2]).prod() + (other[2:] - other[:2]).prod() - intersection
                    if intersection / union > 0.5:
                        duplicate = True
                        break
                if not duplicate:
                    kept.append(candidate)
            detections = kept
        result = {
            "frame_id": frame_id,
            "status": "succeeded",
            "detections": detections,
            "rgb_sha256": hashlib.sha256(Path(rgb_path).read_bytes()).hexdigest(),
            "model_sha256": self.model_sha256,
            "providers": self.providers,
            "input_size": [self.width, self.height],
            "person_label": self.person_label,
            "inference_views": views,
            "tile_size": self.tile_size,
            "minimum_tile_overlap": 0.25 if len(views) > 1 else None,
            "merge_nms_iou": 0.5 if len(views) > 1 else None,
            "truncated_tile_detections": truncated_tile_detections,
            "tile_edge_policy": "exclude boxes within 1px of internal crop edges; keep full image boundaries" if len(views) > 1 else None,
            "preprocessing": "RGB PIL bilinear resize; float32 /255; NCHW; original sizes W,H",
            "inference_wall_ns": time.perf_counter_ns() - started,
        }
        if output_dir is not None:
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            stem = hashlib.sha256(frame_id.encode()).hexdigest()[:24]
            raw_path = output / f"{stem}-detections-{time.time_ns()}.json"
            draw = ImageDraw.Draw(rgb)
            for detection in detections:
                draw.rectangle(detection["bbox_xyxy"], outline="red", width=3)
                draw.text(
                    detection["bbox_xyxy"][:2],
                    f"person {detection['score']:.2f}",
                    fill="red",
                )
            annotated_path = raw_path.with_suffix(".jpg")
            rgb.save(annotated_path, quality=90)
            raw_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            result["raw_result_path"], result["annotated_path"] = (
                str(raw_path),
                str(annotated_path),
            )
        return result


class PersonTracker:
    """Associate estimated world positions, never confuse camera motion with a person.

    Detections lacking usable depth/pose remain unassociated. These track IDs
    are local anonymous hypotheses, not human identities.
    """

    def __init__(self, uav_id: str, distance_threshold_m: float = 3.0):
        from uuid import uuid4

        from norfair import Tracker

        self.uav_id = uav_id
        # Tracker dynamics are in-memory. A restart must not reuse old local
        # numbers as if they were persistent human identities.
        self.session_id = uuid4().hex
        self.tracker = Tracker(
            distance_function="euclidean",
            distance_threshold=distance_threshold_m,
            hit_counter_max=10,
            initialization_delay=0,
        )
        self.last_ns: int | None = None
        self.previous_ids: set[str] = set()

    def update(
        self,
        detections: list[dict[str, Any]],
        observed_at_ns: int,
        locations: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        from norfair import Detection

        if self.last_ns is not None and observed_at_ns <= self.last_ns:
            return {
                "status": "unavailable",
                "reason": "tracking requires a newer frame",
            }
        self.last_ns = observed_at_ns
        inputs = []
        unassociated = []
        for detection in detections:
            location = locations.get(detection["detection_id"], {})
            if location.get("status") != "available":
                unassociated.append(detection["detection_id"])
                continue
            inputs.append(
                Detection(
                    points=np.asarray([location["position_enu_m"][:2]]),
                    scores=np.asarray([detection["score"]]),
                    data=detection,
                )
            )
        tracked = self.tracker.update(detections=inputs)
        results = [
            {
                "track_id": f"{self.uav_id}:track:{self.session_id}:{item.id}",
                "position_enu_xy_m": item.estimate[0].tolist(),
                "detection_id": item.last_detection.data["detection_id"],
                "observed_at": observed_at_ns,
                "status": "observed"
                if item.last_detection in inputs
                else "predicted_not_observed",
            }
            for item in tracked
        ]
        ids = {item["track_id"] for item in results}
        lost = sorted(self.previous_ids - ids)
        self.previous_ids = ids
        return {
            "status": "succeeded",
            "tracks": results,
            "lost_track_ids": lost,
            "unassociated_detection_ids": unassociated,
            "association_frame": "world_enu from synchronized PX4 estimate and depth",
        }
