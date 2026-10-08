"""Native and object AST auditing with scoped parameters and real dependencies."""

from __future__ import annotations

import ast
import copy
from collections import Counter, defaultdict

from .core import Audit, Record
from .states import clock_declared, kind, path_schema, typed_schema
from .units import UnitInferenceError, infer_operator, parse_unit, units_compatible

TEMPORAL = {
    "hold",
    "holds",
    "all_window",
    "any_window",
    "count_window",
    "delta",
    "rate",
    "rise",
    "fall",
    "changed",
    "stable_window",
    "ordered_sequence",
    "duration_fraction",
    "episode_active",
    "entered",
}
NATIVE_COMPLEX = {
    "dag_longest_path",
    "robust_slope",
    "mahalanobis",
    "record_count",
    "geometry_relation",
    "polygon_signed_distance",
    "volumes_overlap_4d",
    "swept_envelopes_overlap",
    "max_consecutive_interval",
    "required_envelope_compliant",
    "geometry_observation_intersects",
    "boundary_crossing_time",
    "geometry_collection_clear",
    "swept_polygon_intersection",
    "geometry_envelope_intersects",
    "geometry_coverage_complete",
}
NUMERIC = {"number", "integer"}


def native_kind(schema):
    if not isinstance(schema, dict):
        return None
    if kind(schema) == "union":
        branches = schema.get("oneOf", schema.get("anyOf"))
        if branches is not None:
            kinds = {native_kind(b) for b in branches} - {None, "null"}
        else:
            kinds = (
                set(schema.get("type", [])) - {"null"}
                if isinstance(schema.get("type"), list)
                else set()
            )
        return next(iter(kinds)) if len(kinds) == 1 else "union"
    if kind(schema) == "enum":
        values = schema.get("enum", schema.get("values", []))
        if values and all(type(v) is bool for v in values):
            return "boolean"
        if values and all(type(v) in (int, float) for v in values):
            return "number"
    return kind(schema)


def prepare(audit: Audit) -> None:
    path = "research/original-graph/decoded.json"
    model = audit.documents.get(path, {}).get("model", {})
    if not model:
        return
    fields = model.get("F", [])
    columns = model.get("TC", [])
    kinds = model.get("K", [])
    if (
        len(columns) != 7
        or any(not isinstance(c, list) or len(c) != len(kinds) for c in columns)
        or len(model.get("MC", [])) != 4
        or any(len(c) != len(fields) for c in model.get("MC", []))
        or len(model.get("Oi", [])) != len(fields)
    ):
        audit.add(
            "semantic.original_columns",
            "blocker",
            path,
            "Original model columns are misaligned",
        )
        return
    targets = columns[0]
    for label, ids in (("field", fields), ("target", targets)):
        if len(set(ids)) != len(ids):
            audit.add(
                "semantic.original_duplicate",
                "blocker",
                path,
                f"Duplicate native {label} IDs",
            )
    for i, identity in enumerate(fields):
        if identity not in audit.fields:
            audit.add(
                "semantic.original_field",
                "blocker",
                path,
                f"Original field {identity!r} missing from registry",
                pointer=f"/model/F/{i}",
            )
        index = model["Oi"][i]
        if type(index) is not int or index < -1 or index >= len(model.get("Own", [])):
            audit.add(
                "semantic.owner_role_index",
                "blocker",
                path,
                f"Invalid native owner role index {index!r}",
                pointer=f"/model/Oi/{i}",
            )
    original_rows = []
    for i, identity in enumerate(targets):
        row = Record(
            {
                "id": "original:rule:" + identity,
                "native_ast": columns[4][i],
                "native_applicability": model.get("A", {}).get(str(i)),
                "origin": "original_graph",
                "targetId": identity,
            },
            path,
            f"/model/TC/4/{i}",
        )
        audit.rules.update(
            audit.index([row], "rule")
        ) if row.id not in audit.rules else audit.add(
            "rule.duplicate_id",
            "blocker",
            row,
            "Original rule duplicates another identity",
        )
        target = Record(
            {
                "id": identity,
                "expression": {"rule": row.id},
                "origin": "original_graph",
            },
            path,
            f"/model/TC/0/{i}",
        )
        registry = (
            audit.events
            if kinds[i] == "event"
            else audit.predicates
            if kinds[i] == "predicate"
            else None
        )
        if registry is None:
            audit.add(
                "semantic.original_kind",
                "blocker",
                target,
                f"Invalid target kind {kinds[i]!r}",
            )
        elif identity in registry:
            audit.add(
                "semantic.target_duplicate", "blocker", target, "Duplicate target ID"
            )
        else:
            registry[identity] = target
        original_rows.append(row)
    audit.semantic_rows.extend(original_rows)
    audit.metrics["original"] = {
        "fields": len(fields),
        "predicates": kinds.count("predicate"),
        "events": kinds.count("event"),
        "root_rules": len(targets),
        "embedded_samples": len(model.get("X", [])),
        "sampled_targets": len(
            {x[0] for x in model.get("X", []) if isinstance(x, list) and x}
        ),
        "historical_validation_counts": model.get("C"),
    }
    derive_profiles(audit)


