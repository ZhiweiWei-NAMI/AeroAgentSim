"""Built-in scenario plugins.

Built-ins register through exactly the same ``ScenarioPlugin`` contract that
external distributions use via the ``aeroagentsim.scenarios`` entry-point
group. An installed plugin with the same name replaces the builtin; this is
how an external AeroRescue distribution can replace ``flood-rescue`` and
``rescue``.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aeroagentsim.plugin_api import ScenarioPlugin

#: Templates shipped with AeroAgentSim; each is its own one-template plugin.
TEMPLATES = (
    "air-taxi", "city-day", "crop-spraying", "flood-rescue", "food-delivery",
    "medical-delivery", "parcel-delivery", "powerline-inspection",
    "security-patrol", "traffic-accident",
)


def _lazy(module: str, template: str | None = None) -> Callable[[], ScenarioPlugin]:
    """Defer importing a plugin module until discovery asks for it."""
    def factory() -> ScenarioPlugin:
        loaded = import_module(module)
        return loaded.plugin() if template is None else loaded.template_plugin_for(template)

    return factory


BUILTINS: dict[str, ScenarioPlugin | Callable[[], ScenarioPlugin]] = {
    # Hook providers: reusable knob hooks owned by the domain packs.
    "lowalt": _lazy("aeroagentsim.packs.lowalt.scenario_hooks"),
    "traffic": _lazy("aeroagentsim.packs.traffic_accident.scenario_hooks"),
    "rescue": _lazy("aeroagentsim.packs.lowalt.rescue_hooks"),
    # Scenario plugins: one template each.
    **{name: _lazy("aeroagentsim.scenarios.builtin", name) for name in TEMPLATES},
}

__all__ = ["BUILTINS", "TEMPLATES"]
