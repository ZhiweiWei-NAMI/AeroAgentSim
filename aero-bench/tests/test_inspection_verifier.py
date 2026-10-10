from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import struct
from types import SimpleNamespace
import zlib

import aero_bench.tasks.inspection.sealed_evidence as sealed_evidence_module

import pytest
from pydantic import ValidationError

from aero_bench.artifacts.contracts import ArtifactRecord, SealManifest
from aero_bench.config.models import (
    AgentSpec,
    ArtifactRequirement,
    AssetAudience,
    AssetRef,
    ClockSpec,
    EnvironmentSpec,
    FileRef,
    GatewaySpec,
    GoalSpec,
    ImplementationIdentity,
    NamedValue,
    ObservationGrant,
    ProviderRef,
    ResourceBudget,
    RuntimeImage,
    RuntimeSpec,
    SchemaBoundFile,
    TaskPackageRef,
    TaskSpec,
    VerifierSpec,
)
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers import ProviderManifest
from aero_bench.runtime import (
    AgentTurnCompletion,
    BusinessEnvironmentStageResult,
    EnuLinearVelocity,
    EventLedger,
    MotionStageResult,
    NedLinearVelocity,
    ProviderEvent,
    SceneContribution,
    SimulationTime,
    StateSample,
    StepReceipt,
    ledger_jsonl_bytes,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    scene_state_jsonl_bytes,
    state_sample_digest_value,
    step_receipt_digest_value,
)
from aero_bench.runtime.harness import HarnessCoordinator
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection import (
    AuthoritativeViewContext,
    BusinessCommandEnvelope,
    BusinessLedgerBinding,
    BusinessStateEvidence,
    CameraFrameDataRecord,
    ClaimWorkOrderCommand,
    CompleteWorkOrderCommand,
    DefectDetection,
    DefectTruth,
    DeliveryEvidence,
    EvidenceBinding,
    EventLedgerEvidence,
    InspectionBoundResolution,
    InspectionArtifactRequirement,
    InspectionEvidenceBundle,
    InspectionReportDetection,
    InspectionReportPayload,
    InspectionProviderConfigBinding,
    InspectionTaskPackage,
    InspectionVerifier,
    InspectionBusinessService,
    load_sealed_inspection_evidence as _strict_load,
    InspectionBusinessState,
    ObservationMetadata,
    ObservationReadyCommand,
    PublicSensorFrameRecord,
    rgb_observation_public_payload,
    StartWorkOrderCommand,
    SubmitInspectionCommand,
    TheoreticalBoundsEvidence,
    TrajectoryEvidence,
    calculate_theoretical_bounds,
)
from aero_bench.tasks.inspection.contracts import (
    TrajectoryLinearVelocity,
    TrajectoryOrientation,
    TrajectoryWgs84Position,
)
from aero_bench.tasks.inspection.payloads import evidence_payload_bytes
from aero_bench.world.resolved import (
    ResolvedInspectionObservationProjection,
    ResolvedProvider,
    ResolvedTaskScenarioProjection,
    _resolved_task_binding,
    scenario_digest_value,
)
from tests.test_inspection_business import (
    FILE_DIGEST,
    RUN_ID,
    TRUTH_PAYLOAD_DIGEST,
    _package,
)
from tests.support import fake_finalization_receipt
from tests.world.support import deterministic_inspection_scenario

ATTEMPT_ID = "attempt.test"
FRAME_ID = "frame.1"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _rgb_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x10\x20\x30"))
        + _png_chunk(b"IEND", b"")
    )


IMAGE_BYTES = _rgb_png()
IMAGE_SHA256 = hashlib.sha256(IMAGE_BYTES).hexdigest()
OBSERVATION_PAYLOAD_DIGEST = hashlib.sha256(
    canonical_json_bytes(
        rgb_observation_public_payload(
            target_id="asset.1",
            camera_id="camera.1",
            distance_m=10.0,
            view_angle_deg=20.0,
            frame_id=FRAME_ID,
            vehicle_id="vehicle.1",
            engine_sim_time_ns=13_000_000_000,
            logical_origin_engine_ns=10_000_000_000,
            image_sha256=IMAGE_SHA256,
            size_bytes=len(IMAGE_BYTES),
            selector=f"frames/{FRAME_ID}",
            width=1,
            height=1,
            capture_pose_source="gazebo.pose.private_digest",
            camera_pose_sha256=FILE_DIGEST,
            target_pose_sha256=FILE_DIGEST,
            image_base64=base64.b64encode(IMAGE_BYTES).decode("ascii"),
        )
    )
).hexdigest()


def _detection_payload(run_id: str) -> bytes:
    return canonical_json_bytes(
        [
            {
                "schema_version": "aero-bench.inspection-detection/v1",
                "source_artifact_id": "artifact.detection",
                "run_id": run_id,
                "work_order_id": "wo.1",
                "observation_id": "obs.1",
                "frame_id": FRAME_ID,
                "image_sha256": IMAGE_SHA256,
                "defect_id": "defect.1",
                "target_id": "asset.1",
            }
        ]
    )


def _detection_payload_digest(run_id: str) -> str:
    return hashlib.sha256(_detection_payload(run_id)).hexdigest()


DETECTION_PAYLOAD_DIGEST = _detection_payload_digest(RUN_ID)


def _report(*, run_id: str = RUN_ID) -> InspectionReportPayload:
    return InspectionReportPayload(
        source_artifact_id="artifact.report",
        run_id=run_id,
        work_order_id="wo.1",
        observation_id="obs.1",
        observation_payload_digest=OBSERVATION_PAYLOAD_DIGEST,
        detection_payload_digest=_detection_payload_digest(run_id),
        detections=(
            InspectionReportDetection(
                defect_id="defect.1",
                target_id="asset.1",
                frame_id=FRAME_ID,
                image_sha256=IMAGE_SHA256,
            ),
        ),
    )


def _report_payload_digest(run_id: str) -> str:
    return hashlib.sha256(
        canonical_json_bytes([_report(run_id=run_id).model_dump(mode="json")])
    ).hexdigest()


REPORT_PAYLOAD_DIGEST = _report_payload_digest(RUN_ID)


def _provider_config_bytes(
    package: InspectionTaskPackage,
    provider_id: str,
) -> bytes:
    if provider_id == "business":
        document = {"inspection_bounds": package.bounds.model_dump(mode="json")}
    elif provider_id == "observation.provider":
        document = {}
    else:
        raise AssertionError(f"unknown fixture provider: {provider_id}")
    return canonical_json_bytes(document)


def _provider_config_digest(
    package: InspectionTaskPackage,
    provider_id: str,
) -> str:
    return hashlib.sha256(_provider_config_bytes(package, provider_id)).hexdigest()


def _resolution(package: InspectionTaskPackage) -> InspectionBoundResolution:
    return InspectionBoundResolution(
        bounds=package.bounds,
        provider_configs=tuple(
            InspectionProviderConfigBinding(
                provider_id=provider_id,
                config_digest=_provider_config_digest(package, provider_id),
            )
            for provider_id in ("business", "observation.provider")
        ),
    )


def _package_config_bytes(package: InspectionTaskPackage) -> bytes:
    return canonical_json_bytes(package.model_dump(mode="json"))


def _package_schema_bytes() -> bytes:
    return canonical_json_bytes({"type": "object"})


def _write_package_bundle(package: InspectionTaskPackage, root) -> None:
    (root / "configs").mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / "configs" / "inspection.json").write_bytes(_package_config_bytes(package))
    (root / "schemas" / "inspection.json").write_bytes(_package_schema_bytes())
    for provider_id, name in (
        ("business", "business"),
        ("observation.provider", "observation"),
    ):
        (root / "configs" / f"{name}.json").write_bytes(
            _provider_config_bytes(package, provider_id)
        )
        (root / "schemas" / f"{name}.json").write_bytes(_package_schema_bytes())


def _bundle_root(sealed_root):
    return sealed_root.parent / f"{sealed_root.name}-bundle"


def _load_sealed(
    *,
    package: InspectionTaskPackage,
    run: ResolvedRunSpec,
    seal: SealManifest,
    sealed_root,
) -> InspectionEvidenceBundle:
    return _strict_load(
        package=package,
        bundle_root=_bundle_root(sealed_root),
        run=run,
        seal=seal,
        sealed_root=sealed_root,
    )


def _verify_formally(
    verifier: InspectionVerifier,
    *,
    run: ResolvedRunSpec,
    seal: SealManifest,
    sealed_root,
):
    return verifier.verify_sealed(
        bundle_root=_bundle_root(sealed_root),
        run=run,
        seal=seal,
        sealed_root=sealed_root,
    )


def _verifier(package: InspectionTaskPackage) -> InspectionVerifier:
    return InspectionVerifier(package, resolved_bounds=_resolution(package))


