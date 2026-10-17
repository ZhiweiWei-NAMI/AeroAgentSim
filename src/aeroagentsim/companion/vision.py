"""Real RGB inference and anonymous world-coordinate track association."""

from __future__ import annotations

import hashlib
import json
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
    ):
        import onnxruntime as ort

        ort.disable_telemetry_events()
        self.path = Path(model_path)
        self.threshold, self.person_label = score_threshold, person_label
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
        resized = rgb.resize((self.width, self.height), Image.Resampling.BILINEAR)
        tensor = np.asarray(resized, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
        labels, boxes, scores = self.session.run(
            ["labels", "boxes", "scores"],
            {
                "images": tensor,
                "orig_target_sizes": np.asarray(
                    [[rgb.width, rgb.height]], dtype=np.int64
                ),
            },
        )
        detections = []
        for index, (label, box, score) in enumerate(
            zip(labels[0], boxes[0], scores[0])
        ):
            if int(label) != self.person_label or float(score) < self.threshold:
                continue
            x1, y1, x2, y2 = map(float, box)
            bbox = [
                max(0.0, x1),
                max(0.0, y1),
                min(float(rgb.width), x2),
                min(float(rgb.height), y2),
            ]
            if (
                bbox[0] >= bbox[2]
                or bbox[1] >= bbox[3]
                or not np.isfinite(bbox + [float(score)]).all()
            ):
                continue
            detections.append(
                {
                    "detection_id": f"{frame_id}:person:{index}",
                    "frame_id": frame_id,
                    "class": "person",
                    "label": int(label),
                    "score": float(score),
                    "bbox_xyxy": bbox,
                }
            )
        result = {
            "frame_id": frame_id,
            "status": "succeeded",
            "detections": detections,
            "rgb_sha256": hashlib.sha256(Path(rgb_path).read_bytes()).hexdigest(),
            "model_sha256": self.model_sha256,
            "providers": self.providers,
            "input_size": [self.width, self.height],
            "person_label": self.person_label,
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
        from norfair import Tracker

        self.uav_id = uav_id
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
                "track_id": f"{self.uav_id}:track:{item.id}",
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
