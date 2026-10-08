"""Declared engine contracts for serial conservative execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .errors import KernelError
from .ids import validate_text
from .time import Cut, Instant
from .values import FrozenValue

if TYPE_CHECKING:
    from .messages import Delivery, Dirty
    from .rng import RNGStreams
    from .state import StateView


@dataclass(frozen=True)
class Timing:
    """Native time model; nonsplittable fixed-step inputs latch to its grid."""

    mode: str = "des"
    step_ns: int | None = None
    origin_ns: int = 0
    latch: bool = True

    def boundary(self, ns: int) -> int:
        """Round a recipient's eligibility up to its native grid."""
        if self.mode != "fixed_step":
            return ns
        if self.step_ns is None or self.step_ns <= 0:
            raise ValueError("fixed_step requires a positive step")
        return (
            self.origin_ns
            + max(0, (ns - self.origin_ns + self.step_ns - 1) // self.step_ns)
            * self.step_ns
        )


@dataclass(frozen=True)
class Dependency:
    """Field read scope and physical lag; equality suppression is opt-in."""

    field: str
    type_id: str | None = None
    id_pattern: str = "*"
    lag_ns: int = 0
    value_only: bool = False
    require_coverage: bool = False


@dataclass(frozen=True)
class Partition:
    """One independently scheduled authority within an engine."""

    id: str
    engine_id: str
    produces: tuple[str, ...] = ()
    consumes: tuple[Dependency, ...] = ()
    commands: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    subscribes: tuple[str, ...] = ()
    timing: Timing = Timing()
    reactive: bool = True
    lifecycle: bool = False
    rng_streams: tuple[str, ...] = ()
    lifecycle_reads: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    message_targets: tuple[str, ...] = ()
    message_lag_ns: int = 0

    def __post_init__(self) -> None:
        validate_text(self.id)
        validate_text(self.engine_id)
        for name in (
            "produces",
            "commands",
            "emits",
            "subscribes",
            "rng_streams",
            "lifecycle_reads",
            "features",
            "message_targets",
        ):
            values = tuple(getattr(self, name))
            if len(set(values)) != len(values):
                raise KernelError("PARTITION_DECLARATION", "duplicate declaration")
            for value in values:
                validate_text(value)
            object.__setattr__(self, name, tuple(sorted(values)))
        object.__setattr__(self, "consumes", tuple(self.consumes))
        if any(type(v) is not bool for v in (self.reactive, self.lifecycle)):
            raise KernelError("PARTITION_CAPABILITY", "capability flags must be bool")


@dataclass(frozen=True)
class Horizon:
    """An input-cut-specific promise of safe logical advancement."""

    reached: Instant
    native_reached_ns: int
    next_wakeup_ns: int | None
    output_lb_ns: int | None
    grant_limit_ns: int | None
    input_cut: Cut

    def __post_init__(self) -> None:
        if not isinstance(self.reached, Instant) or not isinstance(self.input_cut, Cut):
            raise KernelError(
                "HORIZON_COORDINATES", "typed frontier and input cut required"
            )
        if type(self.native_reached_ns) is not int or self.native_reached_ns < 0:
            raise KernelError(
                "HORIZON_NATIVE", "native frontier must be a nonnegative integer"
            )
        for bound in (self.next_wakeup_ns, self.output_lb_ns, self.grant_limit_ns):
            if bound is not None and (type(bound) is not int or bound < 0):
                raise KernelError(
                    "HORIZON_BOUND", "finite bounds must be nonnegative integers"
                )


@dataclass(frozen=True)
class Batch:
    """Ordered proposals, echoing private invocation metadata."""

    reached: Instant
    transaction_base_cut: Cut
    read_cut: Cut
    native_reached_ns: int
    native_input_cut: Cut
    operations: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.reached, Instant) or any(
            not isinstance(c, Cut)
            for c in (self.transaction_base_cut, self.read_cut, self.native_input_cut)
        ):
            raise KernelError(
                "BATCH_COORDINATES", "typed invocation coordinates required"
            )
        if type(self.native_reached_ns) is not int or self.native_reached_ns < 0:
            raise KernelError(
                "BATCH_NATIVE", "native frontier must be a nonnegative integer"
            )
        object.__setattr__(self, "operations", tuple(self.operations))


@dataclass(frozen=True)
class RunContext:
    """Pinned namespace and exclusive declared random streams."""

    root_seed: int
    run_id: str
    epoch: str
    rng: dict[str, RNGStreams]
    configuration: FrozenValue = None


class Engine(Protocol):
    """In-process engines honor declared reads, frontiers and RNG ownership."""

    partitions: tuple[Partition, ...]
    version: str

    def reset(self, context: RunContext, view: StateView) -> tuple[Batch, ...]:
        """Return initial proposals for predeclared entities."""
        ...

    def horizon(self, partition: str, cut: Cut) -> Horizon:
        """Report promises tied to the supplied current input cut."""
        ...

    def advance(self, partition: str, to: Instant, view: StateView) -> Batch:
        """Reach the exact logical grant without a boundary inbox."""
        ...

    def react(
        self,
        partition: str,
        to: Instant,
        view: StateView,
        inbox: tuple[Delivery, ...],
        dirty: tuple[Dirty, ...],
    ) -> Batch:
        """React without advancing native physical time."""
        ...

    def close(self) -> None:
        """Idempotently release resources; report real cleanup failures."""
        ...
