from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


_GEMINI_ALLOWED_FILES = frozenset(
    {
        "dialogues/main.jsonl",
        "dialogues/frontend_internal.jsonl",
        "dialogues/backend_internal.jsonl",
        "dialogues/review_internal.jsonl",
        "sessions.json",
    }
)
_REDACTED_VALUES = frozenset(
    {
        "redacted",
        "<redacted>",
        "[redacted]",
    }
)
_CREDENTIAL_KEY_TOKENS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
_KEY_PAIR_TOKENS = frozenset(
    {
        "access",
        "api",
        "auth",
        "client",
        "consumer",
        "encryption",
        "master",
        "oauth",
        "private",
        "refresh",
        "session",
        "signing",
        "ssh",
        "ssl",
        "tls",
    }
)
_PROVIDER_TOKENS = frozenset(
    {
        "alibaba",
        "aliyun",
        "amazon",
        "anthropic",
        "aws",
        "azure",
        "cloud",
        "cohere",
        "datadog",
        "gemini",
        "github",
        "gitlab",
        "google",
        "gcp",
        "hf",
        "huggingface",
        "openai",
        "oracle",
        "oci",
        "provider",
        "sentry",
        "slack",
        "stripe",
        "twilio",
    }
)
_COMPACT_CREDENTIAL_KEYS = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "authtoken",
        "clientsecret",
        "clienttoken",
        "privatekey",
        "refreshtoken",
        "secretkey",
        "sessiontoken",
    }
)
_CREDENTIAL_KEY_SUFFIXES = (
    "accesskey",
    "accesskeyid",
    "accesstoken",
    "apikey",
    "authtoken",
    "clientsecret",
    "clienttoken",
    "privatekey",
    "refreshtoken",
    "secretkey",
    "secretaccesskey",
    "sessiontoken",
)
_OBSOLETE_HANDOFF_CLAIM = (
    "两张 `frontend/design/*.png` 是 built-in image generation 生成的设计参考，"
    "不是 trace、截图或 evidence。"
)
_JSON_KEY_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[^A-Za-z0-9]+")
_CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"(?ix)"
    r"(?<![A-Za-z0-9_-])(?P<key_quote>[\"']?)"
    r"(?P<key>[A-Za-z][A-Za-z0-9_-]*(?:[ \t]+[A-Za-z][A-Za-z0-9_-]*)*)"
    r"(?P=key_quote)\s*[:=]\s*"
    r"(?P<value_quote>[\"']?)"
    r"(?!<redacted>|\[redacted\]|redacted\b)"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._~+/=-]{7,})"
    r"(?P=value_quote)"
)
_REFERENCE_VALUE_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}\b"),
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
        r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?i)[\"']?\b(?:api[_-]?key|authorization|x-api-key)\b[\"']?\s*[:=]\s*"
        r"[\"']?(?!<redacted>|\[redacted\]|redacted\b)"
        r"[A-Za-z0-9._:+/-]{16,}"
    ),
    re.compile(r"(?i)\bbearer\s+(?!<redacted>|\[redacted\])" r"[A-Za-z0-9._~+/-]{16,}"),
    re.compile(
        r"(?i)[\"']?\bbase[-_ ]?url\b[\"']?\s*[:=]\s*"
        r"[\"']?(?!<redacted>|\[redacted\]|redacted\b)[^\"'\s,}\]]+"
    ),
)


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _json_key_tokens(key: str) -> frozenset[str]:
    return frozenset(
        token.casefold()
        for part in _JSON_KEY_SPLIT.split(key)
        for token in (part,)
        if token
    )


def _is_credential_key(key: str) -> bool:
    tokens = _json_key_tokens(key)
    compact = re.sub(r"[^a-z0-9]", "", key.casefold())
    if (
        compact in _COMPACT_CREDENTIAL_KEYS
        or "privatekey" in compact
        or any(compact.endswith(suffix) for suffix in _CREDENTIAL_KEY_SUFFIXES)
    ):
        return True
    if len(tokens) == 1 and tokens & _CREDENTIAL_KEY_TOKENS:
        return True
    if "private" in tokens and "key" in tokens:
        return True
    if "key" in tokens and tokens & (_KEY_PAIR_TOKENS | {"secret"}):
        return True
    if tokens & _PROVIDER_TOKENS and tokens & (
        _CREDENTIAL_KEY_TOKENS | {"access", "key"}
    ):
        return True
    if tokens & _CREDENTIAL_KEY_TOKENS and tokens & _KEY_PAIR_TOKENS:
        return True
    return False


