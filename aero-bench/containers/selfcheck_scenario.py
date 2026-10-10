"""Pure-stdlib ResolvedScenario fixture shared by provider selfchecks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


SCENARIO_SCHEMA = "aero-bench.resolved-scenario/v4"


@dataclass(frozen=True, slots=True)
class ScenarioProvider:
    provider_id: str
    roles: tuple[str, ...]
    capability_ids: tuple[str, ...]
    runtime_stage: str


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_label(label: str) -> str:
    return hashlib.sha256(f"selfcheck:{label}".encode("utf-8")).hexdigest()


def _fixture_ref(label: str) -> dict[str, str]:
    return {
        "path": f"fixture/{label}.json",
        "sha256": _sha256_label(label),
    }


def _origin_coordinate() -> dict[str, object]:
    return {
        "enu": {"east_m": 0.0, "north_m": 0.0, "up_m": 0.0},
        "ned": {"north_m": 0.0, "east_m": 0.0, "down_m": 0.0},
        "ecef": {"x_m": 6_378_137.0, "y_m": 0.0, "z_m": 0.0},
        "wgs84": {
            "longitude_deg": 0.0,
            "latitude_deg": 0.0,
            "ellipsoid_height_m": 0.0,
        },
        "geoid_separation_m": 0.0,
        "amsl_m": 0.0,
        "terrain_amsl_m": 0.0,
        "agl_m": 0.0,
    }


def _origin_pose() -> dict[str, object]:
    half_sqrt_two = 2.0**-0.5
    return {
        "position": _origin_coordinate(),
        "orientation_enu": {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0},
        "orientation_ned": {
            "qw": 0.0,
            "qx": half_sqrt_two,
            "qy": half_sqrt_two,
            "qz": 0.0,
        },
    }


def _public_audiences(
    provider_ids: tuple[str, ...], *, verifier_id: str
) -> list[dict[str, str]]:
    audiences = [
        {"role": "harness", "workload_id": "harness"},
        *(
            {"role": "provider", "workload_id": provider_id}
            for provider_id in provider_ids
        ),
        {"role": "verifier", "workload_id": verifier_id},
    ]
    return sorted(audiences, key=lambda item: (item["role"], item["workload_id"]))


def _world_asset(
    asset_id: str,
    asset_role: str,
    source_frame: str,
    *,
    public: bool,
    audiences: list[dict[str, str]],
) -> dict[str, object]:
    units: list[dict[str, str]] = [{"quantity": "file_size", "unit": "B"}]
    precision: list[dict[str, object]] = [
        {
            "quantity": "file_size",
            "kind": "exact_bytes",
            "unit": "B",
            "exact_bytes": 1,
            "absolute_tolerance": None,
        }
    ]
    if asset_role in {"geoid_model", "terrain_model"}:
        units.append({"quantity": "height", "unit": "m"})
        precision.append(
            {
                "quantity": "height",
                "kind": "absolute_tolerance",
                "unit": "m",
                "exact_bytes": None,
                "absolute_tolerance": 0.1,
            }
        )
    path = f"assets/{asset_id}.bin"
    return {
        "source_kind": "world",
        "asset_id": asset_id,
        "file": {"path": path, "sha256": _sha256_label(f"asset:{asset_id}")},
        "byte_size": 1,
        "classification": "public" if public else "private",
        "audiences": audiences,
        "world": {
            "asset_role": asset_role,
            "media_type": (
                "application/json"
                if asset_role in {"geoid_model", "terrain_model"}
                else "application/octet-stream"
            ),
            "units": units,
            "precision": precision,
            "source_frame": source_frame,
            "provenance": {
                "source_kind": "bundled_offline",
                "recorded_by": "scenario.compiler",
                "source_dataset": "aero-bench-selfcheck",
                "source_version": "1",
                "runtime_download": False,
            },
            "selector": path,
            "selector_fragment": None,
            "license": {
                "license_id": "CC0-1.0",
                "selector": "licenses/selfcheck.txt",
                "selector_fragment": None,
                "file": {
                    "path": "licenses/selfcheck.txt",
                    "sha256": _sha256_label("license"),
                },
                "byte_size": 1,
            },
        },
    }


def build_resolved_scenario(
    *,
    seed: int,
    providers: tuple[ScenarioProvider, ...],
    dynamic_provider_id: str,
    verifier_id: str = "verifier.selfcheck",
    world_id: str = "selfcheck.world",
) -> dict[str, object]:
    """Build a complete, digest-bound scenario without importing project models."""

    if not providers or dynamic_provider_id not in {
        provider.provider_id for provider in providers
    }:
        raise ValueError("selfcheck scenario providers are invalid")
    normalized_providers = tuple(
        sorted(
            (
                ScenarioProvider(
                    provider.provider_id,
                    tuple(sorted(provider.roles)),
                    tuple(sorted(provider.capability_ids)),
                    provider.runtime_stage,
                )
                for provider in providers
            ),
            key=lambda provider: provider.provider_id,
        )
    )
    provider_ids = tuple(provider.provider_id for provider in normalized_providers)
    public_audiences = _public_audiences(provider_ids, verifier_id=verifier_id)
    private_provider_audiences = [
        {"role": "provider", "workload_id": dynamic_provider_id}
    ]
    assets = [
        _world_asset(
            "geoid.model",
            "geoid_model",
            "raster_pixel",
            public=False,
            audiences=[],
        ),
        _world_asset(
            "imagery.tiles",
            "imagery_tiles",
            "raster_pixel",
            public=True,
            audiences=public_audiences,
        ),
        _world_asset(
            "scene.gazebo",
            "other",
            "non_spatial",
            public=False,
            audiences=private_provider_audiences,
        ),
        _world_asset(
            "terrain.model",
            "terrain_model",
            "raster_pixel",
            public=False,
            audiences=[],
        ),
        _world_asset(
            "terrain.tiles",
            "terrain_tiles",
            "ENU",
            public=True,
            audiences=public_audiences,
        ),
        _world_asset(
            "uav.model",
            "entity_model",
            "asset_local",
            public=False,
            audiences=private_provider_audiences,
        ),
    ]
    assets.sort(key=lambda asset: str(asset["asset_id"]))
    identity_rotation = {
        "rows": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    }
    scenario: dict[str, object] = {
        "schema_version": SCENARIO_SCHEMA,
        "source_world_package": _fixture_ref("source-world-package"),
        "world_schema_version": "aero-bench.world/v2",
        "world_id": world_id,
        "world_digest": _sha256_label("world"),
        "source_asset_digest": _sha256_label("source-assets"),
        "scenario_asset_digest": "0" * 64,
        "selected_launch_site_id": "launch.selfcheck",
        "seed": seed,
        "frame_authority": {
            "geodetic_frame_id": "WGS84",
            "ecef_frame_id": "ECEF",
            "enu_frame_id": "ENU",
            "ned_frame_id": "NED",
            "origin": _origin_coordinate(),
            "origin_ecef": {"x_m": 6_378_137.0, "y_m": 0.0, "z_m": 0.0},
            "ecef_to_enu_rotation": {
                "rows": [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]]
            },
            "enu_to_ecef_rotation": {
                "rows": [[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
            },
            "enu_to_ned_rotation": {
                "rows": [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]]
            },
            "ned_to_enu_rotation": {
                "rows": [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]]
            },
            "spatial_extent": {
                "min_east_m": -1.0,
                "max_east_m": 1.0,
                "min_north_m": -1.0,
                "max_north_m": 1.0,
                "min_up_m": -1.0,
                "max_up_m": 1.0,
                "vertical_reference": "enu_up",
            },
            "geoid_correction_asset_id": "geoid.model",
            "terrain_height_asset_id": "terrain.model",
            "geoid_interpolation": "bilinear",
            "terrain_interpolation": "bilinear",
            "geoid_precision_m": 0.1,
            "terrain_precision_m": 0.1,
        },
        "providers": [
            {
                "provider_id": provider.provider_id,
                "runtime_stage": provider.runtime_stage,
                "roles": list(provider.roles),
                "capability_ids": list(provider.capability_ids),
            }
            for provider in normalized_providers
        ],
        "assets": assets,
        "task": {
            "task_id": "task.selfcheck",
            "package_id": "package.selfcheck",
            "package_config": _fixture_ref("task-config"),
            "package_schema": _fixture_ref("task-schema"),
            "instruction": _fixture_ref("instruction"),
            "verifier_id": verifier_id,
            "required_capability_ids": [],
            "required_tool_ids": [],
            "logical_endpoint_ids": [],
            "asset_ids": [],
            "tools": [],
            "queries": [],
            "observations": [],
            "goals": [
                {
                    "goal_id": "goal.projection",
                    "verifier_id": verifier_id,
                    "metric_id": "projection.bound",
                    "operator": "eq",
                    "threshold": 1.0,
                    "evidence": "authoritative_state",
                    "parameters": [],
                }
            ],
            "task_contract_digest": _sha256_label("task-contract"),
        },
        "base_layers": [
            {
                "layer_id": "base.imagery",
                "kind": "imagery",
                "asset_id": "imagery.tiles",
                "visibility": "public",
                "default_visible": True,
            },
            {
                "layer_id": "base.terrain",
                "kind": "terrain",
                "asset_id": "terrain.tiles",
                "visibility": "public",
                "default_visible": True,
            },
        ],
        "layers": [],
        "buildings": [],
        "roads": [],
        "regions": [],
        "launch_sites": [
            {
                "launch_site_id": "launch.selfcheck",
                "primary_uav_entity_id": "uav.selfcheck",
                "allowed_uav_entity_ids": ["uav.selfcheck"],
                "pose": _origin_pose(),
                "pad_radius_m": 1.0,
                "selected": True,
            }
        ],
        "entities": [
            {
                "entity_id": "static.selfcheck",
                "kind": "static_asset",
                "owner_kind": "scenario",
                "owner_id": "scenario.compiler",
                "source_provider_id": None,
                "authority_kind": "scenario_static",
                "state": "static",
                "model_asset_id": "uav.model",
                "initial_pose": _origin_pose(),
                "selected_launch_override": False,
            },
            {
                "entity_id": "uav.selfcheck",
                "kind": "uav",
                "owner_kind": "provider",
                "owner_id": dynamic_provider_id,
                "source_provider_id": dynamic_provider_id,
                "authority_kind": "gazebo_physics",
                "state": "dynamic",
                "model_asset_id": "uav.model",
                "initial_pose": _origin_pose(),
                "selected_launch_override": True,
            },
        ],
        "sensors": [],
        "semantic_targets": [],
        "weather": [
            {
                "sample_id": "weather.clear",
                "mode": "deterministic_constant",
                "wind": {"east_mps": 0.0, "north_mps": 0.0, "up_mps": 0.0},
                "visibility_m": 1_000.0,
                "precipitation": "none",
                "precipitation_rate_mm_per_h": 0.0,
                "temperature_c": 20.0,
                "pressure_pa": 101_325.0,
            }
        ],
        "engine_frame_bindings": [
            {
                "binding_id": "frame.gazebo",
                "provider_id": dynamic_provider_id,
                "engine": "gazebo",
                "scene_asset_id": "scene.gazebo",
                "source_frame_id": "gazebo/world",
                "target_frame_id": "ENU",
                "translation_m": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
                "rotation_matrix": identity_rotation,
                "rotation_quaternion": {
                    "qw": 1.0,
                    "qx": 0.0,
                    "qy": 0.0,
                    "qz": 0.0,
                },
            }
        ],
        "sumo": None,
        "network": None,
        "mission_requirements": [],
        "expected_public_assets": [],
        "scenario_digest": "0" * 64,
    }
    scenario["scenario_asset_digest"] = hashlib.sha256(
        _canonical_json_bytes(assets)
    ).hexdigest()
    digest_body = dict(scenario)
    digest_body.pop("scenario_digest")
    scenario["scenario_digest"] = hashlib.sha256(
        _canonical_json_bytes(digest_body)
    ).hexdigest()
    return scenario


def project_scenario_assets(
    scenario: dict[str, object], *, role: str, workload_id: str
) -> list[dict[str, object]]:
    assets = scenario.get("assets")
    if not isinstance(assets, list):
        raise ValueError("selfcheck scenario assets are not an array")
    return [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and any(
            audience == {"role": role, "workload_id": workload_id}
            for audience in asset.get("audiences", [])
        )
    ]


__all__ = ["ScenarioProvider", "build_resolved_scenario", "project_scenario_assets"]
