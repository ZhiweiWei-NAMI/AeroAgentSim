"""One wire admission policy; decode-only access is not semantic verification."""

from __future__ import annotations

import base64
import json
import zlib
from dataclasses import fields
from pathlib import Path

import pytest

from aerokernel import Journal, Kernel, KernelError, ResourceLimit, replay
from aerokernel.codec import RecordDecodeError, decode_record
from aerokernel.compact import expand_record
from aerokernel.journal import read_records
from aerokernel.journal_codec import (
    JournalFrameError,
    JournalHeaderError,
    JournalTruncatedError,
    UnsupportedJournalVersion,
    decode_frame,
)
from aerokernel.journal_stream import RecordReader, iter_records
from aerokernel.messages import ActionState
from aerokernel.sampling import SampleSpec
from aerokernel.values import ResourceBudget, parse_json
from examples.two_engine_toy import MS, make_toy

READERS = ("eager", "streamed", "replay")
CODECS = ("json", "positional-deflate")


def wire(record):
    # Mutations intentionally bypass writer admission, not reader admission.
    return (json.dumps(record, separators=(",", ":")) + "\n").encode()


def consume(reader, data, *, recover_truncated=False):
    if reader == "eager":
        return read_records(
            data.read_bytes() if isinstance(data, Path) else data,
            recover_truncated=recover_truncated,
        )
    if reader == "streamed":
        with RecordReader(data, recover_truncated=recover_truncated) as source:
            return list(source), source.incomplete
    return replay(data, recover_truncated=recover_truncated)


@pytest.fixture(scope="module")
def journals():
    result = {}
    for provenance in ("lean", "full"):
        for codec in CODECS:
            toy = make_toy(provenance=provenance)
            kernel = Kernel(
                provenance=provenance,
                journal=Journal(codec=codec),
                root_seed=42,
                configuration=dict(toy.configuration),
            )
            kernel.bind(
                toy._store.registry, toy._store.manifest, tuple(toy._engines.values())
            )
            kernel.start()
            kernel.run_until(13 * MS)
            result[provenance, codec] = kernel.journal.bytes
    return result


@pytest.mark.parametrize("reader", READERS)
@pytest.mark.parametrize("codec", CODECS)
@pytest.mark.parametrize("as_path", [False, True], ids=["bytes", "path"])
@pytest.mark.parametrize(
    "case,error,code",
    [
        ("valid lean", None, None),
        ("valid full", None, None),
        ("unsupported version", UnsupportedJournalVersion, "JOURNAL_HEADER"),
        ("truncated header", JournalTruncatedError, "JOURNAL_TRUNCATED"),
        ("corrupt frame", JournalFrameError, None),
        ("oversize frame", ResourceLimit, "RESOURCE_LIMIT"),
    ],
)
def test_reader_admission_matrix(
    journals, tmp_path, reader, codec, as_path, case, error, code
):
    data = journals["lean" if case == "valid lean" else "full", codec]
    header_line = data.splitlines(keepends=True)[0]
    header = parse_json(header_line)
    if case == "unsupported version":
        data = wire({**header, "minor": 99})
    elif case == "truncated header":
        data = header_line[:-1]
    elif case == "corrupt frame":
        frame = (
            wire({"type": "seal", "index": 1, "data": "invalid!"})
            if codec != "json" else b"not json\n"
        )
        data = header_line + frame
        code = "JOURNAL_CODEC" if codec != "json" else "JSON_INVALID"
    elif case == "oversize frame":
        header["budget"]["frame_bytes"] = 32768
        data = wire(header) + b"x" * 32769 + b"\n"
    source = data
    if as_path:
        source = tmp_path / "journal.jsonl"
        source.write_bytes(data)
    if error is not None:
        with pytest.raises(error) as caught:
            consume(reader, source)
        assert type(caught.value) is error
        assert caught.value.code == code
        assert isinstance(caught.value, (KernelError, ValueError))
    elif reader == "replay":
        restored = consume(reader, source)
        assert restored.header == header
        assert restored.provenance == ("lean" if case == "valid lean" else "full")
        assert tuple(restored.iter_records()) == tuple(
            expand_record(record) for record in read_records(data)[0][1:]
        )
        assert not restored.incomplete
    else:
        records, incomplete = consume(reader, source)
        assert records == list(iter_records(data))
        assert records[0] == header
        assert incomplete is False


