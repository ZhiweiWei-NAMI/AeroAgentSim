"""Source-backed dialect normalization into the kernel's portable subset."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from typing import Any

from aerokernel.values import typed_equal

from .source import Record, escape

Log = Callable[[str, str, Any, Any], None]
ALIASES = {"byte": "By", "Cel": "degC", "count": "1", "count/s": "1/s"}
ROLES = {
    "identity",
    "spec",
    "config",
    "observation",
    "derived",
    "requirement",
    "metadata",
}
ANNOTATIONS = {
    "description",
    "title",
    "$comment",
    "$defs",
    "unit",
    "unitField",
    "unitBasis",
    "identityComponents",
    "encoding",
    "x-referenceSemantics",
    "vocabularyStatus",
    "closed",
    "notes",
}


def scalar_kind(value: Any) -> str:
    kind = {
        type(None): "null",
        bool: "boolean",
        int: "integer",
        float: "number",
        str: "string",
    }
    if type(value) not in kind:
        raise ValueError("Only scalar enum/const values fit the portable enum subset")
    return kind[type(value)]


class Normalizer:
    def __init__(self, row: Record, definitions: dict[str, Any], log: Log):
        self.row, self.definitions, self.log = row, definitions, log
        self.references: set[str] = set()
        self.annotations: dict[int, dict[str, Any]] = {}
        self.locations: dict[int, str] = {}

    def remember(
        self, result: dict[str, Any], original: dict[str, Any], pointer: str
    ) -> dict[str, Any]:
        self.annotations[id(result)] = original
        self.locations[id(result)] = pointer
        return result

    def schema(
        self, node: Any, pointer: str = "/valueSchema", trail: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        if not isinstance(node, dict):
            raise self.row.error(f"{pointer}: schema must be a record")
        original = dict(node)
        if pointer == "/valueSchema" and "$defs" in node:
            self.definitions = {**self.definitions, "$defs": node["$defs"]}
        if "$ref" in node:
            ref = node["$ref"]
            if (
                not isinstance(ref, str)
                or not ref.startswith("#/$defs/")
                or ref in trail
            ):
                raise self.row.error(
                    f"{pointer}: missing, external or cyclic schema ref {ref!r}"
                )
            target: Any = self.definitions
            try:
                for token in ref[2:].split("/"):
                    target = target[token.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as exc:
                raise self.row.error(
                    f"{pointer}: unresolved local schema {ref!r}"
                ) from exc
            if set(node) - {"$ref"} - ANNOTATIONS:
                raise self.row.error(
                    f"{pointer}: active $ref siblings need an intersection API"
                )
            self.log("schema.local_ref", pointer, ref, target)
            resolved = self.schema(target, pointer, trail + (ref,))
            self.annotations[id(resolved)].update(
                {k: v for k, v in node.items() if k != "$ref"}
            )
            return resolved
        known = ANNOTATIONS | {
            "type",
            "nullable",
            "enum",
            "values",
            "const",
            "oneOf",
            "anyOf",
            "branches",
            "variants",
            "discriminator",
            "cases",
            "members",
            "properties",
            "required",
            "additionalProperties",
            "extra",
            "items",
            "shape",
            "length",
            "rows",
            "columns",
            "minItems",
            "maxItems",
            "minLength",
            "maxLength",
            "min_length",
            "max_length",
            "minimum",
            "maximum",
            "target",
            "targetClass",
            "target_type",
        }
        unsupported = set(node) - known
        if unsupported:
            raise self.row.error(
                f"{pointer}: unsupported active schema constraints {sorted(unsupported)}; "
                "supply a portable source contract or exclude this field"
            )
        if "nullable" in node and type(node["nullable"]) is not bool:
            raise self.row.error(f"{pointer}: nullable must be bool")
        if "values" in node and node.get("type") != "enum":
            raise self.row.error(
                f"{pointer}: values is only valid for the native enum dialect"
            )
        branches = [k for k in ("oneOf", "anyOf", "branches", "variants") if k in node]
        if branches and (
            isinstance(node.get("type"), list)
            or node.get("type") not in (None, "union")
        ):
            raise self.row.error(
                f"{pointer}: union type/sibling constraints cannot be discarded"
            )
        if len(branches) > 1:
            raise self.row.error(f"{pointer}: multiple union dialects are ambiguous")
        if branches or isinstance(node.get("type"), list):
            key = branches[0] if branches else "type"
            raw = node[key]
            if not isinstance(raw, list) or not raw:
                raise self.row.error(
                    f"{pointer}/{key}: union branches must be a nonempty array"
                )
            if set(node) - ANNOTATIONS - {key, "nullable", "type"}:
                raise self.row.error(
                    f"{pointer}: union sibling constraints cannot be discarded"
                )
            parts = [
                self.schema(
                    {"type": v} if key == "type" else v, f"{pointer}/{key}/{i}", trail
                )
                for i, v in enumerate(raw)
            ]
            nonnull = [p for p in parts if p.get("type") != "null"]
            if len(parts) == 2 and len(nonnull) == 1:
                result = dict(nonnull[0])
                original = {**self.annotations[id(nonnull[0])], **original}
                result["nullable"] = True
                if "enum" in result and None not in result["enum"]:
                    result["enum"] = [*result["enum"], None]
                self.log("schema.null_branch", pointer, original, result)
            else:
                result = {
                    "type": "union",
                    "discriminator": "$case",
                    "cases": {str(i): part for i, part in enumerate(parts)},
                }
                self.log("schema.tagged_union", pointer, original, result)
            if "nullable" in node:
                result["nullable"] = node["nullable"] or result.get("nullable", False)
            return self.remember(result, original, pointer)
        kind = node.get("type")
        values = node.get("values") if kind == "enum" else node.get("enum")
        if "const" in node:
            if values is not None:
                raise self.row.error(f"{pointer}: simultaneous const/enum unsupported")
            values = [node["const"]]
        if values is not None:
            if not isinstance(values, list) or not values:
                raise self.row.error(f"{pointer}: empty enum needs a source vocabulary")
            if any(typed_equal(v, p) for i, v in enumerate(values) for p in values[:i]):
                raise self.row.error(f"{pointer}: duplicate typed enum values")
            try:
                kinds = sorted({scalar_kind(v) for v in values})
            except ValueError as exc:
                raise self.row.error(f"{pointer}: {exc}") from exc
            if kind in (None, "enum"):
                if len(kinds) > 1:
                    if (
                        set(node)
                        - ANNOTATIONS
                        - {"type", "enum", "values", "const", "nullable"}
                    ):
                        raise self.row.error(
                            f"{pointer}: heterogeneous enum sibling constraints unsupported"
                        )
                    result = {
                        "type": "union",
                        "discriminator": "$case",
                        "cases": {
                            k: self.remember(
                                {
                                    "type": k,
                                    "enum": [v for v in values if scalar_kind(v) == k],
                                },
                                original,
                                pointer,
                            )
                            for k in kinds
                        },
                    }
                    self.log("schema.typed_enum_union", pointer, original, result)
                    if "nullable" in node:
                        result["nullable"] = node["nullable"]
                    return self.remember(result, original, pointer)
                kind = kinds[0]
                self.log(
                    "schema.enum", pointer, original, {"type": kind, "enum": values}
                )
        if kind == "enum":
            raise self.row.error(f"{pointer}: enum has no values")
        if not isinstance(kind, str):
            raise self.row.error(f"{pointer}: explicit schema type required")
        result = {"type": "record" if kind == "object" else kind}
        if kind == "object":
            self.log("schema.object_record", pointer + "/type", kind, "record")
        if "nullable" in node:
            result["nullable"] = node["nullable"]
        if values is not None:
            if node.get("nullable") is True and not any(v is None for v in values):
                values = [*values, None]
                self.log("schema.nullable_enum", pointer, original, values)
            result["enum"] = values
        for key in ("minimum", "maximum", "min_length", "max_length"):
            if key in node:
                result[key] = node[key]
        for old, new in (
            ("minLength", "min_length"),
            ("maxLength", "max_length"),
            ("minItems", "min_length"),
            ("maxItems", "max_length"),
        ):
            if old in node:
                if new in node and node[new] != node[old]:
                    raise self.row.error(f"{pointer}: conflicting bounds {old}/{new}")
                result[new] = node[old]
                self.log(
                    "schema.bound", pointer + "/" + old, node[old], {new: node[old]}
                )
        if kind in ("record", "object"):
            if "members" in node and "properties" in node:
                raise self.row.error(f"{pointer}: ambiguous members/properties")
            members = node.get("members", node.get("properties"))
            if not isinstance(members, dict):
                raise self.row.error(
                    f"{pointer}: explicit record members/properties required"
                )
            key = "members" if "members" in node else "properties"
            result["members"] = {
                k: self.schema(v, pointer + "/" + key + "/" + escape(k), trail)
                for k, v in sorted(members.items())
            }
            # Native record is a structural product. JSON Schema object retains
            # JSON Schema's optional/open defaults; these choices are recorded.
            result["required"] = node.get(
                "required", sorted(members) if kind == "record" else []
            )
            result["extra"] = node.get(
                "extra", node.get("additionalProperties", kind == "object")
            )
            if "required" not in node or (
                "extra" not in node and "additionalProperties" not in node
            ):
                self.log(
                    "schema.record_contract",
                    pointer,
                    original,
                    {"required": result["required"], "extra": result["extra"]},
                )
        if kind in ("array", "vector", "matrix"):
            result["items"] = self.schema(node.get("items"), pointer + "/items", trail)
            if kind in ("vector", "matrix"):
                dims = ("length",) if kind == "vector" else ("rows", "columns")
                shape = node.get("shape")
                for i, dim in enumerate(dims):
                    if dim in node:
                        result[dim] = node[dim]
                    elif isinstance(shape, list) and len(shape) == len(dims):
                        result[dim] = shape[i]
                        self.log(
                            "schema.shape", pointer + "/shape", shape, {dim: shape[i]}
                        )
                    else:
                        raise self.row.error(f"{pointer}: explicit {dim} required")
        if kind == "ref":
            targets = [
                node[k] for k in ("target", "targetClass", "target_type") if k in node
            ]
            if (
                not targets
                or any(t != targets[0] for t in targets)
                or not isinstance(targets[0], str)
            ):
                raise self.row.error(
                    f"{pointer}: an unambiguous reference target is required"
                )
            self.references.add(targets[0])
            result["target_type"] = targets[0]
            self.log(
                "schema.identity_ref",
                pointer,
                original,
                {
                    "target_type": targets[0],
                    "wire": "$ref(run_id,epoch,id,generation,type_id)",
                },
            )
        if kind == "union":
            if node.get("discriminator") != "$case" or not isinstance(
                node.get("cases"), dict
            ):
                raise self.row.error(
                    f"{pointer}: union needs branches or explicit $case/cases"
                )
            result["discriminator"] = "$case"
            result["cases"] = {
                k: self.schema(v, pointer + "/cases/" + escape(k), trail)
                for k, v in node["cases"].items()
            }
        # Reject known keywords used on the wrong kind instead of dropping them.
        applicable = ANNOTATIONS | {"type", "nullable", "enum", "const"}
        if kind == "enum" or "values" in node:
            applicable.add("values")
        applicable |= {
            "record": {"members", "required", "extra", "additionalProperties"},
            "object": {"properties", "required", "additionalProperties", "extra"},
            "ref": {"target", "targetClass", "target_type"},
            "array": {"items", "minItems", "maxItems", "min_length", "max_length"},
            "vector": {"items", "shape", "length"},
            "matrix": {"items", "shape", "rows", "columns"},
            "union": {"discriminator", "cases"},
            "number": {"minimum", "maximum"},
            "integer": {"minimum", "maximum"},
            "string": {"minLength", "maxLength", "min_length", "max_length"},
        }.get(kind, set())
        if set(node) - applicable:
            raise self.row.error(
                f"{pointer}: constraints do not apply to {kind}: {sorted(set(node) - applicable)}"
            )
        return self.remember(result, original, pointer)

    def unit(self, unit: Any, pointer: str) -> dict[str, Any]:
        result: dict[str, Any]
        if isinstance(unit, str):
            result = {"symbol": unit, "status": "exact"}
            self.log("unit.string", pointer, unit, result)
        elif isinstance(unit, dict):
            result = dict(unit)
        else:
            raise self.row.error(f"{pointer}: explicit unit declaration required")
        if result.get("status") not in {
            "exact",
            "explicit",
            "dimensionless",
            "not_applicable",
            "unresolved",
            "unknown",
            "member_specific",
            "structured",
            "per_member",
        }:
            raise self.row.error(
                f"{pointer}: unknown unit status {result.get('status')!r}"
            )
        if result.get("status") == "explicit":
            if not isinstance(result.get("symbol"), str) or not result["symbol"]:
                raise self.row.error(f"{pointer}: explicit unit has no usable symbol")
            result["status"] = "exact"
            self.log("unit.explicit", pointer + "/status", "explicit", "exact")
        if result.get("status") in ("unresolved", "unknown"):
            raise self.row.error(
                f"{pointer}: unresolved units need a source declaration/conversion"
            )
        symbol = result.get("symbol")
        if isinstance(symbol, str) and symbol in ALIASES:
            result["symbol"] = ALIASES[symbol]
            self.log("unit.alias", pointer + "/symbol", symbol, ALIASES[symbol])
        if "memberUnits" in result:
            members, alternate = result.get("members", {}), result.pop("memberUnits")
            if not isinstance(members, dict) or not isinstance(alternate, dict):
                raise self.row.error(f"{pointer}: members/memberUnits must be maps")
            if any(k in members and members[k] != v for k, v in alternate.items()):
                raise self.row.error(f"{pointer}: conflicting members/memberUnits")
            result["members"] = {**members, **alternate}
            self.log("unit.member_units", pointer, unit, result)
        if "members" in result:
            if not isinstance(result["members"], dict):
                raise self.row.error(f"{pointer}: members must be a mapping")
            normalized_members: dict[str, Any] = {}
            for name, declared in result["members"].items():
                if isinstance(declared, str):
                    normalized_members[name] = ALIASES.get(declared, declared)
                    if declared != normalized_members[name]:
                        self.log(
                            "unit.alias",
                            pointer + "/members/" + escape(name),
                            declared,
                            normalized_members[name],
                        )
                else:
                    normalized_members[name] = self.unit(
                        declared, pointer + "/members/" + escape(name)
                    )
            result["members"] = normalized_members
        if result.get("status") in ("exact", "dimensionless") and (
            not isinstance(result.get("symbol"), str) or not result["symbol"]
        ):
            raise self.row.error(f"{pointer}: unit status requires an explicit symbol")
        return result

    def metadata(self, schema: dict[str, Any]) -> dict[str, Any]:
        data = self.row.data
        rawrole = data.get("role")
        if not isinstance(rawrole, str):
            raise self.row.error("Field requires an explicit semantic role")
        role = {"configuration": "config", "specification": "spec"}.get(
            rawrole, rawrole
        )
        if role not in ROLES:
            raise self.row.error(
                f"Unknown role {rawrole!r}; supply a reviewed role mapping"
            )
        if role != rawrole:
            self.log("role.vocabulary", "/role", rawrole, role)
        unit = self.unit(data.get("unit"), "/unit")
        frame = data.get("frame")
        if isinstance(frame, str):
            normalized_frame = {
                "status": "declared",
                "declaration": frame,
                "binding_requirement": "instance frame and transform revision",
            }
            self.log("frame.string", "/frame", frame, normalized_frame)
            frame = normalized_frame
        if isinstance(frame, dict) and frame.get("status") in ("unresolved", "unknown"):
            raise self.row.error(
                "Selected field frame is unresolved; supply a source frame declaration"
            )
        time = data.get("time")
        if isinstance(time, dict) and time.get("status") in ("unresolved", "unknown"):
            raise self.row.error(
                "Selected field temporal contract is unresolved; supply its source clock/time declaration"
            )
        timing: list[dict[str, Any]] = []
        if time:
            timing.append({"source": "time", "declaration": time})
        for key in ("clockRef", "bindingPolicy"):
            if data.get(key) and (
                key == "clockRef"
                or re.search(
                    r"clock|valid.time|available.time|时钟|有效时间",
                    str(data[key]),
                    re.IGNORECASE,
                )
            ):
                timing.append({"source": key, "declaration": data[key]})
                self.log("time.dialect", "/" + key, data[key], timing[-1])
        writer = data.get("writer")
        prose = writer.get("description") if isinstance(writer, dict) else writer
        if isinstance(prose, str) and re.search(
            r"clock|时钟|同钟|时刻|时窗|有效期|配置版本", prose, re.IGNORECASE
        ):
            timing.append({"source": "writer", "declaration": prose})
            self.log("time.writer_declaration", "/writer", writer, timing[-1])
        lifetime = data.get("lifetime")
        if isinstance(lifetime, str) and re.search(
            r"config|definition.static", lifetime, re.IGNORECASE
        ):
            timing.append({"source": "lifetime", "declaration": lifetime})
            self.log("time.configuration_lifetime", "/lifetime", lifetime, timing[-1])
        numeric_units: dict[str, Any] = {}
        declarations = unit.get("members", {})
        if not isinstance(declarations, dict):
            raise self.row.error("unit.members must be a mapping")

        def walk(
            node: dict[str, Any], path: tuple[str, ...], inherited: Any, identity: bool
        ) -> None:
            kind = node.get("type")
            symbol = inherited
            annotation = self.annotations[id(node)]
            pointer = self.locations[id(node)]
            identity = (
                identity
                or bool(annotation.get("identityComponents"))
                or "InstanceRef" in str(annotation.get("x-referenceSemantics", ""))
            )
            if "unit" in annotation:
                symbol = self.unit(annotation["unit"], pointer + "/unit").get("symbol")
            if "unitField" in annotation:
                symbol = {"dynamic_unit_member": annotation["unitField"]}
                self.log(
                    "unit.dynamic_context",
                    pointer + "/unitField",
                    annotation["unitField"],
                    symbol,
                )
            for pattern, value in declarations.items():
                actual = path if "*" in pattern else tuple(p for p in path if p != "*")
                if fnmatch.fnmatchcase(".".join(actual), pattern):
                    symbol = (
                        self.unit(value, "/unit/members/" + escape(pattern)).get(
                            "symbol"
                        )
                        if isinstance(value, dict)
                        else ALIASES.get(value, value)
                    )
            if kind in ("number", "integer"):
                quotation = "所有秒值使用明确来源时钟"
                if (
                    symbol is None
                    and data.get("id")
                    == "oo:human_urban.Incident.occurrenceTimeEvidence"
                    and quotation
                    in str(data.get("meaning", data.get("description", "")))
                    and path
                    and path[-1] in {"atS", "startS", "endS", "firstObservedAtS"}
                ):
                    symbol = "s"
                    self.log(
                        "unit.source_time_quotation",
                        pointer,
                        None,
                        {
                            "symbol": "s",
                            "basis": {"field": "meaning", "quotation": quotation},
                        },
                    )
                if symbol in (None, "not_applicable"):
                    if identity and kind == "integer":
                        symbol = "1"
                        self.log(
                            "unit.identity_integer",
                            pointer,
                            None,
                            "1",
                        )
                    else:
                        raise self.row.error(
                            f"Numeric leaf {'.'.join(path) or '<root>'} has no explicit unit; "
                            "supply unit/memberUnits or a sibling dynamic unit contract"
                        )
                numeric_units[".".join(path) or "$"] = symbol
            for name, child in node.get("members", {}).items():
                childunit = None if symbol == "1" and not identity else symbol
                if (
                    name == "value"
                    and childunit is None
                    and node["members"].get("unit", {}).get("type") in ("string", "ref")
                ):
                    childunit = {"dynamic_unit_member": "unit"}
                    self.log(
                        "unit.dynamic_context",
                        self.locations[id(child)],
                        None,
                        childunit,
                    )
                walk(child, path + (name,), childunit, identity)
                if name in {
                    "clockRef",
                    "playbackClockRef",
                    "sourceClock",
                    "clock",
                    "startSeconds",
                    "endSeconds",
                    "startS",
                    "endS",
                    "ageS",
                    "softAgeS",
                    "hardAgeS",
                    "acquiredAt",
                    "availableAt",
                    "validFrom",
                    "validUntil",
                } or re.search(
                    r"clock|timestamp|(?:AgeS|AgeSeconds)$", name, re.IGNORECASE
                ):
                    timing.append(
                        {"source": "schema_member", "path": list(path + (name,))}
                    )
                    self.log(
                        "time.schema_member",
                        self.locations[id(child)],
                        name,
                        timing[-1],
                    )
            if "items" in node:
                walk(node["items"], path + ("*",), symbol, identity)
            for name, child in node.get("cases", {}).items():
                walk(child, path + ("$case:" + name,), symbol, identity)

        symbol = unit.get("symbol")
        if isinstance(symbol, str) and symbol.startswith("mixed:"):
            self.log(
                "unit.structured_context",
                "/unit",
                unit,
                {
                    "requirement": "explicit member units or sibling dynamic unit context"
                },
            )
            symbol = None
        if unit.get("status") in (
            "not_applicable",
            "member_specific",
            "structured",
            "per_member",
        ):
            symbol = None
        if schema.get("type") == "record" and symbol == "1" and role != "identity":
            symbol = None  # count of containers never types quantity members
        walk(schema, (), symbol, role == "identity")
        if (
            numeric_units
            and role not in ("identity", "config", "spec", "metadata")
            and not timing
        ):
            raise self.row.error(
                "Selected numeric observation has no time/clock declaration; supply a source contract"
            )
        if schema.get("type") in ("vector", "matrix") and (
            not isinstance(frame, dict) or not frame.get("status")
        ):
            raise self.row.error(
                "Vector/matrix field has no frame declaration; do not invent a frame"
            )
        return {
            "role": role,
            "unit": unit,
            "numeric_units": numeric_units,
            "frame": frame,
            "time": time,
            "temporal_declarations": timing,
            "raw": data,
        }


def cardinality(row: Record, log: Log) -> dict[str, Any]:
    data = row.data.get("cardinality")
    if not isinstance(data, dict):
        raise row.error("Relation requires explicit cardinality")
    snake = any(k in data for k in ("targets_per_source", "sources_per_target"))
    camel = any(k in data for k in ("targetsPerSource", "sourcesPerTarget"))
    if (
        snake
        and camel
        or (snake or camel)
        and any(k in data for k in ("min", "max", "inverse"))
    ):
        raise row.error("Ambiguous cardinality dialects; resolve upstream")
    result: dict[str, Any] = {}

    def bound(value: Any, low: str, high: str, directional: bool) -> dict[str, Any]:
        if not isinstance(value, dict) or low not in value or high not in value:
            raise row.error(
                f"Cardinality requires explicit {low}/{high}; missing max is not unbounded"
            )
        lo, hi = value[low], value[high]
        unbounded = hi == "*" or hi is None and directional
        if (
            type(lo) is not int
            or lo < 0
            or not (unbounded or type(hi) is int and hi >= lo)
        ):
            raise row.error(f"Invalid cardinality bounds {value!r}")
        return {"min": lo, "max": None if unbounded else hi}

    if snake or camel:
        for name, old in (
            (
                "targets_per_source",
                "targets_per_source" if snake else "targetsPerSource",
            ),
            (
                "sources_per_target",
                "sources_per_target" if snake else "sourcesPerTarget",
            ),
        ):
            if old in data:
                result[name] = bound(
                    data[old],
                    "minimum" if snake else "min",
                    "maximum" if snake else "max",
                    True,
                )
        if "targets_per_source" not in result:
            raise row.error("Forward targets_per_source bound required")
    else:
        result["targets_per_source"] = bound(data, "min", "max", False)
        if "inverse" in data:
            result["sources_per_target"] = bound(data["inverse"], "min", "max", False)
    if "scope" in data:
        result["scope"] = data["scope"]
    log("relation.directional_cardinality", "/cardinality", data, result)
    return result
