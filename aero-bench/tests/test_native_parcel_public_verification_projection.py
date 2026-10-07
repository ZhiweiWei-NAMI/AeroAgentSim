"""Synthetic public-support regression fixtures, not formal flight evidence."""
import hashlib
from types import SimpleNamespace as NS

import pytest

from aero_bench.artifacts import ArtifactRecord, EvidenceReference, seal_manifest
from aero_bench.runtime.contracts import scene_state_digest_value
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facility_physics import facility_physics
from aero_bench.tasks.logistics.native_parcel_rpc import NativeParcelProjection, NativeParcelSceneFrame
from aero_bench.trace.projector import PublicProjectorError, SealedPublicArtifacts, _project_report, project_public_run_event
from aero_bench.verifier.contracts import GoalResult,MetricResult,VerificationReport
from tests.tasks import test_native_parcel_runtime as h
from tests.tasks import test_logistics_physical_observations as physical
from tests.tasks.test_native_parcel_hook_projection import compose,invoke


GOALS=("parcel.pickup","parcel.transport","parcel.dropoff","parcel.delivered","carrier.terminal")
BUSINESS="logistics.native-parcel"
BUSINESS_ARTIFACT="artifact.native-parcel-state"


@pytest.fixture
def source(tmp_path):
    package=h.lower_logistics_task_package(h._package_document())
    contacts=[facility_physics(package.facilities.require(name)).contacts[0]
              for name in (h._PICKUP,h._DROPOFF)]
    sites=tuple(NS(launch_site_id=c.model_name,allowed_uav_entity_ids=(h._VEHICLE_ID,),
        pose=NS(position=NS(enu=NS(east_m=c.east_m,north_m=c.north_m,up_m=c.up_top_m))))
        for c in contacts)
    tokens=tuple(f"launch_pad.{c.model_name}" for c in contacts)
    states=[]; events=[]; ledger=EventLedger(run_id=h._RUN_ID)
    for tick,state_name in enumerate(("awaiting_pickup","loaded","in_transit","in_transit","in_transit","delivered"),1):
        pickup=tick<=3
        east=contacts[0].east_m if pickup else contacts[1].east_m
        north=contacts[0].north_m if pickup else contacts[1].north_m
        if tick==4:
            east=(contacts[0].east_m+contacts[1].east_m)/2
            north=(contacts[0].north_m+contacts[1].north_m)/2
        airborne=tick in (3,4)
        sample=physical._state_sample(h._dwell_tick(tick),run_id=h._RUN_ID,
            scenario_digest=h._SCENARIO_DIGEST,vehicle_id=h._VEHICLE_ID,
            east_m=east,north_m=north,up_m=10 if airborne else contacts[0 if pickup else 1].up_top_m,
            yaw_deg=0,east_mps=1 if airborne else 0,landed=not airborne,in_air=airborne,
            landed_state="IN_AIR" if airborne else "ON_GROUND",ground_contact=tick!=4,
            armed=tick in (2,3,4),contacts=() if tick==4 else (tokens[0 if pickup else 1],))
        scene=physical._scene_state(run_id=h._RUN_ID,scenario_digest=h._SCENARIO_DIGEST,
            at=sample.at,samples=(sample,))
        if states:
            scene=scene.model_copy(update={"previous_scene_state_digest":states[-1].scene_state_digest})
            scene=type(scene).model_validate({**scene.model_dump(mode="json"),
                "scene_state_digest":scene_state_digest_value(scene)})
        holder_kind=("pickup_facility" if tick==1 else "dropoff_facility" if tick==6 else "carrier")
        parcel=NativeParcelProjection(schema_version="aero-bench.native-parcel-projection/v1",
            run_id=h._RUN_ID,scenario_digest=h._SCENARIO_DIGEST,at=sample.at,
            source_scene_state_digest=scene.scene_state_digest,
            source_stage_barrier_digest=scene.stage_barrier.barrier_digest,
            source_observation_digest="d"*64,parcel_id=h._PARCEL_ID,order_id=h._ORDER_ID,
            carrier_entity_id=h._VEHICLE_ID if holder_kind=="carrier" else None,
            custody_holder_id=h._PICKUP if tick==1 else h._DROPOFF if tick==6 else h._AIRCRAFT_ID,
            custody_holder_kind=holder_kind,destination_id=h._DROPOFF,state=state_name,
            pose={"x_m":east,"y_m":sample.pose.position.enu.up_m,"z_m":-north,
                  "orientation":{"qw":1,"qx":0,"qy":0,"qz":0}},authority="modeled_business_custody")
        frame=NativeParcelSceneFrame(schema_version="aero-bench.native-parcel-scene-frame/v1",
            scene_state=scene,parcel=parcel)
        invoke(compose(frame,[],ledger),scene)
        events.append(project_public_run_event(ledger.records[-1].event,public_event_ids=set()))
        states.append(scene)
    records=[]
    for artifact_id,kind,producer,visibility,path,raw in (
        ("artifact.scene-states","scene.state-history","harness","public","harness/scene-states.jsonl",
         b"".join(canonical_json_bytes(s.model_dump(mode="json"))+b"\n" for s in states)),
        (BUSINESS_ARTIFACT,"logistics.business.state",BUSINESS,"private","business/state.json",b'{"private_custody_fixture":true}')):
        destination=tmp_path/path; destination.parent.mkdir(parents=True,exist_ok=True); destination.write_bytes(raw)
        records.append(ArtifactRecord(artifact_id=artifact_id,artifact_type=kind,producer_id=producer,
            visibility=visibility,relative_path=path,sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw)))
    seal=seal_manifest(root=tmp_path,run_id=h._RUN_ID,attempt_id="fixture",execution_scope="formal_benchmark",
        event_chain_root=ledger.chain_root,artifacts=tuple(records))
    run=NS(run_id=h._RUN_ID,task=NS(package=NS(package_id="logistics.task.native-parcel.v1")),
        environment=NS(clock=NS(max_steps=6),providers=(NS(provider_id=BUSINESS,adapter="logistics.native-parcel"),
            NS(provider_id="flight",adapter="px4.gazebo"))),
        scenario=NS(scenario_digest=h._SCENARIO_DIGEST,launch_sites=sites,entities=(NS(
            entity_id=h._VEHICLE_ID,kind="uav",state="dynamic",owner_kind="provider",source_provider_id="flight"),)),
        artifact_requirements=tuple(NS(**r.model_dump()) for r in records))
    report=VerificationReport(schema_version="aero-bench.verification/v1",run_id=h._RUN_ID,
        execution_scope="formal_benchmark",status="passed",coverage_complete=True,
        goals=tuple(GoalResult(goal_id=goal,passed=True,metrics=(MetricResult(metric_id=goal,value=1,unit="1",
            evidence=(EvidenceReference(artifact_id=BUSINESS_ARTIFACT,selector="logistics.business.state"),)),))
            for goal in GOALS))
    return NS(run=run,resources=SealedPublicArtifacts(seal),states=tuple(states),events=tuple(events),report=report)


