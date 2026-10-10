from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from aero_bench.artifacts.contracts import (
    ArtifactRecord,
    EvidenceReference,
    SealManifest,
    seal_manifest,
)
from aero_bench.config.models import ArtifactRequirement
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.events import RunEventAudience
from aero_bench.runtime.ledger import (
    EventLedger,
    ledger_jsonl_bytes,
    verifier_event_segment_from_jsonl_bytes,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.contracts import DemoTaskPackage
from aero_bench.tasks.urban_recovery_demo.goals import urban_goals
from aero_bench.tasks.urban_recovery_demo import verifier as urban
from aero_bench.verifier.contracts import GoalResult, VerificationReport


RUN_ID = "a" * 64
SCENARIO_DIGEST = "b" * 64
CHAIN_ROOT = "c" * 64
REQUIRED_CHANNELS = (
    "agent",
    "gazebo.airspace",
    "gazebo.contact",
    "gazebo.force",
    "gazebo.wind",
    "mavlink",
    "ns3",
    "px4",
    "sumo",
    "sumo.signals",
)


def _config_document() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.urban-recovery-verifier/v1",
        "package_id": "urban.uav-recovery-demo.v1",
        "duration_ns": 600_000_000_000,
        "step_ns": 200_000_000,
        "final_tick": 3000,
        "required_channels": list(REQUIRED_CHANNELS),
    }


def _package_document() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.urban-recovery-demo/v1",
        "package_id": "urban.uav-recovery-demo.v1",
        "replay_mode": "indexed",
        "task_id": "task.urban.recovery",
        "verifier_id": "verifier.urban.recovery",
        "scene_source_sha256": "d" * 64,
        "duration_ns": 600_000_000_000,
        "step_ns": 200_000_000,
        "physics_step_ns": 4_000_000,
        "final_tick": 3000,
        "roles": [
            {
                "agent_id": "groundstation.rule",
                "role": "groundstation",
                "endpoint_id": "endpoint.groundstation",
                "vehicle_id": None,
                "telemetry_observation_id": None,
                "safety_observation_id": None,
                "mailbox_observation_id": "network.mailbox.endpoint.groundstation",
            },
            {
                "agent_id": "uav.policy.01",
                "role": "uav",
                "endpoint_id": "endpoint.uav.01",
                "vehicle_id": "uav.01",
                "telemetry_observation_id": "observation.uav.01.telemetry",
                "safety_observation_id": "observation.uav.01.safety",
                "mailbox_observation_id": "network.mailbox.endpoint.uav.01",
            },
            {
                "agent_id": "uav.policy.02",
                "role": "uav",
                "endpoint_id": "endpoint.uav.02",
                "vehicle_id": "uav.02",
                "telemetry_observation_id": "observation.uav.02.telemetry",
                "safety_observation_id": "observation.uav.02.safety",
                "mailbox_observation_id": "network.mailbox.endpoint.uav.02",
            },
        ],
        "recovery": {
            "incident_vehicle_id": "uav.01",
            "incident_region_id": "region.no-fly.recovery",
            "injection_start_ns": 60_000_000_000,
            "heartbeat_interval_ns": 1_000_000_000,
            "response_timeout_ns": 10_000_000_000,
            "maximum_retries": 3,
            "vehicle_radius_m": 1.0,
            "obstacle_margin_m": 5.0,
            "cruise_agl_m": 45.0,
            "landing_deadline_ns": 540_000_000_000,
        },
        "flight_provider_id": "flight",
        "network_provider_id": "network",
        "traffic_provider_id": "traffic",
        "required_channels": list(REQUIRED_CHANNELS),
    }


