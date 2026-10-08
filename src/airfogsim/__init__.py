"""Compatibility namespace for the legacy ``airfogsim`` package path.

The primary source tree now lives under ``aeroagentsim``. Legacy imports are
kept working by exposing the same public exports and forwarding submodule
resolution through the new package path.
"""

import sys
import warnings
from importlib import abc, import_module, util
from pathlib import Path

from aeroagentsim import _require_legacy

_require_legacy()
warnings.warn(
    "airfogsim is deprecated; use aeroagentsim.Simulation. "
    "See docs/platform/MIGRATION-v1.md",
    DeprecationWarning,
    stacklevel=2,
)
_compat_dir = Path(__file__).resolve().parent
_compat_prefix = __name__
_primary_prefix = "aeroagentsim"


class _AliasLoader(abc.Loader):
    def __init__(self, alias_name: str, target_name: str) -> None:
        self.alias_name = alias_name
        self.target_name = target_name

    def create_module(self, spec):
        module = import_module(self.target_name)
        sys.modules[self.alias_name] = module
        return module

    def exec_module(self, module) -> None:
        sys.modules[self.alias_name] = module


class _AliasFinder(abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(f"{_compat_prefix}."):
            return None

        mapped_name = fullname.replace(_compat_prefix, _primary_prefix, 1)
        target_spec = util.find_spec(mapped_name)
        if target_spec is None:
            return None

        spec = util.spec_from_loader(
            fullname,
            _AliasLoader(fullname, mapped_name),
            origin=target_spec.origin,
            is_package=target_spec.submodule_search_locations is not None,
        )
        if spec is not None and target_spec.submodule_search_locations is not None:
            spec.submodule_search_locations = list(
                target_spec.submodule_search_locations
            )
        return spec


def _install_alias_finder() -> None:
    if any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
        return
    sys.meta_path.insert(0, _AliasFinder())


__path__ = [str(_compat_dir)]
_install_alias_finder()

_primary = import_module("aeroagentsim")

# Reuse already-loaded aeroagentsim modules under the legacy namespace when
# possible so callers do not end up with avoidable duplicate module objects.
for module_name, module in list(sys.modules.items()):
    if module_name == "aeroagentsim" or module_name.startswith("aeroagentsim."):
        legacy_name = module_name.replace("aeroagentsim", "airfogsim", 1)
        sys.modules.setdefault(legacy_name, module)

Environment = _primary.Environment
AirFogSimEnv = Environment
AeroAgentSimEnv = Environment
__version__ = _primary.__version__
__all__ = ["AeroAgentSimEnv", "AirFogSimEnv", "Environment", "__version__"]


def __getattr__(name):
    return getattr(_primary, name)
