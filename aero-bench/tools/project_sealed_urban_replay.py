#!/usr/bin/env python3
"""Publish an unverified public replay from an unchanged urban runtime seal."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aero_bench.artifacts.contracts import SealManifest  # noqa: E402
from aero_bench.config.resolver import ResolvedRunSpec  # noqa: E402
from aero_bench.runner.execution import _project_and_write_public_trace  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402


def _write(path: Path, value: object) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--seal-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.absolute()
    source = args.seal_root.absolute()
    output = args.output.absolute()
    run = ResolvedRunSpec.model_validate_json((bundle / "resolved-run.json").read_bytes())
    seal = SealManifest.model_validate_json((source / "seal-manifest.json").read_bytes())
    if (
        run.scenario.task.package_id != "urban.uav-recovery-demo.v1"
        or run.execution_scope != "executor_validation"
        or seal.run_id != run.run_id
        or seal.execution_scope != run.execution_scope
    ):
        raise ValueError("replay source is not the bound urban engineering runtime")
    output.mkdir(exist_ok=False)
    _write(output / "source-provenance.json", {
        "purpose": "recorded-public-flight-visualization",
        "runtime_reused": True,
        "simulation_rerun": False,
        "bundle_root": str(bundle),
        "source_seal_root": str(source),
        "seal_manifest_digest": seal.manifest_digest,
        "run_id": run.run_id,
        "verification": "not_verified_by_this_public_only_projection",
        "formal_benchmark_pass": False,
        "visual_scoring": "not_run",
    })
    print(f"VISUALIZATION_OUTPUT={output}", flush=True)
    started = time.monotonic()
    try:
        identity = _project_and_write_public_trace(
            run=run, seal=seal, validated=None,
            bundle_root=bundle, run_root=output, seal_root=source,
            progress=lambda stage: print(f"PUBLICATION_STAGE={stage}", flush=True),
        )
        _write(output / "publication.json", {
            "identity": identity.model_dump(mode="json"),
            "status": "published_not_browser_checked",
            "elapsed_seconds": time.monotonic() - started,
        })
    except Exception as error:
        _write(output / "projection-failure.json", {
            "error_type": type(error).__name__, "error": str(error),
            "elapsed_seconds": time.monotonic() - started,
        })
        raise
    print(f"PUBLIC_TRACE={output / identity.relative_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
