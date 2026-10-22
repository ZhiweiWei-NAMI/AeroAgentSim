"""Shared admission of trusted installed contributions, without runtime startup."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from importlib.metadata import entry_points
from typing import Any, Generic, TypeVar

from .records import Contribution

T = TypeVar("T")
NAME_PATTERN = r"[a-z0-9][a-z0-9_-]*"


def validate_name(
    name: Any, *, label: str, pattern: str | None = NAME_PATTERN
) -> None:
    """Validate a provider namespace; legacy families may retain their own syntax."""
    if not isinstance(name, str) or not name or (
        pattern is not None and not re.fullmatch(pattern, name)
    ):
        rule = f"name must match {pattern}" if pattern else "name must be nonempty text"
        raise ValueError(f"{label} {name!r}: {rule}")


def validate_api_version(value: Any, supported: int, *, label: str) -> None:
    """Do not coerce booleans, floats or text into an admitted API version."""
    if type(value) is not int or value != supported:
        raise ValueError(
            f"{label}: unsupported API version {value!r}; "
            f"supported version is {supported}"
        )


@dataclass(frozen=True)
class EntryPointConflict:
    group: str
    name: str
    entries: tuple[Contribution, ...]


class DuplicateEntryPointError(ValueError):
    """An ambiguous installation, diagnosed before any provider is imported."""

    def __init__(self, conflict: EntryPointConflict) -> None:
        self.conflict = conflict
        distributions = " and ".join(
            f"{entry.distribution!r} (version {entry.version!r}, "
            f"entry point {entry.entry_point!r})"
            for entry in conflict.entries
        )
        super().__init__(
            f"Duplicate {conflict.group} entry point name: {conflict.name!r}; "
            f"distributions: {distributions}"
        )


@dataclass(frozen=True)
class DiscoveryResult(Generic[T]):
    """Sorted providers plus detached evidence for *all* declarations.

    ``providers`` is empty in metadata-only mode. ``contributions`` retains
    shadowed builtins and every duplicate, in installation order within a name.
    ``resolve`` supports lazy catalogs; callable engine factories are loaded,
    not invoked. Executable objects are not copied or sandboxed; their contracts
    remain responsible for snapshotting any mutable declaration data.
    """

    names: tuple[str, ...]
    providers: tuple[T, ...]
    contributions: tuple[Contribution, ...]
    conflicts: tuple[EntryPointConflict, ...]
    _resolve: Callable[[str], T] = field(repr=False, compare=False)

    def require_unique(self) -> None:
        if self.conflicts:
            raise DuplicateEntryPointError(self.conflicts[0])

    def resolve(self, name: str) -> T:
        """Load/admit one provider, rejecting ambiguity across the entire group."""
        self.require_unique()
        if name not in self.names:
            raise KeyError(name)
        return self._resolve(name)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _record(group: str, entry: Any) -> Contribution:
    distribution = getattr(entry, "dist", None)
    return Contribution(
        group=group,
        name=entry.name,
        source="installed",
        distribution=_text(getattr(distribution, "name", None)),
        version=_text(getattr(distribution, "version", None)),
        entry_point=_text(getattr(entry, "value", None)),
    )


def _admit(
    name: str,
    value: Any,
    origin: str,
    *,
    expected_type: type[T],
    api_version: int | None,
    name_of: Callable[[T], str] | None,
    factory_label: str | None,
    type_error: str | None,
    name_pattern: str | None,
) -> T:
    if not isinstance(value, expected_type) and factory_label and callable(value):
        try:
            value = value()
        except TypeError as exc:
            raise TypeError(
                f"{origin} {name!r}: zero-argument {factory_label} factory failed: {exc}"
            ) from exc
    if not isinstance(value, expected_type):
        if type_error is not None:
            raise TypeError(type_error.format(name=name))
        suffix = " or a zero-argument callable returning one" if factory_label else ""
        raise TypeError(
            f"{origin} {name!r}: expected a {expected_type.__name__}{suffix}"
        )
    if name_of is not None:
        actual = name_of(value)
        if actual != name:
            raise ValueError(
                f"{origin} {name!r}: plugin name {actual!r} does not match"
            )
        validate_name(actual, label=factory_label or origin, pattern=name_pattern)
    if api_version is not None:
        validate_api_version(
            getattr(value, "api_version", None), api_version,
            label=f"{factory_label or origin} {name!r}",
        )
    return value


def discover(
    group: str,
    builtins: Mapping[str, Any],
    *,
    expected_type: type[T] | None = None,
    api_version: int | None = None,
    name_of: Callable[[T], str] | None = None,
    load: bool = True,
    entries: Iterable[Any] | None = None,
    factory_label: str | None = "plugin",
    builtin_loader: Callable[[Any], Any] | None = None,
    type_error: str | None = None,
    name_pattern: str | None = NAME_PATTERN,
) -> DiscoveryResult[T]:
    """Admit the sorted builtin/installed union, replacing whole providers.

    ``entries`` permits an adapter to retain its existing metadata query seam.
    Versioned families supply ``expected_type``, ``api_version``, ``name_of``
    and a ``factory_label`` for zero-argument factories. Unversioned callable
    families must not invent descriptor versions or invoke factories here.

    ``load=False`` never calls entry.load(), builtin loaders or factories. It
    returns conflicts as immutable diagnostics; runtime callers must reject
    them with ``require_unique()`` (also enforced by ``resolve``). Inventory
    can therefore describe an ambiguous installation without picking a winner.
    """
    if api_version is not None and type(api_version) is not int:
        raise TypeError("discovery api_version: expected an integer or None")
    if load and expected_type is None:
        raise TypeError("discovery expected_type is required when loading")
    builtin_values = dict(builtins)
    installed: dict[str, list[Any]] = {}
    declared = entry_points(group=group) if entries is None else entries
    for entry in declared:
        validate_name(entry.name, label=f"{group} entry point", pattern=name_pattern)
        installed.setdefault(entry.name, []).append(entry)
    for name in builtin_values:
        validate_name(name, label=f"builtin {group}", pattern=name_pattern)
    names = tuple(sorted(set(builtin_values) | set(installed)))
    contributions: list[Contribution] = []
    conflicts: list[EntryPointConflict] = []
    for name in names:
        if name in builtin_values:
            contributions.append(Contribution(group, name, "builtin", None, None, None))
        records = tuple(_record(group, entry) for entry in installed.get(name, ()))
        contributions.extend(records)
        if len(records) > 1:
            conflicts.append(EntryPointConflict(group, name, records))

    def resolve(name: str) -> T:
        # Even metadata-only results cannot import an unconfigured provider.
        if expected_type is None:
            raise TypeError("discovery expected_type is required when loading")
        if name in installed:
            value = installed[name][0].load()
            origin = f"{group} entry point"
        else:
            value = builtin_values[name]
            if builtin_loader is not None:
                value = builtin_loader(value)
            origin = f"builtin {factory_label or group}"
        return _admit(
            name, value, origin, expected_type=expected_type, api_version=api_version,
            name_of=name_of, factory_label=factory_label, type_error=type_error,
            name_pattern=name_pattern,
        )

    result: DiscoveryResult[T] = DiscoveryResult(
        names, (), tuple(contributions), tuple(conflicts), resolve,
    )
    if load:
        result.require_unique()
        return replace(result, providers=tuple(result.resolve(name) for name in names))
    return result


__all__ = [
    "DiscoveryResult", "DuplicateEntryPointError", "EntryPointConflict", "discover",
    "validate_api_version", "validate_name",
]
