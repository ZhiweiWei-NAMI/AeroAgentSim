"""Traffic intent -> real camera/storage receipts -> independently verified upload."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, cast

from aerokernel import CommandRequest, Dependency, EntityRef, Interval, Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.time import cut_data
from aerokernel.values import thaw

from aeroagentsim.observations.contracts import CaptureRequest
from aeroagentsim.platform.plugins import EngineBuild

REQUESTED = "traffic.capture.requested"
STORED = "traffic.capture.stored"
ACCEPTED = "traffic.capture.accepted"
FAILED = "traffic.capture.failed"
VERIFIED = "traffic.capture.upload_verified"


@dataclass
class Job:
    request: CaptureRequest
    incident: EntityRef
    record: dict[str, Any] = field(default_factory=dict)
    accepted: bool = False


class TrafficCaptureBridge(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        if set(self.cfg) != {
            "capture_target",
            "request_schema",
            "upload_schema",
            "upload_lag_ns",
            "camera",
            "asset_digest",
            "width",
            "height",
            "timeout_s",
            "pose_fields",
        }:
            raise ValueError(
                "traffic capture: explicit camera, pose sources, owner and upload lag required"
            )
        if type(self.cfg["upload_lag_ns"]) is not int or self.cfg["upload_lag_ns"] <= 0:
            raise ValueError("traffic capture: positive upload lag required")
        self.jobs: dict[str, Job] = {}
        self.pending: deque[tuple[str, str]] = deque()
        self.correlations: dict[str, tuple[str, str]] = {}
        super().__init__(
            Partition(
                build.id,
                build.id,
                consumes=tuple(
                    Dependency(f)
                    for f in (
                        *self.cfg["pose_fields"].values(),
                        "traffic.capture.source_cut",
                        "traffic.capture.png_sha256",
                    )
                ),
                produces=("traffic.capture.incident",),
                emits=(
                    STORED,
                    ACCEPTED,
                    FAILED,
                    self.cfg["request_schema"],
                    self.cfg["upload_schema"],
                ),
                subscribes=(REQUESTED, VERIFIED),
                message_targets=(STORED, ACCEPTED, FAILED, self.cfg["capture_target"]),
                relation_produces=("traffic.capture-actor", "traffic.capture-incident"),
                features=("relations",),
            )
        )

    def _submit(self, ctx: EngineContext, job: Job, stage: str) -> None:
        payload = (
            job.request.to_command_data()
            if stage == "render"
            else {"request_id": job.request.request_id, "digest": job.record["digest"]}
        )
        schema = (
            self.cfg["request_schema"]
            if stage == "render"
            else self.cfg["upload_schema"]
        )
        ctx.submit(CommandRequest(schema, self.cfg["capture_target"], ctx.now, payload))
        self.pending.append((job.request.request_id, stage))

    def _payload(self, job: Job) -> dict[str, Any]:
        return {
            "actor": {"$ref": job.request.actor.to_data()},
            "incident": {"$ref": job.incident.to_data()},
            "request_id": job.request.request_id,
            "source_cut": job.request.source_cut.index,
        }

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            ctx.inputs = [delivery.dispatch_ref]
            payload = cast(dict[str, Any], thaw(delivery.message.payload))
            if delivery.message.schema_id == REQUESTED:
                identity = payload["request_id"]
                if identity in self.jobs:
                    raise ValueError("traffic capture: request ID already used")
                actor = EntityRef.from_data(payload["actor"]["$ref"])
                incident = EntityRef.from_data(payload["incident"]["$ref"])
                entities: list[dict[str, Any]] = []
                acquired = None
                for ref in sorted(
                    ctx.view._store.lives, key=lambda r: (r.id, r.generation)
                ):
                    field_id = self.cfg["pose_fields"].get(ref.type_id)
                    if field_id is None or not ctx.view._store.alive(
                        ref, ctx.view.cut, ctx.now
                    ):
                        continue
                    ctx.get(ref, field_id)
                    fact = ctx.view.field((ref, field_id), ctx.now)
                    if not isinstance(fact, Fact):
                        raise TypeError(
                            f"traffic camera: missing committed {ref.id}.{field_id}"
                        )
                    entities.append(
                        {
                            "id": ref.id,
                            "generation": ref.generation,
                            "position": thaw(fact.value),
                            "field": field_id,
                            "version": asdict(fact.version),
                            "acquired": asdict(fact.acquired),
                        }
                    )
                    if ref == actor:
                        acquired = fact.acquired
                if acquired is None:
                    raise ValueError(
                        "traffic camera: requested actor has no measured pose"
                    )
                camera = {
                    **self.cfg["camera"],
                    "snapshot": {"cut": cut_data(ctx.view.cut), "entities": entities},
                }
                request = CaptureRequest(
                    identity,
                    actor.run_id,
                    actor,
                    ctx.view.cut,
                    camera,
                    self.cfg["asset_digest"],
                    acquired,
                    self.cfg["width"],
                    self.cfg["height"],
                    self.cfg["timeout_s"],
                )
                job = Job(request, incident)
                self.jobs[identity] = job
                self._submit(ctx, job, "render")
            elif delivery.message.schema_id == VERIFIED:
                job = self.jobs[payload["request_id"]]
                if (
                    job.accepted
                    or payload["actor"] != {"$ref": job.request.actor.to_data()}
                    or payload["source_cut"] != cut_data(job.request.source_cut)
                    or payload["digest"] != job.record["digest"]
                ):
                    raise ValueError(
                        "traffic capture: upload verification identity/cut/digest mismatch"
                    )
                job.accepted = True
                ctx.emit(
                    ACCEPTED,
                    {
                        **self._payload(job),
                        "capture": payload["record"],
                        "png_sha256": payload["digest"],
                        "reason": "edge verified stored PNG bytes and source identity",
                    },
                    topic=ACCEPTED,
                )
            else:
                raise ValueError("traffic capture: undeclared input event")
        for dirty in ctx.dirty:
            ctx.inputs = [dirty.cause]
            payload = cast(dict[str, Any], thaw(dirty.payload))
            if dirty.kind == "timer":
                self._submit(ctx, self.jobs[payload["upload"]], "upload")
            elif dirty.kind == "receipt":
                command, status = payload["command_id"], payload["status"]
                if status == "submitted":
                    self.correlations[command] = self.pending.popleft()
                    continue
                identity, stage = self.correlations[command]
                job = self.jobs[identity]
                if status in {"failed", "rejected", "canceled"}:
                    ctx.emit(
                        FAILED,
                        {**self._payload(job), "reason": str(payload["result"])},
                        topic=FAILED,
                    )
                elif status == "succeeded" and stage == "render":
                    result = payload["result"]
                    if (
                        result["status"] != "succeeded"
                        or result["request_id"] != identity
                    ):
                        raise ValueError(
                            "traffic capture: terminal receipt has inconsistent request identity"
                        )
                    job.record = result["record"]
                    record_ref = EntityRef.from_data(job.record["ref"]["$ref"])
                    ctx.set(
                        record_ref,
                        "traffic.capture.incident",
                        {"$ref": job.incident.to_data()},
                        acquired=job.request.acquired,
                        valid=Interval(ctx.now, None),
                    )
                    for suffix, relation, target in (
                        ("actor", "traffic.capture-actor", job.request.actor),
                        ("incident", "traffic.capture-incident", job.incident),
                    ):
                        ctx.relate(
                            record_ref.id + "/" + suffix,
                            relation,
                            record_ref,
                            target,
                            valid=Interval(ctx.now, None),
                            acquired=job.request.acquired,
                        )
                    ctx.emit(
                        STORED,
                        {
                            **self._payload(job),
                            "capture": {"$ref": record_ref.to_data()},
                            "png_sha256": job.record["digest"],
                            "reason": "actual headless Three.js PNG persisted",
                        },
                        topic=STORED,
                    )
                    ctx.wake_at(
                        ctx.now.ns + self.cfg["upload_lag_ns"], {"upload": identity}
                    )


def build(context: EngineBuild) -> TrafficCaptureBridge:
    return TrafficCaptureBridge(context)
