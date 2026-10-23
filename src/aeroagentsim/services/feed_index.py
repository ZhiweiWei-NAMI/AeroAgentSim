"""Rebuildable SQLite viewer cuts and resumable committed-state checkpoints.

The journal and its pinned declarations remain authoritative. A transaction
publishes cuts and all three projector cursors together, so readers never see
a cut whose lifecycle, KPI or narrative state belongs to another prefix.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import zlib
from collections import OrderedDict
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from stat import S_IFMT
from typing import Any

from aerokernel.errors import KernelError
from aerokernel.registry import MemoryRegistry
from aerokernel.values import parse_json

from ..presentation_api import PresentationPlugin, presentation_plugin_digest
from ..presentations import presentation_selection
from ..runs import RunReadError, open_run
from ..runs.projections import (
    PROJECTION_PINS_FILENAME,
    _IMPLEMENTATION_FILES,
    _definitions_digest,
    _provenance_for_cut,
    projection_definitions,
)
from .kpis import KPIProjector
from .narratives import NarrativeProjector
from .projector import project, projection_record
from .storage import RunStorage, committed_prefix
from .subjects import SubjectProjection

FORMAT = "aeroagentsim.viewer-index/v1"
FILENAME = "viewer-index.sqlite3"
TERMINAL = {"completed", "stopped", "faulted", "interrupted", "input_timeout"}
_LOCK = threading.RLock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}


@dataclass
class _Projection:
    checkpoint: bytes
    signature: list[Any]
    next: int
    subjects: SubjectProjection
    kpis: KPIProjector
    narratives: NarrativeProjector
    storage: RunStorage
    provenance: dict[str, Any] | None
    definition_digests: dict[str, str | None] | None


# A bounded working set, never the source of truth. The committed SQLite blob
# must match before reusing a cursor, including after another process updates it.
_PROJECTIONS: OrderedDict[Path, _Projection] = OrderedDict()
# Definition content is expensive (host sources and registry); validate the cheap
# signature on every request, then compute these identities once per signature.
_DEFINITIONS: OrderedDict[Path, tuple[list[Any], dict[str, Any]]] = OrderedDict()
# Strict parsing is needed only when the small manifest's exact bytes change.
_MANIFESTS: OrderedDict[Path, tuple[str, str]] = OrderedDict()


def _manifest_signature(path: Path) -> str | None:
    try:
        with (path / "manifest.json").open("rb") as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
    except FileNotFoundError:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    with _LOCK:
        cached = _MANIFESTS.get(path)
        if cached is None or cached[0] != digest:
            try:
                metadata = parse_json(raw)
                if not isinstance(metadata, dict):
                    raise ValueError("Manifest must be an object")
                # Include field presence: an absent kernel identity is allowed,
                # whereas an explicit null must agree with the journal header.
                identity = [[key, metadata[key]] for key in (
                    "id", "kernel_run_id", "epoch", "configuration_digest",
                    "journal_codec", "created_at",
                ) if key in metadata]
                fingerprint = hashlib.sha256(json.dumps(
                    identity, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")).hexdigest()
            except (KernelError, ValueError, TypeError, RecursionError):
                fingerprint = "reader-rejected"
            _MANIFESTS[path] = (digest, fingerprint)
        _MANIFESTS.move_to_end(path)
        while len(_MANIFESTS) > 4:
            _MANIFESTS.popitem(last=False)
        return _MANIFESTS[path][1]


def _projection_signature(signature: list[Any]) -> list[Any]:
    """Manifest admission/identity changes never invalidate reducer progress."""
    return [item for item in signature if item[0] != "provenance-manifest"]


def _retain(path: Path, projection: _Projection) -> None:
    with _LOCK:
        _PROJECTIONS[path] = projection
        _PROJECTIONS.move_to_end(path)
        while len(_PROJECTIONS) > 4:
            _PROJECTIONS.popitem(last=False)


def _path_lock(path: Path) -> threading.RLock:
    with _LOCK:
        return _PATH_LOCKS.setdefault(path, threading.RLock())


def _signature(
    path: Path, *, plugins: tuple[PresentationPlugin, ...] | None = None,
) -> list[Any]:
    from aeroagentsim import metrics_api

    authored = [path / "scenario.json"]
    if (path / "behaviour.ir.json").exists():
        authored.append(path / "behaviour.ir.json")
    files = [*authored, path / "runtime.registry.json"]
    base = Path(__file__).resolve().parents[1]
    files.extend(base / name for name in _IMPLEMENTATION_FILES)
    metric_plugins = metrics_api.discover_metric_plugins()
    result: list[Any] = [FORMAT, ["metrics", metrics_api.metric_plugin_fingerprint(metric_plugins)]]
    # Preserve presentation admission and exact authored-content checks on hits;
    # stats alone miss equal-length edits with restored mtimes.
    plugins, snapshot = presentation_selection(path, plugins)
    result.append(["narratives", snapshot["digest"] if snapshot is not None
                   else presentation_plugin_digest(plugins)])
    result.append(["authored", [
        [str(file), hashlib.sha256(file.read_bytes()).hexdigest()] for file in authored
    ]])
    pins = path / PROJECTION_PINS_FILENAME
    result.append(["projection-pins", hashlib.sha256(pins.read_bytes()).hexdigest()
                   if pins.exists() else None])
    for file in files:
        stat = file.stat()
        result.append([str(file), stat.st_size, stat.st_mtime_ns, stat.st_ino,
                       stat.st_dev, stat.st_ctime_ns])
    journal = path / "journal.jsonl"
    if journal.exists():
        stat = journal.stat()
        result.append([str(journal), stat.st_dev, stat.st_ino])
    # Reader-only container restrictions must not narrow service admission. If a
    # container changes (including introduction/removal of an ACK or symlink),
    # invalidate any retained admission/omission. ACK coordinates are deliberately
    # excluded: append-only growth must not re-admit the header or replay offsets.
    info = path.stat()
    containers: list[Any] = [[info.st_dev, info.st_ino]]
    for name, bound in (("journal.jsonl", None), ("manifest.json", 8 * 1024 * 1024),
                        ("committed-prefix.json", 4096)):
        source = path / name
        try:
            info = source.lstat()
            containers.append([name, S_IFMT(info.st_mode),
                               bound is not None and info.st_size > bound])
        except FileNotFoundError:
            containers.append([name, None])
    result.append(["provenance-source", containers])
    with _LOCK:
        cached = _DEFINITIONS.get(path)
        if cached is None or cached[0] != result:
            definitions = projection_definitions(path, presentation_plugins=plugins)
            _DEFINITIONS[path] = (result, definitions)
        else:
            definitions = cached[1]
        _DEFINITIONS.move_to_end(path)
        while len(_DEFINITIONS) > 4:
            _DEFINITIONS.popitem(last=False)
    # Keep reader admission separate from definition/reducer invalidation. A
    # status-only rewrite hashes/validates the manifest but retains this identity.
    return [*result, ["definitions", definitions],
            ["provenance-manifest", _manifest_signature(path)]]


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path / FILENAME, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS cuts "
        "(idx INTEGER PRIMARY KEY, at_ns TEXT NOT NULL, payload BLOB NOT NULL, "
        "kpis TEXT NOT NULL, cut_bytes INTEGER)"
    )
    # Older rebuildable indexes predate the internal ACK byte-bound column.
    if "cut_bytes" not in {row[1] for row in connection.execute("PRAGMA table_info(cuts)")}:
        # The first check keeps migrated readers cheap. Another process may have
        # migrated since it ran, so acquire the writer lock BEFORE checking again.
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if "cut_bytes" not in {row[1] for row in connection.execute("PRAGMA table_info(cuts)")}:
                connection.execute("ALTER TABLE cuts ADD COLUMN cut_bytes INTEGER")
        # Commit the migration before the updater opens its own write transaction.
    connection.execute(
        "CREATE TABLE IF NOT EXISTS checkpoint "
        "(id INTEGER PRIMARY KEY CHECK(id=1), data BLOB NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS projection_progress "
        "(id INTEGER PRIMARY KEY CHECK(id=1), next INTEGER NOT NULL, signature TEXT NOT NULL)"
    )
    return connection


def _encode(value: Any) -> bytes:
    return zlib.compress(
        json.dumps(value, separators=(",", ":"), allow_nan=False).encode(), 1
    )


def _decode(value: bytes) -> Any:
    return json.loads(zlib.decompress(value))


def _results_state(path: Path, metadata: dict[str, Any], checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    final_cursor = metadata.get("final_cursor")
    cursor = checkpoint["next"] if checkpoint is not None else None
    prefix = committed_prefix(path)
    final = (
        metadata["status"] in TERMINAL
        and type(final_cursor) is int and final_cursor >= 1
        and cursor == final_cursor
        and (prefix is None or prefix["index"] + 1 == final_cursor)
    )
    return {"results_final": final, "results_cursor": cursor,
            "final_commit_index": final_cursor - 1 if type(final_cursor) is int else None}


def results_state(path: Path, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the committed KPI/Story checkpoint without starting aggregation.

    Both projectors and cuts are published in the same SQLite transaction.
    A missing or obsolete cache is unfinished, never evidence of final results.
    """
    path = path.resolve()
    metadata = RunStorage(path).metadata() if metadata is None else metadata
    checkpoint = None
    database_path = path / FILENAME
    if database_path.exists():
        with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as database:
            exists = database.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='projection_progress'"
            ).fetchone()
            row = database.execute("SELECT next, signature FROM projection_progress WHERE id=1").fetchone() if exists else None
            if row is not None:
                if json.loads(row[1]) == _signature(path):
                    checkpoint = {"next": row[0]}
    return _results_state(path, metadata, checkpoint)


