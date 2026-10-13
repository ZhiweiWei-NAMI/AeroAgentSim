import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import * as Leaflet from 'leaflet';
import { RegionMap, enuLines, geometryPolygons } from './RegionMap';
import { enuToLonLat } from '../../geo';
import { sceneMapGeometry, previewMapGeometry } from './model';
import { captureMaps, forceSvgRenderer, mapLayers } from './leafletTestSetup';
import { BASEMAP, COVERAGE, ORIGIN, PREVIEW, SCENE, WGS_RINGS } from './mapTestFixtures';

const L = (en: string, _zh: string) => en;
let maps: Leaflet.Map[];
beforeAll(forceSvgRenderer);
beforeEach(() => { maps = captureMaps(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const polygons = (map: Leaflet.Map) => mapLayers(map).filter((layer): layer is Leaflet.Polygon => layer instanceof Leaflet.Polygon && !(layer instanceof Leaflet.Rectangle));
const roads = (map: Leaflet.Map) => mapLayers(map).filter((layer): layer is Leaflet.Polyline => layer instanceof Leaflet.Polyline && !(layer instanceof Leaflet.Polygon));

describe('RegionMap lifecycle and real geometry (T1-R2-008 / T4-R2-001 / T1-R2-004)', () => {
  it('initialises a view before attaching saved geometry, then accepts coverage on reopen', () => {
    const addLayer = Leaflet.Map.prototype.addLayer;
    vi.spyOn(Leaflet.Map.prototype, 'addLayer').mockImplementation(function (this: Leaflet.Map, layer) {
      if (layer instanceof Leaflet.Path) expect(Number.isFinite(this.getZoom())).toBe(true);
      return addLayer.call(this, layer);
    });
    const geometry = sceneMapGeometry(SCENE);
    const view = render(<RegionMap L={L} basemap={null} geometry={geometry} />);
    expect(maps).toHaveLength(1);
    expect(screen.queryByText('Map unavailable — bounds can still be typed')).toBeNull();
    view.rerender(<RegionMap L={L} basemap={null} geometry={geometry} coverage={COVERAGE} bounds={COVERAGE} />);
    expect(maps).toHaveLength(1);
    expect(polygons(maps[0])).toHaveLength(3);
    expect(roads(maps[0])).toHaveLength(1);
    view.unmount();
    render(<RegionMap L={L} basemap={null} geometry={geometry} coverage={COVERAGE} bounds={COVERAGE} />);
    expect(maps).toHaveLength(2);
    expect(screen.queryByText('Map unavailable — bounds can still be typed')).toBeNull();
  });

  it('keeps saved WGS84 buildings in place and projects ENU road records exactly once', () => {
    render(<RegionMap L={L} basemap={null} coverage={COVERAGE} geometry={sceneMapGeometry(SCENE)} />);
    const buildings = polygons(maps[0]);
    expect(buildings).toHaveLength(3);
    for (const [index, building] of buildings.entries()) {
      const first = (building.getLatLngs() as Leaflet.LatLng[][])[0][0];
      expect(first.lng).toBe(WGS_RINGS[index][0][0]);
      expect(first.lat).toBe(WGS_RINGS[index][0][1]);
      expect(building.getBounds().getEast()).toBeGreaterThan(building.getBounds().getWest());
      expect(building.options.fillOpacity).toBe(0.35);
    }
    expect(new Set(buildings.map(building => building.getElement()?.getAttribute('d'))).size).toBe(3);
    const road = roads(maps[0])[0];
    const point = (road.getLatLngs() as Leaflet.LatLng[])[0];
    expect(point.lng).toBeCloseTo(COVERAGE[2], 10);
    expect(point.lat).toBeCloseTo(COVERAGE[3], 10);
    expect(road.options).toMatchObject({ weight: 2, color: '#14202e' });
  });

  it('projects the server C10 preview footprints with its own origin (T3-R2-003)', () => {
    render(<RegionMap L={L} basemap={null} coverage={COVERAGE} geometry={previewMapGeometry(PREVIEW)} />);
    const first = (polygons(maps[0])[0].getLatLngs() as Leaflet.LatLng[][])[0][0];
    expect(first.lng).toBeCloseTo(WGS_RINGS[0][0][0], 10);
    expect(first.lat).toBeCloseTo(WGS_RINGS[0][0][1], 10);
    expect(roads(maps[0])).toHaveLength(1);
  });

  it('contains a map effect failure and keeps typed bounds and surrounding controls usable', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const badGeometry = { origin: ORIGIN, buildings: [{ geometry: { type: 'Polygon', coordinates: [[[NaN, 1], [1, 2], [2, 3]]] } }] };
    render(<div><label>West<input defaultValue="121.5" /></label>
      <RegionMap L={L} basemap={null} geometry={badGeometry} /><button>Other action</button></div>);
    expect(screen.getByRole('alert')).toHaveTextContent('Map unavailable — bounds can still be typed');
    fireEvent.change(screen.getByLabelText('West'), { target: { value: '121.51' } });
    expect(screen.getByLabelText('West')).toHaveValue('121.51');
    expect(screen.getByRole('button', { name: 'Other action' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Retry map' })).toBeEnabled();
    expect(consoleError).toHaveBeenCalled();
  });
});

describe('drawing and keyboard ownership', () => {
  it('suspends panning, writes real W/S/E/N, clamps to coverage and restores navigation (T1-R2-003)', () => {
    const onBoundsChange = vi.fn();
    render(<RegionMap L={L} basemap={null} coverage={COVERAGE} bounds={COVERAGE} onBoundsChange={onBoundsChange} />);
    const map = maps[0], before = map.getCenter();
    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }));
    expect(map.dragging.enabled()).toBe(false);
    act(() => {
      map.fire('mousedown', { latlng: Leaflet.latLng(31.302, 121.505) });
      map.fire('mousemove', { latlng: Leaflet.latLng(31.32, 121.53) });
      map.fire('mouseup', { latlng: Leaflet.latLng(31.32, 121.53) });
    });
    expect(onBoundsChange).toHaveBeenCalledTimes(1);
    expect(onBoundsChange).toHaveBeenCalledWith([121.505, 31.302, COVERAGE[2], COVERAGE[3]]);
    expect(map.getCenter()).toEqual(before);
    expect(map.dragging.enabled()).toBe(true);
    expect(screen.getByRole('button', { name: 'Draw area' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('Escape cancels only drawing even with the Workbench window-capture guard registered first (T4-R2-012)', () => {
    const closePanel = vi.fn(), onBoundsChange = vi.fn(), bubbled = vi.fn();
    const workbenchKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || (event.target as HTMLElement)?.closest('[role="dialog"], [role="alertdialog"]')) return;
      if (event.key === 'Escape') closePanel();
    };
    window.addEventListener('keydown', workbenchKey, true);
    window.addEventListener('keydown', bubbled);
    try {
      render(<RegionMap L={L} basemap={null} coverage={COVERAGE} onBoundsChange={onBoundsChange} />);
      fireEvent.click(screen.getByRole('button', { name: 'Draw area' }));
      act(() => {
        maps[0].fire('mousedown', { latlng: Leaflet.latLng(31.302, 121.505) });
        maps[0].fire('mousemove', { latlng: Leaflet.latLng(31.304, 121.51) });
      });
      fireEvent.keyDown(screen.getByTestId('wb-map-surface'), { key: 'Escape' });
      expect(closePanel).not.toHaveBeenCalled();
      expect(bubbled).not.toHaveBeenCalled();
      expect(onBoundsChange).not.toHaveBeenCalled();
      expect(maps[0].dragging.enabled()).toBe(true);
      expect(screen.getByRole('button', { name: 'Draw area' })).toHaveAttribute('aria-pressed', 'false');
      act(() => maps[0].fire('mouseup', { latlng: Leaflet.latLng(31.304, 121.51) }));
      expect(onBoundsChange).not.toHaveBeenCalled();
      fireEvent.keyDown(screen.getByTestId('wb-map-surface'), { key: 'Escape' });
      expect(closePanel).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener('keydown', workbenchKey, true); window.removeEventListener('keydown', bubbled);
    }
  });

  it('a click enables the real wheel handler and a wheel event zooms; leaving disables it (T4-R2-007)', async () => {
    render(<RegionMap L={L} basemap={null} coverage={COVERAGE} />);
    const map = maps[0], before = map.getZoom();
    expect(map.scrollWheelZoom.enabled()).toBe(false);
    act(() => map.fire('click', { latlng: map.getCenter() }));
    expect(map.scrollWheelZoom.enabled()).toBe(true);
    fireEvent.wheel(screen.getByTestId('wb-map-surface'), { deltaY: -100, deltaMode: 0, clientX: 200, clientY: 100 });
    await waitFor(() => expect(map.getZoom()).toBeGreaterThan(before));
    act(() => map.fire('mouseout'));
    expect(map.scrollWheelZoom.enabled()).toBe(false);
    expect(screen.getByText('Click the map to zoom with the wheel')).toBeVisible();
  });
});

describe('basemap, readonly bounds and pick controls', () => {
  it('draws the applied bounds without edit handles, and gives real tile load/error feedback', () => {
    const view = render(<RegionMap L={L} basemap={BASEMAP} coverage={COVERAGE} bounds={COVERAGE} geometry={sceneMapGeometry(SCENE)} />);
    const layers = mapLayers(maps[0]);
    expect(layers.filter(layer => layer instanceof Leaflet.Rectangle)).toHaveLength(2);
    expect(view.container.querySelector('.wb-map-handle')).toBeNull();
    const tiles = layers.find((layer): layer is Leaflet.TileLayer => layer instanceof Leaflet.TileLayer)!;
    expect(tiles.options.maxZoom).toBe(18);
    act(() => tiles.fire('tileload'));
    expect(view.container.querySelector('.wb-map')).toHaveClass('wb-map-basemap');
    act(() => tiles.fire('tileerror'));
    expect(screen.getByText('Basemap tiles unavailable — bounds and geometry are shown')).toBeVisible();
    expect(polygons(maps[0])).toHaveLength(3);
  });

  it('uses the current scene origin for clicks and moves a controlled marker on typed edits', () => {
    const onPick = vi.fn();
    const view = render(<RegionMap L={L} basemap={null} coverage={COVERAGE} bounds={COVERAGE} geometry={{ origin: ORIGIN }} pick={{ onPick, marker: [12, 34] }} />);
    expect(screen.queryByRole('button', { name: 'Draw area' })).toBeNull();
    const point = enuToLonLat(100, 200, ORIGIN);
    act(() => maps[0].fire('click', { latlng: Leaflet.latLng(point.lat, point.lon) }));
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick.mock.calls[0][0]).toBeCloseTo(100, 6);
    expect(onPick.mock.calls[0][1]).toBeCloseTo(200, 6);
    expect(onPick.mock.calls[0][2]).toBe(true);
    view.rerender(<RegionMap L={L} basemap={null} coverage={COVERAGE} bounds={COVERAGE} geometry={{ origin: ORIGIN }} pick={{ onPick, marker: [300, -120] }} />);
    const marker = mapLayers(maps[0]).find((layer): layer is Leaflet.Marker => layer instanceof Leaflet.Marker && layer.getElement()?.classList.contains('wb-map-pick-marker') === true)!;
    const expected = enuToLonLat(300, -120, ORIGIN);
    expect(marker.getLatLng().lng).toBeCloseTo(expected.lon, 10);
    expect(marker.getLatLng().lat).toBeCloseTo(expected.lat, 10);
    expect(screen.getByTestId('wb-map-size')).toBeVisible();
  });

  it('expands an empty map safely and reports explicitly disabled tiles', () => {
    render(<RegionMap L={L} basemap={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'Expand map' }));
    expect(screen.getByRole('button', { name: 'Expand map' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('No basemap configured — bounds and geometry are shown')).toBeVisible();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('geometry shapes', () => {
  it('retains holes and MultiPolygon members instead of flattening a false filled footprint', () => {
    const outer = [[0, 0], [10, 0], [10, 10], [0, 0]], hole = [[1, 1], [2, 1], [2, 2], [1, 1]];
    expect(geometryPolygons([{ geometry: { type: 'MultiPolygon', coordinates: [[outer, hole], [outer]] } }])).toEqual([[outer, hole], [outer]]);
    expect(enuLines([{ id: 'r', points: [[0, 0, 0], [100, 50, 0]], tags: { highway: 'trunk' } }])).toEqual([[[0, 0], [100, 50]]]);
    expect(() => enuLines([{ id: 'bad', points: [[NaN, 0, 0], [1, 2, 0]] }])).toThrow(/finite/);
  });
});
