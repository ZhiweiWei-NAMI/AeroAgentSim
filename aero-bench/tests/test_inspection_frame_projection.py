"""Metadata-only projection fixtures, not native frames or formal run evidence."""

import hashlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aero_bench.artifacts import ArtifactRecord
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.trace.projector import (
    PublicProjectorError,
    SealedPublicArtifacts,
    _project_sensor_frame,
)
from tests.test_trace_projector import (
    FRAME_ID,
    OBSERVATION_ID,
    PROVIDER_ID,
    SENSOR_ARTIFACT_ID,
    _ledger,
    _run,
    _sensor_payload,
)

STEP_NS = 500_000_000
ARCHIVE_ID = "artifact.unit-camera-archive"


def _fixture(*, metadata=True, include_index=True, **changes):
    base = _run()
    providers = tuple(
        SimpleNamespace(
            provider_id=item.provider_id,
            workload=SimpleNamespace(implementation=SimpleNamespace(kind="production")),
        )
        for item in base.environment.providers
    )
    run = base.model_copy(
        update={
            "task": SimpleNamespace(
                package=SimpleNamespace(package_id="inspection.v1")
            ),
            "environment": SimpleNamespace(
                providers=providers, clock=SimpleNamespace(step_ns=STEP_NS)
            ),
        }
    )
    payload = _sensor_payload(artifact_id=ARCHIVE_ID)
    if metadata:
        payload.update(
            camera_id="camera.unit",
            vehicle_id="uav.unit",
            width=640,
            height=480,
            engine_sim_time_ns=1_000_000_000,
            logical_origin_engine_ns=1_000_000_000,
            capture_pose_source="gazebo.pose.private_digest",
            media_type="image/png",
            source="gazebo.camera",
            size_bytes=2059,
            image_sha256=hashlib.sha256(b"unit image").hexdigest(),
            camera_pose_sha256=hashlib.sha256(b"unit camera pose").hexdigest(),
            target_pose_sha256=hashlib.sha256(b"unit target pose").hexdigest(),
        )
    payload.update(changes)
    original = _ledger(run, payload=payload).records[2]
    record = SimpleNamespace(
        sequence=original.sequence,
        event=original.event.model_copy(
            update={"time": SimulationTime(tick=1, sim_time_ns=STEP_NS)}
        ),
    )
    archive = ArtifactRecord(
        artifact_id=ARCHIVE_ID,
        artifact_type="camera-frame-data",
        producer_id=PROVIDER_ID,
        visibility="public",
        relative_path="unit/archive",
        size_bytes=2059,
        sha256=hashlib.sha256(b"unit archive metadata").hexdigest(),
    )
    index = ArtifactRecord(
        artifact_id=SENSOR_ARTIFACT_ID,
        artifact_type="sensor-frame",
        producer_id=PROVIDER_ID,
        visibility="public",
        relative_path="unit/index",
        size_bytes=4,
        sha256=hashlib.sha256(b"unit index metadata").hexdigest(),
    )
    resources = SealedPublicArtifacts(
        SimpleNamespace(artifacts=(archive, index) if include_index else (archive,))
    )
    return run, record, resources


def test_production_inspection_projects_current_metadata_at_next_barrier():
    run, record, resources = _fixture()
    frame = _project_sensor_frame(run, record, resources=resources)
    assert frame.frame_id == FRAME_ID
    assert frame.observation_id == OBSERVATION_ID
    assert frame.at == SimulationTime(tick=1, sim_time_ns=STEP_NS)
    assert frame.artifact.artifact_id == ARCHIVE_ID


def test_production_inspection_rejects_the_thin_fixture_payload():
    run, record, resources = _fixture(metadata=False)
    with pytest.raises(PublicProjectorError, match="payload is invalid"):
        _project_sensor_frame(run, record, resources=resources)


@pytest.mark.parametrize(
    "changes",
    [
        {"engine_sim_time_ns": 1_500_000_000},
        {"logical_origin_engine_ns": 1_000_000_001},
        {"capture_pose_source": "authored.pose"},
        {"width": True},
        {"media_type": "image/jpeg"},
        {"size_bytes": 2 * 1024 * 1024 + 1},
    ],
)
def test_production_inspection_rejects_invalid_capture_metadata(changes):
    run, record, resources = _fixture(**changes)
    with pytest.raises(PublicProjectorError, match="metadata/clock is invalid"):
        _project_sensor_frame(run, record, resources=resources)


def test_production_inspection_rejects_private_pose_coordinates():
    run, record, resources = _fixture(camera_position_enu="private unit coordinates")
    with pytest.raises(PublicProjectorError, match="payload is invalid"):
        _project_sensor_frame(run, record, resources=resources)


def test_production_inspection_requires_its_public_frame_index():
    run, record, resources = _fixture(include_index=False)
    with pytest.raises(PublicProjectorError, match="public archive/index"):
        _project_sensor_frame(run, record, resources=resources)


@pytest.mark.parametrize(
    "module", ["aero_bench.tasks.inspection.integration", "aero_bench.agent.runtime"]
)
def test_inspection_modules_cold_import_without_a_projector_cycle(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}; import aero_bench.trace.projector"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
