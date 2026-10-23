"""Read-only operation descriptors through real catalogue/entry-point discovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aeroagentsim.assistant.api as http_api
import aeroagentsim.assistant_api as operation_api
from aeroagentsim.assistant.catalogue import OperationCatalogue
from aeroagentsim.assistant_api import AssistantOperationPlugin, AssistantReadSpec, OperationSpec, Prerequisite


def forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Descriptor reads must not create sessions, providers or proposals")


class Entry:
    def __init__(self, name: str, value: Any, loaded: list[str]) -> None:
        self.name, self.value, self.loaded = name, value, loaded

    def load(self) -> Any:
        self.loaded.append(self.name)
        return self.value


def family(name: str = "example") -> AssistantOperationPlugin:
    return AssistantOperationPlugin(
        name=name,
        operations=(
            OperationSpec(
                id=f"{name}.prepare", parameters={"type": "object", "properties": {}, "additionalProperties": False},
                title="Prepare annotation", title_zh="准备标注",
                description="Propose annotation metadata", description_zh="提议标注元数据",
                compile=forbidden, applicable=forbidden,
            ),
            OperationSpec(
                id=f"{name}.annotate",
                parameters={"type": "object", "properties": {"text": {"type": "string", "minLength": 1}},
                            "required": ["text"], "additionalProperties": False},
                title="Annotate scene", title_zh="标注场景",
                description="Propose a scene annotation", description_zh="提议场景标注",
                compile=forbidden, applicable=forbidden,
                prerequisites=(Prerequisite(f"{name}.prepare", params={}, when=forbidden,
                                            note="Prepare first / 先准备"),),
            ),
        ),
        read=AssistantReadSpec(summarize=forbidden), dependencies=forbidden, _host=forbidden,
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def entries(*, group: str):
        assert group == operation_api.ENTRY_POINT_GROUP
        return []

    monkeypatch.setattr(operation_api, "entry_points", entries)
    # Inject inert host services. Neither mounting nor GET needs a real session
    # store, model connection, provider client, harness or workspace.
    connection = SimpleNamespace(get=lambda: {"configured": False}, provider=forbidden, test=forbidden)
    sessions = SimpleNamespace(create=forbidden, begin_turn=forbidden)
    harness = SimpleNamespace(submit=forbidden)
    for name in ("ConnectionStore", "SessionStore", "Harness"):
        monkeypatch.setattr(http_api, name, forbidden)
    app = FastAPI()
    app.include_router(http_api.create_router(
        SimpleNamespace(get=forbidden), run_root=tmp_path, templates=SimpleNamespace(),
        connection=connection, sessions=sessions, harness=harness,
    ))
    connection.get = forbidden
    with TestClient(app, raise_server_exceptions=False) as value:
        yield value
    assert list(tmp_path.iterdir()) == []


def test_route_shape_and_both_builtin_families(client: TestClient) -> None:
    response = client.get("/v1/assistant/operations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"api_version", "families"}
    assert type(body["api_version"]) is int and body["api_version"] == 1
    assert [row["name"] for row in body["families"]] == ["lowalt", "traffic"]
    catalogue = OperationCatalogue()
    identities = set()
    for provider in body["families"]:
        assert set(provider) == {"name", "operations"}
        assert provider["operations"]
        for row in provider["operations"]:
            assert set(row) == {"id", "title", "summary", "parameters", "prerequisites"} | ({"aliases"} if "aliases" in row else set())
            assert row["id"].startswith(provider["name"] + ".")
            spec = catalogue.describe(row["id"])
            assert row["title"] == {"en": spec["title"], "zh": spec["title_zh"]}
            assert row["summary"] == {"en": spec["description"], "zh": spec["description_zh"]}
            assert row["parameters"] == spec["parameters"]
            assert row["parameters"]["type"] == "object"
            assert row["prerequisites"] == spec["prerequisites"]
            assert row.get("aliases", []) == [alias for alias, target in catalogue.aliases.items() if target == row["id"]]
            identities.add(row["id"])
    assert identities == set(catalogue.operations)
    assert {"lowalt.add_fleet", "traffic.set_wind"} <= identities
    lowalt = body["families"][0]["operations"]
    assert next(row for row in lowalt if row["id"] == "lowalt.add_fleet")["aliases"] == ["add_fleet"]


def test_installed_family_appears_as_data_without_invoking_callbacks(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []
    plugin = family()
    monkeypatch.setattr(operation_api, "entry_points", lambda **kwargs: [Entry("example", lambda: plugin, loaded)])
    assert loaded == []  # Discovery is request-scoped, not router construction.
    response = client.get("/v1/assistant/operations")
    assert response.status_code == 200, response.text
    providers = response.json()["families"]
    assert [row["name"] for row in providers] == ["example", "lowalt", "traffic"]
    assert loaded == ["example"]
    assert providers[0]["operations"][1] == {
        "id": "example.annotate", "title": {"en": "Annotate scene", "zh": "标注场景"},
        "summary": {"en": "Propose a scene annotation", "zh": "提议场景标注"},
        "parameters": {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}},
                       "required": ["text"], "additionalProperties": False},
        "prerequisites": [{"operation": "example.prepare", "params": {}, "conditional": True,
                           "note": "Prepare first / 先准备", "warning": None}],
    }
    assert "compile" not in providers[0] and "read" not in providers[0]
    assert all("aliases" not in row for row in providers[0]["operations"])


def test_installed_replacement_does_not_retain_retired_operations_or_aliases(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(operation_api, "entry_points", lambda **kwargs: [Entry("lowalt", family("lowalt"), loaded)])
    response = client.get("/v1/assistant/operations")
    assert response.status_code == 200, response.text
    operations = response.json()["families"][0]["operations"]
    assert [row["id"] for row in operations] == ["lowalt.prepare", "lowalt.annotate"]
    assert all("aliases" not in row for row in operations)
    assert loaded == ["lowalt"]


def test_duplicate_discovery_preserves_the_legacy_boundary_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(operation_api, "BUILTINS", {"builtin": lambda: loaded.append("builtin")})
    monkeypatch.setattr(operation_api, "entry_points", lambda **kwargs: [Entry("example", family, loaded), Entry("example", family, loaded)])
    response = client.get("/v1/assistant/operations")
    assert response.status_code == 422
    assert response.json() == {"detail": "Duplicate aeroagentsim.assistant_operations entry point name: 'example'"}
    assert loaded == []


def test_invalid_descriptor_preserves_the_legacy_boundary_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(operation_api, "entry_points", lambda **kwargs: [Entry("example", object(), loaded)])
    response = client.get("/v1/assistant/operations")
    assert response.status_code == 422
    assert response.json() == {"detail": "aeroagentsim.assistant_operations entry point 'example': expected an AssistantOperationPlugin or a zero-argument callable returning one"}
    assert loaded == ["example"]


@pytest.mark.parametrize("error,status", [(ValueError("Family unavailable / 操作族不可用"), 422), (RuntimeError("Factory failed"), 500)])
def test_factory_failure_never_becomes_an_empty_success(client: TestClient, monkeypatch: pytest.MonkeyPatch, error: Exception, status: int) -> None:
    def fail():
        raise error

    monkeypatch.setattr(operation_api, "entry_points", lambda **kwargs: [Entry("example", fail, [])])
    response = client.get("/v1/assistant/operations")
    assert response.status_code == status
    if status == 422:
        assert response.json() == {"detail": str(error)}
    else:
        assert response.text == "Internal Server Error"
