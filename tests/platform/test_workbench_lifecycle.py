"""Draft → validate → version → initialize → explicit start, for every shipped template."""

from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.services.app import create_app

SHIPPED = [row["id"] for row in TemplateRegistry(None).list()]


def _hold_worker_network(connection: Any) -> None:
    from aeroagentsim.services.worker import close_inherited_network_sockets

    close_inherited_network_sockets()
    connection.send("ready")
    connection.recv()
    connection.send("IPC intact")
    connection.close()


def test_forked_worker_does_not_keep_closed_http_connections_alive() -> None:
    import multiprocessing
    import socket

    context = multiprocessing.get_context("fork")
    host, worker = context.Pipe()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    browser = socket.create_connection(listener.getsockname(), timeout=2)
    accepted, _ = listener.accept()
    process = context.Process(target=_hold_worker_network, args=(worker,))
    process.start()
    worker.close()
    try:
        assert host.poll(5) and host.recv() == "ready"
        accepted.close()
        listener.close()
        # The client must see EOF while the simulation process is still alive;
        # otherwise its next keep-alive request waits in an orphaned socket.
        assert process.is_alive()
        assert browser.recv(1) == b""
        host.send("finish")
        assert host.poll(5) and host.recv() == "IPC intact"
        process.join(5)
        assert process.exitcode == 0
    finally:
        accepted.close()
        listener.close()
        browser.close()
        host.close()
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_in_process_worker_preserves_host_network_connections(tmp_path: Path, document: dict[str, Any]) -> None:
    import socket
    import threading

    from aeroagentsim.scenario import load_scenario
    from aeroagentsim.services.storage import RunStorage
    from aeroagentsim.services.worker import execute

    document["run"]["until_ns"] = 0
    scenario = load_scenario(document)
    directory = tmp_path / "in-process"
    RunStorage(directory).prepare(scenario)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(2)
        with socket.create_connection(listener.getsockname(), timeout=2) as client:
            accepted, _ = listener.accept()
            with accepted:
                execute(scenario, directory, threading.Event(), threading.Event())
                assert listener.fileno() >= 0 and accepted.fileno() >= 0 and client.fileno() >= 0
                accepted.sendall(b"host connection remains live")
                assert client.recv(128) == b"host connection remains live"
        with socket.create_connection(listener.getsockname(), timeout=2):
            accepted, _ = listener.accept()
            accepted.close()
    assert RunStorage(directory).metadata()["status"] == "completed"


def wait_status(client: TestClient, run_id: str, wanted: set[str], timeout: float = 120) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = next(run for run in client.get("/v1/runs", params={"include_kpis": False}).json() if run["id"] == run_id)
        if row["status"] in wanted:
            return row
        time.sleep(0.05)
    raise AssertionError(f"{run_id} did not reach {wanted}; last {row['status']}")


def test_ten_shipped_templates() -> None:
    assert len(SHIPPED) == 10
    assert {"parcel-delivery", "medical-delivery", "powerline-inspection", "crop-spraying", "air-taxi",
            "security-patrol", "traffic-accident", "city-day", "food-delivery", "flood-rescue"} == set(SHIPPED)


