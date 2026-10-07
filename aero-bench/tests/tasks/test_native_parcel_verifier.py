"""Synthetic replay/lineage regressions, distinct from a sealed native run."""
import copy
from types import SimpleNamespace
import hashlib

import pytest

from aero_bench.config.models import NamedValue
from aero_bench.serialization import canonical_json_bytes
from aero_bench.verifier.contracts import (
    EvidenceReference,GoalResult,MetricResult,VerificationReport,
)
from aero_bench.tasks.logistics.native_parcel_verifier import (
    replay_native_parcel_batches,validate_parcel_command_lineage,
    check_native_carrier_evidence, destination_contact_token,
)
from aero_bench.providers.px4_gazebo.sealed_motion import NativeVehicleSnapshot
from aero_bench.tasks.logistics.facility_physics import facility_physics
from tests.tasks import test_native_parcel_rpc as r
from tests.tasks import test_native_parcel_runtime as h


@pytest.fixture
def source():
    package = h.lower_logistics_task_package(h._package_document())
    component = r.component(package)
    batches = []
    for tick in range(1,11):
        value = r.batch(package,tick,location=h._PICKUP if tick<=3 else h._DROPOFF,
                        moving=tick==4)
        component.ingest_closed_stage(value)
        batches.append(value)
        if tick==3:component.transition(r.request(tick),now=value.at)
        if tick==7:component.transition(r.request(tick,kind="dropoff",command_id="action.dropoff.1"),now=value.at)
    return component,tuple(batches)


def replay(component,batches,actions=None):
    return replay_native_parcel_batches(config=component.config,run_id=h._RUN_ID,
        scenario_digest=h._SCENARIO_DIGEST,business_provider_id="logistics.native-parcel",
        batches=batches,action_records=component.actions if actions is None else actions)


def test_replays_same_custody_and_terminal_without_minting_provenance(source):
    component,batches = source
    result = replay(component,batches)
    assert result.transport_observed
    assert not result.source_verified
    assert not result.passed
    assert result.snapshot == component.snapshot()
    assert all(not o.provenance_verified for b in batches for o in b.observations)


def test_forged_admission_and_missing_exact_stage_are_rejected(source):
    component,batches = source
    forged = copy.deepcopy(component.actions)
    forged[0]["outcome"]["state_after"] = "delivered"
    with pytest.raises(ValueError):replay(component,batches,forged)
    with pytest.raises(ValueError):replay(component,batches[:2])


def test_short_evidence_and_missing_dropoff_do_not_pass(source):
    component,batches = source
    assert not replay(component,batches[:8]).carrier_terminal
    assert not replay(component,batches,component.actions[:1]).delivered


def raw_records(component, batches):
    """Typed raw telemetry fixtures; these do not represent a sealed flight."""
    records = []
    destination = "launch_pad.launch_pad." + component.config.dropoff_pad.facility_id + ".0"
    for batch in batches:
        sample = batch.observations[0].sample
        vehicle = NativeVehicleSnapshot(vehicle_id=h._VEHICLE_ID,
            pose_json=canonical_json_bytes({"x":sample.x,"y":sample.y,"z":sample.z}).decode(),
            position_wgs84_json="{}",velocity_json="{}",angular_velocity_json="{}",
            attitude_json="{}",flight_mode="HOLD", armed=sample.in_air,
            in_air=sample.in_air, landed=sample.landed,
            landed_state="IN_AIR" if sample.in_air else "ON_GROUND",
            battery_percent=90, health="{}", contacts=() if sample.in_air else (destination,),
            ground_contact=not sample.in_air, collision_contact=False,
            simulation_time_ns=batch.at.sim_time_ns)
        records.append(SimpleNamespace(operation="step_stage",tick=batch.at.tick,
            sim_time_ns=batch.at.sim_time_ns,snapshot=SimpleNamespace(vehicles=(vehicle,))))
    return tuple(records), destination


def check_source(component,batches,records=None):
    original, destination = raw_records(component,batches)
    return check_native_carrier_evidence(result=replay(component,batches),
        config=component.config,records=original if records is None else records,
        destination_contact=destination)


def test_raw_carrier_transport_and_destination_are_required(source):
    component,batches=source
    assert check_source(component,batches) == (True,True)
    records,_=raw_records(component,batches)
    assert check_source(component,batches,records[:6]) == (True,False)
    assert check_source(component,batches,records[:9]) == (True,False)
    probe=SimpleNamespace(**{**vars(records[3]),"operation":"snapshot"})
    assert check_source(component,batches,records[:3]+(probe,)+records[4:]) == (False,True)
    changed=copy.deepcopy(records)
    for index in (6,9):
        carrier=changed[index].snapshot.vehicles[0]
        changed[index].snapshot.vehicles=(carrier.model_copy(update={
            "contacts":carrier.contacts+("ground.world",)}),)
    assert check_source(component,batches,changed) == (True,True)


