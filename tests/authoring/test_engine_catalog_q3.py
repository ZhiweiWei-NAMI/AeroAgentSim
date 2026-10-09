"""Installed entry-point factories participate in Studio without being started."""

from __future__ import annotations

import sys
from importlib.metadata import EntryPoint
from types import ModuleType
from typing import Any

import pytest
from aerokernel import BindingManifest, MemoryRegistry, Partition, TypeDescriptor
from aerokernel.sdk import ContextEngine, EngineContext

from aeroagentsim.authoring.catalog import engines
from aeroagentsim.platform import plugins
from aeroagentsim.platform.plugins import BUILTINS, EngineBuild, EngineCatalog


class ScratchEngine(ContextEngine):
    def __init__(self, context: EngineBuild) -> None:
        self.config = context.config
        super().__init__(Partition(context.id, context.id))

    def bootstrap(self, ctx: EngineContext) -> None:
        pass

    def on_inputs(self, ctx: EngineContext) -> None:
        pass


def install(
    monkeypatch: pytest.MonkeyPatch, *, descriptor: Any = None, name: str = "scratch"
) -> list[EngineBuild]:
    calls: list[EngineBuild] = []

    def factory(context: EngineBuild) -> ScratchEngine:
        calls.append(context)
        return ScratchEngine(context)

    if descriptor is not None:
        factory.config_schema = descriptor  # type: ignore[attr-defined]
    module = ModuleType("q3_scratch_engine")
    module.build = factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    entries = [
        EntryPoint(
            name=name, value="q3_scratch_engine:build", group="aeroagentsim.engines"
        )
    ]

    def points(*, group: str) -> list[EntryPoint]:
        assert group == "aeroagentsim.engines"
        return entries

    monkeypatch.setattr(plugins, "entry_points", points)
    return calls


def test_installed_plugin_discovery_descriptor_and_real_factory_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {
        "type": "object",
        "properties": {"rate": {"type": "number"}},
        "required": ["rate"],
    }
    calls = install(monkeypatch, descriptor=schema)
    listing = {row["id"]: row for row in engines()}
    assert set(BUILTINS) | {"scratch"} == set(listing)
    assert {"records", "threshold", "agent-assignment"} <= set(listing)
    assert listing["scratch"]["available"] is True
    assert listing["scratch"]["config_schema"] == schema
    assert calls == []
    registry = MemoryRegistry((TypeDescriptor("Record"),))
    context = EngineBuild(
        "scratch-instance",
        {"rate": 2.5},
        registry,
        BindingManifest("run", "0", ()),
        (),
        {},
    )
    built = EngineCatalog().build("scratch", context)
    assert isinstance(built, ScratchEngine)
    assert built.config == {"rate": 2.5}
    assert calls == [context]


def test_absent_descriptor_uses_raw_editor_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install(monkeypatch)
    row = next(row for row in engines() if row["id"] == "scratch")
    assert row["available"] is True
    assert "config_schema" not in row
    assert calls == []


def test_entry_points_override_builtin_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install(
        monkeypatch, name="records", descriptor={"type": "object", "properties": {}}
    )
    catalog = EngineCatalog()
    assert catalog.names().count("records") == 1
    assert catalog.config_schema("records") == {"type": "object", "properties": {}}
    assert calls == []


@pytest.mark.parametrize(
    "descriptor",
    [False, {"type": "array"}, {"type": "object", "default": float("nan")}],
)
def test_malformed_descriptors_are_visible_errors(
    monkeypatch: pytest.MonkeyPatch, descriptor: Any
) -> None:
    calls = install(monkeypatch, descriptor=descriptor)
    row = next(row for row in engines() if row["id"] == "scratch")
    assert row["available"] is False
    assert row["error"]
    assert "config_schema" not in row
    assert calls == []


def test_broken_installed_entry_is_reported_and_other_engines_remain_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch)
    point = EntryPoint(
        name="broken", value="q3_nonexistent_module:build", group="aeroagentsim.engines"
    )
    monkeypatch.setattr(plugins, "entry_points", lambda **kwargs: [point])
    rows = {row["id"]: row for row in engines()}
    assert rows["broken"]["available"] is False
    assert "ModuleNotFoundError" in rows["broken"]["error"]
    assert rows["records"]["available"] is True


def test_duplicate_entry_points_rejected_from_single_discovery_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point = EntryPoint(
        name="scratch", value="q3_scratch_engine:build", group="aeroagentsim.engines"
    )
    calls = 0

    def points(*, group: str) -> list[EntryPoint]:
        nonlocal calls
        calls += 1
        return [point, point]

    monkeypatch.setattr(plugins, "entry_points", points)
    with pytest.raises(ValueError, match="Duplicate"):
        EngineCatalog()
    assert calls == 1
