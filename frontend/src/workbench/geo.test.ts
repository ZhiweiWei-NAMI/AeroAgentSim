import { describe, expect, it } from 'vitest';
import { enuToLonLat, lonLatToEnu } from './geo';

const ORIGIN = { lat: 31.304_1, lon: 121.512_3, alt: 12 };

describe('workbench/geo ENU ↔ WGS84', () => {
  it('round-trips metres → degrees → metres within a millimetre', () => {
    for (const [east, north] of [[0, 0], [500, -250], [1200, 3400], [-800, 60], [0.001, 0.001]] as const) {
      const { lat, lon } = enuToLonLat(east, north, ORIGIN);
      const back = lonLatToEnu(lon, lat, ORIGIN);
      expect(Math.abs(back.east - east)).toBeLessThan(1e-6);
      expect(Math.abs(back.north - north)).toBeLessThan(1e-6);
    }
  });

  it('reproduces the known point from the scene-compiler formula (1 km north ≈ +0.00898315°)', () => {
    // 1000 m east at lat 31.3041°: (lon−lon0)·rad·a/sqrt(1−e2·sin²) = 1000.
    const scale = 6378137 / Math.sqrt(1 - 6.69437999014e-3 * Math.sin(ORIGIN.lat * Math.PI / 180) ** 2)
      * Math.cos(ORIGIN.lat * Math.PI / 180);
    const eastDeg = 1000 / (Math.PI / 180 * scale);
    const northDeg = 1000 / (6378137 * (1 - 6.69437999014e-3)
      / (1 - 6.69437999014e-3 * Math.sin(ORIGIN.lat * Math.PI / 180) ** 2) ** 1.5 * Math.PI / 180);
    const point = enuToLonLat(1000, 1000, ORIGIN);
    expect(point.lon).toBeCloseTo(ORIGIN.lon + eastDeg, 12);
    expect(point.lat).toBeCloseTo(ORIGIN.lat + northDeg, 12);
    // And a degree-sanity cross-check: 1 km of latitude is about 0.009°.
    expect(point.lat - ORIGIN.lat).toBeGreaterThan(0.0089);
    expect(point.lat - ORIGIN.lat).toBeLessThan(0.0091);
    // The origin itself maps to (0, 0) with its altitude as up.
    expect(lonLatToEnu(ORIGIN.lon, ORIGIN.lat, ORIGIN)).toEqual({ east: 0, north: 0, up: 12 });
  });

  it('is not NaN for finite input and is NaN for non-finite input', () => {
    expect(Number.isFinite(lonLatToEnu(121.52, 31.31, ORIGIN).east)).toBe(true);
    expect(Number.isNaN(lonLatToEnu(Number.NaN, 31.31, ORIGIN).east)).toBe(true);
    expect(Number.isNaN(enuToLonLat(Number.NaN, 0, ORIGIN).lat)).toBe(true);
  });
});
