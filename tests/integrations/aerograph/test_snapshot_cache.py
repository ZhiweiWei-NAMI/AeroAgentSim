"""Snapshot reuse must follow file contents, including edits hidden by mtime."""

import json
import os

import pytest
from aerokernel.registry import MemoryRegistry, TypeDescriptor
from aerokernel.values import canonical_json

from aeroagentsim.integrations.aerograph.model import CompiledRegistry, read_snapshot


def test_relocated_snapshot_is_shared_and_exports_are_detached(tmp_path):
    source = tmp_path / "source.json"
    CompiledRegistry(MemoryRegistry((TypeDescriptor("cache:Type", (), False),)), {}).write_snapshot(source)
    relocated = tmp_path / "relocated.json"
    relocated.write_bytes(source.read_bytes())

    first = read_snapshot(source)
    second = read_snapshot(relocated)
    assert second is first  # Immutable descriptors can cross workspace copies.
    exported = first.to_data()
    exported["registry"]["types"].clear()
    assert read_snapshot(source).registry.types[0].id == "cache:Type"
    with pytest.raises(AttributeError, match="immutable"):
        second.registry.revision = "changed"


def test_rewrite_with_same_size_and_mtime_is_recompiled_and_errors_stay_visible(tmp_path):
    path = tmp_path / "snapshot.json"
    CompiledRegistry(MemoryRegistry((TypeDescriptor("cache:Rewrite", (), False),)), {}).write_snapshot(path)
    first = read_snapshot(path)
    original = path.stat()
    data = json.loads(path.read_bytes())
    data["registry"]["revision"] = "2"
    path.write_bytes(canonical_json(data))
    assert path.stat().st_size == original.st_size
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    second = read_snapshot(path)
    assert second is not first and second.registry.revision == "2"
    assert first.registry.revision == "1"

    path.write_text("{broken")
    with pytest.raises(ValueError):
        read_snapshot(path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        read_snapshot(path)
