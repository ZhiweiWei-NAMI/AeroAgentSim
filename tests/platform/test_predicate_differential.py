"""Hypothesis histories compared with copied, hash-pinned native JS runtimes."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aeroagentsim.engines.predicate import NATIVE_SHA256, digest, prepare
from aeroagentsim.engines.predicate_ast import evaluate, validate_ast
from aeroagentsim.scenario.paths import source_path


@pytest.fixture(scope="module")
def oracle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("native-predicates")
    root = source_path("${AEROAGENTSIM_AEROGRAPH_ROOT}")
    for name in ("original_runtime.js", "expanded_runtime.js"):
        source = root / "semantic-directory/src" / name
        copied = directory / name
        shutil.copyfile(source, copied)
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == NATIVE_SHA256[name]
        assert (
            hashlib.sha256(copied.read_bytes()).digest()
            == hashlib.sha256(source.read_bytes()).digest()
        )
    shutil.copyfile(
        Path("tests/platform/fixtures/predicate_oracle.cjs"), directory / "oracle.cjs"
    )
    return directory / "oracle.cjs"


def compare(oracle: Path, commands: list[dict[str, Any]]) -> None:
    result = subprocess.run(
        ["node", str(oracle)],
        input=json.dumps(commands),
        text=True,
        capture_output=True,
        check=True,
    )
    rows = json.loads(result.stdout)
    assert len(rows) == len(commands)
    for command, row in zip(commands, rows, strict=True):
        validate_ast(command["ast"], "differential")
        actual = evaluate(command["ast"], command["history"], command["dialect"])
        assert row["status"] not in {"execution_error", "invalid_input"}, (command, row)
        assert actual == row["value"], (command, actual, row)


def literal(value: Any) -> dict[str, Any]:
    return {"literal": value}


def op(name: str, *values: Any) -> dict[str, Any]:
    return {"op": name, "args": [literal(v) for v in values]}


@given(st.integers(-20, 20), st.integers(1, 20), st.booleans(), st.booleans())
@settings(max_examples=30, deadline=None)
def test_scalar_constructs_against_native(
    oracle: Path, x: int, y: int, a: bool, b: bool
) -> None:
    original = [
        op(name, x, y)
        for name in (
            "eq",
            "ne",
            "lt",
            "lte",
            "gt",
            "gte",
            "add",
            "sub",
            "mul",
            "div",
            "min",
            "max",
        )
    ]
    original += [
        op("abs", x),
        op("sqrt", abs(x)),
        op("pow", abs(x), 2),
        op("clamp", x, -5, 5),
        op("between", x, -5, 5),
    ]
    original += [
        op("and", a, b),
        op("or", a, b),
        op("not", a),
        op("if", a, x, y),
        op("is_unknown", None),
    ]
    original += [
        op("in", x, [x, y]),
        op("subset", [x], [y, x]),
        op("disjoint", [x], [y]),
        op("set_equal", [x, y], [y, x]),
        op("unique_count", [x, y, x]),
        op("len", [x, y]),
        op("sum", [x, y]),
        op("norm", [x, y]),
        op("dot", [x, y], [y, x]),
        op("distance", [x, y], [y, x]),
    ]
    original += [
        op("and", False, None),
        op("or", True, None),
        op("not", None),
        op("eq", True, 1),
        op("eq", 1, 1.0),
    ]
    expanded = [
        op("implies", a, b),
        op("contains", [x, y], x),
        op("count", [x, y]),
        op("vector_norm", [x, y]),
        op("cross", [x, y, 1], [y, x, 2]),
        op("interval_overlap", [0, y], [y, y + 1]),
        op("interval_contains", [0, y], y),
        op("date_time", "2026-10-08T00:00:00Z"),
    ]
    ref = {"id": "e", "refType": "T", "runId": "r", "epoch": "0", "generation": 0}
    expanded.append(op("same_identity", ref, dict(ref)))
    for name in ("all", "any"):
        expanded.append(
            {
                "op": name,
                "array": literal([x, y]),
                "var": "item",
                "predicate": {
                    "op": "gt",
                    "args": [{"var": "item", "path": []}, literal(0)],
                },
            }
        )
    commands = [
        {
            "dialect": dialect,
            "ast": ast,
            "history": [{"t": 0, "fields": {}, "relations": {}, "parameters": {}}],
        }
        for dialect, asts in (("original", original), ("expanded", expanded))
        for ast in asts
    ]
    compare(oracle, commands)


@given(
    st.lists(
        st.tuples(
            st.integers(1, 6), st.booleans(), st.integers(-10, 10), st.integers(0, 1)
        ),
        min_size=2,
        max_size=15,
    ),
    st.integers(0, 10),
    st.one_of(st.none(), st.integers(0, 10)),
)
@settings(max_examples=40, deadline=None)
def test_temporal_constructs_against_native(
    oracle: Path,
    samples: list[tuple[int, bool, int, int]],
    window: int,
    gap: int | None,
) -> None:
    history: list[dict[str, Any]] = []
    time = 0
    for advance, boolean, number, scope in samples:
        time += advance
        history.append(
            {
                "t": time,
                "fields": {"subject": {"b": boolean, "n": number, "scope": scope}},
                "parameters": {"window": window},
                "relations": {},
            }
        )
    commands = []
    for name in (
        "hold",
        "all_window",
        "any_window",
        "count_window",
        "delta",
        "rate",
        "rise",
        "fall",
        "changed",
        "stable_window",
        "entered",
        "exited",
    ):
        field = "n" if name in {"delta", "rate", "changed", "stable_window"} else "b"
        ast = {
            "op": name,
            "args": [
                {"field": field, "role": "subject", "path": []},
                {"parameter": "window"},
                literal(gap),
            ],
            "asScope": {
                "op": "sequence",
                "args": [{"field": "scope", "role": "subject", "path": []}],
            },
        }
        commands.append({"dialect": "original", "ast": ast, "history": history})
    compare(oracle, commands)


def test_relation_role_direction_completeness_against_native(oracle: Path) -> None:
    ast = {"relation": "R", "sourceRole": "a", "targetRole": "b"}
    commands = [
        {
            "dialect": "expanded",
            "ast": ast,
            "history": [
                {"t": 10, "fields": {}, "relations": relations, "parameters": {}}
            ],
        }
        for relations in ({"R|a|b": True}, {"R|a|b": False}, {}, {"R|b|a": True})
    ]
    compare(oracle, commands)


def test_paths_time_and_guarded_quantification_against_native(oracle: Path) -> None:
    history = [
        {
            "t": 123,
            "fields": {"subject": {"record": {"items": [7]}}},
            "parameters": {"limit": 7},
            "relations": {},
        }
    ]
    path = {"field": "record", "role": "subject", "path": ["items", 0]}
    item = {"var": "item", "path": ["value"]}
    expressions = [
        {"op": "eq", "args": [path, {"parameter": "limit"}]},
        {"op": "eq", "args": [{"time": True}, literal(123)]},
        op(
            "interval_contains",
            ["2026-10-08T00:00:00Z", "2026-10-09T00:00:00Z"],
            ["2026-10-08T01:00:00Z", "2026-10-08T02:00:00Z"],
        ),
        op("interval_overlap", [None, 2], [0, 3]),
        {"op": "implies", "args": [literal(False), op("gt", "bad", 1)]},
    ]
    for name in ("all", "any"):
        expressions.append(
            {
                "op": name,
                "array": literal([{"value": -1}, {"value": 7}]),
                "var": "item",
                "predicate": {"op": "eq", "args": [item, literal(7)]},
                "applicabilityExpression": {"op": "gt", "args": [item, literal(0)]},
            }
        )
    compare(
        oracle,
        [
            {"dialect": "expanded", "ast": ast, "history": history}
            for ast in expressions
        ],
    )


def test_exact_ns_above_javascript_safe_integer() -> None:
    # The native Number clock cannot represent these integer-ns boundaries.
    # This extension uses the documented anchor rule with Python integer time.
    boundary = 10**18
    ast = {"op": "hold", "args": [literal(True), literal(3)]}
    assert evaluate(ast, [{"t": boundary}, {"t": boundary + 2}], "original") is None
    assert evaluate(ast, [{"t": boundary}, {"t": boundary + 3}], "original") is True


def test_real_aerograph_golden_predicates(oracle: Path) -> None:
    fixtures = json.loads(
        Path("tests/platform/fixtures/predicates-golden.json").read_text()
    )["fixtures"]
    assert len({row["target"] for row in fixtures}) >= 20
    assert len({domain for row in fixtures for domain in row["domains"]}) >= 4
    assert any(row["ast"].get("op") == "hold" for row in fixtures)
    assert any("relation" in json.dumps(row["ast"]) for row in fixtures)
    compare(oracle, fixtures)
    for row in fixtures:
        assert (
            evaluate(row["ast"], row["history"], row["dialect"]) is row["expected"]
        ), row["target"]


def test_expanded_event_ast_against_native_transition(oracle: Path) -> None:
    condition = {
        "op": "gt",
        "args": [{"field": "value", "role": "subject", "path": []}, literal(0)],
    }
    definitions = {
        "event:event": {
            "schemaVersion": "aerograph.predicate-definition/v1",
            "kind": "event",
            "execution": {"nativeDialect": "expanded_typed_ast"},
            "inputs": {"rules": ["expanded:rule:contract"]},
            "expression": {
                "op": "transition_event",
                "args": [],
                "contractId": "contract",
                "transition": "entered",
                "requiresSameRoleInstances": True,
                "requiresSameClock": True,
                "requiresPreviousAndCurrentFrames": True,
            },
        },
        "rule:expanded:rule:contract": {"expression": condition},
    }
    config = {
        "version": "aerograph-predicate/1",
        "target": "event:event",
        "definitions": definitions,
        "definitions_sha256": digest(definitions),
        "parameters": {},
        "context": "test",
        "event": "event",
        "topic": "events",
        "transition": "level",
        "temporal_unit": "ns",
        "native_references": [
            {
                "path": str(oracle.parent / "expanded_runtime.js"),
                "sha256": NATIVE_SHA256["expanded_runtime.js"],
            }
        ],
    }
    ast = prepare(config)
    frames = [
        {
            "t": t,
            "fields": {"subject": {"value": value}},
            "parameters": {},
            "relations": {},
        }
        for t, value in enumerate([-1, 1, 1, -1, 1])
    ]
    commands = [
        {
            "dialect": "expanded",
            "transition": "entered",
            "ast": condition,
            "history": frames[: i + 1],
        }
        for i in range(len(frames))
    ]
    result = subprocess.run(
        ["node", str(oracle)],
        input=json.dumps(commands),
        text=True,
        capture_output=True,
        check=True,
    )
    rows = json.loads(result.stdout)
    assert (
        [evaluate(ast, frames[: i + 1], "expanded") for i in range(len(frames))]
        == [row["value"] for row in rows]
        == [None, True, False, False, True]
    )
