"""Normalized schema closure remains usable across actual RPC and scoped views."""

import pytest

from aerokernel import (
    ABSENT,
    BindingManifest,
    BindingRule,
    Create,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Stamp,
    TypeDescriptor,
)
from aerokernel.rpc import RemoteEngine, _projection, _view
from aerokernel.sdk import SimpleEngine
from aerokernel.state import StateView
from tests.kernel.test_m2_rpc import service


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("kind", ["simple_named", "named", "ref", "nested"])
def test_normalized_named_and_reference_contracts_local_and_remote(remote, kind):
    ref = EntityRef("r", "e", "s", 0, "T")
    schemas = {
        "N": {"schema_ref": "Number"},
        "Number": {"type": "integer"},
        "Link": {"type": "ref", "target_type": "Other"},
        "Nested": {
            "type": "array",
            "items": {
                "type": "union",
                "discriminator": "$case",
                "cases": {"n": {"schema_ref": "N"}, "r": {"schema_ref": "Link"}},
            },
        },
        "Unused": {"type": "boolean"},
    }
    if kind == "simple_named":
        schemas["N"] = {"type": "integer"}
    schema = (
        {"schema_ref": "N"}
        if kind in ("simple_named", "named")
        else {"type": "ref", "target_type": "Other"}
        if kind == "ref"
        else {"schema_ref": "Nested"}
    )
    value = 7 if kind in ("simple_named", "named") else [{"$case": "n", "value": 7}]

    class Owner(SimpleEngine):
        def initialize(self, view):
            ops = (Create(ref),)
            if kind != "ref":
                ops += (
                    FactWrite(
                        (ref, "x"),
                        value,
                        Stamp("canonical", 0, 1, "canonical"),
                        Interval(Instant(0), None),
                    ),
                )
            return ops

    owner = Owner(Partition("owner", "owner", produces=("x",), lifecycle=True))
    reg = MemoryRegistry(
        (
            TypeDescriptor("T"),
            TypeDescriptor("Parent"),
            TypeDescriptor("Other", ("Parent",)),
            TypeDescriptor("Unused"),
        ),
        (FieldDescriptor("x", "T", schema),),
        schemas=schemas,
    )
    client = thread = None
    if remote:
        client, thread, failures = service(engine=owner)
        engine = RemoteEngine(client)
    else:
        engine = owner
    k = Kernel(provenance="full")
    try:
        k.bind(
            reg,
            BindingManifest(
                "r",
                "e",
                (ref,),
                rules=(BindingRule("owner", "T", ("x",)),),
                lifecycle=(LifecycleRule("owner", "T"),),
            ),
            (engine,),
        )
        k.start()
        result = k.view().field((ref, "x"), Instant(0))
        if kind == "ref":
            assert result is ABSENT
        else:
            from aerokernel.values import thaw

            assert thaw(result.value) == value
        projection = _projection(
            StateView(k._store, partition="owner"), owner.partitions
        )
        restored = _view(projection, k.budget)
        assert restored.field((ref, "x"), Instant(0)) == result
        assert "Unused" not in projection["registry"]["schemas"]
        assert "Unused" not in {t["id"] for t in projection["registry"]["types"]}
        if kind in ("ref", "nested"):
            assert {"Other", "Parent"} <= {
                t["id"] for t in projection["registry"]["types"]
            }
    finally:
        k.close()
        if client is not None:
            client.close()
            thread.join(1)
            assert not thread.is_alive() and failures == []


def test_message_result_feedback_schemas_retain_distinct_target_types():
    def schema(name):
        return {"schema_ref": name}

    schemas = {
        name: {"type": "ref", "target_type": name}
        for name in ("Payload", "Result", "Feedback")
    }
    owner = SimpleEngine(Partition("p", "e", commands=("do",)))
    registry = MemoryRegistry(
        tuple(TypeDescriptor(name) for name in schemas),
        messages=(
            MessageDescriptor(
                "do",
                "command",
                schema("Payload"),
                result_schema={"type": "array", "items": schema("Result")},
                feedback_schema=schema("Feedback"),
            ),
        ),
        schemas=schemas,
    )
    k = Kernel(provenance="full")
    try:
        k.bind(registry, BindingManifest("r", "e"), (owner,))
        k.start()
        projection = _projection(StateView(k._store, partition="p"), owner.partitions)
        restored = _view(projection, k.budget)
        assert set(restored._store.registry.schemas) == set(schemas)
        assert {t.id for t in restored._store.registry.types} == set(schemas)
    finally:
        k.close()
