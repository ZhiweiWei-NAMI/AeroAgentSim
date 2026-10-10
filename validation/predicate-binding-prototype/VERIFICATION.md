# Verification and external dependency boundary

Source-only prototype, verified 2026-10-05.

- Without ATLAS_ROOT: 42 standalone tests passed; 22 native-dependent tests explicitly skipped.
- With an existing external observable_v2 checkout: 64 tests passed; no skips, errors or failures.
- Seven synthetic entity-kind JSON cases evaluated through the unchanged external Atlas evaluator.
- Fixture custody race: one commit, one stale rejection; three simultaneous contact records remain independent.
- Resource JSON demo: admitted, stale-precondition rejection, capacity-exceeded rejection after re-reading.
- No physical commands, server execution, live provider integration or production-safety claim.

The external Atlas engine, operators, definitions, source bundles and native implementation are not included. Neither BENCH source/assets nor raw private traces are included. All checked-in input values are newly authored synthetic fixtures. Target/field names are integration identifiers, not copied implementations.

Run standalone: `python run_checks.py`. Run native integration: `ATLAS_ROOT=/path/to/observable_v2 python run_checks.py`. Detailed actual outcomes are in verification_standalone.json and verification_native.json.

The local development-only fixture authoring helper and generated native trace/fingerprint report are deliberately omitted from this Git handoff. demo.py can regenerate a local report only when the caller supplies the external runtime.
