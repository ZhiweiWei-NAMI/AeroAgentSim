from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.world import frame_math
from aero_bench.world.workload_scenario import ValidatedWorkloadScenario
from containers.selfcheck_scenario import _origin_pose
from containers.sumo.service import (
    FINALIZATION_BINDING_SCHEMA,
    PROTOCOL_VERSION,
    EvidenceWriter,
    PreparedConfig,
    SumoService,
    SumoServiceError,
    WorkloadIdentity,
    _artifact_requirement,
)


def _requirement(**updates: object) -> dict[str, object]:
    requirement: dict[str, object] = {
        "artifact_id": "artifact.sumo.traffic",
        "artifact_type": "sumo.traffic.evidence",
        "producer_id": "traffic",
        "visibility": "private",
        "relative_path": "nested/traffic/evidence.jsonl",
        "max_size_bytes": 1024,
        "source_asset_id": None,
    }
    requirement.update(updates)
    return requirement


def _identity_frame_fields(step_ns: int) -> dict[str, object]:
    return {
        "geoid_grid_file": {"path": "geoid.json", "sha256": "c" * 64},
        "terrain_grid_file": {"path": "terrain.json", "sha256": "d" * 64},
        "clock_step_ns": step_ns,
        "clock_max_steps": 10,
        "enu_transform": frame_math.EnuTransform(0.0, 0.0, 0.0),
        "geoid_interpolation": "bilinear",
        "terrain_interpolation": "bilinear",
        "geoid_precision_m": 0.1,
        "terrain_precision_m": 0.1,
    }


def _prepared_frame_fields(identity: WorkloadIdentity) -> dict[str, object]:
    grid = frame_math.ScalarGridSampler(
        east_axis_m=(-100_000.0, 100_000.0),
        north_axis_m=(-100_000.0, 100_000.0),
        values_m=((0.0, 0.0), (0.0, 0.0)),
    )
    return {
        "scenario_digest": identity.scenario_digest,
        "clock_max_steps": identity.clock_max_steps,
        "enu_transform": identity.enu_transform,
        "geoid_grid": grid,
        "terrain_grid": grid,
        "geoid_interpolation": identity.geoid_interpolation,
        "terrain_interpolation": identity.terrain_interpolation,
        "geoid_precision_m": identity.geoid_precision_m,
        "terrain_precision_m": identity.terrain_precision_m,
    }


def test_sumo_public_traffic_light_schema_matches_runtime_contract() -> None:
    snapshot_digest = "d" * 64
    evidence_digest = "e" * 64
    traffic_lights = [
        {
            "signal_id": "junction.1",
            "state": "Gr",
            "phase_index": 0,
            "next_switch_s": 20.0,
            "program_id": "0",
            "telemetry_source": "sumo-traci",
        }
    ]
    snapshot = {
        "simulation_time_ns": 100,
        "entities": [],
        "traffic_lights": traffic_lights,
    }
    config = SimpleNamespace(
        run_id="a" * 64,
        provider_id="traffic",
        artifact_requirement={"relative_path": "traffic/evidence.jsonl"},
    )
    service = object.__new__(SumoService)
    service._restriction_applications = []
    service._require_runtime = lambda: (config, object())
    service._snapshot = lambda _config, _connection: snapshot
    service._record = lambda **_kwargs: (snapshot_digest, evidence_digest)

    receipt, emitted_snapshot = service._receipt(
        target=(1, 100), operation="step_stage"
    )

    assert emitted_snapshot is snapshot
    events = receipt["events"]
    assert isinstance(events, list)
    public_event = next(
        event
        for event in events
        if event["event_id"] == "public.traffic-light"
    )
    assert public_event == {
        "provider_id": "traffic",
        "event_id": "public.traffic-light",
        "time": {"tick": 1, "sim_time_ns": 100},
        "payload_schema_id": "sumo.traffic_light.v1",
        "payload": [
            {"name": "snapshot_digest", "value": snapshot_digest},
            {"name": "simulation_time_ns", "value": 100},
            {
                "name": "traffic_lights_json",
                "value": (
                    '[{"next_switch_s":20.0,"phase_index":0,"program_id":"0",'
                    '"signal_id":"junction.1","state":"Gr",'
                    '"telemetry_source":"sumo-traci"}]'
                ),
            },
        ],
    }


