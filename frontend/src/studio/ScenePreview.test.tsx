import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';

const mocks = vi.hoisted(() => ({ renderers: 0, draws: 0, scene: undefined as THREE.Scene | undefined, camera: undefined as THREE.PerspectiveCamera | undefined, city: vi.fn(), osm: vi.fn() }));
vi.mock('three', async importOriginal => {
  const actual = await importOriginal<typeof import('three')>();
  class FakeRenderer {
    domElement = document.createElement('canvas');
    constructor() { mocks.renderers++; }
    setPixelRatio() {} setSize() {} render(scene: THREE.Scene, camera: THREE.PerspectiveCamera) { mocks.draws++; mocks.scene = scene; mocks.camera = camera; } dispose() {}
  }
  return { ...actual, WebGLRenderer: FakeRenderer };
});
vi.mock('three/addons/controls/OrbitControls.js', () => ({ OrbitControls: class { target = new THREE.Vector3(); update() {} dispose() {} addEventListener() {} } }));
vi.mock('../viewport/assets', async importOriginal => ({ ...await importOriginal<object>(), Assets: class { dispose() {} } }));
vi.mock('../scene/traffic-city', () => ({ loadTrafficCity: mocks.city }));
vi.mock('../scene/source-geometry', () => ({ loadOsmBuildings: mocks.osm }));
import { authoredPositions, draftBinding, framePreview, ScenePreview } from './ScenePreview';

const workspace: any = {
  id: 'w1', demo_console: { scene: { city: { kind: 'traffic-city', url: '/city/scene.json' } } },
  scene: { ground: { width_m: 300, depth_m: 300 }, origin: { lat: 1, lon: 2, alt: 0 }, roads: [{ points: [[0, 0, 0], [10, 10, 0]] }] },
  scenario: { city: 'huangpu', presentation: [{ typeId: 't', positionField: 'pos' }], entities: [
    { type: 't', facts: { pos: [1, 2, 3], 'aas.studio.airspace': { floor_m: 0, ceiling_m: 50, polygon: [[0, 0], [5, 0], [5, 5]] } } }] },
};
const ALL = ['ground', 'buildings', 'roads', 'entities', 'airspace'];

