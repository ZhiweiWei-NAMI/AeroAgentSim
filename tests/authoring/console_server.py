"""Built console on the real local backend; run artifacts stay outside git."""

from pathlib import Path

from aeroagentsim.services.app import create_app

WORKTREE = Path(__file__).resolve().parents[2]
ROOT = Path("/tmp/aas-q/e2/backend")
app = create_app(
    ROOT / "runs",
    scenario_root=WORKTREE,
    frontend=WORKTREE / "frontend/dist",
    studio_root=ROOT / "workspaces",
)
