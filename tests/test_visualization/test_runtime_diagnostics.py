import importlib
import sys
import time

from fastapi.testclient import TestClient


def load_visualization_app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AEROAGENTSIM_DB_PATH", str(tmp_path / "runtime" / "diagnostics.sqlite"))

    for module_name in list(sys.modules):
        if module_name.startswith("airfogsim.visualization"):
            sys.modules.pop(module_name, None)

    app_module = importlib.import_module("airfogsim.visualization.app")
    return app_module, TestClient(app_module.app)


def test_default_snapshot_preflight_is_ready(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    response = client.post("/api/configs/default/preflight")

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_ready"] is True
    statuses = {item["name"]: item["status"] for item in payload["checks"]}
    assert statuses["agents"] == "pass"
    assert statuses["workflows"] == "pass"
    assert statuses["registry_proxies"] == "pass"
    assert statuses["resources"] in {"pass", "warning"}


def test_preflight_failure_creates_error_run_and_reset_endpoint(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    save_response = client.put(
        "/api/configs/default",
        json={
            "name": "Broken Resources Config",
            "coordinate_mode": "simulation_plane",
            "airspaces": [
                {
                    "x_range": [0, 100],
                    "y_range": [0, 100],
                    "altitude_range": [0, 50],
                }
            ],
            "frequencies": [],
            "landing_spots": [],
            "traffic": {},
            "agents": [
                {
                    "id": "drone_alpha",
                    "name": "Drone Alpha",
                    "type": "DroneAgent",
                    "initial_position": [10, 10, 20],
                    "initial_battery": 92,
                    "components": ["MoveToComponent", "ChargingComponent"],
                    "properties": {},
                }
            ],
            "workflows": [
                {
                    "id": "inspection_alpha",
                    "name": "Inspection Alpha",
                    "type": "inspection",
                    "agent_id": "drone_alpha",
                    "properties": {
                        "inspection_points": [
                            [10, 10, 20],
                            [150, 60, 40],
                            [220, 180, 50],
                        ]
                    },
                }
            ],
        },
    )

    assert save_response.status_code == 200
    config_id = save_response.json()["config_id"]

    start_response = client.post("/api/runs", json={"config_id": config_id})

    assert start_response.status_code == 409
    detail = start_response.json()["detail"]
    run_id = detail["run_id"]
    assert run_id.startswith("run_")
    assert any("AirspaceManager" in message for message in detail["errors"])

    status_response = client.get(f"/api/runs/{run_id}/status")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "error"

    logs_response = client.get(f"/api/runs/{run_id}/logs")
    assert logs_response.status_code == 200
    assert any(log["source"] == "Preflight" for log in logs_response.json())

    health_response = client.get("/api/health")
    assert health_response.status_code == 200
    assert health_response.json()["recent_startup_error"]["run_id"] == run_id

    reset_response = client.post("/api/runtime/reset")
    assert reset_response.status_code == 200
    assert reset_response.json()["simulation_status"] == "STOPPED"


def test_runtime_reset_marks_running_run_stopped(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    start_response = client.post("/api/runs", json={"config_id": "default"})

    assert start_response.status_code == 200
    run_id = start_response.json()["run_id"]

    latest_status = None
    latest_time = 0.0
    for _ in range(30):
        status_response = client.get(f"/api/runs/{run_id}/status")
        assert status_response.status_code == 200
        payload = status_response.json()
        latest_status = payload["status"]
        latest_time = max(latest_time, float(payload.get("simulation_time") or 0.0))
        if latest_status in {"running", "paused", "completed"} and latest_time > 0:
            break
        time.sleep(0.2)

    reset_response = client.post("/api/runtime/reset")
    assert reset_response.status_code == 200
    assert reset_response.json()["simulation_status"] == "STOPPED"

    final_status = client.get(f"/api/runs/{run_id}/status").json()
    assert final_status["status"] == "stopped"
    assert float(final_status["simulation_time"]) >= latest_time
