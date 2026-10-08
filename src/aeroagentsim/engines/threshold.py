"""Settled sampled gte/entered research profile over configured field slots."""

from __future__ import annotations

import math
from typing import Any, cast

from aerokernel import Dependency, Partition
from aerokernel.operations import SampleFrame
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.values import FrozenValue, thaw

from aeroagentsim.platform.plugins import EngineBuild


class Threshold(ContextEngine):
    """Narrow expanded-AST subset; every result and unknown baseline is recorded."""

    def __init__(self, build: EngineBuild) -> None:
        cfg = build.config
        self.spec = next(
            spec for spec in build.manifest.samples if spec.context_id == cfg["context"]
        )
        ast = cfg["ast"]
        if ast["op"] != "gte" or len(ast["args"]) != 2:
            raise ValueError(
                "threshold.ast: this plugin supports the explicit expanded gte subset"
            )
        left, right = ast["args"]
        if left["role"] != "subject" or len(left["path"]) != 1:
            raise ValueError("threshold.ast: select one subject vector component")
        self.field = str(left["field"])
        self.index = left["path"][0]
        schema = build.registry.field(self.field).schema
        if (
            type(self.index) is not int
            or schema["type"] != "vector"
            or not 0 <= self.index < schema["length"]
            or cfg["field"] != self.field
        ):
            raise ValueError(
                "threshold.ast: field and integer vector index must match the declared input"
            )
        value = cfg["parameters"][right["parameter"]]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("threshold.parameters: finite numeric threshold required")
        if thaw(cast(FrozenValue, self.spec.parameters)) != cfg["parameters"]:
            raise ValueError(
                "threshold.parameters: must match pinned sample parameters"
            )
        self.threshold = float(value)
        self.schema, self.topic = str(cfg["event"]), str(cfg["topic"])
        super().__init__(
            Partition(
                build.id,
                build.id,
                consumes=(Dependency(self.field),),
                emits=(self.schema,),
                message_targets=(self.topic,),
                features=("sampled",),
            )
        )

    def on_inputs(self, ctx: EngineContext) -> None:
        frames = ctx.view.sample_frames(self.spec.context_id)
        previous = frames[-1] if frames else None
        prior = (
            thaw(cast(FrozenValue, previous.frame.result))
            if previous is not None
            else None
        )
        if previous is not None:
            ctx.inputs.append(previous.version)
        states: dict[str, Any] = {}
        entered: list[tuple[str, int]] = []
        for role, ref in self.spec.bindings.items():
            ctx.get(ref, self.field)
            fact = ctx.view.field((ref, self.field), ctx.now)
            if not isinstance(fact, Fact):
                states[role] = {
                    "status": "required_input",
                    "reason": "no current valid position",
                }
                continue
            if (
                fact.producer != self.spec.sources[role]
                or (fact.acquired.clock_id, fact.acquired.mapping_id)
                != self.spec.clocks[role]
            ):
                states[role] = {
                    "status": "invalid_input",
                    "reason": "source or native clock mismatch",
                }
                continue
            value = thaw(fact.value)
            if not isinstance(value, list) or type(value[self.index]) not in (
                int,
                float,
            ):
                raise TypeError(
                    "threshold.field: expected the configured numeric vector component"
                )
            component = value[self.index]
            assert isinstance(component, (int, float))
            current = component >= self.threshold
            states[role] = {"status": "known", "value": current}
            before = (
                cast(dict[str, Any], prior["states"]).get(role)
                if isinstance(prior, dict)
                else None
            )
            if (
                previous is not None
                and isinstance(before, dict)
                and before.get("status") == "known"
                and before.get("value") is False
                and current
                and previous.frame.physical_ns < ctx.now.ns
            ):
                entered.append((ref.id, previous.frame.physical_ns))
        ctx.ops.append(
            SampleFrame(
                self.spec.context_id,
                ctx.now.ns,
                ctx.view.cut,
                self.spec.bindings,
                self.spec.clocks,
                {"states": states},
                self.spec.sources,
                tuple(ctx.inputs),
            )
        )
        for entity, previous_ns in entered:
            ctx.emit(
                self.schema,
                {"entity": entity, "from_ns": previous_ns, "to_ns": ctx.now.ns},
                topic=self.topic,
            )


def build(context: EngineBuild) -> Threshold:
    return Threshold(context)
