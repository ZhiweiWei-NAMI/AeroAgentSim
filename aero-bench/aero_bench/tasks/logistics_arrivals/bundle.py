"""Author a small Business-only arrivals bundle from explicit pinned inputs."""

import hashlib
import json
from pathlib import Path

import yaml

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentSpec,
    EnvironmentSpec,
    FileRef,
    TaskSpec,
    SuiteSpec,
)
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.logistics_business.scheduled_arrivals import (
    SCHEDULED_ARRIVAL_CAPABILITY,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facilities import compile_authored_facilities
from aero_bench.tasks.logistics.fleet import compile_authored_fleet
from aero_bench.tasks.logistics_arrivals.contracts import (
    LogisticsArrivalsPackage,
    LogisticsArrivalsVerifierConfig,
    PACKAGE_ID,
    METRIC_ID,
)
from aero_bench.world.contracts import WorldPackage, WorldPackageContent, world_package

CAPABILITIES = tuple(
    sorted(
        (
            "logistics.facilities.state",
            "logistics.orders.authority",
            SCHEDULED_ARRIVAL_CAPABILITY,
        )
    )
)
PROVIDER_ID = "logistics.business"
VERIFIER_ID = "logistics.arrivals.verifier"
AGENT_ID = "participant.agent"
MAX_STEPS = 8
STEP_NS = 1_000_000_000


def file_ref(root, path):
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def write_json(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value))
    return file_ref(root, path)


def write_spec(root, relative, model):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(yaml.safe_dump(model.model_dump(mode="json"), sort_keys=True))
    return file_ref(root, path)


def authored_facilities():
    """Declared ledger destinations, not measured pads or physical delivery evidence."""
    return [
        {
            "id": identity,
            "name": identity,
            "kind": "hub" if identity == "facility.origin" else "vertiport",
            "placement": "ground",
            "buildingId": None,
            "supportHeightM": None,
            "position": {"x": x, "z": -20},
            "rotationDeg": 0,
            "widthM": 12,
            "depthM": 8,
            "heightM": 4,
            "landing": {"parkingSlots": 3, "movementsPerHour": 30},
            "cargo": {"storageCapacityKg": 100, "throughputPerHourKg": 50}
            if identity == "facility.origin"
            else None,
            "charging": None,
        }
        for identity, x in (("facility.origin", 10), ("facility.destination", 30))
    ]


def scheduled_orders():
    return [
        {
            "event_id": f"editor.order.{ordinal}",
            "at_tick": tick,
            "actor_id": PROVIDER_ID,
            "order": {
                "order_id": f"scheduled.order.{ordinal}",
                "origin_facility_id": "facility.origin",
                "destination_facility_id": "facility.destination",
                "hub_handoff_facility_id": "facility.origin",
                "cargo_mass_kg": float(ordinal),
                "release_time_s": release,
                "deadline_s": 60.0,
            },
        }
        for ordinal, tick, release in ((1, 2, 3.0), (2, 5, 7.0))
    ]


