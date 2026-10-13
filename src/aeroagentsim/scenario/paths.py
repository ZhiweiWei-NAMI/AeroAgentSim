"""Resolve explicitly authored environment references in source paths."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any


def source_path(value: str, base: Path = Path(".")) -> Path:
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if not resolved:
            raise ValueError(f"source path requires environment variable {name}")
        return resolved

    return base / re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", substitute, value)


def fixture_digests(document: dict[str, Any], base: Path) -> dict[str, str]:
    """Hash the files actually referenced by engine fixture configuration.

    Keep the authored reference as the key so consumers can compare relocated
    fixtures. Stored fixture_sha256 annotations are not evidence of the bytes.
    """
    digests = {}
    for engine in document.get("engines", {}).values():
        config = engine.get("config", {})
        if "fixture_path" not in config:
            continue
        reference = config["fixture_path"]
        if not isinstance(reference, str) or not reference:
            raise ValueError("fixture_path: nonempty file reference required")
        if reference not in digests:
            digests[reference] = "sha256:" + hashlib.sha256(source_path(reference, base).read_bytes()).hexdigest()
    return digests