@pytest.mark.parametrize("template_id", SHIPPED)
def test_template_draft_version_initialize_start(tmp_path: Path, template_id: str) -> None:
    with TestClient(create_app(tmp_path / "runs", studio_root=tmp_path / "studio"), base_url="http://localhost") as client:
        created = client.post(f"/v1/templates/{template_id}/workspace", json={"knobs": {}})
        assert created.status_code == 201, created.text
        workspace_id = created.json()["workspace_id"]
        draft = client.get(f"/v1/studio/workspaces/{workspace_id}").json()
        assert draft["template"]["id"] == template_id
        assert draft["latest_version"] is None and draft["dirty"] is True
        assert client.get("/v1/runs").json() == []  # Creating from a template never runs.

        # A version requires a passing validation of exactly that revision.
        refused = client.post(f"/v1/studio/workspaces/{workspace_id}/versions", json={"revision": draft["revision"]})
        assert refused.status_code == 422
        validation = client.post(f"/v1/studio/workspaces/{workspace_id}/validate", json={"revision": draft["revision"]}).json()
        assert validation["valid"], validation
        assert client.get("/v1/runs").json() == []  # Validation never runs.
        version = client.post(f"/v1/studio/workspaces/{workspace_id}/versions", json={"revision": draft["revision"]})
        assert version.status_code == 201, version.text
        frozen = version.json()
        assert frozen["version"] == 1 and frozen["digest"].startswith("sha256:")
        again = client.post(f"/v1/studio/workspaces/{workspace_id}/versions", json={"revision": draft["revision"]}).json()
        assert again["version"] == 1  # Same revision, same version.
        summary = client.get(f"/v1/studio/workspaces/{workspace_id}").json()
        assert summary["dirty"] is False and summary["latest_version"]["version"] == 1

        run = client.post("/v1/runs", json={"studio_workspace": workspace_id, "version": 1})
        assert run.status_code == 201, run.text
        metadata = run.json()
        run_id = metadata["id"]
        assert metadata["status"] == "initialized"
        assert metadata["workspace_version"] == 1 and metadata["workspace_digest"] == frozen["digest"]
        header = client.get(f"/v1/runs/{run_id}/header")
        assert header.status_code == 200, header.text
        initial = client.get(f"/v1/runs/{run_id}/commits", params={"from": 0, "limit": 1000}).json()
        assert initial["commits"] and {row["at"]["ns"] for row in initial["commits"]} == {"0"}
        # Initialized: no simulated progress until start.
        time.sleep(0.3)
        assert wait_status(client, run_id, {"initialized"})["status"] == "initialized"
        refreshed = client.get(f"/v1/runs/{run_id}/commits", params={"from": 0, "limit": 1000}).json()
        assert refreshed == initial  # Read/reconnect has no lifecycle side effects.
        for factor in (0.5, 5, 50, None):
            changed = client.post(f"/v1/runs/{run_id}/speed", json={"factor": factor})
            assert changed.status_code == 200 and changed.json()["factor"] == factor
            assert client.get(f"/v1/runs/{run_id}/speed").json()["factor"] == factor
        assert client.post(f"/v1/runs/{run_id}/pause", json={}).status_code == 409

        assert client.post(f"/v1/runs/{run_id}/start", json={}).status_code == 200
        assert client.post(f"/v1/runs/{run_id}/start", json={}).status_code == 409
        wait_status(client, run_id, {"running", "waiting_for_input", "paused", "completed"})
        stop = client.post(f"/v1/runs/{run_id}/stop", json={})
        assert stop.status_code in {200, 409}  # A fast template may complete first.
        final = wait_status(client, run_id, {"stopped", "completed"})
        assert final["workspace_version"] == 1

        derived = client.post(f"/v1/runs/{run_id}/derive-workspace", json={"name": "variant"})
        assert derived.status_code == 201, derived.text
        child = client.get(f"/v1/studio/workspaces/{derived.json()['workspace_id']}").json()
        assert child["derived_from"] == {"run_id": run_id, "workspace_id": workspace_id, "version": 1}
        assert child["template"]["id"] == template_id
        assert child["name"] == "variant"
        assert client.post(f"/v1/studio/workspaces/{child['id']}/validate", json={}).json()["valid"]


def test_configuration_diff_keeps_absence_null_and_json_pointer_identity() -> None:
    from aeroagentsim.services.app import configuration_differences

    differences = configuration_differences({"a": {"same": 1, "field/a~b": None}, "b": {"same": 1}})
    assert differences == [{"path": "/field~1a~0b", "values": {"a": None}, "present": {"a": True, "b": False}}]
    assert configuration_differences({"a": {"value": False}, "b": {"value": 0}})[0]["path"] == "/value"
    assert configuration_differences({"a": {"value": 10}, "b": {"value": 10.0}}) == []
    assert configuration_differences({"a": {"nested": [False]}, "b": {"nested": [0]}})[0]["path"] == "/nested/0"


