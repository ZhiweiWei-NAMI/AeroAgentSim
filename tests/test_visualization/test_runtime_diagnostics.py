import importlib
import sys
import time

from fastapi.testclient import TestClient


def load_visualization_app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AEROAGENTSIM_DB_PATH", str(tmp_path / "runtime" / "diagnostics.sqlite"))

    for module_name in list(sys.modules):
        if module_name.startswith("airfogsim.visualization") or module_name.startswith("aeroagentsim.visualization"):
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


def test_default_snapshot_is_not_shadowed_by_latest_saved_config(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    save_response = client.put(
        "/api/configs/custom-broken",
        json={
            "name": "Broken Logistics Config",
            "coordinate_mode": "simulation_plane",
            "traffic": {},
            "agents": [
                {
                    "id": "agent_2",
                    "name": "Agent 2",
                    "type": "delivery_drone_agent",
                    "initial_position": [0, 0, 30],
                    "initial_battery": 90,
                    "components": ["MoveToComponent", "LogisticsComponent"],
                    "properties": {},
                }
            ],
            "workflows": [
                {
                    "id": "workflow_2",
                    "name": "Broken Workflow",
                    "type": "logistics_workflow",
                    "agent_id": "agent_2",
                    "enabled": True,
                    "properties": {
                        "pickup_location": [],
                        "delivery_location": [],
                        "payloads": [],
                        "source_agent_id": "",
                        "target_agent_id": "",
                    },
                }
            ],
        },
    )

    assert save_response.status_code == 200

    default_response = client.get("/api/configs/default")
    assert default_response.status_code == 200
    payload = default_response.json()
    assert payload["config_id"] == "default"
    assert payload["workflows"][0]["properties"]["source_agent_id"] == "station_source"
    assert payload["workflows"][0]["properties"]["target_agent_id"] == "station_target"

    preflight_response = client.post("/api/configs/default/preflight")
    assert preflight_response.status_code == 200
    assert preflight_response.json()["is_ready"] is True


def test_preflight_failure_creates_error_run_and_reset_endpoint(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    save_response = client.put(
        "/api/configs/default",
        json={
            "name": "Broken Traffic Config",
            "coordinate_mode": "simulation_plane",
            "traffic": {"source": "sumo", "sumo_config": {}},
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
    assert any("TrafficDataProvider" in message for message in detail["errors"])

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
