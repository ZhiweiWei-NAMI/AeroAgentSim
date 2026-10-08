import { describe, expect, it } from 'vitest';
import { Vector3 } from 'three';
import { worldOrientation, worldPosition } from './coordinates';
import { parseMeshPack } from './mesh-pack';

describe('descriptor frame conversion', () => {
  it('maps ENU and NED into the same renderer metres', () => {
    expect(worldPosition([2, 3, 4], 'enu').toArray()).toEqual([2, 4, -3]);
    expect(worldPosition([3, 2, -4], 'ned').toArray()).toEqual([2, 4, -3]);
  });
  it('rotates the source frame axes with the same basis as position', () => {
    const identity = [0, 0, 0, 1];
    for (const frame of ['enu', 'ned'] as const) {
      for (const axis of [[1, 0, 0], [0, 1, 0], [0, 0, 1]]) {
        const actual = new Vector3(...axis as [number, number, number]).applyQuaternion(worldOrientation(identity, frame));
        expect(actual.distanceTo(worldPosition(axis, frame))).toBeLessThan(1e-10);
      }
    }
  });
  it('uses the declared WGS84 origin and never invents one', () => {
    const origin = { lat: 31, lon: 121, alt: 24 };
    expect(worldPosition([31, 121, 24], 'wgs84', origin).length()).toBe(0);
    expect(worldPosition([31, 121, 34], 'wgs84', origin).y).toBeCloseTo(10);
    expect(() => worldPosition([31, 121, 24], 'wgs84')).toThrow(/origin/);
    expect(() => worldPosition([null, 1, 2] as unknown as number[], 'enu')).toThrow(/finite/);
  });
  it('rejects missing or zero-length orientation rather than substituting identity', () => {
    expect(() => worldOrientation([0, 0, 0, 0], 'enu')).toThrow(/quaternion/);
    expect(() => worldOrientation([0, 0, 1], 'enu')).toThrow(/quaternion/);
  });
  it('recognizes only the supported OSM2World manifest contract', () => {
    expect(() => parseMeshPack({ schema_version: 'unknown' })).toThrow();
  });
});