beforeEach(() => {
  mocks.renderers = 0; mocks.draws = 0; mocks.scene = undefined;
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

  it('drops the shared city when the draft no longer declares it', async () => {
    const view = render(<ScenePreview workspace={workspace} api="/api" layers={ALL} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalledTimes(1));
    view.rerender(<ScenePreview workspace={{ ...workspace, scenario: { ...workspace.scenario, city: null } }} api="/api" layers={ALL} />);
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(mocks.city).toHaveBeenCalledTimes(1);
  });

  it('rebuilds only for a genuinely different scene', async () => {
    const view = render(<ScenePreview workspace={workspace} api="/api" layers={ALL} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalledTimes(1));
    view.rerender(<ScenePreview workspace={{ ...workspace, id: 'w2' }} api="/api" layers={ALL} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalledTimes(2));
    expect(mocks.renderers).toBe(2);
  });

  it('shows streamed geometry and progress before the city finishes loading', async () => {
    let finish!: (group: THREE.Group) => void;
    const city = new THREE.Group(); city.add(new THREE.Mesh(new THREE.BoxGeometry(2000, 150, 800)));
    mocks.city.mockImplementation((_url, _assets, _signal, options) => {
      options.onUpdate(city, { phase: 'buildings', loaded: 8, total: 300 });
      return new Promise<THREE.Group>(resolve => { finish = resolve; });
    });
    render(<ScenePreview bare workspace={workspace} api="/api" layers={ALL} />);
    expect(screen.getByRole('status')).toHaveTextContent('Buildings 8 / 300');
    expect(mocks.scene?.getObjectByName('layer-buildings')?.children).toContain(city);
    const ground = mocks.scene?.getObjectByName('layer-ground')?.children[0] as THREE.Mesh<THREE.PlaneGeometry>;
    expect(ground.geometry.parameters.width).toBeGreaterThan(2000);
    expect(ground.geometry.parameters.height).toBeGreaterThan(800);
    expect(screen.getByTestId('draft-preview')).toHaveAttribute('data-rendered', 'true');
    await act(async () => { finish(city); });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('frames every corner of a tall, off-center city at narrow and wide aspects', () => {
    const box = new THREE.Box3(new THREE.Vector3(-700, 0, -100), new THREE.Vector3(2300, 240, 600));
    for (const aspect of [1.6, 1920 / 1080, 0.8]) {
      const camera = new THREE.PerspectiveCamera(45, aspect), target = new THREE.Vector3();
      framePreview(camera, box, target); camera.lookAt(target); camera.updateMatrixWorld();
      expect(camera.position.y).toBeGreaterThan(box.max.y);
      for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
        const projected = new THREE.Vector3(x, y, z).project(camera);
        expect(Math.abs(projected.x)).toBeLessThan(1); expect(Math.abs(projected.y)).toBeLessThan(1);
        expect(projected.z).toBeLessThan(1);
      }
    }
  });

  it('keeps the city in the unobstructed area beside the configuration panel', () => {
    const box = new THREE.Box3(new THREE.Vector3(-700, 0, -100), new THREE.Vector3(2300, 240, 600));
    const camera = new THREE.PerspectiveCamera(45), target = new THREE.Vector3();
    const frame = { x: 20, y: 120, width: 580, height: 600, fullWidth: 1280, fullHeight: 800 };
    framePreview(camera, box, target, frame); camera.lookAt(target); camera.updateMatrixWorld();
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      const point = new THREE.Vector3(x, y, z).project(camera);
      const px = (point.x + 1) * 640, py = (1 - point.y) * 400;
      expect(px).toBeGreaterThan(frame.x); expect(px).toBeLessThan(frame.x + frame.width);
      expect(py).toBeGreaterThan(frame.y); expect(py).toBeLessThan(frame.y + frame.height);
    }
  });

  it('reframes automatically when the configuration panel mounts after the city', async () => {
    const bounds = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      const id = this.getAttribute('data-testid');
      return { x: 0, y: 0, left: id === 'wb-config' ? 628 : 0, top: 0, right: 1280, bottom: id === 'top' ? 103 : 800, width: 1280, height: 800, toJSON() {} };
    });
    try {
      const Shell = ({ panel = false }: { panel?: boolean }) => <div data-testid="workbench"><div className="wb-top" data-testid="top" /><ScenePreview bare workspace={workspace} api="/api" layers={ALL} />{panel && <aside data-testid="wb-config" />}</div>;
      const view = render(<Shell />);
      await vi.waitFor(() => expect(mocks.camera).toBeDefined());
      expect(mocks.camera!.view).toBeNull();
      view.rerender(<Shell panel />);
      await vi.waitFor(() => expect(mocks.camera!.view?.enabled).toBe(true));
      expect(mocks.camera!.view?.fullWidth).toBe(1280);
      expect(mocks.renderers).toBe(1); expect(mocks.city).toHaveBeenCalledTimes(1);
    } finally { bounds.mockRestore(); }
  });

  it('renders locations for real subtypes with an inherited position binding', () => {
    const scenario = { registry: { types: [{ id: 'Restaurant', parents: ['Site'] }, { id: 'Site', parents: [] }] }, presentation: [{ typeId: 'Site', positionField: 'site.position', visual: { color: '#ffc658' } }], entities: [{ id: 'kitchen', type: 'Restaurant', facts: { 'site.position': [10, 20, 30] } }] };
    expect(draftBinding(scenario, 'Restaurant')).toBe(scenario.presentation[0]);
    expect(authoredPositions(scenario)).toEqual([[10, 20, 30]]);
    render(<ScenePreview workspace={{ ...workspace, scenario }} api="/api" layers={['entities']} />);
    const marker = mocks.scene?.getObjectByName('kitchen') as THREE.Sprite;
    expect(marker.position.toArray()).toEqual([10, 30, -20]); expect(marker.material.sizeAttenuation).toBe(false);
    expect(draftBinding(scenario, 'UnboundType')).toBeUndefined();
  });

  it('refits the ground when saved entity positions change, without reloading the city', async () => {
    const view = render(<ScenePreview workspace={workspace} api="/api" layers={ALL} />);
    await vi.waitFor(() => expect(mocks.city).toHaveBeenCalledTimes(1));
    view.rerender(<ScenePreview workspace={{ ...workspace, scenario: { ...workspace.scenario, entities: [{ type: 't', facts: { pos: [8000, 9000, 30] } }] } }} api="/api" layers={ALL} />);
    const ground = mocks.scene!.getObjectByName('layer-ground')!.children[0] as THREE.Mesh<THREE.PlaneGeometry>;
    expect(ground.geometry.parameters.width).toBeGreaterThan(8000);
    expect(ground.geometry.parameters.height).toBeGreaterThan(9000);
    expect(mocks.renderers).toBe(1); expect(mocks.city).toHaveBeenCalledTimes(1);
  });

  it('redraws changes while leaving a static preview idle', async () => {
    const view = render(<ScenePreview workspace={workspace} api="/api" layers={ALL} />);
    await vi.waitFor(() => expect(mocks.draws).toBeGreaterThan(1));
    const settled = mocks.draws;
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 80)); });
    expect(mocks.draws).toBe(settled);
    view.rerender(<ScenePreview workspace={workspace} api="/api" layers={['ground']} />);
    await vi.waitFor(() => expect(mocks.draws).toBeGreaterThan(settled));
    expect(mocks.scene!.getObjectByName('layer-buildings')!.visible).toBe(false);
  });
});

