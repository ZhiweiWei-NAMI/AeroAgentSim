"""The same installed business functions for rule/model decisions and the CLI."""
from __future__ import annotations

import base64
import json
import math
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .planning import PublicMapPlanner

from .payload import PayloadDelivery, select_drop_site
from .transport import NS, encoded, save_json

TOOL_DESCRIPTIONS = {
    "search-area": "Fly a public-area coverage route and collect actual new frames at reached viewpoints.",
    "detect-persons": "Run the installed ONNX person detector on a local frame.",
    "track-persons": "Associate anonymous detections across actual frames.",
    "locate-target": "Back-project valid registered depth with same-time PX4 estimated pose.",
    "verify-target": "Compare independently acquired, geometrically different observations.",
    "compare-observations": "Compare old and new local evidence without treating occlusion as departure.",
    "retrieve-sightings": "Search this UAV's persistent history or reanalyze an actual old frame.",
    "package-evidence": "Encode a traceable image crop within a byte budget.",
    "read-evidence": "Read a local artifact or radio-delivered copy.",
    "estimate-assignment": "Estimate feasible current and predecessor-target route legs from local state.",
    "offer-rescue-role": "Send a local capability and cost bid through radio.",
    "probe-route": "Submit actual radio probes and return pending while the world advances.",
    "send-evidence": "Transfer real file bytes or structured report through damaged radio.",
    "hold-relay": "Fly a public candidate, probe the path, and retain the relay commitment.",
    "handover-role": "Send the current role and outstanding obligations to a replacement through radio.",
    "select-drop-site": "Choose a point inside the public receiving polygon away from uncertain people locations.",
    "deliver-supply": "Fly to a safe selected point, then command the physical release actuator.",
    "verify-delivery": "Require observed detachment, a new frame and an actual radio contact receipt.",
    "flight-command": "Submit an arm/takeoff/goto/hold/land primitive and yield until its actual operation completes.",
    "reobserve-target": "Move to a reachable geometrically different viewpoint and capture a new frame.",
    "resume-or-return": "Resume explicitly saved task obligations or fly a public return route and land.",
}

# A single JSON request field preserves rich nested perception records without
# maintaining separate CLI, model and HTTP versions of the business schemas.
def tool_schemas() -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": name.replace("-", "_"), "description": description, "parameters": {"type": "object", "properties": {"request_json": {"type": "string", "description": "JSON object for this tool; see the installed SKILL.md input fields."}}, "required": ["request_json"], "additionalProperties": False}, "strict": True}} for name, description in TOOL_DESCRIPTIONS.items()]


