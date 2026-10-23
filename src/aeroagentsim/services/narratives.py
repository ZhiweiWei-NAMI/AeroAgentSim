"""Generic committed-event projection using presentation-family DATA.

The host owns interpolation, identity, evidence order, positions and dedup. Packs
own vocabulary and (where necessary) pure normalization of detached read records.
Missing placeholders fail; optional links and positions are never invented.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.messages import Emit, Message
from aerokernel.operations import Create, FactWrite, Remove, RetractFact
from aerokernel.registry import MemoryRegistry
from aerokernel.values import thaw

from ..presentation_api import (
    CATEGORIES,
    SEVERITIES,
    PresentationPlugin,
    admit_presentation_plugins,
    discover_presentation_plugins,
    freeze_data,
    json_data,
    placeholders,
    presentation_digest,
    presentation_snapshot,
    resolve_poses,
    validate_narratives,
    validate_plugins,
)
from ..presentations import presentation_label

_MISSING = object()
_CONTRACT_KEY = "\x00aeroagentsim.presentation/v1"  # Private cache metadata, never a binding.


@lru_cache(maxsize=1)
def _legacy_label_data() -> Mapping[str, Any]:
    # Resolve public DATA lazily, not a pack-private translation implementation.
    provider, = discover_presentation_plugins(names=("lowalt",))
    return provider.data


def _chinese_label(label: str) -> str:
    """Source-compatible vocabulary rendering through the generic interpreter."""
    return presentation_label(label, "zh", _legacy_label_data())


def __getattr__(name: str) -> Any:
    # Source-compatible legacy exports, not a domain dispatch in projection.
    if name in {"LOWALT_DEFAULTS", "TASK_KIND_CATEGORY", "TASK_NOUN", "TASK_DONE", "TASK_NOUN_ZH", "TASK_DONE_ZH"}:
        from ..packs.lowalt import presentation

        return getattr(presentation, name)
    raise AttributeError(name)


def validate_declarations(data: dict[str, Any]) -> None:
    """Retain the authored event_narratives/v1 admission and error contract."""
    validate_narratives(data)


def _is_enu(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(
        type(member) in (int, float) and math.isfinite(member) for member in value
    )


def _entity_id(value: Any) -> str | None:
    if type(value) is str and value:
        return value
    if isinstance(value, Mapping):
        ref = value.get("$ref", value)
        if isinstance(ref, Mapping) and type(ref.get("id")) is str and ref["id"]:
            return ref["id"]
    return None


def _at(value: Any, path: Any) -> Any:
    for part in path:
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, (tuple, list)) and type(part) is int and 0 <= part < len(value):
            value = value[part]
        else:
            return _MISSING
    return value


def _enum_key(value: Any, path: str) -> str:
    if value is not None and type(value) not in (str, int, float, bool):
        raise ValueError(f"{path}: enum requires scalar field")
    return value if type(value) is str else json.dumps(value, ensure_ascii=False, allow_nan=False)


def _merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for schema, declaration in source.items():
        if schema in target and target[schema] != declaration:
            raise ValueError(f"event_narratives: conflicting declaration for {schema}")
        target[schema] = json_data(declaration)


class NarrativeProjector:
    """Project each committed event emission once, without domain switches."""

    def __init__(
        self,
        declarations: dict[str, dict[str, Any]] | None = None,
        position_fields: set[str] | None = None,
        labels: dict[str, str] | None = None,
        labels_zh: dict[str, str] | None = None,
        *,
        plugins: tuple[PresentationPlugin, ...] | None = None,
    ) -> None:
        self._plugins = discover_presentation_plugins() if plugins is None else plugins
        validate_plugins(self._plugins)
        self._declarations: dict[str, Any] = {}
        self._cache_key: str | None = None
        self._vocabulary: dict[str, Any] = {}
        self._numbered: dict[str, Any] = {}
        self._label_groups: list[Any] = []
        self._enums: dict[str, Any] = {}
        self._normalizers: list[Any] = []
        self._states: list[Any] = []
        self._by_schema: dict[str, int] = {}
        self._by_field: dict[str, list[int]] = {}
        self._overrides: set[str] = set()
        for plugin in self._plugins:
            data = json_data(plugin.data)
            self._declarations.update(data.get("narratives", {}))
            self._vocabulary.update(data.get("labels", {}))
            self._numbered.update(data.get("numbered_labels", {}))
            self._label_groups.extend(data.get("label_groups", []))
            for schema, fields in data.get("enums", {}).items():
                self._enums.setdefault(schema, {}).update(fields)
            for normalizer in plugin.normalizers:
                index = len(self._normalizers)
                self._normalizers.append(normalizer)
                self._states.append(json_data(normalizer.initial()))
                for schema in normalizer.input_schemas:
                    if schema in self._by_schema:
                        raise ValueError(f"Conflicting normalized narrative schema: {schema}")
                    self._by_schema[schema] = index
                for field in normalizer.fact_fields:
                    self._by_field.setdefault(field, []).append(index)
        self._provider_declarations = deepcopy(self._declarations)
        if declarations is not None:
            self._override(declarations)
        self._labels = dict(labels or {})
        self._labels_zh = dict(labels_zh or {})
        self._seen: set[str] = set()
        self._position_fields = position_fields or set()
        self._positions: dict[tuple[str, str, str, int], tuple[float, ...]] = {}
        self._by_id: dict[str, list[tuple[str, str, str, int]]] = {}
        self._pin_contract()

    def _override(self, declarations: Mapping[str, Any]) -> None:
        validate_declarations(declarations)
        if self._cache_key is not None:
            self._declarations.pop(self._cache_key)
            self._cache_key = None
        for schema, declaration in declarations.items():
            # Retain normalized bindings, but do not inherit translations or
            # variants accidentally. Explicit legacy variants still apply below.
            base = self._provider_declarations.get(schema, {})
            legacy_keys = {"template", "template_zh", "category", "severity", "entities", "position", "hidden"}
            declared = json_data(declaration)
            if declared == {key: value for key, value in base.items() if key in legacy_keys}:
                # Old run materializers copied pack declarations into the pinned
                # scenario. Identical DATA is not an override of its own recipe.
                self._declarations[schema] = {**deepcopy(base), **declared}
                self._overrides.discard(schema)
            else:
                metadata = {key: base[key] for key in ("selectors", "category_field", "category_map") if key in base}
                self._declarations[schema] = {**metadata, **declared}
                self._overrides.add(schema)
            self._declarations[schema]["_legacy_bindings"] = True

    def _pin_contract(self) -> None:
        if self._cache_key is not None:
            self._declarations.pop(self._cache_key)
        self.contract_digest = presentation_digest({
            "providers": presentation_snapshot(self._plugins),
            "declarations": self._declarations, "labels": self._labels, "labels_zh": self._labels_zh,
        })
        # Retain the private declaration-fingerprint contract for callers even
        # when providers have NO narratives. The feed index fingerprints provider
        # DATA directly, without constructing a projector just for this mapping.
        # This hidden metadata is never an event declaration or viewer row.
        self._cache_key = _CONTRACT_KEY
        while self._cache_key in self._declarations:
            self._cache_key = "\x00" + self._cache_key
        self._declarations[self._cache_key] = {"hidden": True, "digest": self.contract_digest}

    @classmethod
    def from_directory(
        cls, path: str | Path, *, plugins: tuple[PresentationPlugin, ...] | None = None,
    ) -> NarrativeProjector:
        base = Path(path)

        def read(name: str) -> dict[str, Any]:
            with (base / name).open("r", encoding="utf-8") as handle:
                return json.load(handle)

        scenario = read("scenario.json")
        registry = MemoryRegistry.from_data(read("runtime.registry.json"))
        labels = {item["id"]: item["label"] for item in scenario.get("entities", [])
                  if isinstance(item, dict) and type(item.get("id")) is str
                  and type(item.get("label")) is str and item["label"]}
        labels_zh = {item["id"]: item["label_zh"] for item in scenario.get("entities", [])
                     if isinstance(item, dict) and type(item.get("id")) is str
                     and type(item.get("label_zh")) is str and item["label_zh"].strip()}
        from ..presentations import presentation_selection

        selected, _ = presentation_selection(base, plugins)
        presentations = {item["positionField"] for item in scenario.get("presentation", []) if item["frame"] == "enu"}
        for plugin in selected:
            poses, _ = resolve_poses({"format": plugin.data["format"], "poses": plugin.data.get("poses", ())}, registry)
            presentations.update(pose["positionField"] for pose in poses if pose["frame"] == "enu")
        position_fields = set()
        for field in registry.fields:
            unit, frame = field.metadata.get("unit"), field.metadata.get("frame")
            unit = unit.get("symbol") if isinstance(unit, Mapping) else unit
            frame = frame.get("convention") if isinstance(frame, Mapping) else frame
            if ((field.id in presentations or "position" in field.id) and unit == "m" and frame == "enu"
                    and field.metadata.get("role") != "config"):
                position_fields.add(field.id)
        projector = cls(None, position_fields, labels, labels_zh, plugins=selected)
        # Validate only activated schemas against the pinned registry. A pack's
        # absent optional schemas do not make an unrelated domain inadmissible.
        admit_presentation_plugins(projector._plugins, registry, document=scenario)
        behaviour = read("behaviour.ir.json") if (base / "behaviour.ir.json").exists() else {}
        packages = behaviour.get("packages", [])
        if not isinstance(packages, list):
            raise ValueError("behaviour.ir.json: packages must be a list")
        declarations: dict[str, Any] = {}
        for package in packages:
            document = package.get("document", {}) if isinstance(package, dict) else {}
            if "event_narratives" in document:
                validate_declarations(document["event_narratives"])
                _merge(declarations, document["event_narratives"])
        if declarations:
            projector._override(declarations)
        if "event_narratives" in scenario:
            projector._override(scenario["event_narratives"])
        projector._pin_contract()
        return projector

    def _legacy_task_context(self) -> dict[str, Any]:
        # Compatibility with the original public cursor, not domain dispatch in
        # projection. The pack normalizer remains the sole owner of task state.
        for normalizer, state in zip(self._normalizers, self._states):
            if normalizer.id == "lowalt.task-context":
                return state
        return {"task_kinds": {}, "task_info": {}}

    def cursor(self) -> dict[str, Any]:
        context = self._legacy_task_context()
        return {
            "seen": sorted(self._seen), "presentation_digest": self.contract_digest,
            "task_kinds": json_data(context["task_kinds"]),
            "task_info": json_data(context["task_info"]),
            "normalizers": json_data(self._states),
            "positions": [[*key, list(value)] for key, value in self._positions.items()],
        }

    def restore_cursor(self, cursor: dict[str, Any]) -> None:
        if not isinstance(cursor, dict) or cursor.get("presentation_digest") != self.contract_digest:
            raise ValueError("narrative cursor: presentation selection/revision changed; rebuild from committed evidence")
        states = cursor.get("normalizers")
        if not isinstance(states, list) or len(states) != len(self._normalizers):
            raise ValueError("narrative cursor: invalid normalizer states")
        self._states = json_data(states)
        context = self._legacy_task_context()
        for field in ("task_kinds", "task_info"):
            if field in cursor:
                context[field] = json_data(cursor[field])
        self._seen = set(cursor["seen"])
        self._positions.clear()
        self._by_id.clear()
        for run_id, epoch, identity, generation, value in cursor["positions"]:
            key = (run_id, epoch, identity, int(generation))
            self._positions[key] = tuple(float(member) for member in value)
            self._key_hint(key, identity)

    @classmethod
    def projection_context(
        cls, path: str | Path, cursor: dict[str, Any], *, plugins: tuple[PresentationPlugin, ...] | None = None,
    ) -> NarrativeProjector:
        projector = cls.from_directory(path, plugins=plugins)
        projector.restore_cursor(cursor)
        return projector

    def consume(self, raw_record: dict[str, Any]) -> list[dict[str, Any]]:
        return self.consume_decoded(decode_record(expand_record(raw_record)))

    def consume_decoded(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        events = []
        for ordinal, item in enumerate(record.get("items", [])):
            if not isinstance(item, dict):
                continue
            op = item.get("proposal")
            if isinstance(op, Create):
                self._key_hint(self._key(op.ref), op.ref.id)
            elif isinstance(op, Remove):
                self._positions.pop(self._key(op.ref), None)
            elif isinstance(op, FactWrite):
                for index in self._by_field.get(op.key[1], ()):
                    state = deepcopy(self._states[index])
                    result = self._normalizers[index].consume(state, freeze_data({
                        "kind": "fact", "entity": op.key[0].to_data(), "field": op.key[1],
                        "value": json_data(thaw(op.value)),
                        "evidence": {"journal_index": record.get("index"), "item_ordinal": ordinal},
                    }))
                    if result is not None:
                        raise ValueError("presentation fact normalizer must return None")
                    self._states[index] = json_data(state)
                self._track_position(op)
            elif isinstance(op, RetractFact) and op.key[1] in self._position_fields:
                self._positions.pop(self._key(op.key[0]), None)
            message = item.get("message")
            if not isinstance(op, Emit) or not isinstance(message, Message) or message.kind != "event" or message.id in self._seen:
                continue
            event = self._narrate(message)
            self._seen.add(message.id)  # Failed interpolation remains an error on retry.
            if event is not None:
                events.append(event)
        return events

    @staticmethod
    def _key(ref: Any) -> tuple[str, str, str, int]:
        return (ref.run_id, ref.epoch, ref.id, ref.generation)

    def _key_hint(self, key: tuple[str, str, str, int], identity: str) -> None:
        keys = self._by_id.setdefault(identity, [])
        if key not in keys:
            keys.append(key)

    def _track_position(self, op: FactWrite) -> None:
        if op.key[1] not in self._position_fields:
            return
        value = op.value
        vector = value if _is_enu(value) else value.get("position") if isinstance(value, Mapping) else None
        if not _is_enu(vector):
            return
        key = self._key(op.key[0])
        self._positions[key] = tuple(float(member) for member in vector)
        self._key_hint(key, op.key[0].id)

    def _stored_position(self, value: Any) -> tuple[float, ...] | None:
        if isinstance(value, Mapping) and "$ref" in value:
            ref = value["$ref"]
            return self._positions.get((ref["run_id"], ref["epoch"], ref["id"], ref["generation"]))
        identity = _entity_id(value)
        if identity is None:
            return None
        for key in reversed(self._by_id.get(identity, [])):
            if key in self._positions:
                return self._positions[key]
        return None

    def _narrate(self, message: Message) -> dict[str, Any] | None:
        declaration = self._declarations.get(message.schema_id)
        if declaration is None or declaration.get("hidden"):
            return None  # Uninterpreted events remain available in the raw feed.
        payload = thaw(message.payload)
        if not isinstance(payload, dict):
            raise ValueError(f"narratives.{message.schema_id}: payload mapping required")
        index = self._by_schema.get(message.schema_id)
        state = None
        if index is not None:
            state = deepcopy(self._states[index])
            normalized = json_data(self._normalizers[index].consume(state, freeze_data({
                "kind": "event", "schema": message.schema_id, "payload": payload,
                "evidence": {"message_id": message.id, "at_ns": str(message.at.ns)},
            })))
            if normalized is None:
                self._states[index] = json_data(state)
                return None  # Reduction retained the evidence but emitted no view.
            if not isinstance(normalized, dict) or set(normalized) - {"payload", "variant"} or not isinstance(normalized.get("payload"), dict):
                raise ValueError("presentation normalizer: {payload, variant?} DATA required")
            payload = normalized["payload"]
            if "variant" in normalized:
                variant = normalized["variant"]
                recipe = self._provider_declarations[message.schema_id]
                variants = recipe.get("variants", {})
                if type(variant) is not str or variant not in variants:
                    raise ValueError("presentation normalizer: undeclared variant")
                if message.schema_id not in self._overrides or variant in recipe.get("legacy_variants", ()):
                    declaration = {**declaration, **variants[variant]}
        category = declaration["category"]
        if category == "mixed" and "category_field" in declaration:
            value = payload.get(declaration["category_field"])
            if value is not None:
                category = declaration.get("category_map", {}).get(_enum_key(value, f"narratives.{message.schema_id}.category"), category)
        if category not in CATEGORIES:
            category = "mixed"  # Unknown styles use the generic viewer-feed/v1 category.
        texts = {}
        for language, template_key, output in (("en", "template", "text"), ("zh", "template_zh", "text_zh")):
            if template_key not in declaration:
                continue
            values = {}
            for name in placeholders(declaration[template_key], f"narratives.{message.schema_id}.{template_key}"):
                selector = declaration.get("selectors", {}).get(name)
                if selector is not None:
                    values[name] = self._select(payload, selector, language, name, message.schema_id, template_key)
                elif name in payload:
                    value = payload[name]
                    enums = self._enums.get(message.schema_id, {}).get(name)
                    if language == "zh" and type(value) is str and enums is not None:
                        values[name] = enums.get(value, {}).get("zh", value)
                    else:
                        values[name] = self._render(value, language)
                else:
                    raise ValueError(f"narratives.{message.schema_id}.{template_key}: missing placeholder {name}")
            texts[output] = declaration[template_key].format(**values)
        entities: list[str] = []
        if "entity_list" in declaration:
            links = payload.get(declaration["entity_list"], [])
            if not isinstance(links, (tuple, list)) or len(links) > 4096:
                raise ValueError("presentation: bounded entity list required")
        else:
            links = [self._bound(payload, declaration, name) for name in declaration.get("entities", [])]
        for value in links:
            identity = _entity_id(value)
            if identity is not None and identity not in entities:
                entities.append(identity)
        event = {"message_id": message.id, "schema": message.schema_id, "at_ns": str(message.at.ns),
                 "category": category, "severity": declaration["severity"], **texts, "entities": entities}
        position = self._position(payload, declaration, entities)
        if position is not None:
            event["position"] = list(position)
        if index is not None:
            self._states[index] = json_data(state)
        return event

    @staticmethod
    def _bound(payload: Mapping[str, Any], declaration: Mapping[str, Any], name: str) -> Any:
        if declaration.get("_legacy_bindings"):
            return payload.get(name, _MISSING)
        selector = declaration.get("selectors", {}).get(name, {"path": [name]})
        return _at(payload, selector["path"])

    def _select(self, payload: Any, selector: Mapping[str, Any], language: str,
                name: str, schema: str, template_key: str) -> str:
        if selector.get("prefer_payload") and name in payload:
            return self._render(payload[name], language)
        value = _at(payload, selector["path"])
        if value is _MISSING or value == "":
            if "fallback" in selector:
                return selector["fallback"][language]
            if value is _MISSING:
                raise ValueError(f"narratives.{schema}.{template_key}: missing placeholder {name}")
        if "join" in selector:
            if not isinstance(value, (tuple, list)) or len(value) > 4096:
                raise ValueError(f"narratives.{schema}.{name}: bounded array required")
            join = selector["join"]
            template = join["template_zh" if language == "zh" else "template"]
            return join["separator"][language].join(template.format(**{
                field: self._select(row, child, language, field, schema, template_key)
                for field, child in join["selectors"].items()
            }) for row in value)
        if "enum" in selector:
            key = _enum_key(value, f"narratives.{schema}.{name}")
            if key in selector["enum"]:
                return selector["enum"][key][language]
            if "fallback" in selector:
                return selector["fallback"][language]
            if type(value) is str and not selector.get("label"):
                return value  # Unknown enum tokens are not inferred entity identities.
        if selector.get("label"):
            identity = _entity_id(value)
            if identity is not None:
                return self.label(identity, language)
            if value != "":
                raise ValueError(f"narratives.{schema}.{name}: identity required")
        return self._render(value, language)

    def label(self, identity: str, language: str = "en") -> str:
        label = self._labels.get(identity, identity)
        if language == "zh" and identity in self._labels_zh:
            return self._labels_zh[identity]
        return presentation_label(label, language, {
            "labels": self._vocabulary, "numbered_labels": self._numbered,
            "label_groups": self._label_groups,
        })

    def _render(self, value: Any, language: str = "en") -> str:
        if type(value) is str:
            return self.label(value, language) if value in self._labels or language == "zh" and value in self._labels_zh else value
        identity = _entity_id(value)
        if identity is not None:
            return self.label(identity, language)
        if isinstance(value, (list, tuple)):
            parts = [self._render(item, language) for item in value]
            separator = "、" if language == "zh" else ", "
            if len(parts) > 3:
                extra = f"，另有 {len(parts) - 3} 项" if language == "zh" else f" and {len(parts) - 3} more"
                return separator.join(parts[:3]) + extra
            return separator.join(parts) if parts else ("无" if language == "zh" else "none")
        if isinstance(value, Mapping):
            return "详情" if language == "zh" else "details"
        if value is None:
            return "未知" if language == "zh" else "unknown"
        return _plain(value)

    def _position(self, payload: dict[str, Any], declaration: dict[str, Any], entities: list[str]) -> tuple[float, ...] | None:
        field = declaration.get("position")
        value = self._bound(payload, declaration, field) if field is not None else _MISSING
        if value is not _MISSING:
            return tuple(float(member) for member in value) if _is_enu(value) else self._stored_position(value)
        for name in declaration.get("entities", []):
            stored = self._stored_position(self._bound(payload, declaration, name))
            if stored is not None:
                return stored
        return None


def _plain(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        rounded = round(value, 1)
        return str(int(rounded)) if rounded.is_integer() else str(rounded)
    return str(value)
