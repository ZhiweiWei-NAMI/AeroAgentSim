from __future__ import annotations

import hashlib

from aero_bench.agent.session_contracts import (
    FunctionTool,
    ReasoningEffort,
    SessionDescriptor,
    SessionPolicy,
    text_digest,
    tool_catalog_digest,
)


def descriptor(
    *,
    max_model_requests: int = 8,
    reasoning_effort: ReasoningEffort = "xhigh",
) -> SessionDescriptor:
    instruction = "Use only the declared mission functions. Complete the inspection."
    initial_input = "Execute the authorized inspection task now."
    tools = (
        FunctionTool(
            name="flight.observe",
            description="Read the current authorized flight observation.",
            parameters={
                "type": "object",
                "properties": {"scope": {"type": "string", "const": "public"}},
                "required": ["scope"],
                "additionalProperties": False,
            },
            strict=True,
        ),
    )
    return SessionDescriptor(
        schema_version="aero-bench.agent-session/v1",
        run_id="1" * 64,
        attempt_id="attempt-1",
        task_id="inspection.task",
        agent_id="inspection.agent",
        driver_id="inspection.driver",
        instruction=instruction,
        instruction_sha256=text_digest(instruction),
        initial_input=initial_input,
        initial_input_sha256=text_digest(initial_input),
        policy=SessionPolicy(
            schema_version="aero-bench.agent-session-policy/v1",
            model="gpt-6-astra",
            reasoning_effort=reasoning_effort,
            max_model_requests=max_model_requests,
            max_tool_calls=20,
            max_image_observations=12,
            model_call_timeout_s=30,
            session_wall_timeout_s=300,
            max_output_tokens=4096,
        ),
        tools=tools,
        tools_digest=tool_catalog_digest(tools),
    )


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
