"""Read-only plugin inventory, including loaded scenario data contributions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib.metadata import entry_points
from typing import Any

import yaml

import aeroagentsim.plugin_api as plugin_api
from aeroagentsim.extensions.discovery import NAME_PATTERN, discover, validate_name
from aeroagentsim.extensions.records import Contribution

MODEL_BUILTINS = ("linear", "wind")


def _rows(
    group: str, builtins: Mapping[str, Any], *, entries: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    # Legacy algorithm/model/engine entry-point names have their own syntax.
    # No provider type, API version or contributed IDs can be checked without load.
    discovery = discover(
        group, builtins,
        entries=entry_points(group=group) if entries is None else entries, load=False,
        name_pattern=(
            NAME_PATTERN if group in {"aeroagentsim.scenarios", "aeroagentsim.metrics"}
            else None
        ),
    )
    installed: dict[str, list[Contribution]] = {}
    for contribution in discovery.contributions:
        if contribution.source == "installed":
            installed.setdefault(contribution.name, []).append(contribution)
    rows: list[dict[str, Any]] = []
    for name in discovery.names:
        entries = installed.get(name, [])
        row: dict[str, Any] = {
            "name": name,
            "origin": "entry-point" if entries else "builtin",
            "distribution": entries[0].distribution if entries else None,
            "status": "ok",
        }
        if len(entries) > 1:
            # Preserve every declaration, including repeated/unknown distributions.
            row["conflict"] = [entry.distribution for entry in entries]
            row["status"] = "duplicate"
        rows.append(row)
    return rows


def _template_id(source: Any) -> str:
    path = source.directory / "template.yaml"
    metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
        raise ValueError(f"template source {path}: template.yaml needs a string id")
    return metadata["id"]


def _scenario_rows() -> list[dict[str, Any]]:
    from aeroagentsim.scenarios import BUILTINS

    rows = _rows(
        "aeroagentsim.scenarios", BUILTINS,
        entries=plugin_api.entry_points(group="aeroagentsim.scenarios"),
    )
    fallback = any(row["status"] == "duplicate" for row in rows)
    if not fallback:
        try:
            for row in rows:
                validate_name(row["name"], label="aeroagentsim.scenarios entry point")
        except ValueError:
            fallback = True
    if fallback:
        # Preserve the legacy section-wide duplicate fallback, also for malformed
        # declarations: contributions are unknown and no scenario plugin is loaded.
        return [{**row, "templates": [], "apply_hooks": []} for row in rows]

    plugins = {plugin.name: plugin for plugin in plugin_api.discover_scenario_plugins()}
    return [
        {
            **row,
            "templates": sorted(
                _template_id(source) for source in plugins[row["name"]].templates
            ),
            "apply_hooks": sorted(plugins[row["name"]].apply_hooks),
        }
        for row in rows
    ]


def _engine_rows() -> list[dict[str, Any]]:
    from aeroagentsim.extensions.engine_builtins import BUILTINS

    return _rows("aeroagentsim.engines", BUILTINS)


def _algorithm_rows() -> list[dict[str, Any]]:
    from aeroagentsim.algorithms import BUILTINS

    # Slot-qualified names, the form entry points use (``allocation.nearest``).
    builtins = {f"{slot}.{name}": None for slot, names in BUILTINS.items() for name in names}
    return _rows("aeroagentsim.algorithms", builtins)


def _model_rows() -> list[dict[str, Any]]:
    return _rows("aeroagentsim.models", dict.fromkeys(MODEL_BUILTINS))


def _metric_rows() -> list[dict[str, Any]]:
    from aeroagentsim.metrics_api import BUILTINS

    return _rows("aeroagentsim.metrics", BUILTINS)


def plugin_inventory() -> dict[str, Any]:
    """Load scenario data, but never start engines or model providers.

    Other families use metadata only. Status ``ok`` means an unambiguous name,
    not runtime readiness. Malformed scenario names and duplicates retain
    diagnostic rows with legacy empty arrays instead of loading a partial set.
    """
    return {
        "api_version": plugin_api.API_VERSION,
        "scenarios": _scenario_rows(),
        "engines": _engine_rows(),
        "algorithms": _algorithm_rows(),
        "models": _model_rows(),
        "metrics": _metric_rows(),
    }


__all__ = ["plugin_inventory"]
