"""Replacement contract validation; native readiness requires actual services."""

from __future__ import annotations

from typing import Any

from aeroagentsim.adapters.px4_gazebo import ACTIONS as PX4_ACTIONS
from aeroagentsim.adapters.sumo import ACTIONS as SUMO_ACTIONS


def validate_profile(profile: dict[str, Any]) -> tuple[str, ...]:
    """Return precise unresolved requirements, without certifying native execution."""
    if profile["format"] != "traffic-accident-replacement-profile/v1":
        raise ValueError("unsupported traffic replacement profile")
    if profile["switch_policy"] != "new_run_and_epoch":
        raise ValueError("kernel does not support hot writer transfer")
    if (
        type(profile["sampled_return_lag_ns"]) is not int
        or profile["sampled_return_lag_ns"] <= 0
    ):
        raise ValueError("sampled physical feedback needs an explicit positive lag")
    owned: set[str] = set()
    for owner, entry in profile["owners"].items():
        fields = entry["fields"]
        if len(set(fields)) != len(fields) or owned & set(fields):
            raise ValueError(f"profile ownership overlaps at {owner}")
        owned.update(fields)
        capabilities = profile["capabilities"].get(owner, [])
        native = {"sumo": SUMO_ACTIONS, "px4_gazebo": PX4_ACTIONS}
        if entry["plugin"] in native and not set(capabilities) <= set(
            native[entry["plugin"]]
        ):
            raise ValueError(f"profile advertises unsupported native commands: {owner}")
    if profile["id"] != "kinematic" and not profile["remove_owners"]:
        raise ValueError("native replacement must remove the previous physical owner")
    return tuple(profile["configuration_required"])
