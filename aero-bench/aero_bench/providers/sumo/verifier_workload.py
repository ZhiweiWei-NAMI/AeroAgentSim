"""SUMO restriction gate followed by the unchanged inspection verifier workload."""

import importlib.util
import sys

from aero_bench.providers.sumo.restriction_verifier import verify_sumo_restrictions
from aero_bench.serialization import canonical_json_bytes


def main() -> int:
    spec = importlib.util.spec_from_file_location("inspection_entrypoint", "/opt/aero-bench/entrypoint.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("inspection verifier entrypoint is unavailable")
    inspection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inspection)
    if sys.argv[1:] == ["selfcheck"]:
        return inspection.main(["selfcheck"])
    if sys.argv[1:] != ["verify"]:
        raise ValueError("SUMO restriction verifier accepts only verify or selfcheck")
    contract = inspection._load_contract()
    seal_root = inspection._normalized_root("AERO_BENCH_SEAL_DIR")
    seal = inspection._load_seal(seal_root, contract)
    result = verify_sumo_restrictions(
        bundle_root=inspection._normalized_root("AERO_BENCH_BUNDLE_DIR"),
        run=contract.run, seal=seal, sealed_root=seal_root,
    )
    print(canonical_json_bytes({"sumo_restrictions_verified": result}).decode(), flush=True)
    return inspection.main(["verify"])


if __name__ == "__main__":
    raise SystemExit(main())
