"""Event feed wording on real template runs, and allocator-visible assignments."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.platform.simulation import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.narratives import NarrativeProjector
from aeroagentsim.services.projector import project
from aeroagentsim.services.storage import RunStorage

RAW_ID = re.compile(
    r"\b(?:task|drone|customer|vertiport|pad|zone|uav|site)-\d+\b", re.IGNORECASE
)
REPEATED_WORD = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)
HORIZON_S = {"city-day": 40, "traffic-accident": 60}


def run_template(
    tmp_path: Path, template: str, horizon_s: int, knobs: dict[str, Any] | None = None
) -> Path:
    prepared = TemplateRegistry().apply(template, knobs or {})
    prepared.document["run"]["until_ns"] = horizon_s * 1_000_000_000
    path = tmp_path / template
    with RunSession(
        load_scenario(prepared.document, base=prepared.base), path
    ) as session:
        session.run()
    return path


def feed(path: Path) -> list[dict[str, Any]]:
    storage = RunStorage(path)
    projector = NarrativeProjector.from_directory(path)
    events: list[dict[str, Any]] = []
    for raw in storage.records(1, len(storage.entries)):
        events.extend(projector.consume(raw))
    return events


@pytest.mark.parametrize(
    "template",
    [
        "parcel-delivery",
        "medical-delivery",
        "city-day",
        "air-taxi",
        "powerline-inspection",
        "crop-spraying",
        "security-patrol",
        "traffic-accident",
    ],
)
def test_feed_text_is_plain_for_every_template(tmp_path: Path, template: str) -> None:
    events = feed(run_template(tmp_path, template, HORIZON_S.get(template, 90)))
    assert events, template
    for event in events:
        text = event["text"]
        assert text.strip() == text and len(text) > 8, text
        for token in ("{", "}", "[", "]", "$ref", "None", "nan", "occupancy"):
            assert token not in text, text
        assert RAW_ID.search(text) is None, text
        assert REPEATED_WORD.search(text) is None, text


def test_parcel_feed_uses_labels_and_task_kind(tmp_path: Path) -> None:
    texts = [e["text"] for e in feed(run_template(tmp_path, "parcel-delivery", 200))]
    assert any(re.fullmatch(r"New parcel order for Customer .+", t) for t in texts)
    assert any(t.startswith("Drone P") and "picked up the parcel" in t for t in texts)
    assert any(
        re.fullmatch(
            r"Drone P\d+ delivered the parcel to Customer .+ \(\d+(\.\d)? s\)", t
        )
        for t in texts
    )


def assignments(path: Path) -> dict[str, str]:
    storage = RunStorage(path)
    result: dict[str, str] = {}
    for raw in storage.records(1, len(storage.entries)):
        for message in project(raw)["messages"]:
            if message["schemaId"] == "lowalt.task.assigned":
                payload = message["payload"]
                result.setdefault(payload["task"], payload["vehicle"])
    return result


def test_allocators_make_different_assignments_on_a_template_seed(
    tmp_path: Path,
) -> None:
    runs = {}
    for name in ("nearest", "hungarian"):
        sub = tmp_path / name
        sub.mkdir()
        runs[name] = assignments(
            run_template(sub, "parcel-delivery", 150, {"allocator": name})
        )
    common = set(runs["nearest"]) & set(runs["hungarian"])
    assert len(common) >= 5
    assert any(runs["nearest"][t] != runs["hungarian"][t] for t in common)
