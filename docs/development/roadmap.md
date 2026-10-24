# Extension roadmap

This roadmap lists the extension contracts the maintainers intend to add and
the order in which they are needed. It is derived from the 2026-10-22 programme
and is deliberately smaller than a feature list: a family is added when a real
consumer needs it, with a version, tests and one owner.

Shipped contract families are marked **Available** and link to their
reference; everything else is **planned**. Naming a family here does not make
it importable or registerable, and an open PR is not a shipped contract.
Engines, algorithms, models and provider profiles are existing contracts
documented in [plugin contracts](../reference/plugin-contracts.md), which also
lists the internal surfaces that must not be used as plugin APIs.
Repository-level choices are recorded in [decision
records](../decisions/README.md).

## Waves

| Wave | Focus | Status |
| --- | --- | --- |
| 1 | Correctness and governance: agent grant pairing, provider credential containment, kernel close cleanup, fail-closed initialization, assistant authorship, duplicate inventory names, this roadmap and the PR template. | Done |
| 2 | Public run reader, owner-mediated mission commands (phase M1), metrics reducers, and product correctness (preview positions, gallery headings, truthful hook values, provisional comparisons, documented product story). | Done |
| 3 | Agent-execution view, the #17 salvage (native primitives, browser depth, build knobs) and the installed-wheel packaging gate. | Done |
| 4 | Runtime ownership and evidence: exception-safe sessions, one journal admission routine, kernel growth measurement (no budgets yet), bounded experiments with comparison evidence, a browser experiment journey, one frontend transport policy, dock agent observation with non-causal link wording, and bilingual parity tests. | Done |
| 5 | Shared extension discovery and admission, assistant operation families, bounded service lifecycle and remote-serving policy. | Done |
| 6 | Presentations and effective controls, preparation contract / scenario v2. | Done |
| 7 | Moving algorithms, the algorithm catalog and assistant operations onto the shared discovery helper; in-repo evidence consumers on the public run reader; agent token budgets reserved before sending. | Done |
| 8 | Projection provenance: original versus recomputed projections, with run-time definition pins. | Done |
| 9 | Descriptor-driven product surfaces (first steps): assistant operation copy and metric labels, units, direction and help come from contributed data; consumption models join shared discovery. | Done |
| 10 | Bounded projection cost: a projection cost bench, incremental latency statistics, KPI series paging and shared storage offsets. | Done |
| Next | Read-only feed storage outside source runs; descriptor-driven spatial presentation (viewport) and authoring forms; external evidence consumers (AeroRescue episodes). | Planned |
| Later | Registry overlays, algorithm capability metadata, kernel growth budgets (after measurements), and the owner-led AeroRescue extraction. | Later |

Each family lands as a contract, an implementation and acceptance evidence in
small PRs.

## Contract families