it('T1-R3-005: depicts an area only at its authored polygon, ignoring its legacy origin anchor', () => {
  const area = { id: 'zone-away', type: 'airspace', facts: { pos: [0, 0, 0], 'aas.studio.airspace': {
    floor_m: 10, ceiling_m: 80, polygon: [[1000, 1000], [1040, 1000], [1040, 1040], [1000, 1040]],
  } } };
  const scenario = { presentation: [{ typeId: 'airspace', positionField: 'pos' }], entities: [area] };
  expect(authoredPositions(scenario)).toEqual([
    [1000, 1000, 10], [1000, 1000, 80], [1040, 1000, 10], [1040, 1000, 80],
    [1040, 1040, 10], [1040, 1040, 80], [1000, 1040, 10], [1000, 1040, 80],
  ]);
  render(<ScenePreview bare workspace={{ id: 'area-only', scenario } as any} api="/api" layers={ALL} />);
  expect(mocks.scene!.getObjectByName('layer-airspace')!.children).toHaveLength(2);
  expect(mocks.scene!.getObjectByName('layer-entities')!.children).toHaveLength(0);
  expect(screen.getByTestId('draft-preview')).toHaveAttribute('data-marker-positions', '[]');
  const ground = mocks.scene!.getObjectByName('layer-ground')!.children[0] as THREE.Mesh<THREE.PlaneGeometry>;
  expect(ground.position.x).toBe(1020);
  expect(ground.position.z).toBe(-1020);
  expect(mocks.camera!.position.y).toBeGreaterThan(80);
});

it('T1-R3-005: a malformed area never becomes a fabricated point at its legacy anchor', () => {
  const scenario = { presentation: [{ typeId: 'airspace', positionField: 'pos' }], entities: [
    { id: 'bad-zone', type: 'airspace', facts: { pos: [0, 0, 0], 'aas.studio.airspace': { polygon: null, floor_m: 10, ceiling_m: 80 } } },
  ] };
  expect(authoredPositions(scenario)).toEqual([]);
  render(<ScenePreview workspace={{ ...workspace, scenario }} api="/api" layers={ALL} />);
  expect(mocks.scene!.getObjectByName('layer-entities')!.children).toHaveLength(0);
  expect(mocks.scene!.getObjectByName('layer-airspace')!.children).toHaveLength(0);
});

it('D15: the ground covers out-of-bounds roads on initial render, road edits and entity edits', () => {
  const initial = { ...workspace, scenario: {}, scene: { ...workspace.scene, roads: [{ points: [[-1500, -2400, 0], [2500, 3400, 0]] }] } };
  const view = render(<ScenePreview workspace={initial} api="/api" layers={['ground', 'roads']} />);
  const covered = (points: number[][]) => {
    const ground = mocks.scene!.getObjectByName('layer-ground')!.children[0] as THREE.Mesh<THREE.PlaneGeometry>;
    for (const [east, north] of points) {
      expect(Math.abs(east - ground.position.x)).toBeLessThan(ground.geometry.parameters.width / 2);
      expect(Math.abs(-north - ground.position.z)).toBeLessThan(ground.geometry.parameters.height / 2);
    }
  };
  covered(initial.scene.roads[0].points);
  const next = { ...initial, scene: { ...initial.scene, roads: [{ points: [[-7500, 6100, 0], [5200, -8400, 0]] }] } };
  view.rerender(<ScenePreview workspace={next} api="/api" layers={['ground', 'roads']} />);
  covered(next.scene.roads[0].points);
  view.rerender(<ScenePreview workspace={{ ...next, scenario: { presentation: [{ typeId: 't', positionField: 'pos' }], entities: [{ id: 'point', type: 't', facts: { pos: [20, 30, 0] } }] } }} api="/api" layers={ALL} />);
  covered(next.scene.roads[0].points);
  expect(mocks.renderers).toBe(1);
});
