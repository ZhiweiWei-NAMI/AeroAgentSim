"""Incident observations request actual pixels from the installed camera owner."""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any, cast

from aerokernel import CommandRequest, Dependency, Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.time import cut_data
from aerokernel.values import thaw

from aeroagentsim.observations.contracts import CaptureRequest
from aeroagentsim.platform.plugins import EngineBuild


def expand(document: dict[str, Any]) -> None:
    """Reuse an authored camera manifest and storage owner (e.g. city-day)."""
    bridges = [row["config"] for row in document["engines"].values() if row["plugin"] == "traffic_capture_bridge"]
    for original in bridges:
        target = document["engines"].get(original["capture_target"])
        if target is None or target["plugin"] not in {"lowalt_city_capture", "traffic_camera_capture"}:
            continue
        config = {key: copy.deepcopy(original[key]) for key in (
            "capture_target", "request_schema", "camera", "asset_digest", "width", "height", "timeout_s", "pose_fields",
        )}
        config["camera"]["target_field"] = "lowalt.incident_position"
        config["pose_fields"]["lowalt:UAV"] = "lowalt.position"
        engine = {"plugin": "lowalt_incident_capture", "config": config}
        existing = document["engines"].get("lowalt_incident_capture")
        if existing is not None and existing != engine:
            raise ValueError("cooperation: conflicting incident capture engine / 事故拍摄引擎冲突")
        document["engines"].setdefault("lowalt_incident_capture", engine)
        return


class IncidentCapture(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build, self.cfg = build, build.config
        if set(self.cfg) != {"capture_target", "request_schema", "camera", "asset_digest", "width", "height", "timeout_s", "pose_fields"}:
            raise ValueError("cooperation capture: authored camera manifest required / 必须提供相机配置")
        if self.cfg["camera"]["revision"] != "uav-gimbal-oblique/v1":
            raise ValueError("cooperation capture: oblique UAV camera required / 必须为无人机斜视相机")
        self.refs = {ref.id: ref for ref in build.entities}
        super().__init__(Partition(
            build.id, build.id,
            consumes=tuple(Dependency(field) for field in dict.fromkeys((*self.cfg["pose_fields"].values(), "lowalt.incident_position"))),
            emits=(self.cfg["request_schema"],), subscribes=("lowalt.events",),
            message_targets=(self.cfg["capture_target"],),
        ))

    def on_inputs(self, ctx: EngineContext) -> None:
        for delivery in ctx.inbox:
            if delivery.message.schema_id != "lowalt.incident.photographed":
                continue
            payload = cast(dict[str, Any], thaw(delivery.message.payload))
            actor, incident = self.refs[payload["vehicle"]], self.refs[payload["incident"]]
            ctx.inputs = [delivery.dispatch_ref]
            entities = []
            acquired = None
            for ref in sorted(self.build.entities, key=lambda ref: ref.id):
                field = "lowalt.incident_position" if ref == incident else next((field for kind, field in self.cfg["pose_fields"].items() if self.build.registry.is_a(ref.type_id, kind)), None)
                if field is None:
                    continue
                ctx.get(ref, field)
                fact = ctx.view.field((ref, field), ctx.now)
                if not isinstance(fact, Fact):
                    raise ValueError(f"cooperation capture: missing committed pose {ref.id}.{field} / 缺少已提交位置")
                entities.append({"id": ref.id, "generation": ref.generation, "position": thaw(fact.value), "field": field, "version": asdict(fact.version), "acquired": asdict(fact.acquired)})
                if ref == actor:
                    acquired = fact.acquired
            if acquired is None:
                raise ValueError("cooperation capture: responder has no committed pose / 响应者没有已提交位置")
            camera = {
                **self.cfg["camera"],
                "subject": {"id": incident.id, "generation": incident.generation, "field": "lowalt.incident_position"},
                "snapshot": {"cut": cut_data(ctx.view.cut), "entities": entities},
            }
            request = CaptureRequest(
                f"cooperation/{incident.id}/{actor.id}/{delivery.message.id}", actor.run_id,
                actor, ctx.view.cut, camera, self.cfg["asset_digest"], acquired,
                self.cfg["width"], self.cfg["height"], self.cfg["timeout_s"],
            )
            ctx.submit(CommandRequest(self.cfg["request_schema"], self.cfg["capture_target"], ctx.now, request.to_command_data()))


def build(context: EngineBuild) -> IncidentCapture:
    return IncidentCapture(context)
