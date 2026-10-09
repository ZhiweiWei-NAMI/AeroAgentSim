"""Reverse selectors for discovery and concrete subjects for chain dispatch."""

from __future__ import annotations

from typing import Any, cast

from aerokernel import EntityRef, MemoryRegistry
from aerokernel.sdk import EngineContext
from aerokernel.values import canonical_json, thaw

Key = tuple[str, str]


class SelectorIndex:
    """Only changes capable of changing a join invalidate its retained tuples."""

    def __init__(self, registry: MemoryRegistry) -> None:
        self.registry = registry
        self.types: dict[str, set[Key]] = {}
        self.fields: dict[str, dict[str, set[Key]]] = {}
        self.relations: dict[str, set[Key]] = {}
        self.keys: set[Key] = set()

    def register(
        self,
        key: Key,
        roles: dict[str, str],
        match: dict[str, Any],
        reads: list[tuple[str, str]],
        relations: set[str],
    ) -> None:
        self.keys.add(key)
        for type_id in roles.values():
            self.types.setdefault(type_id, set()).add(key)
        for role, selector in match.items():
            if "field" in selector:
                reads.append((role, selector["field"]))
            if "relation" in selector:
                relations.add(selector["relation"])
        for role, field_id in reads:
            self.fields.setdefault(field_id, {}).setdefault(roles[role], set()).add(key)
        for relation in relations:
            self.relations.setdefault(relation, set()).add(key)

    def affected(self, ctx: EngineContext) -> set[Key]:
        result: set[Key] = set()
        for dirty in ctx.dirty:
            if dirty.key is not None:
                ref, field_id = dirty.key
                for type_id, keys in self.fields.get(field_id, {}).items():
                    if self.registry.is_a(ref.type_id, type_id):
                        result.update(keys)
            elif dirty.kind == "lifecycle":
                ref = EntityRef.from_data(
                    cast(dict[str, Any], thaw(dirty.payload))["$ref"]
                )
                for type_id, keys in self.types.items():
                    if self.registry.is_a(ref.type_id, type_id):
                        result.update(keys)
            elif dirty.kind == "relation":
                relation = cast(dict[str, Any], thaw(dirty.payload))["relation_id"]
                result.update(self.relations.get(relation, ()))
        return result


class InstanceIndex:
    """Subjects are exact generations; event schemas and sampled pools are pinned."""

    def __init__(self) -> None:
        self.fields: dict[tuple[EntityRef, str], set[str]] = {}
        self.entities: dict[EntityRef, set[str]] = {}
        self.relations: dict[str, set[str]] = {}
        self.events: dict[str, set[str]] = {}
        self.event_subjects: dict[tuple[str, str, bytes], set[str]] = {}
        self.event_fields: dict[str, set[str]] = {}
        self.samples: dict[str, set[str]] = {}
        self.members: dict[str, list[tuple[str, Any]]] = {}

    def replace(self, identity: str, keys: dict[str, list[Any]]) -> None:
        """State changes replace memberships; unrelated commits never rebuild them."""
        for category, key in self.members.pop(identity, []):
            index = cast(dict[Any, set[str]], getattr(self, category))
            index[key].discard(identity)
            if not index[key]:
                del index[key]
        memberships: list[tuple[str, Any]] = []
        for category, values in keys.items():
            index = cast(dict[Any, set[str]], getattr(self, category))
            for key in set(values):
                index.setdefault(key, set()).add(identity)
                memberships.append((category, key))
                if category == "event_subjects":
                    self.event_fields.setdefault(key[0], set()).add(key[1])
        self.members[identity] = memberships

    def event_instances(self, schema: str, payload: dict[str, Any]) -> set[str]:
        result = set(self.events.get(schema, ()))
        for field in self.event_fields.get(schema, ()):
            if field in payload:
                result.update(
                    self.event_subjects.get(
                        (schema, field, canonical_json(payload[field])), ()
                    )
                )
        return result

    def affected(self, ctx: EngineContext) -> set[str]:
        result: set[str] = set()
        for dirty in ctx.dirty:
            if dirty.key is not None:
                result.update(self.fields.get(dirty.key, ()))
            elif dirty.kind == "lifecycle":
                ref = EntityRef.from_data(
                    cast(dict[str, Any], thaw(dirty.payload))["$ref"]
                )
                result.update(self.entities.get(ref, ()))
            elif dirty.kind == "relation":
                relation = cast(dict[str, Any], thaw(dirty.payload))["relation_id"]
                result.update(self.relations.get(relation, ()))
        return result
