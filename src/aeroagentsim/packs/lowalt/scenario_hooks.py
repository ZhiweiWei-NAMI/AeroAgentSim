"""Low-altitude scenario plugin hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aeroagentsim.plugin_api import ScenarioPlugin


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


def plugin() -> ScenarioPlugin:
    return ScenarioPlugin(
        name="lowalt",
        templates=(),
        apply_hooks={"lowalt.operator_mode": _operator_mode},
    )


__all__ = ["plugin"]