def update(
    path: Path, *, through_index: int | None = None, max_batches: int | None = None
) -> bool:
    """Build bounded batches so first-page readers need only their requested cuts.

    Release the per-run lock between batches. A background tail or metrics read
    must not hold the interactive first page behind a whole journal rebuild.

    Background warmup can yield after a bounded amount of work. The return
    value reports whether this acknowledged prefix is completely projected.
    """
    if max_batches is not None and (type(max_batches) is not int or max_batches <= 0):
        raise ValueError("Viewer index: max_batches must be a positive integer")
    path = path.resolve()
    prefix = committed_prefix(path)
    if prefix is not None:
        available = prefix["index"] + 1
    else:
        storage = RunStorage(path)
        available = storage.entries[-1]["index"] + 1 if storage.entries else 1
    target = min(available, through_index) if through_index is not None else available
    batches = 0
    while True:
        complete = _update_batch(path, target)
        batches += 1
        if complete or (max_batches is not None and batches >= max_batches):
            return complete


def _update_batch(path: Path, requested_target: int) -> bool:
    """Publish one complete checkpoint, at most 64 new journal records."""
    with _path_lock(path), closing(_connect(path)) as database, database:
        plugins, _ = presentation_selection(path)
        signature = _signature(path, plugins=plugins)
        signature_text = json.dumps(signature, separators=(",", ":"))
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT checkpoint.data, projection_progress.next, projection_progress.signature "
            "FROM checkpoint LEFT JOIN projection_progress ON projection_progress.id=checkpoint.id "
            "WHERE checkpoint.id=1"
        ).fetchone()
        # Remove the working cursor before mutation. A projection/SQLite failure
        # cannot leave an advanced in-memory cursor attached to the old prefix.
        with _LOCK:
            cached = _PROJECTIONS.pop(path, None)
        if cached is not None and (row is None or cached.checkpoint != row[0]):
            cached = None
        # Live readers retain their byte cursor. Recopying the whole JSON index
        # on every SSE poll makes long runs progressively slower.
        storage = (
            cached.storage
            if cached is not None
            and _projection_signature(cached.signature) == _projection_signature(signature)
            and committed_prefix(path) is not None
            else RunStorage(path)
        )
        storage.refresh_committed()
        target = storage.entries[-1]["index"] + 1 if storage.entries else 1
        if storage.entries:
            tail = storage.entries[-1]
            if (path / "journal.jsonl").stat().st_size < tail["offset"] + tail["length"]:
                raise ValueError("Viewer index: WAL is shorter than its retained prefix")
        # Progress and checkpoint/cuts are published in one transaction. Check
        # this uncompressed identity before inflating a checkpoint, including on
        # reconnects or working-set eviction. Old indexes without progress fall
        # through to checkpoint validation and repair below. Keep the byte check
        # above: a retained mutable cursor must still match the durable checkpoint.
        if (row is not None and row[2] == signature_text
                and min(target, requested_target) <= row[1] <= target):
            if cached is not None:
                _retain(path, cached)
            return True
        state: dict[str, Any] = (
            {"signature": cached.signature, "next": cached.next}
            if cached is not None
            else _decode(row[0])
            if row
            else {}
        )
        valid = (
            bool(state)
            and _projection_signature(state["signature"]) == _projection_signature(signature)
            and state["next"] <= target
        )
        admission_changed = valid and state["signature"] != signature
        if valid and not admission_changed and state["next"] >= min(target, requested_target):
            progress = (state["next"], signature_text)
            if row[1:] != progress:
                database.execute("INSERT OR REPLACE INTO projection_progress VALUES (1, ?, ?)", progress)
            if cached is not None:
                _retain(path, cached)
            return True
        if valid and cached is not None:
            subjects, kpis, narratives = cached.subjects, cached.kpis, cached.narratives
        else:
            registry = MemoryRegistry.from_data(
                json.loads((path / "runtime.registry.json").read_text())
            )
            document = json.loads((path / "scenario.json").read_text())
            subjects = SubjectProjection(registry, document)
            # Source declarations can invalidate reducer progress without changing
            # the immutable journal header. Retain only its routing/source context;
            # metric state must still rebuild from the first committed record.
            journal_identity = next(
                (item for item in signature if item[0] == str(path / "journal.jsonl")),
                None,
            )
            message_context = None
            if journal_identity is not None and journal_identity in state.get("signature", []):
                message_context = (
                    cached.kpis.message_context if cached is not None
                    else state.get("kpis", {}).get("message_context")
                )
            kpis = KPIProjector(
                registry, document.get("presentation", []), message_context=message_context,
            )
            if not valid and message_context is None and storage.entries:
                # Bootstrap a new journal only. Valid checkpoints restore their
                # header context below without a redundant record-zero read.
                for header in storage.records(0, 1):
                    kpis.consume(header)
            narratives = NarrativeProjector.from_directory(path, plugins=plugins)
        if valid:
            cursor = state["next"]
            if cached is None:
                subjects.restore_cursor(state["subjects"])
                kpis.restore_cursor(state["kpis"])
                narratives.restore_cursor(state["narratives"])
        else:
            cursor = 1
            database.execute("DELETE FROM cuts")
            database.execute("DELETE FROM checkpoint")
        previous_provenance = (
            cached.provenance if valid and cached is not None
            else state.get("projection_provenance") if valid
            else None
        )
        provenance = (
            previous_provenance if valid and not admission_changed
            else _new_provenance(path, signature, storage)
        )
        # Hash full definitions only on admission, not for each cut or warm page.
        definition_digests = (
            cached.definition_digests if valid and cached is not None
            else state.get("projection_definition_digests") if valid
            else None
        )
        if provenance is not None and definition_digests is None:
            definition_digests = {
                "pinned_digest": _definitions_digest(provenance["pinned_definitions"]),
                "current_digest": _definitions_digest(provenance["current_definitions"]),
            }
        if admission_changed:
            if provenance is not None:
                # Admission starts at the header; keep the DTO at the existing
                # projected cut even when there is no new tail to consume.
                entry = storage.entries[cursor - 1]
                provenance["source_cut"].update(
                    cut_index=entry["index"], cut_bytes=entry["offset"] + entry["length"],
                )
            if (previous_provenance is None) != (provenance is None):
                # Compact refs contain no manifest identities. Only an admission
                # transition needs a one-off rewrite, never reducer/WAL replay.
                _refresh_cut_provenance(database, storage, provenance, definition_digests)
        target = min(target, requested_target, cursor + 64)
        while cursor < target:
            records = storage.records(cursor, min(256, target - cursor))
            if not records:
                # records() takes its own index snapshot. A final-tail publisher
                # can interleave with that read; reacquire the source once before
                # diagnosing a genuinely missing retained prefix.
                retry_storage = RunStorage(path)
                retry_storage.refresh_committed()
                records = retry_storage.records(cursor, min(256, target - cursor))
                if not records:
                    raise ValueError("Viewer index: retained WAL prefix is missing")
                storage = retry_storage
            for record in records:
                if record["index"] != cursor:
                    raise ValueError("Viewer index: non-contiguous committed prefix")
                record = projection_record(record)
                cut = project(record, subjects=subjects, kpis=kpis)
                cut["events"] = narratives.consume(record)
                cut_bytes = None
                if provenance is not None:
                    entry = storage.entries[cursor]
                    if entry["index"] != cursor:
                        raise ValueError("Projection provenance: offset index differs from projected cut")
                    cut_bytes = entry["offset"] + entry["length"]
                    provenance = {
                        **provenance,
                        "source_cut": {
                            **provenance["source_cut"], "cut_index": cursor,
                            "cut_bytes": cut_bytes,
                        },
                    }
                    cut["projection_provenance"] = {
                        "format": provenance["format"],
                        "mode": provenance["mode"],
                        "cut_index": cursor,
                        "definitions": definition_digests,
                    }
                database.execute(
                    "INSERT INTO cuts (idx, at_ns, payload, kpis, cut_bytes) VALUES (?, ?, ?, ?, ?)",
                    (
                        cursor,
                        cut["at"]["ns"],
                        _encode(cut),
                        json.dumps(cut["kpis"], separators=(",", ":")),
                        cut_bytes,
                    ),
                )
                cursor += 1
        checkpoint = _encode(
            {
                "signature": signature,
                "next": cursor,
                "subjects": subjects.cursor(),
                "kpis": kpis.cursor(),
                "narratives": narratives.cursor(),
                "final": kpis.snapshot(),
                "projection_provenance": provenance,
                "projection_definition_digests": definition_digests,
            }
        )
        database.execute(
            "INSERT OR REPLACE INTO checkpoint VALUES (1, ?)", (checkpoint,)
        )
        # Status polls read this small coordinate instead of inflating the full
        # subject/KPI/narrative checkpoint. Publish it in the same transaction.
        database.execute("INSERT OR REPLACE INTO projection_progress VALUES (1, ?, ?)",
                         (cursor, signature_text))
        database.commit()
        _retain(
            path, _Projection(
                checkpoint, signature, cursor, subjects, kpis, narratives, storage,
                provenance, definition_digests,
            )
        )
        return cursor >= requested_target


