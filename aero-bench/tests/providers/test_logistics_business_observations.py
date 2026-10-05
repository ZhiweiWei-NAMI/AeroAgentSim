"""Service/client tests for the private physical-observation ingest surface.

The only legitimate observation-ingest path is the private
``logistics.observation.ingest`` operation carried by the real JSON-line
loopback into the actual logistics business workspace workload.  These tests
drive the real provider session client against a typed, honestly-derived batch
(from the actual closed motion stage under explicit pose-reference
calibrations), then exercise replay/deduplication, atomic conflict rejection,
foreign/stale/mismatched-config/token rejection with zero journal mutation, and
persistence of the immutable observation journal inside the declared business
artifact.  Physical pickup/handoff/deliver/charge transitions stay refused.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aero_bench.providers.logistics_business import (
    LOGISTICS_ORDER_CREATE_TOOL,
    LogisticsBusinessProviderError,
)
from aero_bench.providers.rpc import ProviderRemoteError
from aero_bench.runtime.contracts import (
    CommandRequest,
    ProviderFinalizationRequest,
    SimulationTime,
)
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import (
    PresenceTolerances,
    assess_facility_presence,
)
from aero_bench.tasks.logistics.observation_ingress import (
    LOGISTICS_OBSERVATION_INGEST_OPERATION,
    LogisticsObservationBatch,
    LogisticsObservationSpec,
    LogisticsObservationSpecItem,
    ObservationIngestResult,
    ObservationJournalRecord,
    build_observation_batch,
    derive_physical_observations,
)
from aero_bench.tasks.logistics.physical_observations import (
    FacilityPadPhysicalObservation,
    observation_digest_value,
)

from tests.providers.test_logistics_business_service import (
    _SERVICE,
    PROVIDER_ID,
    PROTOCOL_VERSION,
    SEED,
    SESSION_TOKEN,
)
from tests.tasks.test_logistics_physical_observations import (
    _scene_state,
    _state_event,
    _state_event_values,
    _state_sample,
)
from tests.tasks.test_logistics_runtime_hook import (
    AT,
    _CohesiveFixture,
    _derive_observation_batch,
    _observation_spec,
    _pose_references,
    _presence_tolerances,
    _running_stack,
    _stage_request_with_scenario,
    build_fixture,
)


def _ingest_payload(batch, *, session_token: str = SESSION_TOKEN) -> dict[str, object]:
    """A typed ingest frame the real service handle consumes (token included)."""
    return {
        "provider_id": PROVIDER_ID,
        "run_id": batch.run_id,
        "session_token": session_token,
        "protocol_version": PROTOCOL_VERSION,
        "batch": batch.model_dump(mode="json"),
    }


def _command_args(*pairs: tuple[str, object]) -> tuple[tuple[str, object], ...]:
    return tuple(sorted(pairs))


def _refused_command(client, *, tool_id: str, command_id: str, **arguments: object):
    return CommandRequest(
        run_id=client._run_id,
        command_id=command_id,
        agent_id="agent.provider",
        tool_id=tool_id,
        issued_at=AT,
        arguments=tuple(
            __import__("aero_bench.config.models", fromlist=["NamedValue"]).NamedValue(
                name=name, value=value
            )
            for name, value in arguments.items()
        ),
    )


@pytest.fixture(scope="module")
def logistics_cohesive_fixture(tmp_path_factory) -> _CohesiveFixture:
    root = Path(tmp_path_factory.mktemp("logistics-business-observations"))
    return build_fixture(root)


def test_observation_ingest_journals_through_real_rpc_and_persists(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            result = await client.ingest_observations(batch)
            assert result.replayed is False
            assert result.sequence_count == 1
            assert len(result.accepted_records) == 1
            assert result.journal_digest == service._observations.canonical_digest()

            records = service._observations.records
            assert len(records) == 1
            observation = records[0].observation
            assert observation.run_id == fixture.resolved_run.run_id
            assert observation.scenario_digest == fixture.scenario.scenario_digest
            assert observation.aircraft_id == "fleet-alpha:1"
            assert observation.assessment.eligible is True
            assert observation.source_stage_barrier_digest == (
                batch.source_stage_barrier_digest
            )

            receipt = await client.finalize(
                ProviderFinalizationRequest(
                    schema_version="aero-bench.provider-finalization-request/v1",
                    run_id=fixture.resolved_run.run_id,
                    terminal_event="run.completed",
                    terminal_time=AT,
                    event_chain_root="d" * 64,
                )
            )
            assert receipt.artifacts[0].artifact_id == "artifact.logistics"
            artifact = tmp_path / "artifacts" / "logistics/state.json"
            document = json.loads(artifact.read_text(encoding="utf-8"))
            assert len(document["observation_journal"]["records"]) == 1
            assert document["observation_journal"]["records"][0]["sequence"] == 1
            assert (
                document["observation_journal_digest"]
                == service._observations.canonical_digest()
            )
            assert document["observation_journal"]["records"][0]["observation"][
                "source_stage_barrier_digest"
            ] == batch.source_stage_barrier_digest

    asyncio.run(run())


def test_observation_ingest_replay_dedups_journal(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            first = await client.ingest_observations(batch)
            assert first.replayed is False
            digest_after_first = service._observations.canonical_digest()

            replay = await client.ingest_observations(batch)
            assert replay.replayed is True
            assert replay.accepted_records == ()
            assert replay.sequence_count == 1
            assert replay.journal_digest == digest_after_first
            assert service._observations.canonical_digest() == digest_after_first
            assert service._observations.records[0].sequence == 1

    asyncio.run(run())


def test_observation_ingest_conflict_is_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    conflicting = _derive_observation_batch(fixture, up_m_offset=0.03)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            await client.ingest_observations(batch)
            digest_before = service._observations.canonical_digest()

            # Same closed stage barrier but a divergent observation content: the
            # append kernel rejects it as a conflict, never mutating the journal.
            assert conflicting.source_stage_barrier_digest == batch.source_stage_barrier_digest
            assert conflicting.observation_digests != batch.observation_digests
            with pytest.raises(ProviderRemoteError) as errors:
                await client.ingest_observations(conflicting)
            assert errors.value.code == "request.invalid"
            assert "diverges" in errors.value.detail
            assert service._observations.canonical_digest() == digest_before
            assert len(service._observations.records) == 1

    asyncio.run(run())


def test_observation_ingest_foreign_run_is_rejected_with_zero_mutation(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture
    foreign_batch = _derive_observation_batch(fixture, run_id="b" * 64)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            # Client-side pre-check before any RPC frame.
            with pytest.raises(LogisticsBusinessProviderError, match="another run"):
                await client.ingest_observations(foreign_batch)
            assert service._observations.records == ()

            # The authoritative service atomically rejects the foreign frame too.
            with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
                await service.handle(
                    LOGISTICS_OBSERVATION_INGEST_OPERATION,
                    _ingest_payload(foreign_batch),
                )
            assert errors.value.code == "identity.mismatch"
            assert service._observations.records == ()

    asyncio.run(run())


def test_observation_ingest_stale_time_is_rejected_with_zero_mutation(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture
    stale_batch = _derive_observation_batch(
        fixture, at=SimulationTime(tick=2, sim_time_ns=2_000_000_000)
    )

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            # The client guards the authoritative provider time before the wire.
            with pytest.raises(LogisticsBusinessProviderError, match="authoritative provider time"):
                await client.ingest_observations(stale_batch)

            # The service rejects a frame whose batch.at is not the barrier.
            with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
                await service.handle(
                    LOGISTICS_OBSERVATION_INGEST_OPERATION,
                    _ingest_payload(stale_batch),
                )
            assert errors.value.code == "request.invalid"
            assert "time must equal the current provider barrier" in errors.value.detail
            assert service._observations.records == ()

    asyncio.run(run())


def test_observation_ingest_mismatched_config_payload_is_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    torn = batch.model_dump(mode="json")
    torn["scenario_digest"] = "2" * 64
    for observation in torn["observations"]:
        observation["scenario_digest"] = "2" * 64

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            payload = _ingest_payload(batch)
            payload["batch"] = torn
            with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
                await service.handle(
                    LOGISTICS_OBSERVATION_INGEST_OPERATION, payload
                )
            assert errors.value.code == "request.invalid"
            assert service._observations.records == ()

    asyncio.run(run())


def test_observation_ingest_wrong_session_token_is_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            forged = _ingest_payload(batch, session_token="8" * 64)
            with pytest.raises(_SERVICE.LogisticsBusinessServiceError, match="session token") as errors:
                await service.handle(
                    LOGISTICS_OBSERVATION_INGEST_OPERATION, forged
                )
            assert errors.value.code == "principal.denied"
            assert service._observations.records == ()

    asyncio.run(run())


def test_physical_transitions_stay_refused_after_observation(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            result = await client.ingest_observations(batch)
            assert result.sequence_count == 1

            for tool_id, command_id, arguments in (
                (
                    "logistics.order.pick_up",
                    "command.pickup",
                    {"order_id": "order-1", "expected_version": 0, "evidence_ref": "evidence.photo.1"},
                ),
                (
                    "logistics.order.deliver",
                    "command.deliver",
                    {"order_id": "order-1", "expected_version": 0, "evidence_ref": "evidence.photo.1", "handoff_facility_id": "hub-3"},
                ),
            ):
                outcome = await client.handle_command(
                    _refused_command(client, tool_id=tool_id, command_id=command_id, **arguments)
                )
                assert tuple(item.phase for item in outcome.receipts) == (
                    "received",
                    "failed",
                )
                assert "physical evidence interface pending" in (outcome.receipts[-1].detail or "")
                assert outcome.events == ()

            # The observation journal is untouched by the refused transitions.
            assert len(service._observations.records) == 1

    asyncio.run(run())


def _batch_for_pad(
    fixture: _CohesiveFixture, pad_index: int
) -> LogisticsObservationBatch:
    """Derive an honest batch for a different canonical pad of facility-1."""
    run_id = fixture.resolved_run.run_id
    pads = facility_landing_pads(fixture.package.facilities.require("facility-1"))
    pad = pads[pad_index]
    up_m = pad.y + 0.1
    sample = _state_sample(
        AT,
        run_id=run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=up_m,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=(f"ground.pad.{pad_index}",),
    )
    scene_state = _scene_state(
        run_id=run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        at=AT,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=up_m,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=(f"ground.pad.{pad_index}",),
        simulation_time_ns=AT.sim_time_ns,
    )
    event = _state_event(AT, values=values)
    observations = derive_physical_observations(
        scene_state=scene_state,
        events=(event,),
        package=fixture.package,
        bindings=fixture.bindings,
        scenario=fixture.scenario,
        spec=LogisticsObservationSpec(
            schema_version="aero-bench.logistics-observation-spec/v1",
            items=(
                LogisticsObservationSpecItem(
                    aircraft_id="fleet-alpha:1",
                    facility_id="facility-1",
                    pad_index=pad_index,
                ),
            ),
        ),
        pose_references=_pose_references(),
        tolerances=_presence_tolerances(),
        stage_barriers=(scene_state.stage_barrier,),
        expected_run_id=run_id,
        target=AT,
    )
    return build_observation_batch(
        observations=observations,
        run_id=run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        at=AT,
        source_scene_state_digest=scene_state.scene_state_digest,
        source_stage_barrier_digest=scene_state.stage_barrier.barrier_digest,
    )


def _rebuild_tampered(
    fixture: _CohesiveFixture,
    batch: LogisticsObservationBatch,
    mutate,
) -> LogisticsObservationBatch:
    """Recompute every digest over a mutated first observation (forgery closure)."""
    obs = batch.observations[0]
    altered = mutate(obs)
    altered = altered.model_copy(
        update={"observation_digest": observation_digest_value(altered)}
    )
    altered = FacilityPadPhysicalObservation.model_validate(altered.model_dump())
    return LogisticsObservationBatch.model_validate(
        batch.model_copy(update={"observations": (altered,)}).model_dump()
    )


def _fabricated_result(
    fixture: _CohesiveFixture,
    batch: LogisticsObservationBatch,
    *,
    replayed: bool,
    journal_digest: str = "e" * 64,
    sequence_count: int = 0,
    accepted_records: tuple[ObservationJournalRecord, ...] = (),
) -> dict[str, object]:
    return {
        "result": ObservationIngestResult(
            schema_version="aero-bench.logistics-observation-ingest-result/v1",
            run_id=fixture.resolved_run.run_id,
            provider_id=PROVIDER_ID,
            at=batch.at,
            journal_digest=journal_digest,
            sequence_count=sequence_count,
            accepted_records=accepted_records,
            replayed=replayed,
        ).model_dump(mode="json")
    }


def test_observation_ingest_unbound_foreign_provider_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """Recomputed-hash forgery with a foreign provider is rejected (closure)."""
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    tampered = _rebuild_tampered(
        fixture,
        batch,
        lambda obs: obs.model_copy(
            update={
                "provider_id": "foreign.flight",
                "sample": obs.sample.model_copy(
                    update={"provider_id": "foreign.flight"}
                ),
                "event_binding": obs.event_binding.model_copy(
                    update={"provider_id": "foreign.flight"}
                ),
            }
        ),
    )

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            with pytest.raises(ProviderRemoteError) as errors:
                await client.ingest_observations(tampered)
            assert errors.value.code == "request.invalid"
            assert "foreign.flight" in errors.value.detail
            assert "declared native binding" in errors.value.detail
            assert service._observations.records == ()
            assert client._observation_journal.records == ()

    asyncio.run(run())


def test_observation_ingest_calibration_mismatch_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """A forged payload with a guessed pose-reference calibration is rejected."""
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    forged_profile = batch.observations[0].profile.model_copy(
        update={"pose_reference_above_contact_m": 0.2}
    )
    tampered = _rebuild_tampered(
        fixture,
        batch,
        lambda obs: obs.model_copy(update={"profile": forged_profile}),
    )

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            with pytest.raises(ProviderRemoteError) as errors:
                await client.ingest_observations(tampered)
            assert errors.value.code == "request.invalid"
            assert "pose-reference calibration" in errors.value.detail
            assert service._observations.records == ()

    asyncio.run(run())


def test_observation_ingest_assessment_tolerance_mismatch_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """An assessment forged under different tolerances is rejected atomically."""
    fixture = logistics_cohesive_fixture
    # The declared tolerance is 0.05 m; the forged assessment uses 0.01 m so the
    # 0.03 m contact offset becomes ineligible under the forged tolerances but
    # remains eligible under the declared ones.
    drifted = _derive_observation_batch(fixture, up_m_offset=0.03)
    obs = drifted.observations[0]
    tighter = PresenceTolerances(
        vertical_tolerance_m=0.01,
        horizontal_uncertainty_m=0.0,
        max_stationary_speed_m_s=0.5,
    )
    forged_assessment = assess_facility_presence(
        pad=obs.pad,
        sample=obs.sample,
        profile=obs.profile,
        event=obs.event_binding,
        tolerances=tighter,
    )
    assert forged_assessment.eligible is False
    tampered = _rebuild_tampered(
        fixture,
        drifted,
        lambda o: o.model_copy(update={"assessment": forged_assessment}),
    )

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            with pytest.raises(ProviderRemoteError) as errors:
                await client.ingest_observations(tampered)
            assert errors.value.code == "request.invalid"
            assert "not reproducible under the declared presence tolerances" in (
                errors.value.detail
            )
            assert service._observations.records == ()

    asyncio.run(run())


def test_observation_ingest_plan_item_not_declared_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """A package-valid pad the plan does not declare is rejected atomically."""
    fixture = logistics_cohesive_fixture
    # facility-1 pad 1 is a real declared pad (package-valid) but the declared
    # observation plan only contains (fleet-alpha:1, facility-1, pad 0).
    batch = _batch_for_pad(fixture, pad_index=1)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            with pytest.raises(ProviderRemoteError) as errors:
                await client.ingest_observations(batch)
            assert errors.value.code == "request.invalid"
            assert "observation plan declares no" in errors.value.detail
            assert service._observations.records == ()

    asyncio.run(run())


def test_client_rejects_false_replay_ack_for_unsubmitted_barrier(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """The client binds a replay ack to a barrier it actually ingested."""
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            async def fabricated(operation, payload):
                return _fabricated_result(
                    fixture,
                    batch,
                    replayed=True,
                    journal_digest="f" * 64,
                    sequence_count=0,
                )

            client._request = fabricated
            with pytest.raises(
                LogisticsBusinessProviderError, match="false replay"
            ):
                await client.ingest_observations(batch)
            assert service._observations.records == ()
            assert client._observation_journal.records == ()

    asyncio.run(run())


def test_client_rejects_ack_not_covering_submitted_records(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """A non-replay ack must accept exactly the submitted record set."""
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    other = _derive_observation_batch(fixture, up_m_offset=0.03).observations[0]
    wrong_record = ObservationJournalRecord(
        sequence=1,
        observation=other,
        received_at=AT,
    )

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            async def fabricated(operation, payload):
                return _fabricated_result(
                    fixture,
                    batch,
                    replayed=False,
                    sequence_count=1,
                    accepted_records=(wrong_record,),
                )

            client._request = fabricated
            with pytest.raises(
                LogisticsBusinessProviderError, match="exact submitted record set"
            ):
                await client.ingest_observations(batch)
            assert service._observations.records == ()

    asyncio.run(run())


def test_client_rejects_ack_without_sequence_continuation(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """A fresh ack must continue the confirmed journal sequence."""
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    obs = batch.observations[0]
    wrong_sequence = ObservationJournalRecord(
        sequence=2,
        observation=obs,
        received_at=AT,
    )

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            async def fabricated(operation, payload):
                return _fabricated_result(
                    fixture,
                    batch,
                    replayed=False,
                    sequence_count=2,
                    accepted_records=(wrong_sequence,),
                )

            client._request = fabricated
            with pytest.raises(
                LogisticsBusinessProviderError, match="continue the confirmed journal sequence"
            ):
                await client.ingest_observations(batch)
            assert service._observations.records == ()

    asyncio.run(run())


def test_observation_ingest_disabled_config_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """An explicitly disabled observation branch rejects every batch atomically."""
    from aero_bench.providers.logistics_business.config import (
        LogisticsBusinessConfig,
    )

    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    document = fixture.business_config.model_dump(mode="json")
    document["observation"] = None
    disabled = LogisticsBusinessConfig.model_validate(document)
    disabled_fixture = replace(fixture, business_config=disabled)

    async def run() -> None:
        async with _running_stack(disabled_fixture, tmp_path) as (
            service,
            client,
        ):
            with pytest.raises(ProviderRemoteError) as errors:
                await client.ingest_observations(batch)
            assert errors.value.code == "request.invalid"
            assert "observation=null" in errors.value.detail
            assert "disabled" in errors.value.detail
            assert service._observations.records == ()

    asyncio.run(run())


def test_observation_ingest_forged_body_width_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """A recomputed-hash forgery of the declared body footprint width is rejected.

    The presence profile ``body_width_m`` must equal the canonical package
    performance profile ``aircraft_body.x_m`` for the bound fleet entry; a
    forged width with a freshly recomputed assessment AND a freshly recomputed
    content hash is still rejected with zero journal mutation.
    """
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    declared = batch.observations[0].profile.body_width_m
    forged = 0.001
    assert declared != forged

    def mutate(obs):
        profile = obs.profile.model_copy(update={"body_width_m": forged})
        assessment = assess_facility_presence(
            pad=obs.pad,
            sample=obs.sample,
            profile=profile,
            event=obs.event_binding,
            tolerances=_presence_tolerances(),
        )
        return obs.model_copy(update={"profile": profile, "assessment": assessment})

    tampered = _rebuild_tampered(fixture, batch, mutate)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            with pytest.raises(ProviderRemoteError) as errors:
                await client.ingest_observations(tampered)
            assert errors.value.code == "request.invalid"
            assert "body_width_m" in errors.value.detail
            assert "0.001" in errors.value.detail
            assert "declared package profile" in errors.value.detail
            assert service._observations.records == ()
            assert client._observation_journal.records == ()

    asyncio.run(run())


def test_observation_ingest_forged_body_depth_rejected_atomically(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """A recomputed-hash forgery of the declared body footprint depth is rejected.

    The presence profile ``body_depth_m`` must equal the canonical package
    performance profile ``aircraft_body.z_m`` for the bound fleet entry; a
    forged depth with a freshly recomputed assessment AND a freshly recomputed
    content hash is still rejected with zero journal mutation.
    """
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    declared = batch.observations[0].profile.body_depth_m
    forged = 0.001
    assert declared != forged

    def mutate(obs):
        profile = obs.profile.model_copy(update={"body_depth_m": forged})
        assessment = assess_facility_presence(
            pad=obs.pad,
            sample=obs.sample,
            profile=profile,
            event=obs.event_binding,
            tolerances=_presence_tolerances(),
        )
        return obs.model_copy(update={"profile": profile, "assessment": assessment})

    tampered = _rebuild_tampered(fixture, batch, mutate)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            with pytest.raises(ProviderRemoteError) as errors:
                await client.ingest_observations(tampered)
            assert errors.value.code == "request.invalid"
            assert "body_depth_m" in errors.value.detail
            assert "0.001" in errors.value.detail
            assert "declared package profile" in errors.value.detail
            assert service._observations.records == ()
            assert client._observation_journal.records == ()

    asyncio.run(run())


def test_client_rejects_false_fresh_digest_on_first_barrier(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """An arbitrary changed journal digest on the very first ingest is rejected.

    The client computes the canonical expected digest from its empty confirmed
    journal and the submitted batch; the workload returning a fabricated
    ``journal_digest`` (here ``f``*64) is not an acceptance and leaves both the
    service and the client journal empty.
    """
    fixture = logistics_cohesive_fixture
    batch = _derive_observation_batch(fixture)
    honest_record = ObservationJournalRecord(
        sequence=1,
        observation=batch.observations[0],
        received_at=AT,
    )

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            async def fabricated(operation, payload):
                return _fabricated_result(
                    fixture,
                    batch,
                    replayed=False,
                    sequence_count=1,
                    accepted_records=(honest_record,),
                    journal_digest="f" * 64,
                )

            client._request = fabricated
            with pytest.raises(
                LogisticsBusinessProviderError, match="journal digest"
            ):
                await client.ingest_observations(batch)
            assert service._observations.records == ()
            assert client._observation_journal.records == ()

    asyncio.run(run())


def test_client_rejects_false_fresh_digest_on_later_barrier(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """An arbitrary changed journal digest after an honest barrier is rejected.

    After one honest barrier is confirmed at tick 1, a second barrier at tick 2
    must continue the confirmed journal to the canonical digest.  A fabricated
    ``journal_digest`` on that later fresh acknowledgment is rejected and the
    client journal stays at the one confirmed record.
    """
    fixture = logistics_cohesive_fixture
    at2 = SimulationTime(tick=2, sim_time_ns=2_000_000_000)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            batch1 = _derive_observation_batch(fixture)
            first = await client.ingest_observations(batch1)
            assert first.replayed is False
            assert first.sequence_count == 1
            confirmed_digest = first.journal_digest

            req1 = _stage_request_with_scenario(
                tick=1,
                sim_time_ns=1_000_000_000,
                scenario=fixture.scenario,
                run_id=fixture.resolved_run.run_id,
            )
            req2 = _stage_request_with_scenario(
                tick=2,
                sim_time_ns=2_000_000_000,
                scenario=fixture.scenario,
                run_id=fixture.resolved_run.run_id,
                previous_scene_state=req1.scene_state,
            )
            await client.step_stage(req2)
            batch2 = _derive_observation_batch(fixture, at=at2)
            honest_record = ObservationJournalRecord(
                sequence=2,
                observation=batch2.observations[0],
                received_at=at2,
            )

            async def fabricated(operation, payload):
                return _fabricated_result(
                    fixture,
                    batch2,
                    replayed=False,
                    sequence_count=2,
                    accepted_records=(honest_record,),
                    journal_digest="f" * 64,
                )

            client._request = fabricated
            with pytest.raises(
                LogisticsBusinessProviderError, match="journal digest"
            ):
                await client.ingest_observations(batch2)
            assert service._observations.canonical_digest() == confirmed_digest
            assert len(service._observations.records) == 1
            assert len(client._observation_journal.records) == 1
            assert client._observation_journal.canonical_digest() == confirmed_digest

    asyncio.run(run())


def test_client_honest_multi_barrier_replay_and_reset_positive_controls(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    """Honest multi-barrier continuation, replay and reset all validate cleanly."""
    fixture = logistics_cohesive_fixture
    at2 = SimulationTime(tick=2, sim_time_ns=2_000_000_000)

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            batch1 = _derive_observation_batch(fixture)
            first = await client.ingest_observations(batch1)
            assert first.replayed is False
            assert first.sequence_count == 1
            digest1 = first.journal_digest
            assert service._observations.canonical_digest() == digest1

            replay1 = await client.ingest_observations(batch1)
            assert replay1.replayed is True
            assert replay1.sequence_count == 1
            assert replay1.journal_digest == digest1
            assert service._observations.canonical_digest() == digest1

            req1 = _stage_request_with_scenario(
                tick=1,
                sim_time_ns=1_000_000_000,
                scenario=fixture.scenario,
                run_id=fixture.resolved_run.run_id,
            )
            req2 = _stage_request_with_scenario(
                tick=2,
                sim_time_ns=2_000_000_000,
                scenario=fixture.scenario,
                run_id=fixture.resolved_run.run_id,
                previous_scene_state=req1.scene_state,
            )
            await client.step_stage(req2)
            batch2 = _derive_observation_batch(fixture, at=at2)
            second = await client.ingest_observations(batch2)
            assert second.replayed is False
            assert second.sequence_count == 2
            assert len(second.accepted_records) == 1
            assert second.accepted_records[0].sequence == 2
            digest2 = second.journal_digest
            assert digest2 != digest1
            assert len(client._observation_journal.records) == 2
            assert service._observations.canonical_digest() == digest2

            replay2 = await client.ingest_observations(batch2)
            assert replay2.replayed is True
            assert replay2.sequence_count == 2
            assert replay2.journal_digest == digest2

            # Reset clears the confirmed journal exactly like the workload.
            await client.reset(seed=SEED)
            assert len(client._observation_journal.records) == 0
            assert service._observations.records == ()

            # A fresh re-step to tick 1 re-ingests the first barrier as a NEW
            # fresh acceptance (sequence starts again at 1, same canonical
            # digest), proving no stale accepted journal survived the reset.
            req1b = _stage_request_with_scenario(
                tick=1,
                sim_time_ns=1_000_000_000,
                scenario=fixture.scenario,
                run_id=fixture.resolved_run.run_id,
            )
            await client.step_stage(req1b)
            again = await client.ingest_observations(batch1)
            assert again.replayed is False
            assert again.sequence_count == 1
            assert again.journal_digest == digest1
            assert len(service._observations.records) == 1

    asyncio.run(run())


__all__ = ["_ingest_payload"]
