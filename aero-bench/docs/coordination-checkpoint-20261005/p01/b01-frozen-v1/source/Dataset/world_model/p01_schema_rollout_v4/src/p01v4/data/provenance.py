"""S01.04 supplement: source identity, split-group coupling, producer policy.

Parent cross-review supplement rules implemented here:

- ``transported_q`` (historical diagnostic: TRUE future values mixed with
  TRAIN-frozen Gaussian noise) is an oracle diagnostic only and must never
  enter new prediction inputs;
- ``source_family`` and ``split_group`` are derived from the ORIGINAL
  scenario and seed: every old/new copy, 36overlay, normal/congested branch
  and every supervision window derived from the same source share exactly
  one split group - versions are not independent examples;
- producer mechanism, available_time/cutoff and same-run capture binding are
  recorded where established; anything not established stays explicitly
  unknown (never guessed).

The decorated-episode grammar (copy/overlay/branch suffixes) is parent-
declared lineage; no decorated identifier exists in this package's sources,
so the parser preserves unknown decoration marks verbatim and flags the
grammar as unverified instead of inventing splits from them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

ORIGINAL_ID_RE = re.compile(
    r"^(?P<scenario>.+?)_v(?P<version>\d+)__seed(?P<seed>\d+)$")


class SplitGroupError(ValueError):
    """Raised when split-group coupling or transported-input rules break."""


@dataclass(frozen=True)
class OriginalIdentity:
    """Original scenario/seed behind an episode id, plus variant marks."""

    scenario: str
    version: int
    seed: int
    variant_marks: tuple[str, ...]
    grammar_verified: bool
    _raw_key: str = ""

    @property
    def original_key(self) -> str:
        # the raw pre-decoration head, preserving zero-padding exactly
        return self._raw_key

    def document(self) -> dict[str, Any]:
        return {
            "original_key": self.original_key,
            "scenario": self.scenario,
            "version": self.version,
            "seed": self.seed,
            "variant_marks": list(self.variant_marks),
            "grammar_verified": self.grammar_verified,
        }


def original_identity(episode_id: str, *, variant_delimiters: tuple[str, ...] = ("::", "++")) -> OriginalIdentity:
    """Derive the ORIGINAL scenario/seed identity from any episode id.

    Known-verified grammar: ``<scenario>_v<N>__seed<M>`` (the form used by
    every host_data_v2 cohort entry).  Any additional decoration (copies,
    36overlay marks, normal/congested branch suffixes) is expected to appear
    after a variant delimiter or trailing suffix; such marks are preserved
    verbatim and ``grammar_verified`` is False when they are present, so a
    caller can never mistake an unverified decoration for a known id form.
    """
    text = episode_id
    marks: list[str] = []
    for delim in variant_delimiters:
        if delim in text:
            head, tail = text.split(delim, 1)
            marks.append(tail)
            text = head
    m = ORIGINAL_ID_RE.match(text)
    if m is None:
        raise SplitGroupError(
            f"episode id '{episode_id}' does not match the original-identity grammar "
            f"<scenario>_v<N>__seed<M> (decorations seen: {marks or 'none'})")
    return OriginalIdentity(scenario=m.group("scenario"), version=int(m.group("version")),
                            seed=int(m.group("seed")), variant_marks=tuple(marks),
                            grammar_verified=not marks, _raw_key=text)


def split_group(episode_id: str, split_table: Mapping[str, str]) -> str:
    """Resolve the one split group shared by all versions of one source.

    ``split_table`` maps ORIGINAL keys (``<scenario>_v<N>__seed<M>``) to a
    group label and must be loaded from a versioned config.  Every copy,
    36overlay, normal/congested branch and window of the same original
    resolves to that original's single entry (decorated ids share the
    original's group by construction, so versions are never independent
    examples); an UNDECORATED original missing from the table is an error,
    never a silent default.
    """
    ident = original_identity(episode_id)
    if ident.original_key not in split_table:
        raise SplitGroupError(
            f"original '{ident.original_key}' (from '{episode_id}') is not in the "
            "split table; refusing to invent a split group")
    return split_table[ident.original_key]


# ----------------------- transported_q producer policy --------------------

TRANSPORTED_Q_FAMILY = "transported_q"
# Parent-declared historical mechanism (B01 supplement): transported_q was
# produced from TRUE future values with TRAIN-frozen Gaussian noise added.
# No code in this repository re-derives that mechanism; it is recorded as
# lineage and used only to keep it out of prediction inputs.
TRANSPORTED_Q_MECHANISM = {
    "source_family": TRANSPORTED_Q_FAMILY,
    "producer": "historical P01 diagnostic export (pre-v4 lineage; produced outside this package)",
    "mechanism": "TRUE future values + TRAIN-frozen Gaussian noise (parent supplement declaration)",
    "role": "oracle diagnostic only",
    "admissible_as_prediction_input": False,
    "input_rejection": "reject_transported_inputs() raises on any input-branch record from this family",
}


def reject_transported_inputs(input_records: Iterable[Mapping[str, Any]]) -> None:
    """Raise if any input-branch record carries the transported_q family.

    Diagnostic-side storage of transported_q records is legitimate; only
    their use as prediction input is forbidden.  Detection uses the record's
    declared source family (``source_family`` or provenance
    ``producer_mechanism.source_family``), never a guessed name match on ids.
    """
    for rec in input_records:
        fam = rec.get("source_family")
        if fam is None:
            prov = rec.get("provenance") or {}
            fam = prov.get("producer_mechanism", {}).get("source_family")
        if fam == TRANSPORTED_Q_FAMILY:
            raise SplitGroupError(
                f"transported_q record '{rec.get('id')}' cannot be a prediction input: "
                "historical oracle diagnostic (TRUE future + TRAIN-frozen noise)")


def is_oracle_diagnostic_family(source_family: str) -> bool:
    """True for families admissible only as oracle diagnostics."""
    return source_family == TRANSPORTED_Q_FAMILY


# ------------------- provenance and capture binding -----------------------

def capture_binding_record(episode_id: str, *, frame_tick: int,
                           observations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Same-run capture binding between a truth frame and its modalities.

    Established for the pilot rgb/lidar supplement: an observation at tick t
    is bound to the truth frame at the same tick of the same run.  Returns
    the binding document the window builder stores in provenance.
    """
    obs = list(observations)
    ticks = {o.get("tick") for o in obs}
    if ticks and ticks != {frame_tick}:
        raise SplitGroupError(
            f"capture binding for tick {frame_tick} got observations at {sorted(ticks)}; "
            "same-run binding requires matching ticks")
    return {
        "rule": "same_run_capture_binding",
        "episode_id": episode_id,
        "frame_tick": frame_tick,
        "modalities": sorted({o.get("fields", {}).get("observation.modality")
                              for o in obs if o.get("fields")}),
        "established": True,
    }


