"""Engine factories compose kernel protocols without prescribing timing models."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, cast

from aerokernel import BindingManifest, EntityRef, MemoryRegistry
from aerokernel.engine import Engine, Partition
from aerokernel.values import normalize

from aeroagentsim.extensions.discovery import (
    DiscoveryResult,
    DuplicateEntryPointError,
    discover,
    validate_name,
)
from aeroagentsim.extensions.engine_builtins import BUILTINS
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


def _engine_discovery(entries: Iterable[Any]) -> DiscoveryResult[Callable]:
    discovery = discover(
        "aeroagentsim.engines", BUILTINS,
        expected_type=Callable, api_version=None, name_of=None, factory_label=None,
        entries=entries, load=False, name_pattern=None,
        builtin_loader=lambda module: import_module(module).build,
        type_error="plugin {name!r}: engine factory must be callable",
    )
    try:
        discovery.require_unique()
    except DuplicateEntryPointError as exc:
        raise ValueError("Duplicate aeroagentsim.engines entry point names") from exc
    for entry in discovery.contributions:
        label = (
            "aeroagentsim.engines entry point" if entry.source == "installed"
            else "builtin aeroagentsim.engines"
        )
        validate_name(entry.name, label=label, pattern=None)
    return discovery


class EngineCatalog:
    """Resolve installed plugins lazily; a factory returns any kernel Engine."""

    def __init__(self) -> None:
        installed = tuple(entry_points(group="aeroagentsim.engines"))
        _engine_discovery(installed)
        self.entries = {entry.name: entry for entry in installed}

    def _discover(self) -> DiscoveryResult[Callable]:
        # adapters.register / packs.register may extend this public collection.
        # Snapshot it without querying installed metadata again or loading code.
        return _engine_discovery(self.entries.values())

    def names(self) -> tuple[str, ...]:
        """Every installed engine and builtin, with entry points taking precedence."""
        return self._discover().names

    def factory(self, plugin: str) -> Factory:
        """Import the declared factory without constructing an engine."""
        discovery = self._discover()
        if plugin not in discovery.names:
            raise ValueError(f"unknown plugin {plugin!r}; install its distribution")
        return cast(Factory, discovery.resolve(plugin))

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
        if plugin not in self.names():
            raise ValueError(
                f"engines.{context.id}.plugin: unknown plugin {plugin!r}; install its distribution"
            )
        return self.factory(plugin)(context)
