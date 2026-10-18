# Deliverable index

Snapshot audited on 2026-10-18. Platform and application pull requests are still
draft/open and target main. New experiments must retain separate run evidence;
the previous report is not updated with predicted outcomes.

## AeroAgentSim: platform source

- [PR17](https://github.com/ZhiweiWei-NAMI/AeroAgentSim/pull/17), branch
  `feat/companion-platform`, audited head `196d1b1`.
- Generic native camera/estimate streams, cancellation, configurable physical
  payload and native radio interfaces live under the platform adapters and
  `containers/px4-gazebo/`, `containers/ns3/`.
- This branch excludes AeroRescue policy, skill folders and model/data assets.
  The legacy platform rescue expansion/planner remains unchanged.
- Native Docker/PX4/Gazebo/ns-3 execution is still pending.

## AeroRescue: application source and review documents

- [PR18](https://github.com/ZhiweiWei-NAMI/AeroAgentSim/pull/18), branch
  `aerorescue`, audited documentation head `592280a`, code checkpoint
  `95dbfcd`. This branch includes PR17's dependency; its application changes
  relative to `196d1b1` are confined to `AeroRescue/`.
- `src/aerorescue/`: Python execution and application algorithms;
  `harness/`: Node Cordis/DSH launcher, plugins and npm lockfile;
  `skills/`: 21 independently described skill folders using shared execution.
- `scenarios/`, `tools/`, `deploy/`, `containers/companion/`: scenario inputs,
  actual runners, asset preparation, image definitions and recorded dependencies.
  Run local mock cases from [README](../README.md); native image entrypoints are
  documented in [guide](guide.md) and remain unbuilt/unrun on this host.
- [Stage report](mock-report.md), [50-item ledger](mock-acceptance.csv) and
  [focused review](mock-review.md) retain successes, incomplete attempts and
  remaining native/live conditions. The audited ledger has 25 MOCK_PASS,
  24 PARTIAL and 1 NOT_COVERED; all 50 user approvals are pending.
- No tracked runtime source file was found missing between the audited remote
  heads and matching clean local checkpoints. Wheel installation carries Python
  resources; the Node harness and source runners require the source checkout.

## Google Drive: private assets and evidence

Model weights, original photographs, retained run directories and document
artifacts belong in the separately delivered private data/evidence collection.
They are deliberately excluded from public Git. Private links are supplied only
in the user's private delivery, never in this index.

The existing evidence inventory contains 69 frozen directories and 66,147 source
files across one main package and three supplements. All four packages and the private restore index have verified uploads; the
private index is available in the private evidence collection. New experiment directories are additional evidence, not automatically
part of those four packages. Use each verified restore catalog for exact package
contents and recovery instructions. Model/runtime asset archives are separate
from these run packages.

Some narrow historical diagnostic drivers and analysis scripts remain only in
the retained run evidence (for example `runs/development/c03-c07-fault-components/`
and `runs/development/closed-relay-no-spin-02/`). They are not required to launch
the shipped C01/C03/C04/C07 runner. Exact diagnostic reproduction requires that
evidence archive; do not assume every historical diagnostic is a public CLI.

## Reliability and necessity: current evidence and next comparison

Existing end-to-end and fault-injection results establish behavior for their
recorded inputs. They do not estimate general operational reliability or prove
that an LLM is required. Model-provider and native execution remain unverified.

The requested stage-wise allocation without replanning after resource withdrawal versus online-replanning comparison is pending
new recorded results. It can assess the need for adaptation under its chosen
resource change, not LLM necessity: a traditional planner can also replan online.
The short-contact payload branch also lacks an independent run in the current
ledger. Keep new outcomes separate until their actual evidence is reviewed.
