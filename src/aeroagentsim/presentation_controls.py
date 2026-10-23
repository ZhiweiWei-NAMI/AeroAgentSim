"""Read effective controls from pinned JSON, without invoking authoring hooks.

Readers are trusted, pure, synchronous server adapters. They receive only a
recursive read-only JSON snapshot; this module never discovers or applies hooks.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class Known:
    value: Any
    evidence_digest: str


@dataclass(frozen=True)
class Unavailable:
    reason: str


ControlReader = Callable[[Mapping[str, Any]], Known | Unavailable]


def _json_data(value: Any) -> Any:
    """Snapshot JSON data, rejecting live objects and non-finite numbers."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("control evidence: JSON object keys must be strings")
        return {key: _json_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_data(item) for item in value]
    raise TypeError("control evidence: finite JSON data required")


def _readonly(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_readonly(item) for item in value)
    return value


def known(value: Any, evidence: Any) -> Known:
    """Bind a JSON value to the SHA-256 digest of its canonical JSON evidence."""
    encoded = json.dumps(
        _json_data(evidence), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return Known(_json_data(value), hashlib.sha256(encoded).hexdigest())


def _pointer_parts(pointer: Any) -> tuple[str, ...]:
    # RFC 6901's empty pointer selects the whole document. URI fragments and
    # malformed escapes are not JSON pointers; '-' is never an array read index.
    if not isinstance(pointer, str) or (
        pointer and (not pointer.startswith("/") or re.search(r"~(?![01])", pointer))
    ):
        raise ValueError(f"control pointer {pointer!r}: invalid JSON pointer")
    return tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer[1:].split("/")
    ) if pointer else ()


@dataclass(frozen=True)
class ControlRegistry:
    """Immutable declarations and read adapters, separate from apply registries.

    Declarations may retain historical template fields (including ``default``),
    but only ``id`` and the explicit ``pointer``/``apply`` binding are read.
    """

    controls: tuple[Mapping[str, Any], ...] = ()
    readers: Mapping[str, ControlReader] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.controls, (list, tuple)):
            raise TypeError("controls: a tuple of mapping declarations is required")
        declarations = []
        identifiers: set[str] = set()
        for declaration in self.controls:
            if not isinstance(declaration, Mapping):
                raise TypeError("control declaration: mapping required")
            identifier = declaration.get("id")
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError("control declaration: nonempty id required")
            if identifier in identifiers:
                raise ValueError(f"Duplicate control id {identifier!r}")
            identifiers.add(identifier)
            if ("pointer" in declaration) == ("apply" in declaration):
                raise ValueError(f"control {identifier}: exactly one pointer/apply required")
            if "pointer" in declaration:
                _pointer_parts(declaration["pointer"])
            elif not isinstance(declaration["apply"], str) or not declaration["apply"].strip():
                raise ValueError(f"control {identifier}: nonempty apply reader name required")
            declarations.append(_readonly(_json_data(declaration)))
        if not isinstance(self.readers, Mapping):
            raise TypeError("control readers: mapping required")
        readers = dict(self.readers)
        for name, reader in readers.items():
            if not isinstance(name, str) or not name.strip() or not callable(reader):
                raise ValueError("control reader: nonempty name and callable required")
            call = getattr(reader, "__call__", None)
            if any(check(candidate) for check in (inspect.iscoroutinefunction, inspect.isasyncgenfunction)
                   for candidate in (reader, call)):
                raise TypeError(f"control reader {name!r}: synchronous adapter required")
        object.__setattr__(self, "controls", tuple(declarations))
        object.__setattr__(self, "readers", MappingProxyType(readers))


def _at(document: Mapping[str, Any], pointer: str) -> Known | Unavailable:
    value: Any = document
    for part in _pointer_parts(pointer):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(value, tuple) and re.fullmatch(r"0|[1-9][0-9]*", part):
            if len(part) > len(str(len(value))) or int(part) >= len(value):
                return Unavailable(f"Pinned source {pointer!r}: array index does not exist")
            value = value[int(part)]
        else:
            return Unavailable(f"Pinned source {pointer!r}: member does not exist")
    return known(value, {"pointer": pointer, "value": value})


def read_controls(
    document: Mapping[str, Any], registry: ControlRegistry,
) -> dict[str, Known | Unavailable]:
    """Read explicit pinned sources only; absent evidence is never a default.

    Malformed declarations and broken adapter contracts raise errors rather than
    being disguised as missing evidence. Data-dependent absence is represented
    by ``Unavailable`` returned by a pointer read or by a trusted adapter.
    """
    if not isinstance(document, Mapping):
        raise TypeError("control document: JSON mapping required")
    if not isinstance(registry, ControlRegistry):
        raise TypeError("control registry: ControlRegistry required")
    snapshot = _readonly(_json_data(document))
    result: dict[str, Known | Unavailable] = {}
    for declaration in registry.controls:
        if "pointer" in declaration:
            observation = _at(snapshot, declaration["pointer"])
        else:
            name = declaration["apply"]
            reader = registry.readers.get(name)
            observation = (
                Unavailable(f"No effective-control reader registered for {name!r}")
                if reader is None else reader(snapshot)
            )
        if isinstance(observation, Known):
            if not isinstance(observation.evidence_digest, str) or not re.fullmatch(
                r"[0-9a-f]{64}", observation.evidence_digest,
            ):
                raise ValueError("control reader: SHA-256 evidence digest required")
            observation = Known(_json_data(observation.value), observation.evidence_digest)
        elif not isinstance(observation, Unavailable):
            raise TypeError("control reader: expected Known or Unavailable")
        elif not isinstance(observation.reason, str) or not observation.reason.strip():
            raise ValueError("control reader: nonempty unavailable reason required")
        result[declaration["id"]] = observation
    return result


__all__ = ["Known", "Unavailable", "ControlRegistry", "ControlReader", "known", "read_controls"]
