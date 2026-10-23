"""Data-only presentation v1 and trusted, read-only normalization contracts.

Installed Python is trusted code, not a sandbox. A normalizer may only reduce
its detached input and own JSON state; it cannot render, perform I/O, call a
provider or mutate runtime state. Templates are always finite plain-text data.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from importlib import import_module
from importlib.metadata import PackageNotFoundError, entry_points, version
from inspect import isasyncgenfunction, iscoroutinefunction
from threading import RLock
from types import MappingProxyType
from typing import Any, Protocol

from .extensions.discovery import DiscoveryResult, discover, validate_name
from .presentation_controls import ControlReader, ControlRegistry, Known, Unavailable, known, read_controls

PRESENTATION_API_VERSION = 1
PRESENTATION_ENTRY_POINT_GROUP = "aeroagentsim.presentations"
PRESENTATION_FORMAT = "aeroagentsim.presentation/v1"
PRESENTATION_SNAPSHOT_FORMAT = "aeroagentsim.presentation-snapshot/v1"
HEADER_RESOURCE_KEYS = frozenset({"legacy-huangpu/v1"})
CATEGORIES = frozenset({
    "logistics", "emergency", "inspection", "agriculture", "mobility",
    "security", "airspace", "weather", "energy", "mixed",
})
SEVERITIES = frozenset({"info", "notice", "warning", "critical"})
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BRACES = re.compile(r"\{[^{}]*\}|[{}]")


def json_data(value: Any) -> Any:
    """Detach immutable mappings/tuples as finite JSON, without coercing values."""
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("presentation data: string keys required")
        return {key: json_data(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_data(child) for child in value]
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("presentation data: finite JSON values required")


def freeze_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze_data(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_data(child) for child in value)
    return value


@dataclass(frozen=True, eq=False)
class _ValidatedSnapshot(Mapping[str, Any]):
    """An owned immutable snapshot whose validation cannot become stale."""

    _data: Mapping[str, Any]

    def __post_init__(self) -> None:
        validate_presentation_snapshot(self._data)
        object.__setattr__(self, "_data", freeze_data(self._data))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def freeze_presentation_snapshot(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate once and own frozen DATA for sharing on read-only paths."""
    return snapshot if isinstance(snapshot, _ValidatedSnapshot) else _ValidatedSnapshot(snapshot)


