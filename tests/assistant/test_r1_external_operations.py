"""Assistant v1 contributions: discovery, pure compilation and real host staging.

Synthetic entry points stand in for installed distribution metadata. No provider
or engine is constructed by these contract tests.
"""

from __future__ import annotations

import builtins
import copy
import io
import json
import os
import socket
import urllib.request
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import aeroagentsim.assistant_api as api
from aeroagentsim.assistant.catalogue import OperationCatalogue
from aeroagentsim.assistant.compiler import (
    CompileContext as HostContext,
    apply_patches,
    compile_operations,
    compile_plan,
)
from aeroagentsim.assistant.operations import OperationError, obj, validate_operation
from aeroagentsim.assistant.scene import summarize
from aeroagentsim.assistant.tools import operation_catalog, validate_arguments
from aeroagentsim.assistant_api import (
    AssistantOperationPlugin,
    AssistantReadSpec,
    CompileContext,
    CompileResult,
    OperationSpec,
    Prerequisite,
    ResourceProposal,
    thaw,
)
from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.authoring.workspace import WorkspaceStore


class Entry:
    def __init__(self, name, value, loaded: list[str]) -> None:
        self.name, self.value, self.loaded = name, value, loaded

    def load(self):
        self.loaded.append(self.name)
        return self.value


def expected_completion() -> dict:
    return {"id": "external:completed", "text": "If executed, the annotation may be completed",
            "text_zh": "若执行，标注可能完成", "layer": "behaviour",
            "match": {"schema": "external.annotation.completed", "where": {}},
            "approx_s": None, "precheck": None, "depends_on": []}


def external_plugin() -> AssistantOperationPlugin:
    def prepare(context, params):
        return CompileResult(patches=({"op": "add", "path": "/title", "value": "prepared:" + context.document.get("title", "")},))

    def annotate(context, params):
        assert context.operation_id
        assert not hasattr(context, "store") and not hasattr(context, "templates")
        assert not hasattr(context, "provider") and not hasattr(context, "llm")
        prefix = context.resources.get("annotation.prefix", b"")
        operation = {"id": context.operation_id, "op": "external.annotate", "params": thaw(params)}
        return CompileResult(
            patches=({"op": "add", "path": "/title", "value": "external:" + params["text"]},),
            resources=(ResourceProposal("notes/annotation.txt", prefix + params["text"].encode("utf-8")),),
            issues=({"severity": "warning", "field": "text", "message": "Configuration only / 仅配置"},),
            effective_operations=(operation,),
            effects=({"group": "changed", "kind": "configuration", "id": "external:annotation",
                      "label": "Annotation proposal", "label_zh": "标注提案"},),
            references=({"kind": "resource", "id": "notes/annotation.txt", "relation": "configured_input"},),
            expectations=(expected_completion(),),
        )

    def read(context):
        return {"external": {"title": context.document.get("title")}}

    def expectations(document, summary):
        return [expected_completion()] if str(document.get("title", "")).startswith("external:") else []

    prerequisite = OperationSpec(
        id="external.prepare", parameters=obj({}), title="Prepare annotation", title_zh="准备标注",
        description="Prepare annotation metadata", description_zh="准备标注元数据", compile=prepare,
        topic="annotations",
    )
    operation = OperationSpec(
        id="external.annotate", parameters=obj({"text": {"type": "string", "minLength": 1, "maxLength": 80}}, ("text",)),
        title="Annotate the scene", title_zh="标注场景", description="Propose a title and text resource",
        description_zh="提议标题和文本资源", compile=annotate,
        applicable=lambda context, params: context.document.get("run", {}).get("until_ns", 0) > 0,
        prerequisites=(Prerequisite("external.prepare", when=lambda context, params: not str(context.document.get("title", "")).startswith("prepared:"),
                                    automatic_id="external-prerequisite", note="Prepare metadata / 准备元数据"),),
        topic="annotations",
    )
    return AssistantOperationPlugin(
        name="external", operations=(prerequisite, operation),
        read=AssistantReadSpec(summarize=read, expectations=expectations),
        metadata={"topics": {"annotations": ["Annotations", "标注"]},
                  "section_labels": {"external": ["Annotation configuration", "标注配置"]}},
    )


