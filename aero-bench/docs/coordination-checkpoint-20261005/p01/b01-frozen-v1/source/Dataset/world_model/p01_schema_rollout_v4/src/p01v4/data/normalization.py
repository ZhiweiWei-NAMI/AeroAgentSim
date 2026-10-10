"""S01.04 normalization with explicit fit-scope provenance.

Scalers are fitted only on the allowed input-branch history of TRAIN-split
episodes (tick <= cutoff).  VALID/TEST fit attempts and transform-time split
violations raise instead of silently proceeding: test-fit normalization is
rejected by construction, not by convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

NORM_VERSION = "p01v4-norm-1"
ALLOWED_FIT_SPLITS = ("TRAIN",)


class NormalizationScopeError(ValueError):
    """Raised when a scaler is fitted or applied outside its declared scope."""


@dataclass
class Scaler:
    """Per-field standardization scaler with explicit fit-scope provenance."""

    field: str
    episode_id: str
    split: str
    max_fit_tick: int
    mean: float
    std: float
    count: int
    version: str = NORM_VERSION

    def scope_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "field": self.field,
            "fit_episode_id": self.episode_id,
            "fit_split": self.split,
            "max_fit_tick": self.max_fit_tick,
            "count": self.count,
        }

    def transform(self, value: float) -> float:
        if self.split not in ALLOWED_FIT_SPLITS:
            raise NormalizationScopeError(
                f"scaler fitted on split '{self.split}' may not transform values")
        if self.count == 0 or self.std == 0.0:
            raise NormalizationScopeError("degenerate scaler: zero count or zero std")
        return (float(value) - self.mean) / self.std


def fit_scaler(records: Iterable[Mapping[str, Any]], *, field: str, episode_id: str,
               split: str, cutoff: int) -> Scaler:
    """Fit mean/std on input-branch values only (tick <= cutoff, TRAIN split).

    VALID/TEST splits and future records are excluded by construction: the
    fit input is the caller-declared allowed history, and the split gate
    rejects any non-TRAIN attempt (test-fit normalization is impossible).
    """
    if split not in ALLOWED_FIT_SPLITS:
        raise NormalizationScopeError(
            f"scaler fit attempted on split '{split}'; allowed: {ALLOWED_FIT_SPLITS}")
    values: list[float] = []
    max_tick = -1
    for rec in records:
        tick = rec.get("tick")
        if not isinstance(tick, int) or tick > cutoff:
            continue  # future records never enter the fit
        v = rec.get("fields", {}).get(field)
        if isinstance(v, Mapping) or v is None:
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            values.append(float(v))
            max_tick = max(max_tick, tick)
    if not values:
        raise NormalizationScopeError(f"no fit values for field '{field}' within cutoff")
    n = len(values)
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / n
    return Scaler(field=field, episode_id=episode_id, split=split,
                  max_fit_tick=max_tick, mean=mean, std=var ** 0.5, count=n)