def presentation_digest(value: Any) -> str:
    encoded = json.dumps(json_data(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def placeholders(template: Any, path: str) -> set[str]:
    if type(template) is not str or not template.strip():
        raise ValueError(f"{path}: nonempty string required")
    for match in _BRACES.finditer(template):
        if _PLACEHOLDER.fullmatch(match.group(0)) is None:
            raise ValueError(f"{path}: invalid placeholder {match.group(0)!r}")
    return {match.group(1) for match in _PLACEHOLDER.finditer(template)}


def _localized(value: Any, path: str, *, empty: bool = False) -> None:
    if not isinstance(value, Mapping) or set(value) != {"en", "zh"} or any(
        type(text) is not str or (not empty and not text.strip()) for text in value.values()
    ):
        raise ValueError(f"{path}: localized en/zh text required")


def _path(value: Any, path: str) -> None:
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 8 or any(
        not (type(part) is str and part and part not in {"*", ".."})
        and not (type(part) is int and 0 <= part < 4096) for part in value
    ):
        raise ValueError(f"{path}: bounded literal field path required (1..8 members)")


def _selector(value: Any, path: str, *, item: bool = False) -> None:
    allowed = {"path", "enum", "label", "fallback", "join", "prefer_payload"}
    if not isinstance(value, Mapping) or set(value) - allowed or "path" not in value:
        raise ValueError(f"{path}: selector requires path; unknown selector key")
    _path(value["path"], path + ".path")
    for name in ("label", "prefer_payload"):
        if name in value and type(value[name]) is not bool:
            raise ValueError(f"{path}.{name}: boolean required")
    if "fallback" in value:
        _localized(value["fallback"], path + ".fallback", empty=True)
    if "enum" in value:
        if not isinstance(value["enum"], Mapping):
            raise ValueError(f"{path}.enum: mapping required")
        for key, label in value["enum"].items():
            if type(key) is not str:
                raise ValueError(f"{path}.enum: string keys required")
            _localized(label, path + ".enum." + key)
    if "join" in value:
        join = value["join"]
        if item or not isinstance(join, Mapping) or set(join) != {
            "template", "template_zh", "selectors", "separator"
        }:
            raise ValueError(f"{path}.join: one finite item template required")
        en = placeholders(join["template"], path + ".join.template")
        if en != placeholders(join["template_zh"], path + ".join.template_zh"):
            raise ValueError(f"{path}.join: bilingual placeholders must match")
        if not isinstance(join["selectors"], Mapping) or en != set(join["selectors"]):
            raise ValueError(f"{path}.join: selectors must match placeholders")
        for name, selector in join["selectors"].items():
            _selector(selector, path + ".join." + name, item=True)
        _localized(join["separator"], path + ".join.separator", empty=True)


def validate_narratives(data: Any, *, bilingual: bool = False, recipes: bool = False) -> None:
    """Validate plain declarations; the legacy authored format may omit zh."""
    if not isinstance(data, Mapping) or not data:
        raise ValueError("event_narratives: mapping required")
    for schema, declaration in data.items():
        path = f"event_narratives.{schema}"
        if type(schema) is not str or not schema or not isinstance(declaration, Mapping):
            raise ValueError(f"{path}: schema id and mapping required")
        allowed = {"template", "template_zh", "category", "severity", "entities", "position", "hidden"}
        if recipes:
            allowed |= {"selectors", "variants", "legacy_variants", "category_field", "category_map", "entity_list"}
        if set(declaration) - allowed:
            raise ValueError(f"{path}: unknown keys {sorted(set(declaration) - allowed)}")
        for name in ("template", "category", "severity"):
            if name not in declaration:
                raise ValueError(f"{path}: missing {name}")
        en = placeholders(declaration["template"], path + ".template")
        if bilingual and "template_zh" not in declaration:
            raise ValueError(f"{path}.template_zh: required")
        if "template_zh" in declaration and en != placeholders(declaration["template_zh"], path + ".template_zh"):
            raise ValueError(f"{path}.template_zh: bilingual placeholders must match template")
        if (type(declaration["category"]) is not str or not declaration["category"]
                or (not recipes and declaration["category"] not in CATEGORIES)):
            raise ValueError(f"{path}.category: unknown category")
        if type(declaration["severity"]) is not str or declaration["severity"] not in SEVERITIES:
            raise ValueError(f"{path}.severity: unknown severity")
        entities = declaration.get("entities", ())
        if not isinstance(entities, (tuple, list)) or any(type(name) is not str or not name for name in entities):
            raise ValueError(f"{path}.entities: list of strings required")
        for key in ("position", "entity_list", "category_field"):
            if key in declaration and (type(declaration[key]) is not str or not declaration[key]):
                raise ValueError(f"{path}.{key}: string required")
        if "hidden" in declaration and type(declaration["hidden"]) is not bool:
            raise ValueError(f"{path}.hidden: boolean required")
        selectors = declaration.get("selectors", {})
        if not isinstance(selectors, Mapping):
            raise ValueError(f"{path}.selectors: mapping required")
        for name, selector in selectors.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"{path}.selectors: invalid placeholder name")
            _selector(selector, path + ".selectors." + name)
        if "category_map" in declaration and (
            not isinstance(declaration["category_map"], Mapping)
            or any(type(value) is not str or not value for value in declaration["category_map"].values())
        ):
            raise ValueError(f"{path}.category_map: text mapping required")
        variants = declaration.get("variants", {})
        if not isinstance(variants, Mapping):
            raise ValueError(f"{path}.variants: mapping required")
        legacy_variants = declaration.get("legacy_variants", ())
        if (not isinstance(legacy_variants, (list, tuple))
                or any(type(name) is not str or name not in variants for name in legacy_variants)
                or len(set(legacy_variants)) != len(legacy_variants)):
            raise ValueError(f"{path}.legacy_variants: unique declared variant names required")
        for name, variant in variants.items():
            if not isinstance(variant, Mapping) or {"variants", "legacy_variants"} & set(variant):
                raise ValueError(f"{path}.variants.{name}: one-level declaration required")
            merged = {**declaration, **variant}
            merged.pop("variants", None)
            merged.pop("legacy_variants", None)
            validate_narratives({schema: merged}, bilingual=bilingual, recipes=recipes)


class NarrativeNormalizer(Protocol):
    """Pure read reduction to normalized fields, never to rendered prose.

    Inputs are frozen JSON records: event {kind, schema, payload, evidence}, or
    fact {kind, entity (full identity), field, value, evidence}. Only declared
    exact schemas/fields are delivered. State and results must be finite JSON.
    Output is {payload, variant?}; a variant selects DATA in the declaration.
    """

    id: str
    version: str
    input_schemas: tuple[str, ...]
    fact_fields: tuple[str, ...]
    output_schemas: Mapping[str, Mapping[str, Any]]

    def initial(self) -> Any: ...
    def consume(self, state: Any, record: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


def _synchronous(value: Any) -> bool:
    return callable(value) and not any(
        check(candidate) for check in (iscoroutinefunction, isasyncgenfunction)
        for candidate in (value, getattr(value, "__call__", None))
    )


def _implementation(value: Any) -> str:
    if isinstance(value, _FrozenNormalizer):
        return value.implementation
    target = value if hasattr(value, "__qualname__") else type(value)
    return f"{target.__module__}:{target.__qualname__}"


def _source(value: Any) -> None:
    if not isinstance(value, Mapping) or (value and set(value) != {"kind", "entry_point", "distribution"}):
        raise ValueError("presentation source: observed kind/entry_point/distribution mapping required")
    if not value:
        return  # Explicit in-process selection has no observed installation claim.
    if type(value["kind"]) is not str or value["kind"] not in {"builtin", "installed"}:
        raise ValueError("presentation source kind: builtin or installed required")
    entry, distribution = value["entry_point"], value["distribution"]
    if entry is not None and (type(entry) is not str or not entry):
        raise ValueError("presentation source entry_point: nonempty text or None required")
    if distribution is not None and (
        not isinstance(distribution, (tuple, list)) or len(distribution) != 2
        or any(type(part) is not str or not part for part in distribution)
    ):
        raise ValueError("presentation source distribution: name/version pair or None required")


def _normalized_registry(schemas: Mapping[str, Any]) -> Any:
    """Use the existing portable schema compiler, never an expression evaluator."""
    from aerokernel.errors import KernelError
    from aerokernel.registry import MemoryRegistry, TypeDescriptor

    targets: set[str] = set()

    def visit(value: Any) -> None:
        if not isinstance(value, Mapping):
            return  # The portable compiler diagnoses malformed schema nodes.
        kind = value.get("type")
        if kind == "ref" and "target_type" in value:
            target = value["target_type"]
            if type(target) is not str or not target:
                raise ValueError("presentation normalized ref: target_type text required")
            targets.add(target)
        if kind in {"array", "vector", "matrix"}:
            visit(value.get("items"))
        for name in ("members", "cases"):
            children = value.get(name, {})
            if isinstance(children, Mapping):
                for child in children.values():
                    visit(child)

    detached = json_data(schemas)
    for schema in detached.values():
        visit(schema)
    try:
        registry = MemoryRegistry(tuple(TypeDescriptor(name) for name in sorted(targets)), schemas=detached)
        if any(_resolved_schema(schema, registry)["type"] != "record" for schema in detached.values()):
            raise ValueError("presentation normalized payload: record schema required")
    except KernelError as exc:
        raise ValueError(f"presentation normalized payload schema: {exc}") from exc
    return registry


@dataclass(frozen=True)
class _FrozenNormalizer:
    """Owned descriptors plus trusted callbacks; never reread provider metadata."""

    id: str
    version: str
    implementation: str
    input_schemas: tuple[str, ...]
    fact_fields: tuple[str, ...]
    output_schemas: Mapping[str, Mapping[str, Any]]
    initial: Callable[[], Any]
    consume: Callable[[Any, Mapping[str, Any]], Mapping[str, Any] | None]

    def __post_init__(self) -> None:
        # Own every DATA container, including inputs supplied by dataclasses.replace.
        for name in ("input_schemas", "fact_fields", "output_schemas"):
            object.__setattr__(self, name, freeze_data(json_data(getattr(self, name))))


@dataclass(frozen=True)
class PresentationPlugin:
    name: str
    data: Mapping[str, Any]
    api_version: int = 1
    revision: str = "1"
    normalizers: tuple[NarrativeNormalizer, ...] = ()
    control_readers: Mapping[str, Any] = field(default_factory=dict)
    source: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.name) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.name):
            raise ValueError("presentation plugin name must match [a-z0-9][a-z0-9_-]*")
        if type(self.api_version) is not int or self.api_version != PRESENTATION_API_VERSION:
            raise ValueError(f"presentation plugin {self.name!r}: unsupported API version {self.api_version!r}")
        if type(self.revision) is not str or not self.revision:
            raise ValueError("presentation plugin revision: nonempty text required")
        detached = json_data(self.data)
        validate_presentation(detached)
        object.__setattr__(self, "data", freeze_data(detached))
        if not isinstance(self.normalizers, tuple):
            raise ValueError("presentation normalizers must be a tuple")
        ids: set[str] = set()
        schemas: set[str] = set()
        normalizers: list[_FrozenNormalizer] = []
        for normalizer in self.normalizers:
            for name in ("id", "version"):
                if type(getattr(normalizer, name, None)) is not str or not getattr(normalizer, name):
                    raise ValueError(f"presentation normalizer {name}: nonempty text required")
            if normalizer.id in ids:
                raise ValueError(f"Duplicate presentation normalizer id: {normalizer.id}")
            ids.add(normalizer.id)
            for name in ("input_schemas", "fact_fields"):
                values = getattr(normalizer, name, None)
                if (not isinstance(values, tuple) or any(type(item) is not str or not item for item in values)
                        or len(set(values)) != len(values)):
                    raise ValueError(f"presentation normalizer {name}: tuple of unique exact ids required")
            for schema in normalizer.input_schemas:
                if schema in schemas or schema not in self.data.get("narratives", {}):
                    raise ValueError(f"Conflicting or undeclared normalized schema: {schema}")
                schemas.add(schema)
            for name in ("initial", "consume"):
                method = getattr(normalizer, name, None)
                if not _synchronous(method):
                    raise ValueError(f"presentation normalizer {name}: synchronous callable required")
            if not isinstance(getattr(normalizer, "output_schemas", None), Mapping) or set(normalizer.output_schemas) != set(normalizer.input_schemas):
                raise ValueError("presentation normalizer must declare each normalized payload schema")
            if not isinstance(normalizer, _FrozenNormalizer):
                normalizer = _FrozenNormalizer(
                    id=normalizer.id, version=normalizer.version,
                    implementation=f"{type(normalizer).__module__}:{type(normalizer).__qualname__}",
                    input_schemas=tuple(normalizer.input_schemas), fact_fields=tuple(normalizer.fact_fields),
                    output_schemas=normalizer.output_schemas,
                    initial=normalizer.initial, consume=normalizer.consume,
                )
            schema_registry = _normalized_registry(normalizer.output_schemas)
            validate_references(self.data, schema_registry, schemas=normalizer.output_schemas,
                                selected=set(normalizer.input_schemas))
            normalizers.append(normalizer)
        # Freeze at construction for both discovered and explicit providers, so
        # identity-based snapshot/digest hits cannot hide later descriptor drift.
        object.__setattr__(self, "normalizers", tuple(normalizers))
        if not isinstance(self.control_readers, Mapping) or not isinstance(self.source, Mapping):
            raise ValueError("presentation control_readers/source: mappings required")
        _source(self.source)
        object.__setattr__(self, "source", freeze_data(json_data(self.source)))
        readers = dict(self.control_readers)
        for name, reader in readers.items():
            if type(name) is not str or not name or not _synchronous(reader):
                raise ValueError("presentation control readers require exact ids and synchronous callables")
        object.__setattr__(self, "control_readers", MappingProxyType(readers))


