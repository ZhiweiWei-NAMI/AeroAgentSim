"""Function-only adapter over one executor-issued Agent Gateway identity.

The adapter advances declared Provider barriers and serializes artifacts. All
mission choices, movement destinations, observations, and report content come
from the model's explicit function arguments.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator
from pydantic import Field

from aero_bench.agent.runtime import AgentContext, GatewayClient, GatewayRequestError
from aero_bench.agent.session_contracts import (
    FunctionTool,
    GatewayCallAudit,
    SessionDescriptor,
    SessionPolicy,
    ToolCallRequest,
    ToolExecutionAudit,
    ToolExecutionResult,
    ToolImageContent,
    ToolTextContent,
)
from aero_bench.config.models import FileRef, Sha256, StrictModel
from aero_bench.executor.contracts import AgentWorkloadContract
from aero_bench.providers.rpc import parse_json_object
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes


class AgentDriverConfig(StrictModel):
    schema_version: Literal["aero-bench.agent-driver-config/v1"]
    policy: SessionPolicy
    initial_input: Annotated[str, Field(min_length=1, max_length=131072)]
    codex_cli_version: Annotated[str, Field(min_length=1, max_length=128)]
    codex_binary_sha256: Sha256


class ToolInputError(ValueError):
    """A model-supplied argument or declared unavailable result is invalid."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _name(prefix: str, identity: str) -> str:
    return prefix + re.sub(r"[^a-z0-9_]", "_", identity)


def _schema(ref: FileRef, read_file: Callable[[FileRef], bytes]) -> dict[str, Any]:
    payload = read_file(ref)
    if _digest(payload) != ref.sha256:
        raise ValueError("tool schema digest differs from declared input")
    value = parse_json_object(payload)
    Draft202012Validator.check_schema(value)
    return value


def compile_tools(
    contract: AgentWorkloadContract,
    read_file: Callable[[FileRef], bytes],
) -> tuple[FunctionTool, ...]:
    """Compile a deterministic catalog only from the exact authorized grants."""
    result: list[FunctionTool] = []

    def add(name: str, description: str, parameters: dict[str, Any]) -> None:
        result.append(
            FunctionTool(
                name=name, description=description, parameters=parameters, strict=True
            )
        )

    string = {"type": "string", "minLength": 1, "maxLength": 128}
    for grant in contract.agent.tools:
        schema = _schema(grant.request_schema, read_file)
        properties = schema["properties"]
        if "decision_summary" in properties:
            raise ValueError(
                "Provider tool schema uses the reserved decision_summary field"
            )
        if "actor_id" in properties:
            del properties["actor_id"]
            schema["required"].remove("actor_id")
        if grant.tool_id == "network.send":
            for key in ("payload_base64", "payload_sha256"):
                del properties[key]
                schema["required"].remove(key)
            properties["artifact_id"] = {
                "type": "string",
                "enum": [
                    item.artifact_id for item in contract.agent.artifact_requirements
                ],
            }
            schema["required"].append("artifact_id")
        properties["decision_summary"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
            "pattern": r"\S",
            "description": "Short public reason for this action.",
        }
        schema["required"].append("decision_summary")
        add(
            _name("action_", grant.tool_id),
            f"Invoke authorized {grant.tool_id}. Returns a command receipt; use "
            "wait_for_command to advance simulation until physical completion. "
            + (
                "The selected artifact's exact bytes and SHA256 are transmitted."
                if grant.tool_id == "network.send"
                else ""
            ),
            schema,
        )
    for grant in contract.agent.queries:
        schema = _schema(grant.request_schema, read_file)
        if "actor_id" in schema["properties"]:
            del schema["properties"]["actor_id"]
            schema["required"].remove("actor_id")
        add(
            _name("query_", grant.query_type),
            f"Read authorized {grant.query_type} from its declared Provider.",
            schema,
        )
    for grant in contract.agent.observations:
        add(
            _name("read_", grant.observation_id),
            f"Read {grant.observation_id} at the current Provider barrier. "
            "RGB observations include the actual image and its frame/hash metadata. "
            "This call does not advance simulation.",
            _object({}),
        )
    add(
        "get_run_status",
        "Read the authoritative run clock; simulation is paused during model inference.",
        _object({}),
    )
    add(
        "get_command_status",
        "Read the latest receipt for a previously issued command.",
        _object({"command_id": string}),
    )
    wait_fields = {
        "timeout_sim_seconds": {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": 1000,
        },
        "timeout_wall_seconds": {"type": "integer", "minimum": 1, "maximum": 1800},
    }
    add(
        "wait_for_command",
        "Advance every required Provider barrier until this command is completed or failed, or a declared timeout expires. No flight command is generated.",
        _object({"command_id": string, **wait_fields}),
    )
    add(
        "wait_duration",
        "Advance every required Provider barrier for the requested simulated duration, rounded up to the next tick. No movement or observation is selected automatically.",
        _object(
            {
                "duration_sim_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000,
                },
                "timeout_wall_seconds": wait_fields["timeout_wall_seconds"],
            }
        ),
    )
    add(
        "record_decision_summary",
        "Record a short explicit decision summary in the authoritative Agent event log.",
        _object({"summary": {"type": "string", "minLength": 1, "maxLength": 4096}}),
    )
    if contract.scenario_assets:
        add(
            "read_public_asset",
            "Read one explicitly granted public task asset. JSON data and output schemas are returned as text.",
            _object(
                {
                    "asset_id": {
                        "type": "string",
                        "enum": [a.asset_id for a in contract.scenario_assets],
                    }
                }
            ),
        )
    if contract.agent.artifact_requirements:
        add(
            "write_json_artifact",
            "Write a declared output artifact once using exact model-authored JSON. content_json is a JSON string and may encode a list. Follow the published output schemas; bytes, size and SHA256 are returned.",
            _object(
                {
                    "artifact_id": {
                        "type": "string",
                        "enum": [
                            a.artifact_id for a in contract.agent.artifact_requirements
                        ],
                    },
                    "content_json": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 1048576,
                    },
                }
            ),
        )
    add(
        "finish",
        "End this Agent after all declared output artifacts have been written. Mission outcome is evaluated independently from sealed physical and business evidence.",
        _object({}),
    )
    names = [item.name for item in result]
    if len(names) != len(set(names)):
        raise ValueError("authorized grant names collide in the function catalog")
    return tuple(sorted(result, key=lambda item: item.name))


