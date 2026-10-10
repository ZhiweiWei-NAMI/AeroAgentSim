from __future__ import annotations

import base64
import hashlib
import struct
from types import SimpleNamespace
import zlib

import pytest

from aero_bench.config.models import FileRef
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection import (
    AuthoritativeViewContext,
    CameraFrameDataRecord,
    ExternalVisualRequest,
    InspectionEvidenceBundle,
    ObservationContract,
    ObservationMetadata,
    ObservationReceipt,
    ObservationRequest,
    ObservationTrigger,
    PublicSensorFrameRecord,
    evaluate_trigger,
    rgb_observation_public_payload,
    public_observation,
)
from aero_bench.tasks.inspection.sealed_evidence import (
    InspectionSealedEvidenceLoader,
    _load_camera_frame_archive,
)


DIGEST = "a" * 64


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _image() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x10\x20\x30"))
        + _png_chunk(b"IEND", b"")
    )


def _trigger() -> ObservationTrigger:
    return ObservationTrigger(
        observation_id="observation.1",
        target_id="target.1",
        provider_id="observation.provider",
        camera_id="camera.1",
        min_distance_m=5.0,
        max_distance_m=15.0,
        min_view_angle_deg=10.0,
        max_view_angle_deg=40.0,
        earliest_time_ns=1_000_000_000,
        latest_time_ns=5_000_000_000,
    )


def _context(*, distance_m: float = 10.0) -> AuthoritativeViewContext:
    return AuthoritativeViewContext(
        source_provider_id="observation.provider",
        camera_id="camera.1",
        observation_id="observation.1",
        target_id="target.1",
        time=SimulationTime(tick=2, sim_time_ns=2_000_000_000),
        distance_m=distance_m,
        view_angle_deg=20.0,
        camera_pose_digest=DIGEST,
        target_pose_digest=DIGEST,
        provider_config_digest=DIGEST,
    )


def _payload(*, distance_m: float = 10.0) -> dict[str, object]:
    context = _context(distance_m=distance_m)
    image = _image()
    return rgb_observation_public_payload(
        target_id=context.target_id,
        camera_id=context.camera_id,
        distance_m=context.distance_m,
        view_angle_deg=context.view_angle_deg,
        frame_id="frame.1",
        vehicle_id="vehicle.1",
        engine_sim_time_ns=12_000_000_000,
        logical_origin_engine_ns=10_000_000_000,
        image_sha256=hashlib.sha256(image).hexdigest(),
        size_bytes=len(image),
        selector="frames/frame.1",
        width=1,
        height=1,
        capture_pose_source="gazebo.pose.private_digest",
        camera_pose_sha256=DIGEST,
        target_pose_sha256=DIGEST,
        image_base64=base64.b64encode(image).decode("ascii"),
    )


def _metadata(
    *,
    payload_digest: str | None = None,
    distance_m: float = 10.0,
) -> ObservationMetadata:
    context = _context(distance_m=distance_m)
    envelope_digest = hashlib.sha256(
        canonical_json_bytes(_payload(distance_m=distance_m))
    ).hexdigest()
    return ObservationMetadata(
        schema_version="aero-bench.inspection-observation-metadata/v1",
        source_artifact_id="artifact.observation",
        run_id=DIGEST,
        work_order_id="work.1",
        observation_id="observation.1",
        target_id="target.1",
        time=SimulationTime(tick=2, sim_time_ns=2_000_000_000),
        camera_id="camera.1",
        frame_id="frame.1",
        selector="frames/frame.1",
        vehicle_id="vehicle.1",
        engine_sim_time_ns=12_000_000_000,
        logical_origin_engine_ns=10_000_000_000,
        image_sha256=hashlib.sha256(_image()).hexdigest(),
        size_bytes=len(_image()),
        width=1,
        height=1,
        media_type="image/png",
        source="gazebo.camera",
        capture_pose_source="gazebo.pose.private_digest",
        camera_pose_sha256=DIGEST,
        target_pose_sha256=DIGEST,
        payload_digest=payload_digest or envelope_digest,
        view=context,
        visual_kind="gazebo_rgb",
        visual_status="captured",
    )


def test_observation_metadata_binds_real_rgb_payload_and_archive_bytes() -> None:
    metadata = _metadata()
    sensor_frame = PublicSensorFrameRecord(
        schema_version="aero-bench.public-sensor-frame-artifact/v2",
        source_artifact_id="artifact.sensor-frame",
        run_id=DIGEST,
        frame_id=metadata.frame_id,
        selector=metadata.selector,
        observation_id=metadata.observation_id,
        time=metadata.time,
        camera_id=metadata.camera_id,
        vehicle_id=metadata.vehicle_id,
        engine_sim_time_ns=metadata.engine_sim_time_ns,
        logical_origin_engine_ns=metadata.logical_origin_engine_ns,
        image_sha256=metadata.image_sha256,
        size_bytes=metadata.size_bytes,
        width=metadata.width,
        height=metadata.height,
        media_type=metadata.media_type,
        source=metadata.source,
        capture_pose_source=metadata.capture_pose_source,
        camera_pose_sha256=metadata.camera_pose_sha256,
        target_pose_sha256=metadata.target_pose_sha256,
        payload_digest=metadata.payload_digest,
    )
    archive_frame = CameraFrameDataRecord(
        source_artifact_id="artifact.camera-frame-data",
        frame_id=metadata.frame_id,
        image_sha256=metadata.image_sha256,
        size_bytes=metadata.size_bytes,
        width=metadata.width,
        height=metadata.height,
        media_type="image/png",
        png_bytes=_image(),
    )

    assert metadata.public_payload(
        image_base64=base64.b64encode(_image()).decode("ascii")
    ) == _payload()
    assert metadata.visual_kind == "gazebo_rgb"
    assert metadata.visual_status == "captured"
    assert metadata.payload_digest == hashlib.sha256(
        canonical_json_bytes(_payload())
    ).hexdigest()
    InspectionSealedEvidenceLoader._validate_rgb_frames(
        observations=(metadata,),
        sensor_frames=(sensor_frame,),
        camera_frames=(archive_frame,),
    )

    with pytest.raises(ValueError, match="payload digest"):
        InspectionSealedEvidenceLoader._validate_rgb_frames(
            observations=(_metadata(payload_digest="c" * 64),),
            sensor_frames=(sensor_frame.model_copy(update={"payload_digest": "c" * 64}),),
            camera_frames=(archive_frame,),
        )
    mismatched = metadata.model_dump()
    mismatched["camera_id"] = "camera.other"
    with pytest.raises(ValueError, match="camera"):
        ObservationMetadata.model_validate(mismatched)


