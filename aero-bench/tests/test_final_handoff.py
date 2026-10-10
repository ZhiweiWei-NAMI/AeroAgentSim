from __future__ import annotations

import io
import hashlib
import json
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Callable

import pytest

from tools import build_final_handoff

_SECRET_VALUE = "qT8rN2xV" + "7mK4pL9sH3dF6jC8wB0y"


def _write_tar(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))


def test_copy_gemini_history_uses_the_explicit_allowlist(tmp_path: Path) -> None:
    source = tmp_path / "source.tar.gz"
    _write_tar(
        source,
        {
            "handoff/gemini/dialogues/main.jsonl": b'{"role":"user"}\n',
            "handoff/gemini/dialogues/frontend_internal.jsonl": b'{"role":"model"}\n',
            "handoff/gemini/sessions.json": b'{"main":"session-id"}',
            "handoff/gemini/dialogues/unrelated.jsonl": b'{"secret":"not copied"}\n',
            "handoff/gemini/artifacts/old.md": b"not copied",
        },
    )

    destination = tmp_path / "gemini_history"
    build_final_handoff._copy_gemini_history(source, destination)

    assert (destination / "dialogues/main.jsonl").is_file()
    assert (destination / "dialogues/frontend_internal.jsonl").is_file()
    assert (destination / "sessions.json").is_file()
    assert not (destination / "dialogues/unrelated.jsonl").exists()
    assert not (destination / "artifacts/old.md").exists()


@pytest.mark.parametrize(
    "payload",
    (
        lambda: '{"api_key":"' + "x" * 20 + '"}',
        lambda: "Authorization: " + "x" * 20,
        lambda: "Bearer " + "x" * 20,
        lambda: "base-url: https://relay.invalid.example/v1",
        lambda: "sk-" + "x" * 20,
    ),
)
def test_copy_gemini_history_rejects_credentials(
    tmp_path: Path, payload: Callable[[], str]
) -> None:
    source = tmp_path / "source.tar.gz"
    _write_tar(
        source,
        {"handoff/gemini/dialogues/main.jsonl": (payload() + "\n").encode()},
    )

    with pytest.raises(ValueError, match="unredacted credential"):
        build_final_handoff._copy_gemini_history(source, tmp_path / "output")


@pytest.mark.parametrize(
    "assignment",
    (
        lambda: "AWS_SECRET_ACCESS_KEY=" + _SECRET_VALUE,
        lambda: "AWS_SECRET_ACCESS_KEY='" + _SECRET_VALUE + "'",
        lambda: "api_key: " + _SECRET_VALUE,
        lambda: "password = " + _SECRET_VALUE,
    ),
)
def test_copy_gemini_history_rejects_credential_assignments_regardless_of_quoting(
    tmp_path: Path, assignment: Callable[[], str]
) -> None:
    source = tmp_path / "source.tar.gz"
    line = '{"text":"' + assignment() + '"}\n'
    _write_tar(source, {"handoff/gemini/dialogues/main.jsonl": line.encode()})

    with pytest.raises(ValueError, match="unredacted credential"):
        build_final_handoff._copy_gemini_history(source, tmp_path / "output")


def test_copy_gemini_history_accepts_redacted_credentials(tmp_path: Path) -> None:
    source = tmp_path / "source.tar.gz"
    payload = {
        "api_key": "<redacted>",
        "authorization": "[redacted]",
        "base_url": "redacted",
        "note": "Bearer <redacted>",
    }
    _write_tar(
        source,
        {"handoff/gemini/dialogues/main.jsonl": (json.dumps(payload) + "\n").encode()},
    )

    destination = tmp_path / "output"
    build_final_handoff._copy_gemini_history(source, destination)
    assert (destination / "dialogues/main.jsonl").read_text() == (
        json.dumps(payload) + "\n"
    )


@pytest.mark.parametrize(
    "key",
    (
        "aws_secret_access_key",
        "aws_access_key_id",
        "cloud_api_key",
        "provider_token",
        "access_token",
        "password",
        "cookie",
        "private_key",
    ),
)
def test_copy_gemini_history_rejects_credential_like_json_keys(
    tmp_path: Path, key: str
) -> None:
    source = tmp_path / "source.tar.gz"
    payload = json.dumps({key: "not-redacted"}) + "\n"
    _write_tar(
        source,
        {"handoff/gemini/dialogues/main.jsonl": payload.encode()},
    )

    with pytest.raises(ValueError, match="unredacted credential"):
        build_final_handoff._copy_gemini_history(source, tmp_path / "output")