def build_descriptor(
    contract: AgentWorkloadContract,
    attempt_id: str,
    read_file: Callable[[FileRef], bytes],
) -> SessionDescriptor:
    driver = contract.agent.driver
    if driver is None:
        raise ValueError("session bridge requires a declared driver")
    instruction = read_file(contract.instruction)
    config_bytes = read_file(driver.config.file)
    if (
        _digest(instruction) != contract.instruction.sha256
        or _digest(config_bytes) != driver.config.file.sha256
    ):
        raise ValueError("session inputs differ from their declared digests")
    config = AgentDriverConfig.model_validate(parse_json_object(config_bytes))
    Draft202012Validator(_schema(driver.config.schema_file, read_file)).validate(
        config.model_dump(mode="json")
    )
    tools = compile_tools(contract, read_file)
    return SessionDescriptor(
        schema_version="aero-bench.agent-session/v1",
        run_id=contract.run_id,
        attempt_id=attempt_id,
        task_id=contract.task_id,
        agent_id=contract.agent.agent_id,
        driver_id=driver.driver_id,
        instruction=instruction.decode("utf-8"),
        instruction_sha256=_digest(instruction),
        initial_input=config.initial_input,
        initial_input_sha256=_digest(config.initial_input.encode("utf-8")),
        policy=config.policy,
        tools=tools,
        tools_digest=_digest(
            canonical_json_bytes([t.model_dump(mode="json") for t in tools])
        ),
    )


