#!/usr/bin/env python3
"""Audit sealed closed motion inputs; do not run a task Verifier or mint a pass."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aero_bench.artifacts.contracts import SealManifest  # noqa: E402
from aero_bench.config.resolver import ResolvedRunSpec  # noqa: E402
from aero_bench.providers.rpc import parse_json_object  # noqa: E402
from aero_bench.runtime.sealed_motion import load_sealed_motion_evidence  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolved-run", type=Path, required=True)
    parser.add_argument("--seal-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.resolve().is_relative_to(args.seal_root.resolve()):
        raise ValueError("motion audit report must stay outside the immutable runtime seal")
    run = ResolvedRunSpec.model_validate(parse_json_object(args.resolved_run.read_bytes()))
    seal = SealManifest.model_validate(parse_json_object((args.seal_root / "seal-manifest.json").read_bytes()))
    evidence = load_sealed_motion_evidence(run=run, seal=seal, seal_root=args.seal_root)
    result = {
        "schema_version": "aero-bench.sealed-motion-audit/v1",
        "run_id": run.run_id, "scenario_digest": run.scenario.scenario_digest,
        "seal_digest": seal.manifest_digest, "event_chain_root": seal.event_chain_root,
        "artifact_count": len(seal.artifacts), "closed_frame_count": len(evidence.frames),
        "px4_source_event_count": sum(len(frame.events) for frame in evidence.frames),
        "integrity_status": "checked", "task_verdict_produced": False,
        "benchmark_verifier_executed": False,
    }
    raw = canonical_json_bytes(result) + b"\n"
    with args.report.open("xb") as report:
        report.write(raw)
    print(raw.decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
