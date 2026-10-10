#!/usr/bin/env python3
"""Compile an unchanged saved city workspace against an explicit registration."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aero_bench.authoring.compilation_contracts import CityCompileRequest  # noqa: E402
from aero_bench.authoring.draft_compiler import CityDraftCompiler  # noqa: E402
from aero_bench.authoring.native_registry import NativeSceneRegistry  # noqa: E402
from aero_bench.authoring.workspace import CityWorkspaceDraft  # noqa: E402
from aero_bench.serialization import canonical_json_bytes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--registration-id", required=True)
    parser.add_argument("--registration-sha256", required=True)
    parser.add_argument("--native-scenes-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Fresh compilation output directory")
    args = parser.parse_args()
    draft = CityWorkspaceDraft.from_json_bytes(args.draft.read_bytes())
    request = CityCompileRequest.model_validate({
        "schema_version": "aero-bench.city-compile-request/v1",
        "registration_id": args.registration_id,
        "registration_sha256": args.registration_sha256,
        "draft": draft,
    })
    registry = (NativeSceneRegistry.from_manifest(args.native_scenes_manifest)
                if args.native_scenes_manifest is not None else NativeSceneRegistry())
    result = CityDraftCompiler(args.output, registry).compile(request)
    print(canonical_json_bytes(result.model_dump(mode="json")).decode("utf-8"))
    return 0 if result.status == "compiled" else 2


if __name__ == "__main__":
    raise SystemExit(main())