def _resolved_run(
    package: InspectionTaskPackage,
    *,
    truth_asset_digest: str = TRUTH_PAYLOAD_DIGEST,
    business_config_digest: str | None = None,
    world_asset_audiences: tuple[AssetAudience, ...] | None = None,
    world_asset_classification: str = "private",
) -> ResolvedRunSpec:
    if business_config_digest is None:
        business_config_digest = _provider_config_digest(package, "business")
    if world_asset_audiences is None:
        world_asset_audiences = (
            AssetAudience(
                role="provider",
                workload_ids=("observation.provider",),
            ),
        )
    observation_config_digest = _provider_config_digest(package, "observation.provider")

    def file(path: str, digest: str = FILE_DIGEST) -> FileRef:
        return FileRef(path=path, sha256=digest)

    def runtime(digest_character: str, component_id: str) -> RuntimeSpec:
        return RuntimeSpec(
            runtime=RuntimeImage(
                image=f"registry.test/{component_id}@sha256:{digest_character * 64}",
                command=("run",),
            ),
            resources=ResourceBudget(
                cpu_millicores=1,
                memory_mib=64,
                gpu_count=0,
            ),
            implementation=ImplementationIdentity(
                component_id=component_id,
                kind="production",
                source_uri=f"https://github.com/aero-bench/{component_id}",
                source_revision=digest_character * 40,
                version="test-fixture-1",
            ),
        )

    requirements = tuple(
        ArtifactRequirement.model_validate(
            requirement.model_dump(mode="json", exclude={"evidence_kind"})
        )
        for requirement in package.artifact_requirements
    )
    requirements_by_producer: dict[str, tuple[ArtifactRequirement, ...]] = {
        producer_id: tuple(
            requirement
            for requirement in requirements
            if requirement.producer_id == producer_id
        )
        for producer_id in {requirement.producer_id for requirement in requirements}
    }
    verifier_id = package.verifier_id
    package_config_file = FileRef(
        path="configs/inspection.json",
        sha256=hashlib.sha256(_package_config_bytes(package)).hexdigest(),
    )
    package_schema_file = FileRef(
        path="schemas/inspection.json",
        sha256=hashlib.sha256(_package_schema_bytes()).hexdigest(),
    )
    task = TaskSpec(
        schema_version="aero-bench.task/v1",
        task_id=package.task_id,
        package=TaskPackageRef(
            package_id="inspection.v1",
            config=SchemaBoundFile(
                file=package_config_file,
                schema_file=package_schema_file,
            ),
        ),
        instruction=file("instructions/inspection.txt"),
        required_capabilities=("business.work-order", "observation.capture"),
        required_tools=(),
        assets=(
            AssetRef(
                asset_id="world.images",
                file=package.assets[0].file,
                classification=world_asset_classification,
                audiences=world_asset_audiences,
            ),
            AssetRef(
                asset_id="verifier.labels",
                file=file("private/labels.json", truth_asset_digest),
                classification="private",
                audiences=(
                    AssetAudience(role="verifier", workload_ids=(verifier_id,)),
                ),
            ),
        ),
        goals=tuple(
            GoalSpec(
                goal_id=goal.goal_id,
                verifier_id=verifier_id,
                metric_id=goal.metric_id,
                operator=goal.operator,
                threshold=goal.threshold,
                evidence=goal.evidence,
                parameters=goal.parameters,
            )
            for goal in package.goals
        ),
        verifier=VerifierSpec(
            verifier_id=verifier_id,
            workload=runtime("5", verifier_id),
            config=SchemaBoundFile(
                file=file("configs/verifier.json"),
                schema_file=file("schemas/verifier.json"),
            ),
            artifact_requirements=requirements,
            output_artifacts=(
                ArtifactRequirement(
                    artifact_id="artifact.verification",
                    artifact_type="verification.report",
                    producer_id=verifier_id,
                    visibility="public",
                    relative_path="verification.json",
                    max_size_bytes=1_048_576,
                    source_asset_id=None,
                ),
            ),
        ),
    )
    environment = EnvironmentSpec(
        schema_version="aero-bench.environment/v1",
        environment_id="inspection.environment",
        harness=runtime("1", "aero-bench.harness"),
        clock=ClockSpec(
            authority="provider_barrier",
            step_ns=1_000_000_000,
            max_steps=10,
            provider_timeout_ms=1_000,
        ),
        gateway=GatewaySpec(protocol_schema=file("schemas/gateway.json"), port=30_000),
        providers=(
            ProviderRef(
                provider_id="business",
                adapter="business.adapter",
                port=30_001,
                workload=runtime("2", "business.adapter"),
                config=SchemaBoundFile(
                    file=file("configs/business.json", business_config_digest),
                    schema_file=file("schemas/business.json"),
                ),
                protocol_schema=file("schemas/business-protocol.json"),
                capabilities=("business.work-order",),
                artifact_requirements=requirements_by_producer["business"],
            ),
            ProviderRef(
                provider_id="flight",
                adapter="flight.adapter",
                port=30_003,
                workload=runtime("6", "flight.adapter"),
                config=SchemaBoundFile(
                    file=file("configs/flight.json"),
                    schema_file=file("schemas/flight.json"),
                ),
                protocol_schema=file("schemas/flight-protocol.json"),
                capabilities=("gazebo.frames", "gazebo.physics"),
                artifact_requirements=(),
            ),
            ProviderRef(
                provider_id="observation.provider",
                adapter="observation.adapter",
                port=30_002,
                workload=runtime("3", "observation.adapter"),
                config=SchemaBoundFile(
                    file=file(
                        "configs/observation.json",
                        observation_config_digest,
                    ),
                    schema_file=file("schemas/observation.json"),
                ),
                protocol_schema=file("schemas/observation-protocol.json"),
                capabilities=(
                    "camera.rgb",
                    "environment.sensor",
                    "observation.capture",
                ),
                artifact_requirements=requirements_by_producer["observation.provider"],
            ),
        ),
        harness_artifact_requirements=requirements_by_producer["harness"],
    )
    agents = (
        AgentSpec(
            schema_version="aero-bench.agent/v2",
            agent_id="agent.1",
            workload=runtime("4", "agent.1"),
            tools=(),
            queries=(),
            observations=tuple(
                ObservationGrant(
                    observation_id=observation.observation_id,
                    provider_id=observation.trigger.provider_id,
                    schema_file=observation.metadata_schema,
                    timeout_ms=1_000,
                )
                for observation in package.observations
            ),
            artifact_requirements=requirements_by_producer["agent.1"],
        ),
    )
    artifact_requirements = tuple(
        sorted(requirements, key=lambda requirement: requirement.artifact_id)
    )
    scenario = deterministic_inspection_scenario(seed=7)
    projection = ResolvedTaskScenarioProjection(
        logical_endpoint_ids=(),
        logical_capability_ids=(),
        observations=tuple(
            ResolvedInspectionObservationProjection(
                projection_kind="inspection",
                observation_id=observation.observation_id,
                work_order_id=next(
                    order.work_order_id
                    for order in package.work_orders
                    if order.required_observation_id == observation.observation_id
                ),
                target_id=observation.target_id,
                simulation_asset_id=observation.simulation_asset_id,
                sensor_id=observation.trigger.camera_id,
                media_type=observation.media_type,
                min_distance_m=observation.trigger.min_distance_m,
                max_distance_m=observation.trigger.max_distance_m,
                min_view_angle_deg=observation.trigger.min_view_angle_deg,
                max_view_angle_deg=observation.trigger.max_view_angle_deg,
                earliest_time_ns=observation.trigger.earliest_time_ns,
                latest_time_ns=observation.trigger.latest_time_ns,
            )
            for observation in package.observations
        ),
    )
    scenario_providers = tuple(
        sorted(
            (
                ResolvedProvider(
                    provider_id="business",
                    runtime_stage="business_environment",
                    roles=("mission",),
                    capability_ids=("business.work-order",),
                ),
                *(
                    provider.model_copy(
                        update={
                            "capability_ids": (
                                "camera.rgb",
                                "environment.sensor",
                                "observation.capture",
                            )
                        }
                    )
                    if provider.provider_id == "observation.provider"
                    else provider
                    for provider in scenario.providers
                ),
            ),
            key=lambda provider: provider.provider_id,
        )
    )
    scenario = scenario.model_copy(
        update={
            "providers": scenario_providers,
            "task": _resolved_task_binding(
                task=task,
                agents=agents,
                providers=scenario_providers,
                task_projection=projection,
            ),
        }
    )
    scenario = scenario.model_copy(
        update={"scenario_digest": scenario_digest_value(scenario)}
    )
    theoretical_bounds = calculate_theoretical_bounds(
        package.bounds,
        network_delivery_required=package.network_delivery_required,
    )
    payload = {
        "schema_version": "aero-bench.resolved-run/v5",
        "executor_kind": "docker_reference",
        "execution_scope": "formal_benchmark",
        "suite": file("suite.yaml").model_dump(mode="json"),
        "suite_id": "inspection.suite",
        "case_id": "inspection.case",
        "launch_site_id": scenario.selected_launch_site_id,
        "seed": 7,
        "scenario": scenario.model_dump(mode="json"),
        "task": task.model_dump(mode="json"),
        "environment": environment.model_dump(mode="json"),
        "agents": [agent.model_dump(mode="json") for agent in agents],
        "feasibility": {
            "package_id": "inspection.v1",
            "feasible": theoretical_bounds.success_upper_bound > 0.0,
            "success_upper_bound": theoretical_bounds.success_upper_bound,
            "failed_conditions": theoretical_bounds.failed_conditions,
            "bounds": (
                {
                    "name": "mission.minimum-time-s",
                    "value": theoretical_bounds.mission_minimum_time_s,
                },
                {
                    "name": "upload.minimum-time-s",
                    "value": theoretical_bounds.upload_minimum_time_s,
                },
                {
                    "name": "imaging.projected-defect-pixels",
                    "value": theoretical_bounds.projected_defect_pixels,
                },
                {
                    "name": "recall.upper-bound",
                    "value": theoretical_bounds.recall_upper_bound,
                },
                {
                    "name": "detection-f1.upper-bound",
                    "value": theoretical_bounds.detection_f1_upper_bound,
                },
            ),
        },
        "artifact_requirements": [
            requirement.model_dump(mode="json") for requirement in artifact_requirements
        ],
        "verification_outputs": [
            requirement.model_dump(mode="json")
            for requirement in task.verifier.output_artifacts
        ],
        "overrides": (),
    }
    run_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ResolvedRunSpec(run_id=run_id, **payload)