def build_arrivals_bundle(
    *, source_bundle: Path, image_lock: Path, output: Path, seed=1701
):
    """Pin native inputs and actual production images; do not execute or mint evidence."""
    lock = json.loads(image_lock.read_bytes())
    if (
        lock["schema_version"] != "aero-bench.logistics-arrivals-image-lock/v1"
        or lock["participant_profile"] != "logistics_arrivals"
    ):
        raise ValueError("arrivals build requires its distinct image lock")
    revision = lock["runtime_source_revision"]
    images = lock["images"]
    if set(images) != {"agent", "business", "harness", "verifier"}:
        raise ValueError("arrivals image lock must contain exactly four workloads")
    output = output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source_bundle = source_bundle.resolve(strict=True)
    source = WorldPackage.model_validate(
        json.loads((source_bundle / "world/package.json").read_bytes())
    )
    reader = BundleReader(source_bundle)
    raw = source.model_dump(mode="json")
    for key in ("asset_digest", "world_digest"):
        del raw[key]
    raw["world_id"] = "world.logistics-arrivals.v1"
    raw["provider_requirements"] = [
        {
            "provider_id": PROVIDER_ID,
            "roles": ["mission"],
            "required_capability_ids": list(CAPABILITIES),
        }
    ]
    for field in (
        "base_layers",
        "layers",
        "buildings",
        "roads",
        "regions",
        "launch_sites",
        "sensors",
        "semantic_targets",
        "engine_frame_bindings",
        "mission_requirements",
        "expected_public_assets",
    ):
        raw[field] = []
    raw["sumo"], raw["network"] = None, None
    raw["entities"] = [
        entity.model_dump(mode="json")
        for entity in source.entities
        if entity.entity_id == "station.operations"
    ]
    if len(raw["entities"]) != 1 or raw["entities"][0]["state"] != "static":
        raise ValueError("source bundle must contain the explicit static station")
    required_assets = {
        source.frame.vertical_datum.geoid_correction_asset_id,
        source.frame.vertical_datum.terrain_height_asset_id,
        raw["entities"][0]["model_asset_id"],
    }
    raw["assets"] = []
    copied = {}
    for asset in source.assets:
        if asset.artifact.artifact_id not in required_assets:
            continue
        document = asset.model_dump(mode="json")
        if asset.visibility != "public":
            raise ValueError("arrivals static scene inputs must be public")
        document["audiences"] = [{"audience_kind": "public", "audience_id": "all"}]
        raw["assets"].append(document)
        for selector in (asset.artifact, asset.license_file):
            path = selector.selector.split("#", 1)[0]
            copied[path] = reader.resolve_file(
                FileRef(path=path, sha256=selector.sha256)
            )
    for relative, path in copied.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(path.read_bytes())
    content = WorldPackageContent.model_validate(raw)
    world = world_package(
        **{
            name: getattr(content, name)
            for name in type(content).model_fields
            if name != "schema_version"
        }
    )
    world_ref = write_json(output, "world/package.json", world.model_dump(mode="json"))
    catalogue = compile_authored_facilities(authored_facilities())
    fleet = compile_authored_fleet(
        [
            {
                "id": "payload.capacity",
                "assetId": "model:logistics-drone-v1",
                "count": 1,
                "homeFacilityId": "facility.origin",
                "batteryWh": 20000,
                "reserveRatio": 0.2,
                "maxPayloadKg": 5,
            }
        ],
        catalogue,
    )
    origin = world.frame.origin
    business = LogisticsBusinessConfig.model_validate(
        {
            "schema_version": "aero-bench.logistics-business/v3",
            "provider_id": PROVIDER_ID,
            "principal_bindings": [],
            "observation": None,
            "scheduled_orders": scheduled_orders(),
            "task_package": {
                "schema_version": "aero-bench.logistics-arrivals-domain/v1",
                "task_id": PACKAGE_ID,
                "verifier_id": VERIFIER_ID,
                "scene": {
                    "scene_id": world.world_id,
                    "coordinate_frame": "scene_east_south_m",
                    "origin_latitude_deg": origin.latitude_deg,
                    "origin_longitude_deg": origin.longitude_deg,
                    "origin_altitude_m": origin.altitude_m,
                    "scene_source_sha256": world.asset_digest,
                },
                "facilities": catalogue.model_dump(mode="json"),
                "fleet": fleet.model_dump(mode="json"),
                "actor_grants": [{"actor_id": PROVIDER_ID, "role": "business"}],
                "orders": [],
            },
        }
    )

    def bound(relative, model):
        return {
            "file": write_json(
                output, f"configs/{relative}.json", model.model_dump(mode="json")
            ),
            "schema_file": write_json(
                output, f"schemas/{relative}.json", type(model).model_json_schema()
            ),
        }

    config = bound("business", business)
    package = bound(
        "package",
        LogisticsArrivalsPackage(
            schema_version="aero-bench.logistics-arrivals-task/v1",
            package_id=PACKAGE_ID,
            task_id=PACKAGE_ID,
            verifier_id=VERIFIER_ID,
            provider_id=PROVIDER_ID,
            business_config=config,
        ),
    )
    verifier_config = bound(
        "verifier",
        LogisticsArrivalsVerifierConfig(
            schema_version="aero-bench.logistics-arrivals-verifier/v1",
            package_id=PACKAGE_ID,
        ),
    )
    protocol = write_json(
        output,
        "schemas/protocol.json",
        json.loads((source_bundle / "schemas/provider-protocol.json").read_bytes()),
    )

    def workload(image, component, version, command, memory):
        return {
            "runtime": {"image": images[image], "command": command},
            "resources": {"cpu_millicores": 1000, "memory_mib": memory, "gpu_count": 0},
            "implementation": {
                "component_id": component,
                "kind": "production",
                "source_uri": "https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
                "source_revision": revision,
                "version": version,
            },
        }

    def artifact(identity, kind, producer, path, visibility="private"):
        return {
            "artifact_id": identity,
            "artifact_type": kind,
            "producer_id": producer,
            "visibility": visibility,
            "relative_path": path,
            "max_size_bytes": 16 * 1024 * 1024,
            "source_asset_id": None,
        }

    harness_artifacts = [
        artifact(
            "artifact.event-log", "event.log", "harness", "harness/event-log.jsonl"
        ),
        artifact(
            "artifact.scene-states",
            "scene.state-history",
            "harness",
            "harness/scene-states.jsonl",
            "public",
        ),
    ]
    business_artifacts = [
        artifact(
            "artifact.logistics",
            "logistics.business.state",
            PROVIDER_ID,
            "logistics/state.json",
        )
    ]
    environment = EnvironmentSpec.model_validate(
        {
            "schema_version": "aero-bench.environment/v1",
            "environment_id": PACKAGE_ID,
            "harness": workload(
                "harness", "aero-bench.harness", "0.4.0-harness.1", ["serve"], 1024
            ),
            "clock": {
                "authority": "provider_barrier",
                "step_ns": STEP_NS,
                "max_steps": MAX_STEPS,
                "provider_timeout_ms": 30000,
            },
            "gateway": {"protocol_schema": protocol, "port": 17432},
            "harness_artifact_requirements": harness_artifacts,
            "providers": [
                {
                    "provider_id": PROVIDER_ID,
                    "adapter": PROVIDER_ID,
                    "port": 18435,
                    "workload": workload(
                        "business",
                        PROVIDER_ID,
                        "0.4.0-logistics-business.2",
                        ["provider", "serve"],
                        512,
                    ),
                    "config": config,
                    "protocol_schema": protocol,
                    "capabilities": list(CAPABILITIES),
                    "artifact_requirements": business_artifacts,
                }
            ],
        }
    )
    instruction = output / "instruction.md"
    instruction.write_text(
        "Advance the native Business barriers to the declared horizon. No Logistics commands or physical operations are authorized.\n"
    )
    task = TaskSpec.model_validate(
        {
            "schema_version": "aero-bench.task/v1",
            "task_id": PACKAGE_ID,
            "instruction": file_ref(output, instruction),
            "package": {"package_id": PACKAGE_ID, "config": package},
            "required_capabilities": list(CAPABILITIES),
            "required_tools": [],
            "assets": [],
            "goals": [
                {
                    "goal_id": "goal.scheduled-arrivals",
                    "verifier_id": VERIFIER_ID,
                    "metric_id": METRIC_ID,
                    "operator": "eq",
                    "threshold": 1.0,
                    "evidence": "authoritative_state",
                    "parameters": [],
                }
            ],
            "verifier": {
                "verifier_id": VERIFIER_ID,
                "workload": workload(
                    "verifier",
                    VERIFIER_ID,
                    "0.4.0-logistics-arrivals-verifier.1",
                    ["verify"],
                    1024,
                ),
                "config": verifier_config,
                "artifact_requirements": [*harness_artifacts, *business_artifacts],
                "output_artifacts": [
                    artifact(
                        "artifact.verification",
                        "verification.report",
                        VERIFIER_ID,
                        "verifier/report.json",
                        "public",
                    ),
                    artifact(
                        "artifact.verification-events",
                        "verification.event-segment",
                        VERIFIER_ID,
                        "verifier/events.jsonl",
                        "public",
                    ),
                ],
            },
        }
    )
    agent = AgentSpec.model_validate(
        {
            "schema_version": "aero-bench.agent/v2",
            "agent_id": AGENT_ID,
            "workload": workload(
                "agent", AGENT_ID, "0.4.0-logistics-clock.1", ["run"], 512
            ),
            "tools": [],
            "queries": [],
            "observations": [],
            "artifact_requirements": [],
            "driver": None,
        }
    )
    suite = SuiteSpec.model_validate(
        {
            "schema_version": "aero-bench.suite/v2",
            "suite_id": PACKAGE_ID,
            "aggregator_id": "aggregator.single-run",
            "execution_scope": "formal_benchmark",
            "cases": [
                {
                    "case_id": "arrivals",
                    "task": write_spec(output, "task/task.yaml", task),
                    "environment": write_spec(
                        output, "environment/environment.yaml", environment
                    ),
                    "agents": [write_spec(output, "agent/agent.yaml", agent)],
                    "world_package": world_ref,
                    "launch_site_ids": [None],
                    "seeds": [seed],
                    "axes": [],
                }
            ],
        }
    )
    write_spec(output, "suite.yaml", suite)
    write_json(output, "image-lock.json", lock)
    return output
