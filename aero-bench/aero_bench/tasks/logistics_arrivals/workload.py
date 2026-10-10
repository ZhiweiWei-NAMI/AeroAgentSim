"""Separate offline workload for the non-physical arrivals verifier."""

import argparse
import hashlib
import os
from pathlib import Path

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.executor.contracts import VerifierWorkloadContract
from aero_bench.providers.rpc import parse_json_object
from aero_bench.runtime.evidence import load_sealed_event_ledger
from aero_bench.runtime.events import RunEventAudience
from aero_bench.runtime.ledger import (
    VerifierEventLedger,
    verifier_event_segment_jsonl_bytes,
)
from aero_bench.config.models import NamedValue
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics_arrivals.verifier import (
    verify_logistics_arrivals_sealed,
)


def root(name):
    value = Path(os.environ[name])
    if (
        not value.is_absolute()
        or value.resolve(strict=True) != value
        or not value.is_dir()
    ):
        raise ValueError(f"{name} must be a real absolute directory")
    return value


def canonical_object(path):
    raw = path.read_bytes()
    value = parse_json_object(raw)
    if canonical_json_bytes(value) + b"\n" != raw:
        raise ValueError("verifier input must be canonical JSON")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify",))
    parser.parse_args()
    contract = VerifierWorkloadContract.model_validate(
        canonical_object(Path(os.environ["AERO_BENCH_CONTRACT"]))
    )
    if (
        os.environ["AERO_BENCH_ROLE"] != "verifier"
        or os.environ["AERO_BENCH_RUN_ID"] != contract.run_id
        or os.environ["AERO_BENCH_WORKLOAD_ID"] != contract.workload_id
        or int(os.environ["AERO_BENCH_SEED"]) != contract.seed
    ):
        raise ValueError(
            "arrivals verifier environment differs from its executor contract"
        )
    seal_root = root("AERO_BENCH_SEAL_DIR")
    artifact_root = root("AERO_BENCH_ARTIFACT_DIR")
    if any(artifact_root.iterdir()):
        raise ValueError("arrivals verifier output must be empty at startup")
    outputs = {item.artifact_type: item for item in contract.verifier.output_artifacts}
    if (
        len(contract.verifier.output_artifacts) != 2
        or set(outputs) != {"verification.report", "verification.event-segment"}
        or any(
            item.visibility != "public"
            or item.source_asset_id is not None
            or item.producer_id != contract.workload_id
            for item in outputs.values()
        )
    ):
        raise ValueError(
            "arrivals verifier requires one public report and one evidence segment"
        )
    seal = SealManifest.model_validate(
        canonical_object(seal_root / "seal-manifest.json")
    )
    report = verify_logistics_arrivals_sealed(
        bundle_root=root("AERO_BENCH_BUNDLE_DIR"),
        run=contract.run,
        seal=seal,
        sealed_root=seal_root,
    )
    report_payload = canonical_json_bytes(report.model_dump(mode="json")) + b"\n"
    runtime = load_sealed_event_ledger(run=contract.run, seal=seal, seal_root=seal_root)
    ledger = VerifierEventLedger(runtime_records=runtime.records)
    for goal in report.goals:
        ledger.append_event(
            source=contract.workload_id,
            workload_id=contract.workload_id,
            event_type=f"verifier.evidence.{goal.goal_id}",
            time=runtime.records[-1].event.time,
            payload=tuple(
                NamedValue(name=name, value=value)
                for name, value in (
                    ("goal_id", goal.goal_id),
                    ("passed", goal.passed),
                    ("failure_class", goal.failure_class),
                    (
                        "goal_result_json",
                        canonical_json_bytes(goal.model_dump(mode="json")).decode(),
                    ),
                    ("report_sha256", hashlib.sha256(report_payload).hexdigest()),
                    ("report_status", report.status),
                )
            ),
            payload_schema_id="verifier.evidence.v1",
            interaction_type="verifier.evidence.v1",
            correlation_id=f"verification.{goal.goal_id}",
            visibility=(RunEventAudience(scope="public", audience_id=None),),
        )
    for artifact_type, payload in (
        ("verification.report", report_payload),
        (
            "verification.event-segment",
            verifier_event_segment_jsonl_bytes(
                runtime_records=runtime.records, verifier_records=ledger.records
            ),
        ),
    ):
        requirement = outputs[artifact_type]
        if len(payload) > requirement.max_size_bytes:
            raise ValueError("arrival verifier output exceeds its declared size")
        target = artifact_root / requirement.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    print(
        f"arrivals-verified goal_count={len(report.goals)} physical_delivery=false",
        flush=True,
    )


if __name__ == "__main__":
    main()
