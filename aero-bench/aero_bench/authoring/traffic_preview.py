"""Materialize offline SUMO preview demand from a validated city workspace.

This module defines an authoring boundary only. It does not bind the formal SUMO
Provider and does not turn an engineering preview into Run evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol

from aero_bench.authoring.workspace import CityWorkspaceDraft


TRAFFIC_PREVIEW_DEMAND_SCHEMA = "aero-bench.city-traffic-preview-demand/v1"


class _WorkspaceTraffic(Protocol):
    vehicles: int
    pedestrians: int
    bicycles: int


class _WorkspaceWithTraffic(Protocol):
    schema_version: str
    seed: int
    traffic: _WorkspaceTraffic


@dataclass(frozen=True, slots=True)
class TrafficPreviewDemand:
    """The workspace fields that control one offline SUMO preview recording."""

    seed: int
    motor_vehicles: int
    pedestrians: int
    bicycles: int
    workspace_schema_version: str
    workspace_sha256: str
    workspace_size_bytes: int

    def source_identity(self) -> dict[str, object]:
        return {
            "schema_version": TRAFFIC_PREVIEW_DEMAND_SCHEMA,
            "workspace_schema_version": self.workspace_schema_version,
            "workspace_sha256": self.workspace_sha256,
            "workspace_size_bytes": self.workspace_size_bytes,
            "seed": self.seed,
            "traffic": {
                "vehicles": self.motor_vehicles,
                "pedestrians": self.pedestrians,
                "bicycles": self.bicycles,
            },
        }


def demand_from_validated_workspace(
    draft: _WorkspaceWithTraffic,
    *,
    workspace_bytes: bytes,
) -> TrafficPreviewDemand:
    """Extract demand without copying or reinterpreting the workspace schema."""

    if not isinstance(workspace_bytes, bytes):
        raise TypeError("workspace_bytes must be the exact imported bytes")
    return TrafficPreviewDemand(
        seed=draft.seed,
        motor_vehicles=draft.traffic.vehicles,
        pedestrians=draft.traffic.pedestrians,
        bicycles=draft.traffic.bicycles,
        workspace_schema_version=draft.schema_version,
        workspace_sha256=hashlib.sha256(workspace_bytes).hexdigest(),
        workspace_size_bytes=len(workspace_bytes),
    )


def parse_workspace_preview_demand(workspace_bytes: bytes) -> TrafficPreviewDemand:
    """Validate the current workspace contract, then materialize its traffic input."""

    draft = CityWorkspaceDraft.from_json_bytes(workspace_bytes)
    return demand_from_validated_workspace(draft, workspace_bytes=workspace_bytes)
