# Catalog-to-module mapping audit

Status: audit plan and explicit source boundary, 2026-10-05.

## Available evidence and blocked source

The requested `observable_v2` source is not present in this cloud workspace.
The current public BENCH export does not bundle Atlas. PR10 takes Atlas as a
caller-supplied external dependency and is not merged into this branch.

The existing P02 release documentation reports 1,686 predicates, 965 event
definitions (2,651 target rules), 3,754 declared state specifications and three
additional scope-validation metadata fields. It separately reports 11,778 offline
examples. These are **reported release counts**, not source-recounted mapping or
live coverage results from this task.

The previously delivered source archive could be identified, but materialization
was refused by its current access scope. No alternate access route was attempted.
An authorized readable copy of the original archive or `observable_v2` is required
to pin hashes, enumerate the exact revision and compute a full mapping denominator.
The original written plan's Appendix B explicitly enumerates 35 business
workflows. That verifies its declared design scope, not the executable activity
inventory or source-backed workflow coverage in the unavailable Atlas archive.

PR10's README reports loading 1,420 native targets from four explicitly supplied
predicate files. That is a different pinned scope from the reported 1,686-predicate
release. Do not call the difference missing coverage without reconciling exact
catalog files and revisions. PR10 also reports seven synthetic entity-kind cases
and 64 tests with external Atlas (42 standalone, 22 skipped without it). None was
rerun here; fixture execution is not live source coverage.

## Current accounting

| Measure | Current defensible result |
| --- | --- |
| BENCH source revision inspected | `e8ea3ab3ffcf19c6165fb13be2e1ab58cb421fe7` |
| Source/ownership groups inspected | 14, listed in `MODULE_STATE_INVENTORY.md`; groups are not individual states |
| Representative conceptual chains described | 4: charging, parcel logistics, link degradation, return |
| Atlas source catalog independently recounted here | Not available; original source access blocked |
| Per-state source mappings verified against that catalog | Not computed |
| Predicate bindings verified against current BENCH facts | Not computed |
| New executable integration/tests in this change | None; documentation only |
| Real-host-connected P02 chains verified here | None; no connection or simulator execution performed |

“Not computed” must not become `0%` or `100%`. The denominator and relation between
catalog state specifications and concrete runtime fields are not yet verified.

## Required per-state audit record

Create a machine-readable audit only after the source is pinned. Each record needs:

- Catalog revision and state ID; exact definition and applicable entity/relation type
- Quantity meaning, type, unit/frame, validity/freshness and availability requirements
- One selected computing authority for each configured run scope
- Actual source path/symbol/API field, with source revision
- Direct mapping, explicitly derived mapping, or unavailable reason
- Required dependency states and declared update/stage coupling
- Conversion expression and its permitted domain, if any
- Agent visibility and authority boundary, separately from physical truth
- Declared/implemented/tested/real-host-connected evidence, independently recorded
- Test case, artifact/run evidence and unresolved gap owner

Keep mapping status separate from runtime truth. Suggested mapping statuses:
`unreviewed`, `direct_source`, `derived_source`, `declaration_only`,
`missing_source`, `semantic_mismatch`, `blocked_source_access`.
These are audit labels, not extra Atlas truth values.

## Required per-predicate/event audit record

1. Pin the exact native rule ID/revision, signature, parameters and transitive
   state/reference/history dependencies. Do not infer dependencies from prose.
2. Bind entity and relation roles by exact typed identity. Confirm direction,
   lifecycle and applicable capability scope.
3. Map every state leaf through the audited state records. Preserve unavailable
   reasons; never substitute a hand-written threshold for native Atlas execution.
4. Declare temporal policy, clock mapping and availability cutoff. For absence
   predicates, require complete exact-scope coverage. For events, specify onset,
   sustained occurrence, recovery and duplicate handling as applicable.
5. Run native rule checks and adapter boundary tests separately. Test decisive
   three-valued cases, stale/missing/conflicting values, wrong-generation joins,
   late availability and unit/frame mismatches.
6. Replay genuine provider observations read-only and retain prepared inputs,
   results, provenance and revision. No replayed command may execute.
7. Only then assess live connection or control readiness under a separate
   authorized run and explicit admission/effect-confirmation contract.

## Incremental order

The immediate implementation priority is author/review configuration in the
existing console, as specified in [CONFIGURATION_UI_PLAN.md](CONFIGURATION_UI_PLAN.md).
Full source recovery and real-backend integration are not prerequisites for
that work. The gates below govern catalog mapping and later live claims, not
permission to start a clearly labelled fixture/stub configuration UI.

### Gate A: terminology and ownership review

Accept or amend the fixed terms, independent agent layer and initial ownership
inventory. Resolve unclear semantics before implementation. Do not merge an
integration that treats goals as actual position or receipts as physical success.

### Gate B: pin and audit the full catalog

Obtain authorized original bytes; record digest and version. Enumerate all
activities, state specifications, predicates and event definitions with separate
counts. Compute dependency closure, collapse repeated identical state IDs and
retain distinct quantities. Assign source status and a computing authority per
field; keep multi-module dependencies. Produce exact counts by status with a
reproducible denominator and explicit unresolved IDs.

### Gate C: four read-only representative chains

- Charging: distinguish planning Wh from battery observation and missing actual
  connection/current evidence.
- Logistics: distinguish cargo declaration, actual attachment/contact, custody
  and order completion; preserve the current physical-interface refusal.
- Link degradation: bind direction, flow/cohort and time window; preserve
  aggregate/per-link and radio/delivery distinctions.
- Return: distinguish desired goal, accepted command and actual motion; require
  appropriate arrival/landing evidence.

The native rule IDs for these examples must come from the pinned catalog. The
architecture's conceptual examples do not invent them in advance.

### Gate D: extend by missing shared states

Prioritize missing reusable state producers and explicit couplings across the
30+ activities. Prefer existing PX4/Gazebo, ns-3 and SUMO boundaries where their
semantics fit. Evaluate a compute backend such as SimGrid and a broader energy
module against concrete missing fields. Do not build one-off pairwise activity
compatibility lists or claim the eight categories prove complete coverage.

## Stop conditions for this documentation slice

The slice is complete when terminology, architecture, initial source inventory
and next mapping gate are reviewable in the existing repository, with verified
paths and no unsupported live-coverage claim. Full source recovery, catalog
mapping, new runtime code, tests, hosting and simulator execution are separate
work beyond this initial documentation slice.
