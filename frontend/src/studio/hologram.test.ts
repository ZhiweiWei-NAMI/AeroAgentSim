import { describe, expect, it } from 'vitest';
import { hologramCenter, hologramMode, sweepRadius, SWEEP_DURATION_MS } from './hologram';
import type { PreviewOverlay } from './plan-overlay';

const proposal: PreviewOverlay = {
  tone: 'proposed', routes: [], markers: [
    { id: 'a', position: [20, 40, 10], kind: 'place', state: 'new', label: 'A' },
    { id: 'b', position: [80, -20, 90], kind: 'vehicle', state: 'new', label: 'B' },
    { id: 'existing', position: [900, 900, 0], kind: 'place', state: 'existing', label: 'Existing' },
  ],
};

describe('hologram mode', () => {
  it('keeps ordinary and configured previews real; only a new apply sweeps', () => {
    const played = new Set(['applied']);
    expect(hologramMode(undefined, played)).toBe('real');
    expect(hologramMode({ ...proposal, tone: 'configured' }, played)).toBe('real');
    expect(hologramMode({ ...proposal, tone: 'configured', reveal: { key: 'new', order: ['a'] } }, played)).toBe('sweep');
    expect(hologramMode({ ...proposal, tone: 'configured', reveal: { key: 'applied', order: ['a'] } }, played)).toBe('real');
    expect(hologramMode({ ...proposal, reveal: { key: 'applied', order: ['a'] } }, played)).toBe('blueprint');
    expect([...played]).toEqual(['applied']);
  });
});

describe('materialize radius', () => {
  it('starts at zero, expands monotonically with diminishing speed, and covers the full extent', () => {
    const radii = [0, 400, 800, 1200, 1600].map(age => sweepRadius(age, SWEEP_DURATION_MS, 1000));
    expect(radii[0]).toBe(0); expect(radii[4]).toBe(1000);
    const steps = radii.slice(1).map((radius, index) => radius - radii[index]);
    expect(steps.every(step => step > 0)).toBe(true);
    expect(steps.slice(1).every((step, index) => step < steps[index])).toBe(true);
    expect(sweepRadius(-100, SWEEP_DURATION_MS, 1000)).toBe(0);
    expect(sweepRadius(5000, SWEEP_DURATION_MS, 1000)).toBe(1000);
    expect(sweepRadius(0, 0, 1000)).toBe(1000);
  });
});

describe('blueprint and reveal centres', () => {
  it('uses all proposal markers and only revealed marker identities for an apply, in render XZ', () => {
    expect(hologramCenter({ ...proposal, markers: proposal.markers.slice(0, 2) }, [5, 6])).toEqual([50, -10]);
    const configured: PreviewOverlay = { ...proposal, tone: 'configured', reveal: { key: 'apply', order: ['b', 'a', 'a', 'route-id'] } };
    expect(hologramCenter(configured, [5, 6])).toEqual([50, -10]);
    expect(hologramCenter({ ...configured, reveal: { key: 'apply', order: ['b'] } }, [5, 6])).toEqual([80, 20]);
  });

  it('uses the actual scene centre when no revealed marker has a position', () => {
    expect(hologramCenter({ ...proposal, markers: [] }, [17, -29])).toEqual([17, -29]);
    expect(hologramCenter({ ...proposal, tone: 'configured', reveal: { key: 'apply', order: ['route-id'] } }, [17, -29])).toEqual([17, -29]);
  });
});
