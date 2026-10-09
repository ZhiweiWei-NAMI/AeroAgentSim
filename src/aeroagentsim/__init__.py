"""Import-light public API for the shared kernel runtime."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("aeroagentsim")
except PackageNotFoundError:
    __version__ = "2.0.0"  # source distribution version

_EXPORTS = {
    "Simulation": ("aeroagentsim.platform.simulation", "Simulation"),
    "RunSession": ("aeroagentsim.platform.simulation", "RunSession"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(target[0]), target[1])
    globals()[name] = value
    return value


__all__ = ["RunSession", "Simulation", "__version__"]