def project(source):
    return _project_report(source.report,resources=source.resources,run=source.run,
        scene_states=source.states,public_events=source.events)


def test_five_metrics_have_real_bound_public_stages_and_report_is_unchanged(source):
    original=source.report.model_dump_json()
    public=project(source)
    references={g.goal_id:tuple(e.selector for e in g.metrics[0].evidence) for g in public.goals}
    assert references["parcel.pickup"] == ("harness/scene-states.jsonl#ticks/1","harness/scene-states.jsonl#ticks/2")
    assert references["parcel.transport"] == ("harness/scene-states.jsonl#ticks/4",)
    assert references["parcel.dropoff"] == ("harness/scene-states.jsonl#ticks/5","harness/scene-states.jsonl#ticks/6")
    assert references["parcel.delivered"] == ("harness/scene-states.jsonl#ticks/6",)
    assert references["carrier.terminal"] == ("harness/scene-states.jsonl#ticks/6",)
    assert source.report.model_dump_json() == original
    assert BUSINESS_ARTIFACT not in public.model_dump_json()
    assert "private_custody_fixture" not in public.model_dump_json()


@pytest.mark.parametrize("field",["source_scene_state_digest","source_stage_barrier_digest","scenario_digest","carrier_entity_id","order_id"])
def test_foreign_projection_binding_cannot_supply_metric_evidence(source,field):
    event=source.events[3]
    changed=tuple(v.model_copy(update={"value":"e"*64 if "digest" in field else "foreign.identity"})
        if v.name==field else v for v in event.public_payload)
    source.events=source.events[:3]+(event.model_copy(update={"public_payload":changed}),)+source.events[4:]
    with pytest.raises(PublicProjectorError):project(source)


@pytest.mark.parametrize("change",["missing_projection","ambiguous_carrier","armed_terminal","wrong_pad","no_airborne_motion"])
def test_missing_real_condition_never_becomes_public_success(source,change):
    if change=="missing_projection":source.events=source.events[:-1]
    elif change=="ambiguous_carrier":source.run.scenario.entities*=2
    else:
        index=3 if change=="no_airborne_motion" else 5
        sample=source.states[index].samples[0]
        if change=="armed_terminal":sample=sample.model_copy(update={"armed":True})
        elif change=="wrong_pad":sample=sample.model_copy(update={"contacts":("launch_pad.foreign",)})
        else:
            sample=sample.model_copy(update={"attributes":tuple(a.model_copy(update={"value":True})
                if a.name=="ground_contact" else a for a in sample.attributes)})
        source.states=source.states[:index]+(source.states[index].model_copy(update={"samples":(sample,)}),)+source.states[index+1:]
    with pytest.raises(PublicProjectorError):project(source)


def test_private_only_metric_remains_rejected_without_native_context(source):
    with pytest.raises(PublicProjectorError,match="no sealed public evidence"):
        _project_report(source.report,resources=source.resources)
