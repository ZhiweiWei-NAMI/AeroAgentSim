import { describe, expect, it } from 'vitest';
import * as T from 'three';
import { splatTransform } from './splat-layer';

const base = { url: '/data/test.spz' };

function finiteMatrix(transform: ReturnType<typeof splatTransform>) {
  const matrix = new T.Matrix4().compose(
    new T.Vector3().fromArray(transform.position),
    new T.Quaternion().fromArray(transform.quaternion),
    new T.Vector3(transform.scale, transform.scale, transform.scale),
  );
  return matrix.elements.every(Number.isFinite);
}

describe('splatTransform', () => {
  it('defaults only absent optional properties', () => {
    expect(splatTransform(base)).toEqual({ position: [0, 0, 0], quaternion: [0, 0, 0, 1], scale: 1 });
  });

  it('coerces legacy exponent strings such as "5e-06" into finite numbers', () => {
    const transform = splatTransform({ ...base,
      position: [22.5, 34.279, -150],
      quaternion: [0.999988, -0.004833, '5e-06', -0.001079] as never,
      scale: 51.42 });
    expect(transform.quaternion.every(Number.isFinite)).toBe(true);
    expect(transform.quaternion[2]).toBeCloseTo(0.000005, 9);
    expect(finiteMatrix(transform)).toBe(true);
  });

  it('normalises the quaternion to unit length', () => {
    const transform = splatTransform({ ...base, quaternion: [0, 0, 0, 2] });
    expect(new T.Quaternion().fromArray(transform.quaternion).length()).toBeCloseTo(1, 9);
  });

  it.each([
    ['wrong position length', { position: [1, 2] }],
    ['wrong quaternion length', { quaternion: [0, 0, 0] }],
    ['boolean component', { position: [true, 1, 2] }],
    ['null component', { position: [null, 1, 2] }],
    ['empty string', { scale: '' }],
    ['nonfinite number', { scale: Number.POSITIVE_INFINITY }],
    ['zero-length quaternion', { quaternion: [0, 0, 0, 0] }],
    ['nonpositive scale', { scale: 0 }],
  ])('rejects %s', (_label, patch) => {
    expect(() => splatTransform({ ...base, ...(patch as object) } as never)).toThrow();
  });
});
