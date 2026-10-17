"""Local execution service for the Cordis rescue Harness.

No simulator reset/advance capability lives here. Future flight, image and radio
results end the current decision; the private coordinator continues advancing.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import copy
import json
import os
import signal
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from aerorescue.resources import scenario_path

from .centre import RescueCentre
from .flight import FlightClient
from .tools import CompanionTools
from .transport import NS, encoded, save_json

TERMINAL = {"succeeded", "failed", "canceled", "unavailable", "rejected"}


class CompanionRuntime:
    def __init__(self, config: dict[str, Any], *, run_id: str, uav_id: str, data_dir: str | Path, socket_path: str, agent_mode: str = "rule", role: str = "uav", provider_profile: str = "default", execution_only: bool = True) -> None:
        if agent_mode not in {"rule", "model"}:
            raise ValueError("agent mode must be rule or model")
        self.config, self.run_id, self.uav_id, self.mode, self.role = config, run_id, uav_id, agent_mode, role
        self.root = Path(data_dir)
        for folder in ("inbox", "outbox", "tasks", "logs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.path = self.root / "runtime-state.json"
        self.state: dict[str, Any] = {"run_id": run_id, "uav_id": uav_id, "task": None, "missions": {}, "seen_messages": [], "step_index": 0, "results": {}, "model_client_constructions": 0, "model_calls": 0, "commitment_version": 0, "sim_ns": 0}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            if self.state["run_id"] != run_id or self.state["uav_id"] != uav_id:
                raise ValueError("runtime data directory belongs to another identity/run")
            for mission in self.state["missions"].values():
                if mission["status"] not in TERMINAL:
                    mission["reconcile_required"] = True
        self.flight = FlightClient(socket_path, run_id)
        self.tools = CompanionTools(self.root, run_id, uav_id, self.flight, config, self.submit_mission)
        self.centre = RescueCentre(self.root, execution_backend=config.get("execution_backend", "native")) if role == "centre" else None
        from .energy import LocalEnergyLedger
        self.energy = LocalEnergyLedger(self.root, config.get("vehicles", {}).get(uav_id, {}), run_id=run_id, uav_id=uav_id, execution_backend=config.get("execution_backend", "native")) if role == "uav" else None
        # Cordis owns every policy/model turn. This process never builds a provider.
        if not execution_only:
            raise ValueError("The application worker is execution-only; select rule/model in Cordis")
        self.provider = None
        self.execution_only = True
        self.events: list[dict[str, Any]] = []
        self.published_version = -1
        self.proposal_continuation: dict[str, Any] = {}
        self.last_proposal: dict[str, Any] | None = None
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"companion-{uav_id}")
        self.future: concurrent.futures.Future[Any] | None = None
        self.future_kind = ""
        self.future_revision: tuple[str, int] | None = None
        self.future_call: dict[str, Any] | None = None
        self.active_tool: dict[str, Any] | None = self.state.get("active_tool")
        if self.state.get("tool_inflight") and self.active_tool is None:
            self.state["task_blocker"] = {"status": "reconcile_required", "reason": "Interrupted tool submission has no acknowledged operation; do not replay physical actions or radio sends"}
        self.capture_futures: dict[str, concurrent.futures.Future[Any]] = {}
        self.closed = False
        self._save()
        associated = {op["call"]["request_id"] for op in self.state.get("control_operations", {}).values()}
        if self.active_tool:
            associated.add(self.active_tool["call"].get("local_request_id"))
        for running in (self.root / "tasks" / "tool-requests").glob("*.running.json"):
            request = json.loads(running.read_text())
            result_path = running.with_name(running.name.replace(".running.json", ".result.json"))
            if not result_path.exists() and request["request_id"] not in associated:
                self._queue_result(request, {"status": "failed", "reason": "reconcile_required: interrupted submission has no acknowledged operation; action was not replayed"})

    def _save(self) -> None:
        if hasattr(self, "active_tool"):
            self.state["active_tool"] = self.active_tool
        save_json(self.path, self.state)

    def _decision_event(self) -> None:
        self.state["decision_event_version"] = self.state.get("decision_event_version", 0) + 1

    def _log(self, filename: str, data: dict[str, Any]) -> None:
        with (self.root / "logs" / filename).open("ab") as stream:
            stream.write(encoded({"run_id": self.run_id, "uav_id": self.uav_id, "sim_ns": self.state["sim_ns"], "wall_ns": time.time_ns(), **data}) + b"\n")

    def context(self) -> dict[str, Any]:
        task = self.state.get("task") or {}
        relays = [m for m in self.state["missions"].values() if m["tool_name"] == "hold-relay" and m["status"] == "succeeded" and (m["task_id"], m["task_revision"]) == (task.get("task_id"), task.get("task_revision"))]
        relay = {k: copy.deepcopy(relays[-1][k]) for k in ("operation_id", "metadata", "probe_results")} if relays else None
        return {"sim_ns": self.state["sim_ns"], "event_id": task.get("event_id", ""), "task_id": task.get("task_id", ""), "task_revision": task.get("task_revision", 0), "deadline_ns": task.get("deadline_ns", self.state["sim_ns"] + 120 * NS), "commitment": task.get("commitment"), "commitment_version": self.state["commitment_version"], "relay_held": self.state.get("relay_held", False), "relay_release_authorized": self.state.get("relay_release_authorized"), "last_assignment_estimate": self.state.get("last_assignment_estimate"), "payload_state": self.state.get("payload_state"), "local_energy": self.state.get("local_energy"), "relay_mission": relay, "handover_authorized": task.get("accept_relay_handover", False), "handover_incoming": copy.deepcopy(self.state.get("handover_incoming")), "handover_outgoing": copy.deepcopy(self.state.get("handover_outgoing"))}

    def _send_resource_report(self, key: str, data: dict[str, Any], task: dict[str, Any]) -> None:
        submissions = self.state.setdefault("resource_submissions", {})
        if key in submissions:
            return
        entry = {"status": "submitting", "data": data, "event_id": task["event_id"]}
        submissions[key] = entry
        self._save()
        reply = self.flight.request("radio_send", destination="centre", kind="report", data=data,
            event_id=task["event_id"], task_id=task["task_id"], task_revision=task["task_revision"],
            deadline_ns=task.get("deadline_ns", self.state["sim_ns"] + 120 * NS), route=self._evidence_route())
        entry.update(status="pending", request_id=reply["request_id"])
        self._log("resource-events.jsonl", {"stage": "radio_submission", **entry})

    def _invalidate_resources(self, reason: str, task: dict[str, Any]) -> None:
        previous = self.state["commitment_version"]
        self.state["commitment_version"] = previous + 1
        self.state.pop("last_assignment_estimate", None)
        self._decision_event()
        offer = self.state.get("last_offer", {})
        data = {"kind": "resource_withdrawal", "reason": reason, "commitment_version": previous + 1,
            "invalidated_commitment_version": offer.get("summary", {}).get("commitment_version", previous),
            "bid_message_id": offer.get("message_id"), "observed_at_ns": self.state["sim_ns"], "local_energy": self.state.get("local_energy")}
        if offer:
            offer["withdrawn"] = True
        self.state["resource_invalidation"] = {"data": data, "task": task, "submitted": False}

    def _resource_tick(self, local_state: dict[str, Any]) -> None:
        if self.energy is not None:
            snapshot = self.energy.observe(local_state)
            self.state["local_energy"] = None if snapshot["status"] == "disabled" else snapshot
        offer = self.state.get("last_offer")
        budget = self.state.get("local_energy")
        if offer and not offer.get("withdrawn") and budget and (offer.get("summary", {}).get("soc") or 0) >= 0.2:
            if budget["status"] != "available" or budget["effective_fraction"] < 0.2:
                self._invalidate_resources("local energy estimate unavailable" if budget["status"] != "available" else "declared local energy budget crossed the solver's existing 0.2 eligibility floor", self.state.get("task") or offer["task"])
        for entry in self.state.get("resource_submissions", {}).values():
            if entry["status"] == "submitting":
                entry.update(status="reconcile_required", reason="interrupted radio submission; no acknowledged request ID")
            elif entry["status"] == "pending":
                result = self._poll_pending(entry)
                if result.get("status") in TERMINAL:
                    entry.update(status=result["status"], result=result)
                    self._log("resource-events.jsonl", {"stage": "radio_terminal", **entry})

    def _assignment_is_canceled(self, task: dict[str, Any]) -> bool:
        cancel = self.state.get("assignment_cancel")
        return bool(cancel and (cancel["task_id"], cancel["task_revision"]) == (task.get("task_id"), task.get("task_revision")))

    def _assignment_cancel_tick(self) -> None:
        cancel = self.state.get("assignment_cancel")
        if not cancel or cancel.get("report_submitted"):
            return
        if self.state["sim_ns"] >= cancel["data"]["deadline_ns"]:
            cancel["failure"] = "cancellation confirmation deadline reached; actual operation state retained"
            self.state["task_blocker"] = {"status": "failed", "reason": cancel["failure"]}
            return
        if self.future is not None or self.active_tool is not None or any(self._assignment_is_canceled(m) and m["status"] not in TERMINAL for m in self.state["missions"].values()):
            return
        released = any((p["task_id"], p["task_revision"]) == (cancel["task_id"], cancel["task_revision"]) and p.get("released") for p in self.tools.payload.packages.values())
        data = {"kind": "assignment_canceled", "cancel_message_id": cancel["message_id"],
            "operation_results": cancel.get("operation_results", []), "no_active_operation": True,
            "payload_already_released": released, "commitment_version": self.state["commitment_version"]}
        self._send_resource_report("cancel-" + cancel["message_id"], data, {**cancel, "deadline_ns": cancel["data"]["deadline_ns"]})
        cancel["report_submitted"] = True

    def submit_mission(self, name: str, steps: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
        return {"status": "accepted", "mission_spec": {"name": name, "steps": steps, "metadata": metadata}}

    def _register_mission(self, name: str, steps: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
        if any(row["status"] not in TERMINAL for row in self.state["missions"].values()):
            raise ValueError("one task worker may own only one active mission")
        operation_id = "mission-" + uuid.uuid4().hex
        mission = {"operation_id": operation_id, "tool_name": name, "steps": steps, "index": 0, "pending": None, "status": "accepted", "metadata": metadata, "frames": [], **self.context()}
        self.state["missions"][operation_id] = mission
        return {"operation_id": operation_id, "status": "accepted"}

    def _persist_message(self, message: dict[str, Any]) -> None:
        mid = message["message_id"]
        path = self.root / "inbox" / mid
        path.mkdir(exist_ok=True)
        local = dict(message)
        if "content_base64" in local:
            raw = base64.b64decode(local.pop("content_base64"), validate=True)
            from .transport import digest
            if digest(raw) != local["file_hash"]:
                raise ValueError("private endpoint returned mismatched radio copy")
            (path / "content").write_bytes(raw)
            local["content_path"] = str(path / "content")
            if local["kind"] == "file":
                from .history import ObservationStore
                archive = ObservationStore(self.root, self.run_id, self.uav_id)
                try:
                    suffix = ".jpg" if raw.startswith(b"\xff\xd8") else ".png" if raw.startswith(b"\x89PNG") else ".bin"
                    artifact = archive.save_artifact(raw, suffix, {**local.get("metadata", {}), "radio_sender": local["source"], "received_at": local["received_ns"], "radio_message_id": mid})
                    local["artifact_id"] = artifact["artifact_id"]
                finally:
                    archive.close()

        save_json(path / "message.json", local)
        if mid not in self.state["seen_messages"]:
            self._consume_message(local)
            self.state["seen_messages"].append(mid)
            self._decision_event()
        self._save()
        self.flight.request("radio_inbox_ack", message_id=mid)

    def _consume_message(self, message: dict[str, Any], *, apply: bool = False) -> None:
        if not apply:
            if self.centre is not None:
                self.centre.consume(message, self.state["sim_ns"])
            return
        current_task = self.state.get("task") or {}
        if message.get("kind") == "report" and message["source"] == "centre" and message.get("data", {}).get("kind") == "cancel_assignment" and not (current_task.get("commitment") or {}).get("protected"):
            self.state["assignment_cancel"] = {**message, "operation_results": []}
            revoked = [message["task_id"], message["task_revision"]]
            if revoked not in self.state.setdefault("revoked_assignments", []):
                self.state["revoked_assignments"].append(revoked)
            if self._assignment_is_canceled(current_task):
                self.state["relay_held"] = False
                self.state.pop("task_blocker", None)
            if self.state.get("pending_task") and self._assignment_is_canceled(self.state["pending_task"]):
                self.state.pop("pending_task")
        if message.get("kind") == "report" and message["source"] == "centre" and message.get("data", {}).get("kind") == "mission_closed" and message["event_id"] == current_task.get("event_id") and not (current_task.get("commitment") or {}).get("protected"):
            self.state["mission_closed"] = message
            self.state["relay_release_authorized"] = message["message_id"]
            self.state["relay_held"] = False
            self.state.pop("task_blocker", None)
        relevant_event = self.centre.state["active"] if self.centre is not None else current_task.get("event_id")
        if message.get("kind") == "report" and message.get("data", {}).get("kind") == "relay_ready" and message["event_id"] == relevant_event and message["data"].get("valid_until_ns", -1) >= self.state["sim_ns"]:
            self.state["ready_relay"] = message["source"]
            self.state["ready_relay_valid_until_ns"] = message["data"]["valid_until_ns"]
            self.state["ready_relay_event_id"] = message["event_id"]
            if self.active_tool and self.active_tool.get("message_id"):
                self.flight.request("radio_route", message_id=self.active_tool["message_id"], route=self._evidence_route())
            for submission in self.state.get("resource_submissions", {}).values():
                if submission["status"] != "pending" or submission["event_id"] != relevant_event:
                    continue
                mid = submission.get("message_id")
                if not mid:
                    mid = self.flight.request("result", pending_request_id=submission["request_id"]).get("message_id")
                if mid:
                    reply = self.flight.request("radio_route", message_id=mid, route=self._evidence_route())
                    self._log("resource-events.jsonl", {"stage": "radio_reroute_submitted", "message_id": mid, "route": self._evidence_route(), "request_id": reply.get("request_id")})
            if self.centre is not None:
                self._reroute_centre_outbox(message["source"])
        if self.centre is not None:
            self.centre.consume(message, self.state["sim_ns"])
        if self.role != "uav" or message["kind"] not in {"notice", "plan"}:
            return
        data = message.get("data", {})
        if self.state.get("mission_closed", {}).get("event_id") == message["event_id"]:
            self._log("decisions.jsonl", {"decision": "late_plan_after_terminal_event_ignored", "message_id": message["message_id"]})
            return
        old = self.state.get("task")
        if old and (old.get("commitment") or {}).get("protected") and old["task_id"] != message["task_id"]:
            self._log("decisions.jsonl", {"decision": "protected_task_retained", "message_id": message["message_id"]})
            return
        if old and message["event_id"] == old["event_id"] and message["task_revision"] < old["task_revision"]:
            return
        if [message["task_id"], message["task_revision"]] in self.state.get("revoked_assignments", []):
            return
        selected = data.get("selected_bid")
        if message["kind"] == "plan" and selected:
            budget = self.state.get("local_energy")
            offered_id = self.state.get("last_offer", {}).get("message_id")
            invalid = selected["commitment_version"] != self.state["commitment_version"] or selected["valid_until_ns"] < self.state["sim_ns"] or bool(offered_id and selected["message_id"] != offered_id)
            if budget:
                invalid = invalid or budget["status"] != "available" or budget["effective_fraction"] < 0.2 or data.get("assignment_energy_j", 0) > budget["available_energy_j"]
            if invalid:
                self.state.setdefault("revoked_assignments", []).append([message["task_id"], message["task_revision"]])
                self._invalidate_resources("received plan references an expired or changed local resource commitment", {**message, "deadline_ns": data.get("deadline_ns", self.state["sim_ns"] + 120 * NS)})
                self._log("resource-events.jsonl", {"stage": "stale_plan_rejected", "message_id": message["message_id"], "selected_bid": selected})
                return
        if old and self.state.get("relay_held") and message["event_id"] != old.get("event_id"):
            self.state["pending_task"] = message
            self._log("decisions.jsonl", {"decision": "new_event_waits_for_radio_relay_release", "message_id": message["message_id"]})
            return
        if old and (self.active_tool is not None or self.future is not None or any(m["status"] not in TERMINAL for m in self.state["missions"].values())):
            self._log("decisions.jsonl", {"decision": "new_task_waits_for_cancel_or_completion", "message_id": message["message_id"]})
            self.state["pending_task"] = message
            return
        self.flight.accept_task(message["message_id"], message["task_id"], message["task_revision"])
        task = {**data, **data.get("task", {}), "message_id": message["message_id"], "event_id": message["event_id"], "task_id": message["task_id"], "task_revision": message["task_revision"], "deadline_ns": data.get("deadline_ns", data.get("task", {}).get("deadline_ns", data.get("incident", {}).get("deadline_ns", self.state["sim_ns"] + 120 * NS)))}
        if task.get("intent") == "offer" and not task.get("workflow"):
            task["workflow"] = [
                {"tool": "estimate-assignment", "request": {"actions": task.get("incident", {}).get("initial_graph", {}).get("actions", [])}, "result_key": "estimate"},
                {"tool": "offer-rescue-role", "request": {"destination": "centre"}, "result_key": "bid"},
            ]
        self.state.pop("task_blocker", None)
        self.state.pop("inspection", None)
        if old and (old["task_id"], old["task_revision"]) != (task["task_id"], task["task_revision"]):
            for key in ("handover_incoming", "handover_outgoing", "handover_released"):
                self.state.pop(key, None)
        if self.state.get("assignment_cancel") and message["task_revision"] > self.state["assignment_cancel"]["task_revision"]:
            self.state.pop("assignment_cancel")
        if task.get("recovery_policy"):
            self.state["relay_policy"] = {"event_id": task["event_id"], "policy": task["recovery_policy"], "public_map": task["public_map"]}
        if not old or task["event_id"] != old.get("event_id"):
            self.state.pop("mission_closed", None)
            self.state.pop("relay_release_authorized", None)
            self.state.pop("relay_monitor", None)
            self.state.pop("ready_relay", None)
            self.state.pop("ready_relay_valid_until_ns", None)
            self.state.pop("ready_relay_event_id", None)
            if self.state.get("relay_policy", {}).get("event_id") != task["event_id"]:
                self.state.pop("relay_policy", None)
        self.state.update(task=task, step_index=0, results={}, commitment_version=self.state["commitment_version"] + 1)
        self._log("decisions.jsonl", {"decision": "radio_task_accepted", "message_id": message["message_id"], "task_id": task["task_id"], "task_revision": task["task_revision"]})

    def _gateway_request(self, **params: Any) -> dict[str, Any]:
        result = self.flight.request(**params)
        deadline = time.monotonic() + 10
        while result.get("status") == "pending" and "uav_id" not in result:
            if time.monotonic() >= deadline:
                raise TimeoutError("coordinator management response timed out")
            request_id = result.get("request_id") or params.get("pending_request_id")
            time.sleep(0.02)
            params = {"op": "result", "pending_request_id": request_id}
            result = self.flight.request(**params)
        return result

    def _download_capture(self, result: dict[str, Any]) -> dict[str, Any]:
        from .sensors import download_capture
        return download_capture(self.tools.perception_tools().store, self._gateway_request, result)

    def _poll_pending(self, pending: dict[str, Any]) -> dict[str, Any]:
        if pending.get("kind") == "flight":
            return self.flight.poll(pending["operation_id"])
        try:
            result = self.flight.request("result", pending_request_id=pending["request_id"])
        except RuntimeError as exc:
            return {"status": "failed", "reason": str(exc)}
        if "uav_id" not in result:
            return result
        if result.get("message_id"):
            pending["message_id"] = result["message_id"]
            if pending.get("call", {}).get("tool") == "offer-rescue-role" and self.state.get("last_offer"):
                self.state["last_offer"]["message_id"] = result["message_id"]
        if result.get("status") == "accepted" and result.get("message_id") and pending.get("call", {}).get("request", {}).get("data", {}).get("kind") == "relay_ready":
            # Readiness announcements remain in the persistent radio outbox.
            # Waiting for their peer ACK here would block route recovery when
            # that moving peer has already left the measured path.
            return {**result, "status": "submitted", "delivery_status": "pending", "reason": "readiness announcement queued; no delivery claim"}
        if pending.get("message_id") and result.get("status") not in TERMINAL:
            query = self.flight.request("radio_status", message_id=pending["message_id"])
            pending["request_id"] = query["request_id"]
            return {"status": "pending"}
        if pending.get("kind") == "capture" and result.get("status") == "pending":
            query = self.flight.request("capture_poll", **pending["params"])
            pending["request_id"] = query["request_id"]
        elif pending.get("kind") == "release" and result.get("status") in {"pending", "accepted", "attached"}:
            query = self.flight.request("payload_status", package_id=pending["params"]["package_id"])
            pending["request_id"] = query["request_id"]
        return result

    def _advance_missions(self) -> None:
        for mission in self.state["missions"].values():
            if mission["status"] in TERMINAL:
                continue
            if (mission.get("pending") or {}).get("kind") == "submitting":
                mission.update(reconcile_required=True, reason="submission acknowledgement was not persisted; actual operation is unknown and this step will not be replayed")
                if not mission.get("submission_uncertainty_reported"):
                    mission["submission_uncertainty_reported"] = True
                    self._decision_event()
                continue
            if (self.state.get("mission_closed", {}).get("event_id") == mission["event_id"] and mission["tool_name"] != "resume-or-return") or self._assignment_is_canceled(mission):
                pending = mission.get("pending") or {}
                if pending.get("kind") == "flight":
                    canceled = self.flight.cancel(pending["operation_id"])
                    if canceled.get("status") not in TERMINAL:
                        continue
                    if self._assignment_is_canceled(mission):
                        self.state["assignment_cancel"]["operation_results"].append(canceled)
                elif pending.get("kind") == "release":
                    released = self._poll_pending(pending)
                    if released.get("status") not in TERMINAL | {"detached"}:
                        continue
                    if released.get("released"):
                        self.tools.payload.actuator_state(mission["metadata"]["package_id"], "detached", released.get("source_time_ns"), released["source"], observed_sim_ns=released.get("observed_sim_ns"), release_interval_ns=released.get("release_interval_ns"))
                mission.update(status="canceled", reason="remaining steps canceled by received centre request; submitted physical operation reconciled")
                continue
            if self.state["sim_ns"] >= mission["deadline_ns"]:
                pending = mission.get("pending") or {}
                if pending.get("kind") == "flight":
                    actual = self.flight.cancel(pending["operation_id"])
                    mission["deadline_cancel_result"] = actual
                    if actual.get("status") not in TERMINAL:
                        mission["reason"] = "task deadline reached; awaiting actual flight cancellation terminal"
                        continue
                    mission["last_result"] = actual
                elif pending.get("kind") == "release":
                    actual = self._poll_pending(pending)
                    if actual.get("status") not in TERMINAL | {"detached"}:
                        mission["reason"] = "task deadline reached; submitted physical release remains unreconciled"
                        continue
                    if actual.get("released"):
                        self.tools.payload.actuator_state(mission["metadata"]["package_id"], "detached", actual.get("source_time_ns"), actual["source"], observed_sim_ns=actual.get("observed_sim_ns"), release_interval_ns=actual.get("release_interval_ns"))
                    mission["last_result"] = actual
                mission.update(status="failed", reason="task_deadline", pending=None)
                continue
            pending = mission["pending"]
            if pending:
                if pending["kind"] == "download":
                    future = self.capture_futures.get(mission["operation_id"])
                    if future is None:
                        self.capture_futures[mission["operation_id"]] = self.executor.submit(self._download_capture, pending["capture_result"])
                        continue
                    if not future.done():
                        continue
                    try:
                        frame = future.result()
                    except Exception as exc:
                        mission.update(status="failed", reason="capture_download: " + str(exc))
                        del self.capture_futures[mission["operation_id"]]
                        continue
                    mission["frames"].append(frame)
                    del self.capture_futures[mission["operation_id"]]
                    result = {"status": "succeeded", "frame_id": frame["frame_id"]}
                else:
                    result = self._poll_pending(pending)
                if result.get("status") in {"failed", "canceled", "unavailable", "error"}:
                    if mission["tool_name"] == "hold-relay" and pending["kind"] == "probe":
                        candidate = mission["steps"][mission["index"]]["candidate_index"]
                        next_index = mission["index"] + 1
                        while next_index < len(mission["steps"]) and mission["steps"][next_index]["candidate_index"] == candidate:
                            next_index += 1
                        mission.update(index=next_index, pending=None, probe_results=[])
                        if next_index >= len(mission["steps"]):
                            mission.update(status="failed", reason="no_candidate_has_measured_route")
                        continue
                    mission.update(status="failed", reason=result.get("reason", result["status"]), last_result=result)
                    continue
                if result.get("status") not in {"succeeded", "available", "detached", "delivery_verified"}:
                    continue
                if pending["kind"] == "capture":
                    mission["pending"] = {"kind": "download", "capture_result": result}
                    self.capture_futures[mission["operation_id"]] = self.executor.submit(self._download_capture, result)
                    continue
                if pending["kind"] == "release":
                    if not result.get("released"):
                        mission.update(status="failed", reason="release_not_observed")
                        continue
                    self.tools.payload.actuator_state(mission["metadata"]["package_id"], "detached", result.get("source_time_ns"), result["source"], observed_sim_ns=result.get("observed_sim_ns"), release_interval_ns=result.get("release_interval_ns"))
                if pending["kind"] == "probe":
                    mission.setdefault("probe_results", []).append({"peer": pending["params"]["destination"], "result": result})
                    if len(mission["probe_results"]) == len(mission["metadata"]["required_peers"]):
                        self.state["relay_held"] = True
                        # A newly established responsibility supersedes any
                        # release of an earlier hold, even within the same task.
                        self.state.pop("relay_release_authorized", None)
                        self.state.pop("handover_released", None)
                        self.state["results"].pop("return_after_handover", None)
                        mission.update(status="succeeded", reason="measured_route_ready_relay_retained", last_result=result)
                        continue
                mission.update(index=mission["index"] + 1, pending=None, last_result=result, reconcile_required=False)
            if mission["index"] >= len(mission["steps"]):
                mission["status"] = "succeeded"
                continue
            step = mission["steps"][mission["index"]]
            action, params = step["action"], dict(step["params"])
            if mission["tool_name"] == "hold-relay" and step["candidate_index"] != mission.get("current_candidate"):
                mission.update(current_candidate=step["candidate_index"], probe_results=[])
            mission["pending"] = {"kind": "submitting", "action": action, "params": copy.deepcopy(params), "intent_ns": self.state["sim_ns"]}
            mission["status"] = "running"
            self._save()  # Persist the uncertain side-effect window before RPC.
            if action in {"arm", "takeoff", "goto", "hold", "land", "disarm"}:
                result = self.flight.submit(action, params, mission["task_id"], mission["task_revision"])
                mission["pending"] = {"kind": "flight", "operation_id": result["operation_id"]}
            else:
                if action == "capture":
                    params["after_sim_ns"] = self.state["sim_ns"]
                operation = {"capture": "capture_request", "probe": "probe_route", "release": "payload_release"}[action]
                result = self.flight.request(operation, **params, task_id=mission["task_id"], task_revision=mission["task_revision"], deadline_ns=mission["deadline_ns"], event_id=mission["event_id"])
                mission["pending"] = {"kind": action, "request_id": result["request_id"], "params": params}
                if action == "release":
                    self.tools.payload.release_requested(params["package_id"], result["request_id"], self.state["sim_ns"], mission["task_id"], mission["task_revision"], drop_site=mission["metadata"].get("drop_site"))
            mission["status"] = "running"
            self._save()  # Resume only from acknowledged, persisted operation IDs.
            self._log("decision-yield-trace.jsonl", {"operation_id": mission["operation_id"], "pending": mission["pending"], "yielded": True})

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$results."):
            result: Any = self.state["results"]
            for part in value[len("$results."):].split("."):
                result = result[int(part)] if isinstance(result, list) else result[part]
            return result
        if isinstance(value, dict):
            return {key: self._resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve(item) for item in value]
        return value

    def _policy_call(self, tool: str, request: dict[str, Any], key: str) -> dict[str, Any]:
        return {"tool": tool, "request": request, "result_key": key, "continue_on_unavailable": True}

    def _secondary_clue_decision(self, first: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not self.config.get("incident", {}).get("discover_secondary_clue", False):
            return None
        results = self.state["results"]
        if "secondary_clue" in results:
            return None
        for index, frame in enumerate(frames):
            if frame["observed_at"] <= first["observed_at"]:
                continue
            key = "secondary_detect_" + str(index)
            if key not in results:
                return self._policy_call("detect-persons", {"frame_id": frame["frame_id"]}, key)
            for detection in results[key].get("detections", []):
                locate_key = "secondary_locate_" + str(index) + "_" + detection["detection_id"]
                if locate_key not in results:
                    return self._policy_call("locate-target", {"frame_id": frame["frame_id"], "detection_id": detection["detection_id"]}, locate_key)
                location = results[locate_key]
                from math import dist
                if location.get("status") == "available" and dist(location["position_enu_m"][:2], first["location"]["position_enu_m"][:2]) >= self.config["incident"].get("secondary_clue_separation_m", 15.0):
                    return self._policy_call("send-evidence", {"data": {"kind": "new_incident_clue", "reference_frame_id": first["frame_id"], "reference_detection_id": first["detection_id"], "frame_id": frame["frame_id"], "detection_id": detection["detection_id"]}, "route": self._evidence_route()}, "secondary_clue")
        return None

    def _inspection_decision(self) -> dict[str, Any] | None:
        results = self.state["results"]
        search = results.get("search", {})
        frames = search.get("frames", [])
        if "inspection_report" in results:
            return None
        policy = self.state.setdefault("inspection", {"cursor": 0, "detection_cursor": 0})
        first = policy.get("first")
        if first is None:
            while policy["cursor"] < len(frames):
                frame = frames[policy["cursor"]]
                frame_id = frame["frame_id"]
                key = "detect_" + str(policy["cursor"])
                if key not in results:
                    return self._policy_call("detect-persons", {"frame_id": frame_id}, key)
                detected = results[key]
                if detected["status"] != "succeeded":
                    policy["failure"] = detected.get("reason", "detection unavailable")
                    break
                detections = detected["detections"]
                if policy["detection_cursor"] >= len(detections):
                    policy.update(cursor=policy["cursor"] + 1, detection_cursor=0)
                    continue
                detection = detections[policy["detection_cursor"]]
                location_key = "locate_" + str(policy["cursor"]) + "_" + str(policy["detection_cursor"])
                if location_key not in results:
                    return self._policy_call("locate-target", {"frame_id": frame_id, "detection_id": detection["detection_id"]}, location_key)
                if results[location_key]["status"] != "available":
                    policy["detection_cursor"] += 1
                    continue
                first = {"frame_id": frame_id, "detection_id": detection["detection_id"], "location": results[location_key], "rgb_artifact_id": frame["rgb_artifact_id"], "bbox_xyxy": detection["bbox_xyxy"], "observed_at": frame["observed_at"]}
                policy["first"] = first
                break
            if first is None:
                report = {"observation_verdict": "uncertain", "reason": policy.get("failure", "No located person in the actually captured frames; planned coverage is not proof of observed coverage"), "frame_ids": [row["frame_id"] for row in frames]}
                return self._policy_call("send-evidence", {"data": report, "destination": "centre", "route": self._evidence_route()}, "inspection_report")
        second_clue = self._secondary_clue_decision(first, frames)
        if second_clue:
            return second_clue
        if "new_view" not in results:
            return self._policy_call("reobserve-target", {"frame_id": first["frame_id"], "detection_id": first["detection_id"]}, "new_view")
        new_view = results["new_view"]
        if new_view["status"] != "succeeded" or not new_view.get("frames"):
            return self._policy_call("send-evidence", {"data": {"observation_verdict": "uncertain", "reason": new_view.get("reason", "No independent new observation"), "first": first}, "route": self._evidence_route()}, "inspection_report")
        second_frame = new_view["frames"][-1]
        if "second_detect" not in results:
            return self._policy_call("detect-persons", {"frame_id": second_frame["frame_id"]}, "second_detect")
        candidates = results["second_detect"].get("detections", [])
        second = policy.get("second")
        if second is None:
            for index, detection in enumerate(candidates):
                key = "second_locate_" + str(index)
                if key not in results:
                    return self._policy_call("locate-target", {"frame_id": second_frame["frame_id"], "detection_id": detection["detection_id"]}, key)
                location = results[key]
                if location["status"] == "available":
                    from math import dist
                    if dist(location["position_enu_m"][:2], first["location"]["position_enu_m"][:2]) <= 5:
                        second = {"frame_id": second_frame["frame_id"], "detection_id": detection["detection_id"], "location": location}
                        policy["second"] = second
                        break
        if second is None:
            return self._policy_call("send-evidence", {"data": {"observation_verdict": "uncertain", "reason": "Independent view did not locate a consistent target; disappearance is not established", "first": first, "second_frame_id": second_frame["frame_id"]}, "route": self._evidence_route()}, "inspection_report")
        for name, reference in (("first_tracks", first), ("second_tracks", second)):
            if name not in results:
                return self._policy_call("track-persons", {"frame_id": reference["frame_id"]}, name)
        if "verification" not in results:
            return self._policy_call("verify-target", {"first_frame_id": first["frame_id"], "second_frame_id": second["frame_id"], "first_detection_id": first["detection_id"], "second_detection_id": second["detection_id"]}, "verification")
        verdict = results["verification"]["status"]
        if verdict == "confirmed":
            if "evidence_package" not in results:
                return self._policy_call("package-evidence", {"artifact_id": first["rgb_artifact_id"], "bbox_xyxy": first["bbox_xyxy"], "budget_bytes": 65536}, "evidence_package")
            package = results["evidence_package"]
            if package["status"] != "succeeded":
                verdict = "uncertain"
            elif "evidence_transfer" not in results:
                return self._policy_call("send-evidence", {"artifact_id": package["artifact_id"], "frame_id": first["frame_id"], "destination": "centre", "route": self._evidence_route()}, "evidence_transfer")
            elif results["evidence_transfer"]["status"] != "succeeded":
                verdict = "uncertain"
            if verdict == "confirmed" and "drop_site" not in results:
                return self._policy_call("select-drop-site", {"frame_id": second["frame_id"], "detection_id": second["detection_id"]}, "drop_site")
        report = {"observation_verdict": verdict, "first": first, "second": second, "verification": results["verification"], "evidence_transfer": results.get("evidence_transfer"), "drop_site": results.get("drop_site"), "observed_at_ns": second_frame["observed_at"]}
        return self._policy_call("send-evidence", {"data": report, "destination": "centre", "route": self._evidence_route()}, "inspection_report")

    def _ready_relay(self) -> str | None:
        event_id = self.centre.state["active"] if self.centre is not None else (self.state.get("task") or {}).get("event_id")
        if self.state.get("ready_relay_valid_until_ns", -1) < self.state["sim_ns"] or self.state.get("ready_relay_event_id") != event_id:
            return None
        return self.state.get("ready_relay")

    def _evidence_route(self) -> list[str]:
        relay = self._ready_relay()
        return [self.uav_id, relay, "centre"] if relay and relay != self.uav_id else [self.uav_id, "centre"]

    def _relay_decision(self) -> dict[str, Any] | None:
        results = self.state["results"]
        task = self.state["task"]
        actions = task.get("incident", {}).get("initial_graph", {}).get("actions", task.get("assignments", []))
        roles = {action.get("role") for action in actions}
        # Public endpoint/capability roster and this received phase only. Never
        # consult another vehicle's private commitment or runtime state.
        service_peers = [uid for uid, profile in self.config.get("vehicles", {}).items()
                         if uid != self.uav_id and "inspect" in roles and "inspect" in profile.get("capabilities", [])]
        if "deliver" in roles and self.config.get("payload"):
            service_peers.extend(self.config["payload"][key] for key in ("carrier", "receiver_id"))
        service_peers = list(dict.fromkeys(uid for uid in service_peers if uid != self.uav_id))
        if not service_peers:
            return None
        local = self.state.get("self_state", {})
        for action in ("arm", "takeoff"):
            if action == "arm" and local.get("armed") is True or action == "takeoff" and local.get("landed_state") in {"IN_AIR", "in_air"}:
                continue
            key = "relay_" + action
            if key not in results:
                return self._policy_call("flight-command", {"action": action, "params": {"altitude_m": 30} if action == "takeoff" else {}}, key)
            if results[key]["status"] != "succeeded":
                return None
        monitor = self.state.setdefault("relay_monitor", {"generation": 0, "candidate_offset": 0, "probe_round": 0})
        candidates = self.state["relay_policy"]["public_map"]["relay_candidates"]
        generation = monitor["generation"]
        service_index = monitor.get("service_index", 0) % len(service_peers)
        service_peer = service_peers[service_index]
        peers = ["centre", service_peer]
        if not self.state.get("relay_held") or monitor.get("recovery_required"):
            if self.state["sim_ns"] < monitor.get("retry_after_ns", 0):
                return None
            key = "relay_hold_" + str(generation)
            if key not in results:
                offset = monitor["candidate_offset"] % len(candidates)
                ordered = candidates[offset:] + candidates[:offset]
                return self._policy_call("hold-relay", {"candidates": ordered, "peers": peers, "message_ids": []}, key)
            result = results[key]
            if result["status"] != "succeeded":
                monitor.update(generation=generation + 1, recovery_required=True, retry_after_ns=self.state["sim_ns"] + 10 * NS)
                return None
            monitor.update(last_result=result, recovery_required=False, checked_ns=self.state["sim_ns"])
        result = monitor.get("last_result")
        if result is None:
            # The mission has just completed and set relay_held; consume its
            # persisted result before sending any readiness claim.
            result = results.get("relay_hold_" + str(generation))
            if result is None or result["status"] != "succeeded":
                return None
            monitor.update(last_result=result, checked_ns=self.state["sim_ns"])
        oldest_ack = min((row["result"].get("ack_received_ns", -5 * NS) for row in result["probe_results"]), default=-5 * NS)
        measured_peers = {row["peer"] for row in result["probe_results"]}
        if set(peers).issubset(measured_peers) and self.state["sim_ns"] < oldest_ack + 5 * NS:
            for peer in peers:
                key = "relay_notice_" + str(generation) + "_" + str(monitor["probe_round"]) + "_" + peer
                if key not in results:
                    return self._policy_call("send-evidence", {"destination": peer, "route": [self.uav_id, peer], "data": {"kind": "relay_ready", "service_peer": service_peer, "probe_results": result["probe_results"], "observed_at_ns": monitor["checked_ns"], "held_until": "ack_cancel_or_handover"}}, key)
            return None
        # A new accepted phase clears tool results, not the held radio duty.
        # Its old proofs may already be stale; remeasure instead of announcing.
        round_id = monitor["probe_round"]
        next_index = (service_index + 1) % len(service_peers)
        next_peers = ["centre", service_peers[next_index]]
        for peer in next_peers:
            key = "relay_monitor_" + str(round_id) + "_" + peer
            if key not in results:
                return self._policy_call("probe-route", {"destination": peer, "route": [self.uav_id, peer]}, key)
            if results[key]["status"] != "succeeded":
                monitor.update(generation=generation + 1, recovery_required=True, candidate_offset=monitor["candidate_offset"] + result.get("current_candidate", 0) + 1, last_result=None, probe_round=round_id + 1, service_index=next_index)
                self._log("decisions.jsonl", {"decision": "relay_route_lost_reposition_under_received_policy", "peer": peer, "probe": results[key]})
                return None
        monitor.update(checked_ns=self.state["sim_ns"], probe_round=round_id + 1, service_index=next_index, last_result={"probe_results": [{"peer": peer, "result": results["relay_monitor_" + str(round_id) + "_" + peer]} for peer in next_peers], "current_candidate": result.get("current_candidate", 0)})
        # Publish the newly measured round immediately. Waiting another TTL here
        # expires its first probe before either endpoint can receive readiness.
        return self._relay_decision()

    def _delivery_decision(self) -> dict[str, Any] | None:
        results, task = self.state["results"], self.state["task"]
        package_id = task["package_id"]
        if "delivery_report" in results:
            return None
        if "selected_drop" not in results:
            return self._policy_call("select-drop-site", {"plan_message_id": task["message_id"]}, "selected_drop")
        if results["selected_drop"]["status"] != "succeeded":
            return self._policy_call("send-evidence", {"data": {"kind": "delivery_failed", "reason": results["selected_drop"].get("reason", "no safe drop site")}, "route": self._evidence_route()}, "delivery_report")
        if "supply_mission" not in results:
            return self._policy_call("deliver-supply", {"package_id": package_id, "drop_site_id": results["selected_drop"]["drop_site_id"]}, "supply_mission")
        delivery = results["supply_mission"]
        if delivery["status"] != "succeeded":
            return self._policy_call("send-evidence", {"data": {"kind": "delivery_failed", "reason": delivery.get("reason", "physical delivery failed")}, "route": self._evidence_route()}, "delivery_report")
        receipt_id = None
        for mid in reversed(self.state["seen_messages"]):
            message = json.loads((self.root / "inbox" / mid / "message.json").read_text())
            if message["kind"] == "receipt" and message.get("data", {}).get("package_id") == package_id and message["task_id"] == task["task_id"] and message["task_revision"] == task["task_revision"]:
                receipt_id = mid
                break
        if receipt_id is None:
            self.state["delivery_wait"] = dict(self.tools.payload.packages.get(package_id, {}))
            return None  # No synthetic receiver response; keep advancing.
        if "settled_observation" not in results:
            return self._policy_call("verify-delivery", {"package_id": package_id, "receipt_message_id": receipt_id}, "settled_observation")
        settled = results["settled_observation"]
        if settled["status"] != "succeeded" or not settled.get("frames"):
            return self._policy_call("send-evidence", {"data": {"kind": "delivery_failed", "reason": "No post-contact observation", "observation": settled}, "route": self._evidence_route()}, "delivery_report")
        if "delivery_verification" not in results:
            return self._policy_call("verify-delivery", {"package_id": package_id, "receipt_message_id": receipt_id, "post_drop_frame_id": settled["frames"][-1]["frame_id"]}, "delivery_verification")
        verified = results["delivery_verification"]
        return self._policy_call("send-evidence", {"data": {"kind": "delivery_verified" if verified.get("delivery_verified") else "delivery_failed", "delivery": verified}, "route": self._evidence_route()}, "delivery_report")

    def _closure_return(self) -> dict[str, Any] | None:
        """Common harness disposition after a received cancellation, in both modes."""
        if "return_after_closure" in self.state["results"]:
            return None
        spawn = self.config["vehicles"][self.uav_id]["spawn"]
        return self._policy_call("resume-or-return", {"return_position_enu_m": [spawn[0], spawn[1], max(3.0, spawn[2])]}, "return_after_closure")

    def _rule_decision(self) -> dict[str, Any] | None:
        task = self.state.get("task")
        if not task:
            return None
        handover = self._handover_decision()
        if handover is not None or self.state.get("handover_released"):
            return handover
        steps = task.get("workflow", [])
        index = self.state["step_index"]
        if index >= len(steps):
            if task.get("intent") == "inspect":
                return self._inspection_decision()
            if task.get("intent") == "deliver":
                return self._delivery_decision()
            if self.state.get("relay_policy", {}).get("event_id") == task["event_id"]:
                return self._relay_decision()
            return None
        step = steps[index]
        if step["tool"] == "flight-command":
            action = step["request"]["action"]
            local = self.state.get("self_state", {})
            if action == "arm" and local.get("armed") is True or action == "takeoff" and local.get("landed_state") in {"IN_AIR", "in_air"}:
                self.state["step_index"] += 1
                return self._rule_decision()
        return {"tool": step["tool"], "request": self._resolve(step.get("request", {})), "result_key": step.get("result_key", str(index))}

    def _handover_decision(self) -> dict[str, Any] | None:
        task, results = self.state["task"], self.state["results"]
        if self.state.get("handover_released"):
            if "return_after_handover" not in results:
                spawn = self.config["vehicles"][self.uav_id]["spawn"]
                return self._policy_call("resume-or-return", {"return_position_enu_m": [spawn[0], spawn[1], max(3.0, spawn[2])]}, "return_after_handover")
            return None
        for mid in self.state["seen_messages"]:
            message = self._received(mid)
            if message["kind"] != "handover" or message["event_id"] != task["event_id"]:
                continue
            kind = message.get("data", {}).get("kind")
            action = "accept" if kind == "relay_handover_offer" else "release" if kind == "relay_handover_accepted" else None
            key = "handover-" + str(action) + "-" + mid
            if action and key not in results and mid not in self.state.get("handover_applied_messages", []):
                return self._policy_call("handover-role", {"action": action, "message_id": mid}, key)
        incoming = self.state.get("handover_incoming")
        if incoming and incoming["event_id"] == task["event_id"]:
            prefix = "handover-" + incoming["message_id"]
            local = self.state.get("self_state", {})
            for action in ("arm", "takeoff"):
                if action == "arm" and local.get("armed") is True or action == "takeoff" and local.get("landed_state") in {"IN_AIR", "in_air"}:
                    continue
                key = prefix + "-" + action
                if key not in results:
                    return self._policy_call("flight-command", {"action": action, "params": {"altitude_m": incoming["data"]["position_enu_m"][2]} if action == "takeoff" else {}}, key)
                if results[key]["status"] != "succeeded":
                    return None
            key = prefix + "-hold"
            if key not in results:
                return self._policy_call("hold-relay", {"candidates": [incoming["data"]["position_enu_m"]], "peers": incoming["data"]["peers"]}, key)
            if results[key]["status"] == "succeeded" and prefix + "-ack" not in results:
                return self._policy_call("handover-role", {"action": "acknowledge", "message_id": incoming["message_id"]}, prefix + "-ack")
        outgoing = self.state.get("handover_outgoing")
        if outgoing and outgoing.get("status") == "offered" and self.state["sim_ns"] >= outgoing["data"]["deadline_ns"]:
            return self._policy_call("handover-role", {"action": "status"}, "handover-timeout-" + outgoing["message_id"])
        return None

    def _local_context(self, local_state: dict[str, Any]) -> dict[str, Any]:
        # Actual local references stay visible without resending every numerical
        # depth footprint and historical low-level mission step to the provider.
        results = {}
        for key, result in self.state["results"].items():
            value = {k: v for k, v in result.items() if k not in {"steps", "pending"}}
            if "frames" in value and value["frames"] and isinstance(value["frames"][0], dict):
                value["frames"] = [{k: frame[k] for k in ("frame_id", "observed_at", "rgb_artifact_id", "depth_artifact_id") if k in frame} for frame in value["frames"]]
            results[key] = value
        inbox = []
        for mid in self.state["seen_messages"][-20:]:
            message = json.loads((self.root / "inbox" / mid / "message.json").read_text())
            inbox.append({k: v for k, v in message.items() if k not in {"content_path", "native_packet_ids"}})
        return {"self": local_state, "task": self.state["task"], "results": results, "inbox": inbox, "delivery_wait": self.state.get("delivery_wait"), "local_energy": self.state.get("local_energy"), "commitment_version": self.state["commitment_version"], "assignment_cancel": self.state.get("assignment_cancel"), "handover_incoming": self.state.get("handover_incoming"), "handover_outgoing": self.state.get("handover_outgoing"), "handover_released": self.state.get("handover_released")}

    def _start_tool(self, call: dict[str, Any]) -> None:
        if call["tool"] == "offer-rescue-role":
            call = {**call, "offered_summary": self.state.get("last_assignment_estimate")}
        self.future_call = call
        self.future_kind = "tool"
        context = self.context()
        self.future_revision = (context["task_id"], context["task_revision"])
        self.state["tool_inflight"] = call
        self._save()
        self.future = self.executor.submit(self.tools.call, call["tool"], call["request"], context)

    def _complete_tool(self, result: dict[str, Any], call: dict[str, Any]) -> None:
        self.state.pop("tool_inflight", None)
        if call["tool"] == "handover-role":
            if call["request"].get("action") in {"accept", "release"} and result.get("status") not in {"pending", "accepted", "running"}:
                self.state.setdefault("handover_applied_messages", []).append(call["request"]["message_id"])
            if result.get("handover_offer"):
                self.state["handover_outgoing"] = result["handover_offer"]
            if call["request"].get("action", "propose") == "propose" and result.get("message_id") and self.state.get("handover_outgoing"):
                self.state["handover_outgoing"].update(message_id=result["message_id"], status="offered" if result["status"] == "succeeded" else result["status"])
            if result.get("handover_accept"):
                self.state["handover_incoming"] = result["handover_accept"]
            if call["request"].get("action") == "acknowledge" and result.get("status") == "succeeded":
                incoming = self.state["handover_incoming"]
                mission = self.context()["relay_mission"]
                self.state["relay_policy"] = {"event_id": incoming["event_id"], "policy": "accepted_relay_handover", "public_map": {**self.config["public_map"], "relay_candidates": [incoming["data"]["position_enu_m"]]}}
                self.state["relay_monitor"] = {"generation": 0, "candidate_offset": 0, "probe_round": 0, "checked_ns": self.state["sim_ns"], "last_result": {"probe_results": mission["probe_results"], "current_candidate": 0}}
            if result.get("handover_release"):
                self.state["handover_released"] = result["handover_release"]
                self.state["relay_held"] = False
                self.state["relay_release_authorized"] = result["handover_release"]["message_id"]
                self.state["handover_outgoing"]["status"] = "released"
                self.state.pop("relay_policy", None)
            if result.get("handover_timed_out"):
                self.state["handover_outgoing"]["status"] = "timed_out"
        if call["tool"] == "offer-rescue-role" and result.get("request_id") and not result.get("message_id") and result["status"] == "pending":
            self.state["last_offer"] = {"request_id": result["request_id"], "summary": call.get("offered_summary") or {}, "task": self.state["task"], "submitted_ns": self.state["sim_ns"]}
        if call["tool"] == "offer-rescue-role" and result.get("message_id") and self.state.get("last_offer"):
            self.state["last_offer"].update(message_id=result["message_id"], status=result["status"], completed_ns=self.state["sim_ns"])
        if "mission_spec" in result:
            result = {**result, **self._register_mission(**result["mission_spec"])}
            result.pop("mission_spec")
        if result.get("operation_id") in self.state["missions"] and result["status"] in {"accepted", "running"}:
            self.active_tool = {"call": call, "operation_id": result["operation_id"]}
            return
        if result.get("operation_id") and result["status"] in {"accepted", "running"}:
            self.active_tool = {"call": call, "flight_operation_id": result["operation_id"]}
            return
        if result.get("status") == "pending" and result.get("request_id"):
            self.active_tool = {"call": call, "request_id": result["request_id"]}
            return
        self.state["results"][call["result_key"]] = result
        if call["tool"] == "estimate-assignment" and result.get("status") == "succeeded":
            self.state["last_assignment_estimate"] = result
        if call["tool"] == "send-evidence" and result.get("message_id") and result.get("status") == "succeeded":
            save_json(self.root / "outbox" / (result["message_id"] + ".json"), {"result": result, "request": call["request"], **self.context()})
        if result.get("status") in {"succeeded", "submitted", "available", "confirmed", "rejected", "uncertain", "delivery_verified"}:
            if not call.get("local_request_id") or call.get("policy_submission"):
                self.state["step_index"] += 1
        elif self.mode == "rule" and (not call.get("local_request_id") or call.get("policy_submission")) and not call.get("continue_on_unavailable") and not self.state.get("mission_closed") and not self._assignment_is_canceled(self.state.get("task") or {}):
            self.state["task_blocker"] = result
        self._decision_event()
        self._log("decisions.jsonl", {"call": call, "result": result})
        if call.get("local_request_id"):
            self._queue_result(call, result)

    def _reroute_centre_outbox(self, relay: str) -> None:
        submissions = [{"request_id": row["request_id"], "destination": row["uav_id"]} for row in self.state.get("plan_submissions", [])]
        for incident in self.centre.state["incidents"].values():
            submissions.extend(row for row in incident.get("notice_requests", []) if isinstance(row, dict))
            submissions.extend(incident.get("closure_submissions", []))
            submissions.extend({"request_id": row["request_id"], "destination": row["uav_id"]}
                               for row in incident.get("replan_requested", {}).get("cancel_requests", []) if row.get("request_id"))
            if incident.get("clue_ack_submission"):
                submissions.append({"request_id": incident["clue_ack_submission"], "destination": incident["clue_source"]})
        for row in submissions:
            reply = self.flight.request("result", pending_request_id=row["request_id"])
            if reply.get("message_id") and row["destination"] != relay:
                self.flight.request("radio_route", message_id=reply["message_id"], route=["centre", relay, row["destination"]])

    def _publish_plan(self, event_id: str, graph: dict[str, Any], result: dict[str, Any]) -> None:
        if result["status"] != "feasible":
            raise ValueError("only a saved feasible plan can be published")
        incident = self.centre.state["incidents"][event_id]
        incident["graph"] = graph
        public = self.config.get("public_map", {})
        groups: dict[str, list[dict[str, Any]]] = {}
        for assignment in result["assignments"]:
            groups.setdefault(assignment["vehicle"], []).append(assignment)
        for uid, assignments in groups.items():
            assignments.sort(key=lambda item: item["departure_s"])
            workflow = [
                {"tool": "flight-command", "request": {"action": "arm"}, "result_key": "arm"},
                {"tool": "flight-command", "request": {"action": "takeoff", "params": {"altitude_m": public["search_altitude_m"]}}, "result_key": "takeoff"},
            ]
            for assignment in assignments:
                if assignment["role"] == "inspect":
                    workflow.append({"tool": "search-area", "request": {"polygon": public["search_polygon"], "altitude_m": public["search_altitude_m"], "footprint_width_m": public["search_row_spacing_m"] / 0.7}, "result_key": "search"})
            previous, energy_j = "current", 0.0
            for assignment in assignments:
                leg = next(row for row in incident["bids"][uid]["feasible_leg_costs"] if row["from_action"] == previous and row["to_action"] == assignment["action"])
                energy_j += leg["energy_j"]
                previous = assignment["action"]
            data = {"intent": assignments[-1]["role"], "assignments": assignments, "workflow": workflow, "deadline_ns": incident["deadline_ns"], "demand_source": incident["demand_source"], "public_map": public, "selected_bid": result["selected_bids"][uid], "assignment_energy_j": energy_j}
            if assignments[-1]["role"] == "deliver":
                data.update(target_report=incident["target_report"], package_id=self.config["payload"]["package_id"])
            task_id = event_id + "-" + uid
            reply = self.flight.request("radio_send", destination=uid, kind="plan", data=data, event_id=event_id, task_id=task_id, task_revision=result["task_revision"], deadline_ns=incident["deadline_ns"])
            self.state.setdefault("plan_submissions", []).append({"uav_id": uid, "request_id": reply["request_id"], "task_revision": result["task_revision"]})
            incident.setdefault("expected_tasks", {})[uid] = {"task_id": task_id, "task_revision": result["task_revision"]}
        incident["status"] = "executing"
        save_json(self.centre.path, self.centre.state)

    def _broadcast_offer_notice(self, event_id: str, graph: dict[str, Any]) -> None:
        incident = self.centre.state["incidents"][event_id]
        incident.pop("replan_requested", None)
        incident.pop("saved_plan_id", None)
        incident.update(status="submitting_notice", graph=graph, bids={}, notice_requests=[], expected_tasks={})
        incident["bid_tasks"] = {uid: {"task_id": event_id + "-offer", "task_revision": incident["task_revision"] + 1} for uid in self.config["vehicles"]}
        for uid in self.config["vehicles"]:
            data = {"task": {"intent": "offer", "deadline_ns": incident["deadline_ns"]}, "incident": {"initial_graph": graph, "deadline_ns": incident["deadline_ns"], "demand_source": incident["demand_source"]}, "public_map": self.config["public_map"]}
            reply = self.flight.request("radio_send", destination=uid, kind="notice", data=data, event_id=event_id, task_id=event_id + "-offer", task_revision=incident["task_revision"] + 1, deadline_ns=incident["deadline_ns"], route=["centre", self.state["ready_relay"], uid] if self._ready_relay() and uid != self.state["ready_relay"] else ["centre", uid])
            incident["notice_requests"].append({"request_id": reply["request_id"], "destination": uid})
        save_json(self.centre.path, self.centre.state)

    def _centre_resource_replan(self, event_id: str, incident: dict[str, Any]) -> bool:
        request = incident.get("replan_requested")
        if not request:
            return False
        now = self.state["sim_ns"]
        if request["received_ns"] > now:
            return True
        if "cancel_requests" not in request:
            # The current action graph is solved as one unit. Cancel its current
            # assignments before asking for a replacement graph, never overlap.
            request.update(cancel_deadline_ns=min(incident["deadline_ns"], now + 120 * NS), cancel_requests=[{"uav_id": uid, **task} for uid, task in incident.get("expected_tasks", {}).items()])
            save_json(self.centre.path, self.centre.state)
            for submission in request["cancel_requests"]:
                uid = submission["uav_id"]
                task = {key: submission[key] for key in ("task_id", "task_revision")}
                data = {"kind": "cancel_assignment", "reason": request["reason"], "withdrawal_message_id": request["withdrawal_message_id"], "deadline_ns": request["cancel_deadline_ns"]}
                relay = self._ready_relay()
                try:
                    reply = self.flight.request("radio_send", destination=uid, kind="report", data=data,
                        event_id=event_id, **task, deadline_ns=request["cancel_deadline_ns"],
                        route=["centre", relay, uid] if relay and relay != uid else ["centre", uid])
                    submission["request_id"] = reply["request_id"]
                except RuntimeError as exc:
                    submission["failure"] = str(exc)
            incident["status"] = "canceling_for_resource_change"
            self._log("resource-events.jsonl", {"stage": "cancel_assignments_submitted", "event_id": event_id, **request})
        confirmed = []
        for cancel in request["cancel_requests"]:
            if not cancel.get("request_id"):
                continue
            try:
                reply = self.flight.request("result", pending_request_id=cancel["request_id"])
            except RuntimeError as exc:
                cancel["failure"] = str(exc)
                continue
            if reply.get("message_id"):
                cancel["message_id"] = reply["message_id"]
            acknowledgement = next((row for row in incident["reports"] if cancel.get("message_id") and row["source"] == cancel["uav_id"] and row["task_id"] == cancel["task_id"] and row["task_revision"] == cancel["task_revision"] and row["received_ns"] <= now and row.get("data", {}).get("kind") == "assignment_canceled" and row["data"].get("cancel_message_id") == cancel["message_id"] and row["data"].get("no_active_operation") and all(op.get("status") in TERMINAL for op in row["data"].get("operation_results", []))), None)
            if acknowledgement:
                if acknowledgement["data"].get("payload_already_released"):
                    incident["reason"] = "Resources changed after a physical release; replacement delivery is not authorized"
                    request.update(status="failed", reason=incident["reason"])
                    save_json(self.centre.path, self.centre.state)
                    return True
                confirmed.append(acknowledgement["message_id"])
        if len(confirmed) == len(request["cancel_requests"]):
            incident.setdefault("resource_replans", []).append({**request, "cancel_confirmation_message_ids": confirmed, "confirmed_ns": now})
            self._log("resource-events.jsonl", {"stage": "radio_cancellations_confirmed", "event_id": event_id, "message_ids": confirmed})
            request.update(status="succeeded", cancel_confirmation_message_ids=confirmed)
            incident["status"] = "canceled_for_resource_change"
        elif now >= request["cancel_deadline_ns"]:
            incident["reason"] = "Affected assignment cancellation was not confirmed over radio before its deadline"
            request.update(status="failed", reason=incident["reason"])
        save_json(self.centre.path, self.centre.state)
        return True

    def _broadcast_delivery_notice(self, event_id: str, report: dict[str, Any]) -> None:
        incident = self.centre.state["incidents"][event_id]
        graph = {"actions": [{"id": "deliver-supply", "role": "deliver", "target": report["drop_site"]["position_enu_m"], "duration_s": 30, "after": []}], "payload_kg": 1.0}
        incident.update(target_report=report, phase="deliver")
        self._broadcast_offer_notice(event_id, graph)

    def _publish_closures(self, selected_event_id: str) -> None:
        for event_id, incident in self.centre.state["incidents"].items():
            if event_id != selected_event_id:
                continue
            if not incident.get("outcome") or incident.get("closure_submissions"):
                continue
            submissions = []
            for uid in self.config["vehicles"]:
                # A terminal cancellation can use the last candidate route:
                # the transport must freshly probe it before sending any data.
                relay = self.state.get("ready_relay") if self.state.get("ready_relay_event_id") == event_id else None
                route = ["centre", relay, uid] if relay and relay != uid else ["centre", uid]
                reply = self.flight.request("radio_send", destination=uid, kind="report", data={"kind": "mission_closed", **incident["outcome"], "release_basis": "centre terminal outcome; cancel remaining rescue and relay obligations"}, event_id=event_id, task_id=event_id + "-closure", task_revision=incident["task_revision"], deadline_ns=max(self.state["sim_ns"] + 30 * NS, incident["deadline_ns"]), route=route)
                submissions.append({"destination": uid, "request_id": reply["request_id"]})
            incident["closure_submissions"] = submissions
            save_json(self.centre.path, self.centre.state)

    def _acknowledge_clues(self, selected_event_id: str) -> None:
        for event_id, incident in self.centre.state["incidents"].items():
            if event_id != selected_event_id:
                continue
            if not incident.get("clue_message_id") or incident.get("clue_ack_submission"):
                continue
            data = {"kind": "incident_status", "event_id": event_id, "status": "queued" if event_id in self.centre.state["queue"] else incident["status"], "demand_status": "known" if incident.get("demand_source") else "unverified", "deadline_ns": incident["deadline_ns"], "clue_message_id": incident["clue_message_id"]}
            source = incident["clue_source"]
            relay = self._ready_relay()
            route = ["centre", relay, source] if relay and relay != source else ["centre", source]
            reply = self.flight.request("radio_send", destination=source, kind="report", data=data, event_id=event_id, task_id=event_id + "-status", task_revision=1, deadline_ns=max(self.state["sim_ns"] + 30 * NS, incident["deadline_ns"]), route=route)
            incident["clue_ack_submission"] = reply["request_id"]
            save_json(self.centre.path, self.centre.state)

    def _queue_result(self, call: dict[str, Any], result: dict[str, Any]) -> None:
        request_id = call.get("local_request_id", call.get("request_id"))
        if request_id is None:
            return
        self._save()
        save_json(self.root / "tasks" / "tool-requests" / (request_id + ".result.json"), result)
        self.events.append({"event": "tool.failed" if result.get("status") in {"failed", "unavailable", "rejected", "canceled"} else "tool.completed", "request_id": request_id, "operation_id": request_id, "run_id": self.run_id, "uav_id": self.uav_id, "result": result, "sim_ns": self.state["sim_ns"], "task_id": call.get("task_id"), "task_revision": call.get("task_revision")})
        self._decision_event()

    def _received(self, message_id: str) -> dict[str, Any]:
        if message_id not in self.state["seen_messages"]:
            raise ValueError("message is not in this endpoint's committed inbox")
        return json.loads((self.root / "inbox" / message_id / "message.json").read_text())

    def _current_report(self, event_id: str, message_id: str) -> dict[str, Any]:
        message = self._received(message_id)
        incident = self.centre.state["incidents"][event_id]
        expected = incident.get("expected_tasks", {}).get(message["source"])
        if message["event_id"] != event_id or message["kind"] != "report" or not expected or any(message[k] != expected[k] for k in ("task_id", "task_revision")):
            raise ValueError("report does not belong to the current assigned task")
        return message

    def _inspection_report_status(self, event_id: str, message: dict[str, Any]) -> str:
        report = message["data"]
        if report.get("observation_verdict") != "confirmed":
            return "unresolved_evidence"
        transfer = report.get("evidence_transfer") or {}
        incident = self.centre.state["incidents"][event_id]
        received = any(row["kind"] == "file" and row["message_id"] == transfer.get("message_id") and row["file_hash"] == transfer.get("file_hash") and all(row[k] == message[k] for k in ("source", "task_id", "task_revision")) for row in incident["reports"])
        if not received or report.get("verification", {}).get("status") != "confirmed":
            return "unresolved_evidence"
        return "ready" if report.get("drop_site", {}).get("status") == "succeeded" else "unfulfilled_no_resource"

    def _validate_graph(self, event_id: str, graph: dict[str, Any]) -> dict[str, Any]:
        public = self.centre.state["incidents"][event_id].get("graph") or self.config["incident"]["initial_graph"]
        originals = {row["id"]: row for row in public["actions"]}
        actions = graph.get("actions", [])
        if not 0 < len(actions) <= min(4, len(originals)) or len({a["id"] for a in actions}) != len(actions):
            raise ValueError("solver supports a bounded unique subset of the current public actions")
        for action in actions:
            if action["id"] not in originals or any(action.get(k) != originals[action["id"]].get(k) for k in ("target", "role", "duration_s")):
                raise ValueError("changed target/role/duration requires a new notice and new radio bids")
        return {**graph, "payload_kg": public.get("payload_kg", 0)}

    def _control(self, action: str, request: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
        """One explicit Cordis action. No action invokes another policy turn."""
        now = self.state["sim_ns"]
        if action == "wait-until":
            until = int(request["sim_ns"])
            return {"status": "succeeded", "sim_ns": now} if until <= now else {"status": "pending", "control_wait": "timer", "until_ns": until}
        if action in {"accept-task", "apply-radio-control"}:
            message = self._received(request["message_id"])
            mid = message["message_id"]
            if mid in self.state.get("applied_messages", []):
                if action == "accept-task" and self.state.get("pending_task", {}).get("message_id") == mid:
                    return {"status": "pending", "control_wait": "accept", "message_id": mid}
                previous = self.state.get("message_applications", {}).get(mid, {"status": "succeeded", "message_id": mid})
                return {**previous, "already_applied": True}
            if action == "accept-task" and (self.role != "uav" or message["kind"] not in {"notice", "plan"}):
                raise ValueError("accept-task requires a received UAV notice/plan")
            if action == "apply-radio-control" and not (message["kind"] == "report" and message.get("data", {}).get("kind") in {"cancel_assignment", "mission_closed", "relay_ready"}):
                raise ValueError("unsupported received control report")
            self._consume_message(message, apply=True)
            self.state.setdefault("applied_messages", []).append(message["message_id"])
            if action == "accept-task":
                if self.state.get("pending_task", {}).get("message_id") == message["message_id"]:
                    result = {"status": "pending", "control_wait": "accept", "message_id": message["message_id"]}
                    self.state.setdefault("message_applications", {})[mid] = result
                    return result
                if (self.state.get("task") or {}).get("message_id") != message["message_id"]:
                    result = {"status": "rejected", "reason": "stale, protected, revoked or locally infeasible task", "message_id": message["message_id"]}
                    self.state.setdefault("message_applications", {})[mid] = result
                    return result
            result = {"status": "succeeded", "message_id": message["message_id"], "task": copy.deepcopy(self.state.get("task"))}
            self.state.setdefault("message_applications", {})[mid] = result
            return result
        if action == "withdraw-resources":
            if self.role != "uav":
                raise ValueError("local resource withdrawal is a UAV operation")
            change = self.state.get("resource_invalidation")
            if not change:
                raise ValueError("no local resource invalidation is recorded")
            key = change["task"]["event_id"] + "-withdraw-" + str(change["data"]["commitment_version"])
            self._send_resource_report(key, change["data"], change["task"])
            change["submitted"] = True
            return {"status": "pending", "control_wait": "resource", "submission_key": key}
        if self.centre is None:
            raise ValueError("centre controls require the centre endpoint")
        event_id = request.get("event_id")
        if action == "centre-publish":
            saved = self.state.get("solved_plans", {}).get(request["plan_id"])
            if saved is None:
                raise ValueError("unknown locally solved plan_id")
            event_id = saved["event_id"]
        incident = self.centre.state["incidents"][event_id]
        if action in {"centre-solve", "centre-publish", "centre-cancel"} and (incident.get("outcome") or self.centre.state["active"] != event_id):
            raise ValueError("coordination action no longer belongs to the active unresolved incident")
        if action == "centre-solve":
            if incident["status"] != "collecting":
                raise ValueError("solve is available only during the current bid collection round")
            graph = self._validate_graph(event_id, request.get("graph") or incident.get("graph") or self.config["incident"]["initial_graph"])
            result = self.centre.propose_plan(event_id, graph, now)
            plan_id = "plan-" + uuid.uuid4().hex
            self.state.setdefault("solved_plans", {})[plan_id] = {"event_id": event_id, "graph": graph, "result": result}
            incident["saved_plan_id"] = plan_id
            save_json(self.centre.path, self.centre.state)
            return {**result, "status": "succeeded", "solver_status": result["status"], "plan_id": plan_id}
        if action == "centre-publish":
            if saved.get("published"):
                return {"status": "succeeded", "plan_id": request["plan_id"], "already_submitted": True}
            if now >= incident["deadline_ns"] or incident["status"] != "collecting" or incident.get("saved_plan_id") != request["plan_id"]:
                raise ValueError("plan is no longer the active solved proposal")
            for uid, selection in saved["result"].get("selected_bids", {}).items():
                bid = incident["bids"].get(uid)
                if not bid or bid["message_id"] != selection["message_id"] or selection["valid_until_ns"] < now or selection["commitment_version"] < incident.get("resource_versions", {}).get(uid, -1):
                    incident.pop("saved_plan_id", None)
                    save_json(self.centre.path, self.centre.state)
                    raise ValueError("selected radio bid changed or expired; solve again")
            before = len(self.state.get("plan_submissions", []))
            self._publish_plan(event_id, saved["graph"], saved["result"])
            saved["published"] = True
            return {"status": "pending", "control_wait": "radio", "submissions": copy.deepcopy(self.state["plan_submissions"][before:]), "plan_id": request["plan_id"]}
        if action == "centre-notice":
            kind = request.get("kind", "offer")
            if kind in {"offer", "delivery"} and (incident.get("outcome") or self.centre.state["active"] != event_id or now >= incident["deadline_ns"]):
                raise ValueError("new assignment notice requires the current unresolved incident")
            if kind == "delivery":
                report = self._current_report(event_id, request["report_message_id"])
                if self._inspection_report_status(event_id, report) != "ready":
                    raise ValueError("delivery notice requires current confirmed evidence and safe drop site")
                self._broadcast_delivery_notice(event_id, report["data"])
                incident.setdefault("handled_reports", []).append(report["message_id"])
                submissions = incident["notice_requests"]
            elif kind == "offer":
                canceled = incident.get("status") == "canceled_for_resource_change" and incident.get("replan_requested", {}).get("status") == "succeeded"
                if incident.get("status") != "needs_new_notice" and not canceled:
                    raise ValueError("new offer round requires activation or actual radio-confirmed cancellation of current assignments")
                graph = request.get("graph") or incident.get("graph") or self.config["incident"]["initial_graph"]
                if not incident.get("demand_source"):
                    raise ValueError("new rescue assignment needs an explicit public demand source")
                self._broadcast_offer_notice(event_id, graph)
                submissions = incident["notice_requests"]
            elif kind == "closure":
                if not incident.get("outcome"):
                    raise ValueError("closure notice requires a recorded terminal incident outcome")
                self._publish_closures(event_id)
                submissions = incident.get("closure_submissions", [])
            elif kind == "clue-status":
                self._acknowledge_clues(event_id)
                submissions = [{"request_id": incident["clue_ack_submission"]}]
            else:
                raise ValueError("unsupported centre notice kind")
            save_json(self.centre.path, self.centre.state)
            return {"status": "pending", "control_wait": "radio", "submissions": copy.deepcopy(submissions)}
        if action == "centre-cancel":
            replan = incident.get("replan_requested")
            if not replan or request.get("withdrawal_message_id") != replan["withdrawal_message_id"]:
                raise ValueError("cancel must refer to this incident's received resource withdrawal")
            self._centre_resource_replan(event_id, incident)
            return {"status": "pending", "control_wait": "centre-cancel", "event_id": event_id}
        if action == "centre-finish":
            outcome = request["outcome"]
            if incident.get("outcome"):
                if incident["outcome"]["outcome"] != outcome:
                    raise ValueError("incident already has a different terminal outcome")
                return {"status": "succeeded", **incident["outcome"]}
            if outcome == "expired" and now < incident["deadline_ns"]:
                raise ValueError("incident deadline has not elapsed")
            if outcome == "completed_with_delivery":
                report = self._current_report(event_id, request["report_message_id"])
                if report.get("data", {}).get("kind") != "delivery_verified":
                    raise ValueError("current carrier delivery report required")
                self.centre.verified_delivery(event_id, report["data"]["delivery"], carrier_id=report["source"], now_ns=now)
                incident.setdefault("handled_reports", []).append(report["message_id"])
            result = self.centre.finish(event_id, outcome, now)
            save_json(self.root / "outcomes" / (event_id + ".json"), result)
            save_json(self.root / "outcome.json", result)
            return {"status": "succeeded", **result}
        raise ValueError("unknown rescue control action: " + action)

    def _advance_controls(self) -> None:
        for operation in self.state.setdefault("control_operations", {}).values():
            if operation["status"] != "pending":
                continue
            result = operation["result"]
            try:
                kind = result["control_wait"]
                if kind == "timer":
                    final = {"status": "succeeded", "sim_ns": self.state["sim_ns"]} if self.state["sim_ns"] >= result["until_ns"] else None
                elif kind == "accept":
                    pending = self.state.get("pending_task")
                    if pending and pending["message_id"] == result["message_id"] and self.active_tool is None and self.future is None and not any(m["status"] not in TERMINAL for m in self.state["missions"].values()):
                        self.state.pop("pending_task")
                        self._consume_message(pending, apply=True)
                    final = {"status": "succeeded", "task": self.state["task"]} if (self.state.get("task") or {}).get("message_id") == result["message_id"] else None
                    data = self._received(result["message_id"])["data"]
                    deadline = data.get("deadline_ns", data.get("task", {}).get("deadline_ns", data.get("incident", {}).get("deadline_ns", self.context()["deadline_ns"])))
                    if final is None and deadline <= self.state["sim_ns"]:
                        final = {"status": "failed", "reason": "task acceptance deadline"}
                        if self.state.get("pending_task", {}).get("message_id") == result["message_id"]:
                            self.state.pop("pending_task")
                    if final is not None:
                        self.state.setdefault("message_applications", {})[result["message_id"]] = copy.deepcopy(final)
                elif kind == "resource":
                    entry = self.state["resource_submissions"][result["submission_key"]]
                    final = entry if entry["status"] in TERMINAL else {**entry, "status": "failed", "reason": "reconcile_required: no acknowledged radio submission; do not replay"} if entry["status"] == "reconcile_required" else None
                elif kind == "centre-cancel":
                    incident = self.centre.state["incidents"][result["event_id"]]
                    self._centre_resource_replan(result["event_id"], incident)
                    replan = incident["replan_requested"]
                    final = copy.deepcopy(replan) if replan.get("status") in TERMINAL else None
                else:
                    replies = []
                    for submission in result["submissions"]:
                        reply = self._poll_pending(submission)
                        replies.append(reply)
                    final = {"status": "succeeded" if all(r.get("status") == "succeeded" for r in replies) else "failed", "transfers": replies} if all(r.get("status") in TERMINAL for r in replies) else None
                if final is not None:
                    operation.update(status=final["status"], result=final)
                    self._queue_result(operation["call"], final)
            except (ValueError, RuntimeError, KeyError) as exc:
                operation.update(status="failed", result={"status": "failed", "reason": str(exc)})
                self._queue_result(operation["call"], operation["result"])

    def _centre_facts(self, local: dict[str, Any]) -> None:
        # Acknowledgements of an already submitted notice are outbox facts.
        for notice in local.get("notice_submissions", []):
            event_id = notice["event_id"]
            if not self.centre.state["incidents"].get(event_id, {}).get("notice_anchor_confirmed"):
                self.centre.notice_submitted(event_id, notice["submitted_ns"], notice["deadline_ns"], notice["demand_source"])
                incident = self.centre.state["incidents"][event_id]
                incident["bid_tasks"] = {uid: {"task_id": row.get("initial_task", {}).get("task_id", "initial-" + uid), "task_revision": row.get("initial_task", {}).get("task_revision", 1)} for uid, row in self.config["vehicles"].items()}
                incident["bids"] = {uid: bid for uid, bid in incident["bids"].items() if uid in incident["bid_tasks"] and all(bid.get(k) == incident["bid_tasks"][uid][k] for k in ("task_id", "task_revision"))}
                self._decision_event()
        for event_id, incident in self.centre.state["incidents"].items():
            if incident["status"] != "submitting_notice":
                continue
            submitted = [reply["submitted_ns"] for item in incident["notice_requests"] if (reply := self.flight.request("result", pending_request_id=item["request_id"])).get("submitted_ns") is not None]
            if submitted:
                self.centre.notice_submitted(event_id, min(submitted), incident["deadline_ns"], incident["demand_source"])
                self._decision_event()
        save_json(self.centre.path, self.centre.state)

    def _deadline_proposal(self) -> dict[str, Any] | None:
        deadlines = [i["deadline_ns"] for i in self.centre.state["incidents"].values() if not i.get("outcome") and i.get("deadline_ns", 0) > self.state["sim_ns"]]
        if not deadlines:
            return None
        until = min(deadlines)
        if any(op["status"] == "pending" and op["result"].get("control_wait") == "timer" and op["result"].get("until_ns") == until for op in self.state.get("control_operations", {}).values()):
            return None
        return {"control": "wait-until", "request": {"sim_ns": until}, "result_key": "incident-deadline-" + str(until)}

    def _centre_proposal(self) -> dict[str, Any] | None:
        def action(name: str, **request: Any) -> dict[str, Any]:
            return {"control": name, "request": request, "result_key": "centre-" + name + "-" + str(self.state.get("decision_event_version", 0)), "continue_on_unavailable": True}
        now = self.state["sim_ns"]
        for event_id, incident in self.centre.state["incidents"].items():
            if incident.get("outcome") and not incident.get("closure_submissions"):
                return action("centre-notice", event_id=event_id, kind="closure")
            if incident.get("clue_message_id") and not incident.get("clue_ack_submission"):
                return action("centre-notice", event_id=event_id, kind="clue-status")
            if not incident.get("outcome") and now >= incident.get("deadline_ns", 2**63):
                return action("centre-finish", event_id=event_id, outcome="expired")
        event_id = self.centre.state["active"]
        if event_id is None:
            return self._deadline_proposal()
        incident = self.centre.state["incidents"][event_id]
        replan = incident.get("replan_requested")
        if replan:
            if replan.get("status") == "succeeded":
                return action("centre-notice", event_id=event_id, kind="offer")
            if replan.get("status") == "failed":
                return action("centre-finish", event_id=event_id, outcome="failed_execution")
            if "cancel_requests" not in replan and replan["received_ns"] <= now:
                return action("centre-cancel", event_id=event_id, withdrawal_message_id=replan["withdrawal_message_id"])
            return None
        if incident["status"] == "needs_new_notice":
            return action("centre-notice", event_id=event_id, kind="offer") if incident.get("graph") and incident.get("demand_source") else action("centre-finish", event_id=event_id, outcome="unresolved_evidence")
        if incident["status"] == "collecting":
            until = incident.get("collect_until_ns")
            if until is None:
                return None
            if now < until:
                if any(op["status"] == "pending" and op["result"].get("control_wait") == "timer" and op["result"].get("until_ns") == until for op in self.state.get("control_operations", {}).values()):
                    return None
                return action("wait-until", sim_ns=until)
            plan_id = incident.get("saved_plan_id")
            saved = self.state.get("solved_plans", {}).get(plan_id)
            if not saved:
                return action("centre-solve", event_id=event_id)
            if saved["result"]["status"] == "feasible":
                return action("centre-publish", plan_id=plan_id)
            return action("centre-finish", event_id=event_id, outcome="unfulfilled_no_resource")
        if incident["status"] == "executing":
            for message in incident["reports"]:
                expected = incident.get("expected_tasks", {}).get(message["source"])
                if message["kind"] != "report" or message["message_id"] in incident.get("handled_reports", []) or not expected or any(message[k] != expected[k] for k in ("task_id", "task_revision")):
                    continue
                report = message["data"]
                if "observation_verdict" in report:
                    outcome = self._inspection_report_status(event_id, message)
                    return action("centre-notice", event_id=event_id, kind="delivery", report_message_id=message["message_id"]) if outcome == "ready" else action("centre-finish", event_id=event_id, outcome=outcome)
                if report.get("kind") == "delivery_failed":
                    return action("centre-finish", event_id=event_id, outcome="failed_execution")
                if report.get("kind") == "delivery_verified":
                    try:
                        self.centre.delivery_proof(event_id, report["delivery"], carrier_id=message["source"], now_ns=now)
                    except ValueError:
                        continue
                    return action("centre-finish", event_id=event_id, outcome="completed_with_delivery", report_message_id=message["message_id"])
        return self._deadline_proposal()

    def propose(self) -> dict[str, Any] | None:
        """Compute a pure baseline proposal. Only Cordis may submit it."""
        self.proposal_continuation = {}
        if self.state.get("control_inflight"):
            return None  # Interrupted side effect needs reconciliation, not another policy replay.
        applied = self.state.get("applied_messages", [])
        for mid in reversed(self.state["seen_messages"]):
            message = self._received(mid)
            if mid in applied:
                continue
            kind = message.get("data", {}).get("kind")
            if message["kind"] == "report" and kind in {"cancel_assignment", "mission_closed", "relay_ready"}:
                return {"control": "apply-radio-control", "request": {"message_id": mid}, "result_key": "apply-" + mid, "continue_on_unavailable": True}
        if self.role == "uav":
            change = self.state.get("resource_invalidation")
            if change and not change.get("submitted"):
                return {"control": "withdraw-resources", "request": {}, "result_key": "withdraw-" + str(self.state["commitment_version"]), "continue_on_unavailable": True}
            for mid in reversed(self.state["seen_messages"]):
                if mid not in applied and self._received(mid)["kind"] in {"notice", "plan"}:
                    return {"control": "accept-task", "request": {"message_id": mid}, "result_key": "accept-" + mid, "continue_on_unavailable": True}
        if self.future or self.active_tool:
            return None
        if self.centre is not None:
            return self._centre_proposal()
        if any(op["status"] == "pending" for op in self.state.get("control_operations", {}).values()):
            return None
        if not self.state.get("task") or self._assignment_is_canceled(self.state["task"]) or self.state.get("task_blocker") and not self.state.get("mission_closed"):
            return None
        # Existing rule helpers use cursors. Compute them against a detached copy;
        # commit only when Cordis explicitly submits the matching proposal.
        view = copy.copy(self)
        view.state = copy.deepcopy(self.state)
        view._log = lambda *args: None
        call = view._closure_return() if view.state.get("mission_closed") else view._rule_decision()
        keys = ("step_index", "inspection", "relay_monitor", "delivery_wait")
        self.proposal_continuation = {k: copy.deepcopy(view.state[k]) for k in keys if k in view.state}
        if call is None and view.state.get("relay_policy"):
            monitor = view.state.get("relay_monitor", {})
            until = max(self.state["sim_ns"] + NS, monitor.get("retry_after_ns", 0), monitor.get("checked_ns", self.state["sim_ns"]) + 5 * NS)
            call = {"control": "wait-until", "request": {"sim_ns": until}, "result_key": "relay-wait-" + str(until)}
        return call

    def _publish_harness(self, *, ready: bool = False) -> None:
        context = {"run_id": self.run_id, "uav_id": self.uav_id, "role": self.role, "execution_backend": self.config.get("execution_backend", "native"), "sim_ns": self.state["sim_ns"], "decision_event_version": self.state.get("decision_event_version", 0), **self._local_context(self.state.get("self_state", {})), "resource_invalidation": self.state.get("resource_invalidation"), "task_blocker": self.state.get("task_blocker"), "control_operations": self.state.get("control_operations", {}), "gateway": self.state.get("gateway", {"status": "unknown"}), "unacknowledged_control": self.state.get("control_inflight")}
        if self.centre is not None:
            context["centre"] = self.centre.state
            context["public_action_graph"] = self.config.get("incident", {}).get("initial_graph")
        context["control_inputs"] = {
            "accept-task": {"message_id": "own received notice or plan ID; UAV only"},
            "apply-radio-control": {"message_id": "own received cancel_assignment, mission_closed or relay_ready report ID"},
            "withdraw-resources": "empty object; uses the recorded local resource invalidation",
            "wait-until": {"sim_ns": "integer future episode simulation time"},
            "centre-solve": {"event_id": "active incident ID", "graph": "optional bounded subset/order of current public actions"},
            "centre-publish": {"plan_id": "ID returned by centre-solve; publishes exact stored result"},
            "centre-notice": {"event_id": "incident ID", "kind": "offer | delivery | closure | clue-status", "report_message_id": "required for delivery; current confirmed report", "graph": "optional new offer graph; requires fresh radio bids"},
            "centre-cancel": {"event_id": "incident ID", "withdrawal_message_id": "received resource withdrawal ID"},
            "centre-finish": {"event_id": "incident ID", "outcome": "completed_with_delivery | rejected_no_target | unresolved_evidence | unfulfilled_no_resource | expired | failed_execution", "report_message_id": "required for completed_with_delivery; independent receipt and ACK notice also checked"},
        }
        try:
            self.last_proposal = self.propose()
        except (KeyError, ValueError, OSError) as exc:
            self.last_proposal = None
            context["proposal_error"] = str(exc)
            if self.state.get("proposal_error") != str(exc):
                self.state["proposal_error"] = str(exc)
                self._decision_event()
                self._log("runtime-errors.jsonl", {"proposal_error": str(exc)})
        context["decision_event_version"] = self.state.get("decision_event_version", 0)
        save_json(self.root / "harness-context.json", context)
        save_json(self.root / "rule-proposal.json", self.last_proposal)
        self._save()
        events, self.events = self.events, []
        if ready:
            events.insert(0, {"event": "ready", "run_id": self.run_id, "uav_id": self.uav_id})
        version = self.state.get("decision_event_version", 0)
        if version != self.published_version:
            events.append({"event": "context.changed", "run_id": self.run_id, "uav_id": self.uav_id, "decision_event_version": version, "sim_ns": self.state["sim_ns"]})
            self.published_version = version
        for event in events:
            print(json.dumps(event, ensure_ascii=False), flush=True)

    def _accept_queued(self) -> None:
        queued_calls = [(path, json.loads(path.read_text())) for path in (self.root / "tasks" / "tool-requests").glob("*.queued.json")]
        queued_calls.sort(key=lambda item: (0 if item[1].get("control") == "apply-radio-control" else 1 if "control" in item[1] else 2, item[0].name))
        for queued, call in queued_calls:
            if "control" not in call and (self.active_tool or self.future):
                continue
            queued.rename(queued.with_name(queued.name.replace(".queued.json", ".running.json")))
            call.update(local_request_id=call["request_id"], policy_submission=bool(call.get("result_key")))
            call.setdefault("result_key", "cli-" + call["request_id"])
            call.setdefault("task_id", self.context()["task_id"])
            call.setdefault("task_revision", self.context()["task_revision"])
            started_control = False
            try:
                if "control" not in call and call.get("task_id") is not None and (call["task_id"], call.get("task_revision")) != (self.context()["task_id"], self.context()["task_revision"]):
                    raise ValueError("queued tool belongs to an obsolete accepted task")
                closed = self.state.get("mission_closed", {}).get("event_id") == (self.state.get("task") or {}).get("event_id") and self.state.get("mission_closed")
                if "control" not in call and call.get("tool") in {"flight-command", "search-area", "reobserve-target", "hold-relay", "deliver-supply"} and (closed or self._assignment_is_canceled(self.state.get("task") or {})):
                    raise ValueError("task was closed or canceled; no new rescue physical action may start")
                if self.last_proposal and all(call.get(k) == self.last_proposal.get(k) for k in ("tool", "control", "request", "result_key")):
                    self.state.update(copy.deepcopy(self.proposal_continuation))
                if "control" in call:
                    if self.state.get("control_inflight"):
                        raise ValueError("reconcile_required: previous control submission has no persisted acknowledgement")
                    self.state["control_inflight"] = call
                    started_control = True
                    self._save()
                    result = self._control(call["control"], call.get("request", {}), call)
                    self.state.pop("control_inflight")
                    if result.get("control_wait"):
                        self.state.setdefault("control_operations", {})[call["request_id"]] = {"status": "pending", "call": call, "result": result}
                    else:
                        self._queue_result(call, result)
                else:
                    self._start_tool(call)
            except (OSError, ValueError, RuntimeError, KeyError) as exc:
                if started_control and not isinstance(exc, (OSError, RuntimeError, json.JSONDecodeError)):
                    self.state.pop("control_inflight", None)
                reason = "reconcile_required: control acknowledgement unknown; " + str(exc) if started_control and self.state.get("control_inflight") else str(exc)
                self._queue_result(call, {"status": "failed", "reason": reason})
            self._save()
            if "control" not in call:
                break

    def tick(self) -> None:
        try:
            local = self.flight.state()
        except (OSError, RuntimeError) as exc:
            # Historical tools remain usable without inventing a simulator clock.
            unavailable = {"status": "unavailable", "reason": str(exc)}
            if self.state.get("gateway") != unavailable:
                self.state["gateway"] = unavailable
                self._decision_event()
            self._accept_queued()
            if self.future and self.future.done():
                future, self.future = self.future, None
                try:
                    result = future.result()
                except Exception as error:
                    result = {"status": "failed", "reason": str(error)}
                self._complete_tool(result, self.future_call or {})
            self._save()
            return
        self.state["gateway"] = {"status": "available"}
        self.state["sim_ns"] = local["sim_ns"]
        previous = self.state.get("self_state", {})
        if tuple(local.get(k) for k in ("status", "armed", "landed_state")) != tuple(previous.get(k) for k in ("status", "armed", "landed_state")):
            self._decision_event()
        self.state["self_state"] = local
        for package_id, package in self.tools.payload.packages.items():
            previous_status = package.get("status")
            observed = self.tools.payload.status(package_id, self.state["sim_ns"])
            if observed.get("status") != previous_status:
                self.state["delivery_wait"] = observed
                self._decision_event()
        if self.role == "uav":
            self._resource_tick(local)
        outgoing = self.state.get("handover_outgoing")
        if outgoing and outgoing.get("status") == "offered" and not outgoing.get("deadline_observed") and self.state["sim_ns"] >= outgoing["data"]["deadline_ns"]:
            outgoing["deadline_observed"] = True
            self._decision_event()  # Timer fact only; Cordis still selects the response.
        for message in self.flight.request("radio_inbox").get("messages", []):
            self._persist_message(message)
        if self.centre is not None:
            self._centre_facts(local)
        # Read explicit urgent controls before advancing the next physical step.
        self._accept_queued()
        self._advance_missions()
        if self.active_tool:
            active = self.active_tool
            canceled = self._assignment_is_canceled(self.state.get("task") or {})
            if "flight_operation_id" in active and (canceled or self.state.get("mission_closed") and active["call"]["tool"] != "resume-or-return"):
                self.flight.cancel(active["flight_operation_id"])
            result = self.state["missions"][active["operation_id"]] if "operation_id" in active else self.flight.poll(active["flight_operation_id"]) if "flight_operation_id" in active else self._poll_pending(active)
            if result.get("status") in TERMINAL | {"submitted", "available", "confirmed", "uncertain"}:
                if canceled and "flight_operation_id" in active:
                    self.state["assignment_cancel"]["operation_results"].append(result)
                self.active_tool = None
                self._complete_tool(result, active["call"])
        if self.future and self.future.done():
            future, self.future = self.future, None
            try:
                result = future.result()
            except Exception as exc:
                result = {"status": "failed", "reason": str(exc)}
            self._complete_tool(result, self.future_call or {})
        self._assignment_cancel_tick()
        self._advance_controls()
        self._save()


    def close(self) -> None:
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self._save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--role", choices=["uav", "centre"], default="uav")
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID"), required=not bool(os.environ.get("RUN_ID")))
    parser.add_argument("--uav-id", default=os.environ.get("UAV_ID", "centre"))
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "/data"))
    parser.add_argument("--socket", default=os.environ.get("COMPANION_SOCKET", "/run/aas-uav/agent.sock"))
    parser.add_argument("--agent-mode", choices=["rule", "model"], default=os.environ.get("AGENT_MODE", "rule"))
    parser.add_argument("--execution-only", action="store_true", default=True, help="Advance submitted operations; Cordis owns decisions")
    args = parser.parse_args()
    args.config = args.config or str(scenario_path())
    runtime = CompanionRuntime(yaml.safe_load(Path(args.config).read_text()), run_id=args.run_id, uav_id=args.uav_id, data_dir=args.data_dir, socket_path=args.socket, agent_mode=args.agent_mode, role=args.role, execution_only=args.execution_only)
    runtime._publish_harness(ready=True)
    signal.signal(signal.SIGTERM, lambda *_: runtime.close())
    signal.signal(signal.SIGINT, lambda *_: runtime.close())
    while not runtime.closed:
        try:
            runtime.tick()
            runtime._publish_harness()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            runtime._log("runtime-errors.jsonl", {"error": str(exc)})
            runtime._publish_harness()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
