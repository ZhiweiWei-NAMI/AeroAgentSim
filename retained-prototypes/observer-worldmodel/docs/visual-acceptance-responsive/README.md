# Responsive visual review follow-up

Actual Chromium captures of the production authoring route, using independently authored synthetic fixtures. The tested implementation commit is `3bb8714ce06f448e376c9eb99de63c074da9fd73`. This evidence-only follow-up does not change that implementation. Visual approval remains pending; the PR must not be merged until review.

| Scenario | Desktop Chinese | Desktop English | Mobile Chinese | Mobile English |
| --- | --- | --- | --- | --- |
| validation | [PNG](1440x900-zh-CN-validation.png) | [PNG](1440x900-en-US-validation.png) | [PNG](390x844-zh-CN-validation.png) | [PNG](390x844-en-US-validation.png) |
| versions | [PNG](1440x900-zh-CN-versions.png) | [PNG](1440x900-en-US-versions.png) | [PNG](390x844-zh-CN-versions.png) | [PNG](390x844-en-US-versions.png) |
| replay-gap-evidence | [PNG](1440x900-zh-CN-replay-gap-evidence.png) | [PNG](1440x900-en-US-replay-gap-evidence.png) | [PNG](390x844-zh-CN-replay-gap-evidence.png) | [PNG](390x844-en-US-replay-gap-evidence.png) |

[Hash matrix](hash-matrix.json) · [SHA-256 checksums](SHA256SUMS)

Desktop viewports are 1440×900; mobile viewports are 390×844. These are unchanged full-page PNG captures. Use the original PNG at its native width to inspect text rather than a scaled full-page thumbnail.

The versions heading now follows the active language. Validation summaries stack their heading, diagnostics and explanatory text instead of placing them in narrow horizontal columns. Missing-position evidence is a wrapping HTML notice outside the SVG. Mobile entities have 14 px labels and 44 px minimum selection targets, with current availability shown explicitly. The plot omits the tiny mobile text labels; the list selects the same exact entity as the plot and preserves selection locks. Missing motion remains null; no prior sample supplies a marker.

New drafts use R1 pilot radio inputs: 802.11n, 2.412 GHz, 20 MHz, 16 dBm and −95 dBm. This is an authored research choice, not regulatory or hardware approval. Saved/imported legacy profiles keep their exact values. The 5.15–5.35 GHz caution applies independently of research-profile selection; no automatic migration occurs.

Verification: 89 console tests, four Python browser-export contract tests, syntax/import checks and production build passed. Four real browser combinations passed heading, stacked-diagnostic width, wrapping gap notice, readable mobile entity labels, selection-lock and existing interaction checks. No browser errors, lifecycle requests or document horizontal overflow were recorded. Existing React build warnings remain unchanged.

The original 24-shot evidence remains preserved at [commit 6d73600](https://github.com/ZhiweiWei-NAMI/AeroAgentSim/tree/6d73600d502dbdc6c6b23c1a8a3ee5a60ce76dfd/docs/visual-acceptance). No private server source, data, maps, credentials or internal logs are published here. No simulation, live integration, deployment or merge was performed.
