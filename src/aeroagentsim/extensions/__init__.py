"""Common extension admission and installation evidence."""

from .discovery import (
    DiscoveryResult,
    DuplicateEntryPointError,
    EntryPointConflict,
    discover,
)
from .records import Contribution

__all__ = [
    "Contribution", "DiscoveryResult", "DuplicateEntryPointError",
    "EntryPointConflict", "discover",
]
