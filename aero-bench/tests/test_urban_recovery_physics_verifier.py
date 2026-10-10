"""Byte-closure tests only: these fixtures are not native simulator evidence."""
from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

import pytest

from aero_bench.config.models import NamedValue
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.contracts import (
    PHYSICS_STEP_NS, STEP_NS, AppliedWrench, EngineWindow, PhysicsJournalRecord,
)
from aero_bench.tasks.urban_recovery_demo import verifier as urban

RUN_ID = "a" * 64
SCENARIO_DIGEST = "b" * 64
PLUGIN = b"unit fixture, NOT a compiled or loaded Gazebo plugin"
UAV_IDS = {"uav.01", "uav.02"}
ORIGIN = 5_000_000_000


def _record(schema, payload, *, at, vehicle_id=None):
    return SimpleNamespace(event=SimpleNamespace(
        source="flight", provider_id="flight", vehicle_id=vehicle_id,
        time=at, payload_schema_id=schema,
        payload=tuple(NamedValue(name=name, value=value) for name, value in sorted(payload.items())),
    ))


def _fixture(*, tick=1, contact=False, omit=None):
    at = SimulationTime(tick=tick, sim_time_ns=tick * STEP_NS)
    start = ORIGIN + (tick - 1) * STEP_NS
    steps = []
    records = []
    for offset in range(1, 51):
        engine_time = start + offset * PHYSICS_STEP_NS
        wrenches = []
        for vehicle_id in sorted(UAV_IDS):
            components = {"aerodynamic", "gravity", "rotor", "wind"}
            if contact:
                components.add("contact")
            for component in sorted(components):
                if (vehicle_id, offset, component) == omit:
                    continue
                wrench = AppliedWrench(
                    vehicle_id=vehicle_id, engine_link=f"{vehicle_id}::base_link",
                    engine_sim_time_ns=engine_time, component=component,
                    origin="solver_measured" if component == "contact" else "applied",
                    frame="ENU", force_n={"x": 1.0, "y": 2.0, "z": 3.0},
                    torque_nm={"x": 0.0, "y": 0.0, "z": 0.1},
                )
                wrenches.append(wrench)
                records.append(_record(
                    urban._WRENCH_SCHEMA,
                    {"vehicle_id": vehicle_id, "wrench_json": canonical_json_bytes(wrench.model_dump(mode="json")).decode()},
                    at=at, vehicle_id=vehicle_id,
                ))
        steps.append(PhysicsJournalRecord(
            schema_version="aero-bench.gazebo-physics-journal/v1", source="gazebo.system",
            run_id=RUN_ID, scenario_digest=SCENARIO_DIGEST, provider_id="flight",
            plugin_sha256=hashlib.sha256(PLUGIN).hexdigest(), engine_sim_time_ns=engine_time,
            sequence=(tick - 1) * 50 + offset, wrenches=tuple(wrenches),
        ).model_dump(mode="json"))
    raw = b"".join(canonical_json_bytes(step) + b"\n" for step in steps)
    window = EngineWindow(
        run_id=RUN_ID, scenario_digest=SCENARIO_DIGEST, provider_id="flight", at=at,
        engine_start_ns=start, engine_end_ns=start + STEP_NS, logical_origin_engine_ns=ORIGIN,
        first_sequence=(tick - 1) * 50, last_sequence=tick * 50, record_count=50,
        journal_sha256=hashlib.sha256(raw).hexdigest(), plugin_sha256=hashlib.sha256(PLUGIN).hexdigest(),
    )
    trajectory = {vehicle: {"raw": {"contacts": ["ground.world"] if contact else []}} for vehicle in UAV_IDS}
    return steps, raw, window, records, trajectory


def _validate(raw, window, records, trajectory):
    with io.BytesIO(raw) as journal:
        urban._validate_physics_window(journal, window, records, UAV_IDS, trajectory)
        assert not journal.read(1)


def test_one_window_checks_exact_native_bytes_and_each_projected_wrench():
    _, raw, window, records, trajectory = _fixture(contact=True)
    _validate(raw, window, records, trajectory)