def test_sumo_artifact_requirement_is_full_and_nested(tmp_path) -> None:
    requirement = _artifact_requirement(
        _requirement(), label="SUMO evidence ArtifactRequirement"
    )
    writer = EvidenceWriter(tmp_path, requirement)
    writer.reset()
    assert writer.path == tmp_path / "nested/traffic/evidence.jsonl"
    assert writer.path.read_bytes() == b""


def test_sumo_artifact_writer_enforces_accumulated_size_before_write(tmp_path) -> None:
    requirement = _artifact_requirement(
        _requirement(max_size_bytes=1), label="SUMO evidence ArtifactRequirement"
    )
    writer = EvidenceWriter(tmp_path, requirement)
    writer.reset()
    with pytest.raises(SumoServiceError, match="max_size_bytes"):
        writer.append({"real": True})
    assert writer.path.read_bytes() == b""


@pytest.mark.parametrize(
    "relative_path",
    ("/evidence.jsonl", ".", "a/../evidence.jsonl", "a\\evidence.jsonl"),
)
def test_sumo_artifact_requirement_rejects_unsafe_path(relative_path: str) -> None:
    with pytest.raises(SumoServiceError, match="normalized and relative"):
        _artifact_requirement(
            _requirement(relative_path=relative_path),
            label="SUMO evidence ArtifactRequirement",
        )


def test_sumo_artifact_requirement_rejects_old_field_set() -> None:
    old_requirement = _requirement()
    old_requirement.pop("artifact_id")
    old_requirement.pop("relative_path")
    old_requirement.pop("max_size_bytes")
    with pytest.raises(SumoServiceError, match="fields are not exact"):
        _artifact_requirement(
            old_requirement, label="SUMO evidence ArtifactRequirement"
        )


