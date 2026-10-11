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
from .projector import project
from .storage import RunStorage
from .subjects import SubjectProjection

FORMAT = "aeroagentsim.viewer-index/v1"
FILENAME = "viewer-index.sqlite3"
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
    files = [path / "scenario.json", path / "runtime.registry.json"]
    if (path / "behaviour.ir.json").exists():
        files.append(path / "behaviour.ir.json")
    files.extend(
        Path(__file__).with_name(name + ".py")
        for name in ("feed_index", "projector", "subjects", "kpis", "narratives")
    )
    packs = Path(__file__).parents[1] / "packs"
    files.extend(
        (packs / "lowalt/overlay.py", packs / "traffic_accident/narratives.py")
    )
    result: list[Any] = [FORMAT]
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
    return connection


def _encode(value: Any) -> bytes:
    return zlib.compress(
        json.dumps(value, separators=(",", ":"), allow_nan=False).encode(), 1
    )


def _decode(value: bytes) -> Any:
    return json.loads(zlib.decompress(value))


def update(path: Path, *, through_index: int | None = None) -> None:
    """Build bounded batches so first-page readers need only their requested cuts.

    Release the per-run lock between batches. A background tail or metrics read
    must not hold the interactive first page behind a whole journal rebuild.
    """
    path = path.resolve()
    storage = RunStorage(path)
    available = storage.entries[-1]["index"] + 1 if storage.entries else 1
    target = min(available, through_index) if through_index is not None else available
    while not _update_batch(path, target):
        pass


def _update_batch(path: Path, requested_target: int) -> bool:
    """Publish one complete checkpoint, at most 256 new journal records."""
    with _path_lock(path), closing(_connect(path)) as database, database:
        storage = RunStorage(path)
        target = storage.entries[-1]["index"] + 1 if storage.entries else 1
        signature = _signature(path)
        if storage.entries:
            tail = storage.entries[-1]
            if (path / "journal.jsonl").stat().st_size < tail["offset"] + tail[
                "length"
            ]:
                raise ValueError(
                    "Viewer index: WAL is shorter than its retained prefix"
                )
        database.execute("BEGIN IMMEDIATE")
        row = database.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()
        # Remove the working cursor before mutation. A projection/SQLite failure
        # cannot leave an advanced in-memory cursor attached to the old prefix.
        with _LOCK:
            cached = _PROJECTIONS.pop(path, None)
        if cached is not None and (row is None or cached.checkpoint != row[0]):
            cached = None
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
            kpis = KPIProjector(registry, document.get("presentation", []))
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
        target = min(target, requested_target, cursor + 256)
        while cursor < target:
            records = storage.records(cursor, min(256, target - cursor))
            if not records:
                # records() takes its own index snapshot. A final-tail publisher
                # can interleave with that read; reacquire the source once before
                # diagnosing a genuinely missing retained prefix.
                records = RunStorage(path).records(cursor, min(256, target - cursor))
                if not records:
                    raise ValueError("Viewer index: retained WAL prefix is missing")
            for record in records:
                if record["index"] != cursor:
                    raise ValueError("Viewer index: non-contiguous committed prefix")
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
        database.commit()
        _retain(
            path, _Projection(checkpoint, signature, cursor, subjects, kpis, narratives)
        )
        return cursor >= requested_target


def page(path: Path, start: int = 1, limit: int = 256) -> list[dict[str, Any]]:
    """Random-access projected cuts, including reconnects in a fresh process."""
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
        row = database.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Viewer index: checkpoint missing after update")
        final = _decode(row[0])["final"]
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
        return {"contract": "aeroagentsim.kpi/v1", "final": final, "series": series}
