"""Template HTTP execution, editable workspaces and measured run comparisons."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from aeroagentsim.authoring.templates import demo_source
from aeroagentsim.services.app import create_app


@pytest.fixture
def template_root(tmp_path: Path) -> Path:
    root = tmp_path / "templates"
    source = root / "experiment"
    source.mkdir(parents=True)
    shutil.copyfile(
        demo_source("traffic-accident") / "registry.snapshot.json",
        source / "registry.snapshot.json",
    )
    document = {
        "format": "aeroagentsim.scenario/v1",
        "id": "experiment",
        "registry": {"snapshot": "registry.snapshot.json"},
        "entities": [],
        "bindings": {"exact": [], "lifecycle": []},
        "engines": {},
        "algorithms": {"allocation": "nearest"},
        "run": {
            "pacing": "fast",
            "seed": 1,
            "until_ns": 1_000_000_000,
            "advance_ns": 1_000_000_000,
        },
        "outputs": {"durability": "flush"},
    }
    (source / "scenario.yaml").write_text(yaml.safe_dump(document))
    metadata = {
        "format": "aeroagentsim.template/v1",
        "id": "experiment",
        "title": "Experiment",
        "title_zh": "实验",
        "summary": "Compare pinned allocation settings.",
        "summary_zh": "对比固定的任务分配参数。",
        "category": "logistics",
        "difficulty": "beginner",
        "duration_s": 1,
        "scenario": "scenario.yaml",
        "observe": ["Inspect committed task counts.", "Compare run settings."],
        "observe_zh": ["查看已提交的任务计数。", "对比运行参数。"],
        "try": ["Change the random seed.", "Switch the allocation algorithm."],
        "try_zh": ["调整随机种子。", "切换分配算法。"],
        "knobs": [
            {
                "id": "seed",
                "label": "Seed",
                "label_zh": "随机种子",
                "description": "Controls reproducible task arrivals.",
                "description_zh": "控制可复现的任务到达过程。",
                "type": "integer",
                "default": 1,
                "min": 0,
                "max": 10,
                "pointer": "/run/seed",
            },
            {
                "id": "allocator",
                "label": "Allocation",
                "label_zh": "任务分配",
                "type": "choice",
                "default": "nearest",
                "options": ["nearest", "auction"],
                "options_zh": ["就近分配", "竞价分配"],
                "pointer": "/algorithms/allocation",
            },
        ],
        "kpis": ["tasks_created", "tasks_completed"],
    }
    (source / "template.yaml").write_text(yaml.safe_dump(metadata))
    return root


def completed(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        row = next(run for run in client.get("/v1/runs").json() if run["id"] == run_id)
        if row["status"] == "completed":
            return dict(row)
        assert row["status"] not in {"faulted", "interrupted", "input_timeout"}, row
        time.sleep(0.02)
    raise AssertionError(f"Template run {run_id} did not complete: {row}")


def test_template_endpoints_workspace_kpis_comparison(
    template_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    with TestClient(
        create_app(tmp_path / "runs", templates_root=template_root),
        base_url="http://localhost",
    ) as client:
        assert [row["id"] for row in client.get("/v1/templates").json()] == [
            "experiment"
        ]
        detail = client.get("/v1/templates/experiment").json()
        assert detail["knobs"][0]["id"] == "seed"
        assert detail["title_zh"] == "实验"
        assert detail["summary_zh"] == "对比固定的任务分配参数。"
        assert detail["observe_zh"] == ["查看已提交的任务计数。", "对比运行参数。"]
        assert detail["try_zh"] == ["调整随机种子。", "切换分配算法。"]
        assert detail["knobs"][0]["label_zh"] == "随机种子"
        assert detail["knobs"][0]["description_zh"] == "控制可复现的任务到达过程。"
        assert detail["knobs"][1]["options_zh"] == ["就近分配", "竞价分配"]
        assert client.get("/v1/templates").json() == [detail]
        assert client.get("/v1/templates/missing").status_code == 404
        response = client.post(
            "/v1/templates/experiment/workspace", json={"knobs": {"seed": 2}}
        )
        assert response.status_code == 201, response.text
        workspace_id = response.json()["workspace_id"]
        draft = client.get(f"/v1/studio/workspaces/{workspace_id}").json()
        assert draft["scenario"]["run"]["seed"] == 2
        first = client.post(
            "/v1/templates/experiment/runs",
            json={"knobs": {"seed": 2}, "workspace": True},
        )
        assert first.status_code == 201, first.text
        a = first.json()["run_id"]
        assert first.json()["workspace_id"] != workspace_id
        second = client.post(
            "/v1/templates/experiment/runs", json={"knobs": {"allocator": "auction"}}
        )
        assert second.status_code == 201, second.text
        b = second.json()["run_id"]
        row = completed(client, a)
        completed(client, b)
        assert row["template"]["title_zh"] == "实验"
        assert row["title_zh"] == row["template_title_zh"] == "实验"
        assert row["title"] == row["template_title"] == "Experiment"
        measured = client.get(f"/v1/runs/{a}/kpis").json()
        assert measured["contract"] == "aeroagentsim.kpi/v1"
        assert measured["final"] == row["kpis"]
        assert measured["final"]["tasks_completed"]["value"] == 0
        cuts = client.get(f"/v1/runs/{a}/commits").json()["commits"]
        assert cuts[-1]["kpis"] == measured["final"]
        assert "events" in cuts[-1]
        compared = client.get("/v1/runs/compare", params={"ids": f"{a},{b}"})
        assert compared.status_code == 200, compared.text
        result = compared.json()
        assert all(run["title_zh"] == "实验" for run in result["runs"])
        assert all(run["template_title_zh"] == "实验" for run in result["runs"])
        assert result["differences"]["algorithms"][0]["values"] == {
            a: "nearest",
            b: "auction",
        }
        assert {item["name"] for item in result["differences"]["knobs"]} == {
            "seed",
            "allocator",
        }
        assert (
            client.get("/v1/runs/compare", params={"ids": f"{a},{a}"}).status_code
            == 422
        )
        assert (
            client.get("/v1/runs/compare", params={"ids": f"{a},missing"}).status_code
            == 404
        )
        assert client.get("/v1/runs/missing/kpis").status_code == 404


def test_template_workspace_without_city_has_no_demo_scene(
    template_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    with TestClient(
        create_app(tmp_path / "runs", templates_root=template_root),
        base_url="http://localhost",
    ) as client:
        response = client.post("/v1/templates/experiment/workspace", json={})
        assert response.status_code == 201, response.text
        draft = client.get(
            f"/v1/studio/workspaces/{response.json()['workspace_id']}"
        ).json()
        assert "city" not in draft["scenario"]
        assert "demo_console" not in draft


def test_template_workspace_with_city_carries_shared_scene(
    template_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = yaml.safe_load((template_root / "experiment/template.yaml").read_text())
    metadata["city"] = "huangpu"
    (template_root / "experiment/template.yaml").write_text(yaml.safe_dump(metadata))
    document = yaml.safe_load(
        (template_root / "experiment/scenario.yaml").read_text()
    )
    document["city"] = "huangpu"
    (template_root / "experiment/scenario.yaml").write_text(yaml.safe_dump(document))
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    with TestClient(
        create_app(tmp_path / "runs", templates_root=template_root),
        base_url="http://localhost",
    ) as client:
        response = client.post("/v1/templates/experiment/workspace", json={})
        assert response.status_code == 201, response.text
        draft = client.get(
            f"/v1/studio/workspaces/{response.json()['workspace_id']}"
        ).json()
        assert draft["scenario"]["city"] == "huangpu"
        assert draft["demo_console"]["capture_mode"] == "city"
        scene = draft["demo_console"]["scene"]
        assert scene["city"]["kind"] == "traffic-city"
        assert scene["city"]["url"].endswith("/v1/studio/demo-assets/scene.json")
        assert scene["camera"]["position"] == [280, 220, 300]


def test_template_invalid_knobs_and_security(
    template_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AEROAGENTSIM_API_TOKEN", "template-test-token")
    with TestClient(
        create_app(tmp_path / "runs", templates_root=template_root),
        base_url="http://localhost",
    ) as client:
        assert client.get("/v1/templates").status_code == 401
        client.headers["Authorization"] = "Bearer template-test-token"
        assert client.get("/v1/templates").status_code == 200
        endpoint = "/v1/templates/experiment/runs"
        assert client.post(endpoint, content="{}").status_code == 415
        assert (
            client.post(
                endpoint, json={}, headers={"Origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        assert client.post(endpoint, json={"knobs": {"seed": True}}).status_code == 422
        assert client.post(endpoint, json={"knobs": {"seed": 11}}).status_code == 422
        assert (
            client.post(endpoint, json={"knobs": {"allocator": "missing"}}).status_code
            == 422
        )
        assert client.post(endpoint, json={"knobs": {"unknown": 1}}).status_code == 422
        assert client.post(endpoint, json={"knobs": None}).status_code == 422
        assert client.post(endpoint, json={"workspace": "true"}).status_code == 422
        assert client.post("/v1/templates/missing/runs", json={}).status_code == 404


def test_traffic_template_api_completes_without_aerograph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AEROAGENTSIM_AEROGRAPH_ROOT", raising=False)
    with TestClient(
        create_app(tmp_path / "runs"), base_url="http://localhost"
    ) as client:
        response = client.post("/v1/templates/traffic-accident/runs", json={})
        assert response.status_code == 201, response.text
        run_id = response.json()["run_id"]
        completed(client, run_id)
        final = client.get(f"/v1/runs/{run_id}/kpis").json()["final"]
        assert final["tasks_created"]["value"] == 1
        assert final["tasks_completed"]["value"] == 1
        assert final["mean_task_latency_s"]["value"] > 0
        events: list[dict[str, Any]] = []
        cursor = 1
        while True:
            page = client.get(
                f"/v1/runs/{run_id}/commits", params={"from": cursor, "limit": 4096}
            ).json()
            if not page["commits"]:
                break
            events.extend(event for cut in page["commits"] for event in cut["events"])
            cursor = page["next"]
        assert any(
            "upload" in event["text"].lower() or "accepted" in event["text"].lower()
            for event in events
        )


def test_traffic_template_snapshots_its_rendered_city(tmp_path: Path) -> None:
    from aeroagentsim.authoring.replay import scene_header
    from aeroagentsim.services.template_execution import snapshot_template_scene

    snapshot_template_scene(
        "traffic-accident",
        {"engines": {"capture": {"config": {"renderer": {"mode": "traffic_browser"}}}}},
        tmp_path,
    )
    scene = scene_header(tmp_path, "http://localhost:9000")["scene"]
    assert scene["city"]["url"] == "/v1/studio/demo-assets/scene.json"
    assert scene["camera"]["target"] == [91.95, 0, 93.04]


def test_all_shipped_template_translations_pass_through_http(tmp_path: Path) -> None:
    from aeroagentsim.authoring.templates import TemplateRegistry

    source = Path(__file__).resolve().parents[2] / "scenarios/templates"
    registry = TemplateRegistry(source)
    with TestClient(
        create_app(tmp_path / "runs", templates_root=source), base_url="http://localhost"
    ) as client:
        response = client.get("/v1/templates")
        assert response.status_code == 200, response.text
        assert len(response.json()) == 9
        for metadata in registry.list():
            detail = client.get(f"/v1/templates/{metadata['id']}")
            assert detail.status_code == 200, detail.text
            expected = {
                **metadata,
                "thumbnail_url": f"/v1/templates/{metadata['id']}/thumbnail",
            }
            assert detail.json() == expected
            assert expected in response.json()


def test_legacy_template_run_omits_chinese_title(
    template_root: Path, tmp_path: Path
) -> None:
    file = template_root / "experiment/template.yaml"
    metadata = yaml.safe_load(file.read_text())
    del metadata["title_zh"]
    file.write_text(yaml.safe_dump(metadata))
    with TestClient(
        create_app(tmp_path / "runs", templates_root=template_root),
        base_url="http://localhost",
    ) as client:
        assert "title_zh" not in client.get("/v1/templates/experiment").json()
        response = client.post("/v1/templates/experiment/runs", json={})
        assert response.status_code == 201, response.text
        row = completed(client, response.json()["run_id"])
        assert "title_zh" not in row["template"]
        assert "title_zh" not in row and "template_title_zh" not in row
