"""Stable machine-readable kernel faults; no synthetic success paths."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any


class KernelError(ValueError):
    """A stable error code plus structured coordinates and diagnostic context."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.context = MappingProxyType(context.copy())


class ResourceLimit(KernelError):
    """Pinned wire-tree or frame resource budget exceeded."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__("RESOURCE_LIMIT", message, **context)


class MicrostepLimitExceeded(KernelError):
    """Global same-time work budget exhausted."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__("MICROSTEP_LIMIT", message, **context)


class SynchronizationDeadlock(KernelError):
    """No legal conservative progress or settlement is possible."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__("SYNCHRONIZATION_DEADLOCK", message, **context)