def _is_redacted_value(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().casefold() in _REDACTED_VALUES


def _is_metadata_key(key: str) -> bool:
    tokens = _json_key_tokens(key)
    compact = re.sub(r"[^a-z0-9]", "", key.casefold())
    if tokens & {"session", "trace"} and "id" in tokens:
        return True
    return compact in {
        "tokenbudget",
        "tokencount",
        "tokenindex",
        "tokenlimit",
        "tokenusage",
        "tokentype",
    }


def _looks_like_credential_value(value: str, *, quoted: bool) -> bool:
    value = value.strip()
    if len(value) < 16 or _is_redacted_value(value):
        return False
    if _REFERENCE_VALUE_PATTERN.fullmatch(value):
        return False
    if not quoted and re.fullmatch(r"\w+_\w+", value):
        return False
    return True


def _validate_json_credentials(value: object, relative: Path, location: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                _is_credential_key(key)
                and not _is_redacted_value(child)
                and not _is_metadata_key(key)
            ):
                raise ValueError(
                    "Gemini history contains an unredacted credential field "
                    f"{key!r} at {relative}{location}"
                )
            _validate_json_credentials(child, relative, location)
    elif isinstance(value, list):
        for child in value:
            _validate_json_credentials(child, relative, location)


def _scan_secret_patterns(
    text: str,
    relative: Path,
    *,
    context: str,
    include_sensitive_endpoints: bool = True,
) -> None:
    patterns = (
        _SECRET_PATTERNS if include_sensitive_endpoints else _SECRET_PATTERNS[:-1]
    )
    if any(pattern.search(text) for pattern in patterns):
        raise ValueError(f"{context} contains an unredacted credential: {relative}")
    # A quoted value is consumed whole by an outer match, so each captured
    # value must itself be rescanned for assignments nested inside it.
    pending = [text]
    while pending:
        for match in _CREDENTIAL_ASSIGNMENT_PATTERN.finditer(pending.pop()):
            key = match.group("key")
            value = match.group("value")
            if (
                _is_credential_key(key)
                and not _is_metadata_key(key)
                and _looks_like_credential_value(
                    value, quoted=bool(match.group("value_quote"))
                )
            ):
                raise ValueError(
                    f"{context} contains an unredacted credential: {relative}"
                )
            pending.append(value)


def _parse_json(
    text: str, relative: Path, location: str, *, label: str = "JSON"
) -> object:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (json.JSONDecodeError, _DuplicateJsonKey) as error:
        raise ValueError(
            f"Gemini {label} is invalid at {relative}{location}"
        ) from error


def _safe_relative(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe archive path: {name}")
    return Path(*path.parts)


def _extract_regular_files(archive: tarfile.TarFile, destination: Path) -> None:
    for member in archive.getmembers():
        relative = _safe_relative(member.name)
        target = destination / relative
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise ValueError(f"unsupported git archive entry: {member.name}")
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"cannot read git archive entry: {member.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            shutil.copyfileobj(source, stream)
        target.chmod(member.mode & 0o777)


def _validate_redacted_gemini(relative: Path, payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Gemini history is not UTF-8: {relative}") from error
    _scan_secret_patterns(text, relative, context="Gemini history")
    if relative.suffix == ".jsonl":
        for line_number, line in enumerate(text.splitlines(), start=1):
            location = f":{line_number}"
            value = _parse_json(line, relative, location, label="JSONL")
            _validate_json_credentials(value, relative, location)
    elif relative.name == "sessions.json":
        value = _parse_json(text, relative, "", label="sessions.json")
        _validate_json_credentials(value, relative, "")


def _validate_factual_consistency(root: Path) -> None:
    handoff = root / "HANDOFF.md"
    if not handoff.is_file():
        return
    try:
        text = handoff.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("HANDOFF.md is not UTF-8") from error
    if _OBSOLETE_HANDOFF_CLAIM in text:
        raise ValueError(
            "HANDOFF.md contains the obsolete design-image generation claim"
        )


def _scan_tracked_text(root: Path) -> None:
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(root)
        _scan_secret_patterns(
            text,
            relative,
            context="tracked source",
            include_sensitive_endpoints=False,
        )
        suffix = relative.suffix.casefold()
        if suffix == ".json":
            try:
                value = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
            except (json.JSONDecodeError, _DuplicateJsonKey):
                continue
            _validate_json_credentials(value, relative, "")
        elif suffix == ".jsonl":
            for line in text.splitlines():
                try:
                    value = json.loads(
                        line, object_pairs_hook=_reject_duplicate_json_keys
                    )
                except (json.JSONDecodeError, _DuplicateJsonKey):
                    continue
                _validate_json_credentials(value, relative, "")


def _copy_gemini_history(source_archive: Path, destination: Path) -> None:
    with tarfile.open(source_archive, "r:gz") as archive:
        copied = 0
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts
            if "gemini" not in parts:
                continue
            index = parts.index("gemini")
            if len(parts) == index + 1:
                continue
            relative = _safe_relative("/".join(parts[index + 1 :]))
            target = destination / relative
            relative_name = relative.as_posix()
            if relative_name not in _GEMINI_ALLOWED_FILES:
                continue
            if not member.isfile():
                raise ValueError(f"unsupported Gemini archive entry: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read Gemini archive entry: {member.name}")
            payload = source.read()
            _validate_redacted_gemini(relative, payload)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(payload)
            copied += 1
    if copied == 0:
        raise ValueError("source archive contains no allowlisted Gemini files")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(root: Path) -> None:
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "MANIFEST.sha256"
    )
    lines = [f"{_sha256(path)}  {path.relative_to(root).as_posix()}" for path in files]
    (root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _deterministic_tar_info(path: Path, root: Path) -> tarfile.TarInfo:
    relative = path.relative_to(root).as_posix()
    name = root.name if relative == "." else f"{root.name}/{relative}"
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
    elif path.is_file():
        info.type = tarfile.REGTYPE
        info.mode = 0o644
        info.size = path.stat().st_size
    else:
        raise ValueError(f"unsupported package entry: {path}")
    return info


def _write_deterministic_archive(root: Path, output: Path) -> None:
    entries = [
        root,
        *sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()),
    ]
    with output.open("wb") as raw:
        with gzip.GzipFile(
            fileobj=raw,
            filename="",
            mode="wb",
            compresslevel=9,
            mtime=0,
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
            ) as archive:
                for path in entries:
                    info = _deterministic_tar_info(path, root)
                    if info.isfile():
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)


def build_archive(
    *,
    source_archive: Path,
    architecture_kit: Path,
    output: Path,
    max_bytes: int,
) -> None:
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    status = subprocess.run(
        ("git", "status", "--porcelain"),
        check=True,
        text=True,
        capture_output=True,
    )
    if status.stdout:
        raise RuntimeError("refusing to package a dirty worktree")
    commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    git_archive = subprocess.run(
        ("git", "archive", "--format=tar", "HEAD"),
        check=True,
        capture_output=True,
    ).stdout

    output.parent.mkdir(parents=True, exist_ok=True)
    package_name = output.name.removesuffix(".tar.gz")
    with tempfile.TemporaryDirectory(prefix="aero-final-handoff-") as temporary:
        root = Path(temporary) / package_name
        root.mkdir()
        with tarfile.open(fileobj=io.BytesIO(git_archive), mode="r:") as archive:
            _extract_regular_files(archive, root)
        _validate_factual_consistency(root)
        _scan_tracked_text(root)
        _copy_gemini_history(source_archive, root / "gemini_history")
        reference_root = root / "architecture_reference"
        reference_root.mkdir()
        architecture_copy = reference_root / architecture_kit.name
        shutil.copyfile(architecture_kit, architecture_copy)
        (root / "PACKAGE_INFO.txt").write_text(
            "AERO-BENCH final handoff\n"
            f"git_commit={commit}\n"
            f"source_handoff_sha256={_sha256(source_archive)}\n"
            f"architecture_kit_sha256={_sha256(architecture_kit)}\n"
            "gemini_history=redacted untrusted historical material\n"
            "architecture_reference=untrusted design baseline, not production code\n"
            "See HANDOFF.md, docs/VALIDATION.md, and docs/MODEL_CALLS.md.\n",
            encoding="utf-8",
        )
        _write_manifest(root)
        _write_deterministic_archive(root, output)

    actual_size = output.stat().st_size
    if actual_size > max_bytes:
        output.unlink()
        raise ValueError(
            f"archive exceeded size limit: {actual_size} bytes > {max_bytes} bytes"
        )
    output.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package clean tracked code plus redacted Gemini history."
    )
    parser.add_argument("--source-archive", required=True, type=Path)
    parser.add_argument("--architecture-kit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-bytes", required=True, type=int)
    arguments = parser.parse_args()
    build_archive(
        source_archive=arguments.source_archive.resolve(strict=True),
        architecture_kit=arguments.architecture_kit.resolve(strict=True),
        output=arguments.output.resolve(strict=False),
        max_bytes=arguments.max_bytes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
