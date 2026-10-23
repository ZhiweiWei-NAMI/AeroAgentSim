"""Definition pins and read-only provenance, not historical projection execution.

Discovery loads trusted metric/presentation contributions only when requested.
Importing this module does not discover plugins or import service projectors.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .contracts import RunHeader

if TYPE_CHECKING:
    from aeroagentsim.presentation_api import PresentationPlugin

PROJECTION_PROVENANCE_FORMAT = "aeroagentsim.projection-provenance/v1"
PROJECTION_PINS_FORMAT = "aeroagentsim.projection-pins/v1"
PROJECTION_PINS_FILENAME = "projection-pins.json"
_DEFINITION_KEYS = {
    "viewer_feed", "kpi", "implementation_digest", "metric_reducers",
    "presentation_snapshot_digest", "presentation_provider_digest", "authored_digests",
}
# Content identities, independent of checkout paths, inodes and installation times.
# These cover the host interpreters; plugin implementations rely on their declared
# revisions/distribution identities, not an unverifiable historical source lock.
_IMPLEMENTATION_FILES = (
    "services/feed_index.py", "services/projector.py", "services/subjects.py",
    "services/kpis.py", "services/narratives.py", "metrics_api.py", "runs/reader.py",
    "presentations.py", "presentation_api.py",
)


class ProjectionMode(str, Enum):
    ORIGINAL = "original"
    RECOMPUTED_CURRENT_CODE = "recomputed-current-code"
    LEGACY_UNPINNED = "legacy-unpinned"


@dataclass(frozen=True)
class ProjectionProvenance:
    """One reader cut, recorded/current definitions and their comparison mode.

    The JSON trees are detached caller-owned data, like RecordedMessage.payload;
    freezing this envelope does not recursively freeze its dictionaries/lists.
    "original" means matching recorded identities, not archived executable code,
    authentication, run success, or proof that numerical results were retained.
    """

    source_cut: dict[str, Any] | None
    pinned_definitions: dict[str, Any] | None
    current_definitions: dict[str, Any]
    mode: ProjectionMode
    format: str = field(default=PROJECTION_PROVENANCE_FORMAT, init=False)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _definitions_digest(definitions: dict[str, Any] | None) -> str | None:
    """Identify the full definition tree, including descriptors; absence is unknown."""
    return _digest(_json_bytes(definitions)) if definitions is not None else None


def _implementation_digest() -> str:
    base = Path(__file__).resolve().parents[1]
    identities = {name: _digest((base / name).read_bytes()) for name in _IMPLEMENTATION_FILES}
    return _digest(_json_bytes(identities))


def projection_definitions(
    directory: Path, *, presentation_plugins: tuple[PresentationPlugin, ...] | None = None,
) -> dict[str, Any]:
    """Identify current definitions without running reducers or creating caches.

    A stored presentation snapshot restricts provider names/order, just as the
    service selection does. Compare current provider identities WITHOUT replacing
    or reinterpreting the stored DATA. This diagnostic can therefore describe
    provider drift even when the service correctly refuses that projection.
    """
    from aeroagentsim.metrics_api import (
        discover_metric_plugins,
        metric_plugin_fingerprint,
    )
    from aeroagentsim.presentation_api import (
        discover_presentation_plugins,
        presentation_plugin_digest,
    )
    from aeroagentsim.presentations import read_presentation_snapshot

    directory = Path(directory)
    snapshot = read_presentation_snapshot(directory)
    names = tuple(provider["name"] for provider in snapshot["providers"]) if snapshot is not None else None
    if presentation_plugins is None:
        presentation_plugins = discover_presentation_plugins(names=names)
    if names is not None:
        by_name = {plugin.name: plugin for plugin in presentation_plugins}
        if set(names) - by_name.keys():
            raise ValueError("Projection provenance: pinned presentation providers unavailable")
        presentation_plugins = tuple(by_name[name] for name in names)
    authored = {}
    for name in ("scenario.json", "runtime.registry.json", "behaviour.ir.json"):
        path = directory / name
        # Absent historical declarations remain unknown, never hashes of an
        # invented empty document. The ordinary service still validates inputs.
        authored[name] = _digest(path.read_bytes()) if path.exists() else None
    return {
        "viewer_feed": "aeroagentsim.viewer-feed/v1",
        "kpi": "aeroagentsim.kpi/v1",
        "implementation_digest": _implementation_digest(),
        "metric_reducers": metric_plugin_fingerprint(discover_metric_plugins()),
        "presentation_snapshot_digest": snapshot["digest"] if snapshot is not None else None,
        "presentation_provider_digest": presentation_plugin_digest(presentation_plugins),
        "authored_digests": authored,
    }


def _pin_record(value: Any, keys: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Projection pins: incomplete or malformed {label}")


def _pin_text(value: Any, label: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"Projection pins: {label} must be nonempty text")


def _pin_digest(value: Any, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError(f"Projection pins: invalid {label} digest")


def _validate_definitions(definitions: Any) -> None:
    """Validate historical knowledge, not just whether two JSON trees differ."""
    _pin_record(definitions, _DEFINITION_KEYS, "definitions")
    for name, contract in (("viewer_feed", "aeroagentsim.viewer-feed/v1"),
                           ("kpi", "aeroagentsim.kpi/v1")):
        if definitions[name] != contract:
            raise ValueError(f"Projection pins: unsupported {name} contract")
    _pin_digest(definitions["implementation_digest"], "implementation")
    _pin_digest(definitions["presentation_provider_digest"], "presentation provider")
    _pin_digest(definitions["presentation_snapshot_digest"], "presentation snapshot", optional=True)
    authored = definitions["authored_digests"]
    _pin_record(authored, {"scenario.json", "runtime.registry.json", "behaviour.ir.json"}, "authored digests")
    for name, digest in authored.items():
        # These nulls explicitly mean an absent historical input, not an empty one.
        _pin_digest(digest, name, optional=True)
    plugins = definitions["metric_reducers"]
    if not isinstance(plugins, list):
        raise ValueError("Projection pins: metric_reducers must be a list")
    names: set[str] = set()
    metric_ids: set[str] = set()
    for plugin in plugins:
        _pin_record(plugin, {"name", "api_version", "entry_point", "distribution", "reducers"}, "metric plugin")
        name = plugin["name"]
        if type(name) is not str or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name) is None or name in names:
            raise ValueError("Projection pins: invalid or duplicate metric plugin name")
        names.add(name)
        if type(plugin["api_version"]) is not int or plugin["api_version"] < 1:
            raise ValueError("Projection pins: metric API version must be a positive integer")
        if plugin["entry_point"] is not None:
            _pin_text(plugin["entry_point"], "metric entry point")
        distribution = plugin["distribution"]
        if distribution is not None:
            if not isinstance(distribution, list) or len(distribution) != 2 or distribution == [None, None]:
                raise ValueError("Projection pins: invalid metric distribution identity")
            for part in distribution:
                if part is not None:
                    _pin_text(part, "metric distribution")
        reducers = plugin["reducers"]
        if not isinstance(reducers, list):
            raise ValueError("Projection pins: reducers must be a list")
        for reducer in reducers:
            _pin_record(reducer, {"implementation", "version", "input_schemas", "descriptors"}, "metric reducer")
            _pin_text(reducer["implementation"], "reducer implementation")
            if re.fullmatch(r"[^:]+:[^:]+", reducer["implementation"]) is None:
                raise ValueError("Projection pins: invalid reducer implementation identity")
            _pin_text(reducer["version"], "reducer revision")
            schemas = reducer["input_schemas"]
            if not isinstance(schemas, list) or not schemas:
                raise ValueError("Projection pins: input_schemas must be a nonempty list")
            for schema in schemas:
                _pin_text(schema, "input schema")
            descriptors = reducer["descriptors"]
            if not isinstance(descriptors, list) or not descriptors:
                raise ValueError("Projection pins: descriptors must be a nonempty list")
            for descriptor in descriptors:
                text_keys = {"id", "label", "label_zh", "unit", "aggregation", "missing_data_policy"}
                _pin_record(descriptor, text_keys | {"direction"}, "metric descriptor")
                for key in text_keys:
                    _pin_text(descriptor[key], f"descriptor {key}")
                if descriptor["direction"] not in (None, "higher", "lower"):
                    raise ValueError("Projection pins: invalid metric direction")
                if descriptor["id"] in metric_ids:
                    raise ValueError("Projection pins: duplicate metric descriptor id")
                metric_ids.add(descriptor["id"])


def _read_pins(directory: Path) -> dict[str, Any] | None:
    path = directory / PROJECTION_PINS_FILENAME
    try:
        pins = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, RecursionError) as exc:
        raise ValueError("Projection pins: invalid JSON record") from exc
    _pin_record(pins, {"format", "definitions"}, "record")
    if pins["format"] != PROJECTION_PINS_FORMAT:
        raise ValueError("Projection pins: unsupported format")
    _validate_definitions(pins["definitions"])
    # Reject non-finite JSON even if a foreign writer used permissive encoding.
    _json_bytes(pins)
    return pins["definitions"]


def _provenance_for_cut(
    directory: Path, header: RunHeader | None, cut_index: int | None, cut_bytes: int | None,
    current_definitions: dict[str, Any],
) -> ProjectionProvenance:
    """Internal adapter: the caller owns the acknowledged cut coordinates."""
    pinned = _read_pins(directory)
    # JSON identity is type-strict: Python's True == 1 is not a matching API pin.
    mode = (
        ProjectionMode.LEGACY_UNPINNED if pinned is None
        else ProjectionMode.ORIGINAL if _json_bytes(pinned) == _json_bytes(current_definitions)
        else ProjectionMode.RECOMPUTED_CURRENT_CODE
    )
    return ProjectionProvenance(
        source_cut={
            "service_run_id": header.service_run_id,
            "kernel_run_id": header.kernel_run_id,
            "epoch": header.epoch,
            "journal_identity": list(header.journal_identity),
            "configuration_digest": header.configuration_digest,
            "registry_digest": header.registry_digest,
            "cut_index": cut_index,
            "cut_bytes": cut_bytes,
        } if header is not None else None,
        pinned_definitions=deepcopy(pinned),
        current_definitions=deepcopy(current_definitions),
        mode=mode,
    )


def projection_provenance(directory: str | Path) -> ProjectionProvenance:
    """Capture a reader ACK and compare run pins with current definitions.

    Filesystem-read-only: no SQLite, sidecar creation/repair, simulation, reducers
    or projection execution. An absent ACK leaves source_cut unknown (None); no
    index or visible WAL is promoted to a reader cut. When an ACK exists, reader
    admission/errors apply, including for malformed or empty coordinates.
    Invalid pins/snapshots or unavailable current providers raise, never imply
    "original". A missing projection pin is always "legacy-unpinned", even when
    a presentation snapshot alone was recorded by an older host.
    """
    from .reader import open_run

    directory = Path(directory).resolve()
    ack = directory / "committed-prefix.json"
    if directory.is_dir() and not ack.exists() and not ack.is_symlink():
        return _provenance_for_cut(
            directory, None, None, None, projection_definitions(directory),
        )
    with open_run(directory) as run:
        snapshot = run.snapshot()
        return _provenance_for_cut(
            directory, snapshot.header, snapshot.cut_index, snapshot.cut_bytes,
            projection_definitions(directory),
        )
