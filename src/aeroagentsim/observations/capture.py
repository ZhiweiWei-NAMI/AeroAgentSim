"""Kernel capture owner: actual pixels, lifecycle-separated metadata and receipts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from aerokernel import Activate, EntityRef, Interval, Partition
from aerokernel.sdk import Command, ContextEngine, EngineContext
from aerokernel.values import Value

from aeroagentsim.engines.common import bootstrap_owned, policies
from aeroagentsim.platform.plugins import EngineBuild
from aeroagentsim.scenario.loader import contract, text

from .artifacts import ArtifactStore
from .contracts import CaptureReceipt, CaptureRequest, content_digest
from .renderer import BrowserRenderer, Renderer, StubRenderer

# Semantic keys are mapped onto explicitly selected registry fields by config.
METADATA = frozenset(
    {
        "request_id",
        "actor",
        "source_cut",
        "acquired",
        "camera_digest",
        "asset_digest",
        "byte_count",
        "digest",
        "storage_status",
        "renderer_mode",
    }
)


@dataclass(frozen=True)
class InvalidRequest:
    request_id: str
    reason: str


def decode_request(value: Value) -> CaptureRequest | InvalidRequest:
    try:
        return CaptureRequest.from_command_data(value)
    except (ValueError, TypeError, KeyError) as exc:
        # The registered command schema establishes record/string shape before
        # dispatch. Preserve its actual correlation even for invalid dimensions,
        # stamps or deadlines; no failed decoder can fault a valid run prefix.
        correlation = value.get("request_id") if isinstance(value, dict) else None
        if not isinstance(correlation, str):
            raise TypeError(
                "capture request schema must declare string request_id"
            ) from exc
        return InvalidRequest(correlation, str(exc))


@dataclass(frozen=True)
class StoredCapture:
    command: Command[CaptureRequest]
    ref: EntityRef
    record: dict[str, Any]


class Capture(ContextEngine):
    """Offline blocking render profile; replay reads WAL and stored artifacts.

    Online browser replies use the service's existing worker ingress path; this
    engine also verifies those storage results and emits a separate typed event.
    No domain task state or physical telemetry is owned here.
    """

    def __init__(
        self,
        build: EngineBuild,
        *,
        renderer: Renderer | None = None,
        store: ArtifactStore | None = None,
    ) -> None:
        self.build = build
        cfg = contract(
            build.config,
            "capture.config",
            {
                "request_schema",
                "storage_result_schema",
                "accepted_schema",
                "accepted_topic",
                "record_type",
                "record_id_prefix",
                "fields",
                "run_directory",
                "renderer",
            },
            {"ingress_stream_id"},
        )
        self.cfg = cfg
        self.fields: dict[str, str] = cfg["fields"]
        if not isinstance(self.fields, dict) or set(self.fields) != METADATA:
            raise ValueError("capture.fields: declare every metadata key explicitly")
        produces = tuple(self.fields.values())
        if len(set(produces)) != len(produces):
            raise ValueError("capture.fields: metadata slots must be distinct")
        self.record_type = text(cfg["record_type"], "capture.record_type")
        build.registry.is_a(self.record_type, self.record_type)
        for slot in produces:
            descriptor = build.registry.field(slot)
            if not build.registry.is_a(self.record_type, descriptor.declaring_type):
                raise ValueError("capture.fields: inapplicable record metadata slot")
        self.prefix = text(cfg["record_id_prefix"], "capture.record_id_prefix")
        candidate = self._ref("binding-check")
        if set(build.owned_fields(candidate)) != set(produces):
            raise ValueError(
                "capture.fields: record metadata requires declared capture ownership"
            )
        for name, kind in (
            ("request_schema", "command"),
            ("storage_result_schema", "command"),
            ("accepted_schema", "event"),
        ):
            message_descriptor = build.registry.message(cfg[name])
            if (
                message_descriptor.kind != kind
                or message_descriptor.schema.get("type") != "record"
            ):
                raise ValueError("capture schemas must be registered typed records")
            if kind == "command" and message_descriptor.result_schema is None:
                raise ValueError("capture commands require a typed result_schema")
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=produces,
                lifecycle=True,
                commands=(cfg["request_schema"], cfg["storage_result_schema"]),
                emits=(cfg["accepted_schema"],),
                message_targets=(cfg["accepted_topic"],),
            ),
            policies=policies(produces),
        )
        self.store = (
            ArtifactStore(Path(cfg["run_directory"])) if store is None else store
        )
        self.renderer = (
            self._renderer(cfg["renderer"]) if renderer is None else renderer
        )
        self.ready: list[StoredCapture] = []
        self.accepted: set[str] = set()
        self.seen: set[str] = set()

    def _renderer(self, config: Any) -> Renderer:
        if not isinstance(config, dict):
            raise TypeError(
                "capture.renderer: explicit provider configuration required"
            )
        mode = config["mode"]
        if mode == "browser":
            spec = contract(
                config,
                "capture.renderer",
                {"mode", "viewer_url", "node_modules", "timeout_s"},
                {"browser_executable", "software_gl", "service_run_id"},
            )
            return BrowserRenderer(
                spec["viewer_url"],
                node_modules=Path(spec["node_modules"]),
                timeout_s=spec["timeout_s"],
                browser_executable=Path(spec["browser_executable"])
                if "browser_executable" in spec
                else None,
                software_gl=spec.get("software_gl", True),
                service_run_id=spec.get("service_run_id"),
            )
        if mode == "stub":
            spec = contract(config, "capture.renderer", {"mode", "fixtures"})
            return StubRenderer(
                {key: Path(path) for key, path in spec["fixtures"].items()}
            )
        raise ValueError("capture.renderer.mode: select browser or stub explicitly")

    def _ref(self, request_id: str) -> EntityRef:
        return EntityRef(
            self.build.manifest.run_id,
            self.build.manifest.epoch,
            self.prefix + content_digest(request_id.encode()),
            0,
            self.record_type,
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def _validate_source(self, ctx: EngineContext, request: CaptureRequest) -> None:
        if (
            request.run_id != self.build.manifest.run_id
            or request.actor.epoch != self.build.manifest.epoch
        ):
            raise ValueError("capture namespace/epoch mismatch")
        if request.source_cut.index > ctx.view.cut.index:
            raise ValueError("capture source cut is not in the committed prefix")
        source = ctx.view._store
        source.check_cut(request.source_cut)
        if not source.alive(
            request.actor, request.source_cut, request.source_cut.instant
        ):
            raise ValueError(
                "capture actor generation was not alive at the exact source cut"
            )
        if not source.alive(request.actor, ctx.view.cut, ctx.now):
            raise ValueError(
                "capture actor generation is stale at request availability"
            )

    def _publish(self, ctx: EngineContext, capture: StoredCapture) -> None:
        request = capture.command.payload
        record = capture.record
        values = {
            "request_id": request.request_id,
            "actor": {"$ref": request.actor.to_data()},
            "source_cut": record["request"]["source_cut"],
            "acquired": record["request"]["acquired"],
            "camera_digest": record["camera_digest"],
            "asset_digest": request.asset_digest,
            "byte_count": record["byte_count"],
            "digest": record["digest"],
            "storage_status": "stored",
            "renderer_mode": record["renderer_mode"],
        }
        for name, slot in self.fields.items():
            ctx.set(
                capture.ref,
                slot,
                values[name],
                acquired=request.acquired,
                valid=Interval(ctx.now, None),
            )
        result = {"ref": {"$ref": capture.ref.to_data()}, **record}
        ctx.succeed(
            capture.command,
            CaptureReceipt("succeeded", request.request_id, result).to_data(),
        )

    def on_inputs(self, ctx: EngineContext) -> None:
        for capture in self.ready:
            self._publish(ctx, capture)
        self.ready.clear()
        for delivery in ctx.inbox:
            schema = delivery.message.schema_id
            if schema == self.cfg["request_schema"]:
                decoded_command = ctx.remember(delivery, decode_request)
                if isinstance(decoded_command.payload, InvalidRequest):
                    ctx.accept(decoded_command)
                    ctx.execute(decoded_command)
                    ctx.fail(
                        decoded_command,
                        CaptureReceipt(
                            "failed",
                            decoded_command.payload.request_id,
                            reason=decoded_command.payload.reason,
                        ).to_data(),
                    )
                    continue
                command = cast(Command[CaptureRequest], decoded_command)
                ctx.accept(command)
                ctx.execute(command)
                request = command.payload
                try:
                    self._validate_source(ctx, request)
                    if request.request_id in self.seen:
                        raise ValueError(
                            "capture request already has a recorded acquisition"
                        )
                    self.store.register(request)
                    frame = self.renderer.render(request)
                    record = self.store.put(
                        request, frame.png, renderer_mode=frame.mode
                    )
                    ref = self._ref(request.request_id)
                    # Create and initialize in separate committed lifecycle/writer waves.
                    ctx.create(ref)
                    self.seen.add(request.request_id)
                    self.ready.append(StoredCapture(command, ref, record))
                    ctx.ops.append(Activate(self.partition.id))
                except TimeoutError as exc:
                    self.store.close_request(request.request_id, "timeout")
                    ctx.fail(
                        command,
                        CaptureReceipt(
                            "timeout", request.request_id, reason=str(exc)
                        ).to_data(),
                    )
                except (ValueError, TypeError, RuntimeError, OSError, KeyError) as exc:
                    if request.request_id not in self.seen:
                        self.store.close_request(request.request_id, "failed")
                    ctx.fail(
                        command,
                        CaptureReceipt(
                            "failed", request.request_id, reason=str(exc)
                        ).to_data(),
                    )
            elif schema == self.cfg["storage_result_schema"]:
                command_data = ctx.remember(delivery, lambda value: value)
                ctx.accept(command_data)
                ctx.execute(command_data)
                data = command_data.payload
                try:
                    spec = contract(
                        data, "capture.storage_result", {"request_id", "digest"}
                    )
                    record = self.store.get(spec["request_id"])
                    request = CaptureRequest.from_data(record["request"])
                    self._validate_source(ctx, request)
                    if record["digest"] != spec["digest"]:
                        raise ValueError(
                            "upload digest differs from actual stored bytes"
                        )
                    if request.request_id in self.accepted:
                        raise ValueError(
                            "request already has a recorded upload acceptance"
                        )
                    event = {
                        "request_id": request.request_id,
                        "actor": {"$ref": request.actor.to_data()},
                        "source_cut": record["request"]["source_cut"],
                        "digest": record["digest"],
                        "byte_count": record["byte_count"],
                        "record": {"$ref": self._ref(request.request_id).to_data()},
                    }
                    # Schema validation precedes any claim of upload acceptance.
                    self.build.registry.validate(
                        self.build.registry.message(self.cfg["accepted_schema"]).schema,
                        event,
                    )
                    ctx.emit(
                        self.cfg["accepted_schema"],
                        event,
                        topic=self.cfg["accepted_topic"],
                    )
                    self.accepted.add(request.request_id)
                    ctx.succeed(
                        command_data,
                        {"status": "upload_verified", "digest": record["digest"]},
                    )
                except (ValueError, TypeError, RuntimeError, OSError, KeyError) as exc:
                    ctx.fail(command_data, {"status": "failed", "reason": str(exc)})
            else:
                raise ValueError("capture received an undeclared command")

    def close(self) -> None:
        self.renderer.close()
        super().close()


def replay_capture(store: ArtifactStore, request_id: str) -> dict[str, Any]:
    """Read the stored observation; no renderer or plugin factory is called."""
    return store.get(request_id)


def build(context: EngineBuild) -> Capture:
    return Capture(context)
