// Focused host-mount regressions (PR10 revision). jsdom is resolved from the
// existing frontend/node_modules tree (devDependency of the base app,
// jsdom 30.1.2); no dependency is installed by this patch.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const hostRoot = path.dirname(fileURLToPath(import.meta.url));
const workspaceRoot = path.resolve(hostRoot, '..');
const require = createRequire(path.join(workspaceRoot, 'frontend', 'package.json'));
const { JSDOM } = require('jsdom');

const {
  buildHostViewFrame, ingestSceneState, ingestFrameEvidence, ingestRuleEvidence,
  projectHostViewFrame, selectionFor, assertRef, makeRefKey, sameRef, UNKNOWN_IDENTITY,
  assertViewFrame, HOST_FRAME_EVIDENCE_SCHEMA, HOST_RULE_EVIDENCE_SCHEMA,
} = await import('./adapter.js');
const { HostReplayCursor, HostSelectionStore, mountParcelHost } = await import('./mount.js');

// ---------------------------------------------------------------------------
// Contract-faithful SceneState builder (shape mirrors
// frontend/src/generated/aero-bench-contracts.ts, SceneState/StateSample).
// Tick 0 and non-contiguous tick numbers are contract-legal
// (SimulationTime.tick minimum 0).
// ---------------------------------------------------------------------------
const RUN = 'a'.repeat(64);
const RUN_B = 'c'.repeat(64);
const SCENARIO = 'b'.repeat(64);
const hex = (n, seed) => Array.from({ length: 64 }, (_, i) => '0123456789abcdef'[(seed + i * (n + 5)) % 16]).join('');

const SCENE_CTX = { epoch: 'bench.epoch.1', generation: 7, revision: 'host.manifest.v3' };

function sample({ tick, ns, id, east, north, up, ve = 0, vn = 0, vu = 0, kind = 'dynamic', provider = 'bench.motion', runId = RUN, scenario = SCENARIO }) {
  return {
    schema_version: 'aero-bench.state-sample/v1',
    run_id: runId, scenario_digest: scenario,
    at: { tick, sim_time_ns: ns },
    stage: 'motion', entity_id: id, provider_id: provider,
    sample_kind: kind,
    pose: { position: { enu: { east_m: east, north_m: north, up_m: up } } },
    linear_velocity_enu: { east_mps: ve, north_mps: vn, up_mps: vu },
    linear_velocity_ned: { east_mps: ve, north_mps: -vn, down_mps: -vu },
    sample_digest: hex(tick + east, 1),
  };
}

/**
 * One scene over ticks (array of tick numbers, may be 0 or non-contiguous).
 * The parcel rides the uav rigidly at offset [0,0,-0.8].
 */
function sceneState({ tick, ns, ids, runId = RUN, scenario = SCENARIO }) {
  const carrier = { east: 20 + tick * 0.1, north: 20, up: 12 };
  const samples = ids.map((id, i) => {
    const isParcel = id === 'parcel.p1042';
    return sample({
      tick, ns, id, runId, scenario,
      east: isParcel ? carrier.east : 20 + i * 12 + tick * 0.1,
      north: isParcel ? carrier.north : 20 + i * 4,
      up: isParcel ? carrier.up - 0.8 : i === 0 ? 12 : 0,
      ve: 1.5, kind: isParcel ? 'dynamic' : i % 2 === 0 ? 'dynamic' : 'static',
    });
  });
  return {
    schema_version: 'aero-bench.scene-state/v1',
    run_id: runId, scenario_digest: scenario,
    at: { tick, sim_time_ns: ns },
    declared_entity_ids: [...ids],
    samples,
    stage_barrier: {
      schema_version: 'aero-bench.stage-barrier/v1',
      run_id: runId, scenario_digest: scenario,
      at: { tick, sim_time_ns: ns }, stage: 'motion',
      provider_ids: ['bench.motion'], receipts: [], receipt_digests: [],
      barrier_digest: hex(tick, 3),
    },
    contribution_digests: [],
    previous_scene_state_digest: hex(tick + 998, 5),
    scene_state_digest: hex(tick, 7),
  };
}

const IDS = ['uav.delivery.alpha', 'ugv.delivery.alpha', 'parcel.p1042'];

function frameEvidence(context, { tick, state = 'on_uav', custodian = 'uav.delivery.alpha', parcelGen = null, availableAfterCommit = 0, availableNs = null } = {}) {
  return {
    schema_version: HOST_FRAME_EVIDENCE_SCHEMA,
    context,
    evidence: {
      ...(tick !== undefined ? { at_tick: tick } : {}),
      parcels: [{
        id: 'parcel.p1042',
        ...(parcelGen !== null ? { generation: parcelGen } : {}),
        state,
        ...(custodian !== null ? { custodian_id: custodian } : {}),
        ...(custodian !== null ? { attachment: { carrier_id: custodian, offset_enu: [0, 0, -0.8] } } : {}),
        dimensions_m: [0.45, 0.32, 0.26],
        mass_kg: 1.8,
        target: 'locker',
        ...(availableAfterCommit !== 0 ? { available_after_commit: availableAfterCommit } : {}),
        ...(availableNs !== null ? { available_ns: availableNs } : {}),
        source: { pointer: 'demo.parcels[parcel.p1042]', authority: 'host-demo-evidence', frameId: 'demo.frame' },
      }],
      stations: [{ id: 'locker', label_key: 'locker', position_enu: [111, 42, 0] }],
      events: [{
        event_id: 'e.receipt', time_seconds: 6, label_key: 'receipt_ugv', kind: 'custody',
        entity_ids: ['parcel.p1042', 'uav.delivery.alpha'], from_id: 'warehouse', to_id: 'uav.delivery.alpha',
      }],
    },
  };
}

