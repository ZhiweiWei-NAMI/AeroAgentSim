"""Received-only centre state and the existing bounded rescue solver."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from aerorescue.companion.transport import NS, save_json
from .assignment_solver import solve_plan


class RescueCentre:
    """No world/vehicle API is available to this object."""

    def __init__(self, data_dir: str | Path) -> None:
        import json
        self.path = Path(data_dir) / "centre-state.json"
        self.state: dict[str, Any] = {"incidents": {}, "queue": [], "active": None, "consumed": []}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())

    def notice_submitted(self, event_id: str, now_ns: int, deadline_ns: int, demand_source: str) -> None:
        incident = self.state["incidents"].setdefault(event_id, {"bids": {}, "reports": [], "receipts": [], "task_revision": 0, "status": "queued"})
        incident.update(notice_anchor_confirmed=True, collect_until_ns=now_ns + 10 * NS, deadline_ns=deadline_ns, demand_source=demand_source)
        if self.state["active"] is None:
            self.state["active"] = event_id
            incident["status"] = "collecting"
        if self.state["active"] == event_id:
            incident["status"] = "collecting"
        elif self.state["active"] != event_id and event_id not in self.state["queue"]:
            self.state["queue"].append(event_id)
        save_json(self.path, self.state)

    def consume(self, envelope: dict[str, Any], now_ns: int) -> None:
        if "received_ns" not in envelope or not envelope.get("native_packet_ids"):
            raise ValueError("centre accepts only radio-committed messages")
        mid, event_id = envelope["message_id"], envelope["event_id"]
        if mid in self.state["consumed"]:
            return
        self.state["consumed"].append(mid)
        data = envelope.get("data", {})
        if event_id not in self.state["incidents"]:
            self.state["incidents"][event_id] = {"bids": {}, "reports": [], "receipts": [], "task_revision": 0, "status": "awaiting_notice", "collect_until_ns": None, "notice_anchor_confirmed": False}
        incident = self.state["incidents"][event_id]
        kind = envelope["kind"]
        if kind == "bid":
            identity = envelope["source"]
            if data.get("uav_id") not in (None, identity):
                raise ValueError("bid identity differs from bound radio sender")
            previous = incident["bids"].get(identity)
            floor = incident.get("resource_versions", {}).get(identity, -1)
            expected = incident.get("bid_tasks", {}).get(identity)
            current_round = not incident.get("bid_tasks") or expected and (envelope["task_id"], envelope["task_revision"]) == (expected["task_id"], expected["task_revision"])
            if current_round and data["commitment_version"] >= floor and (previous is None or (data["commitment_version"], data["observed_at_ns"]) > (previous["commitment_version"], previous["observed_at_ns"])):
                incident["bids"][identity] = {**data, "uav_id": identity, "received_ns": envelope["received_ns"], "message_id": mid, "task_id": envelope["task_id"], "task_revision": envelope["task_revision"]}
        elif kind == "report" and data.get("kind") == "resource_withdrawal":
            identity = envelope["source"]
            versions = incident.setdefault("resource_versions", {})
            if data["commitment_version"] > versions.get(identity, -1):
                versions[identity] = data["commitment_version"]
                incident.setdefault("resource_withdrawals", []).append(envelope)
                bid = incident["bids"].get(identity)
                if bid and bid["commitment_version"] < data["commitment_version"]:
                    del incident["bids"][identity]
                selected = incident.get("last_plan", {}).get("selected_bids", {}).get(identity)
                if incident.get("status") == "executing" and selected and selected["commitment_version"] < data["commitment_version"]:
                    incident["replan_requested"] = {"source": identity, "withdrawal_message_id": mid, "received_ns": envelope["received_ns"], "reason": data.get("reason")}
        elif kind == "report" and data.get("kind") == "delivery_confirmation":
            if envelope["source"] != "drop-receiver-1":
                raise ValueError("receipt transfer confirmation must come from the physical receiver")
            incident.setdefault("delivery_confirmations", []).append(envelope)
        elif kind in {"report", "file"}:
            incident["reports"].append(envelope)
            if kind == "report" and data.get("kind") == "new_incident_clue" and not incident.get("clue_message_id"):
                incident.update(clue_message_id=mid, clue_source=envelope["source"], deadline_ns=data["deadline_ns"], demand_source=data.get("demand_source"), graph=data["graph"], parent_event_id=data["parent_event_id"], status="queued", phase="inspect")
                if self.state["active"] is None:
                    self.state["active"] = event_id
                    incident["status"] = "needs_new_notice"
                elif event_id != self.state["active"] and event_id not in self.state["queue"]:
                    self.state["queue"].append(event_id)
        elif kind == "receipt":
            if envelope["source"] != "drop-receiver-1" or data.get("source") != "gazebo_contact_sensor":
                raise ValueError("delivery receipt must come from the physical receiver")
            incident["receipts"].append(envelope)
        save_json(self.path, self.state)

    def propose_plan(self, event_id: str, graph: dict[str, Any], now_ns: int) -> dict[str, Any]:
        incident = self.state["incidents"][event_id]
        if self.state["active"] != event_id:
            return {"status": "pending", "reason": "incident_queued"}
        if incident.get("collect_until_ns") is None:
            return {"status": "pending", "reason": "new_activation_notice_required"}
        if now_ns < incident["collect_until_ns"]:
            return {"status": "pending", "collect_until_ns": incident["collect_until_ns"]}
        if now_ns >= incident["deadline_ns"]:
            return {"status": "expired", "reason": "incident_deadline"}
        fleet, bids, legs = [], [], {}
        action_specs = {row["id"]: row for row in graph["actions"]}
        rejected = {}
        for identity, summary in incident["bids"].items():
            if summary.get("valid_until_ns", -1) < now_ns:
                rejected[identity] = "expired_bid"
                continue
            if any(summary.get(key) is None for key in ("soc", "payload_kg", "payload_capacity_kg", "position_enu_m", "speed_m_s", "observed_at_ns", "commitment_version", "available_energy_j")):
                rejected[identity] = "unknown_local_state"
                continue
            commitment = summary.get("commitment") or {}
            if commitment.get("protected"):
                rejected[identity] = "protected_commitment"
                continue
            fleet.append({"id": identity, "capabilities": summary["capabilities"], "soc": summary["soc"], "payload_kg": summary["payload_kg"], "payload_capacity_kg": summary["payload_capacity_kg"], "position": summary["position_enu_m"], "speed_m_s": summary["speed_m_s"], "commitment": commitment, "available_energy_j": summary["available_energy_j"]})
            bids.append({"vehicle": identity, "roles": summary["roles"], "available_after_s": summary["available_after_s"]})
            reported_actions = {row["id"]: row for row in summary.get("action_specs", [])}
            matching_actions = {identity for identity, action in action_specs.items() if identity in reported_actions and all(action.get(key) == reported_actions[identity].get(key) for key in ("target", "role", "duration_s"))}
            for leg in summary.get("feasible_leg_costs", []):
                if leg["to_action"] not in matching_actions or leg["from_action"] != "current" and leg["from_action"] not in matching_actions:
                    continue
                if leg.get("valid_until_ns", -1) >= now_ns and leg.get("source") and leg.get("path_enu_m"):
                    legs[(identity, leg["from_action"], leg["to_action"])] = leg
        graph = {**graph, "deadline_s": min(graph.get("deadline_s", math.inf), incident["deadline_ns"] / NS)}
        result = solve_plan(fleet, bids, graph, now_ns / NS, leg_costs=legs)
        incident["task_revision"] += 1
        result.update(event_id=event_id, task_revision=incident["task_revision"], received_bid_ids=[row["message_id"] for row in incident["bids"].values()], excluded=rejected, missing_bid_policy="unknown", planned_at_ns=now_ns)
        result["selected_bids"] = {uid: {key: incident["bids"][uid][key] for key in ("message_id", "commitment_version", "valid_until_ns")} for uid in {row["vehicle"] for row in result.get("assignments", [])}}
        incident["last_plan"] = result
        save_json(self.path, self.state)
        return result

    def delivery_proof(self, event_id: str, delivery: dict[str, Any], *, carrier_id: str, now_ns: int) -> dict[str, Any]:
        incident = self.state["incidents"][event_id]
        plan = incident.get("last_plan", {})
        receipts = [row for row in incident["receipts"] if row["data"].get("package_id") == delivery.get("package_id") and row["task_id"] == delivery.get("task_id") and row["task_revision"] == plan.get("task_revision")]
        if not receipts or not delivery.get("delivery_verified") or delivery.get("task_revision") != plan.get("task_revision"):
            raise ValueError("current plan's physical and radio delivery evidence required")
        # The carrier's local receipt commit does not establish that the receiver
        # knows either transfer completed. Only its subsequent radio notice can
        # carry that fact here. Do not recursively wait for this notice's ACK.
        confirmation = None
        for envelope in incident.get("delivery_confirmations", []):
            if envelope["received_ns"] > now_ns:
                continue  # Inbox can be newer than this tick's preceding state RPC.
            data = envelope["data"]
            if (envelope["task_id"], envelope["task_revision"], data.get("package_id")) != (delivery.get("task_id"), delivery.get("task_revision"), delivery.get("package_id")):
                continue
            transfers = data.get("receipt_transfers", [])
            if len(transfers) != 2:
                continue
            by_destination = {row.get("destination"): row for row in transfers}
            if set(by_destination) != {"centre", carrier_id}:
                continue
            centre_transfer, carrier_transfer = by_destination["centre"], by_destination[carrier_id]
            if carrier_transfer.get("message_id") != delivery.get("receipt_id"):
                continue
            contact = next((row for row in receipts if row["message_id"] == centre_transfer.get("message_id") and row["data"].get("receipt_id") == data.get("receipt_id")), None)
            if contact is None:
                continue
            acknowledgements = [(centre_transfer.get("ack_received_ns"), contact["received_ns"]), (carrier_transfer.get("ack_received_ns"), delivery.get("receipt_received_ns"))]
            if any(not isinstance(ack, int) or not isinstance(received, int) or not received <= ack <= envelope["received_ns"] for ack, received in acknowledgements):
                continue
            confirmation = envelope
            break
        if confirmation is None:
            raise ValueError("receiver's radio confirmation of both final ACKs is still required")
        return {**delivery, "receipt_confirmation_message_id": confirmation["message_id"], "receipt_confirmation_received_ns": confirmation["received_ns"]}

    def verified_delivery(self, event_id: str, delivery: dict[str, Any], *, carrier_id: str, now_ns: int) -> None:
        self.state["incidents"][event_id]["verified_delivery"] = self.delivery_proof(event_id, delivery, carrier_id=carrier_id, now_ns=now_ns)
        save_json(self.path, self.state)

    def finish(self, event_id: str, outcome: str, now_ns: int) -> dict[str, Any]:
        allowed = {"completed_with_delivery", "rejected_no_target", "unresolved_evidence", "unfulfilled_no_resource", "expired", "failed_execution"}
        if outcome not in allowed:
            raise ValueError("unsupported rescue outcome")
        incident = self.state["incidents"][event_id]
        if outcome == "completed_with_delivery" and (not incident.get("verified_delivery", {}).get("receipt_confirmation_message_id") or not incident["demand_source"]):
            raise ValueError("completion needs receiver-confirmed receipt transfers and an explicit demand source")
        result = {"event_id": event_id, "outcome": outcome, "finished_ns": now_ns}
        incident.update(status=outcome, outcome=result)
        if event_id in self.state["queue"]:
            self.state["queue"].remove(event_id)
        if self.state["active"] == event_id:
            self.state["active"] = self.state["queue"].pop(0) if self.state["queue"] else None
            if self.state["active"] is not None:
                upcoming = self.state["incidents"][self.state["active"]]
                upcoming.update(status="needs_new_notice", bids={}, collect_until_ns=None)
        save_json(self.path, self.state)
        return result
