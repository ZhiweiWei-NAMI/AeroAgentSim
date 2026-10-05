"""In-container mechanical selfcheck for the Inspection Business provider.

The selfcheck runs the real service behind the real JsonLineRpcServer on a
loopback socket and drives the exact RPC surface a formal run uses: probe,
prepare, reset, typed step_stage, commands, query, snapshot, and shutdown. It also
proves the principal counterexamples: a peer that races the first prepare
with a wrong executor-issued token is denied and binds no state, and a
second connection that replays every public identity field cannot forge the
attested principal without the executor-issued session token. It exits zero
only when every identity, principal, artifact, and history check holds.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import tempfile
from pathlib import Path

from aero_bench.config.models import FileRef
from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageRequest,
    BusinessEnvironmentStageResult,
    EnuLinearVelocity,
    NedLinearVelocity,
    SceneContribution,
    SceneState,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    StageReceipt,
    StateSample,
    StepReceipt,
    SCENE_STATE_ROOT_DIGEST,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    scene_state_digest_value,
    stage_barrier_digest_value,
    stage_receipt_digest_value,
    state_sample_digest_value,
    step_receipt_digest_value,
)
from aero_bench.world.resolved import (
    ResolvedCoordinate,
    ResolvedEcefPosition,
    ResolvedEnuPosition,
    ResolvedNedPosition,
    ResolvedPose,
    ResolvedQuaternion,
    ResolvedWgs84Position,
    ResolvedScenario,
)
from aero_bench.executor.contracts import ProviderWorkloadContract
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import (
    InspectionActorGrant,
    InspectionArtifactRequirement,
    InspectionAssetRef,
    InspectionBoundSource,
    InspectionBoundSpec,
    InspectionGoal,
    InspectionTaskPackage,
    ImagingBoundSpec,
    LinkBoundSpec,
    MissionBoundSpec,
    ObservationContract,
    ObservationTrigger,
    WorkOrderSpec,
)
from containers.selfcheck_scenario import (
    ScenarioProvider,
    build_resolved_scenario,
)

from service import (
    PROVIDER_ADAPTER,
    PROVIDER_PROBE_SCHEMA,
    PROTOCOL_VERSION,
    InspectionBusinessProviderService,
    WorkloadIdentity,
)


SEED = 1701
RUN_ID = "a" * 64
RUNTIME_IMAGE = "registry.invalid/aero-bench/inspection-business@sha256:" + "1" * 64
FILE_DIGEST = "c" * 64
PAYLOAD_DIGEST = hashlib.sha256(b"report").hexdigest()
SESSION_TOKEN = "9" * 64
PORT = 15435


class SelfcheckError(RuntimeError):
    pass


def _require(condition: object, detail: str) -> None:
    if not condition:
        raise SelfcheckError(detail)


def _file_ref(path: str) -> dict[str, str]:
    return {"path": path, "sha256": FILE_DIGEST}


def _task_package() -> InspectionTaskPackage:
    schema = FileRef(path="schemas/observation.json", sha256=FILE_DIGEST)
    bound_fields = (
        "mission.shortest_path_m",
        "mission.max_speed_mps",
        "mission.fixed_time_s",
        "mission.inspection_dwell_s",
        "mission.deadline_s",
        "link.minimum_payload_bytes",
        "link.max_bandwidth_bps",
        "link.minimum_latency_s",
        "link.upload_deadline_s",
        "imaging.defect_size_m",
        "imaging.standoff_distance_m",
        "imaging.focal_length_m",
        "imaging.pixel_pitch_m",
        "imaging.minimum_resolvable_pixels",
        "total_defects",
        "geometrically_visible_defects",
    )
    return InspectionTaskPackage(
        schema_version="aero-bench.inspection-task/v4",
        task_id="inspection.task",
        verifier_id="inspection.verifier",
        network_delivery_required=True,
        actors=(
            InspectionActorGrant(actor_id="agent.1", role="agent"),
            InspectionActorGrant(
                actor_id="observation.provider", role="observation_provider"
            ),
            InspectionActorGrant(actor_id="business", role="business"),
            InspectionActorGrant(actor_id="inspection.verifier", role="verifier"),
        ),
        assets=(
            InspectionAssetRef(
                asset_id="world.images",
                file=FileRef(path="private/inspection-target.sdf", sha256=FILE_DIGEST),
                visibility="world",
                media_type="application/vnd.gazebo.sdf+xml",
            ),
            InspectionAssetRef(
                asset_id="verifier.labels",
                file=FileRef(path="private/labels.json", sha256=FILE_DIGEST),
                visibility="verifier",
                media_type="application/json",
            ),
        ),
        work_orders=(
            WorkOrderSpec(
                work_order_id="wo.1",
                target_id="asset.1",
                required_observation_id="obs.1",
                arrival_tolerance_m=1.0,
                report_artifact_type="agent.report",
                upload_deadline_ns=20_000_000_000,
            ),
        ),
        observations=(
            ObservationContract(
                observation_id="obs.1",
                target_id="asset.1",
                simulation_asset_id="world.images",
                metadata_schema=schema,
                media_type="application/json",
                trigger=ObservationTrigger(
                    observation_id="obs.1",
                    target_id="asset.1",
                    provider_id="observation.provider",
                    camera_id="camera.1",
                    min_distance_m=2.0,
                    max_distance_m=20.0,
                    min_view_angle_deg=0.0,
                    max_view_angle_deg=45.0,
                    earliest_time_ns=0,
                    latest_time_ns=20_000_000_000,
                ),
            ),
        ),
        bounds=InspectionBoundSpec(
            mission=MissionBoundSpec(
                shortest_path_m=10.0,
                max_speed_mps=5.0,
                fixed_time_s=1.0,
                inspection_dwell_s=1.0,
                deadline_s=20.0,
            ),
            link=LinkBoundSpec(
                minimum_payload_bytes=1,
                max_bandwidth_bps=1_000.0,
                minimum_latency_s=0.1,
                upload_deadline_s=2.0,
            ),
            imaging=ImagingBoundSpec(
                defect_size_m=0.01,
                standoff_distance_m=10.0,
                focal_length_m=0.02,
                pixel_pitch_m=2e-6,
                minimum_resolvable_pixels=5.0,
            ),
            total_defects=1,
            geometrically_visible_defects=1,
        ),
        bound_sources=tuple(
            InspectionBoundSource(
                bound_field=field,
                provider_id="business",
                config_pointer=f"/inspection_bounds/{field}",
            )
            for field in bound_fields
        ),
        artifact_requirements=(
            InspectionArtifactRequirement(
                artifact_id="artifact.camera-frame-data",
                artifact_type="camera-frame-data",
                producer_id="observation.provider",
                visibility="public",
                relative_path="camera-frames.bin",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="camera_frame_data",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.event-log",
                artifact_type="event.log",
                producer_id="harness",
                visibility="private",
                relative_path="event.log",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="event_log",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.business",
                artifact_type="business.state",
                producer_id="business",
                visibility="private",
                relative_path="business/state.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="business_state",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.trajectory",
                artifact_type="trajectory",
                producer_id="observation.provider",
                visibility="private",
                relative_path="trajectory.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="trajectory",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.network",
                artifact_type="network.delivery",
                producer_id="observation.provider",
                visibility="private",
                relative_path="delivery.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="delivery",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.observation",
                artifact_type="observation.metadata",
                producer_id="observation.provider",
                visibility="private",
                relative_path="observation.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="observation",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.sensor-frames",
                artifact_type="sensor-frame",
                producer_id="observation.provider",
                visibility="public",
                relative_path="sensor-frames.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="sensor_frame",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.truth",
                artifact_type="truth",
                producer_id="bundle",
                visibility="private",
                relative_path="truth.json",
                max_size_bytes=1_048_576,
                source_asset_id="verifier.labels",
                evidence_kind="truth",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.detection",
                artifact_type="detection",
                producer_id="agent.1",
                visibility="public",
                relative_path="detection.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="detection",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.report",
                artifact_type="agent.report",
                producer_id="agent.1",
                visibility="public",
                relative_path="report.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="report",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.bounds",
                artifact_type="theoretical.bounds",
                producer_id="business",
                visibility="public",
                relative_path="bounds.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="theoretical_bounds",
            ),
            InspectionArtifactRequirement(
                artifact_id="artifact.scene-states",
                artifact_type="scene.state-history",
                producer_id="harness",
                visibility="public",
                relative_path="scene-states.jsonl",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="scene_state_history",
            ),
        ),
        goals=(
            InspectionGoal(
                goal_id="goal.complete",
                metric_id="work_order.completion_rate",
                operator="eq",
                threshold=1.0,
                evidence="authoritative_state",
                parameters=(),
            ),
        ),
    )


def _artifact_requirement() -> dict[str, object]:
    return {
        "artifact_id": "artifact.business",
        "artifact_type": "business.state",
        "producer_id": "business",
        "visibility": "private",
        "relative_path": "business/state.json",
        "max_size_bytes": 1_048_576,
        "source_asset_id": None,
    }


def _write_run_inputs(
    root: Path,
) -> tuple[dict[str, dict[str, str]], ProviderWorkloadContract]:
    scenario = ResolvedScenario.model_validate(
        build_resolved_scenario(
            seed=SEED,
            providers=(
                ScenarioProvider(
                    "business",
                    ("mission",),
                    ("business.work-order",),
                    "business_environment",
                ),
                ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
            ),
            dynamic_provider_id="flight",
        )
    )
    config_document = {
        "schema_version": "aero-bench.inspection-business/v1",
        "provider_id": "business",
        "task_package": _task_package().model_dump(mode="json"),
    }
    config_path = root / "provider.config.json"
    config_path.write_bytes(canonical_json_bytes(config_document) + b"\n")
    schema_path = root / "provider.schema.json"
    schema_path.write_bytes(
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n'
    )
    refs = {
        "config": {
            "path": "provider.config.json",
            "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        },
        "schema": {
            "path": "provider.schema.json",
            "sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
        },
    }
    contract = ProviderWorkloadContract.model_validate(
        {
            "schema_version": "aero-bench.workload-contract/v5",
            "role": "provider",
            "run_id": RUN_ID,
            "seed": SEED,
            "workload_id": "business",
            "clock": {
                "authority": "provider_barrier",
                "step_ns": 1_000_000_000,
                "max_steps": 16,
                "provider_timeout_ms": 10_000,
            },
            "provider": {
                "provider_id": "business",
                "adapter": "inspection.business",
                "port": PORT,
                "workload": {
                    "runtime": {
                        "image": RUNTIME_IMAGE,
                        "command": ["provider", "serve"],
                    },
                    "resources": {
                        "cpu_millicores": 1000,
                        "memory_mib": 1024,
                        "gpu_count": 0,
                    },
                    "implementation": {
                        "component_id": "inspection.business",
                        "kind": "mechanical_fixture",
                        "source_uri": "https://github.com/moby/moby",
                        "source_revision": ("4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556"),
                        "version": "test-fixture-1",
                    },
                },
                "config": {
                    "file": refs["config"],
                    "schema_file": refs["schema"],
                },
                "protocol_schema": refs["schema"],
                "capabilities": ["business.work-order"],
                "artifact_requirements": [_artifact_requirement()],
            },
            "scenario_digest": scenario.scenario_digest,
            "scenario": scenario.model_dump(mode="json"),
            "scenario_assets": [
                asset.model_dump(mode="json")
                for asset in scenario.assets
                if any(
                    audience.role == "provider"
                    and audience.workload_id == "business"
                    for audience in asset.audiences
                )
            ],
        }
    )
    return refs, contract


def _rpc(
    connection: socket.socket, operation: str, payload: dict[str, object]
) -> dict[str, object]:
    frame = (
        json.dumps(
            {"operation": operation, **payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    connection.sendall(frame)
    response_bytes = b""
    while not response_bytes.endswith(b"\n"):
        chunk = connection.recv(65536)
        if not chunk:
            raise SelfcheckError("service closed the RPC connection")
        response_bytes += chunk
    response = json.loads(response_bytes)
    if "error" in response:
        return {"__error__": response["error"]}
    return response


def _authorized(payload: dict[str, object]) -> dict[str, object]:
    """The Harness session presents its granted token on every frame."""

    return {"session_token": SESSION_TOKEN, **payload}


def _command(
    connection: socket.socket,
    command_id: str,
    tick: int,
    agent_id: str,
    tool_id: str,
    **arguments: object,
) -> list[dict[str, object]]:
    response = _rpc(
        connection,
        "command",
        _authorized(_command_payload(command_id, tick, agent_id, tool_id, **arguments)),
    )
    if "__error__" in response:
        raise SelfcheckError(f"{command_id}: {response['__error__']}")
    return response["receipts"]  # type: ignore[no-any-return]


def _command_payload(
    command_id: str,
    tick: int,
    agent_id: str,
    tool_id: str,
    **arguments: object,
) -> dict[str, object]:
    return {
        "provider_id": "business",
        "run_id": RUN_ID,
        "request": {
            "run_id": RUN_ID,
            "command_id": command_id,
            "agent_id": agent_id,
            "tool_id": tool_id,
            "issued_at": {"tick": tick, "sim_time_ns": tick * 1_000_000_000},
            "arguments": [
                {"name": name, "value": value}
                for name, value in sorted(arguments.items())
            ],
        },
    }


def _expect_error(
    connection: socket.socket,
    operation: str,
    payload: dict[str, object],
    code: str,
    label: str,
) -> None:
    response = _rpc(connection, operation, payload)
    error = response.get("__error__")
    _require(
        isinstance(error, dict) and error.get("code") == code,
        f"{label} expected error {code!r}, got {response}",
    )


def _static_pose() -> ResolvedPose:
    return ResolvedPose(
        position=ResolvedCoordinate(
            enu=ResolvedEnuPosition(east_m=0.0, north_m=0.0, up_m=0.0),
            ned=ResolvedNedPosition(north_m=0.0, east_m=0.0, down_m=0.0),
            ecef=ResolvedEcefPosition(x_m=6_378_137.0, y_m=0.0, z_m=0.0),
            wgs84=ResolvedWgs84Position(
                longitude_deg=0.0,
                latitude_deg=0.0,
                ellipsoid_height_m=0.0,
            ),
            geoid_separation_m=0.0,
            amsl_m=0.0,
            terrain_amsl_m=0.0,
            agl_m=0.0,
        ),
        orientation_enu=ResolvedQuaternion(qw=1.0, qx=0.0, qy=0.0, qz=0.0),
        orientation_ned=ResolvedQuaternion(qw=1.0, qx=0.0, qy=0.0, qz=0.0),
    )


def _stage_request(
    tick: int,
    scenario_digest: str,
    previous_scene_state_digest: str,
) -> BusinessEnvironmentStageRequest:
    """Construct a canonical motion SceneState for the typed stage input."""

    at = SimulationTime(tick=tick, sim_time_ns=tick * 1_000_000_000)
    sample_fields: dict[str, object] = {
        "schema_version": "aero-bench.state-sample/v1",
        "run_id": RUN_ID,
        "scenario_digest": scenario_digest,
        "at": at,
        "stage": "motion",
        "entity_id": "asset.1",
        "provider_id": "scenario.compiler",
        "sample_kind": "static",
        "pose": _static_pose(),
        "linear_velocity_enu": EnuLinearVelocity(
            east_mps=0.0,
            north_mps=0.0,
            up_mps=0.0,
        ),
        "linear_velocity_ned": NedLinearVelocity(
            north_mps=0.0,
            east_mps=0.0,
            down_mps=0.0,
        ),
        "angular_velocity_body": None,
        "mode": None,
        "armed": None,
        "battery": None,
        "health": None,
        "contacts": (),
        "attributes": (),
    }
    sample_candidate = StateSample.model_construct(
        **sample_fields,
        sample_digest="0" * 64,
    )
    sample = StateSample(
        **sample_fields,
        sample_digest=state_sample_digest_value(sample_candidate),
    )

    contribution_fields: dict[str, object] = {
        "schema_version": "aero-bench.scene-contribution/v1",
        "run_id": RUN_ID,
        "scenario_digest": scenario_digest,
        "at": at,
        "stage": "motion",
        "provider_id": "scenario.compiler",
        "samples": (sample,),
        "attribute_updates": (),
        "payload_digest": "0" * 64,
        "contribution_digest": "0" * 64,
    }
    contribution_payload_candidate = SceneContribution.model_construct(
        **contribution_fields
    )
    contribution_fields["payload_digest"] = scene_contribution_payload_digest_value(
        contribution_payload_candidate
    )
    contribution_candidate = SceneContribution.model_construct(**contribution_fields)
    contribution_fields["contribution_digest"] = scene_contribution_digest_value(
        contribution_candidate
    )
    contribution = SceneContribution(**contribution_fields)

    step_receipt = StepReceipt(
        run_id=RUN_ID,
        provider_id="scenario.compiler",
        reached=at,
        state_digest="d" * 64,
        events=(),
    )
    stage_receipt_fields: dict[str, object] = {
        "schema_version": "aero-bench.stage-receipt/v1",
        "run_id": RUN_ID,
        "scenario_digest": scenario_digest,
        "at": at,
        "stage": "motion",
        "provider_id": "scenario.compiler",
        "state_digest": step_receipt.state_digest,
        "step_receipt_digest": step_receipt_digest_value(step_receipt),
        "contribution_digest": contribution.contribution_digest,
        "payload_digest": contribution.payload_digest,
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
    }
    stage_receipt_candidate = StageReceipt.model_construct(
        **stage_receipt_fields,
        receipt_digest="0" * 64,
    )
    stage_receipt = StageReceipt(
        **stage_receipt_fields,
        receipt_digest=stage_receipt_digest_value(stage_receipt_candidate),
    )
    barrier_fields: dict[str, object] = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": RUN_ID,
        "scenario_digest": scenario_digest,
        "at": at,
        "stage": "motion",
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
        "provider_ids": ("scenario.compiler",),
        "receipts": (stage_receipt,),
        "receipt_digests": (stage_receipt.receipt_digest,),
    }
    barrier_candidate = StageBarrier.model_construct(
        **barrier_fields,
        barrier_digest="0" * 64,
    )
    motion_barrier = StageBarrier(
        **barrier_fields,
        barrier_digest=stage_barrier_digest_value(barrier_candidate),
    )
    scene_state_fields: dict[str, object] = {
        "schema_version": "aero-bench.scene-state/v1",
        "run_id": RUN_ID,
        "scenario_digest": scenario_digest,
        "at": at,
        "declared_entity_ids": ("asset.1",),
        "samples": (sample,),
        "stage_barrier": motion_barrier,
        "contribution_digests": (contribution.contribution_digest,),
        "previous_scene_state_digest": previous_scene_state_digest,
    }
    scene_state_candidate = SceneState.model_construct(
        **scene_state_fields,
        scene_state_digest="0" * 64,
    )
    scene_state = SceneState(
        **scene_state_fields,
        scene_state_digest=scene_state_digest_value(scene_state_candidate),
    )
    return BusinessEnvironmentStageRequest(
        schema_version="aero-bench.provider-stage-request/v1",
        run_id=RUN_ID,
        scenario_digest=scenario_digest,
        provider_id="business",
        target=at,
        stage="business_environment",
        scene_state=scene_state,
        scene_state_digest=scene_state.scene_state_digest,
        predecessor_barriers=(
            StageBarrierDigest(
                stage="motion",
                barrier_digest=motion_barrier.barrier_digest,
            ),
        ),
    )


def _step(
    client: socket.socket,
    tick: int,
    scenario_digest: str,
    previous_scene_state_digest: str,
) -> str:
    request = _stage_request(
        tick,
        scenario_digest,
        previous_scene_state_digest,
    )
    response = _rpc(
        client,
        "step_stage",
        _authorized(
            {
                "provider_id": "business",
                "run_id": RUN_ID,
                "protocol_version": PROTOCOL_VERSION,
                "request": request.model_dump(mode="json"),
            }
        ),
    )
    try:
        result = BusinessEnvironmentStageResult.model_validate(response["result"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SelfcheckError(f"step_stage tick {tick}: {response}") from exc
    _require(
        result.target == request.target
        and result.input_scene_state_digest == request.scene_state_digest
        and result.predecessor_barriers == request.predecessor_barriers
        and result.contribution.samples == ()
        and result.contribution.attribute_updates == (),
        f"step_stage tick {tick} result binding",
    )
    state_events = [
        event
        for event in result.step_receipt.events
        if event.payload_schema_id == "inspection.business.state.v1"
    ]
    _require(len(state_events) == 1, f"step_stage tick {tick} state event")
    state_payload = {item.name: item.value for item in state_events[0].payload}
    expected_predecessors = canonical_json_bytes(
        [item.model_dump(mode="json") for item in request.predecessor_barriers]
    ).decode("utf-8")
    _require(
        state_payload.get("stage_input_scenario_digest") == scenario_digest
        and state_payload.get("stage_input_scene_state_digest")
        == request.scene_state_digest
        and state_payload.get("stage_input_motion_barrier_digest")
        == request.predecessor_barriers[0].barrier_digest
        and state_payload.get("stage_input_predecessor_barriers")
        == expected_predecessors
        and state_payload.get("stage_input_count") == tick,
        f"step_stage tick {tick} authoritative input evidence",
    )
    return request.scene_state_digest


def _run_checks(
    client: socket.socket,
    identity: WorkloadIdentity,
) -> None:
    probe = _rpc(client, "probe", {})
    _require(
        probe.get("schema_version") == PROVIDER_PROBE_SCHEMA
        and probe.get("status") == "accepting"
        and probe.get("provider_id") == "business"
        and probe.get("run_id") == RUN_ID
        and probe.get("adapter") == PROVIDER_ADAPTER
        and probe.get("runtime_image") == RUNTIME_IMAGE
        and probe.get("config_digest") == identity.config_digest,
        f"probe identity mismatch: {probe}",
    )

    package = _task_package().model_dump(mode="json")
    package_digest = hashlib.sha256(canonical_json_bytes(package)).hexdigest()
    prepare = {
        "provider_id": "business",
        "run_id": RUN_ID,
        "protocol_version": PROTOCOL_VERSION,
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": identity.config_digest,
        "artifact_requirements": [_artifact_requirement()],
        "session_token": SESSION_TOKEN,
        "task_id": "inspection.task",
        "package_digest": package_digest,
        "task_package": package,
    }
    _expect_error(
        client,
        "prepare",
        {**prepare, "task_id": "another.task"},
        "identity.mismatch",
        "wrong task_id",
    )
    _expect_error(
        client,
        "prepare",
        {**prepare, "package_digest": "e" * 64},
        "identity.mismatch",
        "wrong package digest",
    )
    # Race counterexample: a peer that reaches the first prepare with a wrong
    # executor-issued token is denied and binds no state; the legitimate
    # session below must still be able to prepare the unprepared service.
    _expect_error(
        client,
        "prepare",
        {**prepare, "session_token": "a" * 64},
        "principal.denied",
        "racing prepare with a wrong executor-issued token",
    )
    # A wrong token is denied before any identity check: even a frame that
    # also drifts public identity learns nothing beyond principal.denied.
    _expect_error(
        client,
        "prepare",
        {**prepare, "session_token": "a" * 64, "task_id": "another.task"},
        "principal.denied",
        "wrong token denied before identity checks",
    )
    _require(_rpc(client, "prepare", prepare).get("status") == "ready", "prepare")
    _expect_error(
        client,
        "prepare",
        prepare,
        "not.ready",
        "second prepare after the service is bound",
    )

    _require(
        _rpc(
            client,
            "reset",
            _authorized({"provider_id": "business", "run_id": RUN_ID, "seed": SEED}),
        )["receipt"]["reached"]
        == {"tick": 0, "sim_time_ns": 0},
        "reset receipt",
    )
    _expect_error(
        client,
        "reset",
        _authorized({"provider_id": "business", "run_id": RUN_ID, "seed": 99}),
        "identity.mismatch",
        "wrong reset seed",
    )

    # Principal counterexample: a second connection that replays every public
    # identity field (provider_id, run_id, a business-role actor, a valid
    # time) — with no token and with a wrong token — must never forge the
    # attested principal.
    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as forger:
        forged = _command_payload(
            "command.forge",
            1,
            "business",
            "business.complete",
            work_order_id="wo.1",
            actor_id="business",
        )
        _expect_error(
            forger,
            "command",
            forged,
            "principal.denied",
            "second connection without the session token",
        )
        _expect_error(
            forger,
            "command",
            {**forged, "session_token": "8" * 64},
            "principal.denied",
            "second connection with a wrong session token",
        )

    scenario_digest = identity.contract.scenario_digest
    previous_scene_state_digest = SCENE_STATE_ROOT_DIGEST
    previous_scene_state_digest = _step(
        client, 1, scenario_digest, previous_scene_state_digest
    )
    _require(
        [
            item["phase"]
            for item in _command(
                client,
                "command.claim",
                1,
                "agent.1",
                "business.claim",
                work_order_id="wo.1",
                actor_id="agent.1",
            )
        ]
        == ["received", "accepted", "applied", "completed"],
        "claim command",
    )
    _expect_error(
        client,
        "command",
        _command_payload(
            "command.spoof",
            2,
            "agent.1",
            "business.observation_ready",
            actor_id="observation.provider",
            observation_id="obs.1",
            work_order_id="wo.1",
        ),
        "principal.denied",
        "agent forging observation authority",
    )
    _expect_error(
        client,
        "command",
        _command_payload(
            "command.spoof2",
            2,
            "agent.1",
            "business.claim",
            actor_id="business",
            work_order_id="wo.1",
        ),
        "principal.denied",
        "agent spoofing another actor",
    )
    previous_scene_state_digest = _step(
        client, 2, scenario_digest, previous_scene_state_digest
    )
    _require(
        [
            item["phase"]
            for item in _command(
                client,
                "command.start",
                2,
                "agent.1",
                "business.start",
                work_order_id="wo.1",
                actor_id="agent.1",
            )
        ]
        == ["received", "accepted", "applied", "completed"],
        "start command",
    )
    previous_scene_state_digest = _step(
        client, 3, scenario_digest, previous_scene_state_digest
    )
    _require(
        [
            item["phase"]
            for item in _command(
                client,
                "command.observed",
                3,
                "observation.provider",
                "business.observation_ready",
                work_order_id="wo.1",
                actor_id="observation.provider",
                observation_id="obs.1",
            )
        ]
        == ["received", "accepted", "applied", "completed"],
        "observation_ready command",
    )
    previous_scene_state_digest = _step(
        client, 4, scenario_digest, previous_scene_state_digest
    )
    _require(
        [
            item["phase"]
            for item in _command(
                client,
                "command.submit",
                4,
                "agent.1",
                "business.submit",
                work_order_id="wo.1",
                actor_id="agent.1",
                observation_id="obs.1",
                report_payload_digest=PAYLOAD_DIGEST,
            )
        ]
        == ["received", "accepted", "applied", "completed"],
        "submit command",
    )
    _step(client, 5, scenario_digest, previous_scene_state_digest)
    _require(
        [
            item["phase"]
            for item in _command(
                client,
                "command.complete",
                5,
                "business",
                "business.complete",
                work_order_id="wo.1",
                actor_id="business",
            )
        ]
        == ["received", "accepted", "applied", "completed"],
        "complete command",
    )

    query_result = _rpc(
        client,
        "query",
        _authorized(
            {
                "provider_id": "business",
                "run_id": RUN_ID,
                "query": {
                    "run_id": RUN_ID,
                    "query_id": "query.1",
                    "kind": "work_order",
                    "work_order_id": "wo.1",
                    "issued_at": {"tick": 5, "sim_time_ns": 5_000_000_000},
                },
            }
        ),
    )
    _require(
        query_result["result"]["work_orders"][0]["status"] == "completed",
        "query result",
    )
    _expect_error(
        client,
        "query",
        _authorized(
            {
                "provider_id": "business",
                "run_id": RUN_ID,
                "query": {
                    "run_id": RUN_ID,
                    "query_id": "query.2",
                    "kind": "work_order",
                    "work_order_id": "wo.missing",
                    "issued_at": {"tick": 5, "sim_time_ns": 5_000_000_000},
                },
            }
        ),
        "query.rejected",
        "unknown work order query",
    )
    _require(
        isinstance(
            _rpc(
                client,
                "snapshot",
                _authorized({"provider_id": "business", "run_id": RUN_ID}),
            )["snapshot_digest"],
            str,
        ),
        "snapshot digest",
    )
    finalization = _rpc(
        client,
        "finalize",
        _authorized(
            {
                "provider_id": "business",
                "run_id": RUN_ID,
                "request": {
                    "schema_version": "aero-bench.provider-finalization-request/v1",
                    "run_id": RUN_ID,
                    "terminal_event": "run.completed",
                    "terminal_time": {"tick": 5, "sim_time_ns": 5_000_000_000},
                    "event_chain_root": "f" * 64,
                },
            }
        ),
    )
    _require(
        finalization.get("receipt", {}).get("event_chain_root") == "f" * 64,
        "finalization receipt",
    )
    _require(
        _rpc(
            client,
            "shutdown",
            _authorized({"provider_id": "business", "run_id": RUN_ID}),
        )
        == {"status": "stopped"},
        "shutdown acknowledgement",
    )


def _drive_checks(identity: WorkloadIdentity) -> None:
    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as client:
        _run_checks(client, identity)


async def _run_selfcheck() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "artifacts").mkdir()
        refs, contract = _write_run_inputs(root)
        identity = WorkloadIdentity(
            run_id=RUN_ID,
            provider_id="business",
            provider_port=PORT,
            runtime_image=RUNTIME_IMAGE,
            config_digest=refs["config"]["sha256"],
            artifact_requirement=_artifact_requirement(),
            seed=SEED,
            contract=contract,
        )
        service = InspectionBusinessProviderService(
            bundle_root=root,
            artifact_root=root / "artifacts",
            rpc_port=PORT,
            workload_identity=identity,
            session_token=SESSION_TOKEN,
        )
        from aero_bench.providers.rpc import JsonLineRpcServer

        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=PORT)
        try:
            # The RPC client is blocking; running it on a worker thread keeps
            # the event loop free to serve every request.
            await asyncio.to_thread(_drive_checks, identity)
        finally:
            await server.graceful_close()
            await handle.wait_closed()
        artifact = root / "artifacts" / "business/state.json"
        _require(artifact.is_file(), "artifact was not written")
        document = json.loads(artifact.read_text(encoding="utf-8"))
        history = document["history"]
        _require(len(history) == 5, "business history must hold five records")
        _require(
            document["business_history_root"] == history[-1]["record_hash"],
            "business history root mismatch",
        )
        _require(
            document["state"]["work_orders"][0]["status"] == "completed",
            "work order must end completed",
        )
        _require(
            document.get("event_chain_root") == "f" * 64
            and "event_ledger" not in document,
            "artifact must carry only its bound event-chain root",
        )
        previous_hash = "0" * 64
        for record in history:
            _require(
                record["previous_hash"] == previous_hash,
                "business history hash chain is broken",
            )
            previous_hash = record["record_hash"]
    print("inspection-business selfcheck: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(_run_selfcheck())
    except SelfcheckError as error:
        print(f"inspection-business selfcheck: FAILED: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
