"""Real backend used by the P7b browser gate; no mock execution endpoints."""

from pathlib import Path

from aeroagentsim.services.app import create_app

ROOT = Path(__file__).resolve().parent / ".runtime" / "e2e"
app = create_app(ROOT / "runs", scenario_root=ROOT, studio_root=ROOT / "workspaces")
