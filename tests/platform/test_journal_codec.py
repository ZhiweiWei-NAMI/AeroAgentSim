"""Normal 2.0 runs and historical 1.x runs project the same committed evidence."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from aerokernel.compact import expand_record
from aerokernel.journal import iter_records, replay

from aeroagentsim.platform import RunSession
from aeroagentsim.scenario import load_scenario
from aeroagentsim.scenario.loader import UniqueLoader
from aeroagentsim.services.projector import project
from aeroagentsim.services.storage import RunStorage


@pytest.mark.parametrize("codec", ["json", "positional-deflate"])
def test_paged_storage_and_replay_preserve_projected_evidence(
    tmp_path: Path, codec: str
) -> None:
    base = Path("scenarios/behaviours").resolve()
    doc = yaml.load((base / "minimal.yaml").read_text(), Loader=UniqueLoader)
    doc["bindings"]["commands"] = []
    directory = tmp_path / codec
    with RunSession(
        load_scenario(doc, base=base), directory, journal_codec=codec
    ) as session:
        session.start()
        session.run_until(20)
    storage = RunStorage(directory)
    storage.index()
    metadata = storage.metadata()
    assert metadata["journal_codec"] == codec
    assert metadata["journal_major"] == (2 if codec == "positional-deflate" else 1)
    expected = [
        expand_record(record) for record in iter_records(directory / "journal.jsonl")
    ]
    assert metadata["journal_major"] == expected[0]["major"]
    # A fresh reader pages from indexed offsets, with neither whole-file replay
    # nor a full-prefix scan. Old index files need no codec annotations.
    for start in range(0, len(expected), 3):
        assert storage.records(start, 3) == expected[start : start + 3]
    assert storage.records(len(expected) + 1, 3) == []
    restored = replay(directory / "journal.jsonl")
    assert list(restored.iter_records()) == expected[1:]
    assert not restored.incomplete
    if codec == "json":
        assert "codec" not in expected[0]  # The historical 1.x wire header.
    assert [project(record) for record in storage.records(1, len(expected))] == [
        project(record) for record in expected[1:]
    ]
