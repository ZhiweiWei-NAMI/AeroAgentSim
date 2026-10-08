"""Selected compiler conformance on small, authored ontology fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from aerokernel.errors import KernelError
from aerokernel.values import canonical_json, thaw
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aeroagentsim.integrations.aerograph import (
    CompileError,
    Policy,
    Selection,
    compile_registry,
    read_snapshot,
    write_snapshot,
)
from aeroagentsim.integrations.aerograph.__main__ import main


def entity(
    id: str, parent: str | None = None, fields: tuple[str, ...] = (), **extra: Any
) -> dict[str, Any]:
    return {
        "id": id,
        "parent": parent,
        "abstract": False,
        "ownFieldIds": list(fields),
        **extra,
    }


def field(
    id: str, owner: str = "t:Base", schema: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "id": id,
        "declaringClass": owner,
        "propertyPath": id,
        "role": "observation",
        "valueSchema": schema or {"type": "number"},
        "unit": {"status": "exact", "symbol": "m"},
        "frame": {"status": "not_applicable"},
        "time": {"clock": "sourceClock", "validity": "explicit interval"},
        "writer": {"description": "Own sensor role"},
        "ownerSubject": {"kind": "class_subject", "classId": owner},
        "roleAliases": ["Own", "Oi"],
        **extra,
    }


def relation(
    id: str, source: str = "t:Base", target: str = "t:Other", **extra: Any
) -> dict[str, Any]:
    return {
        "id": id,
        "sourceClass": source,
        "targetClass": target,
        "cardinality": {"min": 0, "max": "*"},
        "validTime": "explicit source-clock interval",
        **extra,
    }


def ontology(
    root: Path,
    types: list[dict[str, Any]] | None = None,
    fields: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    defs: dict[str, Any] | None = None,
) -> Path:
    docs = {
        "entity-directory/data/concepts.json": {
            "concepts": types
            or [
                entity("t:Base", fields=("f:x",)),
                entity("t:Child", "t:Base"),
                entity("t:Other"),
            ]
        },
        "semantic-directory/data/definitions.json": [field("f:x")]
        if fields is None
        else fields,
        "semantic-directory/data/relations.json": []
        if relations is None
        else relations,
        "semantic-directory/data/schema-definitions.json": {"$defs": defs or {}},
    }
    for name, data in docs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return root


def test_inherited_fields_and_browsing_are_separate(tmp_path: Path) -> None:
    root = ontology(
        tmp_path,
        [
            entity("t:Base", fields=("f:x",)),
            entity("t:Child", "t:Base", navigation={"view": "other"}),
            entity("t:Suggested", "t:Base", parentStatus="suggested"),
            entity("t:Profile", classification="unresolved_profile"),
        ],
    )
    c = compile_registry(root, ["t:Child", "t:Suggested", "t:Profile"])
    assert [f.id for f in c.effective_fields("t:Child")] == ["f:x"]
    assert c.registry.is_a("t:Child", "t:Base")
    assert not c.registry.is_a("t:Suggested", "t:Base")
    assert not c.registry.is_a("t:Profile", "t:Base")
    assert not c.effective_fields("t:Suggested")
    assert any(n["rule"] == "type.suggested_parent_excluded" for n in c.normalizations)
    assert c.producer_hints["f:x"]["authority"] is False
    assert tuple(c.producer_hints["f:x"]["descriptions"]["roleAliases"]) == (
        "Own",
        "Oi",
    )


def test_conflict_requires_explicit_override(tmp_path: Path) -> None:
    types = [entity("t:Base", fields=("f:x",)), entity("t:Child", "t:Base", ("f:y",))]
    fields = [
        field("f:x", propertyPath="length"),
        field("f:y", "t:Child", propertyPath="length"),
    ]
    root = ontology(tmp_path, types, fields)
    with pytest.raises(CompileError, match="conflicting property 'length'"):
        compile_registry(root, ["t:Child"])
    fields[1]["overrides"] = "f:x"
    ontology(root, types, fields)
    c = compile_registry(root, ["t:Child", "t:Base"])
    assert [f.id for f in c.effective_fields("t:Child")] == ["f:y"]
    assert [f.id for f in c.effective_fields("t:Base")] == ["f:x"]
    assert any(n["rule"] == "field.explicit_override" for n in c.normalizations)


def test_multiple_parent_conflicts(tmp_path: Path) -> None:
    types = [
        entity("t:A", fields=("f:a",)),
        entity("t:B", fields=("f:b",)),
        {"id": "t:C", "parents": ["t:A", "t:B"], "abstract": False, "ownFieldIds": []},
    ]
    root = ontology(
        tmp_path,
        types,
        [field("f:a", "t:A", propertyPath="x"), field("f:b", "t:B", propertyPath="x")],
    )
    with pytest.raises(CompileError, match="conflicting property"):
        compile_registry(root, ["t:C"])


def test_invalid_override_fails(tmp_path: Path) -> None:
    root = ontology(tmp_path, fields=[field("f:x", overrides="missing")])
    with pytest.raises(CompileError, match="invalid override"):
        compile_registry(root, ["t:Child"])


@pytest.mark.parametrize(
    "review,expected_reason",
    [
        ({"id": "proposal:x"}, "proposal namespace candidate"),
        ({"reviewStatus": "conflict"}, "review conflict"),
        ({"reviewStatus": "future-status"}, "unsupported review status future-status"),
    ],
)
def test_quarantine_and_explicit_admission(
    tmp_path: Path, review: dict[str, Any], expected_reason: str
) -> None:
    data = {**field("f:x"), **review}
    fid = data["id"]
    root = ontology(tmp_path, [entity("t:Base", fields=(fid,))], [data])
    c = compile_registry(root, ["t:Base"])
    assert not c.registry.fields
    assert c.exclusions[0]["id"] == fid
    assert expected_reason in c.exclusions[0]["reason"]
    admitted = compile_registry(root, ["t:Base"], Policy(admitted_ids=(fid,)))
    assert admitted.registry.field(fid).metadata["research_admitted"] is True
    assert admitted.details["admissions"][0]["research_admitted"] is True
    assert thaw(admitted.registry.field(fid).metadata["raw"]) == data


@pytest.mark.parametrize(
    "review",
    [{"reviewStatus": "proposed"}, {"integrationDisposition": "quarantined"}],
)
def test_proposed_and_marker_definitions_admit_by_default(
    tmp_path: Path, review: dict[str, Any]
) -> None:
    data = {**field("f:x"), **review}
    root = ontology(tmp_path, [entity("t:Base", fields=("f:x",))], [data])
    c = compile_registry(root, ["t:Base"])
    assert [f.id for f in c.registry.fields] == ["f:x"]
    assert c.registry.field("f:x").metadata["reviewStatus"] == data.get("reviewStatus")
    expected_status = data.get("reviewStatus")
    assert (
        c.details["review"]["admitted_by_status"][expected_status or "undeclared"] == 1
    )
    if expected_status == "proposed":
        assert c.statistics["admitted_proposed_definitions"] == 1
        assert c.statistics["admitted_unreviewed_definitions"] == 1
        assert c.statistics["admitted_reviewed_definitions"] == 0
        assert c.details["admissions"]
    # A 'reviewed' source state is admitted without a research-admission flag.
    data["reviewStatus"] = "reviewed"
    ontology(root, [entity("t:Base", fields=("f:x",))], [data])
    reviewed = compile_registry(root, ["t:Base"])
    assert [f.id for f in reviewed.registry.fields] == ["f:x"]
    assert reviewed.statistics["admitted_reviewed_definitions"] == 1
    assert reviewed.statistics["admitted_unreviewed_definitions"] == 0
    assert not reviewed.details["admissions"]


def test_quarantine_relations_and_support_types(tmp_path: Path) -> None:
    root = ontology(tmp_path, relations=[relation("r:x", reviewStatus="proposed")])
    assert len(compile_registry(root, ["t:Child"]).relations) == 1
    assert not compile_registry(
        root, ["t:Child"], Policy(strict_reviewed=True)
    ).relations
    ontology(
        root,
        [
            entity("t:Base", fields=("f:x",)),
            entity("t:Child", "t:Base"),
            entity("proposal:T"),
        ],
        [field("f:x", schema={"type": "ref", "target": "proposal:T"})],
    )
    # An unadmitted proposal candidate target excludes the consuming field.
    without = compile_registry(root, ["t:Child"])
    assert not without.registry.fields
    assert without.exclusions[0]["id"] == "f:x"
    assert "proposal" in without.exclusions[0]["reason"]
    admitted = compile_registry(root, ["t:Child"], Policy(admit_proposed=True))
    assert admitted.registry.is_a("proposal:T", "proposal:T")


def test_strict_reviewed_policy(tmp_path: Path) -> None:
    types = [
        entity("t:Base", fields=("f:reviewed", "f:proposed", "f:undeclared")),
        entity("t:Child", "t:Base"),
    ]
    root = ontology(
        tmp_path,
        types,
        [
            field("f:reviewed", reviewStatus="reviewed"),
            field("f:proposed", reviewStatus="proposed"),
            field("f:undeclared"),
        ],
        [relation("r:reviewed", "t:Child", "t:Base", reviewStatus="reviewed")],
    )
    strict = compile_registry(root, ["t:Child"], Policy(strict_reviewed=True))
    assert [f.id for f in strict.registry.fields] == ["f:reviewed"]
    assert [r["id"] for r in strict.relations] == ["r:reviewed"]
    reasons = {x["id"]: x["reason"] for x in strict.exclusions}
    assert reasons["f:proposed"] == (
        "review status proposed; explicit research admission required"
    )
    assert reasons["f:undeclared"] == (
        "review status undeclared; explicit research admission required"
    )
    assert strict.statistics["admitted_reviewed_definitions"] == 2
    # Undeclared structural types remain usable ancestry under strict review.
    default = compile_registry(root, ["t:Child"])
    assert len(default.registry.fields) == 3
    individual = compile_registry(
        root, ["t:Child"], Policy(strict_reviewed=True, admitted_ids=("f:undeclared",))
    )
    assert {f.id for f in individual.registry.fields} == {"f:reviewed", "f:undeclared"}
    with pytest.raises(ValueError, match="mutually exclusive"):
        Policy(admit_proposed=True, strict_reviewed=True)


def test_proposal_flag_and_individual_admission_for_candidates(
    tmp_path: Path,
) -> None:
    root = ontology(
        tmp_path,
        types=[
            entity("t:Base", fields=("f:x",)),
            entity("t:Other"),
            entity("proposal:T"),
        ],
        relations=[relation("proposal:r:x", "t:Base", "t:Other")],
    )
    flag = compile_registry(root, ["t:Base"], Policy(admit_proposed=True))
    assert flag.registry.is_a("t:Base", "t:Base")
    # proposal:T is not in this slice's ancestry; the relation endpoint is.
    assert [r["id"] for r in flag.relations] == ["proposal:r:x"]
    assert "proposal:T" in {t.id for t in flag.registry.types} or not any(
        x["id"] == "proposal:T" for x in flag.exclusions
    )
    assert any(a["id"] == "proposal:r:x" for a in flag.details["admissions"])
    individual = compile_registry(
        root, ["t:Base"], Policy(admitted_ids=("proposal:r:x",))
    )
    assert [r["id"] for r in individual.relations] == ["proposal:r:x"]
    assert not any(a["id"] == "proposal:T" for a in individual.details["admissions"])
    assert not any(x["id"] == "proposal:r:x" for x in individual.exclusions)


def test_conflict_relation_excluded_by_default_and_individually_admitted(
    tmp_path: Path,
) -> None:
    root = ontology(tmp_path, relations=[relation("r:x", reviewStatus="conflict")])
    blocked = compile_registry(root, ["t:Child"])
    assert not blocked.relations
    assert blocked.exclusions[0]["reason"].startswith("review conflict")
    admitted = compile_registry(root, ["t:Child"], Policy(admitted_ids=("r:x",)))
    assert [r["id"] for r in admitted.relations] == ["r:x"]
    assert admitted.relations[0]["research_admitted"] is True
    assert any(a["id"] == "r:x" for a in admitted.details["admissions"])


def test_relation_endpoint_filtering_and_no_support_field_expansion(
    tmp_path: Path,
) -> None:
    root = ontology(
        tmp_path,
        [
            entity("t:Base", fields=("f:x",)),
            entity("t:Child", "t:Base"),
            entity("t:Other", fields=("unresolved",)),
            entity("t:Else"),
        ],
        relations=[
            relation("r:base"),
            relation("r:child", "t:Other", "t:Child"),
            relation("r:unrelated", "t:Other", "t:Else", cardinality={}),
        ],
    )
    c = compile_registry(root, ["t:Child"])
    assert [r["id"] for r in c.relations] == ["r:base", "r:child"]
    assert [f.id for f in c.registry.fields] == ["f:x"]
    assert "t:Other" in {t.id for t in c.registry.types}
    assert "t:Else" not in {t.id for t in c.registry.types}


@pytest.mark.parametrize(
    "bounds,expected",
    [
        ({"min": 0, "max": "*"}, {"targets_per_source": {"min": 0, "max": None}}),
        (
            {"min": 1, "max": 2, "inverse": {"min": 0, "max": 1}},
            {
                "targets_per_source": {"min": 1, "max": 2},
                "sources_per_target": {"min": 0, "max": 1},
            },
        ),
        (
            {
                "targets_per_source": {"minimum": 1, "maximum": None},
                "sources_per_target": {"minimum": 0, "maximum": 1},
            },
            {
                "targets_per_source": {"min": 1, "max": None},
                "sources_per_target": {"min": 0, "max": 1},
            },
        ),
        (
            {"targetsPerSource": {"min": 0, "max": None}},
            {"targets_per_source": {"min": 0, "max": None}},
        ),
    ],
)
def test_cardinality_dialects(
    tmp_path: Path, bounds: dict[str, Any], expected: dict[str, Any]
) -> None:
    root = ontology(tmp_path, relations=[relation("r:x", cardinality=bounds)])
    c = compile_registry(root, ["t:Child"])
    assert thaw(c.relations[0]["cardinality"]) == expected
    assert any(
        n["rule"] == "relation.directional_cardinality" for n in c.normalizations
    )


@pytest.mark.parametrize(
    "bounds",
    [
        {"min": 0},
        {"min": 0, "max": None},
        {"min": True, "max": 3},
        {"min": 3, "max": 1},
        {"targets_per_source": {"minimum": 0}},
        {"targetsPerSource": {"min": 0, "max": 1}, "min": 0, "max": 1},
    ],
)
def test_invalid_cardinality_does_not_default(
    tmp_path: Path, bounds: dict[str, Any]
) -> None:
    root = ontology(tmp_path, relations=[relation("r:x", cardinality=bounds)])
    with pytest.raises(CompileError, match="[Cc]ardinality"):
        compile_registry(root, ["t:Child"])


def test_role_unit_time_frame_dialects(tmp_path: Path) -> None:
    f = field(
        "f:x",
        schema={"type": "vector", "items": {"type": "number"}, "shape": [3]},
        role="configuration",
        unit={"status": "explicit", "symbol": "byte"},
        frame="BODY_FLU",
        clockRef="source-clock",
        bindingPolicy="source-clock sampling",
        time=None,
        lifetime="versioned configuration",
    )
    root = ontology(tmp_path, fields=[f])
    c = compile_registry(root, ["t:Child"])
    d = c.registry.field("f:x")
    assert d.schema["length"] == 3
    assert d.metadata["role"] == "config"
    assert d.metadata["unit"]["symbol"] == "By"
    assert d.metadata["unit"]["status"] == "exact"
    assert d.metadata["frame"]["declaration"] == "BODY_FLU"
    assert len(d.metadata["temporal_declarations"]) == 3
    assert d.metadata["raw"]["unit"]["symbol"] == "byte"
    assert {n["rule"] for n in c.normalizations} >= {
        "role.vocabulary",
        "unit.alias",
        "unit.explicit",
        "frame.string",
        "time.dialect",
        "schema.shape",
    }


def test_member_units_inline_units_and_dynamic_context(tmp_path: Path) -> None:
    schema = {
        "type": "record",
        "members": {
            "length": {"type": "number"},
            "temperature": {"type": "number", "unit": "Cel"},
            "quantity": {
                "type": "record",
                "members": {"unit": {"type": "string"}, "value": {"type": "number"}},
            },
        },
    }
    root = ontology(
        tmp_path,
        fields=[
            field(
                "f:x",
                schema=schema,
                unit={"status": "member_specific", "memberUnits": {"length": "m"}},
            )
        ],
    )
    c = compile_registry(root, ["t:Child"])
    assert thaw(c.registry.field("f:x").metadata["numeric_units"]) == {
        "length": "m",
        "temperature": "degC",
        "quantity.value": {"dynamic_unit_member": "unit"},
    }


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("Cel", "degC"),
        ("count", "1"),
        ("count/s", "1/s"),
        ("packet", "packet"),
        ("cm", "cm"),
    ],
)
def test_unit_aliases_keep_scale_and_count_identity(
    tmp_path: Path, symbol: str, expected: str
) -> None:
    root = ontology(
        tmp_path, fields=[field("f:x", unit={"status": "exact", "symbol": symbol})]
    )
    assert (
        compile_registry(root, ["t:Child"])
        .registry.field("f:x")
        .metadata["unit"]["symbol"]
        == expected
    )


def test_local_refs_nullable_unions_and_typed_enums(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"$ref": "#/$defs/a~1b~0c"},
            "nullable": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "choice": {"oneOf": [{"type": "integer"}, {"type": "boolean"}]},
            "mixed": {"enum": [1, True, "unknown"]},
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    root = ontology(
        tmp_path,
        fields=[
            field(
                "f:x",
                schema=schema,
                role="metadata",
                unit={"status": "dimensionless", "symbol": "1"},
            )
        ],
        defs={"a/b~c": {"type": "string", "minLength": 1}},
    )
    # This test contains an ordinary numeric union leaf; declare its unit rather
    # than treating the dimensionless container count as leaf quantity evidence.
    data = json.loads((root / "semantic-directory/data/definitions.json").read_text())
    data[0]["unit"] = {
        "status": "member_specific",
        "members": {"choice.$case:0": "1", "mixed.$case:integer": "1"},
    }
    (root / "semantic-directory/data/definitions.json").write_text(json.dumps(data))
    c = compile_registry(root, ["t:Child"])
    d = c.registry.field("f:x")
    assert d.schema["members"]["name"]["min_length"] == 1
    assert d.schema["members"]["nullable"]["nullable"] is True
    assert d.schema["members"]["choice"]["discriminator"] == "$case"
    assert d.schema["members"]["mixed"]["cases"]["integer"]["enum"] == (1,)
    c.registry.validate(d.schema, {"name": "n", "choice": {"$case": "0", "value": 4}})
    with pytest.raises(KernelError):
        c.registry.validate(
            d.schema, {"name": "n", "choice": {"$case": "0", "value": True}}
        )


def test_field_local_definitions_and_reference_dependency(tmp_path: Path) -> None:
    schema = {
        "$ref": "#/$defs/ref",
        "$defs": {
            "ref": {
                "type": "ref",
                "targetClass": "t:Other",
                "identityComponents": ["runId", "id"],
            }
        },
    }
    root = ontology(tmp_path, fields=[field("f:x", schema=schema)])
    c = compile_registry(root, ["t:Child"])
    assert c.registry.field("f:x").schema["target_type"] == "t:Other"
    assert c.registry.is_a("t:Other", "t:Other")


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "pattern": ".*"},
        {"type": "enum", "values": []},
        {"$ref": "#/$defs/missing"},
        {"$ref": "https://example.invalid/schema"},
        {"type": "string", "minimum": 0},
        {"type": "ref"},
        {"anyOf": [{"type": "string"}, {"type": "integer"}], "minimum": 0},
    ],
)
def test_unresolved_schema_fails_with_source(
    tmp_path: Path, schema: dict[str, Any]
) -> None:
    root = ontology(tmp_path, fields=[field("f:x", schema=schema)])
    with pytest.raises(CompileError, match=r"definitions.json#/0"):
        compile_registry(root, ["t:Child"])
    assert not compile_registry(
        root, ["t:Child"], Policy(excluded_ids=("f:x",))
    ).registry.fields


def test_cyclic_local_schema_ref(tmp_path: Path) -> None:
    root = ontology(
        tmp_path,
        fields=[field("f:x", schema={"$ref": "#/$defs/a"})],
        defs={"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}},
    )
    with pytest.raises(CompileError, match="cyclic schema"):
        compile_registry(root, ["t:Child"])


@pytest.mark.parametrize(
    "unit",
    [
        None,
        {"status": "unresolved"},
        {"status": "explicit"},
        {"status": "not_applicable"},
    ],
)
def test_numeric_unit_absence_is_a_blocker(tmp_path: Path, unit: Any) -> None:
    root = ontology(tmp_path, fields=[field("f:x", unit=unit)])
    with pytest.raises(CompileError, match="unit"):
        compile_registry(root, ["t:Child"])


def test_no_fake_unit_for_counted_records(tmp_path: Path) -> None:
    root = ontology(
        tmp_path,
        fields=[
            field(
                "f:x",
                schema={
                    "type": "array",
                    "items": {
                        "type": "record",
                        "members": {"mass": {"type": "number"}},
                    },
                },
                unit="count",
            )
        ],
    )
    with pytest.raises(CompileError, match="no explicit unit"):
        compile_registry(root, ["t:Child"])


def test_identity_integer_requires_declared_context(tmp_path: Path) -> None:
    root = ontology(
        tmp_path,
        fields=[
            field(
                "f:x",
                schema={
                    "type": "record",
                    "x-referenceSemantics": "InstanceRef",
                    "members": {"generation": {"type": "integer"}},
                },
                unit={"status": "not_applicable"},
                role="metadata",
            )
        ],
    )
    assert (
        compile_registry(root, ["t:Child"])
        .registry.field("f:x")
        .metadata["numeric_units"]["generation"]
        == "1"
    )


def test_missing_selected_ids_and_allowlists(tmp_path: Path) -> None:
    root = ontology(tmp_path, relations=[relation("r:x")])
    with pytest.raises(CompileError, match="Unresolved type"):
        compile_registry(root, ["t:Missing"])
    c = compile_registry(
        root, ["t:Child", "t:Missing"], Policy(excluded_ids=("t:Missing",))
    )
    assert c.statistics["selected_types"] == 1
    assert c.exclusions[0]["id"] == "t:Missing"
    empty = compile_registry(root, Selection(("t:Child",), (), ()))
    assert not empty.registry.fields and not empty.relations
    with pytest.raises(CompileError, match="Selected field"):
        compile_registry(root, Selection(("t:Child",), ("absent",)))
    with pytest.raises(CompileError, match="Selected relation"):
        compile_registry(root, Selection(("t:Child",), relation_ids=("absent",)))


def test_missing_inherited_field_is_not_silently_skipped(tmp_path: Path) -> None:
    root = ontology(tmp_path, fields=[])
    with pytest.raises(CompileError, match="Unresolved field 'f:x'"):
        compile_registry(root, ["t:Child"])
    assert not compile_registry(
        root, ["t:Child"], Policy(excluded_ids=("f:x",))
    ).registry.fields


def test_duplicate_definitions_only_block_when_selected(tmp_path: Path) -> None:
    root = ontology(
        tmp_path,
        fields=[field("f:x"), field("bad", "t:Other"), field("bad", "t:Other")],
    )
    assert len(compile_registry(root, ["t:Child"]).registry.fields) == 1
    ontology(root, fields=[field("f:x"), field("f:x")])
    with pytest.raises(CompileError, match="Duplicate field"):
        compile_registry(root, ["t:Child"])


def test_source_strict_json_and_escape_guard(tmp_path: Path) -> None:
    root = ontology(tmp_path)
    (root / "semantic-directory/data/definitions.json").write_text(
        '[{"id":"f:x","id":"f:y"}]'
    )
    with pytest.raises(CompileError, match="duplicate record key"):
        compile_registry(root, ["t:Child"])
    (root / "semantic-directory/data/definitions.json").write_text(
        json.dumps({"parts": [{"path": "../../../outside.json"}]})
    )
    with pytest.raises(CompileError, match="escapes ontology root"):
        compile_registry(root, ["t:Child"])


def test_cycle_and_missing_actual_parent(tmp_path: Path) -> None:
    root = ontology(tmp_path, [entity("t:A", "t:B"), entity("t:B", "t:A")], [])
    with pytest.raises(CompileError, match="Inheritance cycle"):
        compile_registry(root, ["t:A"])
    ontology(root, [entity("t:A", "missing")], [])
    with pytest.raises(CompileError, match="Unresolved type 'missing'"):
        compile_registry(root, ["t:A"])


def test_snapshot_roundtrip_pinned_provenance_and_tampering(tmp_path: Path) -> None:
    root = ontology(tmp_path / "ontology", relations=[relation("r:x")])
    c = compile_registry(root, ["t:Child"])
    path = tmp_path / "registry.snapshot.json"
    write_snapshot(c, path)
    saved = path.read_bytes()
    assert saved == canonical_json(c.to_data())
    descriptor = c.provenance["descriptors"]["fields"]["f:x"]
    assert (
        descriptor["file"] == "semantic-directory/data/definitions.json"
        and descriptor["pointer"] == "/0"
    )
    for name, sha in c.provenance["input_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == sha
    (root / "semantic-directory/data/definitions.json").unlink()
    restored = read_snapshot(path)
    assert restored.digest == c.digest
    assert restored.registry.digest == c.registry.digest
    assert restored.to_data() == c.to_data()
    assert [r["id"] for r in restored.effective_relations("t:Child")] == ["r:x"]
    with pytest.raises(TypeError):
        restored.producer_hints["f:x"]["authority"] = True
    data = json.loads(saved)
    data["details"]["relations"][0]["target_type"] = "changed"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="digest mismatch"):
        read_snapshot(path)


@settings(
    max_examples=20,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    database=None,
    deadline=None,
)
@given(st.permutations(["t:Base", "t:Child", "t:Other"]))
def test_digest_selection_order_invariant(tmp_path: Path, selected: list[str]) -> None:
    root = ontology(tmp_path)
    assert (
        compile_registry(root, selected).digest
        == compile_registry(root, ["t:Other", "t:Child", "t:Base"]).digest
    )


@settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    database=None,
    deadline=None,
)
@given(lo=st.integers(0, 100), width=st.integers(0, 100), unbounded=st.booleans())
def test_cardinality_bound_roundtrip(
    tmp_path: Path, lo: int, width: int, unbounded: bool
) -> None:
    hi = None if unbounded else lo + width
    root = ontology(
        tmp_path,
        relations=[
            relation(
                "r:x",
                cardinality={"targets_per_source": {"minimum": lo, "maximum": hi}},
            )
        ],
    )
    c = compile_registry(root, ["t:Child"])
    path = tmp_path / "snapshot.json"
    c.write_snapshot(path)
    assert read_snapshot(path).digest == c.digest
    assert thaw(c.relations[0]["cardinality"])["targets_per_source"] == {
        "min": lo,
        "max": hi,
    }


def test_manifest_shards(tmp_path: Path) -> None:
    root = ontology(tmp_path)
    path = root / "semantic-directory/data/definitions.json"
    fields = json.loads(path.read_text())
    (path.parent / "part.json").write_text(json.dumps(fields))
    path.write_text(json.dumps({"parts": [{"path": "part.json"}]}))
    c = compile_registry(root, ["t:Child"])
    assert (
        c.provenance["descriptors"]["fields"]["f:x"]["file"]
        == "semantic-directory/data/part.json"
    )


def test_git_metadata_dirty_is_measured_without_git_commands(tmp_path: Path) -> None:
    root = ontology(tmp_path)
    git = root / ".git"
    git.mkdir()
    head = "1" * 40
    (git / "HEAD").write_text(head)
    # One staged blob differs from the worktree. Git commands are unnecessary.
    filename = b"entity-directory/data/concepts.json"
    entry = (
        struct.pack("!10I", 0, 0, 0, 0, 0, 0, 0o100644, 0, 0, 0)
        + bytes(20)
        + struct.pack("!H", len(filename))
        + filename
        + b"\0"
    )
    entry += b"\0" * (-len(entry) % 8)
    (git / "index").write_bytes(struct.pack("!4sII", b"DIRC", 2, 1) + entry)
    c = compile_registry(root, ["t:Child"])
    assert c.provenance["git"]["head"] == head
    assert c.provenance["git"]["dirty"] is True
    assert (
        "entity-directory/data/concepts.json" in c.provenance["git"]["changed_tracked"]
    )


def test_cli_compile_inspect_and_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = ontology(tmp_path / "ontology")
    out, report = tmp_path / "snapshot.json", tmp_path / "report.md"
    assert (
        main(
            [
                "compile",
                "--root",
                str(root),
                "--select",
                "t:Child",
                "--out",
                str(out),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["fields"] == 1
    assert main(["inspect", str(out), "--type", "t:Child"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["fields"][0]["unit"]["symbol"] == "m"
    assert result["fields"][0]["producer_hints"]["authority"] is False
    assert "f:x" in report.read_text() or "fields" in report.read_text()
    with pytest.raises(SystemExit) as exc:
        main(["compile", "--root", str(root), "--select", "missing", "--out", str(out)])
    assert exc.value.code == 2


def test_module_cli_imports_no_legacy_modules(tmp_path: Path) -> None:
    root = ontology(tmp_path / "ontology")
    script = "from aeroagentsim.integrations.aerograph import compile_registry; import sys; assert 'simpy' not in sys.modules; assert not any(x.startswith('aeroagentsim.core') for x in sys.modules)"
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "aeroagentsim.integrations.aerograph",
            "compile",
            "--root",
            str(root),
            "--select",
            "t:Child",
            "--out",
            str(tmp_path / "snapshot.json"),
        ],
        check=True,
        capture_output=True,
    )


def test_nullable_enum_preserves_null_and_unknown(tmp_path: Path) -> None:
    root = ontology(
        tmp_path,
        fields=[
            field(
                "f:x",
                schema={
                    "type": "enum",
                    "values": ["unknown", "known"],
                    "nullable": True,
                },
            )
        ],
    )
    c = compile_registry(root, ["t:Child"])
    schema = c.registry.field("f:x").schema
    c.registry.validate(schema, None)
    c.registry.validate(schema, "unknown")
    with pytest.raises(KernelError):
        c.registry.validate(schema, False)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "values": ["a"]},
        {"type": "string", "oneOf": [{"type": "string"}, {"type": "null"}]},
    ],
)
def test_known_constraints_are_not_silently_dropped(
    tmp_path: Path, schema: dict[str, Any]
) -> None:
    root = ontology(tmp_path, fields=[field("f:x", schema=schema)])
    with pytest.raises(CompileError):
        compile_registry(root, ["t:Child"])


def test_both_member_unit_dialects_merge_and_normalize(tmp_path: Path) -> None:
    root = ontology(
        tmp_path,
        fields=[
            field(
                "f:x",
                schema={
                    "type": "record",
                    "members": {
                        "bytes": {"type": "integer"},
                        "temperature": {"type": "number"},
                    },
                },
                unit={
                    "status": "member_specific",
                    "members": {"bytes": "byte"},
                    "memberUnits": {"temperature": "Cel"},
                },
            )
        ],
    )
    c = compile_registry(root, ["t:Child"])
    assert thaw(c.registry.field("f:x").metadata["numeric_units"]) == {
        "bytes": "By",
        "temperature": "degC",
    }


def test_missing_adopted_inventory_does_not_become_zero_fields(tmp_path: Path) -> None:
    root = ontology(
        tmp_path, types=[{"id": "t:Base", "parent": None, "abstract": False}]
    )
    with pytest.raises(CompileError, match="ownFieldIds"):
        compile_registry(root, ["t:Base"])


def test_no_missing_temporal_or_frame_contracts(tmp_path: Path) -> None:
    root = ontology(tmp_path, fields=[field("f:x", time=None)])
    with pytest.raises(CompileError, match="time/clock"):
        compile_registry(root, ["t:Child"])
    ontology(
        root,
        fields=[
            field(
                "f:x",
                schema={"type": "vector", "items": {"type": "number"}, "shape": [3]},
                frame=None,
            )
        ],
    )
    with pytest.raises(CompileError, match="frame declaration"):
        compile_registry(root, ["t:Child"])


def test_temporal_schema_dialect_retains_acquisition_and_availability(
    tmp_path: Path,
) -> None:
    root = ontology(
        tmp_path,
        fields=[
            field(
                "f:x",
                time=None,
                schema={
                    "type": "record",
                    "members": {
                        "clockRef": {"type": "string"},
                        "acquiredAt": {"type": "number"},
                        "availableAt": {"type": "number"},
                    },
                },
                unit="s",
            )
        ],
    )
    c = compile_registry(root, ["t:Child"])
    paths = [
        tuple(x["path"])
        for x in c.registry.field("f:x").metadata["temporal_declarations"]
    ]
    assert (
        ("acquiredAt",) in paths
        and ("availableAt",) in paths
        and ("clockRef",) in paths
    )


def test_v2_source_time_quotation_is_not_suffix_guessing(tmp_path: Path) -> None:
    fid = "oo:human_urban.Incident.occurrenceTimeEvidence"
    f = field(
        fid,
        schema={"type": "record", "members": {"atS": {"type": "number"}}},
        meaning="所有秒值使用明确来源时钟",
        unit={"status": "not_applicable"},
    )
    root = ontology(tmp_path, [entity("t:Base", fields=(fid,))], [f])
    c = compile_registry(root, ["t:Base"])
    assert c.registry.field(fid).metadata["numeric_units"]["atS"] == "s"
    assert any(n["rule"] == "unit.source_time_quotation" for n in c.normalizations)
    f["meaning"] = "A timestamp name without an explicit source declaration"
    ontology(root, [entity("t:Base", fields=(fid,))], [f])
    with pytest.raises(CompileError, match="no explicit unit"):
        compile_registry(root, ["t:Base"])


@pytest.mark.parametrize(
    "bad", [{"frame": {"status": "unresolved"}}, {"time": {"status": "unresolved"}}]
)
def test_unresolved_frame_and_time_need_real_source_contract(
    tmp_path: Path, bad: dict[str, Any]
) -> None:
    root = ontology(tmp_path, fields=[field("f:x", **bad)])
    with pytest.raises(CompileError, match="unresolved"):
        compile_registry(root, ["t:Child"])
