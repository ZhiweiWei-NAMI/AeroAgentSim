"""Import-light factories for the built-in scenario plugins."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aeroagentsim.plugin_api import ScenarioPlugin


def lowalt() -> ScenarioPlugin:
    from .builtin import lowalt as factory

    return factory()


def traffic() -> ScenarioPlugin:
    from .builtin import traffic as factory

    return factory()


def rescue() -> ScenarioPlugin:
    from .builtin import rescue as factory

    return factory()


def builtin_templates() -> ScenarioPlugin:
    from .builtin import builtin_templates as factory

    return factory()


BUILTINS: dict[str, ScenarioPlugin | Callable[[], ScenarioPlugin]] = {
    "lowalt": lowalt,
    "traffic": traffic,
    "rescue": rescue,
    "builtin-templates": builtin_templates,
}

__all__ = ["BUILTINS"]
