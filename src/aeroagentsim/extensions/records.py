"""Immutable installation evidence, not a pinned run contribution lock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Contribution:
    """One declaration; unknown distribution metadata stays ``None``.

    A builtin and its installed replacement are separate records. Repeated
    installed declarations must likewise remain separate, even within one
    distribution or when they point to the same Python object.
    """

    group: str
    name: str
    source: Literal["builtin", "installed"]
    distribution: str | None
    version: str | None
    entry_point: str | None


__all__ = ["Contribution"]