def _strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ToolInputError("JSON artifact contains duplicate keys")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ToolInputError(f"JSON artifact contains non-finite value: {value}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except json.JSONDecodeError as error:
        raise ToolInputError("content_json must be valid strict JSON") from error


class SessionBridge:
    def __init__(self, context: AgentContext, descriptor: SessionDescriptor) -> None:
        self.context = context
        self.descriptor = descriptor
        self.current = SimulationTime(tick=0, sim_time_ns=0)
        self.finished = False
        self._seen: set[str] = set()
        self._round_commands: set[str] = set()
        self._all_commands: set[str] = set()
        self._round_observations: set[str] = set()
        self._written: dict[str, bytes] = {}
        self._calls: list[GatewayCallAudit] = []
        self._recording = False
        self._image_calls = 0
        self._completion_index = 0
        self.gateway = GatewayClient(context, audit=self._audit_gateway)
        self._actions = {
            _name("action_", g.tool_id): g for g in context.contract.agent.tools
        }
        self._queries = {
            _name("query_", g.query_type): g for g in context.contract.agent.queries
        }
        self._observations = {
            _name("read_", g.observation_id): g
            for g in context.contract.agent.observations
        }
        self._image_functions = {
            name
            for name, grant in self._observations.items()
            if "image_base64"
            in _schema(grant.schema_file, self._read_file).get("properties", {})
        }

    def _read_file(self, ref: FileRef) -> bytes:
        return self.context._read_bundle_file(ref.path, ref.sha256)

    def connect(self) -> None:
        self.gateway.connect()
        self.current = SimulationTime.model_validate(self.gateway.probe()["current"])

    def _audit_gateway(
        self, operation: str, request: dict[str, Any], response: dict[str, Any]
    ) -> None:
        if not self._recording:
            return
        issued = self.current
        if operation == "probe" and "current" in response:
            self.current = SimulationTime.model_validate(response["current"])
        elif operation == "turn.complete" and "at" in response:
            self.current = SimulationTime.model_validate(response["at"])
        request_bytes = canonical_json_bytes(request)
        response_bytes = canonical_json_bytes(response)
        self._calls.append(
            GatewayCallAudit(
                operation=operation,
                request_json=request_bytes.decode("utf-8"),
                response_json=response_bytes.decode("utf-8"),
                request_digest=_digest(request_bytes),
                response_digest=_digest(response_bytes),
                issued_at=issued,
                completed_at=self.current,
            )
        )

    def execute_tool(
        self, request: ToolCallRequest, *, timeout_s: int
    ) -> ToolExecutionResult:
        if self.finished or request.call_id in self._seen:
            raise ValueError("session is finished or call_id was already executed")
        self._seen.add(request.call_id)
        if len(self._seen) > self.descriptor.policy.max_tool_calls:
            raise ValueError("session tool call budget exceeded")
        tool = self.descriptor.tool_map.get(request.name)
        if tool is None:
            raise ValueError("model called an undeclared function")
        errors = list(
            Draft202012Validator(tool.parameters).iter_errors(request.arguments)
        )
        if errors:
            raise ValueError(
                f"tool arguments violate declared schema: {errors[0].message}"
            )
        operation_id = "call." + _digest(request.call_id.encode("ascii"))[:40]
        start = self.current
        self._calls = []
        self._recording = True
        command_id = query_id = observation_id = None
        success = True
        content: list[ToolTextContent | ToolImageContent]
        try:
            args = dict(request.arguments)
            name = request.name
            if name in self._actions:
                grant = self._actions[name]
                decision_summary = args.pop("decision_summary")
                if (
                    "actor_id"
                    in _schema(grant.request_schema, self._read_file)["properties"]
                ):
                    args["actor_id"] = self.context.agent_id
                if grant.tool_id == "network.send":
                    artifact_id = args.pop("artifact_id")
                    if artifact_id not in self._written:
                        raise ToolInputError("selected artifact has not been written")
                    payload = self._written[artifact_id]
                    args["payload_base64"] = base64.b64encode(payload).decode("ascii")
                    args["payload_sha256"] = _digest(payload)
                command_id = operation_id
                self.gateway.decision_summary(
                    summary_id=operation_id,
                    command_id=command_id,
                    at=self.current,
                    summary=decision_summary,
                    observation_ids=sorted(self._round_observations),
                )
                result = self.gateway.command(
                    command_id=command_id,
                    tool_id=grant.tool_id,
                    at=self.current,
                    arguments=args,
                )
                self._round_commands.add(command_id)
                self._all_commands.add(command_id)
                value = result.model_dump(mode="json")
            elif name in self._queries:
                grant = self._queries[name]
                if (
                    "actor_id"
                    in _schema(grant.request_schema, self._read_file)["properties"]
                ):
                    args["actor_id"] = self.context.agent_id
                query_id = operation_id
                result = self.gateway.query(
                    query_id=query_id,
                    query_type=grant.query_type,
                    at=self.current,
                    arguments=args,
                )
                value = self._readable(result.model_dump(mode="json"))
            elif name in self._observations:
                observation_id = self._observations[name].observation_id
                if name in self._image_functions:
                    if (
                        self._image_calls
                        >= self.descriptor.policy.max_image_observations
                    ):
                        raise ToolInputError(
                            "declared RGB observation budget is exhausted"
                        )
                result = self.gateway.observe(
                    observation_id=observation_id, at=self.current
                )
                self._round_observations.add(observation_id)
                value = self._readable(result.model_dump(mode="json"))
            elif name == "get_run_status":
                value = self.gateway.probe()
            elif name == "get_command_status":
                self._require_command(args["command_id"])
                command_id = args["command_id"]
                value = self.gateway.command_status(
                    command_id=command_id, at=self.current
                ).model_dump(mode="json")
            elif name == "wait_for_command":
                command_id = args["command_id"]
                self._require_command(command_id)
                value = self._wait(
                    operation_id,
                    command_id=command_id,
                    duration_s=args["timeout_sim_seconds"],
                    wall_s=min(timeout_s, args["timeout_wall_seconds"]),
                )
            elif name == "wait_duration":
                value = self._wait(
                    operation_id,
                    command_id=None,
                    duration_s=args["duration_sim_seconds"],
                    wall_s=min(timeout_s, args["timeout_wall_seconds"]),
                )
            elif name == "record_decision_summary":
                value = self.gateway.decision_summary(
                    summary_id=operation_id,
                    at=self.current,
                    summary=args["summary"],
                    observation_ids=sorted(self._round_observations),
                ).model_dump(mode="json")
            elif name == "read_public_asset":
                payload = self.context.asset_bytes(args["asset_id"])
                if len(payload) > 1048576:
                    raise ToolInputError("public asset exceeds text tool size bound")
                value = {
                    "asset_id": args["asset_id"],
                    "sha256": _digest(payload),
                    "text": payload.decode("utf-8"),
                }
            elif name == "write_json_artifact":
                artifact_id = args["artifact_id"]
                if artifact_id in self._written:
                    raise ToolInputError(
                        "artifact is already written; outputs are immutable"
                    )
                payload = canonical_json_bytes(_strict_json(args["content_json"]))
                requirement = next(
                    a
                    for a in self.context.contract.agent.artifact_requirements
                    if a.artifact_id == artifact_id
                )
                if len(payload) > requirement.max_size_bytes:
                    raise ToolInputError(
                        "JSON artifact exceeds its declared size bound"
                    )
                self.context.write_artifact(artifact_id, payload)
                self._written[artifact_id] = payload
                value = {
                    "artifact_id": artifact_id,
                    "sha256": _digest(payload),
                    "size_bytes": len(payload),
                }
            elif name == "finish":
                missing = {
                    a.artifact_id
                    for a in self.context.contract.agent.artifact_requirements
                } - self._written.keys()
                if missing:
                    raise ToolInputError(
                        "required artifacts remain unwritten: "
                        + ", ".join(sorted(missing))
                    )
                value = self._complete(operation_id, "finished")
                self.finished = True
            else:
                raise RuntimeError("compiled tool has no adapter implementation")
            content = [
                ToolTextContent(
                    type="inputText", text=canonical_json_bytes(value).decode("utf-8")
                )
            ]
            if name in self._observations and isinstance(value.get("payload"), dict):
                encoded = value["payload"].pop("image_base64", None)
                if encoded is not None:
                    content[0] = ToolTextContent(
                        type="inputText",
                        text=canonical_json_bytes(value).decode("utf-8"),
                    )
                    content.append(
                        ToolImageContent(
                            type="inputImage",
                            imageUrl="data:image/png;base64," + encoded,
                        )
                    )
                    self._image_calls += 1
        except (ToolInputError, GatewayRequestError) as error:
            success = False
            content = [
                ToolTextContent(
                    type="inputText",
                    text=canonical_json_bytes({"error": str(error)}).decode("utf-8"),
                )
            ]
        finally:
            self._recording = False
        visible = {
            "success": success,
            "content": [item.model_dump(mode="json") for item in content],
        }
        return ToolExecutionResult(
            schema_version="aero-bench.agent-tool-execution-result/v1",
            success=success,
            content=tuple(content),
            audit=ToolExecutionAudit(
                operation_id=operation_id,
                operation=request.name,
                command_id=command_id,
                query_id=query_id,
                observation_id=observation_id,
                issued_at=start,
                completed_at=self.current,
                result_digest=_digest(canonical_json_bytes(visible)),
                gateway_calls=tuple(self._calls),
            ),
        )

    @staticmethod
    def _readable(value: dict[str, Any]) -> dict[str, Any]:
        if isinstance(value.get("payload"), list):
            payload = {item["name"]: item["value"] for item in value["payload"]}
            for key in tuple(payload):
                if key.endswith("_json") and isinstance(payload[key], str):
                    payload[key] = _strict_json(payload[key])
            value["payload"] = payload
        return value

    def _require_command(self, command_id: str) -> None:
        if command_id not in self._all_commands:
            raise ToolInputError("command_id was not issued by this session")

    def _complete(self, operation_id: str, disposition: str) -> dict[str, Any]:
        self._completion_index += 1
        result = self.gateway.complete_turn(
            completion_id=f"{operation_id}.tick.{self._completion_index}",
            at=self.current,
            disposition=disposition,
            command_ids=sorted(self._round_commands),
            observation_ids=sorted(self._round_observations),
        )
        if result.status == "waiting":
            raise RuntimeError(
                "single-session bridge cannot advance while another Agent is missing"
            )
        self.current = result.at
        self._round_commands.clear()
        self._round_observations.clear()
        return result.model_dump(mode="json")

    def _wait(
        self,
        operation_id: str,
        *,
        command_id: str | None,
        duration_s: float,
        wall_s: int,
    ) -> dict[str, Any]:
        start = self.current
        ticks = math.ceil(duration_s * 1e9 / self.context.contract.clock.step_ns)
        target_tick = start.tick + ticks
        deadline = time.monotonic() + wall_s
        while True:
            receipt = None
            if command_id is not None:
                receipt = self.gateway.command_status(
                    command_id=command_id, at=self.current
                )
                if receipt.phase in {"completed", "failed"}:
                    return {
                        "condition": "command_terminal",
                        "receipt": receipt.model_dump(mode="json"),
                        "current": self.current.model_dump(mode="json"),
                    }
            if self.current.tick >= target_tick:
                return {
                    "condition": "duration_elapsed"
                    if command_id is None
                    else "sim_timeout",
                    "current": self.current.model_dump(mode="json"),
                    "receipt": None
                    if receipt is None
                    else receipt.model_dump(mode="json"),
                }
            if time.monotonic() >= deadline:
                raise ToolInputError("wait wall timeout reached")
            if self.current.tick >= self.context.contract.clock.max_steps:
                raise ToolInputError("declared simulation step budget exhausted")
            decision = self._complete(operation_id, "advance")
            if decision["status"] == "terminated":
                raise ToolInputError("runtime terminated while waiting")


class BridgeHttpServer(HTTPServer):
    def __init__(
        self, address: tuple[str, int], bridge: SessionBridge, token: str
    ) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", token) or token == "0" * 64:
            raise ValueError("session transport token is invalid")
        self.bridge = bridge
        self.session_token = token
        self.failure: BaseException | None = None
        super().__init__(address, _BridgeHandler)


class _BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeHttpServer
    protocol_version = "HTTP/1.1"

    def log_message(self, *_: object) -> None:
        pass

    def _respond(self, status: int, value: Any) -> None:
        payload = canonical_json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()
        self.close_connection = True

    def _authorized(self) -> bool:
        expected = "Bearer " + self.server.session_token
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self._respond(401, {"error": "authentication.failed"})
            return False
        return True

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path != "/session":
            self._respond(404, {"error": "endpoint.unknown"})
            return
        self._respond(200, self.server.bridge.descriptor.model_dump(mode="json"))

    def do_POST(self) -> None:
        if not self._authorized():
            return
        if self.path != "/tool":
            self._respond(404, {"error": "endpoint.unknown"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 2 * 1024 * 1024 or self.headers.get(
                "Transfer-Encoding"
            ):
                raise ValueError("invalid tool request size or encoding")
            request = ToolCallRequest.model_validate(
                parse_json_object(self.rfile.read(size))
            )
            result = self.server.bridge.execute_tool(request, timeout_s=1800)
            self._respond(200, result.model_dump(mode="json"))
            if self.server.bridge.finished:
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        except Exception as error:
            self.server.failure = error
            self._respond(500, {"error": "bridge.execution.failed"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Serve one authorized Agent session")
    parser.add_argument("component", choices=["bridge"])
    parser.add_argument("command", choices=["run"])
    parser.parse_args()
    context = AgentContext.from_environment()
    descriptor = build_descriptor(
        context.contract,
        os.environ["AERO_BENCH_ATTEMPT_ID"],
        lambda ref: context._read_bundle_file(ref.path, ref.sha256),
    )
    bridge = SessionBridge(context, descriptor)
    bridge.connect()
    with BridgeHttpServer(
        ("0.0.0.0", int(os.environ["AERO_BENCH_SESSION_PORT"])),
        bridge,
        os.environ["AERO_BENCH_SESSION_TOKEN"],
    ) as server:
        server.serve_forever(poll_interval=0.5)
        bridge.gateway.close()
        if server.failure is not None:
            raise RuntimeError("session bridge failed") from server.failure
        if not bridge.finished:
            raise RuntimeError("session bridge closed before finish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