def test_copy_gemini_history_allows_business_metadata_and_redacted_nested_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.tar.gz"
    payload = {
        "provider": "gemini",
        "cloud": "internal-research",
        "session_id": "session-001",
        "trace_id": "trace-001",
        "token_count": 32,
        "token_budget": 128,
        "metadata": {"aws_secret_access_key": "<redacted>"},
        "key": "business-record-id",
    }
    encoded = (json.dumps(payload) + "\n").encode()
    _write_tar(source, {"handoff/gemini/dialogues/main.jsonl": encoded})

    destination = tmp_path / "output"
    build_final_handoff._copy_gemini_history(source, destination)
    assert (destination / "dialogues/main.jsonl").read_bytes() == encoded


def test_copy_gemini_history_allows_unquoted_business_and_metadata_assignments(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.tar.gz"
    text = (
        "session_id=session-001 trace_id=trace-001 "
        "token_count=1234567890 token_budget=81920000 "
        "run_id=aero-bench-run-0001 "
        "password=settings.auth.password"
    )
    line = json.dumps({"text": text}) + "\n"
    _write_tar(source, {"handoff/gemini/dialogues/main.jsonl": line.encode()})

    destination = tmp_path / "output"
    build_final_handoff._copy_gemini_history(source, destination)
    assert (destination / "dialogues/main.jsonl").read_text() == line


def test_copy_gemini_history_rejects_invalid_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "source.tar.gz"
    _write_tar(
        source,
        {"handoff/gemini/dialogues/main.jsonl": b"not-json\n"},
    )

    with pytest.raises(ValueError, match="JSONL is invalid"):
        build_final_handoff._copy_gemini_history(source, tmp_path / "output")


def test_handoff_guard_rejects_obsolete_generation_claim(tmp_path: Path) -> None:
    (tmp_path / "HANDOFF.md").write_text(
        "两张 `frontend/design/*.png` 是 built-in image generation 生成的设计参考，"
        "不是 trace、截图或 evidence。\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="obsolete design-image generation claim"):
        build_final_handoff._validate_factual_consistency(tmp_path)


def test_handoff_guard_accepts_non_attribution_wording(tmp_path: Path) -> None:
    (tmp_path / "HANDOFF.md").write_text(
        "两张 `frontend/design/*.png` 是设计参考。由于没有保留可复现的 "
        "built-in image-generation 调用，本交接不对其生成来源作出模型调用归属声明。\n",
        encoding="utf-8",
    )

    build_final_handoff._validate_factual_consistency(tmp_path)


def test_archive_mode_is_private_and_contains_input_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.tar.gz"
    architecture = tmp_path / "architecture.zip"
    output = tmp_path / "handoff.tar.gz"
    _write_tar(
        source,
        {"handoff/gemini/dialogues/main.jsonl": b'{"ok":true}\n'},
    )
    architecture.write_bytes(b"architecture-kit")

    git_archive = io.BytesIO()
    with tarfile.open(fileobj=git_archive, mode="w:") as archive:
        payload = b"tracked source\n"
        member = tarfile.TarInfo("README.md")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    def fake_run(command: tuple[str, ...], **kwargs: object) -> object:
        class Result:
            stdout: str | bytes

        result = Result()
        if command[:2] == ("git", "status"):
            result.stdout = ""
        elif command[:3] == ("git", "rev-parse", "HEAD"):
            result.stdout = "deadbeef\n"
        else:
            result.stdout = git_archive.getvalue()
        return result

    monkeypatch.setattr(build_final_handoff.subprocess, "run", fake_run)
    build_final_handoff.build_archive(
        source_archive=source,
        architecture_kit=architecture,
        output=output,
        max_bytes=10 * 1024 * 1024,
    )

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
        package_name = output.name.removesuffix(".tar.gz")
        assert f"{package_name}/README.md" in names
        assert f"{package_name}/architecture_reference/{architecture.name}" in names
        assert f"{package_name}/gemini_history/dialogues/main.jsonl" in names
        info = archive.extractfile(f"{package_name}/PACKAGE_INFO.txt")
        assert info is not None
        text = info.read().decode()
        assert "git_commit=deadbeef" in text
        assert build_final_handoff._sha256(source) in text
        assert build_final_handoff._sha256(architecture) in text


def test_archive_rejects_secret_in_tracked_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.tar.gz"
    architecture = tmp_path / "architecture.zip"
    output = tmp_path / "handoff.tar.gz"
    _write_tar(
        source,
        {"handoff/gemini/dialogues/main.jsonl": b'{"ok":true}\n'},
    )
    architecture.write_bytes(b"architecture-kit")

    git_archive = io.BytesIO()
    with tarfile.open(fileobj=git_archive, mode="w:") as archive:
        payload = b'aws_secret_access_key = "' + (b"x" * 40) + b'"\n'
        member = tarfile.TarInfo("docs/aws_secret_access_key.md")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    def fake_run(command: tuple[str, ...], **kwargs: object) -> object:
        class Result:
            stdout: str | bytes

        result = Result()
        if command[:2] == ("git", "status"):
            result.stdout = ""
        elif command[:3] == ("git", "rev-parse", "HEAD"):
            result.stdout = "deadbeef\n"
        else:
            result.stdout = git_archive.getvalue()
        return result

    monkeypatch.setattr(build_final_handoff.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="tracked source.*credential"):
        build_final_handoff.build_archive(
            source_archive=source,
            architecture_kit=architecture,
            output=output,
            max_bytes=10 * 1024 * 1024,
        )


def test_archive_is_reproducible_with_fixed_tar_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.tar.gz"
    architecture = tmp_path / "architecture.zip"
    _write_tar(
        source,
        {"handoff/gemini/dialogues/main.jsonl": b'{"ok":true}\n'},
    )
    architecture.write_bytes(b"architecture-kit")

    git_archive = io.BytesIO()
    with tarfile.open(fileobj=git_archive, mode="w:") as archive:
        payload = b"tracked source\n"
        member = tarfile.TarInfo("docs/aws_secret_access_key.md")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    def fake_run(command: tuple[str, ...], **kwargs: object) -> object:
        class Result:
            stdout: str | bytes

        result = Result()
        if command[:2] == ("git", "status"):
            result.stdout = ""
        elif command[:3] == ("git", "rev-parse", "HEAD"):
            result.stdout = "deadbeef\n"
        else:
            result.stdout = git_archive.getvalue()
        return result

    monkeypatch.setattr(build_final_handoff.subprocess, "run", fake_run)
    outputs = [
        tmp_path / "first" / "handoff.tar.gz",
        tmp_path / "second" / "handoff.tar.gz",
    ]
    for output in outputs:
        build_final_handoff.build_archive(
            source_archive=source,
            architecture_kit=architecture,
            output=output,
            max_bytes=10 * 1024 * 1024,
        )

    first, second = (output.read_bytes() for output in outputs)
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()
    assert first == second
    assert int.from_bytes(first[4:8], "little") == 0
    with tarfile.open(outputs[0], "r:gz") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(
            member.name for member in members
        )
        assert all(
            (member.mtime, member.uid, member.gid) == (0, 0, 0) for member in members
        )
        assert all(
            stat.S_IMODE(member.mode) == (0o755 if member.isdir() else 0o644)
            for member in members
        )


def test_archive_builds_twice_from_the_real_source_tree(tmp_path: Path) -> None:
    status = subprocess.run(
        ("git", "status", "--porcelain"), capture_output=True, text=True, check=True
    )
    if status.stdout:
        pytest.skip("build_archive refuses to package a dirty worktree")

    source = tmp_path / "source.tar.gz"
    _write_tar(source, {"handoff/gemini/dialogues/main.jsonl": b'{"ok":true}\n'})
    architecture = tmp_path / "architecture.zip"
    architecture.write_bytes(b"architecture-kit")

    outputs = [
        tmp_path / "first" / "handoff.tar.gz",
        tmp_path / "second" / "handoff.tar.gz",
    ]
    for output in outputs:
        build_final_handoff.build_archive(
            source_archive=source,
            architecture_kit=architecture,
            output=output,
            max_bytes=10 * 1024 * 1024,
        )

    first, second = (output.read_bytes() for output in outputs)
    assert first == second
    assert all(stat.S_IMODE(output.stat().st_mode) == 0o600 for output in outputs)
    assert all(len(payload) <= 10 * 1024 * 1024 for payload in (first, second))
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
        package_name = outputs[0].name.removesuffix(".tar.gz")
        assert (
            f"{package_name}/gemini_history/dialogues/main.jsonl" in archive.getnames()
        )
