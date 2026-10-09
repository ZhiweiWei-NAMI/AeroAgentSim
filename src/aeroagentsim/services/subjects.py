"""Resolve declared message subjects against recorded lifecycle identities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.ids import EntityRef
from aerokernel.messages import Message
from aerokernel.operations import Create, Remove
from aerokernel.registry import MemoryRegistry
from aerokernel.values import thaw

from aeroagentsim.scenario.subjects import declarations, payload_values

from .storage import RunStorage


class SubjectProjection:
    """String bindings use the live generation at the message's committed cut.

    A generation_path can explicitly identify an older recorded generation.
    No subject identity is invented from an unknown string or a default zero.
    """

    def __init__(self, registry: MemoryRegistry, document: dict[str, Any]) -> None:
        self.registry = registry
        self.bindings = declarations(registry, document)
        self.current: dict[str, EntityRef] = {}
        self.known: dict[tuple[str, int], EntityRef] = {}

    def begin(self, record: dict[str, Any]) -> None:
        for item in record["items"]:
            if "proposal" in item:
                operation = decode_record(item["proposal"])
                if isinstance(operation, Create):
                    ref = operation.ref
                    self.current[ref.id] = ref
                    self.known[(ref.id, ref.generation)] = ref

    def end(self, record: dict[str, Any]) -> None:
        for item in record["items"]:
            if "proposal" in item:
                operation = decode_record(item["proposal"])
                if (
                    isinstance(operation, Remove)
                    and self.current.get(operation.ref.id) == operation.ref
                ):
                    del self.current[operation.ref.id]

    def observe(self, record: dict[str, Any]) -> None:
        record = expand_record(record)
        self.begin(record)
        self.end(record)

    def subjects(self, message: Message) -> list[EntityRef]:
        result = []
        payload = thaw(message.payload)
        schema = self.registry.message(message.schema_id).schema
        for binding in self.bindings.get(message.schema_id, ()):
            try:
                values = payload_values(self.registry, schema, payload, binding.path)
                generations = (
                    None
                    if binding.generation_path is None
                    else payload_values(
                        self.registry, schema, payload, binding.generation_path
                    )
                )
                if generations is not None and len(generations) != len(values):
                    raise ValueError("subject and generation must both be present")
                for index, value in enumerate(values):
                    if isinstance(value, dict) and set(value) == {"$ref"}:
                        ref = EntityRef.from_data(value["$ref"])
                    elif type(value) is str:
                        ref = (
                            self.current[value]
                            if generations is None
                            else self.known[(value, generations[index])]
                        )
                    else:
                        raise ValueError(
                            "declared subject must be an entity id or typed ref"
                        )
                    if not self.registry.is_a(ref.type_id, binding.type_id):
                        raise ValueError(
                            f"{ref.id}: expected entity type {binding.type_id}"
                        )
                    result.append(ref)
            except (IndexError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"messages.{message.schema_id}.subjects.{list(binding.path)}: {exc}"
                ) from exc
        return result


def projection_context(directory: Path, before_index: int) -> SubjectProjection:
    """Prime REST paging/SSE reconnects from the retained lifecycle prefix."""
    registry = MemoryRegistry.from_data(
        json.loads((directory / "runtime.registry.json").read_text())
    )
    document = json.loads((directory / "scenario.json").read_text())
    context = SubjectProjection(registry, document)
    if not context.bindings:
        return context
    storage = RunStorage(directory)
    cursor = 1
    while cursor < before_index:
        records = storage.records(cursor, min(4096, before_index - cursor))
        if not records:
            break
        for record in records:
            context.observe(record)
        cursor = records[-1]["index"] + 1
    return context
