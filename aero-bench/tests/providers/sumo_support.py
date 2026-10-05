"""Shared SUMO service WorkloadIdentity fixture for mechanical service tests."""

from __future__ import annotations

from aero_bench.world import frame_math
from aero_bench.world.workload_scenario import ValidatedWorkloadScenario
from containers.sumo.service import WorkloadIdentity


def sumo_workload_identity(
    *,
    run_id: str,
    runtime_image: str,
    config_digest: str,
) -> WorkloadIdentity:
    """A complete identity with an empty scenario; no SUMO process is started."""

    scenario_config = {"path": "scenario.sumocfg", "sha256": "c" * 64}
    return WorkloadIdentity(
        run_id=run_id,
        provider_id="traffic",
        provider_port=17434,
        runtime_image=runtime_image,
        config_digest=config_digest,
        scenario_digest="e" * 64,
        scenario=ValidatedWorkloadScenario(
            scenario_digest="e" * 64,
            scenario={"entities": []},
            assets=(),
        ),
        scenario_config=scenario_config,
        scenario_files=(scenario_config,),
        geoid_grid_file={"path": "geoid.json", "sha256": "c" * 64},
        terrain_grid_file={"path": "terrain.json", "sha256": "d" * 64},
        clock_step_ns=100,
        clock_max_steps=10,
        object_bindings=(),
        frame_transform=frame_math.RigidTransform(
            rotation=frame_math.RotationMatrix.identity(),
            translation=frame_math.Vector3(0.0, 0.0, 0.0),
        ),
        enu_transform=frame_math.EnuTransform(0.0, 0.0, 0.0),
        geoid_interpolation="bilinear",
        terrain_interpolation="bilinear",
        geoid_precision_m=0.1,
        terrain_precision_m=0.1,
        artifact_requirement={
            "artifact_id": "artifact.sumo.traffic",
            "artifact_type": "sumo.traffic.evidence",
            "producer_id": "traffic",
            "visibility": "private",
            "relative_path": "traffic/evidence.jsonl",
            "max_size_bytes": 4096,
            "source_asset_id": None,
        },
    )