def test_camera_frame_archive_is_strictly_framed_and_png_decoded() -> None:
    image = _image()
    frame_id = b"frame.1"
    archive = struct.pack(">8sIQ", b"ABFRAME1", len(frame_id), len(image))
    archive += frame_id + image

    records = _load_camera_frame_archive(
        archive,
        source_artifact_id="artifact.camera-frame-data",
    )

    assert len(records) == 1
    assert records[0].frame_id == "frame.1"
    assert (records[0].width, records[0].height) == (1, 1)
    assert records[0].png_bytes == image

    with pytest.raises(ValueError, match="truncated"):
        _load_camera_frame_archive(
            archive[:-1],
            source_artifact_id="artifact.camera-frame-data",
        )
    corrupt = bytearray(archive)
    corrupt[-1] ^= 1
    with pytest.raises(ValueError, match="CRC"):
        _load_camera_frame_archive(
            bytes(corrupt),
            source_artifact_id="artifact.camera-frame-data",
        )


def test_external_visual_request_is_reserved_for_renderer_fetch() -> None:
    request = ExternalVisualRequest(
        observation_id="observation.1",
        renderer_id="renderer.external",
        fetch_uri="https://renderer.example/frame",
        view=_context(),
    )
    assert request.schema_version == "aero-bench.external-visual-request/v1"
    assert request.view.distance_m == 10.0


def test_trigger_requires_provider_geometry_and_time_contract() -> None:
    eligible = evaluate_trigger(_trigger(), _context())
    assert eligible.eligible
    rejected = evaluate_trigger(_trigger(), _context(distance_m=2.0))
    assert not rejected.eligible
    assert rejected.failed_conditions == ("distance_below_minimum",)
    wrong_observation = _context().model_copy(
        update={"observation_id": "observation.other"}
    )
    rejected = evaluate_trigger(_trigger(), wrong_observation)
    assert rejected.failed_conditions == ("observation",)


def test_public_observation_contains_digest_only_not_private_asset() -> None:
    request = ObservationRequest(
        run_id=DIGEST,
        agent_id="agent.1",
        work_order_id="work.1",
        observation_id="observation.1",
        requested_at=SimulationTime(tick=1, sim_time_ns=1_000_000_000),
    )
    receipt = ObservationReceipt(
        run_id=DIGEST,
        agent_id="agent.1",
        work_order_id="work.1",
        observation_id="observation.1",
        status="captured",
        time=SimulationTime(tick=2, sim_time_ns=2_000_000_000),
        payload_digest=DIGEST,
    )
    contract = ObservationContract(
        observation_id="observation.1",
        target_id="target.1",
        simulation_asset_id="world.images",
        metadata_schema=FileRef(path="metadata.json", sha256=DIGEST),
        media_type="application/json",
        trigger=_trigger(),
    )
    public = public_observation(
        request=request,
        receipt=receipt,
        contract=contract,
        context=_context(),
    )
    assert public.payload_digest == DIGEST
    assert public.schema_file == contract.metadata_schema
    assert "simulation_asset_id" not in public.model_dump()
    with pytest.raises(ValueError, match="rejected"):
        public_observation(
            request=request,
            receipt=receipt.model_copy(
                update={
                    "status": "not_captured",
                    "payload_digest": None,
                    "reason": "out_of_view",
                }
            ),
            contract=contract,
            context=_context(),
        )
    with pytest.raises(ValueError, match="moved before request"):
        public_observation(
            request=request,
            receipt=receipt.model_copy(
                update={
                    "time": SimulationTime(tick=1, sim_time_ns=0),
                }
            ),
            contract=contract,
            context=_context(),
        )


def test_evidence_allows_distinct_barrier_captures_for_one_observation() -> None:
    first = _metadata()
    second_time = SimulationTime(tick=3, sim_time_ns=3_000_000_000)
    second = first.model_copy(
        update={
            "time": second_time,
            "view": first.view.model_copy(update={"time": second_time}),
        }
    )

    def evidence(observations: tuple[ObservationMetadata, ...]):
        return InspectionEvidenceBundle.model_construct(
            run_id=DIGEST,
            seal=SimpleNamespace(run_id=DIGEST),
            bindings=(),
            event_ledger=SimpleNamespace(),
            scene_states=(),
            business_state=SimpleNamespace(
                state=SimpleNamespace(run_id=DIGEST),
                history=(),
            ),
            business_ledger_bindings=(),
            theoretical_bounds=SimpleNamespace(run_id=DIGEST),
            trajectory=(),
            deliveries=(),
            observations=observations,
            sensor_frames=(),
            camera_frames=(),
            truth=(),
            detections=(),
            report=(),
        )

    distinct = evidence((first, second))
    assert distinct.input_run_ids_match() is distinct

    with pytest.raises(ValueError, match="work-order barrier capture"):
        evidence((first, first)).input_run_ids_match()
