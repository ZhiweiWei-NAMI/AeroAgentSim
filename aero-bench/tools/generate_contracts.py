#!/usr/bin/env python3
"""Generate canonical JSON Schemas, OpenAPI, and TypeScript contracts."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pydantic
from pydantic import TypeAdapter

from aero_bench.agent.bridge import AgentDriverConfig
from aero_bench.agent.session_contracts import (
    AgentSessionManifest,
    InteractionRecord,
    SessionDescriptor,
    ToolExecutionResult,
)
from aero_bench.authoring.compilation_contracts import (
    AuthoringApiErrorResponse,
    CityCompilationResult,
    CityCompileRequest,
    SceneRegistrationCatalog,
)
from aero_bench.authoring.workspace import CityWorkspaceDraft
from aero_bench.authoring.traffic_preview_contracts import (
    TrafficPreviewJob,
    TrafficPreviewProfileCatalog,
    TrafficPreviewRequest,
)
from aero_bench.config.models import StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.control.contracts import (
    ControlApiErrorResponse,
    ControlCatalog,
    ControlStreamEvent,
    PublicRunEventStreamEvent,
    ReplayAccessRequest,
    ReplayAccessResponse,
    RunStatusResponse,
    RunTransitionEvent,
    RuntimeControlRequest,
    RuntimeControlResponse,
    SceneStateStreamEvent,
    StartRunRequest,
    StartRunResponse,
)
from aero_bench.providers.ns3 import NetworkMailboxObservationPayload
from aero_bench.runtime.contracts import SceneState, StateSample
from aero_bench.runtime.events import AgentInteraction, RunEvent
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.contracts import (
    AppliedWrench,
    AirspaceTransition,
    CameraFrameRef,
    DemoTaskPackage,
    EngineWindow,
)
from aero_bench.trace.contracts import (
    PublicReplayIndex,
    PublicReplayManifest,
    PublicScenario,
    PublicTrace,
)
from aero_bench.world.resolved import ResolvedScenario

_SCHEMA_ROOT = _ROOT / "schemas" / "generated"
_FRONTEND_GENERATED = _ROOT / "frontend" / "src" / "generated"
_FRONTEND_SCHEMA_ROOT = _FRONTEND_GENERATED / "schemas"
_TYPESCRIPT_PATH = _FRONTEND_GENERATED / "aero-bench-contracts.ts"
_VALIDATORS_PATH = _FRONTEND_GENERATED / "contract-validators.ts"
_MANIFEST_PATH = _SCHEMA_ROOT / "manifest.json"
_JSON2TS_VERSION = "16.0.0"
_AJV_VERSION = "8.20.0"
_AJV_FORMATS_VERSION = "3.0.1"
_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_OPENAPI_VERSION = "3.1.0"
_GENERATOR_ID = "aero-bench.contract-generator/v1"
_PYDANTIC_VERSION = "2.11.3"

SchemaFactory = Callable[[], dict[str, object]]


class AeroBenchContractBundle(StrictModel):
    """Synthetic root used only to generate one closed TypeScript type graph."""

    resolved_scenario: ResolvedScenario
    resolved_run: ResolvedRunSpec
    agent_driver_config: AgentDriverConfig
    agent_session: SessionDescriptor
    agent_session_manifest: AgentSessionManifest
    model_interaction: InteractionRecord
    agent_tool_execution_result: ToolExecutionResult
    state_sample: StateSample
    scene_state: SceneState
    agent_interaction: AgentInteraction
    run_event: RunEvent
    network_mailbox_observation: NetworkMailboxObservationPayload
    public_trace: PublicTrace
    public_replay_index: PublicReplayIndex
    public_replay_manifest: PublicReplayManifest
    urban_demo_package: DemoTaskPackage
    urban_engine_window: EngineWindow
    urban_airspace_transition: AirspaceTransition
    urban_applied_wrench: AppliedWrench
    urban_camera_frame: CameraFrameRef
    control_catalog: ControlCatalog
    start_run_request: StartRunRequest
    start_run_response: StartRunResponse
    replay_access_request: ReplayAccessRequest
    replay_access_response: ReplayAccessResponse
    run_status_response: RunStatusResponse
    runtime_control_request: RuntimeControlRequest
    runtime_control_response: RuntimeControlResponse
    control_stream_event: ControlStreamEvent
    control_error_response: ControlApiErrorResponse
    city_workspace: CityWorkspaceDraft
    city_compile_request: CityCompileRequest
    city_compilation_result: CityCompilationResult
    native_scene_catalog: SceneRegistrationCatalog
    authoring_error_response: AuthoringApiErrorResponse
    traffic_preview_profile_catalog: TrafficPreviewProfileCatalog
    traffic_preview_request: TrafficPreviewRequest
    traffic_preview_job: TrafficPreviewJob


@dataclass(frozen=True)
class SchemaTarget:
    slug: str
    title: str
    source: str
    factory: SchemaFactory

    @property
    def relative_path(self) -> Path:
        return Path(f"{self.slug}.schema.json")

    @property
    def schema_id(self) -> str:
        return f"urn:aero-bench:schema:{self.slug}"


def _model_factory(model: type[StrictModel]) -> SchemaFactory:
    return lambda: model.model_json_schema(
        by_alias=True,
        mode="validation",
        ref_template="#/$defs/{model}",
    )


def _control_stream_schema() -> dict[str, object]:
    return TypeAdapter(ControlStreamEvent).json_schema(
        by_alias=True,
        mode="validation",
        ref_template="#/$defs/{model}",
    )


_TARGETS: tuple[SchemaTarget, ...] = (
    SchemaTarget(
        "city-workspace", "CityWorkspaceDraft",
        "aero_bench.authoring.workspace.CityWorkspaceDraft", _model_factory(CityWorkspaceDraft),
    ),
    SchemaTarget(
        "city-compile-request", "CityCompileRequest",
        "aero_bench.authoring.compilation_contracts.CityCompileRequest", _model_factory(CityCompileRequest),
    ),
    SchemaTarget(
        "city-compilation-result", "CityCompilationResult",
        "aero_bench.authoring.compilation_contracts.CityCompilationResult", _model_factory(CityCompilationResult),
    ),
    SchemaTarget(
        "native-scene-catalog", "SceneRegistrationCatalog",
        "aero_bench.authoring.compilation_contracts.SceneRegistrationCatalog", _model_factory(SceneRegistrationCatalog),
    ),
    SchemaTarget(
        "authoring-error-response", "AuthoringApiErrorResponse",
        "aero_bench.authoring.compilation_contracts.AuthoringApiErrorResponse", _model_factory(AuthoringApiErrorResponse),
    ),
    SchemaTarget(
        "traffic-preview-profile-catalog", "TrafficPreviewProfileCatalog",
        "aero_bench.authoring.traffic_preview_contracts.TrafficPreviewProfileCatalog",
        _model_factory(TrafficPreviewProfileCatalog),
    ),
    SchemaTarget(
        "traffic-preview-request", "TrafficPreviewRequest",
        "aero_bench.authoring.traffic_preview_contracts.TrafficPreviewRequest",
        _model_factory(TrafficPreviewRequest),
    ),
    SchemaTarget(
        "traffic-preview-job", "TrafficPreviewJob",
        "aero_bench.authoring.traffic_preview_contracts.TrafficPreviewJob",
        _model_factory(TrafficPreviewJob),
    ),
    SchemaTarget(
        "agent-driver-config",
        "AgentDriverConfig",
        "aero_bench.agent.bridge.AgentDriverConfig",
        _model_factory(AgentDriverConfig),
    ),
    SchemaTarget(
        "agent-session",
        "SessionDescriptor",
        "aero_bench.agent.session_contracts.SessionDescriptor",
        _model_factory(SessionDescriptor),
    ),
    SchemaTarget(
        "agent-session-manifest",
        "AgentSessionManifest",
        "aero_bench.agent.session_contracts.AgentSessionManifest",
        _model_factory(AgentSessionManifest),
    ),
    SchemaTarget(
        "model-interaction",
        "InteractionRecord",
        "aero_bench.agent.session_contracts.InteractionRecord",
        _model_factory(InteractionRecord),
    ),
    SchemaTarget(
        "agent-tool-execution-result",
        "ToolExecutionResult",
        "aero_bench.agent.session_contracts.ToolExecutionResult",
        _model_factory(ToolExecutionResult),
    ),
    SchemaTarget(
        "resolved-scenario",
        "ResolvedScenario",
        "aero_bench.world.resolved.ResolvedScenario",
        _model_factory(ResolvedScenario),
    ),
    SchemaTarget(
        "resolved-run",
        "ResolvedRunSpec",
        "aero_bench.config.resolver.ResolvedRunSpec",
        _model_factory(ResolvedRunSpec),
    ),
    SchemaTarget(
        "state-sample",
        "StateSample",
        "aero_bench.runtime.contracts.StateSample",
        _model_factory(StateSample),
    ),
    SchemaTarget(
        "scene-state",
        "SceneState",
        "aero_bench.runtime.contracts.SceneState",
        _model_factory(SceneState),
    ),
    SchemaTarget(
        "agent-interaction",
        "AgentInteraction",
        "aero_bench.runtime.events.AgentInteraction",
        _model_factory(AgentInteraction),
    ),
    SchemaTarget(
        "run-event",
        "RunEvent",
        "aero_bench.runtime.events.RunEvent",
        _model_factory(RunEvent),
    ),
    SchemaTarget(
        "network-mailbox-observation",
        "NetworkMailboxObservationPayload",
        "aero_bench.providers.ns3.provider.NetworkMailboxObservationPayload",
        _model_factory(NetworkMailboxObservationPayload),
    ),
    SchemaTarget(
        "public-scenario",
        "PublicScenario",
        "aero_bench.trace.contracts.PublicScenario",
        _model_factory(PublicScenario),
    ),
    SchemaTarget(
        "public-trace",
        "PublicTrace",
        "aero_bench.trace.contracts.PublicTrace",
        _model_factory(PublicTrace),
    ),
    SchemaTarget(
        "public-replay-manifest",
        "PublicReplayManifest",
        "aero_bench.trace.contracts.PublicReplayManifest",
        _model_factory(PublicReplayManifest),
    ),
    SchemaTarget(
        "public-replay-index",
        "PublicReplayIndex",
        "aero_bench.trace.contracts.PublicReplayIndex",
        _model_factory(PublicReplayIndex),
    ),
    SchemaTarget(
        "urban-recovery-demo-package",
        "DemoTaskPackage",
        "aero_bench.tasks.urban_recovery_demo.contracts.DemoTaskPackage",
        _model_factory(DemoTaskPackage),
    ),
    SchemaTarget(
        "urban-engine-window",
        "EngineWindow",
        "aero_bench.tasks.urban_recovery_demo.contracts.EngineWindow",
        _model_factory(EngineWindow),
    ),
    SchemaTarget(
        "urban-airspace-transition",
        "AirspaceTransition",
        "aero_bench.tasks.urban_recovery_demo.contracts.AirspaceTransition",
        _model_factory(AirspaceTransition),
    ),
    SchemaTarget(
        "urban-applied-wrench",
        "AppliedWrench",
        "aero_bench.tasks.urban_recovery_demo.contracts.AppliedWrench",
        _model_factory(AppliedWrench),
    ),
    SchemaTarget(
        "urban-camera-frame",
        "CameraFrameRef",
        "aero_bench.tasks.urban_recovery_demo.contracts.CameraFrameRef",
        _model_factory(CameraFrameRef),
    ),
    SchemaTarget(
        "control-catalog",
        "ControlCatalog",
        "aero_bench.control.contracts.ControlCatalog",
        _model_factory(ControlCatalog),
    ),
    SchemaTarget(
        "start-run-request",
        "StartRunRequest",
        "aero_bench.control.contracts.StartRunRequest",
        _model_factory(StartRunRequest),
    ),
    SchemaTarget(
        "start-run-response",
        "StartRunResponse",
        "aero_bench.control.contracts.StartRunResponse",
        _model_factory(StartRunResponse),
    ),
    SchemaTarget(
        "replay-access-request",
        "ReplayAccessRequest",
        "aero_bench.control.contracts.ReplayAccessRequest",
        _model_factory(ReplayAccessRequest),
    ),
    SchemaTarget(
        "replay-access-response",
        "ReplayAccessResponse",
        "aero_bench.control.contracts.ReplayAccessResponse",
        _model_factory(ReplayAccessResponse),
    ),
    SchemaTarget(
        "run-status-response",
        "RunStatusResponse",
        "aero_bench.control.contracts.RunStatusResponse",
        _model_factory(RunStatusResponse),
    ),
    SchemaTarget(
        "runtime-control-request",
        "RuntimeControlRequest",
        "aero_bench.control.contracts.RuntimeControlRequest",
        _model_factory(RuntimeControlRequest),
    ),
    SchemaTarget(
        "runtime-control-response",
        "RuntimeControlResponse",
        "aero_bench.control.contracts.RuntimeControlResponse",
        _model_factory(RuntimeControlResponse),
    ),
    SchemaTarget(
        "run-transition-event",
        "RunTransitionEvent",
        "aero_bench.control.contracts.RunTransitionEvent",
        _model_factory(RunTransitionEvent),
    ),
    SchemaTarget(
        "scene-state-stream-event",
        "SceneStateStreamEvent",
        "aero_bench.control.contracts.SceneStateStreamEvent",
        _model_factory(SceneStateStreamEvent),
    ),
    SchemaTarget(
        "public-run-event-stream-event",
        "PublicRunEventStreamEvent",
        "aero_bench.control.contracts.PublicRunEventStreamEvent",
        _model_factory(PublicRunEventStreamEvent),
    ),
    SchemaTarget(
        "control-stream-event",
        "ControlStreamEvent",
        "aero_bench.control.contracts.ControlStreamEvent",
        _control_stream_schema,
    ),
    SchemaTarget(
        "control-error-response",
        "ControlApiErrorResponse",
        "aero_bench.control.contracts.ControlApiErrorResponse",
        _model_factory(ControlApiErrorResponse),
    ),
    SchemaTarget(
        "aero-bench-contracts",
        "AeroBenchContractBundle",
        "tools.generate_contracts.AeroBenchContractBundle",
        _model_factory(AeroBenchContractBundle),
    ),
)

_FRONTEND_VALIDATORS: tuple[tuple[str, str, str], ...] = (
    ("PublicScenario", "public-scenario", "publicScenario"),
    ("SceneState", "scene-state", "sceneState"),
    ("PublicTrace", "public-trace", "publicTrace"),
    ("PublicReplayIndex", "public-replay-index", "publicReplayIndex"),
    ("PublicReplayManifest", "public-replay-manifest", "publicReplayManifest"),
    ("ControlCatalog", "control-catalog", "controlCatalog"),
    ("StartRunRequest", "start-run-request", "startRunRequest"),
    ("StartRunResponse", "start-run-response", "startRunResponse"),
    ("ReplayAccessRequest", "replay-access-request", "replayAccessRequest"),
    ("ReplayAccessResponse", "replay-access-response", "replayAccessResponse"),
    ("RunStatusResponse", "run-status-response", "runStatusResponse"),
    (
        "RuntimeControlRequest",
        "runtime-control-request",
        "runtimeControlRequest",
    ),
    (
        "RuntimeControlResponse",
        "runtime-control-response",
        "runtimeControlResponse",
    ),
    ("RunTransitionEvent", "run-transition-event", "runTransitionEvent"),
    (
        "SceneStateStreamEvent",
        "scene-state-stream-event",
        "sceneStateStreamEvent",
    ),
    (
        "PublicRunEventStreamEvent",
        "public-run-event-stream-event",
        "publicRunEventStreamEvent",
    ),
    (
        "ControlApiErrorResponse",
        "control-error-response",
        "controlErrorResponse",
    ),
    ("SceneRegistrationCatalog", "native-scene-catalog", "sceneRegistrationCatalog"),
    ("CityCompileRequest", "city-compile-request", "cityCompileRequest"),
    ("CityCompilationResult", "city-compilation-result", "cityCompilationResult"),
    ("TrafficPreviewProfileCatalog", "traffic-preview-profile-catalog", "trafficPreviewProfileCatalog"),
    ("TrafficPreviewRequest", "traffic-preview-request", "trafficPreviewRequest"),
    ("TrafficPreviewJob", "traffic-preview-job", "trafficPreviewJob"),
    (
        "AuthoringApiErrorResponse",
        "authoring-error-response",
        "authoringErrorResponse",
    ),
)


def _canonical_document(document: object) -> bytes:
    return canonical_json_bytes(document) + b"\n"


def _schema_document(target: SchemaTarget) -> dict[str, object]:
    generated = target.factory()
    generated["title"] = target.title
    return {
        "$id": target.schema_id,
        "$schema": _SCHEMA_DIALECT,
        **generated,
    }


def _rewrite_component_references(value: object, component_name: str) -> object:
    if isinstance(value, dict):
        return {
            key: _rewrite_component_references(item, component_name)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_component_references(item, component_name) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return f"#/components/schemas/{component_name}/$defs/{value[8:]}"
    return value


def _schema_response(schema_name: str, description: str) -> dict[str, object]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema_name}"}
            }
        },
    }


def _error_responses() -> dict[str, object]:
    return {
        status: {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ControlApiErrorResponse"}
                }
            },
        }
        for status, description in (
            ("400", "Invalid request"),
            ("401", "Authentication failed"),
            ("403", "Origin or CSRF check failed"),
            ("404", "Run or route not found"),
            ("409", "Run state conflict"),
            ("413", "Request body too large"),
            ("429", "Rate limit exceeded"),
            ("500", "Control service failure"),
        )
    }


def _openapi_document(
    schemas: dict[str, dict[str, object]],
) -> dict[str, object]:
    component_targets = tuple(
        target
        for target in _TARGETS
        if target.slug
        in {
            "control-catalog",
            "start-run-request",
            "start-run-response",
            "replay-access-request",
            "replay-access-response",
            "run-status-response",
            "runtime-control-request",
            "runtime-control-response",
            "run-transition-event",
            "scene-state-stream-event",
            "public-run-event-stream-event",
            "control-stream-event",
            "control-error-response",
            "public-trace",
            "public-replay-manifest",
            "city-compile-request",
            "city-compilation-result",
            "native-scene-catalog",
            "authoring-error-response",
            "traffic-preview-profile-catalog",
            "traffic-preview-request",
            "traffic-preview-job",
        }
    )
    components = {
        target.title: _rewrite_component_references(
            {
                key: value
                for key, value in schemas[target.slug].items()
                if key not in {"$id", "$schema"}
            },
            target.title,
        )
        for target in component_targets
    }
    error_responses = _error_responses()
    run_id_parameter = {
        "name": "run_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
    csrf_parameter = {
        "name": "X-Aero-Bench-CSRF",
        "in": "header",
        "required": True,
        "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
    return {
        "openapi": _OPENAPI_VERSION,
        "jsonSchemaDialect": _SCHEMA_DIALECT,
        "info": {
            "title": "AERO-BENCH Run Control API",
            "version": "1.0.0",
            "description": (
                "Authenticated control plane for resolver-backed benchmark runs. "
                "No route exposes Provider RPC or host shell access."
            ),
        },
        "paths": {
            "/authoring/v1/traffic-preview-profiles": {
                "get": {
                    "operationId": "getTrafficPreviewProfiles", "security": [],
                    "responses": {
                        "200": _schema_response("TrafficPreviewProfileCatalog", "Pinned offline SUMO preview profiles"),
                        "500": _schema_response("AuthoringApiErrorResponse", "Profile integrity failure"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Traffic preview generation is not configured"),
                    },
                },
            },
            "/authoring/v1/traffic-previews": {
                "post": {
                    "operationId": "createTrafficPreview", "security": [],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "$ref": "#/components/schemas/TrafficPreviewRequest",
                        }}},
                    },
                    "responses": {
                        "202": _schema_response("TrafficPreviewJob", "Immutable preview job; not formal execution"),
                        "400": _schema_response("AuthoringApiErrorResponse", "Invalid request or scene binding"),
                        "404": _schema_response("AuthoringApiErrorResponse", "Unknown traffic profile"),
                        "409": _schema_response("AuthoringApiErrorResponse", "Profile or publication conflict"),
                        "415": _schema_response("AuthoringApiErrorResponse", "JSON content type required"),
                        "500": _schema_response("AuthoringApiErrorResponse", "Profile integrity or generation failure"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Traffic preview generation is not configured"),
                    },
                },
            },
            "/authoring/v1/traffic-previews/{job_id}": {
                "parameters": [{"name": "job_id", "in": "path", "required": True,
                                "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}],
                "get": {
                    "operationId": "getTrafficPreviewJob", "security": [],
                    "responses": {
                        "200": _schema_response("TrafficPreviewJob", "Current explicit recording/audit state"),
                        "404": _schema_response("AuthoringApiErrorResponse", "Unknown traffic preview job"),
                        "500": _schema_response("AuthoringApiErrorResponse", "Preview integrity failure"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Traffic preview generation is not configured"),
                    },
                },
            },
            "/authoring/v1/traffic-previews/{job_id}/assets/{sha256}": {
                "parameters": [
                    {"name": "job_id", "in": "path", "required": True,
                     "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
                    {"name": "sha256", "in": "path", "required": True,
                     "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
                ],
                "get": {
                    "operationId": "getAuditedTrafficPreviewTrace", "security": [],
                    "responses": {
                        "200": {"description": "Exact digest-pinned public engineering-preview trace",
                                "content": {"application/json": {"schema": {"type": "object"}}}},
                        "404": _schema_response("AuthoringApiErrorResponse", "Unknown job or public artifact"),
                        "409": _schema_response("AuthoringApiErrorResponse", "Independent audit has not passed"),
                        "500": _schema_response("AuthoringApiErrorResponse", "Published trace integrity failure"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Traffic preview generation is not configured"),
                    },
                },
            },
            "/authoring/v1/native-scenes": {
                "get": {
                    "operationId": "getNativeSceneRegistrations",
                    "security": [],
                    "responses": {
                        "200": _schema_response("SceneRegistrationCatalog", "Explicit native scene registrations"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Compiler is not configured"),
                    },
                },
            },
            "/authoring/v1/native-scenes/{registration_id}/scene": {
                "parameters": [{"name": "registration_id", "in": "path", "required": True,
                                "schema": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"}}],
                "get": {
                    "operationId": "getNativePublicScenario", "security": [],
                    "responses": {
                        "200": {"description": "Exact pinned native public scenario",
                                "content": {"application/json": {"schema": {
                                    "$ref": "#/components/schemas/StartRunResponse/$defs/PublicScenario",
                                }}}},
                        "404": _schema_response("AuthoringApiErrorResponse", "Unknown native registration"),
                        "500": _schema_response("AuthoringApiErrorResponse", "Native input integrity failure"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Compiler is not configured"),
                    },
                },
            },
            "/authoring/v1/native-scenes/{registration_id}/assets/{sha256}": {
                "parameters": [
                    {"name": "registration_id", "in": "path", "required": True,
                     "schema": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"}},
                    {"name": "sha256", "in": "path", "required": True,
                     "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
                ],
                "get": {
                    "operationId": "getNativePublicAsset", "security": [],
                    "responses": {
                        "200": {"description": "Pinned public asset or licence bytes",
                                "content": {"application/octet-stream": {"schema": {
                                    "type": "string", "contentEncoding": "binary",
                                }}}},
                        "404": _schema_response("AuthoringApiErrorResponse", "Asset is not declared public"),
                        "500": _schema_response("AuthoringApiErrorResponse", "Native input integrity failure"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Compiler is not configured"),
                    },
                },
            },
            "/authoring/v1/compilations": {
                "post": {
                    "operationId": "compileCityWorkspace",
                    "security": [],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {
                            "$ref": "#/components/schemas/CityCompileRequest",
                        }}},
                    },
                    "responses": {
                        "201": _schema_response("CityCompilationResult", "Immutable compiled bundle; not executed"),
                        "422": _schema_response("CityCompilationResult", "Explicit native execution blockers"),
                        "400": _schema_response("AuthoringApiErrorResponse", "Invalid strict compile request"),
                        "415": _schema_response("AuthoringApiErrorResponse", "JSON content type required"),
                        "500": _schema_response("AuthoringApiErrorResponse", "Native registration or compilation failure"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Compiler is not configured"),
                    },
                },
            },
            "/authoring/v1/compilations/{compilation_id}": {
                "parameters": [{"name": "compilation_id", "in": "path", "required": True,
                                "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}],
                "get": {
                    "operationId": "getCityCompilation",
                    "security": [],
                    "responses": {
                        "200": _schema_response("CityCompilationResult", "Published immutable compilation result"),
                        "404": _schema_response("AuthoringApiErrorResponse", "Unknown compilation ID"),
                        "500": _schema_response("AuthoringApiErrorResponse", "Publication integrity failure"),
                        "503": _schema_response("AuthoringApiErrorResponse", "Compiler is not configured"),
                    },
                },
            },
            "/v1/catalog": {
                "get": {
                    "operationId": "getControlCatalog",
                    "security": [{"bootstrapBearer": []}],
                    "responses": {
                        "200": _schema_response(
                            "ControlCatalog", "Resolved run catalog"
                        ),
                        **error_responses,
                    },
                }
            },
            "/v1/runs": {
                "post": {
                    "operationId": "startResolvedRun",
                    "security": [{"bootstrapBearer": []}],
                    "parameters": [csrf_parameter],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/StartRunRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "202": _schema_response(
                            "StartRunResponse", "Run accepted and credentials issued"
                        ),
                        **error_responses,
                    },
                }
            },
            "/v1/runs/{run_id}/replay-access": {
                "post": {
                    "operationId": "issueReplayAccess",
                    "security": [{"bootstrapBearer": []}],
                    "parameters": [
                        run_id_parameter,
                        csrf_parameter,
                        {
                            "name": "Origin",
                            "in": "header",
                            "required": True,
                            "description": "An exact HTTP origin allowed by the Control server configuration.",
                            "schema": {"type": "string", "minLength": 1},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ReplayAccessRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": _schema_response(
                            "ReplayAccessResponse", "Read-only credentials for an admitted sealed replay"
                        ),
                        **error_responses,
                    },
                }
            },
            "/v1/runs/{run_id}": {
                "get": {
                    "operationId": "getRunStatus",
                    "security": [{"operatorBearer": []}],
                    "parameters": [run_id_parameter],
                    "responses": {
                        "200": _schema_response(
                            "RunStatusResponse", "Current run lifecycle snapshot"
                        ),
                        **error_responses,
                    },
                }
            },
            "/v1/runs/{run_id}/assets/{sha256}": {
                "get": {
                    "operationId": "getPublicRunAsset",
                    "security": [{"operatorBearer": []}],
                    "parameters": [
                        run_id_parameter,
                        {
                            "name": "sha256",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Integrity-verified declared public asset bytes",
                            "content": {
                                "application/octet-stream": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        },
                        **error_responses,
                    },
                }
            },
            "/v1/runs/{run_id}/public/trace": {
                "get": {
                    "operationId": "getPublicRunTrace",
                    "security": [{"operatorBearer": []}],
                    "parameters": [run_id_parameter],
                    "responses": {
                        "200": {
                            "description": (
                                "Exact sealed public-trace.json bytes for a "
                                "terminal run with a published public trace"
                            ),
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/PublicTrace"
                                    }
                                }
                            },
                        },
                        **error_responses,
                    },
                }
            },
            "/v1/runs/{run_id}/public/replay-manifest": {
                "get": {
                    "operationId": "getPublicRunReplayManifest",
                    "security": [{"operatorBearer": []}],
                    "parameters": [run_id_parameter],
                    "responses": {
                        "200": {
                            "description": (
                                "Exact sealed replay/replay-manifest.json bytes "
                                "for a terminal run with a published public trace"
                            ),
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/PublicReplayManifest"
                                    }
                                }
                            },
                        },
                        **error_responses,
                    },
                }
            },
            "/v1/runs/{run_id}/controls/{operation}": {
                "post": {
                    "operationId": "controlRuntime",
                    "security": [{"operatorBearer": []}],
                    "parameters": [
                        run_id_parameter,
                        {
                            "name": "operation",
                            "in": "path",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "enum": ["pause", "resume", "step", "stop"],
                            },
                        },
                        csrf_parameter,
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": (
                                        "#/components/schemas/RuntimeControlRequest"
                                    )
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": _schema_response(
                            "RuntimeControlResponse", "Audited runtime control receipt"
                        ),
                        **error_responses,
                    },
                }
            },
            "/v1/runs/{run_id}/events": {
                "get": {
                    "operationId": "streamRunEvents",
                    "security": [{"operatorBearer": []}],
                    "parameters": [
                        run_id_parameter,
                        {
                            "name": "after_transition",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": -1},
                        },
                        {
                            "name": "after_scene_tick",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 0},
                        },
                        {
                            "name": "after_event_sequence",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "integer", "minimum": -1},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": (
                                "Canonical SSE stream multiplexing run.transition, "
                                "scene.state, and run.event"
                            ),
                            "content": {
                                "text/event-stream": {
                                    "schema": {"type": "string"},
                                    "x-aero-bench-data-schemas": [
                                        {
                                            "$ref": (
                                                "#/components/schemas/RunTransitionEvent"
                                            )
                                        },
                                        {
                                            "$ref": (
                                                "#/components/schemas/SceneStateStreamEvent"
                                            )
                                        },
                                        {
                                            "$ref": (
                                                "#/components/schemas/"
                                                "PublicRunEventStreamEvent"
                                            )
                                        },
                                    ],
                                }
                            },
                        },
                        **error_responses,
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bootstrapBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "opaque-256-bit",
                },
                "operatorBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "opaque-256-bit",
                },
            },
            "schemas": components,
        },
    }


def _load_frontend_package() -> dict[str, object]:
    package_path = _ROOT / "frontend" / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("frontend/package.json is unavailable or invalid") from error
    dependencies = package.get("dependencies")
    dev_dependencies = package.get("devDependencies")
    if not isinstance(dependencies, dict) or not isinstance(dev_dependencies, dict):
        raise RuntimeError("frontend package dependency inventories are invalid")
    required = {
        ("dependencies", "ajv"): _AJV_VERSION,
        ("dependencies", "ajv-formats"): _AJV_FORMATS_VERSION,
        ("devDependencies", "json-schema-to-typescript"): _JSON2TS_VERSION,
    }
    inventories = {
        "dependencies": dependencies,
        "devDependencies": dev_dependencies,
    }
    for (inventory, name), version in required.items():
        if inventories[inventory].get(name) != version:
            raise RuntimeError(f"frontend {name} must be pinned to {version}")
    return package


def _typescript_contracts(aggregate_schema: bytes) -> bytes:
    _load_frontend_package()
    executable = _ROOT / "frontend" / "node_modules" / ".bin" / "json2ts"
    if not executable.is_file():
        raise RuntimeError(
            "json-schema-to-typescript is unavailable; run npm install in frontend"
        )
    with TemporaryDirectory(prefix="aero-contracts-") as temporary:
        temporary_root = Path(temporary)
        input_path = temporary_root / "contracts.schema.json"
        output_path = temporary_root / "contracts.ts"
        input_path.write_bytes(aggregate_schema)
        completed = subprocess.run(
            (
                str(executable),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--cwd",
                str(_ROOT),
                "--unknownAny",
                "--no-enableConstEnums",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"TypeScript contract generation failed: {detail}")
        generated = output_path.read_text(encoding="utf-8")
    header = (
        "// Generated by tools/generate_contracts.py; do not edit.\n"
        f"// json-schema-to-typescript {_JSON2TS_VERSION}.\n"
    )
    return (header + generated.lstrip()).encode("utf-8")


def _typescript_validators() -> bytes:
    type_names = ",\n  ".join(item[0] for item in _FRONTEND_VALIDATORS)
    imports = "\n".join(
        f"import {variable}Schema from './schemas/{slug}.schema.json';"
        for _, slug, variable in _FRONTEND_VALIDATORS
    )
    declarations = "\n".join(
        (f"const {variable}Validator = " f"lazyValidator<{type_name}>({variable}Schema);")
        for type_name, _, variable in _FRONTEND_VALIDATORS
    )
    functions: list[str] = []
    for type_name, _, variable in _FRONTEND_VALIDATORS:
        functions.extend(
            (
                f"export function is{type_name}(value: unknown): value is {type_name} {{",
                f"  return {variable}Validator()(value);",
                "}",
                "",
                (
                    f"export function assert{type_name}(value: unknown): "
                    f"asserts value is {type_name} {{"
                ),
                f"  assertValid('{type_name}', {variable}Validator(), value);",
                "}",
                "",
            )
        )
    function_source = "\n".join(functions).rstrip()
    source = f"""// Generated by tools/generate_contracts.py; do not edit.
