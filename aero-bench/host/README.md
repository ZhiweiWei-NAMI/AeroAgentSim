# P02 · Isolated BENCH host mounting patch (PR12 revision)

An isolated host that mounts the delivered P02 parcel view
(`validation/p02-parcel-host/source/frontend/parcel-prototype`, commit
`15f473a4dc0ed4f80e3000b0acef6e875c617393`) inside BENCH integration
boundaries, using the PR10 structured Ref/typed-state projection from
`validation/p02-parcel-host/binding-source/validation/predicate-binding-prototype/`
(`HANDOFF.md`, `contracts.py`, `parcel_mapping.json`, Apache-2.0).

The source checkout is read-only and unmodified. All files in this directory
are owned by this patch; reused prototype code carries attribution comments in
each module header. `LICENSE` is the checkout's Apache-2.0 license (an earlier
revision of this README mis-cited MIT; the source license is Apache-2.0).

## Run

```bash
cd aero-bench/host
npm start          # http://localhost:4407 (loopback only)
npm test           # 31 focused node:test regressions
```

`npm test` resolves `jsdom` from `aero-bench/frontend/node_modules`
(devDependency jsdom 30.1.2). Install the Viewer dependencies first in a fresh
clone. This export does not include node_modules or prebuilt browser assets.

## What the PR10 revision changed

- **Ref identity** (`adapter.js`): the exact `Ref` shape
  `{run_id, epoch, id, generation, ref_type}` from `contracts.py`. Integer
  generation ≥ 1 and nonempty-string generation are DISTINCT identities
  (keys are type-tagged `i:1` vs `s:1`); dotted ids never split; Ref contract
  violations throw at ingestion.
- **Ingestion/projection split**: `ingestSceneState`, `ingestSceneContext`,
  `ingestFrameEvidence`, `ingestRuleEvidence` validate once per supplied
  record and build immutable exact-key indexes (duplicate-Ref rejection
  included). `projectHostViewFrame` runs per presented tick and performs only
  state and availability projection over those prebuilt indexes — no schema
  re-checks, no regex, no duplicate detection, no throws in the hot path.
- **Tick 0 and non-contiguous ticks**: `SimulationTime.tick` has contract
  minimum 0; the previous `tick >= 1` rejection is fixed. Recorded tick
  identities are explicit and indexed by identity, never by array index
  (0/5/10 coverage in tests).
- **No synthesized identity**: epoch, generation and revision come only from
  an explicitly declared scene context (the PR10 provider-mapper role,
  `mountParcelHost({ sceneContext })` or a per-push override). Absent identity
  renders as visible `UNKNOWN` (`identityComplete: false`, banner note); it is
  never fabricated from digests, run prefixes or ticks.
- **Evidence by exact context**: evidence envelopes carry
  `context: {run_id, epoch, generation, revision}` and every parcel record is
  keyed by its complete typed Ref. One context legitimately holds a commit
  series (one envelope per `at_tick`); presentation resolves the latest
  commit at or before the presented tick, so later commits never leak
  backwards and cross-run/cross-generation joins are impossible.
- **Availability**: records carry `available_after_commit` and
  `available_ns` (evidence availability timestamps, never relabelled as wall
  clock). A record before its gate stays listed and unknown with
  `joinReason: 'not-yet-available'`.
- **External cursor**: `mountParcelHost(root, { cursor })` accepts an existing
  ReplayState-like cursor (`current()`, `currentIndex()`, `subscribe()`); the
  mount observes it and never plays, pauses or disposes it. Ownership stays
  with the caller (`{ createCursor: true }` keeps the legacy owned cursor).
- **Two-way selection**: view pick → `SelectionState.select`, and
  `SelectionState.subscribe` → view selection, with loop suppression and
  same-target no-ops; unresolvable ids are refused, not fabricated.
- **Attachment inconsistency projects, not throws**: a parcel sample detached
  from its declared carrier renders with `attachmentInconsistent: true` and no
  attachment instead of rejecting the frame.
- **Attribution corrected**: Apache-2.0 (was mis-cited MIT), `LICENSE` added.

## What the PR12 revision changed

- **Truth-in-source labelling**: the motion feed carries a typed identity
  (`FEED_MOTION_DEMO` / `FEED_MOTION_BENCH` / UNKNOWN), declared by the page
  (`host.view.setFeedMotion`) and never inferred from ids, provider names or
  digests. The fixture banner, header source badge, run label and footer all
  render from the ACTUAL feed: the demo page labels everything demo
  (`demo.motion`), an embedded host declares its BENCH motion source, and an
  undeclared source renders UNKNOWN. Parcel-evidence status is separate from
  motion identity (`parcelEvidenceKnown`; a real-BENCH feed has none at this
  boundary). The former `fixtureNote` that unconditionally claimed real BENCH
  motion was removed. The scene badge motion text is localized (the raw
  `motionSourceMixed` key is never displayed).
