from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from aero_bench.config.models import FileRef
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection import (
    BusinessCommandEnvelope,
    BusinessQuery,
    BusinessQueryResult,
    ClaimWorkOrderCommand,
    CompleteWorkOrderCommand,
    InspectionArtifactRequirement,
    InspectionAssetRef,
    InspectionBoundSpec,
    InspectionBoundSource,
    InspectionBusinessService,
    InspectionActorGrant,
    InspectionGoal,
    InspectionTaskPackage,
    ImagingBoundSpec,
    LinkBoundSpec,
    MissionBoundSpec,
    ObservationContract,
    ObservationReadyCommand,
    ObservationTrigger,
    StartWorkOrderCommand,
    SubmitInspectionCommand,
    WorkOrderStatus,
)


RUN_ID = "a" * 64
PAYLOAD_DIGEST = hashlib.sha256(b"report").hexdigest()
FILE_DIGEST = hashlib.sha256(canonical_json_bytes({"type": "object"})).hexdigest()
TRUTH_PAYLOAD_DIGEST = hashlib.sha256(
    canonical_json_bytes(
        [
            {
                "source_artifact_id": "artifact.truth",
                "defect_id": "defect.1",
                "target_id": "asset.1",
                "geometrically_visible": True,
            }
        ]
    )
).hexdigest()


