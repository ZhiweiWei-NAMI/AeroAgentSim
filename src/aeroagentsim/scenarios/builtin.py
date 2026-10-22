"""Built-in per-scenario plugins: one template per plugin."""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

from aeroagentsim.plugin_api import ScenarioPlugin, TemplateSource


def _template_directory(identifier: str) -> Path:
    """Locate one shipped template, preferring installed package data."""
    installed = importlib.resources.files("aeroagentsim") / "template_data" / identifier
    if (installed / "template.yaml").is_file():
        return Path(str(installed)).resolve()
    source = (
        Path(__file__).resolve().parents[3] / "scenarios" / "templates" / identifier
    )
    if (source / "template.yaml").is_file():
        return source
    raise FileNotFoundError(f"{identifier} template inputs are not installed")


def template_plugin(identifier: str, **source_options: Any) -> ScenarioPlugin:
    """Build the plugin whose single template is identified by ``identifier``."""
    return ScenarioPlugin(
        name=identifier,
        templates=(
            TemplateSource(
                directory=_template_directory(identifier), **source_options
            ),
        ),
    )


def template_plugin_for(identifier: str) -> ScenarioPlugin:
    """The built-in plugin for one shipped template."""
    if identifier == "traffic-accident":
        from aeroagentsim.authoring.templates import demo_source
        from aeroagentsim.packs.traffic_accident.knobs import weather_knobs

        # The scenario ships as demo package data with derived weather knobs.
        return template_plugin(
            identifier,
            scenario=demo_source(identifier) / "scenario.yaml",
            extra_knobs=weather_knobs,
        )
    return template_plugin(identifier)


__all__ = ["template_plugin", "template_plugin_for"]
