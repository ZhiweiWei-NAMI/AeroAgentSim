"""Engine factories compose kernel protocols without prescribing timing models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
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

    def writers(self, ref: EntityRef) -> dict[str, str]:
        """Resolve authored instance selectors before factories declare partitions.

        This selects authority; the kernel subsequently validates every selected
        writer against the complete, actual partition declarations. Selectors
        apply to future generations as well as bootstrap instances.
        """
        if (ref.run_id, ref.epoch) != (self.manifest.run_id, self.manifest.epoch):
            raise ValueError("writer selector: foreign entity namespace")
        rules = [
            r
            for r in self.manifest.rules
            if self.registry.is_a(ref.type_id, r.type_id)
            and fnmatchcase(ref.id, r.id_pattern)
        ]
        exact = [e for e in self.manifest.exact if e.ref == ref]
        result: dict[str, str] = {}
        for slot in sorted(
            {f for r in rules for f in r.fields} | {e.field for e in exact}
        ):
            overrides = [e.partition for e in exact if e.field == slot]
            if overrides:
                winners = overrides
            else:
                candidates = [r for r in rules if slot in r.fields]
                priority = max(r.priority for r in candidates)
                winners = [r.partition for r in candidates if r.priority == priority]
            if len(winners) != 1:
                raise ValueError(f"writer selector: ambiguous {ref.id}.{slot}")
            result[slot] = winners[0]
        return result

    def owned_fields(self, ref: EntityRef) -> tuple[str, ...]:
        """Selected field slots for this plugin, independent of type-wide scope."""
        return tuple(
            slot for slot, owner in self.writers(ref).items() if owner == self.id
        )


Factory = Callable[[EngineBuild], Engine]
BUILTINS = {
    "decision": "aeroagentsim.agents.decision",
    "agent-assignment": "aeroagentsim.agents.dispatch",
    "kinematic": "aeroagentsim.engines.kinematic",
    "workflow": "aeroagentsim.engines.workflow",
    "records": "aeroagentsim.engines.records",
    "threshold": "aeroagentsim.engines.threshold",
    "logistics": "aeroagentsim.packs.logistics",
    "inspection": "aeroagentsim.packs.inspection",
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
