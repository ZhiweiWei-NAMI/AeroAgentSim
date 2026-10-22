"""Finite legacy display resources, separate from data-only presentation plugins.

``legacy-huangpu/v1`` intentionally retains the old viewer's dependence on the
currently installed asset packs. It is not a pinned run artifact or a provider
callback. Frozen authored geometry must bypass this adapter entirely. Absolute
HTTP URLs remain the responsibility of the existing transport adapter.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

HEADER_RESOURCE_KEYS = frozenset({"legacy-huangpu/v1"})


def resolve_header_resource(
    key: str, *, models: Iterable[str] = (),
) -> dict[str, Any]:
    """Resolve only a named host compatibility resource into detached JSON data.

    The finite key set is not an import path, a filesystem path, or an extension
    point for plugin factories. Domain type-to-model bindings live in provider
    data; this adapter only retains the legacy asset installation/URL convention.
    """
    if key not in HEADER_RESOURCE_KEYS:
        raise ValueError(f"presentation.header: unknown resource {key!r}")

    from aeroagentsim.assets import installed, model_catalog
    from aeroagentsim.authoring.demo import city_presentation, detailed_city

    detail = "detailed" if detailed_city() else "lite"
    packs = [
        {"id": pack["id"], "version": pack["version"], "title": pack["title"]}
        for pack in installed()
    ]
    scene = city_presentation()
    visuals = {
        model: {
            "kind": "model",
            "scale": 1.0,
            "asset": "/v1/studio/demo-assets/models/" + model + ".glb"
            if detailed_city() else "procedural:" + model,
        }
        for model in models
    }
    return {
        "header": {
            "city": "huangpu",
            "cityDetail": detail,
            "assetPacks": packs,
            "scene": scene,
        },
        "scene_source": {
            "kind": "fixed-city", "id": "huangpu", "attribution": scene["attribution"],
        },
        "visuals": visuals,
        "model_catalog": model_catalog(),
    }


__all__ = ["HEADER_RESOURCE_KEYS", "resolve_header_resource"]
