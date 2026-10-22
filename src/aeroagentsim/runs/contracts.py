"""Versioned envelopes with no service, projector or kernel types in their API."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .reader import _SnapshotReader

RUN_READER_API_VERSION = 1


@dataclass(frozen=True)
class RunHeader:
    """Pinned recorded identity and format; unavailable metadata is None."""

    service_run_id: str | None
    kernel_run_id: str | None
    epoch: str | None
    journal_format: str
    journal_major: int
    journal_minor: int
    journal_codec: str | None
    semantic_version: int | None
    policy_version: int | None
    provenance: str
    configuration_digest: str | None
    registry_digest: str | None
    created_at: str | None
    journal_identity: tuple[int, int]
    engine_versions: tuple[tuple[str, str], ...] | None
    api_version: int = field(default=RUN_READER_API_VERSION, init=False)


@dataclass(frozen=True)
class RecordedMessage:
    """One original emission/admission, never a delivery or a console alias.

    Cursor components are the journal record index and zero-based item ordinal.
    Payload and reference dictionaries are detached, ordinary Python JSON trees;
    freezing the envelope does not freeze those caller-owned trees.
    """

    cursor: tuple[int, int]
    message_id: str
    kind: str | None = None
    schema_id: str | None = None
    route: str | None = None
    topic: str | None = None
    target: str | None = None
    source_kind: str | None = None
    source: str | None = None
    origin: dict[str, int] | None = None
    at_ns: int | None = None
    microstep: int | None = None
    available_ns: int | None = None
    available_microstep: int | None = None
    payload: Any = None
    source_stamp: dict[str, Any] | None = None
    sequence: int | None = None
    api_version: int = field(default=RUN_READER_API_VERSION, init=False)


@dataclass(frozen=True)
class Snapshot:
    """One validated ACK, inclusive of cut_index; valid inside its run context."""

    header: RunHeader
    cut_index: int
    cut_bytes: int
    _source: _SnapshotReader = field(repr=False, compare=False)
    api_version: int = field(default=RUN_READER_API_VERSION, init=False)

    def iter_records(
        self, start: int = 0, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield detached decoded records, including header zero when requested."""
        return self._source.iter_records(start=start, limit=limit)

    def iter_messages(
        self,
        kind: str | None = None,
        schema_prefix: str | None = None,
        start: int = 0,
    ) -> Iterator[RecordedMessage]:
        """Filter original messages from an inclusive journal record index."""
        return self._source.iter_messages(
            kind=kind, schema_prefix=schema_prefix, start=start
        )
