import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';

const mocks = vi.hoisted(() => ({ renderers: 0, scene: undefined as THREE.Scene | undefined, city: vi.fn(), osm: vi.fn() }));
vi.mock('three', async importOriginal => {
  const actual = await importOriginal<typeof import('three')>();
  class FakeRenderer {
    domElement = document.createElement('canvas');
    constructor() { mocks.renderers++; }
    setPixelRatio() {} setSize() {} render(scene: THREE.Scene) { mocks.scene = scene; } dispose() {}
  }
  return { ...actual, WebGLRenderer: FakeRenderer };
});
vi.mock('three/addons/controls/OrbitControls.js', () => ({ OrbitControls: class { target = { set() {} }; update() {} dispose() {} } }));
vi.mock('../viewport/assets', async importOriginal => ({ ...await importOriginal<object>(), Assets: class { dispose() {} } }));
vi.mock('../scene/traffic-city', () => ({ loadTrafficCity: mocks.city }));
vi.mock('../scene/source-geometry', () => ({ loadOsmBuildings: mocks.osm }));
import { ScenePreview } from './ScenePreview';

const workspace: any = {
  id: 'w1', demo_console: { scene: { city: { kind: 'traffic-city', url: '/city/scene.json' } } },
  scene: { ground: { width_m: 300, depth_m: 300 }, origin: { lat: 1, lon: 2, alt: 0 }, roads: [{ points: [[0, 0, 0], [10, 10, 0]] }] },
  scenario: { presentation: [{ typeId: 't', positionField: 'pos' }], entities: [
    { type: 't', facts: { pos: [1, 2, 3], 'aas.studio.airspace': { floor_m: 0, ceiling_m: 50, polygon: [[0, 0], [5, 0], [5, 5]] } } }] },
};
const ALL = ['ground', 'buildings', 'roads', 'entities', 'airspace'];

beforeEach(() => {
  mocks.renderers = 0; mocks.scene = undefined;
  mocks.city.mockReset().mockImplementation(async () => new THREE.Group());
  mocks.osm.mockReset().mockImplementation(async () => new THREE.Group());
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('ScenePreview layer toggles', () => {
  it('keeps renderer, canvas and loaded city across layer toggles and follows the checkboxes', async () => {
    const view = render(<ScenePreview workspace={workspace} api="/api" layers={ALL} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalledTimes(1));
    const canvas = view.container.querySelector('canvas');
    for (const layers of [ALL.filter(l => l !== 'airspace'), ALL.filter(l => l !== 'roads'), ['ground'], ALL, ALL.filter(l => l !== 'entities')]) {
      view.rerender(<ScenePreview workspace={{ ...workspace }} api="/api" layers={layers} />);
    }
    expect(mocks.renderers).toBe(1);
    expect(mocks.city).toHaveBeenCalledTimes(1);
    expect(mocks.osm).toHaveBeenCalledTimes(1);
    expect(view.container.querySelector('canvas')).toBe(canvas);
  });

  it('does not load buildings while hidden, and loads them once when first shown', async () => {
    const view = render(<ScenePreview workspace={workspace} api="/api" layers={['ground']} />);
    expect(mocks.city).not.toHaveBeenCalled();
    view.rerender(<ScenePreview workspace={workspace} api="/api" layers={['ground', 'buildings']} />);
    view.rerender(<ScenePreview workspace={workspace} api="/api" layers={['ground']} />);
    view.rerender(<ScenePreview workspace={workspace} api="/api" layers={['ground', 'buildings']} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalledTimes(1));
    expect(mocks.renderers).toBe(1);
  });

  it('renders airspace polygons without a position binding and skips malformed spatial values', () => {
    render(<ScenePreview workspace={{ ...workspace, scenario: { presentation: [], entities: [
      { type: 'airspace', facts: { 'aas.studio.airspace': { floor_m: 10, ceiling_m: 60, polygon: [[0, 0], [10, 0], [10, 10]] } } },
      { type: 'bad', facts: { 'aas.studio.airspace': { floor_m: 10, ceiling_m: 60, polygon: null } } },
    ] } }} api="/api" layers={['airspace']} />);
    expect(mocks.scene?.getObjectByName('layer-airspace')?.children).toHaveLength(2);
    expect(mocks.scene?.getObjectByName('layer-entities')?.children).toHaveLength(0);
  });

  it('keeps loading status until all requested building sources settle', async () => {
    let resolveCity!: (group: THREE.Group) => void, resolveOsm!: (group: THREE.Group) => void;
    mocks.city.mockImplementation(() => new Promise<THREE.Group>(resolve => { resolveCity = resolve; }));
    mocks.osm.mockImplementation(() => new Promise<THREE.Group>(resolve => { resolveOsm = resolve; }));
    render(<ScenePreview workspace={workspace} api="/api" layers={ALL} />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading city geometry…');
    await act(async () => { resolveCity(new THREE.Group()); });
    expect(screen.getByRole('status')).toHaveTextContent('Loading city geometry…');
    await act(async () => { resolveOsm(new THREE.Group()); });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('rebuilds only for a genuinely different scene', async () => {
    const view = render(<ScenePreview workspace={workspace} api="/api" layers={ALL} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalledTimes(1));
    view.rerender(<ScenePreview workspace={{ ...workspace, id: 'w2' }} api="/api" layers={ALL} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalledTimes(2));
    expect(mocks.renderers).toBe(2);
  });
});