function ruleEvidence(context, truth = true, { availableAfterCommit = 0, availableNs = null, generation = undefined } = {}) {
  return {
    schema_version: HOST_RULE_EVIDENCE_SCHEMA,
    context: generation !== undefined ? { ...context, generation } : context,
    evidence: {
      rule_id: 'demo.delivery-link-degraded',
      truth,
      inputs: { delivery_phase: true, rssi_dbm: -98, threshold_dbm: -90 },
      input_source: 'demo scripted feed',
      engine: 'host-demo',
      last_flip_time_seconds: 38,
      ...(availableAfterCommit !== 0 ? { available_after_commit: availableAfterCommit } : {}),
      ...(availableNs !== null ? { available_ns: availableNs } : {}),
      source_pointer: 'demo.rule[demo.delivery-link-degraded]',
    },
  };
}

const EVIDENCE_CTX = { run_id: RUN, epoch: 'bench.epoch.1', generation: 7, revision: 'host.manifest.v3' };

// ---------------------------------------------------------------------------
// Ref / identity primitives
// ---------------------------------------------------------------------------

test('Ref: integer generation 1 and string generation "1" are distinct identities', () => {
  const intRef = { run_id: RUN, epoch: 'e1', id: 'parcel.p1042', generation: 1, ref_type: 'object' };
  const strRef = { run_id: RUN, epoch: 'e1', id: 'parcel.p1042', generation: '1', ref_type: 'object' };
  assert.equal(sameRef(intRef, strRef), false, 'integer 1 and string "1" must not collide');
  assert.notEqual(makeRefKey(intRef), makeRefKey(strRef));
  assert.equal(makeRefKey(intRef).includes('i:1'), true);
  assert.equal(makeRefKey(strRef).includes('s:1'), true);
  assert.equal(sameRef(intRef, { ...intRef }), true);
  // Contract violations rejected at ingestion.
  assert.throws(() => assertRef({ ...intRef, generation: 0 }), /positive integer or nonempty string/);
  assert.throws(() => assertRef({ ...intRef, generation: null }), /generation/);
  assert.throws(() => assertRef({ ...intRef, id: '' }), /nonempty string/);
  assert.throws(() => assertRef({ ...intRef, ref_type: 'edge' }), /object or relation/);
});

test('Ref: UNKNOWN components cannot join actual identities', () => {
  const unknownEpoch = { run_id: RUN, epoch: UNKNOWN_IDENTITY, id: 'parcel.p1042', generation: 7, ref_type: 'object' };
  const actual = { run_id: RUN, epoch: 'bench.epoch.1', id: 'parcel.p1042', generation: 7, ref_type: 'object' };
  assert.equal(sameRef(unknownEpoch, actual), false);
});

// ---------------------------------------------------------------------------
// Tick 0 and non-contiguous ticks
// ---------------------------------------------------------------------------

test('tick 0 is accepted and projects (contract minimum 0, previously rejected)', () => {
  const frame = buildHostViewFrame(
    sceneState({ tick: 0, ns: 0, ids: IDS }),
    frameEvidence(EVIDENCE_CTX),
    ruleEvidence(EVIDENCE_CTX, false),
    { sceneContext: SCENE_CTX },
  );
  assert.equal(frame.tick, 0);
  assert.equal(frame.simTimeNs, '0');
  assert.equal(frame.timeSeconds, 0);
  assert.equal(frame.rule.value, false);
  const parcel = frame.parcels.find(p => p.id === 'parcel.p1042');
  assert.equal(parcel.state, 'on_uav');
  assertViewFrame(frame);
});

test('non-contiguous ticks index by exact recorded identity, never by index', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  const host = mountParcelHost(root, {
    createCursor: true, sceneContext: SCENE_CTX,
  });
  for (const tick of [0, 5, 10]) {
    host.pushSceneState(sceneState({ tick, ns: tick * 1_000_000_000, ids: IDS }));
    host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick }));
    host.setRuleEvidence(ruleEvidence(EVIDENCE_CTX, true));
  }
  assert.deepEqual([...new Set([0, 5, 10])].length, 3);
  host.activeCursor.goToTick(0);
  assert.equal(host.getSelection().tick ?? host.getSelection().frameKey.split(':').at(-1), '0');
  assert.equal(host.getSelection().generation, 7, 'integer generation passes through unchanged');
  host.activeCursor.goToTick(10);
  assert.equal(host.getSelection().frameKey.split(':').at(-1), '10');
  // Cursor never invents an in-between state.
  host.activeCursor.seek(1);
  assert.equal(host.getSelection().frameKey.split(':').at(-1), '5');
  host.destroy();
});

test('0/5/10 tick coverage keeps motion continuous and evidence per tick', () => {
  const scene0 = ingestSceneState(sceneState({ tick: 0, ns: 0, ids: IDS }), SCENE_CTX);
  const scene5 = ingestSceneState(sceneState({ tick: 5, ns: 5e9, ids: IDS }), SCENE_CTX);
  const scene10 = ingestSceneState(sceneState({ tick: 10, ns: 10e9, ids: IDS }), SCENE_CTX);
  const evidence0 = ingestFrameEvidence(frameEvidence(EVIDENCE_CTX, { tick: 0 }));
  const evidence5 = ingestFrameEvidence(frameEvidence(EVIDENCE_CTX, { tick: 5 }));
  const evidence10 = ingestFrameEvidence(frameEvidence(EVIDENCE_CTX, { tick: 10 }));
  for (const [scene, evidence] of [[scene0, evidence0], [scene5, evidence5], [scene10, evidence10]]) {
    const frame = projectHostViewFrame(scene, evidence, null);
    const parcel = frame.parcels.find(p => p.id === 'parcel.p1042');
    assert.equal(parcel.state, 'on_uav', `tick ${scene.tick}`);
    assert.equal(parcel.custodian, 'uav.delivery.alpha');
    // The parcel sample rides its carrier exactly at every sampled tick.
    const uav = frame.entities.find(e => e.id === 'uav.delivery.alpha');
    assert.ok(parcel.position.every((v, i) => Math.abs(v - uav.position[i] - (-0.8 * (i === 2))) < 1e-9));
  }
});

