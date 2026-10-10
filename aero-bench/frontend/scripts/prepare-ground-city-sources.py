#!/usr/bin/env python3
"""Prepare filtered OSM subsources and prove an existing SUMO network is ground-only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aero_bench.world.ground_roads import prepare_ground_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-osm", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--effective-osm-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-source-osm-sha256", required=True)
    parser.add_argument("--expected-network-sha256", required=True)
    parser.add_argument("--expected-effective-osm-sha256", required=True)
    parser.add_argument("--expected-filtered-osm-sha256", required=True)
    parser.add_argument("--expected-projection", required=True)
    args = parser.parse_args()
    receipt = prepare_ground_sources(
        args.source_osm, args.network, args.effective_osm_json, args.output_dir,
        expected_source_osm_sha256=args.expected_source_osm_sha256,
        expected_network_sha256=args.expected_network_sha256,
        expected_effective_osm_sha256=args.expected_effective_osm_sha256,
        expected_filtered_osm_sha256=args.expected_filtered_osm_sha256,
        expected_projection=args.expected_projection,
    )
    print(json.dumps({"output_dir": str(args.output_dir), "state": receipt["state"],
                      "excluded_way_count": receipt["excluded_way_count"],
                      "network": receipt["network"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
