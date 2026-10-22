# 0001 — Branch disposition for the rescue drafts

- **Status:** Accepted
- **Date:** 2026-10-22
- **Deciders:** AeroAgentSim maintainers
- **Contract impact:** None. This record changes references and PR metadata, not a runtime or document contract.

## Context

On 2026-10-22 the repository had three long-lived draft PRs for a rescue
companion: #16 `feat/rescue-companion-native`, #17 `feat/companion-platform`
and #18 `aerorescue`. All three branched from `0b406d01`, before `main` gained
#19–#24. Those four PRs added the scenario plugin API (`API_VERSION = 1`), ten
built-in one-template plugins, three hook providers, a plugin inventory and an
external example. That API is the supported way for a domain to contribute
templates, hooks and packaged resources; runtime, read and presentation
integrations are separate, versioned families.

The drafts contained useful work, but they mixed reusable platform capability
with a rescue application. #16 and #18 placed a companion/application runtime
inside the platform namespace. #16 constructed its own simulation and registry.
Both reached through adapter clients and private caches; #18 also depended on
host demo and renderer objects. Neither used documented commands and reads.
Moving those files outside the checkout would not by itself make them plugins.
#17 was mostly platform work — configurable cameras, source-clock telemetry,
physical payload receipts, cancellation and registered browser depth — but it
also carried a cache-only ns-3 change, lacked nested-configuration validation and
capability admission, and changed only one test assertion. A wholesale merge of
any draft would have created a second extension path and preserved competing
implementations. Detailed file-level evidence is in the 2026-10-22 repository
review.

Two refs were fully merged (`dev/lowalt`, `feat/flood-rescue-prototype`),
`low_altitude_sim` was a separate 1.1.1/SimPy-era tree, and
`archive/airfogsim` was already the documented archive. Keeping stale,
active-looking branches invited accidental merges.

## Decision

| Ref | Action taken | Where the content lives | Preservation |
| --- | --- | --- | --- |
| PR #16 / `feat/rescue-companion-native` | Closed without merge on 2026-10-22; branch deleted on the same day. | Rescue-specific paths move to the external AeroRescue repository. Shared native capability is handled once by the #17 replacements; there is no additional generic-only extraction. | Annotated tag `archive/pr-16-rescue-companion-native` at `56e1bc9b` |
| PR #18 / `aerorescue` | Closed without merge on 2026-10-22; branch deleted on the same day. | The 140-file `AeroRescue/` application is reconciled externally under owner control. Its 27 platform paths duplicate #17 and add no independent platform salvage. | Annotated tag `archive/pr-18-aerorescue` at `cdd8df51` |
| PR #17 / `feat/companion-platform` | Kept open on 2026-10-22. Not approved for merge as is. | To be re-landed as three small replacement PRs: **N** native primitives, **V** registered browser depth capture, **B** build-resource knobs. The ns-3 cache-only delta is excluded. | Annotated tag `archive/pr-17-companion-platform` at `4ea31d5a` |
| `dev/lowalt` | Deleted on 2026-10-22. | Already in `main`; no unique commits. | None needed; fully reachable from `main`. |
| `feat/flood-rescue-prototype` | Deleted on 2026-10-22. | Already merged by #15; no unique commits. | None needed; fully reachable from `main`. |
| `low_altitude_sim` | Deleted on 2026-10-22 after tagging its tip. | Retained for history only: a separate 1.1.1/SimPy-era package, not a merge source. | Annotated tag `archive/low_altitude_sim` at `b65c20ba` |
| `archive/airfogsim` | Kept on 2026-10-22. | Documented archive of retired prototype trees; explicitly not an active integration branch. | Branch `archive/airfogsim` at `7018f2e6`, kept as-is |

No replacement PR is merged in `main` at the time of this record; N, V and B
are planned work, not approved changes. The tags are annotated, point at the
original tips and are preservation refs only. Do not merge or rebase them into
`main`; extract reviewed hunks or re-land them from a branch based on current
`main`. Restoring a deleted branch from its tag is possible, but doing so does
not make its content reviewed.

## Rationale

### The core stays domain-neutral

The platform owns time, committed state, commands and documented reads. A domain
contributes templates, hooks, engines and other declared integrations; it does
not add an application runtime, its own simulation loop or a private path into
another owner's state. #16 and #18 required exactly those private paths. A file
move would preserve the coupling and give the next domain a precedent for
bypassing the owner contracts.

### The scenario plugin API (#21–#24) is the seam

#21–#24 define a real, versioned contribution boundary: a zero-argument factory,
a matching name, literal `api_version=1`, packaged resources and strict hook
obligations. Nothing in this record widens that contract or creates a second
one. A future plugin family needs its own version and its own accepted design;
installing a distribution today does not activate anything beyond the scenario
contract.