@pytest.mark.parametrize("tick",[7,10])
@pytest.mark.parametrize("bad",[
    {"armed":True}, {"contacts":("launch_pad.wrong",)},
    {"contacts":()}, {"ground_contact":False}, {"collision_contact":True},
    {"in_air":True}, {"landed":False},
])
def test_dropoff_and_horizon_need_exact_disarmed_pad_contact(source,tick,bad):
    component,batches=source
    records,_=raw_records(component,batches)
    changed=copy.deepcopy(records)
    changed[tick-1].snapshot.vehicles=(
        changed[tick-1].snapshot.vehicles[0].model_copy(update=bad),)
    assert check_source(component,batches,changed) == (True,False)


def test_no_airborne_transport_does_not_pass_even_when_custody_delivered():
    package=h.lower_logistics_task_package(h._package_document())
    component=r.component(package)
    batches=[]
    for tick in range(1,11):
        batch=r.batch(package,tick,location=h._PICKUP if tick<=3 else h._DROPOFF)
        component.ingest_closed_stage(batch)
        batches.append(batch)
        if tick==3:component.transition(r.request(3),now=batch.at)
        if tick==7:component.transition(r.request(7,kind="dropoff",command_id="action.dropoff.1"),now=batch.at)
    result=replay(component,batches)
    assert result.delivered and result.carrier_terminal
    assert not result.transport_observed and not result.passed
    assert check_source(component,batches) == (False,True)


@pytest.mark.parametrize("motion",["vertical","away","stationary"])
def test_airborne_without_horizontal_progress_is_not_transport(source,motion):
    from aero_bench.tasks.logistics.facility_presence import assess_facility_presence
    from aero_bench.tasks.logistics.physical_observations import observation_digest_value
    component,batches=source
    package=h.lower_logistics_task_package(h._package_document())
    batch=r.batch(package,4,moving=True)
    observations=[]
    for observation in batch.observations:
        changes={"y":observation.sample.y+2,"velocity_east_m_s":0,
                 "velocity_up_m_s":1}
        if motion=="away":
            pad=component.config.dropoff_pad
            changes.update(x=observation.sample.x+(observation.sample.x-pad.x),
                z=observation.sample.z+(observation.sample.z-pad.z),
                velocity_east_m_s=1)
        elif motion=="stationary":
            changes.update(x=component.config.dropoff_pad.x,
                z=component.config.dropoff_pad.z,velocity_up_m_s=0)
        sample=observation.sample.model_copy(update=changes)
        fields={**{name:getattr(observation,name) for name in type(observation).model_fields},"sample":sample,
            "assessment":assess_facility_presence(pad=observation.pad,sample=sample,
                profile=observation.profile,event=observation.event_binding,tolerances=h._TOL)}
        candidate=type(observation).model_construct(**fields)
        fields["observation_digest"]=observation_digest_value(candidate)
        observations.append(type(observation).model_validate(fields))
    batch=r.build_observation_batch(observations=tuple(observations),run_id=h._RUN_ID,
        scenario_digest=h._SCENARIO_DIGEST,at=batch.at,
        source_scene_state_digest=batch.source_scene_state_digest,
        source_stage_barrier_digest=batch.source_stage_barrier_digest)
    result=replay(component,batches[:3]+(batch,)+batches[4:])
    assert result.delivered and not result.transport_observed


@pytest.mark.parametrize("bad",[
    {"armed":False}, {"in_air":False}, {"landed":True},
    {"ground_contact":True}, {"collision_contact":True},
])
def test_airborne_transport_requires_raw_native_flight_facts(source,bad):
    component,batches=source
    records,_=raw_records(component,batches)
    changed=copy.deepcopy(records)
    changed[3].snapshot.vehicles=(changed[3].snapshot.vehicles[0].model_copy(update=bad),)
    assert check_source(component,batches,changed) == (False,True)


def test_destination_contact_comes_from_facility_physics_and_resolved_site(source):
    component,_=source
    package=h.lower_logistics_task_package(h._package_document())
    contact=facility_physics(package.facilities.require(h._DROPOFF)).contacts[0]
    site=SimpleNamespace(launch_site_id=contact.model_name,
        allowed_uav_entity_ids=(h._VEHICLE_ID,),
        pose=SimpleNamespace(position=SimpleNamespace(enu=SimpleNamespace(
            east_m=contact.east_m,north_m=contact.north_m,
            up_m=contact.up_top_m+component.config.contract.carrier.pose_reference_above_contact_m))))
    scenario=SimpleNamespace(launch_sites=(site,))
    assert destination_contact_token(config=component.config,package=package,
        scenario=scenario) == f"launch_pad.{contact.model_name}"
    site.pose.position.enu.up_m += 1
    with pytest.raises(ValueError,match="differs"):
        destination_contact_token(config=component.config,package=package,scenario=scenario)


