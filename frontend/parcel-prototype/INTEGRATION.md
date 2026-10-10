# BENCH host handoff

## Read-only component

```js
import { mountParcelView } from './src/view.js';

const view = mountParcelView(container, {
  language: 'zh',
  onSelectionChange(selection) { hostSelectionStore.select(selection); },
  onSeek(seconds) { hostReplayCursor.seek(seconds); },
});
view.setFrame(normalizedFrame);
view.selectTarget('parcel.p1042');
view.setLanguage('en');
// Later: view.destroy();
```

The component has no timer, simulation step, network connection or command submission. It cannot move a real entity. `src/app.js` owns only the standalone fixture's playback clock; omit it when embedding. The host must supply its single authoritative replay cursor and source evidence. Picking does not change simulation state or invoke a rule/controller.

## View-frame boundary

`schemaVersion = p02.parcel-view-frame/v1` carries:

- runId, epoch, manifestRevision, frameKey, tick, string simTimeNs and timeSeconds.
- entities with exact id, generation, kind, position `[east,north,up]` in metres, velocity in m/s, source pointer, authority and provenance.
- parcels with exact id, generation, state, one explicit policy custodian ID, physical position, optional carrier attachment and optional in-progress transfer.
- attachment `carrierId` and `offsetEnu`. The fixture uses a vertical offset and fixed orientation only. Real rotating carriers require a versioned body-to-ENU rigid transform; do not reuse this simplified offset as a full pose transform.
- current events, separately declared future script, routes, network evidence and rule display model.

`selectionFor` returns exact run/epoch/revision/frame/generation/entity context. Selection remains locked to the same ID across ordinary same-generation replay. Live lifecycle/generation switches, asynchronous stale evidence and host-selection synchronization need explicit host policies before production use. No asynchronous data request exists in this prototype.

## Custody and handoff semantics

Policy custody and physical holding/contact are distinct. During a transfer the departing custodian remains responsible until one successful receipt changes custody. The parcel may physically move toward the receiver while responsibility is unchanged. The fixture records four handoffs at 6, 24, 29 and 78 seconds; tests check every sampled custody change against its receipt and check no duplicate parcel or discontinuous jump appears.

The lightweight `assertFrame` rejects empty/duplicate IDs, unresolved or parcel/self custodians, invalid coordinates, unresolved carrier attachment and detached cargo. It is not a complete host lifecycle or custody transaction validator. A live adapter must validate generation/lifetime, monotonic source records, authorized custody transitions, atomic receipts, location vs responsible party and same-frame stage readiness. Unknown/missing custody must be represented as unavailable rather than invented from vehicle proximity.

## Verified BENCH mapping scope

The independent `normalizeBenchMotion(scene)` reads `samples` and joins exact `entity_id`, using pose.position.enu.{east_m,north_m,up_m} and linear_velocity_enu.{east_mps,north_mps,up_mps}. It reports motion readiness as unverified and returns `parcels: null`, no network evidence and null body dimensions. It does not imply a complete committed multi-domain frame.

Real mobile dimensions are unavailable at the inspected boundary. `model_asset_id` must not be interpreted as dimensions. Scene motion cannot establish parcel custody, business receipts, network measurements or Atlas results. Those require additional, approved source contracts. The prototype's fixed park resources/routes must also be replaced with host scenario geometry rather than presented as real assets.

## Communication → behavior chain

Fixture source: delivery phase, RSSI −98 dBm during [38,54), otherwise −62 dBm; demonstration threshold −90 dBm. True at 38; hold executes at 39; retry fails at 43; replan accepted at 48; alternate trajectory starts at 49; false at 54. These values are authored, not measured.

For live integration: current source state → genuine existing rule/Atlas predicate → truth transition and evidence → controller intent → BENCH future-tick arbitration → accepted/executed receipt → authoritative later motion. Never fabricate changed motion from a detected predicate. Return/landing policies can replace this illustrative replan policy only when their receipts and actual source trajectory support them. Any regenerated trajectory must be a new version with its old evidence preserved.

## Next acceptance gates

1. Real desktop/mobile browser checks in both languages, screenshots and visual review.
2. Fixture-independent domain contract and lifecycle/custody validator.
3. Host-mounted actual entity and parcel binding with shared selection/cursor and authentic evidence.
4. Source-backed rule/effect/trajectory consistency, generation changes and gaps.
5. Only after those gates, an authorized isolated live BENCH / SUMO / ns-3 / Atlas run.

The component performs no deployment or remote server access. This source contains no private BENCH implementation, Atlas runtime bundle or raw private data. Git delivery is a prototype checkpoint, not final product acceptance.
