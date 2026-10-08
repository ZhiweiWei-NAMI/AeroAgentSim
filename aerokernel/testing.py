"""Deterministic stand-ins with explicitly authored native behavior."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import KernelError
from .values import FrozenValue, freeze


@dataclass(frozen=True)
class SimulatorOutput:
    """An authored output at its actual native occurrence time."""

    at_ns: int
    value: FrozenValue


@dataclass(frozen=True)
class SimulatorReturn:
    """Confirmed native time and outputs returned by one simulator call."""

    reached_ns: int
    outputs: tuple[SimulatorOutput, ...]


class FakeLockstepSimulator:
    """Native simulator stand-in, separate from its kernel adapter.

    A call stops only on ``stop_granularity_ns``. Configured early returns are
    one-shot native stop points; a buffering adapter must finish the granted
    interval before acknowledging it. Outputs and inputs are explicit authored
    test data. This helper never manufactures a measurement or action outcome.
    """

    def __init__(
        self,
        stop_granularity_ns: int = 1,
        *,
        early_return_ns: int | None = None,
        outputs: tuple[tuple[int, object], ...] = (),
    ) -> None:
        if type(stop_granularity_ns) is not int or stop_granularity_ns <= 0:
            raise KernelError("SIMULATOR_GRID", "positive native granularity required")
        if early_return_ns is not None and (
            type(early_return_ns) is not int or early_return_ns <= 0
        ):
            raise KernelError("SIMULATOR_EARLY_RETURN", "positive early stop required")
        scheduled = []
        for at_ns, value in outputs:
            if type(at_ns) is not int or at_ns < 0:
                raise KernelError(
                    "SIMULATOR_OUTPUT", "nonnegative output time required"
                )
            scheduled.append(SimulatorOutput(at_ns, freeze(value)))
        self.stop_granularity_ns = stop_granularity_ns
        self.early_return_ns = early_return_ns
        self.native_ns = 0
        self.closed = False
        self.calls: list[tuple[int, int, int]] = []
        self.inputs: list[tuple[int, FrozenValue]] = []
        self._outputs = sorted(scheduled, key=lambda item: item.at_ns)

    def advance(self, to_ns: int) -> SimulatorReturn:
        """Advance native time once, possibly returning early as configured."""
        if self.closed:
            raise KernelError("SIMULATOR_CLOSED", "simulator is closed")
        if type(to_ns) is not int or to_ns <= self.native_ns:
            raise KernelError("SIMULATOR_TIME", "native target must advance time")
        if to_ns % self.stop_granularity_ns:
            raise KernelError("SIMULATOR_STOP", "target is not an exact native stop")
        reached = to_ns
        early = self.early_return_ns
        if early is not None and self.native_ns < early < to_ns:
            reached = early
            self.early_return_ns = None
        self.calls.append((self.native_ns, to_ns, reached))
        self.native_ns = reached
        returned = tuple(item for item in self._outputs if item.at_ns <= reached)
        self._outputs = [item for item in self._outputs if item.at_ns > reached]
        return SimulatorReturn(reached, returned)

    def apply(self, value: object) -> None:
        """Apply an authored input at the currently confirmed native boundary."""
        if self.closed:
            raise KernelError("SIMULATOR_CLOSED", "simulator is closed")
        self.inputs.append((self.native_ns, freeze(value)))

    def close(self) -> None:
        """Release this stand-in idempotently."""
        self.closed = True
