"""Offline scene authoring inputs; separate from formal run evidence."""

from .compiler import COMPILED_INTERCHANGE_ONLY, CompiledSceneSelection, compile_scene_selection
from .selection import (
    SCENE_SELECTION_SCHEMA_VERSION,
    SHANGHAI_SOURCE_ID,
    SHANGHAI_SOURCE_SHA256,
    RegisteredSceneSource,
    SceneSelection,
    SceneSelectionError,
    SceneSourceRegistry,
    VerifiedSceneSource,
    Wgs84Bounds,
    default_scene_source_registry,
)

__all__ = [
    "COMPILED_INTERCHANGE_ONLY",
    "CompiledSceneSelection",
    "RegisteredSceneSource",
    "SCENE_SELECTION_SCHEMA_VERSION",
    "SHANGHAI_SOURCE_ID",
    "SHANGHAI_SOURCE_SHA256",
    "SceneSelection",
    "SceneSelectionError",
    "SceneSourceRegistry",
    "VerifiedSceneSource",
    "Wgs84Bounds",
    "compile_scene_selection",
    "default_scene_source_registry",
]
