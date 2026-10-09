"""Domain-neutral consumption contributions shared by execution and planning.

Factories registered in ``aeroagentsim.models`` receive the selected descriptors
and configuration. Models read through EngineContext; the host publishes facts
with those reads' version causes and remains the sole writer of its bound slots.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Protocol, cast

from aerokernel import EntityRef, MemoryRegistry
from aerokernel.sdk import EngineContext
from aerokernel.state import Absent
from aerokernel.values import thaw


def finite(value: Any, name: str, *, minimum: float = 0.0) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"model.{name}: finite value >= {minimum} required")
    return float(value)


def trajectory_seconds(length: float, speed: float, accel: float) -> float:
    """Rest-to-rest bounded triangular/trapezoidal trajectory duration, seconds."""
    ramp = min(speed / accel, math.sqrt(length / accel))
    return 2 * ramp + max(0.0, (length - accel * ramp * ramp) / speed)


@dataclass(frozen=True)
class Segment:
    """ENU displacement (m), elapsed time (s), and actual path length (m)."""

    displacement: tuple[float, float, float]
    elapsed_s: float
    distance_m: float

    def __post_init__(self) -> None:
        if len(self.displacement) != 3 or any(
            type(v) not in (int, float) or not math.isfinite(v)
            for v in self.displacement
        ):
            raise ValueError("model.segment: finite ENU displacement required")
        finite(self.elapsed_s, "segment.elapsed_s")
        finite(self.distance_m, "segment.distance_m")


@dataclass(frozen=True)
class ModelBuild:
    config: dict[str, Any]
    energy: dict[str, Any]
    registry: MemoryRegistry
    entities: tuple[EntityRef, ...]


class ConsumptionModel(Protocol):
    """A read-only contribution; hosts own publication and reserve policy.

    ``dependencies`` declares all fields that may be read. Budget estimates use
    the currently latched environment, unless a plugin explicitly models a
    forecast. A model must be deterministic and must not mutate kernel state.
    """

    @property
    def dependencies(self) -> tuple[str, ...]: ...

    def consumption(
        self, ctx: EngineContext, ref: EntityRef, segment: Segment
    ) -> float: ...

    def budget(
        self, ctx: EngineContext, ref: EntityRef, segments: tuple[Segment, ...]
    ) -> float: ...


class LinearConsumption:
    """Original idle power plus joules per metre, with original operation order."""

    dependencies: tuple[str, ...] = ()

    def __init__(self, build: ModelBuild) -> None:
        if build.config:
            raise ValueError(
                "linear model: config must be empty; use energy coefficients"
            )
        self.idle = finite(build.energy["idle_w"], "idle_w")
        self.per_m = finite(build.energy["per_m_j"], "per_m_j")

    def consumption(
        self, ctx: EngineContext, ref: EntityRef, segment: Segment
    ) -> float:
        return self.idle * segment.elapsed_s + self.per_m * segment.distance_m

    def budget(
        self, ctx: EngineContext, ref: EntityRef, segments: tuple[Segment, ...]
    ) -> float:
        # Aggregate before multiplying, preserving the legacy route estimator.
        return self.per_m * sum(s.distance_m for s in segments) + self.idle * sum(
            s.elapsed_s for s in segments
        )


class WindConsumption(LinearConsumption):
    """Illustrative headwind exposure; ground-track motion remains unchanged."""

    def __init__(self, build: ModelBuild) -> None:
        super().__init__(ModelBuild({}, build.energy, build.registry, build.entities))
        c = build.config
        required = {"entity", "field", "coefficient_w_per_m_s2"}
        if set(c) - (required | {"vector_index"}) or required - set(c):
            raise ValueError(
                "wind model: declare entity, field, coefficient_w_per_m_s2"
            )
        refs = [r for r in build.entities if r.id == c["entity"]]
        if len(refs) != 1:
            raise ValueError("wind model: bind exactly one actual environment entity")
        self.environment = refs[0]
        self.field = str(c["field"])
        build.registry.field(self.field)
        self.coefficient = finite(c["coefficient_w_per_m_s2"], "wind coefficient")
        self.index: int | None = c.get("vector_index")
        if self.index is not None and (type(self.index) is not int or self.index < 0):
            raise ValueError("wind model: vector_index must be a nonnegative integer")
        self.dependencies = (self.field,)

    def _wind(self, ctx: EngineContext) -> tuple[float, ...]:
        actual = ctx.get(self.environment, self.field)
        if isinstance(actual, Absent):
            raise TypeError(
                "wind model: weather producer must publish a covered wind fact"
            )
        value: Any = thaw(actual)
        if self.index is not None:
            if not isinstance(value, list) or self.index >= len(value):
                raise ValueError(
                    "wind model: vector_index outside actual weather value"
                )
            value = value[self.index]
        if (
            not isinstance(value, list)
            or len(value) not in (2, 3)
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
        ):
            raise ValueError("wind model: actual ENU wind vector in m/s required")
        return tuple(float(v) for v in value) + ((0.0,) if len(value) == 2 else ())

    def _extra(self, wind: tuple[float, ...], segment: Segment) -> float:
        norm = math.sqrt(sum(v * v for v in segment.displacement))
        exposure = (
            max(0.0, -sum(w * d for w, d in zip(wind, segment.displacement)) / norm)
            if norm
            else math.sqrt(sum(w * w for w in wind))
        )
        return self.coefficient * exposure**2 * segment.elapsed_s

    def consumption(
        self, ctx: EngineContext, ref: EntityRef, segment: Segment
    ) -> float:
        return super().consumption(ctx, ref, segment) + self._extra(
            self._wind(ctx), segment
        )

    def budget(
        self, ctx: EngineContext, ref: EntityRef, segments: tuple[Segment, ...]
    ) -> float:
        wind = self._wind(ctx)
        return super().budget(ctx, ref, segments) + sum(
            self._extra(wind, s) for s in segments
        )


class ModelCatalog:
    """Resolve installed contribution factories without requiring engine subclasses."""

    def build(self, plugin: str, build: ModelBuild) -> ConsumptionModel:
        entries = entry_points(group="aeroagentsim.models")
        selected = [e for e in entries if e.name == plugin]
        if len(selected) > 1:
            raise ValueError(f"duplicate aeroagentsim.models entry point {plugin!r}")
        if selected:
            factory = cast("ModelFactory", selected[0].load())
            model = factory(build)
        elif plugin == "linear":
            model = LinearConsumption(build)
        elif plugin == "wind":
            model = WindConsumption(build)
        else:
            raise ValueError(f"unknown aeroagentsim.models plugin {plugin!r}")
        for field in model.dependencies:
            build.registry.field(field)
        return model


class ModelFactory(Protocol):
    def __call__(self, build: ModelBuild) -> ConsumptionModel: ...


@dataclass(frozen=True)
class MotionModel:
    """Shared trajectory timing and consumption instance for one configured mover."""

    consumption: ConsumptionModel
    speed: float
    accel: float
    step_ns: int

    def segment(self, start: tuple[float, ...], target: tuple[float, ...]) -> Segment:
        delta = tuple(b - a for a, b in zip(start, target))
        if len(delta) != 3:
            raise ValueError("model segment: ENU three-vectors required")
        length = math.sqrt(sum(v * v for v in delta))
        duration = trajectory_seconds(length, self.speed, self.accel)
        seconds = max(
            self.step_ns / 1e9,
            math.ceil(duration * 1e9 / self.step_ns) * self.step_ns / 1e9,
        )
        return Segment((delta[0], delta[1], delta[2]), seconds, length)


def motion_model(
    config: dict[str, Any], registry: MemoryRegistry, entities: tuple[EntityRef, ...]
) -> MotionModel:
    selection = config.get("consumption_model", {"plugin": "linear", "config": {}})
    if not isinstance(selection, dict) or set(selection) != {"plugin", "config"}:
        raise ValueError("consumption_model: declare plugin and config")
    if not isinstance(selection["config"], dict):
        raise TypeError("consumption_model.config: mapping required")
    step_ns = config["step_ns"]
    if type(step_ns) is not int or step_ns <= 0:
        raise ValueError("model.step_ns: positive integer required")
    build = ModelBuild(selection["config"], config["energy"], registry, entities)
    # A plugin installation must not silently select a different model for an
    # unchanged legacy scenario. Entry points apply to explicit selections.
    consumption = (
        ModelCatalog().build(selection["plugin"], build)
        if "consumption_model" in config
        else LinearConsumption(build)
    )
    return MotionModel(
        consumption,
        finite(config["max_speed_m_s"], "max_speed_m_s", minimum=1e-300),
        finite(config["max_accel_m_s2"], "max_accel_m_s2", minimum=1e-300),
        step_ns,
    )
