"""Two local real-time engines: fast service before an unrelated slow closure."""

import json
import threading

from aerokernel import (
    BindingManifest,
    CommandRequest,
    IngressPolicy,
    IngressStream,
    Instant,
    Kernel,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Stamp,
    Timing,
    replay,
)
from aerokernel.sdk import LiveIngress, SimpleEngine


class Consumer(SimpleEngine):
    def __init__(self, name):
        super().__init__(
            Partition(name, name, commands=("observe",), timing=Timing("real_time"))
        )
        self.inputs = []
        self.received = threading.Event()

    def on_react(self, view, inbox, dirty):
        self.inputs.extend(delivery.message for delivery in inbox)
        self.received.set()
        return ()


def main():
    fast, slow = Consumer("fast"), Consumer("slow")
    kernel = Kernel(
        ingress_streams=(
            IngressStream(
                "fast-source",
                IngressPolicy(0, allowed_lateness_ns=2),
                "canonical",
                ("fast",),
            ),
            IngressStream(
                "slow-source",
                IngressPolicy(0, lateness="delay", allowed_lateness_ns=5),
                "canonical",
                ("slow",),
            ),
        )
    )
    kernel.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("observe", "command", {"type": "integer"}),)
        ),
        BindingManifest("example", "epoch"),
        (fast, slow),
    )
    kernel.start()
    fast_input = LiveIngress(kernel, "fast-source")
    slow_input = LiveIngress(kernel, "slow-source")
    receipt = fast_input.admit(
        CommandRequest("observe", "fast", Instant(10), 42),
        Stamp("canonical", 10, 1, "canonical"),
    )
    fast_input.advance_source_progress(Stamp("canonical", 12, 1, "canonical"))
    errors = []

    def run():
        try:
            kernel.run_until(10)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        if not fast.received.wait(1):
            raise TimeoutError("fast engine did not receive its actual command")
        # Capture an acknowledged prefix after the callback/transaction returns.
        with kernel._ingress_condition:
            early = {"fast_ns": fast.logical.ns, "slow_ns": slow.logical.ns}
            assert early == {"fast_ns": 10, "slow_ns": 0}
            assert kernel._store.sealed_ns == 0
            prefix = replay(kernel.journal.bytes)
            assert prefix.incomplete
            assert prefix.ingress_receipt(receipt.command_id) == receipt
    finally:
        slow_input.advance_source_progress(Stamp("canonical", 15, 1, "canonical"))
        worker.join(2)
    if worker.is_alive():
        raise TimeoutError("common seal did not finish")
    if errors:
        raise errors[0]
    restored = replay(kernel.journal.bytes)
    assert not restored.incomplete and restored.records == kernel.records
    print(json.dumps({"before_slow_closure": early, "common_seal_ns": 10}))
    kernel.close()


if __name__ == "__main__":
    main()