// ---------------------------------------------------------------------------
// Cross-run same-tick evidence and lifecycle generation switches
// ---------------------------------------------------------------------------

test('cross-run same-tick: evidence from run A never joins a run B scene at the same tick', () => {
  const sceneA = ingestSceneState(sceneState({ tick: 42, ns: 42e9, ids: IDS }), SCENE_CTX);
  const sceneB = ingestSceneState(
    sceneState({ tick: 42, ns: 42e9, ids: IDS, runId: RUN_B }),
    { ...SCENE_CTX, epoch: 'bench.epoch.2', generation: 7 },
  );
  const evidenceA = ingestFrameEvidence(frameEvidence(EVIDENCE_CTX, { tick: 42 }));
  const ruleA = ingestRuleEvidence(ruleEvidence(EVIDENCE_CTX, true));
  // Run A joins.
  const frameA = projectHostViewFrame(sceneA, evidenceA, ruleA);
  assert.equal(frameA.parcels[0].state, 'on_uav');
  // Run B (same tick, different run/epoch) must NOT join run A's evidence.
  const frameB = projectHostViewFrame(sceneB, evidenceA, ruleA);
  assert.equal(frameB.parcels[0].state, 'unknown');
  assert.equal(frameB.parcels[0].custodian, null);
  assert.deepEqual(frameB.unresolvedParcelIds, ['parcel.p1042']);
  assert.equal(frameB.rule.value, null, 'rule from run A never presents in run B');
  // Position is still real motion from run B's own SceneState sample set.
  assert.equal(frameB.parcels[0].position, null, 'unjoined parcel evidence adds no position');
  // 2 scene entities (parcel id is projected as a parcel) + the evidence's
  // own 'locker' station (host geometry, not joined motion — its id does not
  // exist in run B's declared set).
  assert.equal(frameB.entities.length, 3, 'scene entities still present');
  assert.equal(frameB.entities.filter(e => e.sourceKind === 'scene-state').length, 2);
  assert.ok(frameB.entities.some(e => e.id === 'uav.delivery.alpha'));
  assert.ok(frameB.entities.some(e => e.id === 'ugv.delivery.alpha'));
});

test('lifecycle generation switch: integer generation distinctness and late availability', () => {
  // Generation 7 -> generation 8 lifecycle: same run/epoch, new generation.
  const sceneGen7 = ingestSceneState(sceneState({ tick: 9, ns: 9e9, ids: IDS }), SCENE_CTX);
  const sceneGen8 = ingestSceneState(sceneState({ tick: 10, ns: 10e9, ids: IDS }),
    { ...SCENE_CTX, generation: 8 });
  const evidenceGen7 = ingestFrameEvidence(frameEvidence(
    { ...EVIDENCE_CTX, generation: 7 }, { tick: 9 }));
  const ruleGen7 = ingestRuleEvidence(ruleEvidence({ ...EVIDENCE_CTX, generation: 7 }, true));
  const frameGen7 = projectHostViewFrame(sceneGen7, evidenceGen7, ruleGen7);
  assert.equal(frameGen7.parcels[0].state, 'on_uav');
  assert.equal(frameGen7.generation, 7);
  // Generation 8 scene does not join generation 7 evidence...
  const frameGen8 = projectHostViewFrame(sceneGen8, evidenceGen7, ruleGen7);
  assert.equal(frameGen8.parcels[0].state, 'unknown');
  assert.equal(frameGen8.rule.value, null);
  // ...and integer 8 vs string "8" never join either.
  const evidenceStr8 = ingestFrameEvidence(frameEvidence(
    { ...EVIDENCE_CTX, generation: '8' }, { tick: 10 }));
  const frameStr8 = projectHostViewFrame(sceneGen8, evidenceStr8, null);
  assert.equal(frameStr8.parcels[0].state, 'unknown', 'string "8" never joins integer 8');
  // Generation 8 evidence joins generation 8 scene.
  const evidenceGen8 = ingestFrameEvidence(frameEvidence(
    { ...EVIDENCE_CTX, generation: 8 }, { tick: 10 }));
  const frameGen8Joined = projectHostViewFrame(sceneGen8, evidenceGen8, null);
  assert.equal(frameGen8Joined.parcels[0].state, 'on_uav');
  assert.equal(frameGen8Joined.generation, 8);
});

