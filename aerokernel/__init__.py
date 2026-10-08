"""Independent, domain-neutral conservative simulation kernel."""

from .binding import BindingManifest, BindingRule, ExactBinding, LifecycleRule
from .coordinator import Kernel
from .engine import Batch, Dependency, Engine, Horizon, Partition, RunContext, Timing
from .errors import (
    KernelError,
    MicrostepLimitExceeded,
    ResourceLimit,
    SynchronizationDeadlock,
)
from .ids import EntityRef, FieldKey, ItemRef, LocalCause
from .journal import Journal, replay
from .messages import (
    ActionState,
    CancelDecision,
    CancelRequestResult,
    CommandRequest,
    Delivery,
    Dirty,
    Emit,
    Feedback,
    Message,
    Receipt,
    RequestCancel,
)
from .operations import (
    Activate,
    CancelTimer,
    Create,
    FactWrite,
    LifecycleReady,
    Remove,
    RetractFact,
    ScheduleTimer,
)
from .registry import (
    FieldDescriptor,
    MemoryRegistry,
    MessageDescriptor,
    Registry,
    TypeDescriptor,
)
from .state import ABSENT, Absent, Fact, Retraction, StateView
from .time import ClockMapping, Cut, Instant, Interval, Stamp
from .values import (
    ResourceBudget,
    canonical_json,
    freeze,
    normalize,
    parse_json,
    thaw,
    typed_equal,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "ABSENT",
    "Absent",
    "ActionState",
    "Activate",
    "Batch",
    "BindingManifest",
    "BindingRule",
    "CancelDecision",
    "CancelRequestResult",
    "CancelTimer",
    "ClockMapping",
    "CommandRequest",
    "Create",
    "Cut",
    "Delivery",
    "Dependency",
    "Dirty",
    "Emit",
    "Engine",
    "EntityRef",
    "ExactBinding",
    "Fact",
    "FactWrite",
    "Feedback",
    "FieldDescriptor",
    "FieldKey",
    "Horizon",
    "Instant",
    "Interval",
    "ItemRef",
    "Journal",
    "Kernel",
    "KernelError",
    "LifecycleReady",
    "LifecycleRule",
    "LocalCause",
    "MemoryRegistry",
    "Message",
    "MessageDescriptor",
    "MicrostepLimitExceeded",
    "Partition",
    "Receipt",
    "Registry",
    "Remove",
    "RequestCancel",
    "ResourceBudget",
    "ResourceLimit",
    "RetractFact",
    "Retraction",
    "RunContext",
    "ScheduleTimer",
    "Stamp",
    "StateView",
    "SynchronizationDeadlock",
    "Timing",
    "TypeDescriptor",
    "canonical_json",
    "freeze",
    "normalize",
    "parse_json",
    "replay",
    "thaw",
    "typed_equal",
]
