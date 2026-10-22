"""ACK-bounded local decoding, with only in-memory offsets and frame hashes."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from types import MappingProxyType
from typing import Any, BinaryIO

from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.errors import KernelError
from aerokernel.ids import ItemRef
from aerokernel.journal_codec import CODEC, LEAN_CODEC, decode_frame
from aerokernel.journal_stream import RecordReader
from aerokernel.messages import CommandRequest, Emit, Message, RequestCancel
from aerokernel.time import Instant, Stamp
from aerokernel.values import ResourceBudget, parse_json, thaw

from aeroagentsim.services.storage import committed_prefix

from .contracts import RUN_READER_API_VERSION, RecordedMessage, RunHeader, Snapshot
from .errors import (
    CorruptRecord,
    NoCommittedPrefix,
    RunReadError,
    RunReplaced,
    UnsupportedRunFormat,
)

# Bound bootstrap metadata even before the journal's own budgets are admitted.
_HEADER_BYTES = 8 * 1024 * 1024
_DECODE_ERRORS = (
    ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError,
)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _nonnegative(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _text(document: dict[str, Any], key: str) -> str | None:
    value = document.get(key)
    if value is not None and (type(value) is not str or not value):
        raise CorruptRecord(f"Invalid recorded {key}")
    return value


def _prefix(directory: Path) -> dict[str, int]:
    try:
        path = directory / "committed-prefix.json"
        if path.is_symlink():
            raise NoCommittedPrefix("Symlinked ACK sidecars are not supported")
        info = path.stat()
        if not S_ISREG(info.st_mode) or info.st_size > 4096:
            raise NoCommittedPrefix("ACK sidecar must be a small regular file")
        prefix = committed_prefix(directory)
    except (OSError, ValueError, RecursionError) as exc:
        raise NoCommittedPrefix("Invalid or unreadable acknowledged prefix") from exc
    if prefix is None or prefix["bytes"] == 0:
        raise NoCommittedPrefix("A nonempty acknowledged prefix is required")
    return prefix


def _admit_header(header: dict[str, Any]) -> str:
    """Admit known semantic families without constructing or replaying a kernel."""
    if header.get("type") != "header":
        raise CorruptRecord("Record zero is not a journal header")
    if header.get("format") != "aerokernel.journal":
        raise UnsupportedRunFormat("Unsupported journal format")
    major, minor = header.get("major"), header.get("minor")
    if type(major) is not int or type(minor) is not int:
        raise UnsupportedRunFormat("Journal versions must be integers")
    lean = (major, minor) in {(1, 4), (2, 1)}
    if lean:
        if (
            header.get("provenance") != "lean"
            or type(header.get("policy_version")) is not int
            or header["policy_version"] not in {2, 3}
        ):
            raise UnsupportedRunFormat("Unsupported lean provenance policy")
    elif "provenance" in header or "policy_version" in header:
        raise UnsupportedRunFormat("Unsupported full provenance policy")
    if major == 1:
        if (
            minor not in {1, 2, 3, 4}
            or "codec" in header or "semantic_version" in header
        ):
            raise UnsupportedRunFormat("Unsupported uncompressed journal version")
    elif major == 2:
        if (
            minor not in {0, 1}
            or header.get("codec") != (LEAN_CODEC if lean else CODEC)
            or type(header.get("semantic_version")) is not int
            or header["semantic_version"] not in ({4} if lean else {2, 3})
        ):
            raise UnsupportedRunFormat("Unsupported compressed journal version/codec")
    else:
        raise UnsupportedRunFormat("Unsupported journal major version")
    policy = (
        header["policy_version"] if lean
        else header["semantic_version"] if major == 2 else minor
    )
    required = {
        "type", "format", "major", "minor", "index", "instant", "time_origin_ns",
        "run_id", "epoch", "registry", "registry_digest", "manifest", "partitions",
        "mappings", "budget", "max_microsteps", "durability", "root_seed",
        "configuration", "serializer", "rng", "resolved_bindings", "engine_versions",
    }
    if policy in {2, 3}:
        required.update({"ingress_policy", "engine_profiles"})
    if policy == 3:
        required.add("ingress_streams")
    if major == 2:
        required.update({"codec", "semantic_version"})
    if lean:
        required.update({"provenance", "policy_version"})
    if set(header) != required:
        raise CorruptRecord("Malformed header fields for the admitted journal format")
    try:
        instant = decode_record(header["instant"])
    except _DECODE_ERRORS as exc:
        raise CorruptRecord("Invalid journal header instant") from exc
    if (
        type(header["index"]) is not int or header["index"] != 0
        or type(header["time_origin_ns"]) is not int or header["time_origin_ns"] != 0
        or not isinstance(instant, Instant) or instant != Instant(0)
    ):
        raise CorruptRecord("Journal header must start at index/time zero")
    # Missing identity is corrupt for all admitted formats, not an invitation to
    # infer it from the directory name or from a message's formatted ID.
    if _text(header, "run_id") is None or _text(header, "epoch") is None:
        raise CorruptRecord("Journal header lacks recorded run identity")
    return "lean" if lean else "full"


def _reference(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    ref = decode_record(value)
    if not isinstance(ref, ItemRef):
        raise ValueError("Expected a recorded item reference")
    return asdict(ref)


def _stamp(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    stamp = decode_record(value)
    if not isinstance(stamp, Stamp):
        raise ValueError("Expected a recorded source stamp")
    return asdict(stamp)


def _request(value: Any) -> CommandRequest:
    request = decode_record(value)
    if not isinstance(request, CommandRequest):
        raise ValueError("Expected a recorded command request")
    return request


@dataclass(frozen=True)
class _Frame:
    offset: int
    length: int
    digest: bytes


class Run:
    """Read-only owner of context-scoped snapshots; use open_run to construct it."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self._closed = False
        self._streams: set[BinaryIO] = set()
        self._retained_frames: tuple[_Frame, ...] = ()
        prefix = _prefix(self.directory)
        self._path = self.directory / "journal.jsonl"
        try:
            info = self.directory.stat()
            if not S_ISDIR(info.st_mode):
                raise NoCommittedPrefix("Run source is not a directory")
            self._directory_identity = _identity(info)
            if self._path.is_symlink():
                raise UnsupportedRunFormat("Symlinked journals are not supported")
            if not S_ISREG(self._path.stat().st_mode):
                raise UnsupportedRunFormat("Run journal must be a regular file")
            with self._path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                if not S_ISREG(info.st_mode):
                    raise UnsupportedRunFormat("Run journal must be a regular file")
                self._journal_identity = _identity(info)
                if info.st_size < prefix["bytes"]:
                    raise NoCommittedPrefix("Acknowledged prefix exceeds the journal")
                line = stream.readline(min(prefix["bytes"], _HEADER_BYTES + 1))
        except OSError as exc:
            raise NoCommittedPrefix("Acknowledged journal is unavailable") from exc
        if len(line) > _HEADER_BYTES:
            raise CorruptRecord("Journal header exceeds the reader bootstrap limit")
        if not line.endswith(b"\n"):
            raise NoCommittedPrefix("Acknowledged prefix lacks a complete header")
        try:
            with RecordReader(line) as reader:
                header = next(reader)
                assert reader.budget is not None
                self._budget = reader.budget
        except KernelError as exc:
            if exc.code == "JOURNAL_HEADER" and "unsupported" in str(exc):
                raise UnsupportedRunFormat("Unsupported journal header/codec") from exc
            raise CorruptRecord("Invalid journal header") from exc
        except (*_DECODE_ERRORS, StopIteration) as exc:
            raise CorruptRecord("Invalid journal header") from exc
        provenance = _admit_header(header)
        self._header_line = line
        self._major = header["major"]
        self._lean = provenance == "lean"
        self._frame_budget = (
            ResourceBudget(
                self._budget.integer_digits,
                self._budget.frame_bytes,
                self._budget.nesting_depth * 2 + 2,
            )
            if self._lean else self._budget
        )
        metadata = self._metadata()
        identities = (("kernel_run_id", header["run_id"]), ("epoch", header["epoch"]))
        for key, recorded in identities:
            if key in metadata and _text(metadata, key) != recorded:
                raise CorruptRecord("Service and journal run identities disagree")
        manifest = header.get("manifest", {})
        if not isinstance(manifest, dict):
            raise CorruptRecord("Invalid recorded binding manifest")
        self._ingress_source = _text(manifest, "ingress_source")
        self._control_source = _text(manifest, "control_source")
        partitions = header.get("partitions")
        if not isinstance(partitions, list) or any(
            not isinstance(entry, dict)
            or entry.get("$type") != "Partition"
            or not isinstance(entry.get("fields"), dict)
            or type(entry["fields"].get("id")) is not str
            or not entry["fields"]["id"]
            for entry in partitions
        ):
            raise CorruptRecord("Invalid recorded partition declarations")
        self._partitions = frozenset(entry["fields"]["id"] for entry in partitions)
        versions = header.get("engine_versions")
        if versions is not None and (
            not isinstance(versions, dict)
            or any(
                type(k) is not str or type(v) is not str
                for k, v in versions.items()
            )
        ):
            raise CorruptRecord("Invalid recorded engine versions")
        self._header = RunHeader(
            service_run_id=_text(metadata, "id"),
            kernel_run_id=header["run_id"],
            epoch=header["epoch"],
            journal_format=header["format"],
            journal_major=header["major"],
            journal_minor=header["minor"],
            journal_codec=_text(header, "codec") or _text(metadata, "journal_codec"),
            semantic_version=header.get("semantic_version"),
            policy_version=header.get("policy_version"),
            provenance=provenance,
            configuration_digest=_text(metadata, "configuration_digest"),
            registry_digest=_text(header, "registry_digest"),
            created_at=_text(metadata, "created_at"),
            journal_identity=self._journal_identity,
            engine_versions=(
                tuple(sorted(versions.items())) if versions is not None else None
            ),
        )
        # Check both path and descriptor identity, including replacement during open.
        with self._journal():
            pass

    @property
    def header(self) -> RunHeader:
        return self._header

    def _metadata(self) -> dict[str, Any]:
        path = self.directory / "manifest.json"
        try:
            if path.is_symlink():
                raise UnsupportedRunFormat("Symlinked manifests are not supported")
            if not S_ISREG(path.stat().st_mode):
                raise UnsupportedRunFormat("Service manifest must be a regular file")
            with path.open("rb") as stream:
                document = parse_json(stream.read(_HEADER_BYTES + 1))
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise RunReadError("Cannot read the recorded service manifest") from exc
        except _DECODE_ERRORS as exc:
            raise CorruptRecord("Invalid recorded service manifest") from exc
        if not isinstance(document, dict):
            raise CorruptRecord("Service manifest must be an object")
        return document

    def _check_open(self) -> None:
        if self._closed:
            raise RunReadError("Run context is closed")

    def _check_source(self, stream: BinaryIO, through_bytes: int = 0) -> None:
        self._check_open()
        if self._retained_frames:
            last = self._retained_frames[-1]
            through_bytes = max(through_bytes, last.offset + last.length)
        try:
            if (
                _identity(self.directory.stat()) != self._directory_identity
                or self._path.is_symlink()
                or _identity(self._path.stat()) != self._journal_identity
                or _identity(os.fstat(stream.fileno())) != self._journal_identity
                or os.fstat(stream.fileno()).st_size
                < max(through_bytes, len(self._header_line))
            ):
                raise RunReplaced("Run source identity or retained length changed")
            stream.seek(0)
            if stream.read(len(self._header_line)) != self._header_line:
                raise RunReplaced("The admitted journal header changed")
        except OSError as exc:
            raise RunReplaced("Run source is no longer available") from exc

    @contextmanager
    def _journal(self, through_bytes: int = 0) -> Iterator[BinaryIO]:
        self._check_open()
        try:
            with self._path.open("rb") as stream:
                self._check_source(stream, through_bytes)
                self._streams.add(stream)
                try:
                    yield stream
                finally:
                    self._streams.discard(stream)
        except FileNotFoundError as exc:
            raise RunReplaced("Run journal is no longer available") from exc
        except OSError as exc:
            raise RunReadError("Cannot read the run journal") from exc

    def _decode(self, line: bytes, index: int) -> dict[str, Any]:
        try:
            record = parse_json(line, self._frame_budget)
            if not isinstance(record, dict):
                raise ValueError("Record must be an object")
            if self._major == 2 and index != 0:
                record = decode_frame(record, self._budget, lean=self._lean)
            if type(record.get("index")) is not int or record["index"] != index:
                raise ValueError("Non-contiguous journal record index")
            if index != 0 and (
                not isinstance(record.get("items"), list)
                or any(not isinstance(item, dict) for item in record["items"])
                or type(record.get("type")) is not str
            ):
                raise ValueError("Invalid journal record shape")
            return expand_record(record)
        except _DECODE_ERRORS as exc:
            raise CorruptRecord(f"Cannot decode acknowledged record {index}") from exc

    def _messages(self, record: dict[str, Any]) -> Iterator[RecordedMessage]:
        """Inspect original item slots only, never dispatch/inbox/batch copies."""
        try:
            yield from self._message_items(record)
        except _DECODE_ERRORS as exc:
            raise CorruptRecord(f"Invalid message in record {record['index']}") from exc

    def _message_items(self, record: dict[str, Any]) -> Iterator[RecordedMessage]:
        items = record.get("items", [])
        originals: dict[int, Message] = {}
        for ordinal, item in enumerate(items):
            kind = item.get("kind")
            proposal = item.get("proposal")
            emission = (
                kind == "operation" and isinstance(proposal, dict)
                and proposal.get("$type") == "Emit"
            )
            has_value = kind == "enqueue" and "value" in item
            value = (
                item.get("message") if kind in {"operation", "ingress_publish"}
                else item.get("value") if has_value else None
            )
            if (
                emission or kind == "ingress_publish" or has_value
            ) and not isinstance(value, dict):
                raise ValueError("Original message envelope is missing or malformed")
            if isinstance(value, dict):
                message = Message.from_data(value)
                if (
                    not isinstance(message.at, Instant)
                    or not isinstance(message.available, Instant)
                    or (
                        message.origin is not None
                        and not isinstance(message.origin, ItemRef)
                    )
                    or (
                        message.source_stamp is not None
                        and not isinstance(message.source_stamp, Stamp)
                    )
                ):
                    raise ValueError("Invalid recorded message timing/origin")
                originals[ordinal] = message
        explicit_ids = {message.id for message in originals.values()}
        for ordinal, item in enumerate(items):
            cursor = (record["index"], ordinal)
            if ordinal in originals:
                message = originals[ordinal]
                route = None
                if item.get("kind") == "operation":
                    proposal = decode_record(item.get("proposal"))
                    if not isinstance(proposal, Emit):
                        raise ValueError("Recorded emission lacks its Emit proposal")
                    route = proposal.target_or_topic
                    if type(route) is not str or not route:
                        raise ValueError("Invalid recorded emission route")
                if route is None and message.kind in {"command", "cancel"}:
                    route = next(
                        (
                            row["recipient"] for row in items
                            if row.get("kind") == "enqueue"
                            and row.get("message") == message.id
                            and type(row.get("recipient")) is str
                        ),
                        None,
                    )
                target = (
                    route if message.kind in {"command", "cancel"}
                    or route in self._partitions else None
                )
                yield RecordedMessage(
                    cursor=cursor, message_id=message.id, kind=message.kind,
                    schema_id=message.schema_id, route=route, target=target,
                    topic=route if message.kind == "event" and target is None else None,
                    source_kind=message.source_kind, source=message.source,
                    origin=(
                        asdict(message.origin) if message.origin is not None else None
                    ),
                    at_ns=message.at.ns, microstep=message.at.microstep,
                    available_ns=message.available.ns,
                    available_microstep=message.available.microstep,
                    payload=thaw(message.payload), sequence=message.sequence,
                    source_stamp=(
                        asdict(message.source_stamp)
                        if message.source_stamp is not None else None
                    ),
                )
                continue
            kind = item.get("kind")
            mid = item.get("message_id")
            if kind in {"reservation", "offline_request", "cancel_reservation"}:
                if type(mid) is not str or not mid:
                    raise ValueError("Admission lacks a message identity")
                # Prefer an explicit Message in the SAME atomic record. Never
                # enrich an earlier reservation with evidence from a later cut.
                if mid in explicit_ids:
                    continue
                origin = _reference(item.get("origin"))
                sequence = item.get("sequence")
                if sequence is not None:
                    _nonnegative(sequence, "Recorded message sequence")
                if kind == "cancel_reservation":
                    yield RecordedMessage(
                        cursor=cursor, message_id=mid, kind="cancel",
                        source_kind="ingress", source=self._control_source,
                        origin=origin, sequence=sequence,
                    )
                else:
                    request = _request(item.get("request"))
                    yield RecordedMessage(
                        cursor=cursor, message_id=mid, kind="command",
                        schema_id=request.schema_id, route=request.target,
                        target=request.target, source_kind="ingress",
                        source=_text(item, "source") or self._ingress_source,
                        origin=origin, at_ns=request.at.ns,
                        microstep=request.at.microstep, payload=thaw(request.payload),
                        source_stamp=_stamp(item.get("source_stamp")),
                        sequence=sequence,
                    )
            elif kind == "enqueue" and type(item.get("message")) is str:
                # A partition cancellation records its new ID at enqueue, not a
                # complete Message. Only its explicit RequestCancel origin makes
                # this an original; ordinary delivery references are not messages.
                origin = _reference(item.get("origin"))
                if origin is None or origin["record_index"] != record["index"]:
                    continue
                index = origin["item_index"]
                if index >= len(items):
                    raise ValueError("Enqueue origin is outside its record")
                operation = items[index]
                proposal = operation.get("proposal")
                if (
                    not isinstance(proposal, dict)
                    or proposal.get("$type") != "RequestCancel"
                ):
                    continue
                if not isinstance(decode_record(proposal), RequestCancel):
                    raise ValueError("Invalid cancellation origin")
                route = _text(item, "recipient")
                yield RecordedMessage(
                    cursor=cursor, message_id=item["message"], kind="cancel",
                    route=route, target=route, origin=origin,
                    source_kind="partition", source=_text(operation, "partition"),
                )

    def snapshot(self) -> Snapshot:
        """Capture one ACK once; reconcile it without consulting disk indexes."""
        self._check_open()
        with self._journal():
            pass
        prefix = _prefix(self.directory)
        retained = self._retained_frames
        retained_bytes = retained[-1].offset + retained[-1].length if retained else 0
        if prefix["bytes"] < retained_bytes or prefix["index"] < len(retained) - 1:
            raise RunReplaced("Acknowledged cut regressed behind retained evidence")
        frames: list[_Frame] = []
        origins: dict[str, tuple[int, int]] = {}
        with self._journal() as stream:
            if os.fstat(stream.fileno()).st_size < prefix["bytes"]:
                raise NoCommittedPrefix("Acknowledged prefix exceeds the journal")
            offset = 0
            while offset < prefix["bytes"]:
                stream.seek(offset)
                cap = min(prefix["bytes"] - offset, self._budget.frame_bytes + 1)
                line = stream.readline(cap)
                index = len(frames)
                frame = _Frame(offset, len(line), hashlib.sha256(line).digest())
                if index < len(retained) and frame != retained[index]:
                    raise RunReplaced(
                        f"Previously captured journal record {index} changed"
                    )
                if len(line) > self._budget.frame_bytes:
                    raise CorruptRecord(
                        "Acknowledged frame exceeds its resource budget"
                    )
                if not line.endswith(b"\n"):
                    raise NoCommittedPrefix("Acknowledged prefix ends inside a record")
                if index == 0 and line != self._header_line:
                    raise RunReplaced("The admitted journal header changed")
                record = self._decode(line, index)
                for message in self._messages(record):
                    origins.setdefault(message.message_id, message.cursor)
                frames.append(frame)
                offset += len(line)
            if len(frames) - 1 != prefix["index"]:
                raise NoCommittedPrefix("ACK index differs from its byte coordinate")
            self._check_source(stream, prefix["bytes"])
        self._retained_frames = tuple(frames)
        source = _SnapshotReader(self, self._retained_frames, prefix["bytes"], origins)
        return Snapshot(self.header, prefix["index"], prefix["bytes"], source)

    def close(self) -> None:
        """Invalidate snapshots and close even suspended iteration streams."""
        self._closed = True
        for stream in tuple(self._streams):
            stream.close()
        self._streams.clear()

    def __enter__(self) -> Run:
        self._check_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class _SnapshotReader:
    def __init__(
        self, run: Run, frames: tuple[_Frame, ...], cut_bytes: int,
        origins: dict[str, tuple[int, int]],
    ) -> None:
        self._run = run
        self._frames = frames
        self._cut_bytes = cut_bytes
        self._origins = MappingProxyType(origins)

    def iter_records(
        self, start: int = 0, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        _nonnegative(start, "start")
        if limit is not None:
            _nonnegative(limit, "limit")
        self._run._check_open()
        return self._records(start, limit)

    def _records(self, start: int, limit: int | None) -> Iterator[dict[str, Any]]:
        end = (
            len(self._frames) if limit is None
            else min(len(self._frames), start + limit)
        )
        with self._run._journal(self._cut_bytes) as stream:
            for index in range(start, end):
                frame = self._frames[index]
                self._run._check_source(stream, self._cut_bytes)
                stream.seek(frame.offset)
                line = stream.read(frame.length)
                if hashlib.sha256(line).digest() != frame.digest:
                    raise RunReplaced(f"Captured journal record {index} changed")
                record = self._run._decode(line, index)
                self._run._check_source(stream, self._cut_bytes)
                yield record

    def iter_messages(
        self, kind: str | None = None, schema_prefix: str | None = None,
        start: int = 0,
    ) -> Iterator[RecordedMessage]:
        _nonnegative(start, "start")
        if kind is not None and type(kind) is not str:
            raise ValueError("kind must be a string or None")
        if schema_prefix is not None and type(schema_prefix) is not str:
            raise ValueError("schema_prefix must be a string or None")
        self._run._check_open()
        return self._messages(kind, schema_prefix, start)

    def _messages(
        self, kind: str | None, schema_prefix: str | None, start: int,
    ) -> Iterator[RecordedMessage]:
        for record in self.iter_records(start=start):
            for message in self._run._messages(record):
                self._run._check_open()
                # Canonical identity is fixed before paging and before filtering.
                if self._origins[message.message_id] != message.cursor:
                    continue
                if kind is not None and message.kind != kind:
                    continue
                if schema_prefix is not None and (
                    message.schema_id is None
                    or not message.schema_id.startswith(schema_prefix)
                ):
                    continue
                yield message


def open_run(directory: str | os.PathLike[str], *, api_version: int = 1) -> Run:
    """Open a local recorded run without executing code or writing source files."""
    if type(api_version) is not int or api_version != RUN_READER_API_VERSION:
        raise ValueError(f"Unsupported run reader API version: {api_version!r}")
    return Run(Path(directory))