def assert_rejected(data, error, code, *, recover_truncated=False):
    diagnostics = []
    for reader in READERS:
        with pytest.raises(error) as caught:
            consume(reader, data, recover_truncated=recover_truncated)
        diagnostics.append((type(caught.value), caught.value.code, str(caught.value)))
    assert all(item == diagnostics[0] for item in diagnostics)
    assert diagnostics[0][:2] == (error, code)


@pytest.mark.parametrize("provenance", ["lean", "full"])
@pytest.mark.parametrize("codec", CODECS)
@pytest.mark.parametrize(
    "change",
    [
        {"major": 9}, {"major": True}, {"major": 2.0}, {"major": []},
        {"minor": 99}, {"minor": False}, {"minor": None},
        {"codec": "unknown"}, {"semantic_version": True},
        {"semantic_version": 99}, {"provenance": "unknown"},
        {"policy_version": True}, {"policy_version": 99},
    ],
)
def test_unsupported_discriminators_have_one_type(journals, provenance, codec, change):
    header = parse_json(journals[provenance, codec].splitlines(keepends=True)[0])
    assert_rejected(
        wire({**header, **change}), UnsupportedJournalVersion, "JOURNAL_HEADER"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"index": True}, {"index": 1}, {"time_origin_ns": 1.0},
        {"type": "not-header"}, {"extra": None}, {"instant": []},
        {"instant": {"$type": [], "fields": {}}},
        {"instant": {"$type": "Instant", "fields": None}},
        {"budget": None}, {"budget": {"unexpected": 1}},
    ],
)
def test_malformed_headers_are_not_unsupported(journals, change):
    header = parse_json(journals["full", "json"].splitlines(keepends=True)[0])
    data = wire({**header, **change})
    assert_rejected(data, JournalHeaderError, "JOURNAL_HEADER")
    with pytest.raises(JournalHeaderError) as caught:
        read_records(data)
    # The read-only run reader's legacy error mapping depends on this distinction.
    assert "unsupported" not in str(caught.value)


@pytest.mark.parametrize(
    "data,code",
    [
        (b"not json\n", "JSON_INVALID"),
        (b'{"budget":{},"budget":{}}\n', "JSON_DUPLICATE"),
    ],
)
def test_header_parser_diagnostics_keep_their_codes(data, code):
    assert_rejected(data, JournalHeaderError, code)


@pytest.mark.parametrize("lean", [False, True])
def test_expansion_limit_is_not_rewrapped_as_codec_corruption(journals, lean):
    data = journals["lean" if lean else "full", "positional-deflate"]
    header = parse_json(data.splitlines(keepends=True)[0])
    header["budget"]["frame_bytes"] = 32768
    frame = {
        "type": "seal", "index": 1,
        "data": base64.b64encode(zlib.compress(b"x" * 32769)).decode(),
    }
    assert_rejected(wire(header) + wire(frame), ResourceLimit, "RESOURCE_LIMIT")
    with pytest.raises(ResourceLimit) as caught:
        decode_frame(frame, ResourceBudget(frame_bytes=32768), lean=lean)
    assert type(caught.value) is ResourceLimit


@pytest.mark.parametrize(
    "packed", [b"[]", b"[false]", b"[1,[1,2]]", b'[2,"unknown"]']
)
def test_corrupt_positional_shapes_have_one_type(journals, packed):
    header_line = journals["full", "positional-deflate"].splitlines(keepends=True)[0]
    frame = {
        "type": "seal",
        "index": 1,
        "data": base64.b64encode(zlib.compress(packed)).decode(),
    }
    assert_rejected(header_line + wire(frame), JournalFrameError, "JOURNAL_CODEC")


