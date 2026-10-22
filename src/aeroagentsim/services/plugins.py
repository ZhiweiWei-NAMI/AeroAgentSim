"""Read-only inventory of every installed plugin family."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

import yaml

import aeroagentsim.plugin_api as plugin_api

MODEL_BUILTINS = ("linear", "wind")


def _distribution(entry: Any) -> str | None:
    distribution = getattr(entry, "dist", None)
    name = getattr(distribution, "name", None)
    return name if isinstance(name, str) else None


def _entries(group: str) -> dict[str, Any]:
    return {entry.name: entry for entry in entry_points(group=group)}


def _template_id(source: Any) -> str:
    path = source.directory / "template.yaml"
    metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
        raise ValueError(f"template source {path}: template.yaml needs a string id")
    return metadata["id"]


def _scenario_rows() -> list[dict[str, Any]]:
    installed = {
        entry.name: entry
        for entry in plugin_api.entry_points(group="aeroagentsim.scenarios")
    }
    rows = []
    for plugin in plugin_api.discover_scenario_plugins():
        entry = installed.get(plugin.name)
        rows.append(
            {
                "name": plugin.name,
                "origin": "entry-point" if entry is not None else "builtin",
                "distribution": _distribution(entry) if entry is not None else None,
                "templates": sorted(_template_id(source) for source in plugin.templates),
                "apply_hooks": sorted(plugin.apply_hooks),
            }
        )
    return rows


def _engine_rows() -> list[dict[str, Any]]:
    from aeroagentsim.platform.plugins import BUILTINS

    installed = _entries("aeroagentsim.engines")
    return [
        {
            "name": name,
            "origin": "entry-point" if name in installed else "builtin",
            "distribution": _distribution(installed[name])
            if name in installed
            else None,
        }
        for name in sorted(set(BUILTINS) | set(installed))
    ]


def _algorithm_rows() -> list[dict[str, Any]]:
    from aeroagentsim.algorithms import BUILTINS

    # Slot-qualified names, the form entry points use (``allocation.nearest``).
    installed = _entries("aeroagentsim.algorithms")
    builtins = {f"{slot}.{name}" for slot, names in BUILTINS.items() for name in names}
    return [
        {
            "name": name,
            "origin": "entry-point" if name in installed else "builtin",
            "distribution": _distribution(installed[name])
            if name in installed
            else None,
        }
        for name in sorted(set(installed) | builtins)
    ]


def _model_rows() -> list[dict[str, Any]]:
    installed = _entries("aeroagentsim.models")
    return [
        {
            "name": name,
            "origin": "entry-point" if name in installed else "builtin",
            "distribution": _distribution(installed[name])
            if name in installed
            else None,
        }
        for name in sorted(set(installed) | set(MODEL_BUILTINS))
    ]


def plugin_inventory() -> dict[str, Any]:
    """Describe installed plugins without loading engine, algorithm or model entries."""
    return {
        "api_version": plugin_api.API_VERSION,
        "scenarios": _scenario_rows(),
        "engines": _engine_rows(),
        "algorithms": _algorithm_rows(),
        "models": _model_rows(),
    }


__all__ = ["plugin_inventory"]
