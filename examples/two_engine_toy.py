"""Authored §12 M1 trace: native mover, DES orders, reactive zone producer.

Run from the repository: ``.venv/bin/python -m examples.two_engine_toy``.
Relation cardinality and sampled entered frames are M2; this example uses a
separate ``zone.transition`` schema and never claims a sampled-frame profile.
"""

from __future__ import annotations

from aerokernel import (
    ABSENT,
    Activate,
    BindingManifest,
    BindingRule,
    CommandRequest,
    Create,
    Dependency,
    Emit,
    EntityRef,
    Fact,
    FactWrite,
    Feedback,
    FieldDescriptor,
    Instant,
    Interval,
    ItemRef,
    Kernel,
    LifecycleRule,
    LocalCause,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Receipt,
    Stamp,
    Timing,
    TypeDescriptor,
)
from aerokernel.messages import Delivery, Dirty
from aerokernel.state import StateView
from aerokernel.testing import SimpleEngine

MS = 1_000_000
ITEM = EntityRef("toy", "0", "item", 0, "Item")
ORDER = EntityRef("toy", "0", "order", 0, "Order")


def write(view: StateView, ref: EntityRef, field: str, value: object) -> FactWrite:
    """Toy acquisition is the actual canonical boundary, with open validity."""
    return FactWrite(
        (ref, field),
        value,
        Stamp("canonical", view.instant.ns, 1, "canonical"),
        Interval(view.instant, None),
    )


class ItemController(SimpleEngine):
    """Independent Item lifecycle authority; creation grants no field ownership."""

    def initialize(self, view: StateView) -> tuple[object, ...]:
        return (Create(ITEM),)


class Mover(SimpleEngine):
    """Actual toy native integration at 0,5,10,… ms, without interpolation."""

    def __init__(self) -> None:
        super().__init__(
            Partition(
                "mover",
                "mover",
                produces=("position_truth",),
                commands=("move",),
                timing=Timing("fixed_step", 5 * MS),
            )
        )
        self.x = 0
        self.command: str | None = None
        self.dispatch: ItemRef | None = None

    def initialize(self, view: StateView) -> tuple[object, ...]:
        return (write(view, ITEM, "position_truth", self.x),)

    def integrate(self, view: StateView) -> tuple[object, ...]:
        if self.command is not None:
            self.x += (view.instant.ns - self.native_ns) // MS
            return (write(view, ITEM, "position_truth", self.x), Activate("mover"))
        return ()

    def on_react(
        self, view: StateView, inbox: tuple[Delivery, ...], dirty: tuple[Dirty, ...]
    ) -> tuple[object, ...]:
        if inbox:
            delivery = inbox[0]
            self.command, self.dispatch = delivery.message.id, delivery.dispatch_ref
            return (
                Receipt(
                    self.command,
                    "accepted",
                    causes=(self.dispatch, view.action(self.command).head),
                ),
                Receipt(
                    self.command, "executing", causes=(self.dispatch, LocalCause(0))
                ),
            )
        if self.command is not None and self.x >= 10:
            head = view.action(self.command).head
            result = Receipt(
                self.command, "succeeded", {"x": self.x}, (self.dispatch, head)
            )
            self.command = None
            return (result,)
        return ()


class Orders(SimpleEngine):
    """Arrival and independently authored business acceptance remain distinct."""

    def __init__(self) -> None:
        super().__init__(
            Partition(
                "orders",
                "orders",
                produces=("order_status",),
                commands=("fulfill",),
                emits=("move",),
                message_targets=("mover",),
                subscribes=("zone",),
                lifecycle=True,
            )
        )
        self.command: str | None = None
        self.dispatch: ItemRef | None = None
        self.arrived = False

    def initialize(self, view: StateView) -> tuple[object, ...]:
        return (Create(ORDER), write(view, ORDER, "order_status", "new"))

    def on_react(
        self, view: StateView, inbox: tuple[Delivery, ...], dirty: tuple[Dirty, ...]
    ) -> tuple[object, ...]:
        for delivery in inbox:
            if delivery.message.schema_id == "fulfill":
                self.command, self.dispatch = delivery.message.id, delivery.dispatch_ref
                self.wakeup_ns = 2 * MS
                return (
                    Receipt(
                        self.command,
                        "accepted",
                        causes=(self.dispatch, view.action(self.command).head),
                    ),
                    Receipt(
                        self.command, "executing", causes=(self.dispatch, LocalCause(0))
                    ),
                    write(view, ORDER, "order_status", "executing"),
                    Emit(
                        "command",
                        "move",
                        "mover",
                        view.instant,
                        {"goal": 10},
                        (self.dispatch,),
                    ),
                )
            if delivery.message.schema_id == "zone.transition":
                self.arrived = True
                self.wakeup_ns = 13 * MS
                return (write(view, ORDER, "order_status", "awaiting_acknowledgment"),)
        return ()

    def integrate(self, view: StateView) -> tuple[object, ...]:
        self.wakeup_ns = None
        if self.command is None:
            raise RuntimeError("business timer without command")
        causes = (self.dispatch, view.action(self.command).head)
        if self.arrived:
            return (
                write(view, ORDER, "order_status", "accepted_output"),
                Receipt(self.command, "succeeded", {"accepted": True}, causes),
            )
        return (Feedback(self.command, {"progress": "moving"}, causes),)


