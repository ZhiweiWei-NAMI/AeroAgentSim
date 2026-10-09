"""Declared string subjects correlate genuine nonspatial and motion messages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aerokernel import (
    EntityRef,
    Instant,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    TypeDescriptor,
)
from aerokernel.codec import encode
from aerokernel.messages import Message
from aerokernel.operations import Create, Remove
from aerokernel.sdk import ContextEngine, EngineContext
from fastapi.testclient import TestClient

from aeroagentsim.platform import RunSession
from aeroagentsim.platform.plugins import EngineBuild, EngineCatalog
from aeroagentsim.scenario import ScenarioError, load_scenario
from aeroagentsim.scenario.subjects import declarations
from aeroagentsim.services.app import create_app
from aeroagentsim.services.projector import project
from aeroagentsim.services.subjects import SubjectProjection


def registry() -> MemoryRegistry:
    schema = {
        "type": "record",
        "members": {
            "entity": {"type": "string"},
            "generation": {"type": "integer"},
            "note": {"type": "string"},
        },
        "required": ["entity"],
        "extra": False,
    }
    return MemoryRegistry(
        (TypeDescriptor("Job"), TypeDescriptor("Machine")),
        messages=(MessageDescriptor("job.finished", schema=schema),),
    )


def document_for_subjects(**binding: Any) -> dict[str, Any]:
    return {
        "registry": {
            "message_subjects": {
                "job.finished": [{"path": ["entity"], "type_id": "Job", **binding}]
            }
        }
    }


def record(
    index: int, *, ops: tuple[Any, ...] = (), payload: Any = None
) -> dict[str, Any]:
    at = Instant(index)
    items = [{"proposal": encode(op)} for op in ops]
    if payload is not None:
        items.append(
            {
                "message": encode(
                    Message(
                        f"m/{index}",
                        "event",
                        "job.finished",
                        "partition",
                        "jobs",
                        index,
                        at,
                        at,
                        payload,
                        (),
                    )
                )
            }
        )
    return {"index": index, "instant": encode(at), "items": items}


def test_declared_id_resolves_recorded_generation_and_does_not_guess() -> None:
    ref = EntityRef("run", "0", "job", 7, "Job")
    context = SubjectProjection(registry(), document_for_subjects())
    commit = record(1, ops=(Create(ref),), payload={"entity": "job", "note": "job"})
    assert project(commit)["messages"][0]["subjects"] == []
    assert project(commit, subjects=context)["messages"][0]["subjects"] == [
        {"id": "job", "generation": 7}
    ]
    with pytest.raises(ValueError, match="subjects.*missing"):
        project(
            record(2, payload={"entity": "missing", "note": "job"}), subjects=context
        )


def test_subject_type_is_checked_and_not_inferred_from_id() -> None:
    context = SubjectProjection(registry(), document_for_subjects())
    ref = EntityRef("run", "0", "job", 2, "Machine")
    with pytest.raises(ValueError, match="expected entity type Job"):
        project(
            record(1, ops=(Create(ref),), payload={"entity": "job"}), subjects=context
        )


def test_explicit_generation_can_link_a_removed_record_without_using_new_generation() -> (
    None
):
    old = EntityRef("run", "0", "job", 7, "Job")
    new = EntityRef("run", "0", "job", 8, "Job")
    context = SubjectProjection(
        registry(), document_for_subjects(generation_path=["generation"])
    )
    context.observe(record(1, ops=(Create(old),)))
    context.observe(record(2, ops=(Remove(old),)))
    context.observe(record(3, ops=(Create(new),)))
    message = project(
        record(4, payload={"entity": "job", "generation": 7}), subjects=context
    )["messages"][0]
    assert message["subjects"] == [{"id": "job", "generation": 7}]
    with pytest.raises(ValueError, match="subjects"):
        project(record(5, payload={"entity": "job"}), subjects=context)


@pytest.mark.parametrize(
    "binding",
    [
        {"path": ["missing"], "type_id": "Job"},
        {"path": ["generation"], "type_id": "Job"},
        {"path": ["entity"], "type_id": "MissingType"},
        {"path": ["entity"], "type_id": "Job", "generation_path": ["note"]},
    ],
)
def test_invalid_declarations_identify_descriptor(binding: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="registry.messages.job.finished.subjects"):
        declarations(
            registry(), {"registry": {"message_subjects": {"job.finished": [binding]}}}
        )


def test_nested_array_paths_and_optional_subjects() -> None:
    schema = {
        "type": "record",
        "members": {
            "jobs": {"type": "array", "items": {"type": "string"}},
            "optional": {"type": "string", "nullable": True},
        },
        "required": ["jobs"],
        "extra": False,
    }
    reg = MemoryRegistry(
        (TypeDescriptor("Job"),),
        messages=(MessageDescriptor("job.finished", schema=schema),),
    )
    doc = {
        "registry": {
            "message_subjects": {
                "job.finished": [
                    {"path": ["jobs", "*"], "type_id": "Job"},
                    {"path": ["optional"], "type_id": "Job"},
                ]
            }
        }
    }
    context = SubjectProjection(reg, doc)
    ref = EntityRef("run", "0", "job", 7, "Job")
    projected = project(
        record(
            1, ops=(Create(ref),), payload={"jobs": ["job", "job"], "optional": None}
        ),
        subjects=context,
    )
    assert projected["messages"][0]["subjects"] == [{"id": "job", "generation": 7}]


def test_embedded_refs_remain_supported_and_deduplicate_declared_subjects() -> None:
    schema = {
        "type": "record",
        "members": {"entity": {"type": "ref", "target_type": "Job"}},
        "required": ["entity"],
        "extra": False,
    }
    reg = MemoryRegistry(
        (TypeDescriptor("Job"),),
        messages=(MessageDescriptor("job.finished", schema=schema),),
    )
    ref = EntityRef("run", "0", "job", 2**53 + 1, "Job")
    commit = record(1, ops=(Create(ref),), payload={"entity": {"$ref": ref.to_data()}})
    expected = [{"id": "job", "generation": str(2**53 + 1)}]
    assert project(commit)["messages"][0]["subjects"] == expected
    context = SubjectProjection(reg, document_for_subjects())
    assert project(commit, subjects=context)["messages"][0]["subjects"] == expected


def test_missing_declared_array_index_has_message_path_and_original_cause() -> None:
    schema = {
        "type": "record",
        "members": {"jobs": {"type": "array", "items": {"type": "string"}}},
        "required": ["jobs"],
        "extra": False,
    }
    reg = MemoryRegistry(
        (TypeDescriptor("Job"),),
        messages=(MessageDescriptor("job.finished", schema=schema),),
    )
    context = SubjectProjection(reg, document_for_subjects(path=["jobs", 0]))
    with pytest.raises(
        ValueError, match=r"messages.job.finished.subjects.*jobs.*0"
    ) as caught:
        project(record(1, payload={"jobs": []}), subjects=context)
    assert isinstance(caught.value.__cause__, IndexError)


class CompletionEngine(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        super().__init__(
            Partition(
                build.id,
                build.id,
                lifecycle=True,
                emits=("q3.job.finished",),
                message_targets=("jobs",),
            )
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        ref = self.build.entities[0]
        ctx.create(ref)
        ctx.emit("q3.job.finished", {"entity": ref.id}, topic="jobs")

    def on_inputs(self, ctx: EngineContext) -> None:
        pass


def test_real_nonspatial_completion_in_rest_pages_and_sse_reconnect(
    document: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document["registry"]["types"].append(
        {"id": "q3:Job", "parents": [], "abstract": False}
    )
    document["registry"]["messages"].append(
        {
            "id": "q3.job.finished",
            "kind": "event",
            "schema": {
                "type": "record",
                "members": {"entity": {"type": "string"}},
                "required": ["entity"],
                "extra": False,
            },
            "subjects": [{"path": ["entity"], "type_id": "q3:Job"}],
        }
    )
    document["entities"] = [{"id": "job-7", "type": "q3:Job", "facts": {}}]
    document["bindings"] = {"lifecycle": [{"controller": "jobs", "type": "q3:Job"}]}
    document["engines"] = {"jobs": {"plugin": "q3_completion", "config": {}}}
    document["presentation"] = []
    document["run"].update(until_ns=1, advance_ns=1)
    original = EngineCatalog.build

    def build(self: EngineCatalog, plugin: str, context: EngineBuild) -> Any:
        return (
            CompletionEngine(context)
            if plugin == "q3_completion"
            else original(self, plugin, context)
        )

    monkeypatch.setattr(EngineCatalog, "build", build)
    with RunSession(load_scenario(document), tmp_path / "runs" / "job") as session:
        session.run()
    app = create_app(tmp_path / "runs")
    with TestClient(app) as client:
        head = client.get("/v1/runs/job/header").json()
        assert head["presentation"] == []
        assert head["messageSubjects"]["q3.job.finished"] == [
            {"path": ["entity"], "type_id": "q3:Job"}
        ]
        all_commits = client.get("/v1/runs/job/commits").json()["commits"]
        commit = next(c for c in all_commits if c["messages"])
        message = commit["messages"][0]
        assert message["schemaId"] == "q3.job.finished"
        assert message["subjects"] == [{"id": "job-7", "generation": 0}]
        index = commit["commitIndex"]
        page = client.get(f"/v1/runs/job/commits?from={index}&limit=1").json()[
            "commits"
        ][0]
        assert page == commit
        stream = client.get(
            "/v1/runs/job/stream", headers={"Last-Event-ID": str(index - 1)}
        )
        assert '"subjects":[{"id":"job-7","generation":0}]' in stream.text


def test_real_command_and_arrival_declared_subjects(
    document: dict[str, Any], tmp_path: Path
) -> None:
    document["registry"]["message_subjects"] = {
        "aas.motion.move_to": [
            {"path": ["entity"], "type_id": "oo:UAV"},
            {"path": ["machine"], "type_id": "oo:Order"},
        ],
        "aas.motion.arrived": [
            {"path": ["entity"], "type_id": "oo:UAV"},
            {"path": ["machine"], "type_id": "oo:Order"},
        ],
    }
    scenario = load_scenario(document)
    context = SubjectProjection(scenario.registry, scenario.document)
    with RunSession(scenario, tmp_path / "motion") as session:
        session.run()
        commits = [
            project(r, subjects=context) for r in session.simulation.kernel.records[1:]
        ]
    messages = [m for c in commits for m in c["messages"]]
    for schema in ("aas.motion.move_to", "aas.motion.arrived"):
        selected = [m for m in messages if m["schemaId"] == schema]
        assert selected
        assert all(
            m["subjects"]
            == [
                {"id": m["payload"]["entity"], "generation": 0},
                {"id": m["payload"]["machine"], "generation": 0},
            ]
            for m in selected
        )


def test_bad_subject_declaration_is_a_scenario_error(document: dict[str, Any]) -> None:
    document["registry"]["message_subjects"] = {
        "aas.motion.arrived": [{"path": ["position"], "type_id": "oo:UAV"}]
    }
    with pytest.raises(
        ScenarioError, match="registry.messages.aas.motion.arrived.subjects"
    ):
        load_scenario(document)
