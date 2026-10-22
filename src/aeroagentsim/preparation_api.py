"""Public v1 companion contract for deterministic, final-seed preparation.

Preparers are trusted Python, not a sandbox. They must use only authored inputs,
the final seed and supplied read-only resource bytes: no providers, installation,
process startup, file writes, wall clock or unseeded randomness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from importlib import import_module
from importlib.metadata import entry_points
from inspect import iscoroutinefunction
from types import MappingProxyType
from typing import Any, Protocol

PREPARATION_API_VERSION = 1
ENTRY_POINT_GROUP = "aeroagentsim.preparers"
ReadOnlyResources = Mapping[str, bytes]


def _json_data(value: Any) -> None:
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("preparation data: mapping keys must be strings")
        for child in value.values():
            _json_data(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _json_data(child)
    elif value is not None and type(value) not in (str, bool, int, float):
        raise ValueError("preparation data: expected finite JSON data")


def document_digest(value: Any) -> str:
    """SHA-256 of sorted, compact, finite UTF-8 JSON, without a terminal LF."""
    _json_data(value)
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
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
        # Validation also rejects non-JSON objects and nonfinite output values.
        document_digest(self.document)
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
    """Mirror metrics_api discovery until the shared discovery helper lands.

    Duplicate installed names fail BEFORE any load/factory. Traverse the sorted
    builtin/installed union; an installed name replaces the entire builtin,
    without fallback on errors. Merely discovering a preparer never calls it.
    Keep these rules isolated here for the follow-up shared-helper migration.
    """
    installed = {}
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name in installed:
            raise ValueError(f"Duplicate {ENTRY_POINT_GROUP} entry point name: {entry.name!r}")
        installed[entry.name] = entry
    plugins = []
    for name in sorted(set(BUILTINS) | set(installed)):
        entry = installed.get(name)
        reference = BUILTINS[name] if entry is None else entry.value
        if entry is None:
            module, attribute = reference.split(":", 1)
            value = getattr(import_module(module), attribute)
            origin = "builtin preparer"
        else:
            value = entry.load()
            origin = f"{ENTRY_POINT_GROUP} entry point"
        if not isinstance(value, PreparerPlugin) and callable(value):
            try:
                value = value()
            except TypeError as exc:
                raise TypeError(f"{origin} {name!r}: zero-argument preparer factory failed: {exc}") from exc
        if not isinstance(value, PreparerPlugin):
            raise TypeError(f"{origin} {name!r}: expected a PreparerPlugin or a zero-argument factory")
        if value.name != name:
            raise ValueError(f"{origin} {name!r}: plugin name {value.name!r} does not match")
        dist = getattr(entry, "dist", None)
        plugins.append(replace(
            value, entry_point=reference,
            distribution=None if dist is None else (dist.name, dist.version),
        ))
    return tuple(plugins)


__all__ = [
    "PREPARATION_API_VERSION", "ENTRY_POINT_GROUP", "Preparer", "PreparerPlugin",
    "PreparedScenario", "ReadOnlyResources", "discover_preparers",
    "document_digest", "resource_manifest",
]
