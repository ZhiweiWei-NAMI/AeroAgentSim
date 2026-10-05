// Executable, isolated mounting example. Two clearly separated evidence
// sources feed the SAME host mount:
//
// 1. Real BENCH motion — when the page is served with ?source=<sealed replay
//    JSON>, demo.js imports the read-only BENCH frontend modules
//    (trace.ts + view-model.ts through their compiled equivalents) in the
//    browser, verifies the sealed trace digests with the frontend's own
//    verified-bytes/sha256 path, and pushes real SceneStates into the host.
//    This is real BENCH motion, not authored data.
// 2. Demo parcel/custody/rule evidence — a scripted authored feed, labelled
//    everywhere as demo host evidence. It exists because the BENCH public
//    contract publishes no parcel sample kind: the demo keeps the production
//    parcel input explicitly unavailable while making the panel testable.
//
// Attribution: replay loading follows the interface of
// frontend/src/trace.ts (parsePublicTraceBytes, SHA-256 verification) and
// frontend/src/view-model.ts (sceneStateAtTick, simTimeAtTick, recordedTicks)
// of the AERO-BENCH workspace.
import { mountParcelHost } from './mount.js';
import { translator } from './i18n.js';

const APP_RUN_ID = 'a'.repeat(64);
const APP_SCENARIO = 'b'.repeat(64);

function contractId(value) { return value; } // demo ids already satisfy ^[a-z][a-z0-9_.-]*$

function digest64(n, seed = 0) {
  // Demo-only deterministic hex filler for required contract digest fields.
  const hex = '0123456789abcdef';
  let out = '';
  for (let i = 0; i < 64; i++) out += hex[(seed + i * (n + 3)) % 16];
  return out;
}

function sceneState({ tick, simTimeNs, entities }) {
  return {
    schema_version: 'aero-bench.scene-state/v1',
    run_id: APP_RUN_ID,
    scenario_digest: APP_SCENARIO,
    at: { tick, sim_time_ns: simTimeNs },
    declared_entity_ids: entities.map(e => contractId(e.entity_id)),
    samples: entities.map((e, i) => ({
      schema_version: 'aero-bench.state-sample/v1',
      run_id: APP_RUN_ID,
      scenario_digest: APP_SCENARIO,
      at: { tick, sim_time_ns: simTimeNs },
      stage: 'motion',
      entity_id: contractId(e.entity_id),
      provider_id: 'demo.motion',
      sample_kind: e.kind === 'facility' ? 'static' : 'dynamic',
      pose: {
        position: {
          enu: { east_m: e.east, north_m: e.north, up_m: e.up },
        },
      },
      linear_velocity_enu: { east_mps: e.ve, north_mps: e.vn, up_mps: e.vu },
      sample_digest: digest64(tick, i),
    })),
    stage_barrier: {
      schema_version: 'aero-bench.stage-barrier/v1',
      run_id: APP_RUN_ID,
      scenario_digest: APP_SCENARIO,
      at: { tick, sim_time_ns: simTimeNs },
      stage: 'motion',
      provider_ids: ['demo.motion'],
      receipts: [],
      receipt_digests: [],
      barrier_digest: digest64(tick, 7),
    },
    contribution_digests: [],
    previous_scene_state_digest: digest64(tick - 1, 11),
    scene_state_digest: digest64(tick, 13),
  };
}

const STATIONS = [
  { id: 'warehouse', label_key: 'warehouse', position_enu: [13, 17, 0] },
  { id: 'pad', label_key: 'pad', position_enu: [56, 26, 0] },
  { id: 'locker', label_key: 'locker', position_enu: [111, 42, 0] },
];

