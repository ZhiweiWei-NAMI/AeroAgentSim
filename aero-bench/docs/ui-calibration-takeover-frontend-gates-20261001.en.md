# UI calibration frontend takeover gates

Updated: 2026-10-01 08:13 PDT.

B-017 is an historical stop snapshot. Later Claude workers closed the Jing'an
v3 publication and browser gate, native city presentation, the T2 browser path,
the corrected-lamp weather matrix, source-ground captures, motor/person follow
captures, and the quiescent frame profile. The current implementation also
contains the W6 compact viewer and Studio styling.

The accepted evidence remains scoped:

- Jing'an is an engineering preview backed by real SUMO recording and motion
  audit. It is not a formal Provider run.
- Native city browser presentation passed strict rendering checks. Formal I5
  execution and indexed replay delivery retain their own backend gate.
- Visual rain, time of day and lighting do not establish a Weather Provider.
- Current performance figures describe the measured headless GPU-3 readback
  path. Enforced budgets await independent uncontended confirmation.

The old `loading-progress.test.ts` failure is closed. The corrected fixture
keeps incremental progress coverage on the reader branch that provides it,
without weakening byte-length, intermediate progress, percentage or completion
assertions. A fresh focused run passed 4/4 tests. A fresh full Vitest run passed
127 files and 1,173 tests.

The only current frontend check failure observed by this lane came from the new
operations monitor while parallel integration was active. Type checking reported
an unused `targetKey` import and two optional event identifier values passed to a
required string parameter. The import was already removed after the sampled run;
the operations-monitor owner should close the remaining value narrowing and run
the next type check. No `map.ts`, `app.ts`, camera, minimap or monitor source was
edited by this lane.

The remaining frontend sequence is additive:

1. Finish the shared selection, explicit camera modes, onboard view and minimap
   integration under their current file owners.
2. Connect supported telemetry, events, logistics context and freshness without
   inventing unavailable values or command capability.
3. Validate the operator workflow at 1600×900 and 1280×720 after the browser
   baseline worker releases its session.
4. Freeze the integrated source, run the full frontend checks once, and build to
   a fresh validation directory.
5. Capture the final UI and retain any missing provider/API state as explicit
   unavailable evidence before publication review.

Detailed reconciliation and command results are in
`validation/ui-calibration-20261001/takeover-frontend/report.md` and
`validation/ui-calibration-20261001/takeover-frontend/results.json`.