def _header_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(f"{path}: string-keyed mapping required")
    return value


def _header_text(value: Any, path: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{path}: nonempty string required")


def _header_keys(
    value: Mapping[str, Any], path: str, required: set[str], optional: set[str],
) -> None:
    if missing := required - value.keys():
        raise ValueError(f"{path}: missing keys {sorted(missing)}")
    if extra := value.keys() - required - optional:
        raise ValueError(f"{path}: unknown keys {sorted(extra)}")


def _header_rows(value: Any, path: str) -> list[Any] | tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{path}: list required")
    return value


def _header_strings(value: Any, path: str) -> None:
    items = _header_rows(value, path)
    if not items:
        raise ValueError(f"{path}: nonempty list required")
    for item in items:
        _header_text(item, path)
    if len(set(items)) != len(items):
        raise ValueError(f"{path}: duplicate values")


def validate_header_hints(data: Any, *, path: str = "presentation.header") -> None:
    """Validate finite header DATA without importing its runtime interpreter.

    Optional absent sections are empty contributions. A present malformed
    section is an admission error, not an absent hint. This validator performs
    no discovery, resource lookup, file I/O, or provider invocation.
    """
    hints = _header_mapping(data, path)
    _header_keys(hints, path, set(), {"engine_gusts", "lighting", "scenes"})
    for index, value in enumerate(_header_rows(hints.get("engine_gusts", ()), path + ".engine_gusts")):
        location = f"{path}.engine_gusts[{index}]"
        row = _header_mapping(value, location)
        _header_keys(row, location, {"plugin", "fields"}, set())
        _header_text(row["plugin"], location + ".plugin")
        _header_strings(row["fields"], location + ".fields")
    for index, value in enumerate(_header_rows(hints.get("lighting", ()), path + ".lighting")):
        location = f"{path}.lighting[{index}]"
        row = _header_mapping(value, location)
        _header_keys(row, location, {"type_id", "field_id", "values"}, set())
        _header_text(row["type_id"], location + ".type_id")
        _header_text(row["field_id"], location + ".field_id")
        _header_strings(row["values"], location + ".values")
    for index, value in enumerate(_header_rows(hints.get("scenes", ()), path + ".scenes")):
        location = f"{path}.scenes[{index}]"
        row = _header_mapping(value, location)
        _header_keys(row, location, {"when", "resource", "visuals"}, set())
        when = _header_mapping(row["when"], location + ".when")
        if not when:
            raise ValueError(location + ".when: nonempty mapping required")
        for key, expected in when.items():
            _header_text(key, location + ".when")
            _header_text(expected, location + ".when." + key)
        _header_text(row["resource"], location + ".resource")
        if row["resource"] not in HEADER_RESOURCE_KEYS:
            raise ValueError(location + f".resource: unknown resource {row['resource']!r}")
        visuals = _header_mapping(row["visuals"], location + ".visuals")
        for type_id, value in visuals.items():
            _header_text(type_id, location + ".visuals")
            visual_path = location + ".visuals." + type_id
            visual = _header_mapping(value, visual_path)
            _header_keys(visual, visual_path, set(), {"model", "catalog"})
            if not visual:
                raise ValueError(visual_path + ": model or catalog required")
            for key, item in visual.items():
                _header_text(item, visual_path + "." + key)
            if "model" in visual and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", visual["model"]):
                raise ValueError(visual_path + ".model: simple model name required")


def validate_presentation(data: Any, registry: Any = None) -> None:
    if not isinstance(data, Mapping) or data.get("format") != PRESENTATION_FORMAT:
        raise ValueError(f"presentation format must be {PRESENTATION_FORMAT}")
    allowed = {"format", "labels", "numbered_labels", "label_groups", "enums", "narratives", "poses", "metrics", "header"}
    if set(data) - allowed:
        raise ValueError(f"presentation: unknown keys {sorted(set(data) - allowed)}")
    for key in ("labels", "numbered_labels"):
        if not isinstance(data.get(key, {}), Mapping):
            raise ValueError(f"presentation.{key}: mapping required")
        for identity, label in data.get(key, {}).items():
            _localized(label, f"presentation.{key}.{identity}")
    groups = data.get("label_groups", [])
    if not isinstance(groups, (list, tuple)):
        raise ValueError("presentation.label_groups: list required")
    for group in groups:
        if not isinstance(group, Mapping) or set(group) != {"prefix", "names", "numbered"}:
            raise ValueError("presentation.label_groups: prefix/names/numbered required")
        _localized(group["prefix"], "presentation.label_groups.prefix")
        for name in ("names", "numbered"):
            if not isinstance(group[name], Mapping):
                raise ValueError(f"presentation.label_groups.{name}: mapping required")
            for label in group[name].values():
                _localized(label, f"presentation.label_groups.{name}")
    enums = data.get("enums", {})
    if not isinstance(enums, Mapping):
        raise ValueError("presentation.enums: schema -> field -> enum mapping required")
    for schema, fields in enums.items():
        if not isinstance(fields, Mapping):
            raise ValueError(f"presentation.enums.{schema}: field mapping required")
        for name, values in fields.items():
            if not isinstance(values, Mapping):
                raise ValueError(f"presentation.enums.{schema}.{name}: enum mapping required")
            for value, label in values.items():
                _localized(label, f"presentation.enums.{schema}.{name}.{value}")
    narratives = data.get("narratives", {})
    if not isinstance(narratives, Mapping):
        raise ValueError("presentation.narratives: mapping required")
    if narratives:
        validate_narratives(narratives, bilingual=True, recipes=True)
    poses = data.get("poses", [])
    if not isinstance(poses, (list, tuple)):
        raise ValueError("presentation.poses: list required")
    for pose in poses:
        _validate_pose(pose, registry)
    metrics = data.get("metrics", {})
    if not isinstance(metrics, Mapping):
        raise ValueError("presentation.metrics: mapping required")
    for name, hint in metrics.items():
        if not isinstance(hint, Mapping) or set(hint) - {"label", "unit", "decimals", "style"}:
            raise ValueError(f"presentation.metrics.{name}: invalid formatting hint")
        if "label" in hint:
            _localized(hint["label"], f"presentation.metrics.{name}.label")
        if "unit" in hint and type(hint["unit"]) is not str:
            raise ValueError("presentation metric unit: text required")
        if "decimals" in hint and (type(hint["decimals"]) is not int or not 0 <= hint["decimals"] <= 9):
            raise ValueError("presentation metric decimals: integer 0..9 required")
        if type(hint.get("style", "number")) is not str or hint.get("style", "number") not in {"number", "percent", "duration"}:
            raise ValueError("presentation metric style: number, percent or duration required")
    if "header" in data:
        validate_header_hints(data["header"])
    if registry is not None:
        validate_references(data, registry)


def _metadata(value: Any, member: str) -> Any:
    return value.get(member) if isinstance(value, Mapping) else value


def _validate_pose(pose: Any, registry: Any) -> None:
    required = {"typeId", "positionField", "frame", "visual"}
    optional = {"orientationField", "role", "camera", "optional"}
    if not isinstance(pose, Mapping) or required - set(pose) or set(pose) - required - optional:
        raise ValueError("presentation pose: malformed binding")
    for name in ("typeId", "positionField", "orientationField", "role"):
        if name in pose and (type(pose[name]) is not str or not pose[name]):
            raise ValueError(f"presentation pose {name}: nonempty text required")
    if "optional" in pose and type(pose["optional"]) is not bool:
        raise ValueError("presentation pose optional: boolean required")
    if type(pose["frame"]) is not str or pose["frame"] not in {"enu", "ned", "wgs84"}:
        raise ValueError("presentation pose frame: enu, ned or wgs84 required")
    visual = pose["visual"]
    if (not isinstance(visual, Mapping) or set(visual) - {"kind", "asset", "scale", "color"}
            or type(visual.get("kind")) is not str or visual["kind"] not in {"marker", "model", "label"}):
        raise ValueError("presentation pose visual: finite marker/model/label required")
    if visual["kind"] == "model" and "asset" not in visual:
        raise ValueError("presentation pose model: asset required")
    for name in ("asset", "color"):
        if name in visual and (type(visual[name]) is not str or not visual[name]):
            raise ValueError(f"presentation pose visual {name}: text required")
    if "asset" in visual and not (visual["asset"].startswith(("/v1/", "procedural:"))):
        raise ValueError("presentation pose asset: host-owned /v1/ or procedural resource required")
    if "scale" in visual and (type(visual["scale"]) not in (int, float) or not math.isfinite(visual["scale"]) or visual["scale"] <= 0):
        raise ValueError("presentation pose scale: finite positive number required")
    camera = pose.get("camera", {})
    if not isinstance(camera, Mapping) or set(camera) - {"priority", "distance_m"}:
        raise ValueError("presentation pose camera: priority/distance_m only")
    for name, value in camera.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"presentation pose camera {name}: finite nonnegative number required")
    if registry is None:
        return
    types = {item.id for item in registry.types}
    fields = {item.id: item for item in registry.fields}
    missing = pose["typeId"] not in types or any(pose[name] not in fields for name in ("positionField", "orientationField") if name in pose)
    for name, length in (("positionField", 3), ("orientationField", 4)):
        if name not in pose or pose[name] not in fields:
            continue
        descriptor = fields[pose[name]]
        schema = _resolved_schema(descriptor.schema, registry)
        if ((pose["typeId"] in types and not registry.is_a(pose["typeId"], descriptor.declaring_type))
                or schema.get("type") != "vector" or schema.get("length") != length
                or _resolved_schema(schema.get("items", {}), registry).get("type") not in {"number", "integer"}):
            raise ValueError(f"presentation pose {name}: applicable {length}-vector required")
        frame = _metadata(descriptor.metadata.get("frame"), "convention")
        if frame is not None and frame != pose["frame"]:
            raise ValueError(f"presentation pose {name}: frame mismatch")
        unit = _metadata(descriptor.metadata.get("unit"), "symbol")
        if name == "positionField" and pose["frame"] in {"enu", "ned"} and unit not in {None, "m"}:
            raise ValueError("presentation pose positionField: metre unit required")
    if missing and not pose.get("optional"):
        raise ValueError("presentation pose: unknown type or field")


