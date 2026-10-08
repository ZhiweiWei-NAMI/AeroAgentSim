"""I1/I14: strict time, portable typed trees, canonical bytes and exclusive RNG."""

from __future__ import annotations

import hashlib
import math
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aerokernel import (
    ClockMapping,
    Cut,
    EntityRef,
    Instant,
    Interval,
    KernelError,
    ResourceBudget,
    ResourceLimit,
    Stamp,
    canonical_json,
    freeze,
    normalize,
    parse_json,
    thaw,
    typed_equal,
)
from aerokernel.ids import ItemRef, LocalCause, message_id
from aerokernel.rng import RNGStreams, derive_seed
from aerokernel.time import cut_data, cut_from, instant_data, instant_from


@given(
    st.integers(0, 10**100),
    st.integers(0, 10**100),
    st.integers(0, 100),
    st.integers(0, 100),
)
def test_instant_order_is_integer_lexicographic(a, b, m, n):
    assert (Instant(a, m) < Instant(b, n)) == ((a, m) < (b, n))
    assert instant_from(instant_data(Instant(a, m))) == Instant(a, m)
    cut = Cut(a, Instant(b, m))
    assert cut_from(cut_data(cut)) == cut


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Instant(True),
        lambda: Instant(-1),
        lambda: Instant(0, False),
        lambda: Cut(True, Instant(0)),
        lambda: Cut(0, 0),
        lambda: Interval(Instant(1), Instant(1)),
        lambda: Interval(None, None),
        lambda: Stamp("clock", 0, True, "map"),
        lambda: ClockMapping("map", "clock", p=0),
        lambda: ClockMapping("map", "clock", rounding="guess"),
        lambda: EntityRef("r", "e", "x", False, "T"),
        lambda: EntityRef("r", "e", "\ud800", 0, "T"),
        lambda: ItemRef(-1, 0),
        lambda: LocalCause(True),
    ],
)
def test_invalid_coordinates(factory):
    with pytest.raises(KernelError):
        factory()


@given(
    st.integers(-(10**15), 10**15),
    st.integers(1, 10000),
    st.integers(1, 1000),
    st.integers(1, 1000),
    st.integers(-1000, 1000),
)
def test_rational_rounding_is_explicit(n, d, p, q, offset):
    value = offset + Fraction(p, q) * Fraction(n, d)
    stamp = Stamp("c", n, d, "m")
    assert ClockMapping("m", "c", offset, p, q, "floor").map(stamp) == math.floor(value)
    assert ClockMapping("m", "c", offset, p, q, "ceil").map(stamp) == math.ceil(value)
    assert ClockMapping("m", "c", offset, p, q, "nearest_ties_even").map(
        stamp
    ) == round(value)
    exact = ClockMapping("m", "c", offset, p, q, "exact")
    if value.denominator == 1:
        assert exact.map(stamp) == value.numerator
    else:
        with pytest.raises(KernelError):
            exact.map(stamp)
    with pytest.raises(KernelError):
        exact.map(Stamp("other", n, d, "m"))


TREE = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(-(10**100), 10**100),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=30),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(alphabet="abcé", max_size=8), children, max_size=4),
    ),
    max_leaves=20,
)


@given(TREE)
def test_value_roundtrip_is_lossless_and_freeze_detaches(tree):
    frozen = freeze(tree)
    assert typed_equal(tree, thaw(frozen))
    assert typed_equal(tree, parse_json(canonical_json(tree)))
    assert canonical_json(tree) == canonical_json(thaw(frozen))


def test_nested_immutability_and_typed_signed_zero():
    value = {"a": [{"x": -0.0}], "b": 1}
    frozen = freeze(value)
    value["a"][0]["x"] = 99
    assert thaw(frozen) == {"a": [{"x": 0.0}], "b": 1}
    with pytest.raises(TypeError):
        frozen["b"] = 3
    assert not typed_equal(True, 1)
    assert not typed_equal(1, 1.0)
    assert typed_equal(-0.0, 0.0)
    assert canonical_json({"z": -0.0, "a": 0}) == b'{"a":0,"z":0.0}\n'


@pytest.mark.parametrize(
    "value",
    [(1,), {1: "x"}, b"x", {1, 2}, object(), float("nan"), float("inf"), "\ud800"],
)
def test_nonportable_values_rejected(value):
    with pytest.raises(KernelError):
        normalize(value)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b"NaN",
        b"Infinity",
        b'"\xff"',
        b"\xef\xbb\xbf{}",
        b"{} garbage",
        b'"\\ud800"',
        b"1e999",
    ],
)
def test_bad_json_rejected(raw):
    with pytest.raises(KernelError):
        parse_json(raw)


def test_resource_limits_and_cyclic_inputs():
    for budget in (
        lambda: ResourceBudget(integer_digits=True),
        lambda: ResourceBudget(frame_bytes=0),
        lambda: ResourceBudget(integer_digits=5000),
    ):
        with pytest.raises(ResourceLimit):
            budget()
    with pytest.raises(ResourceLimit):
        canonical_json(10**10, ResourceBudget(integer_digits=10))
    with pytest.raises(ResourceLimit):
        parse_json(b"10000000000", ResourceBudget(integer_digits=10))
    with pytest.raises(ResourceLimit):
        canonical_json("long string", ResourceBudget(frame_bytes=3))
    with pytest.raises(ResourceLimit):
        parse_json(b"{}\n", ResourceBudget(frame_bytes=2))
    with pytest.raises(ResourceLimit):
        normalize([[[1]]], ResourceBudget(nesting_depth=2))
    value = []
    value.append(value)
    with pytest.raises(KernelError):
        normalize(value)
    assert parse_json(canonical_json(10**4095)) == 10**4095
    for value, fn in (([1], instant_from), ({}, cut_from), ({}, EntityRef.from_data)):
        with pytest.raises(KernelError):
            fn(value)
    interval = Interval(Instant(1), None)
    assert interval.contains(Instant(100)) and interval.intersects(
        Instant(0), Instant(2)
    )


@given(
    st.integers(-(10**50), 10**50), st.text(alphabet="abcé", min_size=1, max_size=10)
)
def test_rng_derivation_and_stream_isolation(seed, name):
    expected = int.from_bytes(
        hashlib.sha256(
            canonical_json(["aerokernel.rng/v1", seed, "engine", "partition", name])[
                :-1
            ]
        ).digest(),
        "big",
    )
    assert derive_seed(seed, "engine", "partition", name) == expected
    a = RNGStreams(seed, "engine", "partition", ("one", "two"))
    b = RNGStreams(seed, "engine", "partition", ("two", "one"))
    for _ in range(5):
        a.stream("one").random()
    assert a.stream("two").random() == b.stream("two").random()
    assert a.seeds == b.seeds
    with pytest.raises(KernelError):
        a.stream("undeclared")


def test_identity_wire_and_duplicate_streams():
    ref = EntityRef("r", "e", "id", 10**100, "T")
    assert EntityRef.from_data(ref.to_data()) == ref
    assert (
        message_id("r", "e", "partition", "p", 0)
        == '["aerokernel.message/v1","r","e","partition","p",0]'
    )
    with pytest.raises(KernelError):
        message_id("r", "e", "bad", "p", 0)
    with pytest.raises(KernelError):
        RNGStreams(1, "e", "p", ("x", "x"))
    with pytest.raises(KernelError):
        derive_seed(True, "e", "p", "x")
    with pytest.raises(KernelError):
        RNGStreams(True, "e", "p", ())
