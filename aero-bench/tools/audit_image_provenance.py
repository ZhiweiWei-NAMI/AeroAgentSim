from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aero_bench.serialization import canonical_json_bytes  # noqa: E402


CommandRunner = Callable[[tuple[str, ...], Path], str]

AUDIT_SCHEMA_VERSION = "aero-bench.image-provenance-audit/v2"
BASELINE_SCHEMA_VERSION = "aero-bench.image-provenance-baseline/v1"
RAW_CAPTURE_SCHEMA_VERSION = "aero-bench.provenance-baseline/v1"
ALLOWED_LABELS = (
    "io.aero-bench.component",
    "io.aero-bench.implementation-kind",
    "org.opencontainers.image.revision",
    "org.opencontainers.image.source",
    "org.opencontainers.image.version",
)
PLACEHOLDER_REGISTRIES = frozenset(
    {"registry.example", "registry.invalid", "registry.test", "local.invalid"}
)
CLASSIFICATION_DOCUMENTATION = "documentation"
CLASSIFICATION_PRODUCTION = "production_release_runtime"
CLASSIFICATION_TEST_FIXTURE = "test_fixture"
TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".json",
        ".log",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
MAX_STATIC_STRING_LENGTH = 16_384
DOCKERFILE_EMPTY_BASE = "scratch"

_IMAGE_NAME_COMPONENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_IMAGE_REFERENCE = re.compile(
    rf"^(?:{_IMAGE_NAME_COMPONENT}(?::[0-9]+)?/)?"
    rf"{_IMAGE_NAME_COMPONENT}(?:/{_IMAGE_NAME_COMPONENT})*"
    rf"@sha256:[0-9a-f]{{64}}$"
)
_IMAGE_REFERENCE_BYTES = re.compile(
    rb"(?<![A-Za-z0-9._:/-])"
    rb"([a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64})"
    rb"(?![0-9a-f])"
)
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")
_YAML_IMAGE_DECLARATION = re.compile(
    rb"(?m)^[ \t]*(?:image|volume_keeper_image)[ \t]*:[ \t]*([^#\r\n]*)"
)
_DOCKERFILE_FROM = re.compile(
    rb"(?im)^[ \t]*FROM(?:[ \t]+--platform=[^ \t]+)?[ \t]+([^ \t\r\n]+)"
)


def _subprocess_runner(command: tuple[str, ...], cwd: Path) -> str:
    completed = subprocess.run(
        command,
        check=True,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
    )
    return completed.stdout


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_string_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings or null")
    return value


def _validate_captured_at_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("captured_at_utc must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("captured_at_utc must be an ISO-8601 UTC timestamp")
    return value


def capture_timestamp(value: str | None) -> str:
    if value is not None:
        return _validate_captured_at_utc(value)
    return datetime.now(timezone.utc).isoformat()


def _validate_relative_path(path: str) -> str:
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"git returned an unsafe tracked path: {path!r}")
    return candidate.as_posix()


def _tracked_paths(repo_root: Path, runner: CommandRunner) -> list[str]:
    output = runner(("git", "ls-files", "-z"), repo_root)
    paths = [_validate_relative_path(path) for path in output.split("\0") if path]
    if len(paths) != len(set(paths)):
        raise ValueError("git returned duplicate tracked paths")
    return sorted(paths)


def _untracked_paths(repo_root: Path, runner: CommandRunner) -> list[str]:
    output = runner(
        ("git", "ls-files", "-z", "--others", "--exclude-standard"),
        repo_root,
    )
    paths = [_validate_relative_path(path) for path in output.split("\0") if path]
    if len(paths) != len(set(paths)):
        raise ValueError("git returned duplicate untracked paths")
    return sorted(paths)


