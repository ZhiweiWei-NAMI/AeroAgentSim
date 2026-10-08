"""Exclusive named partition RNG streams, derived without discovery order."""

from __future__ import annotations

import hashlib
import random

from .errors import KernelError
from .ids import validate_text
from .values import ResourceBudget, canonical_json


def derive_seed(
    root_seed: int,
    engine_id: str,
    partition_id: str,
    stream_name: str,
    budget: ResourceBudget | None = None,
) -> int:
    """Derive a full SHA-256 big-endian seed using the specified domain encoding."""
    if type(root_seed) is not int:
        raise KernelError("RNG_SEED", "root seed must be an integer")
    for value in (engine_id, partition_id, stream_name):
        validate_text(value)
    data = canonical_json(
        ["aerokernel.rng/v1", root_seed, engine_id, partition_id, stream_name],
        budget,
    )[:-1]
    return int.from_bytes(hashlib.sha256(data).digest(), "big")


class RNGStreams:
    """Eagerly allocate exactly the declared independent streams for one owner."""

    def __init__(
        self,
        root_seed: int,
        engine_id: str,
        partition_id: str,
        names: tuple[str, ...],
        budget: ResourceBudget | None = None,
    ) -> None:
        if len(names) != len(set(names)):
            raise KernelError("RNG_DUPLICATE", "stream names must be unique")
        if type(root_seed) is not int:
            raise KernelError("RNG_SEED", "root seed must be an integer")
        self._seeds = {
            name: derive_seed(root_seed, engine_id, partition_id, name, budget)
            for name in sorted(names)
        }
        self._streams = {
            name: random.Random(seed) for name, seed in self._seeds.items()
        }

    @property
    def seeds(self) -> dict[str, int]:
        """Detached derivation metadata for the run header."""
        return self._seeds.copy()

    def stream(self, name: str) -> random.Random:
        """Return an owned declared stream; undeclared discovery fails."""
        if name not in self._streams:
            raise KernelError("RNG_UNDECLARED", "stream was not declared")
        return self._streams[name]
