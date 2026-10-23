"""Viewer labels/grouping must use the recorded registry snapshot."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aeroagentsim import presentation_api
from aeroagentsim.platform import RunSession
from aeroagentsim.services.app import create_app
from aeroagentsim.services.projector import header


def test_pinned_type_browsing_metadata(slice_run: tuple[RunSession, Path]) -> None:
    _, directory = slice_run
    raw_types = json.loads((directory / "registry.snapshot.json").read_text())[
        "details"
    ]["raw_types"]
    run_header = header(directory)
    for info in run_header["types"]:
        source = raw_types.get(info["typeId"])
        if source is not None:
            assert info["displayName"] == source["name"]
            assert info["directory"] == source["navigation"]["view"]
        else:
            # A scenario-authored type is still browsable by its exact ID and
            # declared inheritance; it is not assigned a guessed directory.
            assert info["displayName"] == info["typeId"]
            assert "directory" not in info


def test_commits_pages_do_not_repeat_presentation_discovery_or_snapshot_verification(
    slice_run: tuple[RunSession, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, source = slice_run
    directory = tmp_path / "run"
    shutil.copytree(source, directory)
    snapshot = presentation_api.presentation_snapshot(presentation_api.discover_presentation_plugins())
    (directory / "presentation.snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({
        **json.loads(manifest.read_text()), "presentation_snapshot_digest": snapshot["digest"],
    }), encoding="utf-8")
    calls = {"entry_points": 0, "discover": 0, "verify": 0}
    original_entries = presentation_api.entry_points
    original_discover = presentation_api.discover
    original_verify = presentation_api.verify_presentation_snapshot

    def entries(*args: Any, **kwargs: Any) -> Any:
        calls["entry_points"] += 1
        return original_entries(*args, **kwargs)

    def discover(*args: Any, **kwargs: Any) -> Any:
        calls["discover"] += 1
        return original_discover(*args, **kwargs)

    def verify(*args: Any, **kwargs: Any) -> Any:
        calls["verify"] += 1
        return original_verify(*args, **kwargs)

    def forbidden_digest(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a warm page must use the stored verified snapshot digest")

    monkeypatch.setattr(presentation_api, "entry_points", entries)
    monkeypatch.setattr(presentation_api, "discover", discover)
    monkeypatch.setattr(presentation_api, "verify_presentation_snapshot", verify)
    presentation_api.clear_presentation_plugin_cache()
    try:
        with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
            response = client.get("/v1/runs/run/commits", params={"from": 1, "limit": 1})
            assert response.status_code == 200, response.text
            first = response.json()
            assert first["commits"] and first["next"] == 2
            assert calls == {"entry_points": 1, "discover": 1, "verify": 1}
            # Finish the cold projection before forbidding digest work. Later
            # pages may restore a checkpoint, but must not rebuild its signature.
            from aeroagentsim.services.feed_index import page

            complete = page(directory, 1, 4096)
            assert calls["verify"] == 1
            monkeypatch.setattr(presentation_api, "presentation_digest", forbidden_digest)
            for _ in range(3):
                assert page(directory, 1, 4096) == complete
                assert calls["verify"] == 1
            baseline = dict(calls)
            for start in (1, 2, 1, 2):
                response = client.get("/v1/runs/run/commits", params={"from": start, "limit": 1})
                assert response.status_code == 200, response.text
                assert response.json()["commits"][0]["commitIndex"] == start
                if start == 1:
                    assert response.json()["commits"] == first["commits"]
                    assert response.json()["next"] == first["next"]
                assert calls == baseline
    finally:
        presentation_api.clear_presentation_plugin_cache()


def test_page_rejects_a_tampered_snapshot_after_warming_the_index(
    slice_run: tuple[RunSession, Path], tmp_path: Path,
) -> None:
    from aeroagentsim.services.feed_index import page

    _, source = slice_run
    directory = tmp_path / "run"
    shutil.copytree(source, directory)
    snapshot = presentation_api.presentation_snapshot(presentation_api.discover_presentation_plugins())
    path = directory / "presentation.snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({
        **json.loads(manifest.read_text()), "presentation_snapshot_digest": snapshot["digest"],
    }), encoding="utf-8")
    assert page(directory, 1, 1)
    snapshot["providers"][0]["revision"] += "-tampered"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot digest mismatch"):
        page(directory, 1, 1)


@pytest.mark.parametrize("explicit_available", [False, True])
def test_second_launch_and_warm_selection_keep_frozen_normalizer_descriptors(
    tmp_path: Path, document: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    explicit_available: bool,
) -> None:
    from aeroagentsim.presentations import presentation_selection

    class MutableNormalizer:
        id = "external.flatten"
        version = "1"
        input_schemas = ("external.raw",)
        fact_fields: tuple[str, ...] = ()

        def __init__(self) -> None:
            self.output_schemas = {"external.raw": {
                "type": "record", "members": {"reading": {"type": "string"}},
                "required": [], "extra": False,
            }}

        def initial(self) -> dict[str, Any]:
            return {}

        def consume(self, state: Any, record: Any) -> None:
            return None

    normalizer = MutableNormalizer()
    provider = presentation_api.PresentationPlugin("external", {
        "format": presentation_api.PRESENTATION_FORMAT,
        "narratives": {"external.raw": {
            "template": "Value: {reading}", "template_zh": "值：{reading}",
            "category": "mixed", "severity": "info",
        }},
    }, normalizers=(normalizer,))
    monkeypatch.setattr(presentation_api, "BUILTINS", {"external": lambda: provider})
    monkeypatch.setattr(presentation_api, "entry_points", lambda **kwargs: ())
    monkeypatch.delenv("AEROAGENTSIM_API_TOKEN", raising=False)
    monkeypatch.delenv("AEROAGENTSIM_CONSOLE_URL", raising=False)
    presentation_api.clear_presentation_plugin_cache()
    application = create_app(tmp_path / "runs")
    starts: list[dict[str, Any]] = []

    def start(scenario: Any, path: Path, **kwargs: Any) -> Path:
        # Exercise real HTTP admission/pinning, but never start a worker.
        snapshot = json.loads((path / "presentation.snapshot.json").read_text())
        presentation_api.validate_presentation_snapshot(snapshot)
        starts.append(snapshot)
        return path

    async def wait_started(path: Path) -> dict[str, Any]:
        return {"id": path.name, "status": "initialized"}

    monkeypatch.setattr(application.state.worker_registry, "start", start)
    monkeypatch.setattr(application.state.worker_registry, "wait_started", wait_started)
    try:
        with TestClient(application, base_url="http://localhost") as client:
            first = client.post("/v1/runs", json={"scenario": document, "autostart": False})
            assert first.status_code == 201, first.text
            directory = tmp_path / "runs" / first.json()["id"]
            available = presentation_api.discover_presentation_plugins() if explicit_available else None
            selected, snapshot = presentation_selection(directory, available)
            digest = presentation_api.presentation_plugin_digest(selected)

            # The provider retains its original objects. Changing all descriptor
            # fields must affect neither a warm identity hit nor the next launch.
            normalizer.output_schemas["external.raw"]["members"].clear()
            normalizer.id = "changed"
            normalizer.version = "2"
            normalizer.input_schemas = ()
            normalizer.fact_fields = ("external.changed",)
            again, same_snapshot = presentation_selection(directory, available)
            assert again is selected and same_snapshot is snapshot
            assert presentation_api.presentation_snapshot(again)["providers"] == starts[0]["providers"]
            assert presentation_api.presentation_plugin_digest(again) == digest
            second = client.post("/v1/runs", json={"scenario": document, "autostart": False})
            assert second.status_code == 201, second.text
            assert len(starts) == 2 and starts[1] == starts[0]
            assert starts[1]["providers"][0]["normalizers"][0]["output_schemas"]["external.raw"]["members"] == {
                "reading": {"type": "string"},
            }
    finally:
        presentation_api.clear_presentation_plugin_cache()


def test_frozen_payload_projection_keeps_refs_and_lossless_json_shapes() -> None:
    from aerokernel.ids import EntityRef

    from aeroagentsim.services.projector import _message_subjects, lossless

    ref = EntityRef("run", "epoch", "sensor", 0, "external:Sensor")
    payload = {"rows": [{"actor": {"$ref": ref.to_data()}}],
               "large": 2**54, "literal": {"$integer": "authored"}}
    frozen = presentation_api.freeze_data(payload)
    assert _message_subjects(frozen) == [ref]
    projected = lossless(frozen)
    assert projected == lossless(payload)
    assert projected["large"] == {"$integer": str(2**54)}
    assert projected["literal"] == {"$record": {"$integer": "authored"}}
    assert json.loads(json.dumps(projected)) == projected
    projected["rows"].clear()
    assert len(frozen["rows"]) == 1
