from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from aero_bench.config.models import FileRef, SchemaBoundFile, StrictModel, SuiteSpec


ModelT = TypeVar("ModelT", bound=StrictModel)


class _DuplicateMappingKeyError(ValueError):
    """Raised when a strict document contains a repeated mapping key."""


class _StrictYamlLoader(yaml.SafeLoader):
    """SafeLoader with duplicate rejection and JSON-like scalar semantics."""


_STRICT_SCALAR_TAGS = frozenset(
    {
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:timestamp",
    }
)
_StrictYamlLoader.yaml_implicit_resolvers = {
    first: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag not in _STRICT_SCALAR_TAGS
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)\Z"),
    list("tf"),
)
_StrictYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:null",
    re.compile(r"^null\Z"),
    ["n"],
)
_StrictYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^-?(?:0|[1-9][0-9]*)\Z"),
    list("-0123456789"),
)
_StrictYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"^-?(?:(?:0|[1-9][0-9]*)\.[0-9]+(?:[eE][+-]?[0-9]+)?"
        r"|(?:0|[1-9][0-9]*)[eE][+-]?[0-9]+)\Z"
    ),
    list("-0123456789"),
)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateMappingKeyError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _strict_yaml_mapping(
    loader: _StrictYamlLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Hashable, object]:
    if not isinstance(node, MappingNode):
        raise ConstructorError(
            None,
            None,
            f"expected a mapping node, but found {node.id}",
            node.start_mark,
        )
    # SafeLoader expands YAML merge keys before constructing the mapping. Doing
    # the same here makes an explicit override or two merged copies a duplicate
    # instead of silently applying YAML's last-key-wins behavior.
    loader.flatten_mapping(node)
    mapping: dict[Hashable, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            )
        if key in mapping:
            raise _DuplicateMappingKeyError("duplicate YAML mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictYamlLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _strict_yaml_mapping,
)


def _load_yaml_document(text: str, path: str) -> object:
    try:
        return yaml.load(text, Loader=_StrictYamlLoader)
    except _DuplicateMappingKeyError:
        raise ValueError(f"duplicate mapping key in YAML document: {path}") from None
    except yaml.YAMLError:
        raise ValueError(f"invalid YAML document: {path}") from None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BundleReader:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"bundle root is not a directory: {self.root}")

    def resolve_file(self, reference: FileRef) -> Path:
        candidate = (self.root / reference.path).resolve(strict=True)
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"bundle path escapes root: {reference.path}")
        if not candidate.is_file():
            raise ValueError(f"bundle path is not a file: {reference.path}")
        actual_digest = sha256_file(candidate)
        if actual_digest != reference.sha256:
            raise ValueError(
                f"sha256 mismatch for {reference.path}: "
                f"expected {reference.sha256}, got {actual_digest}"
            )
        return candidate

    def load_yaml(self, reference: FileRef, model_type: type[ModelT]) -> ModelT:
        source = self.resolve_file(reference)
        raw = _load_yaml_document(source.read_text(encoding="utf-8"), reference.path)
        if not isinstance(raw, dict):
            raise ValueError(f"YAML root must be a mapping: {reference.path}")
        return model_type.model_validate(raw)

    def load_document(self, reference: FileRef) -> object:
        source = self.resolve_file(reference)
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".json":
            try:
                raw = json.loads(
                    text,
                    object_pairs_hook=_strict_json_object,
                    parse_constant=_reject_json_constant,
                )
            except _DuplicateMappingKeyError:
                raise ValueError(
                    f"duplicate mapping key in JSON document: {reference.path}"
                ) from None
            except (json.JSONDecodeError, ValueError):
                raise ValueError(f"invalid JSON document: {reference.path}") from None
        else:
            raw = _load_yaml_document(text, reference.path)
        try:
            json.dumps(raw, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"file is not valid JSON data: {reference.path}"
            ) from error
        self._require_string_keys(raw, reference.path)
        return raw

    def validate_schema(self, reference: FileRef) -> dict[str, object]:
        schema = self.load_document(reference)
        if not isinstance(schema, dict):
            raise ValueError(f"JSON Schema root must be a mapping: {reference.path}")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ValueError(
                f"invalid JSON Schema: {reference.path}: {error}"
            ) from error
        return schema

    def validate_schema_bound_file(self, bound_file: SchemaBoundFile) -> object:
        schema = self.validate_schema(bound_file.schema_file)
        instance = self.load_document(bound_file.file)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(instance),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            first = errors[0]
            location = "/".join(str(part) for part in first.absolute_path) or "<root>"
            raise ValueError(
                f"schema validation failed for {bound_file.file.path} at {location}: "
                f"{first.message}"
            )
        return instance

    @classmethod
    def _require_string_keys(cls, value: object, path: str) -> None:
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError(f"mapping keys must be strings in JSON data: {path}")
            for item in value.values():
                cls._require_string_keys(item, path)
        elif isinstance(value, list):
            for item in value:
                cls._require_string_keys(item, path)


@dataclass(frozen=True, slots=True)
class LoadedSuite:
    root: Path
    suite_path: Path
    suite_digest: str
    suite: SuiteSpec


def load_suite(suite_path: str | Path) -> LoadedSuite:
    resolved_suite_path = Path(suite_path).resolve(strict=True)
    if not resolved_suite_path.is_file():
        raise ValueError(f"suite configuration is not a file: {resolved_suite_path}")

    raw = _load_yaml_document(
        resolved_suite_path.read_text(encoding="utf-8"),
        resolved_suite_path.name,
    )
    if not isinstance(raw, dict):
        raise ValueError("suite configuration root must be a mapping")

    suite = SuiteSpec.model_validate(raw)
    return LoadedSuite(
        root=resolved_suite_path.parent.resolve(),
        suite_path=resolved_suite_path,
        suite_digest=sha256_file(resolved_suite_path),
        suite=suite,
    )
