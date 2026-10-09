"""Literal wire-format cases plus resource, UTF-8 and integer boundary checks."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.wire import MAX_FRAME, PROTOCOL, decode, encode

CASES = json.loads(Path(__file__).with_name("wire_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_literal_contract_cases(case):
    if case["valid"]:
        result = decode(case["frame"].encode("utf-8"))
        assert result["protocol"] == PROTOCOL
        if case["name"] == "id9007199254740993":
            assert result["id"] == 9007199254740993
    else:
        with pytest.raises(ValueError):
            decode(case["frame"].encode("utf-8"))


def envelope(payload=None):
    return {
        "protocol": PROTOCOL,
        "major": 1,
        "minor": 0,
        "id": 1,
        "op": "hello",
        "payload": {} if payload is None else payload,
    }


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\n",
        (
            b'{"protocol":"aeroagentsim.sumo/v1","major":1,"minor":0,"id":1,'
            b'"op":"hello","payload":{"bad":"\\ud800"}}\n'
        ),
        b"{}\r\n",
        b"{}\n{}\n",
    ],
)
def test_encoding_and_multiple_frame_faults(data):
    with pytest.raises(ValueError):
        decode(data)


def test_lf_inclusive_exact_frame_budget():
    base = encode(envelope({"text": ""}))
    value = envelope({"text": "x" * (MAX_FRAME - len(base))})
    frame = encode(value)
    assert len(frame) == MAX_FRAME
    assert decode(frame)["payload"] == value["payload"]
    with pytest.raises(ValueError):
        decode(frame[:-1] + b" \n")
    with pytest.raises(ValueError):
        encode(envelope({"text": value["payload"]["text"] + "x"}))


def test_large_integer_precision_and_decimal_budget():
    value = envelope({"n": int("9" * 4096)})
    assert decode(encode(value)) == value
    raw = (
        b'{"protocol":"aeroagentsim.sumo/v1","major":1,"minor":0,"id":1,'
        b'"op":"hello","payload":{"n":' + b"9" * 4097 + b"}}\n"
    )
    with pytest.raises(ValueError):
        decode(raw)


def test_nesting_budget_ignores_structural_chars_inside_strings():
    assert (
        decode(encode(envelope({"text": "[{" * 1000})))["payload"]["text"]
        == "[{" * 1000
    )
    raw = (
        b'{"protocol":"aeroagentsim.sumo/v1","major":1,"minor":0,"id":1,'
        b'"op":"hello","payload":{"nested":' + b"[" * 129 + b"0" + b"]" * 129 + b"}}\n"
    )
    with pytest.raises(ValueError, match="nesting"):
        decode(raw)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), {"bad": "\udfff"}, {1: "x"}, (1, 2)]
)
def test_outbound_values_fail_without_coercion(value):
    with pytest.raises(ValueError):
        encode(envelope({"value": value}))
