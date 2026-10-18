"""Separate release, sensor contact and radio confirmation stages."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .transport import NS, save_json


class PayloadDelivery:
    def __init__(self, root: str | Path, *, execution_backend: str = "native") -> None:
        if execution_backend not in {"native", "mock"}:
            raise ValueError("unknown payload execution backend")
        self.execution_backend = execution_backend
        self.path = Path(root) / "payload-state.json"
        self.packages: dict[str, dict[str, Any]] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def release_requested(self, package_id: str, operation_id: str, now_ns: int, task_id: str, task_revision: int, drop_site: dict[str, Any] | None = None) -> dict[str, Any]:
        previous = self.packages.get(package_id)
        if previous is not None:
            # Unknown after restart must be reconciled, never release twice.
            return {**previous, "repeat_release": False}
        result = {"execution_backend": self.execution_backend, "package_id": package_id, "operation_id": operation_id, "task_id": task_id, "task_revision": task_revision, "status": "release_requested", "requested_ns": now_ns, "released": False, "observed_at_site": False, "receipt_delivered": False, "delivery_verified": False, "drop_site": drop_site}
        self.packages[package_id] = result
        save_json(self.path, self.packages)
        return result

    @staticmethod
    def _update(package: dict[str, Any]) -> None:
        package["observed_at_site"] = package.get("package_observation", {}).get("status") == "confirmed"
        package["delivery_verified"] = bool(package["released"] and package.get("package_observation", {}).get("status") == "confirmed" and package["receipt_delivered"] and package["package_observation"].get("observed_at", -1) > max(package.get("released_ns", 0), package.get("receipt_received_ns", 0)))
        if package["delivery_verified"]:
            package["status"] = "delivery_verified"
        elif package.get("status") == "delivery_verified":
            package["status"] = "package_visual_unknown"

    def actuator_state(self, package_id: str, state: str, source_ns: int | None, source: str, observed_sim_ns: int | None = None, release_interval_ns: list[int] | None = None) -> dict[str, Any]:
        expected = "mock_detachable_joint" if self.execution_backend == "mock" else "gazebo_detachable_joint"
        component_attached = self.execution_backend == "native" and source == "gazebo_detachable_joint_component_query" and state == "attached"
        if source != expected and not component_attached:
            raise ValueError("joint source does not match the explicitly configured execution backend")
        package = self.packages[package_id]
        if state == "detached":
            if source_ns is None and observed_sim_ns is None:
                raise ValueError("detachment requires a source time or observed interval")
            package.update(released=True, released_ns=package.get("released_ns", source_ns if source_ns is not None else observed_sim_ns), source_time_ns=source_ns, release_interval_ns=release_interval_ns, status="released")
        elif state not in {"attached", "detached"}:
            raise ValueError("unknown joint state")
        self._update(package)
        save_json(self.path, self.packages)
        return package

    def observe(self, package_id: str, observation: dict[str, Any]) -> None:
        package = self.packages[package_id]
        observed_at_ns = observation["observed_at"]
        if package.get("released_ns") is None or observed_at_ns <= package["released_ns"]:
            raise ValueError("post-release frame required")
        if package.get("receipt_received_ns") is not None and observed_at_ns <= package["receipt_received_ns"]:
            raise ValueError("new post-contact-receipt frame required")
        package.update(observed_at_site=observation.get("status") == "confirmed",
                       post_drop_frame_id=observation["frame_id"], package_observation=observation)
        if observation.get("status") != "confirmed":
            package.update(status="package_visual_unknown", reason=observation.get("reason"))
        self._update(package)
        save_json(self.path, self.packages)

    def receipt(self, envelope: dict[str, Any]) -> dict[str, Any]:
        record = envelope["data"]
        package = self.packages[record["package_id"]]
        if envelope.get("source") != "drop-receiver-1" or not envelope.get("native_packet_ids") or "received_ns" not in envelope:
            raise ValueError("receipt must have actually arrived through radio")
        expected = "mock_contact_model" if self.execution_backend == "mock" else "gazebo_contact_sensor"
        if record.get("source") != expected or record.get("contact_duration_ns", 0) < NS:
            raise ValueError("sustained physical contact evidence required")
        if (envelope["task_id"], envelope["task_revision"]) != (package["task_id"], package["task_revision"]):
            raise ValueError("stale payload receipt")
        package.update(receipt_delivered=True, receipt_id=envelope["message_id"], receipt_received_ns=envelope["received_ns"])
        package["status"] = "receipt_delivered"
        self._update(package)
        save_json(self.path, self.packages)
        return package

    def status(self, package_id: str, now_ns: int) -> dict[str, Any]:
        package = self.packages[package_id]
        self._update(package)
        if package["released"] and not package["receipt_delivered"] and now_ns - package["released_ns"] >= 30 * NS:
            package.update(status="receipt_unconfirmed", delivery_verified=False, reason="No radio receipt yet; physical contact outcome is unknown")
            save_json(self.path, self.packages)
        return dict(package)


def observe_marked_package(rgb_bytes: bytes, depth: Any, frame: dict[str, Any], marker: dict[str, Any] | None,
                           polygon: list[list[float]], surface_height_m: float, package_height_m: float) -> dict[str, Any]:
    """Decode a public inventory marker, then locate its visible top from RGB-D.

    No package/entity pose is an input. A missing decode is unknown, not absence.
    The claim is a marked box at the receiving site, never recipient possession.
    """
    import cv2
    import numpy as np
    from .geometry import backproject, quaternion_matrix

    result = {"status": "unknown", "frame_id": frame["frame_id"], "observed_at": frame["observed_at"],
              "rgb_artifact_id": frame.get("rgb_artifact_id"), "depth_artifact_id": frame.get("depth_artifact_id"),
              "method": "public_aruco_inventory_id_and_registered_depth",
              "scope": "marked_box_at_receiving_site; recipient_collection_not_observed"}
    def unknown(reason: str, **details: Any) -> dict[str, Any]:
        return {**result, "reason": reason, **details}
    if not marker or marker.get("dictionary") != "DICT_4X4_50" or marker.get("border_bits") != 1:
        return unknown("supported public inventory marker is not configured")
    result["expected_marker"] = dict(marker)
    estimate, calibration = frame.get("vehicle_estimate", {}), frame.get("calibration", {})
    if frame.get("localization_status") != "available" or estimate.get("status") != "available":
        return unknown("synchronized camera estimate unavailable")
    rgb = cv2.imdecode(np.frombuffer(rgb_bytes, np.uint8), cv2.IMREAD_COLOR)
    if rgb is None or not calibration.get("registered_rgb_depth") or tuple(depth.shape) != rgb.shape[:2] or list(depth.shape) != [calibration.get("height"), calibration.get("width")]:
        return unknown("registered RGB and numerical depth unavailable")
    parameters = cv2.aruco.DetectorParameters()
    parameters.markerBorderBits = marker["border_bits"]
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), parameters)
    corners, ids, rejected = detector.detectMarkers(rgb)
    result.update(decoded_marker_ids=[] if ids is None else ids.reshape(-1).tolist(), rejected_candidates=len(rejected))
    matches = [i for i, value in enumerate(result["decoded_marker_ids"]) if value == marker["id"]]
    if len(matches) != 1:
        return unknown("expected marker not uniquely decoded; absent, occluded or unresolved pixels remain unknown")
    pixels = np.asarray(corners[matches[0]], dtype=float).reshape(4, 2)
    result["marker_corners_xy"] = pixels.tolist()
    centre = pixels.mean(axis=0)
    # Interior supports avoid mixing the decoded boundary with roof/background.
    interior = centre + (pixels - centre) * .4
    sample_pixels = np.vstack([centre, interior])
    extrinsic = np.asarray(calibration["T_body_frd_from_camera_optical"]).reshape(4, 4)
    rotation = quaternion_matrix(estimate["attitude_body_frd_to_world_enu_xyzw"])
    samples, positions = [], []
    for pixel in sample_pixels:
        x, y = np.rint(pixel).astype(int)
        if not 0 <= y < depth.shape[0] or not 0 <= x < depth.shape[1]:
            return unknown("decoded marker depth support outside image")
        measured = float(depth[y, x])
        if not math.isfinite(measured) or not calibration.get("depth_min_m", .2) <= measured <= calibration.get("depth_max_m", 60):
            return unknown("decoded marker has invalid numerical depth")
        optical = backproject([float(x), float(y)], measured, calibration)
        body = (extrinsic @ np.append(optical, 1))[:3]
        point = rotation @ body + np.asarray(estimate["position_enu_m"])
        positions.append(point)
        samples.append({"pixel_xy": [int(x), int(y)], "depth_m": measured, "position_enu_m": point.tolist()})
    position = np.median(np.asarray(positions), axis=0)
    result.update(depth_samples=samples, position_enu_m=position.tolist(), position_reference="visible_marker_top_surface")
    contour = np.asarray(polygon, dtype=np.float32)
    if len(contour) < 3 or cv2.pointPolygonTest(contour, (float(position[0]), float(position[1])), False) < 0:
        return unknown("estimated marker position is outside the public receiving polygon")
    # Retain the existing 0.75 m site-depth allowance, explicitly an engineering
    # allowance, not measured covariance or proof of human receipt.
    residual = float(position[2] - (surface_height_m + package_height_m))
    result.update(surface_height_residual_m=residual, height_allowance_m=.75,
                  height_allowance_source="existing declared site-depth allowance; not calibrated covariance")
    if abs(residual) > .75:
        return unknown("estimated marker top is inconsistent with the receiving surface")
    return {**result, "status": "confirmed", "reason": "expected inventory marker decoded and located at the receiving site"}


def select_drop_site(polygon: list[list[float]], target_locations: list[dict[str, Any]], *, height_m: float, edge_margin_m: float = 1.0, separation_m: float = 5.0, spacing_m: float = 1.0) -> dict[str, Any]:
    """Conservative public-polygon grid search against estimated people only."""
    if len(polygon) < 3 or not target_locations:
        return {"status": "unavailable", "reason": "receiver_polygon_and_located_people_required"}
    if any(item.get("status") == "unavailable" or "position_enu_m" not in item or item.get("horizontal_uncertainty_m") is None for item in target_locations):
        return {"status": "unavailable", "reason": "person_location_or_uncertainty_unknown"}
    def inside(x: float, y: float) -> bool:
        value = False
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
                value = not value
        return value
    def segment_distance(x: float, y: float, a: list[float], b: list[float]) -> float:
        length = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2
        fraction = max(0.0, min(1.0, ((x - a[0]) * (b[0] - a[0]) + (y - a[1]) * (b[1] - a[1])) / length)) if length else 0.0
        return math.hypot(x - a[0] - fraction * (b[0] - a[0]), y - a[1] - fraction * (b[1] - a[1]))
    if spacing_m <= 0:
        raise ValueError("spacing must be positive")
    candidates = []
    x = min(point[0] for point in polygon)
    while x <= max(point[0] for point in polygon):
        y = min(point[1] for point in polygon)
        while y <= max(point[1] for point in polygon):
            if inside(x, y):
                edge = min(segment_distance(x, y, a, b) for a, b in zip(polygon, polygon[1:] + polygon[:1]))
                clearance = min(math.hypot(x - row["position_enu_m"][0], y - row["position_enu_m"][1]) - row["horizontal_uncertainty_m"] - row.get("body_radius_m", 0.5) - edge_margin_m for row in target_locations)
                if edge >= edge_margin_m and clearance >= separation_m:
                    candidates.append((min(edge, clearance), x, y))
            y += spacing_m
        x += spacing_m
    if not candidates:
        return {"status": "unavailable", "reason": "no_safe_drop_site"}
    _, x, y = max(candidates)
    return {"status": "succeeded", "position_enu_m": [x, y, height_m], "person_separation_m": separation_m, "edge_margin_m": edge_margin_m, "evidence": [row.get("frame_id") for row in target_locations]}
