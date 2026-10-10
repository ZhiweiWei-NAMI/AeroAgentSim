# Verification checkpoint — 2026-10-05

The continuation preserves native delivery `a0d1d0a` and the independently authored Python adapter slice. Final visual acceptance remains pending review.

## Automated checks

- Console syntax/import/asset closure: passed.
- Console Node tests: 86 passed, including bilingual modal, draft, variant, selection-lock and revision-key behavior.
- Independent Python adapter and browser-export contracts: 113 passed; the previous 109 tests remain, plus four console-to-Python integration cases.
- Relevant existing registry/compiler, run-repository and runtime-diagnostics tests: seven passed; combined Python verification: 120 passed.
- Existing React frontend: seven suites / 17 tests passed; production build passed with existing unused-function and outdated-browser-data warnings.
- Python Black/Flake8 and Git whitespace checks: passed.

## Actual browser evidence

Chromium, using the installed `/usr/bin/chromium`, runs the checked-in `scripts/browser-qa.cjs`. Both Chinese and English are exercised at 1440×900 and 390×844. Each combination captures six actual screenshots: scenario, entity editor, network profiles, profile diff, versions, and locked replay gap evidence. Full-page images retain the viewport width; dialogs use viewport screenshots to show fixed headers and footers. The output manifest records the tested commit, locale, viewport, scenario and filenames.

The browser checks save/load and actual import/export downloads, invalid time and entity IDs, invalid import, profile Cancel/Apply, independent R3/R4 variants, Close/Escape, repeated Prepare, navigation during playback, null-motion gap seek, language switching inside dialogs and replay, and selection locks across marker clicks, language changes and reload. It checks document overflow and dialog footer clipping and records console, page, request and HTTP failures. No lifecycle/source requests are allowed. Five text/primary-control contrast samples per combination must be at least 4.5; this is a targeted contrast check.

Actual browser-downloaded neutral observation bundles have also been accepted by the Python indexed reader: all 61 frame-byte hashes match in each bundle, and UAV position/velocity at 25 seconds remain null. The Node fixture export is separately covered by the Python test suite.

Browser findings fixed during continuation include hidden mobile navigation, mobile modal footer clipping, missing replay route restoration, reentrant DOM rendering on blur/language changes, and faint small explanatory text.

## Limits and acceptance

BENCH renderer mounting, authenticated live SSE, native sealed replay readers, SUMO/ns-3 execution, native Atlas evaluation and real predicate/event parity are not connected or tested. Body extents remain unavailable; clearance is unknown. Cargo identity/custody/location is reserved for the separately developed native parcel prototype and is not inferred from a carrier.

The PR stays a draft. Actual screenshot files and the interaction report must be reviewed by the coordinating task/user; passing these checks is not visual approval or authorization to merge.
