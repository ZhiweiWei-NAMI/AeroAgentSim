"""Domain engines; ontology IDs and model parameters belong to scenarios."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aeroagentsim.platform.plugins import EngineCatalog


def register(catalog: EngineCatalog) -> EngineCatalog:
    """Add pack factories to a local catalog without changing kernel behavior."""
    for name in ("logistics", "inspection"):
        entry = EntryPoint(
            name=name,
            value=f"aeroagentsim.packs.{name}:build",
            group="aeroagentsim.engines",
        )
        if name in catalog.entries and catalog.entries[name].value != entry.value:
            raise ValueError(f"pack plugin collision: {name}")
        catalog.entries[name] = entry
    return catalog
