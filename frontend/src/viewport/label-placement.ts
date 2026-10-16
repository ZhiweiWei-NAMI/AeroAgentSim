export interface LabelRect { left: number; right: number; top: number; bottom: number }
/** S5: overlay UI that must never sit under a scene label — the glass panels
 * (which include the KPI cards and the bottom conditions/legend card), the
 * camera rail and the other floating workbench surfaces. Shared by every
 * collision site so there is exactly one occluder system. */
export const LABEL_OCCLUDER_SELECTOR = '.wb-glass, .wb-kpis, .wb-kpi-popover, .wb-panel, .wb-camera, .wb-graph, .wb-history, .wb-waiting, .viewport-card, .viewport-conditions, .viewport-city-progress, .viewport-city-alert, .viewport-scene-hint';
/** Only laid-out, actually rendered elements occlude labels: attached to the
 * document and not display:none/visibility:hidden. Read once per layout change. */
export function visibleOccluders(root: ParentNode): Element[] {
  return [...root.querySelectorAll(LABEL_OCCLUDER_SELECTOR)].filter(node => {
    if (!(node instanceof HTMLElement) || !node.isConnected) return false;
    if (node.closest('[hidden], details:not([open]) .wb-kpi-popover')) return false;
    const style = getComputedStyle(node);
    return style.display !== 'none' && style.visibility !== 'hidden';
  });
}
/** Cache the layout signature between actual UI changes. Camera/label writes are
 * deliberately excluded, so animation never polls the panels' DOM geometry. */
export class WorkbenchLayout {
  private dirty = true;
  private cached = '';
  private revision = 0;
  private resize?: ResizeObserver;
  private observed = new Set<Element>();
  private mutation: MutationObserver;
  private root: HTMLElement;
  constructor(private container: HTMLElement) {
    this.root = container.closest<HTMLElement>('.wb') ?? container;
    if (typeof ResizeObserver !== 'undefined') this.resize = new ResizeObserver(this.invalidate);
    this.mutation = new MutationObserver(records => {
      if (records.some(record => {
        const target = record.target instanceof Element ? record.target : record.target.parentElement;
        if (!target || target.closest('.viewport-canvas, .viewport-labels')) return false;
        // Text/value ticks do not move panels. ResizeObserver reports actual
        // size changes; only surface topology and layout attributes need a rescan.
        if (record.type === 'childList') return [...record.addedNodes, ...record.removedNodes].some(node =>
          node instanceof Element && (node.matches(LABEL_OCCLUDER_SELECTOR) || !!node.querySelector(LABEL_OCCLUDER_SELECTOR)));
        return target === this.root || target === this.container || target.matches(`.wb-scene-frame, ${LABEL_OCCLUDER_SELECTOR}`);
      })) this.invalidate();
    });
    this.mutation.observe(this.root, { subtree: true, childList: true, attributes: true,
      attributeFilter: ['class', 'style', 'hidden', 'open', 'data-open', 'aria-expanded'] });
    window.addEventListener('resize', this.invalidate);
  }
  private invalidate = () => { this.dirty = true; };
  get key(): string {
    if (this.dirty) {
      this.dirty = false;
      const nodes = new Set<Element>([this.root, this.container, ...this.root.querySelectorAll(`.wb-scene-frame, ${LABEL_OCCLUDER_SELECTOR}`)]);
      for (const node of this.observed) if (!nodes.has(node)) this.resize?.unobserve(node);
      for (const node of nodes) if (!this.observed.has(node)) this.resize?.observe(node);
      this.observed = nodes;
      this.cached = String(++this.revision);
    }
    return this.cached;
  }
  dispose() { this.resize?.disconnect(); this.mutation.disconnect(); window.removeEventListener('resize', this.invalidate); }
}
const overlaps = (a: LabelRect, b: LabelRect) => a.left < b.right + 6 && a.right + 6 > b.left && a.top < b.bottom + 4 && a.bottom + 4 > b.top;
export interface PlacedLabel { x: number; y: number; box: LabelRect }
export function labelRect(): LabelRect { return { left: 0, right: 0, top: 0, bottom: 0 }; }
/** A pool grows only when the number of panels/pins/visible labels grows. */
export function pooledRect(pool: LabelRect[], index: number): LabelRect { return pool[index] ?? (pool[index] = labelRect()); }

/** Reusable collision workspace. Candidate search visits the same points/order as
 * the old array/flatMap/sort implementation, retaining only the nearest solution. */
