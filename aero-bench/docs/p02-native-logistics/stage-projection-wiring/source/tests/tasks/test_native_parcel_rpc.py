"""Synthetic RPC-component evidence; no native flight or custody physics claim."""
import pytest

from aero_bench.config.models import NamedValue
from aero_bench.runtime.contracts import CommandRequest
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import assess_facility_presence
from aero_bench.tasks.logistics.native_parcel_rpc import (
    DeclaredParcelFacilityPose, NativeParcelRpcComponent, NativeParcelSessionConfig,
    PARCEL_PICKUP_TOOL, PARCEL_DROPOFF_TOOL,
)
from aero_bench.tasks.logistics.observation_ingress import build_observation_batch
from aero_bench.tasks.logistics.physical_observations import (
    FacilityPadPhysicalObservation, observation_digest_value,
)
from aero_bench.world.resolved import ResolvedQuaternion
from tests.tasks import test_native_parcel_runtime as h


def config(package):
    pickup, dropoff = [facility_landing_pads(package.facilities.require(f))[0]
                       for f in (h._PICKUP,h._DROPOFF)]
    def resting(pad):
        return DeclaredParcelFacilityPose(x_m=pad.x,y_m=pad.y+0.1,z_m=pad.z,
            orientation=ResolvedQuaternion(qw=1,qx=0,qy=0,qz=0))
    return NativeParcelSessionConfig(schema_version="aero-bench.native-parcel-session/v1",
        contract=h._contract(),pickup_pad=pickup,dropoff_pad=dropoff,
        initial_parcel_pose=resting(pickup),final_parcel_pose=resting(dropoff),
        step_ns=1_000_000_000,max_steps=10,calibration_digest="e"*64)


def batch(package, tick, *, location=h._PICKUP, moving=False):
    """One actual synthetic carrier pose assessed against both distinct pads."""
    source = h._observation(package,at=h._dwell_tick(tick),facility_id=location,
        landed=not moving,in_air=moving,speed_east=1.0 if moving else 0.0)
    records = []
    for facility in (h._PICKUP,h._DROPOFF):
        pad = facility_landing_pads(package.facilities.require(facility))[0]
        fields = {name:getattr(source,name) for name in type(source).model_fields}
        fields.update(facility_id=facility,pad=pad,
            assessment=assess_facility_presence(pad=pad,sample=source.sample,
                profile=source.profile,event=source.event_binding,tolerances=h._TOL))
        value = FacilityPadPhysicalObservation.model_construct(**fields)
        fields["observation_digest"] = observation_digest_value(value)
        records.append(FacilityPadPhysicalObservation(**fields))
    return build_observation_batch(observations=tuple(records),run_id=h._RUN_ID,
        scenario_digest=h._SCENARIO_DIGEST,at=source.at,
        source_scene_state_digest=source.source_scene_state_digest,
        source_stage_barrier_digest=source.source_stage_barrier_digest)


def component(package):
    return NativeParcelRpcComponent(config=config(package),run_id=h._RUN_ID,
        scenario_digest=h._SCENARIO_DIGEST,business_provider_id="logistics.native-parcel")


def request(tick, *, kind="pickup", command_id="action.pickup.1", principal=h._AUTHORIZED_PRINCIPAL_ID):
    return CommandRequest(run_id=h._RUN_ID,command_id=command_id,agent_id=principal,
        tool_id=PARCEL_PICKUP_TOOL if kind=="pickup" else PARCEL_DROPOFF_TOOL,
        issued_at=h._dwell_tick(tick),arguments=(
            NamedValue(name="order_id",value=h._ORDER_ID),
            NamedValue(name="parcel_id",value=h._PARCEL_ID),
            NamedValue(name="actor_id",value=h._AIRCRAFT_ID)))


@pytest.fixture
def package():
    return h.lower_logistics_task_package(h._package_document())