def test_window_reader_consumes_only_fifty_records_not_the_whole_journal():
    _, first_raw, first, first_records, trajectory = _fixture()
    _, second_raw, second, second_records, _ = _fixture(tick=2)
    with io.BytesIO(first_raw + second_raw) as journal:
        urban._validate_physics_window(journal, first, first_records, UAV_IDS, trajectory)
        assert journal.tell() == len(first_raw)
        urban._validate_physics_window(journal, second, second_records, UAV_IDS, trajectory)
        assert journal.tell() == len(first_raw + second_raw)


@pytest.mark.parametrize("field,value", [
    ("run_id", "c" * 64), ("scenario_digest", "c" * 64),
    ("plugin_sha256", "c" * 64), ("sequence", 2),
    ("engine_sim_time_ns", ORIGIN + 2 * PHYSICS_STEP_NS), ("source", "telemetry.estimate"),
])
def test_rehashing_a_foreign_native_record_does_not_restore_authority(field, value):
    steps, _, window, records, trajectory = _fixture()
    steps[0][field] = value
    raw = b"".join(canonical_json_bytes(step) + b"\n" for step in steps)
    window = window.model_copy(update={"journal_sha256": hashlib.sha256(raw).hexdigest()})
    with pytest.raises(ValueError):
        _validate(raw, window, records, trajectory)


def test_self_consistent_ledger_does_not_replace_native_force_bytes():
    steps, _, window, records, trajectory = _fixture()
    steps[0]["wrenches"][0]["force_n"]["x"] = 123.0
    raw = b"".join(canonical_json_bytes(step) + b"\n" for step in steps)
    window = window.model_copy(update={"journal_sha256": hashlib.sha256(raw).hexdigest()})
    with pytest.raises(ValueError, match="ledger projection"):
        _validate(raw, window, records, trajectory)


def test_arbitrary_nonzero_window_digest_is_not_native_byte_closure():
    _, raw, window, records, trajectory = _fixture()
    window = window.model_copy(update={"journal_sha256": "d" * 64})
    with pytest.raises(ValueError, match="journal digest differs"):
        _validate(raw, window, records, trajectory)


@pytest.mark.parametrize("mutation", ["truncated", "gap", "duplicate", "unsorted", "noncanonical", "duplicate-key", "oversized"])
def test_native_journal_corruption_is_terminal(mutation):
    steps, raw, window, records, trajectory = _fixture()
    if mutation == "truncated":
        raw = raw[:-1]
    elif mutation == "gap":
        raw = b"".join(canonical_json_bytes(step) + b"\n" for step in steps[1:])
    elif mutation == "duplicate":
        raw = canonical_json_bytes(steps[0]) + b"\n" + raw
    elif mutation == "unsorted":
        steps[0]["wrenches"].reverse()
        raw = b"".join(canonical_json_bytes(step) + b"\n" for step in steps)
    elif mutation == "noncanonical":
        raw = b" " + raw
    elif mutation == "duplicate-key":
        raw = b'{"sequence":1,' + raw[1:]
    else:
        raw = b" " * (2 * 1024 * 1024 + 1) + b"\n" + raw
    window = window.model_copy(update={"journal_sha256": hashlib.sha256(raw).hexdigest()})
    with pytest.raises(ValueError):
        _validate(raw, window, records, trajectory)


def test_each_uav_requires_all_components_even_when_ledger_and_journal_agree():
    _, raw, window, records, trajectory = _fixture(omit=("uav.02", 49, "wind"))
    with pytest.raises(urban.UrbanRecoveryVerificationError) as error:
        _validate(raw, window, records, trajectory)
    assert error.value.failure_class == "urban.evidence.force_wind_torque_incomplete"


def test_classified_contact_cannot_use_ordinary_force_as_solver_evidence():
    _, raw, window, records, trajectory = _fixture()
    trajectory["uav.02"]["raw"]["contacts"] = ["ground.world"]
    with pytest.raises(urban.UrbanRecoveryVerificationError) as error:
        _validate(raw, window, records, trajectory)
    assert error.value.failure_class == "urban.evidence.solver_contact_missing"


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "extra", "foreign-provider", "off-grid", "wrong-barrier"])
def test_wrench_projection_must_be_exactly_one_to_one(mutation):
    _, raw, window, records, trajectory = _fixture()
    if mutation == "duplicate":
        records.append(records[0])
    elif mutation == "missing":
        records.pop()
    elif mutation == "extra":
        extra = records[0]
        fields = urban._payload(extra)
        import json
        wrench = json.loads(fields["wrench_json"])
        wrench["engine_link"] = "uav.01::other_link"
        fields["wrench_json"] = canonical_json_bytes(wrench).decode()
        records.append(_record(urban._WRENCH_SCHEMA, fields, at=window.at, vehicle_id="uav.01"))
    elif mutation == "foreign-provider":
        records[0].event.provider_id = "traffic"
    else:
        records[0].event.time = SimulationTime(tick=1, sim_time_ns=STEP_NS - 1) if mutation == "off-grid" else SimulationTime(tick=2, sim_time_ns=2 * STEP_NS)
    with pytest.raises(ValueError):
        _validate(raw, window, records, trajectory)


