"""Journal offsets and manifests are rebuildable, read-only viewer indexes."""

from __future__ import annotations

import copy
import hashlib
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
) -> tuple[int, ResourceBudget, bool]:
    """Cache the immutable header's format/budget, not its full scenario payload."""
    with RecordReader(path) as reader:
        header = next(reader)
        assert reader.budget is not None
        return header["major"], reader.budget, header.get("provenance") == "lean"


def committed_prefix(directory: Path) -> dict[str, int] | None:
    """The worker's acknowledged WAL coordinate, separate from derived offsets.

    Older/headless runs publish index.json only. Never infer a live commit from
    file size: bytes can be visible before their durability acknowledgement.
    """
    path = directory / "committed-prefix.json"
    if not path.exists():
        return None
    prefix = json.loads(path.read_text())
    if (
        not isinstance(prefix, dict)
        or set(prefix) != {"bytes", "index"}
        or any(type(value) is not int or value < 0 for value in prefix.values())
    ):
        raise ValueError("Invalid acknowledged WAL prefix")
    return prefix  # type: ignore[no-any-return]


@lru_cache(maxsize=4096)
def _summary_configuration(path: Path, identity: tuple[int, int, int, int]) -> dict[str, Any]:
    document = json.loads(path.read_text())
    return {"id": document["id"], "algorithms": document.get("algorithms", {})}


def summary_configuration(directory: Path) -> dict[str, Any]:
    """Read the real pinned declarations once, retaining only summary fields."""
    path = (directory / "scenario.json").resolve()
    stat = path.stat()
    return copy.deepcopy(_summary_configuration(
        path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    ))


class RunStorage:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.offset = 0
        self.entries: list[dict[str, int]] = []
        self._live_snapshot = False
        self._published_prefix: tuple[int, int] | None = None
        index = directory / "index.json"
        if index.exists():
            info = index.stat()
            # Readers share the decoded atomic version, while a writer owns its
            # list and rows: appending/rebuilding cannot mutate another reader.
            self.entries = [
                dict(entry)
                for entry in _read_index(
                    index, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                )
            ]
            if self.entries:
                last = self.entries[-1]
                self.offset = last["offset"] + last["length"]

    def prepare(self, scenario: Scenario) -> None:
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / "scenario.yaml").write_text(scenario.source)
        document = canonical_json(scenario.document)
        (self.directory / "scenario.json").write_bytes(document)
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
                "configuration_digest": hashlib.sha256(document).hexdigest(),
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

    def status(
        self,
        status: str,
        *,
        error: str | None = None,
        waiting: dict[str, Any] | None = None,
    ) -> None:
        result = self.metadata()
        result["status"] = status
        if waiting is None:
            result.pop("waiting", None)
        else:
            result["waiting"] = waiting
        if status in {
            "completed",
            "stopped",
            "faulted",
            "interrupted",
            "input_timeout",
        }:
            self.index()
            prefix = committed_prefix(self.directory)
            result["final_cursor"] = max(
                1, prefix["index"] + 1 if prefix is not None
                else self.entries[-1]["index"] + 1 if self.entries else 1
            )
        if error is not None:
            result["error"] = error
        elif status not in {"faulted", "interrupted", "input_timeout"}:
            result.pop("error", None)
        self._atomic("manifest.json", result)

    def publish_prefix(self, byte_length: int, index: int) -> None:
        """Publish only a coordinate supplied after the kernel acknowledged it."""
        coordinate = (byte_length, index)
        if coordinate != self._published_prefix:
            self._atomic("committed-prefix.json", {"bytes": byte_length, "index": index})
            self._published_prefix = coordinate

    def refresh_committed(self) -> None:
        """Extend this reader's offsets to the acknowledged live prefix only."""
        prefix = committed_prefix(self.directory)
        if prefix is None:
            return
        if prefix["bytes"] < self.offset:
            # index.json can be published during finalization before the small
            # live coordinate. A new reader must honor that coordinate too.
            self.entries = [
                entry for entry in self.entries
                if entry["offset"] + entry["length"] <= prefix["bytes"]
            ]
            self.offset = (
                self.entries[-1]["offset"] + self.entries[-1]["length"]
                if self.entries else 0
            )
        if prefix["bytes"] > self.offset:
            self.index(publish=False, through_bytes=prefix["bytes"])
        if self.offset != prefix["bytes"] or (
            self.entries[-1]["index"] if self.entries else 0
        ) != prefix["index"]:
            raise ValueError("Acknowledged WAL index differs from its byte coordinate")
        self._live_snapshot = True

    def index(self, *, publish: bool = True, through_bytes: int | None = None) -> None:
        journal = self.directory / "journal.jsonl"
        if not journal.exists():
            return
        previous_count = len(self.entries)
        with journal.open("rb") as stream:
            if through_bytes is not None and through_bytes > journal.stat().st_size:
                raise ValueError("Acknowledged WAL prefix is beyond the journal")
            stream.seek(self.offset)
            while through_bytes is None or self.offset < through_bytes:
                line = stream.readline(
                    -1 if through_bytes is None else through_bytes - self.offset
                )
                if not line:
                    break
                if not line.endswith(b"\n"):
                    if through_bytes is not None:
                        raise ValueError("Acknowledged WAL prefix ends inside a record")
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
        if publish and (
            len(self.entries) != previous_count
            or not (self.directory / "index.json").exists()
        ):
            self._atomic("index.json", self.entries)

    def records(self, start: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        """Read only complete indexed records; never advance or rewrite a run."""
        index = self.directory / "index.json"
        if not index.exists():
            raise FileNotFoundError(f"Run index missing: {index}")
        if self._live_snapshot:
            entries = self.entries
        else:
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
        major, budget, lean = _journal_format(
            journal, (info.st_dev, info.st_ino, entries[0]["length"])
        )
        # Match RecordReader's admitted format. Lean records are canonical JSON,
        # not positional trees, and need only their one strict parsing pass.
        frame_budget = (
            ResourceBudget(
                budget.integer_digits, budget.frame_bytes, budget.nesting_depth * 2 + 2
            )
            if lean
            else budget
        )
        with journal.open("rb") as stream:
            for entry in entries[offset : offset + limit]:
                stream.seek(entry["offset"])
                record = parse_json(stream.read(entry["length"]), frame_budget)
                if (
                    not isinstance(record, dict)
                    or record.get("index") != entry["index"]
                ):
                    raise ValueError("WAL record differs from its indexed coordinate")
                if major == 2 and entry["index"] != 0:
                    record = decode_frame(record, budget, lean=lean)
                result.append(expand_record(record))
                if len(result) >= limit:
                    break
        return result
