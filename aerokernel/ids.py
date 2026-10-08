"""Pinned entity namespaces, generation identity and causal item coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

from .errors import KernelError
from .values import canonical_json, text


def validate_text(value: str) -> str:
    """Require a nonempty UTF-8 identifier, preserving exact spelling."""
    if type(value) is not str or not value:
        raise KernelError("IDENTIFIER", "nonempty string required")
    return text(value)


def _nonnegative(value: int) -> None:
    if type(value) is not int or value < 0:
        raise KernelError("IDENTITY_INTEGER", "nonnegative integer required")


@dataclass(frozen=True)
class EntityRef:
    """Identity is scoped by run/epoch/id and cross-type generation allocation."""

    run_id: str
    epoch: str
    id: str
    generation: int
    type_id: str

    def __post_init__(self) -> None:
        for value in (self.run_id, self.epoch, self.id, self.type_id):
            validate_text(value)
        _nonnegative(self.generation)

    def to_data(self) -> dict[str, Any]:
        """Untagged identity fields; field schemas wrap them in $ref."""
        return {
            "run_id": self.run_id,
            "epoch": self.epoch,
            "id": self.id,
            "generation": self.generation,
            "type_id": self.type_id,
        }

    @classmethod
    def from_data(cls, data: Any) -> EntityRef:
        """Decode exact identity fields, rejecting missing/extra members."""
        if not isinstance(data, dict) or set(data) != {
            "run_id",
            "epoch",
            "id",
            "generation",
            "type_id",
        }:
            raise KernelError("REF_DATA", "unexpected identity fields")
        return cls(**data)


FieldKey: TypeAlias = tuple[EntityRef, str]


@dataclass(frozen=True)
class ItemRef:
    """A record item coordinate, validated against an invocation's visible prefix."""

    record_index: int
    item_index: int

    def __post_init__(self) -> None:
        _nonnegative(self.record_index)
        _nonnegative(self.item_index)


@dataclass(frozen=True)
class LocalCause:
    """Earlier operation index, scoped only to one originating engine batch."""

    index: int

    def __post_init__(self) -> None:
        _nonnegative(self.index)


def message_id(
    run_id: str, epoch: str, source_kind: str, source_id: str, sequence: int
) -> str:
    """Canonical, run-scoped source-local message identifier."""
    for value in (run_id, epoch, source_id):
        validate_text(value)
    if source_kind not in {"partition", "ingress", "kernel"}:
        raise KernelError("MESSAGE_SOURCE", "unknown source namespace")
    _nonnegative(sequence)
    return canonical_json(
        ["aerokernel.message/v1", run_id, epoch, source_kind, source_id, sequence]
    )[:-1].decode("utf-8")
