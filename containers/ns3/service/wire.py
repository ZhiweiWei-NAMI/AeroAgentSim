"""Strict bounded UTF-8/LF JSON, preserving integer tokens exactly."""

import json
import math
import re

PROTOCOL = "aeroagentsim.ns3/v1"
MAX_FRAME = 8 * 1024 * 1024
MAX_DEPTH = 128
MAX_DIGITS = 4096
TIMEOUTS = {"hello": 10, "reset": 180, "advance": 30, "command": 30, "close": 20}
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\]]|(?<![\w.])[+-]?\d+')


def string(value, name):
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a nonempty string")
    value.encode("utf-8", errors="strict")
    return value


def integer(value, name, minimum=0, maximum=None):
    if (
        type(value) is not int
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise ValueError(f"{name} must be an integer in its permitted range")
    return value


def number(value, name):
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite number")
    return value


def fields(value, required, optional=()):
    if type(value) is not dict:
        raise ValueError("expected object")
    if set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError(f"expected fields {list(required)}, optional {list(optional)}")
    return value


def pairs(items):
    value = {}
    for key, entry in items:
        if key in value:
            raise ValueError(f"duplicate JSON key {key}")
        value[key] = entry
    return value


def nonfinite(value):
    raise ValueError(f"nonfinite JSON number {value}")


def parse_int(token):
    if len(token.lstrip("-")) > MAX_DIGITS:
        raise ValueError("integer token exceeds 4096 digits")
    return int(token)


def check_tree(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("nesting exceeds 128 levels")
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int:
        if len(str(value).lstrip("-")) > MAX_DIGITS:
            raise ValueError("integer exceeds 4096 digits")
    elif kind is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite JSON number")
    elif kind is str:
        value.encode("utf-8", errors="strict")
    elif kind is list:
        for child in value:
            check_tree(child, depth + 1)
    elif kind is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("object key must be string")
            key.encode("utf-8", errors="strict")
            check_tree(child, depth + 1)
    else:
        raise ValueError(f"invalid JSON value type {kind.__name__}")


def decode(data):
    if type(data) is not bytes or len(data) > MAX_FRAME or not data.endswith(b"\n"):
        raise ValueError("incomplete or oversized LF frame")
    if b"\n" in data[:-1] or b"\r" in data:
        raise ValueError("frame must contain exactly one LF and no CR")
    text = data.decode("utf-8", errors="strict")
    depth = 0
    for match in TOKEN.finditer(text):
        token = match.group()
        if token in ("[", "{"):
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError("nesting exceeds 128 levels")
        elif token in ("]", "}"):
            depth -= 1
    value = json.loads(
        text, object_pairs_hook=pairs, parse_constant=nonfinite, parse_int=parse_int
    )
    check_tree(value)
    fields(value, ("protocol", "major", "minor", "id", "op", "payload"))
    if (
        value["protocol"] != PROTOCOL
        or type(value["major"]) is not int
        or value["major"] != 1
    ):
        raise ValueError("unsupported protocol/major")
    integer(value["minor"], "minor", maximum=0)
    integer(value["id"], "id", minimum=1)
    string(value["op"], "op")
    fields(
        value["payload"],
        (),
        tuple(value["payload"]) if type(value["payload"]) is dict else (),
    )
    return value


def encode(value):
    check_tree(value)
    result = (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(result) > MAX_FRAME:
        raise ValueError("response exceeds frame budget")
    return result
