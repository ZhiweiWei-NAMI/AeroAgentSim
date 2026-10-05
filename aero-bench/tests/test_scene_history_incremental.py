from __future__ import annotations

from dataclasses import replace

import pytest

from aero_bench.runtime.scene_history import IncrementalSceneStateHistory
from tests.runtime.test_scene_state import _assemble, _assembler


def test_incremental_history_reads_bounded_pages_and_seals(tmp_path) -> None:
    assembler = _assembler()
    first = _assemble(assembler, 1)
    second = _assemble(assembler, 2, first)
    writer = IncrementalSceneStateHistory(
        tmp_path / "scene-history.jsonl",
        run_id=first.run_id,
        scenario_digest=first.scenario_digest,
    )
    writer.append(first)
    writer.append(second)

    assert writer.count == 2
    assert writer.read_page(after_tick=0, limit=1) == (first,)
    assert writer.read_page(after_tick=1, limit=1) == (second,)
    assert writer.read_page(after_tick=2, limit=1) == ()
    assert writer.seal() == writer.path.read_bytes()


def test_incremental_history_fails_closed_on_staging_mutation(tmp_path) -> None:
    assembler = _assembler()
    first = _assemble(assembler, 1)
    path = tmp_path / "scene-history.jsonl"
    writer = IncrementalSceneStateHistory(
        path,
        run_id=first.run_id,
        scenario_digest=first.scenario_digest,
    )
    writer.append(first)
    path.write_bytes(path.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="append index"):
        writer.read_page(after_tick=0, limit=1)
    with pytest.raises(ValueError):
        writer.seal()


def test_post_seal_readers_survive_discard_without_retaining_bytes(tmp_path) -> None:
    assembler = _assembler()
    first = _assemble(assembler, 1)
    second = _assemble(assembler, 2, first)
    writer = IncrementalSceneStateHistory(
        tmp_path / "scene-history.jsonl",
        run_id=first.run_id,
        scenario_digest=first.scenario_digest,
    )
    writer.append(first)
    writer.append(second)
    payload = writer.seal()
    writer.discard()

    assert not writer.path.exists()
    assert writer.read_all() == (first, second)
    assert writer.read_page(after_tick=1, limit=1) == (second,)
    assert writer.seal() == payload
    assert not any(isinstance(value, bytes) for value in vars(writer).values())
    with pytest.raises(ValueError, match="closed"):
        writer.append(second)
    writer.close()
    with pytest.raises(ValueError, match="cannot be read"):
        writer.read_all()


@pytest.mark.parametrize(
    "change",
    [
        {"tick": 2},
        {"offset": 1},
        {"size_bytes": 1},
        {"scene_state_digest": "a" * 64},
        {"record_sha256": "a" * 64},
        {"sim_time_ns": 1},
    ],
)
def test_read_all_and_seal_validate_every_append_index_field(tmp_path, change) -> None:
    first = _assemble(_assembler(), 1)
    writer = IncrementalSceneStateHistory(
        tmp_path / "scene-history.jsonl",
        run_id=first.run_id,
        scenario_digest=first.scenario_digest,
    )
    writer.append(first)
    writer._entries[0] = replace(writer.entries[0], **change)
    with pytest.raises(ValueError, match="append index"):
        writer.read_all()
    with pytest.raises(ValueError, match="append index"):
        writer.seal()
    writer.discard()


def test_empty_aborted_history_remains_readable_after_discard(tmp_path) -> None:
    writer = IncrementalSceneStateHistory(
        tmp_path / "scene-history.jsonl", run_id="a" * 64, scenario_digest="b" * 64
    )
    with pytest.raises(ValueError, match="empty"):
        writer.seal()
    assert writer.seal(aborted_before_first_motion=True) == b""
    writer.discard()
    assert writer.read_all(aborted_before_first_motion=True) == ()
    assert writer.read_page(after_tick=0, limit=16) == ()
    writer.close()


def test_replaced_staging_is_not_read_or_deleted_even_with_identical_bytes(tmp_path) -> None:
    first = _assemble(_assembler(), 1)
    path = tmp_path / "scene-history.jsonl"
    writer = IncrementalSceneStateHistory(path, run_id=first.run_id, scenario_digest=first.scenario_digest)
    writer.append(first)
    original = tmp_path / "original.jsonl"
    path.rename(original)
    payload = original.read_bytes()
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="identity"):
        writer.read_all()
    with pytest.raises(ValueError, match="identity"):
        writer.seal()
    with pytest.raises(ValueError, match="refusing removal"):
        writer.discard()
    assert path.read_bytes() == payload
    writer.close()