function demoMotion(t) {
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const groundX = clamp(18 + (t - 6) * 2.2, 18, 52);
  const airX = t < 29 ? 59 : clamp(59 + (t - 29) * 1.9, 59, 109);
  const airY = t < 29 ? 27 : 27 + clamp((t - 29) * 0.4, 0, 15);
  const airU = t < 29 ? 2 : clamp(2 + (t - 29) * 0.9, 2, 14);
  const ugvState = t >= 6 && t < 19 ? 'travelling' : 'idle';
  const uavState = t >= 39 && t < 49 ? 'holding' : t >= 68 && t < 73 ? 'descending' : t >= 29 && t < 73 ? 'flying' : 'idle';
  // Parcel position honours the declared carrier offsets exactly:
  // ugv.delivery.alpha + [0,0,+1.4] on the ground, uav.delivery.alpha + [0,0,-0.8] in the air.
  const parcelEast = t < 29 ? groundX : airX;
  const parcelNorth = t < 29 ? 20 : airY;
  const parcelUp = t < 6 ? 1.2 : t < 29 ? 1.4 : airU - 0.8;
  return [
    { entity_id: 'warehouse', kind: 'facility', east: 13, north: 17, up: 0, ve: 0, vn: 0, vu: 0 },
    { entity_id: 'pad', kind: 'facility', east: 56, north: 26, up: 0, ve: 0, vn: 0, vu: 0 },
    { entity_id: 'locker', kind: 'facility', east: 111, north: 42, up: 0, ve: 0, vn: 0, vu: 0 },
    { entity_id: 'ugv.delivery.alpha', kind: 'ugv', east: groundX, north: 20, up: 0, ve: t >= 6 && t < 19 ? 2.2 : 0, vn: 0, vu: 0 },
    { entity_id: 'uav.delivery.alpha', kind: 'uav', east: airX, north: airY, up: airU, ve: t >= 29 && t < 73 ? 1.9 : 0, vn: t >= 29 && t < 73 ? 0.4 : 0, vu: t >= 29 && t < 34 ? 0.9 : 0 },
    { entity_id: 'parcel.p1042', kind: 'parcel', east: parcelEast, north: parcelNorth, up: parcelUp, ve: 0, vn: 0, vu: 0 },
  ];
}

/**
 * Exact typed context for all demo evidence. The demo scene context is
 * declared explicitly too (the PR10 provider-mapper role), so both sides of
 * every Ref join carry actual identity — never synthesized.
 */
const DEMO_SCENE_CONTEXT = { epoch: 'demo.epoch.1', generation: 1, revision: 'demo.manifest.v1' };
const DEMO_EVIDENCE_CONTEXT = {
  run_id: APP_RUN_ID,
  epoch: DEMO_SCENE_CONTEXT.epoch,
  generation: DEMO_SCENE_CONTEXT.generation,
  revision: DEMO_SCENE_CONTEXT.revision,
};

/**
 * Demo parcel/custody/rule evidence, refreshed per tick. Everything here is
 * demo host evidence: authority 'host-demo-evidence', pointer prefixed
 * demo.parcels / demo.rule. Missing custody stays null on purpose so the
 * unknown path is exercised.
 */
function demoEvidence(tick, t) {
  const onUav = t >= 29 && t < 73;
  const delivered = t >= 78;
  const rssi = t >= 38 && t < 54 ? -98 : -62;
  const deliveryPhase = t >= 29 && t < 78;
  const parcels = [{
    id: 'parcel.p1042',
    state: delivered ? 'delivered' : onUav ? (t >= 39 && t < 49 ? 'holding' : t >= 49 ? 'replanned' : 'on_uav') : t >= 6 ? 'on_ugv' : 'loading_ugv',
    custodian_id: t < 6 ? null : t < 24 ? 'ugv.delivery.alpha' : t < 29 ? null : onUav ? 'uav.delivery.alpha' : delivered ? 'locker' : null,
    attachment: (t >= 6 && t < 24) ? { carrier_id: 'ugv.delivery.alpha', offset_enu: [0, 0, 1.4] }
      : onUav ? { carrier_id: 'uav.delivery.alpha', offset_enu: [0, 0, -0.8] } : null,
    transfer: t >= 3 && t < 6 ? { from_id: 'warehouse', to_id: 'ugv.delivery.alpha', progress: (t - 3) / 3 } : null,
    dimensions_m: [0.45, 0.32, 0.26],
    mass_kg: 1.8,
    target: 'locker',
    source: { pointer: 'demo.parcels[parcel.p1042]', authority: 'host-demo-evidence', frameId: `demo.frame.${tick}` },
  }];
  const rule = {
    at_tick: tick,
    rule_id: 'demo.delivery-link-degraded',
    truth: deliveryPhase && rssi < -90,
    inputs: { delivery_phase: deliveryPhase, rssi_dbm: rssi, threshold_dbm: -90 },
    input_source: 'demo scripted feed',
    engine: 'host-demo',
    last_flip_time_seconds: t >= 54 ? 54 : t >= 38 ? 38 : null,
    source_pointer: 'demo.rule[demo.delivery-link-degraded]',
  };
  const network = {
    link_id: 'link.delivery',
    rssi_dbm: rssi,
    degraded: rssi < -90,
    source_pointer: 'demo.network[link.delivery]',
  };
  // Separately typed rule evidence (p02.parcel-host.rule-evidence/v2) with an
  // exact context; the host refreshes it per tick through host.setRuleEvidence.
  const ruleEnvelope = {
    schema_version: 'p02.parcel-host.rule-evidence/v2',
    context: DEMO_EVIDENCE_CONTEXT,
    evidence: rule,
  };
  const frameEnvelope = {
    schema_version: 'p02.parcel-host.frame-evidence/v2',
    context: DEMO_EVIDENCE_CONTEXT,
    evidence: {
      at_tick: tick,
      parcels,
      stations: STATIONS,
      routes: {
        ground: [[18, 18, 0], [27, 18, 0], [27, 24, 0], [52, 24, 0]],
        air: [[59, 27, 14], [74, 38, 14], [109, 42, 14]],
      },
      active_route: 'air',
      network,
      events: [{
        event_id: 'demo.receipt.ugv', time_seconds: 6, label_key: 'receipt_ugv', kind: 'custody',
        entity_ids: ['parcel.p1042', 'ugv.delivery.alpha'], from_id: 'warehouse', to_id: 'ugv.delivery.alpha',
      }, {
        event_id: 'demo.receipt.uav', time_seconds: 29, label_key: 'receipt_uav', kind: 'custody',
        entity_ids: ['parcel.p1042', 'uav.delivery.alpha'], from_id: null, to_id: 'uav.delivery.alpha',
      }].filter(e => e.time_seconds <= t),
    },
  };
  return { frameEnvelope, ruleEnvelope };
}