def _time(tick: int) -> SimulationTime:
    return SimulationTime(tick=tick, sim_time_ns=tick * 1_000_000_000)


def _completed_state(
    package: InspectionTaskPackage,
    *,
    run_id: str = RUN_ID,
    report_payload_digest: str | None = None,
):
    if report_payload_digest is None:
        report_payload_digest = _report_payload_digest(run_id)
    commands = (
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
            report_payload_digest=report_payload_digest,
            issued_at=_time(4),
        ),
        CompleteWorkOrderCommand(
            command_id="command.complete",
            work_order_id="wo.1",
            actor_id="business",
            issued_at=_time(5),
        ),
    )
    service = InspectionBusinessService(package, run_id=run_id)
    for command in commands:
        service.apply(BusinessCommandEnvelope(run_id=run_id, command=command))
    return service.state, service.history


def _submitted_state(
    package: InspectionTaskPackage,
    *,
    run_id: str = RUN_ID,
    report_payload_digest: str | None = None,
):
    if report_payload_digest is None:
        report_payload_digest = _report_payload_digest(run_id)
    service = InspectionBusinessService(package, run_id=run_id)
    commands = (
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
            report_payload_digest=report_payload_digest,
            issued_at=_time(4),
        ),
    )
    for command in commands:
        service.apply(BusinessCommandEnvelope(run_id=run_id, command=command))
    return service.state, service.history


class _InspectionProviderSession:
    def __init__(self, provider, run: ResolvedRunSpec):
        self._run = run
        self.manifest = ProviderManifest(
            provider_id=provider.provider_id,
            adapter=provider.adapter,
            implementation=provider.workload.implementation,
            runtime_image=provider.workload.runtime.image,
            config_digest=provider.config.file.sha256,
            capabilities=provider.capabilities,
            protocol_schema=provider.protocol_schema,
            artifact_requirements=provider.artifact_requirements,
        )

    async def prepare(self) -> None:
        return None

    async def reset(self, *, seed: int) -> StepReceipt:
        return StepReceipt(
            run_id=self._run.run_id,
            provider_id=self.manifest.provider_id,
            reached=_time(0),
            state_digest=hashlib.sha256(
                f"reset:{self.manifest.provider_id}:{seed}".encode()
            ).hexdigest(),
        )

    def _dynamic_sample(self, request) -> StateSample:
        entity = next(
            entity
            for entity in self._run.scenario.entities
            if entity.owner_id == self.manifest.provider_id
            and entity.state == "dynamic"
        )
        fields = {
            "schema_version": "aero-bench.state-sample/v1",
            "run_id": self._run.run_id,
            "scenario_digest": self._run.scenario.scenario_digest,
            "at": request.target,
            "stage": "motion",
            "entity_id": entity.entity_id,
            "provider_id": self.manifest.provider_id,
            "sample_kind": "dynamic",
            "pose": entity.initial_pose,
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
            "mode": "HOLD",
            "armed": True,
            "battery": None,
            "health": None,
            "contacts": (),
            "attributes": (),
        }
        candidate = StateSample.model_construct(
            **fields,
            sample_digest="0" * 64,
        )
        return StateSample(
            **fields,
            sample_digest=state_sample_digest_value(candidate),
        )

    def _sensor_frame_event(self, request) -> tuple[ProviderEvent, ...]:
        if (
            self.manifest.provider_id != "observation.provider"
            or request.target.tick != 4
        ):
            return ()
        return (
            ProviderEvent(
                provider_id=self.manifest.provider_id,
                event_id="public.sensor-frame",
                time=request.target,
                payload_schema_id="sensor.frame_ref.public.v2",
                payload=tuple(
                    NamedValue(name=name, value=value)
                    for name, value in sorted(
                        {
                            "artifact_id": "artifact.camera-frame-data",
                            "camera_id": "camera.1",
                            "camera_pose_sha256": FILE_DIGEST,
                            "capture_pose_source": "gazebo.pose.private_digest",
                            "engine_sim_time_ns": 13_000_000_000,
                            "frame_id": FRAME_ID,
                            "height": 1,
                            "image_sha256": IMAGE_SHA256,
                            "logical_origin_engine_ns": 10_000_000_000,
                            "media_type": "image/png",
                            "observation_id": "obs.1",
                            "payload_digest": OBSERVATION_PAYLOAD_DIGEST,
                            "selector": f"frames/{FRAME_ID}",
                            "size_bytes": len(IMAGE_BYTES),
                            "source": "gazebo.camera",
                            "target_pose_sha256": FILE_DIGEST,
                            "vehicle_id": "vehicle.1",
                            "width": 1,
                        }.items()
                    )
                ),
            ),
        )

    async def step_stage(self, request):
        samples = (self._dynamic_sample(request),) if request.stage == "motion" else ()
        contribution_fields = {
            "schema_version": "aero-bench.scene-contribution/v1",
            "run_id": self._run.run_id,
            "scenario_digest": self._run.scenario.scenario_digest,
            "at": request.target,
            "stage": request.stage,
            "provider_id": self.manifest.provider_id,
            "samples": samples,
            "attribute_updates": (),
        }
        contribution_candidate = SceneContribution.model_construct(
            **contribution_fields,
            payload_digest="0" * 64,
            contribution_digest="0" * 64,
        )
        payload_digest = scene_contribution_payload_digest_value(contribution_candidate)
        contribution_candidate = contribution_candidate.model_copy(
            update={"payload_digest": payload_digest}
        )
        contribution = SceneContribution(
            **contribution_fields,
            payload_digest=payload_digest,
            contribution_digest=scene_contribution_digest_value(contribution_candidate),
        )
        receipt = StepReceipt(
            run_id=self._run.run_id,
            provider_id=self.manifest.provider_id,
            reached=request.target,
            state_digest=hashlib.sha256(
                f"state:{self.manifest.provider_id}:{request.target.tick}".encode()
            ).hexdigest(),
            events=self._sensor_frame_event(request),
        )
        result_fields = {
            "schema_version": "aero-bench.provider-stage-result/v1",
            "run_id": self._run.run_id,
            "scenario_digest": self._run.scenario.scenario_digest,
            "provider_id": self.manifest.provider_id,
            "target": request.target,
            "stage": request.stage,
            "step_receipt": receipt,
            "step_receipt_digest": step_receipt_digest_value(receipt),
            "contribution": contribution,
            "predecessor_barriers": getattr(request, "predecessor_barriers", ()),
        }
        if request.stage == "motion":
            return MotionStageResult(**result_fields)
        return BusinessEnvironmentStageResult(
            **result_fields,
            input_scene_state_digest=request.scene_state_digest,
        )

    async def handle_command(self, request):
        raise AssertionError("inspection verifier fixture does not route commands")

    async def finalize(self, request):
        return fake_finalization_receipt(self.manifest, request)

    async def snapshot_digest(self) -> str:
        return hashlib.sha256(
            f"snapshot:{self.manifest.provider_id}".encode()
        ).hexdigest()

    async def shutdown(self) -> None:
        return None