def test_food_delay_receipt_is_idempotent_and_replay_is_engine_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aeroagentsim.platform.simulation import Simulation

    with TestClient(create_app(tmp_path / "runs", studio_root=tmp_path / "studio"), base_url="http://localhost") as client:
        workspace = client.post("/v1/templates/food-delivery/workspace", json={"knobs": {"run_mode": "interactive"}}).json()["workspace_id"]
        assert client.post(f"/v1/studio/workspaces/{workspace}/validate", json={}).json()["valid"]
        revision = client.get(f"/v1/studio/workspaces/{workspace}").json()["revision"]
        assert client.post(f"/v1/studio/workspaces/{workspace}/versions", json={"revision": revision}).status_code == 201
        run_id = client.post("/v1/runs", json={"studio_workspace": workspace, "version": 1}).json()["id"]
        assert client.post(f"/v1/runs/{run_id}/start", json={}).status_code == 200
        wait_status(client, run_id, {"waiting_for_input"})
        assert client.post(f"/v1/runs/{run_id}/pause", json={}).status_code == 200
        wait_status(client, run_id, {"paused"})
        assert client.post(f"/v1/runs/{run_id}/resume", json={}).status_code == 200
        wait_status(client, run_id, {"waiting_for_input"})
        body = {"schema": "aas.runtime.inject_event", "target": "behaviour", "stream_id": "operator", "at_ns": 1,
                "source_stamp": {"clock_id": "canonical", "mapping_id": "canonical", "numerator": 1, "denominator": 1},
                "idempotency_key": "food-delay-once", "payload": {"injection_point": "restaurant_delay", "payload": {"restaurant": "restaurant-east", "delay_s": 45.0}}}
        invalid = {**body, "payload": {"injection_point": "restaurant_delay", "payload": {"restaurant": "restaurant-east"}}}
        assert client.post(f"/v1/runs/{run_id}/ingress", json=invalid).status_code == 422
        first = client.post(f"/v1/runs/{run_id}/ingress", json=body)
        assert first.status_code == 200, first.text
        assert first.json()["disposition"] == "accepted"
        second = client.post(f"/v1/runs/{run_id}/ingress", json=body)
        assert second.status_code == 200 and second.json()["command_id"] == first.json()["command_id"]
        assert second.json()["journal_index"] == first.json()["journal_index"]
        assert client.post(f"/v1/runs/{run_id}/watermark", json={"stream_id": "operator", "watermark_ns": 50_000_000_000}).status_code == 200
        deadline = time.monotonic() + 60
        while True:
            commits = client.get(f"/v1/runs/{run_id}/commits", params={"from": 0, "limit": 4096}).json()["commits"]
            messages = [message for row in commits for message in row["messages"]]
            if any(message["schemaId"] == "lowalt.operator.restaurant_delay" for message in messages):
                break
            assert time.monotonic() < deadline, "Delay was admitted but never executed"
            time.sleep(0.05)
        assert len([message for message in messages if message["schemaId"] == "aas.runtime.inject_event"]) == 1
        assert any(row["receipts"] for row in commits)
        assert client.post(f"/v1/runs/{run_id}/stop", json={}).status_code == 200
        wait_status(client, run_id, {"stopped"})

        # All history endpoints project existing journals. An accidental engine
        # start/advance during reads fails immediately, including after derive.
        def forbidden(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("History called a simulation engine")
        monkeypatch.setattr(Simulation, "start", forbidden)
        monkeypatch.setattr(Simulation, "run_until", forbidden)
        for endpoint in ("header", "commits?from=0&limit=4096", "kpis", "inputs"):
            assert client.get(f"/v1/runs/{run_id}/{endpoint}").status_code == 200
        progress = client.get(f"/v1/runs/{run_id}/inputs").json()
        assert progress["watermarks"]["operator"] == 50_000_000_000
        # Runs written before the sidecar must recover the same real frontier.
        (tmp_path / "runs" / run_id / "input-progress.json").unlink()
        recovered = client.get(f"/v1/runs/{run_id}/inputs").json()
        assert recovered == progress
        child = client.post(f"/v1/runs/{run_id}/derive-workspace", json={}).json()["workspace_id"]
        assert client.get(f"/v1/studio/workspaces/{child}").json()["derived_from"]["run_id"] == run_id


def test_finished_comparison_uses_pinned_configuration_and_declares_alignment(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "runs", studio_root=tmp_path / "studio"), base_url="http://localhost") as client:
        workspace = client.post("/v1/templates/food-delivery/workspace", json={"knobs": {}}).json()["workspace_id"]
        ids = []
        for seed in (42, 43):
            draft = client.get(f"/v1/studio/workspaces/{workspace}").json()
            draft["scenario"]["run"]["seed"] = seed
            saved = client.post(f"/v1/studio/workspaces/{workspace}", json={"revision": draft["revision"], "scenario": draft["scenario"]}).json()
            assert client.post(f"/v1/studio/workspaces/{workspace}/validate", json={"revision": saved["revision"]}).json()["valid"]
            version = client.post(f"/v1/studio/workspaces/{workspace}/versions", json={"revision": saved["revision"]}).json()["version"]
            run_id = client.post("/v1/runs", json={"studio_workspace": workspace, "version": version}).json()["id"]
            ids.append(run_id)
            assert client.post(f"/v1/runs/{run_id}/stop", json={}).status_code == 200
            wait_status(client, run_id, {"stopped"})
        reply = client.get("/v1/runs/compare", params={"ids": ",".join(ids)})
        assert reply.status_code == 200, reply.text
        comparison = reply.json()
        assert comparison["alignment"] == "recorded_run_end"
        assert set(comparison["recorded_ends"]) == set(ids)
        assert all(end["at_ns"] == "0" and end["status"] == "stopped" and end["commit_index"] >= 1 for end in comparison["recorded_ends"].values())
        seed_diff = next(row for row in comparison["config_diff"] if row["path"] == "/run/seed")
        assert seed_diff["values"] == dict(zip(ids, (42, 43)))
        metadata = client.get("/v1/runs/compare", params={"ids": ",".join(ids), "include_kpis": False}).json()
        assert metadata["config_diff"] == comparison["config_diff"]
        assert all("kpis" not in row for row in metadata["runs"])


