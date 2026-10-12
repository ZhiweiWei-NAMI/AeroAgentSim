"""Traffic camera plugin delegates to the single shared committed-world renderer."""
from __future__ import annotations

from dataclasses import replace

from aeroagentsim.observations.capture import Capture
from aeroagentsim.platform.plugins import EngineBuild

# Preserve the public pack import used by city-day; Capture handles both old
# traffic_browser configuration and the versioned camera observation manifest.
TrafficCameraCapture = Capture


def build(context: EngineBuild) -> Capture:
    if context.config["run_directory"] == "@run":
        if context.run_directory is None:
            raise ValueError("traffic capture requires a RunSession artifact directory")
        context = replace(
            context,
            config={**context.config, "run_directory": str(context.run_directory)},
        )
    return Capture(context)
