"""Streaming legacy JSONL reader (aerokernel.journal_stream) behavior tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aerokernel.errors import KernelError
from aerokernel.journal import read_records
from aerokernel.journal_stream import RecordReader, iter_records
from aerokernel.values import ResourceBudget

HEADER_BUDGET = {
    "integer_digits": 4096,
    "frame_bytes": 8 * 1024 * 1024,
    "nesting_depth": 128,
}


def header(**overrides: int) -> bytes:
    budget = {**HEADER_BUDGET, **overrides}
    return (json.dumps({"budget": budget}, sort_keys=True) + "\n").encode()


def record(payload: dict, **budget_overrides: int) -> bytes:
    budget = {**HEADER_BUDGET, **budget_overrides}
    return (json.dumps({"budget": budget, **payload}, sort_keys=True) + "\n").encode()


def test_bytes_and_path_equivalence(tmp_path: Path) -> None:
    data = header() + record({"n": 1}) + record({"n": 2})
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    from_bytes = list(iter_records(data))
    from_path = list(iter_records(path))
    assert from_bytes == from_path
    # Same result as the legacy whole-file reader.
    legacy, incomplete = read_records(data)
    assert from_bytes == legacy
    assert incomplete is False


def test_header_budget_larger_than_default(tmp_path: Path) -> None:
    # A header declaring a budget larger than the ResourceBudget defaults must
    # bootstrap successfully and apply its policy to later records.
    big_frame = 12 * 1024 * 1024
    data = header(frame_bytes=big_frame) + record({"n": 1}, frame_bytes=big_frame)
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    records = list(iter_records(path))
    assert records[0]["budget"]["frame_bytes"] == big_frame
    assert records[1]["n"] == 1


def test_lazy_iteration_yields_before_later_corruption(tmp_path: Path) -> None:
    data = header() + record({"n": 1}) + b'{"n": 1, "n": 2}\n'
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    with RecordReader(path) as reader:
        assert next(reader)["budget"] == HEADER_BUDGET  # header first
        assert next(reader)["n"] == 1
        with pytest.raises(KernelError) as excinfo:
            next(reader)
        assert excinfo.value.code == "JSON_DUPLICATE"
        assert reader.incomplete is False


def test_duplicate_key_rejected(tmp_path: Path) -> None:
    data = header() + b'{"budget": {"integer_digits": 4096}, "budget": {}}\n'
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    with pytest.raises(KernelError) as excinfo:
        list(iter_records(path))
    assert excinfo.value.code == "JSON_DUPLICATE"


def test_oversized_record_frame_aborts(tmp_path: Path) -> None:
    # Header declares a small frame budget; the next record exceeds it.
    small = 256
    data = header(frame_bytes=small) + record({"pad": "x" * 512}, frame_bytes=small)
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    with pytest.raises(KernelError) as excinfo:
        list(iter_records(path))
    assert excinfo.value.code == "RESOURCE_LIMIT"


def test_explicit_budget_overrides_header(tmp_path: Path) -> None:
    small = 256
    data = header(frame_bytes=small) + record({"pad": "x" * 512}, frame_bytes=small)
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    # With an explicit budget the header is not re-bootstrapped, but the header
    # must still carry a budget object.
    records = list(
        iter_records(path, budget=ResourceBudget(4096, 8 * 1024 * 1024, 128))
    )
    assert len(records) == 2


def test_truncated_tail_rejected_unless_recovering(tmp_path: Path) -> None:
    data = header() + record({"n": 1}) + b'{"n": 2}'
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    with pytest.raises(KernelError) as excinfo:
        list(iter_records(path))
    assert excinfo.value.code == "JOURNAL_TRUNCATED"
    # The complete prefix still yields before the rejection.
    with RecordReader(path) as reader:
        assert next(reader)["budget"] == HEADER_BUDGET
        assert next(reader)["n"] == 1
        with pytest.raises(KernelError):
            next(reader)
        assert reader.incomplete is True

    reader = RecordReader(path, recover_truncated=True)
    recovered = list(reader)
    assert reader.incomplete is True
    # read_records drops the unterminated final line even when recovering.
    assert recovered[-1]["n"] == 1
    assert len(recovered) == 2

    legacy, legacy_incomplete = read_records(data, recover_truncated=True)
    assert recovered == legacy
    assert legacy_incomplete


def test_recovered_truncated_record_is_dropped(tmp_path: Path) -> None:
    # The incomplete tail is discarded on recovery without being parsed,
    # matching read_records; the complete header still yields.
    data = header() + b"12"
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    reader = RecordReader(path, recover_truncated=True)
    assert [r["budget"] for r in reader] == [HEADER_BUDGET]
    assert reader.incomplete is True


def test_empty_source_missing_header(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    with pytest.raises(KernelError) as excinfo:
        list(iter_records(empty))
    assert excinfo.value.code == "JOURNAL_HEADER"
    with pytest.raises(KernelError) as excinfo:
        list(iter_records(b""))
    assert excinfo.value.code == "JOURNAL_HEADER"


def test_first_line_without_budget_object(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(b'{"no": "budget"}\n')
    with pytest.raises(KernelError) as excinfo:
        list(iter_records(path))
    assert excinfo.value.code == "JOURNAL_HEADER"


def test_non_object_record_rejected(tmp_path: Path) -> None:
    data = header() + b"[1, 2, 3]\n"
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    with pytest.raises(KernelError) as excinfo:
        list(iter_records(path))
    assert excinfo.value.code == "JOURNAL_RECORD"


def test_corrupt_complete_line_never_skipped(tmp_path: Path) -> None:
    data = header() + record({"n": 1}) + b"not json\n" + record({"n": 3})
    path = tmp_path / "journal.jsonl"
    path.write_bytes(data)
    with pytest.raises(KernelError):
        list(iter_records(path))


def test_context_manager_closes_path_source(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(header() + record({"n": 1}))
    reader = RecordReader(path)
    next(reader)
    reader.close()
    with pytest.raises(ValueError):
        reader.stream.read(1)


@pytest.mark.parametrize(
    "data, options",
    [
        (b'{"budget":{"unexpected":1}}\n', {}),
        (b'{"budget":null}\n', {}),
        (b"[]\n", {"budget": ResourceBudget()}),
        (b"{}", {"recover_truncated": True}),
    ],
)
def test_stream_and_eager_header_errors(data, options):
    for consume in (
        read_records,
        lambda data, **kwargs: list(iter_records(data, **kwargs)),
    ):
        with pytest.raises(KernelError):
            consume(data, **options)


def test_oversized_truncated_tail_is_discarded_without_parsing():
    data = header(frame_bytes=256) + b"x" * 2000
    with RecordReader(data, recover_truncated=True) as reader:
        assert list(reader) == read_records(data, recover_truncated=True)[0]
        assert reader.incomplete
        with pytest.raises(StopIteration):
            next(reader)
    with pytest.raises(KernelError, match="JOURNAL_TRUNCATED"):
        read_records(data)
    with pytest.raises(KernelError, match="JOURNAL_FRAME"):
        read_records(header() + b"{}\r{}\n")
