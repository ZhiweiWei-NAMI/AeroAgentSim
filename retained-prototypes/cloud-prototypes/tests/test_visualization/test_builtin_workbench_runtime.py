import importlib
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


def load_visualization_app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AEROAGENTSIM_DB_PATH", str(tmp_path / "runtime" / "diagnostics.sqlite"))

    for module_name in list(sys.modules):
        if module_name.startswith("airfogsim.visualization") or module_name.startswith("aeroagentsim.visualization"):
            sys.modules.pop(module_name, None)

    app_module = importlib.import_module("airfogsim.visualization.app")
    return app_module, TestClient(app_module.app)


def put_config(client: TestClient, config_id: str, snapshot: dict) -> dict:
    response = client.put(f"/api/configs/{config_id}", json=snapshot)
    assert response.status_code == 200
    return response.json()


def start_run(client: TestClient, config_id: str) -> str:
    response = client.post("/api/runs", json={"config_id": config_id})
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def fetch_run_bundle(client: TestClient, run_id: str) -> dict:
    status = client.get(f"/api/runs/{run_id}/status")
    logs = client.get(f"/api/runs/{run_id}/logs")
    spatial = client.get(f"/api/runs/{run_id}/spatial")
    trajectories = client.get(f"/api/runs/{run_id}/trajectories")
    assert status.status_code == 200
    assert logs.status_code == 200
    assert spatial.status_code == 200
    assert trajectories.status_code == 200
    return {
        "status": status.json(),
        "logs": logs.json(),
        "spatial": spatial.json(),
        "trajectories": trajectories.json(),
    }


