from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from aero_bench.serialization import canonical_json_bytes
from tools import audit_image_provenance


CAPTURED_AT_UTC = "2026-09-01T02:39:48.823963+00:00"
HEAD = "d" * 40


def _digest(left: str, right: str) -> str:
    return (left + right) * 32


def _image_id(left: str, right: str) -> str:
    return "sha256:" + _digest(left, right)


class FakeRunner:
    def __init__(
        self,
        *,
        tracked_paths: list[str],
        listing: list[dict[str, Any]],
        inspected: list[dict[str, Any]],
        status: str = "",
        untracked_paths: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._tracked_paths = tracked_paths
        self._untracked_paths = untracked_paths or []
        self._listing = listing
        self._inspected = inspected
        self._status = status

    def __call__(self, command: tuple[str, ...], cwd: Path) -> str:
        self.calls.append(command)
        if command == ("git", "rev-parse", "HEAD"):
            return HEAD + "\n"
        if command == ("git", "branch", "--show-current"):
            return "repair/inspection-v1-r5\n"
        if command == ("git", "status", "--porcelain=v1", "--untracked-files=all"):
            return self._status
        if command == ("git", "ls-files", "-z"):
            return "\0".join(self._tracked_paths) + ("\0" if self._tracked_paths else "")
        if command == (
            "git",
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
        ):
            return "\0".join(self._untracked_paths) + (
                "\0" if self._untracked_paths else ""
            )
        if command[:3] == ("docker", "image", "ls"):
            return "\n".join(json.dumps(row) for row in self._listing)
        if command[:3] == ("docker", "image", "inspect"):
            return json.dumps(self._inspected)
        raise AssertionError(f"unexpected command: {command}")


class HistoricalRunner:
    def __init__(
        self,
        *,
        revision: str,
        paths: dict[str, str],
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._revision = revision
        self._paths = paths

    def __call__(self, command: tuple[str, ...], cwd: Path) -> str:
        self.calls.append(command)
        if command == (
            "git",
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            self._revision,
        ):
            return "\0".join(sorted(self._paths)) + "\0"
        if (
            len(command) == 3
            and command[0] == "git"
            and command[1] == "show"
            and command[2].startswith(f"{self._revision}:")
        ):
            path = command[2].split(":", maxsplit=1)[1]
            if path not in self._paths:
                raise AssertionError(f"unexpected git show path: {path}")
            return self._paths[path]
        raise AssertionError(f"unexpected command: {command}")


def _write_sources(tmp_path: Path, sources: dict[str, str]) -> list[str]:
    for relative_path, content in sources.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(sources)


def _build_audit(
    tmp_path: Path,
    sources: dict[str, str],
    listing: list[dict[str, Any]],
    inspected: list[dict[str, Any]],
) -> tuple[dict[str, Any], FakeRunner]:
    runner = FakeRunner(
        tracked_paths=_write_sources(tmp_path, sources),
        listing=listing,
        inspected=inspected,
    )
    return (
        audit_image_provenance.build_audit(
            repo_root=tmp_path,
            captured_at_utc=CAPTURED_AT_UTC,
            runner=runner,
        ),
        runner,
    )


def test_audit_uses_git_and_docker_and_classifies_worktree_references(
    tmp_path: Path,
) -> None:
    production = "127.0.0.1:5000/aero-bench/harness@sha256:" + _digest("a", "b")
    documentation = "registry.example/aero-bench/harness@sha256:" + _digest("c", "d")
    fixture = "registry.example/aero-bench/provider@sha256:" + "1" * 64
    payload, runner = _build_audit(
        tmp_path,
        {
            "releases/inspection-v1/environment.yaml": f"image: {production}\n",
            "docs/validation.md": f"{documentation}\n",
            "tests/test_readiness.py": (
                'RUNTIME_IMAGE = "registry.example/aero-bench/provider@sha256:" '
                '+ "1" * 64\n'
            ),
        },
        [],
        [],
    )

    records = {
        (record["classification"], record["reference"]): record
        for record in payload["worktree_immutable_references"]
    }
    assert records[("production_release_runtime", production)]["source_paths"] == [
        "releases/inspection-v1/environment.yaml"
    ]
    assert records[("documentation", documentation)]["source_paths"] == [
        "docs/validation.md"
    ]
    assert records[("test_fixture", fixture)]["source_paths"] == [
        "tests/test_readiness.py"
    ]
    assert ("production_release_runtime", fixture) not in records
    assert payload["source_state"]["branch"] == "repair/inspection-v1-r5"
    assert payload["source_state"]["head"] == HEAD
    assert payload["source_state"]["source_file_count"] == 3
    assert len(payload["source_state"]["source_tree_digest"]) == 64
    assert payload["source_state"]["worktree_clean"] is True
    assert [record["path"] for record in payload["source_files"]] == sorted(
        [
            "releases/inspection-v1/environment.yaml",
            "docs/validation.md",
            "tests/test_readiness.py",
        ]
    )
    assert ("git", "rev-parse", "HEAD") in runner.calls
    assert ("git", "branch", "--show-current") in runner.calls
    assert ("git", "status", "--porcelain=v1", "--untracked-files=all") in runner.calls
    assert ("git", "ls-files", "-z") in runner.calls
    assert (
        "git",
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
    ) in runner.calls
    assert any(command[:3] == ("docker", "image", "ls") for command in runner.calls)


def test_dirty_and_untracked_source_bytes_are_bound_or_rejected(
    tmp_path: Path,
) -> None:
    tracked_reference = "aero-bench/harness@sha256:" + _digest("a", "b")
    untracked_reference = "python@sha256:" + _digest("c", "d")
    tracked_path = "runtime/image.yaml"
    untracked_path = "containers/new-provider/Dockerfile"
    _write_sources(
        tmp_path,
        {
            tracked_path: f"image: {tracked_reference}\n",
            untracked_path: f"FROM {untracked_reference}\n",
        },
    )
    runner = FakeRunner(
        tracked_paths=[tracked_path],
        untracked_paths=[untracked_path],
        listing=[],
        inspected=[],
        status=f" M {tracked_path}\n?? {untracked_path}\n",
    )

    payload = audit_image_provenance.build_audit(
        repo_root=tmp_path,
        captured_at_utc=CAPTURED_AT_UTC,
        runner=runner,
    )
    assert payload["source_state"]["worktree_clean"] is False
    assert {
        (record["path"], record["git_state"])
        for record in payload["source_files"]
    } == {(tracked_path, "tracked"), (untracked_path, "untracked")}
    assert {
        record["reference"]
        for record in payload["worktree_immutable_references"]
    } == {tracked_reference, untracked_reference}
    with pytest.raises(ValueError, match="clean worktree"):
        audit_image_provenance.build_audit(
            repo_root=tmp_path,
            captured_at_utc=CAPTURED_AT_UTC,
            runner=runner,
            require_clean=True,
        )


def test_audit_sanitizes_and_canonically_orders_local_images(tmp_path: Path) -> None:
    first_id = _image_id("a", "b")
    second_id = _image_id("c", "d")
    foreign_id = _image_id("e", "f")
    first_digest = "127.0.0.1:5000/aero-bench/harness@sha256:" + _digest("1", "2")
    second_digest = (
        "127.0.0.1:5000/participants/inspection-validation@sha256:" + _digest("3", "4")
    )
    listing = [
        {"ID": second_id, "Repository": "aero-bench/foreign"},
        {"ID": foreign_id, "Repository": "foreign/image"},
        {"ID": first_id, "Repository": "aero-bench/harness"},
    ]
    inspected = [
        {
            "Id": second_id,
            "RepoTags": [
                "unrelated/image:latest",
                "participants/inspection-validation:stable",
            ],
            "RepoDigests": [second_digest],
            "Created": "2026-09-01T00:00:02Z",
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {
                "Labels": {
                    "org.opencontainers.image.revision": "external-revision",
                    "org.opencontainers.image.version": "1.0.0",
                    "private.token": "must-not-escape",
                }
            },
            "ContainerConfig": {"Env": ["SECRET=must-not-escape"]},
            "History": [{"CreatedBy": "must-not-escape"}],
        },
        {
            "Id": foreign_id,
            "RepoTags": ["foreign/image:latest"],
            "RepoDigests": [],
            "Created": "2026-09-01T00:00:03Z",
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {"Labels": {"private.token": "must-not-escape"}},
        },
        {
            "Id": first_id,
            "RepoTags": [
                "aero-bench/harness:stable",
                "unrelated/image:latest",
                "127.0.0.1:5000/aero-bench/harness:stable",
            ],
            "RepoDigests": [first_digest],
            "Created": "2026-09-01T00:00:01Z",
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {
                "Labels": {
                    "io.aero-bench.component": "aero-bench.harness",
                    "org.opencontainers.image.revision": "managed-revision",
                    "org.opencontainers.image.version": "0.2.0-harness.1",
                    "unapproved.label": "must-not-escape",
                }
            },
        },
    ]
    sources = {"releases/inspection-v1/environment.yaml": (f"image: {first_digest}\n")}

    payload, _ = _build_audit(tmp_path, sources, listing, inspected)
    reordered_payload, _ = _build_audit(
        tmp_path / "reordered",
        sources,
        list(reversed(listing)),
        list(reversed(inspected)),
    )

    assert [image["image_id"] for image in payload["local_images"]] == [
        first_id,
        second_id,
    ]
    first_image = payload["local_images"][0]
    assert first_image["repo_tags"] == [
        "127.0.0.1:5000/aero-bench/harness:stable",
        "aero-bench/harness:stable",
    ]
    assert first_image["provenance_labels"] == {
        "io.aero-bench.component": "aero-bench.harness",
        "org.opencontainers.image.revision": "managed-revision",
        "org.opencontainers.image.version": "0.2.0-harness.1",
    }
    serialized = canonical_json_bytes(payload).decode("utf-8")
    assert "must-not-escape" not in serialized
    assert "ContainerConfig" not in serialized
    assert canonical_json_bytes(payload) == canonical_json_bytes(reordered_payload)


def test_production_references_reject_placeholders_and_mutable_values() -> None:
    placeholder = "registry.example/aero-bench/harness@sha256:" + "1" * 64
    with pytest.raises(ValueError, match="placeholder"):
        audit_image_provenance._collect_reference_records(
            [
                (
                    "releases/inspection-v1/environment.yaml",
                    f"image: {placeholder}\n".encode(),
                )
            ]
        )
    with pytest.raises(ValueError, match="immutable"):
        audit_image_provenance._collect_reference_records(
            [
                (
                    "releases/inspection-v1/environment.yaml",
                    b"image: aero-bench/harness:latest\n",
                )
            ]
        )


def test_selfcheck_fixture_references_do_not_validate_as_production() -> None:
    placeholder = "registry.example/aero-bench/inspection-business@sha256:" + "1" * 64
    records = audit_image_provenance._collect_reference_records(
        [
            (
                "containers/inspection-business/selfcheck.py",
                f'RUNTIME_IMAGE = "{placeholder}"\n'.encode(),
            )
        ]
    )
    assert records == [
        {
            "classification": "test_fixture",
            "reference": placeholder,
            "source_paths": ["containers/inspection-business/selfcheck.py"],
        }
    ]


def test_docker_mismatch_and_check_mode_fail_closed(tmp_path: Path) -> None:
    listed_id = _image_id("a", "b")
    inspected_id = _image_id("c", "d")
    runner = FakeRunner(
        tracked_paths=[],
        listing=[{"ID": listed_id, "Repository": "aero-bench/harness"}],
        inspected=[{"Id": inspected_id}],
    )
    with pytest.raises(ValueError, match="mismatched image IDs"):
        audit_image_provenance.inventory_local_images(tmp_path, runner=runner)

    output = tmp_path / "provenance.json"
    payload = {"schema_version": "test/v1", "value": ["a", "b"]}
    audit_image_provenance.write_or_check(payload, output, check=False)
    audit_image_provenance.write_or_check(payload, output, check=True)
    with pytest.raises(ValueError, match="does not match"):
        audit_image_provenance.write_or_check(
            {"schema_version": "test/v1", "value": ["b", "a"]}, output, check=True
        )


def test_classify_source_path() -> None:
    for path in (
        "containers/harness/selfcheck.py",
        "containers/inspection-business/selfcheck.py",
        "containers/sumo/selfcheck.py",
        "containers/world-scene/selfcheck.py",
    ):
        assert (
            audit_image_provenance.classify_source_path(path)
            == audit_image_provenance.CLASSIFICATION_TEST_FIXTURE
        )
    assert (
        audit_image_provenance.classify_source_path("tests/test_readiness.py")
        == audit_image_provenance.CLASSIFICATION_TEST_FIXTURE
    )
    assert (
        audit_image_provenance.classify_source_path("docs/validation.md")
        == audit_image_provenance.CLASSIFICATION_DOCUMENTATION
    )


def test_build_prechange_baseline_transforms_and_preserves_reference_facts(
    tmp_path: Path,
) -> None:
    raw_capture = {
        "branch": "repair/inspection-v1-r5",
        "captured_at_utc": CAPTURED_AT_UTC,
        "head": HEAD,
        "repository": str(tmp_path),
        "schema_version": "aero-bench.provenance-baseline/v1",
        "worktree_clean": True,
        "local_aero_bench_images": [
            {
                "architecture": "amd64",
                "created": "2026-09-01T01:00:00.000000+00:00",
                "image_id": _image_id("a", "b"),
                "labels": {
                    "io.aero-bench.component": "aero-bench.harness",
                    "org.opencontainers.image.revision": "c" * 40,
                    "org.opencontainers.image.source": "https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
                    "org.opencontainers.image.version": "0.2.0-harness.1",
                    "unapproved": "discarded",
                },
                "os": "linux",
                "repo_digests": [
                    "127.0.0.1:5000/aero-bench/harness@sha256:" + _digest("a", "b"),
                    "foreign/image@sha256:1234567890abcdef" * 4,
                ],
                "repo_tags": [
                    "aero-bench/harness:stable",
                    "other/repo:latest",
                    "127.0.0.1:5000/aero-bench/harness:stable",
                ],
            }
        ],
        "referenced_runtime_images": [
            {
                "image_id": _image_id("a", "b"),
                "reference": "127.0.0.1:5000/aero-bench/harness@sha256:"
                + _digest("a", "b"),
                "revision": "c" * 40,
                "status": "present",
            },
            {
                "reference": "registry.example/aero-bench/provider@sha256:"
                + _digest("e", "f"),
                "status": "missing",
            },
        ],
    }
    raw_capture_path = tmp_path / "raw-baseline.json"
    raw_capture_path.write_text(json.dumps(raw_capture), encoding="utf-8")

    history = {
        "releases/inspection-v1/environment.yaml": (
            "image: 127.0.0.1:5000/aero-bench/harness@sha256:"
            + _digest("a", "b")
            + "\n"
        ),
        "tests/test_readiness.py": (
            'IMAGE = "registry.example/aero-bench/provider@sha256:'
            + _digest("e", "f")
            + '"\n'
        ),
        "docs/validation.md": "This references no runtime image.\n",
        "containers/harness/selfcheck.py": (
            'RUNTIME = "127.0.0.1:5000/aero-bench/harness@sha256:'
            + _digest("a", "b")
            + '"\n'
        ),
    }
    runner = HistoricalRunner(revision=HEAD, paths=history)
    payload = audit_image_provenance.build_prechange_baseline(
        raw_capture_path=raw_capture_path,
        repo_root=tmp_path,
        runner=runner,
    )

    assert payload["schema_version"] == audit_image_provenance.BASELINE_SCHEMA_VERSION
    assert (
        payload["baseline"]["kind"] == "point_in_time_pre_change_provenance_inventory"
    )
    assert payload["baseline"]["new_release_validation"] is False
    assert payload["source_state"] == {
        "branch": "repair/inspection-v1-r5",
        "head": HEAD,
        "repository": str(tmp_path),
        "worktree_clean": True,
    }
    assert (
        payload["baseline"]["raw_capture_sha256"]
        == hashlib.sha256(raw_capture_path.read_bytes()).hexdigest()
    )
    assert payload["local_images"][0]["provenance_labels"] == {
        "io.aero-bench.component": "aero-bench.harness",
        "org.opencontainers.image.revision": "c" * 40,
        "org.opencontainers.image.source": "https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
        "org.opencontainers.image.version": "0.2.0-harness.1",
    }
    assert payload["referenced_runtime_images"][0]["source_locations"] == [
        {
            "classification": "production_release_runtime",
            "source_paths": [
                "releases/inspection-v1/environment.yaml",
            ],
        },
        {
            "classification": "test_fixture",
            "source_paths": ["containers/harness/selfcheck.py"],
        },
    ]
    assert payload["referenced_runtime_images"][1]["source_locations"] == [
        {
            "classification": "test_fixture",
            "source_paths": ["tests/test_readiness.py"],
        },
    ]
    output = tmp_path / "baseline.json"
    audit_image_provenance.write_or_check(payload, output, check=False)
    audit_image_provenance.write_or_check(payload, output, check=True)
