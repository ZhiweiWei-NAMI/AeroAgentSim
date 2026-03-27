from __future__ import annotations

from typing import Optional


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


def is_drone_type(agent_type: Optional[str]) -> bool:
    return normalize_agent_type(agent_type) == "drone"


def canonical_agent_display_type(agent_type: Optional[str]) -> str:
    if is_drone_type(agent_type):
        return "DroneAgent"
    return str(agent_type or "")
