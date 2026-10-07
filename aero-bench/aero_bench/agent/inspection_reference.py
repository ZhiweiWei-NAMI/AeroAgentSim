"""Deterministic two-target participant for the explicit inspection reference profile.

This participant navigates using public scenario geometry and classifies the
declared red inspection panel from actual Gateway camera pixels. It cannot read
private model assets, truth datasets, Provider state, or Verifier inputs.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from typing import Literal

from pydantic import Field, model_validator

from aero_bench.agent.runtime import AgentContext, AgentFrameworkError, GatewayClient
from aero_bench.config.models import Identifier, StrictModel
from aero_bench.providers.px4_gazebo.observations import InspectionRgbObservation
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import (
    DefectDetection,
    InspectionReportDetection,
    InspectionReportPayload,
)
from aero_bench.tasks.inspection.image_evidence import decode_rgb_png_pixels
from aero_bench.world.frame_math import EnuTransform, Vector3


POLICY_ASSET_ID = "asset.reference-inspection-policy"
REFERENCE_AGENT_VERSION = "0.4.0-inspection-reference.1"


class ReferenceWorkOrder(StrictModel):
    work_order_id: Identifier
    target_id: Identifier
    observation_id: Identifier
    defect_id: Identifier


class InspectionReferencePolicy(StrictModel):
    schema_version: Literal["aero-bench.inspection-reference-policy/v1"]
    vehicle_id: Identifier
    work_orders: tuple[ReferenceWorkOrder, ...] = Field(min_length=2, max_length=2)
    source_endpoint_id: Identifier
    destination_endpoint_id: Identifier
    cruise_up_m: float = Field(gt=12.5, le=80, allow_inf_nan=False)
    standoff_m: float = Field(ge=8, le=12, allow_inf_nan=False)
    dwell_s: float = Field(ge=2, le=10, allow_inf_nan=False)
    delivery_wait_s: float = Field(gt=0, le=60, allow_inf_nan=False)

    @model_validator(mode="after")
    def work_orders_are_distinct(self) -> "InspectionReferencePolicy":
        for field in ("work_order_id", "target_id", "observation_id"):
            values = [getattr(order, field) for order in self.work_orders]
            if len(set(values)) != len(values):
                raise ValueError(f"reference policy repeats {field}")
        return self


def panel_has_dark_patch(image: bytes) -> bool:
    """Classify a centered red panel; reject a frame without a visible panel.

    The versioned reference algorithm requires six dark pixels inside the red
    panel's interior. It does not infer a defect from target or work-order IDs.
    """

    width, height, pixels = decode_rgb_png_pixels(image)
    red: list[tuple[int, int]] = []
    for y in range(height * 3 // 10, height * 7 // 10):
        for x in range(width * 3 // 10, width * 7 // 10):
            offset = (y * width + x) * 3
            r, g, b = pixels[offset : offset + 3]
            if r >= 60 and r > 1.6 * g and r > 1.6 * b:
                red.append((x, y))
    if len(red) < 16:
        raise AgentFrameworkError("reference camera frame has no centered red panel")
    left, right = min(x for x, _ in red), max(x for x, _ in red)
    top, bottom = min(y for _, y in red), max(y for _, y in red)
    if right - left < 4 or bottom - top < 4:
        raise AgentFrameworkError("reference panel is too small to classify")
    dark = 0
    for y in range(top + 1, bottom):
        for x in range(left + 1, right):
            offset = (y * width + x) * 3
            if max(pixels[offset : offset + 3]) <= 45:
                dark += 1
    return dark >= 6


def _payload(envelope: object) -> dict[str, object]:
    return {item.name: item.value for item in envelope.payload}


class InspectionReferenceParticipant:
    def __init__(self, context: AgentContext, gateway: GatewayClient) -> None:
        if context.contract.agent.driver is not None:
            raise AgentFrameworkError(
                "reference participant must not declare a model driver"
            )
        self.context = context
        self.gateway = gateway
        self.policy = InspectionReferencePolicy.model_validate_json(
            context.asset_bytes(POLICY_ASSET_ID)
        )
        self.current = SimulationTime.model_validate(gateway.probe()["current"])
        self._sequence = 0
        self._commands: list[str] = []
        self._observations: list[str] = []

    def _identity(self, kind: str) -> str:
        self._sequence += 1
        return f"reference.{kind}.{self._sequence}"

    def advance(self) -> None:
        if self.current.tick >= self.context.contract.clock.max_steps:
            raise AgentFrameworkError(
                "reference participant exhausted the declared step budget"
            )
        decision = self.gateway.complete_turn(
            completion_id=self._identity("turn"),
            at=self.current,
            disposition="advance",
            command_ids=self._commands,
            observation_ids=self._observations,
        )
        if decision.status != "advanced":
            raise AgentFrameworkError(
                f"reference barrier did not advance: {decision.status}"
            )
        self.current = decision.at
        self._commands.clear()
        self._observations.clear()

    def wait(self, seconds: float) -> None:
        ticks = math.ceil(seconds * 1e9 / self.context.contract.clock.step_ns)
        for _ in range(ticks):
            self.advance()

    def command(self, tool_id: str, arguments: dict[str, object]) -> None:
        command_id = self._identity("command")
        self.gateway.decision_summary(
            summary_id=self._identity("decision"),
            at=self.current,
            summary=(
                f"Reference policy {REFERENCE_AGENT_VERSION} requests {tool_id} "
                "for the declared inspection, report delivery or terminal flight "
                "step. Wait for the owning Provider's terminal receipt."
            ),
            command_id=command_id,
        )
        result = self.gateway.command(
            command_id=command_id,
            tool_id=tool_id,
            at=self.current,
            arguments=arguments,
        )
        self._commands.append(command_id)
        if any(receipt.phase == "failed" for receipt in result.receipts):
            raise AgentFrameworkError(
                f"reference command failed: {result.model_dump(mode='json')}"
            )
        while True:
            receipt = self.gateway.command_status(
                command_id=command_id, at=self.current
            )
            if receipt.phase == "completed":
                return
            if receipt.phase == "failed":
                raise AgentFrameworkError(f"reference command failed: {receipt.detail}")
            self.advance()

    def goto(self, east: float, north: float, up: float) -> None:
        origin = self.context.contract.scenario.frame_authority.origin
        transform = EnuTransform.from_origin(
            longitude_deg=origin.wgs84.longitude_deg,
            latitude_deg=origin.wgs84.latitude_deg,
            altitude_m=origin.wgs84.ellipsoid_height_m,
        )
        longitude, latitude, ellipsoid_height = transform.enu_to_geodetic(
            Vector3(east, north, up)
        )
        self.command(
            "flight.goto",
            {
                "vehicle_id": self.policy.vehicle_id,
                "latitude_deg": latitude,
                "longitude_deg": longitude,
                "altitude_amsl_m": ellipsoid_height - origin.geoid_separation_m,
                "yaw_deg": 0.0,
            },
        )

    def observe(
        self, order: ReferenceWorkOrder
    ) -> tuple[InspectionRgbObservation, str]:
        envelope = self.gateway.observe(
            observation_id=order.observation_id,
            at=self.current,
        )
        self._observations.append(envelope.observation_id)
        observation = InspectionRgbObservation.model_validate(_payload(envelope))
        digest = hashlib.sha256(canonical_json_bytes(_payload(envelope))).hexdigest()
        return observation, digest

    def _navigation_point(
        self, order: ReferenceWorkOrder
    ) -> tuple[float, float, float]:
        scenario = self.context.contract.scenario
        target = next(
            item
            for item in scenario.semantic_targets
            if item.target_id == order.target_id
        )
        sensor = next(
            item
            for item in scenario.sensors
            if item.sensor_id == target.required_sensor_id
        )
        vehicle = next(
            item
            for item in scenario.entities
            if item.entity_id == self.policy.vehicle_id
        )
        if (
            target.surface_normal_target.x,
            target.surface_normal_target.y,
            target.surface_normal_target.z,
        ) != (-1.0, 0.0, 0.0):
            raise AgentFrameworkError(
                "reference policy requires declared west-facing panels"
            )
        mount = sensor.initial_pose.position.enu
        initial = vehicle.initial_pose.position.enu
        position = target.pose.position.enu
        return (
            position.east_m - self.policy.standoff_m - (mount.east_m - initial.east_m),
            position.north_m - (mount.north_m - initial.north_m),
            position.up_m - (mount.up_m - initial.up_m),
        )

    def run(self) -> None:
        launch = next(
            site
            for site in self.context.contract.scenario.launch_sites
            if site.selected
        )
        launch_position = launch.pose.position.enu
        points = [self._navigation_point(order) for order in self.policy.work_orders]
        if any(abs(point[0] - points[0][0]) > 0.05 for point in points):
            raise AgentFrameworkError("reference route requires west-aligned panels")
        vehicle_argument = {"vehicle_id": self.policy.vehicle_id}
        for order in self.policy.work_orders:
            actor = {
                "actor_id": self.context.agent_id,
                "work_order_id": order.work_order_id,
            }
            self.command("business.claim", actor)
            self.command("business.start", actor)
        self.command("flight.arm", vehicle_argument)
        self.command(
            "flight.takeoff",
            {**vehicle_argument, "altitude_m": self.policy.cruise_up_m},
        )
        self.goto(points[0][0], launch_position.north_m, self.policy.cruise_up_m)
        captures: list[tuple[ReferenceWorkOrder, InspectionRgbObservation, str]] = []
        detections: list[dict[str, object]] = []
        for order, (east, north, up) in zip(
            self.policy.work_orders, points, strict=True
        ):
            self.goto(east, north, self.policy.cruise_up_m)
            self.goto(east, north, up)
            self.command("flight.hold", vehicle_argument)
            first, _ = self.observe(order)
            self.wait(self.policy.dwell_s)
            second, observation_digest = self.observe(order)
            if first.frame_id == second.frame_id:
                raise AgentFrameworkError(
                    "reference observations reused a camera frame"
                )
            image = base64.b64decode(second.image_base64, validate=True)
            if hashlib.sha256(image).hexdigest() != second.image_sha256:
                raise AgentFrameworkError("reference observation image digest mismatch")
            captures.append((order, second, observation_digest))
            if panel_has_dark_patch(image):
                detection = DefectDetection(
                    source_artifact_id="artifact.detections",
                    run_id=self.context.run_id,
                    work_order_id=order.work_order_id,
                    observation_id=order.observation_id,
                    frame_id=second.frame_id,
                    image_sha256=second.image_sha256,
                    defect_id=order.defect_id,
                    target_id=order.target_id,
                )
                detections.append(detection.model_dump(mode="json"))
            self.goto(east, north, self.policy.cruise_up_m)
        self.goto(points[-1][0], launch_position.north_m, self.policy.cruise_up_m)
        self.goto(
            launch_position.east_m, launch_position.north_m, self.policy.cruise_up_m
        )
        # The declared constant high-order Wi-Fi mode has inadequate native
        # delivery at the 20 m airborne separation. Land at the operations
        # radio before sending; do not change radio semantics or invent delivery.
        self.command("flight.land", vehicle_argument)
        detection_bytes = canonical_json_bytes(detections)
        self.context.write_artifact("artifact.detections", detection_bytes)
        reports = []
        for order, _frame, observation_digest in captures:
            report = InspectionReportPayload(
                source_artifact_id="artifact.report",
                run_id=self.context.run_id,
                work_order_id=order.work_order_id,
                observation_id=order.observation_id,
                observation_payload_digest=observation_digest,
                detection_payload_digest=hashlib.sha256(detection_bytes).hexdigest(),
                detections=tuple(
                    InspectionReportDetection.model_validate(
                        {
                            key: item[key]
                            for key in (
                                "defect_id",
                                "target_id",
                                "frame_id",
                                "image_sha256",
                            )
                        }
                    )
                    for item in detections
                    if item["work_order_id"] == order.work_order_id
                ),
            )
            reports.append(report.model_dump(mode="json"))
        report_bytes = canonical_json_bytes(reports)
        report_digest = hashlib.sha256(report_bytes).hexdigest()
        self.context.write_artifact("artifact.report", report_bytes)
        for order in self.policy.work_orders:
            self.command(
                "business.submit",
                {
                    "actor_id": self.context.agent_id,
                    "work_order_id": order.work_order_id,
                    "observation_id": order.observation_id,
                    "report_payload_digest": report_digest,
                },
            )
            self.command(
                "network.send",
                {
                    "work_order_id": order.work_order_id,
                    "message_id": self._identity("message"),
                    "source": self.policy.source_endpoint_id,
                    "destination": self.policy.destination_endpoint_id,
                    "payload_base64": base64.b64encode(report_bytes).decode("ascii"),
                    "payload_sha256": report_digest,
                    "traffic_class": "best_effort",
                    "priority": 0,
                    "reliability": "best_effort",
                },
            )
        deadline = self.current.sim_time_ns + int(self.policy.delivery_wait_s * 1e9)
        pending = {order.work_order_id for order in self.policy.work_orders}
        while pending:
            for work_order_id in sorted(pending):
                result = self.gateway.query(
                    query_id=self._identity("query"),
                    query_type="business.work-order",
                    at=self.current,
                    arguments={"work_order_id": work_order_id},
                )
                status = {item.name: item.value for item in result.payload}["status"]
                if status == "completed":
                    pending.remove(work_order_id)
                elif status in {"failed", "cancelled"}:
                    raise AgentFrameworkError(
                        f"reference work order terminated: {status}"
                    )
            if pending:
                if self.current.sim_time_ns >= deadline:
                    raise AgentFrameworkError(
                        "reference reports were not delivered within the declared wait"
                    )
                self.advance()
        self.command("flight.disarm", vehicle_argument)
        self.wait(self.policy.dwell_s)
        decision = self.gateway.complete_turn(
            completion_id=self._identity("finish"),
            at=self.current,
            disposition="finished",
            command_ids=self._commands,
            observation_ids=self._observations,
        )
        if decision.status != "terminated":
            raise AgentFrameworkError("reference participant did not terminate the run")
        print(
            json.dumps(
                {
                    "status": "reference-participant-finished",
                    "run_id": self.context.run_id,
                    "last_tick": self.current.tick,
                    "detection_count": len(detections),
                },
                sort_keys=True,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run",))
    parser.parse_args()
    context = AgentContext.from_environment()
    with context.gateway() as gateway:
        InspectionReferenceParticipant(context, gateway).run()


if __name__ == "__main__":
    main()
