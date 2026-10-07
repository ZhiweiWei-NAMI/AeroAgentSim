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
)
from tests.tasks import test_native_parcel_rpc as r
from tests.tasks import test_native_parcel_runtime as h


@pytest.fixture
def source():
    package = h.lower_logistics_task_package(h._package_document())
    component = r.component(package)
    batches = []
    for tick in range(1,11):
        value = r.batch(package,tick,location=h._PICKUP if tick<=4 else h._DROPOFF,
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
    assert result.passed
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
    measures={"parcel.pickup":True,"parcel.dropoff":True,"parcel.delivered":True,
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
    assert result.passed
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
