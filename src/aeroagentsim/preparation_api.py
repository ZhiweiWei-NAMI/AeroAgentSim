"""Public v1 companion contract for deterministic, final-seed preparation.

Preparers are trusted Python, not a sandbox. They must use only authored inputs,
the final seed and supplied read-only resource bytes: no providers, installation,
process startup, file writes, wall clock or unseeded randomness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from importlib import import_module
from importlib.metadata import entry_points
from inspect import iscoroutinefunction
from types import MappingProxyType
from typing import Any, Protocol

from aeroagentsim.extensions.discovery import discover

PREPARATION_API_VERSION = 1
ENTRY_POINT_GROUP = "aeroagentsim.preparers"
ReadOnlyResources = Mapping[str, bytes]


def _json_data(value: Any, *, finite: bool = False) -> None:
    """Check JSON structure, optionally validating scalars without serializing ints."""
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("preparation data: mapping keys must be strings")
        if finite:
            for key in value:
                key.encode("utf-8")
        for child in value.values():
            _json_data(child, finite=finite)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _json_data(child, finite=finite)
    elif value is not None and type(value) not in (str, bool, int, float):
        raise ValueError("preparation data: expected finite JSON data")
    elif finite:
        if type(value) is float and not math.isfinite(value):
            raise ValueError("preparation data: expected finite JSON data")
        if type(value) is str:
            value.encode("utf-8")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _yaml_evidence(value: Any) -> bytes:
    """Total, type-tagged canonical encoding of SafeLoader values.

    Work bottom-up without Python/JSON recursion limits. Ancestor-relative refs
    encode recursive aliases without object addresses; other aliases expand by
    value. Mapping rows and set members sort by encoded bytes, not Python values.
    """
    pending = [(value, False)]
    encoded: list[bytes] = []
    ancestors: dict[int, int] = {}
    while pending:
        node, finishing = pending.pop()
        if finishing:
            count = len(node) * (2 if isinstance(node, dict) else 1)
            children = encoded[-count:] if count else []
            if count:
                del encoded[-count:]
            if isinstance(node, dict):
                children = sorted(
                    b"[" + key + b"," + child + b"]"
                    for key, child in zip(children[::2], children[1::2])
                )
            elif isinstance(node, (set, frozenset)):
                children.sort()
            tag = type(node).__name__.encode("ascii")
            encoded.append(b'["' + tag + b'",[' + b",".join(children) + b"]]")
            del ancestors[id(node)]
            continue
        if isinstance(node, (dict, list, tuple, set, frozenset)):
            if id(node) in ancestors:
                tagged = ["ref", len(ancestors) - 1 - ancestors[id(node)]]
            else:
                ancestors[id(node)] = len(ancestors)
                pending.append((node, True))
                children = (
                    [item for row in node.items() for item in row]
                    if isinstance(node, dict) else list(node)
                )
                pending.extend((child, False) for child in reversed(children))
                continue
        elif node is None or type(node) in (str, bool):
            tagged = [type(node).__name__, node]
        elif type(node) is int:
            # Hex has no decimal-conversion digit limit, even for YAML hex ints.
            tagged = ["int", hex(node)]
        elif type(node) is float:
            tagged = ["float", node.hex()]  # Includes inf, -inf and nan.
        elif isinstance(node, datetime):  # datetime is also a date.
            tagged = ["datetime", node.isoformat()]
        elif isinstance(node, date):
            tagged = ["date", node.isoformat()]
        elif isinstance(node, bytes):
            tagged = ["bytes", node.hex()]
        else:
            raise ValueError(f"preparation data: unsupported YAML value {type(node).__name__}")
        # ASCII escaping also covers lone surrogates constructible via YAML escapes.
        encoded.append(json.dumps(tagged, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
    return encoded[0]


def document_digest(value: Any, *, legacy_yaml: bool = False) -> str:
    """Hash strict finite JSON, or always use tagged SafeLoader evidence for v1.

    Legacy encoding is value-defined, independent of decimal-conversion and
    recursion limits, never a normalization or an additional admission restriction
    on authored/prepared v1 values. Strict JSON digest bytes are unchanged.
    """
    if legacy_yaml:
        data = b"aeroagentsim.yaml-evidence/v1\0" + _yaml_evidence(value)
    else:
        _json_data(value)
        data = _canonical_json(value)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def resource_manifest(resources: ReadOnlyResources) -> dict[str, str]:
    """Hash the actual input bytes, not a plugin's claimed resource digests."""
    return {
        name: "sha256:" + hashlib.sha256(resources[name]).hexdigest()
        for name in sorted(resources)
    }


