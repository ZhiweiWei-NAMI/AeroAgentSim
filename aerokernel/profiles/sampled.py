"""Settled Boolean evaluations and an optional discrete entered profile."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..engine import Partition
from ..errors import KernelError
from ..operations import SampleFrame
from ..sampling import SampleSpec
from ..sdk import ContextEngine, EngineContext


@dataclass(frozen=True)
class Evaluation:
    """An explicit predicate result; unresolved and inapplicable are not false."""

    status: str
    value: bool | None
    diagnostics: tuple[str, ...] = ()
    applicability: bool | None = True

    def __post_init__(self) -> None:
        if self.status not in {"known", "unresolved", "invalid"}:
            raise KernelError("EVALUATION_STATUS", "unknown evaluation status")
        if (
            (self.status == "known" and type(self.value) is not bool)
            or (self.status != "known" and self.value is not None)
            or (self.applicability is not None and type(self.applicability) is not bool)
            or any(not isinstance(d, str) for d in self.diagnostics)
        ):
            raise KernelError("EVALUATION_VALUE", "explicit typed evaluation required")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def to_data(self) -> dict[str, Any]:
        """Portable result, including resolution and applicability diagnostics."""
        return {
            "status": self.status,
            "value": self.value,
            "diagnostics": list(self.diagnostics),
            "applicability": self.applicability,
        }


@dataclass(frozen=True)
class EnteredResult:
    """A sampled crossing interval, without a claim of continuous observation."""

    status: str
    value: bool | None
    crossing: tuple[int, int] | None = None
    diagnostics: tuple[str, ...] = ()

    def to_data(self) -> dict[str, Any]:
        """Portable profile result for the evaluator journal."""
        return {
            "status": self.status,
            "value": self.value,
            "crossing": None if self.crossing is None else list(self.crossing),
            "diagnostics": list(self.diagnostics),
        }


def _evaluation(frame: SampleFrame) -> Evaluation:
    data = frame.result
    if not isinstance(data, Mapping) or not {
        "status",
        "value",
        "diagnostics",
        "applicability",
    }.issubset(data):
        return Evaluation("unresolved", None, ("missing predicate evaluation",))
    try:
        return Evaluation(
            data["status"],
            data["value"],
            tuple(data["diagnostics"]),
            data["applicability"],
        )
    except (KernelError, TypeError):
        return Evaluation("invalid", None, ("malformed predicate evaluation",))


def entered(current: SampleFrame, history: Sequence[SampleFrame]) -> EnteredResult:
    """Compare only the latest strictly earlier, identity-compatible frame."""
    if any(
        f.context_id != current.context_id or f.physical_ns >= current.physical_ns
        for f in history
    ):
        return EnteredResult("invalid", None, diagnostics=("invalid frame history",))
    times = [f.physical_ns for f in history]
    if len(times) != len(set(times)):
        return EnteredResult("invalid", None, diagnostics=("duplicate frame time",))
    evaluation = _evaluation(current)
    if evaluation.status != "known" or evaluation.applicability is not True:
        return EnteredResult(
            evaluation.status if evaluation.status != "known" else "unresolved",
            None,
            diagnostics=(*evaluation.diagnostics, "predicate not resolved/applicable"),
        )
    if not history:
        return EnteredResult("unresolved", None, diagnostics=("no prior frame",))
    previous = max(history, key=lambda f: f.physical_ns)
    if (
        dict(previous.bindings) != dict(current.bindings)
        or dict(previous.sources) != dict(current.sources)
        or dict(previous.clocks) != dict(current.clocks)
    ):
        return EnteredResult(
            "invalid", None, diagnostics=("identity/source/clock changed",)
        )
    prior = _evaluation(previous)
    if prior.status != "known" or prior.applicability is not True:
        return EnteredResult(
            "unresolved", None, diagnostics=("prior predicate unresolved",)
        )
    if prior.value is False and evaluation.value is True:
        return EnteredResult("known", True, (previous.physical_ns, current.physical_ns))
    return EnteredResult("known", False)


class SampledEvaluator(ContextEngine):
    """SDK driver that publishes one authored evaluation per settled invocation."""

    def __init__(self, partition: Partition, spec: SampleSpec) -> None:
        if partition.id != spec.partition:
            raise KernelError("SAMPLE_DECLARATION", "evaluator and spec differ")
        super().__init__(partition)
        self.spec = spec

    def evaluate(self, ctx: EngineContext) -> Evaluation:
        """Implement the model's actual predicate calculation."""
        raise NotImplementedError

    def frame(self, ctx: EngineContext, result: object) -> SampleFrame:
        """Construct a frame using the actual invocation and pinned declaration."""
        return SampleFrame(
            self.spec.context_id,
            ctx.now.ns,
            ctx.view.cut,
            self.spec.bindings,
            self.spec.clocks,
            result,
            self.spec.sources,
            ctx._causes(),
        )

    def on_inputs(self, ctx: EngineContext) -> None:
        """Evaluate once and journal every resolution status."""
        result = self.evaluate(ctx)
        if not isinstance(result, Evaluation):
            raise KernelError("EVALUATION_VALUE", "evaluate must return Evaluation")
        ctx._append(self.frame(ctx, result.to_data()))


class EnteredEvaluator(SampledEvaluator):
    """Emit only an observed false-to-true transition between settled frames."""

    def __init__(
        self,
        partition: Partition,
        spec: SampleSpec,
        *,
        event_schema: str | None = None,
        topic: str | None = None,
    ) -> None:
        super().__init__(partition, spec)
        if (event_schema is None) != (topic is None):
            raise KernelError("SAMPLE_EVENT", "declare both schema and topic")
        self.event_schema, self.topic = event_schema, topic

    def on_inputs(self, ctx: EngineContext) -> None:
        evaluation = self.evaluate(ctx)
        if not isinstance(evaluation, Evaluation):
            raise KernelError("EVALUATION_VALUE", "evaluate must return Evaluation")
        history = ctx.view.sample_frames(self.spec.context_id)
        if history:
            ctx.inputs.append(history[-1].version)
        frame = self.frame(ctx, evaluation.to_data())
        result = entered(frame, tuple(row.frame for row in history))
        cause = ctx._append(
            self.frame(ctx, {**evaluation.to_data(), "entered": result.to_data()})
        )
        if result.value is True and self.event_schema is not None:
            ctx.inputs.append(cause)
            assert result.crossing is not None
            ctx.emit(
                self.event_schema,
                {
                    "context_id": self.spec.context_id,
                    "start_ns": result.crossing[0],
                    "end_ns": result.crossing[1],
                },
                topic=self.topic,
            )
