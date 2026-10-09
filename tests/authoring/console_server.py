"""Built console on the real local backend; run artifacts stay outside git."""

import os
from pathlib import Path

from aeroagentsim.services.app import create_app

WORKTREE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("AEROAGENTSIM_CONSOLE_DATA_ROOT", "/tmp/aas-q/e2/backend"))
app = create_app(
    ROOT / "runs",
    scenario_root=WORKTREE,
    frontend=WORKTREE / "frontend/dist",
    studio_root=ROOT / "workspaces",
)