test('late availability: record exists but stays unknown until its availability gate', () => {
  const scene = ingestSceneState(sceneState({ tick: 5, ns: 5e9, ids: IDS }), SCENE_CTX);
  // available_after_commit = 9: record exists for tick 5's context but is
  // only usable from tick 9 on.
  const evidenceLate = ingestFrameEvidence(
    frameEvidence(EVIDENCE_CTX, { tick: 5, availableAfterCommit: 9 }));
  const before = projectHostViewFrame(scene, evidenceLate, null);
  const parcelBefore = before.parcels.find(p => p.id === 'parcel.p1042');
  assert.equal(parcelBefore.state, 'unknown', 'before the gate the record is unknown');
  assert.equal(parcelBefore.position, null, 'no position while unavailable');
  assert.equal(parcelBefore.joinReason, 'not-yet-available');
  assert.deepEqual(before.unresolvedParcelIds, ['parcel.p1042']);
  // At a scene tick >= 9 with the same context the record becomes usable.
  const scene9 = ingestSceneState(sceneState({ tick: 9, ns: 9e9, ids: IDS }), SCENE_CTX);
  const after = projectHostViewFrame(scene9, evidenceLate, null);
  const parcelAfter = after.parcels.find(p => p.id === 'parcel.p1042');
  assert.equal(parcelAfter.state, 'on_uav', 'after the gate the record presents');
  // available_ns gate: unavailable until sim time passes it.
  const evidenceNs = ingestFrameEvidence(
    frameEvidence(EVIDENCE_CTX, { tick: 5, availableNs: 6e9 }));
  const frameNs = projectHostViewFrame(scene, evidenceNs, null);
  assert.equal(frameNs.parcels.find(p => p.id === 'parcel.p1042').state, 'unknown');
  const evidenceNs2 = ingestFrameEvidence(
    frameEvidence(EVIDENCE_CTX, { tick: 5, availableNs: 4e9 }));
  const frameNs2 = projectHostViewFrame(scene, evidenceNs2, null);
  assert.equal(frameNs2.parcels.find(p => p.id === 'parcel.p1042').state, 'on_uav');
});

// ---------------------------------------------------------------------------
// Missing identity is visible, not fabricated
// ---------------------------------------------------------------------------

test('missing epoch/generation/revision stays UNKNOWN, never synthesized', () => {
  // No scene context declared: epoch/generation/revision are UNKNOWN.
  const scene = ingestSceneState(sceneState({ tick: 3, ns: 3e9, ids: IDS }));
  const frame = projectHostViewFrame(scene, null, null);
  assert.equal(frame.epoch, UNKNOWN_IDENTITY);
  assert.equal(frame.generation, UNKNOWN_IDENTITY);
  assert.equal(frame.manifestRevision, UNKNOWN_IDENTITY);
  assert.equal(frame.identityComplete, false);
  assert.equal(frame.hostContext.generationIsInteger, false);
  // Entity generation is null (not a run-prefix/tick synthesis).
  assert.equal(frame.entities[0].generation, null);
  // Evidence with actual identity cannot join the UNKNOWN-context scene...
  const evidence = ingestFrameEvidence(frameEvidence(EVIDENCE_CTX, { tick: 3 }));
  const joined = projectHostViewFrame(scene, evidence, null);
  assert.deepEqual(joined.unresolvedParcelIds, ['parcel.p1042']);
  assert.equal(joined.parcels[0].joinReason, 'identity-mismatch');
  // ...and the scene's own Ref keys carry UNKNOWN so the mismatch is exact.
  assert.equal(scene.epoch, null);
  assert.equal(scene.generation, null);
});

// ---------------------------------------------------------------------------
// External cursor: accepted, observed, never owned or disposed
// ---------------------------------------------------------------------------

test('external ReplayState-like cursor is observed, never played or disposed', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  const ticks = [0, 5, 10];
  let listener = null;
  let disposed = false;
  let pausedCalls = 0;
  const externalCursor = {
    ticks,
    timesNs: ticks.map(t => t * 1_000_000_000),
    position: 0,
    current() { return this.ticks[this.position] ?? null; },
    currentIndex() { return this.position; },
    subscribe(l) { listener = l; return () => { listener = null; }; },
    pause() { pausedCalls++; },
    dispose() { disposed = true; },
  };
  const host = mountParcelHost(root, { cursor: externalCursor, sceneContext: SCENE_CTX });
  for (const tick of ticks) {
    host.pushSceneState(sceneState({ tick, ns: tick * 1e9, ids: IDS }));
    host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick }));
    host.setRuleEvidence(ruleEvidence(EVIDENCE_CTX, true));
  }
  assert.equal(typeof listener, 'function', 'mount subscribed to the external cursor');
  assert.equal(host.ownsCursor, false);
  // Drive the external cursor like ReplayState does; the mount follows.
  externalCursor.position = 2;
  listener();
  assert.equal(host.getSelection().frameKey.split(':').at(-1), '10');
  externalCursor.position = 0;
  listener();
  assert.equal(host.getSelection().frameKey.split(':').at(-1), '0');
  assert.equal(host.getSelection().generation, 7, 'external-cursor path preserves integer generation');
  // Destroying the mount leaves the external cursor alive and untouched.
  host.destroy();
  assert.equal(disposed, false, 'external cursor is never disposed by the mount');
  assert.equal(pausedCalls, 0, 'external cursor is never paused by the mount');
  assert.equal(typeof externalCursor.current, 'function');
  // And its subscription was released.
  assert.equal(listener, null);
});

test('cursor and createCursor are mutually exclusive; missing cursor is rejected', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  assert.throws(() => mountParcelHost(root, {
    cursor: { current: () => 1, subscribe: () => () => {} },
    createCursor: true,
  }), /either an external cursor or createCursor/);
  assert.throws(() => mountParcelHost(root), /needs a host cursor or createCursor/);
  assert.throws(() => mountParcelHost(root, { cursor: {} }), /subscribe/);
});

// ---------------------------------------------------------------------------
// Two-way selection synchronization
// ---------------------------------------------------------------------------

