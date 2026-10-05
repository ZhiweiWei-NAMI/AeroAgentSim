#!/usr/bin/env python3
"""Audit sealed logistics source/journal binding, without a benchmark task verdict."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aero_bench.artifacts.contracts import SealManifest  # noqa: E402
from aero_bench.config.loader import BundleReader  # noqa: E402
from aero_bench.config.resolver import ResolvedRunSpec  # noqa: E402
from aero_bench.providers.rpc import parse_json_object  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402
from aero_bench.tasks.logistics.sealed_observations import (
    load_sealed_logistics_observations,
)  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolved-run", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--seal-root", type=Path, required=True)
    parser.add_argument("--business-provider-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve().is_relative_to(args.seal_root.resolve()):
        raise ValueError(
            "logistics audit report must stay outside the immutable runtime seal"
        )
    run = ResolvedRunSpec.model_validate(
        parse_json_object(args.resolved_run.read_bytes())
    )
    seal = SealManifest.model_validate(
        parse_json_object((args.seal_root / "seal-manifest.json").read_bytes())
    )
    evidence = load_sealed_logistics_observations(
        run=run,
        reader=BundleReader(args.bundle_root),
        seal=seal,
        seal_root=args.seal_root,
        business_provider_id=args.business_provider_id,
    )
    result = {
        "schema_version": "aero-bench.logistics-observation-audit/v1",
        "run_id": run.run_id,
        "scenario_digest": run.scenario.scenario_digest,
        "seal_digest": seal.manifest_digest,
        "event_chain_root": seal.event_chain_root,
        "business_artifact_id": evidence.business_artifact.artifact_id,
        "closed_frame_count": len(evidence.replay.ticks),
        "observation_count": evidence.replay.journal.last_sequence,
        "journal_digest": evidence.replay.journal.canonical_digest(),
        "airspace_segment_count": sum(
            len(tick.segments) for tick in evidence.replay.ticks
        ),
        "integrity_status": "source_checked",
        "task_verdict_produced": False,
        "benchmark_verifier_executed": False,
        "dwell_provenance_issued": False,
        "airspace_scope": "sample_segment_reference_point_only",
        "battery_transfer_checked": False,
        "order_transitions_checked": False,
    }
    raw = canonical_json_bytes(result) + b"\n"
    with args.report.open("xb") as report:
        report.write(raw)
    print(raw.decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
