#!/usr/bin/env python3
"""Explicit bicycle census revision of the retained canonical motion auditor.

Independently count actual bicycle identities, including zero. Geometry, motion,
route, provenance and all other v2 gates remain unchanged. Keep the v2 auditor
available for jobs whose profile pins its exact bytes.
"""
import hashlib
import importlib.util
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent))
spec = importlib.util.spec_from_file_location("canonical_motion_v2", SCRIPTS / "audit-sumo-canonical-motion-v2.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
original_inventory = audit.native_inventory
original_audit_paths = audit.audit_paths


def explicit_inventory(native):
    vehicles, persons, counts = original_inventory(native)
    counts["bicycle"] = sum(kind == "bicycle" for kind in vehicles.values())
    return vehicles, persons, counts


audit.native_inventory = explicit_inventory


def audit_paths(**paths):
    result = original_audit_paths(**paths)
    result["verifier_revision"] = {
        "id": "explicit-bicycle-census-v3",
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "canonical_auditor_sha256": hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest(),
        "change": "Independently count bicycle identities, including explicit zero; every other gate is unchanged.",
    }
    return result


audit.audit_paths = audit_paths
if __name__ == "__main__":
    audit.main()
