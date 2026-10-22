"""Public API v1 for read-only, acknowledged local run snapshots."""

from .contracts import RUN_READER_API_VERSION, RecordedMessage, RunHeader, Snapshot
from .errors import (
    CorruptRecord,
    NoCommittedPrefix,
    RunReadError,
    RunReplaced,
    UnsupportedRunFormat,
)
from .reader import Run, open_run

__all__ = [
    "RUN_READER_API_VERSION",
    "CorruptRecord",
    "NoCommittedPrefix",
    "RecordedMessage",
    "Run",
    "RunHeader",
    "RunReadError",
    "RunReplaced",
    "Snapshot",
    "UnsupportedRunFormat",
    "open_run",
]
