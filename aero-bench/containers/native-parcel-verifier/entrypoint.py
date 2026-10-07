"""Dedicated parcel verifier entrypoint. No native simulation is launched here."""
import importlib.util
import os
from pathlib import Path
import sys

import hashlib

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import StrictModel, Identifier
from aero_bench.verifier.contracts import VerificationReport,GoalResult,MetricResult,validate_report_against_run
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.native_parcel_verifier import verify_sealed_native_parcel
from aero_bench.artifacts.contracts import EvidenceReference
from typing import Literal


class NativeParcelVerifierConfig(StrictModel):
    schema_version: Literal["aero-bench.native-parcel-verifier/v1"]
    business_provider_id: Identifier


def run():
    # Reuse the supported sealed-workload IO validators, including the corrected
    # datum-aware image's common entrypoint, rather than weakening seal contracts.
    path=Path(__file__).with_name("inspection_entrypoint.py")
    spec=importlib.util.spec_from_file_location("inspection_verifier_io",path)
    if spec is None or spec.loader is None:raise ValueError("missing pinned verifier IO module")
    io=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(io)
    contract=io._load_contract()
    bundle=io._normalized_root("AERO_BENCH_BUNDLE_DIR")
    seal_root=io._normalized_root("AERO_BENCH_SEAL_DIR")
    output=io._normalized_root("AERO_BENCH_ARTIFACT_DIR")
    seal=io._load_seal(seal_root,contract)
    reader=BundleReader(bundle)
    config=NativeParcelVerifierConfig.model_validate(
        reader.validate_schema_bound_file(contract.run.task.verifier.config))
    result=verify_sealed_native_parcel(run=contract.run,reader=reader,seal=seal,
        seal_root=seal_root,business_provider_id=config.business_provider_id)
    measures={"parcel.pickup":result.pickup_admitted,"parcel.transport":result.transport_observed,
              "parcel.dropoff":result.dropoff_admitted,
              "parcel.delivered":result.delivered,"carrier.terminal":result.carrier_terminal}
    if set(g.goal_id for g in contract.run.task.goals)!=set(measures):
        raise ValueError("native parcel task must declare the five exact verifier goals")
    business=next(a for a in seal.artifacts if a.producer_id==config.business_provider_id
                  and a.artifact_type=="logistics.business.state")
    payload_bytes=(seal_root/business.relative_path).read_bytes()
    if (len(payload_bytes)!=business.size_bytes
            or hashlib.sha256(payload_bytes).hexdigest()!=business.sha256):
        raise ValueError("evidence artifact no longer matches its sealed record")
    evidence=EvidenceReference(artifact_id=business.artifact_id,
        selector="logistics.business.state")
    goals=tuple(GoalResult(goal_id=g.goal_id,passed=measures[g.goal_id],
        metrics=(MetricResult(metric_id=g.metric_id,value=float(measures[g.goal_id]),
                              unit="1",evidence=(evidence,)),),
        failure_class=None if measures[g.goal_id] else "parcel_condition_unconfirmed")
        for g in contract.run.task.goals)
    report=VerificationReport(schema_version="aero-bench.verification/v1",
        run_id=contract.run_id,execution_scope=contract.run.execution_scope,
        status="passed" if result.passed else "failed",goals=goals,coverage_complete=True)
    validate_report_against_run(report,contract.run)
    requirement,segment=io._output_requirements(contract)
    if any(output.iterdir()):raise ValueError("verifier output directory is not empty")
    payload=canonical_json_bytes(report.model_dump(mode="json"))+b"\n"
    io._write_output(output,relative_path=requirement.relative_path,
        max_size_bytes=requirement.max_size_bytes,payload=payload)
    if segment is not None:
        data=io._verifier_event_segment(contract=contract,seal=seal,seal_root=seal_root,
            report=report,report_payload=payload)
        io._write_output(output,relative_path=segment.relative_path,
                         max_size_bytes=segment.max_size_bytes,payload=data)


def main():
    if sys.argv[1:]!=["verify"]:
        print("usage: native-parcel-verifier verify",file=sys.stderr)
        return 2
    try:run()
    except Exception as exc:
        print(f"native-parcel-verifier: FAILED: {exc}",file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":raise SystemExit(main())
