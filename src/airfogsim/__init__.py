"""Public package exports for AirFogSim / AeroAgentSim."""

from importlib.metadata import PackageNotFoundError, version

from airfogsim.core.environment import Environment

AirFogSimEnv = Environment

try:
    __version__ = version("airfogsim")
except PackageNotFoundError:  # pragma: no cover - fallback for editable/local use
    __version__ = "0.0.0"

if not __version__:  # pragma: no cover - defensive fallback for incomplete metadata
    __version__ = "0.0.0"

__all__ = ["Environment", "AirFogSimEnv", "__version__"]
