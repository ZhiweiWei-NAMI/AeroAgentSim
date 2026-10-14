import { describe, expect, it } from 'vitest';
import * as T from 'three';
import { IncidentFX } from './incident-fx';
import { Rain, rainCount } from './rain';
import { WindStreaks, streakCount } from './wind';
import { fogDensity } from './sky-weather';
import { fxHash, fxSeed } from './fx-shared';
import type { Conditions } from '../conditions';

const conditions: Conditions = {
  wind: { east: 6, north: 0, speed: 6, fromDeg: 270, gust: false },
  precipitationMmH: 4, visibilityM: 500, light: 'day',
  incidents: [{ id: 'fire-a', kind: 'fire', label: 'Building fire', position: [20, 40, 0], radiusM: 30, phase: 'active', sinceS: 5 }],
};
const camera = new T.PerspectiveCamera(); camera.position.set(60, 40, -60); camera.updateMatrixWorld();

/** Instance matrices (smoke/flames) or slot seeds — identical at the same (conditions, t). */
function matrices(mesh: T.InstancedMesh): number[] {
  return [...mesh.instanceMatrix.array as Float32Array].map(value => Math.fround(value));
}

describe('atmosphere determinism: same (conditions, t) → same instance matrices', () => {
  it('smoke puffs repeat exactly at the same simulation time', () => {
    const first = new IncidentFX('med'), second = new IncidentFX('med');
    const ring = new Map<string, number>();
    first.update(conditions.incidents, 12.5, camera, ring, undefined, () => 0);
    second.update(conditions.incidents, 12.5, camera, ring, undefined, () => 0);
    const gather = (layer: IncidentFX) => [...layer.root.children[0].children]
      .filter(node => node instanceof T.InstancedMesh) as T.InstancedMesh[];
    const a = gather(first), b = gather(second);
    expect(a.length).toBe(b.length);
    for (let i = 0; i < a.length; i++) expect(matrices(a[i])).toEqual(matrices(b[i]));
    first.dispose(); second.dispose();
  });
  it('rain and wind streak counts follow rate/speed and their seeds are pure functions of the slot', () => {
    expect(rainCount(1, 'high')).toBe(1500); expect(rainCount(4, 'high')).toBe(4000);
    expect(rainCount(10, 'high')).toBe(8000); expect(rainCount(10, 'low')).toBe(Math.round(8000 / 3));
    expect(streakCount(1, 'high')).toBe(0); expect(streakCount(12, 'high')).toBeLessThanOrEqual(160);
    expect(streakCount(12, 'low')).toBeLessThanOrEqual(40);
    const rain = new Rain('med'), wind = new WindStreaks('med');
    rain.update(4, 30, camera, conditions.wind ?? undefined); wind.update(conditions.wind, 30, camera);
    const rainGeometry = (rain as unknown as { geometry: T.InstancedBufferGeometry }).geometry;
    const seedsA = [...(rainGeometry.getAttribute('dropSeed').array as Float32Array)];
    rain.update(4, 90, camera, conditions.wind ?? undefined);
    const seedsB = [...(rainGeometry.getAttribute('dropSeed').array as Float32Array)];
    expect(seedsA.slice(0, 20)).toEqual(seedsB.slice(0, 20)); // slot seeds are time-independent
    rain.dispose(); wind.dispose();
  });
  it('fog density matches the 5 % contrast contract and the hash is stable', () => {
    expect(fogDensity(500)).toBeCloseTo(Math.sqrt(-Math.log(0.05)) / 500, 12);
    expect(fxHash(1234, 7)).toBe(fxHash(1234, 7));
    expect(fxSeed('fire-a')).toBe(fxSeed('fire-a'));
    expect(fxHash(1, 1)).not.toBe(fxHash(1, 2));
  });
});