@pytest.fixture
def installed(monkeypatch):
    loaded: list[str] = []
    plugin = external_plugin()

    def entries(*, group):
        assert group == api.ENTRY_POINT_GROUP == "aeroagentsim.assistant_operations"
        return [Entry("external", lambda: plugin, loaded)]

    monkeypatch.setattr(api, "entry_points", entries)
    return plugin, loaded


def test_novel_installed_operation_compiles_through_the_real_host(installed, tmp_path: Path) -> None:
    context = HostContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    operation = {"id": "novel", "op": "external.annotate", "params": {"text": "New capability / 新能力"}}
    result = compile_plan(context, {"template": "food-delivery"}, [operation], tmp_path / "plan")
    assert result.ok, result.errors
    assert result.document["title"] == "external:New capability / 新能力"
    assert (result.files_dir / "notes/annotation.txt").read_bytes() == "New capability / 新能力".encode()
    assert result.operations == [{"id": "external-prerequisite", "op": "external.prepare", "params": {}}, operation]
    assert result.notes == ["Prepare metadata / 准备元数据"]
    assert any(row["id"] == "notes/annotation.txt" for row in result.diff_base["added"])
    assert not any(row["id"] == "notes/annotation.txt" for row in result.diff_base["removed"])
    assert any(row["id"] == "external:annotation" for row in result.diff_base["changed"])
    assert result.expectations.count(expected_completion()) == 1
    assert result.summary["external"]["title"] == result.document["title"]
    assert result.contributions["references"] == [{"kind": "resource", "id": "notes/annotation.txt", "relation": "configured_input"}]
    assert result.contributions["resources"][0]["path"] == "notes/annotation.txt"
    assert result.contributions["resources"][0]["digest"].startswith("sha256:")
    assert any(row["op_id"] == "novel" and row["message"] == "Configuration only / 仅配置" for row in result.warnings)
    assert not list(context.store.root.glob("studio-*"))


def test_external_schema_is_available_to_tools_without_a_host_switch(installed) -> None:
    schema, descriptions = operation_catalog()
    validate_arguments(schema, [{"op": "external.annotate", "params": {"text": "Data"}}])
    assert "external.annotate(text): Propose a title and text resource" in descriptions
    with pytest.raises(ValueError):
        validate_arguments(schema, [{"op": "external.annotate", "params": {"text": 12}}])
    assert OperationCatalogue().resolve("set_duration").id == "lowalt.set_duration"


def test_compile_is_data_only_with_file_and_network_io_disabled(installed, monkeypatch) -> None:
    catalogue = OperationCatalogue()
    document = {"id": "memory", "title": "Before", "run": {"until_ns": 10}}
    before = copy.deepcopy(document)
    context = CompileContext(document, resources={"annotation.prefix": b"prefix:"})
    operation = {"id": "novel", "op": "external.annotate", "params": {"text": "Pure"}}

    def forbidden(*args, **kwargs):
        raise AssertionError("I/O is forbidden inside pure compilation")

    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "open", forbidden)
        blocked.setattr(io, "open", forbidden)
        blocked.setattr(os, "open", forbidden)
        blocked.setattr(socket, "socket", forbidden)
        blocked.setattr(socket, "create_connection", forbidden)
        blocked.setattr(urllib.request, "urlopen", forbidden)
        result = compile_operations(context, [operation], catalogue=catalogue)
        changed = apply_patches(document, result.patches)
    assert document == before and thaw(context.document) == before
    assert changed["title"] == "external:Pure"
    assert result.resources == (ResourceProposal("notes/annotation.txt", b"prefix:Pure"),)
    assert thaw(result.references) == [{"kind": "resource", "id": "notes/annotation.txt", "relation": "configured_input"}]
    assert thaw(result.expectations) == [expected_completion()]
    assert [row["op"] for row in result.effective_operations] == ["external.prepare", "external.annotate"]