def _package() -> InspectionTaskPackage:
    schema = FileRef(path="schemas/observation.json", sha256=FILE_DIGEST)
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
                file=FileRef(
                    path="private/labels.json",
                    sha256=TRUTH_PAYLOAD_DIGEST,
                ),
                visibility="verifier",
                media_type="application/json",
            ),
        ),
        work_orders=(
            {
                "work_order_id": "wo.1",
                "target_id": "asset.1",
                "required_observation_id": "obs.1",
                "arrival_tolerance_m": 1.0,
                "report_artifact_type": "agent.report",
                "upload_deadline_ns": 20_000_000_000,
            },
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
        bound_sources=(
            InspectionBoundSource(
                bound_field="mission.shortest_path_m",
                provider_id="business",
                config_pointer="/inspection_bounds/mission/shortest_path_m",
            ),
            InspectionBoundSource(
                bound_field="mission.max_speed_mps",
                provider_id="business",
                config_pointer="/inspection_bounds/mission/max_speed_mps",
            ),
            InspectionBoundSource(
                bound_field="mission.fixed_time_s",
                provider_id="business",
                config_pointer="/inspection_bounds/mission/fixed_time_s",
            ),
            InspectionBoundSource(
                bound_field="mission.inspection_dwell_s",
                provider_id="business",
                config_pointer="/inspection_bounds/mission/inspection_dwell_s",
            ),
            InspectionBoundSource(
                bound_field="mission.deadline_s",
                provider_id="business",
                config_pointer="/inspection_bounds/mission/deadline_s",
            ),
            InspectionBoundSource(
                bound_field="link.minimum_payload_bytes",
                provider_id="business",
                config_pointer="/inspection_bounds/link/minimum_payload_bytes",
            ),
            InspectionBoundSource(
                bound_field="link.max_bandwidth_bps",
                provider_id="business",
                config_pointer="/inspection_bounds/link/max_bandwidth_bps",
            ),
            InspectionBoundSource(
                bound_field="link.minimum_latency_s",
                provider_id="business",
                config_pointer="/inspection_bounds/link/minimum_latency_s",
            ),
            InspectionBoundSource(
                bound_field="link.upload_deadline_s",
                provider_id="business",
                config_pointer="/inspection_bounds/link/upload_deadline_s",
            ),
            InspectionBoundSource(
                bound_field="imaging.defect_size_m",
                provider_id="business",
                config_pointer="/inspection_bounds/imaging/defect_size_m",
            ),
            InspectionBoundSource(
                bound_field="imaging.standoff_distance_m",
                provider_id="business",
                config_pointer="/inspection_bounds/imaging/standoff_distance_m",
            ),
            InspectionBoundSource(
                bound_field="imaging.focal_length_m",
                provider_id="business",
                config_pointer="/inspection_bounds/imaging/focal_length_m",
            ),
            InspectionBoundSource(
                bound_field="imaging.pixel_pitch_m",
                provider_id="business",
                config_pointer="/inspection_bounds/imaging/pixel_pitch_m",
            ),
            InspectionBoundSource(
                bound_field="imaging.minimum_resolvable_pixels",
                provider_id="business",
                config_pointer="/inspection_bounds/imaging/minimum_resolvable_pixels",
            ),
            InspectionBoundSource(
                bound_field="total_defects",
                provider_id="business",
                config_pointer="/inspection_bounds/total_defects",
            ),
            InspectionBoundSource(
                bound_field="geometrically_visible_defects",
                provider_id="business",
                config_pointer="/inspection_bounds/geometrically_visible_defects",
            ),
        ),
        artifact_requirements=(
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
                relative_path="business.json",
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
                artifact_id="artifact.camera-frame-data",
                artifact_type="camera-frame-data",
                producer_id="observation.provider",
                visibility="public",
                relative_path="camera-frame-data.json",
                max_size_bytes=1_048_576,
                source_asset_id=None,
                evidence_kind="camera_frame_data",
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


def _time(tick: int) -> SimulationTime:
    return SimulationTime(tick=tick, sim_time_ns=tick * 1_000_000_000)


def test_task_package_is_strict_and_initializes_private_truth_boundary() -> None:
    package = _package()
    assert (
        package.initial_business_state(run_id=RUN_ID).work_orders[0].status
        is WorkOrderStatus.CREATED
    )
    with pytest.raises(ValidationError, match="unexpected"):
        InspectionTaskPackage.model_validate(
            {**package.model_dump(), "unexpected": True}
        )


def test_business_query_json_round_trip_preserves_strict_enum_members() -> None:
    service = InspectionBusinessService(_package(), run_id=RUN_ID)
    result = service.query(
        BusinessQuery(
            run_id=RUN_ID,
            query_id="query.json-round-trip",
            kind="work_order",
            work_order_id="wo.1",
            issued_at=_time(0),
        )
    )

    reparsed = BusinessQueryResult.model_validate(result.model_dump(mode="json"))

    assert reparsed == result
    assert reparsed.work_orders[0].status is WorkOrderStatus.CREATED
    invalid = result.model_dump(mode="json")
    invalid["work_orders"][0]["status"] = "not-a-work-order-status"
    with pytest.raises(ValidationError):
        BusinessQueryResult.model_validate(invalid)


def test_task_package_requires_truth_bundle_asset_binding() -> None:
    raw = _package().model_dump()
    truth = next(
        item
        for item in raw["artifact_requirements"]
        if item["evidence_kind"] == "truth"
    )
    truth["producer_id"] = "business"
    truth["source_asset_id"] = None

    with pytest.raises(ValidationError, match="immutable truth evidence"):
        InspectionTaskPackage.model_validate(raw)

    raw = _package().model_dump()
    truth = next(
        item
        for item in raw["artifact_requirements"]
        if item["evidence_kind"] == "truth"
    )
    truth["visibility"] = "public"
    with pytest.raises(ValidationError, match="immutable truth evidence"):
        InspectionTaskPackage.model_validate(raw)


def test_task_package_allows_task_local_business_ownership() -> None:
    raw = _package().model_dump()
    raw["actors"] = [actor for actor in raw["actors"] if actor["role"] != "business"]

    package = InspectionTaskPackage.model_validate(raw)

    assert all(actor.role != "business" for actor in package.actors)


def test_artifacts_are_unique_by_artifact_id() -> None:
    package = _package()
    raw = package.model_dump()
    detection = next(
        item
        for item in raw["artifact_requirements"]
        if item["evidence_kind"] == "detection"
    )
    detection["artifact_id"] = "artifact.report"
    with pytest.raises(ValidationError, match="artifact ids"):
        InspectionTaskPackage.model_validate(raw)


def test_business_commands_follow_deterministic_work_order_barrier() -> None:
    service = InspectionBusinessService(_package(), run_id=RUN_ID)
    service.apply(
        BusinessCommandEnvelope(
            run_id=RUN_ID,
            command=ClaimWorkOrderCommand(
                command_id="command.claim",
                work_order_id="wo.1",
                actor_id="agent.1",
                issued_at=_time(1),
            ),
        )
    )
    service.apply(
        BusinessCommandEnvelope(
            run_id=RUN_ID,
            command=StartWorkOrderCommand(
                command_id="command.start",
                work_order_id="wo.1",
                actor_id="agent.1",
                issued_at=_time(2),
            ),
        )
    )
    service.apply(
        BusinessCommandEnvelope(
            run_id=RUN_ID,
            command=ObservationReadyCommand(
                command_id="command.observed",
                work_order_id="wo.1",
                actor_id="observation.provider",
                observation_id="obs.1",
                issued_at=_time(3),
            ),
        )
    )
    service.apply(
        BusinessCommandEnvelope(
            run_id=RUN_ID,
            command=SubmitInspectionCommand(
                command_id="command.submit",
                work_order_id="wo.1",
                actor_id="agent.1",
                observation_id="obs.1",
                report_payload_digest=PAYLOAD_DIGEST,
                issued_at=_time(4),
            ),
        )
    )
    result = service.apply(
        BusinessCommandEnvelope(
            run_id=RUN_ID,
            command=CompleteWorkOrderCommand(
                command_id="command.complete",
                work_order_id="wo.1",
                actor_id="business",
                issued_at=_time(5),
            ),
        )
    )
    assert result.state.work_orders[0].status is WorkOrderStatus.COMPLETED
    query = service.query(
        BusinessQuery(
            run_id=RUN_ID,
            query_id="query.1",
            kind="work_order",
            work_order_id="wo.1",
            issued_at=_time(5),
        )
    )
    assert query.work_orders[0].report_payload_digest == PAYLOAD_DIGEST


def test_work_order_rejects_illegal_transition_and_time_reversal() -> None:
    service = InspectionBusinessService(_package(), run_id=RUN_ID)
    with pytest.raises(ValueError, match="invalid work order transition"):
        service.apply(
            BusinessCommandEnvelope(
                run_id=RUN_ID,
                command=SubmitInspectionCommand(
                    command_id="command.submit",
                    work_order_id="wo.1",
                    actor_id="agent.1",
                    observation_id="obs.1",
                    report_payload_digest=PAYLOAD_DIGEST,
                    issued_at=_time(1),
                ),
            )
        )


def test_actor_id_cannot_claim_another_declared_role() -> None:
    service = InspectionBusinessService(_package(), run_id=RUN_ID)
    with pytest.raises(ValueError, match="actor id is not authorized"):
        service.apply(
            BusinessCommandEnvelope(
                run_id=RUN_ID,
                command=ClaimWorkOrderCommand(
                    command_id="command.imposter",
                    work_order_id="wo.1",
                    actor_id="inspection.verifier",
                    issued_at=_time(1),
                ),
            )
        )


def test_verifier_cannot_complete_a_work_order() -> None:
    service = InspectionBusinessService(_package(), run_id=RUN_ID)
    for command in (
        ClaimWorkOrderCommand(
            command_id="command.claim",
            work_order_id="wo.1",
            actor_id="agent.1",
            issued_at=_time(1),
        ),
        StartWorkOrderCommand(
            command_id="command.start",
            work_order_id="wo.1",
            actor_id="agent.1",
            issued_at=_time(2),
        ),
        ObservationReadyCommand(
            command_id="command.observed",
            work_order_id="wo.1",
            actor_id="observation.provider",
            observation_id="obs.1",
            issued_at=_time(3),
        ),
        SubmitInspectionCommand(
            command_id="command.submit",
            work_order_id="wo.1",
            actor_id="agent.1",
            observation_id="obs.1",
            report_payload_digest=PAYLOAD_DIGEST,
            issued_at=_time(4),
        ),
    ):
        service.apply(BusinessCommandEnvelope(run_id=RUN_ID, command=command))
    with pytest.raises(ValueError, match="actor id is not authorized"):
        service.apply(
            BusinessCommandEnvelope(
                run_id=RUN_ID,
                command=CompleteWorkOrderCommand(
                    command_id="command.verifier-complete",
                    work_order_id="wo.1",
                    actor_id="inspection.verifier",
                    issued_at=_time(5),
                ),
            )
        )


def test_tick_and_sim_time_each_move_monotonically() -> None:
    service = InspectionBusinessService(_package(), run_id=RUN_ID)
    service.apply(
        BusinessCommandEnvelope(
            run_id=RUN_ID,
            command=ClaimWorkOrderCommand(
                command_id="command.claim",
                work_order_id="wo.1",
                actor_id="agent.1",
                issued_at=_time(2),
            ),
        )
    )
    with pytest.raises(ValueError, match="time moved backwards"):
        service.apply(
            BusinessCommandEnvelope(
                run_id=RUN_ID,
                command=StartWorkOrderCommand(
                    command_id="command.backwards",
                    work_order_id="wo.1",
                    actor_id="agent.1",
                    issued_at=SimulationTime(tick=3, sim_time_ns=1_000_000_000),
                ),
            )
        )
