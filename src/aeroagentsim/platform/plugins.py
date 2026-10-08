"""Engine factories compose kernel protocols without prescribing timing models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import entry_points
from typing import Any, cast

from aerokernel import BindingManifest, EntityRef, MemoryRegistry
from aerokernel.engine import Engine, Partition


@dataclass(frozen=True)
class EngineBuild:
    id: str
    config: dict[str, Any]
    registry: MemoryRegistry
    manifest: BindingManifest
    entities: tuple[EntityRef, ...]
    initial: dict[str, dict[str, Any]]
    partitions: dict[str, Partition] = field(default_factory=dict)


Factory = Callable[[EngineBuild], Engine]
BUILTINS = {
    "kinematic": "aeroagentsim.engines.kinematic",
    "workflow": "aeroagentsim.engines.workflow",
    "records": "aeroagentsim.engines.records",
    "threshold": "aeroagentsim.engines.threshold",
}


class EngineCatalog:
    """Resolve installed plugins lazily; a factory returns any kernel Engine."""

    def __init__(self) -> None:
        self.entries = {
            entry.name: entry for entry in entry_points(group="aeroagentsim.engines")
        }
        if len(self.entries) != len(entry_points(group="aeroagentsim.engines")):
            raise ValueError("Duplicate aeroagentsim.engines entry point names")

    def build(self, plugin: str, context: EngineBuild) -> Engine:
        if plugin in self.entries:
            factory = cast(Factory, self.entries[plugin].load())
        elif plugin in BUILTINS:
            factory = cast(Factory, import_module(BUILTINS[plugin]).build)
        else:
            raise ValueError(
                f"engines.{context.id}.plugin: unknown plugin {plugin!r}; install its distribution"
            )
        return factory(context)
