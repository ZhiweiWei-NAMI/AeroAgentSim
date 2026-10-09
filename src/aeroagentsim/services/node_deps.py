"""Resolve the actual npm runtime used by the standalone demo camera."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Keep these aligned with the console's package-lock.json.
PINNED_VERSIONS = {"playwright": "1.64.0", "three": "0.170.0"}
REQUIRED = ("playwright/index.mjs", "three/build/three.module.js")


def _require_modules(path: Path) -> Path:
    for filename in REQUIRED:
        if not (path / filename).is_file():
            raise FileNotFoundError(f"Demo Node runtime missing {path / filename}")
    return path.resolve()


def node_modules_root() -> Path:
    """Use declared/local dependencies, or install the pinned camera runtime."""
    if "AEROAGENTSIM_NODE_MODULES" in os.environ:
        override = os.environ["AEROAGENTSIM_NODE_MODULES"]
        if not override:
            raise ValueError("AEROAGENTSIM_NODE_MODULES must be a nonempty path")
        return _require_modules(Path(override))
    repository = Path(__file__).resolve().parents[3] / "frontend" / "node_modules"
    if repository.exists():
        return _require_modules(repository)
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    runtime = cache / "aeroagentsim" / "camera-playwright-1.64.0-three-0.170.0"
    modules = runtime / "node_modules"
    if not all((modules / name).is_file() for name in REQUIRED):
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "package.json").write_text(
            json.dumps(
                {
                    "name": "aeroagentsim-demo-capture",
                    "private": True,
                    "dependencies": PINNED_VERSIONS,
                }
            )
        )
        subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund", "--prefix", str(runtime)],
            check=True,
            stdout=sys.stderr,
        )
    return _require_modules(modules)
