"""I3: immutable portable schemas and explicit field/lifecycle authority."""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aerokernel import (
    BindingManifest,
    BindingRule,
    EntityRef,
    ExactBinding,
    FieldDescriptor,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    TypeDescriptor,
)


def registry(fields=(), messages=(), schemas=None):
    return MemoryRegistry(
        (
            TypeDescriptor("Root", abstract=True),
            TypeDescriptor("Leaf", ("Root",)),
            TypeDescriptor("Other"),
        ),
        fields,
        messages,
        schemas,
    )


def test_inheritance_digest_metadata_and_immutable_descriptors():
    schema = {"type": "integer"}
    metadata = {"unit": "opaque", "frame": {"origin": "opaque"}, "role": "truth"}
    field = FieldDescriptor("f", "Root", schema, metadata)
    r = registry((field,))
    schema["type"] = "string"
    metadata["frame"]["origin"] = "changed"
    assert r.is_a("Leaf", "Root") and r.is_a("Leaf", "Leaf")
    assert not r.is_a("Other", "Root")
    assert (
        field.schema["type"] == "integer"
        and field.metadata["frame"]["origin"] == "opaque"
    )
    with pytest.raises(TypeError):
        field.schema["type"] = "string"
    with pytest.raises(AttributeError):
        r.revision = "changed"
    assert MemoryRegistry.from_data(r.to_data()).digest == r.digest
    assert MemoryRegistry(tuple(reversed(r.types)), r.fields).digest == r.digest
    for fn in (
        lambda: r.is_a("missing", "Root"),
        lambda: r.field("missing"),
        lambda: r.message("missing"),
    ):
        with pytest.raises(KernelError):
            fn()
    for types in (
        (TypeDescriptor("A", ("B",)), TypeDescriptor("B", ("A",))),
        (TypeDescriptor("A", ("missing",)),),
        (TypeDescriptor("A"), TypeDescriptor("A")),
    ):
        with pytest.raises(KernelError):
            MemoryRegistry(types)


SCHEMAS = [
    ({"type": "integer", "minimum": 0, "maximum": 3}, [0, 3], [True, 1.0, -1, 4]),
    (
        {"type": "number", "minimum": 0.0, "maximum": 3.0},
        [0.0, 3.0],
        [0, True, -1.0, 4.0],
    ),
    ({"type": "boolean"}, [False, True], [0, 1, None]),
    ({"type": "string", "min_length": 1, "max_length": 2}, ["a", "é"], ["", "abc", 1]),
    ({"type": "null"}, [None], [False, 0]),
    ({"type": "integer", "nullable": True}, [None, 1], [False, "1"]),
    ({"type": "integer", "enum": [1, 2]}, [1, 2], [True, 1.0, 3]),
    (
        {
            "type": "array",
            "items": {"type": "integer"},
            "min_length": 1,
            "max_length": 2,
        },
        [[1], [1, 2]],
        [[], [1, 2, 3], [True], (1,)],
    ),
    (
        {"type": "vector", "items": {"type": "number"}, "length": 2},
        [[1.0, 2.0]],
        [[1.0], [1, 2]],
    ),
    (
        {"type": "matrix", "items": {"type": "integer"}, "rows": 2, "columns": 1},
        [[[1], [2]]],
        [[[1]], [[1, 2], [3, 4]], [[1], True]],
    ),
    (
        {
            "type": "record",
            "members": {"x": {"type": "integer"}},
            "required": ["x"],
            "extra": False,
        },
        [{"x": 1}],
        [{}, {"x": 1, "y": 2}, {"x": True}],
    ),
    (
        {"type": "record", "members": {}, "required": [], "extra": True},
        [{"x": [1]}, {}],
        [(1,)],
    ),
    (
        {
            "type": "union",
            "discriminator": "$case",
            "cases": {"a": {"type": "integer"}, "b": {"type": "string"}},
        },
        [{"$case": "a", "value": 1}, {"$case": "b", "value": "x"}],
        [
            {"$case": "a", "value": True},
            {"$case": "unknown", "value": 1},
            {"value": 1},
            {"$case": "a", "value": 1, "extra": 3},
        ],
    ),
]


@pytest.mark.parametrize("schema,valid,invalid", SCHEMAS)
def test_portable_subset(schema, valid, invalid):
    r = registry((FieldDescriptor("f", "Root", schema),))
    for value in valid:
        r.validate(r.field("f").schema, value)
    for value in invalid:
        with pytest.raises(KernelError):
            r.validate(r.field("f").schema, value)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "custom"},
        {"type": "integer", "pattern": "ignored"},
        {"type": "boolean", "nullable": 0},
        {"type": "integer", "minimum": True},
        {"type": "integer", "minimum": 3, "maximum": 2},
        {"type": "string", "min_length": -1},
        {"type": "string", "min_length": 3, "max_length": 2},
        {"type": "array"},
        {"type": "vector", "items": {"type": "integer"}},
        {"type": "matrix", "items": {"type": "integer"}, "rows": 1, "columns": True},
        {"type": "record", "members": {}},
        {"type": "record", "members": {}, "required": ["x"], "extra": False},
        {
            "type": "record",
            "members": {"x": {"type": "integer"}},
            "required": ["x", "x"],
            "extra": False,
        },
        {"type": "union", "discriminator": "type", "cases": {"a": {"type": "integer"}}},
        {"type": "integer", "enum": [1, 1]},
        {"type": "integer", "enum": []},
        {"type": "integer", "enum": [True]},
        {"schema_ref": "missing"},
        {"type": "ref", "target_type": "missing"},
    ],
)
def test_unsupported_or_malformed_selected_constraints_fail_binding(schema):
    with pytest.raises(KernelError):
        registry((FieldDescriptor("f", "Root", schema),))