def _resolved_schema(schema: Any, registry: Any) -> Mapping[str, Any]:
    seen: set[str] = set()
    while isinstance(schema, Mapping) and "schema_ref" in schema:
        name = schema["schema_ref"]
        if registry is None or name in seen or name not in registry.schemas:
            raise ValueError("presentation: unresolved referenced field schema")
        seen.add(name)
        schema = registry.schemas[name]
    if not isinstance(schema, Mapping) or "type" not in schema:
        raise ValueError("presentation: malformed referenced field schema")
    return schema


def _schema_path(schema: Mapping[str, Any], path: Any, registry: Any = None) -> Mapping[str, Any]:
    for part in path:
        schema = _resolved_schema(schema, registry)
        if type(part) is str and schema.get("type") == "record" and part in schema.get("members", {}):
            schema = schema["members"][part]
        elif type(part) is int and schema.get("type") in {"array", "vector"} and (
            schema.get("type") != "vector" or part < schema["length"]
        ) and ("max_length" not in schema or part < schema["max_length"]):
            schema = schema["items"]
        else:
            raise ValueError(f"presentation: unknown referenced field path {list(path)!r}")
    return _resolved_schema(schema, registry)


def _selector_reference(selector: Mapping[str, Any], schema: Mapping[str, Any], registry: Any,
                        path: str) -> Mapping[str, Any]:
    member = _schema_path(schema, selector["path"], registry)
    if selector.get("label") and member["type"] not in {"string", "ref"}:
        raise ValueError(f"{path}: label requires identity text/ref")
    if "enum" in selector and member["type"] not in {"string", "boolean", "integer", "number"}:
        raise ValueError(f"{path}: enum requires scalar field")
    if "join" in selector:
        if member["type"] not in {"array", "vector"}:
            raise ValueError(f"{path}: join requires array")
        for name, child in selector["join"]["selectors"].items():
            _selector_reference(child, member["items"], registry, path + "." + name)
    return member


