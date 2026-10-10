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
    allowed = {"mode", "rate_per_s", "start_ns"}
    if config["mode"] != "poisson" or not allowed <= set(config) <= allowed | {
        "burst_size"
    }:
        raise ValueError("arrivals: select scheduled or poisson parameters")
    rate = finite(config["rate_per_s"], "arrival rate", 0)
    if rate == 0:
        raise ValueError("arrival rate must be positive")
    # burst_size > 1 releases orders in simultaneous groups; rate_per_s stays the
    # average number of orders per second, so bursts arrive at rate / burst_size;
    # the first burst is released 2 s after start so a run never opens empty.
    burst = ns(config.get("burst_size", 1), "burst size", 1)
    current = ns(config["start_ns"], "arrival start")
    result: list[int] = []
    while len(result) < count:
        if burst > 1 and not result:
            current += 2_000_000_000  # the first burst opens the run
        else:
            current += max(1, math.ceil(rng.expovariate(rate / burst) * 1e9))
        result.extend([current] * min(burst, count - len(result)))
    return tuple(result)
