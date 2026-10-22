"""Historical full journals stay readable without invented run metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel.compact import expand_record
from aerokernel.journal import iter_records

from aeroagentsim.runs import open_run

FIXTURES = Path(__file__).resolve().parents[2] / "aerokernel/tests/kernel/fixtures"


@pytest.mark.parametrize(
    "name,minor",
    [("m1-1.1.jsonl", 1), ("journal_1_2_live.jsonl", 2)],
)
def test_historical_full_golden_journal_with_explicit_ack(
    tmp_path: Path, name: str, minor: int
) -> None:
    source = FIXTURES / name
    data = source.read_bytes()
    expected = [expand_record(record) for record in iter_records(data)]
    directory = tmp_path / "legacy-recorded-run"
    directory.mkdir()
    (directory / "journal.jsonl").write_bytes(data)
    # Golden WALs predate service sidecars. Explicitly acknowledge their complete
    # recorded prefix in the test copy; never infer it in the public reader.
    (directory / "committed-prefix.json").write_text(
        json.dumps({"bytes": len(data), "index": expected[-1]["index"]})
    )
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
        for path in directory.iterdir()
    }
    with open_run(directory) as run:
        header = run.header
        raw = expected[0]
        assert (header.journal_major, header.journal_minor) == (1, minor)
        assert header.provenance == "full"
        assert header.semantic_version is None and header.policy_version is None
        assert header.journal_codec is None
        assert header.service_run_id is None
        assert header.kernel_run_id == raw["run_id"]
        assert header.epoch == raw["epoch"]
        assert header.configuration_digest is None
        assert header.registry_digest == raw["registry_digest"]
        assert header.created_at is None
        assert header.engine_versions == tuple(sorted(raw["engine_versions"].items()))
        snapshot = run.snapshot()
        assert snapshot.cut_bytes == len(data)
        assert snapshot.cut_index == expected[-1]["index"]
        assert list(snapshot.iter_records()) == expected
        pages = [
            record
            for start in range(0, len(expected), 3)
            for record in snapshot.iter_records(start=start, limit=3)
        ]
        assert pages == expected
        messages = list(snapshot.iter_messages())
        assert messages
        assert len({message.message_id for message in messages}) == len(messages)
        assert [message.cursor for message in messages] == sorted(
            message.cursor for message in messages
        )
        origins: dict[str, tuple[int, int]] = {}
        for record in expected:
            for ordinal, item in enumerate(record.get("items", [])):
                fields: Any = item.get("message")
                if isinstance(fields, dict):
                    fields = fields.get("fields", {})
                    message_id = fields.get("id")
                else:
                    message_id = item.get("message_id")
                if isinstance(message_id, str):
                    origins.setdefault(message_id, (record["index"], ordinal))
        assert {message.message_id for message in messages} == set(origins)
        for message in messages:
            assert json.loads(message.message_id)[:3] == [
                "aerokernel.message/v1", raw["run_id"], raw["epoch"]
            ]
            assert message.source in {
                raw["manifest"]["ingress_source"],
                *(row["fields"]["id"] for row in raw["partitions"]),
            }
        # Starting later must not expose a publication/dispatch copy of an
        # earlier admission (particularly the 1.2 live-ingress golden).
        start = messages[0].cursor[0] + 1
        assert list(snapshot.iter_messages(start=start)) == [
            message for message in messages if message.cursor[0] >= start
        ]
    assert {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
        for path in directory.iterdir()
    } == before
    assert source.read_bytes() == data
