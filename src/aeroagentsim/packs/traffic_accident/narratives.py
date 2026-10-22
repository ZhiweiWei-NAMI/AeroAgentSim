"""Compatibility exports for the pack's presentation-family declarations.

The authoritative wording now lives in :mod:`.presentation`. These legacy
imports retain their shape for existing callers; no second vocabulary is kept.
"""

from __future__ import annotations

import copy
from typing import Any

from .presentation import EVENT_NARRATIVES


def declarations() -> dict[str, dict[str, Any]]:
    """Return a copy of the pack's narrative declarations."""
    return copy.deepcopy(EVENT_NARRATIVES)
