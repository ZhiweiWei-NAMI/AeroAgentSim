"""Bounded canonical AeroGraph operators; native clocks become exact integer ns.

None is an undetermined result, never a substituted observation. The original
and expanded dialects deliberately retain different unknown propagation rules.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from itertools import pairwise
from typing import Any, cast

TEMPORAL = frozenset(
    {
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
    }
)
ARITY = {
    "not": 1,
    "if": 3,
    "implies": 2,
    "is_unknown": 1,
    "eq": 2,
    "ne": 2,
    "lt": 2,
    "lte": 2,
    "gt": 2,
    "gte": 2,
    "sub": 2,
    "div": 2,
    "abs": 1,
    "sqrt": 1,
    "pow": 2,
    "clamp": 3,
    "between": 3,
    "in": 2,
    "contains": 2,
    "subset": 2,
    "disjoint": 2,
    "set_equal": 2,
    "unique_count": 1,
    "len": 1,
    "count": 1,
    "sum": 1,
    "vector_norm": 1,
    "dot": 2,
    "cross": 2,
    "distance": 2,
    "date_time": 1,
    "interval_overlap": 2,
    "interval_contains": 2,
}
VARIADIC = frozenset({"and", "or", "add", "mul", "min", "max", "norm", "same_identity"})
SUPPORTED = frozenset(ARITY) | VARIADIC | TEMPORAL | {"all", "any", "sequence"}


def validate_ast(ast: dict[str, Any], predicate_id: str) -> None:
    """Validate a finite, enumerated dialect without executing arbitrary code."""
    count = 0

    def visit(
        n: Any,
        depth: int,
        variables: frozenset[str] = frozenset(),
        *,
        scope_sequence: bool = False,
    ) -> None:
        nonlocal count
        count += 1

        def fail(construct: str) -> None:
            raise ValueError(
                f"predicate {predicate_id}: construct {construct} unsupported or malformed"
            )

        if depth > 128 or count > 100_000:
            fail("AST_budget")
        if not isinstance(n, dict):
            fail("node")
        if "op" not in n:
            leaves = {
                "literal": {"literal"},
                "parameter": {
                    "parameter",
                    "sourceName",
                    "sourceDefaultPresent",
                    "sourceDefault",
                },
                "field": {"field", "role", "path"},
                "relation": {"relation", "sourceRole", "targetRole"},
                "var": {"var", "path"},
                "time": {"time"},
            }
            matches = [k for k in leaves if k in n]
            if len(matches) != 1:
                fail("leaf")
            kind = matches[0]
            if set(n) - leaves[kind]:
                fail(kind)
            if kind in {"field", "var"}:
                path = n.get("path", [])
                if not isinstance(path, list) or any(
                    type(p) not in (str, int) or type(p) is int and p < 0 for p in path
                ):
                    fail("path")
            if kind == "var" and n["var"] not in variables:
                fail("unbound_variable")
            if kind == "time" and n["time"] is not True:
                fail("time")
            return
        op = n["op"]
        if op == "sequence" and not scope_sequence:
            fail("sequence_outside_scope")
        if op not in SUPPORTED:
            fail(str(op))
        if op in {"all", "any"}:
            if set(n) - {
                "op",
                "array",
                "var",
                "predicate",
                "applicabilityExpression",
            } or not isinstance(n.get("var"), str):
                fail(op)
            visit(n["array"], depth + 1, variables)
            bound = variables | {n["var"]}
            visit(n["predicate"], depth + 1, bound)
            if "applicabilityExpression" in n:
                visit(n["applicabilityExpression"], depth + 1, bound)
            return
        if (
            set(n) - {"op", "args", "nativeOperator", "asScope"}
            or "asScope" in n
            and op not in TEMPORAL
        ):
            fail(op)
        args = n.get("args")
        if (
            not isinstance(args, list)
            or op in ARITY
            and len(args) != ARITY[op]
            or op in VARIADIC
            and not args
        ):
            fail(op)
        if op in TEMPORAL and len(args) not in (
            {1, 2, 3} if op in {"entered", "exited"} else {2, 3}
        ):
            fail(op)
        for child in args:
            visit(child, depth + 1, variables)
        if "asScope" in n:
            scope = n["asScope"]
            if not isinstance(scope, dict) or scope.get("op") != "sequence":
                fail("asScope")
            visit(scope, depth + 1, variables, scope_sequence=True)

    visit(ast, 0)


def equal(a: Any, b: Any) -> bool:
    """JSON equality with JS number identity and Boolean/number separation."""
    if type(a) in (int, float) and type(b) in (int, float):
        return bool(a == b)
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return bool(a == b)


def number(v: Any) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def project(v: Any, path: list[int | str]) -> Any:
    for p in path:
        if (
            isinstance(v, dict)
            and isinstance(p, str)
            and p in v
            or isinstance(v, list)
            and type(p) is int
            and 0 <= p < len(v)
        ):
            v = v[cast(int, p)]
        else:
            return None
    return v


def native_hypot(values: list[Any]) -> float:
    """V8 Math.hypot uses a scaled compensated sum, then sqrt * maximum."""
    if not all(number(value) for value in values):
        raise ArithmeticError("hypot: non-finite operand")
    maximum = max((abs(x) for x in values), default=0)
    if maximum == 0:
        return 0.0
    total = 0.0
    compensation = 0.0
    for value in values:
        normalized = value / maximum
        summand = normalized * normalized - compensation
        preliminary = total + summand
        compensation = (preliminary - total) - summand
        total = preliminary
    result = math.sqrt(total) * maximum
    if not math.isfinite(result):
        raise ArithmeticError("hypot: non-finite result")
    return result


def scalar(op: str, v: list[Any], dialect: str) -> Any:
    if op == "is_unknown":
        return v[0] is None
    if op == "implies":
        if v[0] is False:
            return True
        return v[1] if v[0] is True and type(v[1]) is bool else None
    if op in {"and", "or", "not"}:
        if any(x is not None and type(x) is not bool for x in v):
            raise TypeError(f"{op}: Boolean operands required")
        if dialect == "original":
            if op == "and":
                return (
                    False if False in v else True if all(x is True for x in v) else None
                )
            if op == "or":
                return (
                    True if True in v else False if all(x is False for x in v) else None
                )
        if any(x is None for x in v):
            return None
        return all(v) if op == "and" else any(v) if op == "or" else not v[0]
    if op == "if":
        if v[0] is not None and type(v[0]) is not bool:
            raise TypeError("if: Boolean condition required")
        if v[0] is True:
            return v[1]
        if v[0] is False:
            return v[2]
        return v[1] if equal(v[1], v[2]) else None
    if any(x is None for x in v):
        return None
    if op in {"eq", "ne"}:
        if dialect == "original" and (type(v[0]) is bool) != (type(v[1]) is bool):
            return None
        same = equal(v[0], v[1])
        return same if op == "eq" else not same
    if op in {"in", "contains"}:
        item, container = v if op == "in" else [v[1], v[0]]
        if isinstance(container, list):
            return any(equal(item, x) for x in container)
        if isinstance(container, str) and isinstance(item, str):
            return item in container
        if op == "in" and isinstance(container, dict):
            return str(item) in container
        raise TypeError(f"{op}: collection operands required")
    if op in {"subset", "disjoint", "set_equal"}:
        if not all(isinstance(x, list) for x in v):
            raise TypeError(f"{op}: arrays required")
        if dialect == "original" and any(
            isinstance(x, (dict, list)) for a in v for x in a
        ):
            return None
        subset = all(any(equal(x, y) for y in v[1]) for x in v[0])
        return (
            not any(equal(x, y) for x in v[0] for y in v[1])
            if op == "disjoint"
            else subset and all(any(equal(x, y) for x in v[0]) for y in v[1])
            if op == "set_equal"
            else subset
        )
    if op in {"count", "len", "unique_count"}:
        if op == "unique_count":
            if not isinstance(v[0], list) or any(
                isinstance(x, (dict, list)) for x in v[0]
            ):
                return None
            return sum(
                not any(equal(x, y) for y in v[0][:i]) for i, x in enumerate(v[0])
            )
        if not isinstance(v[0], list) and (
            op == "count" or not isinstance(v[0], (str, dict))
        ):
            raise TypeError(f"{op}: invalid collection")
        return len(v[0])
    if op == "same_identity":

        def identity(x: Any) -> dict[str, Any]:
            if not isinstance(x, dict):
                raise TypeError("same_identity: full versioned reference required")
            x = dict(x)
            for key, alias in (("id", "instanceId"), ("refType", "entityTypeId")):
                if alias in x:
                    if key in x and not equal(x[key], x[alias]):
                        raise ValueError("same_identity: conflicting aliases")
                    x[key] = x[alias]
            keys = ("id", "refType", "runId", "epoch", "generation")
            if any(k not in x or x[k] is None for k in keys):
                raise ValueError("same_identity: incomplete reference")
            for key in keys:
                member = x[key]
                if isinstance(member, str) and member:
                    continue
                if (
                    key in {"epoch", "generation"}
                    and type(member) is int
                    and abs(member) <= 2**53 - 1
                ):
                    continue
                raise ValueError(f"same_identity: invalid reference member {key}")
            return {k: x[k] for k in keys}

        refs = [identity(x) for x in v]
        return all(equal(refs[0], x) for x in refs[1:])
    if op == "date_time":
        if not isinstance(v[0], str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", v[0]
        ):
            raise ValueError("date_time: explicit RFC3339 timezone required")
        return (
            math.floor(
                datetime.fromisoformat(v[0].replace("Z", "+00:00")).timestamp() * 1000
            )
            / 1000
        )
    if op in {"interval_overlap", "interval_contains"}:

        def interval(x: Any) -> list[Any] | None:
            if not isinstance(x, list) or len(x) != 2:
                raise ValueError(f"{op}: ordered finite interval required")
            if any(y is None for y in x):
                return None
            if all(isinstance(y, str) for y in x):
                x = [scalar("date_time", [y], "expanded") for y in x]
            if not all(number(y) for y in x) or x[1] < x[0]:
                raise ValueError(f"{op}: ordered finite interval required")
            return cast(list[Any], x)

        a = interval(v[0])
        if a is None:
            return None
        if op == "interval_contains" and number(v[1]):
            return a[0] <= v[1] < a[1]
        b = interval(v[1])
        if b is None:
            return None
        return (
            a[0] < a[1] and b[0] < b[1] and max(a[0], b[0]) < min(a[1], b[1])
            if op == "interval_overlap"
            else a[0] <= b[0] and b[1] <= a[1]
        )
    if op in {"sum", "norm", "vector_norm", "distance", "dot", "cross"}:

        def vector(x: Any) -> list[Any]:
            if not isinstance(x, list) or not all(number(y) for y in x):
                raise TypeError(f"{op}: finite numeric vector required")
            return x

        if op == "sum":
            total = sum(vector(v[0]))
            return total if number(total) else None
        if op == "norm":
            a = vector(v[0] if len(v) == 1 and isinstance(v[0], list) else v)
            norm = math.sqrt(sum(x * x for x in a))
            if not number(norm):
                raise ArithmeticError("norm: non-finite result")
            return norm
        if op == "vector_norm":

            def flat(x: Any) -> list[Any]:
                if not isinstance(x, list) or not x:
                    raise TypeError("vector_norm: nonempty numeric tensor required")
                result = []
                for y in x:
                    result.extend(flat(y) if isinstance(y, list) else vector([y]))
                return result

            return native_hypot(flat(v[0]))
        a, b = vector(v[0]), vector(v[1])
        if len(a) != len(b):
            raise ValueError(f"{op}: vector dimensions differ")
        if op == "cross":
            if len(a) != 3:
                raise ValueError("cross: three dimensions required")
            cross = [
                a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0],
            ]
            if not all(number(value) for value in cross):
                raise ArithmeticError("cross: non-finite result")
            return cross
        if op == "dot":
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            if not number(dot):
                raise ArithmeticError("dot: non-finite result")
            return dot
        return native_hypot([x - y for x, y in zip(a, b, strict=True)])
    if not all(number(x) for x in v):
        raise TypeError(f"{op}: finite numeric operands required")
    a = v[0]
    if op == "gt":
        return a > v[1]
    if op == "gte":
        return a >= v[1]
    if op == "lt":
        return a < v[1]
    if op == "lte":
        return a <= v[1]
    if op == "between":
        return v[1] <= a <= v[2]
    result: Any
    if op == "add":
        result = sum(v)
    elif op == "sub":
        result = a - v[1]
    elif op == "mul":
        result = math.prod(v)
    elif op == "div":
        result = a / v[1] if v[1] != 0 else None
    elif op == "abs":
        result = abs(a)
    elif op == "sqrt":
        result = math.sqrt(a) if a >= 0 else None
    elif op == "pow":
        try:
            result = math.pow(a, v[1])
        except (ValueError, OverflowError):
            result = None
    elif op == "min":
        result = min(v)
    elif op == "max":
        result = max(v)
    elif op == "clamp":
        result = max(v[1], min(v[2], a))
    else:
        raise ValueError(f"unsupported scalar {op}")
    return result if result is None or number(result) else None


def evaluate(ast: dict[str, Any], history: list[dict[str, Any]], dialect: str) -> Any:
    """Evaluate retained settled samples. No interpolation or source defaults."""
    if dialect not in {"original", "expanded"}:
        raise ValueError(f"unsupported native dialect {dialect}")
    if not history:
        return None
    if any(type(f["t"]) is not int for f in history) or any(
        a["t"] >= b["t"] for a, b in pairwise(history)
    ):
        raise ValueError("history: strictly increasing exact integer ns required")

    def node(n: dict[str, Any], i: int, variables: dict[str, Any]) -> Any:
        frame = history[i]
        if "literal" in n:
            return n["literal"]
        if "field" in n:
            return project(
                frame.get("fields", {}).get(n["role"], {}).get(n["field"]),
                n.get("path", []),
            )
        if "parameter" in n:
            return history[-1].get("parameters", {}).get(n["parameter"])
        if "relation" in n:
            return frame.get("relations", {}).get(
                n["relation"] + "|" + n["sourceRole"] + "|" + n["targetRole"]
            )
        if "time" in n:
            return frame["t"]
        if "var" in n and "op" not in n:
            return project(variables.get(n["var"]), n.get("path", []))
        op = n["op"]
        if op in {"all", "any"}:
            array = node(n["array"], i, variables)
            if array is None:
                return None
            if not isinstance(array, list):
                raise TypeError(f"{op}: array required")
            results: list[Any] = []
            applicable = 0
            for item in array:
                bound = {**variables, n["var"]: item}
                guard = (
                    node(n["applicabilityExpression"], i, bound)
                    if "applicabilityExpression" in n
                    else True
                )
                if guard is None:
                    results.append(None)
                    continue
                if type(guard) is not bool:
                    raise TypeError("quantifier: Boolean applicability required")
                if not guard:
                    results.append(op == "all")
                    continue
                applicable += 1
                value = node(n["predicate"], i, bound)
                if value is not None and type(value) is not bool:
                    raise TypeError("quantifier: Boolean predicate required")
                results.append(value)
            if any(x is None for x in results):
                return None
            if "applicabilityExpression" in n and array and not applicable:
                return False
            return all(results) if op == "all" else any(results)
        args = n["args"]
        if op == "implies":
            antecedent = node(args[0], i, variables)
            if antecedent is False:
                return True
            if antecedent is not None and type(antecedent) is not bool:
                raise TypeError("implies: Boolean antecedent required")
            consequent = node(args[1], i, variables)
            if consequent is not None and type(consequent) is not bool:
                raise TypeError("implies: Boolean consequent required")
            return consequent if antecedent is True else None
        if op == "sequence":
            return [node(a, i, variables) for a in args]
        if op in TEMPORAL:
            expression = args[0]
            gap = node(args[2], i, variables) if len(args) > 2 else None
            scope = n.get("asScope", {"op": "sequence", "args": []})

            def at(j: int) -> Any:
                return node(expression, j, variables)

            def identity(j: int) -> Any:
                return node(scope, j, variables)

            if gap is not None and (type(gap) is not int or gap < 0):
                raise ValueError("temporal: nonnegative integer ns gap required")
            if op in {"rise", "fall", "changed", "entered", "exited"}:
                if i == 0 or gap is not None and frame["t"] - history[i - 1]["t"] > gap:
                    return None
                ids = [identity(i - 1), identity(i)]
                if any(x is None for row in ids for x in row) or not equal(*ids):
                    return None
                a, b = at(i - 1), at(i)
                if op == "changed":
                    return None if a is None or b is None else not equal(a, b)
                return (
                    (
                        a is False and b is True
                        if op in {"rise", "entered"}
                        else a is True and b is False
                    )
                    if type(a) is bool and type(b) is bool
                    else None
                )
            duration = node(args[1], i, variables)
            if duration is None:
                return None
            if type(duration) is not int or duration < 0:
                raise ValueError(
                    "temporal: exact nonnegative integer ns duration required"
                )
            boundary = frame["t"] - duration
            anchors = [j for j in range(i + 1) if history[j]["t"] <= boundary]
            anchor = anchors[-1] if anchors else 0
            complete = bool(anchors)
            ids = [identity(j) for j in range(anchor, i + 1)]
            if any(x is None for row in ids for x in row) or any(
                not equal(row, ids[-1]) for row in ids
            ):
                return None
            if gap is not None and any(
                history[j]["t"] - history[j - 1]["t"] > gap
                for j in range(anchor + 1, i + 1)
            ):
                return None
            if op in {"delta", "rate"}:
                if not complete:
                    return None
                a, b = at(anchor), at(i)
                elapsed = frame["t"] - history[anchor]["t"]
                return (
                    None
                    if not number(a) or not number(b) or op == "rate" and not elapsed
                    else b - a
                    if op == "delta"
                    else (b - a) / elapsed
                )
            values = [at(j) for j in range(anchor, i + 1)]
            if op == "stable_window":
                now = at(i)
                if any(
                    x is not None and now is not None and not equal(x, now)
                    for x in values
                ):
                    return False
                return True if complete and all(x is not None for x in values) else None
            if op == "count_window":
                values = [at(j) for j in range(i + 1) if history[j]["t"] >= boundary]
                return (
                    sum(x is True for x in values)
                    if complete and all(type(x) is bool for x in values)
                    else None
                )
            return scalar(
                "or" if op == "any_window" else "and",
                values + ([] if complete else [None]),
                "original",
            )
        return scalar(op, [node(a, i, variables) for a in args], dialect)

    return node(ast, len(history) - 1, {})