- **Explicit causal response only**: events may declare
  `response_rule_id` + `response_flip_time_seconds` (typed, validated at
  ingestion, preserved verbatim). The projection exposes `frame.ruleResponse`
  only when an event explicitly references the PRESENTED rule id AND its
  declared flip instant equals the rule's `lastFlip` and the response time is
  not earlier than that flip; kind, ordering, timing
  and proximity infer nothing. Without such a declaration the response chain
  node renders its unbound label (zh 响应未绑定 / en Response unbound) while
  unrelated recent events stay listed in the ledger. The demo ledger declares
  no causal reference, so the demo page honestly shows an unbound response.
- **Attachment id shown once**: when the carrier's display name equals its id
  the attachment cell renders the id once instead of `id · id`, without
  breaking the id into separate words.
- **Visible, localized reset control**: the reset button now always has a
  visible icon + label (`#reset-text`) and an accessible name
  (`t('resetAccessKey')`), refreshed on language change together with every
  other feed-status surface.
- **Accessible language switch**: both language buttons carry `aria-pressed`
  reflecting the actual page language, re-applied from `pageshow` (bfcache
  safe), plus explicit `aria-label`s.
- **Narrow-screen readable cargo IDs**: on ≤850 px viewports stable-ID chips
  are overlaid on the scene in screen space, using the REAL projected
  coordinates of drawn entities (viewBox→client mapping incl. letterboxing);
  unplaced parcels anchor to the explicit unknown list. Chips are select
  buttons (`data-select`, `aria-pressed`) feeding the same exact-id selection.
  Screen-space labels are placed without overlapping one another, with a
  leader to the unchanged source marker. The language controls stay on one line.
  No coordinate or custody value is fabricated; >850 px renders no overlay.
- **Collision-aware parcel callout**: the P-1042 callout tries right, left,
  above, below in PROJECTED space against already-placed label rectangles and
  takes the first non-colliding slot (desktop tick-44 now places it left of
  the parcel instead of over the locker label). Presentation only — no data
  coordinate or custody value changes.

## Host API

```js
import { mountParcelHost } from './mount.js';

const host = mountParcelHost(root, {
  cursor,             // existing ReplayState-like host cursor (owned outside)
  selectionState,     // existing SelectionState (frontend/src/state/selection.ts)
  language: 'zh',
  sceneContext: { epoch: 'bench.epoch.1', generation: 7, revision: 'm.v3' },
});

// 1. Authoritative motion: real BENCH SceneState objects only. Tick 0 and
//    non-contiguous ticks are fine. Optional per-push context override:
host.pushSceneState(sceneState);
host.pushSceneState(otherScene, { epoch: 'bench.epoch.1', generation: 8, revision: 'm.v3' });

// 2. Parcel/geometry/network evidence, typed by exact context (commit series):
host.setEvidence({
  schema_version: 'p02.parcel-host.frame-evidence/v2',
  context: { run_id, epoch, generation, revision },
  evidence: {
    at_tick: sceneState.at.tick,
    parcels: [{ id, state, custodian_id, attachment, available_after_commit,
                available_ns, source: { pointer } }],
    stations: [{ id, position_enu, label_key }],
    routes: { name: [[e, n, u], ...] },
    network: { link_id, rssi_dbm, degraded },
    events: [{ event_id, time_seconds, label_key, kind, entity_ids }],
  },
});

// 3. Rule evidence, separately typed:
host.setRuleEvidence({
  schema_version: 'p02.parcel-host.rule-evidence/v2',
  context: { run_id, epoch, generation, revision },
  evidence: { rule_id, truth, inputs, input_source, engine,
              last_flip_time_seconds, available_after_commit, available_ns },
});

// The mount never drives the external cursor:
cursor.goToTick(44);            // the host's own clock moves the view
host.getSelection();            // exact run/epoch/revision/frame/generation context
```

## Guaranteed behaviour (each covered by a named test)

1. One clock: the host cursor is the only timeline; the mount follows it and
   never advances, interpolates or disposes it.
2. Exact opaque ids: `uav.delivery.alpha` stays whole; selection resolves by
   exact string only.
3. Integer vs string generation never join (`i:1` ≠ `s:1`).
4. Tick 0 and non-contiguous ticks (0/5/10) project with per-tick evidence.
5. Cross-run same-tick evidence never joins the other run's scene.
6. Lifecycle generation switch: gen-7 evidence never joins a gen-8 scene;
   the gen-7 records stay usable for their own scene.
7. Late availability: a record before its gate is listed, unknown, unplaced.
8. Missing epoch/generation/revision renders UNKNOWN and is announced
   (`.identity-note`); UNKNOWN-context evidence never joins actual identity.
9. Custody is only what the host declares; `contacts` and proximity grant
   nothing; an unresolvable custodian renders unknown with the declared value
   still visible (`custodianDeclared`).
