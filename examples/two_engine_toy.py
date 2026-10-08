"""Authored §12 M1 trace: native mover, DES orders, reactive zone producer.

Run from the repository: ``.venv/bin/python -m examples.two_engine_toy``.
This compatibility example uses an ordinary ``zone.transition`` schema.
See ``sampled_relations.py`` for temporal relations and the optional entered profile.
"""

from __future__ import annotations

from aerokernel import (
    ABSENT,
    Activate,
    BindingManifest,
    BindingRule,
    CommandRequest,
    Dependency,
    EntityRef,
    FieldDescriptor,
    Instant,
    Interval,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Stamp,
    Timing,
    TypeDescriptor,
)
from aerokernel.sdk import (
    Command,
    ContextEngine,
    EngineContext,
    FactPolicy,
    dispatch_commands,
    handles,
)

MS = 1_000_000
ITEM = EntityRef("toy", "0", "item", 0, "Item")
ORDER = EntityRef("toy", "0", "order", 0, "Order")


POLICIES = {
    field: FactPolicy(
        acquired=lambda now: Stamp("canonical", now.ns, 1, "canonical"),
        valid=lambda now: Interval(now, None),
    )
    for field in ("position_truth", "order_status", "in_zone")
}


class ItemController(ContextEngine):
    """Independent Item lifecycle authority; no borrowed field ownership."""

    def bootstrap(self, ctx: EngineContext) -> None:
        ctx.create(ITEM)


class Mover(ContextEngine):
    """Authored integration at 0,5,10,… ms with no interpolation."""

    def __init__(self) -> None:
        super().__init__(
            Partition(
                "mover",
                "mover",
                produces=("position_truth",),
                commands=("move",),
                timing=Timing("fixed_step", 5 * MS),
            ),
            policies=POLICIES,
        )
        self.x = 0
        self.active: Command[object] | None = None

    def bootstrap(self, ctx: EngineContext) -> None:
        ctx.set(ITEM, "position_truth", self.x)

    @handles("move")
    def move(self, ctx: EngineContext, command: Command[object]) -> None:
        self.active = command
        ctx.accept(command)
        ctx.execute(command)

    def step(self, ctx: EngineContext) -> None:
        if self.active is not None:
            self.x += (ctx.now.ns - self.native_ns) // MS
            ctx.set(ITEM, "position_truth", self.x)
            ctx.ops.append(Activate("mover"))

    def on_inputs(self, ctx: EngineContext) -> None:
        super().on_inputs(ctx)
        if not ctx.inbox and self.active is not None and self.x >= 10:
            ctx.succeed(self.active, {"x": self.x})
            self.active = None


class Orders(ContextEngine):
    """Business acceptance is independently authored after arrival."""

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
            ),
            policies=POLICIES,
        )
        self.active: Command[object] | None = None
        self.arrived = False

    def bootstrap(self, ctx: EngineContext) -> None:
        ctx.create(ORDER)
        ctx.set(ORDER, "order_status", "new")

    @handles("fulfill")
    def fulfill(self, ctx: EngineContext, command: Command[object]) -> None:
        self.active = command
        self.wakeup_ns = 2 * MS  # Explicit internal DES event, advertised by horizon.
        ctx.accept(command)
        ctx.execute(command)
        ctx.set(ORDER, "order_status", "executing")
        ctx.submit(CommandRequest("move", "mover", ctx.now, {"goal": 10}))

    def on_inputs(self, ctx: EngineContext) -> None:
        dispatch_commands(
            self, ctx, tuple(d for d in ctx.inbox if d.message.kind == "command")
        )
        for delivery in ctx.inbox:
            if delivery.message.schema_id == "zone.transition":
                self.arrived = True
                self.wakeup_ns = 13 * MS
                ctx.set(ORDER, "order_status", "awaiting_acknowledgment")

    def step(self, ctx: EngineContext) -> None:
        self.wakeup_ns = None
        if self.active is None:
            raise RuntimeError("business timer without command")
        if self.arrived:
            ctx.set(ORDER, "order_status", "accepted_output")
            ctx.succeed(self.active, {"accepted": True})
        else:
            ctx.feedback(self.active, {"progress": "moving"})


class Zone(ContextEngine):
    """Ordinary reactive transition, distinct from sampled entered."""

    def __init__(self) -> None:
        super().__init__(
            Partition(
                "zone_eval",
                "zone_eval",
                produces=("in_zone",),
                consumes=(Dependency("position_truth"),),
                emits=("zone.transition",),
                message_targets=("zone",),
            ),
            policies=POLICIES,
        )
        self.previous, self.previous_ns = False, 0

    def on_inputs(self, ctx: EngineContext) -> None:
        if not any(d.key == (ITEM, "position_truth") for d in ctx.dirty):
            return
        position = ctx.get(ITEM, "position_truth")
        if position is ABSENT:
            raise RuntimeError("toy position is absent")
        assert isinstance(position, int)
        inside = position >= 10
        ctx.set(ITEM, "in_zone", inside)
        if inside and not self.previous:
            ctx.emit(
                "zone.transition",
                {"start_ns": self.previous_ns, "end_ns": ctx.now.ns},
                topic="zone",
            )
        self.previous, self.previous_ns = inside, ctx.now.ns


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
