"""Field and relation contracts: schema shape, inheritance and producer authority."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .core import Audit, escape
from .units import UnitInferenceError, parse_unit

KINDS = {
    "boolean",
    "integer",
    "number",
    "string",
    "enum",
    "ref",
    "vector",
    "matrix",
    "record",
    "array",
    "union",
    "object",
    "null",
}


def resolve(schema, definitions, trail=()):
    if isinstance(schema, list):
        return [resolve(s, definitions, trail) for s in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            raise ValueError(f"Unsupported schema reference {ref!r}")
        if ref in trail:
            raise ValueError(f"Cyclic schema reference {ref}")
        key = ref[8:].replace("~1", "/").replace("~0", "~")
        if key not in definitions:
            raise ValueError(f"Missing schema reference {ref}")
        result = resolve(definitions[key], definitions, trail + (ref,))
    result.update(
        {k: resolve(v, definitions, trail) for k, v in schema.items() if k != "$ref"}
    )
    return result


def kind(schema):
    if not isinstance(schema, dict):
        return None
    if "enum" in schema or schema.get("type") == "enum":
        return "enum"
    if "anyOf" in schema or "oneOf" in schema or isinstance(schema.get("type"), list):
        return "union"
    if "const" in schema:
        value = schema["const"]
        return (
            "boolean"
            if type(value) is bool
            else "number"
            if type(value) in (int, float)
            else "string"
            if isinstance(value, str)
            else "null"
            if value is None
            else "record"
        )
    return "record" if schema.get("type") == "object" else schema.get("type")


def schema_nodes(schema, pointer=""):
    if not isinstance(schema, dict):
        return
    yield pointer, schema
    for key in ("members", "properties"):
        for name, child in schema.get(key, {}).items():
            yield from schema_nodes(child, pointer + "/" + key + "/" + escape(name))
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            yield from schema_nodes(schema[key], pointer + "/" + key)
    for key in ("anyOf", "oneOf", "variants", "branches", "prefixItems"):
        for i, child in enumerate(schema.get(key, [])):
            yield from schema_nodes(child, pointer + f"/{key}/{i}")


def unit_symbol(field):
    unit = field.get("unit")
    return (
        unit.get("symbol")
        if isinstance(unit, dict)
        else unit
        if isinstance(unit, str)
        else None
    )


def static_role(field, schema):
    nodes = list(schema_nodes(schema))
    identity_wrapper = (
        kind(schema) == "record"
        and any(
            kind(node) == "ref" and node.get("identityComponents") for _, node in nodes
        )
        and all(kind(node) in ("record", "ref", "string", "enum") for _, node in nodes)
    )
    return field.get("role") in (
        "identity",
        "identity_or_configuration",
        "config",
        "configuration",
        "spec",
        "specification",
        "metadata",
    ) or (
        field.get("role") not in ("observation", "derived", "state")
        and (kind(schema) in ("string", "enum", "ref") or identity_wrapper)
    )


def temporal_dialect(field, schema):
    """Locate declarations; partial alternate dialects still need normalization."""
    declarations = []
    policy = field.get("bindingPolicy")
    if isinstance(policy, str) and re.search(
        r"clock|valid time|available time|时钟|有效时间", policy, re.I
    ):
        declarations.append("bindingPolicy")
    if field.get("clockRef"):
        declarations.append("clockRef")
    lifetime = field.get("lifetime")
    if isinstance(lifetime, str) and re.search(
        r"configuration|revision.config|versioned.config", lifetime, re.I
    ):
        declarations.append("lifetime")
    writer = field.get("writer")
    description = writer.get("description") if isinstance(writer, dict) else writer
    if isinstance(description, str) and re.search(
        r"clock|时钟|同钟|时刻|时窗|时间序列|有效期|生命周期|修订|版本化|配置版本",
        description,
        re.I,
    ):
        declarations.append("writer.description")
    names = {
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
    }
    for pointer, node in schema_nodes(schema):
        for key in ("members", "properties"):
            for name in node.get(key, {}):
                if name not in names and not re.search(
                    r"clock|timestamp|(?:AgeS|AgeSeconds)$", name, re.I
                ):
                    continue
                declarations.append("valueSchema" + pointer + "/" + key + "/" + name)
    return sorted(declarations)


def clock_declared(field, schema):
    time = field.get("time")
    if isinstance(time, dict) and any(
        time.get(k)
        for k in (
            "clock",
            "clockBinding",
            "clockRequired",
            "clockRef",
        )
    ):
        return True
    if field.get("clockRef"):
        return True
    writer = field.get("writer")
    writer_text = writer.get("description") if isinstance(writer, dict) else writer
    if any(
        isinstance(text, str) and re.search(r"clock|时钟|同钟", text, re.I)
        for text in (field.get("bindingPolicy"), writer_text)
    ):
        return True
    return any(
        re.search(r"clock", name, re.I)
        for _, node in schema_nodes(schema)
        for key in ("members", "properties")
        for name in node.get(key, {})
        if kind(node[key][name]) in ("ref", "string")
    )


def with_units(schema, inherited=None):
    """Propagate declared units while preserving mixed and dynamic quantities."""
    if not isinstance(schema, dict):
        return schema
    result = dict(schema)
    unit = schema.get("unit", inherited)
    if isinstance(unit, dict):
        unit = unit.get("symbol")
    result["unit"] = unit
    for key in ("members", "properties"):
        if key in schema:
            result[key] = {
                name: with_units(value, None if unit == "1" else unit)
                for name, value in schema[key].items()
            }
    if isinstance(schema.get("items"), dict):
        # A scalar collection unit describes numeric items, not record members.
        item_unit = None if kind(schema["items"]) == "record" else unit
        result["items"] = with_units(schema["items"], item_unit)
    for key in ("oneOf", "anyOf", "branches"):
        if key in schema:
            result[key] = [with_units(branch, unit) for branch in schema[key]]
    return result


def typed_schema(field, resolved):
    unit = unit_symbol(field)
    if isinstance(unit, str) and (
        unit.startswith("mixed:")
        or unit in ("member_specific", "per_member", "structured", "not_applicable")
    ):
        unit = None
    schema = with_units(resolved, unit)
    # Mirror the source-backed adapter dialect, without importing its code or
    # deriving units from member names alone. Retain the exact source quotation.
    quotation = "所有秒值使用明确来源时钟"
    if field.get(
        "id"
    ) == "oo:human_urban.Incident.occurrenceTimeEvidence" and quotation in field.get(
        "meaning", field.get("description", "")
    ):
        for _, branch in schema_nodes(schema):
            for name, member in branch.get("members", {}).items():
                if name in ("atS", "startS", "endS", "firstObservedAtS"):
                    member["unit"] = "s"
                    member["unitBasis"] = {
                        "kind": "source_meaning",
                        "quotation": quotation,
                    }
    declarations = {}
    if isinstance(field.get("unit"), dict):
        for key in ("memberUnits", "members"):
            declarations.update(field["unit"].get(key, {}))

    def annotate(node, path=(), identity=False):
        if not isinstance(node, dict):
            return
        import fnmatch

        reference = str(node.get("x-referenceSemantics", ""))
        identity = (
            identity
            or bool(node.get("identityComponents"))
            or "InstanceRef" in reference
        )
        if identity and kind(node) == "integer" and node.get("unit") is None:
            node["unit"] = "1"
            node["audit_identity_integer"] = True

        for pattern, symbol in declarations.items():
            actual = path if "*" in pattern else tuple(p for p in path if p != "*")
            if len(pattern.split(".")) == len(actual) and all(
                fnmatch.fnmatchcase(str(v), p)
                for p, v in zip(pattern.split("."), actual, strict=True)
            ):
                # Remove synthesized absence before propagating an explicit unit.
                def clean(value):
                    if isinstance(value, dict):
                        if value.get("unit") is None:
                            value.pop("unit", None)
                        for child in value.values():
                            clean(child)
                    elif isinstance(value, list):
                        for child in value:
                            clean(child)

                clean(node)
                replacement = with_units(node, symbol)
                node.clear()
                node.update(replacement)
        for key in ("members", "properties"):
            members = node.get(key, {})
            context = members.get("unit")
            value = members.get("value")
            if (
                isinstance(context, dict)
                and kind(context) in ("string", "enum", "ref")
                and isinstance(value, dict)
                and kind(value) in ("number", "integer")
                and value.get("unit") is None
            ):
                value["unitField"] = "unit"
            for name, child in node.get(key, {}).items():
                annotate(child, path + (name,), identity)
        if isinstance(node.get("items"), dict):
            annotate(node["items"], path + ("*",), identity)
        for key in ("oneOf", "anyOf", "branches"):
            for branch in node.get(key, []):
                annotate(branch, path, identity)

    annotate(schema, identity=field.get("role") == "identity")
    return schema


def path_schema(schema, path):
    if not path:
        return schema
    segment, *tail = path
    branches = schema.get("oneOf", schema.get("anyOf", schema.get("branches")))
    if branches is not None:
        resolved = []
        for branch in branches:
            try:
                resolved.append(path_schema(branch, path))
            except ValueError:
                continue
        if not resolved:
            raise ValueError(f"Path absent from all union branches: {path}")
        return (
            resolved[0]
            if all(r == resolved[0] for r in resolved)
            else {"oneOf": resolved}
        )
    collection = kind(schema) in ("array", "vector", "matrix")
    if segment == "*" and collection:
        return {
            "type": "array",
            "items": path_schema(schema.get("items", {}), tail),
            "unit": schema.get("unit"),
        }
    if collection and (
        type(segment) is int or isinstance(segment, str) and segment.isdecimal()
    ):
        index = int(segment)
        bound = (schema.get("shape") or [schema.get("maxItems")])[0]
        if index < 0 or bound is not None and index >= bound:
            raise ValueError(f"Array index outside declared shape: {path}")
        items = schema.get("items", {})
        if isinstance(items, list):
            if index >= len(items):
                raise ValueError(f"Array index outside tuple: {path}")
            items = items[index]
        return path_schema(items, tail)
    members = schema.get("members", schema.get("properties", {}))
    if segment not in members:
        raise ValueError(f"Missing schema member {segment!r}")
    return path_schema(members[segment], tail)


def writer_status(writer):
    if not isinstance(writer, dict) or not writer:
        return "missing"
    status = writer.get("instanceBindingStatus", writer.get("bindingStatus"))
    if status in (
        "not_asserted",
        "unbound",
        "source_role_alias_only",
        "owner_role_alias_only",
    ):
        return (
            "alias_only"
            if writer.get("aliases") or "alias" in str(writer.get("kind"))
            else "declared"
        )
    # A kind named bound_source_producer is a requirement, not an instance binding.
    if status == "bound" and any(
        isinstance(writer.get(k), str) and writer[k].strip()
        for k in ("instanceId", "producerId", "moduleId", "partitionId")
    ):
        return "bound"
    return (
        "alias_only"
        if "alias" in str(writer.get("kind")) or writer.get("aliases")
        else "declared"
    )


def run(audit: Audit) -> None:
    definitions = audit.documents.get(
        "semantic-directory/data/schema-definitions.json", {}
    ).get("$defs", {})
    state_schema = audit.documents.get("semantic-directory/data/state.schema.json")
    relation_schema = audit.documents.get(
        "semantic-directory/data/relation.schema.json"
    )
    if not isinstance(state_schema, dict):
        audit.add(
            "field.schema_missing",
            "blocker",
            "semantic-directory/data/state.schema.json",
            "State schema unavailable",
        )
    if not isinstance(relation_schema, dict):
        audit.add(
            "relation.schema_missing",
            "blocker",
            "semantic-directory/data/relation.schema.json",
            "Relation schema unavailable",
        )
    statuses = {}
    writers = Counter()
    roles = Counter()
    resolved = {}
    for row in audit.field_rows:
        field = row.data
        roles[str(field.get("role"))] += 1
        if isinstance(state_schema, dict):
            errors = audit.schema_errors(field, state_schema)
            if errors:
                audit.add(
                    "field.schema_conformance",
                    "major",
                    row,
                    f"{len(errors)} violations of state.schema.json; "
                    "source and extension dialects require explicit normalization",
                    count=len(errors),
                    details=[
                        {"pointer": row.pointer + p, "message": m} for p, m in errors
                    ],
                )
        if field.get("role") in ("configuration", "specification"):
            audit.add(
                "field.role_drift",
                "major",
                row,
                f"Role {field['role']!r} must map to "
                f"{'config' if field['role'] == 'configuration' else 'spec'}",
                pointer=row.pointer + "/role",
            )
        for owner in [field.get("declaringClass"), *field.get("declaredOnTypeIds", [])]:
            if owner not in audit.entities:
                audit.add(
                    "field.declaring_class",
                    "blocker",
                    row,
                    f"Declaring class {owner!r} does not exist",
                )
        try:
            schema = resolve(field.get("valueSchema"), definitions)
        except ValueError as error:
            audit.add(
                "field.schema_reference",
                "blocker",
                row,
                str(error),
                pointer=row.pointer + "/valueSchema",
            )
            schema = None
        resolved[row.id] = schema
        typed = kind(schema) in KINDS
        if not typed:
            audit.add(
                "field.untyped",
                "blocker",
                row,
                "Resolved valueSchema has no supported type",
                pointer=row.pointer + "/valueSchema",
            )
        for pointer, node in schema_nodes(schema):
            if kind(node) == "enum" and not node.get("enum", node.get("values")):
                typed = False
                audit.add(
                    "field.empty_enum",
                    "blocker",
                    row,
                    "Enum has no values; "
                    f"vocabularyStatus={node.get('vocabularyStatus')!r}, "
                    f"closed={node.get('closed')!r}",
                    pointer=row.pointer + "/valueSchema" + pointer,
                )
            if kind(node) == "ref":
                target = node.get(
                    "targetClass",
                    node.get(
                        "refType",
                        node.get(
                            "targetType",
                            node.get(
                                "target",
                                node.get(
                                    "x-referenceTargets",
                                    node.get(
                                        "targetTypeIds", node.get("targetClasses")
                                    ),
                                ),
                            ),
                        ),
                    ),
                )
                targets = target if isinstance(target, list) else [target]
                for t in targets:
                    if t not in audit.entities:
                        typed = False
                        audit.add(
                            "field.ref_target",
                            "blocker",
                            row,
                            f"Reference target type {t!r} is missing",
                            pointer=row.pointer + "/valueSchema" + pointer,
                        )
        unit = field.get("unit")
        static_units = isinstance(unit, dict) and unit.get("status") in (
            "exact",
            "explicit",
            "dimensionless",
            "not_applicable",
            "member_specific",
            "per_member",
            "structured",
            "mixed",
            "per_axis",
            "per_component",
        )
        numeric_nodes = [
            (p, n)
            for p, n in schema_nodes(typed_schema(field, schema))
            if kind(n) in ("number", "integer")
        ]
        missing_units = []
        dynamic_units = []
        for pointer, node in numeric_nodes:
            symbol = node.get("unit")
            if (
                node.get("unitField")
                or isinstance(symbol, str)
                and any(
                    t in symbol
                    for t in ("value.unit", "metric_unit", "由", "unit from")
                )
            ):
                dynamic_units.append(pointer)
            else:
                try:
                    if parse_unit(symbol) is None:
                        missing_units.append(pointer)
                except UnitInferenceError:
                    missing_units.append(pointer)
        if (
            kind(schema) in ("vector", "matrix")
            and not numeric_nodes
            and isinstance(unit, dict)
            and not unit.get("symbol")
        ):
            missing_units.append("")
        if (
            isinstance(unit, dict)
            and unit.get("status") in ("exact", "explicit")
            and not unit.get("symbol")
            and not unit.get("members")
            and not unit.get("memberUnits")
        ):
            static_units = False
        unit_ok = static_units and not missing_units and not dynamic_units
        numeric_not_applicable = (
            isinstance(unit, dict)
            and unit.get("status") == "not_applicable"
            and (
                missing_units
                or kind(schema) in ("number", "integer", "vector", "matrix")
                and any(not n.get("audit_identity_integer") for _, n in numeric_nodes)
            )
        )
        if (not static_units or missing_units) and not numeric_not_applicable:
            audit.add(
                "field.unit_unresolved",
                "blocker",
                row,
                f"Unit unresolved or numeric member declarations incomplete: {unit!r}",
                pointer=row.pointer + "/unit",
                details={"missing_numeric_unit_paths": missing_units},
            )
        if dynamic_units:
            audit.add(
                "field.dynamic_unit_context",
                "major",
                row,
                "Numeric unit depends on an explicit instance quantity/unit "
                "context; not counted as statically unit-resolved",
                pointer=row.pointer + "/unit",
                details={"dynamic_numeric_paths": dynamic_units},
            )
        if numeric_not_applicable:
            unit_ok = False
            audit.add(
                "field.numeric_unit_not_applicable",
                "major",
                row,
                "Numeric quantity needs a physical unit or an explicit "
                "dimensionless declaration",
                pointer=row.pointer + "/unit",
            )
        frame = field.get("frame")
        spatial = (
            kind(schema) in ("vector", "matrix")
            or bool(
                re.search(
                    r"(position|pose|velocity|quaternion|orientation|coordinate)",
                    str(field.get("propertyPath", "")),
                    re.IGNORECASE,
                )
            )
            and kind(schema) in ("array", "record")
            and bool(numeric_nodes)
        )
        if (
            kind(schema) not in ("vector", "matrix")
            and isinstance(frame, dict)
            and frame.get("status") == "not_applicable"
            and (frame.get("notes") or frame.get("reason"))
        ):
            spatial = False
        if spatial and (
            not frame
            or isinstance(frame, dict)
            and frame.get("status") in (None, "unresolved", "not_applicable")
        ):
            audit.add(
                "field.frame_missing",
                "blocker",
                row,
                "Spatial vector/pose-like contract lacks a usable frame declaration",
                pointer=row.pointer + "/frame",
            )
        elif (
            spatial and isinstance(frame, dict) and frame.get("status") == "from_record"
        ):
            audit.add(
                "field.frame_binding",
                "major",
                row,
                "Frame delegated to record; concrete reference and transform "
                "revision required at binding",
                pointer=row.pointer + "/frame",
            )
        if not field.get("time") and temporal_dialect(field, schema):
            audit.add(
                "field.time_dialect",
                "major",
                row,
                "Temporal declaration exists outside time; normalize its "
                "clock/validity binding without inventing timestamps",
                details={"declarations": temporal_dialect(field, schema)},
            )
        elif not field.get("time") and not static_role(field, schema):
            audit.add(
                "field.time_missing",
                "blocker",
                row,
                "No temporal contract declaration",
                pointer=row.pointer + "/time",
            )
        elif isinstance(field.get("time"), dict) and not clock_declared(field, schema):
            audit.add(
                "field.clock_missing",
                "major",
                row,
                "Time declaration has no explicit clock requirement/reference",
                pointer=row.pointer + "/time",
            )
        if not field.get("lifetime"):
            audit.add(
                "field.lifetime_missing",
                "major",
                row,
                "Lifetime declaration missing",
                pointer=row.pointer + "/lifetime",
            )
        status = writer_status(field.get("writer"))
        writers[status] += 1
        if status != "bound":
            audit.add(
                "field.writer_" + status,
                "major",
                row,
                {
                    "missing": "Writer declaration missing",
                    "alias_only": "Producer alias only; no concrete instance writer",
                    "declared": (
                        "Producer requirement declared; no concrete instance writer"
                    ),
                }[status],
                pointer=row.pointer + "/writer",
            )
        statuses[row.id] = {
            "typed_unit_resolved": typed and unit_ok,
            "writer_bound": status == "bound",
            "writer_status": status,
        }
    audit.metrics["field_status"] = statuses
    audit.metrics["field_writer_counts"] = dict(sorted(writers.items()))
    audit.metrics["field_role_counts"] = dict(sorted(roles.items()))
    audit.metrics["resolved_schemas"] = resolved
    conflicts = defaultdict(set)
    for typ in sorted(audit.entities):
        paths = defaultdict(list)
        for row in audit.effective_fields(typ):
            paths[row.data.get("propertyPath")].append(row)
        for path, rows in paths.items():
            signatures = {(kind(resolved.get(r.id)), unit_symbol(r.data)) for r in rows}
            if path and len(signatures) > 1:
                ids = tuple(sorted(r.id for r in rows))
                conflicts[(path, ids)].add(typ)
    for (path, ids), types in sorted(conflicts.items()):
        row = audit.fields[ids[-1]]
        audit.add(
            "field.inheritance_conflict",
            "blocker",
            row,
            f"Property {path!r} has conflicting types/units across "
            "inherited declarations",
            details={"field_ids": list(ids), "affected_types": sorted(types)},
            count=len(types),
        )
    endpoint_groups = defaultdict(list)
    for row in audit.relation_rows:
        rel = row.data
        cardinality = rel.get("cardinality")
        directional = isinstance(cardinality, dict) and any(
            k in cardinality
            for k in (
                "targets_per_source",
                "sources_per_target",
                "targetsPerSource",
                "sourcesPerTarget",
            )
        )
        if isinstance(relation_schema, dict):
            errors = audit.schema_errors(rel, relation_schema)
            if directional:
                errors = [
                    (p, m)
                    for p, m in errors
                    if not (
                        p in ("/cardinality/min", "/cardinality/max")
                        and m == "Required key missing"
                    )
                ]
            if errors:
                audit.add(
                    "relation.schema_conformance",
                    "major",
                    row,
                    f"{len(errors)} relation schema violations",
                    count=len(errors),
                    details=[
                        {"pointer": row.pointer + p, "message": m} for p, m in errors
                    ],
                )
        for key in ("sourceClass", "targetClass"):
            if rel.get(key) not in audit.entities:
                audit.add(
                    "relation.endpoint",
                    "blocker",
                    row,
                    f"{key} {rel.get(key)!r} does not exist",
                    pointer=row.pointer + "/" + key,
                )
        cardinality = rel.get("cardinality")
        if not isinstance(cardinality, dict):
            audit.add(
                "relation.cardinality",
                "blocker",
                row,
                "Cardinality missing/not an object",
            )
        else:
            bounds = [(cardinality, "min", "max", "/cardinality")]
            if (
                "targets_per_source" in cardinality
                or "sources_per_target" in cardinality
            ):
                bounds = [
                    (cardinality.get(k, {}), "minimum", "maximum", "/cardinality/" + k)
                    for k in ("targets_per_source", "sources_per_target")
                ]
                audit.add(
                    "relation.cardinality_dialect",
                    "major",
                    row,
                    "Bidirectional minimum/maximum dialect requires normalization; "
                    "preserve inverse cardinality and explicit "
                    "null-as-unbounded source semantics",
                    pointer=row.pointer + "/cardinality",
                )
            elif "targetsPerSource" in cardinality or "sourcesPerTarget" in cardinality:
                bounds = [
                    (
                        cardinality.get("targetsPerSource", {}),
                        "min",
                        "max",
                        "/cardinality/targetsPerSource",
                    )
                ]
                if "sourcesPerTarget" in cardinality:
                    bounds.append(
                        (
                            cardinality["sourcesPerTarget"],
                            "min",
                            "max",
                            "/cardinality/sourcesPerTarget",
                        )
                    )
                audit.add(
                    "relation.cardinality_dialect",
                    "major",
                    row,
                    "Camel-case directional cardinality requires normalization; "
                    "preserve declared scope",
                    pointer=row.pointer + "/cardinality",
                )
            elif isinstance(cardinality.get("inverse"), dict):
                bounds.append(
                    (cardinality["inverse"], "min", "max", "/cardinality/inverse")
                )
            for bound, low_key, high_key, pointer in bounds:
                if not isinstance(bound, dict):
                    audit.add(
                        "relation.cardinality",
                        "blocker",
                        row,
                        f"Cardinality bound must be an object: {bound!r}",
                        pointer=row.pointer + pointer,
                    )
                    continue
                lo, hi = bound.get(low_key), bound.get(high_key)
                unbounded = (
                    hi == "*" or hi is None and directional and high_key in bound
                )
                if (
                    type(lo) is not int
                    or lo < 0
                    or not (unbounded or type(hi) is int and hi >= 0)
                    or type(lo) is int
                    and type(hi) is int
                    and lo > hi
                ):
                    audit.add(
                        "relation.cardinality",
                        "blocker",
                        row,
                        f"Invalid cardinality {bound!r}",
                        pointer=row.pointer + pointer,
                    )
        if not rel.get("validTime"):
            audit.add(
                "relation.valid_time",
                "blocker",
                row,
                "Relation has no validity-time declaration",
                pointer=row.pointer + "/validTime",
            )
        inverse = rel.get("inverseId", rel.get("inverse", rel.get("inverseRelationId")))
        if isinstance(inverse, dict):
            inverse = inverse.get("id")
        if inverse is not None:
            other = audit.relations.get(inverse)
            if (
                other is None
                or other.data.get("sourceClass") != rel.get("targetClass")
                or other.data.get("targetClass") != rel.get("sourceClass")
                or other.data.get(
                    "inverseId",
                    other.data.get("inverse", other.data.get("inverseRelationId")),
                )
                not in (None, row.id)
            ):
                audit.add(
                    "relation.inverse",
                    "blocker",
                    row,
                    f"Inverse {inverse!r} is missing or not reciprocal "
                    "with swapped endpoints",
                )
        endpoint_groups[
            (
                rel.get("sourceClass"),
                rel.get("targetClass"),
                rel.get("kind"),
                rel.get("displayName"),
            )
        ].append(row)
    for key, rows in endpoint_groups.items():
        if len(rows) > 1:
            audit.add(
                "relation.duplicate_semantics",
                "major",
                rows[0],
                f"{len(rows)} relation IDs share endpoint/kind/name signature {key!r}",
                count=len(rows),
                details={"ids": [r.id for r in rows]},
            )