10. Wrong-context evidence is a non-join (not a stale flag) and never renders.
11. The motion feed identity is typed declared input (`feedMotion`); an
    undeclared or unknown value renders UNKNOWN, never a real-looking source.
12. A response binds only to an explicit causal reference
    (`response_rule_id` + `response_flip_time_seconds` matching the presented
    rule's flip and response time at or after that flip); otherwise the response node shows its unbound label while
    recent events stay in the ledger as records.
13. Clearing selection and an empty frame render without a null-item crash.

## Demo feed and evidence labelling

The default page feed is DEMO data: authored demo motion (one state per
second over 0–84 s, including tick 0) plus demo parcel/custody/rule evidence
carrying `authority: 'host-demo-evidence'` and `demo.*` source pointers. The
demo scene context is declared explicitly, so all joins are actual-identity.
The standalone server does not provide BENCH's TypeScript public-trace parser.
`?source=<replay JSON>` therefore reports an explicit unavailable-loader error,
with empty UNKNOWN state; it does not replace the requested source with the
demo. To show authoritative motion, embed `mountParcelHost` inside BENCH and
supply actual `SceneState` frames and the host-owned cursor. That live path is
not demonstrated by these screenshots.

## Unsupported live sources (precise)

The BENCH public contract (`frontend/src/generated/schemas/scene-state.schema.json`,
`aero-bench.scene-state/v1`) publishes: scene identity digests, declared
entity ids, motion samples (`pose`, ENU/NED velocities, battery, health,
mode, attributes, contacts), stage barriers and receipts. It does NOT publish:

1. **Parcel samples** — no parcel sample kind exists, so no live parcel
   position/state source is available at this boundary. Parcel evidence in
   this patch is demo-only.
2. **Custody/handoff records** — no responsible-party relation, receipt or
   custody transaction appears on the public boundary; `contacts` is a
   motion-stage contact list, not custody evidence. Custody here is demo-only
   or unknown.
3. **Atlas results** — no rule-engine output, predicate truth, rule inputs or
   truth-flip timestamps exist on the boundary. `rule.atlasValue` is always
   `unknown`; demo rule values are authored.
4. **Epoch/generation/revision of a run** — the SceneState carries run_id and
   scenario_digest but no epoch, generation or manifest revision. Those stay
   UNKNOWN unless a host mapper explicitly declares them; the prototype has
   no authoritative lifecycle/current-generation registry.
5. **Evidence availability domain** — no commit counters or availability
   timestamps are published; demo `available_after_commit`/`available_ns`
   values are authored under an explicit hypothetical policy and are never
   presented as measured provider receipts or wall-clock times.
6. **Model dimensions** — `model_asset_id` is not geometry; body dimensions
   stay null/unknown.
7. **Sealed replay artifact** — no sealed public replay file exists in this
   workspace, so no screenshot shows real BENCH motion. The standalone
   `?source=` loader is unavailable; the embedded mount API accepts supplied frames.

## Screenshots

`host/screenshots/`, captured with the existing Playwright install and cached
Chromium (no installs). All four shots are actual page captures of the same
demo state — demo feed, tick 44, `parcel.p1042` selected (degradation window:
rule truth 真/True, parcel 悬停等待/Holding position, custodian
`uav.delivery.alpha`, response node unbound/未绑定) — under the PR12 light
presentation with source-truthful labels. Regenerate them with
`node capture.mjs` after installing the sibling frontend dependencies
(or set `P02_PLAYWRIGHT_MODULE` explicitly to an existing Playwright package).
It starts the host's loopback server on 127.0.0.1 and stops it afterward; the
capture script installs nothing:

- `host-demo-zh.png` — zh, desktop viewport 1600×1000 @2x
- `host-demo-en.png` — en, desktop viewport 1600×1000 @2x
- `host-demo-zh-mobile.png` — zh, mobile viewport 390×844 @2x (screen-space
  stable-ID chips overlay the letterboxed scene; single-line language controls)
- `host-demo-en-mobile.png` — en, mobile viewport 390×844 @2x (same chips)
- `capture.json` — machine-readable capture record written by `capture.mjs`
  (tool, per-file page state read back from the live DOM — source
  banner/badge/run-label/footer, `feedMotion`, response node state, visible
  reset control with its accessible name, `aria-pressed` of both language
  buttons with measured text-line counts, narrow-screen label bounds and
  overlap pairs — plus `pageErrors`,
  `realBenchMotion: false` with the reason, and pending visual acceptance)

The earlier `bench-motion-zh.png` (the `?source=` surface with the
missing-source fallback active, sanitized in the previous export) was removed;
the standalone `?source=` loader now reports its missing integration explicitly,
so no screenshot claims real motion.

All screenshots show demo/fixture evidence only (motion included: the demo
feed's motion samples are authored `demo.motion` data, labelled as such in the
banner, source badge, run label, footer and scene badge). None is real BENCH
or Atlas runtime evidence. None is visual acceptance: visual acceptance of the
PR12 fixes is pending with the parent review.
