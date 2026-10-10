# Branch integration

This integration keeps `low_altitude_sim` as the working home for the existing
AeroAgentSim simulator and AERO-BENCH workbench. The separate 2.0 platform on
`main` is unchanged.

## Active code and documentation

- The configuration-console visual evidence is retained under
  `docs/visual-acceptance`. All 27 recorded file checksums match. These are
  historical synthetic-fixture captures, not fresh native-backend acceptance.
- P02's live workbench and authoring changes are integrated with the current
  target code. Its native-parcel checkpoint files remain documented snapshots
  under `aero-bench/docs/p02-native-logistics`; they do not activate a native
  parcel service or supply missing private scenario data.
- The P02 terrain-texture request was excluded because it fetched an undeclared
  manifest outside the run asset transport and broke existing native-city
  loading, disposal, and tamper checks. The existing native loader is retained.
- JOSS publication sources, reviewer responses, figures, and research scripts
  are preserved. Historical claims remain historical; see
  `response_to_JOSS/README.md`. Packaging paths and the publication workflow
  follow the current package layout. The coordination branch's logo is used
  in both repository READMEs.

## Independent legacy simulator

`legacy/airfogsim` retains the crowdsensing simulator as an independent project.
The blockchain branch is already in that source history. It is not installed
as part of the current package and is not substituted for the current
`aeroagentsim` runtime. Its README and source manifest explain dependencies,
reproducible repairs, and omitted generated artifacts. Original commits remain
reachable in the integration history.

## Retired prototype branches

Four backup branch heads and their complete unchanged trees are retained in
`archive/airfogsim`, under `retained-prototypes`. The archive's original files
are unchanged. These are recovery snapshots, not enabled runtime features.
The retention commit is `7018f2e64546edd2b993041621e71be6a7f3e82d`.

Branch reference removal does not remove the recorded source histories.

## Validation of the combined result

- Root Python suite: 169 passed, 17 failed, 2 skipped. All 17 failures are the
  existing missing-example cases also present before this integration.
- Isolated legacy regression/integrity suite: 8 passed. All 279 original Python
  source files compile and parse with Python 3.10 grammar; original blob hashes
  are checked except for the three documented source corrections.
- AERO-BENCH's final subtree is identical to the reviewed/tested P02 merge:
  85 affected frontend tests and 45 draft-compiler tests pass; standalone Vite
  bundling passes. The broader frontend suite has 1,297 passes, the same 39
  failures and 48 skips as the target baseline, and the same Blob-environment
  error. Typecheck retains its four baseline errors. Nine city-registration
  setups remain blocked by missing private fixtures.
- Package sdist/wheel generation, relocated asset links, and publication
  bibliography processing pass. Historical visual/checkpoint checksums match.

The repository-wide build is not fully green. See the
[P02 review](../aero-bench/docs/p02-native-logistics/branch-integration-review.md)
and [legacy project guide](../legacy/airfogsim/README.md) for exact checks and
remaining prerequisites. These source checks do not establish native-provider,
flight, SUMO, or reinforcement-learning acceptance.
