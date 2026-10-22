"""Versioned, data-only assistant operation-family contracts and discovery."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import Any

ASSISTANT_API_VERSION = 1
ENTRY_POINT_GROUP = "aeroagentsim.assistant_operations"
MAX_EXPECTATIONS = 4096
_NAME = r"[a-z0-9][a-z0-9_-]*"
_OPERATION = rf"{_NAME}\.[a-z0-9][a-z0-9_-]*"


def freeze(value: Any) -> Any:
    """Snapshot JSON-shaped data read-only; arbitrary objects are not context data."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("assistant data: mapping keys must be strings")
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if value is None or type(value) in (str, int, bool, bytes):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError(f"assistant data: unsupported value {type(value).__name__}")


def thaw(value: Any) -> Any:
    """Return an editable copy of a descriptor or context snapshot."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class CompileContext:
    """Inputs already read by the host. No paths, stores, providers or I/O handles.

    ``agent_profile`` is only an operator-side profile selector, never a resolved
    provider. ``resources`` maps portable relative names to already-read bytes.
    ``data`` holds optional host-prepared metadata, not executable services.
    """

    document: Mapping[str, Any]
    resources: Mapping[str, bytes] = field(default_factory=dict)
    data: Mapping[str, Any] = field(default_factory=dict)
    controls: Mapping[str, Any] = field(default_factory=dict)
    operation_id: str | None = None
    agent_profile: str | None = None
    locale: str = "en"

    def __post_init__(self) -> None:
        for name in ("document", "resources", "data", "controls"):
            if not isinstance(getattr(self, name), Mapping):
                raise TypeError(f"assistant context {name}: mapping required")
            object.__setattr__(self, name, freeze(getattr(self, name)))
        for path, content in self.resources.items():
            _resource_path(path)
            if not isinstance(content, bytes):
                raise TypeError("assistant context resources: bytes required")
        for name in ("operation_id", "agent_profile"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise TypeError(f"assistant context {name}: nonempty string or None required")
        if not isinstance(self.locale, str) or not self.locale.strip():
            raise TypeError("assistant context locale: nonempty string required")


def _resource_path(path: str) -> None:
    if (not isinstance(path, str) or not path or path.startswith("/") or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/")) or ":" in path
            or any(ord(character) < 32 for character in path)):
        raise ValueError(f"assistant resource {path!r}: portable relative path required")


@dataclass(frozen=True)
class ResourceProposal:
    """Write bytes (or remove a file with ``content=None``) in isolated plan files."""

    path: str
    content: bytes | None

    def __post_init__(self) -> None:
        _resource_path(self.path)
        if (self.path == "region.plan.json" or any(
                part in {"versions", "__pycache__"} or part.startswith("validation-")
                or part.startswith("draft") and part.endswith(".json") for part in self.path.split("/"))):
            raise ValueError(f"assistant resource {self.path!r}: host-owned plan/workspace path")
        if self.content is not None and not isinstance(self.content, bytes):
            raise TypeError("assistant resource content: bytes or None required")


@dataclass(frozen=True)
class CompileResult:
    """Proposals only: not an applied plan, command ACK or measured outcome.

    Patches use the bounded JSON Pointer add/replace/remove vocabulary. Issues
    carry ``severity`` (error/warning), ``field`` and bilingual ``message``.
    Expectations use finite schema/payload matches, never executable predicates.
    """

    patches: tuple[Mapping[str, Any], ...] = ()
    resources: tuple[ResourceProposal, ...] = ()
    issues: tuple[Mapping[str, Any], ...] = ()
    effective_operations: tuple[Mapping[str, Any], ...] = ()
    effects: tuple[Mapping[str, Any], ...] = ()
    references: tuple[Mapping[str, Any], ...] = ()
    expectations: tuple[Mapping[str, Any], ...] = ()
    controls: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("patches", "issues", "effective_operations", "effects", "references", "expectations"):
            rows = getattr(self, name)
            if not isinstance(rows, tuple) or any(not isinstance(row, Mapping) for row in rows):
                raise TypeError(f"assistant result {name}: tuple of mappings required")
            object.__setattr__(self, name, freeze(rows))
            json.dumps(thaw(rows), allow_nan=False)
        if not isinstance(self.resources, tuple) or any(not isinstance(row, ResourceProposal) for row in self.resources):
            raise TypeError("assistant result resources: tuple of ResourceProposal required")
        for resource in self.resources:
            resource.__post_init__()
        if len({row.path for row in self.resources}) != len(self.resources):
            raise ValueError("assistant result resources: conflicting paths")
        for patch in self.patches:
            if patch.get("op") not in {"add", "replace", "remove"} or not isinstance(patch.get("path"), str):
                raise ValueError("assistant patch: add/replace/remove and JSON Pointer path required")
            if patch["path"] and not patch["path"].startswith("/"):
                raise ValueError("assistant patch: JSON Pointer path required")
            if patch["op"] != "remove" and "value" not in patch:
                raise ValueError("assistant patch: value required")
            if set(patch) - {"op", "path", "value"}:
                raise ValueError("assistant patch: unknown fields")
        for issue in self.issues:
            if (issue.get("severity") not in {"error", "warning"} or not isinstance(issue.get("message"), str)
                    or not isinstance(issue.get("field"), str)):
                raise ValueError("assistant issue: severity, field and message required")
        for effect in self.effects:
            if (effect.get("group", "changed") not in {"added", "changed", "removed"}
                    or any(not isinstance(effect.get(key), str) or not effect[key] for key in ("kind", "id"))):
                raise ValueError("assistant effect: diff group, kind and id required")
        validate_expectations(self.expectations)
        if self.controls is not None:
            if not isinstance(self.controls, Mapping):
                raise TypeError("assistant result controls: mapping required")
            object.__setattr__(self, "controls", freeze(self.controls))
            json.dumps(thaw(self.controls), allow_nan=False)


def validate_expectations(rows: Any) -> None:
    """Bound conditional, data-only event matches without implying completion."""
    if len(rows) > MAX_EXPECTATIONS:
        raise ValueError(f"assistant expectations: at most {MAX_EXPECTATIONS} entries")
    identities = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("assistant expectation: mapping required")
        identity, match = row.get("id"), row.get("match")
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError("assistant expectations: unique nonempty ids required")
        identities.add(identity)
        if any(not isinstance(row.get(key), str) or not row[key] for key in ("text", "text_zh")):
            raise ValueError("assistant expectation: zh/en text required")
        if not isinstance(match, Mapping) or not isinstance(match.get("schema"), str) or not match["schema"] or not isinstance(match.get("where"), Mapping):
            raise ValueError("assistant expectation: schema and payload where mapping required")
        if set(match) - {"schema", "where", "count", "released_schema", "count_basis", "demand"}:
            raise ValueError("assistant expectation: unsupported match vocabulary")
        if "count" in match and (type(match["count"]) is not int or not 1 <= match["count"] <= 100000):
            raise ValueError("assistant expectation: positive bounded count required")
        released_schema = match.get("released_schema")
        if released_schema is not None and (not isinstance(released_schema, str) or not released_schema):
            raise ValueError("assistant expectation: nonempty released schema required")
        if match.get("count_basis") not in (None, "released"):
            raise ValueError("assistant expectation: unsupported count basis")
        if match.get("count_basis") == "released" and (not isinstance(match.get("released_schema"), str) or not isinstance(match.get("demand"), Mapping)):
            raise ValueError("assistant expectation: released schema and demand required")
        for key in ("depends_on", "alternatives"):
            links = row.get(key, ())
            if not isinstance(links, (list, tuple)) or len(links) > MAX_EXPECTATIONS or any(not isinstance(link, str) or not link for link in links):
                raise ValueError(f"assistant expectation {key}: bounded id array required")
        approximate = row.get("approx_s")
        if approximate is not None and (type(approximate) not in (int, float) or not math.isfinite(approximate) or approximate < 0):
            raise ValueError("assistant expectation approx_s: nonnegative finite time or None required")
        # Freeze traverses the entire record, rejecting callables and nonfinite values.
        freeze(row)


def document_patches(before: Mapping, after: Mapping) -> tuple[dict, ...]:
    """Top-level patches preserve authored list order and all untouched fields."""
    return tuple(
        [{"op": "remove", "path": "/" + key.replace("~", "~0").replace("/", "~1")} for key in before if key not in after]
        + [{"op": "replace" if key in before else "add", "path": "/" + key.replace("~", "~0").replace("/", "~1"), "value": thaw(value)}
           for key, value in after.items() if key not in before or
           json.dumps(thaw(before[key]), sort_keys=True, ensure_ascii=False, allow_nan=False) !=
           json.dumps(thaw(value), sort_keys=True, ensure_ascii=False, allow_nan=False)]
    )


def _always(context: CompileContext, params: Mapping[str, Any]) -> bool:
    return True


@dataclass(frozen=True)
class Prerequisite:
    """An explicit operation installed visibly when its pure condition is true."""

    operation: str
    params: Mapping[str, Any] = field(default_factory=dict)
    when: Callable[[CompileContext, Mapping[str, Any]], bool] = _always
    automatic_id: str = "prerequisite"
    display_operation: str | None = None
    note: str | None = None
    warning: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not re.fullmatch(_OPERATION, self.operation):
            raise ValueError("assistant prerequisite: namespaced operation required")
        if not callable(self.when):
            raise TypeError("assistant prerequisite when: callable required")
        if not isinstance(self.automatic_id, str) or not self.automatic_id.strip():
            raise ValueError("assistant prerequisite: automatic_id required")
        if not isinstance(self.params, Mapping):
            raise TypeError("assistant prerequisite params: mapping required")
        for key in ("display_operation", "note", "warning"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise TypeError(f"assistant prerequisite {key}: nonempty string required")
        object.__setattr__(self, "params", freeze(self.params))


_SCHEMA_KEYS = {
    "type", "properties", "required", "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "enum", "const", "anyOf", "oneOf", "allOf", "not", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern", "minProperties", "maxProperties",
    "title", "description", "default", "examples", "$schema", "$id",
}


def _parameter_schema(schema: Mapping[str, Any]) -> None:
    """Admit the v1 local JSON Schema vocabulary; never resolve remote schemas."""
    if not isinstance(schema, Mapping) or set(schema) - _SCHEMA_KEYS:
        raise ValueError("assistant parameters: unsupported JSON Schema keyword")
    kind = schema.get("type")
    kinds = kind if isinstance(kind, (tuple, list)) else [kind] if kind is not None else []
    if (isinstance(kind, (tuple, list)) and not kinds
            or any(not isinstance(value, str) or value not in {"object", "array", "string", "number", "integer", "boolean", "null"} for value in kinds)):
        raise ValueError("assistant parameters: unsupported JSON Schema type")
    for key in ("minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise TypeError(f"assistant parameter {key}: nonnegative integer required")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            raise TypeError(f"assistant parameter {key}: finite number required")
    if "multipleOf" in schema and schema["multipleOf"] <= 0:
        raise ValueError("assistant parameter multipleOf: positive number required")
    if "enum" in schema and (not isinstance(schema["enum"], (tuple, list)) or not schema["enum"]):
        raise TypeError("assistant parameter enum: nonempty array required")
    if "uniqueItems" in schema and type(schema["uniqueItems"]) is not bool:
        raise TypeError("assistant parameter uniqueItems: bool required")
    for name in ("properties",):
        if name in schema:
            if not isinstance(schema[name], Mapping):
                raise TypeError("assistant parameter properties: mapping required")
            for child in schema[name].values():
                _parameter_schema(child)
    for name in ("items", "not"):
        if name in schema:
            _parameter_schema(schema[name])
    additional = schema.get("additionalProperties", True)
    if type(additional) is not bool:
        _parameter_schema(additional)
    for name in ("anyOf", "oneOf", "allOf"):
        if name in schema:
            if not isinstance(schema[name], (tuple, list)) or not schema[name]:
                raise TypeError(f"assistant parameter {name}: nonempty schema array required")
            for child in schema[name]:
                _parameter_schema(child)
    if "required" in schema and (not isinstance(schema["required"], (tuple, list)) or any(not isinstance(key, str) for key in schema["required"])):
        raise TypeError("assistant parameter required: string array required")
    if "pattern" in schema:
        re.compile(schema["pattern"])


@dataclass(frozen=True)
class OperationSpec:
    id: str
    parameters: Mapping[str, Any]
    title: str
    title_zh: str
    description: str
    description_zh: str
    compile: Callable[[CompileContext, Mapping[str, Any]], CompileResult]
    applicable: Callable[[CompileContext, Mapping[str, Any]], bool] = _always
    prerequisites: tuple[Prerequisite, ...] = ()
    topic: str = "operations"
    # Compatibility copy and private builtin staging tags; compile is authoritative.
    display: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not re.fullmatch(_OPERATION, self.id):
            raise ValueError(f"assistant operation {self.id!r}: expected <family>.<op>")
        for key in ("title", "title_zh", "description", "description_zh", "topic"):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise ValueError(f"assistant operation {self.id!r}: {key} required")
        if not isinstance(self.parameters, Mapping) or self.parameters.get("type") != "object":
            raise TypeError(f"assistant operation {self.id!r}: object parameter schema required")
        if not callable(self.compile) or not callable(self.applicable):
            raise TypeError(f"assistant operation {self.id!r}: pure compiler and applicability callable required")
        if not isinstance(self.prerequisites, tuple) or any(not isinstance(row, Prerequisite) for row in self.prerequisites):
            raise TypeError(f"assistant operation {self.id!r}: tuple of Prerequisite required")
        if not isinstance(self.display, Mapping):
            raise TypeError(f"assistant operation {self.id!r}: display mapping required")
        _parameter_schema(self.parameters)
        json.dumps(thaw(self.parameters), allow_nan=False)
        object.__setattr__(self, "parameters", freeze(self.parameters))
        object.__setattr__(self, "display", freeze(self.display))


@dataclass(frozen=True)
class AssistantReadSpec:
    """Optional pure read projections, contributed alongside the operations.

    Summary sections are merged as data (lists concatenate, mappings merge).
    Per-section priority preserves existing builtin card/event order.
    """

    summarize: Callable[[CompileContext], Mapping[str, Any]] | None = None
    expectations: Callable[[Mapping, Mapping], list[dict]] | None = None
    interactions: Callable[[Mapping], Mapping] | None = None
    missing: Callable[..., dict | None] | None = None
    priority: Mapping[str, int] = field(default_factory=dict)
    _callbacks: Mapping[str, Callable[..., Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for key in ("summarize", "expectations", "interactions", "missing"):
            if getattr(self, key) is not None and not callable(getattr(self, key)):
                raise TypeError(f"assistant read {key}: callable required")
        if not isinstance(self.priority, Mapping) or any(type(value) is not int for value in self.priority.values()):
            raise TypeError("assistant read priority: actual integers required")
        object.__setattr__(self, "priority", freeze(self.priority))
        if not isinstance(self._callbacks, Mapping) or any(not isinstance(key, str) or not callable(value) for key, value in self._callbacks.items()):
            raise TypeError("assistant builtin read adapters: named callables required")
        object.__setattr__(self, "_callbacks", MappingProxyType(dict(self._callbacks)))


@dataclass(frozen=True)
class AssistantOperationPlugin:
    name: str
    api_version: int = 1
    operations: tuple[OperationSpec, ...] = ()
    read: AssistantReadSpec | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Private compatibility seam for the shipped authoring/resource adapters.
    # Not part of the external v1 operation API; never enters CompileContext.
    _host: Callable[..., Any] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(_NAME, self.name):
            raise ValueError(f"assistant plugin {self.name!r}: name must match {_NAME}")
        if type(self.api_version) is not int or self.api_version != ASSISTANT_API_VERSION:
            raise ValueError(f"assistant plugin {self.name!r}: unsupported API version {self.api_version!r}; supported version is {ASSISTANT_API_VERSION}")
        if not isinstance(self.operations, tuple) or any(not isinstance(row, OperationSpec) for row in self.operations):
            raise TypeError(f"assistant plugin {self.name!r}: operations must be a tuple of OperationSpec")
        seen = set()
        for operation in self.operations:
            operation.__post_init__()
            if not operation.id.startswith(self.name + "."):
                raise ValueError(f"assistant plugin {self.name!r}: operation {operation.id!r} is outside its namespace")
            if operation.id in seen:
                raise ValueError(f"assistant plugin {self.name!r}: conflicting operation id {operation.id!r}")
            seen.add(operation.id)
        if self.read is not None and not isinstance(self.read, AssistantReadSpec):
            raise TypeError("assistant plugin read: AssistantReadSpec required")
        if self._host is not None and not callable(self._host):
            raise TypeError("assistant plugin _host: callable required")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("assistant plugin metadata: mapping required")
        object.__setattr__(self, "metadata", freeze(self.metadata))


def _builtin(module: str) -> Callable[[], AssistantOperationPlugin]:
    def factory() -> AssistantOperationPlugin:
        return import_module(module).plugin()
    return factory


BUILTINS = {
    "lowalt": _builtin("aeroagentsim.packs.lowalt.assistant"),
    "traffic": _builtin("aeroagentsim.packs.traffic_accident.assistant"),
}


def _plugin(name: str, value: Any, origin: str) -> AssistantOperationPlugin:
    if not isinstance(value, AssistantOperationPlugin) and callable(value):
        try:
            value = value()
        except TypeError as exc:
            raise TypeError(f"{origin} {name!r}: zero-argument assistant plugin factory failed: {exc}") from exc
    if not isinstance(value, AssistantOperationPlugin):
        raise TypeError(f"{origin} {name!r}: expected an AssistantOperationPlugin or a zero-argument callable returning one")
    if value.name != name:
        raise ValueError(f"{origin} {name!r}: plugin name {value.name!r} does not match")
    # Recheck admission even for descriptors produced by unusual factories.
    value.__post_init__()
    return value


def discover_assistant_plugins() -> tuple[AssistantOperationPlugin, ...]:
    """Fail closed on duplicate installations, then replace whole builtin providers."""
    installed = {}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name in installed:
            raise ValueError(f"Duplicate {ENTRY_POINT_GROUP} entry point name: {entry.name!r}")
        installed[entry.name] = entry
    for name in set(BUILTINS) | set(installed):
        if not isinstance(name, str) or not re.fullmatch(_NAME, name):
            raise ValueError(f"assistant entry point {name!r}: invalid plugin name")
    plugins, owners = [], {}
    for name in sorted(set(BUILTINS) | set(installed)):
        value = installed[name].load() if name in installed else BUILTINS[name]
        plugin = _plugin(name, value, ENTRY_POINT_GROUP + " entry point" if name in installed else "builtin assistant plugin")
        for operation in plugin.operations:
            if operation.id in owners:
                raise ValueError(f"Conflicting assistant operation id {operation.id!r}: {owners[operation.id]!r} and {name!r}")
            owners[operation.id] = name
        plugins.append(plugin)
    return tuple(plugins)


__all__ = [
    "ASSISTANT_API_VERSION", "ENTRY_POINT_GROUP", "AssistantOperationPlugin", "AssistantReadSpec",
    "CompileContext", "CompileResult", "OperationSpec", "Prerequisite", "ResourceProposal",
    "discover_assistant_plugins", "document_patches", "freeze", "thaw",
]
