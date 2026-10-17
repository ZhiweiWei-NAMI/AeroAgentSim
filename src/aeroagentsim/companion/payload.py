"""Separate release, sensor contact and radio confirmation stages."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .transport import NS, save_json


class PayloadDelivery:
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root) / "payload-state.json"
        self.packages: dict[str, dict[str, Any]] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def release_requested(self, package_id: str, operation_id: str, now_ns: int, task_id: str, task_revision: int, drop_site: dict[str, Any] | None = None) -> dict[str, Any]:
        previous = self.packages.get(package_id)
        if previous is not None:
            # Unknown after restart must be reconciled, never release twice.
            return {**previous, "repeat_release": False}
        result = {"package_id": package_id, "operation_id": operation_id, "task_id": task_id, "task_revision": task_revision, "status": "release_requested", "requested_ns": now_ns, "released": False, "observed_at_site": False, "receipt_delivered": False, "delivery_verified": False, "drop_site": drop_site}
        self.packages[package_id] = result
        save_json(self.path, self.packages)
        return result

    @staticmethod
    def _update(package: dict[str, Any]) -> None:
        package["delivery_verified"] = bool(package["released"] and package["observed_at_site"] and package["receipt_delivered"])
        if package["delivery_verified"]:
            package["status"] = "delivery_verified"

    def actuator_state(self, package_id: str, state: str, source_ns: int | None, source: str, observed_sim_ns: int | None = None, release_interval_ns: list[int] | None = None) -> dict[str, Any]:
        if source != "gazebo_detachable_joint" and not (source == "gazebo_detachable_joint_component_query" and state == "attached"):
            raise ValueError("physical joint state required; component lookup cannot prove detachment")
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

    def observe(self, package_id: str, frame_id: str, observed_at_ns: int) -> None:
        package = self.packages[package_id]
        if package.get("released_ns") is None or observed_at_ns <= package["released_ns"]:
            raise ValueError("post-release frame required")
        package.update(observed_at_site=True, post_drop_frame_id=frame_id)
        self._update(package)
        save_json(self.path, self.packages)

    def receipt(self, envelope: dict[str, Any]) -> dict[str, Any]:
        record = envelope["data"]
        package = self.packages[record["package_id"]]
        if envelope.get("source") != "drop-receiver-1" or not envelope.get("native_packet_ids") or "received_ns" not in envelope:
            raise ValueError("receipt must have actually arrived through radio")
        if record.get("source") != "gazebo_contact_sensor" or record.get("contact_duration_ns", 0) < NS:
            raise ValueError("sustained physical contact evidence required")
        if (envelope["task_id"], envelope["task_revision"]) != (package["task_id"], package["task_revision"]):
            raise ValueError("stale payload receipt")
        package.update(receipt_delivered=True, receipt_id=envelope["message_id"], receipt_received_ns=envelope["received_ns"])
        package["delivery_verified"] = package["released"] and package["observed_at_site"]
        package["status"] = "delivery_verified" if package["delivery_verified"] else "receipt_delivered"
        self._update(package)
        save_json(self.path, self.packages)
        return package

    def status(self, package_id: str, now_ns: int) -> dict[str, Any]:
        package = self.packages[package_id]
        if package["released"] and not package["receipt_delivered"] and now_ns - package["released_ns"] >= 30 * NS:
            package.update(status="receipt_unconfirmed", delivery_verified=False, reason="No radio receipt yet; physical contact outcome is unknown")
            save_json(self.path, self.packages)
        return dict(package)


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