def wait_for_bundle(client: TestClient, run_id: str, predicate, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    latest = fetch_run_bundle(client, run_id)
    while time.time() < deadline:
        latest = fetch_run_bundle(client, run_id)
        if predicate(latest):
            return latest
        time.sleep(0.2)
    return latest


def extract_agent(spatial_payload: dict, agent_id: str):
    return next(
        (
            agent
            for agent in spatial_payload.get("agents", [])
            if agent.get("agent_id") == agent_id
        ),
        None,
    )


def extract_trajectory(trajectory_payload: dict, agent_id: str):
    return next(
        (
            trajectory
            for trajectory in trajectory_payload.get("trajectories", [])
            if trajectory.get("agent_id") == agent_id
        ),
        None,
    )


def unique_positions(trajectory: dict) -> set:
    return {
        tuple(point.get("position") or [])
        for point in (trajectory or {}).get("points", [])
    }


def normalize_workflow_token(value: str) -> str:
    return str(value or "").lower().replace("_", "").replace("-", "").replace(" ", "").replace("workflow", "")


def has_successful_task_log(logs: list, task_classes=None) -> bool:
    allowed = set(task_classes or [])
    for log in logs:
        event = str(log.get("event") or "").lower()
        value = log.get("value") or {}
        result = log.get("result") or value.get("result") or {}
        task_class = (
            log.get("task_class")
            or value.get("task_class")
            or result.get("task_class")
        )
        status = str(
            log.get("status")
            or value.get("status")
            or result.get("status")
            or ""
        ).lower()
        if allowed and task_class not in allowed:
            continue
        if event.endswith("task_completed") and status == "completed":
            return True
    return False


def logistics_snapshot():
    return {
        "name": "AeroAgentSim Logistics Config",
        "coordinate_mode": "simulation_plane",
        "traffic": {},
        "agents": [
            {
                "id": "station_source",
                "name": "Source Station",
                "type": "delivery_station",
                "initial_position": [20, 40, 0],
                "initial_battery": 100,
                "components": [],
                "properties": {
                    "position": [20, 40, 0],
                    "storage_capacity": 20,
                    "service_radius": 120.0,
                },
            },
            {
                "id": "station_target",
                "name": "Target Station",
                "type": "delivery_station",
                "initial_position": [240, 180, 0],
                "initial_battery": 100,
                "components": [],
                "properties": {
                    "position": [240, 180, 0],
                    "storage_capacity": 20,
                    "service_radius": 120.0,
                },
            },
            {
                "id": "delivery_drone_alpha",
                "name": "Delivery Drone Alpha",
                "type": "delivery_drone_agent",
                "initial_position": [120, 20, 30],
                "initial_battery": 92,
                "components": ["MoveToComponent", "LogisticsComponent", "ChargingComponent"],
                "properties": {
                    "battery_level": 92,
                    "max_payload_weight": 5,
                    "max_payload_volume": 1,
                },
            },
        ],
        "workflows": [
            {
                "id": "workflow_logistics_1",
                "name": "Logistics Path Alpha",
                "type": "logistics_workflow",
                "agent_id": "delivery_drone_alpha",
                "enabled": True,
                "properties": {
                    "pickup_location": [20, 40, 30],
                    "delivery_location": [240, 180, 30],
                    "payloads": [
                        {
                            "id": "payload_demo",
                            "weight": 1.2,
                            "dimensions": [0.25, 0.15, 0.1],
                        }
                    ],
                    "source_agent_id": "station_source",
                    "target_agent_id": "station_target",
                },
            }
        ],
    }


def inspection_snapshot():
    return {
        "name": "Inspection Demo",
        "coordinate_mode": "simulation_plane",
        "traffic": {},
        "agents": [
            {
                "id": "drone_alpha",
                "name": "Drone Alpha",
                "type": "DroneAgent",
                "initial_position": [0, 0, 10],
                "initial_battery": 95,
                "components": ["MoveToComponent", "ChargingComponent"],
                "properties": {},
            }
        ],
        "workflows": [
            {
                "id": "inspection_alpha",
                "name": "Inspection Alpha",
                "type": "inspection_workflow",
                "agent_id": "drone_alpha",
                "enabled": True,
                "properties": {
                    "inspection_points": [[20, 0, 10], [20, 20, 10]],
                },
            }
        ],
    }


def charging_snapshot():
    return {
        "name": "Charging Demo",
        "coordinate_mode": "simulation_plane",
        "traffic": {},
        "agents": [
            {
                "id": "charging_drone",
                "name": "Charging Drone",
                "type": "DroneAgent",
                "initial_position": [80, 80, 30],
                "initial_battery": 10,
                "components": ["MoveToComponent", "ChargingComponent"],
                "properties": {
                    "battery_level": 10,
                },
            }
        ],
        "workflows": [
            {
                "id": "charging_alpha",
                "name": "Charging Alpha",
                "type": "charging_workflow",
                "agent_id": "charging_drone",
                "enabled": True,
                "properties": {
                    "battery_threshold": 20,
                    "target_charge_level": 60,
                },
            }
        ],
    }


def image_processing_snapshot():
    return {
        "name": "Image Processing Demo",
        "coordinate_mode": "simulation_plane",
        "traffic": {},
        "agents": [
            {
                "id": "sensor_drone",
                "name": "Sensor Drone",
                "type": "DroneAgent",
                "initial_position": [10, 10, 20],
                "initial_battery": 88,
                "components": ["ImageSensingComponent", "ComputationComponent"],
                "properties": {},
            }
        ],
        "workflows": [
            {
                "id": "image_alpha",
                "name": "Image Alpha",
                "type": "image_processing_workflow",
                "agent_id": "sensor_drone",
                "enabled": True,
                "properties": {
                    "sensing_locations": [[10, 10, 20]],
                    "image_resolution": "1280x720",
                    "image_format": "jpeg",
                },
            }
        ],
    }


def test_catalog_hides_unsupported_builtin_workflows(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    response = client.get("/api/catalog/workflows")

    assert response.status_code == 200
    workflow_ids = {normalize_workflow_token(item["id"]) for item in response.json()}
    assert workflow_ids == {
        "charging",
        "imageprocessing",
        "inspection",
        "logistics",
    }


def test_default_logistics_run_records_successful_task_logs_and_trajectories(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    put_config(client, "default", logistics_snapshot())
    run_id = start_run(client, "default")

    try:
        bundle = wait_for_bundle(
            client,
            run_id,
            lambda current: (
                extract_agent(current["spatial"], "delivery_drone_alpha") is not None
                and tuple(
                    extract_agent(current["spatial"], "delivery_drone_alpha").get("position") or []
                ) != (120, 20, 30)
                and len(unique_positions(extract_trajectory(current["trajectories"], "delivery_drone_alpha"))) > 1
                and has_successful_task_log(
                    current["logs"],
                    {"MoveToTask", "PickupTask", "HandoverTask"},
                )
            ),
            timeout=12.0,
        )

        drone_snapshot = extract_agent(bundle["spatial"], "delivery_drone_alpha")
        drone_trajectory = extract_trajectory(bundle["trajectories"], "delivery_drone_alpha")

        assert bundle["status"]["status"] in {"running", "completed", "paused"}
        assert drone_snapshot is not None
        assert tuple(drone_snapshot.get("position") or []) != (120, 20, 30)
        assert drone_trajectory is not None
        assert len(unique_positions(drone_trajectory)) > 1
        assert has_successful_task_log(bundle["logs"], {"MoveToTask", "PickupTask", "HandoverTask"})

        points_path = Path(bundle["status"]["output_dir"]) / "trajectories" / "points.jsonl"
        assert points_path.exists()
        assert points_path.read_text().strip()
    finally:
        client.post("/api/runtime/reset")


def test_inspection_run_exposes_movement_logs_and_trajectory(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    put_config(client, "inspection_demo", inspection_snapshot())
    run_id = start_run(client, "inspection_demo")

    try:
        bundle = wait_for_bundle(
            client,
            run_id,
            lambda current: (
                extract_agent(current["spatial"], "drone_alpha") is not None
                and tuple(extract_agent(current["spatial"], "drone_alpha").get("position") or []) != (0, 0, 10)
                and len(unique_positions(extract_trajectory(current["trajectories"], "drone_alpha"))) > 1
                and has_successful_task_log(current["logs"], {"MoveToTask"})
            ),
            timeout=12.0,
        )

        assert has_successful_task_log(bundle["logs"], {"MoveToTask"})
        assert len(unique_positions(extract_trajectory(bundle["trajectories"], "drone_alpha"))) > 1
    finally:
        client.post("/api/runtime/reset")


def test_charging_run_moves_to_charger_and_logs_success(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    put_config(client, "charging_demo", charging_snapshot())
    run_id = start_run(client, "charging_demo")

    try:
        bundle = wait_for_bundle(
            client,
            run_id,
            lambda current: (
                extract_agent(current["spatial"], "charging_drone") is not None
                and tuple(extract_agent(current["spatial"], "charging_drone").get("position") or []) != (80, 80, 30)
                and len(unique_positions(extract_trajectory(current["trajectories"], "charging_drone"))) > 1
                and has_successful_task_log(
                    current["logs"],
                    {"MoveToTask", "ChargingTask", "RequestChargingStationTask"},
                )
            ),
            timeout=12.0,
        )

        drone_snapshot = extract_agent(bundle["spatial"], "charging_drone")
        assert drone_snapshot is not None
        assert tuple(drone_snapshot.get("position") or []) != (80, 80, 30)
        assert has_successful_task_log(
            bundle["logs"],
            {"MoveToTask", "ChargingTask", "RequestChargingStationTask"},
        )
        assert len(unique_positions(extract_trajectory(bundle["trajectories"], "charging_drone"))) > 1
    finally:
        client.post("/api/runtime/reset")


def test_image_processing_run_emits_successful_task_logs_without_trajectory_requirement(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    put_config(client, "image_demo", image_processing_snapshot())
    run_id = start_run(client, "image_demo")

    try:
        bundle = wait_for_bundle(
            client,
            run_id,
            lambda current: has_successful_task_log(
                current["logs"],
                {"FileCollectTask", "FileComputeTask"},
            ),
            timeout=12.0,
        )

        assert has_successful_task_log(bundle["logs"], {"FileCollectTask", "FileComputeTask"})
    finally:
        client.post("/api/runtime/reset")


def test_invalid_logistics_draft_is_blocked_by_validate_and_preflight(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    invalid_snapshot = {
        "name": "Broken Logistics Config",
        "coordinate_mode": "simulation_plane",
        "agents": [
            {
                "id": "station_source",
                "name": "Source Station",
                "type": "delivery_station",
                "initial_position": [0, 0, 0],
                "components": [],
                "properties": {
                    "position": [0, 0, 0],
                    "storage_capacity": 10,
                },
            },
            {
                "id": "delivery_drone_alpha",
                "name": "Delivery Drone Alpha",
                "type": "delivery_drone_agent",
                "initial_position": [10, 10, 30],
                "initial_battery": 90,
                "components": ["ChargingComponent"],
                "properties": {},
            },
        ],
        "workflows": [
            {
                "id": "workflow_logistics_1",
                "name": "Broken Logistics Flow",
                "type": "logistics_workflow",
                "agent_id": "delivery_drone_alpha",
                "enabled": True,
                "properties": {
                    "pickup_location": [0, 0],
                    "delivery_location": [100, 100, "high"],
                    "payloads": [],
                    "source_agent_id": "missing_source",
                    "target_agent_id": "",
                },
            }
        ],
    }

    validate_response = client.post("/api/configs/default/validate", json=invalid_snapshot)
    assert validate_response.status_code == 200
    validation_payload = validate_response.json()
    assert validation_payload["is_valid"] is False
    assert any("pickup_location" in message for message in validation_payload["errors"])
    assert any("delivery_location" in message for message in validation_payload["errors"])
    assert any("at least one payload" in message for message in validation_payload["errors"])
    assert any("missing source agent" in message for message in validation_payload["errors"])
    assert any("missing target agent" in message for message in validation_payload["errors"])
    assert any("MoveToComponent" in message for message in validation_payload["errors"])
    assert any("LogisticsComponent" in message for message in validation_payload["errors"])

    preflight_response = client.post("/api/configs/default/preflight", json=invalid_snapshot)
    assert preflight_response.status_code == 200
    preflight_payload = preflight_response.json()
    assert preflight_payload["is_ready"] is False
    assert any(check["name"] == "validation" and check["status"] == "error" for check in preflight_payload["checks"])
    assert any("pickup_location" in message for message in preflight_payload["errors"])


def test_unsupported_builtin_workflows_are_blocked_by_validate_and_preflight(monkeypatch, tmp_path):
    _app_module, client = load_visualization_app(monkeypatch, tmp_path)

    unsupported_snapshot = {
        "name": "Unsupported Contract Demo",
        "coordinate_mode": "simulation_plane",
        "agents": [
            {
                "id": "drone_alpha",
                "name": "Drone Alpha",
                "type": "DroneAgent",
                "initial_position": [0, 0, 10],
                "initial_battery": 80,
                "components": ["MoveToComponent"],
                "properties": {},
            }
        ],
        "workflows": [
            {
                "id": "contract_alpha",
                "name": "Contract Alpha",
                "type": "contract_workflow",
                "agent_id": "drone_alpha",
                "enabled": True,
                "properties": {
                    "contract_id": "contract_demo",
                    "tasks": [],
                },
            }
        ],
    }

    validate_response = client.post("/api/configs/default/validate", json=unsupported_snapshot)
    assert validate_response.status_code == 200
    validation_payload = validate_response.json()
    assert validation_payload["is_valid"] is False
    assert any("not supported by the workbench runtime" in message for message in validation_payload["errors"])

    preflight_response = client.post("/api/configs/default/preflight", json=unsupported_snapshot)
    assert preflight_response.status_code == 200
    preflight_payload = preflight_response.json()
    assert preflight_payload["is_ready"] is False
    assert any("not supported by the workbench runtime" in message for message in preflight_payload["errors"])


def test_custom_registry_mobility_workflow_produces_trajectory_and_success_logs(monkeypatch, tmp_path):
    app_module, client = load_visualization_app(monkeypatch, tmp_path)

    app_module.registry_service.save_definition(
        "workflows",
        {
            "id": "mobility_sample",
            "version": "1.0.0",
            "display_name": {"en_US": "Mobility Sample"},
            "description": {"en_US": "Custom registry workflow that moves a drone"},
            "adapter_type": "custom",
            "states": ["moving", "completed"],
            "start_state": "moving",
            "property_templates": {},
            "trigger_conditions": [
                {
                    "source_state": "moving",
                    "target_state": "completed",
                    "trigger_type": "state",
                    "source_ref": "position",
                    "operator": "equals",
                    "config": {
                        "state_key": "position",
                        "target_value": [40, 0, 10],
                    },
                }
            ],
            "task_bindings": [
                {
                    "workflow_state": "moving",
                    "component": "MoveToComponent",
                    "task_class": "MoveToTask",
                    "task_name": "Move to custom waypoint",
                    "target_state": {"position": [40, 0, 10]},
                    "properties": {
                        "movement_type": "path_following",
                        "target_position": [40, 0, 10],
                    },
                }
            ],
            "critical_path": ["moving", "completed"],
            "supported_agent_types": ["drone_agent"],
        },
    )

    custom_snapshot = {
        "name": "Custom Mobility Demo",
        "coordinate_mode": "simulation_plane",
        "traffic": {},
        "agents": [
            {
                "id": "custom_drone",
                "name": "Custom Drone",
                "type": "drone_agent",
                "initial_position": [0, 0, 10],
                "initial_battery": 80,
                "components": ["MoveToComponent"],
                "properties": {},
            }
        ],
        "workflows": [
            {
                "id": "custom_mobility_run",
                "name": "Custom Mobility Run",
                "type": "mobility_sample",
                "source": "custom",
                "definition_ref": {
                    "kind": "workflows",
                    "definition_id": "mobility_sample",
                    "version": "1.0.0",
                    "source": "custom",
                },
                "agent_id": "custom_drone",
                "enabled": True,
                "properties": {},
            }
        ],
    }

    put_config(client, "custom_mobility_demo", custom_snapshot)
    run_id = start_run(client, "custom_mobility_demo")

    try:
        bundle = wait_for_bundle(
            client,
            run_id,
            lambda current: (
                extract_agent(current["spatial"], "custom_drone") is not None
                and tuple(extract_agent(current["spatial"], "custom_drone").get("position") or []) != (0, 0, 10)
                and len(unique_positions(extract_trajectory(current["trajectories"], "custom_drone"))) > 1
                and has_successful_task_log(current["logs"], {"MoveToTask"})
            ),
            timeout=12.0,
        )

        assert has_successful_task_log(bundle["logs"], {"MoveToTask"})
        assert len(unique_positions(extract_trajectory(bundle["trajectories"], "custom_drone"))) > 1
    finally:
        client.post("/api/runtime/reset")