def _identity_schema(schema: Mapping[str, Any], registry: Any, *, arrays: bool = True) -> bool:
    schema = _resolved_schema(schema, registry)
    return schema["type"] in {"string", "ref"} or (
        arrays and schema["type"] == "array"
        and _resolved_schema(schema["items"], registry)["type"] in {"string", "ref"}
    )


def validate_references(data: Mapping[str, Any], registry: Any, *, schemas: Mapping[str, Any] | None = None,
                        selected: set[str] | None = None) -> None:
    raw = {item.id: item.schema for item in registry.messages} if registry is not None else {}
    available = {**raw, **(schemas or {})}
    for schema_id, declaration in data.get("narratives", {}).items():
        if selected is not None and schema_id not in selected:
            continue
        if schema_id not in available:
            continue  # Catalogue membership is not activation in an unrelated domain.
        schema = available[schema_id]
        for recipe in (declaration, *({**declaration, **variant} for variant in declaration.get("variants", {}).values())):
            selectors = recipe.get("selectors", {})
            names = placeholders(recipe["template"], schema_id + ".template") | set(selectors)
            names |= set(recipe.get("entities", ()))
            if "position" in recipe:
                names.add(recipe["position"])
            for name in names:
                selector = selectors.get(name, {"path": [name]})
                member = _selector_reference(selector, schema, registry, f"presentation {schema_id}.{name}")
                if name in recipe.get("entities", ()) and not _identity_schema(member, registry):
                    raise ValueError(f"presentation {schema_id}.{name}: entity requires identity text/ref")
                if name == recipe.get("position") and member["type"] not in {"string", "ref"} and not (
                    member["type"] == "vector" and member.get("length") == 3
                    and _resolved_schema(member.get("items", {}), registry)["type"] in {"number", "integer"}
                ):
                    raise ValueError(f"presentation {schema_id}.{name}: position requires ENU vector or identity")
            if "category_field" in recipe:
                member = _schema_path(schema, [recipe["category_field"]], registry)
                if member["type"] not in {"string", "boolean", "integer"}:
                    raise ValueError(f"presentation {schema_id}: category requires scalar field")
            if "entity_list" in recipe:
                member = _schema_path(schema, [recipe["entity_list"]], registry)
                if member["type"] != "array" or not _identity_schema(member["items"], registry, arrays=False):
                    raise ValueError(f"presentation {schema_id}: entity_list requires identity array")
    for schema_id, fields in data.get("enums", {}).items():
        if schema_id not in raw or (selected is not None and schema_id not in selected):
            continue
        for name in fields:
            if _schema_path(raw[schema_id], [name], registry)["type"] not in {"string", "boolean", "integer", "number"}:
                raise ValueError(f"presentation {schema_id}.{name}: enum requires scalar field")


