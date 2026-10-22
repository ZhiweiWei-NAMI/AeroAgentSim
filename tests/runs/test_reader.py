"""Public v1 reads of genuine kernel ACKs, without replay or viewer indexes.

The small telemetry scenario supplies a command, dispatch and typed fact.  The
nonspatial behaviour example supplies ordinary emitted events.  RunSession owns
its artifacts but does not publish the worker ACK, so these helpers publish the
actual kernel coordinate explicitly.  No platform-directory fixtures are used.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from typing import Any

import pytest
import yaml
from aerokernel import CommandRequest, Instant, Journal, Stamp
from aerokernel.compact import expand_record
from aerokernel.journal import iter_records
from aerokernel.values import canonical_json

from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.runs import (
    RUN_READER_API_VERSION,
    CorruptRecord,
    NoCommittedPrefix,
    RecordedMessage,
    RunHeader,
    RunReadError,
    RunReplaced,
    Snapshot,
    UnsupportedRunFormat,
    open_run,
)
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader
from aeroagentsim.services.storage import RunStorage

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = REPO / "scenarios"
COMMAND = "aas.telemetry.observe"
FORMATS = [
    pytest.param("lean", "json", id="lean-json"),
    pytest.param("lean", "positional-deflate", id="lean-deflate"),
    pytest.param("full", "json", id="full-json"),
    pytest.param("full", "positional-deflate", id="full-deflate"),
]


def telemetry_document() -> dict[str, Any]:
    authored = load_scenario(SCENARIOS / "realtime-ingress.yaml")
    document = copy.deepcopy(authored.document)
    document["registry"]["snapshot"] = str(
        (authored.base / document["registry"]["snapshot"]).resolve()
    )
    # The authored 300 ms watermark already closes this scheduled 100 ms run.
    document["run"].update(until_ns=100_000_000, advance_ns=50_000_000)
    return document


def publish_ack(session: RunSession) -> tuple[int, int]:
    coordinate = session.simulation.kernel.acknowledged_prefix
    session.storage.publish_prefix(*coordinate)
    return coordinate


def write_run(
    directory: Path,
    *,
    provenance: str = "lean",
    codec: str = "positional-deflate",
    document: dict[str, Any] | None = None,
    base: Path | None = None,
) -> Path:
    scenario = load_scenario(
        telemetry_document() if document is None else document, base=base
    )
    with RunSession(
        scenario, directory, provenance=provenance, journal_codec=codec
    ) as session:
        session.run()
    publish_ack(session)
    return directory


def recorded_records(
    directory: Path, through_bytes: int | None = None
) -> list[dict[str, Any]]:
    data = (directory / "journal.jsonl").read_bytes()
    if through_bytes is not None:
        data = data[:through_bytes]
    return [expand_record(record) for record in iter_records(data)]


def inventory(directory: Path) -> dict[str, tuple[Any, ...]]:
    """Include new/deleted files, identities, sizes, mtimes and file contents.

    Atimes are intentionally excluded: a read is allowed to update an atime.
    """
    result = {}
    for path in [directory, *sorted(directory.rglob("*"))]:
        info = path.stat()
        result[str(path.relative_to(directory))] = (
            info.st_mode,
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
    return result


def explicit_messages(
    records: list[dict[str, Any]],
) -> list[tuple[tuple[int, int], dict[str, Any], str | None]]:
    """Independent oracle: actual item Message envelopes, not delivery copies."""
    result = []
    seen = set()
    for record in records:
        items = record.get("items", [])
        for ordinal, item in enumerate(items):
            if item.get("kind") not in {"operation", "ingress_publish"}:
                continue
            encoded = item.get("message")
            if not isinstance(encoded, dict) or encoded.get("$type") != "Message":
                continue
            fields = encoded["fields"]
            if fields["id"] in seen:
                continue
            seen.add(fields["id"])
            proposal = item.get("proposal", {}).get("fields", {})
            route = proposal.get("target_or_topic")
            if route is None:
                route = next(
                    (
                        row["recipient"]
                        for row in items
                        if row.get("kind") == "enqueue"
                        and row.get("message") == fields["id"]
                    ),
                    None,
                )
            result.append(((record["index"], ordinal), fields, route))
    return result


def assert_message(
    actual: RecordedMessage,
    cursor: tuple[int, int],
    fields: dict[str, Any],
    route: str | None,
    header: dict[str, Any],
) -> None:
    assert isinstance(actual, RecordedMessage)
    assert actual.api_version == 1
    assert actual.cursor == cursor
    assert actual.message_id == fields["id"]
    identity = [
        "aerokernel.message/v1",
        header["run_id"],
        header["epoch"],
        fields["source_kind"],
        fields["source"],
        fields["sequence"],
    ]
    assert json.loads(actual.message_id) == identity
    assert actual.message_id == json.dumps(
        identity, ensure_ascii=False, separators=(",", ":")
    )
    assert actual.kind == fields["kind"]
    assert actual.schema_id == fields["schema_id"]
    assert actual.source_kind == fields["source_kind"]
    assert actual.source == fields["source"]
    assert actual.sequence == fields["sequence"]
    assert actual.route == route
    partitions = {entry["fields"]["id"] for entry in header["partitions"]}
    target = route if actual.kind in {"command", "cancel"} or route in partitions else None
    assert actual.target == target
    assert actual.topic == (route if actual.kind == "event" and target is None else None)
    assert actual.at_ns == fields["at"]["fields"]["ns"]
    assert actual.microstep == fields["at"]["fields"]["microstep"]
    assert actual.available_ns == fields["available"]["fields"]["ns"]
    assert actual.available_microstep == fields["available"]["fields"]["microstep"]
    assert actual.payload == fields["payload"]
    assert actual.origin == (
        None if fields.get("origin") is None else fields["origin"]["fields"]
    )
    assert actual.source_stamp == (
        None
        if fields.get("source_stamp") is None
        else fields["source_stamp"]["fields"]
    )


def write_prefix(directory: Path, value: Any) -> None:
    (directory / "committed-prefix.json").write_text(json.dumps(value))


def replace_header(directory: Path, key: str, value: Any) -> None:
    journal = directory / "journal.jsonl"
    first, tail = journal.read_bytes().split(b"\n", 1)
    header = json.loads(first)
    header[key] = value
    data = canonical_json(header) + tail
    journal.write_bytes(data)
    prefix = json.loads((directory / "committed-prefix.json").read_text())
    write_prefix(directory, {"bytes": len(data), "index": prefix["index"]})


@pytest.fixture(
    params=[
        pytest.param(("lean", "json"), id="lean-json"),
        pytest.param(("lean", "positional-deflate"), id="lean-deflate"),
        pytest.param(("full", "json"), id="full-json"),
        pytest.param(("full", "positional-deflate"), id="full-deflate"),
    ]
)
def recorded_run(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    provenance, codec = request.param
    return write_run(tmp_path / "service-reader-run", provenance=provenance, codec=codec)


def test_public_version_and_typed_error_hierarchy() -> None:
    assert type(RUN_READER_API_VERSION) is int
    assert RUN_READER_API_VERSION == 1
    for error in (
        UnsupportedRunFormat,
        NoCommittedPrefix,
        CorruptRecord,
        RunReplaced,
    ):
        assert issubclass(error, RunReadError)


def test_header_uses_recorded_identity_formats_and_real_digests(recorded_run: Path) -> None:
    raw = recorded_records(recorded_run)[0]
    metadata = json.loads((recorded_run / "manifest.json").read_text())
    journal_stat = (recorded_run / "journal.jsonl").stat()
    with open_run(recorded_run, api_version=1) as run:
        header = run.header
        assert isinstance(header, RunHeader)
        assert header.api_version == 1
        assert header.service_run_id == recorded_run.name == metadata["id"]
        assert header.kernel_run_id == raw["run_id"] == "realtime-ingress"
        assert header.service_run_id != header.kernel_run_id
        assert header.epoch == raw["epoch"]
        assert header.journal_format == raw["format"] == "aerokernel.journal"
        assert header.journal_major == raw["major"]
        assert header.journal_minor == raw["minor"]
        assert header.journal_codec == raw.get("codec", metadata["journal_codec"])
        assert header.semantic_version == raw.get("semantic_version")
        assert header.policy_version == raw.get("policy_version")
        assert header.provenance == raw.get("provenance", "full")
        assert header.configuration_digest == metadata["configuration_digest"]
        assert header.configuration_digest == hashlib.sha256(
            canonical_json(raw["configuration"]["scenario"])
        ).hexdigest()
        assert header.registry_digest == raw["registry_digest"]
        assert header.registry_digest == hashlib.sha256(
            canonical_json(raw["registry"])[:-1]
        ).hexdigest()
        # The viewer's historical registry_id is an alias, not a SHA-256 digest.
        assert header.registry_digest != metadata["registry_id"]
        assert header.created_at is None
        assert header.journal_identity == (journal_stat.st_dev, journal_stat.st_ino)
        assert header.engine_versions == tuple(sorted(raw["engine_versions"].items()))
        snapshot = run.snapshot()
        assert isinstance(snapshot, Snapshot)
        assert snapshot.header == header
        assert snapshot.api_version == 1


def test_expanded_records_and_inclusive_pages_match_kernel_stream(recorded_run: Path) -> None:
    expected = recorded_records(recorded_run)
    prefix = json.loads((recorded_run / "committed-prefix.json").read_text())
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        assert snapshot.cut_index == prefix["index"] == expected[-1]["index"]
        assert snapshot.cut_bytes == prefix["bytes"]
        assert [record["index"] for record in expected] == list(
            range(snapshot.cut_index + 1)
        )
        assert list(snapshot.iter_records()) == expected
        assert list(snapshot.iter_records(start=0, limit=1)) == [expected[0]]
        paged = []
        for start in range(0, len(expected), 3):
            page = list(snapshot.iter_records(start=start, limit=3))
            assert page == expected[start : start + 3]
            paged.extend(page)
        assert paged == expected
        assert list(snapshot.iter_records(start=snapshot.cut_index)) == expected[-1:]
        assert list(snapshot.iter_records(start=snapshot.cut_index + 1)) == []
        assert list(snapshot.iter_records(start=snapshot.cut_index + 100)) == []
        assert list(snapshot.iter_records(limit=0)) == []
        assert list(snapshot.iter_records(limit=None)) == expected
        detached = list(snapshot.iter_records(limit=1))[0]
        detached["registry"]["types"].clear()
        assert list(snapshot.iter_records(limit=1)) == expected[:1]


def test_commands_are_original_envelopes_not_dispatch_copies(recorded_run: Path) -> None:
    records = recorded_records(recorded_run)
    expected = explicit_messages(records)
    assert expected and all(fields["kind"] == "command" for _, fields, _ in expected)
    assert any("delivery" in item for record in records for item in record.get("items", []))
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        messages = list(snapshot.iter_messages())
        assert len(messages) == len(expected) == 1
        for actual, (cursor, fields, route) in zip(messages, expected, strict=True):
            assert_message(actual, cursor, fields, route, records[0])
        assert list(snapshot.iter_messages(kind="command")) == messages
        assert list(snapshot.iter_messages(kind="event")) == []
        assert list(snapshot.iter_messages(schema_prefix="aas.telemetry.")) == messages
        assert list(snapshot.iter_messages(schema_prefix="unrecorded.")) == []
        assert list(snapshot.iter_messages(start=messages[0].cursor[0])) == messages
        assert list(snapshot.iter_messages(start=messages[0].cursor[0] + 1)) == []
        with pytest.raises(FrozenInstanceError):
            messages[0].kind = "event"  # type: ignore[misc]
        messages[0].payload["value"] = "caller-owned mutation"
        assert list(snapshot.iter_messages())[0].payload == expected[0][1]["payload"]


@pytest.mark.parametrize("provenance,codec", FORMATS)
def test_real_events_match_emit_items_and_filters(
    tmp_path: Path, provenance: str, codec: str
) -> None:
    base = SCENARIOS / "behaviours"
    document = yaml.load((base / "minimal.yaml").read_text(), Loader=UniqueLoader)
    # Keep this an offline, nonspatial behaviour run, without injected interrupts.
    document["bindings"]["commands"] = []
    actions = document["behaviours"][0]["chains"]["queue"]["transitions"][0]["actions"]
    actions.extend(
        {
            "id": identifier,
            "kind": "emit",
            "schema": "example.conflict",
            "topic": "reader.same",
            "payload": {"task": {"$role": "task"}},
        }
        for identifier in ("reader-first", "reader-second")
    )
    directory = write_run(
        tmp_path / "events", provenance=provenance, codec=codec, document=document, base=base
    )
    records = recorded_records(directory)
    expected = [row for row in explicit_messages(records) if row[1]["kind"] == "event"]
    assert expected
    with open_run(directory) as run:
        snapshot = run.snapshot()
        events = list(snapshot.iter_messages(kind="event"))
        suspended_messages = snapshot.iter_messages(kind="event")
        assert next(suspended_messages) == events[0]
        assert len(events) == len(expected)
        assert len({event.message_id for event in events}) == len(events)
        assert [event.cursor for event in events] == sorted(event.cursor for event in events)
        paged_events = [
            event for start in range(0, snapshot.cut_index + 1, 3)
            for event in snapshot.iter_messages(kind="event", start=start)
            if event.cursor[0] < start + 3
        ]
        assert paged_events == events
        for actual, (cursor, fields, route) in zip(events, expected, strict=True):
            assert_message(actual, cursor, fields, route, records[0])
        equal_payloads = [event for event in events if event.route == "reader.same"]
        assert len(equal_payloads) == 2
        assert equal_payloads[0].payload == equal_payloads[1].payload
        assert equal_payloads[0].message_id != equal_payloads[1].message_id
        assert equal_payloads[0].cursor != equal_payloads[1].cursor
        ref = equal_payloads[0].payload["task"]["$ref"]
        assert ref == {
            "run_id": "nonspatial-task-queue", "epoch": "0", "id": "task-1",
            "generation": 0, "type_id": "example:Task",
        }
        assert type(ref["generation"]) is int
        prefix = events[0].schema_id
        assert isinstance(prefix, str)
        assert list(snapshot.iter_messages(kind="event", schema_prefix=prefix)) == [
            event for event in events
            if event.schema_id is not None and event.schema_id.startswith(prefix)
        ]
        start = events[len(events) // 2].cursor[0]
        assert list(snapshot.iter_messages(kind="event", start=start)) == [
            event for event in events if event.cursor[0] >= start
        ]
    with pytest.raises(RunReadError):
        next(suspended_messages)


@pytest.mark.parametrize("provenance,codec", FORMATS)
def test_first_admission_identity_survives_later_publication_and_start_filters(
    tmp_path: Path, provenance: str, codec: str
) -> None:
    document = telemetry_document()
    document["bindings"]["commands"] = []
    document["engines"]["sensor"]["ingress"]["initial_watermark_ns"] = 0
    directory = tmp_path / "live"
    with RunSession(
        load_scenario(document), directory, provenance=provenance, journal_codec=codec
    ) as session:
        session.start()
        request = CommandRequest(COMMAND, "sensor", Instant(50_000_000), {"value": 24.0}, "once")
        stamp = Stamp("sensor-clock", 50_000_000, 1, "sensor-ns")
        receipt = session.submit_live("sensor", request, stamp)
        assert session.submit_live("sensor", request, stamp) == receipt
        publish_ack(session)
        with open_run(directory) as run:
            initial = run.snapshot()
            messages = list(initial.iter_messages())
            assert len(messages) == 1
            original = messages[0]
            assert original.cursor == (receipt.journal_index, 0)
            assert original.message_id == receipt.command_id
            assert original.kind == "command" and original.schema_id == COMMAND
            assert original.route == original.target == "sensor"
            assert original.topic is None
            assert original.source_kind == "ingress"
            assert original.source == "stream:sensor"
            assert original.origin == {"record_index": receipt.journal_index, "item_index": 0}
            assert original.source_stamp == asdict(stamp)
            assert original.at_ns == 50_000_000 and original.microstep == 0
            assert original.available_ns is None and original.available_microstep is None
            assert original.payload == {"value": 24.0}
            session.advance_watermark(100_000_000)
            session.run_until(100_000_000)
            publish_ack(session)
            later = run.snapshot()
            assert later.cut_index > initial.cut_index
            # Later Message/dispatch evidence must not enrich or relocate admission.
            assert list(later.iter_messages()) == messages
            assert list(initial.iter_messages()) == messages
            assert list(later.iter_messages(start=original.cursor[0] + 1)) == []
            assert list(
                later.iter_messages(
                    kind="command", schema_prefix="aas.telemetry.", start=original.cursor[0] + 1
                )
            ) == []


@pytest.mark.parametrize("provenance,codec", FORMATS)
def test_snapshot_is_fixed_while_genuine_wal_and_ack_advance(
    tmp_path: Path, provenance: str, codec: str
) -> None:
    directory = tmp_path / "advancing"
    with RunSession(
        load_scenario(telemetry_document()), directory,
        provenance=provenance, journal_codec=codec,
    ) as session:
        session.start()
        session.run_until(50_000_000)
        first_bytes, first_index = publish_ack(session)
        with open_run(directory) as run:
            first = run.snapshot()
            expected = recorded_records(directory, first_bytes)
            # Bootstrap publishes the authored command at reset; its activation
            # and resulting telemetry fact are still scheduled for 100 ms.
            initial_messages = list(first.iter_messages())
            assert initial_messages
            assert not any(
                item.get("proposal", {}).get("$type") == "FactWrite"
                for record in expected for item in record.get("items", [])
            )
            session.run_until(100_000_000)
            assert (directory / "journal.jsonl").stat().st_size > first_bytes
            # RunSession has also moved index.json ahead; neither is an ACK.
            index = json.loads((directory / "index.json").read_text())
            assert index[-1]["index"] > first_index
            behind = run.snapshot()
            assert (behind.cut_bytes, behind.cut_index) == (first_bytes, first_index)
            assert list(behind.iter_records()) == expected
            assert list(first.iter_records()) == expected
            second_bytes, second_index = publish_ack(session)
            second = run.snapshot()
            assert (second.cut_bytes, second.cut_index) == (second_bytes, second_index)
            assert second.cut_index > first.cut_index
            assert second.cut_bytes > first.cut_bytes
            assert list(second.iter_messages()) == initial_messages
            assert list(first.iter_messages()) == initial_messages
            assert list(first.iter_records()) == expected
            second_records = list(second.iter_records())
            assert second_records[: len(expected)] == expected
            assert any(
                item.get("proposal", {}).get("$type") == "FactWrite"
                for record in second_records[len(expected):]
                for item in record.get("items", [])
            )
            # An unacknowledged partial tail is no more authoritative than a full one.
            with (directory / "journal.jsonl").open("ab") as stream:
                stream.write(b'{"index":')
            with_tail = run.snapshot()
            assert (with_tail.cut_bytes, with_tail.cut_index) == (second_bytes, second_index)
            assert list(with_tail.iter_records()) == list(second.iter_records())
            assert list(first.iter_records()) == expected


def test_each_snapshot_reads_ack_once_and_iteration_does_not_reacquire_it(
    recorded_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix_path = recorded_run / "committed-prefix.json"
    original_open = Path.open
    reads = []

    def tracked_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == prefix_path:
            reads.append(path)
        return original_open(path, *args, **kwargs)

    with open_run(recorded_run) as run:
        monkeypatch.setattr(Path, "open", tracked_open)
        first = run.snapshot()
        assert len(reads) == 1
        list(first.iter_records())
        list(first.iter_messages())
        assert len(reads) == 1
        second = run.snapshot()
        assert len(reads) == 2
        assert second.cut_index == first.cut_index
        assert second.cut_bytes == first.cut_bytes


@pytest.mark.parametrize("provenance,codec", FORMATS)
def test_lossless_large_integers_in_real_headers_messages_facts_and_times(
    tmp_path: Path, provenance: str, codec: str
) -> None:
    big = 2**63 + 7
    document = telemetry_document()
    field = next(row for row in document["registry"]["fields"] if row["id"] == "aas.telemetry.value")
    field["schema"] = {"type": "integer"}
    command = next(row for row in document["registry"]["messages"] if row["id"] == COMMAND)
    command["schema"]["members"]["value"] = {"type": "integer"}
    document["bindings"]["commands"][0].update(at_ns=big + 1, payload={"value": big})
    document["engines"]["sensor"]["ingress"]["initial_watermark_ns"] = big + 2
    document["run"].update(seed=big, until_ns=big + 2, advance_ns=big + 2)
    directory = write_run(tmp_path / "integers", provenance=provenance, codec=codec, document=document)
    expected = recorded_records(directory)
    with open_run(directory) as run:
        snapshot = run.snapshot()
        records = list(snapshot.iter_records())
        assert records == expected
        assert type(records[0]["root_seed"]) is int
        assert records[0]["root_seed"] == big
        messages = list(snapshot.iter_messages(kind="command"))
        assert len(messages) == 1
        message = messages[0]
        assert type(message.payload["value"]) is int and message.payload["value"] == big
        assert type(message.at_ns) is int and message.at_ns == big + 1
        # Authored bootstrap commands are published at reset, before activation.
        cursor, fields, route = explicit_messages(expected)[0]
        assert_message(message, cursor, fields, route, expected[0])
        assert type(message.available_ns) is int and message.available_ns == 0
        facts = [
            item["proposal"]["fields"]["value"]
            for record in records
            for item in record.get("items", [])
            if item.get("proposal", {}).get("$type") == "FactWrite"
        ]
        assert facts and all(type(value) is int and value == big for value in facts)
        # Raw Python reads do not apply the viewer's $integer string-tag projection.
        assert not isinstance(message.payload["value"], (dict, str, float))


@pytest.mark.parametrize("index_state", ["missing", "stale", "corrupt"])
def test_readonly_inventory_and_ignored_viewer_index(recorded_run: Path, index_state: str) -> None:
    index = recorded_run / "index.json"
    if index_state == "missing":
        index.unlink()
    elif index_state == "stale":
        index.write_text("[]")
    else:
        index.write_text("not a JSON viewer index")
    expected = recorded_records(recorded_run)
    before = inventory(recorded_run)
    for _ in range(2):
        with open_run(recorded_run) as run:
            snapshot = run.snapshot()
            assert list(snapshot.iter_records()) == expected
            assert list(snapshot.iter_messages())
            assert run.header == snapshot.header
    assert inventory(recorded_run) == before


@pytest.mark.parametrize("provenance,codec", FORMATS)
def test_direct_simulation_needs_ack_and_does_not_invent_service_metadata(
    tmp_path: Path, provenance: str, codec: str
) -> None:
    directory = tmp_path / "bare-simulation"
    directory.mkdir()
    simulation = Simulation(
        load_scenario(telemetry_document()),
        journal=Journal(directory / "journal.jsonl", codec=codec),
        provenance=provenance,
    )
    try:
        simulation.start()
        simulation.run_until(100_000_000)
    finally:
        simulation.close()
    assert {path.name for path in directory.iterdir()} == {"journal.jsonl"}
    with pytest.raises(NoCommittedPrefix):
        with open_run(directory) as run:
            run.snapshot()
    RunStorage(directory).publish_prefix(*simulation.kernel.acknowledged_prefix)
    before = inventory(directory)
    with open_run(directory) as run:
        assert run.header.service_run_id is None
        assert run.header.kernel_run_id == "realtime-ingress"
        assert run.header.configuration_digest is None
        assert run.header.created_at is None
        raw = recorded_records(directory)[0]
        assert run.header.journal_codec == raw.get("codec")
        assert list(run.snapshot().iter_records()) == recorded_records(directory)
    assert inventory(directory) == before


def test_missing_and_empty_directories_are_not_successful_empty_runs(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "missing"
    with pytest.raises(NoCommittedPrefix):
        open_run(directory)
    assert not directory.exists()
    directory.mkdir()
    before = inventory(directory)
    with pytest.raises(NoCommittedPrefix):
        open_run(directory)
    assert inventory(directory) == before


def test_header_only_genuine_ack_is_a_valid_zero_cut(tmp_path: Path) -> None:
    directory = tmp_path / "header-only"
    with RunSession(load_scenario(telemetry_document()), directory) as session:
        with pytest.raises(NoCommittedPrefix):
            open_run(directory)
        byte_length, index = publish_ack(session)
        assert index == 0
        with open_run(directory) as run:
            snapshot = run.snapshot()
            assert (snapshot.cut_bytes, snapshot.cut_index) == (byte_length, 0)
            assert len(list(snapshot.iter_records())) == 1
            assert list(snapshot.iter_messages()) == []


@pytest.mark.parametrize("api_version", [False, True, 1.0, "1", None, 0, 2, -1])
def test_wrong_api_versions_are_not_coerced(recorded_run: Path, api_version: Any) -> None:
    with pytest.raises(ValueError):
        with open_run(recorded_run, api_version=api_version):
            pass


@pytest.mark.parametrize("start", [-1, True, False, 1.0, "1", None, (1, 0)])
def test_invalid_record_and_message_starts(recorded_run: Path, start: Any) -> None:
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        with pytest.raises(ValueError):
            list(snapshot.iter_records(start=start))
        with pytest.raises(ValueError):
            list(snapshot.iter_messages(start=start))


@pytest.mark.parametrize("limit", [-1, True, False, 1.0, "1"])
def test_invalid_record_limits(recorded_run: Path, limit: Any) -> None:
    with open_run(recorded_run) as run:
        with pytest.raises(ValueError):
            list(run.snapshot().iter_records(limit=limit))


@pytest.mark.parametrize(
    "prefix_case",
    ["missing", "invalid-json", "nonobject", "missing-field", "extra-field", "boolean",
     "float", "negative", "empty", "beyond-eof", "inside-record", "wrong-index"],
)
def test_invalid_ack_has_no_terminal_index_fallback(recorded_run: Path, prefix_case: str) -> None:
    prefix_path = recorded_run / "committed-prefix.json"
    prefix = json.loads(prefix_path.read_text())
    metadata = json.loads((recorded_run / "manifest.json").read_text())
    assert metadata["status"] == "completed"
    assert (recorded_run / "index.json").exists()
    if prefix_case == "missing":
        prefix_path.unlink()
    elif prefix_case == "invalid-json":
        prefix_path.write_text("{")
    elif prefix_case == "nonobject":
        write_prefix(recorded_run, [])
    elif prefix_case == "missing-field":
        write_prefix(recorded_run, {"bytes": prefix["bytes"]})
    elif prefix_case == "extra-field":
        write_prefix(recorded_run, {**prefix, "offset": 0})
    elif prefix_case == "boolean":
        write_prefix(recorded_run, {**prefix, "index": True})
    elif prefix_case == "float":
        write_prefix(recorded_run, {**prefix, "bytes": float(prefix["bytes"])})
    elif prefix_case == "negative":
        write_prefix(recorded_run, {**prefix, "index": -1})
    elif prefix_case == "empty":
        write_prefix(recorded_run, {"bytes": 0, "index": 0})
    elif prefix_case == "beyond-eof":
        write_prefix(recorded_run, {**prefix, "bytes": prefix["bytes"] + 1})
    elif prefix_case == "inside-record":
        write_prefix(recorded_run, {**prefix, "bytes": prefix["bytes"] - 1})
    else:
        write_prefix(recorded_run, {**prefix, "index": prefix["index"] + 1})
    before = inventory(recorded_run)
    with pytest.raises(NoCommittedPrefix):
        with open_run(recorded_run) as run:
            list(run.snapshot().iter_records())
    assert inventory(recorded_run) == before


def test_new_snapshot_revalidates_ack_but_old_snapshot_does_not(recorded_run: Path) -> None:
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        expected = list(snapshot.iter_records())
        (recorded_run / "committed-prefix.json").write_text("{")
        with pytest.raises(NoCommittedPrefix):
            run.snapshot()
        assert list(snapshot.iter_records()) == expected


@pytest.mark.parametrize(
    "key,value", [("format", "other.journal"), ("major", 99), ("minor", 99), ("codec", "unknown/v9")]
)
def test_unsupported_recorded_format_is_typed(recorded_run: Path, key: str, value: Any) -> None:
    replace_header(recorded_run, key, value)
    with pytest.raises(UnsupportedRunFormat):
        with open_run(recorded_run) as run:
            list(run.snapshot().iter_records())


def test_malformed_complete_committed_frame_is_corrupt_not_unacknowledged(recorded_run: Path) -> None:
    journal = recorded_run / "journal.jsonl"
    data = bytearray(journal.read_bytes())
    assert data[-1:] == b"\n"
    # Keep the ACK byte coordinate and LF boundary intact, corrupt only its frame.
    data[-2] = ord("!")
    journal.write_bytes(data)
    with pytest.raises(CorruptRecord):
        with open_run(recorded_run) as run:
            list(run.snapshot().iter_records())


@pytest.mark.parametrize("replacement", [None, 7, {}])
def test_malformed_original_message_envelope_is_not_silently_omitted(
    tmp_path: Path, replacement: Any,
) -> None:
    directory = write_run(tmp_path / "bad-message", provenance="full", codec="json")
    records = recorded_records(directory)
    original = next(
        item for record in records for item in record.get("items", [])
        if item.get("kind") == "ingress_publish"
    )
    original["message"] = replacement
    data = b"".join(canonical_json(record) for record in records)
    (directory / "journal.jsonl").write_bytes(data)
    write_prefix(directory, {"bytes": len(data), "index": records[-1]["index"]})
    with pytest.raises(CorruptRecord):
        with open_run(directory) as run:
            run.snapshot()


def test_same_bytes_replacement_inode_is_not_the_captured_run(recorded_run: Path) -> None:
    journal = recorded_run / "journal.jsonl"
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        previous_identity = snapshot.header.journal_identity
        replacement = recorded_run / "replacement.jsonl"
        replacement.write_bytes(journal.read_bytes())
        replacement.replace(journal)
        info = journal.stat()
        assert (info.st_dev, info.st_ino) != previous_identity
        with pytest.raises(RunReplaced):
            list(snapshot.iter_records())
        with pytest.raises(RunReplaced):
            run.snapshot()


def test_truncated_captured_prefix_is_replaced(recorded_run: Path) -> None:
    journal = recorded_run / "journal.jsonl"
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        with journal.open("r+b") as stream:
            stream.truncate(snapshot.cut_bytes - 1)
        with pytest.raises(RunReplaced):
            list(snapshot.iter_records(start=snapshot.cut_index, limit=1))
        with pytest.raises(RunReplaced):
            list(snapshot.iter_records(start=0, limit=1))


def test_same_size_changed_record_is_detected_only_when_accessed(recorded_run: Path) -> None:
    journal = recorded_run / "journal.jsonl"
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        expected_first = list(snapshot.iter_records(start=0, limit=1))
        data = bytearray(journal.read_bytes())
        line_start = data.rfind(b"\n", 0, len(data) - 1) + 1
        token = b'"index":' + str(snapshot.cut_index).encode()
        position = data.index(token, line_start) + len(token) - 1
        data[position] = ord("0") + (data[position] - ord("0") + 1) % 10
        previous = journal.stat()
        journal.write_bytes(data)
        current = journal.stat()
        assert (current.st_dev, current.st_ino, current.st_size) == (
            previous.st_dev, previous.st_ino, previous.st_size
        )
        # Reading an unchanged early frame does not hash-scan unrelated frames.
        assert list(snapshot.iter_records(start=0, limit=1)) == expected_first
        with pytest.raises(RunReplaced):
            list(snapshot.iter_records(start=snapshot.cut_index, limit=1))
        # A new snapshot cannot bless altered evidence retained by the same run.
        with pytest.raises(RunReplaced):
            run.snapshot()


def test_ack_rollback_cannot_replace_a_previously_captured_cut(recorded_run: Path) -> None:
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        expected = list(snapshot.iter_records())
        header_bytes = len((recorded_run / "journal.jsonl").read_bytes().splitlines(keepends=True)[0])
        write_prefix(recorded_run, {"bytes": header_bytes, "index": 0})
        with pytest.raises(RunReplaced):
            run.snapshot()
        assert list(snapshot.iter_records()) == expected


@pytest.mark.parametrize(
    "key,value",
    [
        ("unexpected_header_field", True),
        ("time_origin_ns", True),
        ("instant", {"$type": "Instant", "fields": {"ns": 1, "microstep": 0}}),
    ],
)
def test_known_format_with_bad_header_shape_is_corrupt(
    recorded_run: Path, key: str, value: Any
) -> None:
    replace_header(recorded_run, key, value)
    with pytest.raises(CorruptRecord):
        with open_run(recorded_run) as run:
            run.snapshot()


def test_snapshots_and_precreated_iterators_are_context_scoped(recorded_run: Path) -> None:
    with open_run(recorded_run) as run:
        snapshot = run.snapshot()
        records = snapshot.iter_records()
        messages = snapshot.iter_messages()
        suspended_records = snapshot.iter_records()
        assert next(suspended_records)["index"] == 0
    with pytest.raises(RunReadError):
        list(snapshot.iter_records())
    with pytest.raises(RunReadError):
        list(snapshot.iter_messages())
    with pytest.raises(RunReadError):
        list(records)
    with pytest.raises(RunReadError):
        list(messages)
    with pytest.raises(RunReadError):
        next(suspended_records)
    with pytest.raises(RunReadError):
        run.snapshot()


def test_fresh_reader_does_not_import_live_platform_server_or_providers(recorded_run: Path) -> None:
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys
        from pathlib import Path

        forbidden = (
            'fastapi', 'starlette', 'uvicorn', 'openai', 'langgraph', 'langchain',
            'aeroagentsim.platform', 'aeroagentsim.engines', 'aeroagentsim.agents',
            'aeroagentsim.adapters', 'aeroagentsim.providers',
            'aeroagentsim.services.app', 'aeroagentsim.services.worker',
        )

        class ForbidLiveImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
                    raise AssertionError('Live import during recorded read: ' + fullname)
                return None

        sys.meta_path.insert(0, ForbidLiveImports())
        from aeroagentsim.runs import RUN_READER_API_VERSION, open_run

        assert RUN_READER_API_VERSION == 1
        with open_run(Path(sys.argv[1])) as run:
            snapshot = run.snapshot()
            assert list(snapshot.iter_records())
            assert list(snapshot.iter_messages())
        assert not any(
            module == name or module.startswith(name + '.')
            for module in sys.modules for name in forbidden
        )
        """
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "aerokernel"), environment.get("PYTHONPATH", "")]
    )
    before = inventory(recorded_run)
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script, str(recorded_run)],
        cwd=REPO, env=environment, capture_output=True, text=True, check=False, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert inventory(recorded_run) == before
