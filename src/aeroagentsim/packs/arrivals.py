"""Scheduled arrivals and seeded Poisson interarrival times, in nanoseconds."""

from __future__ import annotations

import math
import random
from typing import Any

from .common import finite, ns


def arrival_times(
    config: dict[str, Any], count: int, rng: random.Random
) -> tuple[int, ...]:
    """Each authored order is released exactly once, never before its timestamp."""
    if config["mode"] == "scheduled":
        if set(config) != {"mode", "times_ns"} or len(config["times_ns"]) != count:
            raise ValueError("arrivals: supply one scheduled time per order")
        times = tuple(ns(v, "arrival") for v in config["times_ns"])
        if times != tuple(sorted(times)):
            raise ValueError("arrivals: times must be nondecreasing")
        return times
    if config["mode"] != "poisson" or set(config) != {"mode", "rate_per_s", "start_ns"}:
        raise ValueError("arrivals: select scheduled or poisson parameters")
    rate = finite(config["rate_per_s"], "arrival rate", 0)
    if rate == 0:
        raise ValueError("arrival rate must be positive")
    current = ns(config["start_ns"], "arrival start")
    result: list[int] = []
    for _ in range(count):
        current += max(1, math.ceil(rng.expovariate(rate) * 1e9))
        result.append(current)
    return tuple(result)
