"""Immutable package IR and authored-path diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FORMAT = "aeroagentsim.behaviour-package/v1"
IR_FORMAT = "aeroagentsim.behaviour-ir/v1"


class CompileError(ValueError):
    def __init__(self, source: str, path: str, message: str) -> None:
        super().__init__(f"{source}: {path}: {message}")
        self.source, self.path = source, path


@dataclass(frozen=True)
class PackageIR:
    document: dict[str, Any]
    package_id: str
    source: str
    dependencies: dict[str, tuple[tuple[str, str], ...]]

    def to_data(self) -> dict[str, Any]:
        evaluator = {
            "version": "aerograph-predicate/1",
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
            "package_id": self.package_id,
            "source": self.source,
        }
