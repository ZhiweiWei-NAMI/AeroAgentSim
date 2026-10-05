"""Assemble the minimal `aero_bench` subset for the projection image.

The shared runtime contracts import resolved poses and their frame and model
dependencies. The standalone workload_scenario validator separately validates
the executor's read-only ResolvedScenario/v4 projection.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


MODULES = (
    "serialization.py",
    "config/models.py",
    "config/loader.py",
    "runtime/contracts.py",
    "providers/stages.py",
    "providers/rpc.py",
    "world/resolved.py",
    "world/contracts.py",
    "world/frame_math.py",
    "world/frames.py",
)

INITS = (
    ("", "Minimal deployment subset for the world-scene projection image."),
    ("config", ""),
    ("runtime", ""),
    ("providers", ""),
    ("world", ""),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    source: Path = arguments.source
    output: Path = arguments.output
    if output.exists():
        raise SystemExit(f"staging output already exists: {output}")
    package_root = output / "aero_bench"
    for relative, docstring in INITS:
        init = package_root / relative / "__init__.py"
        init.parent.mkdir(parents=True, exist_ok=True)
        init.write_text(f'"""{docstring}"""\n' if docstring else "\n", encoding="utf-8")
    for module in MODULES:
        origin = source / module
        if not origin.is_file():
            raise SystemExit(f"staged module is missing from the source: {module}")
        target = package_root / module
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
