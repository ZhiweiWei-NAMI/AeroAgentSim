"""Declared native demand dimensions and conservative viewer boxes, in metres.

Native dimensions are authored SUMO vType inputs and are verified against TraCI.
Displayed dimensions are the existing viewer's fitted body boxes, rather than
measurements of an unknown source model or roadway width.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


@dataclass(frozen=True)
class GroundFleetType:
    vehicle_class: str
    length_m: float
    native_width_m: float
    displayed_width_m: float
    max_speed_mps: float


GROUND_FLEET = {
    "sedan": GroundFleetType("passenger",4.5,1.8,1.8,13.9),
    "taxi": GroundFleetType("taxi",4.7,1.8,1.85,13.9),
    "police": GroundFleetType("emergency",4.8,1.9,1.9,13.9),
    "bus": GroundFleetType("bus",11.5,2.5,2.5,11.1),
    "truck": GroundFleetType("delivery",7.0,2.2,2.2,11.1),
    "bicycle": GroundFleetType("bicycle",1.8,.65,.65,5.5),
}
BODY_DIMENSIONS = {name:(item.displayed_width_m,item.length_m) for name,item in GROUND_FLEET.items()}


def fleet_width_contract() -> dict:
    return {"schema_version":"aero-bench.city-ground-fleet-widths/v1",
            "source_contract":"authored-sumo-vtypes-and-declared-viewer-conservative-body-boxes/v1",
            "sources":[{"role":"native-demand-and-displayed-body-dimensions", "path":"frontend/scripts/city_ground_fleet.py",
                        "sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}],
            "vehicles":[{"vehicle_type":name,"vehicle_class":item.vehicle_class,
                         "native_width_m":item.native_width_m,"displayed_width_m":item.displayed_width_m}
                        for name,item in GROUND_FLEET.items()]}
