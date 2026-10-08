"""Engine authoring facades that produce ordinary, authority-checked proposals."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from .engine import Batch, Horizon, Partition, RunContext
from .errors import KernelError
from .ids import EntityRef, ItemRef, LocalCause
from .messages import (
    CancelDecision,
    CommandRequest,
    Delivery,
    Dirty,
    Emit,
    Feedback,
    Receipt,
)
from .operations import (
    AssertEdge,
    CancelEdge,
    CloseEdge,
    Create,
    FactWrite,
    Remove,
    RetractFact,
    ScheduleTimer,
)
from .relations import Edge
from .rng import RNGStreams
from .state import ABSENT, Absent, Fact, StateView
from .time import Cut, Instant, Interval, Stamp
from .values import FrozenValue, Value, thaw


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
        if timing.mode in {"fixed_step", "lockstep"} and timing.step_ns is not None:
            next_ns: int | None = timing.boundary(self.native_ns + 1)
        else:
            next_ns = self.wakeup_ns
        return Horizon(self.logical, self.native_ns, next_ns, next_ns, None, cut)

    def advance(self, partition: str, to: Instant, view: StateView) -> Batch:
        """Integrate on the grid; an intermediate hold never manufactures output."""
        timing = self.partition.timing
        if timing.mode in {"fixed_step", "lockstep"}:
            run = (
                timing.exact_stop or not timing.latch or timing.boundary(to.ns) == to.ns
            )
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


T = TypeVar("T")
Cause = ItemRef | LocalCause


@dataclass(frozen=True)
class Command(Generic[T]):
    """Engine-retained handle to an actual command dispatch and decoded payload."""

    delivery: Delivery
    payload: T

    @property
    def id(self) -> str:
        """The kernel-assigned identity, learned through recipient dispatch."""
        return self.delivery.message.id


@dataclass(frozen=True)
class FactPolicy:
    """Explicit model choices for acquisition and validity at computation time.

    External observations should pass their actual source stamp/interval to set;
    this policy grants no producer, field selection, hold or read authority.
    """

    acquired: Callable[[Instant], Stamp]
    valid: Callable[[Instant], Interval]


class EngineContext:
    """Accumulate ordered proposals during one real invocation.

    Inputs and successful field reads contribute their authorized causes. The
    coordinator still validates every proposal and derives all publication data.
    Retain ``commands`` on the engine to issue later receipts from real dispatches.
    """

    def __init__(
        self,
        view: StateView,
        inbox: tuple[Delivery, ...] = (),
        dirty: tuple[Dirty, ...] = (),
        commands: MutableMapping[str, Command[Any]] | None = None,
        policies: Mapping[str, FactPolicy] | None = None,
        *,
        rng: RNGStreams | None = None,
    ) -> None:
        self.view, self.now = view, view.instant
        self.inbox, self.dirty = inbox, dirty
        self.commands = {} if commands is None else commands
        self.policies = {} if policies is None else policies
        self.ops: list[object] = []
        self.heads: dict[str, Cause] = {}
        self.inputs: list[Cause] = []
        self.inputs.extend(d.dispatch_ref for d in inbox)
        self.inputs.extend(d.cause for d in dirty)
        self._rng = rng

    def _causes(self, *extra: Cause) -> tuple[Cause, ...]:
        return tuple(dict.fromkeys(self.inputs + list(extra)))

    def _append(self, operation: object) -> LocalCause:
        index = len(self.ops)
        self.ops.append(operation)
        return LocalCause(index)

    def get(
        self, ref: EntityRef, field: str, *, valid_at: Instant | None = None
    ) -> FrozenValue | Absent:
        """Read an explicitly selected/dependent field and retain its version cause."""
        fact = self.view.field((ref, field), self.now if valid_at is None else valid_at)
        if isinstance(fact, Fact):
            self.inputs.append(fact.version)
            return fact.value
        return ABSENT

    def set(
        self,
        ref: EntityRef,
        field: str,
        value: object,
        *,
        acquired: Stamp | None = None,
        valid: Interval | None = None,
    ) -> LocalCause:
        """Write with explicit stamps/validity or the field's explicit model policy."""
        if acquired is None or valid is None:
            if field not in self.policies:
                raise KernelError(
                    "SDK_FACT_POLICY",
                    "supply acquisition/validity or an explicit policy",
                    field=field,
                )
            policy = self.policies[field]
            acquired = policy.acquired(self.now) if acquired is None else acquired
            valid = policy.valid(self.now) if valid is None else valid
        return self._append(
            FactWrite((ref, field), value, acquired, valid, self._causes())
        )

    def retract(
        self, ref: EntityRef, field: str, valid: Interval, reason: str
    ) -> LocalCause:
        """Publish an explicit absence version over the supplied interval."""
        return self._append(RetractFact((ref, field), valid, reason, self._causes()))

    def relate(
        self,
        edge_id: str,
        relation_id: str,
        source: EntityRef,
        target: EntityRef,
        *,
        valid: Interval,
        acquired: Stamp,
    ) -> LocalCause:
        """Assert a new edge with explicit actual acquisition and validity."""
        return self._append(
            AssertEdge(
                edge_id, relation_id, source, target, valid, acquired, self._causes()
            )
        )

    def unrelate(self, edge_id: str, *, cancel: bool = False) -> LocalCause:
        """Close a started edge, or explicitly cancel one not yet started."""
        if type(cancel) is not bool:
            raise KernelError(
                "SDK_RELATION_POLICY", "explicit bool cancellation required"
            )
        op = (
            CancelEdge(edge_id, self._causes())
            if cancel
            else CloseEdge(edge_id, self._causes())
        )
        return self._append(op)

    def relations(
        self, relation_id: str, *, valid_at: Instant | None = None
    ) -> tuple[Edge, ...]:
        """Read declared effective edges and retain their actual version causes."""
        edges = self.view.relations(
            relation_id, self.now if valid_at is None else valid_at
        )
        self.inputs.extend(e.version for e in edges)
        return edges

    def replace_relation(
        self,
        old_edge_id: str,
        edge_id: str,
        relation_id: str,
        source: EntityRef,
        target: EntityRef,
        *,
        valid: Interval,
        acquired: Stamp,
        cancel: bool = False,
    ) -> tuple[LocalCause, LocalCause]:
        """Replace by authorized closure/cancellation and a never-reused new ID."""
        ended = self.unrelate(old_edge_id, cancel=cancel)
        created = self.relate(
            edge_id, relation_id, source, target, valid=valid, acquired=acquired
        )
        return ended, created

    def create(self, ref: EntityRef) -> LocalCause:
        """Propose creation under this partition's declared lifecycle domain."""
        return self._append(Create(ref, self._causes()))

    def remove(
        self, ref: EntityRef, cleanup_refs: tuple[Cause, ...] = ()
    ) -> LocalCause:
        """Propose removal after actual, explicitly supplied cleanup acknowledgments."""
        return self._append(Remove(ref, cleanup_refs, self._causes()))

    def remember(self, delivery: Delivery, decode: Callable[[Value], T]) -> Command[T]:
        """Decode/retain a command from this invocation's actual authorized inbox."""
        if (
            delivery.message.kind != "command"
            or delivery.recipient != self.view.partition
            or delivery not in self.inbox
        ):
            raise KernelError(
                "SDK_DISPATCH",
                "an actual command delivery to this partition is required",
            )
        command = Command(delivery, decode(thaw(delivery.message.payload)))
        self.commands[command.id] = command
        return command

    def command(self, command_id: str) -> Command[Any]:
        """Resolve only an engine-retained dispatch handle; correlation alone fails."""
        return self.commands[command_id]

    def _head(self, cmd: Command[Any]) -> Cause:
        if cmd.id not in self.heads:
            head = self.view.action(cmd.id).head
            if head is None:
                raise KernelError("SDK_ACTION_HEAD", "command has no submitted receipt")
            self.heads[cmd.id] = head
        return self.heads[cmd.id]

    def _receipt(
        self, cmd: Command[Any], status: str, result: object = None
    ) -> LocalCause:
        ref = self._append(
            Receipt(
                cmd.id,
                status,
                result,
                self._causes(cmd.delivery.dispatch_ref, self._head(cmd)),
            )
        )
        self.heads[cmd.id] = ref
        return ref

    def accept(self, cmd: Command[Any]) -> LocalCause:
        """Record target acceptance; completion remains an explicit later outcome."""
        return self._receipt(cmd, "accepted")

    def reject(self, cmd: Command[Any], result: object = None) -> LocalCause:
        """Record the target's explicit rejection decision."""
        return self._receipt(cmd, "rejected", result)

    def execute(self, cmd: Command[Any]) -> LocalCause:
        """Record actual start of execution after acceptance."""
        return self._receipt(cmd, "executing")

    def feedback(self, cmd: Command[Any], payload: object) -> LocalCause:
        """Publish typed progress without advancing the action's receipt head."""
        return self._append(
            Feedback(
                cmd.id,
                payload,
                self._causes(cmd.delivery.dispatch_ref, self._head(cmd)),
            )
        )

    def succeed(self, cmd: Command[Any], result: object = None) -> LocalCause:
        """Record completion confirmed by the model or external adapter."""
        return self._receipt(cmd, "succeeded", result)

    def fail(self, cmd: Command[Any], result: object = None) -> LocalCause:
        """Record an explicit execution/cleanup failure."""
        return self._receipt(cmd, "failed", result)

    def decide_cancel(
        self,
        cmd: Command[Any],
        cancel: Delivery,
        accepted: bool,
        reason: str | None = None,
    ) -> LocalCause:
        """Decide an actual dispatched cancellation against the current action head."""
        if (
            cancel.message.kind != "cancel"
            or cancel.recipient != self.view.partition
            or cancel not in self.inbox
        ):
            raise KernelError("SDK_DISPATCH", "an actual cancel delivery is required")
        ref = self._append(
            CancelDecision(
                cmd.id,
                cancel.message.id,
                accepted,
                reason,
                self._causes(cancel.dispatch_ref, self._head(cmd)),
            )
        )
        if accepted:
            self.heads[cmd.id] = ref
        return ref

    def canceled(self, cmd: Command[Any]) -> LocalCause:
        """Record completion only after the caller has actually finished cleanup."""
        return self._receipt(cmd, "canceled")

    def emit(
        self,
        schema: str,
        payload: object,
        *,
        topic: str | None = None,
        target: str | None = None,
        at: Instant | None = None,
        stamp: Stamp | None = None,
    ) -> LocalCause:
        """Emit an event to exactly one explicit topic or target domain."""
        if (topic is None) == (target is None):
            raise KernelError("SDK_ROUTE", "supply exactly one topic or target")
        destination = topic if topic is not None else target
        assert destination is not None
        return self._append(
            Emit(
                "event",
                schema,
                destination,
                self.now if at is None else at,
                payload,
                self._causes(),
                stamp,
            )
        )

    def submit(self, command: CommandRequest) -> LocalCause:
        """Submit a child command; its ID is learned from routed submitted receipts."""
        if command.idempotency_key is not None or command.ingress_at_ns is not None:
            raise KernelError(
                "SDK_COMMAND_OPTIONS",
                "ingress-only options are invalid for child commands",
            )
        return self._append(
            Emit(
                "command",
                command.schema_id,
                command.target,
                command.at,
                command.payload,
                self._causes(),
            )
        )

    def wake_at(self, ns: int, payload: object = None) -> str:
        """Schedule an explicit one-shot wakeup without choosing a grant/frontier."""
        timer_id = f"sdk:{self.now.ns}:{self.now.microstep}:{len(self.ops)}"
        self._append(ScheduleTimer(timer_id, Instant(ns), payload, self._causes()))
        return timer_id

    def rng(self, name: str) -> random.Random:
        """Access exactly this invoking partition's declared named stream."""
        if self._rng is None:
            raise KernelError("SDK_RNG_CONTEXT", "no partition RNG context supplied")
        return self._rng.stream(name)

    def batch(self) -> Batch:
        """Echo the real invocation metadata; publication remains coordinator-owned."""
        return self.view.batch(tuple(self.ops))