def test_only_sealed_loader_path_attests_a_valid_source_fixture(source,tmp_path,monkeypatch):
    """Exercise loader composition with synthetic trusted boundaries, not a flight claim."""
    from aero_bench.tasks.logistics import native_parcel_verifier as verifier
    component,batches=source
    package=h.lower_logistics_task_package(h._package_document())
    contact=facility_physics(package.facilities.require(h._DROPOFF)).contacts[0]
    site=SimpleNamespace(launch_site_id=contact.model_name,
        allowed_uav_entity_ids=(h._VEHICLE_ID,),
        pose=SimpleNamespace(position=SimpleNamespace(enu=SimpleNamespace(
            east_m=contact.east_m,north_m=contact.north_m,
            up_m=contact.up_top_m+component.config.contract.carrier.pose_reference_above_contact_m))))
    config=SimpleNamespace(native_parcel=component.config,task_package=package,
        observation=SimpleNamespace(bindings=None,spec=None,pose_references=None,tolerances=None))
    raw=canonical_json_bytes({"native_parcel":component.snapshot()})
    (tmp_path/"business.json").write_bytes(raw)
    artifact=SimpleNamespace(relative_path="business.json",size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest())
    calls=[]
    def observations(**kwargs):
        calls.append("sealed_logistics")
        source=motion(**kwargs)
        source.motion.seal=kwargs["seal"]
        return SimpleNamespace(business_artifact=artifact,seal=kwargs["seal"],native_sources=(source,))
    frames=tuple(SimpleNamespace(scene_state=SimpleNamespace(at=b.at,
        scene_state_digest=b.source_scene_state_digest,
        stage_barrier=SimpleNamespace(barrier_digest=b.source_stage_barrier_digest)),
        events=(),stage_barriers=()) for b in batches)
    records,_=raw_records(component,batches)
    native=SimpleNamespace(records=records,artifact=SimpleNamespace(producer_id=component.config.contract.carrier.provider_id),motion=SimpleNamespace(frames=frames,
        ledger=SimpleNamespace(records=lineage(component))))
    def motion(**kwargs):
        calls.append("sealed_px4")
        return native
    monkeypatch.setattr(verifier,"load_sealed_logistics_observations",observations)
    monkeypatch.setattr(verifier.NativeParcelBusinessConfig,"model_validate",lambda _:config)
    monkeypatch.setattr(verifier,"derive_physical_observations",
        lambda **kwargs:batches[kwargs["target"].tick-1].observations)
    reader=SimpleNamespace(validate_schema_bound_file=lambda _:None,load_document=lambda _:None)
    run=SimpleNamespace(run_id=h._RUN_ID,scenario=SimpleNamespace(
        scenario_digest=h._SCENARIO_DIGEST,launch_sites=(site,)),
        environment=SimpleNamespace(providers=(SimpleNamespace(
            provider_id="logistics.native-parcel",config=SimpleNamespace(file=None)),)))
    result=verifier.verify_sealed_native_parcel(run=run,reader=reader,seal=object(),
        seal_root=tmp_path,business_provider_id="logistics.native-parcel")
    assert calls == ["sealed_logistics","sealed_px4"]
    assert result.source_verified and result.passed
    assert result.snapshot == component.snapshot()
    assert not replay(component,batches).passed
    wrong=copy.deepcopy(records)
    wrong[6].snapshot.vehicles=(wrong[6].snapshot.vehicles[0].model_copy(update={"armed":True}),)
    native.records=wrong
    failed=verifier.verify_sealed_native_parcel(run=run,reader=reader,seal=object(),
        seal_root=tmp_path,business_provider_id="logistics.native-parcel")
    assert failed.source_verified and not failed.carrier_terminal and not failed.passed
    test_seal=object()
    native.motion.seal=test_seal
    for source_set in ((native,native),()):
        monkeypatch.setattr(verifier,"load_sealed_logistics_observations",lambda **kwargs:
            SimpleNamespace(business_artifact=artifact,seal=test_seal,native_sources=source_set))
        with pytest.raises(ValueError,match="exact validated carrier"):
            verifier.verify_sealed_native_parcel(run=run,reader=reader,seal=test_seal,
                seal_root=tmp_path,business_provider_id="logistics.native-parcel")
    monkeypatch.setattr(verifier,"load_sealed_logistics_observations",lambda **kwargs:
        SimpleNamespace(business_artifact=artifact,seal=test_seal,native_sources=(native,)))
    native.motion.seal=object()
    with pytest.raises(ValueError,match="exact validated carrier"):
        verifier.verify_sealed_native_parcel(run=run,reader=reader,seal=test_seal,
            seal_root=tmp_path,business_provider_id="logistics.native-parcel")