class Zone(SimpleEngine):
    """Ordinary discrete reactive transition, distinct from M2 sampled entered."""

    def __init__(self) -> None:
        super().__init__(
            Partition(
                "zone_eval",
                "zone_eval",
                produces=("in_zone",),
                consumes=(Dependency("position_truth"),),
                emits=("zone.transition",),
                message_targets=("zone",),
            )
        )
        self.previous = False
        self.previous_ns = 0

    def on_react(
        self, view: StateView, inbox: tuple[Delivery, ...], dirty: tuple[Dirty, ...]
    ) -> tuple[object, ...]:
        if not any(d.key == (ITEM, "position_truth") for d in dirty):
            return ()
        position = view.field((ITEM, "position_truth"), view.instant)
        if position is ABSENT:
            raise RuntimeError("toy position is absent")
        assert isinstance(position, Fact) and isinstance(position.value, int)
        inside = position.value >= 10
        ops: tuple[object, ...] = (write(view, ITEM, "in_zone", inside),)
        if inside and not self.previous:
            ops += (
                Emit(
                    "event",
                    "zone.transition",
                    "zone",
                    view.instant,
                    {"start_ns": self.previous_ns, "end_ns": view.instant.ns},
                    (position.version,),
                ),
            )
        self.previous, self.previous_ns = inside, view.instant.ns
        return ops


def make_toy(seed: int = 42) -> Kernel:
    """Bind selected toy descriptors, actual producers and offline command."""
    integer = {"type": "integer"}
    record = {
        "type": "record",
        "members": {"x": integer},
        "required": ["x"],
        "extra": False,
    }
    registry = MemoryRegistry(
        (TypeDescriptor("Item"), TypeDescriptor("Order")),
        (
            FieldDescriptor("position_truth", "Item", integer),
            FieldDescriptor("in_zone", "Item", {"type": "boolean"}),
            FieldDescriptor("order_status", "Order", {"type": "string"}),
        ),
        (
            MessageDescriptor(
                "move",
                "command",
                {
                    "type": "record",
                    "members": {"goal": integer},
                    "required": ["goal"],
                    "extra": False,
                },
                result_schema=record,
            ),
            MessageDescriptor(
                "fulfill",
                "command",
                {"type": "record", "members": {}, "required": [], "extra": False},
                result_schema={
                    "type": "record",
                    "members": {"accepted": {"type": "boolean"}},
                    "required": ["accepted"],
                    "extra": False,
                },
                feedback_schema={
                    "type": "record",
                    "members": {"progress": {"type": "string"}},
                    "required": ["progress"],
                    "extra": False,
                },
            ),
            MessageDescriptor(
                "zone.transition",
                "event",
                {
                    "type": "record",
                    "members": {"start_ns": integer, "end_ns": integer},
                    "required": ["start_ns", "end_ns"],
                    "extra": False,
                },
            ),
        ),
    )
    manifest = BindingManifest(
        "toy",
        "0",
        (ITEM, ORDER),
        rules=(
            BindingRule("mover", "Item", ("position_truth",)),
            BindingRule("zone_eval", "Item", ("in_zone",)),
            BindingRule("orders", "Order", ("order_status",)),
        ),
        lifecycle=(
            LifecycleRule("item_controller", "Item"),
            LifecycleRule("orders", "Order"),
        ),
        bootstrap_commands=(CommandRequest("fulfill", "orders", Instant(0), {}),),
    )
    kernel = Kernel(
        root_seed=seed,
        configuration={
            "scenario": "authored two-engine toy",
            "velocity_unit_per_ms": 1,
            "native_step_ms": 5,
            "zone_threshold": 10,
            "feedback_ms": 2,
            "acceptance_ms": 13,
        },
    )
    kernel.bind(
        registry,
        manifest,
        (
            Mover(),
            Orders(),
            Zone(),
            ItemController(
                Partition("item_controller", "item_controller", lifecycle=True)
            ),
        ),
    )
    return kernel


def main() -> None:
    """Print the committed trace; no external simulation data is fabricated."""
    kernel = make_toy()
    kernel.start()
    kernel.run_until(13 * MS)
    for record in kernel.records:
        if record["type"] == "transaction":
            print(
                record["instant"],
                record["phase"],
                [item.get("receipt") for item in record["items"] if "receipt" in item],
            )
    kernel.close()


if __name__ == "__main__":
    main()
