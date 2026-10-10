"""Strict configuration-driven AERO-BENCH benchmark runner."""

from aero_bench.runner.contracts import (
    ArtifactIdentity,
    PreflightIdentity,
    PublicReplayIdentity,
    PublicTraceIdentity,
    RunSummary,
    RunnerConfig,
    RunnerSummary,
    SealIdentity,
    VerificationIdentity,
)
from aero_bench.runner.execution import (
    RunnerError,
    load_runner_config,
    run_suite,
)
from aero_bench.runner.session import (
    RunExecutionPhase,
    RunExecutionSession,
    RunExecutionSnapshot,
    RunExecutionTransition,
)

__all__ = [
    "ArtifactIdentity",
    "PreflightIdentity",
    "PublicReplayIdentity",
    "PublicTraceIdentity",
    "RunExecutionPhase",
    "RunExecutionSession",
    "RunExecutionSnapshot",
    "RunExecutionTransition",
    "RunSummary",
    "RunnerConfig",
    "RunnerError",
    "RunnerSummary",
    "SealIdentity",
    "VerificationIdentity",
    "load_runner_config",
    "run_suite",
]
