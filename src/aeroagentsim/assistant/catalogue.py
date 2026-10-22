"""Effective assistant operations and explicit v1 legacy-name bindings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from aeroagentsim.assistant_api import (
    AssistantOperationPlugin,
    CompileContext,
    CompileResult,
    OperationSpec,
    discover_assistant_plugins,
    freeze,
    thaw,
)

# This is a compatibility lock, not suffix-based alias inference. New families
# never acquire old names merely by installing an operation with the same suffix.
LEGACY_ALIASES = MappingProxyType({
    "add_lowalt_components": "lowalt.add_lowalt_components",
    "set_control": "lowalt.set_control",
    "set_duration": "lowalt.set_duration",
    "set_run_mode": "lowalt.set_run_mode",
    "set_wind": "lowalt.set_wind",
    "add_gust": "lowalt.add_gust",
    "remove_gust": "lowalt.remove_gust",
    "set_weather": "lowalt.set_weather",
    "set_rule": "lowalt.set_rule",
    "set_algorithm": "lowalt.set_algorithm",
    "add_place": "lowalt.add_place",
    "update_place": "lowalt.update_place",
    "remove_place": "lowalt.remove_place",
    "add_fleet": "lowalt.add_fleet",
    "set_fleet": "lowalt.set_fleet",
    "remove_fleet": "lowalt.remove_fleet",
    "set_vehicle": "lowalt.set_vehicle",
    "add_orders": "lowalt.add_orders",
    "set_orders": "lowalt.set_orders",
    "remove_orders": "lowalt.remove_orders",
    "set_task": "lowalt.set_task",
    "add_incident": "lowalt.add_incident",
    "set_incident": "lowalt.set_incident",
    "remove_incident": "lowalt.remove_incident",
    "schedule_event": "lowalt.schedule_event",
    "set_cooperation": "lowalt.set_cooperation",
    "remove_cooperation": "lowalt.remove_cooperation",
    "set_region": "lowalt.set_region",
})


class OperationCatalogue:
    """An immutable discovery snapshot; compiling against it performs no discovery."""

    def __init__(self, plugins: tuple[AssistantOperationPlugin, ...] | None = None, *,
                 aliases: Mapping[str, str] | Iterable[tuple[str, str]] = LEGACY_ALIASES) -> None:
        self.plugins = discover_assistant_plugins() if plugins is None else tuple(plugins)
        operations, owners, names = {}, {}, set()
        for plugin in self.plugins:
            if not isinstance(plugin, AssistantOperationPlugin):
                raise TypeError("assistant catalogue: AssistantOperationPlugin required")
            if plugin.name in names:
                raise ValueError(f"Duplicate assistant provider {plugin.name!r}")
            names.add(plugin.name)
            for spec in plugin.operations:
                if spec.id in operations:
                    raise ValueError(f"Conflicting assistant operation id {spec.id!r}: {owners[spec.id]!r} and {plugin.name!r}")
                operations[spec.id], owners[spec.id] = spec, plugin.name
            plugin.__post_init__()
        selected = {}
        for alias, target in aliases.items() if isinstance(aliases, Mapping) else aliases:
            if not isinstance(alias, str) or not alias or "." in alias or not isinstance(target, str):
                raise ValueError("assistant aliases: explicit unqualified name and namespaced id required")
            if alias in selected:
                raise ValueError(f"Ambiguous assistant alias {alias!r}")
            selected[alias] = target
        if aliases is not LEGACY_ALIASES:
            for alias, target in selected.items():
                if target not in operations:
                    raise ValueError(f"Assistant alias {alias!r}: unknown target {target!r}")
        self.operations = MappingProxyType(operations)
        self.owners = MappingProxyType(owners)
        # A replacement may intentionally retire a builtin operation. Its old
        # alias becomes unknown, never redirected to another installed family.
        self.aliases = MappingProxyType({alias: target for alias, target in selected.items() if target in operations})
        for spec in operations.values():
            for prerequisite in spec.prerequisites:
                if prerequisite.operation not in operations:
                    raise ValueError(f"{spec.id}: unknown prerequisite {prerequisite.operation!r}")
        active, seen = set(), set()
        def visit(identity: str) -> None:
            if identity in active:
                raise ValueError(f"Circular assistant prerequisites at {identity!r}")
            if identity not in seen:
                active.add(identity)
                for prerequisite in operations[identity].prerequisites:
                    visit(prerequisite.operation)
                active.remove(identity)
                seen.add(identity)
        for identity in operations:
            visit(identity)

    def resolve(self, name: str) -> OperationSpec:
        from .operations import OperationError

        identity = self.aliases.get(name, name) if isinstance(name, str) else None
        if identity not in self.operations:
            raise OperationError("op", f"Unknown operation {name!r}; valid operations: {list(self.aliases) + list(self.operations)} / 未知操作；允许操作见列表")
        return self.operations[identity]

    def provider(self, name: str) -> AssistantOperationPlugin:
        owner = self.owners[self.resolve(name).id]
        return next(plugin for plugin in self.plugins if plugin.name == owner)

    def compile(self, context: CompileContext, operation: dict[str, Any]) -> CompileResult:
        from .operations import OperationError, validate_operation

        if not isinstance(context, CompileContext):
            raise TypeError("assistant compilation: data-only CompileContext required")
        name, params = validate_operation(operation, catalogue=self)
        spec = self.resolve(name)
        applicable = spec.applicable(context, freeze(params))
        if type(applicable) is not bool:
            raise TypeError(f"{spec.id}: applicability must return bool")
        if not applicable:
            raise OperationError("base", f"Operation {spec.id} is not applicable to this scene / 此操作不适用于当前场景")
        result = spec.compile(context, freeze(params))
        if not isinstance(result, CompileResult):
            raise TypeError(f"{spec.id}: compiler must return CompileResult")
        result.__post_init__()
        for effective in result.effective_operations:
            validate_operation(thaw(effective), catalogue=self)
        return result

    def prerequisites(self, context: CompileContext, operations: list[dict]) -> tuple[list[dict], list[str], list[dict]]:
        """Expand explicit prerequisites without changing the requested recipes."""
        from .operations import OperationError

        result = thaw(freeze(operations))
        present = {}
        used_ids = {row.get("id") for row in result if isinstance(row, dict)}
        for row in result:
            if isinstance(row, dict):
                try:
                    present.setdefault(self.resolve(row.get("op")).id, []).append(row.get("params", {}))
                except OperationError:
                    pass  # Retain invalid operations for located compiler diagnostics.
        notes, warnings, additions = [], [], []
        def require(spec: OperationSpec, params: Mapping) -> None:
            for prerequisite in spec.prerequisites:
                needed = prerequisite.when(context, freeze(params))
                if type(needed) is not bool:
                    raise TypeError(f"{spec.id}: prerequisite condition must return bool")
                if not needed:
                    continue
                if prerequisite.operation in present:
                    if not any(actual == thaw(prerequisite.params) for actual in present[prerequisite.operation]):
                        raise OperationError("operations", f"Conflicting parameters for prerequisite {prerequisite.operation} / 前置操作参数冲突")
                    continue
                target = self.resolve(prerequisite.operation)
                require(target, prerequisite.params)
                identity = prerequisite.automatic_id
                while identity in used_ids:
                    identity += "-1"
                used_ids.add(identity)
                name = prerequisite.display_operation or target.id
                if self.resolve(name).id != target.id:
                    raise ValueError(f"{spec.id}: prerequisite display name has another target")
                additions.append({"id": identity, "op": name, "params": thaw(prerequisite.params)})
                present.setdefault(target.id, []).append(thaw(prerequisite.params))
                if prerequisite.note:
                    notes.append(prerequisite.note)
                if prerequisite.warning:
                    warnings.append({"op_id": identity, "field": "operations", "message": prerequisite.warning})
        for row in result:
            if isinstance(row, dict):
                try:
                    spec = self.resolve(row.get("op"))
                except OperationError:
                    continue
                params = row.get("params", {})
                if isinstance(params, dict):
                    require(spec, params)
        return additions + result, notes, warnings

    def display(self, *, namespaced: bool = False) -> dict[str, dict]:
        """Keep legacy forms/model copy byte-for-byte; expose canonical ids on request."""
        result = {}
        entries = list(self.aliases.items())
        aliased = set(self.aliases.values())
        entries.extend((identity, identity) for identity, spec in self.operations.items()
                       if namespaced or identity not in aliased and not spec.display.get("namespaced_only"))
        for name, identity in entries:
            spec = self.operations[identity]
            row = thaw(spec.display) if spec.display else {
                "name": name, "topic": spec.topic, "description": spec.description,
                "description_zh": spec.description_zh, "parameters": thaw(spec.parameters),
                "defaults": {},
            }
            row.pop("legacy_operation", None)
            row.pop("namespaced_only", None)
            row["name"] = name
            if "example" in row:
                row["example"]["op"] = name
            if name == identity:
                row.update(id=spec.id, title=spec.title, title_zh=spec.title_zh,
                           prerequisites=self.describe(identity)["prerequisites"])
            result[name] = row
        return result

    def describe(self, name: str) -> dict:
        spec = self.resolve(name)
        return {"id": spec.id, "provider": self.owners[spec.id], "api_version": 1,
                "parameters": thaw(spec.parameters), "title": spec.title, "title_zh": spec.title_zh,
                "description": spec.description, "description_zh": spec.description_zh,
                "prerequisites": [{"operation": row.operation, "params": thaw(row.params), "conditional": True,
                                   "note": row.note, "warning": row.warning} for row in spec.prerequisites]}


def operation_catalogue() -> OperationCatalogue:
    return OperationCatalogue()


def metadata(key: str, default: Any = None, *, catalogue: OperationCatalogue | None = None) -> Any:
    """Merge contributed copy/data without teaching the host any family names."""
    selected = OperationCatalogue() if catalogue is None else catalogue
    values = [thaw(plugin.metadata[key]) for plugin in selected.plugins if key in plugin.metadata]
    if not values:
        return default
    result = values[0]
    for value in values[1:]:
        if isinstance(result, dict) and isinstance(value, dict):
            for name, item in value.items():
                if name in result and result[name] != item:
                    raise ValueError(f"Conflicting assistant metadata {key}.{name}")
                result[name] = item
        elif isinstance(result, list) and isinstance(value, list):
            result.extend(item for item in value if item not in result)
        elif result != value:
            raise ValueError(f"Conflicting assistant metadata {key}")
    return result


def callbacks(name: str, *, catalogue: OperationCatalogue | None = None):
    selected = OperationCatalogue() if catalogue is None else catalogue
    return tuple(plugin.read._callbacks[name] for plugin in selected.plugins
                 if plugin.read is not None and name in plugin.read._callbacks)
