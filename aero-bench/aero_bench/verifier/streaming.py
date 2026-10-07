"""Bounded JSONL input buffers with unchanged authoritative-ledger validation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.runtime.evidence import validate_authoritative_runtime_ledger
from aero_bench.runtime.ledger import EventLedger, LedgerRecord
from aero_bench.serialization import canonical_json_bytes


def _read_records(stream: BinaryIO, *, size_bytes: int, sha256: str) -> EventLedger:
    digest = hashlib.sha256()
    consumed = 0
    records: list[LedgerRecord] = []
    while True:
        # A malformed file cannot allocate more than its sealed size. A valid
        # file retains only the current line in addition to its typed records.
        line = stream.readline(size_bytes - consumed + 1)
        if not line:
            break
        consumed += len(line)
        if consumed > size_bytes:
            raise ValueError("sealed event.log size does not match the seal")
        digest.update(line)
        if not line.endswith(b"\n") or line == b"\n":
            raise ValueError("authoritative event ledger must be non-empty canonical JSONL")
        try:
            record = LedgerRecord.model_validate(json.loads(line.decode("utf-8")))
        except (UnicodeError, ValueError, TypeError) as error:
            raise ValueError("authoritative event ledger contains invalid JSON") from error
        if line != canonical_json_bytes(record.model_dump(mode="json")) + b"\n":
            raise ValueError("authoritative event ledger record is not canonical JSON")
        records.append(record)
    if consumed != size_bytes:
        raise ValueError("sealed event.log size does not match the seal")
    if digest.hexdigest() != sha256:
        raise ValueError("sealed event.log digest does not match the seal")
    return EventLedger.from_records(tuple(records))


def load_sealed_event_ledger_streaming(
    *, run: ResolvedRunSpec, seal: SealManifest, seal_root: Path
) -> EventLedger:
    """Read one line at a time; preserve identity, pin, chain and protocol gates.

    Verdict semantics still require the typed history. This eliminates whole-file
    byte/split buffers, not the history or any authoritative validation.
    """
    if seal.run_id != run.run_id or seal.execution_scope != run.execution_scope:
        raise ValueError("runtime seal identity does not match ResolvedRun")
    requirements = tuple(r for r in run.artifact_requirements if r.artifact_type == "event.log")
    artifacts = tuple(r for r in seal.artifacts if r.artifact_type == "event.log")
    if len(requirements) != 1 or len(artifacts) != 1:
        raise ValueError("runtime seal requires one declared event.log")
    requirement, artifact = requirements[0], artifacts[0]
    if (
        artifact.artifact_id != requirement.artifact_id
        or artifact.producer_id != requirement.producer_id
        or artifact.producer_id != "harness"
        or artifact.visibility != requirement.visibility
        or artifact.visibility != "private"
        or artifact.relative_path != requirement.relative_path
    ):
        raise ValueError("sealed event.log does not match its artifact requirement")
    if artifact.size_bytes > requirement.max_size_bytes:
        raise ValueError("sealed event.log exceeds its declared maximum size")
    root = seal_root.absolute()
    if root != root.resolve(strict=True):
        raise ValueError("runtime seal root must not contain symbolic links")
    relative = PurePosixPath(artifact.relative_path)
    if relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
        raise ValueError("sealed event.log path must be normalized and relative")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    directory = os.open(root, flags | os.O_DIRECTORY)
    try:
        for part in relative.parts[:-1]:
            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        descriptor = os.open(relative.parts[-1], flags, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != artifact.size_bytes:
                raise ValueError("sealed event.log must match the sealed regular file")
            ledger = _read_records(stream, size_bytes=artifact.size_bytes, sha256=artifact.sha256)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                raise ValueError("sealed event.log changed while being read")
    finally:
        os.close(directory)
    validate_authoritative_runtime_ledger(run, ledger)
    if ledger.chain_root != seal.event_chain_root:
        raise ValueError("sealed event.log chain root does not match the seal")
    return ledger
