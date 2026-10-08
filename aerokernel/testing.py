"""Ergonomic in-process engine helpers and explicitly authored toy engines."""

from __future__ import annotations

from .engine import Batch, Horizon, Partition, RunContext
from .messages import Delivery, Dirty
from .state import StateView
from .time import Cut, Instant


class SimpleEngine:
    """One-partition helper: override initialize, integrate and on_react only.

    The helper echoes invocation metadata and maintains acknowledged frontiers.
    Native model state and actual outputs remain the subclass's responsibility.
    ``wakeup_ns`` is an explicit DES event frontier; None means no internal work.
    """

    version = "1"

    def __init__(self, partition: Partition) -> None:
        self.partitions = (partition,)
        self.partition = partition
        self.logical = Instant(0)
        self.native_ns = 0
        self.wakeup_ns: int | None = None
        self.context: RunContext | None = None
        self.closed = False

    def initialize(self, view: StateView) -> tuple[object, ...]:
        """Return authored bootstrap operations (empty when none are needed)."""
        return ()

    def integrate(self, view: StateView) -> tuple[object, ...]:
        """Perform actual native integration/internal DES work at a granted boundary."""
        return ()

    def on_react(
        self, view: StateView, inbox: tuple[Delivery, ...], dirty: tuple[Dirty, ...]
    ) -> tuple[object, ...]:
        """Process dispatched inputs without native integration."""
        return ()

    def reset(self, context: RunContext, view: StateView) -> tuple[Batch, ...]:
        """Initialize once with pinned context and private bootstrap cuts."""
        self.context = context
        return (view.batch(self.initialize(view)),)

    def horizon(self, partition: str, cut: Cut) -> Horizon:
        """Promise certified holds between fixed grid points or DES events."""
        timing = self.partition.timing
        if timing.mode == "fixed_step":
            if timing.step_ns is None:
                raise ValueError("fixed_step requires a step")
            next_ns: int | None = self.native_ns + timing.step_ns
        else:
            next_ns = self.wakeup_ns
        return Horizon(self.logical, self.native_ns, next_ns, next_ns, None, cut)

    def advance(self, partition: str, to: Instant, view: StateView) -> Batch:
        """Integrate on the grid; an intermediate hold never manufactures output."""
        timing = self.partition.timing
        if timing.mode == "fixed_step":
            run = timing.boundary(to.ns) == to.ns
        else:
            run = self.wakeup_ns is not None and self.wakeup_ns == to.ns
        ops = self.integrate(view) if run else ()
        self.logical = to
        self.native_ns = view.native_reached_ns
        return view.batch(ops)

    def react(
        self,
        partition: str,
        to: Instant,
        view: StateView,
        inbox: tuple[Delivery, ...],
        dirty: tuple[Dirty, ...],
    ) -> Batch:
        """Acknowledge reaction while keeping the native frontier unchanged."""
        ops = self.on_react(view, inbox, dirty)
        self.logical = to
        return view.batch(ops)

    def close(self) -> None:
        """Idempotently close a local toy engine."""
        self.closed = True
