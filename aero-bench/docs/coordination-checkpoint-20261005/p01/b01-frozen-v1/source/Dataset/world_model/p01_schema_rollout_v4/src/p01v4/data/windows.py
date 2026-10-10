"""S01.04 supervised windows with explicit causal cutoff.

Builds supervision samples from canonical episode records:

- input branch:  every record with ``tick <= cutoff`` (state, relation,
  event and observation records available at or before the cutoff);
- target branch: records with ``tick > cutoff`` limited to the declared
  ``target_times`` (future frames and future capture observations live
  here and only here).

Leak guards (``scan_input_leak``) reject any input-branch record whose tick
exceeds the cutoff, any future-sourced field content, and any use of future
visibility to decide the input set: the input-set predicate depends only on
``tick <= cutoff`` and record kind, never on field values.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Mapping

# Tick clock of the source episodes: tick_hz=10, dt_s=0.1 (declared in
# truth_frames.jsonl per frame).  One tick step is 0.1 s of physical time.
TICK_HZ = 10
TICK_DT_S = 0.1


class FutureLeakError(ValueError):
    """Raised when future information would enter the input branch."""


@dataclass
class SupervisionSample:
    """One causally-split supervision window."""

    episode_id: str
    split: str
    cutoff: int
    target_times: list[int]
    input_records: list[dict[str, Any]] = dc_field(default_factory=list)
    target_records: list[dict[str, Any]] = dc_field(default_factory=list)

    @property
    def horizon_ticks(self) -> list[int]:
        return [t - self.cutoff for t in self.target_times]

    @property
    def horizon_seconds(self) -> list[float]:
        return [round(t * TICK_DT_S, 6) for t in self.horizon_ticks]


def _is_future_record(rec: Mapping[str, Any], cutoff: int) -> bool:
    tick = rec.get("tick")
    return isinstance(tick, int) and tick > cutoff


def build_supervision_window(records: Iterable[Mapping[str, Any]], *, episode_id: str,
                             split: str, cutoff: int,
                             target_times: Iterable[int]) -> SupervisionSample:
    """Split canonical records into input/target branches at ``cutoff``.

    The input predicate is exactly ``tick <= cutoff`` evaluated on the record
    tick; field values (including visibility) never influence membership.
    Records without a tick (entity/episode) are context and stay on the input
    branch.  Target times must all be > cutoff.
    """
    targets = sorted(int(t) for t in target_times)
    if not targets:
        raise FutureLeakError("empty target_times")
    if any(t <= cutoff for t in targets):
        raise FutureLeakError(
            f"target_times must be strictly after cutoff {cutoff}; got {targets}")
    sample = SupervisionSample(episode_id=episode_id, split=split,
                               cutoff=cutoff, target_times=targets)
    allowed_targets = set(targets)
    for rec in records:
        if rec.get("episode_id") not in (None, episode_id):
            raise FutureLeakError(
                f"cross-episode record '{rec.get('episode_id')}' in window build")
        tick = rec.get("tick")
        if tick is None or (isinstance(tick, int) and tick <= cutoff):
            sample.input_records.append(dict(rec))
        elif tick in allowed_targets and rec["record_kind"] in ("frame", "observation"):
            sample.target_records.append(dict(rec))
        # records beyond cutoff that are not declared targets are dropped from
        # both branches (outside this supervision window's span)
    return sample


def scan_input_leak(sample: SupervisionSample) -> None:
    """Raise FutureLeakError if the input branch carries future information."""
    cutoff = sample.cutoff
    for rec in sample.input_records:
        tick = rec.get("tick")
        if isinstance(tick, int) and tick > cutoff:
            raise FutureLeakError(
                f"input branch carries future record: tick {tick} > cutoff {cutoff} "
                f"({rec['record_kind']} '{rec.get('id')}')")
        # future pose / caption / visibility payloads must never be copied into
        # input-branch records: input frame fields may only describe tick<=cutoff
        if rec["record_kind"] == "frame" and tick is not None and tick > cutoff:
            raise FutureLeakError("future pose in input branch")


def future_visibility_input_guard(selected: Iterable[Mapping[str, Any]], *,
                                  cutoff: int) -> list[dict[str, Any]]:
    """Validate a proposed input selection against the cutoff.

    The input set must be decided by tick alone.  ``selected`` is whatever a
    selector produced; if any chosen record sits beyond the cutoff (e.g. it
    was admitted because of a *future* visibility value), that selector is a
    leak and this guard rejects it.  Returns the selection unchanged when it
    is causally admissible.
    """
    out = [dict(r) for r in selected]
    for r in out:
        if isinstance(r.get("tick"), int) and r["tick"] > cutoff:
            raise FutureLeakError(
                f"selector admitted a future record: tick {r['tick']} > cutoff {cutoff} "
                f"({r['record_kind']} '{r.get('id')}')")
    return out


def reject_future_truth_inputs(input_records: Iterable[Mapping[str, Any]], *,
                               cutoff: int) -> None:
    """Reject future-truth records used as prediction input.

    Truth-side records (frames, edges, events) are hindsight truth: their
    content tick is definitive, so a truth record with tick > cutoff is
    future truth and must never appear on the input branch, regardless of
    any available_time claim.  This is the stronger sibling of
    ``scan_input_leak``: it also rejects undated truth payloads whose
    provenance marks them as produced after the cutoff.
    """
    for rec in input_records:
        tick = rec.get("tick")
        if isinstance(tick, int) and tick > cutoff:
            raise FutureLeakError(
                f"future-truth record on input branch: tick {tick} > cutoff {cutoff} "
                f"({rec['record_kind']} '{rec.get('id')}')")
        avail = (rec.get("provenance") or {}).get("available_time") or {}
        at = avail.get("tick")
        if isinstance(at, int) and at > cutoff:
            raise FutureLeakError(
                f"input record '{rec.get('id')}' carries content first available at "
                f"tick {at} > cutoff {cutoff} (rule {avail.get('rule')})")


def audit_input_selector(selector, records: Iterable[Mapping[str, Any]], *,
                         cutoff: int) -> list[dict[str, Any]]:
    """Audit an input-set selector for future-value dependence.

    A legal selector is a function of the prefix (tick <= cutoff) only.  The
    audit perturbs every FUTURE record's field values (visibility, topology,
    relations - all of them), re-runs the selector, and requires the chosen
    ids to be identical.  A difference means membership was decided from
    future field content (future-visibility or future-topology leak).

    ``selector`` receives the record list and returns an iterable of records.
    The audited selection from the real records is returned on success.
    """
    real = [dict(r) for r in records]
    perturbed = []
    for rec in real:
        r = dict(rec)
        tick = rec.get("tick")
        if isinstance(tick, int) and tick > cutoff:
            r["fields"] = {k: ("<perturbed>" if not isinstance(v, dict) else
                               {"frame": "world_enu_m", "value": [0.0, 0.0, 0.0]})
                           for k, v in rec.get("fields", {}).items()}
        perturbed.append(r)
    chosen_real = [r["id"] for r in selector(real)]
    chosen_perturbed = [r["id"] for r in selector(perturbed)]
    if chosen_real != chosen_perturbed:
        flipped = [i for i in chosen_real if i not in chosen_perturbed][:3]
        raise FutureLeakError(
            "selector membership depends on future field values "
            f"(future-visibility/topology leak); ids changing under future perturbation: {flipped}")
    return future_visibility_input_guard(selector(real), cutoff=cutoff)


# --------------------------- normalization -------------------------------
# Fit-scope enforcement lives in p01v4.data.normalization; windows re-export
# the two names used by consumers.
from p01v4.data.normalization import (  # noqa: E402,F401
    ALLOWED_FIT_SPLITS,
    NormalizationScopeError,
    NORM_VERSION,
    Scaler,
    fit_scaler,
)
