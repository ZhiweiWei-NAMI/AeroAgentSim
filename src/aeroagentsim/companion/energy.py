"""A declared onboard energy budget integrated from this UAV's PX4 estimates.

This is a research consumption model, not a replacement PX4 battery sensor.
The initial reserve and power coefficients are explicit scenario assumptions.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from .transport import NS, encoded, save_json


class LocalEnergyLedger:
    def __init__(self, root: str | Path, profile: dict[str, Any], *,
                 run_id: str, uav_id: str) -> None:
        self.root, self.run_id, self.uav_id = Path(root), run_id, uav_id
        budget = profile.get("energy_budget")
        self.enabled = budget is not None
        self.path = self.root / "energy-state.json"
        self.state: dict[str, Any] = {}
        if not self.enabled:
            return
        self.parameters = {
            "capacity_j": float(profile["capacity_j"]),
            "initial_fraction": float(budget["initial_fraction"]),
            "hover_power_w": float(profile["hover_power_w"]),
            "energy_per_m_j": float(profile["energy_per_m_j"]),
            "max_gap_ns": int(budget.get("max_gap_ns", NS)),
        }
        if (not all(math.isfinite(value) for value in self.parameters.values())
                or self.parameters["capacity_j"] <= 0
                or not 0 <= self.parameters["initial_fraction"] <= 1
                or self.parameters["hover_power_w"] < 0
                or self.parameters["energy_per_m_j"] < 0
                or self.parameters["max_gap_ns"] <= 0):
            raise ValueError("energy budget requires finite capacity, reserve and consumption parameters")
        self.state = {"run_id": run_id, "uav_id": uav_id,
            "parameters": self.parameters,
            "remaining_j": self.parameters["capacity_j"] * self.parameters["initial_fraction"],
            "last": None, "integrated_time_s": 0.0, "integrated_distance_m": 0.0}
        if self.path.exists():
            previous = json.loads(self.path.read_text())
            if (previous["run_id"], previous["uav_id"], previous["parameters"]) != (run_id, uav_id, self.parameters):
                raise ValueError("energy ledger belongs to another run, UAV or declared model")
            self.state = previous

    def _result(self, state: dict[str, Any], status: str, reason: str | None = None,
                interval: dict[str, Any] | None = None) -> dict[str, Any]:
        last = self.state.get("last") or {}
        available = status == "available"
        result = {"status": status, "source": "declared_onboard_energy_model",
            "kinematics_source": "PX4_MavlinkDirect/LOCAL_POSITION_NED",
            "initial_reserve_source": "explicit scenario energy_budget.initial_fraction",
            "integration": "trapezoidal PX4 speed plus observed airborne hover power over source time",
            "available_energy_j": self.state["remaining_j"] if available else None,
            "effective_fraction": self.state["remaining_j"] / self.parameters["capacity_j"] if available else None,
            "source_sim_ns": last.get("source_sim_ns"), "clock_epoch": last.get("clock_epoch"),
            "source_time_domain": "episode_sim", "input_clock": last.get("source_clock"),
            "clock_mapping_uncertainty_ns": last.get("clock_mapping_uncertainty_ns"),
            "observed_sim_ns": state.get("sim_ns"), "parameters": dict(self.parameters),
            "integrated_time_s": self.state["integrated_time_s"],
            "integrated_distance_m": self.state["integrated_distance_m"],
            "px4_battery": copy.deepcopy(state.get("battery")),
            "reason": reason}
        self.state["snapshot"] = result
        save_json(self.path, self.state)
        log = self.root / "logs" / "energy-ledger.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as stream:
            stream.write(encoded({"run_id": self.run_id, "uav_id": self.uav_id,
                **result, "interval": interval}) + b"\n")
        return result

    def observe(self, state: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "available_energy_j": None,
                "effective_fraction": None, "source": None,
                "source_sim_ns": None, "clock_epoch": None,
                "px4_battery": copy.deepcopy(state.get("battery"))}
        if self.state.get("blocked_reason"):
            return self._result(state, "unavailable", self.state["blocked_reason"])
        estimate = state.get("vehicle_estimate") or {}
        if (state.get("uav_id") != self.uav_id or estimate.get("status") != "available"
                or estimate.get("source") != "PX4_MavlinkDirect"):
            return self._result(state, "unavailable", "own PX4 source estimate unavailable")
        stamp, now, epoch = estimate.get("source_sim_ns"), state.get("sim_ns"), estimate.get("clock_epoch")
        velocity = estimate.get("velocity_enu_m_s")
        if (type(stamp) is not int or type(now) is not int or epoch is None
                or not isinstance(velocity, (list, tuple)) or len(velocity) != 3
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in velocity)):
            return self._result(state, "unavailable", "PX4 source time, epoch or velocity missing")
        if stamp > now or now - stamp > 250_000_000:
            return self._result(state, "unavailable", "PX4 source estimate is future or stale")
        landed = state.get("landed_state")
        if type(state.get("armed")) is not bool or landed not in {"ON_GROUND", "IN_AIR", "TAKING_OFF", "LANDING"}:
            return self._result(state, "unavailable", "local airborne power state unknown")
        sample = {"source_sim_ns": stamp, "clock_epoch": epoch,
            "velocity_enu_m_s": list(velocity), "speed_m_s": math.hypot(*velocity),
            "airborne": state["armed"] and landed != "ON_GROUND",
            "source_clock": estimate.get("source_clock"),
            "clock_mapping_uncertainty_ns": (estimate.get("clock_map") or {}).get("uncertainty_ns")}
        last = self.state["last"]
        if last is None:
            self.state["last"] = sample
            return self._result(state, "available", "declared reserve anchored to first valid local source sample")
        delta = stamp - last["source_sim_ns"]
        if epoch != last["clock_epoch"] or delta < 0 or delta > self.parameters["max_gap_ns"]:
            self.state["blocked_reason"] = "source epoch changed or source-time continuity lost; balance remains unknown until a fresh run"
            return self._result(state, "unavailable", self.state["blocked_reason"])
        if delta == 0:
            return self._result(state, "available", "duplicate source sample; no energy debited")
        elapsed = delta / NS
        distance = (last["speed_m_s"] + sample["speed_m_s"]) * 0.5 * elapsed
        hover_j = (int(last["airborne"]) + int(sample["airborne"])) * 0.5 * self.parameters["hover_power_w"] * elapsed
        distance_j = distance * self.parameters["energy_per_m_j"]
        debit = hover_j + distance_j
        before = self.state["remaining_j"]
        self.state["remaining_j"] = max(0.0, before - debit)
        self.state["integrated_time_s"] += elapsed
        self.state["integrated_distance_m"] += distance
        self.state["last"] = sample
        return self._result(state, "available", interval={"from": last, "to": sample,
            "elapsed_s": elapsed, "distance_m": distance,
            "hover_energy_j": hover_j, "distance_energy_j": distance_j,
            "debited_energy_j": debit, "remaining_before_j": before,
            "remaining_after_j": self.state["remaining_j"]})