test('selection synchronizes both ways: view->store and store->view with no loop', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  // Minimal SelectionState (frontend/src/state/selection.ts shape).
  const listeners = new Set();
  let current = null;
  const selectionState = {
    get: () => current,
    select(target) { current = target; for (const l of [...listeners]) l(current); },
    clear() { current = null; for (const l of [...listeners]) l(current); },
    subscribe(l) { listeners.add(l); return () => listeners.delete(l); },
  };
  let changeCount = 0;
  const host = mountParcelHost(root, {
    selectionState, createCursor: true, sceneContext: SCENE_CTX,
    onSelectionChange: () => changeCount++,
  });
  host.pushSceneState(sceneState({ tick: 1, ns: 1e9, ids: IDS }));
  host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick: 1 }));
  // Forward: view pick -> store.
  host.selectTarget('uav.delivery.alpha');
  assert.deepEqual(selectionState.get(), { kind: 'entity', id: 'uav.delivery.alpha' });
  const afterForward = changeCount;
  // Reverse: store -> view, different target.
  selectionState.select({ kind: 'entity', id: 'parcel.p1042' });
  assert.equal(host.getSelection().entityId, 'parcel.p1042');
  // Same-target re-emit is a no-op: the view selection is unchanged and no
  // extra onSelectionChange emission happens.
  const before = changeCount;
  selectionState.select({ kind: 'entity', id: 'parcel.p1042' });
  assert.equal(changeCount, before, 'sameTarget no-op prevents echo');
  assert.equal(host.getSelection().entityId, 'parcel.p1042');
  // Forward again: view -> store updates the store.
  host.selectTarget('locker');
  assert.deepEqual(selectionState.get(), { kind: 'entity', id: 'locker' });
  assert.ok(changeCount >= afterForward);
  // A store target the frame cannot resolve is refused, not fabricated.
  selectionState.select({ kind: 'entity', id: 'ghost.id' });
  assert.equal(host.getSelection().entityId, 'locker', 'unresolvable target does not move the view');
  host.destroy();
});

test('HostSelectionStore enforces the SelectionState-like contract', () => {
  assert.throws(() => new HostSelectionStore({}), /SelectionState-like/);
  assert.throws(() => new HostSelectionStore(null), /SelectionState-like/);
});

// ---------------------------------------------------------------------------
// Evidence keyed by exact context (mount level), cross-run leak check
// ---------------------------------------------------------------------------

test('mount stores evidence per exact context; wrong-context evidence renders unknown', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  const host = mountParcelHost(root, { createCursor: true, sceneContext: SCENE_CTX });
  host.pushSceneState(sceneState({ tick: 1, ns: 1e9, ids: IDS }));
  host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick: 1 }));
  host.setRuleEvidence(ruleEvidence(EVIDENCE_CTX, true));
  assert.ok(root.textContent.includes('demo.delivery-link-degraded'));
  // A lifecycle switch to generation 99 (same run/epoch, new generation):
  // the scene is pushed with an explicit per-push context override.
  const gen99Ctx = { run_id: RUN, ...SCENE_CTX, generation: 99 };
  host.pushSceneState(sceneState({ tick: 2, ns: 2e9, ids: IDS }), gen99Ctx);
  host.activeCursor.goToTick(2);
  // (no gen-99 evidence supplied at all: unknown, never the gen-7 values)
  assert.ok(!root.textContent.includes('demo.delivery-link-degraded'), 'gen-7 rule never renders in the gen-99 scene');
  assert.ok(root.textContent.includes('未知') || root.textContent.includes('unknown'));
  // Supplying gen-99 evidence joins only the gen-99 scene; the gen-7 records
  // remain stored under their own context and never leak.
  host.setEvidence(frameEvidence(gen99Ctx, { tick: 2 }));
  host.setRuleEvidence(ruleEvidence(gen99Ctx, true));
  assert.ok(root.textContent.includes('demo.delivery-link-degraded'), 'matching-context rule renders');
  host.activeCursor.goToTick(1);
  assert.ok(root.textContent.includes('demo.delivery-link-degraded'), 'gen-7 rule still joins the gen-7 scene');
  host.destroy();
});

// ---------------------------------------------------------------------------
// Retained regressions from the first pass
// ---------------------------------------------------------------------------

test('ingested-scene presentation validates once and re-projects without revalidation', () => {
  const scene = ingestSceneState(sceneState({ tick: 1, ns: 1e9, ids: IDS }), SCENE_CTX);
  const evidence = ingestFrameEvidence(frameEvidence(EVIDENCE_CTX, { tick: 1 }));
  const rule = ingestRuleEvidence(ruleEvidence(EVIDENCE_CTX, true));
  // Project repeatedly from the same prebuilt indexes: pure state projection.
  const frames = [1, 2, 3].map(() => projectHostViewFrame(scene, evidence, rule));
  for (const frame of frames) {
    assert.equal(frame.parcels[0].state, 'on_uav');
    assert.equal(frame.rule.value, true);
  }
  assert.deepEqual(frames[0].hostContext, frames[2].hostContext);
});

test('declared parcel without a SceneState sample stays listed but unknown and unplaced', () => {
  const frame = buildHostViewFrame(
    sceneState({ tick: 1, ns: 1e9, ids: ['uav.delivery.alpha'] }),
    frameEvidence(EVIDENCE_CTX, { tick: 1 }),
    null,
    { sceneContext: SCENE_CTX },
  );
  assert.deepEqual(frame.unresolvedParcelIds, ['parcel.p1042']);
  const parcel = frame.parcels[0];
  assert.equal(parcel.position, null);
  assert.equal(parcel.custodian, null);
  assert.equal(parcel.custodyKnown, false);
  assert.equal(parcel.state, 'unknown');
  assert.equal(selectionFor(frame, 'parcel.p1042').entity.position, null);
});

test('custody is only what the host declares: proximity and contacts never produce it', () => {
  const scene = sceneState({ tick: 1, ns: 1e9, ids: ['uav.delivery.alpha'] });
  scene.samples[0].contacts = ['parcel.p1042'];
  const frame = buildHostViewFrame(scene,
    frameEvidence({ ...EVIDENCE_CTX, generation: 7 }, { tick: 1, custodian: null }), null,
    { sceneContext: SCENE_CTX });
  // The evidence carries no custodian; contacts on the sample grant nothing.
  assert.equal(frame.parcels[0].custodian, null);
  assert.equal(frame.parcels[0].custodyKnown, false);
});

