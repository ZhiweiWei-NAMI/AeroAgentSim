"""Resolve explicitly authored environment references in source paths."""

from __future__ import annotations

import os
import re
from pathlib import Path


def source_path(value: str, base: Path = Path(".")) -> Path:
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if not resolved:
            raise ValueError(f"source path requires environment variable {name}")
        return resolved

    return base / re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", substitute, value)
