"""Finite data interpreters for public vocabulary and viewer-header hints.

Providers supply vocabulary and ``header`` data, never projection callbacks.
Header keys are ``engine_gusts`` (profile interval unions), ``lighting`` (an authored initial
fact), and ``scenes`` (literal scenario selectors and a finite host resource).
Authored scene geometry is still read by the generic authoring scene adapter.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .presentation_api import validate_header_hints
from .presentation_resources import resolve_header_resource

_NUMBERED = re.compile(r"[A-Z]?\d+\Z")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(f"{path}: string-keyed mapping required")
    return value


def presentation_label(label: str, language: str, data: Mapping[str, Any]) -> str:
    """Interpret admitted public vocabulary DATA, preserving unknown authored prose."""
    vocabulary = data.get("labels", {})
    if label in vocabulary:
        return vocabulary[label][language]
    if language != "zh":
        return label
    for prefix, localized in data.get("numbered_labels", {}).items():
        if label.startswith(prefix + " ") and _NUMBERED.fullmatch(label[len(prefix) + 1:]):
            return localized[language] + label[len(prefix):]
    for group in data.get("label_groups", ()):
        prefix = group["prefix"]["en"] + " "
        if not label.startswith(prefix):
            continue
        suffix = label[len(prefix):]
        if suffix in group["names"]:
            return group["prefix"][language] + " " + group["names"][suffix][language]
        for numbered, localized in group["numbered"].items():
            if suffix.startswith(numbered + " ") and suffix[len(numbered) + 1:].isdecimal():
                return group["prefix"][language] + " " + localized[language] + suffix[len(numbered):]
    return label


def read_presentation_snapshot(directory: Path) -> dict[str, Any] | None:
    """Read family metadata only; legacy directories remain explicitly unpinned."""
    from .presentation_api import validate_presentation_snapshot

    manifest = directory / "manifest.json"
    metadata = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    expected = metadata.get("presentation_snapshot_digest")
    if "presentation_snapshot_digest" in metadata and (
        type(expected) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", expected) is None
    ):
        raise ValueError("pinned presentation: malformed manifest snapshot digest")
    path = directory / "presentation.snapshot.json"
    if not path.exists():
        if expected is not None:
            raise ValueError("pinned presentation unavailable: recorded snapshot is missing")
        return None
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    validate_presentation_snapshot(snapshot)
    if expected is not None and snapshot["digest"] != expected:
        raise ValueError("pinned presentation: snapshot digest differs from run manifest")
    return snapshot


def presentation_selection(
    directory: Path, plugins: Iterable[Any] | None = None,
) -> tuple[tuple[Any, ...], dict[str, Any] | None]:
    """Use exactly the run's recorded family selection when one was pinned."""
    from .presentation_api import (
        discover_presentation_plugins, validate_plugins, verify_presentation_snapshot,
    )

    snapshot = read_presentation_snapshot(directory)
    names = tuple(provider["name"] for provider in snapshot["providers"]) if snapshot is not None else None
    selected = discover_presentation_plugins(names=names) if plugins is None else tuple(plugins)
    if snapshot is not None:
        selected = verify_presentation_snapshot(snapshot, selected)
    else:
        validate_plugins(selected)
    return selected, snapshot


def _headers(plugins: Iterable[Any] | None) -> list[Mapping[str, Any]]:
    if plugins is None:
        from .presentation_api import discover_presentation_plugins

        plugins = discover_presentation_plugins()
    result = []
    names: set[str] = set()
    for plugin in plugins:
        if plugin.name in names:
            raise ValueError(f"Duplicate presentation plugin name: {plugin.name!r}")
        names.add(plugin.name)
        data = _mapping(plugin.data, f"presentation plugin {plugin.name!r}.data")
        header = data.get("header", {})
        validate_header_hints(header, path=f"presentation plugin {plugin.name!r}.header")
        result.append(header)
    return result


def _engine_gusts(
    result: dict[str, Any], scenario: Mapping[str, Any], headers: list[Mapping[str, Any]],
) -> None:
    fields: dict[str, set[str]] = {}
    for hints in headers:
        for rule in hints.get("engine_gusts", ()):
            fields.setdefault(rule["plugin"], set()).update(rule["fields"])
    for row in result.get("engines", ()):
        selected = fields.get(row["plugin"])
        if not selected:
            continue
        spec = scenario["engines"][row["id"]]
        intervals = sorted({
            (gust["start_ns"], gust["end_ns"])
            for profile in spec.get("config", {}).get("profiles", {}).values()
            for field_id, authored in profile.items()
            if field_id in selected
            for gust in authored.get("gusts", [])
        })
        if intervals:
            gusts: list[list[int]] = []
            for start, end in intervals:
                if gusts and start <= gusts[-1][1]:
                    gusts[-1][1] = max(gusts[-1][1], end)
                else:
                    gusts.append([start, end])
            row["gusts"] = gusts


