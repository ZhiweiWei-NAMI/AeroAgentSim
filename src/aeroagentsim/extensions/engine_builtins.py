"""Engine declarations shared with inventory without importing the platform.

The public ``platform.plugins.BUILTINS`` name re-exports this same mapping.
Values are module paths, not imported factories or constructed engines.
"""

BUILTINS = {
    "lowalt_city_capture": "aeroagentsim.packs.lowalt.city_capture",
    "lowalt_tasks": "aeroagentsim.packs.lowalt.tasks",
    "lowalt_incident_capture": "aeroagentsim.packs.lowalt.incident_capture",
    "lowalt_airspace": "aeroagentsim.packs.lowalt.airspace",
    "lowalt_energy": "aeroagentsim.packs.lowalt.energy",
    "traffic_decision_failures": "aeroagentsim.packs.traffic_accident.decision_failures",
    "traffic_decisions": "aeroagentsim.packs.traffic_accident.decisions",
    "traffic_capture_bridge": "aeroagentsim.packs.traffic_accident.capture",
    "traffic_camera_capture": "aeroagentsim.packs.traffic_accident.camera",
    "behaviour": "aeroagentsim.engines.behaviour",
    "ingress-consumer": "aeroagentsim.engines.ingress_consumer",
    "langgraph": "aeroagentsim.agents.langgraph",
    "telemetry": "aeroagentsim.engines.telemetry",
    "decision": "aeroagentsim.agents.decision",
    "agent-assignment": "aeroagentsim.agents.dispatch",
    "kinematic": "aeroagentsim.engines.kinematic",
    "workflow": "aeroagentsim.engines.workflow",
    "records": "aeroagentsim.engines.records",
    "threshold": "aeroagentsim.engines.threshold",
    "predicate": "aeroagentsim.engines.predicate",
    "logistics": "aeroagentsim.packs.logistics",
    "inspection": "aeroagentsim.packs.inspection",
    "environment": "aeroagentsim.engines.environment",
}

__all__ = ["BUILTINS"]
