#!/usr/bin/env python3
"""Validate Huangpu Provider workloads with their production parsers.

The command stages the exact materialized inputs with hard links in a temporary
directory and loads the production PX4/Gazebo, SUMO, ns-3, and Inspection
Business service parsers. It does not start a Provider, Docker, or the formal
run.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from typing import Any
import xml.etree.ElementTree as ET


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aero_bench.authoring.city_native_registration import (  # noqa: E402
    CityNativeRegistrationError,
    assess_city_native_registration,
    load_city_native_input_lock,
)
from aero_bench.config.loader import BundleReader, load_suite, sha256_file  # noqa: E402
from aero_bench.config.resolver import resolve_suite  # noqa: E402
from aero_bench.executor.planning import build_execution_plan  # noqa: E402
from aero_bench.providers import rpc  # noqa: E402
from aero_bench.providers.registry import builtin_provider_registry  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.tasks.registry import builtin_task_package_resolvers  # noqa: E402
from aero_bench.world import frame_math, workload_scenario  # noqa: E402


REPORT_SCHEMA_VERSION = "aero-bench.huangpu-native-provider-contract-check/v1"
SERVICE_PATHS = {
    "flight": REPOSITORY_ROOT / "containers/px4-gazebo/service.py",
    "traffic": REPOSITORY_ROOT / "containers/sumo/service.py",
    "network": REPOSITORY_ROOT / "containers/ns3/server.py",
    "business": REPOSITORY_ROOT / "containers/inspection-business/service.py",
}


class HuangpuNativeProviderContractError(ValueError):
    """A registered Provider workload fails its production parser contract."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="Repository root used to resolve the registered files.",
    )
    parser.add_argument("--input-lock", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def _load_service(provider_id: str) -> ModuleType:
    sys.modules["aero_frame_math"] = frame_math
    sys.modules["rpc"] = rpc
    sys.modules["workload_scenario"] = workload_scenario
    path = SERVICE_PATHS[provider_id]
    module_name = f"huangpu_native_{provider_id}_service_validation"
    specification = importlib.util.spec_from_file_location(
        module_name,
        path,
    )
    if specification is None or specification.loader is None:
        raise HuangpuNativeProviderContractError(
            f"production {provider_id} service source cannot be imported"
        )
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(document) + b"\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _shape_points(shape: str, *, label: str) -> list[frame_math.Vector3]:
    points: list[frame_math.Vector3] = []
    for token in shape.split():
        components = token.split(",")
        if len(components) not in {2, 3}:
            raise HuangpuNativeProviderContractError(
                f"{label} has an invalid point: {token}"
            )
        try:
            east_m = float(components[0])
            north_m = float(components[1])
        except ValueError as exc:
            raise HuangpuNativeProviderContractError(
                f"{label} has a non-numeric point: {token}"
            ) from exc
        if not (math.isfinite(east_m) and math.isfinite(north_m)):
            raise HuangpuNativeProviderContractError(f"{label} has a non-finite point")
        points.append(frame_math.Vector3(east_m, north_m, 0.0))
    if not points:
        raise HuangpuNativeProviderContractError(f"{label} has an empty shape")
    return points


def _sumo_surface(
    *,
    service: ModuleType,
    bundle_root: Path,
    identity: object,
) -> dict[str, object]:
    sumo = identity.scenario.scenario["sumo"]
    network_asset = identity.scenario.asset(sumo["network_asset_id"])
    reference = network_asset["file"]
    network_path = service._resolve_root_file(
        bundle_root,
        reference,
        label="ResolvedScenario SUMO network",
    )
    try:
        network = ET.parse(network_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise HuangpuNativeProviderContractError(
            f"production SUMO network cannot be parsed: {exc}"
        ) from exc

    source_points: list[frame_math.Vector3] = []
    lane_count = 0
    for lane in network.iter("lane"):
        shape = lane.get("shape")
        if not shape:
            raise HuangpuNativeProviderContractError(
                f"SUMO lane has no shape: {lane.get('id')}"
            )
        source_points.extend(_shape_points(shape, label=f"SUMO lane {lane.get('id')}"))
        lane_count += 1

    junction_count = 0
    for junction in network.findall("junction"):
        shape = junction.get("shape")
        if shape:
            source_points.extend(
                _shape_points(shape, label=f"SUMO junction {junction.get('id')}")
            )
        source_points.extend(
            _shape_points(
                f"{junction.get('x')},{junction.get('y')}",
                label=f"SUMO junction {junction.get('id')} position",
            )
        )
        junction_count += 1
    if not source_points:
        raise HuangpuNativeProviderContractError(
            "production SUMO network has no lane or junction surface points"
        )

    try:
        enu_points = [
            identity.frame_transform.apply_position(point) for point in source_points
        ]
    except frame_math.FrameMathError as exc:
        raise HuangpuNativeProviderContractError(
            "SUMO network surface cannot be transformed into ENU"
        ) from exc
    bounds = {
        "min_east_m": min(point.x for point in enu_points),
        "max_east_m": max(point.x for point in enu_points),
        "min_north_m": min(point.y for point in enu_points),
        "max_north_m": max(point.y for point in enu_points),
    }
    return {
        "network_path": reference["path"],
        "network_sha256": reference["sha256"],
        "lane_count": lane_count,
        "junction_count": junction_count,
        "surface_point_count": len(source_points),
        "surface_bounds_enu_m": bounds,
    }


def _validate_grid_surface_coverage(
    *,
    grid: frame_math.ScalarGridSampler,
    grid_path: str,
    interpolation: str,
    label: str,
    surface_bounds: dict[str, float],
) -> dict[str, object]:
    grid_bounds = {
        "min_east_m": grid.east_axis_m[0],
        "max_east_m": grid.east_axis_m[-1],
        "min_north_m": grid.north_axis_m[0],
        "max_north_m": grid.north_axis_m[-1],
    }
    outside = [
        key
        for key in (
            "min_east_m",
            "max_east_m",
            "min_north_m",
            "max_north_m",
        )
        if (
            surface_bounds[key] < grid_bounds[key]
            if key.startswith("min_")
            else surface_bounds[key] > grid_bounds[key]
        )
    ]
    if outside:
        raise HuangpuNativeProviderContractError(
            f"{label} does not cover the SUMO network surface: "
            f"outside={outside}, grid={grid_bounds}, surface={surface_bounds}"
        )
    try:
        for east_m in (
            surface_bounds["min_east_m"],
            surface_bounds["max_east_m"],
        ):
            for north_m in (
                surface_bounds["min_north_m"],
                surface_bounds["max_north_m"],
            ):
                grid.sample(east_m, north_m, interpolation)
    except frame_math.FrameMathError as exc:
        raise HuangpuNativeProviderContractError(
            f"{label} cannot sample a SUMO network surface corner"
        ) from exc
    return {
        "path": grid_path,
        "interpolation": interpolation,
        "east_axis_m": list(grid.east_axis_m),
        "north_axis_m": list(grid.north_axis_m),
        "coverage_bounds_enu_m": grid_bounds,
        "covers_sumo_surface": True,
    }


def build_report(*, root: Path, input_lock_path: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    definition = load_city_native_input_lock(input_lock_path)
    if definition.execution is None:
        raise HuangpuNativeProviderContractError(
            "city input lock has no execution binding"
        )
    readiness = assess_city_native_registration(root, definition)
    if readiness.run_id is None:
        raise HuangpuNativeProviderContractError(
            "city execution binding did not resolve a Run ID"
        )

    reader = BundleReader(root)
    suite_path = reader.resolve_file(definition.execution.suite.file)
    loaded_suite = load_suite(suite_path)
    registry = builtin_provider_registry()
    runs = resolve_suite(
        str(suite_path),
        executor_kind="docker_reference",
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=registry,
    )
    if len(runs) != 1 or runs[0].run_id != readiness.run_id:
        raise HuangpuNativeProviderContractError(
            "registered city Suite does not resolve the assessed Run ID"
        )
    run = runs[0]
    plan = build_execution_plan(
        run,
        executor_kind="docker_reference",
        bundle_root=loaded_suite.root,
        task_package_resolvers=builtin_task_package_resolvers(),
        provider_registry=registry,
    )
    provider_ids = ("flight", "traffic", "network", "business")
    workload_plans = {
        item.workload_id: item
        for item in plan.runtime_workloads
        if item.workload_id in provider_ids
    }
    providers = {
        item.provider_id: item
        for item in run.environment.providers
        if item.provider_id in provider_ids
    }
    if set(workload_plans) != set(provider_ids) or set(providers) != set(provider_ids):
        raise HuangpuNativeProviderContractError(
            "city run must materialize the four declared production Providers"
        )
    services = {provider_id: _load_service(provider_id) for provider_id in provider_ids}

    report_parent = root / "validation/codex-takeover-20261001/B"
    with tempfile.TemporaryDirectory(
        prefix=".huangpu-provider-contract-", dir=report_parent
    ) as temporary:
        temporary_root = Path(temporary).resolve()
        identities: dict[str, object] = {}
        for provider_id in provider_ids:
            workload = workload_plans[provider_id]
            provider = providers[provider_id]
            staging = temporary_root / provider_id
            bundle = staging / "bundle"
            staging.mkdir()
            for item in workload.bundle_inputs:
                source = loaded_suite.root / item.source.path
                destination = staging / item.destination
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, destination)
            contract_path = staging / "contract.json"
            contract_path.write_text(
                workload.contract.content_utf8,
                encoding="utf-8",
            )
            loader = (
                services[provider_id]._load_ns3_workload_identity
                if provider_id == "network"
                else services[provider_id]._load_workload_identity
            )
            identities[provider_id] = loader(
                contract_path=contract_path,
                bundle_root=bundle,
                expected_run_id=run.run_id,
                expected_provider_id=provider_id,
                expected_provider_port=provider.port,
                expected_seed=run.seed,
            )

        identity = identities["flight"]
        flight_bundle = temporary_root / "flight/bundle"
        aliases = dict(
            services["flight"]._scenario_contact_model_tokens(
                scenario=identity.scenario,
                vehicles=identity.vehicles,
                world_source_sdf=identity.world_source_sdf,
                inspections=identity.inspections,
            )
        )
        expected_launch_token = "launch_pad.launch.huangpu-research"
        if aliases.get("launch.huangpu-research") != expected_launch_token:
            raise HuangpuNativeProviderContractError(
                "production PX4 parser did not classify the declared launch pad"
            )
        if aliases.get("ground") != "ground.world":
            raise HuangpuNativeProviderContractError(
                "production PX4 parser did not classify the city ground plane"
            )
        world_sdf = identity.world_source_sdf
        world_relative = world_sdf.relative_to(flight_bundle).as_posix()
        traffic = identities["traffic"]
        traffic_bundle = temporary_root / "traffic/bundle"
        surface = _sumo_surface(
            service=services["traffic"],
            bundle_root=traffic_bundle,
            identity=traffic,
        )
        surface_bounds = surface["surface_bounds_enu_m"]
        if not isinstance(surface_bounds, dict):
            raise HuangpuNativeProviderContractError(
                "SUMO network surface bounds are malformed"
            )
        geoid_grid = services["traffic"]._load_scalar_grid(
            traffic_bundle,
            traffic.geoid_grid_file,
            label="ResolvedScenario geoid scalar grid",
        )
        terrain_grid = services["traffic"]._load_scalar_grid(
            traffic_bundle,
            traffic.terrain_grid_file,
            label="ResolvedScenario terrain scalar grid",
        )
        coordinate_coverage = {
            "sumo_surface": surface,
            "geoid_grid": _validate_grid_surface_coverage(
                grid=geoid_grid,
                grid_path=traffic.geoid_grid_file["path"],
                interpolation=traffic.geoid_interpolation,
                label="geoid scalar grid",
                surface_bounds=surface_bounds,
            ),
            "terrain_grid": _validate_grid_surface_coverage(
                grid=terrain_grid,
                grid_path=traffic.terrain_grid_file["path"],
                interpolation=traffic.terrain_interpolation,
                label="terrain scalar grid",
                surface_bounds=surface_bounds,
            ),
        }
        network = identities["network"]
        business = identities["business"]
        result = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "validated-not-executed",
            "run_id": identity.run_id,
            "scenario_digest": run.scenario.scenario_digest,
            "runtime_images": {
                provider_id: workload_plans[provider_id].workload.runtime.image
                for provider_id in provider_ids
            },
            "service_sources": {
                provider_id: {
                    "path": SERVICE_PATHS[provider_id].relative_to(root).as_posix(),
                    "sha256": sha256_file(SERVICE_PATHS[provider_id]),
                    "size_bytes": SERVICE_PATHS[provider_id].stat().st_size,
                }
                for provider_id in provider_ids
            },
            "world": {
                "name": identity.world_name,
                "path": world_relative,
                "sha256": sha256_file(world_sdf),
                "size_bytes": world_sdf.stat().st_size,
            },
            "providers": {
                "flight": {
                    "input_count": len(workload_plans["flight"].bundle_inputs),
                    "vehicle_count": len(identity.vehicles),
                    "inspection_count": len(identity.inspections),
                    "contact_alias_count": len(aliases),
                    "launch_contact": {
                        "model_name": "launch.huangpu-research",
                        "token": expected_launch_token,
                    },
                    "ground_contact": {
                        "model_name": "ground",
                        "token": "ground.world",
                    },
                },
                "traffic": {
                    "input_count": len(workload_plans["traffic"].bundle_inputs),
                    "scenario_config": traffic.scenario_config["path"],
                    "scenario_file_count": len(traffic.scenario_files),
                    "object_binding_count": len(traffic.object_bindings),
                    "artifact_type": traffic.artifact_requirement["artifact_type"],
                },
                "network": {
                    "input_count": len(workload_plans["network"].bundle_inputs),
                    "radio_count": len(network.network.radio_profiles),
                    "node_count": len(network.network.node_bindings),
                    "link_count": len(network.network.links),
                    "propagation_volume_count": len(
                        network.network.propagation_volumes
                    ),
                    "artifact_type": network.artifact_requirement["artifact_type"],
                },
                "business": {
                    "input_count": len(workload_plans["business"].bundle_inputs),
                    "config_sha256": business.config_digest,
                    "artifact_type": business.artifact_requirement["artifact_type"],
                },
            },
            "coordinate_coverage": coordinate_coverage,
            "formal_execution_started": False,
            "validation_scope": (
                "Production service contract parsing plus PX4 contact "
                "classification and SUMO-surface datum-grid coverage; no Provider "
                "process, physics, barrier, or formal execution."
            ),
        }
    return result


def main() -> int:
    args = _parser().parse_args()
    try:
        report = build_report(root=args.root, input_lock_path=args.input_lock)
        _write_atomic(args.report, report)
    except (
        CityNativeRegistrationError,
        HuangpuNativeProviderContractError,
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"city Provider contract validation failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "report": str(args.report),
                "run_id": report["run_id"],
                "status": report["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
