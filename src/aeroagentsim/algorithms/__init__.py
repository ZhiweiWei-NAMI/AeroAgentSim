"""Algorithm discovery: installed entry points, builtins or module:Class."""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import entry_points
from typing import Any

BUILTINS = {
    "allocation": {
        "nearest": "Nearest",
        "auction": "Auction",
        "hungarian": "Hungarian",
        "llm": "LLMAllocation",
    },
    "path_planning": {"straight": "Straight", "corridor_astar": "CorridorAstar"},
    "conflict_resolution": {
        "altitude_layers": "AltitudeLayers",
        "hold_and_go": "HoldAndGo",
    },
    "charging": {"threshold_return": "ThresholdReturn"},
}
METHODS = {
    "allocation": "allocate",
    "path_planning": "plan",
    "conflict_resolution": "resolve",
    "charging": "charge",
}


def load_algorithm(slot: str, name: str, config: dict[str, Any] | None = None) -> Any:
    if slot not in BUILTINS:
        raise ValueError(f"unknown algorithm slot {slot!r}")
    installed = entry_points(group="aeroagentsim.algorithms")
    entries = {e.name: e for e in installed}
    if len(entries) != len(installed):
        raise ValueError("duplicate aeroagentsim.algorithms entry point names")
    entry = entries.get(f"{slot}.{name}", entries.get(name))
    if entry is not None:
        factory = entry.load()
    elif ":" in name:
        module, identity = name.split(":", 1)
        factory = getattr(import_module(module), identity)
    elif name in BUILTINS[slot]:
        module = ".llm" if name == "llm" else ".builtins"
        factory = getattr(import_module(module, __name__), BUILTINS[slot][name])
    else:
        raise ValueError(f"unknown {slot} algorithm {name!r}")
    instance = factory(**({} if config is None else config))
    if not callable(getattr(instance, METHODS[slot], None)):
        raise TypeError(f"{name} does not implement {slot}.{METHODS[slot]}")
    return instance


__all__ = ["BUILTINS", "load_algorithm"]