@pytest.mark.parametrize("tail", [b"{}", b"\xff", b"x" * 32769])
@pytest.mark.parametrize("provenance", ["lean", "full"])
@pytest.mark.parametrize("codec", CODECS)
def test_only_unterminated_tail_is_recovered(journals, tail, provenance, codec):
    data = journals[provenance, codec]
    lines = data.splitlines(keepends=True)
    header = parse_json(lines[0])
    # Header-only is a valid decoded prefix but is not a settled semantic run.
    header["budget"]["frame_bytes"] = 32768
    prefix = wire(header)
    for reader in READERS:
        recovered = consume(reader, prefix + tail, recover_truncated=True)
        if reader == "replay":
            assert recovered.header == header and recovered.incomplete
        else:
            assert recovered == ([header], True)
    assert_rejected(prefix + tail, JournalTruncatedError, "JOURNAL_TRUNCATED")
    assert_rejected(
        prefix[:-1], JournalHeaderError, "JOURNAL_HEADER", recover_truncated=True
    )
    # No implicit live-ACK cutoff: all complete records remain available on disk.
    assert consume("streamed", data, recover_truncated=True)[0] == read_records(data)[0]
    assert (
        consume("replay", data, recover_truncated=True).view().cut
        == replay(data).view().cut
    )


@pytest.mark.parametrize("recover", [False, True])
def test_complete_corruption_precedes_truncated_tail(journals, recover):
    prefix = journals["full", "json"].splitlines(keepends=True)[0]
    assert_rejected(
        prefix + b'{"x":1,"x":2}\n' + b"unterminated",
        JournalFrameError, "JSON_DUPLICATE", recover_truncated=recover,
    )
    assert_rejected(
        prefix + b"{}\r{}\n",
        JournalFrameError, "JOURNAL_FRAME", recover_truncated=recover,
    )


@pytest.mark.parametrize(
    "name", ["m1-1.1.jsonl", "journal_1_2_live.jsonl", "k3-before-toy.jsonl"]
)
def test_historical_full_fixtures_keep_decoded_meanings(name):
    data = Path(__file__).with_name("fixtures").joinpath(name).read_bytes()
    expected = read_records(data)[0]
    assert list(iter_records(data)) == expected
    restored = replay(data)
    assert restored.provenance == "full"
    assert restored.header == expected[0]
    assert tuple(restored.iter_records()) == tuple(
        expand_record(record) for record in expected[1:]
    )
    assert restored.header.get("policy_version") is None
    # CRLF was accepted historically and must not be mistaken for an embedded CR.
    crlf = data.replace(b"\n", b"\r\n")
    assert read_records(crlf)[0] == expected
    assert replay(crlf).view().cut == restored.view().cut


def test_decode_only_does_not_verify_registry_or_record_effects(journals):
    header = parse_json(journals["full", "json"].splitlines(keepends=True)[0])
    header["registry_digest"] = "corrupt"
    data = wire(header)
    assert read_records(data) == ([header], False)
    assert list(iter_records(data)) == [header]
    with pytest.raises(KernelError, match="REGISTRY_DIGEST"):
        replay(data)


def test_budget_only_decode_compatibility_is_not_a_semantic_header():
    header = {"budget": {"frame_bytes": 256}}
    data = wire(header) + wire({"payload": "x" * 512})
    for reader in (
        read_records, lambda data, **kw: (list(iter_records(data, **kw)), False)
    ):
        records, incomplete = reader(data, budget=ResourceBudget())
        assert len(records) == 2 and not incomplete
        with pytest.raises(ResourceLimit):
            reader(data)
    with pytest.raises(JournalHeaderError):
        replay(wire(header))


@pytest.mark.parametrize(
    "record,code",
    [
        ({"$type": [], "fields": {}}, "RECORD_TYPE"),
        ({"$type": "Instant", "fields": None}, "RECORD_FIELDS"),
        ({"$type": "Timing", "fields": {"mode": "fixed_step"}}, "RECORD_FIELDS"),
    ],
)
def test_typed_record_shape_errors_remain_value_errors(record, code):
    with pytest.raises(ValueError) as caught:
        decode_record(record)
    assert type(caught.value) is RecordDecodeError
    assert caught.value.code == code


@pytest.mark.parametrize("lean", [False, True])
@pytest.mark.parametrize("index", [True, 1.0])
def test_compressed_coordinates_require_strict_integer_types(journals, lean, index):
    header_line = journals[
        "lean" if lean else "full", "positional-deflate"
    ].splitlines(keepends=True)[0]
    packed = (
        {"type": "seal", "index": index}
        if lean else [1, ["type", "seal"], ["index", index]]
    )
    frame = {
        "type": "seal", "index": 1,
        "data": base64.b64encode(zlib.compress(wire(packed))).decode(),
    }
    assert_rejected(header_line + wire(frame), JournalFrameError, "JOURNAL_CODEC")