def _read_worktree_entry(
    repo_root: Path,
    relative_path: str,
    *,
    tracked: bool,
) -> tuple[dict[str, Any], bytes | None]:
    root = repo_root.resolve()
    path = root / PurePosixPath(relative_path)
    if not os.path.lexists(path):
        if not tracked:
            raise ValueError(f"untracked path disappeared during audit: {relative_path}")
        return (
            {
                "byte_size": 0,
                "classification": classify_source_path(relative_path),
                "entry_kind": "deleted",
                "git_state": "tracked",
                "path": relative_path,
                "sha256": None,
            },
            None,
        )
    if path.is_symlink():
        target = os.readlink(path)
        content = os.fsencode(target)
        entry_kind = "symlink"
        scan_content = None
    else:
        try:
            path.resolve(strict=True).relative_to(root)
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(f"worktree path escapes repository: {relative_path}") from exc
        if not path.is_file():
            raise ValueError(f"worktree path is not a regular file: {relative_path}")
        content = path.read_bytes()
        entry_kind = "file"
        scan_content = content
    return (
        {
            "byte_size": len(content),
            "classification": classify_source_path(relative_path),
            "entry_kind": entry_kind,
            "git_state": "tracked" if tracked else "untracked",
            "path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        scan_content,
    )


def _worktree_source_inventory(
    repo_root: Path,
    runner: CommandRunner,
) -> tuple[list[dict[str, Any]], list[tuple[str, bytes]]]:
    tracked_paths = _tracked_paths(repo_root, runner)
    untracked_paths = _untracked_paths(repo_root, runner)
    overlap = set(tracked_paths) & set(untracked_paths)
    if overlap:
        raise ValueError(f"git returned paths as tracked and untracked: {sorted(overlap)}")
    records: list[dict[str, Any]] = []
    files: list[tuple[str, bytes]] = []
    for tracked, paths in ((True, tracked_paths), (False, untracked_paths)):
        for relative_path in paths:
            record, content = _read_worktree_entry(
                repo_root,
                relative_path,
                tracked=tracked,
            )
            records.append(record)
            if content is not None:
                files.append((relative_path, content))
    records.sort(key=lambda record: record["path"])
    files.sort(key=lambda item: item[0])
    return records, files


def classify_source_path(relative_path: str) -> str:
    path = PurePosixPath(_validate_relative_path(relative_path))
    if path.suffix.lower() in {".md", ".rst"} or path.parts[0] == "docs":
        return CLASSIFICATION_DOCUMENTATION
    if path.parts[0] == "containers" and path.name == "selfcheck.py":
        return CLASSIFICATION_TEST_FIXTURE
    if (
        path.parts[0] == "tests"
        or path.name.startswith("test_")
        or path.parts[:2] == ("frontend", "e2e")
    ):
        return CLASSIFICATION_TEST_FIXTURE
    return CLASSIFICATION_PRODUCTION


def _static_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.BinOp):
        return None
    if isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        if left is None or right is None:
            return None
        value = left + right
        if len(value) > MAX_STATIC_STRING_LENGTH:
            return None
        return value
    if isinstance(node.op, ast.Mult):
        text = _static_string(node.left)
        count = node.right.value if isinstance(node.right, ast.Constant) else None
        if (
            not isinstance(text, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
        ):
            text = _static_string(node.right)
            count = node.left.value if isinstance(node.left, ast.Constant) else None
        if (
            not isinstance(text, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
        ):
            return None
        if count < 0 or len(text) * count > MAX_STATIC_STRING_LENGTH:
            return None
        return text * count
    return None


def is_immutable_image_reference(reference: str) -> bool:
    return _IMAGE_REFERENCE.fullmatch(reference) is not None


def _references_from_bytes(content: bytes, relative_path: str) -> set[str]:
    references = {
        match.group(1).decode("ascii")
        for match in _IMAGE_REFERENCE_BYTES.finditer(content)
        if is_immutable_image_reference(match.group(1).decode("ascii"))
    }
    if not relative_path.endswith(".py"):
        return references
    try:
        tree = ast.parse(content.decode("utf-8"), filename=relative_path)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"cannot parse tracked Python source: {relative_path}"
        ) from exc
    for node in ast.walk(tree):
        value = _static_string(node)
        if value is None:
            continue
        references.update(
            match.group(1).decode("ascii")
            for match in _IMAGE_REFERENCE_BYTES.finditer(value.encode("utf-8"))
            if is_immutable_image_reference(match.group(1).decode("ascii"))
        )
    return references


def _registry_and_repository(reference: str) -> tuple[str | None, str]:
    name = reference.split("@", maxsplit=1)[0]
    parts = name.split("/")
    if len(parts) > 1 and (
        "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
    ):
        return parts[0], "/".join(parts[1:])
    return None, name


def _is_placeholder_reference(reference: str) -> bool:
    registry, _ = _registry_and_repository(reference)
    digest = reference.rsplit(":", maxsplit=1)[1] if "@sha256:" in reference else ""
    return registry in PLACEHOLDER_REGISTRIES or (digest and len(set(digest)) == 1)


def validate_production_reference(reference: str) -> None:
    if not is_immutable_image_reference(reference):
        raise ValueError(
            "production image references must be immutable name@sha256:<64 hex>: "
            f"{reference}"
        )
    if _is_placeholder_reference(reference):
        raise ValueError(f"production image reference is a placeholder: {reference}")


def _is_scoped_repository(reference: str) -> bool:
    registry, repository = _registry_and_repository(reference)
    if registry in PLACEHOLDER_REGISTRIES:
        return False
    return repository.startswith("aero-bench/") or repository.startswith(
        "participants/inspection-"
    )


def _strip_yaml_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        return value[1:-1]
    return value


def _declared_production_images(files: Iterable[tuple[str, bytes]]) -> list[str]:
    images: list[str] = []
    for path, content in files:
        if (
            path.endswith((".yaml", ".yml"))
            and classify_source_path(path) == CLASSIFICATION_PRODUCTION
        ):
            for match in _YAML_IMAGE_DECLARATION.finditer(content):
                value = _strip_yaml_quotes(match.group(1).decode("utf-8").strip())
                images.append(value)
        if path.startswith("containers/") and PurePosixPath(path).name == "Dockerfile":
            images.extend(
                image
                for image in (
                    match.group(1).decode("utf-8")
                    for match in _DOCKERFILE_FROM.finditer(content)
                )
                if image != DOCKERFILE_EMPTY_BASE
            )
    return images


def _collect_reference_records(
    files: Iterable[tuple[str, bytes]], *, validate_production: bool = True
) -> list[dict[str, Any]]:
    materialized_files = list(files)
    if validate_production:
        for reference in _declared_production_images(materialized_files):
            validate_production_reference(reference)
    grouped: dict[tuple[str, str], set[str]] = {}
    for path, content in materialized_files:
        classification = classify_source_path(path)
        for reference in _references_from_bytes(content, path):
            if validate_production and classification == CLASSIFICATION_PRODUCTION:
                validate_production_reference(reference)
            grouped.setdefault((classification, reference), set()).add(path)
    return [
        {
            "classification": classification,
            "reference": reference,
            "source_paths": sorted(paths),
        }
        for (classification, reference), paths in sorted(grouped.items())
    ]


def collect_worktree_references(
    repo_root: Path, runner: CommandRunner = _subprocess_runner
) -> list[dict[str, Any]]:
    _, files = _worktree_source_inventory(repo_root, runner)
    return _collect_reference_records(files)


def _git_source_state(
    repo_root: Path,
    runner: CommandRunner,
    *,
    source_files: list[dict[str, Any]],
    require_clean: bool,
) -> dict[str, Any]:
    head = runner(("git", "rev-parse", "HEAD"), repo_root).strip()
    if _GIT_HEAD.fullmatch(head) is None:
        raise ValueError("git returned an invalid HEAD")
    branch = runner(("git", "branch", "--show-current"), repo_root).strip()
    status = runner(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"), repo_root
    )
    worktree_clean = status == ""
    if require_clean and not worktree_clean:
        raise ValueError("new-release provenance requires a clean worktree")
    return {
        "branch": branch or None,
        "head": head,
        "source_file_count": len(source_files),
        "source_tree_digest": hashlib.sha256(
            canonical_json_bytes(source_files)
        ).hexdigest(),
        "worktree_clean": worktree_clean,
    }


def _json_lines(output: str, command: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{command} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{command} returned a non-object JSON row")
        entries.append(value)
    return entries


def _sanitized_labels(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Docker image labels must be an object or null")
    labels: dict[str, str] = {}
    for name in ALLOWED_LABELS:
        label = value.get(name)
        if label is None:
            continue
        if not isinstance(label, str):
            raise ValueError(f"Docker image label {name} must be a string")
        labels[name] = label
    return labels


def _sanitize_image_fields(
    *,
    image_id: object,
    repo_tags: object,
    repo_digests: object,
    created: object,
    os_name: object,
    architecture: object,
    labels: object,
) -> dict[str, Any]:
    image_id_value = _require_string(image_id, "Docker image Id")
    if _IMAGE_ID.fullmatch(image_id_value) is None:
        raise ValueError(f"Docker image Id is invalid: {image_id_value}")
    scoped_tags = sorted(
        set(
            tag
            for tag in _require_string_list(repo_tags, "Docker RepoTags")
            if _is_scoped_repository(tag)
        )
    )
    scoped_digests = sorted(
        set(
            digest
            for digest in _require_string_list(repo_digests, "Docker RepoDigests")
            if _is_scoped_repository(digest)
        )
    )
    if not scoped_tags and not scoped_digests:
        raise ValueError(
            f"Docker image is outside the AERO-BENCH scope: {image_id_value}"
        )
    return {
        "architecture": _require_string(architecture, "Docker image Architecture"),
        "created": _require_string(created, "Docker image Created"),
        "image_id": image_id_value,
        "os": _require_string(os_name, "Docker image Os"),
        "provenance_labels": _sanitized_labels(labels),
        "repo_digests": scoped_digests,
        "repo_tags": scoped_tags,
    }


def _sanitize_inspected_image(inspected: dict[str, Any]) -> dict[str, Any]:
    config = inspected.get("Config")
    if config is None:
        labels = None
    elif isinstance(config, dict):
        labels = config.get("Labels")
    else:
        raise ValueError("Docker image Config must be an object or null")
    return _sanitize_image_fields(
        image_id=inspected.get("Id"),
        repo_tags=inspected.get("RepoTags"),
        repo_digests=inspected.get("RepoDigests"),
        created=inspected.get("Created"),
        os_name=inspected.get("Os"),
        architecture=inspected.get("Architecture"),
        labels=labels,
    )


def _inspected_image_is_scoped(inspected: dict[str, Any]) -> bool:
    references = _require_string_list(
        inspected.get("RepoTags"), "Docker RepoTags"
    ) + _require_string_list(inspected.get("RepoDigests"), "Docker RepoDigests")
    return any(_is_scoped_repository(reference) for reference in references)


def _listing_image_ids(rows: Iterable[dict[str, Any]]) -> set[str]:
    image_ids: set[str] = set()
    for row in rows:
        image_id = row.get("ID")
        if not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None:
            raise ValueError("docker image ls returned an invalid image ID")
        image_ids.add(image_id)
    return image_ids


def _listing_scope_ids(rows: Iterable[dict[str, Any]]) -> set[str]:
    scoped_ids: set[str] = set()
    for row in rows:
        image_id = row.get("ID")
        if not isinstance(image_id, str) or _IMAGE_ID.fullmatch(image_id) is None:
            raise ValueError("docker image ls returned an invalid image ID")
        repository = row.get("Repository")
        if not isinstance(repository, str):
            raise ValueError("docker image ls returned an invalid repository")
        if repository != "<none>" and _is_scoped_repository(repository):
            scoped_ids.add(image_id)
    return scoped_ids


def inventory_local_images(
    repo_root: Path,
    docker_binary: str = "docker",
    runner: CommandRunner = _subprocess_runner,
) -> list[dict[str, Any]]:
    listing = _json_lines(
        runner(
            (
                docker_binary,
                "image",
                "ls",
                "--all",
                "--no-trunc",
                "--format",
                "{{json .}}",
            ),
            repo_root,
        ),
        "docker image ls",
    )
    image_ids = sorted(_listing_image_ids(listing))
    if not image_ids:
        return []
    inspected_value = json.loads(
        runner((docker_binary, "image", "inspect", *image_ids), repo_root)
    )
    if not isinstance(inspected_value, list) or any(
        not isinstance(item, dict) for item in inspected_value
    ):
        raise ValueError("docker image inspect returned invalid JSON")
    inspected_ids = {item.get("Id") for item in inspected_value}
    if inspected_ids != set(image_ids) or len(inspected_ids) != len(inspected_value):
        raise ValueError("docker image ls and inspect returned mismatched image IDs")
    images: list[dict[str, Any]] = []
    for inspected in inspected_value:
        if _inspected_image_is_scoped(inspected):
            images.append(_sanitize_inspected_image(inspected))
    images.sort(key=lambda item: item["image_id"])
    if len({item["image_id"] for item in images}) != len(images):
        raise ValueError("docker image inspect returned duplicate scoped image IDs")
    listed_scope_ids = _listing_scope_ids(listing)
    captured_scope_ids = {item["image_id"] for item in images}
    if not listed_scope_ids.issubset(captured_scope_ids):
        raise ValueError("docker image listing and inspection scope mismatch")
    return images


def build_audit(
    *,
    repo_root: Path,
    captured_at_utc: str,
    docker_binary: str = "docker",
    runner: CommandRunner = _subprocess_runner,
    require_clean: bool = False,
) -> dict[str, Any]:
    source_files, worktree_files = _worktree_source_inventory(repo_root, runner)
    return {
        "captured_at_utc": _validate_captured_at_utc(captured_at_utc),
        "local_images": inventory_local_images(repo_root, docker_binary, runner),
        "schema_version": AUDIT_SCHEMA_VERSION,
        "source_files": source_files,
        "source_state": _git_source_state(
            repo_root,
            runner,
            source_files=source_files,
            require_clean=require_clean,
        ),
        "worktree_immutable_references": _collect_reference_records(worktree_files),
    }


def _is_historical_text_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.suffix.lower() in TEXT_SUFFIXES or candidate.name == "Dockerfile"


def _historical_reference_records(
    repo_root: Path,
    revision: str,
    runner: CommandRunner,
    *,
    validate_production: bool = False,
) -> list[dict[str, Any]]:
    output = runner(("git", "ls-tree", "-r", "-z", "--name-only", revision), repo_root)
    paths = [
        _validate_relative_path(path)
        for path in output.split("\0")
        if path and _is_historical_text_path(path)
    ]
    files = [
        (
            path,
            runner(("git", "show", f"{revision}:{path}"), repo_root).encode("utf-8"),
        )
        for path in sorted(paths)
    ]
    return _collect_reference_records(files, validate_production=validate_production)


def _sanitize_raw_image(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("raw capture local_aero_bench_images entries must be objects")
    expected = {
        "architecture",
        "created",
        "image_id",
        "labels",
        "os",
        "repo_digests",
        "repo_tags",
    }
    if set(raw) != expected:
        raise ValueError("raw capture image record has unexpected fields")
    return _sanitize_image_fields(
        image_id=raw["image_id"],
        repo_tags=raw["repo_tags"],
        repo_digests=raw["repo_digests"],
        created=raw["created"],
        os_name=raw["os"],
        architecture=raw["architecture"],
        labels=raw["labels"],
    )


def _raw_reference_record(
    raw: object, locations: list[dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(
            "raw capture referenced_runtime_images entries must be objects"
        )
    if not {"reference", "status"}.issubset(raw) or not set(raw).issubset(
        {"image_id", "reference", "revision", "status"}
    ):
        raise ValueError("raw capture reference record has unexpected fields")
    reference = _require_string(raw["reference"], "raw capture reference")
    if not is_immutable_image_reference(reference):
        raise ValueError(f"raw capture reference is not immutable: {reference}")
    status = raw["status"]
    if status not in {"missing", "present"}:
        raise ValueError(f"raw capture reference has invalid status: {reference}")
    if not locations:
        raise ValueError(
            f"raw capture reference is absent from its captured source: {reference}"
        )
    result: dict[str, Any] = {
        "reference": reference,
        "source_locations": locations,
        "status": status,
    }
    for name in ("image_id", "revision"):
        value = raw.get(name)
        if value is not None:
            result[name] = _require_string(value, f"raw capture {name}")
    if status == "present" and "image_id" not in result:
        raise ValueError(f"present raw capture reference has no image ID: {reference}")
    return result


def _validate_raw_presence(
    references: Iterable[dict[str, Any]], images: Iterable[dict[str, Any]]
) -> None:
    by_id = {image["image_id"]: image for image in images}
    for reference in references:
        if reference["status"] != "present":
            continue
        image = by_id.get(reference["image_id"])
        if image is None or reference["reference"] not in image["repo_digests"]:
            raise ValueError(
                "raw capture present reference does not match its captured local image: "
                f"{reference['reference']}"
            )
        revision = reference.get("revision")
        if (
            revision is not None
            and image["provenance_labels"].get("org.opencontainers.image.revision")
            != revision
        ):
            raise ValueError(
                "raw capture revision does not match its captured local image: "
                f"{reference['reference']}"
            )


def build_prechange_baseline(
    *,
    raw_capture_path: Path,
    repo_root: Path,
    runner: CommandRunner = _subprocess_runner,
) -> dict[str, Any]:
    raw_bytes = raw_capture_path.read_bytes()
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError("raw capture is not valid JSON") from exc
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != RAW_CAPTURE_SCHEMA_VERSION
    ):
        raise ValueError("raw capture schema version is not supported")
    captured_at_utc = _validate_captured_at_utc(
        _require_string(raw.get("captured_at_utc"), "raw capture captured_at_utc")
    )
    branch = raw.get("branch")
    if branch is not None and (not isinstance(branch, str) or not branch):
        raise ValueError("raw capture branch must be a string or null")
    source_state = {
        "branch": branch,
        "head": _require_string(raw.get("head"), "raw capture head"),
        "repository": _require_string(raw.get("repository"), "raw capture repository"),
        "worktree_clean": raw.get("worktree_clean"),
    }
    if _GIT_HEAD.fullmatch(source_state["head"]) is None:
        raise ValueError("raw capture head is invalid")
    if not isinstance(source_state["worktree_clean"], bool):
        raise ValueError("raw capture worktree_clean must be a boolean")
    historical_references = _historical_reference_records(
        repo_root,
        source_state["head"],
        runner,
        validate_production=False,
    )
    locations_by_reference: dict[str, list[dict[str, Any]]] = {}
    for record in historical_references:
        locations_by_reference.setdefault(record["reference"], []).append(
            {
                "classification": record["classification"],
                "source_paths": record["source_paths"],
            }
        )
    raw_images = raw.get("local_aero_bench_images")
    raw_references = raw.get("referenced_runtime_images")
    if not isinstance(raw_images, list) or not isinstance(raw_references, list):
        raise ValueError("raw capture inventory fields must be lists")
    images = sorted(
        (_sanitize_raw_image(image) for image in raw_images),
        key=lambda item: item["image_id"],
    )
    if len({image["image_id"] for image in images}) != len(images):
        raise ValueError("raw capture has duplicate local image IDs")
    references = sorted(
        (
            _raw_reference_record(
                reference,
                locations_by_reference.get(
                    _require_string(
                        reference.get("reference")
                        if isinstance(reference, dict)
                        else None,
                        "raw capture reference",
                    ),
                    [],
                ),
            )
            for reference in raw_references
        ),
        key=lambda item: item["reference"],
    )
    _validate_raw_presence(references, images)
    return {
        "baseline": {
            "kind": "point_in_time_pre_change_provenance_inventory",
            "new_release_validation": False,
            "raw_capture_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
        "captured_at_utc": captured_at_utc,
        "local_images": images,
        "referenced_runtime_images": references,
        "schema_version": BASELINE_SCHEMA_VERSION,
        "source_state": source_state,
    }


def write_or_check(payload: dict[str, Any], output_path: Path, check: bool) -> None:
    expected = canonical_json_bytes(payload) + b"\n"
    if check:
        if not output_path.is_file():
            raise ValueError(f"provenance output does not exist: {output_path}")
        if output_path.read_bytes() != expected:
            raise ValueError(f"provenance output does not match: {output_path}")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(expected)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Capture a sanitized AERO-BENCH image provenance audit."
    )
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--check", action="store_true")
    result.add_argument("--captured-at-utc")
    result.add_argument("--docker-binary", default="docker")
    result.add_argument("--require-clean", action="store_true")
    result.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    result.add_argument("--baseline-from-raw-capture", type=Path)
    return result


def main() -> None:
    args = parser().parse_args()
    repo_root = args.repo_root.resolve()
    if args.baseline_from_raw_capture is not None:
        if args.captured_at_utc is not None or args.require_clean:
            raise ValueError(
                "raw-capture baseline fixes its timestamp and cannot apply --require-clean"
            )
        payload = build_prechange_baseline(
            raw_capture_path=args.baseline_from_raw_capture.resolve(),
            repo_root=repo_root,
        )
    else:
        if args.check and args.captured_at_utc is None:
            raise ValueError(
                "--check requires --captured-at-utc for an exact comparison"
            )
        payload = build_audit(
            repo_root=repo_root,
            captured_at_utc=capture_timestamp(args.captured_at_utc),
            docker_binary=args.docker_binary,
            require_clean=args.require_clean,
        )
    output_path = args.output.resolve()
    write_or_check(payload, output_path, args.check)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "status": "checked" if args.check else "written",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