def _groundstation_participant_document() -> dict[str, object]:
    return {
        "role": "groundstation",
        "endpoint_id": "endpoint.groundstation",
        "groundstation_endpoint_id": "endpoint.groundstation",
        "vehicle_id": None,
        "telemetry_observation_id": None,
        "safety_observation_id": None,
        "mailbox_observation_id": "network.mailbox.endpoint.groundstation",
        "heartbeat_interval_ns": 1_000_000_000,
        "injection_start_ns": 60_000_000_000,
        "response_timeout_ns": 10_000_000_000,
        "maximum_retries": 3,
        "vehicle_radius_m": 1.0,
        "obstacle_margin_m": 5.0,
        "cruise_agl_m": 45.0,
        "origin_latitude_deg": 31.2304,
        "origin_longitude_deg": 121.4737,
        "origin_ellipsoid_height_m": 50.0,
        "origin_amsl_m": 20.0,
        "recovery_goal_enu_m": {"x": 300.0, "y": 300.0, "z": 45.0},
        "forbidden_polygons": [
            [[-50.0, -50.0], [50.0, -50.0], [50.0, 50.0], [-50.0, 50.0]]
        ],
        "vehicle_endpoint_ids": {
            "uav.01": "endpoint.uav.01",
            "uav.02": "endpoint.uav.02",
        },
        "vehicle_agent_ids": {
            "uav.01": "uav.policy.01",
            "uav.02": "uav.policy.02",
        },
    }


def _goals():
    return urban_goals("verifier.urban.recovery")


def _report_run(*, scope: str = "formal_benchmark") -> SimpleNamespace:
    return SimpleNamespace(
        run_id=RUN_ID,
        execution_scope=scope,
        task=SimpleNamespace(goals=_goals(), verifier=SimpleNamespace(verifier_id="verifier.urban.recovery")),
    )


def test_verifier_config_defaults_physics_step_and_requires_exact_horizon() -> None:
    config = urban.UrbanRecoveryVerifierConfig.model_validate(_config_document())
    assert config.physics_step_ns == 4_000_000
    assert config.required_channels == REQUIRED_CHANNELS

    for field, value in (
        ("duration_ns", 599_999_999_999),
        ("step_ns", 100_000_000),
        ("physics_step_ns", 2_000_000),
        ("final_tick", 2999),
    ):
        invalid = _config_document()
        invalid[field] = value
        with pytest.raises(ValidationError, match="exact fixed contract"):
            urban.UrbanRecoveryVerifierConfig.model_validate(invalid)


def test_verifier_config_rejects_reordered_or_substituted_channels() -> None:
    for channels in (tuple(reversed(REQUIRED_CHANNELS)), REQUIRED_CHANNELS[:-1] + ("other",)):
        invalid = _config_document()
        invalid["required_channels"] = list(channels)
        with pytest.raises(ValidationError, match="exact fixed contract"):
            urban.UrbanRecoveryVerifierConfig.model_validate(invalid)


def test_demo_package_fixture_requires_explicit_indexed_replay() -> None:
    package = DemoTaskPackage.model_validate(_package_document())
    assert package.replay_mode == "indexed"
    missing = _package_document()
    missing.pop("replay_mode")
    with pytest.raises(ValidationError, match="replay_mode"):
        DemoTaskPackage.model_validate(missing)


def test_invalid_report_maps_every_goal_without_metrics() -> None:
    run = _report_run()
    report = urban._invalid_report(run, "urban.evidence.physics_authority_missing")
    assert report.status == "invalid"
    assert report.coverage_complete is False
    assert tuple(goal.goal_id for goal in report.goals) == tuple(goal.goal_id for goal in run.task.goals)
    assert all(
        not goal.passed
        and goal.metrics == ()
        and goal.failure_class == "urban.evidence.physics_authority_missing"
        for goal in report.goals
    )


def test_measured_report_has_finite_metrics_and_valid_evidence_for_every_goal() -> None:
    run = _report_run()
    evidence = (
        EvidenceReference(artifact_id="artifact.event-log", selector="runtime-chain-root"),
        EvidenceReference(artifact_id="artifact.trajectory", selector="ticks/0-3000"),
    )
    report = urban._measured_report(
        run,
        horizon=SimulationTime(tick=3000, sim_time_ns=600_000_000_000),
        mission_passed=False,
        failure_class="urban.mission.final_uav_state_invalid",
        evidence=evidence,
        coverage_complete=False,
    )
    assert report.status == "failed"
    assert report.coverage_complete is False
    assert [goal.metrics[0].value for goal in report.goals] == [1.0, 0.0]
    assert report.goals[0].passed and report.goals[0].failure_class is None
    assert not report.goals[1].passed
    assert all(goal.metrics[0].evidence == evidence for goal in report.goals)


