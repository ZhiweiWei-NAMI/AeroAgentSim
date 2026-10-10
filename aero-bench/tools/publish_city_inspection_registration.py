#!/usr/bin/env python3
"""Publish the verified Huangpu inspection profile into a fresh native catalog."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aero_bench.authoring.city_inspection_registration import (  # noqa: E402
    publish_city_inspection_registration,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--source-input-lock", type=Path, required=True)
    parser.add_argument("--source-readiness", type=Path, required=True)
    parser.add_argument("--public-scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registration-id", default="inspection.huangpu.native.v6")
    parser.add_argument(
        "--scene-path",
        default="/city-presentation/huangpu-native-inspection-v6.json",
    )
    parser.add_argument("--name", default="Huangpu native inspection v6")
    parser.add_argument("--battery-wh", type=float, default=500.0)
    parser.add_argument("--reserve-ratio", type=float, default=0.2)
    args = parser.parse_args()
    manifest = publish_city_inspection_registration(
        repository_root=args.repository_root,
        source_input_lock=args.source_input_lock,
        source_readiness=args.source_readiness,
        public_scenario=args.public_scenario,
        output=args.output,
        registration_id=args.registration_id,
        scene_path=args.scene_path,
        name=args.name,
        battery_wh=args.battery_wh,
        reserve_ratio=args.reserve_ratio,
    )
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