export class LabelPlacer {
  readonly panels: LabelRect[] = [];
  readonly pins: LabelRect[] = [];
  readonly occupied: LabelRect[] = [];
  panelCount = 0;
  pinCount = 0;
  occupiedCount = 0;
  readonly safe = labelRect();
  private candidate = labelRect();
  private anchor = labelRect();
  private result: PlacedLabel = { x: 0, y: 0, box: labelRect() };
  private best = Infinity;
  private bestBeside = false;
  private x = 0; private y = 0; private width = 0; private height = 0;
  private includePins = true;
  private selected = false;
  private box(x: number, y: number, out: LabelRect) {
    out.left = x - this.width / 2; out.right = x + this.width / 2; out.top = y - this.height; out.bottom = y;
  }
  private consider(x: number, y: number) {
    const safe = this.safe;
    if (this.selected) {
      x = Math.max(safe.left + this.width / 2, Math.min(safe.right - this.width / 2, x));
      y = Math.max(safe.top + this.height, Math.min(safe.bottom, y));
    }
    const box = this.candidate; this.box(x, y, box);
    if (box.left < safe.left || box.right > safe.right || box.top < safe.top || box.bottom > safe.bottom) return;
    for (let i = 0; i < this.panelCount; i++) if (overlaps(box, this.panels[i])) return;
    for (let i = 0; i < this.occupiedCount; i++) if (overlaps(box, this.occupied[i])) return;
    if (this.includePins) for (let i = 0; i < this.pinCount; i++) if (overlaps(box, this.pins[i])) return;
    let beside = false;
    if (this.includePins) for (let i = 0; i < this.pinCount; i++) {
      const pin = this.pins[i];
      if (!overlaps(this.anchor, pin)) continue;
      if (!(box.right < pin.left - 6 || box.left > pin.right + 6)) { beside = false; break; }
      beside = true;
    }
    const distance = (x - this.x) ** 2 + (y - this.y) ** 2;
    if (this.bestBeside && !beside || beside === this.bestBeside && distance >= this.best) return;
    this.best = distance; this.bestBeside = beside;
    this.result.x = x; this.result.y = y; Object.assign(this.result.box, box);
  }
  private around(box: LabelRect) {
    this.consider(box.left - this.width / 2 - 8, this.y);
    this.consider(box.right + this.width / 2 + 8, this.y);
    this.consider(this.x, box.top - 6);
    this.consider(this.x, box.bottom + this.height + 6);
  }
  place(x: number, y: number, width: number, height: number, selected: boolean, includePins = true): PlacedLabel | undefined {
    if (width > this.safe.right - this.safe.left || height > this.safe.bottom - this.safe.top) return;
    this.x = x; this.y = y; this.width = width; this.height = height; this.selected = selected; this.includePins = includePins;
    this.best = Infinity; this.bestBeside = false; this.box(x, y, this.anchor);
    this.consider(x, y);
    // Every label can move clear of a panel; selected labels can also move
    // around other labels/pins to retain their existing priority.
    for (let i = 0; i < this.panelCount; i++) this.around(this.panels[i]);
    if (selected) {
      if (includePins) for (let i = 0; i < this.pinCount; i++) this.around(this.pins[i]);
      for (let i = 0; i < this.occupiedCount; i++) this.around(this.occupied[i]);
    }
    return this.best < Infinity ? this.result : undefined;
  }
}

/** Label tier policy (UX2-E): 1 is always shown, 2 only near the camera and 3
 * only on hover/selection or very close. Tiers change visibility only — collision
 * and occluder handling are untouched. */
export type LabelTier = 1 | 2 | 3;
export const TIER2_DISTANCE_M = 260, TIER3_DISTANCE_M = 90, TIER_HYSTERESIS = 0.1;
/** Classification inputs, all derived from committed facts and registry ancestors
 * (never hard-coded entity ids). */
export interface LabelTierInfo {
  selected?: boolean; hovered?: boolean; incident?: boolean;
  /** Policy layer (semantic mode) or other reason to treat this as always-on. */
  always?: boolean;
  /** UAV with an active task or an operational role. */
  activeVehicle?: boolean;
  /** Mission site referenced by an active task. */
  missionSite?: boolean;
  /** Other UAVs, pads, vertiports and named sites (tier 2). */
  secondary?: boolean;
  /** Background traffic and behaviour instances (tier 3). */
  background?: boolean;
}
/**
 * The tier this entity's label may render at, or `undefined` when it must stay
 * hidden at this distance. `previous` is the entity's last decision (null means
 * hidden, undefined means unseen) and feeds
 * the ±10 % hysteresis so pacing around a threshold never flickers: a shown
 * label keeps showing up to 110 % of the threshold, a hidden one appears only
 * inside 90 % of it. `showAll` (the user's "Show all labels" toggle) shows every
 * classified tier regardless of distance.
 */
export function labelTier(info: LabelTierInfo, distance: number, previous?: LabelTier | null, showAll = false): LabelTier | undefined {
  if (info.selected || info.hovered || info.incident || info.activeVehicle || info.missionSite || info.always) return 1;
  const tier: LabelTier | undefined = info.background ? 3 : info.secondary ? 2 : undefined;
  if (tier === undefined) return undefined;
  if (showAll) return tier;
  const threshold = tier === 3 ? TIER3_DISTANCE_M : TIER2_DISTANCE_M;
  const limit = threshold * (previous === tier ? 1 + TIER_HYSTERESIS : previous === null ? 1 - TIER_HYSTERESIS : 1);
  return distance <= limit ? tier : undefined;
}

/** Convenience API for one-off callers; EntityLayer retains its own workspace. */
export function placeLabel(x: number, y: number, width: number, height: number, safe: LabelRect, obstacles: LabelRect[], selected: boolean, fixed: LabelRect[] = []) {
  const placer = new LabelPlacer(); Object.assign(placer.safe, safe);
  placer.panels.push(...obstacles); placer.panelCount = obstacles.length;
  placer.pins.push(...fixed); placer.pinCount = fixed.length;
  return placer.place(x, y, width, height, selected);
}
