"""Provider adapters, contracts, and the explicit runtime-stage registry.

``stages`` is imported eagerly because it is a cycle-free leaf that defines the
single ``ProviderStage``. The heavier ``contracts`` and ``registry`` modules
import ``world.resolved``/``runtime.contracts``, so they are resolved lazily via
PEP 562 ``__getattr__``. This keeps ``from aero_bench.providers.stages import
ProviderStage`` free of an import cycle when reached from ``world.resolved``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aero_bench.providers.stages import (
    BUSINESS_ENVIRONMENT,
    MOTION,
    NETWORK,
    PROVIDER_STAGES,
    ProviderStage,
    ordered_enabled_stages,
    parse_runtime_stage,
)

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from aero_bench.providers.contracts import (
        ProviderCommandResult,
        ProviderManifest,
        ProviderSession,
    )
    from aero_bench.providers.registry import (
        ProviderRegistration,
        ProviderRegistry,
        ProviderRegistryError,
        RuntimeEndpoint,
        builtin_provider_registry,
    )

_LAZY_EXPORTS = {
    "ProviderCommandResult": "aero_bench.providers.contracts",
    "ProviderManifest": "aero_bench.providers.contracts",
    "ProviderSession": "aero_bench.providers.contracts",
    "ProviderRegistration": "aero_bench.providers.registry",
    "ProviderRegistry": "aero_bench.providers.registry",
    "ProviderRegistryError": "aero_bench.providers.registry",
    "RuntimeEndpoint": "aero_bench.providers.registry",
    "builtin_provider_registry": "aero_bench.providers.registry",
}

__all__ = [
    "BUSINESS_ENVIRONMENT",
    "MOTION",
    "NETWORK",
    "PROVIDER_STAGES",
    "ProviderCommandResult",
    "ProviderManifest",
    "ProviderRegistration",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderSession",
    "ProviderStage",
    "RuntimeEndpoint",
    "builtin_provider_registry",
    "ordered_enabled_stages",
    "parse_runtime_stage",
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