async def _runtime_evidence(
    run: ResolvedRunSpec,
    business_history,
):
    ledger = EventLedger(run_id=run.run_id)
    coordinator = HarnessCoordinator(
        run=run,
        providers={
            provider.provider_id: _InspectionProviderSession(provider, run)
            for provider in run.environment.providers
        },
        ledger=ledger,
    )
    await coordinator.prepare()
    history_by_tick = {
        record.transition.time.tick: record for record in business_history
    }
    business_bindings = []
    for tick in range(1, 6):
        await coordinator.submit_agent_turn(
            AgentTurnCompletion(
                schema_version="aero-bench.agent-turn-completion/v1",
                run_id=run.run_id,
                agent_id="agent.1",
                completion_id=f"turn.advance.{tick}",
                at=coordinator.current,
                disposition="advance",
                command_ids=(),
                observation_ids=(),
            )
        )
        history_record = history_by_tick.get(tick)
        if history_record is not None:
            ledger_record = ledger.append_event(
                source="business",
                event_type="business.transition",
                time=history_record.transition.time,
                payload=(
                    NamedValue(
                        name="schema_id",
                        value="aero-bench.business-history/v1",
                    ),
                    NamedValue(
                        name="business_history_sequence",
                        value=history_record.sequence,
                    ),
                    NamedValue(
                        name="business_history_record_hash",
                        value=history_record.record_hash,
                    ),
                    NamedValue(
                        name="command_id",
                        value=history_record.command.command.command_id,
                    ),
                ),
            )
            business_bindings.append(
                BusinessLedgerBinding(
                    business_history_sequence=history_record.sequence,
                    business_history_record_hash=history_record.record_hash,
                    command_id=history_record.command.command.command_id,
                    ledger_sequence=ledger_record.sequence,
                    ledger_event_hash=ledger_record.event_hash,
                )
            )
        if tick == 3:
            ledger.append_event(
                source="gateway",
                source_kind="gateway",
                workload_id="gateway",
                event_type="observation.validated",
                time=_time(3),
                payload=(
                    NamedValue(
                        name="payload_digest",
                        value=OBSERVATION_PAYLOAD_DIGEST,
                    ),
                ),
                payload_schema_id="inspection.observation.v1",
                interaction_type="agent.observation.v1",
                correlation_id="observation.fixture",
                agent_id="agent.1",
                provider_id="observation.provider",
                observation_id="obs.1",
            )
    await coordinator.submit_agent_turn(
        AgentTurnCompletion(
            schema_version="aero-bench.agent-turn-completion/v1",
            run_id=run.run_id,
            agent_id="agent.1",
            completion_id="turn.finished",
            at=coordinator.current,
            disposition="finished",
            command_ids=(),
            observation_ids=(),
        )
    )
    return ledger, coordinator.scene_state_history, tuple(business_bindings)


