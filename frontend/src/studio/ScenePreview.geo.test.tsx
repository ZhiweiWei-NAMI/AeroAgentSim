import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';

const mocks = vi.hoisted(() => ({ renderers: 0, draws: 0, city: vi.fn(), osm: vi.fn() }));
vi.mock('three', async importOriginal => {
  const actual = await importOriginal<typeof import('three')>();
  class FakeRenderer {
    domElement = document.createElement('canvas');
    constructor() { mocks.renderers++; }
    setPixelRatio() {} setSize() {} render() { mocks.draws++; } dispose() {}
  }
  return { ...actual, WebGLRenderer: FakeRenderer };
});
vi.mock('three/addons/controls/OrbitControls.js', () => ({ OrbitControls: class { target = new THREE.Vector3(); update() {} dispose() {} addEventListener() {} } }));
vi.mock('../viewport/assets', async importOriginal => ({ ...await importOriginal<object>(), Assets: class { dispose() {} } }));
vi.mock('../scene/traffic-city', () => ({ loadTrafficCity: mocks.city }));
vi.mock('../scene/source-geometry', () => ({ loadOsmBuildings: mocks.osm }));
import { ScenePreview } from './ScenePreview';

const blank: any = { id: 'w1', scenario: {} };
const withRegion: any = {
  id: 'w1', scene: { ground: { width_m: 500, depth_m: 500 }, origin: { lat: 1, lon: 2, alt: 0 }, roads: [], geojson: { features: [] } }, scenario: {},
};
const withCity: any = {
  id: 'w1', demo_console: { scene: { city: { kind: 'traffic-city', url: '/city/scene.json' } } }, scenario: { city: 'huangpu' },
};

beforeEach(() => {
  mocks.renderers = 0; mocks.draws = 0;
  mocks.city.mockReset().mockImplementation(async () => new THREE.Group());
  mocks.osm.mockReset().mockImplementation(async () => new THREE.Group());
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('ScenePreview geography empty states', () => {
  it('labels the blank project plane as a schematic local ENU plane and points to Space > Region', async () => {
    const view = render(<ScenePreview workspace={blank} api="/api" layers={['ground', 'buildings']} />);
    expect(view.getByTestId('plane-note')).toBeTruthy();
    expect(view.getByTestId('plane-note').textContent).toContain('not geography');
    expect(view.getByTestId('plane-note').textContent).toContain('Region');
    expect(view.queryByTestId('no-objects')).toBeNull();
  });

  it('keeps city and region geometry visible with zero objects and reports No places yet', async () => {
    const view = render(<ScenePreview workspace={withRegion} api="/api" layers={['ground', 'buildings']} />);
    await vi.waitFor(() => expect(mocks.osm).toHaveBeenCalled());
    expect(view.getByTestId('no-objects').textContent).toContain('No places yet');
    expect(view.queryByTestId('plane-note')).toBeNull();
    view.unmount();
    const cityView = render(<ScenePreview workspace={withCity} api="/api" layers={['ground', 'buildings']} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalled());
    expect(cityView.getByTestId('no-objects')).toBeTruthy();
    expect(cityView.queryByTestId('plane-note')).toBeNull();
  });

  it('shows neither overlay once objects exist', () => {
    const populated: any = { ...withRegion, scenario: { entities: [{ type: 'lowalt:Depot', facts: { 'lowalt.site_position': [1, 2, 3] } }] } };
    const view = render(<ScenePreview workspace={populated} api="/api" layers={['ground', 'buildings']} />);
    expect(view.queryByTestId('plane-note')).toBeNull();
    expect(view.queryByTestId('no-objects')).toBeNull();
  });

  it('does not break the renderer lifecycle for these overlays', async () => {
    render(<ScenePreview workspace={blank} api="/api" layers={['ground']} />);
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
    expect(mocks.renderers).toBe(1);
  });

  it('counts a fleet even before it has an explicit presentation binding', () => {
    const fleet = { ...withRegion, scenario: { entities: [{ id: 'drone-1', type: 'lowalt:UAV', facts: { 'lowalt.position': [0, 0, 0] } }] } };
    const view = render(<ScenePreview workspace={fleet} api="/api" layers={['ground']} />);
    expect(view.queryByTestId('no-objects')).toBeNull();
    view.rerender(<ScenePreview workspace={{ ...withRegion, scenario: { entities: [{ id: 'weather', type: 'lowalt:Environment', facts: {} }] } }} api="/api" layers={['ground']} />);
    expect(view.getByTestId('no-objects')).toHaveTextContent('No places yet');
  });

  it('loads proposal buildings from its own compiled GeoJSON and reloads changed proposal geometry', async () => {
    const proposal = { ...withRegion, proposal: true };
    const view = render(<ScenePreview workspace={proposal} api="/api" layers={['ground', 'buildings']} />);
    await vi.waitFor(() => expect(mocks.osm).toHaveBeenCalledWith(proposal.scene.geojson, proposal.scene.origin, expect.any(AbortSignal)));
    const changed = { ...proposal, scene: { ...proposal.scene, geojson: { type: 'FeatureCollection', features: [{ id: 'new' }] } } };
    view.rerender(<ScenePreview workspace={changed} api="/api" layers={['ground', 'buildings']} />);
    await vi.waitFor(() => expect(mocks.osm).toHaveBeenCalledWith(changed.scene.geojson, changed.scene.origin, expect.any(AbortSignal)));
    expect(mocks.osm.mock.calls.every(([source]) => typeof source === 'object')).toBe(true);
  });
});
