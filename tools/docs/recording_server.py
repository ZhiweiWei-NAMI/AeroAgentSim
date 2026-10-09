"""The same real service as the console e2e, with isolated recording storage."""
from __future__ import annotations

import os
from pathlib import Path

from aeroagentsim.services.app import create_app

WORKTREE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ["AEROAGENTSIM_DOCS_ROOT"]).resolve()
app = create_app(
    ROOT / "runs",
    scenario_root=WORKTREE,
    frontend=WORKTREE / "frontend/dist",
    studio_root=ROOT / "workspaces",
)