def test_executor_validation_is_explicitly_invalid_without_reading_evidence(tmp_path: Path) -> None:
    report = urban.verify_urban_recovery_sealed(
        bundle_root=tmp_path / "absent-bundle",
        run=_report_run(scope="executor_validation"),
        seal=None,  # type: ignore[arg-type]
        sealed_root=tmp_path / "absent-seal",
        config=urban.UrbanRecoveryVerifierConfig.model_validate(_config_document()),
    )
    assert report.status == "invalid"
    assert report.coverage_complete is False
    assert {goal.failure_class for goal in report.goals} == {
        "urban.scope.executor_validation_not_scorable"
    }


def _sealed_fixture(root: Path) -> tuple[SimpleNamespace, SealManifest, dict[str, bytes]]:
    contents = {
        "event.log": b"event\n",
        "scene.state-history": b"scene\n",
        "trajectory": b"trajectory\n",
        "gazebo.physics-journal": b"unit-only journal\n",
        "gazebo.physics-plugin": b"unit-only plugin bytes\n",
        "network.delivery": b"network\n",
        "sumo.traffic.evidence": b"sumo\n",
    }
    identities = {
        "event.log": ("artifact.event-log", "harness", "private", "harness/event.jsonl"),
        "scene.state-history": ("artifact.scene-history", "harness", "public", "harness/scene.jsonl"),
        "trajectory": ("artifact.trajectory", "flight", "public", "flight/trajectory.jsonl"),
        "gazebo.physics-journal": ("artifact.physics-journal", "flight", "private", "flight/physics-journal.jsonl"),
        "gazebo.physics-plugin": ("artifact.physics-plugin", "flight", "private", "flight/physics-plugin.so"),
        "network.delivery": ("artifact.network", "network", "public", "network/delivery.json"),
        "sumo.traffic.evidence": ("artifact.sumo", "traffic", "private", "traffic/evidence.jsonl"),
    }
    requirements: list[ArtifactRequirement] = []
    records: list[ArtifactRecord] = []
    for artifact_type, content in contents.items():
        artifact_id, producer, visibility, relative = identities[artifact_type]
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        requirement = ArtifactRequirement(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            producer_id=producer,
            visibility=visibility,
            relative_path=relative,
            max_size_bytes=1024,
            source_asset_id=None,
        )
        requirements.append(requirement)
        records.append(
            ArtifactRecord(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                producer_id=producer,
                visibility=visibility,
                relative_path=relative,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            )
        )
    seal = seal_manifest(
        root=root,
        run_id=RUN_ID,
        attempt_id="attempt.test",
        execution_scope="formal_benchmark",
        event_chain_root=CHAIN_ROOT,
        artifacts=tuple(records),
    )
    (root / "seal-manifest.json").write_bytes(
        canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
    )
    requirements_tuple = tuple(requirements)
    run = SimpleNamespace(
        run_id=RUN_ID,
        execution_scope="formal_benchmark",
        artifact_requirements=requirements_tuple,
        task=SimpleNamespace(
            verifier=SimpleNamespace(artifact_requirements=requirements_tuple)
        ),
    )
    return run, seal, contents


def test_descriptor_relative_seal_validation_accepts_only_exact_inventory(tmp_path: Path) -> None:
    run, seal, expected = _sealed_fixture(tmp_path)
    records, contents = urban._validated_sealed_bytes(run, seal, tmp_path)
    assert set(records) == set(expected)
    assert contents == expected


@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink", "special", "digest"])
def test_sealed_inventory_tampering_fails_closed(tmp_path: Path, mutation: str) -> None:
    run, seal, _ = _sealed_fixture(tmp_path)
    if mutation == "extra":
        (tmp_path / "undeclared.bin").write_bytes(b"x")
    elif mutation == "missing":
        (tmp_path / "network/delivery.json").unlink()
    elif mutation == "symlink":
        (tmp_path / "undeclared-link").symlink_to(tmp_path / "harness/event.jsonl")
    elif mutation == "special":
        os.mkfifo(tmp_path / "undeclared-fifo")
    else:
        (tmp_path / "flight/trajectory.jsonl").write_bytes(b"TRAJECTORY\n")
    with pytest.raises((OSError, ValueError)):
        urban._validated_sealed_bytes(run, seal, tmp_path)


