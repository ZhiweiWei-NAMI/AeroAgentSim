"""Existing traffic camera with discovery of real installed headless Chromium."""

from __future__ import annotations

import copy
import os
from dataclasses import replace
from pathlib import Path

from aeroagentsim.packs.traffic_accident.camera import TrafficCameraCapture
from aeroagentsim.packs.traffic_accident.camera import build as build_traffic_camera
from aeroagentsim.platform.plugins import EngineBuild


def build(context: EngineBuild) -> TrafficCameraCapture:
    config = copy.deepcopy(context.config)
    renderer = config["renderer"]
    if (
        "browser_executable" not in renderer
        and "AEROAGENTSIM_CHROMIUM" not in os.environ
    ):
        # Playwright's installed cache can contain a usable headless shell from
        # another package revision. Use the actual executable, never a fixture.
        cache = (
            Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
            if "PLAYWRIGHT_BROWSERS_PATH" in os.environ
            else Path.home() / ".cache/ms-playwright"
        )
        installed = sorted(
            cache.glob(
                "chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"
            )
        )
        if installed:
            renderer["browser_executable"] = str(installed[-1])
    return build_traffic_camera(replace(context, config=config))
