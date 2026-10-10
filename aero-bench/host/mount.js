// Owned host mounting layer — PR10 revision.
//
// Attribution: interaction patterns (pick, follow, seek callbacks) are adapted
// from the P02 parcel prototype
// (validation/p02-parcel-host/source @ 15f473a4dc0ed4f80e3000b0acef6e875c617393,
// files src/app.js and src/view.js, Apache-2.0 per the checkout's LICENSE).
// The external-cursor contract follows frontend/src/state/replay.ts
// (ReplayState: current() -> tick, currentIndex(), subscribe() -> Unsubscribe,
// dispose()); the two-way selection contract follows
// frontend/src/state/selection.ts (SelectionState: select/get/subscribe/clear).
//
// Responsibilities:
// - Accept an EXISTING host cursor (ReplayState-like). The host owns and
//   disposes it; this mount never stops, plays, rewires or disposes it. A
//   legacy internal cursor can still be created with { createCursor: true }.
// - Synchronize selection BOTH ways: view pick -> SelectionState.select, and
//   external SelectionState.subscribe -> view selection, with loop suppression
//   and same-target no-ops (sameTarget semantics).
// - Ingest every supplied record ONCE (full validation + exact-key indexes in
//   adapter.ingest*); per-tick presentation only projects state/availability
//   over the prebuilt indexes (adapter.projectHostViewFrame). Code-level
//   checks stay out of the presentation hot path.
// - Evidence is keyed by exact run/epoch/revision/typed-Ref context; a tick
//   without its own envelope renders absent data (unknown), never another
//   record's values.
import {
  ingestSceneState, ingestFrameEvidence, ingestRuleEvidence,
  projectHostViewFrame, selectionFor,
} from './adapter.js';
import { mountParcelView } from './view.js';
import { translator } from './i18n.js';

/**
 * Minimal host cursor with the same presentation policy as
 * frontend/src/state/replay.ts (10 Hz sampling, exact recorded times only).
 * Used only when the caller asks for an internally owned cursor; the default
 * mount accepts an external one instead.
 */
export class HostReplayCursor {
  constructor({ intervalMs = 100 } = {}) {
    this.intervalMs = intervalMs;
    this.timesNs = [];
    this.ticks = [];
    this.index = -1;
    this.playing = false;
    this.speed = 1;
    this.timer = null;
    this.listeners = new Set();
    this.anchorSimNs = 0;
    this.anchorWallMs = 0;
  }

  /** ticks and timesNs are parallel recorded timelines; ticks are explicit. */
  setTimeline(ticks, timesNs) {
    if (ticks.length !== timesNs.length) {
      throw new TypeError('Host cursor requires one sim time per tick');
    }
    for (let i = 0; i < timesNs.length; i++) {
      if (!Number.isSafeInteger(timesNs[i]) || timesNs[i] < 0 ||
          (i > 0 && timesNs[i] <= timesNs[i - 1])) {
        throw new TypeError('Host cursor requires strictly increasing nonnegative sim_time_ns values');
      }
    }
    this.timesNs = [...timesNs];
    this.ticks = [...ticks];
    this.index = timesNs.length ? 0 : -1;
    this.pause();
    this.emit();
  }

  get length() { return this.timesNs?.length ?? 0; }
  currentIndex() { return this.index; }
  /** The recorded tick identity under the cursor, or null before data. */
  currentTick() { return this.index >= 0 ? this.ticks[this.index] ?? null : null; }
  /** ReplayState-compatible alias for the recorded tick under the cursor. */
  current() { return this.currentTick(); }
  currentSimTimeNs() { return this.index >= 0 && this.timesNs ? this.timesNs[this.index] : null; }
  isPlaying() { return this.playing; }

  seek(index) {
    if (!this.length) return;
    const next = Math.max(0, Math.min(this.timesNs.length - 1, index));
    if (next === this.index) return;
    this.index = next;
    this.reanchor();
    this.emit();
  }

  /** ReplayState-compatible: seek to the recorded tick identity. */
  goToTick(tick) {
    const index = this.ticks.indexOf(tick);
    if (index !== -1) this.seek(index);
  }

  step(delta) { this.seek(this.index + delta); }

  play() {
    if (!this.length || this.playing) return;
    if (this.index >= this.timesNs.length - 1) this.seek(0);
    this.playing = true;
    this.reanchor();
    this.schedule();
    this.emit();
  }

