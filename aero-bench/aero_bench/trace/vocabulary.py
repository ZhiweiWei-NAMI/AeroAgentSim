"""Closed Provider event vocabulary eligible for Public Trace v3.

Event names alone never authorize publication. Harness also attaches explicit public
RunEvent visibility, and the projector requires that visibility before projecting.
"""

from __future__ import annotations

PUBLIC_EVENT_EVENT_TYPE = "public.event"
PUBLIC_NETWORK_LINK_EVENT_TYPE = "public.network-link"
PUBLIC_SENSOR_FRAME_EVENT_TYPE = "public.sensor-frame"
PUBLIC_STATUS_EVENT_TYPE = "public.status"
PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE = "public.traffic-light"
PUBLIC_AGENT_INTERACTION_EVENT_TYPE = "public.agent-interaction"

PUBLIC_PROVIDER_EVENT_TYPES = frozenset(
    {
        PUBLIC_EVENT_EVENT_TYPE,
        PUBLIC_NETWORK_LINK_EVENT_TYPE,
        PUBLIC_SENSOR_FRAME_EVENT_TYPE,
        PUBLIC_STATUS_EVENT_TYPE,
        PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE,
        PUBLIC_AGENT_INTERACTION_EVENT_TYPE,
    }
)

__all__ = [
    "PUBLIC_EVENT_EVENT_TYPE",
    "PUBLIC_NETWORK_LINK_EVENT_TYPE",
    "PUBLIC_PROVIDER_EVENT_TYPES",
    "PUBLIC_SENSOR_FRAME_EVENT_TYPE",
    "PUBLIC_STATUS_EVENT_TYPE",
    "PUBLIC_TRAFFIC_LIGHT_EVENT_TYPE",
]
