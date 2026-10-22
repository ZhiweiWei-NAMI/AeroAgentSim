"""Metadata-only inventory of every installed plugin family."""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import entry_points
from typing import Any

import aeroagentsim.plugin_api as plugin_api
from aeroagentsim.extensions.discovery import NAME_PATTERN, discover
from aeroagentsim.extensions.records import Contribution

MODEL_BUILTINS = ("linear", "wind")


def _rows(group: str, builtins: Mapping[str, Any]) -> list[dict[str, Any]]:
    # Legacy algorithm/model/engine entry-point names have their own syntax.
    # No provider type, API version or contributed IDs can be checked without load.
    discovery = discover(
        group, builtins, entries=entry_points(group=group), load=False,
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


def _scenario_rows() -> list[dict[str, Any]]:
    from aeroagentsim.scenarios import BUILTINS

    # Retain the v1 list fields for older decoders. metadata_only marks their
    # contents as uninspected, not proof that the provider contributes no IDs.
    return [
        {**row, "templates": [], "apply_hooks": []}
        for row in _rows("aeroagentsim.scenarios", BUILTINS)
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
    """Describe declarations, never import or invoke their providers.

    Status ``ok`` means an unambiguous name, not loaded/validated contributions
    or runtime readiness. Unknown scenario contribution lists are placeholders.
    """
    return {
        "api_version": plugin_api.API_VERSION,
        "metadata_only": True,
        "scenarios": _scenario_rows(),
        "engines": _engine_rows(),
        "algorithms": _algorithm_rows(),
        "models": _model_rows(),
        "metrics": _metric_rows(),
    }


__all__ = ["plugin_inventory"]
