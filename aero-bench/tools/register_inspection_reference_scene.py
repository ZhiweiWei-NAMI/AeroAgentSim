#!/usr/bin/env python3
"""Publish an explicit pinned native reference; never register the authored city."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aero_bench.authoring.reference_registration import publish_reference_registration  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registration-id", required=True)
    parser.add_argument("--scene-path", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--battery-wh", type=float, required=True,
                        help="Explicit authoring declaration; not measured or consumed as physical energy")
    parser.add_argument("--reserve-ratio", type=float, required=True)
    args = parser.parse_args()
    manifest = publish_reference_registration(
        suite=args.suite, alignment=args.alignment, output=args.output,
        registration_id=args.registration_id, scene_path=args.scene_path,
        name=args.name, battery_wh=args.battery_wh, reserve_ratio=args.reserve_ratio,
    )
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