def test_stop_initialized_run_and_draft_edits_do_not_reach_version(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "runs", studio_root=tmp_path / "studio"), base_url="http://localhost") as client:
        workspace_id = client.post("/v1/templates/parcel-delivery/workspace", json={"knobs": {}}).json()["workspace_id"]
        draft = client.get(f"/v1/studio/workspaces/{workspace_id}").json()
        client.post(f"/v1/studio/workspaces/{workspace_id}/validate", json={"revision": draft["revision"]})
        client.post(f"/v1/studio/workspaces/{workspace_id}/versions", json={"revision": draft["revision"]})
        scenario = draft["scenario"]
        scenario["run"]["seed"] = int(scenario["run"].get("seed", 0)) + 7
        saved = client.post(f"/v1/studio/workspaces/{workspace_id}", json={"scenario": scenario, "revision": draft["revision"]}).json()
        assert saved["revision"] == draft["revision"] + 1
        frozen = client.get(f"/v1/studio/workspaces/{workspace_id}/versions/1").json()
        assert frozen["scenario"]["run"]["seed"] != scenario["run"]["seed"]
        assert client.get(f"/v1/studio/workspaces/{workspace_id}").json()["dirty"] is True

        run_id = client.post("/v1/runs", json={"studio_workspace": workspace_id, "version": 1}).json()["id"]
        assert client.post(f"/v1/runs/{run_id}/stop", json={}).status_code == 200
        assert wait_status(client, run_id, {"stopped"})["status"] == "stopped"


def test_validation_issue_carries_section(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "runs", studio_root=tmp_path / "studio"), base_url="http://localhost") as client:
        workspace_id = client.post("/v1/templates/parcel-delivery/workspace", json={"knobs": {}}).json()["workspace_id"]
        draft = client.get(f"/v1/studio/workspaces/{workspace_id}").json()
        scenario = draft["scenario"]
        scenario["engines"]["missing"] = {"plugin": "no_such_plugin", "config": {}}
        result = client.post(f"/v1/studio/workspaces/{workspace_id}/validate", json={"scenario": scenario, "revision": draft["revision"]}).json()
        assert result["valid"] is False
        assert result["issues"][0].get("section") == "components", result