def admit_presentation_plugins(
    plugins: tuple[PresentationPlugin, ...], registry: Any, *, document: Mapping[str, Any] | None = None,
) -> None:
    """Validate selected field bindings before projection or service execution."""
    validate_plugins(plugins)
    for plugin in plugins:
        for pose in plugin.data.get("poses", ()):
            _validate_pose(pose, registry)
            if document is not None and pose["frame"] == "wgs84" and "origin" not in document and not pose.get("optional"):
                raise ValueError("presentation.wgs84: explicit origin required")
        normalized_ids = {schema for normalizer in plugin.normalizers for schema in normalizer.input_schemas}
        validate_references({
            "narratives": {schema: recipe for schema, recipe in plugin.data.get("narratives", {}).items()
                           if schema not in normalized_ids},
            "enums": plugin.data.get("enums", {}),
        }, registry)
        # Normalized descriptors (including output schemas) are detached/frozen
        # and their DATA references admitted by PresentationPlugin construction.
        # Only run-specific bindings belong here; no per-launch recompilation.
        fields = {item.id: item for item in registry.fields}
        types = {item.id for item in registry.types}
        for lighting in plugin.data.get("header", {}).get("lighting", ()):
            descriptor = fields.get(lighting["field_id"])
            if descriptor is not None and (
                _resolved_schema(descriptor.schema, registry)["type"] != "string" or (
                    lighting["type_id"] in types
                    and not registry.is_a(lighting["type_id"], descriptor.declaring_type)
                )
            ):
                raise ValueError("presentation lighting: applicable string field required")


