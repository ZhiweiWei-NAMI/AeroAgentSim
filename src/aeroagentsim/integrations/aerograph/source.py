"""Read source JSON without upstream imports or Git commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aerokernel.errors import KernelError
from aerokernel.values import ResourceBudget, parse_json

from .model import CompileError


def escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


@dataclass(frozen=True)
class Record:
    data: dict[str, Any]
    file: str
    pointer: str

    def location(self) -> dict[str, str]:
        return {"file": self.file, "pointer": self.pointer}

    def error(self, message: str) -> CompileError:
        return CompileError(
            [f"{self.file}#{self.pointer} ({self.data.get('id')}): {message}"]
        )


PROPOSAL_PREFIX = "proposal:"


def proposal_candidate(data: dict[str, Any]) -> bool:
    """True for the explicit ``proposal:`` namespace of new design candidates."""
    return str(data.get("id", "")).startswith(PROPOSAL_PREFIX)


def review_status(data: dict[str, Any]) -> str | None:
    """Declared review status, or None when the definition declares none."""
    status = data.get("reviewStatus")
    return status if isinstance(status, str) else None


def review_gated(data: dict[str, Any]) -> bool:
    """True when the source carries a research gate marker.

    ``proposal:`` candidates and definitions declaring a review status other
    than ``reviewed`` are gate definitions. Admission never changes the source
    review state; it only records research use of such definitions.
    """
    return proposal_candidate(data) or review_status(data) not in (
        None,
        "reviewed",
    )


class Sources:
    """Index persisted definitions only; no generated samples or browser data."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.documents: dict[str, Any] = {}
        self.types: dict[str, list[Record]] = {}
        self.fields: dict[str, list[Record]] = {}
        self.relations: dict[str, list[Record]] = {}
        self.definitions: dict[str, Any] = {}
        self.rows("entity-directory/data/concepts.json", "concepts", self.types)
        manifest = self.read("semantic-directory/data/definitions.json")
        if isinstance(manifest, list):
            self.rows("semantic-directory/data/definitions.json", None, self.fields)
        elif isinstance(manifest, dict) and isinstance(manifest.get("parts"), list):
            for part in manifest["parts"]:
                if not isinstance(part, dict) or not isinstance(part.get("path"), str):
                    raise CompileError(
                        ["definitions.json: parts require explicit paths"]
                    )
                self.rows("semantic-directory/data/" + part["path"], None, self.fields)
        else:
            raise CompileError(
                ["definitions.json: expected field array or parts manifest"]
            )
        self.rows("semantic-directory/data/relations.json", None, self.relations)
        definitions_path = "semantic-directory/data/schema-definitions.json"
        if (self.root / definitions_path).is_file():
            self.definitions = self.read(definitions_path)
        for path in sorted((self.root / "semantic-directory/data").rglob("*.json")):
            name = path.relative_to(self.root).as_posix()
            if (
                name.endswith(("/pilot.json", "/capability-definitions.json"))
                or "/expanded/" in name
            ):
                doc = self.read(name)
                if isinstance(doc, dict) and "fields" in doc:
                    self.rows(name, "fields", self.fields)
                if isinstance(doc, dict) and "relations" in doc:
                    self.rows(name, "relations", self.relations)

    def read(self, name: str) -> Any:
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise CompileError([f"Input path escapes ontology root: {name}"])
        if name not in self.documents:
            try:
                raw = path.read_bytes()
                doc = parse_json(raw, ResourceBudget(frame_bytes=64 * 1024 * 1024))
            except (OSError, ValueError, KernelError) as exc:
                raise CompileError([f"{name}: cannot read source JSON: {exc}"]) from exc
            self.documents[name] = doc
        return self.documents[name]

    def rows(self, name: str, key: str | None, index: dict[str, list[Record]]) -> None:
        doc = self.read(name)
        value = doc.get(key) if key is not None and isinstance(doc, dict) else doc
        if not isinstance(value, list):
            raise CompileError([f"{name}: expected array at {key or '/'}"])
        for i, data in enumerate(value):
            pointer = ("/" + escape(key) if key is not None else "") + f"/{i}"
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("id"), str)
                or not data["id"]
            ):
                raise CompileError([f"{name}#{pointer}: record requires a nonempty ID"])
            index.setdefault(data["id"], []).append(Record(data, name, pointer))

    @staticmethod
    def get(index: dict[str, list[Record]], id: str, purpose: str) -> Record:
        rows = index.get(id, [])
        if not rows:
            raise CompileError(
                [
                    (
                        f"Unresolved {purpose} {id!r}; supply its persisted definition "
                        "or explicitly list it in Policy.excluded_ids"
                    )
                ]
            )
        if len(rows) != 1:
            raise CompileError(
                [
                    f"Duplicate {purpose} {id!r}: "
                    + ", ".join(f"{r.file}#{r.pointer}" for r in rows)
                    + "; resolve upstream or exclude the ID"
                ]
            )
        return rows[0]
