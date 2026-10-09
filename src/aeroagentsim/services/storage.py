"""Journal offsets and manifests are rebuildable, read-only viewer indexes."""

from __future__ import annotations

import json
from bisect import bisect_left
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aerokernel.compact import expand_record
from aerokernel.journal_codec import decode_frame
from aerokernel.journal_stream import RecordReader
from aerokernel.values import ResourceBudget, canonical_json, parse_json

if TYPE_CHECKING:
    from aeroagentsim.scenario import Scenario


@lru_cache(maxsize=16)
def _read_index(
    path: Path, identity: tuple[int, int, int, int]
) -> list[dict[str, int]]:
    """Cache only immutable atomic index versions; WAL records stay authoritative."""
    return json.loads(path.read_text())  # type: ignore[no-any-return]


@lru_cache(maxsize=16)
def _journal_format(
    path: Path, identity: tuple[int, int, int]
) -> tuple[int, ResourceBudget]:
    """Cache the immutable header's format/budget, not its full scenario payload."""
    with RecordReader(path) as reader:
        header = next(reader)
        assert reader.budget is not None
        return header["major"], reader.budget


class RunStorage:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.offset = 0
        self.entries: list[dict[str, int]] = []
        index = directory / "index.json"
        if index.exists():
            self.entries = json.loads(index.read_text())
            if self.entries:
                last = self.entries[-1]
                self.offset = last["offset"] + last["length"]

    def prepare(self, scenario: Scenario) -> None:
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / "scenario.yaml").write_text(scenario.source)
        (self.directory / "scenario.json").write_bytes(
            canonical_json(scenario.document)
        )
        scenario.compiled.write_snapshot(self.directory / "registry.snapshot.json")
        (self.directory / "runtime.registry.json").write_bytes(
            canonical_json(scenario.registry.to_data())
        )
        self._atomic("index.json", [])  # No committed records before execution.
        self._atomic(
            "manifest.json",
            {
                "id": self.directory.name,
                "kernel_run_id": scenario.run_id,
                "scenario": scenario.document["id"],
                "registry_id": scenario.run_id + "/registry",
                "until_ns": str(scenario.until_ns),
                "status": "created",
            },
        )

    def _atomic(self, name: str, value: Any) -> None:
        target = self.directory / name
        temporary = self.directory / (name + ".writing")
        temporary.write_bytes(canonical_json(value))
        temporary.replace(target)

    def metadata(self) -> dict[str, Any]:
        return json.loads((self.directory / "manifest.json").read_text())  # type: ignore[no-any-return]

    def status(self, status: str, *, error: str | None = None) -> None:
        result = self.metadata()
        result["status"] = status
        if status in {"completed", "stopped", "faulted", "interrupted"}:
            self.index()
            result["final_cursor"] = max(
                1, self.entries[-1]["index"] + 1 if self.entries else 1
            )
        if error is not None:
            result["error"] = error
        self._atomic("manifest.json", result)

    def index(self) -> None:
        journal = self.directory / "journal.jsonl"
        if not journal.exists():
            return
        previous_count = len(self.entries)
        with journal.open("rb") as stream:
            stream.seek(self.offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                record = json.loads(line)
                index = record["index"]
                expected = self.entries[-1]["index"] + 1 if self.entries else 0
                if type(index) is not int or index != expected:
                    raise ValueError(f"WAL index gap: expected {expected}, got {index}")
                self.entries.append(
                    {"index": index, "offset": self.offset, "length": len(line)}
                )
                self.offset += len(line)
        if (
            len(self.entries) != previous_count
            or not (self.directory / "index.json").exists()
        ):
            self._atomic("index.json", self.entries)

    def records(self, start: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        """Read only complete indexed records; never advance or rewrite a run."""
        index = self.directory / "index.json"
        if not index.exists():
            raise FileNotFoundError(f"Run index missing: {index}")
        info = index.stat()
        entries = _read_index(
            index, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        )
        result: list[dict[str, Any]] = []
        if not entries:
            return result
        offset = bisect_left(entries, start, key=lambda entry: entry["index"])
        journal = self.directory / "journal.jsonl"
        info = journal.stat()
        major, budget = _journal_format(
            journal, (info.st_dev, info.st_ino, entries[0]["length"])
        )
        with journal.open("rb") as stream:
            for entry in entries[offset : offset + limit]:
                stream.seek(entry["offset"])
                record = parse_json(stream.read(entry["length"]), budget)
                if (
                    not isinstance(record, dict)
                    or record.get("index") != entry["index"]
                ):
                    raise ValueError("WAL record differs from its indexed coordinate")
                if major == 2 and entry["index"] != 0:
                    record = decode_frame(record, budget)
                result.append(expand_record(record))
                if len(result) >= limit:
                    break
        return result
