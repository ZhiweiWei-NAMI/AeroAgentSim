"""Journal offsets and manifests are rebuildable, read-only viewer indexes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aerokernel.values import canonical_json

if TYPE_CHECKING:
    from aeroagentsim.scenario import Scenario


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
                "scenario_digest": scenario.digest,
                "registry_digest": scenario.compiled.digest,
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
        if error is not None:
            result["error"] = error
        self._atomic("manifest.json", result)

    def index(self) -> None:
        journal = self.directory / "journal.jsonl"
        if not journal.exists():
            return
        with journal.open("rb") as stream:
            stream.seek(self.offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                record = json.loads(line)
                index = record["index"]
                self.entries.append(
                    {"index": index, "offset": self.offset, "length": len(line)}
                )
                self.offset += len(line)
        self._atomic("index.json", self.entries)

    def records(self, start: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        """Read only complete indexed records; never advance or rewrite a run."""
        index = self.directory / "index.json"
        if not index.exists():
            raise FileNotFoundError(f"Run index missing: {index}")
        entries: list[dict[str, int]] = json.loads(index.read_text())
        result: list[dict[str, Any]] = []
        with (self.directory / "journal.jsonl").open("rb") as stream:
            for entry in entries:
                if entry["index"] < start:
                    continue
                stream.seek(entry["offset"])
                result.append(json.loads(stream.read(entry["length"])))
                if len(result) >= limit:
                    break
        return result
