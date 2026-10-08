"""Import-light public API for the kernel platform and deprecated v1 runtime."""

import warnings
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("aeroagentsim")
except PackageNotFoundError:
    __version__ = "1.1.1"  # source distribution version

_MIGRATION = "docs/platform/MIGRATION-v1.md"
# pip does not record selected extras. Check their distributions without importing
# the legacy stack; separately installing the same dependencies is equivalent.
_LEGACY_DEPENDENCIES = (
    "simpy",
    "numpy",
    "pint",
    "python-dotenv",
    "requests",
    "pandas",
    "openai",
    "tqdm",
    "fastapi",
    "tabulate",
    "pydantic",
    "uvicorn",
    "matplotlib",
)


def _require_legacy() -> None:
    missing = []
    for dependency in _LEGACY_DEPENDENCIES:
        try:
            version(dependency)
        except PackageNotFoundError:
            missing.append(dependency)
    if missing:
        raise ImportError(
            "The deprecated SimPy runtime requires aeroagentsim[legacy]. "
            "Install with: pip install -e '.[legacy]' (or pip install "
            "'aeroagentsim[legacy]'). Missing: "
            + ", ".join(missing)
            + ". New code should use 'from aeroagentsim import Simulation'. "
            + "See "
            + _MIGRATION
        )


_EXPORTS = {
    "Simulation": ("aeroagentsim.platform.simulation", "Simulation"),
    "RunSession": ("aeroagentsim.platform.simulation", "RunSession"),
    "Environment": ("aeroagentsim.core.environment", "Environment"),
    "AirFogSimEnv": ("aeroagentsim.core.environment", "Environment"),
    "AeroAgentSimEnv": ("aeroagentsim.core.environment", "Environment"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in {"Environment", "AirFogSimEnv", "AeroAgentSimEnv"}:
        _require_legacy()
        warnings.warn(
            f"aeroagentsim.{name} is the deprecated v1 SimPy runtime; "
            f"use Simulation for kernel runs. See {_MIGRATION}",
            DeprecationWarning,
            stacklevel=2,
        )
    value = getattr(import_module(target[0]), target[1])
    if name in {"Simulation", "RunSession"}:
        globals()[name] = value
    return value


__all__ = ["RunSession", "Simulation", "__version__"]
