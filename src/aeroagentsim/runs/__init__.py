"""Public API v1 for read-only, acknowledged local run snapshots."""

from .contracts import RUN_READER_API_VERSION, RecordedMessage, RunHeader, Snapshot
from .errors import (
    CorruptRecord,
    NoCommittedPrefix,
    RunReadError,
    RunReplaced,
    UnsupportedRunFormat,
)
from .projections import ProjectionMode, ProjectionProvenance, projection_provenance
from .reader import Run, iter_record_messages, open_run

__all__ = [
    "RUN_READER_API_VERSION",
    "CorruptRecord",
    "NoCommittedPrefix",
    "ProjectionMode",
    "ProjectionProvenance",
    "RecordedMessage",
    "Run",
    "RunHeader",
    "RunReadError",
    "RunReplaced",
    "Snapshot",
    "UnsupportedRunFormat",
    "iter_record_messages",
    "open_run",
    "projection_provenance",
]
