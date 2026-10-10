"""Publish a distinct non-physical arrivals profile without executing it."""

import hashlib
from pathlib import Path

from aero_bench.authoring.native_registry import (
    LogisticsArrivalsSceneDefinition,
    NativeSceneRegistryManifest,
    verify_logistics_arrivals_scene,
)
from aero_bench.authoring.reference_registration import _references
from aero_bench.authoring.workspace import CityOrderGeneration, WORKSPACE_SCHEMA
from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import FileRef
from aero_bench.config.resolver import resolve_suite
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.registry import builtin_provider_registry
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics_arrivals.contracts import PACKAGE_ID
from aero_bench.tasks.logistics_arrivals.integration import (
    LogisticsArrivalsTaskPackageResolver,
)
from aero_bench.trace.projector import project_public_scenario
from aero_bench.world.contracts import WorldPackage


def reference(root, relative):
    return FileRef(
        path=relative, sha256=hashlib.sha256((root / relative).read_bytes()).hexdigest()
    )


def publish_arrivals_registration(*, suite: Path, output: Path, registration_id: str):
    loaded = load_suite(suite)
    reader = BundleReader(loaded.root)
    runs = resolve_suite(
        str(suite),
        executor_kind="docker_reference",
        task_package_resolvers=(LogisticsArrivalsTaskPackageResolver(),),
        provider_registry=builtin_provider_registry(),
    )
    if len(runs) != 1 or len(loaded.suite.cases) != 1:
        raise ValueError("arrivals publisher requires exactly one native case/run")
    run = runs[0]
    world_ref = loaded.suite.cases[0].world_package
    world = WorldPackage.model_validate(reader.load_document(world_ref))
    config = LogisticsBusinessConfig.model_validate(
        reader.validate_schema_bound_file(run.environment.providers[0].config)
    )
    raw_files = {}
    for pin in (
        *_references(loaded.suite.model_dump(mode="json")),
        *_references(run.model_dump(mode="json")),
    ):
        raw = reader.resolve_file(pin).read_bytes()
        if pin.path in raw_files and raw_files[pin.path] != raw:
            raise ValueError("arrivals inputs have conflicting file identities")
        raw_files[pin.path] = raw
    scene_path = "/city-presentation/logistics-arrivals-v1.json"
    scene_relative = scene_path.removeprefix("/")
    if scene_relative in raw_files:
        raise ValueError("arrivals public presentation collides with a native input")
    raw_files[scene_relative] = canonical_json_bytes(
        project_public_scenario(run).model_dump(mode="json")
    )
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for relative, raw in sorted(raw_files.items()):
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
    definition = LogisticsArrivalsSceneDefinition.model_validate(
        {
            "schema_version": "aero-bench.native-logistics-arrivals-registration/v1",
            "registration_id": registration_id,
            "profile_id": PACKAGE_ID,
            "scene_path": scene_path,
            "suite": run.suite.model_dump(mode="json"),
            "world": world_ref.model_dump(mode="json"),
            "world_id": world.world_id,
            "world_digest": world.world_digest,
            "scene_document": {
                "file": reference(output, scene_relative).model_dump(mode="json"),
                "size_bytes": len(raw_files[scene_relative]),
            },
            "files": [
                {
                    "file": reference(output, relative).model_dump(mode="json"),
                    "size_bytes": len(raw),
                }
                for relative, raw in sorted(raw_files.items())
            ],
            "reference_draft": {
                "purpose": "scenario-authoring",
                "schema_version": WORKSPACE_SCHEMA,
                "name": "Non-physical scheduled order arrivals",
                "seed": run.seed,
                "scenePath": scene_path,
                "environment": {
                    "cloudCover": 0,
                    "precipitation": "none",
                    "precipitationRateMmPerH": 0,
                    "visibilityM": 10000,
                    "windMps": 0,
                    "windDirectionDeg": 0,
                    "timeOfDay": "day",
                    "reflectionsEnabled": True,
                },
                "fleet": [],
                "facilities": [],
                "airspace": [],
                "traffic": {"vehicles": 0, "pedestrians": 0, "bicycles": 0},
                "algorithms": {
                    "mode": "centralized",
                    "assignment": "external",
                    "routing": "external",
                    "energy": "external",
                    "parameters": {"profile": PACKAGE_ID},
                },
                "deployment": {
                    "executor": "docker_reference",
                    "imageRef": run.agents[0].workload.runtime.image,
                },
                "events": [
                    {
                        "id": item.event_id,
                        "type": "order.created",
                        "atS": item.at_tick * run.environment.clock.step_ns / 1e9,
                        "targetId": item.order.order_id,
                        "payload": {
                            "actorId": item.actor_id,
                            "order": item.order.model_dump(mode="json"),
                        },
                    }
                    for item in config.scheduled_orders
                ],
                "actionRules": [],
                "stateKeyframes": [],
                "labelRules": [],
                "orders": [],
                "orderGeneration": CityOrderGeneration.disabled(run.seed).model_dump(
                    mode="json"
                ),
                "performanceProfiles": [],
                "authoredLandscape": [],
            },
        }
    )
    verified = verify_logistics_arrivals_scene(output, definition)
    (output / "native-scene.json").write_bytes(
        canonical_json_bytes(definition.model_dump(mode="json"))
    )
    manifest = NativeSceneRegistryManifest(
        schema_version="aero-bench.native-scene-registry/v1",
        registrations=(reference(output, "native-scene.json"),),
    )
    path = output / "native-scenes.json"
    path.write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))
    (output / "registration-public.json").write_bytes(
        canonical_json_bytes(verified.public().model_dump(mode="json"))
    )
    return path