def _new_provenance(
    path: Path, signature: list[Any], storage: RunStorage,
) -> dict[str, Any] | None:
    """Admit once per signature; publish metadata with cuts in their transaction.

    Reuse the updater's incremental offsets. Warm REST/SSE/KPI reads never open a
    second storage or reparse the header merely to attach optional metadata.
    Service-readable sources outside reader v1 admission retain their old payloads.
    """
    if committed_prefix(path) is None or not storage.entries:
        return None
    try:
        with open_run(path) as run:
            header = run.header
    except (RunReadError, KernelError):
        # Strict manifest JSON/budget admission can also raise kernel decode
        # errors. This try covers reader admission only, never pin validation.
        return None
    definitions = next(item[1] for item in signature if item[0] == "definitions")
    entry = storage.entries[0]
    provenance = _provenance_for_cut(
        path, header, entry["index"], entry["offset"] + entry["length"], definitions,
    )
    payload = asdict(provenance)
    payload["mode"] = provenance.mode.value
    return payload


def _refresh_cut_provenance(
    database: sqlite3.Connection, storage: RunStorage,
    provenance: dict[str, Any] | None, definition_digests: dict[str, str | None] | None,
) -> None:
    """Repair optional refs once on admission changes, in the checkpoint transaction."""
    for idx, payload in database.execute("SELECT idx, payload FROM cuts ORDER BY idx"):
        cut = _decode(payload)
        cut_bytes = None
        if provenance is None:
            cut.pop("projection_provenance", None)
        else:
            entry = storage.entries[idx]
            if entry["index"] != idx:
                raise ValueError("Projection provenance: offset index differs from projected cut")
            cut_bytes = entry["offset"] + entry["length"]
            cut["projection_provenance"] = {
                "format": provenance["format"],
                "mode": provenance["mode"],
                "cut_index": idx,
                "definitions": definition_digests,
            }
        database.execute(
            "UPDATE cuts SET payload=?, cut_bytes=? WHERE idx=?", (_encode(cut), cut_bytes, idx),
        )