@pytest.mark.parametrize("value", ["../outside", "/absolute", "a\\b", "a//b", "seal-manifest.json"])
def test_sealed_artifact_paths_reject_traversal_and_aliases(value: str) -> None:
    with pytest.raises(ValueError, match="normalized and relative"):
        urban._relative_path(value)


def _patch_sparse_pipeline(monkeypatch: pytest.MonkeyPatch, *, replay_mode: str = "indexed") -> SimpleNamespace:
    package = DemoTaskPackage.model_validate(_package_document())
    if replay_mode != "indexed":
        package = SimpleNamespace(**{**package.model_dump(mode="python"), "replay_mode": replay_mode})
    assessment = SimpleNamespace(feasible=True)
    verifier_requirements = ()
    participant_config_ref = object()
    run = SimpleNamespace(
        run_id=RUN_ID,
        execution_scope="formal_benchmark",
        task=SimpleNamespace(
            goals=_goals(),
            assets=(
                SimpleNamespace(
                    asset_id="asset.participant.groundstation.rule",
                    file=participant_config_ref,
                    classification="public",
                    audiences=(
                        SimpleNamespace(
                            role="agent", workload_ids=("groundstation.rule",)
                        ),
                        SimpleNamespace(
                            role="verifier",
                            workload_ids=("verifier.urban.recovery",),
                        ),
                    ),
                ),
            ),
            verifier=SimpleNamespace(verifier_id="verifier.urban.recovery", artifact_requirements=verifier_requirements),
        ),
        environment=SimpleNamespace(
            providers=tuple(
                SimpleNamespace(provider_id=provider_id, config=SimpleNamespace(file=object()))
                for provider_id in ("flight", "network", "traffic")
            )
        ),
        agents=(),
        scenario=SimpleNamespace(scenario_digest=SCENARIO_DIGEST),
        feasibility=assessment,
        artifact_requirements=(),
    )

    class Reader:
        def __init__(self, root: Path):
            self.root = root

        def load_document(self, reference: object) -> dict[str, object]:
            if reference is participant_config_ref:
                return _groundstation_participant_document()
            return {}

    class Resolver:
        def resolve(self, **kwargs: object) -> object:
            return assessment

    class ConfigContract:
        @classmethod
        def model_validate(cls, document: object) -> SimpleNamespace:
            return SimpleNamespace()

    records = {
        artifact_type: SimpleNamespace(artifact_id=f"artifact.{index}")
        for index, artifact_type in enumerate(urban._ARTIFACT_AUTHORITIES)
    }
    sparse = {artifact_type: b"{}\n" for artifact_type in urban._ARTIFACT_AUTHORITIES}
    monkeypatch.setattr(urban, "BundleReader", Reader)
    monkeypatch.setattr(urban, "load_urban_recovery_package", lambda **kwargs: package)
    monkeypatch.setattr(urban, "UrbanRecoveryTaskPackageResolver", Resolver)
    monkeypatch.setattr(urban, "Px4GazeboConfig", ConfigContract)
    monkeypatch.setattr(urban, "SumoConfig", ConfigContract)
    monkeypatch.setattr(urban, "_validated_sealed_bytes", lambda *args: (records, sparse))
    monkeypatch.setattr(urban, "_load_ledger", lambda *args: object())
    monkeypatch.setattr(urban, "_load_scene_history", lambda *args: ())
    monkeypatch.setattr(urban, "_validated_observations", lambda *args: {})
    monkeypatch.setattr(urban, "_command_issues", lambda *args: {})
    monkeypatch.setattr(urban, "_validate_agent_timeline", lambda *args: None)
    monkeypatch.setattr(urban, "_validate_trajectory", lambda *args: ({}, {}))
    monkeypatch.setattr(urban, "_validate_telemetry_observations", lambda *args: None)
    monkeypatch.setattr(urban, "_validate_sumo", lambda *args: None)
    return run


