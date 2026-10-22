"""Public, versioned contracts and discovery for scenario plugins."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from aeroagentsim.extensions.discovery import (
    DuplicateEntryPointError,
    discover,
    validate_api_version,
    validate_name,
)

API_VERSION = 1


class ApplyHook(Protocol):
    """Pure, deterministic mutation of a scenario document for one knob.

    A hook must be deterministic and may only edit ``document``. Invalid values
    must raise ``ValueError`` with a ``knob <id>:``-style message. No I/O is
    permitted except reading files under ``base``, the scenario source directory.
    """

    def __call__(self, document: dict[str, Any], base: Path, value: Any) -> None: ...


class KnobProvider(Protocol):
    """Derive extra knob declarations from the scenario document."""

    def __call__(self, document: dict[str, Any]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class TemplateSource:
    """A template directory, optional absolute scenario override and extra knobs."""

    directory: Path
    scenario: Path | None = None
    extra_knobs: KnobProvider | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.directory, Path):
            raise ValueError("template source directory: expected a Path")
        if self.scenario is not None and (
            not isinstance(self.scenario, Path) or not self.scenario.is_absolute()
        ):
            raise ValueError("template source scenario: expected an absolute Path")
        if self.extra_knobs is not None and not callable(self.extra_knobs):
            raise ValueError("template source extra_knobs: expected a callable")


@dataclass(frozen=True)
class ScenarioPlugin:
    """Templates and reusable knob hooks owned by one versioned plugin namespace.

    The hook mapping is snapshotted read-only to preserve namespace ownership.
    """

    name: str
    templates: tuple[TemplateSource, ...] = ()
    apply_hooks: Mapping[str, ApplyHook] = field(default_factory=dict)
    api_version: int = API_VERSION

    def __post_init__(self) -> None:
        validate_name(self.name, label="scenario plugin")
        validate_api_version(
            self.api_version, API_VERSION, label=f"scenario plugin {self.name!r}"
        )
        if not isinstance(self.templates, tuple) or any(
            not isinstance(source, TemplateSource) for source in self.templates
        ):
            raise ValueError(
                f"scenario plugin {self.name!r}: templates must be a tuple "
                "of TemplateSource"
            )
        if not isinstance(self.apply_hooks, Mapping):
            raise ValueError(
                f"scenario plugin {self.name!r}: apply_hooks must be a mapping"
            )
        hooks = dict(self.apply_hooks)
        prefix = self.name + "."
        for name, hook in hooks.items():
            if (
                not isinstance(name, str)
                or not name.startswith(prefix)
                or len(name) == len(prefix)
            ):
                raise ValueError(
                    f"scenario plugin {self.name!r}: apply hook {name!r} must be in "
                    f"namespace {prefix}<name>"
                )
            if not callable(hook):
                raise ValueError(
                    f"scenario plugin {self.name!r}: apply hook {name!r} must be callable"
                )
        object.__setattr__(self, "apply_hooks", MappingProxyType(hooks))


def discover_scenario_plugins() -> tuple[ScenarioPlugin, ...]:
    """Merge builtins with installed plugins; installed names take precedence."""
    from aeroagentsim.scenarios import BUILTINS

    try:
        return discover(
            "aeroagentsim.scenarios", BUILTINS,
            expected_type=ScenarioPlugin, api_version=API_VERSION,
            name_of=lambda plugin: plugin.name, factory_label="scenario plugin",
            entries=entry_points(group="aeroagentsim.scenarios"),
        ).providers
    except DuplicateEntryPointError as exc:
        raise ValueError(
            f"Duplicate aeroagentsim.scenarios entry point name: {exc.conflict.name!r}"
        ) from exc


__all__ = [
    "API_VERSION",
    "ApplyHook",
    "KnobProvider",
    "ScenarioPlugin",
    "TemplateSource",
    "discover_scenario_plugins",
]
