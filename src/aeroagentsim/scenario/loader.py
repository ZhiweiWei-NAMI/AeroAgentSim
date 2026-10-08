"""Compile explicitly authored YAML into pinned registry and authority contracts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from aerokernel import (
    BindingManifest,
    BindingRule,
    CommandRequest,
    EntityRef,
    ExactBinding,
    FieldDescriptor,
    Instant,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    TypeDescriptor,
)
from aerokernel.relations import (
    Cardinality,
    ObligationRule,
    RelationDescriptor,
    RelationRule,
)
from aerokernel.sampling import SampleSpec
from aerokernel.values import canonical_json

from aeroagentsim.integrations.aerograph import (
    CompiledRegistry,
    Policy,
    Selection,
    compile_registry,
    read_snapshot,
)

FORMAT = "aeroagentsim.scenario/v1"


class ScenarioError(ValueError):
    """A scenario path identifies the configuration requiring correction."""


class UniqueLoader(yaml.SafeLoader):
    """Do not let duplicate YAML keys silently replace authored bindings."""


def _mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ScenarioError(
                f"line {key_node.start_mark.line + 1}: mapping keys must be strings"
            )
        if key in result:
            raise ScenarioError(
                f"line {key_node.start_mark.line + 1}: duplicate key {key!r}"
            )
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def obj(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioError(f"{path}: expected a mapping")
    return cast(dict[str, Any], value)


def seq(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ScenarioError(f"{path}: expected a list")
    return value


def text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScenarioError(f"{path}: expected a nonempty string")
    return value


def integer(value: Any, path: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ScenarioError(f"{path}: expected integer >= {minimum}")
    return value


def _finite(value: Any, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ScenarioError(f"{path}: nonfinite numeric value")
    if isinstance(value, dict):
        for key, child in value.items():
            _finite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _finite(child, f"{path}[{index}]")


@dataclass(frozen=True)
class Scenario:
    document: dict[str, Any]
    source: str
    base: Path
    digest: str
    run_id: str
    compiled: CompiledRegistry
    registry: MemoryRegistry
    manifest: BindingManifest
    initial: dict[str, dict[str, Any]]
    engines: dict[str, dict[str, Any]]
    seed: int
    until_ns: int
    pacing: str
    advance_ns: int


def load_scenario(
    source: Path | str | dict[str, Any], *, base: Path | None = None
) -> Scenario:
    """Read a file or YAML body; never infer domain kinds or missing observations."""
    try:
        if isinstance(source, dict):
            document = source
            source_text = yaml.safe_dump(source, sort_keys=True)
            directory = Path.cwd() if base is None else base
        else:
            path = Path(source).resolve()
            source_text = path.read_text()
            document = obj(yaml.load(source_text, Loader=UniqueLoader), "scenario")
            directory = path.parent
        return _compile(document, source_text, directory)
    except ScenarioError:
        raise
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as exc:
        raise ScenarioError(f"scenario: {exc}") from exc


def _compile(document: dict[str, Any], source: str, base: Path) -> Scenario:
    _finite(document, "scenario")
    allowed = {
        "format",
        "id",
        "registry",
        "entities",
        "bindings",
        "engines",
        "presentation",
        "run",
        "outputs",
        "origin",
    }
    if extra := set(document) - allowed:
        raise ScenarioError(f"scenario: unknown keys {sorted(extra)}")
    if document.get("format") != FORMAT:
        raise ScenarioError(f"format: expected {FORMAT}")
    run_id = text(document["id"], "id")
    registry_spec = obj(document["registry"], "registry")
    if ("snapshot" in registry_spec) == ("compile" in registry_spec):
        raise ScenarioError("registry: select exactly one snapshot or compile")
    if "snapshot" in registry_spec:
        compiled = read_snapshot(
            base / text(registry_spec["snapshot"], "registry.snapshot")
        )
        if registry_spec.get("digest") != compiled.digest:
            raise ScenarioError(
                "registry.digest: snapshot digest must be explicitly pinned and match"
            )
    else:
        spec = obj(registry_spec["compile"], "registry.compile")
        compiled = compile_registry(
            base / text(spec["root"], "registry.compile.root"),
            Selection(
                tuple(seq(spec["types"], "registry.compile.types")),
                None
                if "fields" not in spec
                else tuple(seq(spec["fields"], "registry.compile.fields")),
                None
                if "relations" not in spec
                else tuple(seq(spec["relations"], "registry.compile.relations")),
            ),
            Policy(**obj(spec.get("policy", {}), "registry.compile.policy")),
        )
    types = list(compiled.registry.types)
    fields = list(compiled.registry.fields)
    messages = list(compiled.registry.messages)
    for item in seq(registry_spec.get("types", []), "registry.types"):
        item = obj(item, "registry.types[]")
        types.append(
            TypeDescriptor(item["id"], tuple(item["parents"]), item["abstract"])
        )
    for item in seq(registry_spec.get("fields", []), "registry.fields"):
        item = obj(item, "registry.fields[]")
        fields.append(
            FieldDescriptor(item["id"], item["type"], item["schema"], item["metadata"])
        )
    for item in seq(registry_spec.get("messages", []), "registry.messages"):
        item = obj(item, "registry.messages[]")
        messages.append(MessageDescriptor(**item))
    relations = []
    source_relations = {item["id"]: item for item in compiled.relations}
    for item in registry_spec.get("relations", []):
        native = source_relations[item["id"]]
        if (
            item["source_type"] != native["source_type"]
            or item["target_type"] != native["target_type"]
        ):
            raise ScenarioError(
                "registry.relations: endpoint classes must match the compiled source"
            )
        for direction, declared in native["cardinality"].items():
            chosen = item[direction]
            if chosen != {"minimum": declared["min"], "maximum": declared["max"]}:
                raise ScenarioError(
                    f"registry.relations.{direction}: may not weaken source cardinality"
                )
        relations.append(
            RelationDescriptor(
                item["id"],
                item["source_type"],
                item["target_type"],
                Cardinality(**item["targets_per_source"]),
                Cardinality(**item["sources_per_target"]),
                item["identity_policy"],
                item["metadata"],
            )
        )
    registry = MemoryRegistry(
        tuple(types),
        tuple(fields),
        tuple(messages),
        schemas=compiled.registry.to_data()["schemas"],
        relations=tuple(relations),
    )
    refs: list[EntityRef] = []
    initial: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(seq(document["entities"], "entities")):
        item = obj(item, f"entities[{index}]")
        entity_id = text(item["id"], f"entities[{index}].id")
        type_id = text(item["type"], f"entities[{index}].type")
        if entity_id in initial:
            raise ScenarioError(f"entities[{index}].id: duplicate {entity_id!r}")
        registry.is_a(type_id, type_id)
        refs.append(EntityRef(run_id, "0", entity_id, 0, type_id))
        initial[entity_id] = obj(item["facts"], f"entities[{index}].facts")
        for field, value in initial[entity_id].items():
            descriptor = registry.field(field)
            if not registry.is_a(type_id, descriptor.declaring_type):
                raise ScenarioError(
                    f"entities[{index}].facts.{field}: field does not apply to {type_id}"
                )
            registry.validate(descriptor.schema, value)
    by_id = {ref.id: ref for ref in refs}
    bindings = obj(document["bindings"], "bindings")
    manifest = BindingManifest(
        run_id,
        "0",
        tuple(refs),
        rules=tuple(
            BindingRule(
                item["writer"],
                item["type"],
                tuple(item["fields"]),
                item.get("ids", "*"),
                item.get("priority", 0),
            )
            for item in bindings.get("rules", [])
        ),
        exact=tuple(
            ExactBinding(by_id[item["entity"]], item["field"], item["writer"])
            for item in bindings.get("exact", [])
        ),
        lifecycle=tuple(
            LifecycleRule(
                item["controller"],
                item["type"],
                item.get("ids", "*"),
                item.get("priority", 0),
            )
            for item in bindings["lifecycle"]
        ),
        bootstrap_commands=tuple(
            CommandRequest(
                item["schema"],
                item["target"],
                Instant(integer(item["at_ns"], "bindings.commands.at_ns")),
                item["payload"],
            )
            for item in bindings.get("commands", [])
        ),
        cohorts=tuple(tuple(group) for group in bindings.get("cohorts", [])),
        relation_rules=tuple(
            RelationRule(
                item["writer"], item["relation"], item["type"], item.get("ids", "*")
            )
            for item in bindings.get("relations", [])
        ),
        obligation_rules=tuple(
            ObligationRule(
                item["controller"],
                item["relation"],
                item["direction"],
                item["type"],
                item.get("ids", "*"),
            )
            for item in bindings.get("obligations", [])
        ),
        samples=tuple(
            SampleSpec(
                item["context"],
                item["partition"],
                tuple(item["upstream"]),
                {role: by_id[entity] for role, entity in item["bindings"].items()},
                item["sources"],
                {role: tuple(clock) for role, clock in item["clocks"].items()},
                parameters=item["parameters"],
            )
            for item in bindings.get("samples", [])
        ),
    )
    engines = obj(document["engines"], "engines")
    for engine_id, item in engines.items():
        item = obj(item, f"engines.{engine_id}")
        text(item["plugin"], f"engines.{engine_id}.plugin")
        obj(item["config"], f"engines.{engine_id}.config")
    for item in seq(document["presentation"], "presentation"):
        item = obj(item, "presentation[]")
        registry.is_a(item["typeId"], item["typeId"])
        position = registry.field(item["positionField"])
        if (
            position.schema.get("type") != "vector"
            or position.schema.get("length") != 3
        ):
            raise ScenarioError("presentation.positionField: requires a three-vector")
        if item["frame"] not in {"enu", "ned", "wgs84"}:
            raise ScenarioError("presentation.frame: expected enu, ned, wgs84")
        if item["visual"]["kind"] not in {"marker", "model", "label"}:
            raise ScenarioError(
                "presentation.visual.kind: expected marker, model, label"
            )
    run = obj(document["run"], "run")
    pacing = text(run["pacing"], "run.pacing")
    if pacing not in {"fast", "realtime"}:
        raise ScenarioError("run.pacing: expected fast or realtime")
    outputs = obj(document["outputs"], "outputs")
    if outputs["durability"] not in {"flush", "fsync"}:
        raise ScenarioError("outputs.durability: expected flush or fsync")
    digest = hashlib.sha256(canonical_json(document)).hexdigest()
    return Scenario(
        document,
        source,
        base,
        digest,
        run_id,
        compiled,
        registry,
        manifest,
        initial,
        engines,
        integer(run["seed"], "run.seed"),
        integer(run["until_ns"], "run.until_ns"),
        pacing,
        integer(run["advance_ns"], "run.advance_ns", 1),
    )