def stream_page(path: Path, start: int = 1, limit: int = 32) -> list[dict[str, Any]]:
    """Emit a ready committed fragment before computing subsequent live cuts."""
    path = path.resolve()
    prefix = committed_prefix(path)
    database_path = path / FILENAME
    if prefix is not None and database_path.exists():
        # WAL readers can consume the already committed derived prefix while
        # a writer projects later cuts. Taking its write lock first defeats
        # bounded batches: every live subscriber queues behind future work.
        # Validate the source signature and clip to the actual kernel ACK.
        with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as database:
            # Signature validation and the cut SELECT must share a snapshot: a
            # concurrent rebuild may replace both between these two reads.
            database.execute("BEGIN")
            tables = {
                item[0] for item in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('cuts','checkpoint','projection_progress')"
                )
            }
            row = (
                database.execute(
                    "SELECT projection_progress.signature FROM projection_progress "
                    "JOIN checkpoint ON checkpoint.id=projection_progress.id "
                    "WHERE projection_progress.id=1"
                ).fetchone()
                if tables == {"cuts", "checkpoint", "projection_progress"} else None
            )
            if row is not None and row[0] == json.dumps(_signature(path), separators=(",", ":")):
                rows = database.execute(
                    "SELECT payload, cut_bytes FROM cuts WHERE idx>=? AND idx<=? ORDER BY idx LIMIT ?",
                    (max(1, start), prefix["index"], limit),
                ).fetchall()
                if rows and (path / "journal.jsonl").stat().st_size >= prefix["bytes"]:
                    # Source coordinates stay internal; cuts carry only content refs.
                    if all(cut_bytes is None or cut_bytes <= prefix["bytes"]
                           for _, cut_bytes in rows):
                        return [_decode(payload) for payload, _ in rows]
    return page(path, start, limit)


