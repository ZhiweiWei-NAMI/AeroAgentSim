"""Immutable package IR and authored-path diagnostics."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aerokernel.values import canonical_json

FORMAT = "aeroagentsim.behaviour-package/v1"
IR_FORMAT = "aeroagentsim.behaviour-ir/v1"


class CompileError(ValueError):
    def __init__(self, source: str, path: str, message: str) -> None:
        super().__init__(f"{source}: {path}: {message}")
        self.source, self.path = source, path


@dataclass(frozen=True)
class PackageIR:
    document: dict[str, Any]
    digest: str
    source: str
    dependencies: dict[str, tuple[tuple[str, str], ...]]

    def to_data(self) -> dict[str, Any]:
        evaluator = {
            "version": "aerograph-predicate/1",
            "sources": {
                name: hashlib.sha256(
                    (Path(__file__).parents[1] / "engines" / name).read_bytes()
                ).hexdigest()
                for name in ("predicate.py", "predicate_ast.py")
            },
        }
        semantic = {
            "format": IR_FORMAT,
            "document": self.document,
            "dependencies": {
                key: [list(pair) for pair in deps]
                for key, deps in self.dependencies.items()
            },
            "evaluator": evaluator,
        }
        return {
            **semantic,
            "digest": self.digest,
            "ir_digest": hashlib.sha256(canonical_json(semantic)).hexdigest(),
            "source": self.source,
        }
