# Extension roadmap

This roadmap lists the extension contracts the maintainers intend to add and
the order in which they are needed. It is derived from the 2026-10-22 programme
and is deliberately smaller than a feature list: a family is added when a real
consumer needs it, with a version, tests and one owner.

Among the families listed below, only the [scenario plugin
API](../reference/plugin-contracts.md) is shipped today (`API_VERSION = 1`, ten
built-in templates and three hook providers). Engines, algorithms, models and
provider profiles are existing contracts documented in the same reference; they
are not new families on this roadmap. Every row in the table below is
**planned**. Naming one here does not make it importable or registerable, and an
open draft PR is not a shipped contract. The current support boundary, including
the internal surfaces that must not be used as plugin APIs, is documented in
[plugin contracts](../reference/plugin-contracts.md). Repository-level choices
are recorded in [decision records](../decisions/README.md).

## Waves

| Wave | Focus | Status |
| --- | --- | --- |
| 1 | Correctness and governance fixes: agent grant pairing, provider credential containment, kernel close cleanup, fail-closed initialization, assistant authorship, duplicate inventory names, this roadmap and the PR template. | In progress |
| 2 | Public run reader, owner-mediated mission commands (phase M1), metrics reducers, and product correctness fixes (preview positions, gallery headings, truthful hook values, provisional comparisons, docs). | Planned |
| 3 | Presentations and effective controls, assistant operation families, preparation/scenario v2, agent-execution evidence, the #17 salvage (native primitives, browser depth, build knobs) and the installed-wheel packaging gate. | Planned |
| Later | Registry overlays, algorithm capability metadata, kernel growth budgets, a single journal decoder, and the owner-led AeroRescue extraction. | Later |

Wave 1 changes behaviour without adding a contract family. Waves 2 and 3 add
the families below; they are split so a contract, its implementation and its
acceptance evidence can land in separate small PRs.

## Contract families

| Contract | Purpose | Status | How to help |
| --- | --- | --- | --- |
| Public run reader — `aeroagentsim.runs` v1 | Read a committed run through a versioned, read-only facade instead of importing `RunStorage`, service indexes or kernel internals. | Planned — wave 2 | Propose the batch envelope from committed data; add fixtures for partial prefixes, gaps, reconnects, old runs and lossless numbers. Do not add a second storage API. |
| Owner-mediated mission commands — `aas.mission.*` v1 | Let an owner engine reserve, divert, resume and release a mission through versioned commands, validation and terminal receipts. | Planned — wave 2, phase M1 | Work with the runtime owners on schemas and state transitions. Test stale revisions, concurrency, expiry, cancellation and payload ownership. Never use `owner.flights`; an ACK is not completion. |
| Metrics reducers — `aeroagentsim.metrics` v1 | Contribute deterministic reducers that turn committed batches into named metrics, so CLI, live views and comparisons agree. | Planned — wave 2 | Define descriptor, checkpoint and deduplication rules; keep missing data unmeasured rather than zero or success. Preserve existing KPI meanings through explicit aliases. |
| Presentations and effective controls — `aeroagentsim.presentations` v1 | Contribute localized labels, field and enum mappings, narrative templates and finite display primitives, and read back an applied knob's effective value. | Planned — wave 3 | Start from the existing pose/narrative bindings and the flood-rescue marker/region case. Add tests for unknown fields, missing values and bilingual placeholders. No remote JavaScript or executable renderers. |
| Assistant operation families — `aeroagentsim.assistant_operations` v1 | Package semantic operations, parameter schemas, prerequisites and expectations together, while the host keeps staging, diff display, consent and validation. | Planned — wave 3 | Build a synthetic external family that compiles without a host switch. Test unknown operations, invalid parameters, zero-expectation “unassessed” and a compiler that makes no provider call. |
| Preparation contract and scenario v2 — `aeroagentsim.scenario/v2` | Add opt-in v2 admission and deterministic final-seed preparation while v1 documents remain readable. | Planned — wave 3 | Write v1 compatibility fixtures and repeatable same-seed/different-seed preparation tests. Move domain expansion out of the generic loader instead of adding another branch to it. |
| Unified agent-execution evidence v1 | One evidence envelope for observation, prompt, tool call, validation and result, so a recorded decision can be replayed and audited. | Planned — wave 3 | Propose the record shape with the agent and runtime owners. Test replay with no provider, retained IDs/order and explicit unknown evidence. A model reply is not mission completion. |
| Registry overlays — `aeroagentsim.registry_overlays` | Compose declared registry overlays with explicit ordering, dependency checks and conflict rejection once explicit snapshots are insufficient. | Planned — later (P2) | Bring a second packaged domain that needs reusable composition. Until then use an explicit snapshot or inline descriptors; no implicit namespace takeover. |

Statuses describe code in `main`. “Planned” means no implementation is merged,
not that nobody is thinking about it. We update this page when a family ships;
we do not promise dates.

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
