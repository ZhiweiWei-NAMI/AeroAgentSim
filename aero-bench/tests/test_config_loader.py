from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import Field, ValidationError

from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import FileRef, StrictModel


class _ExampleDocument(StrictModel):
    name: str
    values: dict[str, int] = Field(default_factory=dict)


class _StrictScalarDocument(StrictModel):
    name: str
    enabled: bool
    count: int
    ratio: float
    optional: str | None
    sequence: tuple[int, ...]


class _NestedSequenceDocument(StrictModel):
    values: tuple[tuple[int, ...], ...]


def _reference(root: Path, name: str, content: str) -> FileRef:
    path = root / name
    path.write_text(content, encoding="utf-8")
    return FileRef(
        path=name,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def test_json_duplicate_keys_are_rejected_at_every_mapping_depth(
    tmp_path: Path,
) -> None:
    reader = BundleReader(tmp_path)
    for index, content in enumerate(
        (
            '{"name":"first","name":"second"}',
            '{"name":"ok","values":{"value":1,"value":2}}',
        )
    ):
        reference = _reference(tmp_path, f"duplicate-{index}.json", content)
        with pytest.raises(ValueError, match="duplicate mapping key") as error:
            reader.load_document(reference)
        assert "first" not in str(error.value)
        assert "second" not in str(error.value)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_json_nonfinite_numbers_are_rejected(tmp_path: Path, literal: str) -> None:
    reader = BundleReader(tmp_path)
    reference = _reference(
        tmp_path, f"nonfinite-{literal}.json", f'{{"value":{literal}}}'
    )

    with pytest.raises(ValueError, match="invalid JSON document"):
        reader.load_document(reference)


def test_normal_json_document_remains_available(tmp_path: Path) -> None:
    reader = BundleReader(tmp_path)
    reference = _reference(
        tmp_path,
        "normal.json",
        '{"name":"example","values":{"left":1,"right":2}}',
    )

    assert reader.load_document(reference) == {
        "name": "example",
        "values": {"left": 1, "right": 2},
    }


def test_yaml_duplicate_keys_are_rejected_at_every_mapping_depth(
    tmp_path: Path,
) -> None:
    reader = BundleReader(tmp_path)
    for index, content in enumerate(
        (
            "name: first\nname: second\n",
            "name: ok\nvalues:\n  value: 1\n  value: 2\n",
        )
    ):
        reference = _reference(tmp_path, f"duplicate-{index}.yaml", content)
        with pytest.raises(ValueError, match="duplicate mapping key") as error:
            reader.load_document(reference)
        assert "first" not in str(error.value)
        assert "second" not in str(error.value)


def test_yaml_merge_conflict_is_rejected(tmp_path: Path) -> None:
    reader = BundleReader(tmp_path)
    reference = _reference(
        tmp_path,
        "merge-conflict.yaml",
        "base: &base\n  value: 1\nobject:\n  <<: *base\n  value: 2\n",
    )

    with pytest.raises(ValueError, match="duplicate mapping key"):
        reader.load_document(reference)


def test_normal_yaml_document_and_model_loading_remain_available(
    tmp_path: Path,
) -> None:
    reader = BundleReader(tmp_path)
    content = "name: example\nvalues:\n  left: 1\n  right: 2\n"
    reference = _reference(tmp_path, "normal.yaml", content)

    assert reader.load_document(reference) == {
        "name": "example",
        "values": {"left": 1, "right": 2},
    }
    assert reader.load_yaml(reference, _ExampleDocument).name == "example"


def test_yaml_scalars_are_json_like_and_sequences_remain_yaml_native(
    tmp_path: Path,
) -> None:
    reader = BundleReader(tmp_path)
    content = (
        "name: 2026-09-01\n"
        "enabled: true\n"
        "count: 12\n"
        "ratio: 1e3\n"
        "optional: null\n"
        "sequence: [1, 2, 3]\n"
    )
    reference = _reference(tmp_path, "strict-scalars.yaml", content)

    document = reader.load_document(reference)
    assert document["name"] == "2026-09-01"
    assert type(document["name"]) is str
    model = reader.load_yaml(reference, _StrictScalarDocument)
    assert model.enabled is True
    assert model.count == 12
    assert model.ratio == 1_000.0
    assert model.optional is None
    assert model.sequence == (1, 2, 3)


def test_nested_yaml_sequences_follow_declared_tuple_structure(
    tmp_path: Path,
) -> None:
    reader = BundleReader(tmp_path)
    reference = _reference(
        tmp_path,
        "nested-sequences.yaml",
        "values:\n  - [1, 2]\n  - [3, 4]\n",
    )

    assert reader.load_yaml(reference, _NestedSequenceDocument).values == (
        (1, 2),
        (3, 4),
    )


def test_yaml_legacy_null_token_is_not_coerced(tmp_path: Path) -> None:
    reader = BundleReader(tmp_path)
    reference = _reference(tmp_path, "literal-tilde.yaml", "value: ~\n")

    assert reader.load_document(reference) == {"value": "~"}


@pytest.mark.parametrize(
    ("field", "literal"),
    (
        ("enabled", "yes"),
        ("enabled", "True"),
        ("count", "01"),
        ("count", "0x10"),
        ("ratio", ".inf"),
    ),
)
def test_yaml_legacy_scalar_coercions_are_rejected(
    tmp_path: Path,
    field: str,
    literal: str,
) -> None:
    reader = BundleReader(tmp_path)
    values = {
        "name": "example",
        "enabled": "true",
        "count": "1",
        "ratio": "1.0",
        "optional": "null",
        "sequence": "[1]",
    }
    values[field] = literal
    content = "".join(f"{name}: {value}\n" for name, value in values.items())
    reference = _reference(tmp_path, f"legacy-{field}-{literal}.yaml", content)

    with pytest.raises(ValidationError):
        reader.load_yaml(reference, _StrictScalarDocument)


def test_load_suite_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    suite = tmp_path / "suite.yaml"
    suite.write_text("suite_id: first\nsuite_id: second\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate mapping key") as error:
        load_suite(suite)
    assert "first" not in str(error.value)
    assert "second" not in str(error.value)