def test_closed_stage_pickup_movement_dropoff_and_detachment(package):
    rpc = component(package)
    for tick in range(1,4):rpc.ingest_closed_stage(batch(package,tick))
    result = rpc.transition(request(3),now=h._dwell_tick(3))
    assert result["outcome"]["status"] == "admitted"
    assert result["action"]["received_at"] == h._dwell_tick(3).model_dump()
    assert rpc.projection().state == "loaded"
    assert rpc.projection().carrier_entity_id == h._VEHICLE_ID
    rpc.ingest_closed_stage(batch(package,4,moving=True))
    assert rpc.projection().state == "in_transit"
    for tick in range(5,8):rpc.ingest_closed_stage(batch(package,tick,location=h._DROPOFF))
    result = rpc.transition(request(7,kind="dropoff",command_id="action.dropoff.1"),now=h._dwell_tick(7))
    assert result["outcome"]["status"] == "admitted"
    projected = rpc.projection()
    assert projected.state == "delivered"
    assert projected.carrier_entity_id is None
    assert projected.custody_holder_id == h._DROPOFF
    assert projected.pose == rpc.config.final_parcel_pose
    assert len([r for r in rpc.actions if r["outcome"]["transfer_id"] is not None]) == 2


def test_same_request_returns_original_and_payload_reuse_rejected(package):
    rpc = component(package)
    for tick in range(1,4):rpc.ingest_closed_stage(batch(package,tick))
    one = rpc.transition(request(3),now=h._dwell_tick(3))
    assert rpc.transition(request(3),now=h._dwell_tick(3)) == one
    assert len(rpc.actions) == 1
    with pytest.raises(ValueError,match="different request"):
        rpc.transition(request(3,kind="dropoff"),now=h._dwell_tick(3))


def test_rpc_uses_current_closed_tick_and_independent_principal(package):
    rpc = component(package)
    for tick in range(1,4):rpc.ingest_closed_stage(batch(package,tick))
    with pytest.raises(ValueError,match="current closed"):
        rpc.transition(request(3),now=h._dwell_tick(4))
    with pytest.raises(ValueError,match="principal"):
        rpc.transition(request(3,principal="agent.foreign"),now=h._dwell_tick(3))
    assert rpc.machine.state == "awaiting_pickup"


def test_rpc_session_requires_every_declared_pad_per_stage(package):
    """Partial batches are rejected atomically through the session.  Batch
    completeness (every declared pad exactly once) is owned by the strict
    stage batch validator inside consume_batch; the session keeps compiled
    geometry, clock and run-bound checks only.  The rejection must still be
    atomic: no machine mutation, no tombstone, no projection source."""
    rpc = component(package)
    good = batch(package, 1)
    before = rpc.machine.snapshot()
    pickup_only = good.model_copy(update={"observations": good.observations[:1]})
    with pytest.raises(ValueError, match="missing declared pad"):
        rpc.ingest_closed_stage(pickup_only)
    assert rpc.machine.snapshot() == before
    assert rpc.batch is None
    duplicated = good.model_copy(
        update={"observations": (good.observations[0], good.observations[0])})
    with pytest.raises(ValueError, match="repeats one observation digest"):
        rpc.ingest_closed_stage(duplicated)
    assert rpc.machine.snapshot() == before
    rpc.ingest_closed_stage(good)
    assert rpc.batch is good
    assert rpc.projection().state == "awaiting_pickup"


def test_batch_missing_pad_wrong_pose_and_tick_jump_are_atomic(package):
    rpc = component(package)
    good = batch(package,1)
    before = rpc.machine.snapshot()
    bad = good.model_copy(update={"observations":good.observations[:1]})
    with pytest.raises(ValueError):rpc.ingest_closed_stage(bad)
    assert rpc.machine.snapshot() == before
    one = h._observation(package,at=h._dwell_tick(1),facility_id=h._DROPOFF)
    bad = good.model_copy(update={"observations":(good.observations[0],one)})
    with pytest.raises(ValueError):rpc.ingest_closed_stage(bad)
    assert rpc.machine.snapshot() == before
    rpc.ingest_closed_stage(good)
    before = rpc.machine.snapshot()
    with pytest.raises(ValueError):rpc.ingest_closed_stage(batch(package,3))
    assert rpc.machine.snapshot() == before