@pytest.mark.parametrize("journal,plugin,expected", [
    (None, None, "physics_journal_missing"), (b"", PLUGIN, "physics_journal_missing"),
    (b"journal", None, "physics_plugin_missing"), (b"journal", b"", "physics_plugin_missing"),
])
def test_missing_separately_sealed_native_authority_cannot_use_ledger_hashes(journal, plugin, expected):
    with pytest.raises(urban.UrbanRecoveryVerificationError) as error:
        urban._validate_physics(object(), object(), object(), {}, journal, plugin)
    assert error.value.failure_class == f"urban.evidence.{expected}"


def test_plugin_digest_is_checked_against_sealed_bytes_before_window_coverage():
    _, raw, window, records, trajectory = _fixture()
    window_record = _record(urban._ENGINE_WINDOW_SCHEMA, {"window_json": canonical_json_bytes(window.model_dump(mode="json")).decode()}, at=window.at)
    run = SimpleNamespace(run_id=RUN_ID, scenario=SimpleNamespace(scenario_digest=SCENARIO_DIGEST))
    ledger = SimpleNamespace(records=(window_record, *records))
    with pytest.raises(ValueError, match="plugin digest differs from sealed plugin bytes"):
        urban._validate_physics(run, object(), ledger, {1: trajectory}, raw, b"different bytes")


def test_builder_declares_private_native_evidence_for_flight_and_verifier(tmp_path):
    import yaml
    from tests.test_urban_recovery_inputs import _inputs

    _, task, agents = _inputs(tmp_path)
    environment = yaml.safe_load((tmp_path / "environment/environment.yaml").read_text())
    flight = next(provider for provider in environment["providers"] if provider["provider_id"] == "flight")
    artifacts = {item["artifact_type"]: item for item in flight["artifact_requirements"]}
    assert set(artifacts) == {
        "trajectory",
        "observation",
        "sensor-frame",
        "camera-frame-data",
        "gazebo.physics-journal",
        "gazebo.physics-plugin",
    }
    verifier_artifacts = {item.artifact_type: item for item in task.verifier.artifact_requirements}
    for artifact_type in ("gazebo.physics-journal", "gazebo.physics-plugin"):
        assert artifacts[artifact_type]["visibility"] == "private"
        assert artifacts[artifact_type] == verifier_artifacts[artifact_type].model_dump(mode="json")
    assert all(not agent.artifact_requirements for agent in agents)


def test_legacy_inventory_without_native_bytes_cannot_be_verified(tmp_path):
    from tests.test_urban_recovery_verifier import _sealed_fixture

    run, seal, _ = _sealed_fixture(tmp_path)
    run.artifact_requirements = tuple(item for item in run.artifact_requirements if not item.artifact_type.startswith("gazebo.physics-"))
    with pytest.raises(ValueError, match="exact urban inventory"):
        urban._validated_sealed_bytes(run, seal, tmp_path)


def test_one_well_formed_window_is_not_a_3000_tick_physics_proof():
    _, raw, window, records, trajectory = _fixture()
    window_record = _record(urban._ENGINE_WINDOW_SCHEMA, {"window_json": canonical_json_bytes(window.model_dump(mode="json")).decode()}, at=window.at)
    run = SimpleNamespace(run_id=RUN_ID, scenario=SimpleNamespace(scenario_digest=SCENARIO_DIGEST))
    with pytest.raises(urban.UrbanRecoveryVerificationError) as error:
        urban._validate_physics(run, object(), SimpleNamespace(records=(window_record, *records)), {1: trajectory}, raw, PLUGIN)
    assert error.value.failure_class == "urban.evidence.physics_window_incomplete"
