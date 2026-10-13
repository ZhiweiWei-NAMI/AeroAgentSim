"""Slot readers verified against shipped engine constructors and protocol calls.

Templates copy all slots into several configs; that is not consumption. Keep
the factory identity here so an installed override never inherits that claim.
"""

from __future__ import annotations

from importlib import metadata

from aeroagentsim.platform.plugins import BUILTINS as ENGINE_PLUGINS

READERS = {
    "lowalt_tasks": ("tasks", "aeroagentsim.packs.lowalt.tasks:build", ("allocation", "path_planning")),
    "lowalt_airspace": ("airspace", "aeroagentsim.packs.lowalt.airspace:build", ("conflict_resolution",)),
    "lowalt_energy": ("energy", "aeroagentsim.packs.lowalt.energy:build", ("charging",)),
}


def slot_consumers() -> dict[str, list[dict[str, str]]]:
    """Describe supported plugin roles, not instance ids in a user's draft.

    Unknown third-party engine implementations have no verified readers here.
    Entry-point declarations are inspected without importing/starting engines.
    """
    installed = list(metadata.entry_points(group="aeroagentsim.engines"))
    entries = {entry.name: entry for entry in installed}
    if len(entries) != len(installed):
        raise ValueError("Duplicate engine entry point names / 引擎入口名称重复")
    consumers: dict[str, list[dict[str, str]]] = {}
    for plugin, (engine, expected_factory, slots) in READERS.items():
        actual = entries[plugin].value if plugin in entries else ENGINE_PLUGINS.get(plugin, "") + ":build"
        if actual != expected_factory:
            continue
        for slot in slots:
            consumers.setdefault(slot, []).append({"engine": engine, "plugin": plugin})
    return consumers
