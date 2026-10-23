"""Definition pins and read-only provenance, not historical projection execution.

Discovery loads trusted metric/presentation contributions only when requested.
Importing this module does not discover plugins or import service projectors.
"""

from __future__ import annotations

import hashlib
import json
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


def _read_pins(directory: Path) -> dict[str, Any] | None:
    path = directory / PROJECTION_PINS_FILENAME
    try:
        pins = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (
        not isinstance(pins, dict) or set(pins) != {"format", "definitions"}
        or pins["format"] != PROJECTION_PINS_FORMAT
        or not isinstance(pins["definitions"], dict)
        or set(pins["definitions"]) != _DEFINITION_KEYS
    ):
        raise ValueError("Projection pins: malformed record or unsupported format")
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
