import { describe, expect, it, vi } from 'vitest';
import * as T from 'three';
import { Viewport, followCameraOffset } from './viewport';
import { StaticOcclusion } from './occlusion';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { at, emptyCommit, fixtureCommits, fixtureHeader, vehicle } from '../behaviours/extension-fixture';
import { CameraDirector } from '../scene/camera-director';

// Exercise the real camera/cache logic; replace only GPU submission and glyphs.
function display(mode: 'follow' | 'chase' | 'cinematic', semantic = true) {
  const store = new TemporalFeedStore(fixtureHeader);
  store.ingest(fixtureCommits[0]); store.seek('0');
  const container = document.createElement('div'), camera = new T.PerspectiveCamera();
  camera.position.set(140, 95, 170);
  const controls = { target: new T.Vector3(), update: vi.fn(), enabled: false };
  const positions = new Map<string, T.Vector3>();
  const layer = {
    positions, orientations: new Map(), selection: [], ring: { visible: false }, transformsChanged: false,
    labelsMs: 0, payloadCount: 0, highlightedShown: 0,
    update: (_store: unknown, ns: string, _camera: unknown, _chosen: unknown, _trails: unknown, _origin: unknown, _occlusion: unknown, aim?: () => void) => {
      const position = store.sample(vehicle, 'position', ns) as number[];
      positions.set('vehicle-1:2', new T.Vector3(position[0], position[2], -position[1]));
      aim?.();
    },
  };
  const submit = vi.fn();
  const viewport = Object.assign(Object.create(Viewport.prototype), {
    container, camera, controls, layer, mode, selected: vehicle, options: { semantic },
    staticRoot: new T.Group(), origin: new T.Vector3(), occlusion: new StaticOcclusion(),
    framed: true, cameraSettled: false, incidentLabels: new Map(),
    revision: 0, lastFrame: '', renderedFrames: 0, shadowKey: '', shadowRevision: -1,
    quality: 'low', qualityTime: 0, qualityFrames: 0,
    lighting: { update: vi.fn(), sun: { position: new T.Vector3() } },
    renderer: { shadowMap: {}, info: { reset: vi.fn(), render: { calls: 0, triangles: 0 }, memory: { geometries: 0, textures: 0 } } },
    composer: { render: submit, outline: { selection: { set: vi.fn() } } },
  }) as Viewport;
  const settle = () => { for (let i = 0; i < 100; i++) viewport.render(store, '0', 0.1); };
  return { viewport, store, container, camera, controls, submit, settle };
}

describe('unchanged tracked views', () => {
  it.each(['follow', 'chase'] as const)('stops GPU submissions after a paused %s camera settles', mode => {
    const view = display(mode); view.settle();
    expect(view.container.dataset.idle).toBe('true');
    const count = view.submit.mock.calls.length;
    for (let i = 0; i < 100; i++) view.viewport.render(view.store, '0', 0.1);
    expect(view.submit).toHaveBeenCalledTimes(count);
    const focus = new T.Vector3(10, 5, -20);
    expect(view.controls.target.toArray()).toEqual(focus.toArray());
    expect(view.camera.position.toArray()).toEqual(focus.add(mode === 'follow' ? followCameraOffset(focus.y) : new T.Vector3(-4, 2.4, 5.5)).toArray());
  });

  it('redraws a new recorded pose and a camera gesture after a settled follow view', () => {
    const view = display('follow'); view.settle(); const count = view.submit.mock.calls.length;
    view.store.ingest({ ...emptyCommit(2, '1000'), facts: [{ entity: vehicle, fieldId: 'position', value: [50, 60, 15], producer: 'motion', validFrom: at('1000') }] });
    view.store.seek('1000'); view.viewport.render(view.store, '1000', 0.1);
    expect(view.submit).toHaveBeenCalledTimes(count + 1); expect(view.container.dataset.idle).toBe('false');
    view.viewport.zoom(1.2); view.viewport.render(view.store, '1000', 0.1);
    expect(view.submit).toHaveBeenCalledTimes(count + 2); expect(view.container.dataset.idle).toBe('false');
  });

  it('keeps the continuously orbiting expert director animated', () => {
    const view = display('cinematic', false);
    // The actual director owns the continuous wall-time camera movement.
    Object.assign(view.viewport, { director: new CameraDirector() });
    view.settle(); const count = view.submit.mock.calls.length;
    view.viewport.render(view.store, '0', 0.1);
    expect(view.submit).toHaveBeenCalledTimes(count + 1); expect(view.container.dataset.idle).toBe('false');
  });

  it('reuses world-space shadows for camera gestures and refreshes changed casters and light', () => {
    const view = display('follow'); view.settle();
    view.viewport.renderer.shadowMap.needsUpdate = false;
    view.viewport.zoom(1.1); view.viewport.render(view.store, '0', 0.1);
    expect(view.viewport.renderer.shadowMap.needsUpdate).toBe(false);
    const layer = (view.viewport as unknown as { layer: { transformsChanged: boolean } }).layer;
    layer.transformsChanged = true;
    view.viewport.zoom(1.1); view.viewport.render(view.store, '0', 0.1);
    expect(view.viewport.renderer.shadowMap.needsUpdate).toBe(true);
    layer.transformsChanged = false; view.viewport.renderer.shadowMap.needsUpdate = false;
    const lighting = (view.viewport as unknown as { lighting: { sun: T.Light } }).lighting;
    lighting.sun.position.x += 1;
    view.viewport.zoom(1.1); view.viewport.render(view.store, '0', 0.1);
    expect(view.viewport.renderer.shadowMap.needsUpdate).toBe(true);
  });

  it('reuses an existing quality pipeline when returning to that quality', () => {
    const view = display('follow');
    const low = { dispose: vi.fn(), setDusk: vi.fn() }, med = { dispose: vi.fn(), setDusk: vi.fn() };
    const applyPreset = vi.fn(), resize = vi.fn(), onQuality = vi.fn();
    Object.assign(view.viewport, { composers: new Map([['low', low], ['med', med]]), composer: low,
      applyPreset, resize, options: { onQuality } });
    view.viewport.setQuality('med'); view.viewport.setQuality('low'); view.viewport.setQuality('med');
    expect((view.viewport as unknown as { composer: unknown }).composer).toBe(med);
    expect(low.dispose).not.toHaveBeenCalled(); expect(med.dispose).not.toHaveBeenCalled();
    expect(applyPreset).toHaveBeenCalledTimes(3); expect(resize).toHaveBeenCalledTimes(3);
    expect(onQuality.mock.calls).toEqual([['med'], ['low'], ['med']]);
  });
});
