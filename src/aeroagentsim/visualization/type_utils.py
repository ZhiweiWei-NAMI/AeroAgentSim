from __future__ import annotations

from typing import Optional


SUPPORTED_WORKBENCH_BUILTIN_WORKFLOWS = frozenset(
    {"inspection", "charging", "logistics", "imageprocessing"}
)
UNSUPPORTED_WORKBENCH_BUILTIN_WORKFLOWS = frozenset({"contract", "orderexecution"})
COMPONENT_NAME_ALIASES = {
    "sensing": "ImageSensingComponent",
}


def _normalize_token(value: Optional[str], *strip_words: str) -> str:
    normalized = str(value or "").lower()
    for marker in ("_", "-", " "):
        normalized = normalized.replace(marker, "")
    for word in strip_words:
        normalized = normalized.replace(word, "")
    return normalized


def normalize_agent_type(agent_type: Optional[str]) -> str:
    return _normalize_token(agent_type, "agent")


def normalize_workflow_type(workflow_type: Optional[str]) -> str:
    return _normalize_token(workflow_type, "workflow")


def normalize_component_name(component_name: Optional[str]) -> Optional[str]:
    token = str(component_name or "").strip()
    if not token:
        return None
    normalized_token = _normalize_token(token, "component")
    if normalized_token in COMPONENT_NAME_ALIASES:
        return COMPONENT_NAME_ALIASES[normalized_token]
    if token.endswith("Component"):
        return token
    return f"{token}Component"


def is_supported_workbench_workflow_type(workflow_type: Optional[str]) -> bool:
    return normalize_workflow_type(workflow_type) in SUPPORTED_WORKBENCH_BUILTIN_WORKFLOWS


def is_explicitly_unsupported_workbench_workflow_type(workflow_type: Optional[str]) -> bool:
    return normalize_workflow_type(workflow_type) in UNSUPPORTED_WORKBENCH_BUILTIN_WORKFLOWS


def is_drone_type(agent_type: Optional[str]) -> bool:
    return normalize_agent_type(agent_type) == "drone"


def is_drone_capable_type(agent_type: Optional[str]) -> bool:
    return "drone" in normalize_agent_type(agent_type)


def is_station_type(agent_type: Optional[str]) -> bool:
    return "station" in normalize_agent_type(agent_type)


def is_coordinate3d(value) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(isinstance(item, (int, float)) for item in value)
    )


def canonical_agent_display_type(agent_type: Optional[str]) -> str:
    if is_drone_type(agent_type):
        return "DroneAgent"
    return str(agent_type or "")