test('custodian declared but not in the scene renders unknown custodian, still visible', () => {
  const frame = buildHostViewFrame(
    sceneState({ tick: 1, ns: 1e9, ids: ['uav.delivery.alpha', 'parcel.p1042'] }),
    frameEvidence(EVIDENCE_CTX, { tick: 1, custodian: 'ghost.holder' }),
    null,
    { sceneContext: SCENE_CTX },
  );
  const parcel = frame.parcels[0];
  assert.equal(parcel.custodian, null, 'unresolvable custodian shows unknown');
  assert.equal(parcel.custodianDeclared, 'ghost.holder', 'declared value stays visible');
  assert.equal(parcel.custodyKnown, false);
});

test('invalid evidence is rejected explicitly instead of being coerced', () => {
  assert.throws(() => buildHostViewFrame(
    sceneState({ tick: 1, ns: 1e9, ids: IDS }),
    { schema_version: HOST_FRAME_EVIDENCE_SCHEMA, context: EVIDENCE_CTX, evidence: { parcels: [{ id: 'parcel.p1042' }] } },
    null,
    { sceneContext: SCENE_CTX },
  ), /source\.pointer/);
  // Wrong envelope schema is rejected, never silently accepted.
  assert.throws(() => buildHostViewFrame(sceneState({ tick: 1, ns: 1e9, ids: IDS }),
    { schema_version: 'wrong.schema/v1', context: EVIDENCE_CTX, evidence: { parcels: [] } }, null,
    { sceneContext: SCENE_CTX },
  ), /frame evidence must use/);
  // Evidence without the required context block is rejected.
  assert.throws(() => ingestFrameEvidence({
    schema_version: HOST_FRAME_EVIDENCE_SCHEMA,
    evidence: { parcels: [] },
  }), /context/);
  // Non-object rule evidence is rejected.
  assert.throws(() => ingestRuleEvidence('not-an-envelope'), /rule evidence must use/);
  // Evidence context identity mismatch with a run is a non-join, not an error
  // (the evidence is simply for another context).
  const scene = ingestSceneState(sceneState({ tick: 1, ns: 1e9, ids: IDS }), SCENE_CTX);
  const other = ingestFrameEvidence(frameEvidence(
    { ...EVIDENCE_CTX, run_id: RUN_B }, { tick: 1 }));
  const frame = projectHostViewFrame(scene, other, null);
  assert.equal(frame.parcels[0].state, 'unknown');
});

test('attachment inconsistency projects as detached-with-flag instead of throwing', () => {
  // Parcel sample detached from its declared carrier: the projection flags it.
  const scene = sceneState({ tick: 1, ns: 1e9, ids: ['uav.delivery.alpha', 'parcel.p1042'] });
  scene.samples[1] = sample({ tick: 1, ns: 1e9, id: 'parcel.p1042', east: 99, north: 99, up: 1, ve: 0 });
  const frame = buildHostViewFrame(scene,
    frameEvidence(EVIDENCE_CTX, { tick: 1 }), null,
    { sceneContext: SCENE_CTX });
  const parcel = frame.parcels[0];
  assert.equal(parcel.attachment, null, 'inconsistent attachment is not drawn as attached');
  assert.equal(parcel.attachmentInconsistent, true, 'the inconsistency stays visible');
  assert.equal(parcel.state, 'on_uav', 'declared state still presents');
});

test('frame identity and BENCH context round-trip exactly (run/epoch/revision/frame/generation)', () => {
  const frame = buildHostViewFrame(
    sceneState({ tick: 7, ns: 7e9, ids: IDS }),
    frameEvidence(EVIDENCE_CTX, { tick: 7 }), ruleEvidence(EVIDENCE_CTX, true),
    { sceneContext: SCENE_CTX },
  );
  assert.equal(frame.runId, RUN);
  assert.equal(frame.epoch, 'bench.epoch.1');
  assert.equal(frame.manifestRevision, 'host.manifest.v3');
  assert.equal(frame.tick, 7);
  assert.equal(frame.simTimeNs, '7000000000');
  assert.equal(frame.timeSeconds, 7);
  assert.equal(frame.generation, 7);
  assert.equal(frame.hostContext.generationIsInteger, true);
  assert.equal(frame.hostContext.sceneStateDigest, hex(7, 7));
  const selection = selectionFor(frame, 'parcel.p1042');
  assert.equal(selection.runId, RUN);
  assert.equal(selection.epoch, 'bench.epoch.1');
  assert.equal(selection.manifestRevision, 'host.manifest.v3');
  assert.equal(selection.frameKey, frame.frameKey);
  assert.equal(selection.generation, 7);
  assert.equal(selection.entity.ref.run_id, RUN);
});

test('bilingual unknowns: custody, rule truth and Atlas result stay labelled in zh and en', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  const host = mountParcelHost(root, { createCursor: true, sceneContext: SCENE_CTX });
  host.pushSceneState(sceneState({ tick: 1, ns: 1e9, ids: IDS }));
  host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick: 1 })); // no rule evidence
  host.selectTarget('parcel.p1042');
  assert.ok(root.textContent.includes('未知') || root.textContent.includes('ruleUnknown'));
  host.setLanguage('en');
  assert.ok(root.textContent.includes('Unknown') || root.textContent.includes('unknown'));
  host.setRuleEvidence(ruleEvidence(EVIDENCE_CTX, false));
  assert.ok(root.textContent.includes('False'));
  // Missing identity note is visible when context is undeclared.
  host.refreshEvidence();
  host.destroy();
});