import Ajv2020, {{ type ValidateFunction }} from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

import type {{
  {type_names},
}} from './aero-bench-contracts';
{imports}

const ajv = new Ajv2020({{
  allErrors: true,
  strict: true,
  validateFormats: true,
}});
addFormats(ajv);

// Compile each schema on first use: compiling all of them eagerly blocks page start-up.
function lazyValidator<T>(schema: object): () => ValidateFunction<T> {{
  let validator: ValidateFunction<T> | undefined;
  return () => (validator ??= ajv.compile<T>(schema));
}}

{declarations}

function assertValid(
  contractName: string,
  validator: ValidateFunction,
  value: unknown,
): void {{
  if (!validator(value)) {{
    const detail = ajv.errorsText(validator.errors, {{ separator: '; ' }});
    throw new TypeError(`${{contractName}} validation failed: ${{detail}}`);
  }}
}}

{function_source}
"""
    return source.encode("utf-8")


def _generated_outputs() -> dict[Path, bytes]:
    if pydantic.__version__ != _PYDANTIC_VERSION:
        raise RuntimeError(
            f"Pydantic must be pinned to {_PYDANTIC_VERSION} for generation"
        )
    schemas = {target.slug: _schema_document(target) for target in _TARGETS}
    outputs: dict[Path, bytes] = {}
    for target in _TARGETS:
        payload = _canonical_document(schemas[target.slug])
        outputs[_SCHEMA_ROOT / target.relative_path] = payload
        outputs[_FRONTEND_SCHEMA_ROOT / target.relative_path] = payload
    outputs[_SCHEMA_ROOT / "control-api.openapi.json"] = _canonical_document(
        _openapi_document(schemas)
    )
    aggregate = outputs[_SCHEMA_ROOT / "aero-bench-contracts.schema.json"]
    outputs[_TYPESCRIPT_PATH] = _typescript_contracts(aggregate)
    outputs[_VALIDATORS_PATH] = _typescript_validators()

    listed = []
    for path in sorted(outputs, key=lambda item: item.relative_to(_ROOT).as_posix()):
        payload = outputs[path]
        listed.append(
            {
                "relative_path": path.relative_to(_ROOT).as_posix(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
    manifest = {
        "schema_version": _GENERATOR_ID,
        "pydantic_version": _PYDANTIC_VERSION,
        "json_schema_dialect": _SCHEMA_DIALECT,
        "openapi_version": _OPENAPI_VERSION,
        "typescript_generator": {
            "name": "json-schema-to-typescript",
            "version": _JSON2TS_VERSION,
        },
        "runtime_validator": {
            "name": "ajv",
            "version": _AJV_VERSION,
            "formats_name": "ajv-formats",
            "formats_version": _AJV_FORMATS_VERSION,
        },
        "contracts": [
            {
                "schema_id": target.schema_id,
                "source": target.source,
                "schema_path": target.relative_path.as_posix(),
            }
            for target in _TARGETS
        ],
        "files": listed,
    }
    outputs[_MANIFEST_PATH] = _canonical_document(manifest)
    return outputs


def _managed_existing_files() -> set[Path]:
    existing: set[Path] = set()
    if _SCHEMA_ROOT.is_dir():
        existing.update(_SCHEMA_ROOT.glob("*.schema.json"))
        existing.update(_SCHEMA_ROOT.glob("*.openapi.json"))
        if _MANIFEST_PATH.exists():
            existing.add(_MANIFEST_PATH)
    if _FRONTEND_SCHEMA_ROOT.is_dir():
        existing.update(_FRONTEND_SCHEMA_ROOT.glob("*.schema.json"))
    for path in (_TYPESCRIPT_PATH, _VALIDATORS_PATH):
        if path.exists():
            existing.add(path)
    return existing


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write(outputs: dict[Path, bytes]) -> int:
    expected = set(outputs)
    for stale in sorted(_managed_existing_files() - expected):
        stale.unlink()
    for path in sorted(outputs):
        payload = outputs[path]
        if path.exists() and path.read_bytes() == payload:
            continue
        _write_atomic(path, payload)
    print(f"wrote {len(outputs)} generated contract files")
    return 0


def _check(outputs: dict[Path, bytes]) -> int:
    failures: list[str] = []
    expected = set(outputs)
    for path in sorted(expected):
        relative = path.relative_to(_ROOT).as_posix()
        if not path.is_file():
            failures.append(f"missing: {relative}")
        elif path.read_bytes() != outputs[path]:
            failures.append(f"changed: {relative}")
    for path in sorted(_managed_existing_files() - expected):
        failures.append(f"unexpected: {path.relative_to(_ROOT).as_posix()}")
    if failures:
        print("generated contract drift detected:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print(f"checked {len(outputs)} generated contract files")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write generated files")
    mode.add_argument("--check", action="store_true", help="fail on generated drift")
    arguments = parser.parse_args(argv)
    try:
        outputs = _generated_outputs()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    return _write(outputs) if arguments.write else _check(outputs)


if __name__ == "__main__":
    raise SystemExit(main())
