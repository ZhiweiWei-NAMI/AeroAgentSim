"""Count persisted AeroGraph canonical definitions without running its build."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def read_directory(root: Path) -> dict[str, Any]:
    page = (root / "semantic-directory/index.html").read_text()
    marker = '<script id="semantic-data" type="application/json">'
    start = page.index(marker) + len(marker)
    data: dict[str, Any] = json.loads(page[start : page.index("</script>", start)])
    return data


def closure(
    node: dict[str, Any],
    definitions: dict[str, Any],
    active: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if node.get("op") == "transition_event":
        result = dict(node)
        key = "rule:expanded:rule:" + node["contractId"]
        result["args"] = [
            closure(definitions[key]["expression"], definitions, active | {key})
        ]
        return result
    if "rule" in node:
        if node.get("referenceMode") == "inline":
            return closure(node["expression"], definitions, active)
        key = "rule:" + node["rule"]
        if key in active:
            raise ValueError(f"cyclic canonical rule: {key}")
        return closure(definitions[key]["expression"], definitions, active | {key})
    result = dict(node)
    for key, value in node.items():
        if key == "literal":
            continue
        if isinstance(value, dict):
            result[key] = closure(value, definitions, active)
        elif isinstance(value, list):
            result[key] = [
                closure(child, definitions, active)
                if isinstance(child, dict)
                else child
                for child in value
            ]
    return result


def constructs(node: dict[str, Any]) -> Counter[str]:
    found: Counter[str] = Counter()
    if "op" in node:
        found[node["op"]] += 1
    else:
        for leaf in (
            "field",
            "literal",
            "parameter",
            "relation",
            "rule",
            "var",
            "time",
        ):
            if leaf in node:
                found[leaf] += 1
                break
    for key, value in node.items():
        if key == "literal":
            continue
        if isinstance(value, dict):
            found.update(constructs(value))
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    found.update(constructs(child))
    return found


def census(root: Path) -> dict[str, Any]:
    from aeroagentsim.engines.predicate import TEMPORAL, nodes
    from aeroagentsim.engines.predicate_ast import validate_ast

    data = read_directory(root)
    definitions = data["predicateFormat"]["definitions"]
    counts: dict[str, Counter[str]] = {"predicate": Counter(), "event": Counter()}
    original: dict[str, Counter[str]] = {"predicate": Counter(), "event": Counter()}
    totals: Counter[str] = Counter()
    dialects: dict[str, Counter[str]] = {"predicate": Counter(), "event": Counter()}
    supported: Counter[str] = Counter()
    for row in definitions.values():
        kind = row["kind"]
        if kind not in counts:
            continue
        totals[kind] += 1
        dialects[kind][row["execution"]["nativeDialect"]] += 1
        ast = closure(row["expression"], definitions)
        found = constructs(ast)
        if row["applicability"] is not None:
            found.update(constructs(closure(row["applicability"], definitions)))
        counts[kind].update(found.keys())  # Definition incidence, not node count.
        if row["execution"]["nativeDialect"] == "original_compact_ast":
            original[kind].update(found.keys())
        try:
            if row["applicability"] is not None:
                continue
            if (
                ast.get("op") == "transition_event"
                and definitions["rule:expanded:rule:" + ast["contractId"]][
                    "applicability"
                ]
                is not None
            ):
                continue
            admission = (
                {"op": "entered", "args": ast["args"]}
                if ast.get("op") == "transition_event"
                else ast
            )
            validate_ast(admission, row["id"])
            if any(
                node.get("op")
                in TEMPORAL | {"rise", "fall", "changed", "entered", "exited"}
                and any(
                    "literal" not in arg and "parameter" not in arg
                    for arg in node["args"][1:]
                )
                for node in nodes(admission)
            ):
                continue
        except ValueError:
            continue
        supported[kind] += 1
    paths = [
        root / "semantic-directory/index.html",
        root / "semantic-directory/src/original_runtime.js",
        root / "semantic-directory/src/expanded_runtime.js",
        root / "semantic-directory/src/predicate_format.py",
        root / "research/original-graph/decoded.json",
    ]
    return {
        "count_basis": "target incidence in execution rule closure plus applicability; overlapping rows",
        "totals": dict(totals),
        "rules_total": sum(row["kind"] == "rule" for row in definitions.values()),
        "dialects": {k: dict(v) for k, v in dialects.items()},
        "constructs": {
            op: {kind: counts[kind][op] for kind in counts}
            for op in sorted(set(counts["predicate"]) | set(counts["event"]))
        },
        "syntactically_supported": dict(supported),
        "original_constructs": {
            op: {kind: original[kind][op] for kind in original}
            for op in sorted(set(counts["predicate"]) | set(counts["event"]))
        },
        "sha256": {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(json.dumps(census(args.root), indent=2) + "\n")
