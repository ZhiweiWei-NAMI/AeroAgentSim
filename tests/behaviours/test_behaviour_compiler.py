"""Authored paths, content pinning and bounded Q6 compilation."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from aeroagentsim.behaviours.compiler import compile_package, resolve_packages
from aeroagentsim.behaviours.schema import CompileError
from aeroagentsim.scenario.loader import UniqueLoader


def package() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        yaml.load(
            Path("scenarios/behaviours/minimal.yaml").read_text(), Loader=UniqueLoader
        )["behaviours"][0],
    )


def test_compiled_dependencies_and_content_identity() -> None:
    p = package()
    ir = compile_package(p, source="queue.yaml")
    assert ir.dependencies["ready"] == (("task", "example.task.phase"),)
    assert ir.package_id == f"{p['id']}@{p['revision']}"
    assert ir.to_data()["document"] == p
    p["id"] = "changed"
    assert ir.document["id"] == "example.queue"


@pytest.mark.parametrize(
    "mutation,path",
    [
        ("temporal", "$.predicates.ready.expression"),
        ("operator", "$.predicates.ready.expression"),
        ("role", "$.predicates.ready.expression"),
        ("budget", "$.budgets.max_instances"),
        ("state", "$.chains.queue.transitions[0]"),
        ("priority", "$.chains.queue.transitions[0].priority"),
        ("action", "$.chains.queue.transitions[0].actions[0]"),
        ("variable", "$.chains.queue.transitions[0].actions[0].value"),
        ("selector", "$.bindings[0].match.task"),
    ],
)
def test_invalid_construct_has_authored_path(mutation: str, path: str) -> None:
    p = package()
    if mutation == "temporal":
        p["predicates"]["ready"]["expression"] = {
            "op": "entered",
            "args": [{"literal": True}],
        }
    elif mutation == "operator":
        p["predicates"]["ready"]["expression"]["op"] = "execute_python"
    elif mutation == "role":
        p["predicates"]["ready"]["expression"]["args"][0]["role"] = "missing"
    elif mutation == "budget":
        p["budgets"]["max_instances"] = False
    elif mutation == "state":
        p["chains"]["queue"]["transitions"][0]["to"] = "missing"
    elif mutation == "priority":
        p["chains"]["queue"]["transitions"][0]["priority"] = True
    elif mutation == "action":
        p["chains"]["queue"]["transitions"][0]["actions"][0]["kind"] = "physical_set"
    elif mutation == "variable":
        p["chains"]["queue"]["transitions"][0]["actions"][0]["value"] = {
            "$variable": "missing"
        }
    else:
        p["bindings"][0]["match"]["task"] = {"relation": "r"}
    with pytest.raises(CompileError) as error:
        compile_package(p, source="queue.yaml")
    assert "queue.yaml:" in str(error.value)
    assert path in str(error.value)


def test_external_package_and_duplicate_yaml(tmp_path: Path) -> None:
    file = tmp_path / "package.yaml"
    file.write_text(yaml.safe_dump(package()))
    resolved = resolve_packages([{"path": file.name}], tmp_path)
    assert resolved[0]["document"] == package()
    assert "digest" not in resolved[0] and "ir_digest" not in resolved[0]
    file.write_text("id: first\nid: second\n")
    with pytest.raises(ValueError, match="duplicate"):
        resolve_packages([{"path": file.name}], tmp_path)


def test_repeat_compile_does_not_mutate_package() -> None:
    p = package()
    before = copy.deepcopy(p)
    assert compile_package(p).to_data() == compile_package(p).to_data()
    assert p == before