def page(path: Path, start: int = 1, limit: int = 256) -> list[dict[str, Any]]:
    """Project the requested acknowledged prefix for REST/history readers."""
    path = path.resolve()
    update(path, through_index=max(1, start) + limit)
    with closing(_connect(path)) as database, database:
        # A single SELECT already has a consistent SQLite snapshot.
        return [
            _decode(row[0])
            for row in database.execute(
                "SELECT payload FROM cuts WHERE idx>=? ORDER BY idx LIMIT ?",
                (max(1, start), limit),
            )
        ]


def kpi_result(path: Path, *, include_series: bool = True) -> dict[str, Any]:
    update(path)
    with closing(_connect(path)) as database, database:
        database.execute("BEGIN")
        row = database.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Viewer index: checkpoint missing after update")
        checkpoint = _decode(row[0])
        final = checkpoint["final"]
        series = (
            [
                {"commitIndex": idx, "at_ns": at_ns, "kpis": json.loads(kpis)}
                for idx, at_ns, kpis in database.execute(
                    "SELECT idx, at_ns, kpis FROM cuts ORDER BY idx"
                )
            ]
            if include_series
            else []
        )
        # Summary-only results are deterministic numerical payloads shared by
        # service and headless hosts. Keep run-local reader identities/pin modes
        # outside this legacy byte-stable view; full KPI responses expose them.
        provenance = checkpoint.get("projection_provenance") if include_series else None
        return {"contract": "aeroagentsim.kpi/v1", "final": final, "series": series,
                **_results_state(path, RunStorage(path).metadata(), checkpoint),
                **({"projection_provenance": provenance} if provenance is not None else {})}