def _scene(
    result: dict[str, Any], scenario: Mapping[str, Any], directory: Path,
    headers: list[Mapping[str, Any]],
) -> None:
    # Frozen region geometry wins before any optional mutable resource lookup,
    # including for legacy scenarios that still carry a conflicting selector.
    if (directory / "studio-scene.json").is_file():
        return
    matched = []
    for hints in headers:
        for rule in hints.get("scenes", ()):
            if all(scenario.get(key) == expected for key, expected in rule["when"].items()):
                if rule not in matched:
                    matched.append(rule)
    if len(matched) > 1:
        raise ValueError("presentation.header.scenes: conflicting matching scene rules")
    if not matched:
        return
    rule = matched[0]
    bindings = result["presentation"]
    visuals = rule["visuals"]
    models = [
        visuals[binding["typeId"]]["model"]
        for binding in bindings
        if "model" in visuals.get(binding["typeId"], {})
    ]
    resource = resolve_header_resource(rule["resource"], models=models)
    result.update(resource["header"])
    result.setdefault("scene_source", resource["scene_source"])
    result["presentation"] = [dict(binding) for binding in bindings]
    for binding in result["presentation"]:
        model = visuals.get(binding["typeId"], {}).get("model")
        if model is not None:
            binding["visual"] = deepcopy(resource["visuals"][model])
    catalog = resource["model_catalog"]
    if catalog:
        result["modelCatalog"] = catalog
        for binding in result["presentation"]:
            kind = visuals.get(binding["typeId"], {}).get("catalog")
            if kind is not None:
                binding["visual"] = {**binding["visual"], "catalog": kind}


def _lighting(
    result: dict[str, Any], scenario: Mapping[str, Any], headers: list[Mapping[str, Any]],
) -> None:
    selected: str | None = None
    for hints in headers:
        for rule in hints.get("lighting", ()):
            light = next((
                entity["facts"][rule["field_id"]]
                for entity in scenario.get("entities", [])
                if entity.get("type") == rule["type_id"]
                and rule["field_id"] in entity.get("facts", {})
            ), None)
            if light in rule["values"]:
                if selected is not None and selected != light:
                    raise ValueError("presentation.header.lighting: conflicting initial facts")
                selected = light
    if selected is not None:
        result.setdefault("scene", {})["lighting"] = selected


def apply_header_presentations(
    result: dict[str, Any], scenario: Mapping[str, Any], directory: Path,
    plugins: Iterable[Any] | None = None, *, registry: Any = None,
) -> None:
    """Apply validated provider data without changing the viewer-feed/v1 shape.

    Engine row/order and authored bindings are retained. Only nonempty interval
    unions, a selected compatibility scene and explicitly authored allowed light
    facts add hints. An explicit empty plugin selection never triggers discovery.
    """
    from .presentation_api import (
        PRESENTATION_FORMAT, admit_presentation_plugins, json_data, presentation_snapshot, resolve_poses,
    )

    selected, _ = presentation_selection(directory, plugins)
    if registry is not None:
        admit_presentation_plugins(selected, registry, document=scenario)
    headers = _headers(selected)
    poses, diagnostics, metrics = [], [], {}
    bindings = list(result["presentation"])
    bound_types = {binding["typeId"] for binding in bindings}
    for plugin in selected:
        data = plugin.data
        metrics.update(json_data(data.get("metrics", {})))
        if registry is not None:
            resolved, issues = resolve_poses(
                {"format": PRESENTATION_FORMAT, "poses": data.get("poses", ())}, registry,
            )
            diagnostics.extend(issues)
            for pose in resolved:
                if pose["frame"] == "wgs84" and "origin" not in scenario:
                    if any(item["typeId"] == pose["typeId"] and item.get("optional") for item in data.get("poses", ())):
                        diagnostics.append(f"Optional pose unavailable for {pose['typeId']}: no origin; generic labelled view")
                        continue
                    raise ValueError("presentation.wgs84: explicit origin required")
                poses.append(pose)
                if pose["typeId"] not in bound_types:
                    bindings.append({key: value for key, value in pose.items()
                                     if key in {"typeId", "positionField", "orientationField", "frame", "visual"}})
                    bound_types.add(pose["typeId"])
        labels = data.get("labels", {})
        for key, identity in (("types", "typeId"), ("fields", "fieldId")):
            for row in result.get(key, ()):
                if row[identity] in labels:
                    row["displayName"] = labels[row[identity]]["en"]
                    row["displayName_zh"] = labels[row[identity]]["zh"]
    if poses:
        result["presentation"] = bindings
    if poses or diagnostics or metrics:
        # An optional, separately tagged DATA extension; existing builtin runs
        # contribute none of these sections and keep their header byte shape.
        result["presentationHints"] = {
            "format": PRESENTATION_FORMAT, "digest": presentation_snapshot(selected)["digest"],
            "poses": poses, "metrics": metrics, "diagnostics": diagnostics,
        }
    _engine_gusts(result, scenario, headers)
    _scene(result, scenario, directory, headers)
    _lighting(result, scenario, headers)


__all__ = [
    "apply_header_presentations", "validate_header_hints", "presentation_label",
    "read_presentation_snapshot", "presentation_selection",
]