### #17 is split, not rejected

The generic capability is worth keeping. It is not ready to merge unchanged:
new native operations need explicit capability admission, nested sensor and
payload configuration needs the same fail-closed checks as outer keys, browser
depth needs validation of missing/malformed results, private caches must be
removed, and each feature needs regression coverage. N, V and B are independent
replacement PRs, not copies of the draft, so each can land on its own contract
and test evidence. The cache-only ns-3 delta has no consumer and is dropped.

### Deletion is reversible because tips are tagged

Every deleted ref is reachable from an annotated tag or from `main`. The
repository keeps one current platform tree and one documented legacy archive
(`archive/airfogsim`) without keeping stale branches that look mergeable. No
evidence, journal or archive is deleted by this decision.

## AeroRescue migration (owner-led future work)

The external AeroRescue repository owns this work. It is recorded here so the
platform contracts and the external packaging do not diverge while the drafts
are retired. None of it is implemented by this change.

1. **Keep three distributions distinct.** Preserve the existing `aerorescue`
   distribution for training, filtering and its CLI. Add `aerorescue-scenarios`
   with namespace `aerorescue_scenarios` for templates and hooks, and
   `aerorescue-companion` with namespace `aerorescue_companion` for the
   reconciled application. Do not install two distributions over `aerorescue/`
   or replace its CLI silently; the filter adapter is imported by the current
   low-altitude pack and must keep its behaviour and provenance.

2. **Read public HTTP feeds instead of storage internals.** Replace `sys.path`
   injection, `RunStorage`, `committed_prefix`, `decode_record` and `thaw` with
   `GET /v1/runs/{id}/header` and
   `GET /v1/runs/{id}/commits?from=N&limit=M`. Validate the
   `aeroagentsim.viewer-feed/v1` contract and run identity; advance over empty
   commits, honour `finalCursor` and partial prefixes, reject gaps, deduplicate
   reconnects, retain lossless time and `messages[].schemaId`. Keep normalized
   offline JSONL as an explicit fallback, and do not assume that a public
   `aeroagentsim.runs.open_run` exists.

3. **Contribute a scenario, not an application launcher.** Register a scenario
   through the existing entry-point group:

   ```toml
   [project.entry-points."aeroagentsim.scenarios"]
   aerorescue-research = "aerorescue_scenarios:plugin"
   ```

   The zero-argument factory returns a `ScenarioPlugin` whose name matches and
   whose `api_version` is the literal `1`. Ship a packaged
   `aeroagentsim.template/v1` template plus a complete
   `aeroagentsim.scenario/v1` scenario, registry snapshot and relative
   resources. Start with the unique template ID `aerorescue-research`.
   Replacing `flood-rescue` or `rescue` later is a separate, deliberate decision
   that must supply the complete advertised hook set. Hooks may read packaged
   resources, validate knobs and edit the copied document only; they may not
   start runtimes, download models or call providers.

4. **Use a distinct engine name if runtime integration is added.** Register
   `aerorescue_mission` rather than overriding `lowalt_tasks`, declare the
   fields it writes and consume committed inputs. Do not reach into
   `owner.flights` or another engine's private state. Reserve, divert, resume
   and release must wait for the planned owner-mediated `aas.mission.*` v1
   commands and correlated receipts; payload consumption needs its own owner
   command. A missing mission semantic is a contract dependency, not permission
   for a private write.

5. **Preserve preparation, provenance and honest evidence.** Prepare from
   authored inputs only after the final seed, pin the elaborated document and
   keep packaged resource lifetimes valid. Pin platform, kernel, plugin, native,
   harness and model revisions separately. Replay must make no provider calls.
   Migration must not upgrade the existing research claims: no mission-success
   probability, no native acceptance from mocks, and no capture or deployment
   claim without its own evidence.

## Consequences

- `main` remains one current platform tree. The rescue application does not
  enter it through a branch merge.
- Every old tip and the legacy tree are reachable by an annotated tag or a kept
  archive branch; deleting the active-looking refs loses no commit.
- #17 remains open work. Closing it depends on the N, V and B replacement PRs
  landing and being reviewed, not on this record.
- The external migration has its own owners, packaging and evidence. It is not
  scheduled by this record.
- CI and contribution guidance must not target the retired branch; the workflow
  trigger cleanup is owned by the CI change, not this record.

## References

- [Plugin contracts](../reference/plugin-contracts.md)
- [Scenario plugins guide](../guides/scenario-plugins.md)
- [Viewer feed](../reference/viewer-feed.md) and [HTTP API](../reference/http-api.md)
- [Decision records](README.md)
- Tags and tips: `git tag -l 'archive/*'`, `git show --no-patch <tag>`