def test_sparse_nonempty_artifacts_cannot_pass_without_physics_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _patch_sparse_pipeline(monkeypatch)

    def missing_physics(*args: object) -> None:
        raise urban.UrbanRecoveryVerificationError(
            "urban.evidence.physics_authority_missing", "missing engine truth"
        )

    monkeypatch.setattr(urban, "_validate_physics", missing_physics)
    report = urban.verify_urban_recovery_sealed(
        bundle_root=tmp_path,
        run=run,
        seal=None,  # type: ignore[arg-type]
        sealed_root=tmp_path,
        config=urban.UrbanRecoveryVerifierConfig.model_validate(_config_document()),
    )
    assert report.status == "invalid"
    assert report.coverage_complete is False
    assert all(goal.metrics == () for goal in report.goals)
    assert {goal.failure_class for goal in report.goals} == {
        "urban.evidence.physics_authority_missing"
    }


def test_missing_indexed_replay_declaration_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _patch_sparse_pipeline(monkeypatch, replay_mode="embedded")
    report = urban.verify_urban_recovery_sealed(
        bundle_root=tmp_path,
        run=run,
        seal=None,  # type: ignore[arg-type]
        sealed_root=tmp_path,
        config=urban.UrbanRecoveryVerifierConfig.model_validate(_config_document()),
    )
    assert report.status == "invalid"
    assert report.coverage_complete is False
    assert {goal.failure_class for goal in report.goals} == {
        "urban.authority.package_config_mismatch"
    }


def _load_entrypoint_module():
    path = Path(__file__).parents[1] / "containers/urban-recovery-verifier/entrypoint.py"
    spec = importlib.util.spec_from_file_location("urban_recovery_verifier_entrypoint_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module



def _verification_output(artifact_type: str, artifact_id: str, path: str) -> ArtifactRequirement:
    return ArtifactRequirement(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        producer_id="verifier.urban.recovery",
        visibility="public",
        relative_path=path,
        max_size_bytes=16 * 1024 * 1024,
        source_asset_id=None,
    )


def test_container_requires_exact_report_and_event_segment_outputs() -> None:
    entrypoint = _load_entrypoint_module()
    report = _verification_output(
        "verification.report", "artifact.verification-report", "verifier/report.json"
    )
    segment = _verification_output(
        "verification.event-segment",
        "artifact.verification-event-segment",
        "verifier/events.jsonl",
    )
    contract = SimpleNamespace(
        workload_id="verifier.urban.recovery",
        verifier=SimpleNamespace(output_artifacts=(report, segment)),
    )
    assert entrypoint._output_requirements(contract) == (report, segment)
    contract.verifier.output_artifacts = (report,)
    with pytest.raises(
        entrypoint.UrbanVerifierWorkloadError, match="exactly one public report"
    ):
        entrypoint._output_requirements(contract)

def test_container_segment_uses_generic_goal_result_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    entrypoint = _load_entrypoint_module()
    runtime = EventLedger(run_id=RUN_ID, wall_time_ns=lambda: 1)
    runtime.append_event(
        source="harness",
        event_type="run.completed",
        time=SimulationTime(tick=3000, sim_time_ns=600_000_000_000),
        visibility=(RunEventAudience(scope="private", audience_id=None),),
    )
    monkeypatch.setattr(
        entrypoint,
        "load_sealed_event_ledger",
        lambda **kwargs: SimpleNamespace(records=runtime.records),
    )
    goal = GoalResult(
        goal_id="goal.urban.first",
        passed=False,
        metrics=(),
        failure_class="urban.evidence.physics_authority_missing",
    )
    report = VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=RUN_ID,
        execution_scope="formal_benchmark",
        status="invalid",
        goals=(goal,),
        coverage_complete=False,
    )
    report_payload = canonical_json_bytes(report.model_dump(mode="json")) + b"\n"
    contract = SimpleNamespace(
        workload_id="verifier.urban.recovery",
        run=SimpleNamespace(),
    )
    payload = entrypoint._segment(contract, object(), Path("/unused"), report, report_payload)
    segment = verifier_event_segment_from_jsonl_bytes(
        runtime_records=runtime.records,
        content=payload,
    )
    assert len(segment.records) == 1
    values = {item.name: item.value for item in segment.records[0].event.payload}
    assert set(values) == {
        "failure_class",
        "goal_id",
        "goal_result_json",
        "passed",
        "report_sha256",
        "report_status",
    }
    assert values["goal_result_json"] == canonical_json_bytes(
        goal.model_dump(mode="json")
    ).decode("utf-8")
    assert values["report_sha256"] == hashlib.sha256(report_payload).hexdigest()


