"""Compare declared JSON normalization against the pre-T19 implementation."""
from __future__ import annotations

import copy
import json
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Annotated, Literal, Tuple, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from aero_bench.config import models
from aero_bench.config.models import StrictModel, SuiteSpec
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.runtime.contracts import SceneState
from aero_bench.tasks.inspection.contracts import BusinessCommandEnvelope
from aero_bench.tasks.inspection.formal_v2_contracts import InspectionFormalEvidenceBundleV2
from aero_bench.tasks.logistics.facilities import AuthoredFacility
from aero_bench.trace.contracts import PublicTrace
from aero_bench.world.frames import LocalFrameOrigin


# Reference implementation, verbatim except for the enclosing class name.
def _normalize_declared_value(value: object, annotation: object) -> object:
    """Normalize JSON-shaped values without coercing declared scalar types.

    Strict Pydantic validation treats a Python ``str`` as different from a
    ``str, Enum`` member, although the member is necessarily represented by
    that string on the JSON wire.  Convert only exact declared enum *values*
    and recurse through declared containers/models; integers, floats, booleans,
    identifiers, and unknown enum values remain untouched for strict rejection.
    """

    origin = get_origin(annotation)
    if origin is Annotated:
        arguments = get_args(annotation)
        return (
            _normalize_declared_value(value, arguments[0]) if arguments else value
        )
    if origin is tuple:
        if isinstance(value, list):
            value = tuple(value)
        if not isinstance(value, tuple):
            return value
        arguments = get_args(annotation)
        if not arguments:
            return value
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            item_annotation = arguments[0]
            return tuple(
                _normalize_declared_value(item, item_annotation) for item in value
            )
        if len(arguments) != len(value):
            return value
        return tuple(
            _normalize_declared_value(item, item_annotation)
            for item, item_annotation in zip(value, arguments, strict=True)
        )
    if origin in (list, set, frozenset):
        arguments = get_args(annotation)
        if len(arguments) != 1 or not isinstance(value, (list, tuple, set, frozenset)):
            return value
        item_annotation = arguments[0]
        normalized = [
            _normalize_declared_value(item, item_annotation) for item in value
        ]
        if origin is list:
            return normalized
        if origin is set:
            return set(normalized)
        return frozenset(normalized)
    if origin is dict:
        arguments = get_args(annotation)
        if len(arguments) != 2 or not isinstance(value, dict):
            return value
        key_annotation, item_annotation = arguments
        return {
            _normalize_declared_value(key, key_annotation): _normalize_declared_value(
                item, item_annotation
            )
            for key, item in value.items()
        }
    if origin in (Union, UnionType):
        for branch in get_args(annotation):
            normalized = _normalize_declared_value(value, branch)
            if normalized is not value:
                return normalized
        return value
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if isinstance(value, str) and not isinstance(value, annotation):
            try:
                return annotation(value)
            except ValueError:
                return value
        return value
    if (
        isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
        and isinstance(value, dict)
    ):
        normalized = dict(value)
        for name, field in annotation.model_fields.items():
            if name in normalized:
                normalized[name] = _normalize_declared_value(
                    normalized[name], field.annotation
                )
        return normalized
    return value


class _ReferenceStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_declared_json_values(cls, value: object) -> object:
        """Normalize structural JSON values at the strict model boundary.

        Pydantic strict mode correctly rejects scalar coercion but Python
        mappings decoded from JSON also need their exact enum values converted
        to members before validation.  The normalization is structural and
        never accepts aliases, legacy field names, or scalar coercion.
        """

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            if name in normalized:
                normalized[name] = _normalize_declared_value(
                    normalized[name], field.annotation
                )
        return normalized


class _Color(str, Enum):
    RED = "red"
    BLUE = "blue"


class _Child(StrictModel):
    color: _Color
    items: tuple[_Color, ...]
    mapping: dict[_Color, tuple[_Color, int]]


