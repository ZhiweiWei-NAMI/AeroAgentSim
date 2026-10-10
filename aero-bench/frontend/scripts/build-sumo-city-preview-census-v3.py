#!/usr/bin/env python3
"""Record SUMO through the pinned producer, then export an explicit actor census.

The pinned producer and its original native recording remain unchanged. The
versioned recording differs only in its measured census, and public provenance
binds its new bytes. This is an offline engineering preview, not formal evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def explicit_census(native: dict) -> dict[str, int]:
    vehicles: dict[str, str] = {}
    persons: set[str] = set()
    if not isinstance(native.get("frames"), list) or not native["frames"]:
        raise ValueError("A complete native recording is required for the census")
    for frame in native["frames"]:
        for row in frame["vehicles"]:
            if len(row) != 8 or not isinstance(row[0], str) or not isinstance(row[4], str):
                raise ValueError("Malformed native vehicle census row")
            if row[0] in vehicles and vehicles[row[0]] != row[4]:
                raise ValueError("Native vehicle changed type")
            vehicles[row[0]] = row[4]
        for row in frame["persons"]:
            if len(row) != 5 or not isinstance(row[0], str):
                raise ValueError("Malformed native pedestrian census row")
            persons.add(row[0])
    counts = {"vehicles": len(vehicles), "persons": len(persons), **dict(Counter(vehicles.values()))}
    if native["demand"]["observed"] != counts:
        raise ValueError("Original native census differs from actual recorded actor identities")
    counts["bicycle"] = sum(kind == "bicycle" for kind in vehicles.values())
    return counts


def encoded(document: dict) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def export_census(public_path: Path, native_path: Path, output: Path, native_output: Path) -> dict:
    if output.exists() or native_output.exists():
        raise FileExistsError("Fresh versioned census output paths are required")
    public = json.loads(public_path.read_bytes())
    native_bytes = native_path.read_bytes()
    native = json.loads(native_bytes)
    if public["demand"] != native["demand"]:
        raise ValueError("Public and native demand differ before census export")
    identity = public["native_motion_source"]
    if identity["sha256"] != hashlib.sha256(native_bytes).hexdigest() or identity["size_bytes"] != len(native_bytes):
        raise ValueError("Public source does not bind the actual native recording")
    census = explicit_census(native)
    native["demand"]["observed"] = census
    public["demand"]["observed"] = census
    revised_native = encoded(native)
    identity.update(sha256=hashlib.sha256(revised_native).hexdigest(), size_bytes=len(revised_native))
    for path, content in ((native_output, revised_native), (output, encoded(public))):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
    return {"census": census, "native": str(native_output), "public": str(output),
            "native_sha256": identity["sha256"], "original_native_sha256": hashlib.sha256(native_bytes).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-evidence-dir", type=Path, required=True)
    args, forwarded = parser.parse_known_args()
    intermediate = args.output.with_name(args.output.stem + ".sparse-census-v2.json")
    native = args.private_evidence_dir / (intermediate.stem + ".native-sumo-v1.json")
    revised = args.private_evidence_dir / (args.output.stem + ".explicit-census-v3.native-sumo-v1.json")
    if any(path.exists() for path in (args.output, intermediate, native, revised)):
        parser.error("Fresh versioned output paths are required")
    subprocess.run([sys.executable, str(Path(__file__).with_name("build-sumo-city-preview.py")),
                    *forwarded, "--output", str(intermediate),
                    "--private-evidence-dir", str(args.private_evidence_dir)], check=True)
    print(json.dumps(export_census(intermediate, native, args.output, revised), indent=2))


if __name__ == "__main__":
    main()
