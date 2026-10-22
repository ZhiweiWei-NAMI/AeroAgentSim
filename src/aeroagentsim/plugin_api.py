"""Public, versioned contracts and discovery for scenario plugins."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

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
        if not isinstance(self.name, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]*", self.name
        ):
            raise ValueError(
                f"scenario plugin {self.name!r}: name must match [a-z0-9][a-z0-9_-]*"
            )
        if type(self.api_version) is not int or self.api_version != API_VERSION:
            raise ValueError(
                f"scenario plugin {self.name!r}: unsupported API version "
                f"{self.api_version!r}; supported version is {API_VERSION}"
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


def _plugin(name: str, value: Any, origin: str) -> ScenarioPlugin:
    if not isinstance(value, ScenarioPlugin) and callable(value):
        try:
            value = value()
        except TypeError as exc:
            raise TypeError(
                f"{origin} {name!r}: zero-argument scenario plugin factory failed: {exc}"
            ) from exc
    if not isinstance(value, ScenarioPlugin):
        raise TypeError(
            f"{origin} {name!r}: expected a ScenarioPlugin or a zero-argument "
            "callable returning one"
        )
    if value.name != name:
        raise ValueError(
            f"{origin} {name!r}: plugin name {value.name!r} does not match"
        )
    return value


def discover_scenario_plugins() -> tuple[ScenarioPlugin, ...]:
    """Merge builtins with installed plugins; installed names take precedence."""
    from aeroagentsim.scenarios import BUILTINS

    installed = {}
    for entry in entry_points(group="aeroagentsim.scenarios"):
        if entry.name in installed:
            raise ValueError(
                f"Duplicate aeroagentsim.scenarios entry point name: {entry.name!r}"
            )
        installed[entry.name] = entry
    plugins = []
    for name in sorted(set(BUILTINS) | set(installed)):
        if name in installed:
            value = installed[name].load()
            origin = "aeroagentsim.scenarios entry point"
        else:
            value = BUILTINS[name]
            origin = "builtin scenario plugin"
        plugins.append(_plugin(name, value, origin))
    return tuple(plugins)


__all__ = [
    "API_VERSION",
    "ApplyHook",
    "KnobProvider",
    "ScenarioPlugin",
    "TemplateSource",
    "discover_scenario_plugins",
]
