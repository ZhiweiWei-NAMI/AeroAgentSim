"""Version admission and pure preparation before generic registry compilation."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from aeroagentsim.preparation_api import (
    ENTRY_POINT_GROUP,
    PREPARATION_API_VERSION,
    PreparedScenario,
    PreparerPlugin,
    ReadOnlyResources,
    discover_preparers,
    document_digest,
    resource_manifest,
)

from .loader import ScenarioError, _finite, contract, integer, obj, text
from .paths import source_path

FORMAT_V1 = "aeroagentsim.scenario/v1"
FORMAT_V2 = "aeroagentsim.scenario/v2"
# This is the unchanged, closed v1 field set, not domain dispatch. Only v2
# admits executable preparation selection; no meaningful v1 keys are added.
SCENARIO_KEYS = frozenset({
    "format", "id", "registry", "entities", "bindings", "engines", "presentation",
    "run", "outputs", "origin", "clock_mappings", "ingress_streams", "behaviours",
    "provenance", "algorithms", "generators", "event_narratives", "city",
})


def _explicit(
    authored_document: dict[str, Any], final_seed: int,
    read_only_resources: ReadOnlyResources,
) -> PreparedScenario:
    # The explicit provider is an identity, not an inference/expansion hook.
    if {"algorithms", "generators"} & authored_document.keys():
        raise ScenarioError("preparation: explicit documents cannot contain legacy shorthand")
    return PreparedScenario(
        authored_document, resource_manifest=resource_manifest(read_only_resources)
    )


def explicit_preparer() -> PreparerPlugin:
    return PreparerPlugin("explicit", _explicit, revision="explicit/v1")


def _resources(
    document: dict[str, Any], references: dict[str, Any], base: Path,
    supplied: ReadOnlyResources | None,
) -> ReadOnlyResources:
    paths: dict[str, str] = {}
    registry = obj(document["registry"], "registry")
    if "snapshot" in registry:
        paths["registry.snapshot"] = text(registry["snapshot"], "registry.snapshot")
    for name, reference in references.items():
        text(name, "preparation.resources key")
        if name in paths:
            raise ScenarioError(f"preparation.resources: reserved resource name {name!r}")
        paths[name] = text(reference, f"preparation.resources.{name}")
    if supplied is None:
        resources = {
            name: source_path(reference, base).read_bytes()
            for name, reference in sorted(paths.items())
        }
    else:
        if not isinstance(supplied, Mapping):
            raise ScenarioError("preparation resources: expected a mapping of immutable bytes")
        resources = dict(supplied)
        if resources.keys() != paths.keys():
            raise ScenarioError("preparation resources: must match the declared resource names")
    if any(type(value) is not bytes for value in resources.values()):
        raise ScenarioError("preparation resources: expected immutable bytes")
    return MappingProxyType(resources)


def prepare_scenario(
    authored_document: dict[str, Any], final_seed: int | None = None,
    read_only_resources: ReadOnlyResources | None = None, *, base: Path | None = None,
) -> PreparedScenario:
    """Prepare a fresh authored input, after resolving its final nonnegative seed.

    ``None`` means use authored run.seed; an explicit zero is a real override.
    Resource acquisition is read-only host work. The selected callable receives
    only detached authored data, the resolved seed and immutable resource bytes.
    This function neither compiles registries nor starts engines/providers.
    """
    try:
        document = copy.deepcopy(obj(authored_document, "scenario"))
        _finite(document, "scenario")
        format_id = document.get("format")
        if format_id not in (FORMAT_V1, FORMAT_V2):
            raise ScenarioError(f"format: expected {FORMAT_V1} or {FORMAT_V2}")
        allowed = SCENARIO_KEYS | ({"preparation"} if format_id == FORMAT_V2 else set())
        if extra := document.keys() - allowed:
            raise ScenarioError(f"scenario: unknown keys {sorted(extra)}")
        run = obj(document["run"], "run")
        seed = integer(run["seed"] if final_seed is None else final_seed, "run.seed")
        legacy_yaml = format_id == FORMAT_V1
        authored_digest = document_digest(document, legacy_yaml=legacy_yaml)
        run["seed"] = seed
        config: dict[str, Any] = {}
        references: dict[str, Any] = {}
        if format_id == FORMAT_V1:
            # Fixed compatibility is deliberate: v1 never discovers/activates
            # installed preparers, including overrides of this historical name.
            from aeroagentsim.packs.lowalt.preparation import plugin

            selected = plugin()
        else:
            selection = contract(
                document.get("preparation"), "preparation",
                {"plugin", "api_version", "config"}, {"resources"},
            )
            name = text(selection["plugin"], "preparation.plugin")
            version = selection["api_version"]
            if type(version) is not int or version != PREPARATION_API_VERSION:
                raise ScenarioError("preparation.api_version: unsupported preparation API version")
            config = obj(selection["config"], "preparation.config")
            references = obj(selection.get("resources", {}), "preparation.resources")
            plugins = {item.name: item for item in discover_preparers()}
            if name not in plugins:
                raise ScenarioError(f"preparation.plugin: unknown preparer {name!r}")
            selected = plugins[name]
        # Validators cannot silently normalize/discard configuration typos.
        validation_config = copy.deepcopy(config)
        config_digest = document_digest(config)
        if (selected.validate_config(validation_config) is not None
                or document_digest(validation_config) != config_digest):
            raise ScenarioError("preparation.config: validator must not mutate config or return a value")
        resources = _resources(
            document, references, Path.cwd() if base is None else base, read_only_resources
        )
        manifest = resource_manifest(resources)
        working = copy.deepcopy(document)
        input_digest = document_digest(working, legacy_yaml=legacy_yaml)
        prepared = selected.prepare(working, seed, resources)
        if document_digest(working, legacy_yaml=legacy_yaml) != input_digest:
            raise ScenarioError("preparation: preparer must not mutate authored inputs")
        if not isinstance(prepared, PreparedScenario):
            raise ScenarioError("preparation: expected a PreparedScenario")
        result = prepared.document
        _finite(result, "prepared scenario")
        if result.get("format") != format_id:
            raise ScenarioError("preparation: preparer must preserve the scenario format")
        if result.keys() - allowed:
            raise ScenarioError("preparation: unsupported output document keys")
        if (format_id == FORMAT_V2
                and document_digest(result.get("preparation")) != document_digest(document["preparation"])):
            raise ScenarioError("preparation: preparer must preserve its explicit selection")
        if integer(obj(result.get("run"), "run").get("seed"), "run.seed") != seed:
            raise ScenarioError("preparation: preparer changed the final seed")
        if prepared.resource_manifest != manifest:
            raise ScenarioError("preparation: resource manifest must hash the supplied bytes")
        identity = {
            "family": ENTRY_POINT_GROUP,
            "name": selected.name,
            "api_version": selected.api_version,
            "revision": selected.revision,
            "entry_point": selected.entry_point,
            "distribution": None if selected.distribution is None else list(selected.distribution),
            "implementation": (
                f"{getattr(selected.prepare, '__module__', type(selected.prepare).__module__)}:"
                f"{getattr(selected.prepare, '__qualname__', type(selected.prepare).__qualname__)}"
            ),
            "config_digest": config_digest,
            "authored_document_digest": authored_digest,
            "final_seed": seed,
            "resource_manifest_digest": document_digest(manifest),
            "contribution_digest": document_digest({
                "document": result, "contribution_selections": prepared.contribution_selections,
                "resource_manifest": manifest,
            }, legacy_yaml=legacy_yaml),
        }
        selections = (identity, *prepared.contribution_selections)
        names: set[tuple[str, str]] = set()
        for contribution in selections:
            key = (
                text(contribution.get("family"), "contribution.family"),
                text(contribution.get("name"), "contribution.name"),
            )
            if key in names:
                raise ScenarioError(f"preparation: conflicting contribution selection {key!r}")
            names.add(key)
        return PreparedScenario(result, selections, manifest)
    except ScenarioError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ScenarioError(f"preparation: {exc}", code=getattr(exc, "code", None)) from exc
