"""Discover algorithm choices without constructing an engine or LLM provider."""

from __future__ import annotations

import asyncio
from importlib import metadata
from typing import Any

from fastapi import FastAPI, HTTPException

from aeroagentsim.algorithms import BUILTINS, METHODS
from aeroagentsim.algorithms.consumers import slot_consumers
from aeroagentsim.algorithms.metadata import BUILTIN_COPY_ZH

ENTRY_POINT_GROUP = "aeroagentsim.algorithms"

BUILTIN_COPY: dict[str, tuple[str, str]] = {
    "nearest": (
        "Nearest available drone",
        "Assign each task to the closest available drone.",
    ),
    "auction": (
        "Auction bids",
        "Choose a drone using distance and battery-aware bids.",
    ),
    "hungarian": (
        "Match tasks and drones",
        "Match several tasks and drones to reduce total estimated completion time.",
    ),
    "llm": (
        "Language-model agent",
        "Ask a configured LangGraph agent to propose assignments.",
    ),
    "straight": ("Straight line", "Fly directly between the requested places."),
    "corridor_astar": (
        "Safe corridor route",
        "Find a route around active no-fly areas.",
    ),
    "coverage": ("Area sweep", "Cover the declared area in parallel rows."),
    "survey": ("Inspection waypoints", "Visit the declared inspection waypoints."),
    "patrol": ("Patrol loop", "Follow the declared patrol loop."),
    "altitude_layers": (
        "Separate flying heights",
        "Give conflicting drones different flying heights.",
    ),
    "hold_and_go": ("Wait, then go", "Hold one drone until its route is clear."),
    "threshold_return": (
        "Return before battery runs low",
        "Return low-battery drones to a nearby charging pad.",
    ),
}


def _plugin_entry(name: str, factory: Any) -> dict[str, str]:
    label = getattr(factory, "label", None) or getattr(factory, "name", None)
    if not isinstance(label, str) or not label.strip():
        label = name.replace("_", " ").replace("-", " ").capitalize()
    description = getattr(factory, "description", None)
    if not isinstance(description, str) or not description.strip():
        description = (getattr(factory, "__doc__", "") or "").strip().split("\n")[0]
    row = {"id": name, "label": label, "description": description}
    for field in ("label_zh", "description_zh"):
        translated = getattr(factory, field, None)
        if isinstance(translated, str) and translated.strip():
            row[field] = translated
    return row


def discover_algorithm_catalog() -> dict[str, list[dict[str, Any]]]:
    """Mirror qualified-entry → unqualified-entry → builtin lookup precedence.

    A slot-qualified factory can advertise its slot without construction.
    Unqualified classes advertise the actual protocol methods they implement.
    Discovery errors fail the catalog request; incomplete catalogs are not success.
    """
    installed = list(metadata.entry_points(group=ENTRY_POINT_GROUP))
    entries = {entry.name: entry for entry in installed}
    if len(entries) != len(installed):
        raise ValueError("Duplicate aeroagentsim.algorithms entry point names")
    classified: dict[str, dict[str, tuple[str, Any]]] = {slot: {} for slot in BUILTINS}
    for entry in installed:
        prefix, separator, suffix = entry.name.partition(".")
        try:
            factory = entry.load()
        except Exception as error:
            raise ValueError(
                f"Cannot load algorithm entry point {entry.name!r}: {type(error).__name__}"
            ) from error
        if separator and prefix in BUILTINS:
            if not suffix:
                raise ValueError(f"Algorithm entry point {entry.name!r} has no id")
            slots, identity = [prefix], suffix
        else:
            slots = [
                slot
                for slot, method in METHODS.items()
                if callable(getattr(factory, method, None))
            ]
            identity = entry.name
            if not slots:
                raise ValueError(
                    f"Entry point {entry.name!r} needs a slot-qualified name or an algorithm protocol method"
                )
        for slot in slots:
            previous = classified[slot].get(identity)
            if previous is None or entry.name == f"{slot}.{identity}":
                classified[slot][identity] = (entry.name, factory)
    consumers = slot_consumers()
    catalog: dict[str, list[dict[str, Any]]] = {}
    for slot, builtins in BUILTINS.items():
        rows = []
        for identity in sorted(set(builtins) | classified[slot].keys()):
            installed_entry = classified[slot].get(identity)
            if installed_entry is not None:
                rows.append(_plugin_entry(identity, installed_entry[1]))
            else:
                label, description = BUILTIN_COPY[identity]
                label_zh, description_zh = BUILTIN_COPY_ZH[identity]
                rows.append(
                    {
                        "id": identity,
                        "label": label,
                        "description": description,
                        "label_zh": label_zh,
                        "description_zh": description_zh,
                    }
                )
        catalog[slot] = [{**row, "consumers": [dict(reader) for reader in consumers.get(slot, [])]} for row in rows]
    return catalog


def mount_algorithm_catalog(app: FastAPI) -> None:
    @app.get("/v1/algorithms")
    async def algorithm_catalog() -> dict[str, list[dict[str, Any]]]:
        try:
            return await asyncio.to_thread(discover_algorithm_catalog)
        except ValueError as error:
            raise HTTPException(503, str(error)) from error
