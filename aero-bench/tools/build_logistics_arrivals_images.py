#!/usr/bin/env python3
"""Build four frozen-source, digest-pinned non-physical Logistics workloads."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_agent_inspection_images import build_images  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--registry", default="localhost:5000/aero-bench")
    arguments = parser.parse_args()
    print(
        build_images(
            arguments.output_root,
            participant_profile="logistics_arrivals",
            verifier_profile="logistics_arrivals",
            registry=arguments.registry,
        )
    )


if __name__ == "__main__":
    main()
