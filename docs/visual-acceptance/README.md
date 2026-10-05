# Visual acceptance evidence

These 24 unchanged screenshots capture independently authored synthetic fixtures in the built configuration console. They are actual Chromium captures, not mockups. No private BENCH/Atlas source, data, assets, credentials or internal logs are included.

The tested implementation is commit `b6e3797e903ad3daff070a55130ce2e56c9dc4ab`, source tree `5bbc0093e7a2519d373d9f18a0b2e8bc8da83558`. The evidence-only follow-up does not change the tested implementation. The route is `/configuration/index.html`; the driver is Playwright 1.62.1 with Chromium.

Visual approval is pending. Automated browser assertions are evidence for review and do not approve or merge the draft PR.

## Screenshots

| Scenario | Desktop Chinese 1440×900 | Desktop English 1440×900 | Mobile Chinese 390×844 | Mobile English 390×844 |
| --- | --- | --- | --- | --- |
| scenario | [PNG](1440x900-zh-CN-scenario.png) | [PNG](1440x900-en-US-scenario.png) | [PNG](390x844-zh-CN-scenario.png) | [PNG](390x844-en-US-scenario.png) |
| entity-editor | [PNG](1440x900-zh-CN-entity-editor.png) | [PNG](1440x900-en-US-entity-editor.png) | [PNG](390x844-zh-CN-entity-editor.png) | [PNG](390x844-en-US-entity-editor.png) |
| network-profiles | [PNG](1440x900-zh-CN-network-profiles.png) | [PNG](1440x900-en-US-network-profiles.png) | [PNG](390x844-zh-CN-network-profiles.png) | [PNG](390x844-en-US-network-profiles.png) |
| network-diff | [PNG](1440x900-zh-CN-network-diff.png) | [PNG](1440x900-en-US-network-diff.png) | [PNG](390x844-zh-CN-network-diff.png) | [PNG](390x844-en-US-network-diff.png) |
| versions | [PNG](1440x900-zh-CN-versions.png) | [PNG](1440x900-en-US-versions.png) | [PNG](390x844-zh-CN-versions.png) | [PNG](390x844-en-US-versions.png) |
| replay-gap-evidence | [PNG](1440x900-zh-CN-replay-gap-evidence.png) | [PNG](1440x900-en-US-replay-gap-evidence.png) | [PNG](390x844-zh-CN-replay-gap-evidence.png) | [PNG](390x844-en-US-replay-gap-evidence.png) |

Full-page screenshots retain the viewport width and can be taller than the viewport. Entity-editor and network-diff dialogs use viewport screenshots to show header and footer placement.

[HTML gallery and interaction report](report.html) · [Machine-readable hash and outcome matrix](hash-matrix.json) · [SHA-256 checksums](SHA256SUMS)

GitHub renders the PNGs and this Markdown index. Clone/download this directory and open `report.html` locally for the gallery. To verify the captured PNGs and reports, run `sha256sum -c SHA256SUMS` from this directory.

## Verification

Console checks and build passed with 86 tests. Python verification passed with 120 tests: 113 integration cases and seven relevant existing tests. The React build and seven suites / 17 tests passed. Python lint and whitespace checks passed.

Each locale/viewport combination exercised baseline save, invalid time and ID handling, unsaved modal fields across language switches, Close/Escape, network profile Cancel/Apply, version Cancel/Load, actual import/export, invalid import rejection, persisted drafts, repeated Prepare, entity locks, a 25-second null-motion seek, language/reload cursor preservation and navigation during playback. Browser errors and lifecycle requests were zero; document horizontal overflow and modal footer clipping were absent. Five contrast samples per combination met 4.5; this was a targeted check rather than a full accessibility audit.

Each of four actual browser-exported synthetic observation bundles was accepted by the Python indexed reader with 61 frames and all byte hashes verified. UAV position/velocity at 25 seconds remained null; body extent remained unavailable. The bundles and raw browser logs are not included in this visual-evidence directory.

Browser findings corrected in the tested implementation were mobile navigation labels, modal footer visibility, replay route restoration, reentrant rendering during blur/language changes and small explanatory-text contrast.

## Integration limits

BENCH renderer mounting, authenticated live SSE, native sealed replay, SUMO/ns-3 execution, native Atlas evaluation and real predicate/event parity are not connected or tested. Body extents and clearance remain unknown. No native evaluator or boolean substitute ran. No remote simulation, deployment or merge was performed.