@pytest.mark.parametrize("policy", [2, 3])
@pytest.mark.parametrize("provenance", ["lean", "full"])
@pytest.mark.parametrize("codec", CODECS)
def test_supported_header_policy_families(journals, policy, provenance, codec):
    header = parse_json(journals[provenance, codec].splitlines(keepends=True)[0])
    if policy == 3:
        header["ingress_streams"] = []
    if provenance == "lean":
        header["policy_version"] = policy
    elif codec == "json":
        header["minor"] = policy
    else:
        header["semantic_version"] = policy
    data = wire(header)
    assert read_records(data) == ([header], False)
    assert list(iter_records(data)) == [header]
    restored = replay(data)
    assert restored.header == header
    assert restored.provenance == provenance
    assert restored.incomplete  # Complete header is not a settled simulation.


@pytest.mark.parametrize("budget", [None, ResourceBudget()])
@pytest.mark.parametrize(
    "declaration,error",
    [
        ({"unexpected": 1}, JournalHeaderError),
        ({"frame_bytes": False}, ResourceLimit),
        ({"nesting_depth": 0}, ResourceLimit),
    ],
)
def test_explicit_budget_overrides_limits_not_declaration_shape(
    budget, declaration, error
):
    data = wire({"budget": declaration})
    for reader in (read_records, lambda data, **kw: list(iter_records(data, **kw))):
        with pytest.raises(error) as caught:
            reader(data, budget=budget)
        assert type(caught.value) is error


def test_minimal_legacy_diagnostic_header_remains_decode_only(journals):
    original = parse_json(journals["full", "json"].splitlines(keepends=True)[0])
    header = {
        key: original[key]
        for key in ("type", "format", "major", "minor", "index", "budget", "instant")
    }
    data = wire(header) + wire({"type": "commit", "index": 1, "items": []})
    assert read_records(data)[0] == list(iter_records(data))
    with pytest.raises(JournalHeaderError):
        replay(data)
    # The compatibility shape must not bypass version admission.
    assert_rejected(
        wire({**header, "minor": 99}), UnsupportedJournalVersion, "JOURNAL_HEADER"
    )


@pytest.mark.parametrize("limit", ["integer_digits", "nesting_depth"])
def test_resource_tree_limits_have_identical_diagnostics(journals, limit):
    header = parse_json(journals["full", "json"].splitlines(keepends=True)[0])
    if limit == "integer_digits":
        # Keep the real header within its declared integer policy.
        header["budget"][limit] = 128
        frame = b'{"n":' + b"1" * 129 + b"}\n"
    else:
        header["budget"][limit] = 32
        frame = b'{"n":' + b"[" * 40 + b"0" + b"]" * 40 + b"}\n"
    assert_rejected(wire(header) + frame, ResourceLimit, "RESOURCE_LIMIT")


@pytest.mark.parametrize("cls", [ActionState, SampleSpec])
def test_malformed_nested_record_fields_have_typed_diagnostics(cls):
    record = {
        "$type": cls.__name__,
        "fields": {field.name: None for field in fields(cls)},
    }
    with pytest.raises(RecordDecodeError, match="RECORD_FIELDS"):
        decode_record(record)


@pytest.mark.parametrize("codec", CODECS)
def test_stream_is_lazy_and_preserves_original_frame_offsets(journals, codec):
    lines = journals["lean", codec].splitlines(keepends=True)
    with RecordReader(lines[0] + lines[1] + b"bad\n") as reader:
        assert next(reader)["type"] == "header"
        assert reader.last_offset == 0 and reader.last_line == lines[0]
        assert next(reader) == read_records(lines[0] + lines[1])[0][1]
        assert reader.last_offset == len(lines[0])
        assert reader.last_line == lines[1]  # Compressed bytes, not a re-encoding.
        with pytest.raises(JournalFrameError):
            next(reader)
        assert reader.incomplete is False
    assert reader.stream.closed