class _Nested(StrictModel):
    child: _Child
    children: tuple[_Child, ...]


class _TupleBranch(StrictModel):
    kind: Literal["tuple"]
    value: tuple[int, ...]


class _ObjectBranch(StrictModel):
    kind: Literal["object"]
    value: object


class _ModelUnion(StrictModel):
    branch: _TupleBranch | _ObjectBranch


class _BareTupleBranch(StrictModel):
    kind: Literal["bare-tuple"]
    value: Tuple | list[int]


class _BareTupleModelUnion(StrictModel):
    branch: _BareTupleBranch | _ObjectBranch


class _EnumField(StrictModel):
    value: _Color


class _StringField(StrictModel):
    value: str


class _PlainChild(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    color: _Color
    values: tuple[_Color, ...]


class _PlainParent(StrictModel):
    child: _PlainChild


class _Containers(StrictModel):
    bare: Tuple
    fixed: tuple[int, str]
    items: list[int]
    unique: set[_Color]
    frozen: frozenset[_Color]
    annotated: Annotated[tuple[_Color, ...], "metadata"]
    optional: _Child | None


class _Recursive(StrictModel):
    child: _Recursive | None


class _RecursiveUnsafe(StrictModel):
    child: _RecursiveUnsafe | None
    value: Tuple | list[int]


def _validate(model, document):
    try:
        return True, model.model_validate(copy.deepcopy(document))
    except ValidationError:
        return False, None


def _compare(monkeypatch, model, document, label):
    function = StrictModel.normalize_declared_json_values.__func__
    with monkeypatch.context() as patch:
        patch.setattr(models, "_normalize_declared_value", _normalize_declared_value)
        patch.setattr(
            function, "__code__",
            _ReferenceStrictModel.normalize_declared_json_values.__func__.__code__,
        )
        old_accepted, old_model = _validate(model, document)
    new_accepted, new_model = _validate(model, document)
    assert old_accepted == new_accepted, label
    if old_accepted:
        assert old_model == new_model, (
            f"{label}: old={old_model!r}; new={new_model!r}"
        )
        assert old_model.model_dump(mode="json") == new_model.model_dump(mode="json"), label
    return old_accepted


def _real_documents():
    root = Path(__file__).parent
    paths = sorted(root.rglob("*.json")) + sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml"))
    for path in paths:
        if path.suffix == ".json":
            document = json.loads(path.read_text(encoding="utf-8"))
        else:
            from aero_bench.config.loader import _load_yaml_document
            document = _load_yaml_document(path.read_text(encoding="utf-8"), str(path))
        # These fixture roots are renderer/golden-data envelopes rather than
        # SuiteSpecs. Retain their rejected root cases as well as typed records.
        yield SuiteSpec, document, str(path.relative_to(root))
        if "cases" in document:
            for case in document["cases"]:
                yield AuthoredFacility, case["facility"], case["case_id"]
        if "origin" in document:
            yield LocalFrameOrigin, document["origin"], "golden origin"


def test_real_document_normalization_equivalence(monkeypatch, capsys):
    corpus = list(_real_documents())
    accepted = sum(
        _compare(monkeypatch, model, document, label)
        for model, document, label in corpus
    )
    with capsys.disabled():
        print(f"normalization corpus: {len(corpus)} real documents/records; accepted={accepted}; rejected={len(corpus) - accepted}")


def test_existing_bundle_documents_are_equivalent(monkeypatch, tmp_path, capsys):
    """Exercise the typed JSON/YAML inputs produced by the shared test fixture."""
    from tests.support import build_bundle, resolve_bundle

    corpus = []
    validate = BaseModel.model_validate.__func__

    @classmethod
    def record(cls, value, **kwargs):
        if issubclass(cls, StrictModel):
            corpus.append((cls, copy.deepcopy(value), cls.__name__))
        return validate(cls, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(BaseModel, "model_validate", record)
        bundle = build_bundle(tmp_path)
        resolve_bundle(bundle.suite, executor_kind="docker_reference")
    assert corpus
    accepted = sum(_compare(monkeypatch, *entry) for entry in corpus)
    safe_count = 0
    normalize = _ReferenceStrictModel.normalize_declared_json_values.__func__
    for model, document, label in corpus:
        if models._normalization_is_safe(model):
            safe_count += 1
            once = normalize(model, copy.deepcopy(document))
            assert normalize(model, once) == once, label
            new_once = model.normalize_declared_json_values(copy.deepcopy(document))
            assert new_once == once, label
            assert model.normalize_declared_json_values(new_once) is new_once, label
    with capsys.disabled():
        print(f"normalization shared-bundle corpus: {len(corpus)} documents; "
              f"accepted={accepted}; safe idempotence={safe_count}")


def _targeted_documents():
    child = {"color": "red", "items": ["red", "blue"], "mapping": {"blue": ["red", 1]}}
    return [
        (_Child, child, "enums in tuples/dicts"),
        (_Nested, {"child": child, "children": [child]}, "nested enums"),
        (_Child, {**child, "color": "unknown"}, "unknown enum"),
        (_Child, {**child, "mapping": {"blue": ["red", "1"]}}, "scalar coercion"),
        (_EnumField, {"value": "RED"}, "enum name rejected"),
        (_EnumField, {"value": "red"}, "strict string to enum"),
        (_EnumField, {"value": _Color.RED}, "strict enum input"),
        (_StringField, {"value": "red"}, "strict string input"),
        (_StringField, {"value": _Color.RED}, "enum to strict string"),
        (_PlainParent, {"child": {"color": "red", "values": ["blue"]}}, "non-StrictModel recursion"),
        (_ModelUnion, {"branch": {"kind": "tuple", "value": [1]}}, "first union branch"),
        (_ModelUnion, {"branch": {"kind": "object", "value": [1]}}, "second union branch"),
        (_Containers, {"bare": [1], "fixed": [1, "x"], "items": (1,),
                       "unique": ["red", "blue"], "frozen": ["blue"],
                       "annotated": ["red"], "optional": child}, "container conversions"),
        (_Containers, {"bare": [], "fixed": [1], "items": ["1"],
                       "unique": ["unknown"], "frozen": [],
                       "annotated": [], "optional": None}, "invalid containers"),
        (_Recursive, {"child": {"child": None}}, "recursive safe model"),
        (_RecursiveUnsafe, {"child": None, "value": [1]}, "recursive unsafe model"),
    ]


def _non_idempotent_document():
    return (
        _BareTupleModelUnion,
        {"branch": {"kind": "bare-tuple", "value": [1]}},
        "model union containing a bare-tuple/list union",
    )


def test_targeted_normalization_equivalence(monkeypatch, capsys):
    corpus = _targeted_documents() + [_non_idempotent_document()]
    with capsys.disabled():
        print(f"normalization targeted corpus: {len(corpus)} documents")
    for model, document, label in corpus:
        _compare(monkeypatch, model, document, label)


def test_reference_normalization_is_idempotent_on_corpus(capsys):
    corpus = [
        entry for entry in list(_real_documents()) + _targeted_documents()
        if models._normalization_is_safe(entry[0])
    ]
    normalize = _ReferenceStrictModel.normalize_declared_json_values.__func__
    with capsys.disabled():
        print(f"normalization idempotence corpus: {len(corpus)} documents/records")
    for model, document, label in corpus:
        once = normalize(model, copy.deepcopy(document))
        twice = normalize(model, once)
        assert once == twice, f"{label}: once={once!r}; twice={twice!r}"
        new_once = model.normalize_declared_json_values(copy.deepcopy(document))
        assert new_once == once, label
        assert model.normalize_declared_json_values(new_once) == new_once, label
        assert model.normalize_declared_json_values(new_once) is new_once, label


def test_reference_normalization_is_not_always_idempotent():
    """A2 cannot skip this child's second pass without changing its model."""
    normalize = _ReferenceStrictModel.normalize_declared_json_values.__func__
    model, document, _ = _non_idempotent_document()
    parent_normalized = normalize(model, document)
    child = parent_normalized["branch"]
    assert child["value"] == (1,)
    assert normalize(_BareTupleBranch, child)["value"] == [1]
    assert normalize(model, parent_normalized) != parent_normalized


def test_normalization_safety_classifier():
    for model in (
        _ModelUnion, _BareTupleBranch, _BareTupleModelUnion, _RecursiveUnsafe,
        LogisticsBusinessConfig, BusinessCommandEnvelope,
        InspectionFormalEvidenceBundleV2,
    ):
        assert not models._normalization_is_safe(model), model.__name__
    for model in (PublicTrace, SceneState, _Nested, _Containers, _Recursive):
        assert models._normalization_is_safe(model), model.__name__
    for model, name in (
        (LogisticsBusinessConfig, "task_package"),
        (BusinessCommandEnvelope, "command"),
        (InspectionFormalEvidenceBundleV2, "network_outcome"),
    ):
        annotation = model.model_fields[name].annotation
        assert sum(models._annotation_is_active(branch) for branch in get_args(annotation)) >= 2


def test_union_counterexamples_keep_old_models():
    value = _ModelUnion.model_validate({"branch": {"kind": "object", "value": [1]}})
    assert value.branch.value == (1,)
    model, document, _ = _non_idempotent_document()
    value = model.model_validate(document)
    assert value.branch.value == [1]


def test_wrong_class_marker_does_not_skip_normalization():
    value = _TupleBranch.normalize_declared_json_values({"kind": "object", "value": [1]})
    normalized = _ObjectBranch.normalize_declared_json_values(value)
    assert normalized is not value
    assert normalized.model is _ObjectBranch


def test_cached_plans_preserve_values_exceptions_and_union_identity(capsys):
    annotations = (
        object, int, str, _Color, Tuple, tuple[int, ...], tuple[_Color, int],
        list[int], set[_Color], frozenset[_Color], dict[_Color, tuple[_Color, int]],
        list, tuple, dict, Union[int, str, None], Tuple | list[int],
        tuple[int, ...] | list[int], _TupleBranch | _ObjectBranch,
        _Child, _PlainChild, Annotated[tuple[_Color, ...], ["unhashable metadata"]],
        Annotated[str, ["unhashable scalar metadata"]],
        Tuple | str, list[int] | None,
        tuple[Ellipsis, int], tuple[int, Ellipsis, str],
    )
    values = (
        None, True, 1, 1.0, "1", "red", "unknown", _Color.BLUE,
        [], [1], ["red"], ["red", 1], (1,), ("red", 1), {1},
        frozenset({"blue"}), {}, {"blue": ["red", 1]},
        {"kind": "object", "value": [1]},
        {"color": "red", "items": ["blue"], "mapping": {}},
        {"color": "red", "values": ["blue"]},
    )
    for annotation in annotations:
        assert models._normalization_plan(annotation) is models._normalization_plan(annotation)
        for value in values:
            old_input = copy.deepcopy(value)
            new_input = copy.deepcopy(value)
            try:
                old = _normalize_declared_value(old_input, annotation)
            except (TypeError, ValueError) as error:
                try:
                    models._normalize_declared_value(new_input, annotation)
                except (TypeError, ValueError) as new_error:
                    assert type(error) is type(new_error), (annotation, value)
                else:
                    raise AssertionError((annotation, value, type(error))) from error
            else:
                new = models._normalize_declared_value(new_input, annotation)
                assert old == new, (annotation, value, old, new)
                assert (old is old_input) == (new is new_input), (annotation, value)
    with capsys.disabled():
        print(f"normalization plan corpus: {len(annotations) * len(values)} annotation/value pairs")
