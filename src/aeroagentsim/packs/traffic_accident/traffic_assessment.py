"""Measured-pose feasibility records using the mover's actual shared model."""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

from aerokernel import Dependency, EntityRef, Interval, Partition, Stamp
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.values import Value

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.models import MotionModel, Segment
from aeroagentsim.packs.common import finite, read, vec
from aeroagentsim.platform.plugins import EngineBuild

PREFIX = "traffic.assessment."
FIELDS = tuple(
    PREFIX + x
    for x in ("distance_m", "eta_s", "budget_j", "in_region", "feasible", "evidence")
)


def metrics(
    position: tuple[float, float, float],
    target: tuple[float, float, float],
    anchor: tuple[float, float, float],
    radius: float,
    energy: float,
    reserve: float,
    model: MotionModel,
) -> tuple[Segment, bool, float]:
    """The region is horizontal about the authored incident anchor, not an edge pose."""
    region = math.hypot(position[0] - anchor[0], position[1] - anchor[1]) <= finite(
        radius, "region radius", 0
    )
    finite(energy, "measured energy", 0)
    finite(reserve, "reserve", 0)
    return model.segment(position, target), region, energy - reserve


class TrafficAssessment(ContextEngine):
    """Recompute only on declared committed dependencies; retain source versions.

    No award, task interruption or decision orchestration happens here. These
    records are planning estimates; native flight observations remain separate.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        c = build.config
        if set(c) != {
            "motion_owner",
            "subjects",
            "position_field",
            "energy_field",
            "target_field",
            "anchor",
            "radius_m",
            "reserve_j",
            "link_command",
        }:
            raise ValueError(
                "traffic_assessment.config: explicit subjects/model/fields/region/reserve required"
            )
        self.model = build.models[c["motion_owner"]]
        self.refs = {r.id: r for r in build.entities}
        self.anchor = vec(c["anchor"])
        self.radius = finite(c["radius_m"], "region radius", 0)
        self.reserve = finite(c["reserve_j"], "reserve", 0)
        self.links: dict[str, tuple[EntityRef, str]] = {}
        for actor, row in c["subjects"].items():
            self.refs[actor]
            self.refs[row["task"]]
            ref = self.refs[row["record"]]
            if set(build.owned_fields(ref)) != set(FIELDS):
                raise ValueError("assessment ownership: every computed field required")
        dependencies = (
            c["position_field"],
            c["energy_field"],
            c["target_field"],
            "traffic.bid.actor",
        ) + self.model.consumption.dependencies
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=FIELDS,
                lifecycle=True,
                consumes=tuple(Dependency(f) for f in dict.fromkeys(dependencies)),
                commands=(c["link_command"],),
                relation_produces=("traffic.assessment-bid",),
                features=("relations",),
            ),
            policies=policies(FIELDS),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def on_inputs(self, ctx: EngineContext) -> None:
        c = self.build.config
        for delivery in ctx.inbox:
            command = ctx.remember(delivery, payload)
            actor = EntityRef.from_data(command.payload["actor"]["$ref"])
            bid = EntityRef.from_data(command.payload["bid"]["$ref"])
            if actor.id not in c["subjects"] or self.refs.get(actor.id) != actor:
                ctx.reject(
                    command, {"reason": "actor not in the configured assessment domain"}
                )
                continue
            recorded_actor = read(ctx, bid, "traffic.bid.actor")
            if recorded_actor != command.payload["actor"]:
                ctx.reject(
                    command,
                    {"reason": "bid's actual subject does not match actor generation"},
                )
                continue
            record = self.refs[c["subjects"][actor.id]["record"]]
            previous = self.links.get(actor.id)
            ctx.accept(command)
            ctx.execute(command)
            if previous is None or previous[0] != bid:
                if previous is not None:
                    ctx.unrelate(previous[1])
                edge_id = f"{record.id}/bid/{command.id}"
                ctx.relate(
                    edge_id,
                    "traffic.assessment-bid",
                    record,
                    bid,
                    valid=Interval(ctx.now, None),
                    acquired=Stamp("canonical", ctx.now.ns, 1, "canonical"),
                )
                self.links[actor.id] = (bid, edge_id)
            ctx.succeed(command, {"assessment": {"$ref": record.to_data()}})
        for identity, row in sorted(c["subjects"].items()):
            actor, task, record = (
                self.refs[identity],
                self.refs[row["task"]],
                self.refs[row["record"]],
            )
            position = vec(read(ctx, actor, c["position_field"]))
            target = vec(read(ctx, task, c["target_field"]))
            energy = finite(read(ctx, actor, c["energy_field"]), "energy", 0)
            segment, region, available = metrics(
                position,
                target,
                self.anchor,
                self.radius,
                energy,
                self.reserve,
                self.model,
            )
            budget = finite(
                self.model.consumption.budget(ctx, actor, (segment,)),
                "computed budget",
                0,
            )
            evidence: list[dict[str, Any]] = []
            for ref, field in (
                (actor, c["position_field"]),
                (actor, c["energy_field"]),
                (task, c["target_field"]),
            ):
                fact = ctx.view.field((ref, field), ctx.now)
                if not isinstance(fact, Fact):
                    raise TypeError(
                        f"assessment source lacks a committed fact: {ref.id}.{field}"
                    )
                evidence.append(
                    {
                        "entity": {"$ref": ref.to_data()},
                        "field": field,
                        "version": asdict(fact.version),
                        "acquired": asdict(fact.acquired),
                    }
                )
            values = (
                segment.distance_m,
                segment.elapsed_s,
                budget,
                region,
                budget <= available,
                {
                    "model": c["motion_owner"],
                    "kind": "rest_to_rest_current_weather/v1",
                    "sources": evidence,
                },
            )
            for field, value in zip(FIELDS, values):
                ctx.set(record, field, value)


def build(context: EngineBuild) -> TrafficAssessment:
    return TrafficAssessment(context)


def payload(value: Value) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("assessment command requires a typed record")
    return value
