from __future__ import annotations

import os
from pathlib import Path

from aero_bench.executor.contracts import VerifierWorkloadContract
from aero_bench.tasks.inspection.sealed_evidence import InspectionSealedEvidenceLoader
from aero_bench.tasks.inspection.verifier import (
    InspectionVerifier,
    core_verification_report,
)
from aero_bench.verifier.contracts import VerificationReport


def main() -> int:
    if os.geteuid() == 0:
        raise RuntimeError("Inspection Verifier must not run as root")
    required = (
        Path("/opt/aero-bench/entrypoint.py"),
        Path("/opt/aero-bench/aero_bench/tasks/inspection/verifier.py"),
        Path("/opt/aero-bench/aero_bench/tasks/inspection/sealed_evidence.py"),
    )
    if not all(path.is_file() for path in required):
        raise RuntimeError("Inspection Verifier runtime files are incomplete")
    if not all(
        isinstance(value, type)
        for value in (
            VerifierWorkloadContract,
            InspectionSealedEvidenceLoader,
            InspectionVerifier,
            VerificationReport,
        )
    ) or not callable(core_verification_report):
        raise RuntimeError("Inspection Verifier contracts are unavailable")
    print("inspection-verifier selfcheck: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