function kindFor(id) {
  if (id === 'ugv.delivery.alpha') return 'ugv';
  if (id === 'uav.delivery.alpha') return 'uav';
  if (id === 'parcel.p1042') return 'parcel';
  return 'facility';
}

/** Minimal browser SelectionState (frontend/src/state/selection.ts shape). */
class DemoSelectionState {
  constructor() { this.value = null; this.listeners = new Set(); }
  get() { return this.value; }
  isSelected(target) { return this.value?.id === target?.id && this.value?.kind === target?.kind; }
  select(target) { this.value = target; for (const l of [...this.listeners]) l(this.value); }
  clear() { this.value = null; for (const l of [...this.listeners]) l(this.value); }
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
}

const language = new URLSearchParams(location.search).get('lang') === 'en' ? 'en' : 'zh';
const t = translator(language);

// ---------------------------------------------------------------------
// Mount (host API under exercise).
// ---------------------------------------------------------------------
const selectionState = new DemoSelectionState();
selectionState.subscribe(target => {
  document.getElementById('selection-json').textContent =
    target === null ? '—' : JSON.stringify(target);
});
const host = mountParcelHost(document.getElementById('view'), {
  createCursor: true,
  selectionState,
  language,
  entityKind: kindFor,
  parcelLabel: id => (id === 'parcel.p1042' ? 'P-1042 · demo' : id),
  entityLabel: id => (id.endsWith('.alpha') ? id : t(id)),
  sceneContext: DEMO_SCENE_CONTEXT,
  onSelectionChange(selection) {
    if (!selection) return;
    document.getElementById('selection-context').textContent =
      `${selection.runId.slice(0, 8)} · ${selection.frameKey.split(':').at(-1)} · gen ${selection.generation ?? '—'}`;
  },
});

// ---------------------------------------------------------------------
// Feed A (default): demo SceneStates + demo evidence, clearly labelled.
// The demo timeline includes tick 0 and mirrors the fixture's second scale
// (84 one-second states), so the authored event windows (6/29/38/49/54/78 s)
// stay at their authored positions. Each state is one exact recorded sample.
const DEMO_TICKS = Array.from({ length: 85 }, (_, i) => i); // ticks 0..84 = 1 s steps

function pushDemoTimeline() {
  for (const tick of DEMO_TICKS) {
    const tSeconds = tick;
    host.pushSceneState(sceneState({
      tick,
      simTimeNs: tick * 1_000_000_000,
      entities: demoMotion(tSeconds),
    }));
    const { frameEnvelope, ruleEnvelope } = demoEvidence(tick, tSeconds);
    host.setEvidence(frameEnvelope);
    host.setRuleEvidence(ruleEnvelope);
  }
}

