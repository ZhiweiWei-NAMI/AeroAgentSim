# Verification — 2026-10-05

## Passed

Node.js 24.19.0:

- `npm run check`: JavaScript syntax, local module imports and HTML asset closure.
- `npm test`: 27 tests passed, zero failed/skipped.
- `npm run build`: complete static distribution, no external runtime dependencies.

Coverage:

- All 840 fixture frames validate; same-time replay is deterministic and seek-order independent.
- Every custody change matches an explicit receipt; responsibility does not change midway through a transfer.
- Carriers remain stationary while transferring; cargo remains rigidly attached in transport; no duplicate parcel or discontinuous sampled jump.
- Empty/duplicate identity, parcel/self custody, nonfinite coordinates and detached cargo are rejected.
- Scheduled fault boundaries, predicate flips, ordered source/response links, zero hold displacement/velocity and later alternate-route execution.
- Same-frame selection identity/generation, source-labelled unknown Atlas evidence, translation-key parity, escaped source labels.
- DOM-emulated app boot, Chinese/English switch, entity/SVG picking, stable selection across seek, follow/fit, play/pause, reset, future-ledger toggle and keyboard-focus retention.
- Bounded real HTTP test serves intended frontend assets, rejects internal files and POST, and supplies restrictive response headers.

Development-only DOM dependency jsdom 30.1.2 was reused from the already installed local package tree. `package.json` / lockfile declare it for portable `npm ci`; the temporary `node_modules` link is excluded from delivery. This does not introduce a runtime dependency.

## Not established

- Real-browser visual/responsive QA, screenshots and visual approval. Earlier cloud-browser localhost access was blocked with `ERR_BLOCKED_BY_CLIENT`; no alternative route was used to bypass it.
- Cross-tool preview reachability. A persistent server session printed the loopback URL, but another tool process could not connect to it. The bounded server test succeeded within one process environment. Do not present the localhost URL as a verified user-accessible preview.
- Actual BENCH host mounting, real parcel source contracts, replay shards, live SSE, source barrier/integrity validation, lifecycle/generation transition handling, real telemetry or private assets.
- Atlas evaluator binding, real controller intent/receipt execution or real SUMO/ns-3 response trajectories.
- Full app accessibility, layout overflow or screen-size verification. DOM tests cannot establish those visual properties.

The deliverable is a runnable local prototype and integration boundary, not completed P02 acceptance or a live integrated system.
