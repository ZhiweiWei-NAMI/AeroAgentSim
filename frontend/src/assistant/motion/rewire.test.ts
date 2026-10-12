import { describe, expect, it } from 'vitest';
import {
  easeInOutCubic,
  fillGrid,
  knnEdges,
  mixColor,
  pairAnchors,
  phaseAt,
  quadPoint,
} from './rewire';

// mixColor rounds each channel to the nearest integer (0.5 rounds up),
// so 0.5 between #000000 and #ffffff is 127.5 → 128.

describe('phaseAt', () => {
  it('reports boundaries at scale 1', () => {
    expect(phaseAt(0).phase).toBe('charge');
    expect(phaseAt(0).local).toBe(0);
    expect(phaseAt(179).phase).toBe('charge');
    expect(phaseAt(180).phase).toBe('dissolve');
    expect(phaseAt(180).local).toBe(0);
    expect(phaseAt(619).phase).toBe('dissolve');
    expect(phaseAt(620).phase).toBe('rewire');
    expect(phaseAt(620).local).toBe(0);
    expect(phaseAt(999).phase).toBe('rewire');
    expect(phaseAt(1000).phase).toBe('crystallize');
    expect(phaseAt(1000).local).toBe(0);
    expect(phaseAt(1349).phase).toBe('crystallize');
    expect(phaseAt(1350).phase).toBe('done');
  });

  it('scales every duration', () => {
    expect(phaseAt(359, 2).phase).toBe('charge');
    expect(phaseAt(359, 2).local).toBeCloseTo(359 / 360, 5);
    expect(phaseAt(360, 2).phase).toBe('dissolve');
    expect(phaseAt(1239, 2).phase).toBe('dissolve');
    expect(phaseAt(1240, 2).phase).toBe('rewire');
    expect(phaseAt(2000, 2).phase).toBe('crystallize');
    expect(phaseAt(2700, 2).phase).toBe('done');
  });
});

describe('easeInOutCubic', () => {
  it('hits 0, 0.5 and 1', () => {
    expect(easeInOutCubic(0)).toBe(0);
    expect(easeInOutCubic(1)).toBe(1);
    expect(easeInOutCubic(0.5)).toBe(0.5);
  });
});

describe('mixColor', () => {
  it('mixes with rounding to nearest', () => {
    expect(mixColor('#000000', '#ffffff', 0.5)).toBe('rgb(128,128,128)');
    expect(mixColor('#38c6f4', '#8a7dff', 0)).toBe('rgb(56,198,244)');
    expect(mixColor('#38c6f4', '#8a7dff', 1)).toBe('rgb(138,125,255)');
  });
});

describe('quadPoint', () => {
  it('hits the endpoints', () => {
    const a = { x: 0, y: 0 };
    const b = { x: 10, y: 20 };
    const c = { x: 100, y: 100 };
    expect(quadPoint(a, c, b, 0)).toEqual(a);
    expect(quadPoint(a, c, b, 1)).toEqual(b);
  });
});

describe('knnEdges', () => {
  const grid = [
    { x: 0, y: 0 }, { x: 10, y: 0 }, { x: 20, y: 0 },
    { x: 0, y: 10 }, { x: 10, y: 10 }, { x: 20, y: 10 },
  ];

  it('deduplicates undirected edges', () => {
    const edges = knnEdges(grid, 2, 150);
    const keys = new Set(edges.map(([i, j]) => (i < j ? `${i}:${j}` : `${j}:${i}`)));
    expect(keys.size).toBe(edges.length);
  });

  it('respects maxLength', () => {
    const edges = knnEdges(grid, 5, 11);
    for (const [i, j] of edges) {
      const d = Math.hypot(grid[i].x - grid[j].x, grid[i].y - grid[j].y);
      expect(d).toBeLessThanOrEqual(11 + 1e-9);
    }
  });

  it('caps the result at 220', () => {
    const many: Array<{ x: number; y: number }> = [];
    for (let i = 0; i < 40; i++) {
      for (let j = 0; j < 40; j++) many.push({ x: i * 5, y: j * 5 });
    }
    const edges = knnEdges(many, 12, 1000);
    expect(edges.length).toBeLessThanOrEqual(220);
  });
});

describe('pairAnchors', () => {
  const from = [
    { x: 0, y: 0 }, { x: 50, y: 10 }, { x: 10, y: 30 },
  ];
  const to = [
    { x: 0, y: 100 }, { x: 40, y: 120 }, { x: 5, y: 140 }, { x: 60, y: 160 },
  ];

  it('produces n = min(max, max(len)) pairs', () => {
    const pairs = pairAnchors(from, to, 96);
    expect(pairs.length).toBe(4);
    expect(pairAnchors(from, to, 2).length).toBe(2);
    expect(pairAnchors([], to).length).toBe(0);
  });

  it('zips in reading order (top first)', () => {
    const pairs = pairAnchors(from, to);
    for (let i = 1; i < pairs.length; i++) {
      expect(pairs[i].b.y).toBeGreaterThanOrEqual(pairs[i - 1].b.y);
    }
  });

  it('keeps delay in 0..1', () => {
    for (const p of pairAnchors(from, to)) {
      expect(p.delay).toBeGreaterThanOrEqual(0);
      expect(p.delay).toBeLessThanOrEqual(1);
    }
  });
});

describe('fillGrid', () => {
  const rect = { x: 100, y: 100, width: 300, height: 200 };

  it('is deterministic for the same seed', () => {
    expect(fillGrid(rect, 12, 7)).toEqual(fillGrid(rect, 12, 7));
    expect(fillGrid(rect, 12, 7)).not.toEqual(fillGrid(rect, 12, 8));
  });

  it('stays inside the rect inset by 16px', () => {
    for (const p of fillGrid(rect, 20, 7)) {
      expect(p.x).toBeGreaterThanOrEqual(116);
      expect(p.x).toBeLessThanOrEqual(384);
      expect(p.y).toBeGreaterThanOrEqual(116);
      expect(p.y).toBeLessThanOrEqual(284);
    }
  });

  it('keeps 18px distance from existing points', () => {
    const existing = [{ x: 200, y: 200 }];
    const pts = fillGrid(rect, 15, 7, existing);
    for (const p of pts) {
      expect(Math.hypot(p.x - 200, p.y - 200)).toBeGreaterThanOrEqual(18);
    }
  });
});
