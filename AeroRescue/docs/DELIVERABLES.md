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
  `aerorescue`, prior documentation head `592280a`; current reviewed marker-vision code
  `ecb1ae0` (CI pending). The new visual run used equivalent frozen source. This branch includes PR17's dependency; its application changes
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
  remaining native/live conditions. The audited ledger has 26 MOCK_PASS,
  23 PARTIAL and 1 NOT_COVERED; all 50 user approvals are pending.
- No tracked runtime source file was found missing between the audited remote
  heads and matching clean local checkpoints. Wheel installation carries Python
  resources; the Node harness and source runners require the source checkout.

## Google Drive: private assets and evidence

Model weights, original photographs, retained run directories and document
artifacts belong in the separately delivered private data/evidence collection.
They are deliberately excluded from public Git. Private links are supplied only
in the user's private delivery, never in this index.

The verified private evidence collection now contains seven packages covering
86 evidence directories, 74,265 run/analysis files and 1,530 frozen-source files.
It includes the original four packages, P04/replanning comparison, new
marked-package visual evidence and the reason-field follow-up. The private
restore index lists each package and exact recovery paths; it is available in
the private evidence collection. Model/runtime asset archives remain separate
from these run packages. Future runs are not automatically included.

Some narrow historical diagnostic drivers and analysis scripts remain only in
the retained run evidence (for example `runs/development/c03-c07-fault-components/`
and `runs/development/closed-relay-no-spin-02/`). They are not required to launch
the shipped C01/C03/C04/C07 runner. Exact diagnostic reproduction requires that
evidence archive; do not assume every historical diagnostic is a public CLI.

## Reliability and necessity: current evidence and next comparison

Existing end-to-end and fault-injection results establish behavior for their
recorded inputs. They do not estimate general operational reliability or prove
that an LLM is required. Model-provider and native execution remain unverified.

The completed stage-wise allocation without replanning after resource withdrawal
versus online-replanning pair restored inspection only in the online arm.
Neither arm dropped a package; online incurred more calls, submitted hop bytes
and elapsed time. A conventional planner can also replan online. One pair does
not establish Agent/LLM necessity or a general reliability rate.

The P04 short-contact/sustained-contact tool-chain component now has actual
contrasting receipt results. A separate new C01 five-DSH run recognizes the
public inventory marker in a new post-release RGB-D frame and locates the marked
box at the receiving site. This new gate does not retroactively apply to the old
three seeds or replanning comparison. Human collection and long-term retention
remain unobserved; see the updated stage report for exact scopes.
