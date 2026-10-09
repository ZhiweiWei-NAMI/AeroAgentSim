"""Compile explicitly authored YAML into pinned registry and authority contracts."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, ClassVar, cast

import yaml
from aerokernel import (
    BindingManifest,
    BindingRule,
    ClockMapping,
    CommandRequest,
    EntityRef,
    ExactBinding,
    FieldDescriptor,
    IngressPolicy,
    IngressStream,
    Instant,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    TypeDescriptor,
)
from aerokernel.errors import KernelError
from aerokernel.relations import (
    Cardinality,
    ObligationRule,
    RelationDescriptor,
    RelationRule,
)
from aerokernel.sampling import SampleSpec
from aerokernel.values import thaw

from aeroagentsim.integrations.aerograph import (
    CompiledRegistry,
    Policy,
    Selection,
    compile_registry,
    read_snapshot,
)

from .paths import source_path
from .subjects import declarations

FORMAT = "aeroagentsim.scenario/v1"


class ScenarioError(ValueError):
    """A scenario path identifies the configuration requiring correction."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class UniqueLoader(yaml.SafeLoader):
    """YAML 1.2 Boolean spellings and duplicate-key rejection."""

    yaml_implicit_resolvers: ClassVar[dict[Any, Any]] = {  # type: ignore[misc]
        key: [(tag, regex) for tag, regex in entries if tag != "tag:yaml.org,2002:bool"]
        for key, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


UniqueLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


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


def contract(
    value: Any, path: str, required: set[str], optional: set[str] | None = None
) -> dict[str, Any]:
    result = obj(value, path)
    if missing := required - set(result):
        raise ScenarioError(f"{path}: missing keys {sorted(missing)}")
    if extra := set(result) - required - (optional or set()):
        raise ScenarioError(f"{path}: unknown keys {sorted(extra)}")
    return result


def numeric(value: Any, path: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ScenarioError(f"{path}: finite number required")
    return float(value)


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
class EngineIngress:
    mapping_id: str
    policy: IngressPolicy
    stream_id: str


@dataclass(frozen=True)
class Scenario:
    document: dict[str, Any]
    source: str
    base: Path
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
    clock_mappings: tuple[ClockMapping, ...] | None = None
    ingress: dict[str, EngineIngress] = dataclass_field(default_factory=dict)
    ingress_streams: tuple[IngressStream, ...] = ()
    provenance: str = "lean"


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
        raise ScenarioError(
            f"scenario: {exc}", code=getattr(exc, "code", None)
        ) from exc


def _compile(document: dict[str, Any], source: str, base: Path) -> Scenario:
    document = copy.deepcopy(document)
    _finite(document, "scenario")
    behaviour_engines = [
        name
        for name, item in document.get("engines", {}).items()
        if item.get("plugin") == "behaviour"
    ]
    if "behaviours" in document or behaviour_engines:
        from aeroagentsim.behaviours.compiler import resolve_packages
        from aeroagentsim.behaviours.records import TYPE, overlay

        if len(behaviour_engines) != 1:
            raise ScenarioError("behaviours: declare exactly one behaviour engine")
        engine_id = behaviour_engines[0]
        config = document["engines"][engine_id]["config"]
        specs = document.get("behaviours", config.get("packages", []))
        resolved = resolve_packages(specs, base)
        config["packages"] = resolved
        from aeroagentsim.behaviours.sampled import expand_pool

        expand_pool(document, resolved)
        if "behaviours" in document:
            document["behaviours"] = resolved
        descriptors = overlay()
        for category, items in descriptors.items():
            authored = document["registry"].setdefault(category, [])
            existing = {item["id"]: item for item in authored}
            for item in items:
                if item["id"] in existing:
                    if existing[item["id"]] != item:
                        raise ScenarioError(
                            f"registry.{category}.{item['id']}: conflicts "
                            "with behaviour overlay"
                        )
                else:
                    authored.append(item)
        bindings = document["bindings"]
        rules = bindings.setdefault("rules", [])
        fields = [item["id"] for item in descriptors["fields"]]
        if not any(
            rule["type"] == TYPE and rule["writer"] == engine_id for rule in rules
        ):
            rules.append(
                {
                    "writer": engine_id,
                    "type": TYPE,
                    "fields": fields,
                    "ids": "behaviour:*",
                }
            )
        if not any(
            rule["type"] == TYPE and rule["controller"] == engine_id
            for rule in bindings["lifecycle"]
        ):
            bindings["lifecycle"].append(
                {"controller": engine_id, "type": TYPE, "ids": "behaviour:*"}
            )
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
        "clock_mappings",
        "ingress_streams",
        "behaviours",
        "provenance",
    }
    if extra := set(document) - allowed:
        raise ScenarioError(f"scenario: unknown keys {sorted(extra)}")
    if document.get("format") != FORMAT:
        raise ScenarioError(f"format: expected {FORMAT}")
    run_id = text(document["id"], "id")
    registry_spec = obj(document["registry"], "registry")
    contract(
        registry_spec,
        "registry",
        set(),
        {
            "snapshot",
            "digest",
            "compile",
            "types",
            "fields",
            "messages",
            "message_subjects",
            "relations",
            "field_metadata",
        },
    )
    if ("snapshot" in registry_spec) == ("compile" in registry_spec):
        raise ScenarioError("registry: select exactly one snapshot or compile")
    if "snapshot" in registry_spec:
        compiled = read_snapshot(
            source_path(text(registry_spec["snapshot"], "registry.snapshot"), base)
        )
    else:
        spec = obj(registry_spec["compile"], "registry.compile")
        contract(
            spec,
            "registry.compile",
            {"root", "types"},
            {"fields", "relations", "policy"},
        )
        compiled = compile_registry(
            source_path(text(spec["root"], "registry.compile.root"), base),
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
        item = contract(item, "registry.types[]", {"id", "parents", "abstract"})
        if type(item["abstract"]) is not bool:
            raise ScenarioError("registry.types[].abstract: explicit bool required")
        types.append(
            TypeDescriptor(item["id"], tuple(item["parents"]), item["abstract"])
        )
    for item in seq(registry_spec.get("fields", []), "registry.fields"):
        item = contract(item, "registry.fields[]", {"id", "type", "schema", "metadata"})
        fields.append(
            FieldDescriptor(item["id"], item["type"], item["schema"], item["metadata"])
        )
    for item in seq(registry_spec.get("messages", []), "registry.messages"):
        item = contract(
            item,
            "registry.messages[]",
            {"id", "kind", "schema"},
            {"result_schema", "feedback_schema", "cancel_support", "subjects"},
        )
        messages.append(
            MessageDescriptor(
                **{key: value for key, value in item.items() if key != "subjects"}
            )
        )
    overrides = obj(registry_spec.get("field_metadata", {}), "registry.field_metadata")
    known_fields = {f.id for f in fields}
    if set(overrides) - known_fields:
        raise ScenarioError("registry.field_metadata: unknown field")
    for index, descriptor in enumerate(fields):
        if descriptor.id in overrides:
            metadata = contract(
                overrides[descriptor.id],
                f"registry.field_metadata.{descriptor.id}",
                {"frame", "transform_revision"},
            )
            if metadata["frame"] not in {"enu", "ned", "wgs84"}:
                raise ScenarioError(
                    "registry.field_metadata.frame: unsupported spatial convention"
                )
            text(
                metadata["transform_revision"],
                "registry.field_metadata.transform_revision",
            )
            fields[index] = FieldDescriptor(
                descriptor.id,
                descriptor.declaring_type,
                obj(thaw(descriptor.schema), "registry.field.schema"),
                {
                    **obj(thaw(descriptor.metadata), "registry.field.metadata"),
                    **metadata,
                },
            )
    relations = []
    source_relations = {item["id"]: item for item in compiled.relations}
    for item in registry_spec.get("relations", []):
        item = contract(
            item,
            "registry.relations[]",
            {
                "id",
                "source_type",
                "target_type",
                "targets_per_source",
                "sources_per_target",
                "identity_policy",
                "metadata",
            },
        )
        for direction in ("targets_per_source", "sources_per_target"):
            cardinality = contract(
                item[direction],
                "registry.relations." + direction,
                {"minimum", "maximum"},
            )
            integer(cardinality["minimum"], "registry.relations.minimum")
            if cardinality["maximum"] is not None:
                integer(cardinality["maximum"], "registry.relations.maximum")
        native = source_relations.get(item["id"])
        if native is not None and (
            item["source_type"] != native["source_type"]
            or item["target_type"] != native["target_type"]
        ):
            raise ScenarioError(
                "registry.relations: endpoint classes must match the compiled source"
            )
        for direction, declared in (
            native["cardinality"].items() if native is not None else []
        ):
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
    try:
        declarations(registry, document)
    except (ValueError, KeyError, TypeError) as exc:
        raise ScenarioError(str(exc), code=getattr(exc, "code", None)) from exc
    refs: list[EntityRef] = []
    initial: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(seq(document["entities"], "entities")):
        item = contract(item, f"entities[{index}]", {"id", "type", "facts"})
        entity_id = text(item["id"], f"entities[{index}].id")
        type_id = text(item["type"], f"entities[{index}].type")
        if entity_id in initial:
            raise ScenarioError(f"entities[{index}].id: duplicate {entity_id!r}")
        try:
            registry.is_a(type_id, type_id)
        except ValueError as exc:
            raise ScenarioError(
                f"entities.{entity_id}.type: {exc}", code=getattr(exc, "code", None)
            ) from exc
        refs.append(EntityRef(run_id, "0", entity_id, 0, type_id))
        initial[entity_id] = obj(item["facts"], f"entities[{index}].facts")
        for field, value in initial[entity_id].items():
            path = f"entities.{entity_id}.facts.{field}"
            try:
                descriptor = registry.field(field)
                if not registry.is_a(type_id, descriptor.declaring_type):
                    raise ScenarioError(f"{path}: field does not apply to {type_id}")
                registry.validate(descriptor.schema, value)
            except ScenarioError:
                raise
            except ValueError as exc:
                raise ScenarioError(
                    f"{path}: {exc}", code=getattr(exc, "code", None)
                ) from exc
    by_id = {ref.id: ref for ref in refs}
    bindings = obj(document["bindings"], "bindings")
    contract(
        bindings,
        "bindings",
        {"lifecycle"},
        {
            "rules",
            "exact",
            "commands",
            "cohorts",
            "relations",
            "obligations",
            "samples",
            "cancel_sources",
            "lifecycle_participants",
        },
    )
    binding_shapes = {
        "rules": ({"writer", "type", "fields"}, {"ids", "priority"}),
        "exact": ({"entity", "field", "writer"}, set()),
        "lifecycle": ({"controller", "type"}, {"ids", "priority"}),
        "commands": ({"schema", "target", "at_ns", "payload"}, set()),
        "relations": ({"writer", "relation", "type"}, {"ids"}),
        "obligations": ({"controller", "relation", "direction", "type"}, {"ids"}),
        "samples": (
            {
                "context",
                "partition",
                "upstream",
                "bindings",
                "sources",
                "clocks",
                "parameters",
            },
            set(),
        ),
    }
    for section, (required, optional) in binding_shapes.items():
        for item in seq(bindings.get(section, []), "bindings." + section):
            contract(item, "bindings." + section, required, optional)
            if "priority" in item and type(item["priority"]) is not int:
                raise ScenarioError("bindings.priority: integer required")
            if "ids" in item:
                text(item["ids"], "bindings.ids")
            for name in {
                "writer",
                "controller",
                "type",
                "entity",
                "field",
                "schema",
                "target",
                "relation",
                "direction",
                "context",
                "partition",
            } & set(item):
                text(item[name], "bindings." + section + "." + name)
            for name in {"fields", "upstream"} & set(item):
                for value in seq(item[name], "bindings." + name):
                    text(value, "bindings." + name + "[]")
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
        cancel_sources=tuple(
            text(value, "bindings.cancel_sources[]")
            for value in seq(
                bindings.get("cancel_sources", []), "bindings.cancel_sources"
            )
        ),
        lifecycle_participants={
            type_id: tuple(
                text(value, "bindings.lifecycle_participants[]")
                for value in seq(values, "bindings.lifecycle_participants")
            )
            for type_id, values in obj(
                bindings.get("lifecycle_participants", {}),
                "bindings.lifecycle_participants",
            ).items()
        },
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
    sampled_asts: dict[str, dict[str, Any]] = {}
    for engine_id, item in engines.items():
        item = contract(item, f"engines.{engine_id}", {"plugin", "config"}, {"ingress"})
        text(item["plugin"], f"engines.{engine_id}.plugin")
        obj(item["config"], f"engines.{engine_id}.config")
    for item in seq(document.get("presentation", []), "presentation"):
        item = contract(
            item,
            "presentation[]",
            {"typeId", "positionField", "frame", "visual"},
            {"orientationField"},
        )
        registry.is_a(item["typeId"], item["typeId"])
        position = registry.field(item["positionField"])
        if not registry.is_a(item["typeId"], position.declaring_type):
            raise ScenarioError(
                "presentation.positionField: field does not apply to type"
            )
        if (
            position.schema.get("type") != "vector"
            or position.schema.get("length") != 3
        ):
            raise ScenarioError("presentation.positionField: requires a three-vector")
        if item["frame"] not in {"enu", "ned", "wgs84"}:
            raise ScenarioError("presentation.frame: expected enu, ned, wgs84")
        declared_frame = position.metadata.get("frame")
        if isinstance(declared_frame, str) and item["frame"] != declared_frame:
            raise ScenarioError(
                "presentation.frame: conflicts with bound descriptor frame"
            )
        if "orientationField" in item:
            orientation = registry.field(item["orientationField"])
            if (
                not registry.is_a(item["typeId"], orientation.declaring_type)
                or orientation.schema.get("type") != "vector"
                or orientation.schema.get("length") != 4
            ):
                raise ScenarioError(
                    "presentation.orientationField: applicable quaternion required"
                )
            orientation_frame = orientation.metadata.get("frame")
            if (
                isinstance(orientation_frame, str)
                and orientation_frame != item["frame"]
            ):
                raise ScenarioError("presentation.orientationField: frame mismatch")
        visual = contract(
            item["visual"], "presentation.visual", {"kind"}, {"asset", "scale", "color"}
        )
        for name in {"asset", "color"} & set(visual):
            text(visual[name], "presentation.visual." + name)
        if (
            "scale" in visual
            and numeric(visual["scale"], "presentation.visual.scale") <= 0
        ):
            raise ScenarioError("presentation.visual.scale: positive scale required")
        if visual["kind"] == "model" and "asset" not in visual:
            raise ScenarioError("presentation.visual.asset: model asset required")
        if item["visual"]["kind"] not in {"marker", "model", "label"}:
            raise ScenarioError(
                "presentation.visual.kind: expected marker, model, label"
            )
    if "origin" in document:
        origin = contract(document["origin"], "origin", {"lat", "lon", "alt"})
        for name, value in origin.items():
            numeric(value, "origin." + name)
        if not -90 <= origin["lat"] <= 90 or not -180 <= origin["lon"] <= 180:
            raise ScenarioError("origin: invalid latitude or longitude")
    if (
        any(item["frame"] == "wgs84" for item in document.get("presentation", []))
        and "origin" not in document
    ):
        raise ScenarioError("presentation.wgs84: explicit origin required")
    run = contract(document["run"], "run", {"pacing", "seed", "until_ns", "advance_ns"})
    pacing = text(run["pacing"], "run.pacing")
    if pacing not in {"fast", "realtime"}:
        raise ScenarioError("run.pacing: expected fast or realtime")
    outputs = contract(document["outputs"], "outputs", {"durability"})
    if outputs["durability"] not in {"flush", "fsync"}:
        raise ScenarioError("outputs.durability: expected flush or fsync")
    clock_mappings = None
    if "clock_mappings" in document:
        authored_mappings = []
        mapping_ids: set[str] = set()
        for index, item in enumerate(seq(document["clock_mappings"], "clock_mappings")):
            path = f"clock_mappings[{index}]"
            spec = contract(
                item,
                path,
                {"mapping_id", "clock_id"},
                {"offset_ns", "p", "q", "rounding"},
            )
            try:
                mapping = ClockMapping(**spec)
            except (ValueError, TypeError) as exc:
                raise ScenarioError(
                    f"{path}: {exc}", code=getattr(exc, "code", None)
                ) from exc
            if mapping.mapping_id in mapping_ids:
                raise ScenarioError(path + ".mapping_id: duplicate mapping_id")
            mapping_ids.add(mapping.mapping_id)
            authored_mappings.append(mapping)
        clock_mappings = tuple(authored_mappings)
        if not any(
            m.mapping_id == "canonical"
            and m.clock_id == "canonical"
            and m.offset_ns == 0
            and m.p == m.q == 1
            for m in clock_mappings
        ):
            raise ScenarioError(
                "clock_mappings: retain identity canonical mapping for local "
                "model policies"
            )
    ingress: dict[str, EngineIngress] = {}
    mappings = {
        m.mapping_id: m
        for m in clock_mappings or (ClockMapping("canonical", "canonical"),)
    }
    streams: dict[str, IngressStream] = {}
    stream_paths: dict[str, str] = {}

    def stream_policy(spec: dict[str, Any], path: str) -> tuple[str, IngressPolicy]:
        mapping_id = text(spec["mapping_id"], path + ".mapping_id")
        if mapping_id not in mappings:
            raise ScenarioError(path + ".mapping_id: unknown clock mapping")
        lateness = text(spec["lateness"], path + ".lateness")
        if lateness not in {"reject", "delay"}:
            raise ScenarioError(path + ".lateness: expected reject or delay")
        timeout = numeric(spec["timeout_s"], path + ".timeout_s")
        if timeout <= 0:
            raise ScenarioError(path + ".timeout_s: positive wait budget required")
        return mapping_id, IngressPolicy(
            integer(spec["initial_watermark_ns"], path + ".initial_watermark_ns"),
            lateness,
            timeout,
            allowed_lateness_ns=integer(
                spec["allowed_lateness_ns"], path + ".allowed_lateness_ns"
            )
            if "allowed_lateness_ns" in spec
            else None,
        )

    policy_keys = {"mapping_id", "initial_watermark_ns", "lateness", "timeout_s"}
    for index, authored in enumerate(
        seq(document.get("ingress_streams", []), "ingress_streams")
    ):
        path = f"ingress_streams[{index}]"
        spec = contract(
            authored, path, policy_keys | {"id", "engine_ids"}, {"allowed_lateness_ns"}
        )
        stream_id = text(spec["id"], path + ".id")
        if stream_id in streams:
            raise ScenarioError(path + ".id: duplicate stream declaration")
        engine_ids = tuple(
            text(value, f"{path}.engine_ids[{i}]")
            for i, value in enumerate(seq(spec["engine_ids"], path + ".engine_ids"))
        )
        if not engine_ids or len(set(engine_ids)) != len(engine_ids):
            raise ScenarioError(
                path + ".engine_ids: unique nonempty engine list required"
            )
        for i, engine_id in enumerate(engine_ids):
            if engine_id not in engines:
                raise ScenarioError(
                    f"{path}.engine_ids[{i}]: unknown engine {engine_id}"
                )
        mapping_id, policy = stream_policy(spec, path)
        try:
            streams[stream_id] = IngressStream(
                stream_id, policy, mapping_id, engine_ids
            )
        except KernelError as exc:
            raise ScenarioError(f"{path}: {exc}", code=exc.code) from exc
        stream_paths[stream_id] = path
    for engine_id, item in engines.items():
        if "ingress" not in item:
            continue
        path = f"engines.{engine_id}.ingress"
        spec = contract(
            item["ingress"],
            path,
            policy_keys,
            {"stream_id", "allowed_lateness_ns"},
        )
        mapping_id, policy = stream_policy(spec, path)
        stream_id = text(spec.get("stream_id", engine_id), path + ".stream_id")
        prior = streams.get(stream_id)
        if prior is not None:
            if prior.mapping_id != mapping_id or prior.policy != policy:
                raise ScenarioError(
                    f"{path}: inconsistent declaration of stream {stream_id!r}; "
                    f"first declared at {stream_paths[stream_id]}"
                )
            engine_ids = tuple(dict.fromkeys((*prior.engine_ids, engine_id)))
        else:
            engine_ids = (engine_id,)
        try:
            streams[stream_id] = IngressStream(
                stream_id, policy, mapping_id, engine_ids
            )
        except KernelError as exc:
            raise ScenarioError(f"{path}: {exc}", code=exc.code) from exc
        stream_paths.setdefault(stream_id, path)
        ingress[engine_id] = EngineIngress(mapping_id, policy, stream_id)
    # AST admission belongs to scenario load, before a kernel or external engine
    # starts. This does not alter the registry compiler or its snapshot.
    for engine_id, item in engines.items():
        if item["plugin"] == "predicate":
            from aeroagentsim.engines.predicate import prepare

            try:
                sampled_asts[engine_id] = prepare(item["config"])
            except (ValueError, KeyError, TypeError) as exc:
                raise ScenarioError(f"engines.{engine_id}.config: {exc}") from exc
    contexts = {spec.context_id: spec for spec in manifest.samples}
    for engine_id in behaviour_engines:
        for package in engines[engine_id]["config"]["packages"]:
            for predicate_id, definition in package["document"]["predicates"].items():
                if definition["profile"] != "aerograph_sampled/v1":
                    continue
                path = f"{package['source']}: $.predicates.{predicate_id}.adapter"
                adapter = definition["adapter"]
                for context_id in adapter.get("contexts", [adapter.get("context")]):
                    sample_spec = contexts.get(context_id)
                    if sample_spec is None or sample_spec.partition not in sampled_asts:
                        raise ScenarioError(
                            f"{path}: bind an actual finite Q6 predicate sample context"
                        )
                    config = engines[sample_spec.partition]["config"]
                    expected = definition["expression"]
                    if "contexts" in adapter:
                        expected_config = copy.deepcopy(config)
                        expected_config["definitions"][config["target"]][
                            "expression"
                        ] = expected
                        expected = prepare(expected_config)
                    if (
                        config["event"] != adapter["event"]
                        or sampled_asts[sample_spec.partition] != expected
                        or thaw(cast(Any, sample_spec.parameters))
                        != definition.get("parameters", {})
                    ):
                        raise ScenarioError(
                            f"{path}: expression, event and parameters must "
                            "match the Q6 sampled partition"
                        )
                    if set(sample_spec.bindings) != set(definition["roles"]):
                        raise ScenarioError(
                            f"{path}: sampled role aliases must match the "
                            "actual Q6 bindings"
                        )
    provenance = document.get("provenance", "lean")
    if type(provenance) is not str or provenance not in {"lean", "full"}:
        raise ScenarioError("provenance: expected lean or full")
    return Scenario(
        document,
        source,
        base,
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
        clock_mappings,
        ingress,
        tuple(streams.values()),
        provenance,
    )
