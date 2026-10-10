"""Read-only committed snapshots shared by low-altitude algorithm slots."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from aerokernel import EntityRef
from aerokernel.sdk import EngineContext

from aeroagentsim.algorithms.protocols import Pad, Snapshot, Task, Vehicle, Zone
from aeroagentsim.packs.common import finite, read, vec
from aeroagentsim.platform.plugins import EngineBuild


def refs(build: EngineBuild, type_id: str) -> tuple[EntityRef, ...]:
    return tuple(
        sorted(
            (r for r in build.entities if build.registry.is_a(r.type_id, type_id)),
            key=lambda r: r.id,
        )
    )


@dataclass(frozen=True)
class SnapshotIndex:
    """Immutable authored type groups scoped to one EngineBuild, never facts.

    Lowalt snapshots have always used build.entities, not the live lifecycle
    population. Index that same fixed tuple, preserving subtype and ID order;
    a new build/registry must construct its own index.
    """

    build: EngineBuild
    groups: Mapping[str, tuple[EntityRef, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "groups",
            MappingProxyType(
                {
                    kind: refs(self.build, kind)
                    for kind in (
                        "lowalt:UAV",
                        "lowalt:Site",
                        "lowalt:Task",
                        "lowalt:AirspaceZone",
                        "lowalt:ChargingPad",
                        "lowalt:Environment",
                    )
                }
            ),
        )

    def select(self, type_id: str) -> tuple[EntityRef, ...]:
        return self.groups[type_id]


def snapshot(
    ctx: EngineContext, build: EngineBuild, *, index: SnapshotIndex | None = None
) -> Snapshot:
    if index is not None and index.build is not build:
        raise ValueError("snapshot index belongs to a different engine build")
    select = index.select if index is not None else lambda kind: refs(build, kind)
    vehicles = tuple(
        Vehicle(
            r.id,
            vec(read(ctx, r, "lowalt.position")),
            finite(read(ctx, r, "lowalt.soc"), "soc", 0),
            finite(read(ctx, r, "lowalt.payload_kg"), "payload", 0),
            finite(read(ctx, r, "lowalt.payload_capacity_kg"), "payload capacity", 0),
            not read(ctx, r, "lowalt.charging")
            and not read(ctx, r, "lowalt.weather_held")
            and all(abs(v) <= 1e-6 for v in vec(read(ctx, r, "lowalt.velocity"))),
        )
        for r in select("lowalt:UAV")
    )
    sites = {
        r.id: vec(read(ctx, r, "lowalt.site_position")) for r in select("lowalt:Site")
    }
    tasks = tuple(
        Task(
            r.id,
            read(ctx, r, "lowalt.task_kind"),
            read(ctx, r, "lowalt.task_priority"),
            sites[read(ctx, r, "lowalt.task_origin")],
            sites[read(ctx, r, "lowalt.task_destination")],
            finite(read(ctx, r, "lowalt.task_payload_kg"), "task payload", 0),
        )
        for r in select("lowalt:Task")
        if read(ctx, r, "lowalt.task_state") == "queued"
    )
    zones = tuple(
        Zone(
            r.id,
            read(ctx, r, "lowalt.zone_kind"),
            vec(read(ctx, r, "lowalt.zone_lower")),
            vec(read(ctx, r, "lowalt.zone_upper")),
            read(ctx, r, "lowalt.zone_active"),
        )
        for r in select("lowalt:AirspaceZone")
    )
    pads = tuple(
        Pad(
            r.id,
            sites[r.id],
            finite(read(ctx, r, "lowalt.pad_power_w"), "pad power", 0),
        )
        for r in select("lowalt:ChargingPad")
    )
    environments = select("lowalt:Environment")
    if len(environments) != 1:
        raise ValueError("lowalt requires one committed wind environment")
    wind = vec(read(ctx, environments[0], "lowalt.wind"))
    return Snapshot(ctx.now.ns, vehicles, tasks, zones, pads, wind)


SNAPSHOT_FIELDS = (
    "lowalt.position",
    "lowalt.velocity",
    "lowalt.soc",
    "lowalt.payload_kg",
    "lowalt.payload_capacity_kg",
    "lowalt.charging",
    "lowalt.weather_held",
    "lowalt.site_position",
    "lowalt.pad_power_w",
    "lowalt.task_state",
    "lowalt.task_kind",
    "lowalt.task_priority",
    "lowalt.task_origin",
    "lowalt.task_destination",
    "lowalt.task_payload_kg",
    "lowalt.zone_kind",
    "lowalt.zone_lower",
    "lowalt.zone_upper",
    "lowalt.zone_active",
    "lowalt.wind",
)
