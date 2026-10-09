"""Compiler policy and immutable, portable compilation results."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from aerokernel.registry import FieldDescriptor, MemoryRegistry
from aerokernel.values import canonical_json, freeze, parse_json, thaw

FORMAT = "aeroagentsim.aerograph-registry/v1"
COMPILER_VERSION = "1"


class CompileError(ValueError):
    """Actionable source diagnostics; no partial registry is returned."""

    def __init__(self, diagnostics: Iterable[str]):
        self.diagnostics = tuple(diagnostics)
        super().__init__("Registry compilation failed:\n" + "\n".join(self.diagnostics))


@dataclass(frozen=True)
class Selection:
    """Explicit types, with optional global field/relation ID allow-lists."""

    type_ids: tuple[str, ...]
    field_ids: tuple[str, ...] | None = None
    relation_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for name in ("type_ids", "field_ids", "relation_ids"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, str):
                    raise TypeError(
                        f"{name} requires an iterable of full IDs, not a string"
                    )
                if any(not isinstance(x, str) or not x for x in value):
                    raise ValueError(f"{name} requires nonempty string IDs")
                object.__setattr__(self, name, tuple(sorted(set(value))))
        if not self.type_ids:
            raise ValueError("Select at least one explicit type ID")

    def to_data(self) -> dict[str, Any]:
        return {
            name: None if (v := getattr(self, name)) is None else list(v)
            for name in ("type_ids", "field_ids", "relation_ids")
        }


@dataclass(frozen=True)
class Policy:
    """Compile admission policy; it never changes source review state.

    ``admit_proposed`` admits ``proposal:`` namespace candidates. It is
    distinct from the default review admission of definitions whose
    ``reviewStatus`` is ``reviewed`` or ``proposed`` (or undeclared).
    ``strict_reviewed`` narrows admission to ``reviewed`` definitions only.
    The two admission switches are mutually exclusive.
    """

    admit_proposed: bool = False
    strict_reviewed: bool = False
    admitted_ids: tuple[str, ...] = ()
    excluded_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.admit_proposed) is not bool:
            raise ValueError("admit_proposed must be bool")
        if type(self.strict_reviewed) is not bool:
            raise ValueError("strict_reviewed must be bool")
        if self.admit_proposed and self.strict_reviewed:
            raise ValueError(
                "admit_proposed and strict_reviewed are mutually exclusive "
                "admission switches"
            )
        for name in ("admitted_ids", "excluded_ids"):
            value = getattr(self, name)
            if isinstance(value, str):
                raise TypeError(
                    f"{name} requires an iterable of full IDs, not a string"
                )
            if any(not isinstance(x, str) or not x for x in value):
                raise ValueError(f"{name} requires nonempty string IDs")
            object.__setattr__(self, name, tuple(sorted(set(value))))
        if set(self.admitted_ids) & set(self.excluded_ids):
            raise ValueError("An ID cannot be both admitted and excluded")

    def to_data(self) -> dict[str, Any]:
        return {
            "admit_proposed": self.admit_proposed,
            "strict_reviewed": self.strict_reviewed,
            "admitted_ids": list(self.admitted_ids),
            "excluded_ids": list(self.excluded_ids),
        }


@dataclass(frozen=True)
class CompiledRegistry:
    """Kernel descriptors plus relation, source locations and binding hints.

    Mappings/arrays are deeply frozen, and exports are detached.
    """

    registry: MemoryRegistry
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        details = dict(self.details)
        details.pop("normalization_digest", None)
        if "provenance" in details:
            provenance = dict(details["provenance"])
            provenance.pop("input_sha256", None)
            provenance.pop("git", None)
            details["provenance"] = provenance
        object.__setattr__(self, "details", freeze(details))

    def payload(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "compiler_version": COMPILER_VERSION,
            "registry": self.registry.to_data(),
            "details": thaw(self.details),
        }

    def to_data(self) -> dict[str, Any]:
        return self.payload()

    @property
    def relations(self) -> tuple[Mapping[str, Any], ...]:
        return cast(tuple[Mapping[str, Any], ...], self.details["relations"])

    @property
    def provenance(self) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], self.details["provenance"])

    @property
    def normalizations(self) -> tuple[Mapping[str, Any], ...]:
        return cast(tuple[Mapping[str, Any], ...], self.details["normalizations"])

    @property
    def exclusions(self) -> tuple[Mapping[str, Any], ...]:
        return cast(tuple[Mapping[str, Any], ...], self.details["exclusions"])

    @property
    def producer_hints(self) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], self.details["producer_hints"])

    @property
    def statistics(self) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], self.details["statistics"])

    def effective_fields(self, type_id: str) -> tuple[FieldDescriptor, ...]:
        if type_id not in self.details["effective_fields"]:
            raise KeyError(f"Type {type_id!r} was not selected for effective fields")
        return tuple(
            self.registry.field(fid)
            for fid in self.details["effective_fields"][type_id]
        )

    def effective_relations(self, type_id: str) -> tuple[Mapping[str, Any], ...]:
        self.effective_fields(type_id)
        return tuple(
            r
            for r in self.relations
            if self.registry.is_a(type_id, r["source_type"])
            or self.registry.is_a(type_id, r["target_type"])
        )

    def write_snapshot(self, path: Path | str) -> None:
        Path(path).write_bytes(canonical_json(self.to_data()))


def write_snapshot(compiled: CompiledRegistry, path: Path | str) -> None:
    """Write sorted canonical JSON."""
    compiled.write_snapshot(path)


def read_snapshot(path: Path | str) -> CompiledRegistry:
    """Load snapshot JSON; never consult the live ontology.

    Legacy snapshots carrying an artifact ``digest`` or a plain
    ``snapshot_id`` are accepted for compatibility; neither is verified and
    neither is retained.
    """
    data = parse_json(Path(path).read_bytes())
    if (
        not isinstance(data, dict)
        or not {"format", "compiler_version", "registry", "details"} <= set(data)
        or set(data)
        - {"format", "compiler_version", "registry", "details", "digest", "snapshot_id"}
        or data["format"] != FORMAT
        or data["compiler_version"] != COMPILER_VERSION
    ):
        raise ValueError("Unsupported registry snapshot format/version")
    if not isinstance(data["registry"], dict) or not isinstance(data["details"], dict):
        raise TypeError("Snapshot registry and details must be records")
    return CompiledRegistry(MemoryRegistry.from_data(data["registry"]), data["details"])