def lineage(component):
    records=[]
    for record in component.actions:
        req=record["request"]
        digest=hashlib.sha256(canonical_json_bytes(req)).hexdigest()
        at=h.SimulationTime.model_validate(record["action"]["received_at"])
        common=dict(command_id=req["command_id"],agent_id=req["agent_id"],
            provider_id="logistics.native-parcel",run_id=req["run_id"],time=at)
        def append(kind,source,fields):
            records.append(SimpleNamespace(event=SimpleNamespace(**common,event_type=kind,
                source_kind=source,source=req["agent_id"] if source=="agent" else "logistics.native-parcel",
                payload=tuple(NamedValue(name=k,value=v) for k,v in fields.items()))))
        append("command.issued","agent",{"request_digest":digest,
            "arguments_json":canonical_json_bytes(req["arguments"]).decode()})
        for phase in ("received","accepted","applied","completed"):
            append("command.receipt","provider",{"request_digest":digest,"phase":phase})
    return records


def test_actual_receipt_required_not_only_business_final_label(source):
    component,_=source
    records=lineage(component)
    validate_parcel_command_lineage(actions=component.actions,records=records,
                                   business_provider_id="logistics.native-parcel")
    records.pop(1)
    with pytest.raises(ValueError,match="reception"):
        validate_parcel_command_lineage(actions=component.actions,records=records,
                                       business_provider_id="logistics.native-parcel")


def entrypoint_goals(*, run, seal, seal_root):
    """Mirror containers/native-parcel-verifier/entrypoint.py goal construction."""
    measures={"parcel.pickup":True,"parcel.transport":True,"parcel.dropoff":True,"parcel.delivered":True,
              "carrier.terminal":True}
    business=next(a for a in seal.artifacts if a.producer_id=="logistics.native-parcel"
                  and a.artifact_type=="logistics.business.state")
    payload_bytes=(seal_root/business.relative_path).read_bytes()
    if (len(payload_bytes)!=business.size_bytes
            or hashlib.sha256(payload_bytes).hexdigest()!=business.sha256):
        raise ValueError("evidence artifact no longer matches its sealed record")
    evidence=EvidenceReference(artifact_id=business.artifact_id,
        selector="logistics.business.state")
    return tuple(GoalResult(goal_id=g.goal_id,passed=measures[g.goal_id],
        metrics=(MetricResult(metric_id=g.metric_id,value=float(measures[g.goal_id]),
                              unit="1",evidence=(evidence,)),),
        failure_class=None if measures[g.goal_id] else "parcel_condition_unconfirmed")
        for g in run.task.goals)


def test_report_construction_uses_actual_evidence_entrypoint(source,tmp_path):
    """Entrypoint contract path: typed contracts accept only artifact_id/selector evidence."""
    component,batches=source
    result=replay(component,batches)
    assert not result.passed
    payload=b'{"native_parcel":{}}'
    relative_path="artifacts/business-state.json"
    (tmp_path/relative_path).parent.mkdir(parents=True)
    (tmp_path/relative_path).write_bytes(payload)
    artifact=SimpleNamespace(artifact_id="artifact.logistics-business-state",
        artifact_type="logistics.business.state",producer_id="logistics.native-parcel",
        visibility="public",relative_path=relative_path,
        sha256=hashlib.sha256(payload).hexdigest(),size_bytes=len(payload))
    seal=SimpleNamespace(artifacts=(artifact,))
    run=SimpleNamespace(run_id=h._RUN_ID,execution_scope="formal_benchmark",
        task=SimpleNamespace(goals=tuple(
            SimpleNamespace(goal_id=f"parcel.{name}",metric_id=f"parcel.{name}.measured")
            for name in ("pickup","dropoff"))))
    report=VerificationReport(schema_version="aero-bench.verification/v1",
        run_id=run.run_id,execution_scope=run.execution_scope,
        status="passed",goals=entrypoint_goals(run=run,seal=seal,seal_root=tmp_path),
        coverage_complete=True)
    evidence=report.goals[0].metrics[0].evidence[0]
    assert isinstance(evidence,EvidenceReference)
    assert evidence.artifact_id=="artifact.logistics-business-state"
    assert evidence.selector=="logistics.business.state"
    canonical=canonical_json_bytes(report.model_dump(mode="json"))
    assert b'"schema_version":"aero-bench.verification/v1"' in canonical
    with pytest.raises(ValueError):
        EvidenceReference.model_validate({"artifact_id":artifact.artifact_id,
            "sha256":artifact.sha256,"relative_path":artifact.relative_path})
