"""Deterministic in-image self-check for the packaged SUMO service.

The check exercises the installed SUMO/TraCI runtime with mechanically generated
fixture inputs. It is not a formal benchmark run, and its fixture artifact is not
live-traffic evidence.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import socket
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import aero_frame_math as frame_math
from service import (
    EVIDENCE_ARTIFACT_TYPE,
    EVIDENCE_SCHEMA,
    FINALIZATION_BINDING_SCHEMA,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    PROVIDER_ADAPTER,
    STATE_SCHEMA,
    SUMO_COMMIT,
    SUMO_VERSION,
    SumoService,
    WorkloadIdentity,
    _canonical_json,
    _client_handler,
    _load_workload_identity,
)


SEED = 1701
STEP_LENGTH_NS = 100_000_000
RUN_ID = "a" * 64
RUNTIME_IMAGE = "registry.invalid/aero-bench/sumo@sha256:" + "1" * 64
SESSION_TOKEN = "c" * 64
PROVIDER_ID = "traffic"
EVIDENCE_ARTIFACT_ID = "artifact.sumo.traffic"
EVIDENCE_ARTIFACT_PATH = "nested/traffic/sumo-selfcheck.jsonl"
EVIDENCE_MAX_SIZE_BYTES = 16_777_216
SUMO_OBJECT_ID = "reference.0"
SUMO_ENTITY_ID = "ugv.fixture"
SUMO_PERSON_OBJECT_ID = "reference.person.0"
SUMO_PERSON_ENTITY_ID = "pedestrian.fixture"
SUMO_CONFIG_PATH = "scenario.sumocfg"
SUMO_NETWORK_PATH = "network.net.xml"
SUMO_ROUTES_PATH = "route.rou.xml"
SUMO_ADDITIONAL_PATH = "additional.add.xml"
SUMO_FRAME_ROTATION = frame_math.RotationMatrix(
    rows=((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
)
SUMO_FRAME_TRANSLATION = frame_math.Vector3(10.0, -20.0, 3.0)
SCENARIO_ORIGIN = frame_math.EnuTransform.from_origin(
    longitude_deg=0.0,
    latitude_deg=0.0,
    altitude_m=0.0,
)


_SOURCE_FRAME_BY_ROLE = {
    "entity_model": "asset_local",
    "geoid_model": "raster_pixel",
    "imagery_tiles": "raster_pixel",
    "other": "non_spatial",
    "sumo_additional": "non_spatial",
    "sumo_config": "non_spatial",
    "sumo_network": "sumo_net",
    "sumo_routes": "non_spatial",
    "terrain_model": "raster_pixel",
    "terrain_tiles": "ENU",
}


def _sha256_label(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_json(value) + b"\n")


def _write_route(path: Path, edge_ids: tuple[str, ...]) -> None:
    routes = ET.Element("routes")
    ET.SubElement(
        routes,
        "vType",
        id="reference.vehicle",
        accel="2.6",
        decel="4.5",
        length="5.0",
        maxSpeed="13.9",
        sigma="0",
    )
    ET.SubElement(routes, "route", id="reference.route", edges=" ".join(edge_ids))
    ET.SubElement(
        routes,
        "vehicle",
        id=SUMO_OBJECT_ID,
        route="reference.route",
        type="reference.vehicle",
        depart="0",
        departSpeed="0",
    )
    person = ET.SubElement(
        routes,
        "person",
        id=SUMO_PERSON_OBJECT_ID,
        depart="0",
    )
    ET.SubElement(person, "walk", edges=" ".join(edge_ids))
    ET.ElementTree(routes).write(path, encoding="utf-8", xml_declaration=True)


def _write_additional(path: Path) -> None:
    ET.ElementTree(ET.Element("additional")).write(
        path, encoding="utf-8", xml_declaration=True
    )


def _write_sumo_config(path: Path) -> None:
    configuration = ET.Element("configuration")
    inputs = ET.SubElement(configuration, "input")
    ET.SubElement(inputs, "net-file", value=SUMO_NETWORK_PATH)
    ET.SubElement(inputs, "route-files", value=SUMO_ROUTES_PATH)
    ET.SubElement(inputs, "additional-files", value=SUMO_ADDITIONAL_PATH)
    time = ET.SubElement(configuration, "time")
    ET.SubElement(time, "step-length", value="0.1")
    ET.ElementTree(configuration).write(
        path, encoding="utf-8", xml_declaration=True
    )


def _select_route(network_path: Path) -> tuple[str, ...]:
    root = ET.parse(network_path).getroot()
    edges = [
        edge.attrib["id"]
        for edge in root.findall("edge")
        if not edge.attrib["id"].startswith(":")
    ]
    if not edges:
        raise RuntimeError("netgenerate produced no road edge for the SUMO fixture")
    return (edges[0],)


def _file_ref(root: Path, relative_path: str) -> dict[str, str]:
    source = root / relative_path
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


def _fixture_file_ref(label: str) -> dict[str, str]:
    return {
        "path": f"fixture-contract/{label}.json",
        "sha256": _sha256_label(f"sumo-selfcheck:{label}"),
    }


def _coordinate(east_m: float, north_m: float, up_m: float) -> dict[str, Any]:
    enu = frame_math.Vector3(east_m, north_m, up_m)
    ned = frame_math.enu_to_ned(enu)
    ecef = SCENARIO_ORIGIN.ecef_from_enu(enu)
    longitude, latitude, ellipsoid_height = SCENARIO_ORIGIN.enu_to_geodetic(enu)
    return {
        "enu": {"east_m": east_m, "north_m": north_m, "up_m": up_m},
        "ned": {"north_m": ned.x, "east_m": ned.y, "down_m": ned.z},
        "ecef": {"x_m": ecef.x, "y_m": ecef.y, "z_m": ecef.z},
        "wgs84": {
            "longitude_deg": longitude,
            "latitude_deg": latitude,
            "ellipsoid_height_m": ellipsoid_height,
        },
        "geoid_separation_m": 0.0,
        "amsl_m": ellipsoid_height,
        "terrain_amsl_m": 0.0,
        "agl_m": ellipsoid_height,
    }


def _quaternion(value: frame_math.UnitQuaternion) -> dict[str, float]:
    return {"qw": value.w, "qx": value.x, "qy": value.y, "qz": value.z}


def _rotation(value: frame_math.RotationMatrix) -> dict[str, list[list[float]]]:
    return {"rows": [list(row) for row in value.rows]}


def _pose(east_m: float, north_m: float, up_m: float) -> dict[str, Any]:
    return {
        "position": _coordinate(east_m, north_m, up_m),
        "orientation_enu": _quaternion(
            frame_math.UnitQuaternion(1.0, 0.0, 0.0, 0.0)
        ),
        "orientation_ned": _quaternion(
            frame_math.UnitQuaternion.from_rotation_matrix(
                frame_math.ENU_TO_NED_ROTATION
            )
        ),
    }


def _measurement(byte_size: int) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    return (
        [{"quantity": "file_size", "unit": "B"}],
        [
            {
                "quantity": "file_size",
                "kind": "exact_bytes",
                "unit": "B",
                "exact_bytes": byte_size,
                "absolute_tolerance": None,
            }
        ],
    )


def _asset_audiences(
    *, classification: str, provider_ids: tuple[str, ...]
) -> list[dict[str, str]]:
    if classification == "public":
        values = {
            ("harness", "harness"),
            ("provider", "flight"),
            ("provider", PROVIDER_ID),
            ("verifier", "verifier.fixture"),
        }
    else:
        values = {("provider", provider_id) for provider_id in provider_ids}
    return [
        {"role": role, "workload_id": workload_id}
        for role, workload_id in sorted(values)
    ]


def _world_asset(
    root: Path,
    *,
    asset_id: str,
    role: str,
    relative_path: str,
    classification: str,
    provider_ids: tuple[str, ...],
) -> dict[str, Any]:
    source = root / relative_path
    byte_size = source.stat().st_size
    units, precision = _measurement(byte_size)
    if role in {"geoid_model", "terrain_model"}:
        units.append({"quantity": "height", "unit": "m"})
        precision.append(
            {
                "quantity": "height",
                "kind": "absolute_tolerance",
                "unit": "m",
                "exact_bytes": None,
                "absolute_tolerance": 0.05 if role == "geoid_model" else 0.2,
            }
        )
    license_ref = _file_ref(root, "licenses/selfcheck.txt")
    return {
        "source_kind": "world",
        "asset_id": asset_id,
        "file": _file_ref(root, relative_path),
        "byte_size": byte_size,
        "classification": classification,
        "audiences": _asset_audiences(
            classification=classification, provider_ids=provider_ids
        ),
        "world": {
            "asset_role": role,
            "media_type": (
                "application/json"
                if role in {"geoid_model", "terrain_model"}
                else "application/xml"
                if relative_path.endswith((".xml", ".sumocfg"))
                else "application/octet-stream"
            ),
            "units": units,
            "precision": precision,
            "source_frame": _SOURCE_FRAME_BY_ROLE[role],
            "provenance": {
                "source_kind": "bundled_offline",
                "recorded_by": "sumo.selfcheck",
                "source_dataset": "deterministic-mechanical-fixture",
                "source_version": "2",
                "runtime_download": False,
            },
            "selector": relative_path,
            "selector_fragment": None,
            "license": {
                "license_id": "CC0-1.0",
                "selector": license_ref["path"],
                "selector_fragment": None,
                "file": license_ref,
                "byte_size": (root / license_ref["path"]).stat().st_size,
            },
        },
    }


def _write_mechanical_assets(root: Path) -> None:
    license_path = root / "licenses/selfcheck.txt"
    license_path.parent.mkdir(parents=True)
    license_path.write_bytes(
        b"Deterministic SUMO selfcheck fixture; not benchmark evidence.\n"
    )
    (root / "assets").mkdir(parents=True, exist_ok=True)
    scalar_grid = {
        "schema_version": "aero-bench.scalar-grid/v1",
        "frame_id": "ENU",
        "east_axis_m": [-1_000.0, 1_000.0],
        "north_axis_m": [-1_000.0, 1_000.0],
        "values_m": [[0.0, 0.0], [0.0, 0.0]],
    }
    _write_json(root / "assets/geoid.json", scalar_grid)
    _write_json(root / "assets/terrain-heights.json", scalar_grid)
    for relative_path, label in (
        ("assets/imagery.bin", "imagery"),
        ("assets/person-model.bin", "person-model"),
        ("assets/scene.bin", "gazebo-scene"),
        ("assets/terrain-tiles.bin", "terrain-tiles"),
        ("assets/uav-model.bin", "uav-model"),
        ("assets/ugv-model.bin", "ugv-model"),
    ):
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(
            f"aero-bench-sumo-selfcheck:{label}".encode("utf-8")
        ).digest()
        destination.write_bytes(digest)


def _scenario_assets(root: Path) -> list[dict[str, Any]]:
    specifications = (
        (
            "geoid.fixture",
            "geoid_model",
            "assets/geoid.json",
            "private",
            ("flight", PROVIDER_ID),
        ),
        (
            "imagery.fixture",
            "imagery_tiles",
            "assets/imagery.bin",
            "public",
            (),
        ),
        (
            "person.model",
            "entity_model",
            "assets/person-model.bin",
            "private",
            (PROVIDER_ID,),
        ),
        (
            "scene.fixture",
            "other",
            "assets/scene.bin",
            "private",
            ("flight",),
        ),
        (
            "sumo.additional",
            "sumo_additional",
            SUMO_ADDITIONAL_PATH,
            "private",
            (PROVIDER_ID,),
        ),
        (
            "sumo.config",
            "sumo_config",
            SUMO_CONFIG_PATH,
            "private",
            (PROVIDER_ID,),
        ),
        (
            "sumo.network",
            "sumo_network",
            SUMO_NETWORK_PATH,
            "private",
            (PROVIDER_ID,),
        ),
        (
            "sumo.routes",
            "sumo_routes",
            SUMO_ROUTES_PATH,
            "private",
            (PROVIDER_ID,),
        ),
        (
            "terrain.fixture",
            "terrain_model",
            "assets/terrain-heights.json",
            "private",
            ("flight", PROVIDER_ID),
        ),
        (
            "terrain.tiles",
            "terrain_tiles",
            "assets/terrain-tiles.bin",
            "public",
            (),
        ),
        (
            "uav.model",
            "entity_model",
            "assets/uav-model.bin",
            "private",
            ("flight",),
        ),
        (
            "ugv.model",
            "entity_model",
            "assets/ugv-model.bin",
            "private",
            (PROVIDER_ID,),
        ),
    )
    assets = [
        _world_asset(
            root,
            asset_id=asset_id,
            role=role,
            relative_path=relative_path,
            classification=classification,
            provider_ids=provider_ids,
        )
        for asset_id, role, relative_path, classification, provider_ids in (
            specifications
        )
    ]
    assets.sort(key=lambda asset: asset["asset_id"])
    return assets


def _seal_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    scenario["scenario_asset_digest"] = hashlib.sha256(
        _canonical_json(scenario["assets"])
    ).hexdigest()
    body = dict(scenario)
    body.pop("scenario_digest", None)
    scenario["scenario_digest"] = hashlib.sha256(_canonical_json(body)).hexdigest()
    return scenario


def _scenario_document(root: Path) -> dict[str, Any]:
    identity_rotation = frame_math.RotationMatrix.identity()
    identity_quaternion = frame_math.UnitQuaternion(1.0, 0.0, 0.0, 0.0)
    sumo_quaternion = frame_math.UnitQuaternion.from_rotation_matrix(
        SUMO_FRAME_ROTATION
    )
    launch_pose = _pose(0.0, 0.0, 1.0)
    assets = _scenario_assets(root)
    scenario: dict[str, Any] = {
        "schema_version": "aero-bench.resolved-scenario/v4",
        "source_world_package": _fixture_file_ref("world-package"),
        "world_schema_version": "aero-bench.world/v2",
        "world_id": "sumo.selfcheck.world",
        "world_digest": _sha256_label("sumo-selfcheck-world"),
        "source_asset_digest": _sha256_label("sumo-selfcheck-source-assets"),
        "scenario_asset_digest": _sha256_label("unsealed-scenario-assets"),
        "selected_launch_site_id": "launch.fixture",
        "seed": SEED,
        "frame_authority": {
            "geodetic_frame_id": "WGS84",
            "ecef_frame_id": "ECEF",
            "enu_frame_id": "ENU",
            "ned_frame_id": "NED",
            "origin": _coordinate(0.0, 0.0, 0.0),
            "origin_ecef": {
                "x_m": SCENARIO_ORIGIN.origin_ecef.x,
                "y_m": SCENARIO_ORIGIN.origin_ecef.y,
                "z_m": SCENARIO_ORIGIN.origin_ecef.z,
            },
            "ecef_to_enu_rotation": _rotation(SCENARIO_ORIGIN.ecef_to_enu),
            "enu_to_ecef_rotation": _rotation(SCENARIO_ORIGIN.enu_to_ecef),
            "enu_to_ned_rotation": _rotation(frame_math.ENU_TO_NED_ROTATION),
            "ned_to_enu_rotation": _rotation(frame_math.NED_TO_ENU_ROTATION),
            "spatial_extent": {
                "min_east_m": -1_000.0,
                "max_east_m": 1_000.0,
                "min_north_m": -1_000.0,
                "max_north_m": 1_000.0,
                "min_up_m": -100.0,
                "max_up_m": 100.0,
                "vertical_reference": "enu_up",
            },
            "geoid_correction_asset_id": "geoid.fixture",
            "terrain_height_asset_id": "terrain.fixture",
            "geoid_interpolation": "bilinear",
            "terrain_interpolation": "bilinear",
            "geoid_precision_m": 0.05,
            "terrain_precision_m": 0.2,
        },
        "providers": [
            {
                "provider_id": "flight",
                "roles": ["motion"],
                "runtime_stage": "motion",
                "capability_ids": ["gazebo.physics"],
            },
            {
                "provider_id": PROVIDER_ID,
                "roles": ["traffic"],
                "runtime_stage": "motion",
                "capability_ids": ["traffic.state"],
            },
        ],
        "assets": assets,
        "task": {
            "task_id": "task.fixture",
            "package_id": "package.fixture",
            "package_config": _fixture_file_ref("task-config"),
            "package_schema": _fixture_file_ref("task-schema"),
            "instruction": _fixture_file_ref("instruction"),
            "verifier_id": "verifier.fixture",
            "required_capability_ids": [],
            "required_tool_ids": [],
            "logical_endpoint_ids": [],
            "asset_ids": [],
            "tools": [],
            "queries": [],
            "observations": [],
            "goals": [
                {
                    "goal_id": "goal.fixture",
                    "verifier_id": "verifier.fixture",
                    "metric_id": "selfcheck.completed",
                    "operator": "eq",
                    "threshold": 1.0,
                    "evidence": "authoritative_state",
                    "parameters": [],
                }
            ],
            "task_contract_digest": _sha256_label("sumo-selfcheck-task"),
        },
        "base_layers": [
            {
                "layer_id": "base.imagery",
                "kind": "imagery",
                "asset_id": "imagery.fixture",
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
                "launch_site_id": "launch.fixture",
                "primary_uav_entity_id": "uav.fixture",
                "allowed_uav_entity_ids": ["uav.fixture"],
                "pose": launch_pose,
                "pad_radius_m": 3.0,
                "selected": True,
            }
        ],
        "entities": [
            {
                "entity_id": SUMO_PERSON_ENTITY_ID,
                "kind": "pedestrian",
                "owner_kind": "provider",
                "owner_id": PROVIDER_ID,
                "source_provider_id": PROVIDER_ID,
                "authority_kind": "sumo_traffic",
                "state": "dynamic",
                "model_asset_id": "person.model",
                "initial_pose": _pose(10.0, -1.0, 0.0),
                "selected_launch_override": False,
            },
            {
                "entity_id": "uav.fixture",
                "kind": "uav",
                "owner_kind": "provider",
                "owner_id": "flight",
                "source_provider_id": "flight",
                "authority_kind": "gazebo_physics",
                "state": "dynamic",
                "model_asset_id": "uav.model",
                "initial_pose": launch_pose,
                "selected_launch_override": True,
            },
            {
                "entity_id": SUMO_ENTITY_ID,
                "kind": "ugv",
                "owner_kind": "provider",
                "owner_id": PROVIDER_ID,
                "source_provider_id": PROVIDER_ID,
                "authority_kind": "sumo_traffic",
                "state": "dynamic",
                "model_asset_id": "ugv.model",
                "initial_pose": _pose(10.0, 0.0, 0.0),
                "selected_launch_override": False,
            },
        ],
        "sensors": [],
        "semantic_targets": [],
        "weather": [
            {
                "sample_id": "weather.fixture",
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
                "provider_id": "flight",
                "engine": "gazebo",
                "scene_asset_id": "scene.fixture",
                "source_frame_id": "gazebo/world",
                "target_frame_id": "ENU",
                "translation_m": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0},
                "rotation_matrix": _rotation(identity_rotation),
                "rotation_quaternion": _quaternion(identity_quaternion),
            },
            {
                "binding_id": "frame.sumo",
                "provider_id": PROVIDER_ID,
                "engine": "sumo",
                "scene_asset_id": None,
                "source_frame_id": "sumo_net",
                "target_frame_id": "ENU",
                "translation_m": {
                    "x_m": SUMO_FRAME_TRANSLATION.x,
                    "y_m": SUMO_FRAME_TRANSLATION.y,
                    "z_m": SUMO_FRAME_TRANSLATION.z,
                },
                "rotation_matrix": _rotation(SUMO_FRAME_ROTATION),
                "rotation_quaternion": _quaternion(sumo_quaternion),
            },
        ],
        "sumo": {
            "provider_id": PROVIDER_ID,
            "config_asset_id": "sumo.config",
            "network_asset_id": "sumo.network",
            "routes_asset_id": "sumo.routes",
            "additional_asset_id": "sumo.additional",
            "frame_binding_id": "frame.sumo",
            "object_bindings": [
                {
                    "sumo_object_id": SUMO_OBJECT_ID,
                    "entity_id": SUMO_ENTITY_ID,
                    "kind": "vehicle",
                },
                {
                    "sumo_object_id": SUMO_PERSON_OBJECT_ID,
                    "entity_id": SUMO_PERSON_ENTITY_ID,
                    "kind": "person",
                },
            ],
        },
        "network": None,
        "mission_requirements": [],
        "expected_public_assets": [],
        "scenario_digest": _sha256_label("unsealed-scenario"),
    }
    return _seal_scenario(scenario)


def _provider_config(traci_port: int) -> dict[str, Any]:
    return {
        "schema_version": "aero-bench.sumo/v2",
        "provider_id": PROVIDER_ID,
        "sumo": {"version": SUMO_VERSION, "commit": SUMO_COMMIT},
        "sumo_binary": "sumo",
        "traci_port": traci_port,
        "step_length_ns": STEP_LENGTH_NS,
        "sumo_args": ["--no-step-log", "true"],
        "required_commands": ["sumo"],
        "command_timeout_ms": 10_000,
        "restrictions": [],
    }


def _provider_config_schema() -> dict[str, Any]:
    fields = [
        "schema_version",
        "provider_id",
        "sumo",
        "sumo_binary",
        "traci_port",
        "step_length_ns",
        "sumo_args",
        "required_commands",
        "command_timeout_ms",
        "restrictions",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:aero-bench:selfcheck:sumo-config-v2",
        "type": "object",
        "additionalProperties": False,
        "required": fields,
        "properties": {
            "schema_version": {"const": "aero-bench.sumo/v2"},
            "provider_id": {"const": PROVIDER_ID},
            "sumo": {
                "type": "object",
                "additionalProperties": False,
                "required": ["version", "commit"],
                "properties": {
                    "version": {"const": SUMO_VERSION},
                    "commit": {"const": SUMO_COMMIT},
                },
            },
            "sumo_binary": {"const": "sumo"},
            "traci_port": {"type": "integer", "minimum": 1024, "maximum": 65535},
            "step_length_ns": {"type": "integer", "minimum": 1},
            "sumo_args": {"type": "array", "items": {"type": "string"}},
            "required_commands": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "command_timeout_ms": {"type": "integer", "minimum": 1},
            "restrictions": {"type": "array", "maxItems": 0},
        },
    }


def _artifact_requirement() -> dict[str, str | int | None]:
    return {
        "artifact_id": EVIDENCE_ARTIFACT_ID,
        "artifact_type": EVIDENCE_ARTIFACT_TYPE,
        "producer_id": PROVIDER_ID,
        "visibility": "private",
        "relative_path": EVIDENCE_ARTIFACT_PATH,
        "max_size_bytes": EVIDENCE_MAX_SIZE_BYTES,
        "source_asset_id": None,
    }


def _scenario_projection(
    scenario: dict[str, Any], *, role: str, workload_id: str
) -> list[dict[str, Any]]:
    return [
        asset
        for asset in scenario["assets"]
        if any(
            audience == {"role": role, "workload_id": workload_id}
            for audience in asset["audiences"]
        )
    ]


def _write_workload_fixture(
    root: Path, *, rpc_port: int, traci_port: int
) -> tuple[WorkloadIdentity, dict[str, Any], dict[str, Any]]:
    network = root / SUMO_NETWORK_PATH
    subprocess.run(
        (
            "netgenerate",
            "--grid",
            "--grid.number=2",
            "--grid.length=100",
            "--output-file",
            str(network),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    _write_route(root / SUMO_ROUTES_PATH, _select_route(network))
    _write_additional(root / SUMO_ADDITIONAL_PATH)
    _write_sumo_config(root / SUMO_CONFIG_PATH)
    _write_mechanical_assets(root)

    config = _provider_config(traci_port)
    _write_json(root / "provider.config.json", config)
    _write_json(root / "provider.schema.json", _provider_config_schema())
    _write_json(
        root / "protocol.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:aero-bench:selfcheck:sumo-traci-v3",
            "type": "object",
        },
    )
    scenario = _scenario_document(root)
    requirement = _artifact_requirement()
    contract = {
        "schema_version": "aero-bench.workload-contract/v5",
        "role": "provider",
        "run_id": RUN_ID,
        "seed": SEED,
        "workload_id": PROVIDER_ID,
        "clock": {
            "authority": "provider_barrier",
            "step_ns": STEP_LENGTH_NS,
            "max_steps": 10,
            "provider_timeout_ms": 10_000,
        },
        "provider": {
            "provider_id": PROVIDER_ID,
            "adapter": PROVIDER_ADAPTER,
            "port": rpc_port,
            "workload": {
                "runtime": {
                    "image": RUNTIME_IMAGE,
                    "command": ["provider", "serve"],
                },
                "resources": {
                    "cpu_millicores": 1_000,
                    "memory_mib": 1_024,
                    "gpu_count": 0,
                },
                "implementation": {
                    "component_id": PROVIDER_ADAPTER,
                    "kind": "mechanical_fixture",
                    "source_uri": "https://github.com/eclipse-sumo/sumo",
                    "source_revision": SUMO_COMMIT,
                    "version": f"{SUMO_VERSION}-selfcheck",
                },
            },
            "config": {
                "file": _file_ref(root, "provider.config.json"),
                "schema_file": _file_ref(root, "provider.schema.json"),
            },
            "protocol_schema": _file_ref(root, "protocol.schema.json"),
            "capabilities": ["traffic.state"],
            "artifact_requirements": [requirement],
        },
        "scenario_digest": scenario["scenario_digest"],
        "scenario": scenario,
        "scenario_assets": _scenario_projection(
            scenario, role="provider", workload_id=PROVIDER_ID
        ),
    }
    contract_path = root / "workload.contract.json"
    _write_json(contract_path, contract)
    identity = _load_workload_identity(
        contract_path=contract_path,
        bundle_root=root,
        expected_run_id=RUN_ID,
        expected_seed=SEED,
        expected_provider_id=PROVIDER_ID,
        expected_provider_port=rpc_port,
    )
    return identity, config, scenario


def _assert_fixture_projection(
    identity: WorkloadIdentity,
    config: dict[str, Any],
    scenario: dict[str, Any],
    root: Path,
) -> None:
    if config["schema_version"] != "aero-bench.sumo/v2" or {
        "scenario_config",
        "scenario_files",
    }.intersection(config):
        raise RuntimeError("SUMO selfcheck config is not the closed v2 declaration")
    if (
        identity.scenario_digest != scenario["scenario_digest"]
        or identity.scenario.scenario != scenario
    ):
        raise RuntimeError(
            "SUMO selfcheck scenario digest/projection is not contract-bound"
        )
    expected_files = tuple(
        _file_ref(root, relative_path)
        for relative_path in (
            SUMO_CONFIG_PATH,
            SUMO_NETWORK_PATH,
            SUMO_ROUTES_PATH,
            SUMO_ADDITIONAL_PATH,
        )
    )
    if (
        identity.scenario_config != expected_files[0]
        or identity.scenario_files != expected_files
    ):
        raise RuntimeError("SUMO scenario files were not derived from ResolvedScenario")
    if identity.object_bindings != (
        {
            "sumo_object_id": SUMO_OBJECT_ID,
            "entity_id": SUMO_ENTITY_ID,
            "kind": "vehicle",
        },
        {
            "sumo_object_id": SUMO_PERSON_OBJECT_ID,
            "entity_id": SUMO_PERSON_ENTITY_ID,
            "kind": "person",
        },
    ):
        raise RuntimeError("SUMO object bindings differ from ResolvedScenario")
    probe = identity.frame_transform.apply_position(frame_math.Vector3(2.0, 3.0, 0.0))
    if probe != frame_math.Vector3(7.0, -18.0, 3.0):
        raise RuntimeError("SUMO-to-ENU transform differs from ResolvedScenario")


def _available_port(*, excluding: frozenset[int] = frozenset()) -> int:
    while True:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in excluding:
            return port


async def _rpc_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    operation: str,
    payload: dict[str, object],
) -> dict[str, object]:
    writer.write(_canonical_json({"operation": operation, **payload}) + b"\n")
    await writer.drain()
    frame = await reader.readline()
    if not frame:
        raise RuntimeError("SUMO selfcheck RPC server closed the stream")
    response = json.loads(frame)
    if not isinstance(response, dict):
        raise RuntimeError("SUMO selfcheck RPC response is not an object")
    if "error" in response:
        raise RuntimeError(f"SUMO selfcheck RPC failed: {response['error']}")
    return response


def _assert_state_receipt(
    response: dict[str, object], *, tick: int, sim_time_ns: int
) -> dict[str, object]:
    receipt = response.get("receipt")
    expected_time = {"tick": tick, "sim_time_ns": sim_time_ns}
    if not isinstance(receipt, dict) or receipt.get("reached") != expected_time:
        raise RuntimeError(f"SUMO fixture did not reach exact target: {response}")
    events = receipt.get("events")
    state_events = (
        [
            event
            for event in events
            if isinstance(event, dict)
            and event.get("payload_schema_id") == STATE_SCHEMA
        ]
        if isinstance(events, list)
        else []
    )
    if len(state_events) != 1:
        raise RuntimeError(f"SUMO receipt omitted {STATE_SCHEMA}: {receipt}")
    return receipt


def _assert_motion_stage_response(
    response: dict[str, object], *, tick: int, sim_time_ns: int, scenario_digest: str
) -> dict[str, object]:
    receipt = _assert_state_receipt(
        response,
        tick=tick,
        sim_time_ns=sim_time_ns,
    )
    expected_time = {"tick": tick, "sim_time_ns": sim_time_ns}
    if (
        response.get("schema_version")
        != "aero-bench.sumo-motion-stage-response/v1"
        or response.get("run_id") != RUN_ID
        or response.get("scenario_digest") != scenario_digest
        or response.get("provider_id") != PROVIDER_ID
        or response.get("target") != expected_time
        or response.get("stage") != "motion"
    ):
        raise RuntimeError("SUMO staged response binding is invalid")
    samples = response.get("samples")
    if not isinstance(samples, list) or len(samples) != 2:
        raise RuntimeError("SUMO staged response does not close entity samples")
    expected_entity_ids = (SUMO_PERSON_ENTITY_ID, SUMO_ENTITY_ID)
    if tuple(
        sample.get("entity_id") if isinstance(sample, dict) else None
        for sample in samples
    ) != expected_entity_ids:
        raise RuntimeError("SUMO staged samples are not canonically ordered")
    expected_sample_fields = {
        "schema_version",
        "run_id",
        "scenario_digest",
        "at",
        "stage",
        "entity_id",
        "provider_id",
        "sample_kind",
        "pose",
        "linear_velocity_enu",
        "linear_velocity_ned",
        "angular_velocity_body",
        "mode",
        "armed",
        "battery",
        "health",
        "contacts",
        "attributes",
        "sample_digest",
    }
    for sample in samples:
        if not isinstance(sample, dict):
            raise RuntimeError("SUMO staged response contains an invalid state sample")
        body = dict(sample)
        sample_digest = body.pop("sample_digest", None)
        if (
            set(sample) != expected_sample_fields
            or sample.get("schema_version") != "aero-bench.state-sample/v1"
            or sample.get("run_id") != RUN_ID
            or sample.get("scenario_digest") != scenario_digest
            or sample.get("at") != expected_time
            or sample.get("stage") != "motion"
            or sample.get("provider_id") != PROVIDER_ID
            or sample.get("sample_kind") != "dynamic"
            or sample.get("mode") not in {"pending", "active", "arrived", "removed"}
            or sample_digest != hashlib.sha256(_canonical_json(body)).hexdigest()
        ):
            raise RuntimeError("SUMO staged state sample is invalid")
    state_digest = receipt.get("state_digest")
    if not isinstance(state_digest, str) or len(state_digest) != 64:
        raise RuntimeError("SUMO staged receipt state digest is invalid")
    return samples[0]


async def _run_selfcheck() -> int:
    with tempfile.TemporaryDirectory(prefix="aero-sumo-selfcheck-") as temporary:
        root = Path(temporary) / "scenario"
        artifacts = Path(temporary) / "artifacts"
        root.mkdir()
        artifacts.mkdir()
        rpc_port = _available_port()
        traci_port = _available_port(excluding=frozenset({rpc_port}))
        identity, config, scenario = _write_workload_fixture(
            root, rpc_port=rpc_port, traci_port=traci_port
        )
        _assert_fixture_projection(identity, config, scenario, root)

        service = SumoService(
            scenario_root=root,
            artifact_root=artifacts,
            rpc_port=rpc_port,
            workload_identity=identity,
            session_token=SESSION_TOKEN,
        )
        server = await asyncio.start_server(
            lambda reader, writer: _client_handler(service, reader, writer),
            host="127.0.0.1",
            port=rpc_port,
            limit=MAX_FRAME_BYTES + 1,
        )
        reader, writer = await asyncio.open_connection("127.0.0.1", rpc_port)
        finalization_receipt: dict[str, object] | None = None
        try:
            prepare_payload = {
                "provider_id": PROVIDER_ID,
                "run_id": RUN_ID,
                "protocol_version": PROTOCOL_VERSION,
                "session_token": SESSION_TOKEN,
                "runtime_image": RUNTIME_IMAGE,
                "config_digest": identity.config_digest,
                "artifact_requirements": [identity.artifact_requirement],
                "sumo": config["sumo"],
                "sumo_binary": config["sumo_binary"],
                "traci_port": config["traci_port"],
                "step_length_ns": config["step_length_ns"],
                "sumo_args": config["sumo_args"],
                "required_commands": config["required_commands"],
                "command_timeout_ms": config["command_timeout_ms"],
                "restrictions": config["restrictions"],
            }
            if {"scenario_config", "scenario_files"}.intersection(prepare_payload):
                raise RuntimeError("SUMO prepare must not repeat scenario-owned files")
            readiness = await _rpc_request(
                reader, writer, "prepare", prepare_payload
            )
            if (
                readiness.get("status") != "ready"
                or readiness.get("protocol_version") != PROTOCOL_VERSION
                or readiness.get("scenario_digest") != identity.scenario_digest
            ):
                raise RuntimeError(f"SUMO service did not become ready: {readiness}")
            reset = await _rpc_request(
                reader,
                writer,
                "reset",
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "seed": SEED,
                    "session_token": SESSION_TOKEN,
                },
            )
            _assert_state_receipt(reset, tick=0, sim_time_ns=0)
            for tick in range(1, 11):
                response = await _rpc_request(
                    reader,
                    writer,
                    "step_stage",
                    {
                        "provider_id": PROVIDER_ID,
                        "run_id": RUN_ID,
                        "session_token": SESSION_TOKEN,
                        "request": {
                            "schema_version": "aero-bench.provider-stage-request/v1",
                            "run_id": RUN_ID,
                            "scenario_digest": identity.scenario_digest,
                            "provider_id": PROVIDER_ID,
                            "target": {
                                "tick": tick,
                                "sim_time_ns": tick * STEP_LENGTH_NS,
                            },
                            "stage": "motion",
                        },
                    },
                )
                _assert_motion_stage_response(
                    response,
                    tick=tick,
                    sim_time_ns=tick * STEP_LENGTH_NS,
                    scenario_digest=identity.scenario_digest,
                )
            snapshot = await _rpc_request(
                reader,
                writer,
                "snapshot",
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "session_token": SESSION_TOKEN,
                },
            )
            if not isinstance(snapshot.get("snapshot_digest"), str):
                raise RuntimeError("SUMO snapshot did not return a digest")
            finalization = await _rpc_request(
                reader,
                writer,
                "finalize",
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "session_token": SESSION_TOKEN,
                    "request": {
                        "schema_version": "aero-bench.provider-finalization-request/v1",
                        "run_id": RUN_ID,
                        "terminal_event": "run.completed",
                        "terminal_time": {
                            "tick": 10,
                            "sim_time_ns": 10 * STEP_LENGTH_NS,
                        },
                        "event_chain_root": "d" * 64,
                    },
                },
            )
            candidate = finalization.get("receipt")
            if not isinstance(candidate, dict):
                raise RuntimeError("SUMO service did not finalize its fixture artifact")
            finalization_receipt = candidate
            await _rpc_request(
                reader,
                writer,
                "shutdown",
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "session_token": SESSION_TOKEN,
                },
            )
        finally:
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()
            await asyncio.to_thread(service.close_runtime)

        evidence = artifacts / EVIDENCE_ARTIFACT_PATH
        if not evidence.is_file():
            raise RuntimeError("SUMO service did not write its fixture artifact")
        records = [json.loads(line) for line in evidence.read_text().splitlines()]
        state_records = records[:-1]
        finalization_binding = records[-1] if records else None
        if len(state_records) != 12 or any(
            record.get("schema_version") != EVIDENCE_SCHEMA
            or record.get("artifact_id") != EVIDENCE_ARTIFACT_ID
            or record.get("artifact_type") != EVIDENCE_ARTIFACT_TYPE
            or record.get("scenario_config_sha256")
            != identity.scenario_config["sha256"]
            for record in state_records
        ):
            raise RuntimeError(
                "SUMO fixture artifact does not contain every expected v3 state"
            )
        if finalization_binding != {
            "schema_version": FINALIZATION_BINDING_SCHEMA,
            "run_id": RUN_ID,
            "provider_id": PROVIDER_ID,
            "terminal_event": "run.completed",
            "terminal_time": {"tick": 10, "sim_time_ns": 10 * STEP_LENGTH_NS},
            "event_chain_root": "d" * 64,
        }:
            raise RuntimeError("SUMO fixture artifact is not bound to finalization")

        positions: list[dict[str, float]] = []
        observed_lifecycles = {
            SUMO_PERSON_ENTITY_ID: set(),
            SUMO_ENTITY_ID: set(),
        }
        process_stream_offsets = {"stderr": 0, "stdout": 0}
        expected_entity_fields = {
            "entity_id",
            "sumo_object_id",
            "kind",
            "lifecycle",
            "pose",
            "position_enu_m",
            "linear_velocity_enu",
            "linear_velocity_ned",
            "speed_mps",
            "yaw_enu_rad",
            "road_id",
            "lane_id",
        }
        for record in state_records:
            process_streams = record.get("process_streams")
            if (
                not isinstance(process_streams, list)
                or [
                    stream.get("stream")
                    for stream in process_streams
                    if isinstance(stream, dict)
                ]
                != ["stderr", "stdout"]
            ):
                raise RuntimeError("SUMO process stream capture is incomplete")
            for stream in process_streams:
                if not isinstance(stream, dict):
                    raise RuntimeError("SUMO process stream record is invalid")
                stream_name = stream["stream"]
                try:
                    content = base64.b64decode(stream["data"], validate=True)
                except (TypeError, binascii.Error) as exc:
                    raise RuntimeError("SUMO process stream payload is invalid") from exc
                if (
                    stream.get("encoding") != "base64"
                    or stream.get("offset_bytes")
                    != process_stream_offsets[stream_name]
                    or stream.get("size_bytes") != len(content)
                    or stream.get("sha256")
                    != hashlib.sha256(content).hexdigest()
                ):
                    raise RuntimeError("SUMO process stream binding is invalid")
                process_stream_offsets[stream_name] += len(content)
            snapshot = record.get("snapshot")
            entities = snapshot.get("entities") if isinstance(snapshot, dict) else None
            if not isinstance(entities, list) or len(entities) != 2:
                raise RuntimeError(
                    "SUMO fixture state does not match exact object bindings"
                )
            expected_identities = {
                SUMO_PERSON_ENTITY_ID: (SUMO_PERSON_OBJECT_ID, "person"),
                SUMO_ENTITY_ID: (SUMO_OBJECT_ID, "vehicle"),
            }
            if tuple(
                entity.get("entity_id") if isinstance(entity, dict) else None
                for entity in entities
            ) != tuple(sorted(expected_identities)):
                raise RuntimeError("SUMO fixture entities are not canonically ordered")
            for entity in entities:
                if not isinstance(entity, dict):
                    raise RuntimeError("SUMO fixture entity is not an object")
                entity_id = entity.get("entity_id")
                expected_identity = expected_identities.get(entity_id)
                lifecycle = entity.get("lifecycle")
                if (
                    set(entity) != expected_entity_fields
                    or expected_identity is None
                    or entity.get("sumo_object_id") != expected_identity[0]
                    or entity.get("kind") != expected_identity[1]
                    or lifecycle not in {"pending", "active", "arrived", "removed"}
                ):
                    raise RuntimeError("SUMO fixture entity identity is not exact")
                observed_lifecycles[entity_id].add(lifecycle)
                position = entity["position_enu_m"]
                if (
                    not isinstance(position, dict)
                    or set(position) != {"east_m", "north_m", "up_m"}
                    or any(
                        isinstance(position[field], bool)
                        or not isinstance(position[field], (int, float))
                        or not math.isfinite(float(position[field]))
                        for field in ("east_m", "north_m", "up_m")
                    )
                    or (
                        lifecycle == "active"
                        and not math.isclose(
                            float(position["up_m"]),
                            SUMO_FRAME_TRANSLATION.z,
                            abs_tol=1e-9,
                        )
                    )
                ):
                    raise RuntimeError(
                        "SUMO fixture state is not in the declared ENU frame"
                    )
                if entity_id == SUMO_ENTITY_ID and lifecycle == "active":
                    positions.append(
                        {
                            "east_m": float(position["east_m"]),
                            "north_m": float(position["north_m"]),
                            "up_m": float(position["up_m"]),
                        }
                    )
        if any("active" not in states for states in observed_lifecycles.values()):
            raise RuntimeError("SUMO fixture did not observe both vehicle and person")
        if len(positions) < 2 or positions[0] == positions[-1]:
            raise RuntimeError("SUMO mechanical fixture produced no vehicle motion")

        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        finalized_artifacts = (
            None
            if finalization_receipt is None
            else finalization_receipt.get("artifacts")
        )
        if (
            finalization_receipt is None
            or finalization_receipt.get("event_chain_root") != "d" * 64
            or finalized_artifacts
            != [
                {
                    "artifact_id": EVIDENCE_ARTIFACT_ID,
                    "sha256": digest,
                    "size_bytes": evidence.stat().st_size,
                }
            ]
        ):
            raise RuntimeError(
                "SUMO finalization receipt does not bind the fixture artifact"
            )
        print(
            json.dumps(
                {
                    "commit": SUMO_COMMIT,
                    "evidence_schema": EVIDENCE_SCHEMA,
                    "fixture_artifact_id": EVIDENCE_ARTIFACT_ID,
                    "fixture_artifact_path": EVIDENCE_ARTIFACT_PATH,
                    "fixture_artifact_sha256": digest,
                    "fixture_artifact_size_bytes": evidence.stat().st_size,
                    "fixture_kind": "deterministic_mechanical",
                    "formal_evidence": False,
                    "object_bindings": list(identity.object_bindings),
                    "protocol_version": PROTOCOL_VERSION,
                    "scenario_digest": identity.scenario_digest,
                    "seed": SEED,
                    "start_position_enu_m": positions[0],
                    "end_position_enu_m": positions[-1],
                    "state_schema": STATE_SCHEMA,
                    "status": "mechanical-sumo-rpc-selfcheck-ok",
                    "version": SUMO_VERSION,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        from service import main as service_main

        return service_main(arguments)
    return asyncio.run(_run_selfcheck())


if __name__ == "__main__":
    raise SystemExit(main())