| Contract | Purpose | Status | How to help |
| --- | --- | --- | --- |
| Scenario plugins — `aeroagentsim.scenarios` | Templates, knobs and reusable apply hooks. | Available — API v1 ([contract](../reference/plugin-contracts.md)) | Add a scenario as an installable plugin; see the [guide](../guides/scenario-plugins.md). |
| Public run reader — `aeroagentsim.runs` v1 | Read a committed run through a versioned, read-only facade instead of importing `RunStorage`, service indexes or kernel internals. | Available — API v1 ([contract](../reference/run-reader.md)) | In-repo demo, thumbnail and pack readers use it. Migrate external readers (AeroRescue episodes) next, and report capabilities the reader lacks rather than importing internals. |
| Projection provenance — `aeroagentsim.projection-provenance/v1` | Tell an original projection from a recomputation under newer definitions; old runs are `legacy-unpinned`. | Available ([viewer feed](../reference/viewer-feed.md), [run reader](../reference/run-reader.md)) | Add pins for new definition families; never report `original` without a run-time pin. |
| Owner-mediated mission commands — `aas.mission.*` v1 | Let an owner engine reserve, divert, resume and release a mission through versioned commands and typed terminal receipts. | Available — phase M1 ([contract](../reference/mission-commands.md)) | Payload ownership is not covered by these four commands; propose an owner-specific delivery command. An ACK or acceptance is not completion. |
| Metrics reducers — `aeroagentsim.metrics` v1 | Deterministic reducers over recorded messages, so CLI, live views and comparisons agree. | Available — API v1 ([contract](../reference/metrics-plugins.md)) | Add reducers with explicit descriptors and missing-data policies; the browser shows their labels, units, direction and help from the KPI rows, so no host change is needed. Preserve legacy KPI meanings. |
| Agent-execution view v1 | One read view over observation, prompt, tool call, validation and result for recorded decisions. | Available — view v1 ([reference](../reference/agent-execution.md)) | Extend fixtures for scripted, structured-JSON and function-tool modes. A model reply is not mission completion. |
| Comparison evidence | Bounded experiments and per-row cut, definition and compatibility metadata. | Available ([reference](../reference/comparison-evidence.md)) | Add definition revisions to metrics so more comparisons can be known-compatible instead of unknown. |
| Shared extension admission | One discovery helper and admission rules for every family. | Available ([reference](../reference/extension-admission.md)) | Every family uses it; consumption models keep selection-scoped duplicate handling through an opt-in option. Report a family whose semantics diverge rather than forking discovery. |
| Assistant operation families — `aeroagentsim.assistant_operations` v1 | Package semantic operations, parameter schemas, prerequisites and expectations together, while the host keeps staging, diff display, consent and validation. | Available — API v1 ([contract](../reference/assistant-operations.md)) | Contribute an operation family for a new domain; it compiles without any host switch, and the assistant shows its zh/en copy from `GET /v1/assistant/operations`. |
| Presentations and effective controls — `aeroagentsim.presentations` v1 | Localized labels, field and enum mappings, narrative templates and finite display primitives, plus the effective value of an applied knob. | Available — API v1 ([contract](../reference/presentation-plugins.md)) | Add a presentation provider for a new domain: data only, bilingual, no remote JavaScript or executable renderers. Report controls as `Unavailable` rather than guessing. |
| Preparation contract and scenario v2 — `aeroagentsim.preparers` v1, `aeroagentsim.scenario/v2` | Opt-in v2 admission and deterministic final-seed preparation while v1 documents remain readable. | Available — API v1, opt-in v2 ([contract](../reference/preparation-plugins.md)) | Write a deterministic preparer for your domain; add same-seed/different-seed tests. v1 documents keep their legacy preparation. |
| Registry overlays — `aeroagentsim.registry_overlays` | Compose declared registry overlays with explicit ordering, dependency checks and conflict rejection. | Later (P2) | Bring a second packaged domain that needs reusable composition. Until then use explicit snapshots or inline descriptors. |

Statuses describe code in `main`. "In progress" means work is under review,
not merged. We update this page when a family ships; we do not promise dates.
Kernel growth is [measured](../performance/runtime-data.md) but has no budget
yet.

## Anti-goals

- No plugin marketplace, no plugin-supplied browser JavaScript, and no
  universal framework built before it has a consumer.
- No wholesale merge of old drafts. Do not delete evidence, journals or
  archives to make the tree look clean.
- No live credentials in PR CI. Skipped native or live jobs are never reported
  as acceptance.

## Contributing to a contract

1. Describe the consumer and the versioned shape in an issue or a draft
   [decision record](../decisions/README.md) before writing a registry or a
   second dispatch path.
2. Keep the PR to one family and one file set, target `main`, and use the
   [pull request template](../../.github/pull_request_template.md).
3. Add contract tests for wrong versions, unknown keys, duplicate names,
   missing evidence and old-run reading. A new meaning needs a new version, not
   a wider old one.
4. Report the checks you actually ran, and every native, live-model or Docker
   check you skipped, in the PR. See [CONTRIBUTING](../../CONTRIBUTING.md).