@dataclass(frozen=True)
class PreparedScenario:
    """Detached preparation output; not a live registry, engine or run session.

    The driver records its selected preparer in ``contribution_selections``.
    Additional selections are declarative evidence, never implicit activation.
    ``resource_manifest`` names supplied resources and hashes their bytes.
    """

    document: dict[str, Any]
    contribution_selections: tuple[dict[str, Any], ...] = ()
    resource_manifest: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.document, dict):
            raise ValueError("prepared scenario document: expected a mapping")
        if not isinstance(self.contribution_selections, tuple) or any(
            not isinstance(item, dict) for item in self.contribution_selections
        ):
            raise ValueError("prepared contribution selections: expected a tuple of mappings")
        if not isinstance(self.resource_manifest, dict) or any(
            not isinstance(name, str) or not name
            or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            for name, digest in self.resource_manifest.items()
        ):
            raise ValueError("prepared resource manifest: expected named SHA-256 digests")
        # V1 uses the total legacy digest, not stricter output admission: even
        # tuple/set-contained nonfinite floats retain historical v1 semantics.
        legacy_yaml = self.document.get("format") == "aeroagentsim.scenario/v1"
        document_digest(self.document, legacy_yaml=legacy_yaml)
        if legacy_yaml:
            # Selection evidence remains finite JSON, but checking its structure
            # must not decimal-serialize the historically unbounded final seed.
            _json_data(self.contribution_selections, finite=True)
        else:
            document_digest(self.contribution_selections)
        object.__setattr__(self, "document", copy.deepcopy(self.document))
        object.__setattr__(self, "contribution_selections", copy.deepcopy(self.contribution_selections))
        object.__setattr__(self, "resource_manifest", dict(self.resource_manifest))


class Preparer(Protocol):
    """Pure synchronous preparation, repeatable from the original authored input.

    Inputs belong to the host. Return a new PreparedScenario; never mutate or
    retain authored inputs/resources. Configuration lives in the authored v2
    ``preparation.config`` mapping and is validated before this call.
    """

    def __call__(
        self, authored_document: dict[str, Any], final_seed: int,
        read_only_resources: ReadOnlyResources, /,
    ) -> PreparedScenario: ...


def _empty_config(config: dict[str, Any]) -> None:
    if config:
        raise ValueError("preparation.config: this preparer accepts only an empty mapping")


@dataclass(frozen=True)
class PreparerPlugin:
    """Independently versioned, immutable descriptor for one preparer.

    Bump ``revision`` when implementation semantics change. Discovery attaches
    entry-point/distribution metadata; these fields are not plugin activation.
    The default config validator rejects every nonempty configuration.
    """

    name: str
    prepare: Preparer
    revision: str
    api_version: int = 1
    validate_config: Callable[[dict[str, Any]], None] = _empty_config
    entry_point: str | None = None
    distribution: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.name):
            raise ValueError("preparer name must match [a-z0-9][a-z0-9_-]*")
        if type(self.api_version) is not int or self.api_version != PREPARATION_API_VERSION:
            raise ValueError(
                f"preparer {self.name!r}: unsupported API version {self.api_version!r}; "
                f"supported version is {PREPARATION_API_VERSION}"
            )
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError("preparer revision: expected nonempty text")
        for name in ("prepare", "validate_config"):
            method = getattr(self, name)
            if (not callable(method) or iscoroutinefunction(method)
                    or iscoroutinefunction(getattr(method, "__call__", None))):
                raise ValueError(f"preparer {name}: expected a synchronous callable")
        if self.entry_point is not None and (
            not isinstance(self.entry_point, str) or not self.entry_point.strip()
        ):
            raise ValueError("preparer entry_point: expected nonempty text or None")
        if self.distribution is not None and (
            not isinstance(self.distribution, tuple) or len(self.distribution) != 2
            or any(not isinstance(item, str) or not item for item in self.distribution)
        ):
            raise ValueError("preparer distribution: expected (name, version) or None")


# Lazy references keep domain imports out of the public contract. Do not add
# self-entry points: installed providers can replace these complete descriptors.
BUILTINS = MappingProxyType({
    "explicit": "aeroagentsim.scenario.preparation:explicit_preparer",
    "lowalt-legacy": "aeroagentsim.packs.lowalt.preparation:plugin",
})


def discover_preparers() -> tuple[PreparerPlugin, ...]:
    """Use shared preflight and admission, retaining preparer identity evidence.

    Duplicate/malformed declarations fail before any load/factory. Traverse the
    sorted union, replacing whole builtins without fallback. Never call prepare.
    """
    def load_builtin(reference: str) -> Any:
        module, attribute = reference.split(":", 1)
        return getattr(import_module(module), attribute)

    discovered = discover(
        ENTRY_POINT_GROUP, BUILTINS,
        expected_type=PreparerPlugin, api_version=PREPARATION_API_VERSION,
        name_of=lambda plugin: plugin.name, factory_label="preparer",
        builtin_loader=load_builtin, entries=entry_points(group=ENTRY_POINT_GROUP),
    )
    installed = {
        item.name: item for item in discovered.contributions if item.source == "installed"
    }
    plugins = []
    for plugin in discovered.providers:
        record = installed.get(plugin.name)
        plugins.append(replace(
            plugin,
            entry_point=BUILTINS[plugin.name] if record is None else record.entry_point,
            distribution=(
                None if record is None or record.distribution is None or record.version is None
                else (record.distribution, record.version)
            ),
        ))
    return tuple(plugins)


__all__ = [
    "PREPARATION_API_VERSION", "ENTRY_POINT_GROUP", "Preparer", "PreparerPlugin",
    "PreparedScenario", "ReadOnlyResources", "discover_preparers",
    "document_digest", "resource_manifest",
]
