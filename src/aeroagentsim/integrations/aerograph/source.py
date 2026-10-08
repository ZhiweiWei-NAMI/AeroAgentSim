"""Read source JSON and Git metadata without upstream imports or Git commands."""

from __future__ import annotations

import hashlib
import struct
import zlib
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


def gated(data: dict[str, Any]) -> bool:
    return (
        str(data.get("id", "")).startswith("proposal:")
        or data.get("reviewStatus") in ("proposed", "conflict")
        or data.get("integrationDisposition")
        in ("quarantined", "candidate_not_accepted")
        or data.get("acceptedAsCompleteContract") is False
    )


class Sources:
    """Index persisted definitions only; no generated samples or browser data."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.documents: dict[str, Any] = {}
        self.hashes: dict[str, str] = {}
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
            self.hashes[name] = hashlib.sha256(raw).hexdigest()
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

    def git_provenance(self) -> dict[str, Any]:
        """Measure tracked worktree/index differences by reading local metadata.

        Unrelated untracked/ignored files are outside the stated dirty scope.
        Packed objects/unsupported index formats are reported as unmeasured.
        """
        git = self.root / ".git"
        if git.is_file():
            text = git.read_text().strip()
            if not text.startswith("gitdir: "):
                return {
                    "head": None,
                    "dirty": None,
                    "reason": "invalid .git indirection",
                }
            git = (self.root / text[8:]).resolve()
        if not git.is_dir():
            return {"head": None, "dirty": None, "reason": "source has no Git metadata"}
        common = git
        if (git / "commondir").is_file():
            common = (git / (git / "commondir").read_text().strip()).resolve()
        head: str | None = None
        scope = "tracked worktree/index and compiler input files; unrelated untracked files excluded"
        try:
            head = (git / "HEAD").read_text().strip()
            if head.startswith("ref: "):
                ref = head[5:]
                refpath = common / ref
                if refpath.is_file():
                    head = refpath.read_text().strip()
                else:
                    refs = dict(
                        line.split(" ", 1)[::-1]
                        for line in (common / "packed-refs").read_text().splitlines()
                        if line and not line.startswith(("#", "^"))
                    )
                    head = refs[ref]
            raw = (git / "index").read_bytes()
            magic, version, count = struct.unpack_from("!4sII", raw)
            if magic != b"DIRC" or version not in (2, 3):
                raise ValueError(f"unsupported Git index v{version}")
            entries: dict[str, str] = {}
            modes: dict[str, int] = {}
            offset = 12
            for _ in range(count):
                start = offset
                mode = struct.unpack_from("!I", raw, offset + 24)[0]
                sha = raw[offset + 40 : offset + 60].hex()
                flags = struct.unpack_from("!H", raw, offset + 60)[0]
                if flags & 0x3000:
                    return {
                        "head": head,
                        "dirty": True,
                        "dirty_scope": scope,
                        "reason": "unmerged Git index entries",
                    }
                offset += 62 + (2 if flags & 0x4000 else 0)
                end = raw.index(b"\0", offset)
                name = raw[offset:end].decode("utf-8")
                entries[name], modes[name] = sha, mode
                offset = start + ((end + 1 - start + 7) // 8) * 8
            changed = []
            for name, sha in entries.items():
                path = self.root / name
                if modes[name] == 0o160000:
                    raise ValueError(
                        "submodule dirty state requires external measurement"
                    )
                if not path.exists() and not path.is_symlink():
                    changed.append(name)
                    continue
                body = (
                    str(path.readlink()).encode()
                    if path.is_symlink()
                    else path.read_bytes()
                )
                actual = hashlib.sha1(
                    b"blob " + str(len(body)).encode() + b"\0" + body
                ).hexdigest()
                executable_changed = (modes[name] & 0o111 != 0) != (
                    path.stat().st_mode & 0o111 != 0
                )
                if actual != sha or (not path.is_symlink() and executable_changed):
                    changed.append(name)
            untracked_inputs = sorted(set(self.hashes) - entries.keys())
            if changed or untracked_inputs:
                return {
                    "head": head,
                    "dirty": True,
                    "dirty_scope": scope,
                    "changed_tracked": sorted(changed),
                    "untracked_inputs": untracked_inputs,
                }

            def object_body(oid: str) -> bytes:
                obj = zlib.decompress(
                    (common / "objects" / oid[:2] / oid[2:]).read_bytes()
                )
                return obj.split(b"\0", 1)[1]

            baseline: dict[str, str] = {}
            baseline_modes: dict[str, int] = {}

            def tree(oid: str, prefix: str) -> None:
                data, position = object_body(oid), 0
                while position < len(data):
                    end = data.index(b"\0", position)
                    mode, name = data[position:end].split(b" ", 1)
                    sha = data[end + 1 : end + 21].hex()
                    path = prefix + name.decode("utf-8")
                    if mode == b"40000":
                        tree(sha, path + "/")
                    else:
                        baseline[path] = sha
                        baseline_modes[path] = int(mode, 8)
                    position = end + 21

            tree(object_body(head).splitlines()[0].split(b" ")[1].decode(), "")
            return {
                "head": head,
                "dirty": baseline != entries or baseline_modes != modes,
                "dirty_scope": scope,
            }
        except (OSError, ValueError, KeyError, struct.error, zlib.error) as exc:
            return {
                "head": head,
                "dirty": None,
                "dirty_scope": scope,
                "reason": f"Git metadata measurement unavailable: {exc}",
            }
