"""Public package exports for AeroAgentSim.

The package root stays import-light so that kernel-based subpackages
(``aeroagentsim.integrations``, ``aeroagentsim.engines``, ...) do not pull in
the legacy SimPy runtime. Legacy names resolve lazily on first access.
"""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("aeroagentsim")
except PackageNotFoundError:  # pragma: no cover - fallback for editable/local use
    try:
        __version__ = version("airfogsim")
    except PackageNotFoundError:
        __version__ = "0.0.0"

if not __version__:  # pragma: no cover - defensive fallback for incomplete metadata
    __version__ = "0.0.0"

_LEGACY_EXPORTS = {
    "Environment": ("aeroagentsim.core.environment", "Environment"),
    "AirFogSimEnv": ("aeroagentsim.core.environment", "Environment"),
    "AeroAgentSimEnv": ("aeroagentsim.core.environment", "Environment"),
}


def __getattr__(name: str) -> Any:
    target = _LEGACY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


__all__ = ["Environment", "AirFogSimEnv", "AeroAgentSimEnv", "__version__"]