def test_template_knobs_edit_the_draft_in_place(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "runs", studio_root=tmp_path / "studio"), base_url="http://localhost") as client:
        workspace_id = client.post("/v1/templates/parcel-delivery/workspace", json={"knobs": {}}).json()["workspace_id"]
        draft = client.get(f"/v1/studio/workspaces/{workspace_id}").json()
        scenario = draft["scenario"]
        scenario["run"]["seed"] = 41
        draft = client.post(f"/v1/studio/workspaces/{workspace_id}", json={"scenario": scenario, "revision": draft["revision"]}).json()
        changed = client.post(f"/v1/studio/workspaces/{workspace_id}/knobs", json={"knobs": {"fleet_size": 3, "allocator": "auction"}, "revision": draft["revision"]})
        assert changed.status_code == 200, changed.text
        result = changed.json()
        assert result["scenario"]["generators"]["fleet"]["count"] == 3
        assert result["scenario"]["algorithms"]["allocation"] == "auction"
        assert result["scenario"]["run"]["seed"] == 41  # Other edits survive.
        assert result["template"]["knobs"]["fleet_size"] == 3
        stale = client.post(f"/v1/studio/workspaces/{workspace_id}/knobs", json={"knobs": {"fleet_size": 4}, "revision": draft["revision"]})
        assert stale.status_code == 409
        bad = client.post(f"/v1/studio/workspaces/{workspace_id}/knobs", json={"knobs": {"fleet_size": 999}, "revision": result["revision"]})
        assert bad.status_code == 422
        assert client.post(f"/v1/studio/workspaces/{workspace_id}/validate", json={"revision": result["revision"]}).json()["valid"]


def test_run_speed_paces_host_time_only(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "runs", studio_root=tmp_path / "studio"), base_url="http://localhost") as client:
        workspace_id = client.post("/v1/templates/parcel-delivery/workspace", json={"knobs": {}}).json()["workspace_id"]
        draft = client.get(f"/v1/studio/workspaces/{workspace_id}").json()
        client.post(f"/v1/studio/workspaces/{workspace_id}/validate", json={"revision": draft["revision"]})
        client.post(f"/v1/studio/workspaces/{workspace_id}/versions", json={"revision": draft["revision"]})
        run_id = client.post("/v1/runs", json={"studio_workspace": workspace_id, "version": 1}).json()["id"]
        assert client.get(f"/v1/runs/{run_id}/speed").json()["factor"] is None  # fast pacing
        assert client.post(f"/v1/runs/{run_id}/speed", json={"factor": 0}).status_code == 422
        assert client.post(f"/v1/runs/{run_id}/speed", json={"factor": 1}).json()["factor"] == 1
        assert client.post(f"/v1/runs/{run_id}/start", json={}).status_code == 200
        time.sleep(1.5)
        assert wait_status(client, run_id, {"running", "waiting_for_input"})["status"] in {"running", "waiting_for_input"}
        assert client.post(f"/v1/runs/{run_id}/speed", json={"factor": None}).json()["factor"] is None
        assert wait_status(client, run_id, {"completed"})["status"] == "completed"
        assert client.post(f"/v1/runs/{run_id}/speed", json={"factor": 2}).status_code == 409


def test_run_shows_the_shared_city_only_while_the_draft_declares_it(tmp_path: Path) -> None:
    from aeroagentsim.authoring.replay import attach

    scene = {"id": "Huangpu", "city": {"kind": "traffic-city", "url": "/scene.json"}}
    draft = {"scenario": {"city": "huangpu"}, "demo_console": {"capture_mode": "city", "scene": scene}}
    attach(tmp_path, draft, tmp_path)
    assert json.loads((tmp_path / "console-scene.json").read_text()) == scene
    (tmp_path / "console-scene.json").unlink()
    attach(tmp_path, {**draft, "scenario": {"city": None}}, tmp_path)
    assert not (tmp_path / "console-scene.json").exists()
