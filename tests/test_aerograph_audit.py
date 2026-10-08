"""Synthetic audit checks; no fixture depends on the external AeroGraph tree."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools.aerograph_audit import Audit, Record, entities, report, semantics, states
from tools.aerograph_audit.__main__ import main
from tools.aerograph_audit.core import schema_errors

ROOT = "oo:ModelObject"


def record(data, artifact="fixture.json", pointer="/0"):
    return Record(data, artifact, pointer)


def entity(identity=ROOT, parent=None, **extra):
    return {
        "id": identity,
        "name": identity,
        "parent": parent,
        "abstract": False,
        "ownFieldIds": [],
        "originalOwnFieldIds": [],
        "navigation": {"view": "physical"},
        **extra,
    }


def field(identity="f", declaring=ROOT, **extra):
    return {
        "id": identity,
        "displayName": identity,
        "meaning": "test contract",
        "declaringClass": declaring,
        "propertyPath": identity,
        "role": "observation",
        "valueSchema": {"type": "number", "nullable": False},
        "unit": {"status": "exact", "symbol": "m"},
        "frame": {"status": "not_applicable"},
        "requiredWhen": {"kind": "always"},
        "lifetime": "per-observation",
        "time": {"clock": "test-clock"},
        "ownerSubject": {"kind": "class_subject", "classId": declaring},
        "writer": {
            "kind": "adapter",
            "bindingStatus": "bound",
            "producerId": "engine-1",
        },
        "sources": [],
        "example": {"kind": "not_supplied"},
        "legacyRefs": [],
        "decision": "adapted",
        "reviewStatus": "proposed",
        "bounds": {},
        "notes": [],
        **extra,
    }


def relation(identity="rel", **extra):
    return {
        "id": identity,
        "displayName": identity,
        "sourceClass": ROOT,
        "targetClass": ROOT,
        "kind": "has_a",
        "cardinality": {"min": 0, "max": "*"},
        "identityPolicy": "stable references",
        "validTime": "[start,end)",
        "sources": [],
        "reviewStatus": "proposed",
        **extra,
    }


@pytest.fixture
def audit(tmp_path):
    a = Audit(tmp_path)
    a.entity_rows = [record(entity())]
    a.entities = a.index(a.entity_rows, "entity")
    a.field_rows = [record(field())]
    a.fields = a.index(a.field_rows, "field")
    a.relation_rows = [record(relation())]
    a.relations = a.index(a.relation_rows, "relation")
    # Contract shape represents the shipped keywords without reading upstream schemas.
    a.documents["semantic-directory/data/state.schema.json"] = {
        "type": "object",
        "required": ["id", "role", "valueSchema", "unit", "requiredWhen"],
        "properties": {
            "role": {
                "enum": [
                    "identity",
                    "spec",
                    "config",
                    "observation",
                    "derived",
                    "requirement",
                    "metadata",
                ]
            },
            "valueSchema": {
                "type": "object",
                "required": ["type", "nullable"],
                "properties": {
                    "type": {"enum": list(states.KINDS - {"object", "null"})},
                    "nullable": {"type": "boolean"},
                },
            },
            "unit": {
                "type": "object",
                "required": ["status"],
                "properties": {
                    "status": {
                        "enum": [
                            "exact",
                            "dimensionless",
                            "not_applicable",
                            "unresolved",
                        ]
                    }
                },
            },
            "requiredWhen": {
                "type": "object",
                "required": ["kind"],
                "properties": {
                    "kind": {
                        "enum": ["always", "profile", "mode", "conditional", "optional"]
                    }
                },
            },
        },
    }
    a.documents["semantic-directory/data/relation.schema.json"] = {
        "type": "object",
        "required": ["id", "sourceClass", "targetClass", "cardinality", "validTime"],
        "properties": {
            "cardinality": {
                "type": "object",
                "required": ["min", "max"],
                "properties": {
                    "min": {"type": "integer", "minimum": 0},
                    "max": {
                        "anyOf": [{"type": "integer", "minimum": 0}, {"const": "*"}]
                    },
                },
            }
        },
    }
    a.documents["entity-directory/data/navigation.json"] = {
        "homepage": [
            {
                "key": "physical",
                "count": 1,
                "primaryCount": 1,
                "ids": [ROOT],
                "topics": [
                    {
                        "name": "root",
                        "count": 1,
                        "ids": [ROOT],
                        "ownIds": [ROOT],
                        "children": [],
                    }
                ],
            }
        ],
        "extraViews": [],
    }
    return a


def checks(a):
    return {f["check"] for f in a.findings}


def replace_field(a, **changes):
    a.field_rows = [record(field(**changes))]
    a.fields = a.index(a.field_rows, "field")


def test_schema_required_enums_types_anyof_and_bool_not_integer():
    errors = schema_errors(
        {
            "n": False,
            "role": "configuration",
            "unit": {"status": "mystery"},
            "requiredWhen": {"kind": "sometimes"},
        },
        {
            "type": "object",
            "required": ["missing"],
            "properties": {
                "n": {"type": "integer", "minimum": 0},
                "role": {"enum": ["config"]},
                "unit": {
                    "type": "object",
                    "properties": {"status": {"enum": ["exact"]}},
                },
                "requiredWhen": {
                    "type": "object",
                    "properties": {"kind": {"enum": ["always"]}},
                },
            },
        },
    )
    assert {p for p, _ in errors} == {
        "/missing",
        "/n",
        "/role",
        "/unit/status",
        "/requiredWhen/kind",
    }
    assert schema_errors(None, {"anyOf": [{"type": "integer"}, {"const": "*"}]})
    assert not schema_errors("*", {"anyOf": [{"type": "integer"}, {"const": "*"}]})
    assert schema_errors(-1, {"type": "integer", "minimum": 0})


def test_hierarchy_dangling_cycle_roots_abstract_and_names(audit):
    audit.entity_rows += [
        record(entity("a", "b", name="same", abstract=None)),
        record(entity("b", "a", name="same")),
        record(entity("c", "absent")),
        record(entity("detached", None, classification="profile", originalParent=ROOT)),
        record(entity("suggested", ROOT, parentStatus="suggested")),
    ]
    audit.entities = audit.index(audit.entity_rows, "entity")
    entities.run(audit)
    assert {
        "entity.dangling_parent",
        "entity.inheritance_cycle",
        "entity.multiple_roots",
        "entity.abstract_flag",
        "entity.duplicate_name",
        "entity.unreachable_root",
        "profile.unattached",
    } <= checks(audit)
    assert audit.ancestors("suggested") == {"suggested"}
    assert ROOT not in audit.ancestors("detached")


def test_duplicate_identity_is_not_overwritten(audit):
    first = record(field("x", propertyPath="first"))
    second = record(field("x", propertyPath="second"), pointer="/1")
    result = audit.index([first, second], "field")
    assert result["x"] == first
    assert "field.duplicate_id" in checks(audit)
    assert audit.index([record({"id": None})], "field") == {}


def test_navigation_counts_references_and_cross_directory_not_error(audit):
    child = record(entity("child", ROOT, navigation={"view": "business"}))
    audit.entities["child"] = child
    audit.entity_rows.append(child)
    grand = record(entity("grand", "child", navigation={"view": "physical"}))
    audit.entities["grand"] = grand
    audit.entity_rows.append(grand)
    nav = audit.documents["entity-directory/data/navigation.json"]
    nav["homepage"][0]["count"] = 99
    nav["homepage"][0]["ids"].append("absent")
    entities.run(audit)
    assert {
        "navigation.dangling_id",
        "cross.navigation_count",
        "navigation.primary_coverage",
        "navigation.primary_unclassified",
        "entity.cross_directory_inheritance",
    } <= checks(audit)
    assert all(
        f["severity"] == "info"
        for f in audit.findings
        if f["check"] == "entity.cross_directory_inheritance"
    )


def test_information_associated_ids_not_duplicate_primary(audit):
    info = record(entity("info", ROOT, navigation={"view": "information"}))
    audit.entities["info"] = info
    audit.entity_rows.append(info)
    nav = audit.documents["entity-directory/data/navigation.json"]
    nav["homepage"].append(
        {
            "key": "information",
            "count": 2,
            "primaryCount": 1,
            "ids": ["info", ROOT],
            "topics": [
                {
                    "name": "both",
                    "count": 2,
                    "ids": ["info", ROOT],
                    "ownIds": ["info", ROOT],
                    "children": [],
                }
            ],
        }
    )
    entities.run(audit)
    assert "navigation.primary_duplicate" not in checks(audit)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"role": "configuration"}, "field.role_drift"),
        ({"role": "specification"}, "field.role_drift"),
        ({"declaringClass": "absent"}, "field.declaring_class"),
        ({"valueSchema": {"type": "mystery"}}, "field.untyped"),
        ({"valueSchema": {"$ref": "#/$defs/absent"}}, "field.schema_reference"),
        ({"unit": {"status": "unresolved", "symbol": None}}, "field.unit_unresolved"),
        ({"unit": {"status": "not_applicable"}}, "field.numeric_unit_not_applicable"),
        (
            {"valueSchema": {"type": "vector", "nullable": False}, "frame": None},
            "field.frame_missing",
        ),
        (
            {
                "valueSchema": {"type": "vector", "nullable": False},
                "frame": {"status": "from_record"},
            },
            "field.frame_binding",
        ),
        ({"time": None}, "field.time_missing"),
        ({"time": {"validity": "interval"}}, "field.clock_missing"),
        ({"lifetime": None}, "field.lifetime_missing"),
        ({"writer": None}, "field.writer_missing"),
        (
            {"writer": {"kind": "source_role_alias", "aliases": ["adapter"]}},
            "field.writer_alias_only",
        ),
        (
            {
                "writer": {
                    "kind": "bound_source_producer",
                    "description": "bind explicitly",
                }
            },
            "field.writer_declared",
        ),
        (
            {
                "valueSchema": {
                    "type": "ref",
                    "targetClass": "absent",
                    "nullable": False,
                }
            },
            "field.ref_target",
        ),
        (
            {"valueSchema": {"type": "enum", "values": [], "nullable": False}},
            "field.empty_enum",
        ),
    ],
)
def test_field_contract_checks(audit, changes, expected):
    replace_field(audit, **changes)
    states.run(audit)
    assert expected in checks(audit)


def test_schema_refs_nested_enum_and_ref_targets(audit):
    audit.documents["semantic-directory/data/schema-definitions.json"] = {
        "$defs": {
            "node": {
                "type": "record",
                "members": {
                    "ref": {"type": "ref", "target": ROOT},
                    "empty": {"type": "enum", "values": []},
                },
            },
            "cycle": {"$ref": "#/$defs/cycle"},
        }
    }
    replace_field(audit, valueSchema={"$ref": "#/$defs/node"})
    states.run(audit)
    assert "field.empty_enum" in checks(audit)
    assert "field.ref_target" not in checks(audit)
    with pytest.raises(ValueError, match="Cyclic"):
        states.resolve(
            {"$ref": "#/$defs/cycle"},
            audit.documents["semantic-directory/data/schema-definitions.json"]["$defs"],
        )


def test_inherited_property_conflict_and_no_capability_leak(audit):
    audit.entities[ROOT] = record(entity(ownFieldIds=["f"]))
    audit.entities["child"] = record(entity("child", ROOT, ownFieldIds=["g"]))
    audit.field_rows.append(
        record(
            field(
                "g",
                "child",
                propertyPath="f",
                valueSchema={"type": "string", "nullable": False},
                unit={"status": "not_applicable"},
            )
        )
    )
    audit.fields = audit.index(audit.field_rows, "field")
    states.run(audit)
    assert "field.inheritance_conflict" in checks(audit)
    assert {r.id for r in audit.effective_fields("child")} == {"f", "g"}
    audit.profiles["profile"] = record(
        {
            "id": "profile",
            "kind": "record_subject_adapter",
            "compatibleTypeIds": ["child"],
            "fieldIds": ["unrelated"],
        }
    )
    assert {r.id for r in audit.effective_fields("child")} == {"f", "g"}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"sourceClass": "absent"}, "relation.endpoint"),
        ({"targetClass": "absent"}, "relation.endpoint"),
        ({"cardinality": {"min": 2, "max": 1}}, "relation.cardinality"),
        ({"cardinality": {"min": -1, "max": "*"}}, "relation.cardinality"),
        ({"cardinality": {"min": False, "max": "*"}}, "relation.cardinality"),
        ({"cardinality": {"min": 0, "max": None}}, "relation.cardinality"),
        ({"validTime": None}, "relation.valid_time"),
        ({"inverseId": "absent"}, "relation.inverse"),
    ],
)
def test_relation_contract_checks(audit, changes, expected):
    audit.relation_rows = [record(relation(**changes))]
    audit.relations = audit.index(audit.relation_rows, "relation")
    states.run(audit)
    assert expected in checks(audit)


def test_directional_cardinality_null_only_when_explicit(audit):
    card = {
        "targets_per_source": {"minimum": 0, "maximum": None},
        "sources_per_target": {"minimum": 0, "maximum": 1},
        "scope": "null means unbounded",
    }
    audit.relation_rows = [record(relation(cardinality=card))]
    audit.relations = audit.index(audit.relation_rows, "relation")
    states.run(audit)
    assert "relation.cardinality_dialect" in checks(audit)
    assert "relation.cardinality" not in checks(audit)
    card["sources_per_target"]["minimum"] = 2
    audit.findings = []
    states.run(audit)
    assert "relation.cardinality" in checks(audit)


def test_inverse_and_semantic_duplicates(audit):
    audit.relation_rows = [
        record(relation(inverseId="back")),
        record(relation("back", displayName="rel", inverseId="wrong")),
    ]
    audit.relations = audit.index(audit.relation_rows, "relation")
    states.run(audit)
    assert {"relation.inverse", "relation.duplicate_semantics"} <= checks(audit)


def object_semantics(audit, expression, parameters=()):
    states.run(audit)
    rule = record({"id": "r", "expression": expression, "parameters": list(parameters)})
    pred = record(
        {"id": "p", "expression": {"rule": "r"}, "entityTypeIds": [ROOT]}, pointer="/1"
    )
    audit.rules = {"r": rule}
    audit.predicates = {"p": pred}
    semantics.run(audit)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ({"field": "absent"}, "semantic.reference"),
        ({"relation": "absent"}, "semantic.reference"),
        ({"rule": "absent"}, "semantic.reference"),
        ({"event": "absent"}, "semantic.reference"),
        ({"parameter": "missing"}, "semantic.parameter"),
        (
            {"op": "gte", "args": [{"literal": "text"}, {"literal": 2}]},
            "semantic.operator_type_unit",
        ),
        ({"op": "and", "args": [{"field": "f"}]}, "semantic.operator_type_unit"),
        ({"op": "holds", "args": [{"literal": True}]}, "semantic.temporal_window"),
    ],
)
def test_semantic_resolution_and_types(audit, expression, expected):
    object_semantics(audit, expression)
    assert expected in checks(audit)
    assert not audit.metrics["semantic_executable"]["p"]


def test_unit_incompatible_numeric_parameters_and_missing_default_unit(audit):
    object_semantics(
        audit,
        {"op": "gte", "args": [{"field": "f"}, {"parameter": "threshold"}]},
        [{"id": "threshold", "dataType": "number", "unit": "s", "default": 1}],
    )
    assert "semantic.operator_type_unit" in checks(audit)
    audit.findings = []
    object_semantics(
        audit,
        {"op": "eq", "args": [{"parameter": "flag"}, {"literal": False}]},
        [{"id": "flag", "dataType": "boolean", "default": False}],
    )
    assert "semantic.parameter_default_unit" in checks(audit)


def test_rule_cycle_unresolved_event_and_unused_rule(audit):
    states.run(audit)
    audit.rules = {
        "a": record({"id": "a", "expression": {"rule": "b"}}),
        "b": record({"id": "b", "expression": {"rule": "a"}}),
        "unused": record({"id": "unused", "expression": {"literal": True}}),
    }
    audit.predicates = {"p": record({"id": "p", "expression": {"rule": "a"}})}
    audit.events = {
        "e": record(
            {
                "id": "e",
                "predicateId": "p",
                "expression": {"op": "entered", "args": [{"predicate": "p"}]},
            }
        )
    }
    semantics.run(audit)
    assert {
        "semantic.reference_cycle",
        "semantic.event_unresolved",
        "semantic.unused_rule",
    } <= checks(audit)


def test_nested_member_type_and_units(audit):
    replace_field(
        audit,
        valueSchema={
            "type": "record",
            "nullable": False,
            "members": {
                "distance": {"type": "number", "unit": "m"},
                "label": {"type": "string"},
            },
        },
        unit={"status": "exact", "symbol": "mixed", "members": {"distance": "m"}},
    )
    object_semantics(
        audit,
        {"op": "gte", "args": [{"field": "f", "path": ["label"]}, {"literal": 1}]},
    )
    assert "semantic.operator_type_unit" in checks(audit)


def native_model(audit, asts, kinds=None, applicability=None):
    audit.documents["research/original-graph/decoded.json"] = {
        "model": {
            "F": ["f"],
            "TC": [
                [f"t{i}" for i in range(len(asts))],
                [None] * len(asts),
                ["test"] * len(asts),
                ["test"] * len(asts),
                asts,
                ["native"] * len(asts),
                ["observed"] * len(asts),
            ],
            "MC": [["number"], ["m"], [None], [None]],
            "K": kinds or ["predicate"] * len(asts),
            "Oi": [0],
            "Own": ["adapter"],
            "A": applicability or {},
            "X": [],
        }
    }


def test_native_r_executes_canonical_b_executes_inline_and_null_branch(audit):
    states.run(audit)
    native_model(
        audit,
        [
            ["c", None],
            ["b", 0, ["if", ["c", True], ["eq", ["s", 0], ["s", 0]], ["c", None]]],
            ["r", 0],
        ],
    )
    semantics.prepare(audit)
    semantics.run(audit)
    assert audit.metrics["semantic_executable"]["t1"]
    assert not audit.metrics["semantic_executable"]["t0"]
    assert not audit.metrics["semantic_executable"]["t2"]
    assert sum(f["check"] == "semantic.null_expression" for f in audit.findings) == 1


def test_native_references_applicability_parameters_temporal_and_cycle(audit):
    states.run(audit)
    native_model(
        audit,
        [
            ["hold", ["gte", ["s", 0], ["p", "limit", 0]], ["p", "window", 3]],
            ["r", 2],
            ["r", 1],
            ["s", 99],
        ],
        applicability={"0": ["s", 88]},
    )
    semantics.prepare(audit)
    semantics.run(audit)
    assert {
        "semantic.reference",
        "semantic.reference_cycle",
        "semantic.parameter_default_unit",
        "semantic.preserved_temporal_inputs",
    } <= checks(audit)


def test_native_types_specialized_and_boolean_null_union(audit):
    replace_field(
        audit,
        valueSchema={"oneOf": [{"type": "boolean"}, {"type": "null"}]},
        unit={"status": "dimensionless", "symbol": "1"},
    )
    states.run(audit)
    native_model(
        audit,
        [
            ["and", ["s", 0], ["c", True]],
            ["and", ["c", "text"], ["c", True]],
            [
                "geometry_relation",
                ["c", {}],
                ["c", {}],
                ["c", "intersects"],
                ["c", True],
            ],
        ],
    )
    semantics.prepare(audit)
    semantics.run(audit)
    assert audit.metrics["semantic_executable"]["t0"]
    assert not audit.metrics["semantic_executable"]["t1"]
    assert "semantic.specialized_inference" in checks(audit)


def test_capability_references_inheritance_and_projection(audit):
    audit.profiles = {
        "bad": record(
            {
                "id": "bad",
                "kind": "source_hierarchy_reuse",
                "baseTypeIds": ["absent"],
                "compatibleTypeIds": [ROOT],
                "fieldIds": ["absent"],
            }
        ),
        "projection": record(
            {
                "id": "projection",
                "kind": "record_subject_adapter",
                "baseTypeIds": [ROOT],
                "compatibleTypeIds": [ROOT],
                "fieldIds": ["f"],
            }
        ),
    }
    entities.check_profiles(audit)
    assert {
        "profile.reference",
        "profile.inheritance",
        "profile.subject_projection",
    } <= checks(audit)


def test_cross_identity_readme_metadata_and_provenance(audit):
    (audit.root / "entity-directory").mkdir()
    (audit.root / "semantic-directory").mkdir()
    (audit.root / "entity-directory/README.md").write_text(
        "999个唯一ID\n| physical | 88 | 3 |\n"
    )
    audit.documents["entity-directory/data/concepts.json"] = {"totals": {"unique": 999}}
    audit.documents["semantic-directory/data/materialized.json"] = {
        "entities": [{"id": "extra"}]
    }
    audit.field_rows = [record(field(sources=["missing"]))]
    audit.sources = {"source": record({"id": "source", "path": "absent-file"})}
    entities.run(audit)
    assert {
        "cross.identity_set",
        "cross.entity_counts",
        "cross.readme_identity_count",
        "cross.readme_directory_count",
        "cross.source_reference",
        "cross.source_path",
    } <= checks(audit)


def test_runtime_summary_non_vacuous_and_bound_writer_proof(audit):
    audit.entities[ROOT] = record(entity(ownFieldIds=["f"]))
    object_semantics(audit, {"op": "eq", "args": [{"field": "f"}, {"field": "f"}]})
    audit.metrics["directories"] = {"physical": [ROOT], "records": ["passive"]}
    audit.entities["passive"] = record(entity("passive"))
    audit.finish()
    report.summarize(audit)
    physical, passive = audit.metrics["runtime_directories"]
    assert physical["all_effective_fields_typed_unit_resolved"] == 1
    assert physical["at_least_one_writer_bound"] == 1
    assert physical["at_least_one_statically_executable_predicate_event"] == 1
    assert passive["all_effective_fields_typed_unit_resolved"] == 0
    assert passive["no_effective_fields"] == 1
    assert states.writer_status({"kind": "bound_source_producer"}) == "declared"
    assert (
        states.writer_status(
            {"kind": "adapter", "bindingStatus": "bound", "producerId": "real"}
        )
        == "bound"
    )


def test_finding_ids_stable_and_evidence_points_to_real_parent(audit):
    audit.documents["fixture.json"] = [{"id": "f"}]
    audit.add(
        "test", "blocker", record({"id": "f"}), "missing writer", pointer="/0/writer"
    )
    audit.finish()
    first = copy.deepcopy(audit.findings)
    assert first[0]["evidence"]["pointer"] == "/0"
    assert first[0]["details"]["requested_pointer"] == "/0/writer"
    other = Audit(audit.root)
    other.documents = audit.documents
    other.add(
        "test", "blocker", record({"id": "f"}), "missing writer", pointer="/0/writer"
    )
    other.finish()
    assert first == other.findings


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def tree(tmp_path):
    root = tmp_path / "source"
    write_json(
        root / "entity-directory/data/concepts.json",
        {"concepts": [entity(ownFieldIds=["f"])]},
    )
    write_json(
        root / "entity-directory/data/navigation.json",
        {
            "homepage": [
                {
                    "key": "physical",
                    "count": 1,
                    "primaryCount": 1,
                    "ids": [ROOT],
                    "topics": [
                        {
                            "name": "root",
                            "count": 1,
                            "ids": [ROOT],
                            "ownIds": [ROOT],
                            "children": [],
                        }
                    ],
                }
            ]
        },
    )
    write_json(root / "semantic-directory/data/definitions/part-001.json", [field()])
    shard = root / "semantic-directory/data/definitions/part-001.json"
    raw = shard.read_bytes()
    write_json(
        root / "semantic-directory/data/definitions.json",
        {
            "recordCount": 1,
            "parts": [
                {
                    "path": "definitions/part-001.json",
                    "count": 1,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            ],
        },
    )
    write_json(root / "semantic-directory/data/relations.json", [relation()])
    write_json(root / "semantic-directory/data/sources.json", [])
    write_json(root / "semantic-directory/data/state.schema.json", {"type": "object"})
    write_json(
        root / "semantic-directory/data/relation.schema.json", {"type": "object"}
    )
    write_json(
        root / "research/original-graph/decoded.json",
        {
            "model": {
                "F": ["f"],
                "TC": [
                    ["target"],
                    [None],
                    ["test"],
                    ["test"],
                    [["eq", ["s", 0], ["s", 0]]],
                    ["native"],
                    ["observed"],
                ],
                "MC": [["number"], ["m"], [None], [None]],
                "K": ["predicate"],
                "Oi": [0],
                "Own": ["adapter"],
                "A": {},
                "X": [],
            }
        },
    )
    return root


def test_cli_deterministic_readonly_and_manifest_integrity(tmp_path):
    root = tree(tmp_path)
    out = tmp_path / "out"
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }
    assert main([str(root), "--out", str(out)]) == 0
    first = (out / "aerograph-audit.json").read_bytes()
    assert main([str(root), "--out", str(out)]) == 0
    assert (out / "aerograph-audit.json").read_bytes() == first
    assert before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }
    data = json.loads(first)
    assert data["git"]["head"] is None
    assert {
        "id",
        "check",
        "severity",
        "artifact",
        "object_id",
        "message",
        "evidence",
        "count",
    } <= data["findings"][0].keys()
    assert "Recommended normalization rules" in (out / "aerograph-audit.md").read_text()
    path = root / "semantic-directory/data/definitions/part-001.json"
    path.write_text(path.read_text() + " ")
    a = Audit(root)
    a.load()
    assert "cross.shard_integrity" in checks(a)


def test_cli_refuses_external_or_source_output(tmp_path):
    root = tree(tmp_path)
    for output in ("/tmp/forbidden-aerograph-output", root / "audit"):
        with pytest.raises(SystemExit) as error:
            main([str(root), "--out", str(output)])
        assert error.value.code == 2


def test_missing_shard_and_invalid_json_do_not_synthesize_records(tmp_path):
    root = tree(tmp_path)
    (root / "semantic-directory/data/definitions/part-001.json").unlink()
    a = Audit(root)
    a.load()
    assert {"input.missing_shard", "cross.field_count"} <= checks(a)
    bad = root / "broken.json"
    bad.write_text("{broken")
    assert a.read("broken.json") is None
    assert "input.invalid_json" in checks(a)


AEROGRAPH = Path("/mnt/data2/weizhiwei/AeroGraph")


@pytest.mark.skipif(not AEROGRAPH.is_dir(), reason="External AeroGraph path is absent")
def test_optional_integration_inventory():
    a = Audit(AEROGRAPH)
    a.load()
    assert ROOT in a.entities
    assert a.fields and a.relations
    assert all(row.artifact.startswith("semantic-directory/") for row in a.field_rows)


def test_member_units_resolved_without_inventing_dimensionless(audit):
    replace_field(
        audit,
        valueSchema={
            "type": "record",
            "nullable": False,
            "members": {
                "length": {"type": "number"},
                "ref": {"type": "ref", "targetTypeIds": [ROOT]},
            },
        },
        unit={
            "status": "member_specific",
            "members": {"length": "m", "ref": "not_applicable"},
        },
    )
    states.run(audit)
    assert audit.metrics["field_status"]["f"]["typed_unit_resolved"]
    assert "field.unit_unresolved" not in checks(audit)
    assert "field.ref_target" not in checks(audit)
    audit.findings = []
    replace_field(
        audit,
        valueSchema={
            "type": "record",
            "nullable": False,
            "members": {
                "value": {"type": "number", "unitField": "unit"},
                "unit": {"type": "string"},
            },
        },
        unit={"status": "exact", "symbol": "member_specific"},
    )
    states.run(audit)
    assert not audit.metrics["field_status"]["f"]["typed_unit_resolved"]
    assert "field.dynamic_unit_context" in checks(audit)


def test_quantifier_variable_scope_and_structured_seconds(audit):
    replace_field(
        audit,
        valueSchema={
            "type": "record",
            "nullable": False,
            "members": {"start": {"type": "number"}, "end": {"type": "number"}},
        },
        unit={"status": "exact", "symbol": "s"},
    )
    object_semantics(
        audit,
        {
            "op": "gte",
            "args": [
                {"field": "f", "path": ["end"]},
                {"field": "f", "path": ["start"]},
            ],
        },
    )
    assert audit.metrics["semantic_executable"]["p"]
    replace_field(
        audit,
        valueSchema={"type": "array", "nullable": False, "items": {"type": "number"}},
        unit={"status": "exact", "symbol": "m"},
    )
    audit.findings = []
    object_semantics(
        audit,
        {
            "op": "all",
            "array": {"field": "f"},
            "var": "x",
            "predicate": {"op": "gte", "args": [{"var": "x"}, {"literal": 0}]},
        },
    )
    assert audit.metrics["semantic_executable"]["p"]


def test_array_numeric_string_path_and_declared_unit(audit):
    replace_field(
        audit,
        valueSchema={
            "type": "array",
            "nullable": False,
            "items": {"type": "number"},
            "maxItems": 2,
        },
        unit={"status": "exact", "symbol": "s"},
    )
    object_semantics(
        audit,
        {
            "op": "gte",
            "args": [{"field": "f", "path": ["1"]}, {"field": "f", "path": [0]}],
        },
    )
    assert audit.metrics["semantic_executable"]["p"]
    assert (
        states.path_schema(
            states.typed_schema(
                audit.fields["f"].data, audit.metrics["resolved_schemas"]["f"]
            ),
            ["1"],
        )["unit"]
        == "s"
    )
    with pytest.raises(ValueError, match="outside"):
        states.path_schema(
            {"type": "array", "items": {"type": "number"}, "maxItems": 2}, ["2"]
        )


def test_native_temporal_window_type_and_specialized_numeric_guard(audit):
    states.run(audit)
    native_model(
        audit,
        [
            ["hold", ["c", True], ["c", "not-seconds"]],
            ["gt", ["rate", ["c", "text"], ["c", 1]], ["c", 0]],
        ],
    )
    semantics.prepare(audit)
    semantics.run(audit)
    assert not any(audit.metrics["semantic_executable"][t] for t in ("t0", "t1"))
    assert "semantic.operator_type_unit" in checks(audit)


def test_pilot_ids_parameters_and_bindings_are_audited(audit):
    audit.documents["semantic-directory/data/pilot.json"] = {
        "pilots": [
            {
                "id": "pilot",
                "predicateId": "p",
                "parameters": {"absent": 0},
                "values": {"absent": False},
                "relationBindings": {"absent": "r"},
                "bindings": [{"entityTypeId": "absent"}],
            }
        ]
    }
    object_semantics(audit, {"literal": True})
    assert {"semantic.pilot_parameter", "semantic.pilot_reference"} <= checks(audit)


def test_nonfinite_source_json_rejected(tmp_path):
    (tmp_path / "bad.json").write_text('{"default": NaN}')
    a = Audit(tmp_path)
    assert a.read("bad.json") is None
    assert "input.invalid_json" in checks(a)


def test_provenance_current_counts_and_config_reference(audit):
    audit.documents["entity-directory/config/hierarchy-test.json"] = {
        "physical": {"baseIds": ["absent"]}
    }
    audit.documents["semantic-directory/data/provenance.json"] = {
        "candidateRestoration": {"counts": {"concepts": 123}},
        "sourceRegistry": {"path": "sources.json", "recordCount": 3, "sha256": "wrong"},
    }
    audit.documents["semantic-directory/data/sources.json"] = []
    audit.inventory["semantic-directory/data/sources.json"] = {
        "bytes": 2,
        "sha256": "actual",
    }
    entities.run(audit)
    assert {
        "navigation.config_reference",
        "cross.restoration_counts",
        "cross.source_registry_integrity",
    } <= checks(audit)


@pytest.mark.parametrize("camel", [False, True])
def test_v2_null_directional_bounds_without_scope_and_missing_key(audit, camel):
    low, high = ("min", "max") if camel else ("minimum", "maximum")
    forward, reverse = (
        ("targetsPerSource", "sourcesPerTarget")
        if camel
        else ("targets_per_source", "sources_per_target")
    )
    card = {forward: {low: 0, high: None}, reverse: {low: 0, high: None}}
    audit.relation_rows = [record(relation(cardinality=card))]
    audit.relations = audit.index(audit.relation_rows, "relation")
    states.run(audit)
    assert "relation.cardinality" not in checks(audit)
    assert (
        sum(f["check"] == "relation.cardinality_dialect" for f in audit.findings) == 1
    )
    card[forward].pop(high)
    audit.findings.clear()
    states.run(audit)
    assert "relation.cardinality" in checks(audit)


def test_v2_dialect_does_not_duplicate_schema_required_bounds(audit):
    audit.documents["semantic-directory/data/relation.schema.json"] = {
        "type": "object",
        "properties": {"cardinality": {"type": "object", "required": ["min", "max"]}},
    }
    audit.relation_rows = [
        record(
            relation(
                cardinality={
                    "targets_per_source": {"minimum": 0, "maximum": None},
                    "sources_per_target": {"minimum": 0, "maximum": None},
                }
            )
        )
    ]
    audit.relations = audit.index(audit.relation_rows, "relation")
    states.run(audit)
    assert "relation.cardinality_dialect" in checks(audit)
    assert "relation.schema_conformance" not in checks(audit)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("count", "1", True),
        ("count/s", "1/s", True),
        ("packet/s", "1/s", True),
        ("packet", "person", False),
        ("packet/s", "person/s", False),
        ("m", "cm", False),
        ("dB", "1", False),
        ("quaternion", "1", False),
        ("measurement_unit", "1", False),
        ("count/s", "Hz", True),
        ("count/s", "m/s", False),
        ("percent", "count", False),
    ],
)
def test_v2_count_compatibility_preserves_scales_and_semantic_identities(
    left, right, expected
):
    from tools.aerograph_audit.units import units_compatible

    assert units_compatible(left, right) is expected


def test_v2_contains_record_does_not_inherit_scalar_container_count(audit):
    from tools.aerograph_audit.units import UnitInferenceError, infer_operator

    items = {
        "type": "record",
        "members": {"high32": {"type": "integer"}, "low32": {"type": "integer"}},
    }
    schema = states.typed_schema(
        field(unit={"symbol": "count", "status": "exact"}),
        {"type": "array", "items": items},
    )
    result = infer_operator("contains", [schema, {**items, "unit": "count"}], [{}, {}])
    assert result["type"] == "boolean"
    left = {
        "type": "array",
        "items": {"type": "record", "members": {"n": {"type": "number", "unit": "m"}}},
    }
    right = {"type": "record", "members": {"n": {"type": "number", "unit": "s"}}}
    with pytest.raises(UnitInferenceError):
        infer_operator("contains", [left, right], [{}, {}])


@pytest.mark.parametrize("symbol", ["quaternion", "m"])
def test_v2_native_and_expanded_norm_component_units(audit, symbol):
    from tools.aerograph_audit.units import infer_operator, units_compatible

    replace_field(
        audit,
        valueSchema={"type": "array", "items": {"type": "number"}},
        unit={"symbol": symbol, "status": "exact"},
    )
    states.run(audit)
    native_model(audit, [["eq", ["norm", ["s", 0]], ["norm", ["s", 0]]]])
    semantics.prepare(audit)
    checker = semantics.SemanticAudit(audit)
    checker.scan()
    row = checker.registry["t0"]
    result = checker.native_type(row, ["norm", ["s", 0]], row.pointer)
    expected = "1" if symbol == "quaternion" else "m"
    assert units_compatible(result["unit"], expected)
    expanded = infer_operator(
        "vector_norm",
        [
            states.typed_schema(
                audit.fields["f"].data, audit.metrics["resolved_schemas"]["f"]
            )
        ],
        [{}],
    )
    assert units_compatible(expanded["unit"], expected)


@pytest.mark.parametrize("key", ["members", "memberUnits"])
def test_v2_member_unit_keys(audit, key):
    replace_field(
        audit,
        valueSchema={"type": "record", "members": {"rateScale": {"type": "number"}}},
        unit={"status": "structured", key: {"rateScale": "1"}},
    )
    states.run(audit)
    assert audit.metrics["field_status"]["f"]["typed_unit_resolved"]
    assert "field.unit_unresolved" not in checks(audit)


def test_v2_identity_integer_exception_is_contextual(audit):
    schema = {
        "type": "record",
        "x-referenceSemantics": "versioned InstanceRef components",
        "properties": {
            "epoch": {"type": "integer"},
            "generation": {"type": "integer"},
            "id": {"type": "string"},
        },
    }
    replace_field(audit, valueSchema=schema, unit={"status": "not_applicable"})
    states.run(audit)
    assert "field.unit_unresolved" not in checks(audit)
    assert "field.numeric_unit_not_applicable" not in checks(audit)
    assert audit.metrics["field_status"]["f"]["typed_unit_resolved"]
    # An unrelated measurement with a similarly named member is not identity.
    schema.pop("x-referenceSemantics")
    audit.findings.clear()
    states.run(audit)
    assert "field.numeric_unit_not_applicable" in checks(audit)
    assert not audit.metrics["field_status"]["f"]["typed_unit_resolved"]


def test_v2_numeric_not_applicable_emits_one_severity(audit):
    replace_field(
        audit,
        valueSchema={
            "type": "record",
            "members": {"values": {"type": "array", "items": {"type": "number"}}},
        },
        unit={"status": "not_applicable"},
    )
    states.run(audit)
    assert "field.numeric_unit_not_applicable" in checks(audit)
    assert "field.unit_unresolved" not in checks(audit)


def test_v2_dynamic_sibling_quantity_unit_does_not_excuse_other_missing_units(audit):
    schema = {
        "type": "record",
        "members": {
            "value": {"type": "number"},
            "unit": {"type": "enum", "values": ["m", "s"]},
        },
    }
    replace_field(
        audit,
        valueSchema=schema,
        unit={"status": "exact", "symbol": "mixed:unit member"},
    )
    states.run(audit)
    assert "field.dynamic_unit_context" in checks(audit)
    assert "field.unit_unresolved" not in checks(audit)
    schema["members"]["uncalibrated"] = {"type": "number"}
    audit.findings.clear()
    states.run(audit)
    assert "field.unit_unresolved" in checks(audit)


@pytest.mark.parametrize(
    "extra",
    [
        {
            "bindingPolicy": (
                "valid time, available time and source/clock explicitly bound"
            )
        },
        {
            "valueSchema": {
                "type": "record",
                "members": {
                    "clockRef": {"type": "ref", "targetClass": ROOT},
                    "startSeconds": {"type": "number", "unit": "s"},
                },
            }
        },
        {"writer": {"description": "绑定采样时钟"}},
        {"lifetime": "versioned-configuration"},
    ],
)
def test_v2_temporal_dialects(audit, extra):
    replace_field(audit, time=None, **extra)
    states.run(audit)
    assert "field.time_missing" not in checks(audit)
    assert "field.time_dialect" in checks(audit)


@pytest.mark.parametrize(
    "role",
    [
        "identity",
        "config",
        "configuration",
        "spec",
        "specification",
        "identity_or_configuration",
        "metadata",
    ],
)
def test_v2_static_role_time_exemptions(audit, role):
    replace_field(audit, role=role, time=None, lifetime=None)
    states.run(audit)
    assert "field.time_missing" not in checks(audit)


def test_v2_observation_without_clock_does_not_get_lifetime_fallback(audit):
    replace_field(audit, time=None, lifetime="per-observation")
    states.run(audit)
    assert "field.time_missing" in checks(audit)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "items": {"type": "string"}},
        {"type": "array", "items": {"type": "number"}},
    ],
)
def test_v2_frame_not_applicable_for_label_and_joint_arrays(audit, schema):
    replace_field(
        audit,
        propertyPath="joint.positions",
        valueSchema=schema,
        frame={
            "status": "not_applicable",
            "reason": "internal joint positions, not external coordinates",
        },
    )
    states.run(audit)
    assert "field.frame_missing" not in checks(audit)


def test_v2_explicit_spatial_vector_cannot_hide_missing_frame_in_prose(audit):
    replace_field(
        audit,
        valueSchema={"type": "vector", "shape": [3], "items": {"type": "number"}},
        frame={"status": "not_applicable", "notes": "not a coordinate"},
    )
    states.run(audit)
    assert "field.frame_missing" in checks(audit)


def test_v2_enum_vocabulary_status_in_evidence(audit):
    replace_field(
        audit,
        valueSchema={
            "type": "enum",
            "values": [],
            "vocabularyStatus": "source_enum_values_not_supplied",
            "closed": False,
        },
    )
    states.run(audit)
    finding = next(f for f in audit.findings if f["check"] == "field.empty_enum")
    assert "source_enum_values_not_supplied" in finding["message"]
    assert finding["contract_severity"] == "blocker"


@pytest.mark.parametrize(
    "extra",
    [
        {"id": "proposal:f"},
        {"reviewStatus": "proposed"},
        {"integrationDisposition": "quarantined"},
        {"acceptedAsCompleteContract": False},
    ],
)
def test_v2_gates_preserve_contract_severity(audit, extra):
    row = record(field(reviewStatus="reviewed") | extra)
    audit.add("field.test", "blocker", row, "bad contract")
    finding = audit.findings[-1]
    assert finding["severity"] == "info"
    assert finding["contract_severity"] == "blocker"
    assert finding["gated"]


def test_v2_archived_null_caps_severity_but_stays_unexecutable(audit):
    states.run(audit)
    native_model(audit, [["c", None]])
    semantics.prepare(audit)
    semantics.run(audit)
    finding = next(
        f for f in audit.findings if f["check"] == "semantic.null_expression"
    )
    assert finding["severity"] == "major"
    assert finding["preserved_source"]
    assert not audit.metrics["semantic_executable"]["t0"]
    assert "README" in finding["message"]


def test_v2_clock_declarations_follow_native_references(audit):
    states.run(audit)
    native_model(audit, [["eq", ["s", 0], ["s", 0]], ["hold", ["r", 0], ["c", 1]]])
    semantics.prepare(audit)
    semantics.run(audit)
    assert "semantic.temporal_clock_binding" not in checks(audit)
    assert "semantic.preserved_temporal_inputs" in checks(audit)
    audit.fields["f"].data["time"] = {}
    audit.findings.clear()
    semantics.run(audit)
    assert "semantic.temporal_clock_binding" in checks(audit)


def test_v2_native_parameters_group_per_name_and_retain_occurrences(audit):
    states.run(audit)
    native_model(
        audit,
        [
            [
                "and",
                ["gt", ["s", 0], ["p", "limit", 3]],
                ["lt", ["s", 0], ["p", "limit", 3]],
            ]
        ],
    )
    semantics.prepare(audit)
    semantics.run(audit)
    findings = [
        f for f in audit.findings if f["check"] == "semantic.parameter_default_unit"
    ]
    assert len(findings) == 1
    assert findings[0]["count"] == 2
    assert len(findings[0]["details"]["pointers"]) == 2
    assert findings[0]["details"]["suggested_units"] == ["m"]
    assert findings[0]["severity"] == "minor"


def test_v2_selection_closure_and_unselected_gated_rows(audit):
    from tools.aerograph_audit import selection

    audit.entities[ROOT] = record(entity(ownFieldIds=["f"]))
    audit.entities["child"] = record(entity("child", ROOT))
    audit.entities["other"] = record(entity("other"))
    audit.add("field.test", "blocker", audit.fields["f"], "selected inherited failure")
    outside = record(field("outside", "other"))
    audit.add("field.test", "blocker", outside, "unselected candidate failure")
    audit.add(
        "semantic.test",
        "blocker",
        record({"id": "dependency"}),
        "contract dependency failure",
    )
    audit.metrics["semantic_inputs"] = {
        "contract": {
            "fields": ["f", "support"],
            "relations": [],
            "dependencies": ["dependency"],
        }
    }
    audit.rules["contract"] = record({"id": "contract"})
    audit.rules["dependency"] = record({"id": "dependency"})
    audit.finish()
    selection.summarize(audit, ("child", ROOT))
    chosen = audit.metrics["selection"]
    assert chosen["blocker_records"] == 2
    assert len(chosen["slices"]["child"]["blockers"]) == 2
    assert "support" in chosen["slices"]["child"]["additional_contract_field_ids"]
    assert all(f["object_id"] != "outside" for f in chosen["blockers"])
    assert (
        next(f for f in chosen["blockers"] if f["object_id"] == "f")["corpus_severity"]
        == "info"
    )
    with pytest.raises(ValueError, match="do not exist"):
        selection.summarize(audit, ("missing",))


def test_v2_git_identity_reads_metadata_without_commands(tmp_path, monkeypatch):
    import subprocess

    metadata = tmp_path / ".git"
    metadata.mkdir()
    (metadata / "HEAD").write_text("ref: refs/heads/main\n")
    (metadata / "packed-refs").write_text("abc123 refs/heads/main\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("No Git commands authorized")

    monkeypatch.setattr(subprocess, "run", forbidden)
    result = Audit(tmp_path).git_state()
    assert result["head"] == "abc123"
    assert result["dirty"] is None


def test_v2_count_compatibility_does_not_bridge_two_different_counts():
    from tools.aerograph_audit.units import UnitInferenceError, infer_operator

    schemas = [{"type": "number", "unit": u} for u in ("1", "packet", "person")]
    with pytest.raises(UnitInferenceError):
        infer_operator("add", schemas, [{}, {}, {}])


def test_v2_cli_selected_exit_policy_and_corpus_invariance(tmp_path):
    root = tree(tmp_path)
    path = root / "semantic-directory/data/definitions/part-001.json"
    rows = json.loads(path.read_text())
    rows[0]["unit"] = {"status": "unresolved", "symbol": None}
    path.write_text(json.dumps(rows))
    descriptor = root / "semantic-directory/data/definitions.json"
    manifest = json.loads(descriptor.read_text())
    manifest["parts"][0]["bytes"] = path.stat().st_size
    manifest["parts"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    descriptor.write_text(json.dumps(manifest))
    out = tmp_path / "selected-out"
    assert main([str(root), "--out", str(out), "--fail-on-blocker"]) == 0
    before = json.loads((out / "aerograph-audit.json").read_text())["findings"]
    assert (
        main([str(root), "--out", str(out), "--select", ROOT, "--fail-on-blocker"]) == 1
    )
    after = json.loads((out / "aerograph-audit.json").read_text())
    assert after["findings"] == before
    assert after["metrics"]["selection"]["blocker_records"] > 0
    with pytest.raises(SystemExit) as error:
        main([str(root), "--out", str(out), "--select", "absent"])
    assert error.value.code == 2


def test_v2_identity_wrapper_without_sampling_time(audit):
    replace_field(
        audit,
        role=None,
        time=None,
        valueSchema={
            "type": "record",
            "members": {
                "sourceToken": {"type": "string"},
                "instanceRef": {
                    "type": "ref",
                    "targetClass": ROOT,
                    "identityComponents": [
                        "runId",
                        "epoch",
                        "id",
                        "generation",
                        "refType",
                    ],
                },
            },
        },
    )
    states.run(audit)
    assert "field.time_missing" not in checks(audit)


def test_v2_incident_seconds_require_exact_source_evidence(audit):
    schema = {
        "type": "record",
        "members": {"startS": {"type": "number"}, "endS": {"type": "number"}},
    }
    replace_field(
        audit,
        id="oo:human_urban.Incident.occurrenceTimeEvidence",
        valueSchema=schema,
        unit={"status": "not_applicable"},
        meaning="所有秒值使用明确来源时钟。",
    )
    states.run(audit)
    identity = "oo:human_urban.Incident.occurrenceTimeEvidence"
    annotated = states.typed_schema(audit.fields[identity].data, schema)
    assert annotated["members"]["startS"]["unit"] == "s"
    assert (
        annotated["members"]["startS"]["unitBasis"]["quotation"]
        == "所有秒值使用明确来源时钟"
    )
    audit.fields[identity].data["meaning"] = "Start and end of interval"
    audit.findings.clear()
    states.run(audit)
    assert "field.numeric_unit_not_applicable" in checks(audit)


def test_v2_nested_count_arithmetic_retains_identity():
    from tools.aerograph_audit.units import UnitInferenceError, infer_operator

    schemas = [{"type": "number", "unit": u} for u in ("1", "packet")]
    inner = infer_operator("add", schemas, [{}, {}])
    assert inner["unit"] == "packet"
    with pytest.raises(UnitInferenceError):
        infer_operator("add", [inner, {"type": "number", "unit": "person"}], [{}, {}])


def test_v2_archival_structure_severity_cap_does_not_mask_input_failure(audit):
    path = "research/original-graph/decoded.json"
    audit.add("semantic.original_columns", "blocker", path, "bad historic columns")
    audit.add("input.invalid_json", "blocker", path, "malformed JSON")
    assert audit.findings[-2]["severity"] == "major"
    assert audit.findings[-2]["preserved_source"]
    assert audit.findings[-1]["severity"] == "blocker"


def test_v2_shared_support_field_does_not_select_unrelated_typed_contract(audit):
    from tools.aerograph_audit import selection

    audit.entities[ROOT] = record(entity(ownFieldIds=["f"]))
    audit.entities["child"] = record(entity("child", ROOT))
    audit.entities["other"] = record(entity("other"))
    audit.rules["unrelated"] = record(
        {
            "id": "unrelated",
            "entityTypeIds": ["other"],
            "roles": [{"typeIds": ["other"]}],
        }
    )
    audit.metrics["semantic_inputs"] = {
        "unrelated": {"fields": ["f"], "dependencies": []}
    }
    audit.add("semantic.test", "blocker", audit.rules["unrelated"], "unrelated failure")
    audit.finish()
    selection.summarize(audit, ("child",))
    assert audit.metrics["selection"]["blocker_records"] == 0
    assert (
        "unrelated" not in audit.metrics["selection"]["slices"]["child"]["contract_ids"]
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"bindingPolicy": "source/clock explicitly bound"},
        {
            "valueSchema": {
                "type": "record",
                "members": {"playbackClockRef": {"type": "ref", "targetClass": ROOT}},
            }
        },
    ],
)
def test_v2_clock_declaration_dialects_apply_with_existing_time(audit, extra):
    replace_field(audit, time={"kind": "configuration_validity"}, **extra)
    states.run(audit)
    assert "field.clock_missing" not in checks(audit)
    assert semantics.SemanticAudit.field_has_clock(audit.fields["f"].data)
