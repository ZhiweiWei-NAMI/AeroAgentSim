from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RunPhase(str, Enum):
    DISCOVERED = "discovered"
    VALIDATED = "validated"
    RESOLVED = "resolved"
    PREFLIGHTED = "preflighted"
    MATERIALIZED = "materialized"
    RUNNING = "running"
    TERMINATED = "terminated"
    SEALED = "sealed"
    VERIFIED = "verified"
    CLEANED = "cleaned"
    BLOCKED = "blocked"
    FAILED = "failed"


_ALLOWED: dict[RunPhase, frozenset[RunPhase]] = {
    RunPhase.DISCOVERED: frozenset(
        {RunPhase.VALIDATED, RunPhase.BLOCKED, RunPhase.FAILED}
    ),
    RunPhase.VALIDATED: frozenset(
        {RunPhase.RESOLVED, RunPhase.BLOCKED, RunPhase.FAILED}
    ),
    RunPhase.RESOLVED: frozenset(
        {RunPhase.PREFLIGHTED, RunPhase.BLOCKED, RunPhase.FAILED}
    ),
    RunPhase.PREFLIGHTED: frozenset({RunPhase.MATERIALIZED, RunPhase.FAILED}),
    RunPhase.MATERIALIZED: frozenset({RunPhase.RUNNING, RunPhase.FAILED}),
    RunPhase.RUNNING: frozenset({RunPhase.TERMINATED, RunPhase.FAILED}),
    RunPhase.TERMINATED: frozenset({RunPhase.SEALED, RunPhase.FAILED}),
    RunPhase.SEALED: frozenset({RunPhase.VERIFIED, RunPhase.FAILED}),
    RunPhase.VERIFIED: frozenset({RunPhase.CLEANED}),
    RunPhase.BLOCKED: frozenset(),
    RunPhase.FAILED: frozenset({RunPhase.CLEANED}),
    RunPhase.CLEANED: frozenset(),
}


@dataclass(slots=True)
class RunLifecycle:
    phase: RunPhase = RunPhase.DISCOVERED

    def transition(self, target: RunPhase) -> RunPhase:
        if target not in _ALLOWED[self.phase]:
            raise RuntimeError(
                f"invalid lifecycle transition: {self.phase} -> {target}"
            )
        self.phase = target
        return self.phase
