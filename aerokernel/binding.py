"""Pinned lifecycle domains and per-instance-field single-writer compilation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .engine import Partition
from .errors import KernelError
from .ids import EntityRef, validate_text
from .registry import MemoryRegistry

if TYPE_CHECKING:
    from .messages import CommandRequest


@dataclass(frozen=True)
class BindingRule:
    """Explicit field application plus producer priority over an inherited ID domain."""

    partition: str
    type_id: str
    fields: tuple[str, ...]
    id_pattern: str = "*"
    priority: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))


@dataclass(frozen=True)
class LifecycleRule:
    """Separately declared create/remove controller domain."""

    partition: str
    type_id: str
    id_pattern: str = "*"
    priority: int = 0


@dataclass(frozen=True)
class ExactBinding:
    """Explicit per-generation override; declaring production alone grants nothing."""

    ref: EntityRef
    field: str
    partition: str


@dataclass(frozen=True)
class BindingManifest:
    """Scenario-selected active fields, authorities, cohorts and ingress identities."""

    run_id: str
    epoch: str
    entities: tuple[EntityRef, ...] = ()
    rules: tuple[BindingRule, ...] = ()
    exact: tuple[ExactBinding, ...] = ()
    lifecycle: tuple[LifecycleRule, ...] = ()
    cohorts: tuple[tuple[str, ...], ...] = ()
    bootstrap_commands: tuple[CommandRequest, ...] = ()
    ingress_source: str = "host"
    control_source: str = "host"
    cancel_sources: tuple[str, ...] = ()
    lifecycle_participants: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "entities",
            "rules",
            "exact",
            "lifecycle",
            "bootstrap_commands",
            "cancel_sources",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        validate_text(self.run_id)
        validate_text(self.epoch)
        validate_text(self.ingress_source)
        validate_text(self.control_source)
        object.__setattr__(
            self,
            "lifecycle_participants",
            MappingProxyType(
                {k: tuple(v) for k, v in self.lifecycle_participants.items()}
            ),
        )
        # Cohorts are connected components of declared overlaps, canonical at bind.
        groups: list[set[str]] = []
        for cohort in self.cohorts:
            if not cohort or len(set(cohort)) != len(cohort):
                raise KernelError("COHORT", "nonempty unique members required")
            merged = set(cohort)
            rest = []
            for group in groups:
                if merged & group:
                    merged |= group
                else:
                    rest.append(group)
            while any(merged & group for group in rest):
                retained = []
                for group in rest:
                    if merged & group:
                        merged |= group
                    else:
                        retained.append(group)
                rest = retained
            groups = rest + [merged]
        object.__setattr__(
            self, "cohorts", tuple(sorted(tuple(sorted(group)) for group in groups))
        )

    def _namespace(self, ref: EntityRef) -> None:
        if (ref.run_id, ref.epoch) != (self.run_id, self.epoch):
            raise KernelError("NAMESPACE", "binding references foreign namespace")

    @staticmethod
    def _matches(
        ref: EntityRef, rule: BindingRule | LifecycleRule, registry: MemoryRegistry
    ) -> bool:
        return registry.is_a(ref.type_id, rule.type_id) and fnmatchcase(
            ref.id, rule.id_pattern
        )

    def resolve(
        self,
        ref: EntityRef,
        registry: MemoryRegistry,
        partitions: Mapping[str, Partition],
    ) -> dict[str, str]:
        """Select active fields and fail every highest-priority tie before writing."""
        self._namespace(ref)
        matching = [r for r in self.rules if self._matches(ref, r, registry)]
        exact = [e for e in self.exact if e.ref == ref]
        fields = {f for r in matching for f in r.fields} | {e.field for e in exact}
        result = {}
        for field_id in sorted(fields):
            registry.field(field_id)
            overrides = [e.partition for e in exact if e.field == field_id]
            if overrides:
                if len(overrides) != 1:
                    raise KernelError("BINDING_AMBIGUOUS", "multiple exact overrides")
                owner = overrides[0]
            else:
                options = [r for r in matching if field_id in r.fields]
                priority = max(r.priority for r in options)
                winners = [r for r in options if r.priority == priority]
                if len(winners) != 1:
                    raise KernelError("BINDING_AMBIGUOUS", "highest-priority field tie")
                owner = winners[0].partition
            if owner not in partitions or field_id not in partitions[owner].produces:
                raise KernelError(
                    "BINDING_PRODUCES",
                    "resolved writer must declare selected production",
                )
            result[field_id] = owner
        return result

    def controller(
        self,
        ref: EntityRef,
        registry: MemoryRegistry,
        partitions: Mapping[str, Partition],
    ) -> str:
        """Resolve one lifecycle controller independently of field ownership."""
        self._namespace(ref)
        choices = [r for r in self.lifecycle if self._matches(ref, r, registry)]
        if not choices:
            raise KernelError(
                "LIFECYCLE_UNCOVERED", "no controller in selected create domain"
            )
        priority = max(r.priority for r in choices)
        winners = [r for r in choices if r.priority == priority]
        if len(winners) != 1:
            raise KernelError("LIFECYCLE_AMBIGUOUS", "highest-priority lifecycle tie")
        owner = winners[0].partition
        if owner not in partitions or not partitions[owner].lifecycle:
            raise KernelError(
                "LIFECYCLE_DECLARATION", "controller must declare lifecycle support"
            )
        return owner

    def participants(self, ref: EntityRef, registry: MemoryRegistry) -> tuple[str, ...]:
        """Select cleanup participants from all matching explicit type scopes."""
        return tuple(
            sorted(
                {
                    partition
                    for type_id, members in self.lifecycle_participants.items()
                    if registry.is_a(ref.type_id, type_id)
                    for partition in members
                }
            )
        )

    def validate(
        self, registry: MemoryRegistry, partitions: Mapping[str, Partition]
    ) -> None:
        """Preflight known domains; dynamic instances are resolved anew at creation."""
        if len({ref.id for ref in self.entities}) != len(self.entities):
            raise KernelError("BOOTSTRAP_DUPLICATE", "duplicate bootstrap ID")
        all_rules: tuple[BindingRule | LifecycleRule, ...] = (
            *self.rules,
            *self.lifecycle,
        )
        for rule in all_rules:
            validate_text(rule.partition)
            validate_text(rule.id_pattern)
            if type(rule.priority) is not int or rule.partition not in partitions:
                raise KernelError("BINDING_RULE", "invalid priority/partition")
            registry.is_a(rule.type_id, rule.type_id)
            if isinstance(rule, BindingRule):
                if not rule.fields or len(set(rule.fields)) != len(rule.fields):
                    raise KernelError(
                        "BINDING_RULE", "nonempty unique field selection required"
                    )
                for field_id in rule.fields:
                    registry.field(field_id)
            elif not partitions[rule.partition].lifecycle:
                raise KernelError(
                    "LIFECYCLE_DECLARATION", "controller support not declared"
                )
        for exact in self.exact:
            self._namespace(exact.ref)
            registry.is_a(exact.ref.type_id, exact.ref.type_id)
            registry.field(exact.field)
            if exact.partition not in partitions:
                raise KernelError("BINDING_PARTITION", "unknown exact partition")
        for ref in self.entities:
            self._namespace(ref)
            registry.is_a(ref.type_id, ref.type_id)
            if (
                ref.generation != 0
                or next(t for t in registry.types if t.id == ref.type_id).abstract
            ):
                raise KernelError("BOOTSTRAP_REF", "first concrete generation required")
            self.resolve(ref, registry, partitions)
            self.controller(ref, registry, partitions)
        for group in self.cohorts:
            if any(p not in partitions or not partitions[p].reactive for p in group):
                raise KernelError(
                    "COHORT_PARTITION", "cohort requires registered reactive members"
                )
        for type_id, participants in self.lifecycle_participants.items():
            registry.is_a(type_id, type_id)
            if len(set(participants)) != len(participants) or any(
                p not in partitions for p in participants
            ):
                raise KernelError(
                    "CLEANUP_PARTICIPANTS", "invalid native cleanup selection"
                )
        for source in self.cancel_sources:
            validate_text(source)
        for command in self.bootstrap_commands:
            descriptor = registry.message(command.schema_id)
            if (
                descriptor.kind != "command"
                or command.target not in partitions
                or command.schema_id not in partitions[command.target].commands
            ):
                raise KernelError(
                    "BOOTSTRAP_COMMAND", "unsupported bootstrap command route"
                )
            registry.validate(descriptor.schema, command.payload)
            if command.ingress_at_ns is not None or command.idempotency_key is not None:
                raise KernelError(
                    "BOOTSTRAP_COMMAND",
                    "offline requests do not use live reservation controls",
                )

    def to_data(self) -> dict[str, Any]:
        """Encode pinned binding policies with canonical enumeration."""
        from .codec import encode

        return {
            "run_id": self.run_id,
            "epoch": self.epoch,
            "entities": [
                e.to_data()
                for e in sorted(self.entities, key=lambda r: (r.id, r.generation))
            ],
            "rules": [
                {
                    "partition": r.partition,
                    "type_id": r.type_id,
                    "fields": sorted(r.fields),
                    "id_pattern": r.id_pattern,
                    "priority": r.priority,
                }
                for r in sorted(
                    self.rules,
                    key=lambda r: (
                        r.type_id,
                        r.id_pattern,
                        r.priority,
                        r.partition,
                        r.fields,
                    ),
                )
            ],
            "exact": [
                {"ref": e.ref.to_data(), "field": e.field, "partition": e.partition}
                for e in sorted(
                    self.exact,
                    key=lambda e: (e.ref.id, e.ref.generation, e.field, e.partition),
                )
            ],
            "lifecycle": [
                {
                    "partition": r.partition,
                    "type_id": r.type_id,
                    "id_pattern": r.id_pattern,
                    "priority": r.priority,
                }
                for r in sorted(
                    self.lifecycle,
                    key=lambda r: (r.type_id, r.id_pattern, r.priority, r.partition),
                )
            ],
            "cohorts": [list(c) for c in self.cohorts],
            "bootstrap_commands": encode(self.bootstrap_commands),
            "ingress_source": self.ingress_source,
            "control_source": self.control_source,
            "cancel_sources": sorted(self.cancel_sources),
            "lifecycle_participants": {
                k: sorted(v) for k, v in self.lifecycle_participants.items()
            },
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> BindingManifest:
        """Decode pinned bindings without compiler or scenario code."""
        from .codec import decode_record

        expected = {
            "run_id",
            "epoch",
            "entities",
            "rules",
            "exact",
            "lifecycle",
            "cohorts",
            "bootstrap_commands",
            "ingress_source",
            "control_source",
            "cancel_sources",
            "lifecycle_participants",
        }
        if set(data) != expected:
            raise KernelError("MANIFEST_DATA", "unexpected manifest fields")
        return cls(
            data["run_id"],
            data["epoch"],
            tuple(EntityRef.from_data(e) for e in data["entities"]),
            tuple(
                BindingRule(
                    r["partition"],
                    r["type_id"],
                    tuple(r["fields"]),
                    r["id_pattern"],
                    r["priority"],
                )
                for r in data["rules"]
            ),
            tuple(
                ExactBinding(EntityRef.from_data(e["ref"]), e["field"], e["partition"])
                for e in data["exact"]
            ),
            tuple(LifecycleRule(**r) for r in data["lifecycle"]),
            tuple(tuple(c) for c in data["cohorts"]),
            decode_record(data["bootstrap_commands"]),
            data["ingress_source"],
            data["control_source"],
            tuple(data["cancel_sources"]),
            {k: tuple(v) for k, v in data["lifecycle_participants"].items()},
        )