@pytest.mark.parametrize("identity", ["lowalt.set_wind", "traffic.set_wind"])
def test_builtin_family_wind_compilers_do_no_io(monkeypatch, identity) -> None:
    catalogue = OperationCatalogue()
    document = {"id": "memory", "run": {"until_ns": 1_000_000_000},
                "engines": {"weather": {"plugin": "environment", "config": {"profiles": {
                    "world": {"lowalt.wind": {"mode": "constant", "value": [2.0, 0.0, 0.0]}},
                }}}}, "entities": [{"id": "world", "facts": {"lowalt.wind": [2.0, 0.0, 0.0]}}]}
    context = CompileContext(document)

    def forbidden(*args, **kwargs):
        raise AssertionError("I/O is forbidden inside a builtin compiler")

    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "open", forbidden)
        blocked.setattr(io, "open", forbidden)
        blocked.setattr(os, "open", forbidden)
        blocked.setattr(socket, "socket", forbidden)
        blocked.setattr(socket, "create_connection", forbidden)
        blocked.setattr(urllib.request, "urlopen", forbidden)
        result = compile_operations(context, [{"id": "wind", "op": identity, "params": {"speed_mps": 5, "from_deg": 270}}], catalogue=catalogue)
        changed = apply_patches(document, result.patches)
    assert changed["entities"][0]["facts"]["lowalt.wind"] == [5.0, 0.0, 0.0]
    assert document["entities"][0]["facts"]["lowalt.wind"] == [2.0, 0.0, 0.0]
    assert not result.resources


def test_prerequisites_are_explicit_visible_and_ordered(installed) -> None:
    catalogue = OperationCatalogue()
    descriptor = catalogue.describe("external.annotate")
    assert descriptor["prerequisites"] == [{"operation": "external.prepare", "params": {}, "conditional": True,
                                              "note": "Prepare metadata / 准备元数据", "warning": None}]
    assert catalogue.display()["external.annotate"]["prerequisites"] == descriptor["prerequisites"]
    assert catalogue.describe("add_place")["prerequisites"][0]["operation"] == "lowalt.add_lowalt_components"
    context = CompileContext({"title": "Before", "run": {"until_ns": 1}})
    operations = [{"id": "novel", "op": "external.annotate", "params": {"text": "Data"}},
                  {"id": "explicit", "op": "external.prepare", "params": {}}]
    result = compile_operations(context, operations, catalogue=catalogue)
    assert [row["id"] for row in result.effective_operations] == ["explicit", "novel"]
    assert operations[0]["id"] == "novel"  # Ordering never mutates the authored recipe.


@pytest.mark.parametrize("params", [{}, {"text": True}, {"text": 3}, {"text": ""}, {"text": " "}, {"text": "x", "extra": 0}, {"text": "x" * 81}, {"text": "x" + " " * 81}])
def test_external_invalid_parameters_fail_before_compilation(installed, params) -> None:
    with pytest.raises(OperationError):
        OperationCatalogue().compile(CompileContext({"run": {"until_ns": 1}}), {"id": "bad", "op": "external.annotate", "params": params})


@pytest.mark.parametrize("name", ["annotate", "unknown.operation", "set_bogus", True, None])
def test_unknown_operations_do_not_acquire_implicit_aliases(installed, name) -> None:
    with pytest.raises(OperationError, match="Unknown operation"):
        validate_operation({"op": name, "params": {}})


def test_inapplicable_operation_fails_closed(installed) -> None:
    with pytest.raises(OperationError, match="not applicable"):
        OperationCatalogue().compile(CompileContext({"run": {"until_ns": 0}}),
                                     {"id": "bad", "op": "external.annotate", "params": {"text": "Data"}})


def test_duplicate_installed_names_fail_before_loading_any_provider(monkeypatch) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(api, "BUILTINS", {"builtin": lambda: loaded.append("builtin")})
    monkeypatch.setattr(api, "entry_points", lambda **kwargs: [Entry("external", external_plugin, loaded), Entry("external", external_plugin, loaded)])
    with pytest.raises(ValueError, match="Duplicate aeroagentsim.assistant_operations"):
        api.discover_assistant_plugins()
    assert loaded == []


