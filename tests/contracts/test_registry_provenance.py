"""Scenario registries must come from AeroGraph; extensions must inherit it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aerokernel import MemoryRegistry

from aeroagentsim.integrations.aerograph import CompiledRegistry
from aeroagentsim.scenario.loader import ScenarioError, load_registry

BEHAVIOURS = Path("scenarios/behaviours/registry.snapshot.json").resolve()


def _stub(path: Path) -> None:
    stub = CompiledRegistry(
        MemoryRegistry(()),
        {
            "relations": [],
            "provenance": {"source": "hand-written; no checkout required"},
            "normalizations": [],
            "exclusions": [],
            "producer_hints": {},
            "statistics": {},
            "effective_fields": {},
        },
    )
    stub.write_snapshot(path)


def test_hand_written_snapshot_is_rejected(tmp_path: Path) -> None:
    _stub(tmp_path / "registry.snapshot.json")
    with pytest.raises(ScenarioError, match="must be compiled from AeroGraph"):
        load_registry({"snapshot": "registry.snapshot.json"}, base=tmp_path)


def test_compiled_snapshot_without_descriptors_is_rejected(tmp_path: Path) -> None:
    compiled = CompiledRegistry(
        MemoryRegistry(()),
        {
            "relations": [],
            "provenance": {"source": "claims compilation"},
            "normalizations": [],
            "exclusions": [],
            "producer_hints": {},
            "statistics": {},
            "effective_fields": {},
        },
    )
    # Keep the compiler revision but omit the descriptor provenance.
    payload = compiled.to_data()
    payload["registry"]["revision"] = "aerograph-compiler/1"
    (tmp_path / "registry.snapshot.json").write_text(json.dumps(payload))
    with pytest.raises(ScenarioError, match="must be compiled from AeroGraph"):
        load_registry({"snapshot": "registry.snapshot.json"}, base=tmp_path)


def test_local_root_type_is_rejected() -> None:
    with pytest.raises(ScenarioError, match="must inherit"):
        load_registry(
            {
                "snapshot": str(BEHAVIOURS),
                "types": [{"id": "x:Root", "parents": [], "abstract": False}],
            },
            base=Path("."),
        )


def test_local_type_must_resolve_its_parent() -> None:
    with pytest.raises(ScenarioError, match="not in the compiled AeroGraph"):
        load_registry(
            {
                "snapshot": str(BEHAVIOURS),
                "types": [
                    {"id": "x:Child", "parents": ["x:Missing"], "abstract": False}
                ],
            },
            base=Path("."),
        )


def test_local_type_may_extend_the_compiled_hierarchy() -> None:
    _, registry = load_registry(
        {
            "snapshot": str(BEHAVIOURS),
            "types": [
                {"id": "x:Child", "parents": ["oo:ModelObject"], "abstract": False}
            ],
        },
        base=Path("."),
    )
    assert registry.is_a("x:Child", "oo:ModelObject")


def test_disconnected_local_cycle_is_rejected() -> None:
    with pytest.raises(ScenarioError, match="acyclically"):
        load_registry(
            {
                "snapshot": str(BEHAVIOURS),
                "types": [
                    {"id": "x:CycleA", "parents": ["x:CycleB"], "abstract": False},
                    {"id": "x:CycleB", "parents": ["x:CycleA"], "abstract": False},
                ],
            },
            base=Path("."),
        )


def test_install_lowalt_components_on_narrow_snapshot(tmp_path: Path) -> None:
    import yaml

    from aeroagentsim.packs.lowalt.template import install_components

    snapshot = tmp_path / "registry.snapshot.json"
    snapshot.write_bytes(BEHAVIOURS.read_bytes())
    document = yaml.safe_load(Path("scenarios/behaviours/minimal.yaml").read_text())
    document["registry"]["snapshot"] = "registry.snapshot.json"
    document.setdefault("presentation", [])
    install_components(document, directory=tmp_path)
    _, registry = load_registry(document["registry"], base=tmp_path)
    assert registry.is_a("lowalt:UAV", "oo:UAV")


