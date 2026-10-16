export interface LabelRect { left: number; right: number; top: number; bottom: number }
/** A paused canvas still has to react when the glass panels move or resize. */
export function workbenchLayoutKey(container: HTMLElement): string {
  const wb = container.closest('.wb');
  if (!wb) return '';
  return [...wb.querySelectorAll('.wb-scene-frame, .wb-panel, .wb-camera, .wb-graph, .wb-history, .wb-waiting')].map(node => {
    const box = node.getBoundingClientRect(); return `${box.x}/${box.y}/${box.width}/${box.height}`;
  }).join('|');
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
    if (selected) {
      for (let i = 0; i < this.panelCount; i++) this.around(this.panels[i]);
      if (includePins) for (let i = 0; i < this.pinCount; i++) this.around(this.pins[i]);
      for (let i = 0; i < this.occupiedCount; i++) this.around(this.occupied[i]);
    }
    return this.best < Infinity ? this.result : undefined;
  }
}

/** Convenience API for one-off callers; EntityLayer retains its own workspace. */
export function placeLabel(x: number, y: number, width: number, height: number, safe: LabelRect, obstacles: LabelRect[], selected: boolean, fixed: LabelRect[] = []) {
  const placer = new LabelPlacer(); Object.assign(placer.safe, safe);
  placer.panels.push(...obstacles); placer.panelCount = obstacles.length;
  placer.pins.push(...fixed); placer.pinCount = fixed.length;
  return placer.place(x, y, width, height, selected);
}
