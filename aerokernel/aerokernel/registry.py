"""Immutable normalized descriptors and a closed portable schema validator."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from .errors import KernelError
from .ids import EntityRef, validate_text
from .relations import RelationDescriptor
from .values import ResourceBudget, canonical_json, freeze, normalize, thaw, typed_equal


@dataclass(frozen=True)
class TypeDescriptor:
    """Explicit native DAG inheritance; browsing taxonomy has no effect."""

    id: str
    parents: tuple[str, ...] = ()
    abstract: bool = False

    def __post_init__(self) -> None:
        validate_text(self.id)
        object.__setattr__(self, "parents", tuple(self.parents))
        if type(self.abstract) is not bool or len(set(self.parents)) != len(
            self.parents
        ):
            raise KernelError(
                "TYPE_DESCRIPTOR", "invalid abstract flag/duplicate parents"
            )
        for parent in self.parents:
            validate_text(parent)


@dataclass(frozen=True)
class FieldDescriptor:
    """Typed field with opaque unit, frame, role and time metadata."""

    id: str
    declaring_type: str
    schema: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_text(self.id)
        validate_text(self.declaring_type)
        object.__setattr__(self, "schema", freeze(dict(self.schema)))
        object.__setattr__(self, "metadata", freeze(dict(self.metadata)))


@dataclass(frozen=True)
class MessageDescriptor:
    """Portable command/event payload and optional result/feedback contracts."""

    id: str
    kind: str = "event"
    schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "null"})
    result_schema: Mapping[str, Any] | None = None
    feedback_schema: Mapping[str, Any] | None = None
    cancel_support: bool = False

    def __post_init__(self) -> None:
        validate_text(self.id)
        if (
            self.kind not in {"command", "event"}
            or type(self.cancel_support) is not bool
        ):
            raise KernelError("MESSAGE_DESCRIPTOR", "invalid kind/cancel flag")
        if self.kind == "event" and (
            self.result_schema is not None
            or self.feedback_schema is not None
            or self.cancel_support
        ):
            raise KernelError("MESSAGE_DESCRIPTOR", "event has action-only contracts")
        for name in ("schema", "result_schema", "feedback_schema"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, freeze(dict(value)))


class Registry(Protocol):
    """Normalized portable registry, requiring no ontology compiler at replay."""

    revision: str
    digest: str

    def is_a(self, child: str, parent: str) -> bool:
        """Reflexive/transitive membership of known native types."""
        ...

    def field(self, id: str) -> FieldDescriptor:
        """Resolve a known selected field descriptor."""
        ...

    def message(self, id: str) -> MessageDescriptor:
        """Resolve a known selected message descriptor."""
        ...

    def validate(
        self,
        schema: Mapping[str, Any] | str,
        value: object,
        reference_validator: Callable[[EntityRef], None] | None = None,
        *,
        budget: ResourceBudget | None = None,
    ) -> None:
        """Validate without coercing values or executing application code."""
        ...

    def to_data(self) -> dict[str, Any]:
        """Export the complete normalized portable descriptor collection."""
        ...


class MemoryRegistry:
    """Immutable selected descriptor collection and schema compiler."""

    def __init__(
        self,
        types: tuple[TypeDescriptor, ...],
        fields: tuple[FieldDescriptor, ...] = (),
        messages: tuple[MessageDescriptor, ...] = (),
        schemas: dict[str, dict[str, Any]] | None = None,
        revision: str = "1",
        *,
        relations: tuple[RelationDescriptor, ...] = (),
    ) -> None:
        self.revision = validate_text(revision)
        self.types = tuple(sorted(types, key=lambda d: d.id))
        self.fields = tuple(sorted(fields, key=lambda d: d.id))
        self.messages = tuple(sorted(messages, key=lambda d: d.id))
        self.relations = tuple(sorted(relations, key=lambda d: d.id))
        self._relations = self._index(self.relations)
        self._types = self._index(self.types)
        self._fields = self._index(self.fields)
        self._messages = self._index(self.messages)
        raw_schemas = {} if schemas is None else schemas
        self.schemas: Mapping[str, Any] = MappingProxyType(
            {validate_text(k): freeze(v) for k, v in raw_schemas.items()}
        )
        self._ancestors: dict[str, frozenset[str]] = {}

        def ancestry(id: str, path: tuple[str, ...]) -> frozenset[str]:
            if id not in self._types:
                raise KernelError("TYPE_UNKNOWN", "unknown parent type")
            if id in path:
                raise KernelError(
                    "TYPE_CYCLE", "inheritance must be a DAG", path=path + (id,)
                )
            if id not in self._ancestors:
                result = {id}
                for parent in self._types[id].parents:
                    result.update(ancestry(parent, path + (id,)))
                self._ancestors[id] = frozenset(result)
            return self._ancestors[id]

        for type_descriptor in self.types:
            ancestry(type_descriptor.id, ())
        for relation in self.relations:
            self.is_a(relation.source_type, relation.source_type)
            self.is_a(relation.target_type, relation.target_type)
        for field_descriptor in self.fields:
            self.is_a(field_descriptor.declaring_type, field_descriptor.declaring_type)
            self._compile(field_descriptor.schema, ())
        for message_descriptor in self.messages:
            for node in (
                message_descriptor.schema,
                message_descriptor.result_schema,
                message_descriptor.feedback_schema,
            ):
                if node is not None:
                    self._compile(node, ())
        for node in self.schemas.values():
            self._compile(node, ())
        self.digest = hashlib.sha256(canonical_json(self.to_data())[:-1]).hexdigest()
        self._sealed = True

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("registry is immutable")
        object.__setattr__(self, name, value)

    @staticmethod
    def _index(descriptors: tuple[Any, ...]) -> Mapping[str, Any]:
        values = {d.id: d for d in descriptors}
        if len(values) != len(descriptors):
            raise KernelError("DESCRIPTOR_DUPLICATE", "duplicate descriptor ID")
        return MappingProxyType(values)

    def is_a(self, child: str, parent: str) -> bool:
        """Unknown membership never becomes false; only explicit inheritance counts."""
        if child not in self._ancestors or parent not in self._ancestors:
            raise KernelError("TYPE_UNKNOWN", "unknown membership type")
        return parent in self._ancestors[child]

    def field(self, id: str) -> FieldDescriptor:
        """Resolve an active field definition."""
        if id not in self._fields:
            raise KernelError("FIELD_UNKNOWN", "unknown field descriptor")
        descriptor: FieldDescriptor = self._fields[id]
        return descriptor

    def message(self, id: str) -> MessageDescriptor:
        """Resolve an active message definition."""
        if id not in self._messages:
            raise KernelError("MESSAGE_UNKNOWN", "unknown message descriptor")
        descriptor: MessageDescriptor = self._messages[id]
        return descriptor

    def relation(self, id: str) -> RelationDescriptor:
        """Resolve an explicitly normalized directional relation descriptor."""
        if id not in self._relations:
            raise KernelError("RELATION_UNKNOWN", "unknown relation descriptor")
        descriptor: RelationDescriptor = self._relations[id]
        return descriptor

    def _resolve(self, schema: Mapping[str, Any] | str) -> Mapping[str, Any]:
        seen: set[str] = set()
        while isinstance(schema, str) or "schema_ref" in schema:
            if not isinstance(schema, str):
                if set(schema) != {"schema_ref"}:
                    raise KernelError(
                        "SCHEMA_CONSTRAINT", "schema_ref cannot hide extra constraints"
                    )
                name = schema["schema_ref"]
            else:
                name = schema
            if name in seen:
                raise KernelError("SCHEMA_CYCLE", "recursive named schema")
            if name not in self.schemas:
                raise KernelError("SCHEMA_UNKNOWN", "unknown referenced schema")
            seen.add(name)
            schema = self.schemas[name]
        return schema

    def _compile(self, schema: Mapping[str, Any], path: tuple[str, ...]) -> None:
        if not isinstance(schema, Mapping):
            raise KernelError("SCHEMA_NODE", "schema must be a record")
        if "schema_ref" in schema:
            name = schema["schema_ref"]
            if name in path:
                raise KernelError(
                    "SCHEMA_CYCLE", "recursive schemas are outside portable subset"
                )
            self._compile(self._resolve(schema), path + (name,))
            return
        kind = schema.get("type")
        allowed = {
            "null": set(),
            "boolean": set(),
            "integer": {"minimum", "maximum"},
            "number": {"minimum", "maximum"},
            "string": {"min_length", "max_length"},
            "ref": {"target_type"},
            "array": {"items", "min_length", "max_length"},
            "vector": {"items", "length"},
            "matrix": {"items", "rows", "columns"},
            "record": {"members", "required", "extra"},
            "union": {"discriminator", "cases"},
        }
        if kind not in allowed or set(schema) - (
            allowed[kind] | {"type", "nullable", "enum"}
        ):
            raise KernelError("SCHEMA_CONSTRAINT", "unsupported active constraint")
        if "nullable" in schema and type(schema["nullable"]) is not bool:
            raise KernelError("SCHEMA_NULLABLE", "nullable must be explicit bool")
        for lower, upper in (("minimum", "maximum"), ("min_length", "max_length")):
            for key in (lower, upper):
                if key in schema:
                    value = schema[key]
                    if kind in {"array", "string"}:
                        if type(value) is not int or value < 0:
                            raise KernelError(
                                "SCHEMA_BOUND", "nonnegative integer length required"
                            )
                    elif type(value) not in (int, float):
                        raise KernelError("SCHEMA_BOUND", "numeric bound required")
            if lower in schema and upper in schema and schema[lower] > schema[upper]:
                raise KernelError("SCHEMA_BOUND", "inverted bounds")
        if "enum" in schema:
            values = schema["enum"]
            if not isinstance(values, (list, tuple)) or not values:
                raise KernelError("SCHEMA_ENUM", "explicit nonempty enum required")
            for i, value in enumerate(values):
                if any(typed_equal(value, other) for other in values[:i]):
                    raise KernelError("SCHEMA_ENUM", "duplicate typed enum value")
                plain = {k: v for k, v in schema.items() if k != "enum"}
                self._validate(plain, thaw(value), None)
        if kind == "ref" and "target_type" in schema:
            self.is_a(schema["target_type"], schema["target_type"])
        if kind in {"array", "vector", "matrix"}:
            if "items" not in schema:
                raise KernelError("SCHEMA_ITEMS", "explicit element schema required")
            self._compile(schema["items"], path)
            for dimension in (
                ("length",)
                if kind == "vector"
                else ("rows", "columns")
                if kind == "matrix"
                else ()
            ):
                if (
                    dimension not in schema
                    or type(schema[dimension]) is not int
                    or schema[dimension] < 0
                ):
                    raise KernelError(
                        "SCHEMA_DIMENSION", "explicit nonnegative dimension required"
                    )
        if kind == "record":
            if not {"members", "required", "extra"} <= set(schema):
                raise KernelError(
                    "SCHEMA_RECORD", "members/required/extra must be explicit"
                )
            members, required = schema["members"], schema["required"]
            if (
                not isinstance(members, Mapping)
                or not isinstance(required, (tuple, list))
                or type(schema["extra"]) is not bool
                or len(required) != len(set(required))
                or not set(required) <= set(members)
            ):
                raise KernelError("SCHEMA_RECORD", "invalid record contract")
            for name, child in members.items():
                validate_text(name)
                self._compile(child, path)
        if kind == "union":
            if (
                schema.get("discriminator") != "$case"
                or not isinstance(schema.get("cases"), Mapping)
                or not schema["cases"]
            ):
                raise KernelError(
                    "SCHEMA_UNION", "explicit $case discriminator and branches required"
                )
            for name, child in schema["cases"].items():
                validate_text(name)
                self._compile(child, path)

    def validate(
        self,
        schema: Mapping[str, Any] | str,
        value: object,
        reference_validator: Callable[[EntityRef], None] | None = None,
        *,
        budget: ResourceBudget | None = None,
    ) -> None:
        """Validate portable values and optional identity visibility."""
        # Frozen values are accepted only through an explicit detached wire conversion.
        portable = normalize(value, budget)
        self._validate(self._resolve(schema), portable, reference_validator)

    def _validate(
        self,
        schema: Mapping[str, Any],
        value: Any,
        reference_validator: Callable[[EntityRef], None] | None,
    ) -> None:
        schema = self._resolve(schema)
        if value is None and (
            schema.get("nullable") is True or schema["type"] == "null"
        ):
            if "enum" not in schema or any(
                typed_equal(None, item) for item in schema["enum"]
            ):
                return
        kind = schema["type"]
        valid = {
            "null": value is None,
            "boolean": type(value) is bool,
            "integer": type(value) is int,
            "number": type(value) is float,
            "string": type(value) is str,
            "array": type(value) is list,
            "vector": type(value) is list,
            "matrix": type(value) is list,
            "record": type(value) is dict,
            "ref": type(value) is dict,
            "union": type(value) is dict,
        }[kind]
        if not valid:
            raise KernelError("VALUE_SCHEMA", f"expected strict {kind}")
        if "enum" in schema and not any(
            typed_equal(value, item) for item in schema["enum"]
        ):
            raise KernelError("VALUE_ENUM", "value excluded by typed enum")
        if kind in {"integer", "number"}:
            if ("minimum" in schema and value < schema["minimum"]) or (
                "maximum" in schema and value > schema["maximum"]
            ):
                raise KernelError("VALUE_BOUND", "numeric bound violated")
        if kind in {"string", "array"}:
            if ("min_length" in schema and len(value) < schema["min_length"]) or (
                "max_length" in schema and len(value) > schema["max_length"]
            ):
                raise KernelError("VALUE_LENGTH", "length bound violated")
        if kind in {"array", "vector"}:
            if kind == "vector" and len(value) != schema["length"]:
                raise KernelError("VALUE_LENGTH", "vector dimension violated")
            for item in value:
                self._validate(schema["items"], item, reference_validator)
        if kind == "matrix":
            if len(value) != schema["rows"]:
                raise KernelError("VALUE_LENGTH", "matrix row count violated")
            for row in value:
                if type(row) is not list or len(row) != schema["columns"]:
                    raise KernelError("VALUE_LENGTH", "matrix column count violated")
                for item in row:
                    self._validate(schema["items"], item, reference_validator)
        if kind == "record":
            members = schema["members"]
            if not set(schema["required"]) <= set(value) or (
                not schema["extra"] and set(value) - set(members)
            ):
                raise KernelError("VALUE_RECORD", "required/extra members violated")
            for name in set(value) & set(members):
                self._validate(members[name], value[name], reference_validator)
        if kind == "ref":
            if set(value) != {"$ref"}:
                raise KernelError("VALUE_REF", "expected exact $ref wire shape")
            ref = EntityRef.from_data(value["$ref"])
            if "target_type" in schema and not self.is_a(
                ref.type_id, schema["target_type"]
            ):
                raise KernelError("VALUE_REF_TYPE", "identity target type mismatch")
            if reference_validator is not None:
                reference_validator(ref)
        if kind == "union":
            if (
                set(value) != {"$case", "value"}
                or type(value["$case"]) is not str
                or value["$case"] not in schema["cases"]
            ):
                raise KernelError("VALUE_UNION", "expected exact known tagged branch")
            self._validate(
                schema["cases"][value["$case"]], value["value"], reference_validator
            )

    def to_data(self) -> dict[str, Any]:
        """Complete normalized selected descriptors for portable replay."""
        result = {
            "revision": self.revision,
            "types": [
                {"id": d.id, "parents": list(d.parents), "abstract": d.abstract}
                for d in self.types
            ],
            "fields": [
                {
                    "id": d.id,
                    "declaring_type": d.declaring_type,
                    "schema": thaw(d.schema),
                    "metadata": thaw(d.metadata),
                }
                for d in self.fields
            ],
            "messages": [
                {
                    "id": d.id,
                    "kind": d.kind,
                    "schema": thaw(d.schema),
                    "result_schema": None
                    if d.result_schema is None
                    else thaw(d.result_schema),
                    "feedback_schema": None
                    if d.feedback_schema is None
                    else thaw(d.feedback_schema),
                    "cancel_support": d.cancel_support,
                }
                for d in self.messages
            ],
            "schemas": {k: thaw(v) for k, v in self.schemas.items()},
        }

        if self.relations:
            result["relations"] = [r.to_data() for r in self.relations]
        return result

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> MemoryRegistry:
        """Recompile the portable descriptor subset for replay."""
        base = {"revision", "types", "fields", "messages", "schemas"}
        if set(data) not in (base, base | {"relations"}):
            raise KernelError(
                "REGISTRY_DATA", "unexpected normalized descriptor fields"
            )
        return cls(
            tuple(
                TypeDescriptor(d["id"], tuple(d["parents"]), d["abstract"])
                for d in data["types"]
            ),
            tuple(FieldDescriptor(**d) for d in data["fields"]),
            tuple(MessageDescriptor(**d) for d in data["messages"]),
            data["schemas"],
            data["revision"],
            relations=tuple(RelationDescriptor.from_data(d) for d in data["relations"])
            if "relations" in data
            else (),
        )