// ---------------------------------------------------------------------
// Feed B: real BENCH motion from a sealed public replay (?source=...).
// Parcel/custody/Atlas evidence stays explicitly unavailable in this mode:
// the public contract publishes none of it, so nothing is invented here.
// The empty frame evidence carries the real trace's run identity so the
// evidence context stays exact; epoch/generation remain undeclared (UNKNOWN).
// ---------------------------------------------------------------------
async function loadRealReplay(url) {
  const traceMod = await import('/@bench/trace.ts');
  const { parsePublicTraceBytes } = traceMod;
  const bytes = await (await fetch(url)).arrayBuffer();
  const trace = parsePublicTraceBytes(bytes);
  const ticks = [...new Set(trace.scene_states.map(s => s.at.tick))].sort((a, b) => a - b);
  const byTick = new Map(trace.scene_states.map(s => [s.at.tick, s]));
  for (const tick of ticks) {
    const state = byTick.get(tick);
    host.pushSceneState(state);
    // No parcel/rule evidence exists in the public contract: leave the
    // production parcel input unavailable and the panels unknown.
    host.setEvidence({
      schema_version: 'p02.parcel-host.frame-evidence/v2',
      context: { run_id: trace.run_id, epoch: 'UNKNOWN', generation: 'UNKNOWN', revision: 'UNKNOWN' },
      evidence: { parcels: [], stations: [], events: [] },
    });
  }
  document.getElementById('source-mode').textContent =
    language === 'zh' ? `来源：真实 BENCH 回放 · ${ticks.length} 帧 · run ${trace.run_id.slice(0, 8)}` : `Source: real BENCH replay · ${ticks.length} frames · run ${trace.run_id.slice(0, 8)}`;
}

// ---------------------------------------------------------------------
// Transport UI: the single authoritative cursor is host.cursor.
// ---------------------------------------------------------------------
const timeline = document.getElementById('timeline');
const playButton = document.getElementById('play');
const timeValue = document.getElementById('time-value');
const tickValue = document.getElementById('tick-value');

function cursorChanged() {
  const cursor = host.activeCursor;
  const ns = cursor.currentSimTimeNs?.() ?? null;
  const index = cursor.currentIndex?.() ?? null;
  if (ns !== null && ns !== undefined) {
    const seconds = ns / 1e9;
    timeValue.textContent = `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;
    if (index !== null && index !== undefined) timeline.value = String(index);
    const tick = cursor.currentTick?.() ?? cursor.current?.() ?? null;
    tickValue.textContent = `${t('tick')} ${tick ?? '—'} / ${cursor.length ?? '—'}`;
  }
  const playing = cursor.isPlaying?.() ?? false;
  playButton.textContent = playing ? `Ⅱ ${t('pause')}` : `▶ ${t('play')}`;
}
// UI transport controls drive only an OWNED cursor; an externally supplied
// cursor is displayed but never driven from here (its owner controls it).
if (host.ownsCursor) {
  host.cursor.subscribe(cursorChanged);
  playButton.addEventListener('click', () => host.cursor.togglePlay());
  document.getElementById('reset').addEventListener('click', () => host.cursor.seek(0));
  timeline.addEventListener('input', e => host.cursor.seek(Number(e.target.value)));
} else {
  host.activeCursor.subscribe(cursorChanged);
  playButton.disabled = true;
  document.getElementById('reset').disabled = true;
  timeline.disabled = true;
}
document.querySelectorAll('[data-language]').forEach(button =>
  button.addEventListener('click', () => {
    const url = new URL(location.href);
    url.searchParams.set('lang', button.dataset.language);
    location.assign(url);
  }));

document.getElementById('fixture-note').textContent = t('fixtureNote');
document.getElementById('app-title').textContent = t('title');
document.getElementById('app-subtitle').textContent = t('subtitle');
document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';

// Boot: default to the demo feed; ?source=<replay> switches to real motion.
const sourceUrl = new URLSearchParams(location.search).get('source');
if (sourceUrl) {
  loadRealReplay(sourceUrl).catch(error => {
    document.getElementById('source-mode').textContent =
      language === 'zh' ? `真实回放加载失败：${error.message}（演示证据继续可用）` : `Real replay failed to load: ${error.message} (demo evidence continues)`;
    pushDemoTimeline();
  });
} else {
  pushDemoTimeline();
  document.getElementById('source-mode').textContent =
    language === 'zh' ? '来源：演示包裹证据（非真实 BENCH 数据）· 运动为演示样本' : 'Source: demo parcel evidence (not real BENCH data) · motion is demo-sampled';
}
cursorChanged();
window.__parcelHost = host; // test hook
