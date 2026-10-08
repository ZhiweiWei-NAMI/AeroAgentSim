"""Declared camera geometry over actual motion fields, published as observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aerokernel import Activate, Dependency, EntityRef, Interval, Partition, Stamp
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.values import thaw

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform.plugins import EngineBuild

from .common import finite, ns, read, vec
from .geometry import footprint


@dataclass
class Acquisition:
    ref: EntityRef
    subject: EntityRef
    acquired_ns: int
    available_ns: int
    sample: dict[str, Any]


class Inspection(ContextEngine):
    """Flat-plane pinhole sensing; camera parameters are selected entity fields.

    Records distinguish computation acquisition, publication availability and the
    actual source pose stamp. A held pose remains a held pose in the record.
    Area targets are counterclockwise rectangles, waypoint targets are points.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        c = self.cfg
        self.fields: dict[str, str] = dict(c["fields"])
        self.refs = {r.id: r for r in build.entities}
        self.subjects = tuple(self.refs[i] for i in c["subjects"])
        self.sensors = {i: self.refs[s] for i, s in c["sensors"].items()}
        if set(self.sensors) != {r.id for r in self.subjects}:
            raise ValueError("inspection: every subject must bind exactly one sensor")
        self.period = ns(c["sample_period_ns"], "sample period", 1)
        self.delay = ns(c["availability_delay_ns"], "availability delay", 1)
        self.start = ns(c["start_ns"], "inspection start", 1)
        self.end = ns(c["end_ns"], "inspection end", self.start)
        finite(c["ground_z_m"], "ground plane")
        ids: set[str] = set()
        for target in c["targets"]:
            if target["id"] in ids:
                raise ValueError("duplicate inspection target")
            ids.add(target["id"])
            if target["kind"] == "waypoint":
                vec([*target["point"], c["ground_z_m"]])
            elif target["kind"] == "area":
                x0, y0, x1, y1 = (finite(v, "target bounds") for v in target["bounds"])
                if x1 <= x0 or y1 <= y0:
                    raise ValueError("inspection area must have positive area")
            else:
                raise ValueError("inspection target: waypoint or area required")
        if not ids:
            raise ValueError("inspection targets must be nonempty")
        self.pending: dict[str, Acquisition] = {}
        self.ready: list[Acquisition] = []
        self.sequence = 0
        fields = tuple(c["produces"])
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=fields,
                consumes=tuple(Dependency(f) for f in c["consumes"]),
                lifecycle=True,
                emits=(c["event_schema"],),
                message_targets=(c["event_topic"],),
                relation_produces=(c["subject_relation"],),
                features=("relations",),
            ),
            policies=policies(fields),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)
        ctx.wake_at(self.start, {"inspection": "acquire"})

    def _acquire(self, ctx: EngineContext) -> None:
        for subject in self.subjects:
            sensor = self.sensors[subject.id]
            f = self.fields
            position = vec(read(ctx, subject, f["position"]))
            attitude = vec(read(ctx, sensor, f["attitude"]))
            horizontal = finite(
                read(ctx, sensor, f["horizontal_fov"]), "horizontal FOV"
            )
            vertical = finite(read(ctx, sensor, f["vertical_fov"]), "vertical FOV")
            source = ctx.view.field((subject, f["position"]), ctx.now)
            if not isinstance(source, Fact):
                raise TypeError("inspection pose requires an actual committed fact")
            stamp = source.acquired
            sample: dict[str, Any] = {
                "model": "flat_ground_pinhole",
                "subject": subject.id,
                "position_enu_m": list(position),
                "attitude_deg": list(attitude),
                "horizontal_fov_deg": horizontal,
                "vertical_fov_deg": vertical,
                "ground_z_m": self.cfg["ground_z_m"],
                "pose_acquisition": {
                    "clock": stamp.clock_id,
                    "mapping": stamp.mapping_id,
                    "numerator": stamp.numerator,
                    "denominator": stamp.denominator,
                },
                "pose_available_ns": source.available.ns,
            }
            # Geometry outside this model's bounded domain remains an explicit
            # unsuccessful sensor sample, with no invented footprint or image.
            try:
                polygon = footprint(
                    position, attitude, horizontal, vertical, self.cfg["ground_z_m"]
                )
            except ValueError as exc:
                sample.update(footprint=None, reason=str(exc), valid=False)
            else:
                sample.update(footprint=[list(p) for p in polygon], valid=True)
            identity = f"inspection/{self.build.id}/{self.sequence:06d}"
            self.sequence += 1
            ref = EntityRef(
                subject.run_id, subject.epoch, identity, 0, self.cfg["record_type"]
            )
            acquisition = Acquisition(
                ref, subject, ctx.now.ns, ctx.now.ns + self.delay, sample
            )
            self.pending[identity] = acquisition
            ctx.wake_at(
                acquisition.available_ns, {"inspection": "release", "record": identity}
            )
        if ctx.now.ns + self.period <= self.end:
            ctx.wake_at(ctx.now.ns + self.period, {"inspection": "acquire"})

    def _release(self, ctx: EngineContext, identity: str) -> None:
        acquisition = self.pending.pop(identity)
        ctx.create(acquisition.ref)
        self.ready.append(acquisition)
        ctx.ops.append(Activate(self.partition.id))

    def _publish(self, ctx: EngineContext, acquisition: Acquisition) -> None:
        values: dict[str, Any] = {
            "sample": acquisition.sample,
            "acquired": acquisition.acquired_ns,
            "available": ctx.now.ns,
            "subject": {"$ref": acquisition.subject.to_data()},
        }
        for key, value in values.items():
            ctx.set(
                acquisition.ref,
                self.fields[key],
                value,
                acquired=Stamp("canonical", acquisition.acquired_ns, 1, "canonical"),
                valid=Interval(ctx.now, None),
            )
        ctx.relate(
            f"{acquisition.ref.id}/subject",
            self.cfg["subject_relation"],
            acquisition.ref,
            acquisition.subject,
            acquired=Stamp("canonical", acquisition.acquired_ns, 1, "canonical"),
            valid=Interval(ctx.now, None),
        )
        ctx.emit(
            self.cfg["event_schema"],
            {
                "record": acquisition.ref.id,
                "acquired_ns": acquisition.acquired_ns,
                "available_ns": ctx.now.ns,
                "sample": acquisition.sample,
            },
            topic=self.cfg["event_topic"],
        )

    def on_inputs(self, ctx: EngineContext) -> None:
        for acquisition in self.ready:
            self._publish(ctx, acquisition)
        self.ready.clear()
        for dirty in ctx.dirty:
            if dirty.kind != "timer":
                continue
            data = thaw(dirty.payload)
            if not isinstance(data, dict) or "inspection" not in data:
                raise ValueError("inspection timer must carry its operation")
            if data["inspection"] == "acquire":
                self._acquire(ctx)
            elif data["inspection"] == "release":
                identity = data["record"]
                if not isinstance(identity, str):
                    raise TypeError("inspection timer record identity must be a string")
                self._release(ctx, identity)
            else:
                raise ValueError("unknown inspection timer")


def build(context: EngineBuild) -> Inspection:
    return Inspection(context)
