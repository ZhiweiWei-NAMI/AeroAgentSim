import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as T from 'three';
import { AtmosphereLayer } from './index';
import type { Conditions } from '../conditions';

const allocations = vi.hoisted(() => ({ count: 0 }));
vi.mock('three', async original => {
  const three = await original<typeof import('three')>();
  const tracked = { ...three };
  for (const name of ['Vector2', 'Vector3', 'Quaternion', 'Euler', 'Matrix4', 'Color', 'BufferGeometry', 'PlaneGeometry', 'InstancedBufferAttribute', 'ShaderMaterial', 'InstancedMesh'] as const) {
    (tracked as any)[name] = new Proxy(three[name], { construct(target, args) { allocations.count++; return Reflect.construct(target, args); } });
  }
  return tracked;
});
vi.mock('./fx-shared', async original => ({ ...await original<typeof import('./fx-shared')>(), smokeAtlas: async () => new T.Texture() }));
beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ beginPath() {}, arc() {}, fill() {}, stroke() {}, moveTo() {}, bezierCurveTo() {}, fillRect() {} } as any);
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(['high', 'med', 'low'] as const)('keeps %s fires to three effect draws and reuses steady-state storage', async quality => {
  const scene = new T.Scene(); scene.userData.lighting = { setAtmosphere() {} };
  const renderer = { domElement: { clientHeight: 900 }, getPixelRatio: () => 1 } as T.WebGLRenderer;
  const layer = new AtmosphereLayer(scene, renderer, quality); await layer.ready;
  const camera = new T.PerspectiveCamera(); camera.position.set(20, 30, 80); camera.lookAt(0, 0, 0);
  const c: Conditions = { wind: { east: 6, north: 0, speed: 6, fromDeg: 270, gust: false }, precipitationMmH: 4, visibilityM: 800, light: 'dusk',
    incidents: ['a', 'b'].map((id, i) => ({ id, kind: 'fire', label: id, position: [i * 20, 0, 0], radiusM: 30, phase: 'active', sinceS: 30, activeS: 30 })) };
  const roof = vi.fn(() => 0);
  layer.update(c, 30, camera, roof);
  const units = layer.root.children[0].children;
  const first = units[0].children.filter(n => n instanceof T.Mesh) as T.Mesh[];
  const second = units[1].children.filter(n => n instanceof T.Mesh) as T.Mesh[];
  expect(first.filter(n => n.visible)).toHaveLength(3); expect(second.filter(n => n.visible)).toHaveLength(3);
  expect(first[0].geometry).toBe(second[0].geometry); expect(first[2].geometry).toBe(second[2].geometry);
  expect(first[1].material).toBe(second[1].material); // smoke program/material shared across fires
  for (let i = 0; i < 3; i++) {
    expect((first[i].material as T.ShaderMaterial).vertexShader).toBe((second[i].material as T.ShaderMaterial).vertexShader);
    expect((first[i].material as T.ShaderMaterial).fragmentShader).toBe((second[i].material as T.ShaderMaterial).fragmentShader);
  }
  expect(layer.root.children.slice(1).filter(n => n.visible)).toHaveLength(2); // rain and wind: one mesh each
  const smoke = first[1] as T.InstancedMesh, flames = first[0] as T.InstancedMesh;
  const matrices = smoke.instanceMatrix.array, tint = smoke.geometry.getAttribute('puffTint').array;
  const before = allocations.count, write = vi.spyOn(T.InstancedMesh.prototype, 'setMatrixAt');
  for (let i = 0; i < 300; i++) layer.update(c, 30, camera, roof);
  expect(write).not.toHaveBeenCalled(); expect(allocations.count).toBe(before);
  expect(roof).toHaveBeenCalledTimes(2); // once per fire, not once per frame
  // Moving time must update the deterministic transforms, without constructing resources.
  for (let i = 0; i < 300; i++) { c.incidents[0].activeS = c.incidents[1].activeS = 30 + i / 60; layer.update(c, 30 + i / 60, camera, roof); }
  expect(write).toHaveBeenCalled(); expect(allocations.count).toBe(before);
  expect(smoke.instanceMatrix.array).toBe(matrices); expect(smoke.geometry.getAttribute('puffTint').array).toBe(tint);
  expect(roof).toHaveBeenCalledTimes(2);
  const timeUniform = (flames.material as T.ShaderMaterial).uniforms.uTime;
  for (const weather of layer.root.children.slice(1) as T.Mesh<T.BufferGeometry, T.ShaderMaterial>[]) expect(weather.material.uniforms.uTime).toBe(timeUniform);
  layer.invalidateSurface(); layer.update(c, 30 + 299 / 60, camera, roof);
  expect(roof).toHaveBeenCalledTimes(4);
  const frame = [...flames.instanceMatrix.array];
  camera.position.x += 10; layer.update(c, 35, camera);
  layer.update(c, 30 + 299 / 60, camera); // seek back with the same camera and cut remains closed-form
  camera.position.x -= 10; layer.update(c, 30 + 299 / 60, camera);
  expect([...flames.instanceMatrix.array]).toEqual(frame);
  layer.dispose();
});
