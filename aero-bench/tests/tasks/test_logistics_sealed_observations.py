"""Offline journal replay tests; motion and seals here are synthetic fixtures."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json

import pytest

from aero_bench.artifacts.contracts import ArtifactRecord
from aero_bench.config.models import NamedValue
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.events import RunEventAudience
from aero_bench.runtime.ledger import EventLedger
from aero_bench.runtime.sealed_motion import SealedMotionFrame
from aero_bench.tasks.logistics.sealed_observations import (
    load_sealed_logistics_observations,
    replay_logistics_motion,
    validate_logistics_observation_records,
)
from aero_bench.serialization import canonical_json_bytes
from tests.providers.test_logistics_business_service import _requirement
from tests.tasks.test_logistics_runtime_airspace import ZONE
from tests.tasks.test_logistics_runtime_hook import (
    AT,
    _build_hook,
    _closed_stage,
    _running_stack,
    build_fixture,
)


ROOT = "e" * 64


def test_native_sealed_loader_uses_native_package_proof(tmp_path,monkeypatch):
    """A real compiled native package must reach sealed-source loading."""
    from tests.tasks.test_native_parcel_integration import _native_fixture
    from aero_bench.tasks.logistics import sealed_observations as module
    from aero_bench.tasks.logistics.native_parcel_integration import (
        NATIVE_BUSINESS_PROVIDER_ID, NATIVE_PARCEL_PACKAGE_ID,
        prove_declared_native_config_matches_resolved_run,
    )
    fixture=_native_fixture(tmp_path)
    run=fixture["resolved_run"]
    assert run.task.package.package_id == NATIVE_PARCEL_PACKAGE_ID
    declared=prove_declared_native_config_matches_resolved_run(
        run=run,reader=fixture["reader"])
    assert declared.model_dump(mode="json") == fixture["config_document"]
    def wrong_base_proof(*args,**kwargs):
        raise AssertionError("native sealed loader called base package proof")
    monkeypatch.setattr(module.LogisticsRuntimeHookFactory,
        "_prove_declared_config_matches_resolved_run",wrong_base_proof)
    def reached_source(**kwargs):
        raise ValueError("reached actual sealed source boundary")
    monkeypatch.setattr(module,"load_sealed_px4_motion",reached_source)
    with pytest.raises(ValueError,match="reached actual sealed source boundary"):
        load_sealed_logistics_observations(run=run,reader=fixture["reader"],
            seal=None,seal_root=tmp_path,business_provider_id=NATIVE_BUSINESS_PROVIDER_ID)


def test_native_sealed_loader_preserves_full_package_equality(tmp_path,monkeypatch):
    """Matching IDs cannot hide a different cargo mass in the pinned config."""
    import copy
    from tests.tasks.test_native_parcel_integration import _native_fixture
    from aero_bench.tasks.logistics import sealed_observations as module
    from aero_bench.tasks.logistics.native_parcel_integration import (
        NATIVE_BUSINESS_PROVIDER_ID,NativeParcelPackageResolutionError,
    )
    fixture=_native_fixture(tmp_path/"original")
    changed=copy.deepcopy(fixture["config_document"])
    changed["task_package"]["orders"][0]["cargo_mass_kg"] += 0.5
    mismatch=_native_fixture(tmp_path/"mismatch",config_document=changed)
    def forbidden_source(**kwargs):
        raise AssertionError("source loading preceded exact native package proof")
    monkeypatch.setattr(module,"load_sealed_px4_motion",forbidden_source)
    with pytest.raises(NativeParcelPackageResolutionError,match="cargo_mass_kg"):
        load_sealed_logistics_observations(run=mismatch["resolved_run"],
            reader=mismatch["reader"],seal=None,seal_root=tmp_path,
            business_provider_id=NATIVE_BUSINESS_PROVIDER_ID)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    root = tmp_path_factory.mktemp("offline-observations")
    fixture = build_fixture(root / "bundle", no_fly_zones=(ZONE,))
    frames = []
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    for tick in (1, 2):
        at = SimulationTime(tick=tick, sim_time_ns=tick * 1_000_000_000)
        _, scene, event, _ = _closed_stage(fixture, at=at)
        frames.append(SealedMotionFrame(scene, (scene.stage_barrier,), (event,), (), 0))
    replay = replay_logistics_motion(
        run_id=fixture.resolved_run.run_id,
        config=fixture.business_config,
        scenario=fixture.scenario,
        step_ns=fixture.environment.clock.step_ns,
        frames=tuple(frames),
    )
    ticks = []
    for tick in replay.ticks:
        ledger.append_event(
            source="harness", event_type="stage.barrier-closed", time=tick.at
        )
        ledger.append_event(
            source="logistics.business",
            provider_id="logistics.business",
            event_type="logistics.observation.ingested",
            time=tick.at,
            payload=tuple(
                NamedValue(name=name, value=value)
                for name, value in {
                    "run_id": fixture.resolved_run.run_id,
                    "provider_id": "logistics.business",
                    "journal_digest": tick.journal_digest,
                    "sequence_count": tick.sequence_count,
                    "replayed": False,
                }.items()
            ),
        )
        for segment in tick.segments:
            ledger.append_event(
                source="harness",
                event_type="logistics.airspace.segment.v1",
                time=tick.at,
                visibility=(
                    RunEventAudience(scope="private"),
                    RunEventAudience(scope="verifier"),
                ),
                payload=(
                    NamedValue(name="segment_digest", value=segment.canonical_digest()),
                    NamedValue(
                        name="segment_json",
                        value=canonical_json_bytes(
                            segment.model_dump(mode="json")
                        ).decode(),
                    ),
                ),
            )
        commit = ledger.append_event(
            source="harness", event_type="barrier.committed", time=tick.at
        )
        ticks.append(replace(tick, commit_sequence=commit.sequence))
    replay = replace(replay, ticks=tuple(ticks))
    document = {
        "schema_version": "aero-bench.logistics-business-state/v1",
        "source_artifact_id": "artifact.logistics",
        "event_chain_root": ROOT,
        "observation_journal": replay.journal.model_dump(mode="json"),
        "observation_journal_digest": replay.journal.canonical_digest(),
        "facilities": fixture.package.facilities.model_dump(mode="json"),
        "principal_bindings": [
            item.model_dump(mode="json")
            for item in fixture.business_config.principal_bindings
        ],
    }
    raw = canonical_json_bytes(document)
    requirement = _requirement()
    artifact = ArtifactRecord(
        **{
            name: requirement[name]
            for name in ArtifactRecord.model_fields
            if name in requirement
        },
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
    )
    return (
        fixture,
        tuple(frames),
        dict(
            replay=replay,
            records=ledger.records,
            business_document=document,
            artifact=artifact,
            config=fixture.business_config,
            event_chain_root=ROOT,
        ),
    )


def test_replay_rederives_journal_and_private_airspace(source):
    _, _, inputs = source
    validate_logistics_observation_records(**inputs)
    assert inputs["replay"].journal.last_sequence == 2
    assert not inputs["replay"].ticks[0].segments
    segment = inputs["replay"].ticks[1].segments[0]
    assert segment.report.violations[0].event_type == "stationary_activation"
    assert segment.provenance_verified is False
    assert all(
        not record.observation.provenance_verified
        for record in inputs["replay"].journal.records
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("observation_journal_digest", "a" * 64),
        ("event_chain_root", "a" * 64),
        ("source_artifact_id", "other"),
        ("schema_version", "wrong"),
        ("facilities", {}),
        ("principal_bindings", []),
    ],
)
def test_business_artifact_cannot_rewrite_declaration_or_replayed_journal(
    source, field, value
):
    _, _, inputs = source
    with pytest.raises(ValueError):
        validate_logistics_observation_records(
            **{
                **inputs,
                "business_document": {**inputs["business_document"], field: value},
            }
        )


@pytest.mark.parametrize(
    "event_type", ["logistics.observation.ingested", "logistics.airspace.segment.v1"]
)
def test_omitting_hook_records_is_rejected(source, event_type):
    _, _, inputs = source
    records = tuple(
        record for record in inputs["records"] if record.event.event_type != event_type
    )
    with pytest.raises(ValueError):
        validate_logistics_observation_records(**{**inputs, "records": records})


def test_recomputed_journal_cannot_omit_a_native_sample(source):
    _, _, inputs = source
    journal = inputs["replay"].journal.model_copy(
        update={"records": inputs["replay"].journal.records[:1]}
    )
    with pytest.raises(ValueError, match="closed native motion reconstruction"):
        validate_logistics_observation_records(
            **{
                **inputs,
                "business_document": {
                    **inputs["business_document"],
                    "observation_journal": journal.model_dump(mode="json"),
                    "observation_journal_digest": journal.canonical_digest(),
                },
            }
        )


def test_missing_initial_motion_is_rejected(source):
    fixture, frames, _ = source
    with pytest.raises(ValueError, match="consecutive barrier"):
        replay_logistics_motion(
            run_id=fixture.resolved_run.run_id,
            config=fixture.business_config,
            scenario=fixture.scenario,
            step_ns=fixture.environment.clock.step_ns,
            frames=frames[1:],
        )


def test_public_airspace_or_late_hook_record_is_rejected(source):
    _, _, inputs = source
    index = next(
        index
        for index, record in enumerate(inputs["records"])
        if record.event.event_type == "logistics.airspace.segment.v1"
    )
    original = inputs["records"][index]
    for changed in (
        original.model_copy(
            update={"sequence": inputs["replay"].ticks[-1].commit_sequence + 1}
        ),
        original.model_copy(
            update={
                "event": original.event.model_copy(
                    update={"visibility": (RunEventAudience(scope="public"),)}
                )
            }
        ),
    ):
        records = (*inputs["records"][:index], changed, *inputs["records"][index + 1 :])
        with pytest.raises(ValueError):
            validate_logistics_observation_records(**{**inputs, "records": records})


def test_offline_replay_matches_actual_business_rpc_journal(source, tmp_path):
    fixture, frames, _ = source

    async def check():
        async with _running_stack(fixture, tmp_path) as (service, client):
            ledger = EventLedger(run_id=fixture.resolved_run.run_id)
            ledger.append_event(
                source="harness", event_type="stage.barrier-closed", time=AT
            )
            hook = _build_hook(fixture, client, ledger)
            frame = frames[0]
            await hook.on_stage_barriers_closed(
                frame.events,
                scene_state=frame.scene_state,
                stage_barriers=frame.stage_barriers,
                target=AT,
            )
            commit = ledger.append_event(
                source="harness", event_type="barrier.committed", time=AT
            )
            replay = replay_logistics_motion(
                run_id=fixture.resolved_run.run_id,
                config=fixture.business_config,
                scenario=fixture.scenario,
                step_ns=fixture.environment.clock.step_ns,
                frames=(replace(frame, commit_sequence=commit.sequence),),
            )
            assert replay.journal == service._observations
            raw = service._artifact_content(event_chain_root=ROOT)
            requirement = _requirement()
            artifact = ArtifactRecord(
                **{
                    name: requirement[name]
                    for name in ArtifactRecord.model_fields
                    if name in requirement
                },
                sha256=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
            )
            validate_logistics_observation_records(
                replay=replay,
                records=ledger.records,
                business_document=json.loads(raw),
                artifact=artifact,
                config=fixture.business_config,
                event_chain_root=ROOT,
            )

    asyncio.run(check())
