"""Synthetic hook-composition tests; no native execution or custody claim."""

import asyncio
from types import SimpleNamespace

import pytest

from aero_bench.runtime.ledger import EventLedger
from aero_bench.tasks.logistics.native_parcel_hook import NativeParcelRuntimeHook
from aero_bench.tasks.logistics.native_parcel_rpc import (
    DeclaredParcelFacilityPose, NativeParcelProjection, NativeParcelSceneFrame,
)
from aero_bench.tasks.logistics.runtime_hook import (
    LogisticsRuntimeHook, LogisticsRuntimeHookError,
)
from aero_bench.trace.projector import project_public_run_event
from aero_bench.world.resolved import ResolvedQuaternion
from tests.tasks import test_logistics_physical_observations as physical
from tests.tasks import test_native_parcel_runtime as h


def frame():
    at = h._dwell_tick(1)
    sample = physical._state_sample(
        at, run_id=h._RUN_ID, scenario_digest=h._SCENARIO_DIGEST,
        vehicle_id=h._VEHICLE_ID, east_m=1.0, north_m=2.0, up_m=0.137,
        yaw_deg=0.0, landed=True, in_air=False, landed_state="ON_GROUND",
        ground_contact=True,
    )
    scene = physical._scene_state(
        run_id=h._RUN_ID, scenario_digest=h._SCENARIO_DIGEST,
        at=at, samples=(sample,),
    )
    parcel = NativeParcelProjection(
        schema_version="aero-bench.native-parcel-projection/v1",
        run_id=scene.run_id, scenario_digest=scene.scenario_digest, at=scene.at,
        source_scene_state_digest=scene.scene_state_digest,
        source_stage_barrier_digest=scene.stage_barrier.barrier_digest,
        source_observation_digest="d" * 64,
        parcel_id=h._PARCEL_ID, order_id=h._ORDER_ID, carrier_entity_id=None,
        custody_holder_id=h._PICKUP, custody_holder_kind="pickup_facility",
        destination_id=h._DROPOFF, state="awaiting_pickup",
        pose=DeclaredParcelFacilityPose(
            x_m=1.0, y_m=0.15, z_m=-2.0,
            orientation=ResolvedQuaternion(qw=1.0, qx=0.0, qy=0.0, qz=0.0),
        ),
        authority="modeled_business_custody",
    )
    return NativeParcelSceneFrame(
        schema_version="aero-bench.native-parcel-scene-frame/v1",
        scene_state=scene, parcel=parcel,
    )


class ObservationHookProbe(LogisticsRuntimeHook):
    """Probe ordering only; the real hook/RPC has a separate affected suite."""

    def __init__(self, calls):
        self.calls = calls

    async def on_stage_barriers_closed(self, events, **context):
        self.calls.append(("observation-ingest", context["target"]))


def compose(value, calls, ledger):
    async def snapshot(*, at):
        calls.append(("parcel-rpc", at))
        return value

    return NativeParcelRuntimeHook(
        observation_hook=ObservationHookProbe(calls),
        business=SimpleNamespace(
            manifest=SimpleNamespace(provider_id="logistics.native-parcel"),
            parcel_scene_frame=snapshot,
        ),
        ledger=ledger,
    )


def invoke(hook, scene):
    asyncio.run(hook.on_stage_barriers_closed(
        (), scene_state=scene, stage_barriers=(scene.stage_barrier,), target=scene.at,
    ))


def test_original_ingest_precedes_projection_and_motion_scene_stays_immutable():
    value = frame()
    before = value.scene_state.model_dump_json()
    ledger = EventLedger(run_id=h._RUN_ID)
    calls = []
    invoke(compose(value, calls, ledger), value.scene_state)
    assert calls == [("observation-ingest", value.scene_state.at),
                     ("parcel-rpc", value.scene_state.at)]
    assert value.scene_state.model_dump_json() == before
    assert value.scene_state.declared_entity_ids == (h._VEHICLE_ID,)
    private, public = (r.event for r in ledger.records)
    assert private.event_type == "logistics.parcel.scene-frame.v1"
    assert {a.scope for a in private.visibility} == {"private", "verifier"}
    projected = project_public_run_event(public, public_event_ids=set())
    fields = {p.name: p.value for p in projected.public_payload}
    assert "frame_json" not in fields
    assert fields["parcel_id"] == h._PARCEL_ID
    assert fields["carrier_entity_id"] is None
    assert fields["authority"] == "modeled_business_custody"
    assert fields["source_scene_state_digest"] == value.scene_state.scene_state_digest
    assert projected.entity_id == h._PARCEL_ID
    assert projected.at == value.scene_state.at


def test_changed_source_scene_is_rejected_before_projection_events():
    value = frame()
    changed = value.scene_state.model_copy(update={"scenario_digest": "c" * 64})
    ledger = EventLedger(run_id=h._RUN_ID)
    calls = []
    with pytest.raises(LogisticsRuntimeHookError, match="source SceneState"):
        invoke(compose(value, calls, ledger), changed)
    assert ledger.records == ()