  pause() {
    if (this.playing) this.advance(Date.now());
    if (this.timer !== null) { clearTimeout(this.timer); this.timer = null; }
    this.playing = false;
  }

  togglePlay() { if (this.playing) this.pause(); else this.play(); }

  setSpeed(speed) {
    if (!Number.isFinite(speed) || speed <= 0) throw new TypeError('Host cursor speed must be a positive finite number');
    this.speed = speed;
    this.reanchor();
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  dispose() { this.pause(); this.listeners.clear(); this.timesNs = []; this.ticks = []; this.index = -1; }

  reanchor() {
    this.anchorSimNs = this.currentSimTimeNs() ?? 0;
    this.anchorWallMs = Date.now();
  }

  advance(now) {
    if (!this.length) return;
    const last = this.timesNs.length - 1;
    const startMs = this.anchorSimNs / 1e6;
    const target = Math.min(this.timesNs[last] / 1e6, startMs + ((now - this.anchorWallMs) / 1) * this.speed);
    // Exact recorded times only: binary search, never interpolate between
    // recorded ticks (same policy as frontend/src/state/replay.ts).
    let low = 0, high = last;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (this.timesNs[middle] / 1e6 <= target) low = middle; else high = middle - 1;
    }
    if (this.index !== low) { this.index = low; this.emit(); }
  }

  schedule() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.advance(Date.now()); this.schedule(); }, this.intervalMs);
  }

  emit() { for (const listener of [...this.listeners]) listener(this.currentTick()); }
}

/** Read one tick out of a host cursor, ReplayState-like or HostReplayCursor. */
function readCursorTick(cursor) {
  if (typeof cursor.current === 'function') {
    const tick = cursor.current();
    return tick === null || tick === undefined ? null : tick;
  }
  return null;
}

/**
 * Minimal host-selection binding over the agreed TraceTarget shape
 * (frontend/src/state/target.ts). Only the 'entity' kind is produced here.
 */
export class HostSelectionStore {
  constructor(selectionState) {
    if (!selectionState || typeof selectionState.select !== 'function' ||
        typeof selectionState.subscribe !== 'function') {
      throw new TypeError('HostSelectionStore needs a SelectionState-like store (frontend/src/state/selection.ts)');
    }
    this.selectionState = selectionState;
  }

  targetOf(selection) {
    return { kind: 'entity', id: selection.entityId };
  }

  /** Same-target semantics: identical kind+id is a no-op, never a re-select. */
  sameTarget(a, b) {
    if (a === b) return true;
    if (a === null || b === null) return false;
    return a.kind === b.kind && a.id === b.id;
  }

  applySelection(selection) {
    if (selection === null) { this.selectionState.clear(); return null; }
    const target = this.targetOf(selection);
    this.selectionState.select(target);
    return target;
  }
}

/**
 * Mount the parcel host.
 *
 * cursor (recommended): an existing ReplayState-like host cursor. The mount
 *   subscribes to it and mirrors its position; it never plays, pauses or
 *   disposes it. The caller keeps ownership.
 * createCursor: when true (and no cursor is given), the mount creates and
 *   owns an internal HostReplayCursor, exposed as host.cursor and disposed by
 *   host.destroy().
 * selectionState: an existing SelectionState-like store. Selection is
 *   synchronized both ways with loop suppression.
 */