def test_sumo_service_finalizes_exact_artifact_and_freezes_state(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        run_id = "a" * 64
        session_token = "c" * 64
        requirement = _artifact_requirement(
            _requirement(), label="SUMO evidence ArtifactRequirement"
        )
        scenario_root = tmp_path / "scenario"
        artifact_root = tmp_path / "artifacts"
        scenario_root.mkdir()
        artifact_root.mkdir()
        scenario_config = {"path": "scenario.sumocfg", "sha256": "b" * 64}
        frame_transform = frame_math.RigidTransform(
            rotation=frame_math.RotationMatrix.identity(),
            translation=frame_math.Vector3(0.0, 0.0, 0.0),
        )
        identity = WorkloadIdentity(
            run_id=run_id,
            provider_id="traffic",
            provider_port=17434,
            runtime_image="registry.test/sumo@sha256:" + "1" * 64,
            config_digest="f" * 64,
            scenario_digest="e" * 64,
            scenario=ValidatedWorkloadScenario(
                scenario_digest="e" * 64,
                scenario={"entities": []},
                assets=(),
            ),
            scenario_config=scenario_config,
            scenario_files=(scenario_config,),
            object_bindings=(),
            frame_transform=frame_transform,
            artifact_requirement=requirement,
            **_identity_frame_fields(100),
        )
        service = SumoService(
            scenario_root=scenario_root,
            artifact_root=artifact_root,
            rpc_port=17434,
            workload_identity=identity,
            session_token=session_token,
        )
        service._config = PreparedConfig(
            provider_id=identity.provider_id,
            run_id=run_id,
            protocol_version=PROTOCOL_VERSION,
            runtime_image=identity.runtime_image,
            config_digest=identity.config_digest,
            artifact_requirement=requirement,
            sumo_binary="sumo",
            traci_port=17534,
            step_length_ns=100,
            scenario_config=scenario_config,
            scenario_files=(scenario_config,),
            object_bindings=identity.object_bindings,
            frame_transform=identity.frame_transform,
            sumo_args=(),
            required_commands=("sumo",),
            command_timeout_ms=1000,
            **_prepared_frame_fields(identity),
        )
        service._current = (1, 100)
        service._evidence.reset()
        service._evidence.append({"schema_version": "test.record/v1"})
        identity_fields = {
            "provider_id": "traffic",
            "run_id": run_id,
            "session_token": session_token,
        }
        request = {
            **identity_fields,
            "request": {
                "schema_version": "aero-bench.provider-finalization-request/v1",
                "run_id": run_id,
                "terminal_event": "run.completed",
                "terminal_time": {"tick": 1, "sim_time_ns": 100},
                "event_chain_root": "d" * 64,
            },
        }
        first = await service.handle("finalize", request)
        assert await service.handle("finalize", request) == first
        content = service._evidence.path.read_bytes()
        records = [json.loads(line) for line in content.splitlines()]
        assert records[-1] == {
            "schema_version": FINALIZATION_BINDING_SCHEMA,
            "run_id": run_id,
            "provider_id": "traffic",
            "terminal_event": "run.completed",
            "terminal_time": {"tick": 1, "sim_time_ns": 100},
            "event_chain_root": "d" * 64,
        }
        assert first["receipt"]["artifacts"] == [
            {
                "artifact_id": requirement["artifact_id"],
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        ]
        changed_root = {
            **request,
            "request": {**request["request"], "event_chain_root": "e" * 64},
        }
        with pytest.raises(SumoServiceError, match="root cannot be changed"):
            await service.handle("finalize", changed_root)
        for operation, payload in (
            ("reset", {**identity_fields, "seed": 7}),
            (
                "step_stage",
                {
                    **identity_fields,
                    "request": {
                        "run_id": run_id,
                        "target": {"tick": 2, "sim_time_ns": 200},
                    },
                },
            ),
            ("snapshot", identity_fields),
        ):
            with pytest.raises(SumoServiceError, match="already finalized"):
                await service.handle(operation, payload)
        assert await service.handle("shutdown", identity_fields) == {
            "status": "stopped"
        }
        assert service._evidence.path.read_bytes() == content

    asyncio.run(run())


def test_sumo_snapshot_projects_only_active_authorized_bindings(
    tmp_path: Path,
) -> None:
    """Pending bindings are valid; every active SUMO ID must remain exact."""

    class SimulationDomain:
        def __init__(self, connection: "MechanicalConnection") -> None:
            self._connection = connection

        def getTime(self) -> float:
            return self._connection.time_s

    class VehicleDomain:
        def __init__(self, connection: "MechanicalConnection") -> None:
            self._connection = connection

        def getIDList(self) -> list[str]:
            return list(self._connection.vehicle_ids)

        @staticmethod
        def getPosition(_object_id: str) -> tuple[float, float]:
            return (2.0, 3.0)

        @staticmethod
        def getSpeed(_object_id: str) -> float:
            return 4.0

        @staticmethod
        def getAngle(_object_id: str) -> float:
            return 0.0

        @staticmethod
        def getRoadID(_object_id: str) -> str:
            return "edge.fixture"

        @staticmethod
        def getLaneID(_object_id: str) -> str:
            return "lane.fixture"

    class PersonDomain:
        @staticmethod
        def getIDList() -> list[str]:
            return []

    class MechanicalConnection:
        def __init__(self) -> None:
            self.time_s = 0.0
            self.vehicle_ids: tuple[str, ...] = ()
            self.simulation = SimulationDomain(self)
            self.vehicle = VehicleDomain(self)
            self.person = PersonDomain()

    async def run() -> None:
        run_id = "a" * 64
        session_token = "c" * 64
        requirement = _artifact_requirement(
            _requirement(max_size_bytes=65_536),
            label="SUMO evidence ArtifactRequirement",
        )
        scenario_root = tmp_path / "snapshot-scenario"
        artifact_root = tmp_path / "snapshot-artifacts"
        scenario_root.mkdir()
        artifact_root.mkdir()
        transform = frame_math.RigidTransform(
            rotation=frame_math.RotationMatrix(
                rows=((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
            ),
            translation=frame_math.Vector3(10.0, -20.0, 3.0),
        )
        scenario_config = {"path": "scenario.sumocfg", "sha256": "b" * 64}
        object_bindings = (
            {
                "sumo_object_id": "reference.0",
                "entity_id": "ugv.fixture",
                "kind": "vehicle",
            },
        )
        identity = WorkloadIdentity(
            run_id=run_id,
            provider_id="traffic",
            provider_port=17434,
            runtime_image="registry.test/sumo@sha256:" + "1" * 64,
            config_digest="f" * 64,
            scenario_digest="e" * 64,
            scenario=ValidatedWorkloadScenario(
                scenario_digest="e" * 64,
                scenario={"entities": [{"entity_id": "ugv.fixture", "initial_pose": _origin_pose()}]},
                assets=(),
            ),
            scenario_config=scenario_config,
            scenario_files=(scenario_config,),
            object_bindings=object_bindings,
            frame_transform=transform,
            artifact_requirement=requirement,
            **_identity_frame_fields(100_000_000),
        )
        service = SumoService(
            scenario_root=scenario_root,
            artifact_root=artifact_root,
            rpc_port=17434,
            workload_identity=identity,
            session_token=session_token,
        )
        service._config = PreparedConfig(
            provider_id=identity.provider_id,
            run_id=run_id,
            protocol_version=PROTOCOL_VERSION,
            runtime_image=identity.runtime_image,
            config_digest=identity.config_digest,
            artifact_requirement=requirement,
            sumo_binary="sumo",
            traci_port=17534,
            step_length_ns=100_000_000,
            scenario_config=scenario_config,
            scenario_files=(scenario_config,),
            object_bindings=object_bindings,
            frame_transform=transform,
            sumo_args=(),
            required_commands=("sumo",),
            command_timeout_ms=1000,
            **_prepared_frame_fields(identity),
        )
        connection = MechanicalConnection()
        service._connection = connection
        for stream in ("stdout", "stderr"):
            log = tmp_path / f"mechanical-{stream}.log"
            log.write_bytes(b"")
            setattr(service, f"_process_{stream}_path", log)
        service._current = (0, 0)
        service._evidence.reset()
        identity_fields = {
            "provider_id": "traffic",
            "run_id": run_id,
            "session_token": session_token,
        }

        await service.handle("snapshot", identity_fields)
        connection.time_s = 0.1
        connection.vehicle_ids = ("reference.0",)
        service._current = (1, 100_000_000)
        await service.handle("snapshot", identity_fields)

        records = [
            json.loads(line) for line in service._evidence.path.read_text().splitlines()
        ]
        pending = records[0]["snapshot"]["entities"]
        assert len(pending) == 1
        assert pending[0]["entity_id"] == "ugv.fixture"
        assert pending[0]["lifecycle"] == "pending"
        assert pending[0]["pose"] == _origin_pose()
        active = records[1]["snapshot"]["entities"]
        assert len(active) == 1
        assert active[0]["lifecycle"] == "active"
        assert {key: active[0][key] for key in (
            "entity_id", "sumo_object_id", "kind", "position_enu_m",
            "speed_mps", "yaw_enu_rad", "road_id", "lane_id",
        )} == {
                "entity_id": "ugv.fixture",
                "sumo_object_id": "reference.0",
                "kind": "vehicle",
                "position_enu_m": {
                    "east_m": 7.0,
                    "north_m": -18.0,
                    "up_m": 3.0,
                },
                "speed_mps": 4.0,
                "yaw_enu_rad": math.pi,
                "road_id": "edge.fixture",
                "lane_id": "lane.fixture",
            }
        assert active[0]["pose"]["position"]["enu"] == active[0]["position_enu_m"]
        assert active[0]["linear_velocity_enu"]["east_mps"] == -4.0

        connection.vehicle_ids = ("forged.0",)
        with pytest.raises(SumoServiceError, match="not authorized"):
            await service.handle("snapshot", identity_fields)

    asyncio.run(run())
