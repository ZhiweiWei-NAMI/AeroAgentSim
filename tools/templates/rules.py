"""Add the shared ``lowalt.rules`` behaviour package to the seven low-altitude templates.

Run with the project Python and PYTHONPATH=aerokernel:src. Idempotent: documents
that already carry the package only have their registry descriptors refreshed.
``--check`` fails when a template file is not reproducible.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from aeroagentsim.packs.lowalt.rules import apply_rules

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = (
    "parcel-delivery",
    "medical-delivery",
    "powerline-inspection",
    "crop-spraying",
    "air-taxi",
    "security-patrol",
    "city-day",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", type=Path, default=ROOT / "scenarios/templates")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for identity in TEMPLATES:
        path = args.templates / identity / "scenario.yaml"
        before = path.read_text()
        after = yaml.safe_dump(apply_rules(yaml.safe_load(before)), sort_keys=False)
        if args.check:
            if before != after:
                raise SystemExit(f"{identity}: rules differ; regenerate")
        else:
            path.write_text(after)
    print("lowalt.rules: seven templates")


if __name__ == "__main__":
    main()