test('ingestion rejects contract violations exactly once, at ingest time', () => {
  // Duplicate Ref in one envelope.
  const parcel = {
    id: 'parcel.p1042', state: 'on_uav', custodian_id: 'uav.delivery.alpha',
    attachment: { carrier_id: 'uav.delivery.alpha', offset_enu: [0, 0, -0.8] },
    source: { pointer: 'demo' },
  };
  assert.throws(() => ingestFrameEvidence({
    schema_version: HOST_FRAME_EVIDENCE_SCHEMA,
    context: EVIDENCE_CTX,
    evidence: { parcels: [parcel, { ...parcel }] },
  }), /duplicate parcel Ref/);
  // Sample identity mismatch inside a SceneState.
  const bad = sceneState({ tick: 1, ns: 1e9, ids: IDS });
  bad.samples[2] = { ...bad.samples[2], run_id: RUN_B };
  assert.throws(() => ingestSceneState(bad, SCENE_CTX), /run\/scenario identity differs/);
  // Scene context bound to a different run is rejected at ingest.
  assert.throws(() => ingestSceneState(sceneState({ tick: 1, ns: 1e9, ids: IDS }),
    { ...SCENE_CTX, run_id: RUN_B }), /context run\/scenario identity differs/);
});

// ---------------------------------------------------------------------------
// PR12 reviewer fixes: source truth, explicit causal response, view surfaces
// ---------------------------------------------------------------------------

const { FEED_MOTION_DEMO, FEED_MOTION_BENCH } = await import('./adapter.js');

test('feedMotion is typed declared input: demo/bench pass through, anything else is UNKNOWN', () => {
  const scene = ingestSceneState(sceneState({ tick: 1, ns: 1e9, ids: IDS }), SCENE_CTX);
  const evidence = ingestFrameEvidence(frameEvidence(EVIDENCE_CTX, { tick: 1 }));
  const demoFrame = projectHostViewFrame(scene, evidence, null, { feedMotion: FEED_MOTION_DEMO });
  assert.equal(demoFrame.feedMotion, FEED_MOTION_DEMO);
  const benchFrame = projectHostViewFrame(scene, evidence, null, { feedMotion: FEED_MOTION_BENCH });
  assert.equal(benchFrame.feedMotion, FEED_MOTION_BENCH);
  // Undeclared or bogus values never become a real-looking identity.
  assert.equal(projectHostViewFrame(scene, evidence, null).feedMotion, UNKNOWN_IDENTITY);
  assert.equal(projectHostViewFrame(scene, evidence, null, { feedMotion: 'bench' + 'x' }).feedMotion, UNKNOWN_IDENTITY);
  // Parcel-evidence availability is its own flag, independent of motion.
  assert.equal(demoFrame.parcelEvidenceKnown, true);
  const empty = ingestFrameEvidence({
    schema_version: HOST_FRAME_EVIDENCE_SCHEMA,
    context: EVIDENCE_CTX,
    evidence: { parcels: [], stations: [], events: [] },
  });
  assert.equal(projectHostViewFrame(scene, empty, null, { feedMotion: FEED_MOTION_BENCH }).parcelEvidenceKnown, false);
});

test('response binds ONLY to an explicit causal reference; recent events never substitute', () => {
  const scene = ingestSceneState(sceneState({ tick: 44, ns: 44e9, ids: IDS }), SCENE_CTX);
  // The demo-shaped ledger: custody receipts at 6/29s, rule flip at 38s. No
  // event declares a causal reference, so the response stays unbound even
  // though events exist near the flip.
  const evidence = ingestFrameEvidence(frameEvidence(EVIDENCE_CTX, { tick: 44 }));
  const rule = ingestRuleEvidence(ruleEvidence(EVIDENCE_CTX, true));
  const frame = projectHostViewFrame(scene, evidence, rule);
  assert.equal(frame.rule.lastFlip, 38);
  assert.ok(frame.events.length >= 1, 'recent events exist');
  assert.equal(frame.ruleResponse, null, 'no inferred response without an explicit reference');
  // An event that explicitly references THIS rule flip binds.
  const explicit = ingestFrameEvidence({
    schema_version: HOST_FRAME_EVIDENCE_SCHEMA,
    context: EVIDENCE_CTX,
    evidence: {
      parcels: [], stations: [],
      events: [{
        event_id: 'e.hold.response', time_seconds: 39, label_key: 'wait_receipt', kind: 'response',
        entity_ids: ['parcel.p1042'],
        response_rule_id: 'demo.delivery-link-degraded', response_flip_time_seconds: 38,
      }],
    },
  });
  const bound = projectHostViewFrame(scene, explicit, rule);
  assert.equal(bound.ruleResponse?.id, 'e.hold.response', 'explicit reference binds the response');
  // A reference to a DIFFERENT rule or flip time does not bind.
  const wrongFlip = ingestFrameEvidence({
    schema_version: HOST_FRAME_EVIDENCE_SCHEMA,
    context: EVIDENCE_CTX,
    evidence: {
      parcels: [], stations: [],
      events: [{
        event_id: 'e.other.flip', time_seconds: 39, label_key: 'wait_receipt', kind: 'response',
        entity_ids: ['parcel.p1042'],
        response_rule_id: 'demo.delivery-link-degraded', response_flip_time_seconds: 54,
      }],
    },
  });
  assert.equal(projectHostViewFrame(scene, wrongFlip, rule).ruleResponse, null, 'flip mismatch does not bind');
  // A malformed explicit reference is rejected at ingest, not coerced.
  assert.throws(() => ingestFrameEvidence({
    schema_version: HOST_FRAME_EVIDENCE_SCHEMA,
    context: EVIDENCE_CTX,
    evidence: {
      parcels: [], stations: [],
      events: [{ event_id: 'e.bad', time_seconds: 1, label_key: 'x',
        entity_ids: ['parcel.p1042'], response_flip_time_seconds: 'soon' }],
    },
  }), /response_flip_time_seconds/);
});