def test_installed_provider_replaces_instead_of_merging_a_builtin(monkeypatch) -> None:
    loaded: list[str] = []
    replacement = external_plugin()
    monkeypatch.setattr(api, "BUILTINS", {"external": lambda: loaded.append("builtin")})
    monkeypatch.setattr(api, "entry_points", lambda **kwargs: [Entry("external", replacement, loaded)])
    assert api.discover_assistant_plugins() == (replacement,)
    assert loaded == ["external"]
    assert set(OperationCatalogue().operations) == {"external.prepare", "external.annotate"}


@pytest.mark.parametrize("version", [True, False, 1.0, "1", None, 0, 2])
def test_api_version_requires_the_actual_supported_integer(version) -> None:
    with pytest.raises(ValueError, match="unsupported API version"):
        AssistantOperationPlugin(name="external", api_version=version)


@pytest.mark.parametrize("name", [True, 1, "", "External", "external.ops", "../external"])
def test_family_names_are_strict(name) -> None:
    with pytest.raises(ValueError, match="name must match"):
        AssistantOperationPlugin(name=name)


def test_entry_point_name_type_and_operation_namespace_are_checked(monkeypatch) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(api, "BUILTINS", {})
    monkeypatch.setattr(api, "entry_points", lambda **kwargs: [Entry("different", external_plugin(), loaded)])
    with pytest.raises(ValueError, match="does not match"):
        api.discover_assistant_plugins()
    monkeypatch.setattr(api, "entry_points", lambda **kwargs: [Entry("external", object(), loaded)])
    with pytest.raises(TypeError, match="AssistantOperationPlugin"):
        api.discover_assistant_plugins()
    with pytest.raises(TypeError, match="tuple of OperationSpec"):
        AssistantOperationPlugin(name="external", operations=(object(),))
    with pytest.raises(ValueError, match="outside its namespace"):
        AssistantOperationPlugin(name="different", operations=external_plugin().operations)
    with pytest.raises(ValueError, match="expected <family>.<op>"):
        replace(external_plugin().operations[0], id="unqualified")


def test_conflicting_operation_ids_across_providers_are_rejected() -> None:
    first = external_plugin()
    other = AssistantOperationPlugin(name="other")
    # Defend against an installed factory bypassing normal dataclass admission.
    object.__setattr__(other, "operations", (first.operations[0],))
    with pytest.raises(ValueError, match="Conflicting assistant operation id"):
        OperationCatalogue((first, other), aliases={})


def test_aliases_are_explicit_pinned_and_never_ambiguous() -> None:
    plugin = external_plugin()
    with pytest.raises(ValueError, match="Ambiguous assistant alias"):
        OperationCatalogue((plugin,), aliases=[("label", "external.prepare"), ("label", "external.annotate")])
    with pytest.raises(ValueError, match="unknown target"):
        OperationCatalogue((plugin,), aliases={"label": "missing.operation"})
    catalogue = OperationCatalogue((plugin,), aliases={"label": "external.annotate"})
    assert catalogue.resolve("label").id == "external.annotate"
    with pytest.raises(OperationError):
        catalogue.resolve("annotate")


def test_invalid_prerequisites_and_unsupported_parameter_schemas_fail_closed() -> None:
    first, second = external_plugin().operations
    missing = replace(second, prerequisites=(Prerequisite("external.absent"),))
    with pytest.raises(ValueError, match="unknown prerequisite"):
        OperationCatalogue((AssistantOperationPlugin(name="external", operations=(first, missing)),), aliases={})
    cyclic = replace(first, prerequisites=(Prerequisite("external.annotate"),))
    with pytest.raises(ValueError, match="Circular assistant prerequisites"):
        OperationCatalogue((AssistantOperationPlugin(name="external", operations=(cyclic, second)),), aliases={})
    with pytest.raises(ValueError, match="unsupported JSON Schema keyword"):
        replace(second, parameters={"type": "object", "$ref": "https://example.invalid/schema"})