def derive_profiles(audit: Audit) -> None:
    "Recompute mapping counts from the documented policy without running its generator."
    source_path = "semantic-directory/src/capability_profiles.py"
    path = audit.root / source_path
    em = {}
    if path.is_file():
        tree = ast.parse(audit.read_text(path))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "EM_APPLICATIONS"
                for t in node.targets
            ):
                em = ast.literal_eval(node.value)
    rows = []
    candidates = []
    for identity, record in sorted(audit.entities.items()):
        data = record.data
        own = data.get("ownFieldIds", [])
        if own:
            rows.append(
                Record(
                    {
                        "id": "cap.profile.source." + identity,
                        "kind": "source_hierarchy_reuse",
                        "baseTypeIds": [identity],
                        "compatibleTypeIds": sorted(
                            t for t in audit.entities if identity in audit.ancestors(t)
                        ),
                        "fieldIds": own,
                        "derived_audit_mapping": True,
                    },
                    record.artifact,
                    record.pointer,
                )
            )
        if data.get("origin") in ("restored14", "newDesign6"):
            candidates.append(identity)
            bases = []
            mapping_kind = None
            targets = []
            if identity in em:
                bases = em[identity]["bases"]
                targets = em[identity]["targets"]
                mapping_kind = "record_subject_adapter"
            elif identity.startswith("proposal:taxonomy:T01."):
                bases = [
                    "oo:Vehicle",
                    "oo:GroundVehicle"
                    if identity.endswith("RailGuidedGroundVehicle")
                    else "oo:Aircraft",
                ]
                if identity.endswith("AirGroundConvertibleVehicle"):
                    bases.append("oo:GroundVehicle")
                mapping_kind = "explicit_configuration_capability"
            elif identity.startswith("proposal:foundation:"):
                bases = [
                    "proposal:taxonomy:T03.NetworkEndpoint"
                    if identity.endswith("IPsecEndpoint")
                    else "proposal:taxonomy:T03.ProtocolSession"
                ]
                mapping_kind = "explicit_protocol_capability"
            elif identity.startswith("proposal:taxonomy:T05."):
                bases = ["oo:InformationArtifact"]
                mapping_kind = "explicit_document_capability"
            if mapping_kind is None:
                audit.add(
                    "profile.policy_unmapped",
                    "major",
                    record,
                    "Candidate has no recognized explicit policy; inspect "
                    "capability_profiles.py for policy drift",
                )
                continue
            rows.append(
                Record(
                    {
                        "id": "cap.profile.explicit." + identity,
                        "kind": mapping_kind,
                        "baseTypeIds": bases,
                        "compatibleTypeIds": [identity],
                        "fieldIds": sorted(
                            f.id
                            for f in audit.fields.values()
                            if f.data.get("declaringClass") in bases
                        ),
                        "capabilityFieldIds": sorted(
                            f.id
                            for f in audit.fields.values()
                            if f.artifact.endswith("capability-definitions.json")
                            and f.data.get("declaringClass") == identity
                        ),
                        "sourceTargetIds": targets,
                        "derived_audit_mapping": True,
                    },
                    record.artifact,
                    record.pointer,
                )
            )
    covered = {t for r in rows for t in r.data["compatibleTypeIds"]}
    for identity in sorted(audit.entities.keys() - covered):
        record = audit.entities[identity]
        fields = record.data.get("originalOwnFieldIds", [])
        rows.append(
            Record(
                {
                    "id": "cap.profile.unadopted." + identity,
                    "kind": "unadopted_record_contract",
                    "bindingStatus": "not_asserted",
                    "compatibleTypeIds": [identity],
                    "fieldIds": fields,
                    "baseTypeIds": sorted(
                        {
                            audit.fields[f].data.get("declaringClass")
                            for f in fields
                            if f in audit.fields
                        }
                    ),
                    "derived_audit_mapping": True,
                },
                record.artifact,
                record.pointer,
            )
        )
    for row in rows:
        if row.id in audit.profiles:
            audit.add(
                "profile.duplicate_id",
                "blocker",
                row,
                "Derived profile duplicates a materialized profile",
            )
        else:
            audit.profiles[row.id] = row
    counts = Counter(r.data["kind"] for r in rows)
    audit.metrics["capability_profiles"] = {
        "count": len(rows),
        "kinds": dict(sorted(counts.items())),
        "covered_types": len({t for r in rows for t in r.data["compatibleTypeIds"]}),
        "candidate_types": len(candidates),
        "method": "Audit-local reconstruction from current own fields, actual parent "
        "chains, literal EM_APPLICATIONS and explicit candidate prefix "
        "policy; not execution or verification of generated browser "
        "metadata.",
    }
    readme = audit.root / "semantic-directory/README.md"
    if readme.is_file() and "855" in audit.read_text(readme) and len(rows) != 855:
        audit.add(
            "cross.capability_count",
            "minor",
            "semantic-directory/README.md",
            f"Claim 855 profiles; audit reconstruction {len(rows)}",
        )


