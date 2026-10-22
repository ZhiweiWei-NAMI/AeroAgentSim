"""Read-only inventory of every installed plugin family."""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points
from typing import Any

import yaml

import aeroagentsim.plugin_api as plugin_api

MODEL_BUILTINS = ("linear", "wind")


def _distribution(entry: Any) -> str | None:
    distribution = getattr(entry, "dist", None)
    name = getattr(distribution, "name", None)
    return name if isinstance(name, str) else None


def _group(entries: Iterable[Any]) -> dict[str, list[Any]]:
    """Group entry points by declared name, preserving installation order."""
    grouped: dict[str, list[Any]] = {}
    for entry in entries:
        grouped.setdefault(entry.name, []).append(entry)
    return grouped


def _entries(group: str) -> dict[str, list[Any]]:
    return _group(entry_points(group=group))


def _rows(
    installed: dict[str, list[Any]], builtins: Iterable[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in sorted(set(installed) | set(builtins)):
        entries = installed.get(name, [])
        row: dict[str, Any] = {
            "name": name,
            "origin": "entry-point" if entries else "builtin",
            "distribution": _distribution(entries[0]) if entries else None,
            "status": "ok",
        }
        if len(entries) > 1:
            row["conflict"] = [_distribution(entry) for entry in entries]
            row["status"] = "duplicate"
        rows.append(row)
    return rows


def _template_id(source: Any) -> str:
    path = source.directory / "template.yaml"
    metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
        raise ValueError(f"template source {path}: template.yaml needs a string id")
    return metadata["id"]


def _scenario_metadata_rows(installed: dict[str, list[Any]]) -> list[dict[str, Any]]:
    from aeroagentsim.scenarios import BUILTINS

    # Without loading any plugin, contributions are unknown rather than empty claims.
    return [{**row, "templates": [], "apply_hooks": []} for row in _rows(installed, BUILTINS)]


def _scenario_rows() -> list[dict[str, Any]]:
    try:
        plugins = plugin_api.discover_scenario_plugins()
    except ValueError:
        installed = _group(plugin_api.entry_points(group="aeroagentsim.scenarios"))
        if not any(len(entries) > 1 for entries in installed.values()):
            raise
        # Discovery rejects duplicate names before loading any plugin.
        return _scenario_metadata_rows(installed)
    installed = _group(plugin_api.entry_points(group="aeroagentsim.scenarios"))
    rows: list[dict[str, Any]] = []
    for plugin in plugins:
        entries = installed.get(plugin.name, [])
        entry = entries[0] if entries else None
        rows.append(
            {
                "name": plugin.name,
                "origin": "entry-point" if entry is not None else "builtin",
                "distribution": _distribution(entry) if entry is not None else None,
                "templates": sorted(_template_id(source) for source in plugin.templates),
                "apply_hooks": sorted(plugin.apply_hooks),
                "status": "ok",
            }
        )
    return rows


def _engine_rows() -> list[dict[str, Any]]:
    from aeroagentsim.platform.plugins import BUILTINS

    return _rows(_entries("aeroagentsim.engines"), BUILTINS)


def _algorithm_rows() -> list[dict[str, Any]]:
    from aeroagentsim.algorithms import BUILTINS

    # Slot-qualified names, the form entry points use (``allocation.nearest``).
    installed = _entries("aeroagentsim.algorithms")
    builtins = {f"{slot}.{name}" for slot, names in BUILTINS.items() for name in names}
    return _rows(installed, builtins)


def _model_rows() -> list[dict[str, Any]]:
    return _rows(_entries("aeroagentsim.models"), MODEL_BUILTINS)


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