def test_reference_wire_visibility_and_schema_references():
    r = registry(
        (FieldDescriptor("ref", "Root", {"type": "ref", "target_type": "Root"}),),
        schemas={"n": {"type": "integer"}},
    )
    seen = []
    ref = EntityRef("r", "e", "known", 0, "Leaf")
    r.validate(r.field("ref").schema, {"$ref": ref.to_data()}, seen.append)
    assert seen == [ref]
    for value in (
        {"$ref": replace(ref, type_id="Other").to_data()},
        {"$ref": {}},
        {"$ref": ref.to_data(), "extra": True},
    ):
        with pytest.raises(KernelError):
            r.validate(r.field("ref").schema, value)
    r.validate("n", 10**100)
    with pytest.raises(KernelError):
        r.validate("missing", 1)
    with pytest.raises(KernelError):
        registry(
            (FieldDescriptor("f", "Root", {"schema_ref": "n", "nullable": True}),),
            schemas={"n": {"type": "integer"}},
        )
    with pytest.raises(KernelError):
        registry(schemas={"n": {"schema_ref": "n"}})
    with pytest.raises(KernelError):
        MemoryRegistry.from_data({})
    with pytest.raises(KernelError):
        MessageDescriptor("event", "event", {"type": "integer"}, cancel_support=True)


@given(st.integers(-100, 100), st.integers(-100, 100))
def test_writer_priority_exact_override_and_ties(a, b):
    r = registry((FieldDescriptor("f", "Root", {"type": "integer"}),))
    ref = EntityRef("r", "e", "thing", 0, "Leaf")
    parts = {p: Partition(p, p, produces=("f",), lifecycle=True) for p in ("a", "b")}
    m = BindingManifest(
        "r",
        "e",
        (ref,),
        rules=(
            BindingRule("a", "Root", ("f",), priority=a),
            BindingRule("b", "Leaf", ("f",), priority=b),
        ),
        lifecycle=(LifecycleRule("a", "Root"),),
    )
    if a == b:
        with pytest.raises(KernelError):
            m.resolve(ref, r, parts)
    else:
        assert m.resolve(ref, r, parts) == {"f": "a" if a > b else "b"}
    exact = replace(m, exact=(ExactBinding(ref, "f", "a"),))
    exact.validate(r, parts)
    assert exact.resolve(ref, r, parts) == {"f": "a"}
    assert BindingManifest.from_data(exact.to_data()).to_data() == exact.to_data()
    assert m.controller(ref, r, parts) == "a"
    with pytest.raises(KernelError):
        replace(exact, exact=exact.exact * 2).resolve(ref, r, parts)
    with pytest.raises(KernelError):
        m.controller(replace(ref, run_id="foreign"), r, parts)
    with pytest.raises(KernelError):
        replace(m, lifecycle=()).controller(ref, r, parts)
    with pytest.raises(KernelError):
        replace(
            m, lifecycle=(LifecycleRule("a", "Root"), LifecycleRule("b", "Leaf"))
        ).controller(ref, r, parts)
    with pytest.raises(KernelError):
        exact.resolve(ref, r, {"a": replace(parts["a"], produces=()), "b": parts["b"]})


def test_manifest_invalid_domains_and_cohort_merge():
    r = registry((FieldDescriptor("f", "Root", {"type": "integer"}),))
    ref = EntityRef("r", "e", "thing", 0, "Leaf")
    parts = {
        p: Partition(p, p, produces=("f",), lifecycle=True) for p in ("a", "b", "c")
    }
    m = BindingManifest(
        "r",
        "e",
        (ref,),
        rules=(BindingRule("a", "Root", ("f",)),),
        lifecycle=(LifecycleRule("a", "Root"),),
        cohorts=(("a", "b"), ("b", "c")),
    )
    assert m.cohorts == (("a", "b", "c"),)
    m.validate(r, parts)
    for invalid in (
        replace(m, entities=(ref, ref)),
        replace(m, rules=(BindingRule("missing", "Root", ("f",)),)),
        replace(m, rules=(BindingRule("a", "Root", ()),)),
        replace(m, rules=(BindingRule("a", "Root", ("f",), priority=True),)),
        replace(m, entities=(replace(ref, generation=1),)),
        replace(m, lifecycle_participants={"Root": ("missing",)}),
        replace(m, exact=(ExactBinding(ref, "f", "missing"),)),
    ):
        with pytest.raises(KernelError):
            invalid.validate(r, parts)
    with pytest.raises(KernelError):
        BindingManifest("r", "e", cohorts=(("a", "a"),))
    with pytest.raises(KernelError):
        BindingManifest.from_data({})
    with pytest.raises(KernelError):
        m.validate(r, {**parts, "a": replace(parts["a"], lifecycle=False)})
    with pytest.raises(KernelError):
        replace(m, cohorts=(("missing",),)).validate(r, parts)