class SemanticAudit:
    def __init__(self, audit: Audit):
        self.a = audit
        self.model = audit.documents.get(
            "research/original-graph/decoded.json", {}
        ).get("model", {})
        self.refs = defaultdict(set)
        self.field_refs = defaultdict(set)
        self.relation_refs = defaultdict(set)
        self.failed = set()
        self.unproven = set()
        self.inbound = Counter()
        self.ops = Counter()
        self.parameters = defaultdict(dict)
        self.type_cache = {}
        self.active = set()
        self.missing_default_units = defaultdict(list)
        self.temporal = []
        self.registry = {**audit.rules, **audit.predicates, **audit.events}
        self.parameter_scope = defaultdict(set)

    def problem(self, row, check, message, pointer=None, severity="blocker"):
        self.a.add("semantic." + check, severity, row, message, pointer=pointer)
        if severity == "blocker":
            self.failed.add(row.id)

    def resolve_ref(self, row, typ, identity, pointer):
        registry = {
            "field": self.a.fields,
            "relation": self.a.relations,
            "rule": self.a.rules,
            "predicate": self.a.predicates,
            "event": self.a.events,
            "target": {**self.a.predicates, **self.a.events},
        }[typ]
        if not isinstance(identity, str) or identity not in registry:
            self.problem(
                row,
                "reference",
                f"{typ} reference {identity!r} does not resolve",
                pointer,
            )
            return None
        if typ == "field":
            self.field_refs[row.id].add(identity)
        elif typ == "relation":
            self.relation_refs[row.id].add(identity)
        else:
            self.refs[row.id].add(identity)
        self.inbound[(typ, identity)] += 1
        return registry[identity]

    def scan_native(self, row, node, pointer):
        if not isinstance(node, list) or not node:
            self.problem(
                row, "native_ast", "Expected nonempty compact AST list", pointer
            )
            return
        if isinstance(node[0], list):
            for i, child in enumerate(node):
                self.scan_native(row, child, pointer + f"/{i}")
            return
        op = node[0]
        if not isinstance(op, str):
            self.problem(row, "native_ast", "Operator is not a string", pointer)
            return
        self.ops[op] += 1
        if op in ("s", "r", "b"):
            ids = (
                self.model.get("F", []) if op == "s" else self.model.get("TC", [[]])[0]
            )
            n = node[1] if len(node) > 1 else None
            if type(n) is not int or not 0 <= n < len(ids):
                self.problem(
                    row, "reference", f"Invalid native {op} index {n!r}", pointer
                )
                return
            if op == "b":
                # Canonical label resolves, while the supplied inline
                # AST is the executed dependency.
                if ids[n] not in self.registry:
                    self.problem(
                        row,
                        "reference",
                        f"Bound provenance target {ids[n]!r} missing",
                        pointer,
                    )
                self.inbound[("target", ids[n])] += 1
                if len(node) != 3:
                    self.problem(
                        row,
                        "native_ast",
                        "b requires canonical target plus inline AST",
                        pointer,
                    )
                else:
                    self.scan_native(row, node[2], pointer + "/2")
            else:
                self.resolve_ref(
                    row, "field" if op == "s" else "target", ids[n], pointer
                )
                if len(node) != 2:
                    self.problem(row, "native_ast", f"{op} requires one index", pointer)
            return
        if op == "c":
            if len(node) != 2:
                self.problem(
                    row, "native_ast", "Constant requires one payload", pointer
                )
            elif node[1] is None and pointer == row.pointer:
                self.problem(
                    row,
                    "null_expression",
                    "Root null constant cannot establish executable truth; "
                    "semantic-directory/README.md documents this preserved artifact",
                    pointer,
                )
            return
        if op == "p":
            if len(node) not in (2, 3) or not isinstance(node[1], str) or not node[1]:
                self.problem(row, "parameter", "Malformed native parameter", pointer)
                return
            self.parameter_scope[row.id].add(node[1])
            if len(node) == 3:
                self.missing_default_units[row.id].append((node[1], pointer))
            return
        if op in TEMPORAL:
            self.temporal.append((row, pointer, op))
            if len(node) < 3 or node[2] in (None, []):
                self.problem(row, "temporal_window", f"{op} lacks a window", pointer)
            if (
                op == "episode_active"
                and len(node) != 6
                or op in ("ordered_sequence", "duration_fraction")
                and len(node) != 5
            ):
                self.problem(
                    row, "native_ast", f"Malformed {op} temporal operands", pointer
                )
        for i, child in enumerate(node[1:], 1):
            if child == []:
                continue
            self.scan_native(row, child, pointer + f"/{i}")

    def scan_object(self, row, node, pointer):
        if not isinstance(node, dict):
            self.problem(row, "ast", "Object AST node required", pointer)
            return
        for typ in ("field", "relation", "rule", "predicate", "event"):
            if isinstance(node.get(typ), str):
                self.resolve_ref(row, typ, node[typ], pointer + "/" + typ)
        if "parameter" in node:
            name = node["parameter"]
            if name not in self.parameters[row.id]:
                self.problem(
                    row,
                    "parameter",
                    f"Parameter {name!r} not declared in this definition scope",
                    pointer + "/parameter",
                )
            else:
                self.parameter_scope[row.id].add(name)
        if "op" in node:
            self.ops[node["op"]] += 1
            if node["op"] in TEMPORAL:
                self.temporal.append((row, pointer, node["op"]))
                if node["op"] != "entered" and not any(
                    k in node
                    for k in ("window", "windowS", "duration", "durationS", "clock")
                ):
                    self.problem(
                        row,
                        "temporal_window",
                        f"{node['op']} has no explicit window/clock declaration",
                        pointer,
                    )
        for key in (
            "left",
            "right",
            "arg",
            "array",
            "predicate",
            "applicabilityExpression",
        ):
            if isinstance(node.get(key), dict):
                self.scan_object(row, node[key], pointer + "/" + key)
        for i, child in enumerate(node.get("args", [])):
            self.scan_object(row, child, pointer + f"/args/{i}")

    def scan(self):
        for row in self.registry.values():
            for parameter in row.data.get("parameters", []):
                name = parameter.get("id")
                if name in self.parameters[row.id]:
                    self.problem(
                        row,
                        "parameter_duplicate",
                        f"Duplicate scoped parameter {name!r}",
                    )
                self.parameters[row.id][name] = parameter
                if (
                    "default" in parameter or parameter.get("defaultPresent") is True
                ) and not parameter.get("unit"):
                    self.a.add(
                        "semantic.parameter_default_unit",
                        "major",
                        row,
                        f"Parameter {name!r} has a default without unit; "
                        f"missing is not dimensionless",
                    )
            if "native_ast" in row.data:
                self.scan_native(row, row.data["native_ast"], row.pointer)
                if row.data.get("native_applicability") is not None:
                    i = self.model.get("TC", [[]])[0].index(row.data["targetId"])
                    self.scan_native(
                        row, row.data["native_applicability"], f"/model/A/{i}"
                    )
            elif isinstance(row.data.get("expression"), dict):
                self.scan_object(
                    row, row.data["expression"], row.pointer + "/expression"
                )
            else:
                self.problem(row, "expression_missing", "No expression to compile")
            if isinstance(row.data.get("applicabilityExpression"), dict):
                self.scan_object(
                    row,
                    row.data["applicabilityExpression"],
                    row.pointer + "/applicabilityExpression",
                )
            for key, typ in (
                ("inputFieldIds", "field"),
                ("inputRelationIds", "relation"),
                ("ruleIds", "rule"),
                ("inputRuleIds", "rule"),
                ("inputPredicateIds", "predicate"),
                ("inputEventIds", "event"),
            ):
                for i, identity in enumerate(row.data.get(key, [])):
                    self.resolve_ref(row, typ, identity, row.pointer + f"/{key}/{i}")
            for key, typ in (("predicateId", "predicate"), ("eventId", "event")):
                if key in row.data:
                    self.resolve_ref(row, typ, row.data[key], row.pointer + "/" + key)
            for typ in row.data.get("entityTypeIds", []):
                if typ not in self.a.entities:
                    self.problem(
                        row, "entity_type", f"Applicable entity type {typ!r} missing"
                    )
        for identity, occurrences in sorted(self.missing_default_units.items()):
            row = self.registry[identity]
            by_name = defaultdict(list)
            for name, pointer in occurrences:
                by_name[name].append(pointer)
            for name, pointers in sorted(by_name.items()):
                suggestions = self.parameter_unit_suggestions(row, name)
                self.a.add(
                    "semantic.parameter_default_unit",
                    "minor",
                    row,
                    f"Native parameter {name!r} default lacks declared unit; "
                    f"source default and scope preserved",
                    pointer=pointers[0],
                    count=len(pointers),
                    details={
                        "parameter": name,
                        "pointers": pointers,
                        "suggested_units": suggestions,
                        "suggestions_applied": False,
                    },
                )
        preserved_temporal = set()
        for row, pointer, op in self.temporal:
            fields = self.closure(row.id, self.field_refs)
            clock_fields = [
                f for f in sorted(fields) if self.field_has_clock(self.a.fields[f].data)
            ]
            if row.data.get("clock") or op == "entered":
                continue
            if clock_fields:
                # Declaration establishes a binding requirement, not a live clock.
                continue
            if row.data.get("origin") == "original_graph":
                preserved_temporal.add(row.id)
                self.a.add(
                    "semantic.temporal_clock_binding",
                    "major",
                    row,
                    f"{op} has no clock declaration in its transitive input "
                    f"fields; bind external event-time history and sampling "
                    f"gap",
                    pointer=pointer,
                )
            else:
                self.problem(
                    row, "temporal_clock", f"{op} has no clock contract", pointer
                )
        if any(
            row.data.get("origin") == "original_graph" for row, _, _ in self.temporal
        ):
            self.a.add(
                "semantic.preserved_temporal_inputs",
                "info",
                "research/original-graph/decoded.json",
                "Preserved temporal rules consume declared history; clock "
                "declarations do not prove concrete instance bindings",
                details={
                    "rules_without_clock_declarations": sorted(preserved_temporal)
                },
            )

    @staticmethod
    def field_has_clock(field):
        return clock_declared(field, field.get("valueSchema", {}))

    def parameter_unit_suggestions(self, row, name):
        """Suggest units from adjacent operands without changing source declarations."""
        suggestions = set()

        def walk(node):
            if not isinstance(node, list) or not node:
                return
            if node[0] in ("gt", "gte", "lt", "lte", "eq", "ne", "add", "sub"):
                for i, child in enumerate(node[1:], 1):
                    if (
                        isinstance(child, list)
                        and len(child) >= 2
                        and child[:2] == ["p", name]
                    ):
                        for j, other in enumerate(node[1:], 1):
                            if j != i:
                                try:
                                    unit = self.native_type(
                                        row, other, row.pointer
                                    ).get("unit")
                                    if unit:
                                        suggestions.add(unit)
                                except UnitInferenceError:
                                    pass
            for child in node[1:]:
                walk(child)

        walk(row.data.get("native_ast"))
        return sorted(suggestions)

    def field_schema(self, identity, path):
        field = self.a.fields[identity].data
        resolved = self.a.metrics.get("resolved_schemas", {}).get(identity) or {}
        try:
            return path_schema(typed_schema(field, resolved), path)
        except ValueError as error:
            raise UnitInferenceError(f"Field {identity}: {error}") from error

    def native_type(self, row, node, pointer):
        if not isinstance(node, list) or not node:
            return {}
        if isinstance(node[0], list):
            return {"type": "array", "items": {"type": "boolean"}}
        op = node[0]
        if op == "s":
            if (
                len(node) < 2
                or type(node[1]) is not int
                or not 0 <= node[1] < len(self.model.get("F", []))
            ):
                return {}
            identity = self.model["F"][node[1]]
            schema = (
                self.field_schema(identity, []) if identity in self.a.fields else {}
            )
            if schema.get("unit") in (
                "metric_unit",
                "value.unit",
                "profile_unit",
                "member_specific",
            ):
                self.unproven.add(row.id)
                schema["unit"] = None
            return schema
        if op == "c":
            if len(node) != 2:
                return {}
            value = node[1]
            return {
                "type": "boolean"
                if type(value) is bool
                else "number"
                if type(value) in (int, float)
                else "string"
                if isinstance(value, str)
                else "array"
                if isinstance(value, list)
                else "record"
                if isinstance(value, dict)
                else None,
                "literal": True,
            }
        if op == "p":
            # Default evidence constrains value kind only; no fabricated physical unit.
            return (
                self.native_type(row, ["c", node[2]], pointer) if len(node) == 3 else {}
            )
        if op == "r":
            if (
                len(node) < 2
                or type(node[1]) is not int
                or not 0 <= node[1] < len(self.model.get("TC", [[]])[0])
            ):
                return {}
            return self.infer_definition(self.model["TC"][0][node[1]])
        if op == "b":
            return (
                self.native_type(row, node[2], pointer + "/2") if len(node) == 3 else {}
            )
        args = [
            self.native_type(row, x, pointer + f"/{i}")
            for i, x in enumerate(node[1:], 1)
        ]
        kinds = [native_kind(s) for s in args]
        if op in ("and", "or", "not"):
            if not args or op == "not" and len(args) != 1:
                raise UnitInferenceError(f"{op} has invalid arity")
            if any(k is not None and k != "boolean" for k in kinds):
                raise UnitInferenceError(f"{op} expects boolean, got {kinds}")
            return {"type": "boolean"}
        if op == "if":
            if len(args) != 3:
                raise UnitInferenceError("if requires 3 operands")
            if kinds[0] not in (None, "boolean"):
                raise UnitInferenceError("if condition must be boolean")
            if kinds[1] is None:
                return args[2]
            if kinds[2] is None:
                return args[1]
            return args[1] if kinds[1] == kinds[2] else {"type": "union"}
        if op in ("eq", "ne", "lt", "le", "lte", "gt", "ge", "gte", "between"):
            if len(args) != (3 if op == "between" else 2):
                raise UnitInferenceError(f"{op} invalid arity")
            if op not in ("eq", "ne") and any(
                k is not None and k not in NUMERIC and k != "union" for k in kinds
            ):
                raise UnitInferenceError(f"{op} numeric comparison on {kinds}")
            if (
                op in ("eq", "ne")
                and all(k is not None and k != "union" for k in kinds)
                and len(set(kinds)) > 1
                and not set(kinds) <= NUMERIC
                and not set(kinds) <= {"string", "enum", "ref"}
            ):
                raise UnitInferenceError(f"{op} compares incompatible types {kinds}")
            units = [s.get("unit") for s in args]
            declared = [u for u in units if u is not None]
            if (
                all(k in NUMERIC for k in kinds)
                and len(declared) > 1
                and any(
                    not units_compatible(left, right)
                    for i, left in enumerate(declared)
                    for right in declared[i + 1 :]
                )
            ):
                raise UnitInferenceError(f"{op} incompatible declared units {units}")
            return {"type": "boolean"}
        if op in TEMPORAL:
            if (
                op not in ("rise", "fall", "changed")
                and len(kinds) > 1
                and kinds[1] not in (None, "number", "integer")
            ):
                raise UnitInferenceError(
                    f"{op} window must be numeric seconds, got {kinds[1]}"
                )
            if (
                op in ("delta", "rate")
                and kinds
                and kinds[0] not in (None, "number", "integer")
            ):
                raise UnitInferenceError(f"{op} needs numeric input")
            if op in ("delta", "rate", "duration_fraction", "count_window"):
                return {
                    "type": "number",
                    "unit": args[0].get("unit")
                    if op == "delta" and args
                    else (args[0]["unit"] + "/s")
                    if op == "rate" and args and args[0].get("unit")
                    else "1"
                    if op == "duration_fraction"
                    else None,
                }
            if (
                op
                not in (
                    "stable_window",
                    "changed",
                    "ordered_sequence",
                    "episode_active",
                )
                and kinds
                and kinds[0] not in (None, "boolean")
            ):
                raise UnitInferenceError(f"{op} expects boolean input")
            return {"type": "boolean"}
        if op in ("is_unknown", "in", "subset", "set_equal", "disjoint"):
            return {"type": "boolean"}
        if op in ("len", "unique_count"):
            return {"type": "integer", "unit": "count"}
        if op in ("norm", "sum", "distance", "dot"):
            if (
                op in ("norm", "sum")
                and len(args) == 1
                and kinds[0] not in ("array", "vector", "matrix", None)
            ):
                raise UnitInferenceError(f"{op} needs numeric collection")
            if op == "norm" and args:
                return infer_operator("vector_norm", args, [{"native": node[1]}])
            return {
                "type": "number",
                "unit": args[0].get("unit")
                if args and op in ("norm", "sum", "distance")
                else None,
            }
        if op in (
            "add",
            "sub",
            "mul",
            "div",
            "abs",
            "sqrt",
            "pow",
            "min",
            "max",
            "clamp",
        ):
            if any(k is not None and k not in NUMERIC and k != "union" for k in kinds):
                raise UnitInferenceError(f"{op} numeric operation on {kinds}")
            units = [s.get("unit") for s in args if s.get("unit") is not None]
            if (
                op in ("add", "sub", "min", "max")
                and len(units) > 1
                and any(
                    not units_compatible(left, right)
                    for i, left in enumerate(units)
                    for right in units[i + 1 :]
                )
            ):
                raise UnitInferenceError(f"{op} incompatible declared units {units}")
            result_unit = next(
                (
                    u
                    for u in units
                    if any(
                        k.startswith("semantic:") for k, _ in parse_unit(u).dimensions
                    )
                ),
                units[0] if units else None,
            )
            return {
                "type": "number",
                "unit": result_unit
                if units and op in ("abs", "min", "max", "add", "sub")
                else None,
            }
        if op in NATIVE_COMPLEX:
            self.unproven.add(row.id)
            return {}
        raise UnitInferenceError(f"Unsupported native operator {op!r}")

    def object_type(self, row, node, scope=None):
        scope = {} if scope is None else scope
        if "field" in node and node["field"] in self.a.fields:
            return self.field_schema(node["field"], node.get("path", []))
        for typ in ("rule", "predicate", "event"):
            if isinstance(node.get(typ), str):
                return self.infer_definition(node[typ])
        if isinstance(node.get("relation"), str):
            return {"type": "boolean"}
        if "parameter" in node:
            parameter = self.parameters[row.id].get(node["parameter"], {})
            schema = copy.deepcopy(
                parameter.get("valueSchema", {"type": parameter.get("dataType")})
            )
            schema["unit"] = parameter.get("unit")
            return schema
        if "literal" in node or "value" in node:
            value = node.get("literal", node.get("value"))
            return {
                "type": "boolean"
                if type(value) is bool
                else "number"
                if type(value) in (int, float)
                else "string"
                if isinstance(value, str)
                else "array"
                if isinstance(value, list)
                else "record"
                if isinstance(value, dict)
                else "null",
                "unit": "1" if type(value) in (int, float) else None,
                "zeroLiteral": type(value) in (int, float) and value == 0,
            }
        if "time" in node:
            return {"type": "number", "unit": "s"}
        if "var" in node and "op" not in node:
            schema = scope.get(node["var"], {})
            for member in node.get("path", []):
                schema = schema.get("members", schema.get("properties", {})).get(
                    member, {}
                )
            return schema
        op = node.get("op")
        if op in ("all", "any", "forall", "exists") and "array" in node:
            schema = self.object_type(row, node["array"], scope)
            if kind(schema) != "array":
                raise UnitInferenceError(f"{op} quantifies a non-array")
            result = self.object_type(
                row,
                node["predicate"],
                {**scope, node.get("var"): schema.get("items", {})},
            )
            if kind(result) != "boolean":
                raise UnitInferenceError(f"{op} body must be boolean")
            return {"type": "boolean"}
        args = node.get("args")
        if args is None:
            args = [node[k] for k in ("left", "right", "arg") if k in node]
        schemas = [self.object_type(row, x, scope) for x in args]
        if op == "entered":
            if len(schemas) != 1 or kind(schemas[0]) not in ("boolean", None):
                raise UnitInferenceError("entered needs a boolean predicate")
            return {"type": "boolean"}
        if op in TEMPORAL:
            self.unproven.add(row.id)
            return {}
        aliases = {"norm": "vector_norm", "in": "contains"}
        if op == "in":
            schemas = list(reversed(schemas))
            args = list(reversed(args))
        return infer_operator(aliases.get(op, op), schemas, args)

    def infer_definition(self, identity):
        if identity in self.type_cache:
            return self.type_cache[identity]
        if identity in self.active:
            return {}
        row = self.registry.get(identity)
        if row is None:
            return {}
        self.active.add(identity)
        try:
            if "native_ast" in row.data:
                result = self.native_type(row, row.data["native_ast"], row.pointer)
                if row.data.get("native_applicability") is not None:
                    self.native_type(row, row.data["native_applicability"], row.pointer)
            else:
                result = self.object_type(row, row.data.get("expression", {}))
            if native_kind(result) not in ("boolean", None):
                self.problem(
                    row,
                    "result_type",
                    f"Rule/predicate/event root produces {kind(result)!r}, "
                    f"expected boolean",
                )
        except (
            UnitInferenceError,
            KeyError,
            TypeError,
            IndexError,
            ValueError,
        ) as error:
            self.problem(row, "operator_type_unit", str(error))
            result = {}
        self.active.remove(identity)
        self.type_cache[identity] = result
        return result

    def closure(self, identity, attr, seen=None):
        seen = set() if seen is None else seen
        if identity in seen:
            return set()
        seen.add(identity)
        result = set(attr.get(identity, ()))
        for target in self.refs.get(identity, ()):
            result.update(self.closure(target, attr, seen))
        return result

    def run(self):
        self.scan()
        # DFS colors diagnose execution cycles only; b provenance labels do
        # not execute canonical targets.
        colors = {}
        trail = []
        reported = set()

        def visit(identity):
            if colors.get(identity) == 2:
                return
            if colors.get(identity) == 1:
                cycle = tuple(sorted(trail[trail.index(identity) :]))
                if cycle not in reported:
                    reported.add(cycle)
                    self.problem(
                        self.registry[identity],
                        "reference_cycle",
                        "Executable reference cycle",
                        severity="blocker",
                    )
                    self.failed.update(cycle)
                return
            colors[identity] = 1
            trail.append(identity)
            for ref in sorted(self.refs.get(identity, ())):
                visit(ref)
            trail.pop()
            colors[identity] = 2

        for identity in sorted(self.registry):
            visit(identity)
        for identity in sorted(self.registry):
            self.infer_definition(identity)
        for identity in sorted(self.unproven):
            self.a.add(
                "semantic.specialized_inference",
                "info",
                self.registry[identity],
                "Specialized geometry/graph/temporal operator needs a dedicated "
                "compiler; dependency audit complete, static type/unit proof "
                "unavailable",
            )
        executable = {}
        inputs = {}
        targets = {**self.a.predicates, **self.a.events}
        for identity, row in self.registry.items():
            dependencies = self.closure(identity, self.refs)
            fields = self.closure(identity, self.field_refs)
            relations = self.closure(identity, self.relation_refs)
            inputs[identity] = {
                "fields": sorted(fields),
                "relations": sorted(relations),
                "dependencies": sorted(dependencies),
            }
            valid = (
                not ({identity} | dependencies) & (self.failed | self.unproven)
                and native_kind(self.type_cache.get(identity)) == "boolean"
                and all(
                    self.a.metrics.get("field_status", {})
                    .get(f, {})
                    .get("typed_unit_resolved")
                    for f in fields
                )
            )
            executable[identity] = valid
            if identity in self.a.events and not valid:
                self.a.add(
                    "semantic.event_unresolved",
                    "major",
                    row,
                    "Event cannot establish a fully typed, unit-resolved predicate "
                    "dependency closure; no synthetic transition",
                )
        # Reconstruct referenced source targets from audited dependency
        # closures for every profile.
        native_targets = set(self.model.get("TC", [[]])[0])
        targets_by_field = defaultdict(set)
        for target in native_targets:
            for field in inputs.get(target, {}).get("fields", []):
                targets_by_field[field].add(target)
        for profile in self.a.profiles.values():
            data = profile.data
            if not data.get("derived_audit_mapping"):
                continue
            kind_ = data.get("kind")
            if kind_ != "record_subject_adapter":
                selected_fields = (
                    data.get("fieldIds", [])
                    if kind_ in ("source_hierarchy_reuse", "unadopted_record_contract")
                    else [
                        field
                        for base in data.get("baseTypeIds", [])
                        if base in self.a.entities
                        for field in self.a.entities[base].data.get("ownFieldIds", [])
                    ]
                )
                mapped = {
                    t for field in selected_fields for t in targets_by_field[field]
                }
                if kind_ == "explicit_configuration_capability" and not data[
                    "id"
                ].endswith("RailGuidedGroundVehicle"):
                    mapped.update(
                        t
                        for t in native_targets
                        if any(
                            f.startswith("dt.air.")
                            for f in inputs.get(t, {}).get("fields", [])
                        )
                    )
                data["sourceTargetIds"] = sorted(mapped)
            data["sourcePredicateIds"] = sorted(
                t for t in data.get("sourceTargetIds", []) if t in self.a.predicates
            )
            data["sourceEventIds"] = sorted(
                t for t in data.get("sourceTargetIds", []) if t in self.a.events
            )
            compatible = set(data.get("compatibleTypeIds", []))
            if kind_ not in ("source_hierarchy_reuse", "unadopted_record_contract"):
                data["ruleIds"] = sorted(
                    r.id
                    for r in self.a.rules.values()
                    if r.artifact.endswith("capability-definitions.json")
                    and compatible.intersection(r.data.get("entityTypeIds", []))
                )
                data["predicateIds"] = sorted(
                    r.id
                    for r in self.a.predicates.values()
                    if r.artifact.endswith("capability-definitions.json")
                    and compatible.intersection(r.data.get("entityTypeIds", []))
                )
        connected = {
            t
            for p in self.a.profiles.values()
            for t in p.data.get("sourceTargetIds", [])
        }
        self.a.metrics.get("capability_profiles", {})[
            "source_targets_with_applications"
        ] = len(connected)
        self.a.metrics.get("capability_profiles", {})[
            "source_targets_without_applications"
        ] = sorted(native_targets - connected)
        # Fields/types were checked earlier; verify all reconstructed target
        # references now.
        for profile in self.a.profiles.values():
            for target in profile.data.get("sourceTargetIds", []):
                if target not in targets:
                    self.problem(
                        profile,
                        "profile_target_reference",
                        f"Profile target {target!r} is missing",
                    )
        for identity, row in self.a.rules.items():
            if not any(identity in refs for refs in self.refs.values()):
                self.a.add(
                    "semantic.unused_rule",
                    "minor",
                    row,
                    "Rule has no target/reference consumer",
                )
        used_fields = (
            set().union(*self.field_refs.values()) if self.field_refs else set()
        )
        for identity in sorted(self.a.fields.keys() - used_fields):
            self.a.add(
                "semantic.unused_field",
                "info",
                self.a.fields[identity],
                "Field not consumed by supplied original/pilot/capability/domain "
                "contracts; schema-generated conditions are outside this count",
            )
        # Pilot inputs are explicitly authored examples, never actual writer bindings.
        pilot_path = "semantic-directory/data/pilot.json"
        for i, pilot in enumerate(
            self.a.documents.get(pilot_path, {}).get("pilots", [])
        ):
            record = Record(pilot, pilot_path, f"/pilots/{i}")
            target = pilot.get("predicateId")
            if target not in self.a.predicates:
                self.problem(
                    record, "pilot_reference", f"Pilot predicate {target!r} missing"
                )
                continue
            scope = {target} | set(inputs[target]["dependencies"])
            parameters = {p for identity in scope for p in self.parameters[identity]}
            for name in pilot.get("parameters", {}):
                if name not in parameters:
                    self.problem(
                        record,
                        "pilot_parameter",
                        f"Pilot parameter {name!r} outside predicate scope",
                    )
            for key in ("values", "fieldBindings"):
                for field in pilot.get(key, {}):
                    if field not in self.a.fields:
                        self.problem(
                            record,
                            "pilot_reference",
                            f"Pilot {key} references missing field {field!r}",
                        )
            for key in ("relationValues", "relationBindings"):
                for relation in pilot.get(key, {}):
                    if relation not in self.a.relations:
                        self.problem(
                            record,
                            "pilot_reference",
                            f"Pilot {key} references missing relation {relation!r}",
                        )
            for binding in pilot.get("bindings", []):
                if binding.get("entityTypeId") not in self.a.entities:
                    self.problem(
                        record,
                        "pilot_reference",
                        f"Pilot binding type {binding.get('entityTypeId')!r} missing",
                    )
        for identity, row in self.registry.items():
            unused = set(self.parameters[identity]) - self.parameter_scope[identity]
            if unused and identity in self.a.rules:
                self.a.add(
                    "semantic.unused_parameter",
                    "minor",
                    row,
                    "Declared scoped parameters not used by the supplied expression",
                    count=len(unused),
                    details={"ids": sorted(unused)},
                )
        self.a.metrics["semantic"] = {
            "rules": len(self.a.rules),
            "predicates": len(self.a.predicates),
            "events": len(self.a.events),
            "operators": dict(sorted(self.ops.items())),
            "failed_definitions": len(self.failed),
            "specialized_unproven": len(self.unproven),
            "statically_executable_targets": sum(executable[t] for t in targets),
            "scope": "Supplied native roots and authored/domain-pack contracts. "
            "Auto-generated leaf-state predicates are not materialized "
            "input and are excluded; no simulation, observation acquisition "
            "or event dispatch is performed.",
        }
        self.a.metrics["semantic_inputs"] = inputs
        self.a.metrics["semantic_executable"] = executable
        self.a.metrics["semantic_field_consumers"] = {
            f: sorted(t for t in targets if f in inputs[t]["fields"])
            for f in self.a.fields
        }


def run(audit: Audit) -> None:
    SemanticAudit(audit).run()
