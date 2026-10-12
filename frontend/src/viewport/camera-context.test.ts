import { expect, it } from 'vitest';
import * as T from 'three';
import { FOLLOW_CAMERA_OFFSET } from './viewport';

it('keeps rooftops and surrounding streets in view when following a high aircraft', () => {
  const focus = new T.Vector3(0, 145, 0);
  const camera = new T.PerspectiveCamera(48, 1.25, 0.15, 16000);
  camera.position.copy(focus).add(new T.Vector3(...FOLLOW_CAMERA_OFFSET));
  camera.lookAt(focus); camera.updateMatrixWorld();
  for (const point of [new T.Vector3(0, 70, 0), new T.Vector3(-80, 0, -80)]) {
    const projected = point.clone().project(camera);
    expect(Math.abs(projected.x)).toBeLessThan(1);
    expect(Math.abs(projected.y)).toBeLessThan(1);
    expect(projected.z).toBeGreaterThan(-1); expect(projected.z).toBeLessThan(1);
  }
  const topRay = new T.Vector3(0, 1, 0).unproject(camera).sub(camera.position);
  expect(topRay.y).toBeLessThan(0); // The top of the view points at the city, too.
});