def test_descriptors_and_contexts_are_frozen_snapshots() -> None:
    parameters = obj({"text": {"type": "string"}}, ("text",))
    operation = replace(external_plugin().operations[1], parameters=parameters)
    parameters["properties"]["text"]["type"] = "number"
    assert operation.parameters["properties"]["text"]["type"] == "string"
    with pytest.raises(TypeError):
        operation.parameters["properties"]["text"]["type"] = "number"
    with pytest.raises(FrozenInstanceError):
        operation.id = "external.changed"
    with pytest.raises(TypeError, match="unsupported value"):
        CompileContext({}, data={"provider": object()})
    with pytest.raises(TypeError, match="agent_profile"):
        CompileContext({}, agent_profile=object())


@pytest.mark.parametrize("path", ["../outside", "/absolute", "a/../../outside", "a\\outside", "a/./b", "C:/outside"])
def test_resource_proposals_cannot_escape_the_plan(path) -> None:
    with pytest.raises(ValueError, match="portable relative path"):
        ResourceProposal(path, b"data")


@pytest.mark.parametrize("path", ["region.plan.json", "draft.json", "draft.meta.json", "versions/1/scene.json", "validation-cache/data.json", "assets/__pycache__/module.pyc"])
def test_resource_proposals_cannot_write_host_owned_records(path) -> None:
    with pytest.raises(ValueError, match="host-owned"):
        ResourceProposal(path, b"not a host record")


def test_expectations_are_bounded_data() -> None:
    with pytest.raises(ValueError, match="at most"):
        CompileResult(expectations=tuple({**expected_completion(), "id": str(index)} for index in range(api.MAX_EXPECTATIONS + 1)))
    with pytest.raises(ValueError, match="unsupported match vocabulary"):
        CompileResult(expectations=({**expected_completion(), "match": {"schema": "external.done", "where": {}, "callback": "execute"}},))


def test_acceptance_does_not_complete_and_empty_expectations_are_unassessed(tmp_path: Path) -> None:
    from aeroagentsim.assistant.story import run_story
    from .test_story import event, journal_run

    directory = journal_run(tmp_path / "recorded", [{"at_ns": 10, "items": [event("external.annotation.accepted", {}, 10)]}])
    story = run_story(directory, [expected_completion()])
    assert story["assessment"] == "incomplete"
    assert story["expectations"][0]["status"] == "missed"
    assert story["expectations"][0]["at_ns"] is None
    assert story["expectations"][0]["evidence"] is None
    assert run_story(directory, [])["assessment"] == "unassessed"


def test_external_read_summary_is_contributed_not_host_dispatched(installed) -> None:
    prepared = TemplateRegistry().apply("traffic-accident")
    summary = summarize(prepared.document, directory=prepared.base)
    assert summary["external"] == {"title": prepared.document.get("title")}
    assert summary["traffic"]["bound_chains"]
    assert any(fleet.get("domain") == "traffic" for fleet in summary["fleets"])


@pytest.mark.parametrize("template,alias,params", [
    ("food-delivery", "set_duration", {"seconds": 180}),
    ("traffic-accident", "set_wind", {"speed_mps": 4, "from_deg": 270}),
    ("parcel-delivery", "add_place", {"id": "extra-pad", "role": "charging_pad", "at": "center", "power_w": 6000}),
])
def test_builtin_alias_and_canonical_id_have_identical_compiled_documents(tmp_path: Path, template, alias, params) -> None:
    from aeroagentsim.assistant.compiler import _rebase

    context = HostContext(WorkspaceStore(tmp_path / "workspaces", None), TemplateRegistry(), None)
    canonical = OperationCatalogue().resolve(alias).id
    old = compile_plan(context, {"template": template}, [{"id": "op-1", "op": alias, "params": params}], tmp_path / "old")
    new = compile_plan(context, {"template": template}, [{"id": "op-1", "op": canonical, "params": params}], tmp_path / "new")
    assert old.ok and new.ok, (old.errors, new.errors)
    stable = tmp_path / "normalized"
    def encode(result):
        return json.dumps(_rebase(result.document, result.files_dir, stable), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    assert encode(old) == encode(new)
    assert old.expectations == new.expectations
