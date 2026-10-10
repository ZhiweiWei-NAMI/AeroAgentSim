"""Feed narrative declarations for the traffic-accident pack.

Every scenario registry message with kind=event and a ``traffic.`` prefix is
declared, including traffic.inject controls and occupancy telemetry. Templates
only use required payload members; optional links (asset_id, source_cut, ...)
appear in entities only when genuinely present.
"""

from __future__ import annotations

import copy
from typing import Any


def declarations() -> dict[str, dict[str, Any]]:
    """Return a copy of the pack's narrative declarations."""
    return copy.deepcopy(EVENT_NARRATIVES)


EVENT_NARRATIVES: dict[str, dict[str, Any]] = {
    "traffic.incident.activated": {
        "template": "Accident activated",
        "category": "emergency",
        "severity": "critical",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.incident.detected": {
        "template": "Accident detected",
        "category": "emergency",
        "severity": "warning",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.road.blocked": {
        "template": "Road blocked after the accident",
        "category": "emergency",
        "severity": "warning",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.road.cleared": {
        "template": "Road cleared after the accident",
        "category": "emergency",
        "severity": "notice",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.report.sent": {
        "template": "{actor} sent report {report} for {incident}",
        "category": "emergency",
        "severity": "info",
        "entities": ["actor", "incident", "report"],
    },
    "traffic.broadcast": {
        "template": "Accident report broadcast to nearby responders",
        "category": "emergency",
        "severity": "info",
        "entities": ["incident", "actor"],
    },
    "traffic.bid.joined": {
        "template": "{actor} joined the response bid",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor", "bid"],
    },
    "traffic.award.proposed": {
        "template": "Response award proposed",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor", "winner_id"],
    },
    "traffic.award.committed": {
        "template": "Response award committed",
        "category": "logistics",
        "severity": "notice",
        "entities": ["incident", "actor"],
    },
    "traffic.award.conflict": {
        "template": "Response award conflict",
        "category": "logistics",
        "severity": "warning",
        "entities": ["incident", "actor"],
    },
    "traffic.decision.failed": {
        "template": "Response decision failed",
        "category": "mixed",
        "severity": "warning",
        "entities": ["incident", "actor"],
    },
    "traffic.capture.requested": {
        "template": "{actor} requested capture {request_id}",
        "category": "inspection",
        "severity": "info",
        "entities": ["actor", "incident", "capture"],
        "position": "actor",
    },
    "traffic.capture.stored": {
        "template": "{actor} stored capture {request_id}",
        "category": "inspection",
        "severity": "info",
        "entities": ["actor", "incident", "capture"],
    },
    "traffic.capture.accepted": {
        "template": "{actor} capture {request_id} accepted",
        "category": "inspection",
        "severity": "info",
        "entities": ["actor", "incident", "capture"],
    },
    "traffic.capture.failed": {
        "template": "{actor} capture {request_id} failed",
        "category": "inspection",
        "severity": "warning",
        "entities": ["actor", "incident"],
    },
    "traffic.capture.upload_verified": {
        "template": "Capture upload verified for request {request_id}",
        "category": "inspection",
        "severity": "info",
        "entities": ["actor"],
    },
    "traffic.inject.accident": {
        "template": "Accident input received",
        "category": "emergency",
        "severity": "critical",
        "entities": ["incident", "actor"],
        "position": "incident",
    },
    "traffic.inject.wind": {
        "template": "Wind input received",
        "category": "weather",
        "severity": "warning",
        "entities": ["incident", "actor"],
    },
    "traffic.inject.pause-test": {
        "template": "Pause test input received",
        "category": "security",
        "severity": "notice",
        "entities": ["incident", "actor"],
    },
    "traffic.proposal.report": {
        "template": "{actor} proposed {action} for {incident}",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor", "incident"],
    },
    "traffic.proposal.bid": {
        "template": "{actor} bid on {task}: {reason}",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor", "task"],
    },
    "traffic.proposal.award": {
        "template": "Award proposal by {actor}: {action}",
        "category": "logistics",
        "severity": "info",
        "entities": ["actor"],
    },
    "traffic.road.occupancy_evaluated": {
        "template": "Road occupancy evaluated over {actors} actors",
        "category": "logistics",
        "severity": "info",
        "entities": ["actors"],
    },
    "traffic.air.arrived": {
        "template": "{entity} arrived at position",
        "category": "mobility",
        "severity": "info",
        "entities": ["entity"],
        "position": "position",
    },
}
