"""Native simulator plugins for the independent AeroKernel coordinator."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aeroagentsim.platform.plugins import EngineCatalog


def register(catalog: EngineCatalog) -> EngineCatalog:
    """Register factories through a catalog's public entry point collection.

    Installed distributions can expose the same factory paths in the
    ``aeroagentsim.engines`` entry point group without calling this helper.
    """
    for name in ("px4_gazebo", "sumo", "ns3"):
        entry = EntryPoint(
            name=name,
            value=f"aeroagentsim.adapters.{name}:build",
            group="aeroagentsim.engines",
        )
        if name in catalog.entries and catalog.entries[name].value != entry.value:
            raise ValueError(f"engine plugin collision: {name}")
        catalog.entries[name] = entry
    return catalog