def resolve_poses(data: Mapping[str, Any], registry: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Absent optional visuals diagnose and degrade; malformed bindings still fail."""
    validate_presentation(data, registry)
    fields = {item.id for item in registry.fields}
    types = {item.id for item in registry.types}
    poses, diagnostics = [], []
    for pose in data.get("poses", ()):
        if pose["typeId"] not in types or any(pose[name] not in fields for name in ("positionField", "orientationField") if name in pose):
            diagnostics.append(f"Optional pose unavailable for {pose['typeId']}; generic labelled view")
        else:
            poses.append({key: json_data(value) for key, value in pose.items() if key != "optional"})
    return poses, diagnostics


def _builtin(module: str) -> Any:
    return lambda: import_module(module).plugin()


BUILTINS = MappingProxyType({
    "lowalt": _builtin("aeroagentsim.packs.lowalt.presentation"),
    "traffic_accident": _builtin("aeroagentsim.packs.traffic_accident.presentation"),
})


# Serialize cold cache fills too: functools caches alone can compute a miss
# more than once when the server's request and projection threads race.
_PRESENTATION_DISCOVERY_LOCK = RLock()
_PRESENTATION_CACHE_GENERATION = 0


def presentation_plugin_cache_generation() -> int:
    """Let readers invalidate derived caches without a contract-to-runtime import."""
    return _PRESENTATION_CACHE_GENERATION


def clear_presentation_plugin_cache() -> None:
    """Forget discovery/fingerprints and invalidate cached pinned selections.

    Installed entry points are assumed fixed for the process lifetime. Tests
    replacing entry points or BUILTINS must clear before using the replacement
    and again at teardown. Existing plugin tuples/projectors remain unchanged;
    this is not a live plugin reload or a reset of projection state.
    """
    global _PRESENTATION_CACHE_GENERATION
    with _PRESENTATION_DISCOVERY_LOCK:
        _presentation_provider.cache_clear()
        _presentation_discovery.cache_clear()
        _PRESENTATION_DIGESTS.clear()
        _PRESENTATION_CACHE_GENERATION += 1


@lru_cache(maxsize=1)
def _presentation_discovery() -> DiscoveryResult[PresentationPlugin]:
    discovery = discover(
        PRESENTATION_ENTRY_POINT_GROUP, BUILTINS, expected_type=PresentationPlugin,
        api_version=PRESENTATION_API_VERSION, name_of=lambda plugin: plugin.name,
        factory_label="presentation plugin", entries=entry_points(group=PRESENTATION_ENTRY_POINT_GROUP),
        load=False,
    )
    discovery.require_unique()
    for contribution in discovery.contributions:
        origin = "entry point" if contribution.source == "installed" else "builtin"
        validate_name(contribution.name, label=f"{PRESENTATION_ENTRY_POINT_GROUP} {origin}")
    return discovery


@lru_cache(maxsize=None)
def _presentation_provider(name: str) -> PresentationPlugin:
    discovery = _presentation_discovery()
    value = discovery.resolve(name)
    entry = next((item for item in discovery.contributions
                  if item.name == name and item.source == "installed"), None)
    if entry is not None and entry.distribution is not None and entry.version is not None:
        release = [entry.distribution, entry.version]
    elif entry is None:
        try:
            release = ["aeroagentsim", version("aeroagentsim")]
        except PackageNotFoundError:
            release = ["aeroagentsim", "source"]
    else:
        release = None
    return replace(value, source={
        "kind": "installed" if entry is not None else "builtin",
        "entry_point": entry.entry_point if entry is not None else None, "distribution": release,
    })


def discover_presentation_plugins(*, names: tuple[str, ...] | None = None) -> tuple[PresentationPlugin, ...]:
    """Reuse process-local metadata and immutable providers, loading each once.

    Installed entry points do not change within a server process. Enumeration
    and name admission happen once; only selected whole providers are loaded,
    lazily and at most once each, even across different selections. Duplicates
    and ALL declared names are admitted before any provider is loaded. Use
    clear_presentation_plugin_cache explicitly in tests changing installations.
    """
    with _PRESENTATION_DISCOVERY_LOCK:
        discovery = _presentation_discovery()
        selected_names = discovery.names
        if names is not None:
            if (not isinstance(names, tuple) or any(type(name) is not str or not name for name in names)
                    or len(set(names)) != len(names)):
                raise ValueError("presentation selection: unique provider names required")
            if missing := set(names) - set(selected_names):
                raise ValueError(f"pinned presentation unavailable: missing providers {sorted(missing)}")
            selected_names = tuple(sorted(names))
        plugins = tuple(_presentation_provider(name) for name in selected_names)
        validate_plugins(plugins)
        return plugins


def _contribution_ids(
    data: Mapping[str, Any], *, normalizers: Iterable[str] = (), readers: Iterable[str] = (),
) -> Iterable[tuple[str, Any]]:
    for kind in ("narratives", "labels", "numbered_labels", "metrics"):
        for key in data.get(kind, {}):
            yield kind, key
    for schema, fields in data.get("enums", {}).items():
        for name in fields:
            yield "enums", (schema, name)
    for group in data.get("label_groups", ()):
        yield "label_groups", group["prefix"]["en"]
    for pose in data.get("poses", ()):
        yield "poses", pose["typeId"]
    for name in readers:
        yield "control_readers", name
    for name in normalizers:
        yield "normalizers", name


def validate_plugins(plugins: tuple[PresentationPlugin, ...]) -> None:
    ids: dict[tuple[str, Any], str] = {}
    names: set[str] = set()
    for plugin in plugins:
        if not isinstance(plugin, PresentationPlugin):
            raise TypeError("Expected PresentationPlugin")
        if plugin.name in names:
            raise ValueError(f"Duplicate presentation plugin name: {plugin.name}")
        names.add(plugin.name)
        for key in _contribution_ids(plugin.data, readers=plugin.control_readers,
                                     normalizers=(item.id for item in plugin.normalizers)):
            if key in ids:
                raise ValueError(f"Conflicting presentation id {key[0]}.{key[1]}: {ids[key]} and {plugin.name}")
            ids[key] = plugin.name


def presentation_snapshot(
    plugins: tuple[PresentationPlugin, ...], *, controls: tuple[Mapping[str, Any], ...] | None = None,
    registry: Any = None, document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Detached family selection/data, optionally admitting run-specific bindings."""
    if registry is None:
        validate_plugins(plugins)
    else:
        admit_presentation_plugins(plugins, registry, document=document)
    providers = []
    for plugin in plugins:
        providers.append({
            "name": plugin.name, "api_version": plugin.api_version, "revision": plugin.revision,
            "source": json_data(plugin.source), "data": json_data(plugin.data),
            "normalizers": [{"id": item.id, "version": item.version,
                             "implementation": _implementation(item),
                             "input_schemas": list(item.input_schemas), "fact_fields": list(item.fact_fields),
                             "output_schemas": json_data(item.output_schemas)} for item in plugin.normalizers],
            "control_readers": {name: _implementation(reader) for name, reader in plugin.control_readers.items()},
        })
    result = {"format": PRESENTATION_SNAPSHOT_FORMAT, "providers": providers}
    if controls is not None:
        result["controls"] = json_data(ControlRegistry(controls=controls).controls)
    return {**result, "digest": presentation_digest(result)}


# Legacy unpinned directories have no stored digest. Retain their fingerprint
# by provider *identity* (source is excluded from dataclass equality), bounded
# and holding strong references so object IDs cannot be recycled under a hit.
_PRESENTATION_DIGESTS: OrderedDict[
    tuple[int, ...], tuple[tuple[PresentationPlugin, ...], str]
] = OrderedDict()


def presentation_plugin_digest(plugins: tuple[PresentationPlugin, ...]) -> str:
    """Reuse immutable provider fingerprints instead of serializing each page."""
    key = tuple(id(plugin) for plugin in plugins)
    with _PRESENTATION_DISCOVERY_LOCK:
        if key not in _PRESENTATION_DIGESTS:
            _PRESENTATION_DIGESTS[key] = (plugins, presentation_snapshot(plugins)["digest"])
        _PRESENTATION_DIGESTS.move_to_end(key)
        while len(_PRESENTATION_DIGESTS) > 128:
            _PRESENTATION_DIGESTS.popitem(last=False)
        return _PRESENTATION_DIGESTS[key][1]


def validate_presentation_snapshot(snapshot: Any) -> None:
    if isinstance(snapshot, _ValidatedSnapshot):
        return  # Only owned, recursively frozen, already-admitted DATA can skip.
    if (not isinstance(snapshot, Mapping) or snapshot.get("format") != PRESENTATION_SNAPSHOT_FORMAT
            or set(snapshot) - {"format", "providers", "controls", "digest"}
            or not isinstance(snapshot.get("providers"), (list, tuple))):
        raise ValueError("pinned presentation: malformed snapshot or unsupported version")
    if snapshot.get("digest") != presentation_digest({key: value for key, value in snapshot.items() if key != "digest"}):
        raise ValueError("pinned presentation: snapshot digest mismatch")
    names: set[str] = set()
    contributed: dict[tuple[str, Any], str] = {}
    for provider in snapshot["providers"]:
        if not isinstance(provider, Mapping) or set(provider) != {
            "name", "api_version", "revision", "source", "data", "normalizers", "control_readers"
        }:
            raise ValueError("pinned presentation: malformed provider")
        name = provider["name"]
        if type(name) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name) or name in names:
            raise ValueError("pinned presentation: invalid or duplicate provider name")
        names.add(name)
        if type(provider["api_version"]) is not int or provider["api_version"] != PRESENTATION_API_VERSION:
            raise ValueError("pinned presentation: unsupported provider API version")
        if type(provider["revision"]) is not str or not provider["revision"]:
            raise ValueError("pinned presentation: provider revision required")
        validate_presentation(provider["data"])
        _source(provider["source"])
        readers = provider["control_readers"]
        if not isinstance(readers, Mapping) or any(
            type(name) is not str or not name or type(implementation) is not str or not implementation
            for name, implementation in readers.items()
        ):
            raise ValueError("pinned presentation: malformed control reader metadata")
        normalizers = provider["normalizers"]
        if not isinstance(normalizers, (list, tuple)):
            raise ValueError("pinned presentation: normalizer metadata list required")
        normalizer_ids: set[str] = set()
        normalized_schemas: set[str] = set()
        for normalizer in normalizers:
            if not isinstance(normalizer, Mapping) or set(normalizer) != {
                "id", "version", "implementation", "input_schemas", "fact_fields", "output_schemas"
            }:
                raise ValueError("pinned presentation: malformed normalizer metadata")
            if any(type(normalizer[key]) is not str or not normalizer[key] for key in ("id", "version", "implementation")):
                raise ValueError("pinned presentation: normalizer identity/version required")
            if normalizer["id"] in normalizer_ids:
                raise ValueError("pinned presentation: duplicate normalizer id")
            normalizer_ids.add(normalizer["id"])
            for key in ("input_schemas", "fact_fields"):
                values = normalizer[key]
                if (not isinstance(values, (list, tuple)) or any(type(value) is not str or not value for value in values)
                        or len(set(values)) != len(values)):
                    raise ValueError("pinned presentation: malformed normalizer input metadata")
            for schema in normalizer["input_schemas"]:
                if schema in normalized_schemas or schema not in provider["data"].get("narratives", {}):
                    raise ValueError("pinned presentation: conflicting/undeclared normalized schema")
                normalized_schemas.add(schema)
            schemas = normalizer["output_schemas"]
            if not isinstance(schemas, Mapping) or set(schemas) != set(normalizer["input_schemas"]):
                raise ValueError("pinned presentation: normalized payload schemas required")
            schema_registry = _normalized_registry(schemas)
            validate_references(provider["data"], schema_registry, schemas=schemas,
                                selected=set(normalizer["input_schemas"]))
        for key in _contribution_ids(provider["data"], readers=readers, normalizers=normalizer_ids):
            if key in contributed:
                raise ValueError(f"pinned presentation: conflicting contributed id {key}: {contributed[key]} and {name}")
            contributed[key] = name
    if "controls" in snapshot:
        ControlRegistry(controls=snapshot["controls"])


