"""Run the existing rescue perception tools on saved platform RGB-D captures.

Inputs are PlatformCityCamera's actual capture.json/rgb.png/depth.f32 outputs.
No scene, person coordinates, labels, or reference boxes are read. This is an
offline component invocation, not an autonomous flight or a native-sensor test.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from aerorescue.companion.evidence import PerceptionTools
from aerorescue.companion.geometry import quaternion_matrix
from aerorescue.companion.sensors import ingest_frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, action="append", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tile-size", type=int, default=None)
    args = parser.parse_args()
    if not 1 <= len(args.capture) <= 2:
        parser.error("supply one frame, or two independently acquired frames")
    inputs = [(path.resolve(), json.loads(path.read_text())) for path in args.capture]
    first = inputs[0][1]["observation"]
    if any((row["observation"]["run_id"], row["observation"]["uav_id"]) !=
           (first["run_id"], first["uav_id"]) for _, row in inputs):
        parser.error("this local archive requires the same run and observer")
    args.output.mkdir(parents=True, exist_ok=False)
    perception = PerceptionTools(args.output / "agent", first["run_id"], first["uav_id"],
                                 args.model, detector_tile_size=args.tile_size)
    handlers = perception.handlers()
    report = {"scope": "offline existing rescue tools on original platform-rendered RGB-D",
              "pose_source": "preserved capture observation estimate; renderer pose is diagnostic only",
              "analysis_mode": "read-only source-cut replay; no simulated time advanced",
              "detector_threshold": 0.5, "tile_size": args.tile_size,
              "inputs": [str(path) for path, _ in inputs], "frames": [], "tool_calls": []}
    # Analyze at the latest actually recorded boundary, preserving each frame's
    # earlier observed_at. Actual processing wall time is recorded separately.
    analysis_ns = max(row["observation"]["available_sim_ns"] for _, row in inputs)

    def call(name, request):
        started = time.time_ns()
        result = handlers[name](request)
        report["tool_calls"].append({"tool": name, "request": request, "result": result,
                                     "started_wall_ns": started, "completed_wall_ns": time.time_ns()})
        (args.output / "report.json").write_text(json.dumps(report, indent=2))
        return result

    for path, saved in inputs:
        observation = saved["observation"]
        if observation["source"] != "platform_threejs_city_camera":
            raise ValueError("capture is not an original-platform city camera output")
        descriptor = saved["depth"]
        if descriptor["encoding"] != "float32-le-metres" or descriptor["vertical_origin"] != "top-left":
            raise ValueError("unsupported depth byte layout")
        rgb_path = path.parent / saved["png_path"]
        depth = np.frombuffer((path.parent / descriptor["data_path"]).read_bytes(), dtype="<f4")
        depth = depth.reshape(descriptor["height"], descriptor["width"]).astype(np.float32)
        with Image.open(rgb_path) as image:
            if image.size != (depth.shape[1], depth.shape[0]):
                raise ValueError("RGB and depth image dimensions differ")
        estimate = observation["vehicle_estimate"]
        frame = ingest_frame(perception.store, observation, rgb_path.read_bytes(), depth,
                             [estimate], observation["available_sim_ns"])
        # Compare coordinate wiring without replacing the onboard estimate.
        body = np.eye(4)
        body[:3, :3] = quaternion_matrix(estimate["attitude_body_frd_to_world_enu_xyzw"])
        body[:3, 3] = estimate["position_enu_m"]
        estimated_camera = body @ np.asarray(observation["calibration"]["T_body_frd_from_camera_optical"])
        rendered_camera = np.asarray(saved["camera_pose"]["T_world_enu_from_camera_optical"])
        item = {"frame_id": frame["frame_id"], "observed_at": frame["observed_at"],
                "source_cut": observation["source_cut"], "rgb_artifact_id": frame["rgb_artifact_id"],
                "depth_artifact_id": frame["depth_artifact_id"], "depth_valid_fraction": frame["depth_valid_fraction"],
                "estimated_vs_render_camera_max_abs_entry_difference": float(np.max(np.abs(estimated_camera-rendered_camera)))}
        report["frames"].append(item)
        item["detection"] = call("detect-persons", {"frame_id": frame["frame_id"], "analyzed_at": analysis_ns})
        item["locations"] = [call("locate-target", {"frame_id": frame["frame_id"], "detection_id": d["detection_id"]})
                             for d in item["detection"]["detections"]]
    report["verification_candidates"] = []
    if len(report["frames"]) == 2:
        a, b = report["frames"]
        # Retain every real detection-pair result. No truth-based association or
        # selection of just the successful pair is performed.
        for da in a["detection"]["detections"]:
            for db in b["detection"]["detections"]:
                request = {"first_frame_id": a["frame_id"], "second_frame_id": b["frame_id"],
                           "first_detection_id": da["detection_id"], "second_detection_id": db["detection_id"]}
                report["verification_candidates"].append(call("verify-target", request))
    report["observation_conclusion"] = (
        "independent image-and-location agreement observed"
        if any(row["status"] == "confirmed" for row in report["verification_candidates"])
        else "uncertain; no independently confirmed detection pair")
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"report": str(args.output / "report.json"),
                      "detections": [len(f["detection"]["detections"]) for f in report["frames"]],
                      "observation_conclusion": report["observation_conclusion"]}))


if __name__ == "__main__":
    main()
