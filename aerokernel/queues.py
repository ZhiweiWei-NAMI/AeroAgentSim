"""Recipient heaps for deterministic pending work, detached only on mutation."""

from __future__ import annotations

import heapq
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, overload

from .storage import Overlay
from .time import Instant

if TYPE_CHECKING:
    from .state import Work


class WorkQueue(Sequence["Work"]):
    """Immutable committed queues with recipient-indexed candidate dispatch."""

    def __init__(self) -> None:
        self.items: Overlay[int, Work] = Overlay()
        self.heaps: Overlay[str, list[tuple[Instant, int]]] = Overlay()
        self.owned: set[str] = set()
        self.sequence = 0

    def fork(self) -> WorkQueue:
        """Share committed heaps, copying a recipient heap only on first write."""
        other = WorkQueue()
        other.items = self.items.fork()
        other.heaps = self.heaps.fork()
        other.sequence = self.sequence
        return other

    def _heap(self, recipient: str) -> list[tuple[Instant, int]]:
        if recipient not in self.owned:
            self.heaps[recipient] = list(self.heaps.get(recipient, ()))
            self.owned.add(recipient)
        return self.heaps[recipient]

    def append(self, work: Work) -> None:
        """Enqueue once with a stable arrival sequence and deadline."""
        index = self.sequence
        self.sequence += 1
        self.items[index] = work
        heapq.heappush(self._heap(work.recipient), (work.eligible, index))

    def ready(self, instant: Instant) -> set[str]:
        """Recipients whose earliest work is due, independent of history size."""
        return {p for p, heap in self.heaps.items() if heap and heap[0][0] <= instant}

    def earliest(self) -> Instant | None:
        """Earliest pending eligibility across recipient queues."""
        return min((heap[0][0] for heap in self.heaps.values() if heap), default=None)

    def take(self, recipient: str, instant: Instant) -> list[Work]:
        """Dispatch due recipient work in O(k log queue length)."""
        heap = self._heap(recipient)
        result = []
        while heap and heap[0][0] <= instant:
            _, index = heapq.heappop(heap)
            result.append(self.items.pop(index))
        return result

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[Work]:
        for index in sorted(self.items):
            yield self.items[index]

    @overload
    def __getitem__(self, index: int) -> Work: ...

    @overload
    def __getitem__(self, index: slice) -> list[Work]: ...

    def __getitem__(self, index: int | slice) -> Work | list[Work]:
        return list(self)[index]


class TimerQueue:
    """Deadline heap for pending timers; completed history stays in Store.timers."""

    def __init__(self) -> None:
        self.heap: list[tuple[Instant, tuple[str, ...]]] = []
        self.active: Overlay[tuple[str, ...], Instant] = Overlay()
        self.owned = True

    def fork(self) -> TimerQueue:
        """Open a candidate with shared, immutable committed deadlines."""
        other = TimerQueue()
        other.heap = self.heap
        other.active = self.active.fork()
        other.owned = False
        self.owned = False
        return other

    def _detach(self) -> None:
        if not self.owned:
            self.heap = self.heap.copy()
            self.owned = True

    def add(self, key: tuple[str, ...], due: Instant) -> None:
        """Index one validated, never-reused timer identity."""
        self._detach()
        self.active[key] = due
        heapq.heappush(self.heap, (due, key))

    def cancel(self, key: tuple[str, ...]) -> None:
        """Remove its active deadline while retaining the historical timer record."""
        del self.active[key]

    def earliest(self) -> Instant | None:
        """Return the next active deadline, lazily removing canceled heap entries."""
        while self.heap and self.heap[0][1] not in self.active:
            self._detach()
            heapq.heappop(self.heap)
        return self.heap[0][0] if self.heap else None

    def take(self, at: Instant) -> list[tuple[str, ...]]:
        """Remove all due deadlines in O(k log pending timers)."""
        result = []
        while (due := self.earliest()) is not None and due <= at:
            self._detach()
            _, key = heapq.heappop(self.heap)
            del self.active[key]
            result.append(key)
        return result
