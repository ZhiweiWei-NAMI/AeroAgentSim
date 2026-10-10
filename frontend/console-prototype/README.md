# AeroAgentSim 仿真配置中台

A runnable, configuration-first console for the AeroAgentSim + AERO_BENCH + Atlas integration boundary. This is a local fixture prototype, not a connected production simulator.

## Run locally

Use Node.js 24.15 or newer. Runtime has no third-party dependency and no build step is required.

```bash
cd frontend/console-prototype
npm start
# Open http://localhost:4317 in your own browser
```

The included server binds only to `127.0.0.1`. `PORT=... npm start` can select a port on your own machine. No remote address, token, or private server is configured.

For a static package:

```bash
npm run build
# Serve dist/ with an ordinary static server; do not open index.html as file://.
```

To run all checks, including DOM-emulated interaction tests:

```bash
npm ci
npm run check
npm test
npm run build
```

`jsdom` is a development-only dependency. No third-party code is loaded by the application at runtime.

## Workbench entry and language preference

The existing React workbench links to **Configuration authoring · Fixture** at
`/configuration/index.html`. `npm start` and `npm run build` in `frontend/`
stage the console's public distribution automatically. This is a full-page
authoring route, with no iframe, backend proxy or simulation lifecycle requests.
The standalone loopback server remains available at `http://127.0.0.1:4317/`.

Chinese and English cover navigation, forms, dialogs, validation, source
annotations and accessible labels. Preference is saved as
`aero-console.locale.v1`. Switching keeps drafts, modal values, selected entities,
locked selection, sealed runs and replay cursors. Technical IDs, units, public
source titles and user-authored values remain unchanged.

## Observation and selection bridge

**Export adapter observations** downloads the independently authored
`aeroagentsim.observation/v1` frames and `aeroagentsim.replay-index/v1` index used
by `src/aeroagentsim/integration/`. It includes aerial, ground and static entities,
exact decimal-string nanoseconds, byte hashes, source pointers and null motion
where evidence is missing. Body dimensions are absent, so clearance remains
unsupported. This bundle is not a BENCH public trace or native replay file.

The entity lock keeps one stable ID while the view cursor moves. State inputs
show ENU-derived horizontal/vertical speed with units and provenance; rule
parameters remain unvalidated, truth remains null/unknown and last truth change
has no evaluation history. Cargo identity, custody and location belong to the
separate native parcel prototype and are not inferred from vehicle identity.

Selections use the same nested run/frame/evaluation key as the Python shared
store, including epoch, manifest revision, frame hash, stage evidence key and
binding epoch. Exact or previous lookup reports missing bounds instead of
clamping to a fabricated frame. Previously saved prototype runs remain visible
as unverified local cache; preparing a new run enables the observation export.

## Actual browser verification

The development-only Playwright runner uses an installed Chromium executable:

```bash
npm ci
npm start
# In a second terminal:
P02_CHROMIUM=/usr/bin/chromium npm run browser:qa
```

Set `P02_CONSOLE_URL` to an allowed local preview, and `P02_EVIDENCE_DIR` to an
output directory. The runner checks both languages at 1440×900 and 390×844 and
writes actual screenshots, browser request/error observations and interaction
results. It does not contact BENCH, SUMO, ns-3 or Atlas.

## Working surfaces

- Scene, exact step/duration, coordinate and vertical-datum authoring
- Typed entities, stable expanded IDs, resource references, explicit SUMO ownership
- UAV, radio and compute configuration with units and unsupported-capability diagnostics
- R0–R5 network research profiles, explicit variant selection, reviewable field diff before Apply, source classes and public reference links
- Channel width / conditional nominal PHY rate / offered application load separated; propagation reference, receiver noise, MAC queue lifetime and application observation TTL separated
- Atlas binding authoring with explicit entity ownership; no substitute Boolean evaluator
- Local draft persistence; immutable saved versions; restore, diff, JSON import/export and desired-plan export
- Frozen-config fixture records with SHA-256 digests, exact string nanoseconds, an explicit evidence gap, a single shared replay cursor and selected-entity evidence
- Adapter inventory and source/capability boundaries

The two-dimensional ENU preview is a fixture inspection aid. The production monitoring host remains the existing BENCH Three.js viewer. This prototype does not replace its renderer or physics clock.

## First walkthrough

1. Edit scene or entity fields. Changes persist in this browser.
2. Save a baseline version.
3. Open 网络配置. Review R1, then explicitly apply it after reading the field diff. R0 is preserved as the historical reference; R1 changes the frequency-specific 1 m loss. R2–R5 retain additional model requirements as unimplemented research hints.
4. Save a second version and compare the draft with either snapshot.
5. Prepare a fixture run and open replay. Seek to 25 seconds to see missing UAV evidence remain null. Entity selection and evidence share the same tick/hash key.
6. Export the configuration or evidence JSON for inspection.

## Data and truth boundaries

- All scene entities, routes and replay values are independently authored fixtures. No private maps, raw trajectories, runtime code or credentials are included.
- Research profiles summarize reported configuration and public sources. They are not measured RF data, executed backend configurations, device-compliance certification or local calibration.
- Baseline 800 ns GI is a research assumption, not a verified runtime setting. Conditional 6.5 / 26 / 65 Mbps values are not application goodput.
- Configuration data rate is not measured throughput; propagation delay is not end-to-end or application latency. Queue expiry and observation TTL remain different quantities.
- Atlas is not connected and its runtime/catalog is not distributed. Bound predicates display `unknown · 未执行`; unbound entities display `Not bound`. No predicate click sends an actuator command.
- Motion, network, compute and semantic readiness remain separate. Source barrier projection does not establish cryptographic integrity or all-stage commitment.
- Fixture preparation creates local replay records, not a SUMO/ns-3/BENCH simulation. Playback advances a view cursor only.
- Fixture inspection is capped at 250 entities, at most 121 snapshots and at most the first 120 seconds. The full desired configuration can still be saved and exported. Large source steps that cannot fit this window are rejected for fixture preparation.
- Browser-local storage is neither a server database nor authenticated evidence storage. Loaded cached hashes are unverified. Export important work; storage quota failures are surfaced.

## Verification status

See `VERIFICATION.md` for the exact final checks. DOM emulation exercises interactions but does not render CSS or verify browser layout.

An attempted cloud-browser visit to the local preview was blocked (`ERR_BLOCKED_BY_CLIENT`). No alternate host, port, browser or proxy was used to bypass that restriction. Therefore no inspected screenshot, real-browser rendering pass, or responsive visual pass is claimed.

## Integration and publication

See `INTEGRATION.md`. The source is deliberately independent of private BENCH/Atlas implementations. The destination repository and Git publication are coordinated separately. No push, PR, deployment, private trace transfer or remote simulation was performed by this local implementation.
