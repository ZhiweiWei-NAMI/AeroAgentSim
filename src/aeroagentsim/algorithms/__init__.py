"""Algorithm discovery: installed entry points, builtins or module:Class."""

from __future__ import annotations

from collections.abc import Iterable
from importlib import import_module
from importlib.metadata import entry_points
from typing import Any

from aeroagentsim.extensions.discovery import (
    DiscoveryResult,
    DuplicateEntryPointError,
    discover,
    validate_name,
)

BUILTINS = {
    "allocation": {
        "nearest": "Nearest",
        "auction": "Auction",
        "hungarian": "Hungarian",
        "llm": "LLMAllocation",
    },
    "path_planning": {
        "straight": "Straight",
        "corridor_astar": "CorridorAstar",
        "coverage": "Coverage",
        "survey": "SurveyRoute",
        "patrol": "PatrolRoute",
    },
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


def _algorithm_discovery(entries: Iterable[Any]) -> DiscoveryResult[Any]:
    """Admit legacy names without importing or invoking configured factories.

    Slot precedence and protocol checks belong to the callers, not descriptor
    admission. In particular, catalog inspection must not construct algorithms.
    """
    discovery = discover(
        "aeroagentsim.algorithms", {}, entries=entries, load=False,
        expected_type=object, factory_label=None, name_pattern=None,
    )
    discovery.require_unique()
    for name in discovery.names:
        validate_name(name, label="aeroagentsim.algorithms entry point", pattern=None)
        prefix, separator, suffix = name.partition(".")
        if separator and prefix in BUILTINS and not suffix:
            raise ValueError(f"Algorithm entry point {name!r} has no id")
    return discovery


def load_algorithm(slot: str, name: str, config: dict[str, Any] | None = None) -> Any:
    if slot not in BUILTINS:
        raise ValueError(f"unknown algorithm slot {slot!r}")
    try:
        discovery = _algorithm_discovery(entry_points(group="aeroagentsim.algorithms"))
    except DuplicateEntryPointError as exc:
        raise ValueError("duplicate aeroagentsim.algorithms entry point names") from exc
    qualified = f"{slot}.{name}"
    if qualified in discovery.names:
        factory = discovery.resolve(qualified)
    elif name in discovery.names:
        factory = discovery.resolve(name)
    elif ":" in name:
        module, identity = name.split(":", 1)
        factory = getattr(import_module(module), identity)
    elif name in BUILTINS[slot]:
        module = (
            ".routes"
            if name in {"coverage", "survey", "patrol"}
            else ".llm"
            if name == "llm"
            else ".builtins"
        )
        factory = getattr(import_module(module, __name__), BUILTINS[slot][name])
    else:
        raise ValueError(f"unknown {slot} algorithm {name!r}")
    instance = factory(**({} if config is None else config))
    if not callable(getattr(instance, METHODS[slot], None)):
        raise TypeError(f"{name} does not implement {slot}.{METHODS[slot]}")
    return instance


__all__ = ["BUILTINS", "load_algorithm"]
