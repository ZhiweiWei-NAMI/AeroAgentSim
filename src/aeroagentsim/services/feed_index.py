"""Rebuildable SQLite viewer cuts and resumable committed-state checkpoints.

The journal and its pinned declarations remain authoritative. A transaction
publishes cuts and all three projector cursors together, so readers never see
a cut whose lifecycle, KPI or narrative state belongs to another prefix.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import zlib
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aerokernel.registry import MemoryRegistry

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


# A bounded working set, never the source of truth. The committed SQLite blob
# must match before reusing a cursor, including after another process updates it.
_PROJECTIONS: OrderedDict[Path, _Projection] = OrderedDict()


def _retain(path: Path, projection: _Projection) -> None:
    with _LOCK:
        _PROJECTIONS[path] = projection
        _PROJECTIONS.move_to_end(path)
        while len(_PROJECTIONS) > 4:
            _PROJECTIONS.popitem(last=False)


def _path_lock(path: Path) -> threading.RLock:
    with _LOCK:
        return _PATH_LOCKS.setdefault(path, threading.RLock())


def _signature(path: Path) -> list[Any]:
    import hashlib

    from aeroagentsim import metrics_api
    from aeroagentsim.runs import reader as run_reader

    files = [path / "scenario.json", path / "runtime.registry.json"]
    if (path / "behaviour.ir.json").exists():
        files.append(path / "behaviour.ir.json")
    files.extend(
        Path(__file__).with_name(name + ".py")
        for name in ("feed_index", "projector", "subjects", "kpis", "narratives")
    )
    files.extend((Path(metrics_api.__file__), Path(run_reader.__file__)))
    # Every eligible provider is retained for schema-prefix dispatch, including
    # external providers whose first matching message arrives after this cut.
    # Revisions, entry-point identity and descriptors replace pack source paths.
    plugins = metrics_api.discover_metric_plugins()
    result: list[Any] = [FORMAT, ["metrics", metrics_api.metric_plugin_fingerprint(plugins)]]
    # Removing the old pack-file list must not drop narrative invalidation. Hash
    # the effective declarations (including pack/IR/scenario overrides), rather
    # than assuming two particular source filenames own every narrative.
    declarations = NarrativeProjector.from_directory(path)._declarations
    result.append(["narratives", hashlib.sha256(json.dumps(
        declarations, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()])
    for file in files:
        stat = file.stat()
        result.append([str(file), stat.st_size, stat.st_mtime_ns, stat.st_ino])
    journal = path / "journal.jsonl"
    if journal.exists():
        stat = journal.stat()
        result.append([str(journal), stat.st_dev, stat.st_ino])
    return result


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path / FILENAME, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS cuts "
        "(idx INTEGER PRIMARY KEY, at_ns TEXT NOT NULL, payload BLOB NOT NULL, "
        "kpis TEXT NOT NULL)"
    )
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
        signature = _signature(path)
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()
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
            if cached is not None and cached.signature == signature
            and committed_prefix(path) is not None
            else RunStorage(path)
        )
        storage.refresh_committed()
        target = storage.entries[-1]["index"] + 1 if storage.entries else 1
        if storage.entries:
            tail = storage.entries[-1]
            if (path / "journal.jsonl").stat().st_size < tail["offset"] + tail["length"]:
                raise ValueError("Viewer index: WAL is shorter than its retained prefix")
        state: dict[str, Any] = (
            {"signature": cached.signature, "next": cached.next}
            if cached is not None
            else _decode(row[0])
            if row
            else {}
        )
        valid = (
            bool(state) and state["signature"] == signature and state["next"] <= target
        )
        if valid and state["next"] >= min(target, requested_target):
            progress = (state["next"], json.dumps(signature, separators=(",", ":")))
            if database.execute("SELECT next, signature FROM projection_progress WHERE id=1").fetchone() != progress:
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
            narratives = NarrativeProjector.from_directory(path)
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
            for record in records:
                if record["index"] != cursor:
                    raise ValueError("Viewer index: non-contiguous committed prefix")
                record = projection_record(record)
                cut = project(record, subjects=subjects, kpis=kpis)
                cut["events"] = narratives.consume(record)
                database.execute(
                    "INSERT INTO cuts VALUES (?, ?, ?, ?)",
                    (
                        cursor,
                        cut["at"]["ns"],
                        _encode(cut),
                        json.dumps(cut["kpis"], separators=(",", ":")),
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
            }
        )
        database.execute(
            "INSERT OR REPLACE INTO checkpoint VALUES (1, ?)", (checkpoint,)
        )
        # Status polls read this small coordinate instead of inflating the full
        # subject/KPI/narrative checkpoint. Publish it in the same transaction.
        database.execute("INSERT OR REPLACE INTO projection_progress VALUES (1, ?, ?)",
                         (cursor, json.dumps(signature, separators=(",", ":"))))
        database.commit()
        _retain(
            path, _Projection(checkpoint, signature, cursor, subjects, kpis, narratives, storage)
        )
        return cursor >= requested_target


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
            database.execute("BEGIN")
            tables = {
                item[0] for item in database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('cuts','checkpoint')"
                )
            }
            row = (
                database.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()
                if tables == {"cuts", "checkpoint"} else None
            )
            if row is not None and _decode(row[0])["signature"] == _signature(path.resolve()):
                records = [
                    _decode(item[0])
                    for item in database.execute(
                        "SELECT payload FROM cuts WHERE idx>=? AND idx<=? ORDER BY idx LIMIT ?",
                        (max(1, start), prefix["index"], limit),
                    )
                ]
                if records:
                    return records
    return page(path, start, limit)


def page(path: Path, start: int = 1, limit: int = 256) -> list[dict[str, Any]]:
    """Project the requested acknowledged prefix for REST/history readers."""
    path = path.resolve()
    update(path, through_index=max(1, start) + limit)
    with closing(_connect(path)) as database, database:
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
        return {"contract": "aeroagentsim.kpi/v1", "final": final, "series": series,
                **_results_state(path, RunStorage(path).metadata(), checkpoint)}
