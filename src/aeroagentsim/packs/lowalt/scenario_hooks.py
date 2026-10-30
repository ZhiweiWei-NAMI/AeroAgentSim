"""Low-altitude scenario plugin hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.plugin_api import ScenarioPlugin
from aeroagentsim.scenario.loader import UniqueLoader


def _operator_mode(document: dict[str, Any], base: Path, value: Any) -> None:
    operator = next(
        stream for stream in document["ingress_streams"] if stream["id"] == "operator"
    )
    operator["initial_watermark_ns"] = {
        "scheduled": document["run"]["until_ns"], "interactive": 0,
    }[value]
    if value == "interactive":
        # Omission is the ingress schema's explicit unlimited-wait policy;
        # null is not a valid numeric timeout. Stop remains available.
        operator.pop("timeout_s", None)


def _motion_backend(document: dict[str, Any], base: Path, value: Any) -> None:
    """Select the low-altitude motion owner; the scheduler kernel stays authoritative."""
    if value not in {"kinematic", "px4_gazebo"}:
        raise ValueError(f"knob motion_backend: unsupported backend {value!r}")
    motion = document.get("engines", {}).get("motion")
    if not isinstance(motion, dict):
        raise ValueError("knob motion_backend: lowalt requires a motion engine")
    if value == "kinematic":
        # The built-in kinematic scheduler engine computes flight motion
        # directly and therefore replaces Gazebo for high-speed simulation.
        motion["plugin"] = "kinematic"
        return
    from aeroagentsim.packs.traffic_accident.profiles import validate_profile

    profile_path = base / "profiles/px4.yaml"
    if not profile_path.is_file():
        raise ValueError(
            "knob motion_backend: px4_gazebo requires an external PX4/Gazebo "
            "service contract at profiles/px4.yaml; the low-altitude templates ship "
            "only the in-process kinematic scheduler engine"
        )
    profile = yaml.load(profile_path.read_bytes(), Loader=UniqueLoader)
    requirements = validate_profile(profile)
    # Fail closed: a configuration-contract-only profile must never look runnable.
    raise ValueError(
        "knob motion_backend: px4_gazebo requires external PX4/Gazebo service "
        "configuration: " + "; ".join(requirements)
    )


def plugin() -> ScenarioPlugin:
    return ScenarioPlugin(
        name="lowalt",
        templates=(),
        apply_hooks={
            "lowalt.operator_mode": _operator_mode,
            "lowalt.motion_backend": _motion_backend,
        },
    )


__all__ = ["plugin"]
