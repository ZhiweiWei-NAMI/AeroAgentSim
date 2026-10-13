import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import * as Leaflet from 'leaflet';
import { ApiError, WorkbenchApi } from '../../api';
import { enuToLonLat } from '../../geo';
import { PlacePickerMap } from './PlacePickerMap';
import { captureMaps, forceSvgRenderer, mapLayers } from './leafletTestSetup';
import { APPLIED_REGION, BASEMAP, COVERAGE, DRAFT, ORIGIN, SCENE } from './mapTestFixtures';

const L = (en: string, _zh: string) => en;
const workspace = { region: APPLIED_REGION, scene: SCENE };
let maps: Leaflet.Map[];
beforeAll(forceSvgRenderer);
beforeEach(() => { maps = captureMaps(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function apiWithCatalog(catalog: unknown = { extracts: [{ id: 'wujiaochang', bounds: COVERAGE }], basemap: BASEMAP }) {
  const request = vi.fn(async (_url: string, _init?: { signal?: AbortSignal }) => catalog);
  return Object.assign(new WorkbenchApi('http://api.test'), { request });
}
const pickMarkers = (map: Leaflet.Map) => mapLayers(map).filter((layer): layer is Leaflet.Marker => layer instanceof Leaflet.Marker && layer.getElement()?.classList.contains('wb-map-pick-marker') === true);

describe('shared PlacePickerMap (T2-R2-009 / T4-R2-008 / D8)', () => {
  it('loads the same C9 basemap and draws applied bounds, compiled buildings/road and role markers', async () => {
    const api = apiWithCatalog();
    const view = render(<PlacePickerMap L={L} api={api} workspace={workspace} draft={DRAFT} position={[0, 0]} onPick={vi.fn()} />);
    await waitFor(() => expect(mapLayers(maps[0]).some(layer => layer instanceof Leaflet.TileLayer)).toBe(true));
    const layers = mapLayers(maps[0]);
    const tiles = layers.find((layer): layer is Leaflet.TileLayer => layer instanceof Leaflet.TileLayer)!;
    expect(tiles.getTileUrl({ x: 1, y: 2, z: 14 } as Leaflet.Coords)).toContain('tiles.test');
    expect(tiles.options).toMatchObject({ attribution: BASEMAP.attribution, maxZoom: 18 });
    expect(layers.filter(layer => layer instanceof Leaflet.Polygon && !(layer instanceof Leaflet.Rectangle))).toHaveLength(3);
    expect(layers.filter(layer => layer instanceof Leaflet.Polyline && !(layer instanceof Leaflet.Polygon))).toHaveLength(1);
    expect(layers.filter(layer => layer instanceof Leaflet.Rectangle)).toHaveLength(2);
    expect(view.container.querySelector('.wb-map-handle')).toBeNull();
    const kitchen = layers.find((layer): layer is Leaflet.Marker => layer instanceof Leaflet.Marker && layer.options.title === 'Kitchen · kitchen')!;
    expect(kitchen.getElement()?.querySelector('.wb-map-dot')).toHaveTextContent('K');
    expect(kitchen.getElement()?.querySelector('.wb-map-dot')).toHaveStyle({ backgroundColor: '#e0453a' });
    expect(layers.some(layer => layer instanceof Leaflet.Marker && layer.options.title === 'Meal courier 1 · courier-1')).toBe(true);
    expect(layers.some(layer => layer instanceof Leaflet.Marker && layer.options.title === 'weather')).toBe(false);
    expect(api.request).toHaveBeenCalledTimes(1);
    expect(api.request.mock.calls[0][0]).toBe('/v1/studio/catalog');
  });

  it('fills x/y from a click, keeps the picker open and moves its marker on typed x/y edits', async () => {
    const api = apiWithCatalog();
    function Form() {
      const [east, setEast] = useState('0'), [north, setNorth] = useState('0');
      const valid = east.trim() !== '' && north.trim() !== '' && [Number(east), Number(north)].every(Number.isFinite);
      return <><label>East<input value={east} onChange={event => setEast(event.target.value)} /></label>
        <label>North<input value={north} onChange={event => setNorth(event.target.value)} /></label>
        <PlacePickerMap L={L} api={api} workspace={workspace} draft={DRAFT} position={valid ? [Number(east), Number(north)] : null}
          onPick={(e, n) => { setEast(e.toFixed(1)); setNorth(n.toFixed(1)); }} /></>;
    }
    render(<Form />);
    const picked = enuToLonLat(207.9, -54.1, ORIGIN);
    act(() => maps[0].fire('click', { latlng: Leaflet.latLng(picked.lat, picked.lon) }));
    expect(screen.getByLabelText('East')).toHaveValue('207.9');
    expect(screen.getByLabelText('North')).toHaveValue('-54.1');
    expect(screen.getByTestId('wb-map-surface')).toBeVisible();
    fireEvent.change(screen.getByLabelText('East'), { target: { value: '300' } });
    fireEvent.change(screen.getByLabelText('North'), { target: { value: '100' } });
    const moved = enuToLonLat(300, 100, ORIGIN);
    expect(pickMarkers(maps[0])[0].getLatLng().lng).toBeCloseTo(moved.lon, 10);
    expect(pickMarkers(maps[0])[0].getLatLng().lat).toBeCloseTo(moved.lat, 10);
    fireEvent.change(screen.getByLabelText('East'), { target: { value: '' } });
    expect(pickMarkers(maps[0])).toHaveLength(0);
    expect(screen.getByLabelText('East')).toHaveValue('');
    await waitFor(() => expect(api.request).toHaveBeenCalledTimes(1));
  });

  it('uses the server/applied origin and reports picks outside the applied bounds', () => {
    const onPick = vi.fn();
    const view = render(<PlacePickerMap L={L} api={apiWithCatalog()} workspace={workspace} draft={DRAFT} position={[5000, 0]} onPick={onPick} />);
    expect(screen.getByText(/Outside the applied region/)).toBeVisible();
    const point = enuToLonLat(5000, 0, ORIGIN);
    act(() => maps[0].fire('click', { latlng: Leaflet.latLng(point.lat, point.lon) }));
    expect(onPick.mock.calls[0][0]).toBeCloseTo(5000, 6);
    expect(onPick.mock.calls[0][2]).toBe(false);
    view.rerender(<PlacePickerMap L={L} api={apiWithCatalog()} workspace={workspace} draft={DRAFT} position={[0, 0]} onPick={onPick} />);
    expect(screen.queryByText(/Outside the applied region/)).toBeNull();
  });

  it('shows the missing applied-region prerequisite with a one-click way forward', () => {
    const api = apiWithCatalog(), onOpenRegion = vi.fn();
    render(<PlacePickerMap L={L} api={api} workspace={{}} draft={{ origin: ORIGIN }} position={[0, 0]} onPick={vi.fn()} onOpenRegion={onOpenRegion} />);
    expect(screen.getByText(/Apply a region in Scene & space/)).toBeVisible();
    expect(screen.queryByTestId('wb-map-surface')).toBeNull();
    expect(api.request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Select region' }));
    expect(onOpenRegion).toHaveBeenCalledTimes(1);
  });

  it('reports unavailable tiles settings without hiding the real applied geometry or inventing a basemap', async () => {
    const api = apiWithCatalog();
    api.request.mockRejectedValue(new ApiError(404, 'not found'));
    render(<PlacePickerMap L={L} api={api} workspace={workspace} draft={DRAFT} position={null} onPick={vi.fn()} />);
    expect(await screen.findByText(/Map background unavailable/)).toBeVisible();
    expect(mapLayers(maps[0]).some(layer => layer instanceof Leaflet.TileLayer)).toBe(false);
    expect(mapLayers(maps[0]).filter(layer => layer instanceof Leaflet.Polygon && !(layer instanceof Leaflet.Rectangle))).toHaveLength(3);
  });

  it('localizes markers/controls and visibly identifies invalid source positions', async () => {
    const zh = (_en: string, zh: string) => zh;
    const draft = { ...DRAFT, entities: [...DRAFT.entities, { id: 'bad', type: 'lowalt:Customer', facts: { 'lowalt.site_position': [null, 10, 0] } }] };
    render(<PlacePickerMap L={zh} api={apiWithCatalog()} workspace={workspace} draft={draft} position={null} onPick={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('请修正以下无效位置以显示标记：bad');
    expect(screen.getByRole('button', { name: '放大' })).toBeVisible();
    const kitchen = mapLayers(maps[0]).find((layer): layer is Leaflet.Marker => layer instanceof Leaflet.Marker && layer.options.title === '餐厅 · kitchen')!;
    expect(kitchen.getElement()?.querySelector('.wb-map-dot')).toHaveTextContent('餐');
    expect(screen.queryByText(/Click to place/)).toBeNull();
    await waitFor(() => expect(screen.queryByText('正在加载地图底图…')).toBeNull());
  });
});