def available_time_document(*, rule: str, tick: int | None = None,
                            note: str | None = None) -> dict[str, Any]:
    """Explicit available_time provenance; unknown stays unknown.

    - ``capture_bound``: content became available at a capture tick of the
      same run (established for pilot rgb/lidar observations);
    - ``hindsight_truth``: truth-side record (frames/edges/events) whose
      content tick is definitive but which is NOT admissible as input after
      the cutoff purely by tick - the window builder enforces this;
    - ``archive_only``: full event_script/scene_setup, future fault/weather
      schedules, terminal/hidden control, semantic episode names - part of
      the archive view of an episode, addressed but never model-visible
      unless known at the cutoff (point 2 of the six-point supplement);
    - ``unknown``: rule not established; ``tick`` must be None and the note
      must say why.  Never fabricate a tick for this rule.
    """
    if rule == "unknown":
        if tick is not None:
            raise SplitGroupError("available_time 'unknown' must not carry a tick")
        return {"rule": "unknown", "tick": None, "note": note or "not established"}
    return {"rule": rule, "tick": tick, "note": note}


# ---------------- point 1: package vs per-dimension revisions -------------

class ProvenanceError(ValueError):
    """Raised when a revision/provenance document is structurally invalid."""


def package_revision_document(*, package_source_revision: str, dimensions: Mapping[str, Any]) -> dict[str, Any]:
    """Separate the v14 PACKAGE source_revision from per-dimension revisions.

    A package label (e.g. an inherited 36x8 mark) never proves that all
    eight producers changed.  The package carries exactly one
    ``source_revision``; each of the eight dimensions must carry its own
    ``producer_revision`` plus the actual source files mapped for it.  An
    absent dimension or an absent file list is recorded as null with an
    explicit note - unknown stays unknown.
    """
    doc = {
        "rule": "package_source_revision_vs_per_dimension_producer_revision",
        "package_source_revision": package_source_revision,
        "dimensions": {},
    }
    required = ("truth_frames", "world_truth_graph_deltas", "event_occurrences",
                "entity_roster", "rgb", "lidar")
    for name in required:
        d = dimensions.get(name)
        if d is None:
            doc["dimensions"][name] = {
                "producer_revision": None,
                "source_files": [],
                "note": "dimension not provided; producer_revision unknown",
            }
        else:
            files = d.get("source_files", [])
            if not files:
                raise ProvenanceError(
                    f"dimension '{name}' must map its actual source files; "
                    "a package revision never proves a producer changed")
            doc["dimensions"][name] = {
                "producer_revision": d.get("producer_revision"),
                "source_files": list(files),
                "note": d.get("note"),
            }
    return doc


