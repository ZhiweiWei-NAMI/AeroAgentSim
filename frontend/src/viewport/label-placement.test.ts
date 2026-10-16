import { expect, it } from 'vitest';
import { placeLabel, workbenchLayoutKey } from './label-placement';
const safe = { left: 300, right: 900, top: 200, bottom: 700 };
it('invalidates idle labels when glass moves without resizing the canvas', () => {
  const wb = document.createElement('div'); wb.className = 'wb';
  const canvas = document.createElement('div'), panel = document.createElement('aside'); panel.className = 'wb-camera'; wb.append(canvas, panel);
  let x = 850;
  panel.getBoundingClientRect = () => new DOMRect(x, 400, 40, 220);
  const initial = workbenchLayoutKey(canvas); x = 600;
  expect(workbenchLayoutKey(canvas)).not.toBe(initial);
  const moved = workbenchLayoutKey(canvas); expect(workbenchLayoutKey(canvas)).toBe(moved);
  panel.remove(); expect(workbenchLayoutKey(canvas)).not.toBe(moved);
});
it('hides nonselected labels that would be clipped under a side panel', () => {
  expect(placeLabel(310, 400, 180, 28, safe, [], false)).toBeUndefined();
  expect(placeLabel(600, 400, 180, 28, safe, [], false)?.box.left).toBe(510);
});
it('keeps a selected label wholly in the scene and clear of a compact inspector', () => {
  const inspector = { left: 700, right: 900, top: 200, bottom: 350 };
  const placed = placeLabel(850, 320, 180, 28, safe, [inspector], true)!;
  expect(placed).toBeDefined(); expect(placed.box.right).toBeLessThanOrEqual(safe.right);
  expect(placed.box.top >= inspector.bottom || placed.box.right <= inspector.left).toBe(true);
});
it('does not fabricate a place when the label cannot fit', () => {
  expect(placeLabel(500, 300, 700, 28, safe, [], true)).toBeUndefined();
});

it('reuses collision rectangles and its result while selected labels move beside a pin', async () => {
  const { LabelPlacer, pooledRect } = await import('./label-placement');
  const placer = new LabelPlacer(); Object.assign(placer.safe, safe);
  Object.assign(pooledRect(placer.pins, 0), { left: 570, right: 630, top: 350, bottom: 450 }); placer.pinCount = 1;
  const result = placer.place(600, 400, 100, 25, true)!;
  expect(result.box.right < 570 - 6 || result.box.left > 630 + 6).toBe(true);
  const pin = placer.pins[0], occupied = pooledRect(placer.occupied, 0), box = result.box;
  for (let i = 0; i < 600; i++) {
    placer.occupiedCount = 0;
    const placed = placer.place(600 + i % 2, 400, 100, 25, true)!;
    expect(placed).toBe(result); expect(placed.box).toBe(box);
    Object.assign(pooledRect(placer.occupied, placer.occupiedCount++), placed.box);
  }
  expect(placer.pins).toEqual([pin]); expect(placer.occupied).toEqual([occupied]);
});
