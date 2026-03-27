"""Public package exports for AeroAgentSim."""

from importlib.metadata import PackageNotFoundError, version

from aeroagentsim.core.environment import Environment

AirFogSimEnv = Environment
AeroAgentSimEnv = Environment

try:
    __version__ = version("aeroagentsim")
except PackageNotFoundError:  # pragma: no cover - fallback for editable/local use
    try:
        __version__ = version("airfogsim")
    except PackageNotFoundError:
        __version__ = "0.0.0"

if not __version__:  # pragma: no cover - defensive fallback for incomplete metadata
    __version__ = "0.0.0"

__all__ = ["Environment", "AirFogSimEnv", "AeroAgentSimEnv", "__version__"]