def test_engineering_verifier_streams_seal_and_builds_segment_from_terminal_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = EventLedger(run_id=RUN_ID, wall_time_ns=lambda: 1)
    runtime.append_event(
        source="harness",
        event_type="run.started",
        time=SimulationTime(tick=0, sim_time_ns=0),
    )
    runtime.append_event(
        source="harness",
        event_type="run.completed",
        time=SimulationTime(tick=2, sim_time_ns=400_000_000),
    )
    event_content = ledger_jsonl_bytes(runtime.records)
    event_path = tmp_path / "harness/event-log.jsonl"
    event_path.parent.mkdir()
    event_path.write_bytes(event_content)
    requirement = ArtifactRequirement(
        artifact_id="artifact.event-log",
        artifact_type="event.log",
        producer_id="harness",
        visibility="private",
        relative_path="harness/event-log.jsonl",
        max_size_bytes=len(event_content),
        source_asset_id=None,
    )
    record = ArtifactRecord(
        artifact_id=requirement.artifact_id,
        artifact_type=requirement.artifact_type,
        producer_id=requirement.producer_id,
        visibility=requirement.visibility,
        relative_path=requirement.relative_path,
        sha256=hashlib.sha256(event_content).hexdigest(),
        size_bytes=len(event_content),
    )
    seal = seal_manifest(
        root=tmp_path,
        run_id=RUN_ID,
        attempt_id="attempt.test",
        execution_scope="executor_validation",
        event_chain_root=runtime.chain_root,
        artifacts=(record,),
    )
    (tmp_path / "seal-manifest.json").write_bytes(
        canonical_json_bytes(seal.model_dump(mode="json")) + b"\n"
    )
    run = _report_run(scope="executor_validation")
    run.artifact_requirements = (requirement,)
    run.task.verifier.artifact_requirements = (requirement,)
    config_document = _config_document()
    config_document.update(
        execution_profile="engineering",
        duration_ns=400_000_000,
        final_tick=2,
    )
    config = urban.UrbanRecoveryVerifierConfig.model_validate(config_document)
    monkeypatch.setattr(
        urban,
        "_canonical_jsonl",
        lambda *_args, **_kwargs: pytest.fail("engineering parsed the full ledger"),
    )
    report = urban.verify_urban_recovery_sealed(
        bundle_root=tmp_path,
        run=run,
        seal=seal,
        sealed_root=tmp_path,
        config=config,
    )
    assert report.status == "invalid"
    assert {goal.failure_class for goal in report.goals} == {
        "urban.scope.executor_validation_not_scorable"
    }

    entrypoint = _load_entrypoint_module()
    monkeypatch.setattr(
        entrypoint,
        "load_sealed_event_ledger",
        lambda **_kwargs: pytest.fail("engineering materialized the full ledger"),
    )
    contract = SimpleNamespace(
        run_id=RUN_ID,
        workload_id="verifier.urban.recovery",
    )
    report_payload = canonical_json_bytes(report.model_dump(mode="json")) + b"\n"
    payload = entrypoint._segment(
        contract,
        seal,
        tmp_path,
        report,
        report_payload,
        config=config,
    )
    segment = verifier_event_segment_from_jsonl_bytes(
        runtime_records=runtime.records,
        content=payload,
    )
    assert len(segment.records) == len(report.goals)
    assert segment.records[0].previous_hash == seal.event_chain_root
    assert segment.first_verifier_sequence == len(runtime.records)

    invalid_evidence = urban._invalid_report(run, "urban.authority.engineering_runtime_invalid")
    with pytest.raises(entrypoint.UrbanVerifierWorkloadError, match="evidence validation failed"):
        entrypoint._segment(
            contract, seal, tmp_path, invalid_evidence,
            canonical_json_bytes(invalid_evidence.model_dump(mode="json")) + b"\n",
            config=config,
        )