def _evidence(
    package: InspectionTaskPackage,
    root,
    *,
    run: ResolvedRunSpec,
    business_state=None,
    business_history=None,
    report_payload: tuple[InspectionReportPayload, ...] | None = None,
    provider_config_digest: str | None = None,
) -> InspectionEvidenceBundle:
    run_id = run.run_id
    if provider_config_digest is None:
        provider_config_digest = _provider_config_digest(
            package, "observation.provider"
        )
    artifact_values = (
        (
            "artifact.business",
            "business.state",
            "business",
            "private",
            "business_state",
        ),
        ("artifact.event-log", "event.log", "harness", "private", "event_log"),
        (
            "artifact.trajectory",
            "trajectory",
            "observation.provider",
            "private",
            "trajectory",
        ),
        (
            "artifact.network",
            "network.delivery",
            "observation.provider",
            "private",
            "delivery",
        ),
        (
            "artifact.observation",
            "observation.metadata",
            "observation.provider",
            "private",
            "observation",
        ),
        (
            "artifact.sensor-frames",
            "sensor-frame",
            "observation.provider",
            "public",
            "sensor_frame",
        ),
        (
            "artifact.camera-frame-data",
            "camera-frame-data",
            "observation.provider",
            "public",
            "camera_frame_data",
        ),
        ("artifact.truth", "truth", "bundle", "private", "truth"),
        ("artifact.detection", "detection", "agent.1", "public", "detection"),
        ("artifact.report", "agent.report", "agent.1", "public", "report"),
        (
            "artifact.bounds",
            "theoretical.bounds",
            "business",
            "public",
            "theoretical_bounds",
        ),
        (
            "artifact.scene-states",
            "scene.state-history",
            "harness",
            "public",
            "scene_state_history",
        ),
    )
    root.mkdir()
    _write_package_bundle(package, _bundle_root(root))
    requirements_by_id = {
        requirement.artifact_id: requirement
        for requirement in package.artifact_requirements
    }
    report_records = (
        report_payload if report_payload is not None else (_report(run_id=run_id),)
    )
    report_payload_digest = hashlib.sha256(
        canonical_json_bytes(
            [record.model_dump(mode="json") for record in report_records]
        )
    ).hexdigest()
    if business_state is None:
        business_state, business_history = _completed_state(
            package,
            run_id=run_id,
            report_payload_digest=report_payload_digest,
        )
    elif business_history is None:
        raise AssertionError("business_history is required with business_state")
    ledger, scene_states, business_ledger_bindings = asyncio.run(
        _runtime_evidence(run, business_history)
    )
    event_chain_root = ledger.chain_root
    business_history_root = (
        business_history[-1].record_hash if business_history else FILE_DIGEST
    )
    view = AuthoritativeViewContext(
        source_provider_id="observation.provider",
        camera_id="camera.1",
        observation_id="obs.1",
        target_id="asset.1",
        time=_time(3),
        distance_m=10.0,
        view_angle_deg=20.0,
        camera_pose_digest=FILE_DIGEST,
        target_pose_digest=FILE_DIGEST,
        provider_config_digest=provider_config_digest,
    )
    bindings = tuple(
        EvidenceBinding(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            producer_id=producer_id,
            visibility=visibility,
            evidence_kind=(
                "theoretical_bounds"
                if evidence_kind == "theoretical_bounds"
                else evidence_kind
            ),
        )
        for artifact_id, artifact_type, producer_id, visibility, evidence_kind in artifact_values
    )
    draft_artifacts = tuple(
        ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            producer_id=producer_id,
            visibility=visibility,
            relative_path=requirements_by_id[artifact_id].relative_path,
            sha256=FILE_DIGEST,
            size_bytes=0,
        )
        for artifact_id, artifact_type, producer_id, visibility, _ in artifact_values
    )
    draft_seal_body = {
        "schema_version": "aero-bench.seal/v2",
        "run_id": run_id,
        "attempt_id": ATTEMPT_ID,
        "execution_scope": "formal_benchmark",
        "artifacts": [item.model_dump(mode="json") for item in draft_artifacts],
        "event_chain_root": event_chain_root,
    }
    draft = InspectionEvidenceBundle(
        run_id=run_id,
        seal=SealManifest(
            **draft_seal_body,
            manifest_digest=hashlib.sha256(
                json.dumps(
                    draft_seal_body,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        ),
        bindings=bindings,
        event_ledger=EventLedgerEvidence(
            source_artifact_id="artifact.event-log",
            records=ledger.records,
        ),
        scene_states=scene_states,
        business_state=BusinessStateEvidence(
            source_artifact_id="artifact.business",
            event_chain_root=event_chain_root,
            business_history_root=business_history_root,
            history=business_history,
            state=business_state,
        ),
        business_ledger_bindings=tuple(business_ledger_bindings),
        theoretical_bounds=TheoreticalBoundsEvidence(
            source_artifact_id="artifact.bounds",
            run_id=run_id,
            bounds=calculate_theoretical_bounds(
                package.bounds,
                network_delivery_required=package.network_delivery_required,
            ),
            provider_configs=_resolution(package).provider_configs,
        ),
        trajectory=(
            TrajectoryEvidence(
                source_artifact_id="artifact.trajectory",
                run_id=run_id,
                work_order_id="wo.1",
                target_id="asset.1",
                vehicle_id="vehicle.1",
                time=_time(2),
                position_wgs84=TrajectoryWgs84Position(
                    longitude_deg=8.0,
                    latitude_deg=47.0,
                    altitude_m=500.0,
                ),
                target_position_wgs84=TrajectoryWgs84Position(
                    longitude_deg=8.0,
                    latitude_deg=47.0,
                    altitude_m=500.5,
                ),
                orientation=TrajectoryOrientation(qw=1.0, qx=0.0, qy=0.0, qz=0.0),
                linear_velocity_enu_mps=TrajectoryLinearVelocity(
                    east_mps=0.0,
                    north_mps=0.0,
                    up_mps=0.0,
                ),
                flight_mode="hold",
                armed=True,
                battery_percent=80.0,
                collision_contact=False,
                distance_to_target_m=0.5,
            ),
        ),
        deliveries=(
            DeliveryEvidence(
                source_artifact_id="artifact.network",
                run_id=run_id,
                work_order_id="wo.1",
                message_id="message.1",
                payload_digest=report_payload_digest,
                sent_at=_time(4),
                delivered_at=_time(5),
            ),
        ),
        observations=(
            ObservationMetadata(
                schema_version="aero-bench.inspection-observation-metadata/v1",
                source_artifact_id="artifact.observation",
                run_id=run_id,
                work_order_id="wo.1",
                observation_id="obs.1",
                target_id="asset.1",
                time=_time(3),
                camera_id="camera.1",
                frame_id=FRAME_ID,
                selector=f"frames/{FRAME_ID}",
                vehicle_id="vehicle.1",
                engine_sim_time_ns=13_000_000_000,
                logical_origin_engine_ns=10_000_000_000,
                image_sha256=IMAGE_SHA256,
                size_bytes=len(IMAGE_BYTES),
                width=1,
                height=1,
                media_type="image/png",
                source="gazebo.camera",
                capture_pose_source="gazebo.pose.private_digest",
                camera_pose_sha256=FILE_DIGEST,
                target_pose_sha256=FILE_DIGEST,
                payload_digest=OBSERVATION_PAYLOAD_DIGEST,
                view=view,
                visual_kind="gazebo_rgb",
                visual_status="captured",
            ),
        ),
        sensor_frames=(
            PublicSensorFrameRecord(
                schema_version="aero-bench.public-sensor-frame-artifact/v2",
                source_artifact_id="artifact.sensor-frames",
                run_id=run_id,
                frame_id=FRAME_ID,
                selector=f"frames/{FRAME_ID}",
                observation_id="obs.1",
                time=_time(3),
                camera_id="camera.1",
                vehicle_id="vehicle.1",
                engine_sim_time_ns=13_000_000_000,
                logical_origin_engine_ns=10_000_000_000,
                image_sha256=IMAGE_SHA256,
                size_bytes=len(IMAGE_BYTES),
                width=1,
                height=1,
                media_type="image/png",
                source="gazebo.camera",
                capture_pose_source="gazebo.pose.private_digest",
                camera_pose_sha256=FILE_DIGEST,
                target_pose_sha256=FILE_DIGEST,
                payload_digest=OBSERVATION_PAYLOAD_DIGEST,
            ),
        ),
        camera_frames=(
            CameraFrameDataRecord(
                source_artifact_id="artifact.camera-frame-data",
                frame_id=FRAME_ID,
                image_sha256=IMAGE_SHA256,
                size_bytes=len(IMAGE_BYTES),
                width=1,
                height=1,
                media_type="image/png",
                png_bytes=IMAGE_BYTES,
            ),
        ),
        truth=(
            DefectTruth(
                source_artifact_id="artifact.truth",
                defect_id="defect.1",
                target_id="asset.1",
                geometrically_visible=True,
            ),
        ),
        detections=(
            DefectDetection(
                source_artifact_id="artifact.detection",
                run_id=run_id,
                work_order_id="wo.1",
                observation_id="obs.1",
                frame_id=FRAME_ID,
                image_sha256=IMAGE_SHA256,
                defect_id="defect.1",
                target_id="asset.1",
            ),
        ),
        report=report_records,
    )
    payloads = {
        kind: (
            scene_state_jsonl_bytes(draft.scene_states)
            if kind == "scene_state_history"
            else evidence_payload_bytes(draft, kind)
        )
        for kind in (
            "event_log",
            "business_state",
            "trajectory",
            "delivery",
            "observation",
            "sensor_frame",
            "camera_frame_data",
            "truth",
            "detection",
            "report",
            "theoretical_bounds",
            "scene_state_history",
        )
    }
    artifacts = []
    for (
        artifact_id,
        artifact_type,
        producer_id,
        visibility,
        evidence_kind,
    ) in artifact_values:
        content = payloads[evidence_kind]
        path = root / requirements_by_id[artifact_id].relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        artifacts.append(
            ArtifactRecord(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                producer_id=producer_id,
                visibility=visibility,
                relative_path=requirements_by_id[artifact_id].relative_path,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
        )
    artifacts_tuple = tuple(artifacts)
    seal_body = {
        "schema_version": "aero-bench.seal/v2",
        "run_id": run_id,
        "attempt_id": ATTEMPT_ID,
        "execution_scope": "formal_benchmark",
        "artifacts": [item.model_dump(mode="json") for item in artifacts_tuple],
        "event_chain_root": event_chain_root,
    }
    seal = SealManifest(
        **seal_body,
        manifest_digest=hashlib.sha256(
            json.dumps(
                seal_body,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )
    (root / "seal-manifest.json").write_bytes(
        canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
    )
    return draft.model_copy(update={"seal": seal})


def _seal_with_event_root(
    evidence: InspectionEvidenceBundle,
    event_chain_root: str,
) -> SealManifest:
    body = {
        "schema_version": evidence.seal.schema_version,
        "run_id": evidence.seal.run_id,
        "attempt_id": evidence.seal.attempt_id,
        "execution_scope": evidence.seal.execution_scope,
        "artifacts": [
            artifact.model_dump(mode="json") for artifact in evidence.seal.artifacts
        ],
        "event_chain_root": event_chain_root,
    }
    return SealManifest(
        **body,
        manifest_digest=hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )


def _reseal_artifact(
    evidence: InspectionEvidenceBundle,
    root,
    *,
    artifact_id: str,
    content: bytes,
) -> SealManifest:
    artifact = next(
        item for item in evidence.seal.artifacts if item.artifact_id == artifact_id
    )
    (root / artifact.relative_path).write_bytes(content)
    artifacts = tuple(
        item.model_copy(
            update={
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
        if item.artifact_id == artifact_id
        else item
        for item in evidence.seal.artifacts
    )
    body = {
        "schema_version": evidence.seal.schema_version,
        "run_id": evidence.seal.run_id,
        "attempt_id": evidence.seal.attempt_id,
        "execution_scope": evidence.seal.execution_scope,
        "artifacts": [item.model_dump(mode="json") for item in artifacts],
        "event_chain_root": evidence.seal.event_chain_root,
    }
    seal = SealManifest(
        **body,
        manifest_digest=hashlib.sha256(
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )
    (root / "seal-manifest.json").write_bytes(
        canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
    )
    return seal


def _tampered_lifecycle_ledger_bytes(
    evidence: InspectionEvidenceBundle,
    mutation: str,
) -> bytes:
    run_id = evidence.event_ledger.records[0].event.run_id
    ledger = EventLedger(run_id=run_id)
    for record in evidence.event_ledger.records:
        event = record.event
        if mutation == "missing_terminal" and event.event_type == "run.completed":
            continue
        if (
            mutation == "missing_identity"
            and event.event_type == "runtime.identity"
            and event.source == "business"
        ):
            continue
        if (
            mutation == "missing_reset"
            and event.event_type == "provider.reset"
            and event.source == "business"
        ):
            continue
        if mutation == "missing_barrier" and event.event_type == "barrier.ready":
            continue
        payload = event.payload
        if mutation == "wrong_start_scope" and event.event_type == "run.started":
            payload = (
                NamedValue(
                    name="execution_scope",
                    value="executor_validation",
                ),
            )
        ledger.append_event(
            source=event.source,
            source_kind=event.source_kind,
            workload_id=event.workload_id,
            event_type=event.event_type,
            time=event.time,
            payload=payload,
            payload_schema_id=event.payload_schema_id,
            correlation_id=event.correlation_id,
            visibility=event.visibility,
            frame_id=event.frame_id,
            agent_id=event.agent_id,
            provider_id=event.provider_id,
            vehicle_id=event.vehicle_id,
            entity_id=event.entity_id,
            command_id=event.command_id,
            observation_id=event.observation_id,
        )
    return ledger_jsonl_bytes(ledger.records)


def test_verifier_requires_strict_sealed_evidence_loading(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    verifier = _verifier(package)

    with pytest.raises(RuntimeError, match="verify_sealed"):
        verifier.verify(evidence, sealed_root=root)

    report = verifier.verify_sealed(
        bundle_root=_bundle_root(root),
        run=run,
        seal=evidence.seal,
        sealed_root=root,
    )
    assert report.status == "passed"
    assert report.coverage_complete
    assert report.theoretical_bounds.detection_f1_upper_bound == 1.0
    values = {metric.metric_id: metric.value for metric in report.metrics}
    assert values["inspection.arrival_rate"] == 1.0
    assert values["inspection.success_rate"] == 1.0
    assert (
        evidence.business_state.business_history_root != evidence.seal.event_chain_root
    )
    public_artifact_ids = {
        artifact.artifact_id
        for artifact in evidence.seal.artifacts
        if artifact.visibility == "public"
    }
    assert all(
        reference.artifact_id in public_artifact_ids
        for metric in report.metrics
        for reference in metric.evidence
    )


def test_core_bindings_accept_only_loader_validated_extra_seal_artifacts(
    monkeypatch,
) -> None:
    requirement = InspectionArtifactRequirement(
        artifact_id="artifact.core",
        artifact_type="event.log",
        producer_id="harness",
        visibility="private",
        relative_path="core.json",
        max_size_bytes=1024,
        source_asset_id=None,
        evidence_kind="event_log",
    )
    core = ArtifactRecord(
        artifact_id=requirement.artifact_id,
        artifact_type=requirement.artifact_type,
        producer_id=requirement.producer_id,
        visibility=requirement.visibility,
        relative_path=requirement.relative_path,
        sha256="a" * 64,
        size_bytes=1,
    )
    extra = ArtifactRecord(
        artifact_id="artifact.provider-extra",
        artifact_type="provider.evidence",
        producer_id="traffic",
        visibility="private",
        relative_path="provider.json",
        sha256="b" * 64,
        size_bytes=1,
    )

    def make_seal(artifacts: tuple[ArtifactRecord, ...]) -> SealManifest:
        body = {
            "schema_version": "aero-bench.seal/v2",
            "run_id": RUN_ID,
            "attempt_id": ATTEMPT_ID,
            "execution_scope": "formal_benchmark",
            "artifacts": [item.model_dump(mode="json") for item in artifacts],
            "event_chain_root": "c" * 64,
        }
        return SealManifest(
            **body,
            manifest_digest=hashlib.sha256(canonical_json_bytes(body)).hexdigest(),
        )

    binding = EvidenceBinding(
        artifact_id=requirement.artifact_id,
        artifact_type=requirement.artifact_type,
        producer_id=requirement.producer_id,
        visibility=requirement.visibility,
        evidence_kind=requirement.evidence_kind,
    )
    verifier = object.__new__(InspectionVerifier)
    verifier.package = SimpleNamespace(artifact_requirements=(requirement,))
    monkeypatch.setattr(
        InspectionVerifier,
        "_validate_payload_digests",
        lambda *_args, **_kwargs: None,
    )
    evidence = SimpleNamespace(
        run_id=RUN_ID,
        seal=make_seal((core, extra)),
        business_state=SimpleNamespace(event_chain_root="c" * 64),
        bindings=(binding,),
    )

    verifier._validate_seal_and_bindings(evidence)

    with pytest.raises(ValueError, match="bindings must contain exactly"):
        verifier._validate_seal_and_bindings(
            SimpleNamespace(
                **{
                    **vars(evidence),
                    "bindings": (
                        binding,
                        binding.model_copy(update={"artifact_id": extra.artifact_id}),
                    ),
                }
            )
        )
    with pytest.raises(ValueError, match="missing a declared"):
        verifier._validate_seal_and_bindings(
            SimpleNamespace(
                **{
                    **vars(evidence),
                    "seal": make_seal((extra,)),
                }
            )
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_terminal",
        "wrong_start_scope",
        "missing_identity",
        "missing_reset",
        "missing_barrier",
    ),
)
def test_strict_loader_rejects_incomplete_formal_runtime_lifecycle(
    tmp_path,
    mutation: str,
) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / mutation
    evidence = _evidence(package, root, run=run)
    seal = _reseal_artifact(
        evidence,
        root,
        artifact_id="artifact.event-log",
        content=_tampered_lifecycle_ledger_bytes(evidence, mutation),
    )

    with pytest.raises(ValueError, match="authoritative runtime ledger"):
        _load_sealed(
            package=package,
            run=run,
            seal=seal,
            sealed_root=root,
        )


def test_evidence_bundle_requires_explicit_report_evidence(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    evidence = _evidence(package, tmp_path / "sealed", run=run)
    raw = evidence.model_dump(mode="python")
    raw.pop("report")

    with pytest.raises(ValidationError, match="report"):
        InspectionEvidenceBundle.model_validate(raw)


def test_strict_loader_reconstructs_only_the_sealed_evidence_inventory(
    tmp_path,
) -> None:
    package = _package()
    root = tmp_path / "sealed"
    run = _resolved_run(package)
    evidence = _evidence(package, root, run=run)

    loaded = _load_sealed(
        package=package,
        run=run,
        seal=evidence.seal,
        sealed_root=root,
    )

    assert loaded.seal == evidence.seal
    assert loaded.business_ledger_bindings == evidence.business_ledger_bindings
    assert loaded.report == evidence.report
    assert (root / "report.json").read_bytes() == evidence_payload_bytes(
        evidence, "report"
    )
    assert "run_id" not in loaded.truth[0].model_dump()
    assert (
        _verifier(package)
        .verify_sealed(
            bundle_root=_bundle_root(root),
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        )
        .status
        == "passed"
    )


def test_loaded_evidence_revalidation_preserves_validated_graphs(tmp_path, monkeypatch):
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    loaded = sealed_evidence_module._load_verified_sealed_inspection_evidence(
        package=package, bundle_root=_bundle_root(root), run=run,
        seal=evidence.seal, sealed_root=root,
    )
    expected = InspectionEvidenceBundle.model_validate(loaded.evidence.model_dump(mode="python"))

    def reject_full_dump(*args, **kwargs):
        raise AssertionError("revalidation must not duplicate the complete evidence graph")

    monkeypatch.setattr(InspectionEvidenceBundle, "model_dump", reject_full_dump)
    actual = _verifier(package)._validate_loaded_evidence(loaded)
    assert actual == expected
    assert actual.event_ledger is loaded.evidence.event_ledger
    assert all(
        actual_state is loaded_state
        for actual_state, loaded_state in zip(actual.scene_states, loaded.evidence.scene_states, strict=True)
    )


def test_strict_loader_rejects_noncanonical_wrapped_and_invalid_json(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    cases = (
        ("business", "noncanonical"),
        ("report", "wrapper"),
        ("trajectory", "trailing"),
        ("truth", "duplicate_key"),
        ("detection", "nonfinite"),
        ("observation", "invalid_utf8"),
    )

    for artifact_stem, tampering in cases:
        root = tmp_path / f"sealed-{tampering}"
        evidence = _evidence(package, root, run=run)
        if tampering == "noncanonical":
            content = (root / "business.json").read_bytes().replace(b":", b": ", 1)
        elif tampering == "wrapper":
            content = b'{"records":[]}'
        elif tampering == "trailing":
            content = b"[]{}"
        elif tampering == "duplicate_key":
            content = (
                b'[{"source_artifact_id":"artifact.truth",'
                b'"source_artifact_id":"artifact.truth"}]'
            )
        elif tampering == "nonfinite":
            content = b"[NaN]"
        else:
            content = b"\xff"
        seal = _reseal_artifact(
            evidence,
            root,
            artifact_id=f"artifact.{artifact_stem}",
            content=content,
        )

        with pytest.raises(ValueError):
            _load_sealed(
                package=package,
                run=run,
                seal=seal,
                sealed_root=root,
            )


def test_strict_loader_rejects_duplicate_typed_evidence(tmp_path) -> None:
    package = _package()
    root = tmp_path / "sealed"
    run = _resolved_run(package)
    evidence = _evidence(package, root, run=run)
    duplicate_detection = canonical_json_bytes(
        [
            evidence.detections[0].model_dump(mode="json"),
            evidence.detections[0].model_dump(mode="json"),
        ]
    )
    seal = _reseal_artifact(
        evidence,
        root,
        artifact_id="artifact.detection",
        content=duplicate_detection,
    )

    with pytest.raises(ValueError, match="repeats a defect"):
        _load_sealed(
            package=package,
            run=run,
            seal=seal,
            sealed_root=root,
        )


def test_strict_loader_rejects_undeclared_link_and_digest_tampering(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)

    extra_root = tmp_path / "extra"
    extra_evidence = _evidence(package, extra_root, run=run)
    (extra_root / "undeclared.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        _load_sealed(
            package=package,
            run=run,
            seal=extra_evidence.seal,
            sealed_root=extra_root,
        )

    digest_root = tmp_path / "digest"
    digest_evidence = _evidence(package, digest_root, run=run)
    (digest_root / "delivery.json").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        _load_sealed(
            package=package,
            run=run,
            seal=digest_evidence.seal,
            sealed_root=digest_root,
        )

    link_root = tmp_path / "link"
    link_evidence = _evidence(package, link_root, run=run)
    target = tmp_path / "link-target.json"
    target.write_bytes((link_root / "delivery.json").read_bytes())
    (link_root / "delivery.json").unlink()
    (link_root / "delivery.json").symlink_to(target)
    with pytest.raises(ValueError):
        _load_sealed(
            package=package,
            run=run,
            seal=link_evidence.seal,
            sealed_root=link_root,
        )


def test_strict_loader_rejects_duplicate_run_evidence_and_invalid_manifest(
    tmp_path,
) -> None:
    package = _package()
    root = tmp_path / "sealed"
    run = _resolved_run(package)
    evidence = _evidence(package, root, run=run)
    duplicate_run = run.model_copy(
        update={
            "artifact_requirements": (
                *run.artifact_requirements,
                run.artifact_requirements[0],
            )
        }
    )
    with pytest.raises(ValueError):
        _load_sealed(
            package=package,
            run=duplicate_run,
            seal=evidence.seal,
            sealed_root=root,
        )

    invalid_manifest = SealManifest.model_construct(
        schema_version=evidence.seal.schema_version,
        run_id=evidence.seal.run_id,
        execution_scope=evidence.seal.execution_scope,
        artifacts=evidence.seal.artifacts,
        event_chain_root=evidence.seal.event_chain_root,
        manifest_digest="f" * 64,
    )
    with pytest.raises(ValueError):
        _load_sealed(
            package=package,
            run=run,
            seal=invalid_manifest,
            sealed_root=root,
        )


def test_strict_loader_rejects_tampered_control_manifest_and_nested_link(
    tmp_path,
) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "control"
    evidence = _evidence(package, root, run=run)
    root_link = tmp_path / "control-link"
    root_link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        _load_sealed(
            package=package,
            run=run,
            seal=evidence.seal,
            sealed_root=root_link,
        )
    (root / "seal-manifest.json").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="control manifest"):
        _load_sealed(
            package=package,
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        )

    raw_package = _package().model_dump(mode="json")
    delivery_requirement = next(
        item
        for item in raw_package["artifact_requirements"]
        if item["artifact_id"] == "artifact.network"
    )
    delivery_requirement["relative_path"] = "nested/delivery.json"
    nested_package = InspectionTaskPackage.model_validate(raw_package)
    nested_run = _resolved_run(nested_package)
    nested_root = tmp_path / "nested"
    nested_evidence = _evidence(
        nested_package,
        nested_root,
        run=nested_run,
    )
    target_directory = tmp_path / "delivery-target"
    (nested_root / "nested").rename(target_directory)
    (nested_root / "nested").symlink_to(target_directory, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        _load_sealed(
            package=nested_package,
            run=nested_run,
            seal=nested_evidence.seal,
            sealed_root=nested_root,
        )


def test_strict_loader_rejects_regular_file_replaced_with_fifo(
    tmp_path, monkeypatch
) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    original_inventory = sealed_evidence_module._root_inventory
    inventory_calls = 0

    def replace_delivery_after_inventory(root_descriptor: int):
        nonlocal inventory_calls
        inventory = original_inventory(root_descriptor)
        inventory_calls += 1
        if inventory_calls == 1:
            delivery_path = root / "delivery.json"
            delivery_path.unlink()
            os.mkfifo(delivery_path)
        return inventory

    monkeypatch.setattr(
        sealed_evidence_module,
        "_root_inventory",
        replace_delivery_after_inventory,
    )

    with pytest.raises(ValueError, match="not a regular file"):
        _load_sealed(
            package=package,
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        )


def test_strict_loader_rejects_files_added_while_reading(tmp_path, monkeypatch) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    original_read = sealed_evidence_module._iter_artifact_content
    mutated = False

    def add_file_after_last_artifact(*args, **kwargs):
        nonlocal mutated
        yield from original_read(*args, **kwargs)
        artifact = kwargs.get("artifact")
        if artifact is None and len(args) > 1:
            artifact = args[1]
        if not mutated and artifact.artifact_id == "artifact.scene-states":
            (root / "late-addition.json").write_bytes(b"{}")
            mutated = True

    monkeypatch.setattr(
        sealed_evidence_module,
        "_iter_artifact_content",
        add_file_after_last_artifact,
    )

    with pytest.raises(ValueError, match="root changed while being read"):
        _load_sealed(
            package=package,
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        )


def test_strict_loader_rejects_files_removed_while_reading(
    tmp_path, monkeypatch
) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    original_read = sealed_evidence_module._iter_artifact_content
    mutated = False

    def remove_file_after_last_artifact(*args, **kwargs):
        nonlocal mutated
        yield from original_read(*args, **kwargs)
        artifact = kwargs.get("artifact")
        if artifact is None and len(args) > 1:
            artifact = args[1]
        if not mutated and artifact.artifact_id == "artifact.scene-states":
            (root / "delivery.json").unlink()
            mutated = True

    monkeypatch.setattr(
        sealed_evidence_module,
        "_iter_artifact_content",
        remove_file_after_last_artifact,
    )

    with pytest.raises(ValueError, match="root changed while being read"):
        _load_sealed(
            package=package,
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        )


def test_strict_loader_revalidates_run_and_package_bindings(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)

    invalid_run_payload = {
        field_name: getattr(run, field_name)
        for field_name in ResolvedRunSpec.model_fields
    }
    invalid_run_payload["run_id"] = "f" * 64
    invalid_run = ResolvedRunSpec.model_construct(**invalid_run_payload)
    with pytest.raises(ValueError, match="resolved Inspection run is invalid"):
        _load_sealed(
            package=package,
            run=invalid_run,
            seal=evidence.seal,
            sealed_root=root,
        )

    incompatible_run = _resolved_run(package, truth_asset_digest="d" * 64)
    incompatible_root = tmp_path / "truth-digest"
    incompatible_evidence = _evidence(
        package,
        incompatible_root,
        run=incompatible_run,
    )
    with pytest.raises(ValueError, match="truth source asset digest"):
        _load_sealed(
            package=package,
            run=incompatible_run,
            seal=incompatible_evidence.seal,
            sealed_root=incompatible_root,
        )

    alternate_package_data = _package().model_dump(mode="json")
    alternate_package_data["goals"][0]["threshold"] = 0.5
    alternate_package = InspectionTaskPackage.model_validate(alternate_package_data)
    with pytest.raises(ValueError, match="pinned Inspection package"):
        _load_sealed(
            package=alternate_package,
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        )


def test_strict_loader_rejects_agent_visible_world_asset(tmp_path) -> None:
    package = _package()
    run = _resolved_run(
        package,
        world_asset_classification="public",
        world_asset_audiences=(AssetAudience(role="agent", workload_ids=("agent.1",)),),
    )
    root = tmp_path / "agent-visible-world"
    evidence = _evidence(package, root, run=run)

    with pytest.raises(ValueError, match="resolved Inspection context"):
        _load_sealed(
            package=package,
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        )


def test_strict_loader_binds_truth_artifact_to_private_source_asset(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    replacement_truth = DefectTruth(
        source_artifact_id="artifact.truth",
        defect_id="defect.2",
        target_id="asset.1",
        geometrically_visible=True,
    )
    seal = _reseal_artifact(
        evidence,
        root,
        artifact_id="artifact.truth",
        content=canonical_json_bytes([replacement_truth.model_dump(mode="json")]),
    )

    with pytest.raises(ValueError, match="immutable truth artifact digest"):
        _load_sealed(
            package=package,
            run=run,
            seal=seal,
            sealed_root=root,
        )


def test_formal_verification_requires_raw_pinned_package_configuration(
    tmp_path,
) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    altered_package = package.model_copy(
        update={
            "observations": (
                package.observations[0].model_copy(
                    update={
                        "trigger": package.observations[0].trigger.model_copy(
                            update={"max_distance_m": 19.0}
                        )
                    }
                ),
            )
        }
    )
    (_bundle_root(root) / "configs" / "inspection.json").write_bytes(
        _package_config_bytes(altered_package)
    )

    report = _verify_formally(
        _verifier(package),
        run=run,
        seal=evidence.seal,
        sealed_root=root,
    )

    assert report.status == "invalid"


def test_verifier_rejects_run_environment_config_disagreement(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package, business_config_digest="d" * 64)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)

    report = _verifier(package).verify_sealed(
        bundle_root=_bundle_root(root),
        run=run,
        seal=evidence.seal,
        sealed_root=root,
    )

    assert report.status == "invalid"


def test_strict_loader_rejects_repinned_provider_bound_disagreement(
    tmp_path,
) -> None:
    package = _package()
    config = {"inspection_bounds": package.bounds.model_dump(mode="json")}
    config["inspection_bounds"]["mission"]["deadline_s"] = 19.0
    config_bytes = canonical_json_bytes(config)
    run = _resolved_run(
        package,
        business_config_digest=hashlib.sha256(config_bytes).hexdigest(),
    )
    root = tmp_path / "repinned-bounds"
    evidence = _evidence(package, root, run=run)
    (_bundle_root(root) / "configs" / "business.json").write_bytes(config_bytes)

    with pytest.raises(ValueError, match="provider bounds disagree"):
        _load_sealed(
            package=package,
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        )


def test_strict_loader_rejects_duplicate_trajectory_delivery_and_observation(
    tmp_path,
) -> None:
    package = _package()
    run = _resolved_run(package)
    duplicate_cases = (
        ("trajectory", "artifact.trajectory", "trajectory"),
        ("delivery", "artifact.network", "deliveries"),
        ("observation", "artifact.observation", "observations"),
    )

    for name, artifact_id, attribute in duplicate_cases:
        root = tmp_path / name
        evidence = _evidence(package, root, run=run)
        record = getattr(evidence, attribute)[0]
        seal = _reseal_artifact(
            evidence,
            root,
            artifact_id=artifact_id,
            content=canonical_json_bytes(
                [record.model_dump(mode="json"), record.model_dump(mode="json")]
            ),
        )
        with pytest.raises(ValueError, match="repeats"):
            _load_sealed(
                package=package,
                run=run,
                seal=seal,
                sealed_root=root,
            )


def test_model_copy_observation_bypasses_cannot_reach_formal_verification(
    tmp_path,
) -> None:
    package = _package()
    run = _resolved_run(package)
    verifier = _verifier(package)
    invalid_updates = (
        {"payload_digest": "d" * 64},
        {"camera_id": "camera.other"},
    )

    for index, update in enumerate(invalid_updates):
        root = tmp_path / f"observation-{index}"
        evidence = _evidence(package, root, run=run)
        forged = evidence.model_copy(
            update={
                "observations": (evidence.observations[0].model_copy(update=update),)
            }
        )
        seal = _reseal_artifact(
            evidence,
            root,
            artifact_id="artifact.observation",
            content=evidence_payload_bytes(forged, "observation"),
        )
        with pytest.raises(RuntimeError, match="verify_sealed"):
            verifier.verify(forged, sealed_root=root)
        with pytest.raises(TypeError, match="loader-issued"):
            verifier._validate_loaded_evidence(forged)
        assert (
            verifier.verify_sealed(
                bundle_root=_bundle_root(root),
                run=run,
                seal=seal,
                sealed_root=root,
            ).status
            == "invalid"
        )


def test_verifier_binds_report_to_observation_and_detection_evidence(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    verifier = _verifier(package)
    report_updates = (
        {"detection_payload_digest": "d" * 64},
        {"observation_payload_digest": "e" * 64},
        {"detections": ()},
    )

    for index, update in enumerate(report_updates):
        root = tmp_path / f"sealed-{index}"
        report = _report(run_id=run.run_id).model_copy(update=update)
        evidence = _evidence(
            package,
            root,
            run=run,
            report_payload=(report,),
        )
        assert (
            _verify_formally(
                verifier,
                run=run,
                seal=evidence.seal,
                sealed_root=root,
            ).status
            == "invalid"
        )


def test_verifier_separates_global_ledger_and_business_history_roots(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    disguised = _seal_with_event_root(
        evidence,
        evidence.business_state.business_history_root,
    )
    (root / "seal-manifest.json").write_bytes(
        canonical_json_bytes(disguised.model_dump(mode="json")) + b"\n"
    )

    assert (
        _verify_formally(
            _verifier(package),
            run=run,
            seal=disguised,
            sealed_root=root,
        ).status
        == "invalid"
    )


def test_verifier_loaded_path_requires_loader_capability_and_root(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    verifier = _verifier(package)

    loaded = _load_sealed(
        package=package,
        run=run,
        seal=evidence.seal,
        sealed_root=root,
    )
    with pytest.raises(TypeError, match="loader-issued"):
        verifier._validate_loaded_evidence(loaded)
    shutil.rmtree(root)
    with pytest.raises(TypeError, match="loader-issued"):
        verifier._validate_loaded_evidence(loaded)
    assert (
        _verify_formally(
            verifier,
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        ).status
        == "invalid"
    )


def test_verifier_rejects_tampered_seal_and_missing_root(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    verifier = _verifier(package)
    assert (
        verifier.verify_sealed(
            bundle_root=_bundle_root(root),
            run=run,
            seal=evidence.seal,
            sealed_root=tmp_path / "missing",
        ).status
        == "invalid"
    )
    (root / "delivery.json").write_bytes(b"tampered")
    assert (
        verifier.verify_sealed(
            bundle_root=_bundle_root(root),
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        ).status
        == "invalid"
    )


def test_verifier_rejects_business_state_report_digest_mismatch(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    bad_digest = evidence.model_copy(
        update={
            "business_state": evidence.business_state.model_copy(
                update={
                    "state": evidence.business_state.state.model_copy(
                        update={
                            "work_orders": tuple(
                                item.model_copy(
                                    update={"report_payload_digest": "e" * 64}
                                )
                                for item in evidence.business_state.state.work_orders
                            )
                        }
                    )
                }
            )
        }
    )
    seal = _reseal_artifact(
        evidence,
        root,
        artifact_id="artifact.business",
        content=evidence_payload_bytes(bad_digest, "business_state"),
    )

    assert (
        _verify_formally(
            _verifier(package),
            run=run,
            seal=seal,
            sealed_root=root,
        ).status
        == "invalid"
    )


def test_typed_payload_must_match_sealed_file_bytes(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    seal = _reseal_artifact(
        evidence,
        root,
        artifact_id="artifact.business",
        content=b"business",
    )

    assert (
        _verifier(package)
        .verify_sealed(
            bundle_root=_bundle_root(root),
            run=run,
            seal=seal,
            sealed_root=root,
        )
        .status
        == "invalid"
    )


def test_completed_state_without_replay_history_is_invalid(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    initial = package.initial_business_state(run_id=run.run_id)
    direct_completed_data = initial.work_orders[0].model_dump()
    direct_completed_data.update(
        {
            "status": "completed",
            "version": 1,
            "claimed_by": "agent.1",
            "observation_id": "obs.1",
            "report_payload_digest": _report_payload_digest(run.run_id),
        }
    )
    direct_completed = initial.work_orders[0].__class__.model_validate(
        direct_completed_data
    )
    direct_state = InspectionBusinessState(
        run_id=run.run_id,
        current_time=_time(5),
        work_orders=(direct_completed,),
        version=1,
    )
    root = tmp_path / "sealed"
    evidence = _evidence(
        package,
        root,
        run=run,
        business_state=direct_state,
        business_history=(),
    )
    assert (
        _verify_formally(
            _verifier(package),
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        ).status
        == "invalid"
    )


def test_zero_success_ceiling_cannot_pass_success_goal(tmp_path) -> None:
    raw = _package().model_dump(mode="json")
    raw["bounds"]["mission"]["deadline_s"] = 1.0
    raw["goals"] = [
        {
            "goal_id": "goal.success",
            "metric_id": "inspection.success_rate",
            "operator": "ge",
            "threshold": 0.0,
            "evidence": "artifact",
            "parameters": [],
        }
    ]
    package = InspectionTaskPackage.model_validate(raw)
    run = _resolved_run(package)
    submitted_state, submitted_history = _submitted_state(
        package,
        run_id=run.run_id,
    )
    root = tmp_path / "sealed"
    evidence = _evidence(
        package,
        root,
        run=run,
        business_state=submitted_state,
        business_history=submitted_history,
    )
    report = _verify_formally(
        _verifier(package),
        run=run,
        seal=evidence.seal,
        sealed_root=root,
    )
    assert report.status == "failed"
    assert report.goals[0].passed is False


def test_goal_evidence_category_must_match_metric(tmp_path) -> None:
    raw = _package().model_dump(mode="json")
    raw["goals"][0]["evidence"] = "artifact"
    package = InspectionTaskPackage.model_validate(raw)
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    assert (
        _verify_formally(
            _verifier(package),
            run=run,
            seal=evidence.seal,
            sealed_root=root,
        ).status
        == "invalid"
    )


def test_verifier_rejects_bounds_not_from_resolved_environment() -> None:
    package = _package()
    tampered_bounds = package.bounds.model_copy(
        update={
            "mission": package.bounds.mission.model_copy(update={"deadline_s": 21.0})
        }
    )
    tampered_package = package.model_copy(update={"bounds": tampered_bounds})

    with pytest.raises(ValueError, match="resolved inspection bounds"):
        InspectionVerifier(tampered_package, resolved_bounds=_resolution(package))


def test_verifier_rejects_observation_from_another_provider_config(tmp_path) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(
        package,
        root,
        run=run,
        provider_config_digest="d" * 64,
    )

    report = _verify_formally(
        _verifier(package),
        run=run,
        seal=evidence.seal,
        sealed_root=root,
    )

    assert report.status == "invalid"


def test_unreported_observation_cannot_substitute_for_required_capture(
    tmp_path,
) -> None:
    package = _package()
    run = _resolved_run(package)
    root = tmp_path / "sealed"
    evidence = _evidence(package, root, run=run)
    required_record = evidence.observations[0].model_copy(
        update={
            "view": evidence.observations[0].view.model_copy(
                update={"distance_m": 100.0}
            )
        }
    )
    other_record = evidence.observations[0].model_copy(
        update={"observation_id": "obs.2"}
    )
    mixed_observations = evidence.model_copy(
        update={"observations": (required_record, other_record)}
    )
    seal = _reseal_artifact(
        evidence,
        root,
        artifact_id="artifact.observation",
        content=evidence_payload_bytes(mixed_observations, "observation"),
    )

    report = _verify_formally(
        _verifier(package),
        run=run,
        seal=seal,
        sealed_root=root,
    )
    assert report.status == "invalid"


def test_verifier_rejects_report_smaller_than_link_bound_minimum(tmp_path) -> None:
    package = _package()
    larger_minimum = package.bounds.link.model_copy(
        update={"minimum_payload_bytes": 100, "upload_deadline_s": 0.2}
    )
    constrained = package.model_copy(
        update={"bounds": package.bounds.model_copy(update={"link": larger_minimum})}
    )
    run = _resolved_run(constrained)
    root = tmp_path / "sealed"
    evidence = _evidence(constrained, root, run=run)

    report = _verify_formally(
        _verifier(constrained),
        run=run,
        seal=evidence.seal,
        sealed_root=root,
    )

    assert report.status == "invalid"