test('view: response node shows the unbound label without an explicit causal reference', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  const host = mountParcelHost(root, { createCursor: true, sceneContext: SCENE_CTX });
  host.pushSceneState(sceneState({ tick: 44, ns: 44e9, ids: IDS }));
  host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick: 44 }));
  host.setRuleEvidence(ruleEvidence(EVIDENCE_CTX, true));
  assert.ok(root.textContent.includes('响应未绑定'), 'zh unbound response label renders');
  host.setLanguage('en');
  assert.ok(root.textContent.includes('Response unbound'), 'en unbound response label renders');
  host.destroy();
});

test('view: attachment shows the carrier id once when its display name equals the id', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  const host = mountParcelHost(root, { createCursor: true, sceneContext: SCENE_CTX });
  host.pushSceneState(sceneState({ tick: 1, ns: 1e9, ids: IDS }));
  host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick: 1 }));
  host.selectTarget('parcel.p1042');
  const dd = [...root.querySelectorAll('.state-fields dd')];
  const occurrences = dd.map(node => node.textContent).filter(text => text.includes('uav.delivery.alpha'));
  // The custody row shows the custodian id; the attachment row must show the
  // carrier exactly once (no "uav.delivery.alpha · uav.delivery.alpha").
  for (const text of occurrences) {
    assert.ok(!text.includes('uav.delivery.alpha · uav.delivery.alpha'), 'no duplicated id pair');
  }
  assert.ok(occurrences.some(text => text === 'uav.delivery.alpha'), 'single plain-id rendering present');
  host.destroy();
});

test('view: motion badge text is localized, never the raw motionSourceMixed key', () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  const host = mountParcelHost(root, { createCursor: true, sceneContext: SCENE_CTX });
  host.pushSceneState(sceneState({ tick: 44, ns: 44e9, ids: IDS }));
  host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick: 44 }));
  host.view.setFeedMotion(FEED_MOTION_DEMO);
  assert.ok(root.textContent.includes('运动：演示样本（demo.motion）'), 'zh localized demo motion badge');
  assert.ok(root.textContent.includes('包裹证据：演示（未接入真实来源）'), 'parcel fixture source remains separate');
  assert.ok(!root.textContent.includes('motionSourceMixed'), 'raw key never displayed');
  host.setLanguage('en');
  assert.ok(root.textContent.includes('Motion: demo samples'), 'en localized demo badge');
  host.view.setFeedMotion(FEED_MOTION_BENCH);
  assert.ok(root.textContent.includes('Motion: real BENCH SceneState'), 'en localized bench badge');
  host.destroy();
});

test('view: screen-space label chips cover every drawable id on narrow screens (jsdom)', () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>');
  const win = dom.window;
  // jsdom lacks matchMedia; provide the narrow-screen answer deterministically.
  win.matchMedia = query => ({ matches: query.includes('max-width:850px'), media: query });
  const root = win.document.getElementById('root');
  // getBoundingClientRect is all-zero in jsdom; the chips still render with
  // computed positions (verification here is coverage, not pixel truth).
  const host = mountParcelHost(root, { createCursor: true, sceneContext: SCENE_CTX });
  host.pushSceneState(sceneState({ tick: 44, ns: 44e9, ids: IDS }));
  host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick: 44 }));
  const chips = [...root.querySelectorAll('.scene-label-chip')];
  const chipIds = chips.map(chip => chip.dataset.select);
  for (const id of [...IDS, 'locker']) {
    assert.ok(chipIds.includes(id), `chip present for ${id}`);
  }
  assert.ok(chips.every(chip => chip.getAttribute('aria-pressed') !== null), 'chips expose selection state');
  const parcelChip = chips.find(chip => chip.dataset.select === 'parcel.p1042');
  assert.ok(parcelChip?.textContent.includes('parcel.p1042'), 'parcel chip shows the stable id');
  host.destroy();
});


test('an explicit response cannot precede the trigger it references', () => {
  const scene = ingestSceneState(sceneState({ tick: 44, ns: 44e9, ids: IDS }), SCENE_CTX);
  const rule = ingestRuleEvidence(ruleEvidence(EVIDENCE_CTX, true));
  const evidence = ingestFrameEvidence({
    schema_version: HOST_FRAME_EVIDENCE_SCHEMA, context: EVIDENCE_CTX,
    evidence: { parcels: [], stations: [], events: [{
      event_id: 'early.claim', time_seconds: 29, label_key: 'receipt_uav',
      kind: 'custody', entity_ids: ['parcel.p1042'],
      response_rule_id: 'demo.delivery-link-degraded', response_flip_time_seconds: 38,
    }] },
  });
  assert.equal(projectHostViewFrame(scene, evidence, rule).ruleResponse, null);
});

test('clearing selection and empty frames render without a null-item crash', async () => {
  const dom = new JSDOM('<div id="root"></div>');
  const root = dom.window.document.getElementById('root');
  const host = mountParcelHost(root, { createCursor: true, sceneContext: SCENE_CTX });
  host.pushSceneState(sceneState({ tick: 44, ns: 44e9, ids: IDS }));
  host.setEvidence(frameEvidence(EVIDENCE_CTX, { tick: 44 }));
  assert.doesNotThrow(() => host.view.clearSelection());
  assert.equal(host.view.getSelection(), null);
  host.destroy();
  const { mountParcelView } = await import('./view.js');
  const view = mountParcelView(root);
  assert.doesNotThrow(() => view.setFrame({
    parcels: [], entities: [], events: [], script: [], unresolvedParcelIds: [],
    timeSeconds: 0, frameKey: 'empty', rule: null,
  }));
  view.destroy();
});
