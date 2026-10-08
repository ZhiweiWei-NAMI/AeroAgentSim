"""Input inventory, evidence locations and a deliberately bounded schema validator."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def escape(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def reject_constant(value: str):
    raise ValueError(f"Non-finite JSON constant {value} is not valid ontology data")


@dataclass(frozen=True)
class Record:
    data: dict
    artifact: str
    pointer: str

    @property
    def id(self) -> str | None:
        identity = self.data.get("id")
        return identity if isinstance(identity, str) else None


def schema_errors(value: Any, schema: dict, pointer: str = "") -> list[tuple[str, str]]:
    """Validate the keywords present in the shipped contract schemas, not all JSON Schema."""
    errors: list[tuple[str, str]] = []
    kinds = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float),
        "boolean": lambda v: type(v) is bool,
        "null": lambda v: v is None,
    }
    if "anyOf" in schema and all(
        schema_errors(value, option) for option in schema["anyOf"]
    ):
        errors.append((pointer, "Value matches no anyOf branch"))
    if "const" in schema and (
        type(value) is not type(schema["const"]) or value != schema["const"]
    ):
        errors.append((pointer, f"Expected const {schema['const']!r}"))
    if "enum" in schema and not any(
        type(value) is type(v) and value == v for v in schema["enum"]
    ):
        errors.append((pointer, f"Value {value!r} outside enum {schema['enum']!r}"))
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(t in kinds and kinds[t](value) for t in types):
            return errors + [
                (pointer, f"Expected type {schema['type']}, got {type(value).__name__}")
            ]
    if (
        type(value) in (int, float)
        and "minimum" in schema
        and value < schema["minimum"]
    ):
        errors.append((pointer, f"Value below minimum {schema['minimum']}"))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append((pointer + "/" + escape(key), "Required key missing"))
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors.extend(
                    schema_errors(value[key], sub, pointer + "/" + escape(key))
                )
        if schema.get("additionalProperties") is False:
            for key in value.keys() - schema.get("properties", {}).keys():
                errors.append(
                    (pointer + "/" + escape(key), "Additional property forbidden")
                )
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errors.extend(schema_errors(item, schema["items"], pointer + f"/{i}"))
    return errors


class Audit:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.documents: dict[str, Any] = {}
        self.texts: dict[str, str] = {}
        self.inventory: dict[str, dict] = {}
        self.findings: list[dict] = []
        self.metrics: dict[str, Any] = {}
        for name in (
            "entities",
            "fields",
            "relations",
            "rules",
            "predicates",
            "events",
            "profiles",
            "sources",
        ):
            setattr(self, name, {})
        self.entity_rows: list[Record] = []
        self.field_rows: list[Record] = []
        self.relation_rows: list[Record] = []
        self.semantic_rows: list[Record] = []
        self.schema_errors = schema_errors

    def add(
        self,
        check: str,
        severity: str,
        record: Record | str,
        message: str,
        *,
        pointer: str | None = None,
        count: int = 1,
        details: Any = None,
    ) -> None:
        artifact = record.artifact if isinstance(record, Record) else record
        base = record.pointer if isinstance(record, Record) else ""
        evidence_pointer = base if pointer is None else pointer
        if artifact in self.documents and evidence_pointer.startswith("/"):
            value = self.documents[artifact]
            parts = []
            for token in evidence_pointer[1:].split("/"):
                decoded = token.replace("~1", "/").replace("~0", "~")
                try:
                    value = (
                        value[int(decoded)]
                        if isinstance(value, list)
                        else value[decoded]
                    )
                except (KeyError, IndexError, TypeError, ValueError):
                    break
                parts.append(token)
            actual_pointer = "/" + "/".join(parts) if parts else ""
            if actual_pointer != evidence_pointer:
                details = {"requested_pointer": evidence_pointer, "context": details}
                evidence_pointer = actual_pointer
        finding = {
            "check": check,
            "severity": severity,
            "artifact": artifact,
            "object_id": record.id if isinstance(record, Record) else None,
            "message": message,
            "evidence": {
                "path": artifact,
                "pointer": evidence_pointer,
            },
            "count": count,
        }
        if details is not None:
            finding["details"] = details
        self.findings.append(finding)

    def read(self, path: Path | str) -> Any:
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"Input escapes AeroGraph root: {path}")
        relative = str(path.relative_to(self.root))
        if relative in self.documents:
            return self.documents[relative]
        raw = path.read_bytes()
        self.inventory[relative] = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        try:
            result = json.loads(raw, parse_constant=reject_constant)
        except (ValueError, UnicodeDecodeError) as error:
            self.add("input.invalid_json", "blocker", relative, str(error))
            result = None
        self.documents[relative] = result
        return result

    def read_text(self, path: Path | str) -> str:
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        if not path.resolve().is_relative_to(self.root):
            raise ValueError(f"Input escapes AeroGraph root: {path}")
        relative = str(path.relative_to(self.root))
        if relative not in self.texts:
            raw = path.read_bytes()
            self.inventory[relative] = {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            self.texts[relative] = raw.decode("utf-8")
        return self.texts[relative]

    def rows(self, path: str, key: str | None = None) -> list[Record]:
        value = self.read(path)
        if key is not None:
            value = value.get(key) if isinstance(value, dict) else None
        if not isinstance(value, list):
            self.add(
                "input.record_array",
                "blocker",
                path,
                f"Expected record array at {key or '/'}",
            )
            return []
        result = []
        for i, row in enumerate(value):
            pointer = ("/" + escape(key) if key else "") + f"/{i}"
            if not isinstance(row, dict):
                self.add(
                    "input.record_object",
                    "blocker",
                    path,
                    "Record is not an object",
                    pointer=pointer,
                )
            else:
                result.append(Record(row, path, pointer))
        return result

    def index(self, rows: list[Record], label: str) -> dict[str, Record]:
        result = {}
        for row in rows:
            if not row.id:
                self.add(
                    label + ".missing_id",
                    "blocker",
                    row,
                    "Missing nonempty string identity",
                )
            elif row.id in result:
                self.add(
                    label + ".duplicate_id",
                    "blocker",
                    row,
                    "Duplicate identity; first definition retained for continued diagnostics",
                    details={
                        "first": {
                            "path": result[row.id].artifact,
                            "pointer": result[row.id].pointer,
                        }
                    },
                )
            else:
                result[row.id] = row
        return result

    def ancestors(self, identity: str) -> set[str]:
        result = set()
        while identity in self.entities and identity not in result:
            result.add(identity)
            row = self.entities[identity].data
            if row.get("parentStatus") == "suggested":
                break
            identity = row.get("parent")
        return result

    def effective_fields(self, identity: str) -> list[Record]:
        ids = {
            f
            for t in self.ancestors(identity)
            for f in self.entities[t].data.get("ownFieldIds", [])
            if isinstance(f, str)
        }
        return [self.fields[f] for f in sorted(ids) if f in self.fields]

    def load(self) -> None:
        self.entity_rows = self.rows("entity-directory/data/concepts.json", "concepts")
        self.entities = self.index(self.entity_rows, "entity")
        for path in sorted((self.root / "entity-directory").rglob("*.json")):
            self.read(path)
        manifest = self.read("semantic-directory/data/definitions.json")
        if isinstance(manifest, list):
            self.field_rows = self.rows("semantic-directory/data/definitions.json")
        elif isinstance(manifest, dict):
            for i, part in enumerate(manifest.get("parts", [])):
                name = "semantic-directory/data/" + part["path"]
                path = self.root / name
                if not path.is_file():
                    self.add(
                        "input.missing_shard",
                        "blocker",
                        "semantic-directory/data/definitions.json",
                        name,
                        pointer=f"/parts/{i}/path",
                    )
                    continue
                rows = self.rows(name)
                self.field_rows.extend(rows)
                observed = {"count": len(rows), **self.inventory[name]}
                for key in ("count", "bytes", "sha256"):
                    if key in part and observed[key] != part[key]:
                        self.add(
                            "cross.shard_integrity",
                            "blocker",
                            "semantic-directory/data/definitions.json",
                            f"Shard {part['path']} {key}: claimed {part[key]}, actual {observed[key]}",
                            pointer=f"/parts/{i}/{key}",
                        )
            if manifest.get("recordCount") != len(self.field_rows):
                self.add(
                    "cross.field_count",
                    "major",
                    "semantic-directory/data/definitions.json",
                    f"Claimed {manifest.get('recordCount')} source fields; loaded {len(self.field_rows)}",
                    pointer="/recordCount",
                )
            semantic_hash = hashlib.sha256(
                json.dumps(
                    [r.data for r in self.field_rows],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            self.metrics["source_fields_semantic_sha256"] = semantic_hash
            if (
                manifest.get("semanticSha256") is not None
                and manifest["semanticSha256"] != semantic_hash
            ):
                self.add(
                    "cross.semantic_hash",
                    "blocker",
                    "semantic-directory/data/definitions.json",
                    "Insertion-order source field semantic digest differs from manifest",
                    pointer="/semanticSha256",
                )
        self.metrics["source_field_count"] = len(self.field_rows)
        self.relation_rows = self.rows("semantic-directory/data/relations.json")
        source_value = self.read("semantic-directory/data/sources.json")
        source_rows = self.rows(
            "semantic-directory/data/sources.json",
            "sources" if isinstance(source_value, dict) else None,
        )
        groups = {k: [] for k in ("rules", "predicates", "events", "profiles")}
        for path in sorted((self.root / "semantic-directory/data").rglob("*.json")):
            name = str(path.relative_to(self.root))
            doc = self.read(path)
            if (
                name.endswith(("/pilot.json", "/capability-definitions.json"))
                or "/expanded/" in name
            ):
                if not isinstance(doc, dict):
                    continue
                for key, group in groups.items():
                    if key in doc:
                        group.extend(self.rows(name, key))
                if "fields" in doc:
                    self.field_rows.extend(self.rows(name, "fields"))
                if "sources" in doc:
                    source_rows.extend(self.rows(name, "sources"))
                if "contracts" in doc:
                    for row in self.rows(name, "contracts"):
                        # Local audit descriptors mirror documented ID construction; no upstream code execution.
                        common = dict(row.data, contractId=row.id)
                        rule_id = "expanded:rule:" + row.id
                        groups["rules"].append(
                            Record(dict(common, id=rule_id), name, row.pointer)
                        )
                        pred_id = "expanded:predicate:" + row.id
                        groups["predicates"].append(
                            Record(
                                dict(common, id=pred_id, expression={"rule": rule_id}),
                                name,
                                row.pointer,
                            )
                        )
                        if row.data.get("event") == "entered":
                            groups["events"].append(
                                Record(
                                    dict(
                                        common,
                                        id="expanded:event:" + row.id + ":entered",
                                        predicateId=pred_id,
                                        expression={
                                            "op": "entered",
                                            "args": [{"predicate": pred_id}],
                                        },
                                    ),
                                    name,
                                    row.pointer,
                                )
                            )
        self.fields = self.index(self.field_rows, "field")
        self.relations = self.index(self.relation_rows, "relation")
        self.sources = self.index(source_rows, "source")
        for key, rows in groups.items():
            setattr(self, key, self.index(rows, key.rstrip("s")))
        self.semantic_rows = groups["rules"] + groups["predicates"] + groups["events"]
        decoded = self.root / "research/original-graph/decoded.json"
        if decoded.is_file():
            self.read(decoded)
        else:
            self.add(
                "input.original_graph_missing",
                "blocker",
                "research/original-graph/decoded.json",
                "Original rule graph is missing",
            )

    def git_state(self) -> dict:
        if not (self.root / ".git").exists():
            self.add(
                "input.git_unavailable",
                "info",
                ".git",
                "No Git metadata at source root; parent repository is not attributed to this fixture",
            )
            return {"head": None, "status_porcelain": None, "dirty": None}
        result: dict = {}
        for label, args in (
            ("head", ["rev-parse", "HEAD"]),
            ("status_porcelain", ["status", "--porcelain"]),
        ):
            process = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(self.root), *args],
                capture_output=True,
                text=True,
                check=False,
            )
            if process.returncode:
                result[label] = None
                self.add(
                    "input.git_unavailable",
                    "info",
                    ".git",
                    f"Cannot read {label}: {process.stderr.strip()}",
                )
            else:
                result[label] = process.stdout.rstrip("\n")
        result["dirty"] = (
            bool(result["status_porcelain"])
            if result["status_porcelain"] is not None
            else None
        )
        return result

    def finish(self) -> None:
        self.findings.sort(
            key=lambda f: (
                f["check"],
                f["artifact"],
                f["evidence"]["pointer"],
                f["message"],
            )
        )
        for finding in self.findings:
            content = json.dumps(finding, sort_keys=True, ensure_ascii=False)
            finding["id"] = "AG-" + hashlib.sha256(content.encode()).hexdigest()[:16]