def handles(
    schema: str, decode: Callable[[Value], Any] = lambda value: value
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a typed handler for a command already declared on the partition."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, "__kernel_handler__", (schema, decode))  # noqa: B010
        return fn

    return decorate


def _command_handlers(
    engine: SimpleEngine,
) -> dict[str, tuple[Callable[..., Any], Callable[[Value], Any]]]:
    table: dict[str, tuple[Callable[..., Any], Callable[[Value], Any]]] = {}
    for name in sorted(dir(type(engine))):
        fn = getattr(type(engine), name)
        registration = getattr(fn, "__kernel_handler__", None)
        if registration is None:
            continue
        schema, decode = registration
        if schema in table or schema not in engine.partition.commands:
            raise KernelError(
                "SDK_HANDLER", "duplicate or undeclared command handler", schema=schema
            )
        table[schema] = (getattr(engine, name), decode)
    return table


def dispatch_commands(
    engine: SimpleEngine, ctx: EngineContext, deliveries: tuple[Delivery, ...]
) -> None:
    """Call declared handlers in the kernel's existing canonical inbox order."""
    table = _command_handlers(engine)
    for delivery in deliveries:
        if (
            delivery.message.kind != "command"
            or delivery.message.schema_id not in table
        ):
            raise KernelError("SDK_HANDLER", "delivery has no declared command handler")
        fn, decode = table[delivery.message.schema_id]
        fn(ctx, ctx.remember(delivery, decode))


class ContextEngine(SimpleEngine):
    """One-partition driver with context hooks and decorated command handlers."""

    def __init__(
        self, partition: Partition, *, policies: Mapping[str, FactPolicy] | None = None
    ) -> None:
        super().__init__(partition)
        _command_handlers(self)
        self.policies = MappingProxyType({} if policies is None else dict(policies))
        self.commands: dict[str, Command[Any]] = {}

    def _context(
        self,
        view: StateView,
        inbox: tuple[Delivery, ...] = (),
        dirty: tuple[Dirty, ...] = (),
    ) -> EngineContext:
        rng = None if self.context is None else self.context.rng[self.partition.id]
        return EngineContext(view, inbox, dirty, self.commands, self.policies, rng=rng)

    def bootstrap(self, ctx: EngineContext) -> None:
        """Override to produce authored bootstrap proposals."""

    def step(self, ctx: EngineContext) -> None:
        """Override to perform actual integration or internal DES work."""

    def on_inputs(self, ctx: EngineContext) -> None:
        """Handle commands by default; override for event/dirty/cancel processing."""
        dispatch_commands(self, ctx, ctx.inbox)

    def initialize(self, view: StateView) -> tuple[object, ...]:
        """Create a context for the actual reset invocation."""
        ctx = self._context(view)
        self.bootstrap(ctx)
        return tuple(ctx.ops)

    def integrate(self, view: StateView) -> tuple[object, ...]:
        """Run the context hook only at the native/internal boundary."""
        ctx = self._context(view)
        self.step(ctx)
        return tuple(ctx.ops)

    def on_react(
        self, view: StateView, inbox: tuple[Delivery, ...], dirty: tuple[Dirty, ...]
    ) -> tuple[object, ...]:
        """Build a proposal batch from the actual authorized input invocation."""
        ctx = self._context(view, inbox, dirty)
        self.on_inputs(ctx)
        return tuple(ctx.ops)