def verify_presentation_snapshot(
    snapshot: Mapping[str, Any], available: tuple[PresentationPlugin, ...],
) -> tuple[PresentationPlugin, ...]:
    """Select exactly the pinned providers; never replace missing/changed meaning.

    Installation changes cannot activate an extra provider for a pinned run. An
    unavailable dependency is diagnosed; raw committed records are not rewritten.
    """
    snapshot = freeze_presentation_snapshot(snapshot)
    if any(not isinstance(plugin, PresentationPlugin) for plugin in available):
        raise TypeError("Expected PresentationPlugin")
    by_name = {plugin.name: plugin for plugin in available}
    if len(by_name) != len(available):
        raise ValueError("Duplicate presentation plugin name in available selection")
    names = [provider["name"] for provider in snapshot["providers"]]
    missing = set(names) - set(by_name)
    if missing:
        raise ValueError(f"pinned presentation unavailable: missing providers {sorted(missing)}")
    selected = tuple(by_name[name] for name in names)
    if freeze_data(presentation_snapshot(selected)["providers"]) != snapshot["providers"]:
        raise ValueError("pinned presentation unavailable: provider source/revision/DATA changed; matching contributions required")
    return selected


__all__ = [
    "PRESENTATION_API_VERSION", "PRESENTATION_ENTRY_POINT_GROUP", "PRESENTATION_FORMAT",
    "PRESENTATION_SNAPSHOT_FORMAT", "HEADER_RESOURCE_KEYS", "PresentationPlugin", "NarrativeNormalizer",
    "discover_presentation_plugins", "clear_presentation_plugin_cache", "presentation_plugin_cache_generation",
    "validate_presentation", "validate_header_hints", "admit_presentation_plugins", "resolve_poses", "presentation_snapshot",
    "freeze_presentation_snapshot", "presentation_plugin_digest",
    "validate_presentation_snapshot", "verify_presentation_snapshot",
    "ControlReader", "ControlRegistry", "Known", "Unavailable", "known", "read_controls",
]