export function mountParcelHost(root, {
  cursor = null,
  createCursor = false,
  selectionState = null,
  language = 'zh',
  intervalMs = 100,
  onSelectionChange = () => {},
  entityKind = () => 'facility',
  entityLabel = null,
  parcelLabel = null,
  manifestRevision = null,
  sceneContext = null,
} = {}) {
  if (cursor && createCursor) {
    throw new TypeError('mountParcelHost accepts either an external cursor or createCursor, not both');
  }
  const selectionStore = selectionState ? new HostSelectionStore(selectionState) : null;
  const t = translator(language);
  let currentScene = null;        // ingested scene under the cursor
  const scenesByTick = new Map(); // tick -> ingested scene (validated + indexed once)
  // Per-context evidence stores. Because one context (run/epoch/generation)
  // legitimately carries a COMMIT SERIES (one envelope per at_tick, exactly
  // like PR10's per-query commits), each store keeps the full envelope series
  // ordered by at_tick; presentation resolves the latest commit at or before
  // the presented scene's tick. Evidence from a later commit never leaks
  // backwards; a tick before the first commit has absent data (unknown).
  const evidenceByContextKey = new Map(); // contextKey -> Map(at_tick -> ingested)
  const ruleByContextKey = new Map();     // contextKey -> Map(at_tick -> ingested)
  let staleReason = null;
  let presenting = false;
  let unsubscribeCursor = null;
  let unsubscribeSelection = null;

  const ownedCursor = createCursor ? new HostReplayCursor({ intervalMs }) : null;
  const activeCursor = cursor ?? ownedCursor;
  if (!activeCursor) {
    throw new TypeError('mountParcelHost needs a host cursor or createCursor: true');
  }
  if (typeof activeCursor.subscribe !== 'function') {
    throw new TypeError('host cursor must expose subscribe(listener) -> Unsubscribe');
  }

  let suppressReverse = false;
  const view = mountParcelView(root, {
    language,
    entityKind,
    onSelectionChange(selection) {
      // View -> SelectionState direction with loop suppression: the flag is
      // raised while the store is written and lowered after the synchronous
      // reverse emissions settle.
      if (selectionStore && selection) {
        suppressReverse = true;
        try {
          selectionStore.applySelection(selection);
        } finally {
          suppressReverse = false;
        }
      }
      onSelectionChange(selection);
    },
    onSeek(seconds) {
      // Seconds-based seeks (workflow buttons) map onto the owned cursor's
      // recorded sim times; an external cursor is driven only by its owner.
      const times = ownedCursor?.timesNs ?? null;
      if (!times || !times.length) return;
      const targetNs = seconds * 1e9;
      let best = 0, bestDistance = Infinity;
      for (const [index, ns] of times.entries()) {
        const distance = Math.abs(ns - targetNs);
        if (distance < bestDistance) { best = index; bestDistance = distance; }
      }
      ownedCursor.seek(best);
    },
  });

  function present() {
    if (presenting) return; // no nested render from cursor emissions
    presenting = true;
    try {
      const tick = readCursorTick(activeCursor);
      currentScene = tick !== null ? scenesByTick.get(tick) ?? null : null;
      if (currentScene === null) {
        staleReason = tick === null ? 'no-tick' : 'no-scene-for-tick';
        view.setFrame(null);
        root.textContent = t('hostEmpty');
        return;
      }
      const sceneKey = currentScene.contextKey;
      // Commit-series resolution: within the scene's exact context, the
      // latest evidence envelope whose at_tick <= the presented tick. Later
      // commits never leak backwards; missing commits render unknown.
      const frameIngest = latestCommitAtOrBefore(evidenceByContextKey.get(sceneKey) ?? null, currentScene.tick);
      const ruleIngest = latestCommitAtOrBefore(ruleByContextKey.get(sceneKey) ?? null, currentScene.tick);
      staleReason = frameIngest === null && evidenceByContextKey.size ? 'evidence-not-for-this-context' : null;
      // Presentation projects prebuilt indexes only: state + availability.
      const frame = projectHostViewFrame(currentScene, frameIngest, ruleIngest, {
        entityKind,
        entityLabel: entityLabel ?? undefined,
        parcelLabel: parcelLabel ?? undefined,
        manifestRevision: manifestRevision ?? undefined,
      });
      frame.stale = staleReason !== null;
      view.setFrame(frame);
    } finally {
      presenting = false;
    }
  }

  /**
   * Latest envelope in a per-context commit series whose at_tick <= tick,
   * or null when the context has no commit at or before the tick.
   */
  function latestCommitAtOrBefore(series, tick) {
    if (!series) return null;
    let best = null;
    for (const commitTick of series.keys()) {
      if (commitTick <= tick && (best === null || commitTick > best)) best = commitTick;
    }
    return best === null ? null : series.get(best);
  }

  /** Ingest (validate + index) one SceneState exactly once. */
  function pushSceneState(scene, context = null) {
    if (!Number.isSafeInteger(scene?.at?.tick) || scene.at.tick < 0) {
      throw new TypeError('SceneState must carry an integer tick >= 0');
    }
    const ingested = ingestSceneState(scene, context ?? sceneContext);
    scenesByTick.set(ingested.tick, ingested);
    if (ownedCursor) {
      const tickBefore = ownedCursor.currentTick();
      const sorted = [...scenesByTick.keys()].sort((a, b) => a - b);
      const times = sorted.map(tick => scenesByTick.get(tick).simTimeNs);
      if (ownedCursor.length === 0) {
        ownedCursor.setTimeline(sorted, times);
      } else {
        const playing = ownedCursor.isPlaying();
        ownedCursor.setTimeline(sorted, times);
        if (playing) ownedCursor.play();
        if (tickBefore !== null && scenesByTick.has(tickBefore)) {
          ownedCursor.seek(sorted.indexOf(tickBefore));
        }
      }
    }
    present();
  }

  /**
   * Ingest one frame-evidence envelope into its context's commit series.
   * The envelope's at_tick (when declared) is the commit position; an
   * envelope without at_tick replaces the latest commit of its context.
   */
  function setEvidence(envelope) {
    if (envelope === null || envelope === undefined) return;
    const ingested = ingestFrameEvidence(envelope);
    commitEnvelope(evidenceByContextKey, ingested, envelope);
    present();
  }

  /** Ingest one rule-evidence envelope into its context's commit series. */
  function setRuleEvidence(envelope) {
    if (envelope === null || envelope === undefined) return;
    const ingested = ingestRuleEvidence(envelope);
    commitEnvelope(ruleByContextKey, ingested, envelope);
    present();
  }

  function commitEnvelope(store, ingested, envelope) {
    let series = store.get(ingested.contextKey);
    if (!series) {
      series = new Map();
      store.set(ingested.contextKey, series);
    }
    const declared = envelope.evidence?.at_tick;
    series.set(typeof declared === 'number' ? declared : series.size, ingested);
  }

  /** Drop every stored evidence envelope; all contexts become unknown. */
  function refreshEvidence() {
    evidenceByContextKey.clear();
    ruleByContextKey.clear();
    staleReason = null;
    present();
  }

  // Cursor -> presentation subscription. External cursors are never owned:
  // no play/pause/dispose happens here, only observation.
  unsubscribeCursor = activeCursor.subscribe(() => present());

  // SelectionState -> view direction (reverse subscription). The forward
  // direction (view pick -> store) sets suppressReverse = true right before
  // it writes the store and the reverse handler clears it after consuming one
  // emission; while set, every reverse emission is ignored, so a view-driven
  // select can never bounce back into view.selectTarget (no recursion).
  if (selectionStore) {
    unsubscribeSelection = selectionState.subscribe(target => {
      if (suppressReverse) return;
      if (target === null) return;
      // Same-target no-op (sameTarget semantics): the view already shows this
      // id, so no re-render and no echo emission.
      if (view.getSelection()?.entityId === target.id) return;
      // Only targets that resolve in the current frame move the view; an id
      // the frame does not carry stays visible as unresolved instead of being
      // silently accepted.
      view.selectTarget(target.id);
    });
  }

  return {
    view,
    cursor: ownedCursor,
    /** The cursor this mount observes (external or owned). */
    get activeCursor() { return activeCursor; },
    get ownsCursor() { return ownedCursor !== null; },
    pushSceneState,
    setEvidence,
    setRuleEvidence,
    refreshEvidence,
    get staleReason() { return staleReason; },
    setLanguage(next) { view.setLanguage(next); },
    selectTarget(id) {
      if (!currentScene) return false;
      const frameIngest = latestCommitAtOrBefore(evidenceByContextKey.get(currentScene.contextKey) ?? null, currentScene.tick);
      const ruleIngest = latestCommitAtOrBefore(ruleByContextKey.get(currentScene.contextKey) ?? null, currentScene.tick);
      const exists = selectionFor(projectHostViewFrame(currentScene, frameIngest, ruleIngest), id) !== null;
      return exists ? view.selectTarget(id) : false;
    },
    getSelection: () => view.getSelection(),
    destroy() {
      if (unsubscribeCursor) { unsubscribeCursor(); unsubscribeCursor = null; }
      if (unsubscribeSelection) { unsubscribeSelection(); unsubscribeSelection = null; }
      // Only the owned cursor is disposed; an external cursor stays with its owner.
      if (ownedCursor) ownedCursor.dispose();
      view.destroy();
      scenesByTick.clear();
      evidenceByContextKey.clear();
      ruleByContextKey.clear();
      root.replaceChildren();
    },
  };
}
