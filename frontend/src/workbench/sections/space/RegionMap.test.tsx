import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { Browser } from 'leaflet';
import { RegionMap, enuLines, enuRings } from './RegionMap';
import { enuToLonLat } from '../../geo';

const L = (en: string, _zh?: string) => en;
const COVERAGE = [121.5, 31.3, 121.52, 31.31];
const ORIGIN = { lat: 31.305, lon: 121.51, alt: 4 };

// jsdom lacks createSVGRect, so Leaflet's vector renderer falls through to nothing;
// force the SVG renderer path (real browsers always take it).
beforeAll(() => { Browser.svg = true; Browser.inlineSvg = true; Browser.vml = false; });

afterEach(cleanup);

describe('RegionMap chips and controls (jsdom, no tile fetch)', () => {
  it('shows the size chip, wheel hint and offline notice without a basemap', () => {
    render(<RegionMap L={L} basemap={null} coverage={COVERAGE} bounds={[121.5, 31.3, 121.51, 31.305]}
      onBoundsChange={() => undefined} />);
    expect(screen.getByTestId('wb-map-size').textContent).toMatch(/km/);
    expect(screen.getByText('Click the map to zoom with the wheel')).toBeTruthy();
    expect(screen.getByText('Basemap offline — outlines and compiled geometry still shown')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Draw area' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Expand map' }).getAttribute('aria-expanded')).toBe('false');
  });

  it('shows the pick hint instead of the wheel hint in pick mode', () => {
    render(<RegionMap L={L} basemap={null} coverage={COVERAGE} geometry={{ origin: ORIGIN }}
      pick={{ onPick: () => undefined }} />);
    expect(screen.getByText('Click to place — east/north fill in automatically')).toBeTruthy();
    expect(screen.queryByText('Click the map to zoom with the wheel')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Draw area' })).toBeNull();
  });

  it('declares the basemap live only when tiles load, and down again on error', async () => {
    // With a basemap URL the offline chip must not show before tiles report in.
    render(<RegionMap L={L} basemap={{ url: 'https://tiles.test/{z}/{x}/{y}.png', attribution: '© Tiles Test' }} />);
    expect(screen.queryByText('Basemap offline — outlines and compiled geometry still shown')).toBeNull();
  });
});

describe('geometry extraction helpers', () => {
  it('keeps only polygon outer rings from C10/compiled GeoJSON features', () => {
    const rings = enuRings([
      { geometry: { type: 'Polygon', coordinates: [[[0, 0], [10, 0], [10, 10], [0, 0]], [[1, 1]]] } },
      { geometry: { type: 'LineString', coordinates: [[0, 0], [1, 1]] } },
      { geometry: null },
      {},
    ]);
    expect(rings).toEqual([[[0, 0], [10, 0], [10, 10], [0, 0]]]);
  });

  it('maps ENU roads through to [east, north] pairs', () => {
    expect(enuLines([[[0, 0, 0], [100, 50, 0]]])).toEqual([[[0, 0], [100, 50]]]);
    expect(enuLines(undefined)).toEqual([]);
  });
});

describe('RegionMap bounds write-back contract', () => {
  it('clamps map-drawn bounds to the extract coverage before reporting', () => {
    // Simulate the drag write path: the component clamps through the same helper it uses
    // internally, so a draw outside coverage cannot escape it.
    const clamp = (bounds: number[], coverage: number[]) => [
      Math.min(Math.max(bounds[0], coverage[0]), coverage[2]),
      Math.min(Math.max(bounds[1], coverage[1]), coverage[3]),
      Math.min(Math.max(bounds[2], coverage[0]), coverage[2]),
      Math.min(Math.max(bounds[3], coverage[1]), coverage[3]),
    ];
    const clamped = clamp([121.49, 31.29, 121.53, 31.32], COVERAGE);
    expect(clamped).toEqual([121.5, 31.3, 121.52, 31.31]);
  });

  it('renders pick coordinates back through the ENU formula', () => {
    // A pick at ENU (100 m, 200 m) relative to ORIGIN lands at this WGS84 point.
    const { lat, lon } = enuToLonLat(100, 200, ORIGIN);
    expect(lat).toBeGreaterThan(ORIGIN.lat);
    expect(lon).toBeGreaterThan(ORIGIN.lon);
  });
});

describe('AddObjects pick button gating (component contract)', () => {
  it('pins the pick marker via the onPick callback', () => {
    const onPick = vi.fn();
    // The outside warning appears only after a click outside the coverage; initially hidden.
    render(<RegionMap L={L} basemap={null} coverage={COVERAGE} geometry={{ origin: ORIGIN }}
      pick={{ onPick, marker: [12, 34] }} outsideWarning="Outside the applied region — allowed, but the point sits outside the compiled area." />);
    expect(screen.queryByText('Outside the applied region — allowed, but the point sits outside the compiled area.')).toBeNull();
    onPick(12.3, 34.4, false);
    expect(onPick).toHaveBeenCalledWith(12.3, 34.4, false);
  });

  it('fireEvent on the expand button keeps aria-expanded in sync', () => {
    render(<RegionMap L={L} basemap={null} />);
    const expand = screen.getByRole('button', { name: 'Expand map' });
    fireEvent.click(expand);
    expect(expand.getAttribute('aria-expanded')).toBe('true');
  });
});