def changed_dimensions(revision_doc: Mapping[str, Any]) -> list[str]:
    """Dimension names whose producer_revision differs from the package's.

    Comparisons use explicit per-dimension values only; a dimension with an
    unknown (null) producer_revision is never counted as changed or
    unchanged - it is returned separately in the ``unknown`` bucket by
    :func:`revision_summary`.
    """
    pkg = revision_doc["package_source_revision"]
    return sorted(
        name for name, d in revision_doc["dimensions"].items()
        if d.get("producer_revision") is not None
        and d["producer_revision"] != pkg)


def revision_summary(revision_doc: Mapping[str, Any]) -> dict[str, Any]:
    """Package-vs-dimension summary; unknowns stay explicit."""
    pkg = revision_doc["package_source_revision"]
    changed = changed_dimensions(revision_doc)
    unknown = sorted(
        name for name, d in revision_doc["dimensions"].items()
        if d.get("producer_revision") is None)
    same = sorted(
        name for name, d in revision_doc["dimensions"].items()
        if d.get("producer_revision") is not None and d["producer_revision"] == pkg)
    return {
        "package_source_revision": pkg,
        "changed_vs_package": changed,
        "same_as_package": same,
        "unknown_producer_revision": unknown,
        "note": "a shared package label never proves all producers changed",
    }


# ------------- point 2: archive view vs model-visible view ----------------

MODEL_VISIBLE_AT_CUTOFF_RULES = {
    # rule name -> admissible as prediction input when ...
    "capture_bound": "content tick <= cutoff AND same-run capture binding holds",
    "hindsight_truth": "NEVER as input beyond its content tick <= cutoff",
    "archive_only": "only when the plan content is known at the cutoff (known-plan experiment label required)",
}


def model_visible_view(records: Iterable[Mapping[str, Any]], *, cutoff: int) -> list[Mapping[str, Any]]:
    """Filter records down to the model-visible view at ``cutoff``.

    Archive-only content (full event_script/scene_setup, future fault and
    weather schedules, terminal/hidden control, semantic episode names) is
    excluded unless its record carries provenance ``available_time.rule``
    ``capture_bound``/``hindsight_truth`` with tick <= cutoff.  Records with
    an unknown availability rule are excluded and counted, never guessed in.
    """
    visible: list[Mapping[str, Any]] = []
    excluded_archive_only = 0
    excluded_unknown = 0
    for rec in records:
        tick = rec.get("tick")
        in_prefix = tick is None or (isinstance(tick, int) and tick <= cutoff)
        prov = rec.get("provenance") or {}
        avail = prov.get("available_time") or {}
        rule = avail.get("rule")
        if rule in ("capture_bound", "hindsight_truth"):
            at = avail.get("tick", tick)
            ok = isinstance(at, int) and at <= cutoff
        elif rule is None:
            ok = in_prefix and rec.get("source_family") != "transported_q"
            if not ok and in_prefix:
                excluded_archive_only += 1
        elif rule == "archive_only":
            ok = False
            excluded_archive_only += 1
        else:
            ok = False
            excluded_unknown += 1
        if ok:
            visible.append(rec)
    model_visible_view.last_exclusion_counts = {
        "archive_only": excluded_archive_only,
        "unknown_rule": excluded_unknown,
    }
    return visible


# ---------------------- point 5: L/P definition ids -----------------------

L_DEFINITION_LEGACY_Q = "L/p01-legacy-q-bounded-observation"
L_DEFINITION_TTL = "L/p01-ttl-observation-actual-available-time"
#: B00 lineage: old q-based L/P and native mature accepted-cohort TTL L/P are
#: different definitions, never one metric under two cohorts.


def timely_L(cases: Iterable[Mapping[str, Any]], *, cutoff: int) -> dict[str, Any]:
    """TTL L over cases with actual available_time <= cutoff.

    A case without a timely RX (reception) has L UNDEFINED for it - it is
    excluded from the mean and counted in ``l_undefined``, never folded into
    L as zero.  Cases with pending/censored compute tasks contribute no
    hindsight outcome; they are counted, not imputed.
    """
    values: list[float] = []
    undefined = 0
    pending = 0
    for c in cases:
        at = c.get("available_time_tick")
        rx = c.get("rx_tick")
        if not isinstance(at, int) or at > cutoff or not isinstance(rx, int):
            undefined += 1
            continue
        values.append(float(c["latency_ticks"]))
    # pending/censored compute tasks never contribute hindsight outcomes
    pending = sum(1 for c in cases if c.get("compute_status") in ("pending", "censored"))
    return {
        "definition_id": L_DEFINITION_TTL,
        "cutoff": cutoff,
        "n_timely": len(values),
        "l_undefined_cases": undefined,
        "pending_or_censored": pending,
        "L": (sum(values) / len(values)) if values else None,
    }
