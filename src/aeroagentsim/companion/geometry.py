"""Estimated-pose RGB-D geometry. No entity labels or simulator poses are inputs."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def quaternion_matrix(xyzw: list[float]) -> np.ndarray:
    q = np.asarray(xyzw, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or np.linalg.norm(q) < 1e-9:
        raise ValueError("invalid quaternion")
    x, y, z, w = q / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def pixel_ray(pixel: list[float], calibration: dict[str, Any]) -> np.ndarray:
    k = np.asarray(calibration["K"], dtype=float).reshape(3, 3)
    distortion = np.asarray(calibration.get("distortion", []), dtype=float)
    if distortion.size and np.any(distortion):
        import cv2

        points = np.asarray(pixel, dtype=np.float64).reshape(1, 1, 2)
        if calibration.get("distortion_model") == "equidistant":
            xy = cv2.fisheye.undistortPoints(points, k, distortion).reshape(2)
        else:
            xy = cv2.undistortPoints(points, k, distortion).reshape(2)
        return np.array([xy[0], xy[1], 1.0])
    return np.linalg.solve(k, [pixel[0], pixel[1], 1.0])


def backproject(
    pixel: list[float], depth_m: float, calibration: dict[str, Any]
) -> np.ndarray:
    ray = pixel_ray(pixel, calibration)
    convention = calibration["depth_convention"]
    if convention == "optical_z":
        return ray * depth_m
    if convention == "ray_range":
        return ray * depth_m / np.linalg.norm(ray)
    raise ValueError("depth convention must be optical_z or ray_range")


def select_depth(
    depth: np.ndarray,
    bbox: list[float],
    *,
    minimum_m: float = 0.2,
    maximum_m: float = 60.0,
) -> dict[str, Any]:
    """Pick a compact near cluster inside a conservative central person ROI.

    A small foreground island or competing clusters are insufficient evidence.
    This intentionally prefers unavailable to selecting roof/background range.
    """
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    left, right = (
        max(0, int(x1 + 0.25 * w)),
        min(depth.shape[1], math.ceil(x2 - 0.25 * w)),
    )
    top, bottom = (
        max(0, int(y1 + 0.15 * h)),
        min(depth.shape[0], math.ceil(y2 - 0.15 * h)),
    )
    roi = depth[top:bottom, left:right]
    if roi.size < 9:
        return {"status": "unavailable", "reason": "target ROI too small"}
    valid = np.isfinite(roi) & (roi >= minimum_m) & (roi <= maximum_m)
    support = float(valid.mean())
    if support < 0.5:
        return {
            "status": "unavailable",
            "reason": "insufficient valid depth",
            "valid_support": support,
        }
    ys, xs = np.nonzero(valid)
    values = roi[ys, xs]
    order = np.argsort(values)
    # Gaps > 0.5 m separate likely person/background surfaces at this scale.
    splits = np.flatnonzero(np.diff(values[order]) > 0.5) + 1
    groups = np.split(order, splits)
    near = groups[0]
    cluster_support = len(near) / len(values)
    if cluster_support < 0.6:
        return {
            "status": "unavailable",
            "reason": "background or multiple depth clusters ambiguous",
            "valid_support": support,
            "near_cluster_support": cluster_support,
        }
    selected = values[near]
    spread = float(np.percentile(selected, 90) - np.percentile(selected, 10))
    if spread > 1.5:
        return {
            "status": "unavailable",
            "reason": "depth cluster too dispersed",
            "spread_m": spread,
        }
    middle = near[int(np.argmin(np.abs(selected - np.median(selected))))]
    pixel = [int(xs[middle]) + left, int(ys[middle]) + top]
    stride = max(1, len(near) // 128)
    return {
        "status": "available",
        "depth_m": float(values[middle]),
        "pixel": pixel,
        "valid_support": support,
        "near_cluster_support": cluster_support,
        "spread_m": spread,
        "samples": [
            [int(xs[i]) + left, int(ys[i]) + top, float(values[i])]
            for i in near[::stride]
        ],
    }


def locate_target(
    observation: dict[str, Any], detection: dict[str, Any], depth: np.ndarray
) -> dict[str, Any]:
    common = {
        "frame_id": observation["frame_id"],
        "detection_id": detection["detection_id"],
        "target_reference": "visible_person_surface_horizontal_position",
    }
    estimate = observation.get("vehicle_estimate", {})
    calibration = observation.get("calibration", {})
    if (
        observation.get("localization_status") != "available"
        or estimate.get("status") != "available"
    ):
        return {
            **common,
            "status": "unavailable",
            "reason": observation.get(
                "localization_reason", "synchronized PX4 estimate unavailable"
            ),
        }
    if not calibration.get("registered_rgb_depth"):
        return {
            **common,
            "status": "unavailable",
            "reason": "RGB/depth registration not established",
        }
    if list(depth.shape) != [calibration["height"], calibration["width"]]:
        return {
            **common,
            "status": "unavailable",
            "reason": "registered depth shape differs from RGB",
        }
    selection = select_depth(
        depth,
        detection["bbox_xyxy"],
        minimum_m=calibration.get("depth_min_m", 0.2),
        maximum_m=calibration.get("depth_max_m", 60.0),
    )
    if selection["status"] != "available":
        return {**common, **selection}
    camera = backproject(selection["pixel"], selection["depth_m"], calibration)
    extrinsic = np.asarray(
        calibration["T_body_frd_from_camera_optical"], dtype=float
    ).reshape(4, 4)
    body = (extrinsic @ np.append(camera, 1))[:3]
    rotation = quaternion_matrix(estimate["attitude_body_frd_to_world_enu_xyzw"])
    position = rotation @ body + np.asarray(estimate["position_enu_m"])
    return {
        **common,
        "status": "available",
        "position_enu_m": position.tolist(),
        "selected_depth": selection,
        "calibration_version": calibration.get("version"),
        "depth_selection_limitations": "A compact single background plane inside a false-positive box cannot be distinguished by depth clustering alone; detector and independent views are required",
        "origin_version": estimate.get("origin_version"),
        "source_sim_ns": observation["observed_at"],
        "horizontal_uncertainty_m": calibration.get("engineering_horizontal_margin_m"),
        "horizontal_uncertainty_source": "configured conservative engineering budget, not measured PX4 covariance"
        if calibration.get("engineering_horizontal_margin_m") is not None
        else "unknown",
        "uncertainty": {
            "depth_spread_m": selection["spread_m"],
            "pose_horizontal_sigma_m": estimate.get("horizontal_sigma_m"),
            "bound_m": None,
            "unmodeled": [
                "detector ROI surface bias",
                "PX4 covariance unavailable unless separately supplied",
            ],
        },
    }


def verify_target(
    first: dict[str, Any], second: dict[str, Any], min_view_angle_deg: float = 20.0
) -> dict[str, Any]:
    refs = [first.get("frame_id"), second.get("frame_id")]
    if refs[0] == refs[1] or (
        first.get("rgb_sha256")
        and second.get("rgb_sha256")
        and first["rgb_sha256"] == second["rgb_sha256"]
    ):
        return {
            "status": "uncertain",
            "reason": "independent new image required",
            "frames": refs,
        }
    if second["observed_at"] <= first["observed_at"]:
        return {
            "status": "uncertain",
            "reason": "second observation is not newer",
            "frames": refs,
        }
    for item in (first, second):
        if item.get("location", {}).get("status") != "available":
            return {
                "status": "uncertain",
                "reason": "one view lacks a usable target location",
                "frames": refs,
            }
    p1, p2 = (np.asarray(i["location"]["position_enu_m"]) for i in (first, second))
    c1, c2 = (
        np.asarray(i["vehicle_estimate"]["position_enu_m"]) for i in (first, second)
    )
    r1, r2 = p1 - c1, p2 - c2
    angle = math.degrees(
        math.acos(
            float(
                np.clip(
                    np.dot(r1, r2) / (np.linalg.norm(r1) * np.linalg.norm(r2)), -1, 1
                )
            )
        )
    )
    separation = float(np.linalg.norm((p2 - p1)[:2]))
    independent = (
        first.get("uav_id") != second.get("uav_id") or angle >= min_view_angle_deg
    )
    confirmed = independent and separation <= 5.0
    return {
        "status": "confirmed" if confirmed else "uncertain",
        "frames": refs,
        "view_angle_deg": angle,
        "horizontal_separation_m": separation,
        "reason": "independent location agreement"
        if confirmed
        else "view geometry or location agreement insufficient",
    }


def compare_observations(
    first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    old = {
        f"{first['uav_id']}:{d['track_id']}"
        for d in first.get("detections", [])
        if d.get("track_id")
    }
    new = {
        f"{second['uav_id']}:{d['track_id']}"
        for d in second.get("detections", [])
        if d.get("track_id")
    }
    return {
        "frames": [first["frame_id"], second["frame_id"]],
        "observed_at": [first["observed_at"], second["observed_at"]],
        "continued_tracks": sorted(old & new),
        "newly_observed_tracks": sorted(new - old),
        "not_observed_tracks": sorted(old - new),
        "absence_interpretation": "not observed; departure is not established",
    }
