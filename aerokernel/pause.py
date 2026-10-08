"""Journaled wall-clock leases for a settled, paused world."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import KernelError
from .rpc_transport import pause_policy_from_data

if TYPE_CHECKING:
    from .coordinator import Kernel


def validate_hold(kernel: Kernel, duration_s: float, reason: str) -> list[str]:
    """Require every remote peer's pinned lease declaration before any request."""
    from .ids import validate_text

    validate_text(reason)
    if not reason:
        raise KernelError("RPC_HOLD", "hold reason required")
    if (
        kernel._store.sealed_ns is None
        or kernel._store.cut.instant.ns != kernel._store.sealed_ns
        or kernel._store.pending_intents
    ):
        raise KernelError("RPC_HOLD", "hold requires a settled world")
    profiles = kernel.header.get("engine_profiles", {})
    peers = sorted(profiles)
    if not peers:
        raise KernelError("RPC_HOLD", "no negotiated remote lease participants")
    for eid in peers:
        declaration = profiles[eid].get("pause_policy")
        if declaration is None:
            raise KernelError(
                "RPC_HOLD", "all remote peers must negotiate holds", engine=eid
            )
        pause_policy_from_data(declaration).check_hold(duration_s)
    return peers


def hold_wall_clock(kernel: Kernel, duration_s: float, reason: str) -> None:
    """Record intent then lease acknowledgments; never sleep or advance an engine."""
    with kernel._ingress_condition:
        kernel._guard()
        if kernel._store.pending_wall_clock_hold is not None:
            raise KernelError("RPC_HOLD", "previous lease acknowledgment pending")
        peers = validate_hold(kernel, duration_s, reason)
        fields: dict[str, Any] = {
            "duration_s": duration_s,
            "reason": reason,
            "engines": peers,
        }
        kernel._record_control("wall_clock_hold", {**fields, "status": "requested"})
        intent_index = kernel._store.cut.index
        try:
            for eid in peers:
                hold = getattr(kernel._engines[eid], "hold_wall_clock", None)
                if hold is None:
                    raise KernelError(
                        "RPC_HOLD", "declared lease hook missing", engine=eid
                    )
                hold(duration_s, reason)
            kernel._record_control(
                "wall_clock_hold",
                {**fields, "status": "acknowledged", "intent_index": intent_index},
            )
        except Exception as error:
            kernel._fault(error)
            raise
