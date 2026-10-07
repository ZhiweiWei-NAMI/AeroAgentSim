"""Focused Public Trace v3 projection and replay-closure tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aero_bench.artifacts import ArtifactRecord, SealManifest, seal_manifest
from aero_bench.config.models import ArtifactRequirement, FileRef, NamedValue
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.events import RunEventAudience
from aero_bench.runtime.ledger import EventLedger
from aero_bench.runner.execution import _project_and_write_public_trace
from aero_bench.serialization import canonical_json_bytes
from aero_bench.trace import (
    PUBLIC_PROJECTOR_VERSION,
    PUBLIC_TRACE_SCHEMA_VERSION,
    PublicProjectorError,
    PublicReplayManifest,
    PublicTrace,
    project_public_scenario,
    project_public_trace,
)
from aero_bench.world.resolved import ResolvedScenario
from tests.world.support import (
    deterministic_inspection_scenario,
    materialized_inspection_scenario,
)

RUN_ID = hashlib.sha256(b"public-trace-v3-run").hexdigest()
FRAME_PAYLOAD_DIGEST = hashlib.sha256(b"real-camera-payload").hexdigest()
HISTORY_ARTIFACT_ID = "artifact.scene-state-history"
SENSOR_ARTIFACT_ID = "artifact.public-sensor-frames"
EVENT_LOG_ARTIFACT_ID = "artifact.event-log"
HISTORY_PATH = "public/harness/scene-states.jsonl"
SENSOR_PATH = "public/flight/sensor-frames.json"
EVENT_LOG_PATH = "private/harness/events.jsonl"
PROVIDER_ID = "observation.provider"
FRAME_ID = "frame.fixture.agent.obs.1.0000000000000000"
OBSERVATION_ID = "obs.1"
SELECTOR = f"frames/{FRAME_ID}"
AT_ZERO = SimulationTime(tick=0, sim_time_ns=0)


def _run(*, scenario: ResolvedScenario | None = None) -> ResolvedRunSpec:
    """Use a compiler-produced scenario inside a projector-only run shell."""

    scenario = scenario or deterministic_inspection_scenario()
    providers = tuple(
        SimpleNamespace(
            provider_id=provider.provider_id,
            workload=SimpleNamespace(
                implementation=SimpleNamespace(
                    version="fixture.1",
                    kind="mechanical_fixture",
                )
            ),
        )
        for provider in scenario.providers
    )
    return ResolvedRunSpec.model_construct(
        schema_version="aero-bench.resolved-run/v5",
        run_id=RUN_ID,
        executor_kind="docker_reference",
        execution_scope="executor_validation",
        suite=FileRef(path="suite.yaml", sha256=hashlib.sha256(b"suite").hexdigest()),
        suite_id="fixture.suite",
        case_id="fixture.case",
        launch_site_id=scenario.selected_launch_site_id,
        seed=scenario.seed,
        scenario=scenario,
        task=SimpleNamespace(),
        environment=SimpleNamespace(providers=providers),
        agents=(),
        feasibility=SimpleNamespace(),
        artifact_requirements=(
            ArtifactRequirement(
                artifact_id=EVENT_LOG_ARTIFACT_ID,
                artifact_type="event.log",
                producer_id="harness",
                visibility="private",
                relative_path=EVENT_LOG_PATH,
                max_size_bytes=1_048_576,
                source_asset_id=None,
            ),
            ArtifactRequirement(
                artifact_id=HISTORY_ARTIFACT_ID,
                artifact_type="scene.state-history",
                producer_id="harness",
                visibility="public",
                relative_path=HISTORY_PATH,
                max_size_bytes=1_048_576,
                source_asset_id=None,
            ),
            ArtifactRequirement(
                artifact_id=SENSOR_ARTIFACT_ID,
                artifact_type="sensor-frame",
                producer_id=PROVIDER_ID,
                visibility="public",
                relative_path=SENSOR_PATH,
                max_size_bytes=1_048_576,
                source_asset_id=None,
            ),
        ),
        verification_outputs=(),
        overrides=(),
    )


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [(256 * 1024**2 - 1, "embedded"), (256 * 1024**2, "embedded"),
     (256 * 1024**2 + 1, "indexed"), (1024**3, "indexed")],
)
def test_native_inspection_replay_uses_resolved_history_budget(capacity, expected):
    run = _run()
    run = run.model_copy(update={
        "task": SimpleNamespace(package=SimpleNamespace(package_id="inspection.v1")),
        "environment": SimpleNamespace(providers=(SimpleNamespace(workload=SimpleNamespace(
            implementation=SimpleNamespace(component_id="px4.gazebo")
        )),)),
        "artifact_requirements": tuple(
            item.model_copy(update={"max_size_bytes": capacity})
            if item.artifact_type == "scene.state-history" else item
            for item in run.artifact_requirements
        ),
    })
    assert project_public_scenario(run).replay_mode == expected


def test_native_inspection_replay_rejects_missing_history_capacity():
    run = _run().model_copy(update={
        "task": SimpleNamespace(package=SimpleNamespace(package_id="inspection.v1")),
        "environment": SimpleNamespace(providers=(SimpleNamespace(workload=SimpleNamespace(
            implementation=SimpleNamespace(component_id="px4.gazebo")
        )),)),
        "artifact_requirements": (),
    })
    with pytest.raises(PublicProjectorError) as caught:
        project_public_scenario(run)
    assert "one SceneState history" in str(caught.value.__cause__)


def test_urban_recovery_remains_indexed():
    run = _run().model_copy(update={
        "task": SimpleNamespace(package=SimpleNamespace(package_id="urban.uav-recovery-demo.v1"))
    })
    assert project_public_scenario(run).replay_mode == "indexed"


def _sensor_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact_id": SENSOR_ARTIFACT_ID,
        "frame_id": FRAME_ID,
        "observation_id": OBSERVATION_ID,
        "payload_digest": FRAME_PAYLOAD_DIGEST,
        "selector": SELECTOR,
    }
    payload.update(overrides)
    return payload


def _ledger(
    run: ResolvedRunSpec,
    *,
    payload: dict[str, object] | None = None,
    payload_schema_id: str = "sensor.frame_ref.public.v2",
    interaction_type: str = "sensor.frame_ref.v2",
) -> EventLedger:
    ledger = EventLedger(run_id=run.run_id, wall_time_ns=lambda: 1)
    ledger.append_event(
        source="harness",
        source_kind="harness",
        workload_id="harness",
        event_type="validation.scope",
        time=AT_ZERO,
        payload=(
            NamedValue(name="execution_scope", value="executor_validation"),
        ),
    )
    observation = ledger.append_event(
        source=PROVIDER_ID,
        source_kind="provider",
        workload_id=PROVIDER_ID,
        event_type="observation.validated",
        time=AT_ZERO,
        payload=(NamedValue(name="payload_digest", value=FRAME_PAYLOAD_DIGEST),),
        correlation_id="observation.capture",
        provider_id=PROVIDER_ID,
        observation_id=OBSERVATION_ID,
    )
    ledger.append_event(
        source=PROVIDER_ID,
        source_kind="provider",
        workload_id=PROVIDER_ID,
        event_type="public.sensor-frame",
        time=AT_ZERO,
        payload=tuple(
            NamedValue(name=name, value=value)
            for name, value in sorted((payload or _sensor_payload()).items())
        ),
        payload_schema_id=payload_schema_id,
        interaction_type=interaction_type,  # type: ignore[arg-type]
        correlation_id="observation.capture",
        parent_event_id=observation.event.event_id,
        causal_event_ids=(observation.event.event_id,),
        visibility=(RunEventAudience(scope="public", audience_id=None),),
        frame_id=FRAME_ID,
        provider_id=PROVIDER_ID,
        observation_id=OBSERVATION_ID,
    )
    ledger.append_event(
        source="harness",
        source_kind="harness",
        workload_id="harness",
        event_type="run.aborted",
        time=AT_ZERO,
        payload=(
            NamedValue(name="failure_class", value="operator_cancelled"),
            NamedValue(name="provider_failure_count", value=0),
            NamedValue(name="provider_failure_ids", value="[]"),
        ),
    )
    return ledger


def _seal(
    tmp_path: Path,
    ledger: EventLedger,
    *,
    sensor_visibility: str = "public",
    include_sensor: bool = True,
    seal_root: Path | None = None,
) -> SealManifest:
    root = seal_root or (tmp_path / "sealed")
    event_log = root / EVENT_LOG_PATH
    event_log.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_jsonl(event_log)
    event_bytes = event_log.read_bytes()
    history = root / HISTORY_PATH
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(b"")
    records = [
        ArtifactRecord(
            artifact_id=EVENT_LOG_ARTIFACT_ID,
            artifact_type="event.log",
            producer_id="harness",
            visibility="private",
            relative_path=EVENT_LOG_PATH,
            sha256=hashlib.sha256(event_bytes).hexdigest(),
            size_bytes=len(event_bytes),
        ),
        ArtifactRecord(
            artifact_id=HISTORY_ARTIFACT_ID,
            artifact_type="scene.state-history",
            producer_id="harness",
            visibility="public",
            relative_path=HISTORY_PATH,
            sha256=hashlib.sha256(b"").hexdigest(),
            size_bytes=0,
        )
    ]
    if include_sensor:
        sensor_bytes = canonical_json_bytes(
            [{"frame_id": FRAME_ID, "payload_digest": FRAME_PAYLOAD_DIGEST}]
        )
        sensor = root / SENSOR_PATH
        sensor.parent.mkdir(parents=True, exist_ok=True)
        sensor.write_bytes(sensor_bytes)
        records.append(
            ArtifactRecord(
                artifact_id=SENSOR_ARTIFACT_ID,
                artifact_type="sensor-frame",
                producer_id=PROVIDER_ID,
                visibility=sensor_visibility,  # type: ignore[arg-type]
                relative_path=SENSOR_PATH,
                sha256=hashlib.sha256(sensor_bytes).hexdigest(),
                size_bytes=len(sensor_bytes),
            )
        )
    return seal_manifest(
        root=root,
        run_id=ledger.run_id,
        attempt_id="attempt.test",
        execution_scope="executor_validation",
        event_chain_root=ledger.chain_root,
        artifacts=tuple(records),
    )


def _project(
    tmp_path: Path,
    *,
    payload: dict[str, object] | None = None,
    payload_schema_id: str = "sensor.frame_ref.public.v2",
    interaction_type: str = "sensor.frame_ref.v2",
    sensor_visibility: str = "public",
    include_sensor: bool = True,
) -> PublicTrace:
    run = _run()
    ledger = _ledger(
        run,
        payload=payload,
        payload_schema_id=payload_schema_id,
        interaction_type=interaction_type,
    )
    return project_public_trace(
        run=run,
        ledger=ledger,
        seal=_seal(
            tmp_path,
            ledger,
            sensor_visibility=sensor_visibility,
            include_sensor=include_sensor,
        ),
        scene_states=(),
        report=None,
    )


def test_project_public_scenario_matches_trace_scenario(tmp_path: Path) -> None:
    run = _run()
    trace = _project(tmp_path)
    assert project_public_scenario(run) == trace.scenario


def test_projects_aborted_v3_trace_with_sensor_reference(tmp_path: Path) -> None:
    trace = _project(tmp_path)

    assert trace.schema_version == PUBLIC_TRACE_SCHEMA_VERSION
    assert trace.projector_version == PUBLIC_PROJECTOR_VERSION
    assert trace.phase == "aborted"
    assert trace.terminal.kind == "aborted"
    assert trace.time == AT_ZERO
    assert trace.scene_states == ()
    assert trace.trajectories == ()
    assert len(trace.sensor_frames) == 1
    frame = trace.sensor_frames[0]
    assert frame.frame_id == FRAME_ID
    assert frame.observation_id == OBSERVATION_ID
    assert frame.payload_digest == FRAME_PAYLOAD_DIGEST
    assert frame.artifact.artifact_id == SENSOR_ARTIFACT_ID
    assert frame.artifact.selector == SELECTOR
    assert frame.artifact.visibility == "public"

    assert len(trace.events) == 1
    event = trace.events[0]
    assert event.run_id == trace.run_id
    assert event.event_type == "public.sensor-frame"
    assert event.parent_event_id is None
    assert event.causal_event_ids == ()
    assert event.public_payload == ()
    assert PublicTrace.model_validate(trace.model_dump(mode="json")) == trace


@pytest.mark.parametrize(
    ("sensor_visibility", "include_sensor", "message"),
    [
        ("private", True, "private artifact"),
        ("public", False, "unsealed artifact"),
    ],
)
def test_rejects_sensor_reference_without_public_sealed_artifact(
    tmp_path: Path,
    sensor_visibility: str,
    include_sensor: bool,
    message: str,
) -> None:
    with pytest.raises(PublicProjectorError, match=message):
        _project(
            tmp_path,
            sensor_visibility=sensor_visibility,
            include_sensor=include_sensor,
        )


@pytest.mark.parametrize(
    ("payload", "payload_schema_id", "interaction_type", "message"),
    [
        (
            {key: value for key, value in _sensor_payload().items() if key != "selector"},
            "sensor.frame_ref.public.v2",
            "sensor.frame_ref.v2",
            "payload is invalid",
        ),
        (
            _sensor_payload(),
            "sensor.frame_ref.public.v0",
            "sensor.frame_ref.v2",
            "not a v1 document",
        ),
        (
            _sensor_payload(),
            "sensor.frame_ref.public.v2",
            "mission.event.v1",
            "wrong typed interaction",
        ),
    ],
)
def test_rejects_malformed_sensor_event_contract(
    tmp_path: Path,
    payload: dict[str, object],
    payload_schema_id: str,
    interaction_type: str,
    message: str,
) -> None:
    with pytest.raises(PublicProjectorError, match=message):
        _project(
            tmp_path,
            payload=payload,
            payload_schema_id=payload_schema_id,
            interaction_type=interaction_type,
        )


def test_rejects_private_bytes_in_public_provider_event(tmp_path: Path) -> None:
    payload = _sensor_payload(captured_bytes_b64="c2VjcmV0")
    with pytest.raises(PublicProjectorError, match="reserved for private evidence"):
        _project(tmp_path, payload=payload)


def test_public_trace_requires_specialized_event_closure(tmp_path: Path) -> None:
    trace = _project(tmp_path)
    document = trace.model_dump(mode="json")
    document["sensor_frames"] = []

    with pytest.raises(ValidationError, match="does not close over"):
        PublicTrace.model_validate(document)


def test_public_trace_hashes_embedded_scene_history(tmp_path: Path) -> None:
    trace = _project(tmp_path)
    document = trace.model_dump(mode="json")
    history_id = trace.scene_state_history_artifact.artifact_id
    for artifact in document["runtime_artifacts"]:
        if artifact["artifact_id"] == history_id:
            artifact["size_bytes"] = 1

    with pytest.raises(ValidationError, match="embedded SceneStates differ"):
        PublicTrace.model_validate(document)


def test_runner_publishes_closed_content_addressed_replay(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    scenario = materialized_inspection_scenario(bundle_root)
    run = _run(scenario=scenario)
    ledger = _ledger(run)
    run_root = tmp_path / "run"
    seal = _seal(
        tmp_path,
        ledger,
        seal_root=run_root / "runtime-seal",
    )

    identity = _project_and_write_public_trace(
        run=run,
        seal=seal,
        validated=None,
        bundle_root=bundle_root,
        run_root=run_root,
    )

    trace_path = run_root / identity.relative_path
    trace_payload = trace_path.read_bytes()
    assert hashlib.sha256(trace_payload).hexdigest() == identity.sha256
    trace = PublicTrace.model_validate(json.loads(trace_payload))
    manifest_path = run_root / identity.replay.relative_path
    manifest_payload = manifest_path.read_bytes()
    assert hashlib.sha256(manifest_payload).hexdigest() == identity.replay.sha256
    manifest = PublicReplayManifest.model_validate(json.loads(manifest_payload))

    expected_paths = {"public-trace.json"}
    for asset in trace.scenario.assets:
        expected_paths.add(asset.replay_path)
        if asset.license_replay_path is not None:
            expected_paths.add(asset.license_replay_path)
    expected_paths.update(
        artifact.replay_path for artifact in trace.runtime_artifacts
    )
    assert tuple(item.relative_path for item in manifest.files) == tuple(
        sorted(expected_paths)
    )
    assert identity.replay.file_count == len(expected_paths)
    assert EVENT_LOG_ARTIFACT_ID not in {
        artifact.artifact_id for artifact in trace.runtime_artifacts
    }

    replay_root = manifest_path.parent
    for replay_file in manifest.files:
        payload = (replay_root / replay_file.relative_path).read_bytes()
        assert len(payload) == replay_file.size_bytes
        assert hashlib.sha256(payload).hexdigest() == replay_file.sha256
