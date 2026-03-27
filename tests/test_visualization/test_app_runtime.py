import importlib
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"


def test_visualization_app_import_is_cwd_independent(tmp_path):
    db_path = tmp_path / "runtime" / "visualization.sqlite"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT)
    env["AEROAGENTSIM_DB_PATH"] = str(db_path)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from airfogsim.visualization.app import app; print(type(app).__name__)",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "FastAPI" in result.stdout
    assert db_path.exists()
    assert not (tmp_path / "lowspace_sim.db").exists()


def test_legacy_simulation_routes_are_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AEROAGENTSIM_DB_PATH", str(tmp_path / "legacy-disabled.sqlite"))

    for module_name in list(sys.modules):
        if module_name.startswith("airfogsim.visualization"):
            sys.modules.pop(module_name, None)

    app_module = importlib.import_module("airfogsim.visualization.app")
    client = TestClient(app_module.app)

    response = client.post("/api/simulation/start")

    assert response.status_code == 410
    assert "/api/runs" in response.json()["detail"]