class CompanionTools:
    def __init__(self, root: str | Path, run_id: str, uav_id: str, flight: Any, config: dict[str, Any], submit_mission: Callable[[str, list[dict[str, Any]], dict[str, Any]], dict[str, Any]]) -> None:
        self.root, self.run_id, self.uav_id = Path(root), run_id, uav_id
        self.flight, self.config, self.submit_mission = flight, config, submit_mission
        self.context: dict[str, Any] = {}
        self.payload = PayloadDelivery(root, execution_backend=config.get("execution_backend", "native"))
        self.perception: Any = None
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "search-area": self.search_area, "estimate-assignment": self.estimate_assignment,
            "offer-rescue-role": self.offer_role,
            "probe-route": self.probe_route, "send-evidence": self.send_evidence,
            "hold-relay": self.hold_relay, "handover-role": lambda r: self.send_message("handover", r),
            "select-drop-site": self.select_drop_site, "deliver-supply": self.deliver_supply,
            "verify-delivery": self.verify_delivery, "resume-or-return": self.resume_or_return,
            "reobserve-target": self.reobserve_target,
            "flight-command": self.flight_command,
        }

    def perception_tools(self) -> Any:
        if self.perception is None:
            from .evidence import PerceptionTools
            self.perception = PerceptionTools(self.root, self.run_id, self.uav_id, self.config.get("model_path") or os.environ.get("DETECTOR_MODEL_PATH"), self.config.get("embedding_path") or os.environ.get("EMBEDDING_MODEL_PATH"), engineering_horizontal_margin_m=self.config.get("perception", {}).get("engineering_horizontal_margin_m"), engineering_margin_basis=self.config.get("perception", {}).get("engineering_margin_basis"))
            self.handlers.update(self.perception.handlers())
        return self.perception

    def call(self, name: str, request: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        name = name.replace("_", "-")
        started = time.monotonic()
        self.context = context
        request_id = uuid.uuid4().hex
        try:
            if (context.get("commitment") or {}).get("protected") and name in {"search-area", "hold-relay", "deliver-supply", "reobserve-target"}:
                raise ValueError("protected original commitment cannot be replaced by a rescue mission")
            if name not in self.handlers:
                self.perception_tools()
            if name == "detect-persons" or name == "retrieve-sightings" and request.get("reanalyze_frame_id"):
                request = {**request, "analyzed_at": context["sim_ns"]}
            output = self.handlers[name](request)
        except Exception as exc:
            output = {"status": "unavailable", "reason": str(exc)}
        result = {"tool_call_id": request_id, "tool_name": name, "uav_id": self.uav_id, "run_id": self.run_id, "task_id": context.get("task_id"), "task_revision": context.get("task_revision"), "observed_at": context["sim_ns"], "execution_backend": self.config.get("execution_backend", "native"), **output}
        log = self.root / "logs" / "tool-trace.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as stream:
            stream.write(encoded({"request": request, "result": result, "wall_elapsed_s": time.monotonic() - started}) + b"\n")
        return result

    def _route(self, start: list[float], goal: list[float]) -> list[list[float]]:
        result = PublicMapPlanner(self.config.get("public_map", {})).route(start, goal)
        if result["status"] != "succeeded":
            raise ValueError(result["reason"])
        return result["path_enu_m"][1:]

    def _position(self) -> list[float]:
        state = self.flight.state()
        estimate = state.get("vehicle_estimate", {})
        if estimate.get("status") not in {"available", "valid"} or estimate.get("position_enu_m") is None:
            raise ValueError("PX4 estimated position unavailable")
        return estimate["position_enu_m"]

    def flight_command(self, request: dict[str, Any]) -> dict[str, Any]:
        action, params = request["action"], request.get("params", {})
        if action == "goto":
            route = self._route(self._position(), params["position_enu_m"])
            return self.submit_mission("flight-command", [{"action": "goto", "params": {"position_enu_m": point, "yaw_deg": params.get("yaw_deg", 0)}} for point in route], {"route_source": "public-map corridor_astar"})
        return self.flight.submit(action, params, self.context["task_id"], self.context["task_revision"])

    def search_area(self, request: dict[str, Any]) -> dict[str, Any]:
        plan = PublicMapPlanner(self.config.get("public_map", {})).search(self._position(), request["polygon"], request["altitude_m"], request["footprint_width_m"] * 0.7)
        if plan["status"] != "succeeded":
            return plan
        steps = []
        for point in plan["path_enu_m"][1:]:
            steps.append({"action": "goto", "params": {"position_enu_m": point, "yaw_deg": 0}})
            steps.append({"action": "capture", "params": {}})
        return self.submit_mission("search-area", steps, plan)

    def reobserve_target(self, request: dict[str, Any]) -> dict[str, Any]:
        frame = self.perception_tools().store.observation(request["frame_id"])
        location = self.perception_tools().locate(request)
        if location["status"] != "available":
            return location
        calibration = frame["calibration"]
        k = calibration["K"]
        width, height = calibration["width"], calibration["height"]
        camera = {"orientation": calibration["orientation"], "horizontal_fov_deg": math.degrees(2 * math.atan(width / (2 * k[0]))), "vertical_fov_deg": math.degrees(2 * math.atan(height / (2 * k[4]))), "depth_max_m": calibration["depth_max_m"]}
        previous = frame["vehicle_estimate"]["position_enu_m"]
        target = location["position_enu_m"]
        result = PublicMapPlanner(self.config.get("public_map", {})).viewpoints(self._position(), target, radius_m=12, altitude_m=self.config["public_map"]["search_altitude_m"], previous_camera=previous, camera=camera)
        if result["status"] != "succeeded":
            return result
        steps = [{"action": "goto", "params": {"position_enu_m": point, "yaw_deg": 0}} for point in result["candidates"][0]["path_enu_m"][1:]]
        steps.extend([{"action": "hold", "params": {}}, {"action": "capture", "params": {}}])
        return self.submit_mission("reobserve-target", steps, {"first_frame_id": frame["frame_id"], "candidate": result["candidates"][0]})

    def _read_native(self, op: str, **params: Any) -> dict[str, Any]:
        reply = self.flight.request(op, **params)
        request_id = reply.get("request_id")
        until = time.monotonic() + 10
        while reply.get("status") == "pending" and "uav_id" not in reply:
            if time.monotonic() >= until:
                raise TimeoutError("native management query timed out")
            time.sleep(0.02)
            reply = self.flight.request("result", pending_request_id=request_id)
        return reply

    def estimate_assignment(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.flight.state()
        profile = self.config.get("vehicles", {}).get(self.uav_id, {})
        estimate = state.get("vehicle_estimate", {})
        position = estimate.get("position_enu_m")
        now = self.context["sim_ns"]
        speed = profile.get("speed_m_s")
        energy = profile.get("energy_per_m_j")
        local = {"position_enu_m": position, "valid_until_ns": estimate.get("source_sim_ns", -20 * NS) + 15 * NS, "observed_at_ns": estimate.get("source_sim_ns"), "speed_m_s": speed, "energy_per_m_j": energy, "hover_power_w": profile.get("hover_power_w"), "hover_power_source": profile.get("hover_power_source"), "landed_state": state.get("landed_state"), "energy_model_source": profile.get("energy_model"), "commitment_version": self.context.get("commitment_version", 0)}
        estimate_report = PublicMapPlanner(self.config.get("public_map", {})).estimate_legs(local, request["actions"], now)
        legs = estimate_report.get("feasible_leg_costs", [])
        battery = state.get("battery", {})
        px4_soc = battery.get("remaining_fraction") if isinstance(battery, dict) else None
        energy_state = self.context.get("local_energy")
        if energy_state is None and profile.get("energy_budget") is not None:
            energy_state = {"status": "unavailable", "source": "declared_onboard_energy_model", "reason": "local ledger observation is missing"}
        if energy_state:
            soc = energy_state.get("effective_fraction") if energy_state["status"] == "available" else None
            available_energy = energy_state.get("available_energy_j") if energy_state["status"] == "available" else None
        else:
            soc = px4_soc
            available_energy = soc * profile["capacity_j"] if soc is not None and profile.get("capacity_j") is not None else None
        commitment = self.context.get("commitment") or {}
        roles = [] if commitment.get("protected") else list(profile.get("capabilities", []))
        payload = self.context.get("payload_state") or {"mass_kg": profile.get("initial_payload_kg"), "source": "explicit scenario initial inventory" if profile.get("initial_payload_kg") is not None else None}
        configured_payload = self.config.get("payload", {})
        if configured_payload.get("carrier") == self.uav_id:
            measured = self._read_native("payload_status", package_id=configured_payload["package_id"])
            payload = {"mass_kg": configured_payload.get("package_mass_kg", 1.0) if measured["status"] == "attached" else 0.0 if measured["status"] == "detached" else None, "source": measured.get("source") if measured["status"] in {"attached", "detached"} else None}

        return {"status": "succeeded", "uav_id": self.uav_id, "observed_at_ns": local["observed_at_ns"], "valid_until_ns": local["valid_until_ns"], "position_enu_m": position, "capabilities": profile.get("capabilities", []), "roles": roles, "soc": soc, "soc_source": energy_state.get("source") if energy_state else battery.get("source", "mock battery model" if self.config.get("execution_backend") == "mock" else "PX4 battery telemetry"), "px4_soc": px4_soc if self.config.get("execution_backend") != "mock" else None, "local_energy": energy_state, "payload_kg": payload.get("mass_kg"), "payload_source": payload.get("source"), "payload_capacity_kg": profile.get("payload_capacity_kg"), "available_energy_j": available_energy, "speed_m_s": speed, "commitment": commitment, "commitment_version": self.context.get("commitment_version", 0), "available_after_s": max(now / NS, commitment.get("busy_until_s", now / NS)), "feasible_leg_costs": legs, "action_specs": request["actions"]}

    def _message_fields(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"destination": request.get("destination", "centre"), "event_id": self.context["event_id"], "task_id": self.context["task_id"], "task_revision": self.context["task_revision"], "deadline_ns": self.context["deadline_ns"], "route": request.get("route")}

    def offer_role(self, request: dict[str, Any]) -> dict[str, Any]:
        # Only the immediately generated local estimate is eligible for a bid.
        estimate = self.context.get("last_assignment_estimate")
        if not estimate:
            raise ValueError("estimate-assignment must run before offering a role")
        if estimate["commitment_version"] != self.context["commitment_version"] or estimate["valid_until_ns"] < self.context["sim_ns"]:
            raise ValueError("local resource estimate expired or its commitment changed; estimate again before offering")
        return self.send_message("bid", {**request, "data": estimate})

    def send_message(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        return self.flight.request("radio_send", kind=kind, data=request.get("data", request), **self._message_fields(request))

    def send_evidence(self, request: dict[str, Any]) -> dict[str, Any]:
        if "artifact_id" not in request:
            report = dict(request.get("data", {}))
            if report.get("kind") in {"resource_withdrawal", "assignment_canceled", "delivery_confirmation", "cancel_assignment"}:
                raise ValueError("resource and cancellation protocol reports are emitted only from recorded local Harness events")
            if report.get("kind") == "new_incident_clue":
                return self.report_secondary_clue(request)
            if report.get("observation_verdict") == "confirmed":
                perception = self.perception_tools()
                records = {}
                for name in ("first", "second"):
                    reference = report[name]
                    frame = perception.store.observation(reference["frame_id"])
                    location = perception.locate({"frame_id": frame["frame_id"], "detection_id": reference["detection_id"]})
                    records[name] = {"frame_id": frame["frame_id"], "detection_id": reference["detection_id"], "location": location, "observed_at": frame["observed_at"]}
                verification = perception.verify({name + suffix: records[name][field] for name in ("first", "second") for suffix, field in (("_frame_id", "frame_id"), ("_detection_id", "detection_id"))})
                if verification.get("status") != "confirmed":
                    raise ValueError("confirmed report requires two actual local observations that pass verification")
                mid = (report.get("evidence_transfer") or {}).get("message_id", "")
                if not mid.replace("-", "").isalnum():
                    raise ValueError("confirmed report needs a completed local evidence transfer reference")
                transfer = json.loads((self.root / "outbox" / (mid + ".json")).read_text())
                if (transfer["task_id"], transfer["task_revision"]) != (self.context["task_id"], self.context["task_revision"]) or not transfer["request"].get("artifact_id") or transfer["result"]["status"] != "succeeded":
                    raise ValueError("evidence transfer must belong to this accepted task and have a real final ACK")
                artifact_id = transfer["request"]["artifact_id"]
                _, artifact_metadata = perception.store.read(artifact_id)
                linked = False
                for reference in records.values():
                    frame = perception.store.observation(reference["frame_id"])
                    if artifact_id == frame["rgb_artifact_id"]:
                        linked = True
                    if artifact_metadata.get("source_artifact_id") == frame["rgb_artifact_id"]:
                        matches = [row for row in frame.get("last_analysis", {}).get("detections", []) if row["detection_id"] == reference["detection_id"]]
                        crop = artifact_metadata.get("crop_xyxy", [])
                        if matches and len(crop) == 4:
                            bbox = matches[0]["bbox_xyxy"]
                            # Packaging quantizes pixel coordinates to integer edges.
                            linked = linked or crop[0] <= int(bbox[0]) and crop[1] <= int(bbox[1]) and crop[2] >= int(bbox[2]) and crop[3] >= int(bbox[3])
                if not linked:
                    raise ValueError("transmitted image must contain one of the verified detections, with its real source-frame provenance")
                site = self.select_drop_site({"frame_id": records["second"]["frame_id"], "detection_id": records["second"]["detection_id"], "observation_refs": [{"frame_id": row["frame_id"], "detection_id": row["detection_id"]} for row in records.values()]})
                report = {"observation_verdict": "confirmed", **records, "verification": verification, "evidence_transfer": transfer["result"], "drop_site": site, "observed_at_ns": records["second"]["observed_at"]}
            elif report.get("kind") == "relay_ready":
                proofs = []
                for row in report.get("probe_results", []):
                    measured = self._read_native("radio_status", message_id=row["result"]["message_id"])
                    if measured["status"] != "succeeded" or not measured.get("probe_only") or measured.get("source") != self.uav_id or self.context["sim_ns"] - measured.get("ack_received_ns", -10 * NS) > 5 * NS:
                        raise ValueError("relay readiness requires recent actual source-bound probe round trips")
                    proofs.append(measured)
                current_ns = self.flight.state()["sim_ns"]
                if any(current_ns - proof["ack_received_ns"] > 5 * NS for proof in proofs):
                    raise ValueError("one endpoint probe expired while collecting readiness; probe again")
                required = {"centre", *[uid for uid, profile in self.config["vehicles"].items() if "inspect" in profile.get("capabilities", []) and uid != self.uav_id]}
                if not required.issubset({proof["destination"] for proof in proofs}):
                    raise ValueError("relay readiness needs all configured endpoint probes")
                report = {"kind": "relay_ready", "probe_results": proofs, "observed_at_ns": min(proof["ack_received_ns"] for proof in proofs), "valid_until_ns": min(proof["ack_received_ns"] for proof in proofs) + 5 * NS, "held_until": "ack_cancel_or_handover"}
            elif report.get("kind") == "delivery_verified":
                package_id = report.get("delivery", {}).get("package_id")
                actual = self.payload.status(package_id, self.context["sim_ns"])
                if not actual.get("delivery_verified") or (actual["task_id"], actual["task_revision"]) != (self.context["task_id"], self.context["task_revision"]):
                    raise ValueError("delivery report requires this task's recorded physical, frame and radio evidence")
                report = {"kind": "delivery_verified", "delivery": actual}
            return self.send_message("report", {**request, "data": report})
        content, metadata = self.perception_tools().store.read(request["artifact_id"])
        return self.flight.request("radio_send", kind="file", content_base64=base64.b64encode(content).decode(), metadata=metadata, **self._message_fields(request))

    def report_secondary_clue(self, request: dict[str, Any]) -> dict[str, Any]:
        """A new event starts from archive references, never supplied positions."""
        from .transport import digest
        data = request["data"]
        perception = self.perception_tools()
        first = perception.store.observation(data["reference_frame_id"])
        second = perception.store.observation(data["frame_id"])
        if first["frame_id"] == second["frame_id"] or second["observed_at"] <= first["observed_at"]:
            raise ValueError("a secondary clue needs a genuinely newer captured frame")
        previous = perception.locate({"frame_id": first["frame_id"], "detection_id": data["reference_detection_id"]})
        located = perception.locate({"frame_id": second["frame_id"], "detection_id": data["detection_id"]})
        separation = self.config.get("incident", {}).get("secondary_clue_separation_m", 15.0)
        if previous.get("status") != "available" or located.get("status") != "available" or math.dist(previous["position_enu_m"][:2], located["position_enu_m"][:2]) < separation:
            raise ValueError("secondary clue has no distinct, actually located spatial candidate")
        parent = self.context["event_id"]
        event_id = "clue-" + digest(encoded([self.run_id, self.uav_id, parent]))[:20]
        ledger = self.root / "tasks" / (event_id + ".json")
        if ledger.exists():
            return json.loads(ledger.read_text())["submission"]
        ttl = int(self.config.get("incident", {}).get("secondary_clue_ttl_ns", 120 * NS))
        deadline = min(self.context["deadline_ns"], self.context["sim_ns"] + ttl)
        if deadline <= self.context["sim_ns"]:
            raise ValueError("secondary observation is already beyond its declared clue deadline")
        point = list(located["position_enu_m"])
        point[2] = self.config["public_map"]["search_altitude_m"]
        clue = {"kind": "new_incident_clue", "parent_event_id": parent, "frame_id": second["frame_id"], "detection_id": data["detection_id"], "observed_at_ns": second["observed_at"], "discovered_at_ns": self.context["sim_ns"], "location": located, "observation_verdict": "unverified_clue", "demand_source": self.config.get("incident", {}).get("secondary_demand_source"), "deadline_ns": deadline, "graph": {"actions": [{"id": "inspect-clue", "role": "inspect", "target": point, "duration_s": 30, "after": []}], "payload_kg": 1.0}}
        fields = {**self._message_fields(request), "event_id": event_id, "task_id": event_id + "-clue", "task_revision": 1, "deadline_ns": deadline}
        reply = self.flight.request("radio_send", kind="report", data=clue, **fields)
        save_json(ledger, {"event_id": event_id, "clue": clue, "submission": reply})
        return reply

    def probe_route(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.flight.request("probe_route", destination=request["destination"], route=request.get("route"), deadline_ns=self.context["deadline_ns"])

    def hold_relay(self, request: dict[str, Any]) -> dict[str, Any]:
        if not request.get("candidates"):
            return {"status": "unavailable", "reason": "public_candidates_required"}
        steps, previous = [], self._position()
        peers = request["peers"]
        if not peers or self.uav_id in peers:
            raise ValueError("relay probe peers must name other endpoints")
        for candidate_index, point in enumerate(request["candidates"]):
            steps.extend({"action": "goto", "params": {"position_enu_m": p, "yaw_deg": 0}, "candidate_index": candidate_index} for p in self._route(previous, point))
            steps.append({"action": "hold", "params": {}, "candidate_index": candidate_index})
            for peer in peers:
                steps.append({"action": "probe", "params": {"destination": peer, "route": [self.uav_id, peer]}, "candidate_index": candidate_index})
            previous = point
        return self.submit_mission("hold-relay", steps, {"required_peers": peers, "hold_until_ack_for": request.get("message_ids", []), "exit_requires": "end-to-end ACK, cancellation or accepted handover"})

    def select_drop_site(self, request: dict[str, Any]) -> dict[str, Any]:
        public = self.config.get("public_map", {})
        if request.get("plan_message_id"):
            identity = request["plan_message_id"]
            if not identity.replace("-", "").isalnum():
                raise ValueError("invalid plan reference")
            envelope = json.loads((self.root / "inbox" / identity / "message.json").read_text())
            if envelope["kind"] != "plan" or envelope["task_id"] != self.context["task_id"] or envelope["task_revision"] != self.context["task_revision"]:
                raise ValueError("drop selection requires this accepted radio plan")
            report = envelope["data"]["target_report"]
            if report["observation_verdict"] != "confirmed" or self.context["sim_ns"] - report["observed_at_ns"] > 120 * NS:
                raise ValueError("received target evidence is unconfirmed or expired")
            locations = report["drop_site"].get("target_locations")
            if not locations:
                raise ValueError("received report lacks the full observed-person exclusion set")
            source_frame_id = report["second"]["frame_id"]
            source_detection_id = report["second"]["detection_id"]
        else:
            if "frame_id" not in request or "detection_id" not in request:
                raise ValueError("select-drop-site requires a local frame/detection reference")
            locations = []
            references = request.get("observation_refs") or [{"frame_id": request["frame_id"], "detection_id": request["detection_id"]}]
            for reference in references:
                frame = self.perception_tools().store.observation(reference["frame_id"])
                detections = frame.get("last_analysis", {}).get("detections", [])
                if reference["detection_id"] not in {row["detection_id"] for row in detections}:
                    raise ValueError("selected person is absent from the recorded frame inference")
                locations.extend(self.perception_tools().locate({"frame_id": frame["frame_id"], "detection_id": row["detection_id"]}) for row in detections)
            source_frame_id, source_detection_id = request["frame_id"], request["detection_id"]
        result = select_drop_site(public["receiver_polygon"], locations, height_m=public["release_altitude_m"], edge_margin_m=public.get("drop_edge_margin_m", 1.0))
        result["target_locations"] = locations
        result["source_frame_id"] = source_frame_id
        result["source_detection_id"] = source_detection_id
        if result["status"] == "succeeded":
            identity = uuid.uuid4().hex
            result["drop_site_id"] = identity
            save_json(self.root / "tasks" / ("drop-site-" + identity + ".json"), result)
        return result

    def deliver_supply(self, request: dict[str, Any]) -> dict[str, Any]:
        identity = request["drop_site_id"]
        if not identity.isalnum():
            raise ValueError("invalid drop site reference")
        site = json.loads((self.root / "tasks" / ("drop-site-" + identity + ".json")).read_text())
        point = site["position_enu_m"]
        if site.get("status") != "succeeded":
            raise ValueError("safe selected drop site required")
        if request["package_id"] in self.payload.packages:
            return {"status": "pending", "reason": "reconcile_existing_release", **self.payload.status(request["package_id"], self.context["sim_ns"])}
        steps = [{"action": "goto", "params": {"position_enu_m": p, "yaw_deg": 0}} for p in self._route(self._position(), point)]
        steps.extend([{"action": "hold", "params": {}}, {"action": "release", "params": {"package_id": request["package_id"]}}, {"action": "capture", "params": {"after_sim_ns": self.context["sim_ns"]}}])
        return self.submit_mission("deliver-supply", steps, {"package_id": request["package_id"], "drop_site": site})

    def verify_delivery(self, request: dict[str, Any]) -> dict[str, Any]:
        package_id = request["package_id"]
        if request.get("receipt_message_id"):
            identity = request["receipt_message_id"]
            if not identity.replace("-", "").isalnum():
                raise ValueError("invalid receipt reference")
            receipt = json.loads((self.root / "inbox" / identity / "message.json").read_text())
            if receipt.get("data", {}).get("package_id") != package_id:
                raise ValueError("receipt belongs to another package")
            self.payload.receipt(receipt)
        if not request.get("post_drop_frame_id"):
            if not self.payload.packages[package_id].get("receipt_delivered"):
                return {"status": "pending", "reason": "actual contact receipt required before settled observation"}
            return self.submit_mission("verify-delivery", [{"action": "capture", "params": {}}], {"package_id": package_id, "observation_purpose": "post-contact receiving site, not automatic package recognition"})
        if request.get("post_drop_frame_id"):
            frame = self.perception_tools().store.observation(request["post_drop_frame_id"])
            package = self.payload.packages[package_id]
            site = package.get("drop_site")
            if not site or not frame.get("depth_artifact_id"):
                raise ValueError("post-drop image needs its selected site and numerical depth")
            import io
            import numpy as np
            from .geometry import site_visible_in_frame
            depth_bytes, _ = self.perception_tools().store.read(frame["depth_artifact_id"])
            position = list(site["position_enu_m"])
            physical = self.config["payload"]
            position[2] = physical["receiver_pose"][2] + physical["receiver_size"][2] / 2
            visibility = site_visible_in_frame(frame, np.load(io.BytesIO(depth_bytes), allow_pickle=False), position)
            if not visibility.get("visible"):
                return {"status": "unavailable", "reason": "selected receiving site is not visible in the actual new frame", "site_visibility": visibility}
            self.payload.observe(package_id, frame["frame_id"], frame["observed_at"])
        return self.payload.status(package_id, self.context["sim_ns"])

    def resume_or_return(self, request: dict[str, Any]) -> dict[str, Any]:
        state = self.flight.state()
        if state.get("landed_state") in {"ON_GROUND", "on_ground"}:
            return {"status": "succeeded", "disposition": "already_on_ground", "returned_to_base": False, "reason": "No airborne return action is needed; this does not assert base arrival"}
        if self.context.get("relay_held") and not self.context.get("relay_release_authorized"):
            raise ValueError("relay obligation has not been released")
        points = request.get("remaining_route") or [request["return_position_enu_m"]]
        steps, previous = [], self._position()
        for point in points:
            steps.extend({"action": "goto", "params": {"position_enu_m": p, "yaw_deg": 0}} for p in self._route(previous, point))
            previous = point
        if not request.get("remaining_route"):
            steps.extend([{"action": "land", "params": {}}, {"action": "disarm", "params": {}}])
        return self.submit_mission("resume-or-return", steps, {"remaining_obligations": request.get("remaining_route", [])})


def main() -> None:
    """Queue a tool for the existing local worker; never create another owner."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", nargs="?", choices=sorted(TOOL_DESCRIPTIONS))
    parser.add_argument("--request", default="{}", help="JSON object, or @path to a JSON file")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "/data"))
    parser.add_argument("--status", help="Read a previously queued local request")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--control", choices=["accept-task", "apply-radio-control", "withdraw-resources", "centre-solve", "centre-publish", "centre-notice", "centre-cancel", "centre-finish", "wait-until"])
    parser.add_argument("--result-key", help="Cordis rule continuation key; omit for diagnostic calls")
    parser.add_argument("--continue-on-unavailable", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--task-revision", type=int)
    args = parser.parse_args()
    if args.list:
        print(json.dumps(tool_schemas(), ensure_ascii=False))
        return
    root = Path(args.data_dir) / "tasks" / "tool-requests"
    root.mkdir(parents=True, exist_ok=True)
    if args.status:
        if not args.status.isalnum():
            raise ValueError("invalid request ID")
        result = root / (args.status + ".result.json")
        print(result.read_text() if result.exists() else json.dumps({"request_id": args.status, "status": "pending"}))
        return
    if bool(args.tool) == bool(args.control):
        parser.error("provide exactly one tool or --control")
    request = json.loads(Path(args.request[1:]).read_text() if args.request.startswith("@") else args.request)
    request_id = uuid.uuid4().hex
    call = {"request_id": request_id, "request": request, **({"tool": args.tool} if args.tool else {"control": args.control})}
    if args.result_key:
        call.update(result_key=args.result_key, continue_on_unavailable=args.continue_on_unavailable)
    if args.task_id is not None:
        call.update(task_id=args.task_id, task_revision=args.task_revision)
    save_json(root / (request_id + ".queued.json"), call)
    print(json.dumps({"request_id": request_id, "status": "accepted", "worker_required": True}))


if __name__ == "__main__":
    main()
