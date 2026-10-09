"""Engine factories compose kernel protocols without prescribing timing models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, cast

from aerokernel import BindingManifest, EntityRef, MemoryRegistry
from aerokernel.engine import Engine, Partition
from aerokernel.values import normalize

from aeroagentsim.models import MotionModel


@dataclass(frozen=True)
class EngineBuild:
    id: str
    config: dict[str, Any]
    registry: MemoryRegistry
    manifest: BindingManifest
    entities: tuple[EntityRef, ...]
    initial: dict[str, dict[str, Any]]
    partitions: dict[str, Partition] = field(default_factory=dict)
    models: dict[str, MotionModel] = field(default_factory=dict)
    run_directory: Path | None = None

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
    "traffic_decision_failures": "aeroagentsim.packs.traffic_accident.decision_failures",
    "traffic_decisions": "aeroagentsim.packs.traffic_accident.decisions",
    "traffic_capture_bridge": "aeroagentsim.packs.traffic_accident.capture",
    "traffic_camera_capture": "aeroagentsim.packs.traffic_accident.camera",
    "behaviour": "aeroagentsim.engines.behaviour",
    "ingress-consumer": "aeroagentsim.engines.ingress_consumer",
    "langgraph": "aeroagentsim.agents.langgraph",
    "telemetry": "aeroagentsim.engines.telemetry",
    "decision": "aeroagentsim.agents.decision",
    "agent-assignment": "aeroagentsim.agents.dispatch",
    "kinematic": "aeroagentsim.engines.kinematic",
    "workflow": "aeroagentsim.engines.workflow",
    "records": "aeroagentsim.engines.records",
    "threshold": "aeroagentsim.engines.threshold",
    "predicate": "aeroagentsim.engines.predicate",
    "logistics": "aeroagentsim.packs.logistics",
    "inspection": "aeroagentsim.packs.inspection",
    "environment": "aeroagentsim.engines.environment",
}


class EngineCatalog:
    """Resolve installed plugins lazily; a factory returns any kernel Engine."""

    def __init__(self) -> None:
        installed = entry_points(group="aeroagentsim.engines")
        self.entries = {entry.name: entry for entry in installed}
        if len(self.entries) != len(installed):
            raise ValueError("Duplicate aeroagentsim.engines entry point names")

    def names(self) -> tuple[str, ...]:
        """Every installed engine and builtin, with entry points taking precedence."""
        return tuple(sorted(set(BUILTINS) | set(self.entries)))

    def factory(self, plugin: str) -> Factory:
        """Import the declared factory without constructing an engine."""
        if plugin in self.entries:
            factory = cast(Factory, self.entries[plugin].load())
        elif plugin in BUILTINS:
            factory = cast(Factory, import_module(BUILTINS[plugin]).build)
        else:
            raise ValueError(f"unknown plugin {plugin!r}; install its distribution")
        if not callable(factory):
            raise TypeError(f"plugin {plugin!r}: engine factory must be callable")
        return factory

    def config_schema(self, plugin: str) -> dict[str, Any] | None:
        """Optional factory.config_schema is a portable object configuration descriptor.

        It is data, never a factory invocation or a source of default values.
        Studio handles only its supported form vocabulary; other shapes retain
        their complete descriptor and use the raw configuration editor.
        """
        factory = self.factory(plugin)
        if not hasattr(factory, "config_schema"):
            return None
        schema = factory.config_schema
        if not isinstance(schema, dict):
            raise TypeError(f"plugin {plugin!r}.config_schema: expected a mapping")
        result = normalize(schema)
        if not isinstance(result, dict) or result.get("type") != "object":
            raise ValueError(f"plugin {plugin!r}.config_schema: expected object schema")
        return cast(dict[str, Any], result)

    def build(self, plugin: str, context: EngineBuild) -> Engine:
        if plugin not in self.entries and plugin not in BUILTINS:
            raise ValueError(
                f"engines.{context.id}.plugin: unknown plugin {plugin!r}; install its distribution"
            )
        return self.factory(plugin)(context)
