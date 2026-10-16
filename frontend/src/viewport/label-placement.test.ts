import { expect, it } from 'vitest';
import { labelTier, placeLabel, TIER2_DISTANCE_M, TIER3_DISTANCE_M, visibleOccluders, WorkbenchLayout } from './label-placement';
const safe = { left: 300, right: 900, top: 200, bottom: 700 };
const tier2 = { secondary: true }, tier3 = { background: true };

it('always shows tier 1 labels: selected, hovered, incidents, task-active UAVs and mission sites', () => {
  for (const info of [{ selected: true }, { hovered: true }, { incident: true }, { activeVehicle: true }, { missionSite: true }]) {
    expect(labelTier(info, 50_000)).toBe(1);
  }
});

it('shows tier 2 (other UAVs, pads, vertiports, named sites) only within 260 m', () => {
  expect(labelTier(tier2, TIER2_DISTANCE_M)).toBe(2);
  expect(labelTier(tier2, TIER2_DISTANCE_M + 1)).toBeUndefined();
  expect(labelTier(tier2, 100)).toBe(2);
});

it('shows tier 3 (background traffic, behaviour instances) only within 90 m', () => {
  expect(labelTier(tier3, TIER3_DISTANCE_M)).toBe(3);
  expect(labelTier(tier3, TIER3_DISTANCE_M + 1)).toBeUndefined();
  // Hover or selection promotes a tier 3 entity to always-on.
  expect(labelTier({ ...tier3, hovered: true }, 40_000)).toBe(1);
  expect(labelTier({ ...tier3, selected: true }, 40_000)).toBe(1);
});

it('applies ±10 % hysteresis on the distance thresholds so pacing never flickers', () => {
  // Just outside 260 m: not shown the first time…
  expect(labelTier(tier2, TIER2_DISTANCE_M * 1.05)).toBeUndefined();
  // …but a label already shown at tier 2 keeps showing up to +10 %.
  expect(labelTier(tier2, TIER2_DISTANCE_M * 1.05, 2)).toBe(2);
  expect(labelTier(tier2, TIER2_DISTANCE_M * 1.1, 2)).toBe(2);
  expect(labelTier(tier2, TIER2_DISTANCE_M * 1.1 + 1, 2)).toBeUndefined();
  // Symmetrically, a hidden tier 2 label appears only inside 90 % of the threshold.
  expect(labelTier(tier2, TIER2_DISTANCE_M * 0.95, null)).toBeUndefined();
  expect(labelTier(tier2, TIER2_DISTANCE_M * 0.9, null)).toBe(2);
  expect(labelTier(tier3, TIER3_DISTANCE_M * 1.05, 3)).toBe(3);
  expect(labelTier(tier3, TIER3_DISTANCE_M * 1.05)).toBeUndefined();
});

it('honours the show-all override but leaves unclassified entities unlabelled', () => {
  expect(labelTier(tier2, 9_999, undefined, true)).toBe(2);
  expect(labelTier(tier3, 9_999, undefined, true)).toBe(3);
  expect(labelTier({}, 10)).toBeUndefined();
  expect(labelTier({}, 10, undefined, true)).toBeUndefined();
});

it('invalidates idle labels when glass moves without resizing the canvas', async () => {
  const wb = document.createElement('div'); wb.className = 'wb';
  const canvas = document.createElement('div'), panel = document.createElement('aside'); panel.className = 'wb-camera'; wb.append(canvas, panel);
  const layout = new WorkbenchLayout(canvas);
  try {
    const initial = layout.key; panel.style.left = '600px';
    await Promise.resolve();
    expect(layout.key).not.toBe(initial);
    const moved = layout.key; expect(layout.key).toBe(moved);
    panel.remove(); await Promise.resolve(); expect(layout.key).not.toBe(moved);
  } finally { layout.dispose(); }
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

it('collects only laid-out glass overlays as occluders (S5)', () => {
  const wb = document.createElement('div'); wb.className = 'wb';
  const canvas = document.createElement('div');
  const panel = document.createElement('aside'); panel.className = 'wb-panel wb-glass';
  const rail = document.createElement('div'); rail.className = 'wb-camera';
  const hidden = document.createElement('div'); hidden.className = 'wb-glass'; hidden.style.display = 'none';
  wb.append(canvas, panel, rail, hidden); document.body.append(wb);
  try {
    const found = visibleOccluders(wb);
    expect(found).toContain(panel); expect(found).toContain(rail);
    expect(found).not.toContain(hidden); // a detached node never matches the selector at all
    // A collapsed tab (glass only, no .wb-panel) also counts — panels live under it.
    const tab = document.createElement('button'); tab.className = 'wb-collapsed-tab wb-glass'; wb.append(tab);
    expect(visibleOccluders(wb)).toContain(tab);
  } finally { wb.remove(); }
});
it('invalidates the layout key when a KPI strip or bottom overlay card changes', async () => {
  const wb = document.createElement('div'); wb.className = 'wb';
  const canvas = document.createElement('div'), kpis = document.createElement('div'); kpis.className = 'wb-kpis';
  wb.append(canvas, kpis); document.body.append(wb);
  const layout = new WorkbenchLayout(canvas);
  try {
    const initial = layout.key; kpis.style.top = '130px'; await Promise.resolve();
    expect(layout.key).not.toBe(initial);
  } finally { layout.dispose(); wb.remove(); }
});
