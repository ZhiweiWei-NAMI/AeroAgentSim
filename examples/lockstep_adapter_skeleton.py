"""Host adapter skeleton for simulator-specific backend protocols.

PX4 and SUMO containers speak ``aeroagentsim.px4/v1`` and
``aeroagentsim.sumo/v1``. A host adapter implements the kernel Engine protocol
and translates those native protocols here; the backend need not speak kernel
RPC. Replace FakeLockstepSimulator with a real client that confirms native time
and actual output. No transport acknowledgment constitutes business completion.

Run: ``.venv/bin/python -m examples.lockstep_adapter_skeleton``.
"""

from __future__ import annotations

from aerokernel import (
    BindingManifest,
    CommandRequest,
    Emit,
    Instant,
    Kernel,
    KernelError,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Stamp,
    Timing,
)
from aerokernel.messages import Delivery, Dirty
from aerokernel.sdk import SimpleEngine
from aerokernel.state import StateView
from aerokernel.testing import FakeLockstepSimulator, SimulatorOutput

MS = 1_000_000


class LockstepAdapter(SimpleEngine):
    """A 20-ms native-grid adapter with explicit early-return handling.

    Intermediate common boundaries are certified holds: this model makes native
    output available only at communication points. Physical calls finish the
    entire native interval before any boundary inbox is applied. Buffering models
    delayed availability, retaining each output's actual occurrence/source stamp.
    """

    def __init__(
        self,
        simulator: FakeLockstepSimulator,
        *,
        step_ns: int = 20 * MS,
        buffer_early_return: bool = False,
    ) -> None:
        super().__init__(
            Partition(
                "external",
                "external",
                commands=("control",),
                emits=("native.output",),
                message_targets=("observations",),
                timing=Timing("lockstep", step_ns, certified_hold=True),
            )
        )
        self.simulator = simulator
        self.buffer_early_return = buffer_early_return
        self.version = (
            "skeleton/buffer-v1" if buffer_early_return else "skeleton/exact-v1"
        )

    def integrate(self, view: StateView) -> tuple[object, ...]:
        outputs: list[SimulatorOutput] = []
        # A real client also bounds each native request by a wall-clock timeout.
        for _ in range(1024):
            before = self.simulator.native_ns
            result = self.simulator.advance(view.instant.ns)
            if not before < result.reached_ns <= view.instant.ns:
                raise KernelError("LOCKSTEP_NATIVE_TIME", "invalid confirmed stop")
            outputs.extend(result.outputs)
            if result.reached_ns == view.instant.ns:
                break
            if not self.buffer_early_return:
                raise KernelError("LOCKSTEP_EARLY_RETURN", "native stop precedes grant")
        else:
            raise KernelError("LOCKSTEP_RETURN_LIMIT", "too many native returns")
        return tuple(
            Emit(
                "event",
                "native.output",
                "observations",
                Instant(output.at_ns),
                output.value,
                source_stamp=Stamp("canonical", output.at_ns, 1, "canonical"),
            )
            for output in outputs
        )

    def on_react(
        self, view: StateView, inbox: tuple[Delivery, ...], dirty: tuple[Dirty, ...]
    ) -> tuple[object, ...]:
        for delivery in inbox:
            # These controls arrive after native integration at this boundary.
            # Add domain receipts only after actual backend decisions/results.
            self.simulator.apply(delivery.message.payload)
        return ()

    def close(self) -> None:
        self.simulator.close()
        super().close()


def make_adapter_run(
    simulator: FakeLockstepSimulator, *, buffer_early_return: bool = False
) -> tuple[Kernel, LockstepAdapter]:
    """Bind explicitly authored input/output schemas and a native simulator."""
    adapter = LockstepAdapter(simulator, buffer_early_return=buffer_early_return)
    k = Kernel(configuration={"buffer_early_return": buffer_early_return})
    k.bind(
        MemoryRegistry(
            (),
            messages=(
                MessageDescriptor("control", "command", {"type": "integer"}),
                MessageDescriptor("native.output", "event", {"type": "integer"}),
            ),
        ),
        BindingManifest("adapter", "0"),
        (adapter,),
    )
    return k, adapter


def main() -> None:
    """Demonstrate a 3-ms control applied after the 20-ms native boundary."""
    sim = FakeLockstepSimulator(20 * MS)
    k, _ = make_adapter_run(sim)
    k.start()
    k.submit(
        CommandRequest("control", "external", Instant(3 * MS), 9, ingress_at_ns=3 * MS)
    )
    k.run_until(3 * MS)
    assert sim.native_ns == 0 and sim.inputs == []
    k.run_until(20 * MS)
    assert sim.inputs == [(20 * MS, 9)]
    print(
        {"native_ns": sim.native_ns, "native_calls": sim.calls, "applied": sim.inputs}
    )
    k.close()


if __name__ == "__main__":
    main()
